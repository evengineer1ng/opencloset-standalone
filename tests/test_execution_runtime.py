from __future__ import annotations

import io
import os
import tempfile
import uuid

from api.api.app import create_app
from api.api.execution_runtime import ClawExecutionRuntime
from api.agent.runner import RunExecutionResult


class _FakeProcess:
    def __init__(self, lines: list[str], returncode: int = 0):
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -1

    def kill(self):
        self.killed = True
        self.returncode = -9


class TestClawExecutionRuntime:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_session_and_run(self) -> tuple[str, str]:
        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "provider": "llamacpp", "label": "claw runtime test"},
        )
        session_id = session_resp.get_json()["id"]
        message_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "inspect the file"},
        )
        return session_id, message_resp.get_json()["run_id"]

    def test_claw_runtime_translates_cli_stream_into_canonical_events(self, monkeypatch):
        session_id, run_id = self._create_session_and_run()

        lines = [
            '{"type":"stream.assistant_delta","data":{"text":"Hello"}}',
            '{"type":"stream.tool_call","data":{"tool_name":"read_file","tool_id":"call_1","input":{"path":"foo.py"}}}',
            '{"type":"stream.tool_result","data":{"tool_id":"call_1","tool_name":"read_file","status":"success","content":"ok"}}',
            '{"type":"stream.usage","data":{"input_tokens":12,"output_tokens":4}}',
            '{"status":"succeeded","finish_reason":"stop","final_text":"Hello"}',
        ]

        fake_process = _FakeProcess(lines)

        def _fake_popen(*args, **kwargs):
            return fake_process

        monkeypatch.setattr("api.api.execution_runtime.subprocess.Popen", _fake_popen)

        runtime = ClawExecutionRuntime(self.app, self.app.execution_support, command="openclaw", use_local_mode=True, timeout_seconds=30)
        result = runtime.execute_run(session_id, run_id)

        assert result.status == "succeeded"
        assert result.final_text == "Hello"
        assert result.input_tokens == 12
        assert result.output_tokens == 4
        assert len(result.tool_results or []) == 1

        replay = self.app.event_logger.get_run_events(session_id, run_id)
        replay_types = [event["type"] for event in replay]
        assert "run_started" in replay_types
        assert "assistant_delta" in replay_types
        assert "tool_call" in replay_types
        assert "tool_result" in replay_types
        assert "usage" in replay_types
        assert "assistant_final" in replay_types
        assert "run_completed" in replay_types

        tool_rows = self.app.db.execute(
            "SELECT tool_name, status FROM tool_invocations WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        assert len(tool_rows) == 1
        assert tool_rows[0]["tool_name"] == "read_file"
        assert tool_rows[0]["status"] == "success"

    def test_claw_runtime_uses_deterministic_uuid_session_keys(self):
        session_key = ClawExecutionRuntime._claw_session_key("session-123")

        assert session_key == ClawExecutionRuntime._claw_session_key("session-123")
        assert session_key != "opencloset:session-123"
        assert str(uuid.UUID(session_key)) == session_key

    def test_claw_runtime_prefers_openclaw_cmd_on_windows(self, monkeypatch):
        monkeypatch.setattr("api.api.execution_runtime.os.name", "nt")
        monkeypatch.setattr("api.api.execution_runtime.shutil.which", lambda name: r"C:\Users\evana\AppData\Roaming\npm\openclaw.cmd" if name == "openclaw.cmd" else None)

        runtime = ClawExecutionRuntime(self.app, self.app.execution_support, command="openclaw", use_local_mode=True, timeout_seconds=30)

        assert runtime.command == r"C:\Users\evana\AppData\Roaming\npm\openclaw.cmd"

    def test_claw_runtime_parses_pretty_printed_json_output(self, monkeypatch):
        session_id, run_id = self._create_session_and_run()

        lines = [
            "{",
            '  "status": "succeeded",',
            '  "finish_reason": "stop",',
            '  "result": {',
            '    "finalAssistantVisibleText": "Hello from pretty JSON"',
            "  }",
            "}",
        ]

        fake_process = _FakeProcess(lines)

        def _fake_popen(*args, **kwargs):
            return fake_process

        monkeypatch.setattr("api.api.execution_runtime.subprocess.Popen", _fake_popen)

        runtime = ClawExecutionRuntime(self.app, self.app.execution_support, command="openclaw", use_local_mode=True, timeout_seconds=30)
        result = runtime.execute_run(session_id, run_id)

        assert result.status == "succeeded"
        assert result.final_text == "Hello from pretty JSON"

        messages = self.client.get(f"/api/sessions/{session_id}/messages").get_json()["messages"]
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "Hello from pretty JSON"


class TestLocalExecutionRuntimeDelegation:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path, start_background_workers=False)
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_session_and_run(self, message: str) -> tuple[str, str]:
        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "provider": "llamacpp", "label": "delegation runtime test"},
        )
        session_id = session_resp.get_json()["id"]
        message_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": message},
        )
        return session_id, message_resp.get_json()["run_id"]

    def test_local_runtime_auto_delegates_build_request_and_reintegrates_worker_report(self, monkeypatch):
        session_id, run_id = self._create_session_and_run("Fix the runner bug and verify the tests.")

        delegated_task_ids: list[str] = []

        def _fake_execute_task(task_id: str):
            delegated_task_ids.append(task_id)
            return {
                "id": task_id,
                "session_id": session_id,
                "task_type": "implement",
                "substrate_id": "codex_cli",
                "status": "completed",
                "input_tokens": 55,
                "output_tokens": 21,
                "result_text": '{"summary":"Fixed the runner bug","files_touched":["opencloset/api/agent/runner.py"],"tests_passed":["pytest tests/test_api.py -q"],"patch_summary":"Adjusted delegation routing behavior.","exit_status":"success"}',
                "result_summary": "Fixed the runner bug",
                "result_payload": {
                    "summary": "Fixed the runner bug",
                    "files_touched": ["opencloset/api/agent/runner.py"],
                    "commands_run": ["pytest tests/test_api.py -q"],
                    "tests_run": ["pytest tests/test_api.py -q"],
                    "tests_passed": ["pytest tests/test_api.py -q"],
                    "open_questions": [],
                    "risks": [],
                    "patch_summary": "Adjusted delegation routing behavior.",
                    "exit_status": "success",
                },
                "error": None,
            }

        def _unexpected_local_run(*args, **kwargs):
            raise AssertionError("local runner should not execute for auto-delegated build requests")

        monkeypatch.setattr(self.app.delegation_worker, "execute_task", _fake_execute_task)
        monkeypatch.setattr(self.app.agent_runner, "execute_run", _unexpected_local_run)

        result = self.app.execution_runtime.execute_run(session_id, run_id)

        assert delegated_task_ids
        assert result.status == "succeeded"
        assert result.final_text.startswith("I delegated that to")
        assert "Fixed the runner bug" in result.final_text
        assert "pytest tests/test_api.py -q" in result.final_text

        messages = self.client.get(f"/api/sessions/{session_id}/messages").get_json()["messages"]
        assert messages[-1]["role"] == "assistant"
        assert "Adjusted delegation routing behavior." in messages[-1]["content"]

    def test_local_runtime_keeps_plain_conversation_local(self, monkeypatch):
        session_id, run_id = self._create_session_and_run("Why did we choose a delegated runtime here?")

        local_calls: list[tuple[str, str]] = []

        def _fake_local_execute(session_arg: str, run_arg: str):
            local_calls.append((session_arg, run_arg))
            return RunExecutionResult(
                run_id=run_arg,
                session_id=session_arg,
                status="succeeded",
                finish_reason="stop",
                text="Because the local model is acting as the orchestrator.",
                final_text="Because the local model is acting as the orchestrator.",
                transient_text="Because the local model is acting as the orchestrator.",
                tool_results=[],
            )

        def _unexpected_delegate(*args, **kwargs):
            raise AssertionError("plain conversational requests should stay local")

        monkeypatch.setattr(self.app.agent_runner, "execute_run", _fake_local_execute)
        monkeypatch.setattr(self.app.delegation_worker, "execute_task", _unexpected_delegate)

        result = self.app.execution_runtime.execute_run(session_id, run_id)

        assert local_calls == [(session_id, run_id)]
        assert result.final_text == "Because the local model is acting as the orchestrator."

    def test_local_runtime_falls_back_to_local_runner_when_builder_substrate_unavailable(self, monkeypatch):
        session_id, run_id = self._create_session_and_run("Fix the runner bug and verify the tests.")

        local_calls: list[tuple[str, str]] = []

        def _raise_unavailable(*args, **kwargs):
            raise ValueError("delegation substrate is not available: codex_cli")

        def _fake_local_execute(session_arg: str, run_arg: str):
            local_calls.append((session_arg, run_arg))
            return RunExecutionResult(
                run_id=run_arg,
                session_id=session_arg,
                status="succeeded",
                finish_reason="stop",
                text="Local fallback executed.",
                final_text="Local fallback executed.",
                transient_text="Local fallback executed.",
                tool_results=[],
            )

        monkeypatch.setattr(self.app.delegations, "create_task", _raise_unavailable)
        monkeypatch.setattr(self.app.agent_runner, "execute_run", _fake_local_execute)

        result = self.app.execution_runtime.execute_run(session_id, run_id)

        assert local_calls == [(session_id, run_id)]
        assert result.status == "succeeded"
        assert result.final_text == "Local fallback executed."

    def test_local_runtime_blocks_partial_delegated_builder_result(self, monkeypatch):
        session_id, run_id = self._create_session_and_run("Fix the runner bug and verify the tests.")

        delegated_task_ids: list[str] = []

        def _fake_execute_task(task_id: str):
            delegated_task_ids.append(task_id)
            return {
                "id": task_id,
                "session_id": session_id,
                "task_type": "implement",
                "substrate_id": "copilot_cli",
                "status": "blocked",
                "input_tokens": 0,
                "output_tokens": 0,
                "result_text": "Running tests after installing Python dependencies to verify repository health.",
                "result_summary": "Running tests after installing Python dependencies to verify repository health.",
                "result_payload": {
                    "summary": "Running tests after installing Python dependencies to verify repository health.",
                    "files_touched": [],
                    "commands_run": [],
                    "tests_run": [],
                    "tests_passed": [],
                    "open_questions": [],
                    "risks": [],
                    "patch_summary": "",
                    "exit_status": "partial",
                },
                "error": None,
            }

        def _unexpected_local_run(*args, **kwargs):
            raise AssertionError("local runner should not execute for auto-delegated build requests")

        monkeypatch.setattr(self.app.delegation_worker, "execute_task", _fake_execute_task)
        monkeypatch.setattr(self.app.agent_runner, "execute_run", _unexpected_local_run)

        result = self.app.execution_runtime.execute_run(session_id, run_id)

        assert delegated_task_ids
        assert result.status == "blocked"
        assert result.finish_reason == "delegation_blocked"
        assert result.final_text.startswith("I delegated that to")
        assert "stopped before finishing" in result.final_text
