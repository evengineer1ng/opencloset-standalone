# Tests for run lifecycle management

from __future__ import annotations

import os
import re
import tempfile
from unittest.mock import Mock

from api.api.app import create_app
from api.api.run_lifecycle import RunLifecycleError, RunManager
from api.api.streaming import EventQueueStore


class TestRunLifecycle:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()
        self.store = self.app.event_store
        self.rm = self.app.run_manager

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_session(self) -> str:
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "lifecycle test"},
        )
        return resp.get_json()["id"]

    def _submit_message(self, session_id: str) -> str:
        resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello"},
        )
        # Get the latest run id
        row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return row["id"]

    # -- State transitions --

    def test_start_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        # Run should be queued
        run = self.rm.get_run(run_id)
        assert run["status"] == "queued"

        # Start it
        self.rm.start_run(run_id)
        run = self.rm.get_run(run_id)
        assert run["status"] == "running"

    def test_succeed_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)
        self.rm.start_run(run_id)

        self.rm.succeed_run(run_id)
        run = self.rm.get_run(run_id)
        assert run["status"] == "succeeded"
        assert run["completed_at"] is not None

    def test_fail_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)
        self.rm.start_run(run_id)

        self.rm.fail_run(run_id, "model timeout", "timeout")
        run = self.rm.get_run(run_id)
        assert run["status"] == "failed"
        assert run["error"] == "model timeout"

    def test_block_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)
        self.rm.start_run(run_id)

        self.rm.block_run(run_id, "action run exceeded discovery budget", "action_progress_blocked")
        run = self.rm.get_run(run_id)
        assert run["status"] == "blocked"
        assert run["error"] == "action run exceeded discovery budget"

    def test_interrupt_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)
        self.rm.start_run(run_id)

        self.rm.interrupt_run(run_id)
        run = self.rm.get_run(run_id)
        assert run["status"] == "interrupted"

    def test_interrupt_queued_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        self.rm.interrupt_run(run_id)
        run = self.rm.get_run(run_id)
        assert run["status"] == "interrupted"

    def test_rollover_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)
        self.rm.start_run(run_id)

        new_session = self._create_session()
        self.rm.rollover_run(run_id, new_session)
        run = self.rm.get_run(run_id)
        assert run["status"] == "rolled-over"

    def test_invalid_transition_queued_to_succeeded(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        # Can't go queued → succeeded without running first
        try:
            self.rm.succeed_run(run_id)
            assert False, "Should have raised RunLifecycleError"
        except RunLifecycleError:
            pass

    def test_transition_nonexistent_run(self):
        try:
            self.rm.start_run("fake-run-id")
            assert False, "Should have raised RunLifecycleError"
        except RunLifecycleError:
            pass

    # -- Event streaming helpers --

    def test_stream_text_delta(self):
        self.rm.stream_text_delta("r1", "hello")
        event = self.store.get_queue("r1").get(block=False)
        assert event["type"] == "assistant_delta"
        assert event["data"]["text"] == "hello"

    def test_stream_text_delta_persists_replay_event(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        self.rm.stream_text_delta(run_id, "hello")
        replay = self.app.event_logger.get_stream_events(session_id, run_id)

        assert len(replay) == 1
        assert replay[0]["type"] == "assistant_delta"
        assert replay[0]["data"]["text"] == "hello"

    def test_run_replay_includes_lifecycle_events(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        self.rm.start_run(run_id)
        self.rm.stream_text_delta(run_id, "hello")
        self.rm.succeed_run(run_id)

        replay = self.app.event_logger.get_run_events(session_id, run_id)

        assert [event["type"] for event in replay] == [
            "run_queued",
            "run_started",
            "assistant_delta",
            "run_completed",
        ]

    def test_replay_event_timestamps_include_seconds(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        self.rm.start_run(run_id)
        replay = self.app.event_logger.get_run_events(session_id, run_id)

        assert re.search(r"T\d{2}:\d{2}:\d{2}\.\d{6}Z$", replay[0]["created_at"])

    def test_stream_tool_use(self):
        self.rm.stream_tool_use("r1", "read", {"path": "/tmp/test.txt"})
        event = self.store.get_queue("r1").get(block=False)
        assert event["type"] == "tool_call"
        assert event["data"]["tool_name"] == "read"

    def test_stream_tool_result(self):
        self.rm.stream_tool_result("r1", "call_1", "read", "success", "hello", error_code="tool.none")
        event = self.store.get_queue("r1").get(block=False)
        assert event["type"] == "tool_result"
        assert event["data"]["tool_id"] == "call_1"
        assert event["data"]["status"] == "success"
        assert event["data"]["error_code"] == "tool.none"

    def test_stream_thinking_delta(self):
        self.rm.stream_thinking_delta("r1", "thinking...")
        event = self.store.get_queue("r1").get(block=False)
        assert event["type"] == "thinking_delta"

    def test_stream_usage(self):
        self.rm.stream_usage("r1", 100, 50)
        event = self.store.get_queue("r1").get(block=False)
        assert event["type"] == "usage"
        assert event["data"]["input_tokens"] == 100
        assert event["data"]["output_tokens"] == 50

    # -- Message persistence --

    def test_persist_assistant_message(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        msg_id = self.rm.persist_assistant_message(
            session_id=session_id,
            run_id=run_id,
            content="I am an assistant response.",
            token_estimate=5,
            persistent=True,
        )
        assert msg_id is not None

        # Verify it's in the DB
        msg = self.app.db.execute(
            "SELECT id, role, content FROM messages WHERE id = ?",
            (msg_id,),
        ).fetchone()
        assert msg["role"] == "assistant"
        assert "assistant response" in msg["content"].lower()

    def test_persist_tool_result(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        inv_id = self.rm.persist_tool_result(
            session_id=session_id,
            run_id=run_id,
            tool_name="read",
            input_data={"path": "/tmp/test.txt"},
            output_data={"content": "hello"},
            status="completed",
        )
        assert inv_id is not None

        # Check tool_invocations table
        inv = self.app.db.execute(
            "SELECT tool_name, status FROM tool_invocations WHERE id = ?",
            (inv_id,),
        ).fetchone()
        assert inv["tool_name"] == "read"
        assert inv["status"] == "completed"

    def test_persist_tool_result_error(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        inv_id = self.rm.persist_tool_result(
            session_id=session_id,
            run_id=run_id,
            tool_name="exec",
            input_data={"command": "ls"},
            output_data=None,
            status="failed",
            error="permission denied",
        )
        assert inv_id is not None

        inv = self.app.db.execute(
            "SELECT error FROM tool_invocations WHERE id = ?",
            (inv_id,),
        ).fetchone()
        assert "permission denied" in inv["error"]

    # -- Query helpers --

    def test_get_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        run = self.rm.get_run(run_id)
        assert run is not None
        assert run["id"] == run_id

    def test_get_run_not_found(self):
        assert self.rm.get_run("fake-id") is None

    def test_get_active_run(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)

        active = self.rm.get_active_run(session_id)
        assert active is not None
        assert active["status"] == "queued"

        # After starting, still active
        self.rm.start_run(run_id)
        active = self.rm.get_active_run(session_id)
        assert active["status"] == "running"

        # After completing, no active run
        self.rm.succeed_run(run_id)
        active = self.rm.get_active_run(session_id)
        assert active is None

    def test_get_session_runs(self):
        session_id = self._create_session()
        self._submit_message(session_id)
        self._submit_message(session_id)
        self._submit_message(session_id)

        runs = self.rm.get_session_runs(session_id)
        assert len(runs) == 3

    def test_reconcile_stale_running_runs(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)
        self.rm.start_run(run_id)

        count = self.rm.reconcile_stale_running_runs()

        run = self.rm.get_run(run_id)
        assert count == 1
        assert run is not None
        assert run["status"] == "interrupted"
        assert "Recovered stale running run during API startup." in (run["error"] or "")
        assert run["completed_at"] is not None

    # -- SSE integration --

    def test_succeed_sends_sentinel(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)
        self.rm.start_run(run_id)

        self.rm.succeed_run(run_id)
        done = self.store.get_queue(run_id).get(block=False)
        sentinel = self.store.get_queue(run_id).get(block=False)
        assert done["type"] == "done"
        assert done["data"]["status"] == "succeeded"
        assert sentinel is None

    def test_fail_sends_error_event(self):
        session_id = self._create_session()
        run_id = self._submit_message(session_id)
        self.rm.start_run(run_id)

        self.rm.fail_run(run_id, "oops")
        event = self.store.get_queue(run_id).get(block=False)
        assert event["type"] == "error"
        assert event["data"]["message"] == "oops"

        replay = self.app.event_logger.get_stream_events(session_id, run_id)
        assert replay[-1]["type"] == "error"
        assert replay[-1]["data"]["message"] == "oops"
