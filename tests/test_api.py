# Tests for REST API

from __future__ import annotations

import os
import sys
import tempfile
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from flask import Flask
from api.api.app import create_app
from api.api.events import StreamEvent
from api.agent.engine import ConversationRuntime, Message, MessageKind
from api.db.schema import init_db
from api.agent.loop import create_agent_loop as real_create_agent_loop
from api.agent.runner import DEFAULT_BASE_IDENTITY, SessionAgentRunner
from api.provider.base import (
    Provider,
    ProviderEvent,
    ProviderEventType,
    ToolCall as ProviderToolCall,
)
from api.tools.process import exec_exec
from api.tools.registry import ToolRegistry, build_tool
from unittest.mock import MagicMock, patch
from requests import HTTPError
from api.api.routes import _coerce_sqlite_text_param


class _FakeToolProvider(Provider):
    def run_stream(self, messages, **kwargs):
        yield ProviderEvent(
            type=ProviderEventType.TOOL_USE,
            tool_call=ProviderToolCall(
                id="call_1",
                name="echo",
                arguments='{"text": "hello"}',
            ),
        )
        yield ProviderEvent(
            type=ProviderEventType.USAGE,
            input_tokens=12,
            output_tokens=4,
            finish_reason="tool_calls",
        )


class _FakeTextProvider(Provider):
    def __init__(self, text: str, *, input_tokens: int = 12, output_tokens: int = 4, finish_reason: str = "stop"):
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.finish_reason = finish_reason

    def run_stream(self, messages, **kwargs):
        yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=self.text)
        yield ProviderEvent(
            type=ProviderEventType.USAGE,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            finish_reason=self.finish_reason,
        )


class _CapturingTextProvider(_FakeTextProvider):
    def __init__(self, text: str):
        super().__init__(text)
        self.seen_tools: list[list[dict] | None] = []
        self.seen_messages: list[list[dict]] = []

    def run_stream(self, messages, **kwargs):
        tools = kwargs.get("tools")
        self.seen_tools.append(list(tools) if isinstance(tools, list) else tools)
        self.seen_messages.append(list(messages))
        yield from super().run_stream(messages, **kwargs)


class _FakeTwoTurnToolProvider(Provider):
    def __init__(self, tool_name: str, arguments: dict[str, object], *, final_text: str = "done"):
        self.tool_name = tool_name
        self.arguments = arguments
        self.final_text = final_text
        self.call_count = 0

    def run_stream(self, messages, **kwargs):
        if self.call_count == 0:
            self.call_count += 1
            yield ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name=self.tool_name,
                    arguments=json.dumps(self.arguments),
                ),
            )
            yield ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=12,
                output_tokens=4,
                finish_reason="tool_calls",
            )
            return

        yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=self.final_text)
        yield ProviderEvent(
            type=ProviderEventType.USAGE,
            input_tokens=12,
            output_tokens=4,
            finish_reason="stop",
        )


class _ProgressThenValidationProvider(Provider):
    def __init__(self, *, progress_text: str, final_text: str, command: str = "pytest tests/test_api.py -q"):
        self.progress_text = progress_text
        self.final_text = final_text
        self.command = command
        self.call_count = 0

    def run_stream(self, messages, **kwargs):
        if self.call_count == 0:
            self.call_count += 1
            yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=self.progress_text)
            yield ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name="exec",
                    arguments=json.dumps({"command": self.command}),
                ),
            )
            yield ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=12,
                output_tokens=4,
                finish_reason="tool_calls",
            )
            return

        yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=self.final_text)
        yield ProviderEvent(
            type=ProviderEventType.USAGE,
            input_tokens=12,
            output_tokens=4,
            finish_reason="stop",
        )


class _FailingToolThenCrashProvider(Provider):
    def __init__(self, tool_name: str, arguments: dict[str, object], *, crash_message: str = "provider aborted after failed tool"):
        self.tool_name = tool_name
        self.arguments = arguments
        self.crash_message = crash_message
        self.call_count = 0

    def run_stream(self, messages, **kwargs):
        if self.call_count == 0:
            self.call_count += 1
            yield ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name=self.tool_name,
                    arguments=json.dumps(self.arguments),
                ),
            )
            yield ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=12,
                output_tokens=4,
                finish_reason="tool_calls",
            )
            return

        raise RuntimeError(self.crash_message)


class _PartialTextThenCrashProvider(Provider):
    def __init__(self, text: str = "Partial reply before crash.", *, crash_message: str = "Response ended prematurely"):
        self.text = text
        self.crash_message = crash_message

    def run_stream(self, messages, **kwargs):
        yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=self.text)
        raise RuntimeError(self.crash_message)


class _FakeDiagnosticsSuggestionProvider(Provider):
    def __init__(self, summary: str, recovery_prompt: str):
        self.summary = summary
        self.recovery_prompt = recovery_prompt

    def run_stream(self, messages, **kwargs):
        payload = json.dumps(
            {
                "summary": self.summary,
                "recovery_prompt": self.recovery_prompt,
            }
        )
        yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=payload)
        yield ProviderEvent(
            type=ProviderEventType.USAGE,
            input_tokens=20,
            output_tokens=20,
            finish_reason="stop",
        )


def _make_echo_registry():
    registry = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["echo"],
        provider_capabilities={"supports_tool_use": True},
    )
    registry.register(build_tool(
        "echo",
        description="Echo text back",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
        execute=lambda args: args.get("text", ""),
        read_only=True,
        categories=["core"],
    ))
    return registry


def _make_discovery_registry():
    registry = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["file_search", "read_file", "grep_search"],
        provider_capabilities={"supports_tool_use": True},
    )
    registry.register(build_tool(
        "file_search",
        description="Find files",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        execute=lambda args: "analyze_trades.py",
        read_only=True,
        categories=["core"],
    ))
    registry.register(build_tool(
        "read_file",
        description="Read a file",
        input_schema={
            "type": "object",
            "required": ["filePath"],
            "properties": {"filePath": {"type": "string"}},
        },
        execute=lambda args: "existing analysis script",
        read_only=True,
        categories=["core"],
    ))
    registry.register(build_tool(
        "grep_search",
        description="Search text",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        execute=lambda args: "strategy_metadata\ntransient_window",
        read_only=True,
        categories=["core"],
    ))
    return registry


def _make_exec_validation_registry():
    registry = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["exec"],
        provider_capabilities={"supports_tool_use": True},
    )
    registry.register(build_tool(
        "exec",
        description="Run a command",
        input_schema={
            "type": "object",
            "required": ["command"],
            "properties": {"command": {"type": "string"}},
        },
        execute=lambda args: "pytest passed",
        categories=["core"],
    ))
    return registry


class _ThreeTurnDiscoveryProvider(Provider):
    def __init__(self):
        self.call_count = 0

    def run_stream(self, messages, **kwargs):
        turns = [
            ProviderToolCall(id="call_1", name="file_search", arguments='{"query": "analyze_trades.py"}'),
            ProviderToolCall(id="call_2", name="read_file", arguments='{"filePath": "opencloset/analyze_trades.py"}'),
            ProviderToolCall(id="call_3", name="grep_search", arguments='{"query": "transient window"}'),
        ]
        tool_call = turns[self.call_count]
        self.call_count += 1
        yield ProviderEvent(type=ProviderEventType.TOOL_USE, tool_call=tool_call)
        yield ProviderEvent(
            type=ProviderEventType.USAGE,
            input_tokens=12,
            output_tokens=4,
            finish_reason="tool_calls",
        )


class _RepeatedPushIntentProvider(Provider):
    def __init__(self):
        self.call_count = 0

    def run_stream(self, messages, **kwargs):
        turns = [
            "Now pushing to origin.",
            "Trying to push again.",
        ]
        text = turns[self.call_count]
        self.call_count += 1
        yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=text)
        yield ProviderEvent(
            type=ProviderEventType.USAGE,
            input_tokens=12,
            output_tokens=4,
            finish_reason="stop",
        )


class TestCreateApp:
    def test_default_base_identity_includes_windows_acl_and_prerequisite_guidance(self):
        assert "If the user says a prerequisite was fixed externally or manually" in DEFAULT_BASE_IDENTITY
        assert "Strings like CURRENT_USER are invalid" in DEFAULT_BASE_IDENTITY
        assert "test the SSH/listing command next" in DEFAULT_BASE_IDENTITY
        assert "Prefer the structured function calling API" in DEFAULT_BASE_IDENTITY
        assert "do not widen the task without permission" in DEFAULT_BASE_IDENTITY
        assert "that does not authorize creating commits" in DEFAULT_BASE_IDENTITY

    def test_llamacpp_base_identity_adds_tool_fallback_appendix(self):
        fake_app = MagicMock()
        fake_app.config = {}
        runner = SessionAgentRunner(fake_app)

        llamacpp_identity = runner._resolve_base_identity("llamacpp")
        openai_identity = runner._resolve_base_identity("openai")

        assert "## llama.cpp Tool Fallback" in llamacpp_identity
        assert '<tool_call name="TOOL_NAME">' in llamacpp_identity
        assert "## llama.cpp Tool Fallback" not in openai_identity

    def test_creates_app(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            app = create_app(db_path=db_path)
            assert isinstance(app, Flask)
            assert app.config["DB_PATH"] == db_path
        finally:
            app.db.close()
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestInteractiveProcessApi:
    def setup_method(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            self.db_path = handle.name
        self.app = create_app(db_path=self.db_path, start_background_workers=False)
        self.client = self.app.test_client()

    def teardown_method(self):
        self.app.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_process_input_route_round_trips_interactive_exec(self):
        result = exec_exec(
            {
                "script_content": (
                    "print('READY', flush=True)\n"
                    "line = input()\n"
                    "print(f'GOT:{line}', flush=True)\n"
                ),
                "runner": sys.executable,
                "interactive": True,
            },
            store=self.app.process_store,
        )

        process_session_id = None
        for line in result.split("\n"):
            if line.startswith("session_id:"):
                process_session_id = line.split(":", 1)[1].strip()
                break

        assert process_session_id is not None

        state_resp = self.client.get(f"/api/processes/{process_session_id}")
        assert state_resp.status_code == 200
        state_payload = state_resp.get_json()
        assert state_payload["interactive"] is True
        assert state_payload["status"] == "running"

        input_resp = self.client.post(
            f"/api/processes/{process_session_id}/input",
            json={"data": "hello", "submit": True},
        )
        assert input_resp.status_code == 200
        process_payload = input_resp.get_json()["process"]

        if process_payload and process_payload["status"] == "running":
            time.sleep(0.2)
            follow_up = self.client.get(f"/api/processes/{process_session_id}")
            assert follow_up.status_code == 200
            process_payload = follow_up.get_json()

        assert process_payload is not None
        assert process_payload["status"] == "completed"
        assert "GOT:hello" in process_payload["output"]

    def test_process_route_returns_404_for_unknown_session(self):
        resp = self.client.get("/api/processes/missing-session")
        assert resp.status_code == 404

    def test_terminate_process_emits_subprocess_killed_event_for_active_run(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "process kill event"},
        )
        session_id = create_resp.get_json()["id"]
        self.app.db.execute(
            "INSERT INTO runs (id, session_id, status, turn_number) VALUES ('run-1', ?, 'running', 1)",
            (session_id,),
        )
        self.app.db.commit()

        result = exec_exec(
            {
                "script_content": "import time\nprint('READY', flush=True)\ntime.sleep(30)\n",
                "runner": sys.executable,
                "background": True,
                "_runtime_session_id": session_id,
                "_run_id": "run-1",
            },
            store=self.app.process_store,
        )

        process_session_id = None
        for line in result.split("\n"):
            if line.startswith("session_id:"):
                process_session_id = line.split(":", 1)[1].strip()
                break

        assert process_session_id is not None

        terminate_resp = self.client.post(f"/api/processes/{process_session_id}/terminate")

        assert terminate_resp.status_code == 200
        event = self.app.event_store.get_queue("run-1").get(block=False)
        assert event["type"] == "subprocess_killed"
        assert event["data"]["session_id"] == process_session_id
        assert event["data"]["reason"] == "kill"

    def test_db_initialized(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            app = create_app(db_path=db_path)
            # Check tables exist
            rows = app.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = [r["name"] for r in rows]
            assert "sessions" in table_names
            assert "messages" in table_names
            assert "runs" in table_names
            assert "providers" in table_names
        finally:
            app.db.close()
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_loop_defaults_do_not_force_turn_cap(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            app = create_app(db_path=db_path)
            assert app.config["LOOP_MAX_TOOL_CALLS_PER_TURN"] == 4
            assert app.config["LOOP_MAX_TURNS"] is None
            assert app.config["LOOP_MAX_TOKENS"] == 4096
        finally:
            app.db.close()
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_sets_llamacpp_request_tuning_defaults(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            app = create_app(db_path=db_path)
            cfg = app.agent_runner._resolve_provider_config("llamacpp", "qwen3.6-27b")

            assert cfg.options["cache_prompt"] is True
            assert cfg.options["n_cache_reuse"] == 256
            assert cfg.options["reasoning_format"] == "none"
            assert cfg.options["reasoning_budget"] == 0
            assert cfg.options["chat_template_kwargs"] == {"enable_thinking": False}
        finally:
            app.db.close()
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestSessionRoutes:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.app.db = self.app.db  # ensure DB is initialized
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_create_session(self):
        resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "label": "test session"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["model"] == "qwen3.6-27b"
        assert data["label"] == "test session"
        assert data["provider"] == "llamacpp"
        assert data["status"] == "active"
        assert "id" in data
        self.session_id = data["id"]

    def test_create_session_no_model(self):
        resp = self.client.post("/api/sessions", json={})
        assert resp.status_code == 400
        assert "model" in resp.get_json()["error"].lower()

    def test_create_session_custom_provider(self):
        resp = self.client.post(
            "/api/sessions",
            json={"model": "llama3", "provider": "ollama", "context_window": 8192},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["provider"] == "ollama"
        assert data["context_window"] == 8192

    def test_create_session_rejects_frontier_model_on_local_provider(self):
        resp = self.client.post(
            "/api/sessions",
            json={"model": "gpt-5.4", "provider": "llamacpp"},
        )

        assert resp.status_code == 400
        assert "looks like a frontier model" in resp.get_json()["error"]

    def test_create_session_rejects_unknown_provider(self):
        resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "provider": "anthropic"},
        )
        assert resp.status_code == 400
        assert "provider must be one of" in resp.get_json()["error"]

    def test_list_providers(self):
        resp = self.client.get("/api/providers")
        assert resp.status_code == 200
        providers = resp.get_json()["providers"]
        provider_ids = {provider["id"] for provider in providers}
        assert "llamacpp" in provider_ids
        assert "openai" in provider_ids

    def test_patch_provider_hides_api_key(self):
        resp = self.client.patch(
            "/api/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4.1-mini",
                "timeout_sec": 90,
                "enabled": True,
                "api_key": "secret-key",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == "openai"
        assert data["has_api_key"] is True
        assert "api_key" not in data

    def test_list_provider_models_discovers_and_dedupes_models(self):
        response = MagicMock()
        response.json.return_value = {
            "data": [
                {"id": "qwen3.6-27b"},
                {"id": "qwen3.6-27b"},
                {"id": "qwen3.6-14b"},
            ]
        }
        response.raise_for_status.return_value = None

        with patch("api.api.routes.requests.get", return_value=response) as mock_get:
            resp = self.client.get("/api/providers/llamacpp/models")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["provider_id"] == "llamacpp"
        assert data["discovered"] is True
        assert data["models"] == ["qwen3.6-27b", "qwen3.6-14b"]
        mock_get.assert_called_once()

    def test_list_provider_models_falls_back_to_configured_model(self):
        response = MagicMock()
        response.raise_for_status.side_effect = HTTPError("boom")

        with patch("api.api.routes.requests.get", return_value=response):
            resp = self.client.get("/api/providers/openai/models")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["provider_id"] == "openai"
        assert data["discovered"] is False
        assert data["models"]
        assert "error" in data

    def test_execute_run_uses_openai_provider_config(self):
        patch_resp = self.client.patch(
            "/api/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4.1-mini",
                "timeout_sec": 90,
                "enabled": True,
                "api_key": "secret-key",
            },
        )
        assert patch_resp.status_code == 200

        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "gpt-4.1-mini", "provider": "openai", "label": "frontier"},
        )
        assert create_resp.status_code == 201
        session_id = create_resp.get_json()["id"]

        message_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello frontier"},
        )
        assert message_resp.status_code == 201
        run_id = message_resp.get_json()["run_id"]

        captured: dict[str, object] = {}

        def _capture_provider(backend, config=None):
            captured["backend"] = backend
            captured["config"] = config
            return _FakeTextProvider("Frontier reply.")

        with patch("api.agent.runner.create_provider", side_effect=_capture_provider):
            execute_resp = self.client.post(f"/api/sessions/{session_id}/runs/{run_id}/execute")

        assert execute_resp.status_code == 200
        assert captured["backend"] == "openai"
        config = captured["config"]
        assert config.server_url == "https://api.openai.com/v1"
        assert config.model_name == "gpt-4.1-mini"
        assert config.timeout == 90.0
        assert config.api_key == "secret-key"

    def test_execute_run_rejects_openai_without_api_key(self):
        self.app.config["OPENAI_API_KEY"] = ""
        patch_resp = self.client.patch(
            "/api/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4.1-mini",
                "timeout_sec": 90,
                "enabled": True,
                "api_key": None,
            },
        )
        assert patch_resp.status_code == 200

        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "gpt-4.1-mini", "provider": "openai", "label": "frontier missing key"},
        )
        session_id = create_resp.get_json()["id"]

        message_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello frontier"},
        )
        run_id = message_resp.get_json()["run_id"]

        execute_resp = self.client.post(f"/api/sessions/{session_id}/runs/{run_id}/execute")
        assert execute_resp.status_code == 409
        assert "no API key is configured" in execute_resp.get_json()["error"]

    def test_create_session_with_tool_policy(self):
        resp = self.client.post(
            "/api/sessions",
            json={
                "model": "qwen3.6-27b",
                "label": "tool policy",
                "tool_policy": {
                    "enabled_tools": ["read", "write", "edit"],
                    "allow_destructive_tools": ["write", "edit"],
                    "allowed_paths": ["./tmp"],
                },
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["tool_policy"]["enabled_tools"] == ["read", "write", "edit"]
        assert data["tool_policy"]["allow_destructive_tools"] == ["write", "edit"]
        assert data["tool_policy"]["allowed_paths"] == ["./tmp"]

    def test_create_session_default_tool_policy_includes_plan_and_memory_tools(self):
        resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "label": "default tools"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "list_dir" in data["tool_policy"]["enabled_tools"]
        assert "memory_search" in data["tool_policy"]["enabled_tools"]
        assert "plan_get_active" in data["tool_policy"]["enabled_tools"]
        assert "plan_list_stored" in data["tool_policy"]["enabled_tools"]
        assert "plan_add_item" in data["tool_policy"]["enabled_tools"]
        assert data["tool_policy"]["allowed_paths"] == []

    def test_load_tool_policy_migrates_legacy_default_paths_to_permissive(self):
        session_id = "legacy-tool-policy"
        workspace_root = self.app.config.get("WORKSPACE_ROOT", "")
        memory_root = os.path.join(os.path.dirname(workspace_root), "memory") if workspace_root else ""
        self.app.db.execute(
            """
            INSERT INTO sessions (id, label, model, provider, context_window, tool_policy)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                "legacy policy",
                "qwen3.6-27b",
                "llamacpp",
                32768,
                json.dumps({
                    "enabled_tools": ["read", "write", "edit", "exec", "process"],
                    "allow_destructive_tools": [],
                    "allowed_paths": [path for path in [workspace_root, memory_root] if path],
                }),
            ),
        )
        self.app.db.commit()

        resp = self.client.get(f"/api/sessions/{session_id}/tool-policy")

        assert resp.status_code == 200
        assert resp.get_json()["tool_policy"]["allowed_paths"] == []

    def test_patch_session_tool_policy(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "label": "policy patch"},
        )
        session_id = create_resp.get_json()["id"]

        patch_resp = self.client.patch(
            f"/api/sessions/{session_id}/tool-policy",
            json={"allow_destructive_tools": ["write", "edit"], "allowed_paths": ["./notes"]},
        )
        assert patch_resp.status_code == 200
        payload = patch_resp.get_json()
        assert payload["tool_policy"]["allow_destructive_tools"] == ["write", "edit"]
        assert "read" in payload["tool_policy"]["enabled_tools"]
        assert payload["tool_policy"]["allowed_paths"] == ["./notes"]

        get_resp = self.client.get(f"/api/sessions/{session_id}/tool-policy")
        assert get_resp.status_code == 200
        assert get_resp.get_json()["tool_policy"]["allow_destructive_tools"] == ["write", "edit"]
        assert get_resp.get_json()["tool_policy"]["allowed_paths"] == ["./notes"]

    def test_patch_session_tool_policy_rejects_destructive_tool_not_enabled(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "label": "policy invalid"},
        )
        session_id = create_resp.get_json()["id"]

        patch_resp = self.client.patch(
            f"/api/sessions/{session_id}/tool-policy",
            json={
                "enabled_tools": ["read"],
                "allow_destructive_tools": ["write"],
            },
        )
        assert patch_resp.status_code == 400
        assert "destructive tools must also be present" in patch_resp.get_json()["error"]

    def test_list_sessions_empty(self):
        resp = self.client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["sessions"] == []

    def test_list_sessions(self):
        # Create a session first
        self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "list test"},
        )
        resp = self.client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["sessions"]) >= 1

    def test_list_sessions_filter(self):
        self.client.post(
            "/api/sessions",
            json={"model": "m1", "label": "active session"},
        )
        resp = self.client.get("/api/sessions?status=active")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["sessions"]) >= 1

    def test_get_session_not_found(self):
        resp = self.client.get("/api/sessions/nonexistent-id")
        assert resp.status_code == 404

    def test_get_session(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "get test"},
        )
        session_id = create_resp.get_json()["id"]

        resp = self.client.get(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == session_id
        assert data["model"] == "test-model"
        assert data["message_count"] == 0
        assert data["current_run"] is None

    def test_patch_session_updates_model_and_provider(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "provider": "llamacpp", "label": "patch test"},
        )
        session_id = create_resp.get_json()["id"]

        resp = self.client.patch(
            f"/api/sessions/{session_id}",
            json={"provider": "openai", "model": "gpt-4.1-mini", "label": "patched"},
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == session_id
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4.1-mini"
        assert data["label"] == "patched"

    def test_patch_session_rejects_frontier_model_on_local_provider(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "provider": "llamacpp", "label": "patch mismatch"},
        )
        session_id = create_resp.get_json()["id"]

        resp = self.client.patch(
            f"/api/sessions/{session_id}",
            json={"provider": "llamacpp", "model": "gpt-5.4"},
        )

        assert resp.status_code == 400
        assert "looks like a frontier model" in resp.get_json()["error"]

    def test_patch_session_rejects_active_run(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "provider": "llamacpp", "label": "patch active"},
        )
        session_id = create_resp.get_json()["id"]
        self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello"},
        )
        self.app.db.execute(
            "UPDATE runs SET status = 'running' WHERE session_id = ?",
            (session_id,),
        )
        self.app.db.commit()

        resp = self.client.patch(
            f"/api/sessions/{session_id}",
            json={"provider": "openai", "model": "gpt-4.1-mini"},
        )

        assert resp.status_code == 409
        assert "active run" in resp.get_json()["error"]

    def test_delete_session_not_found(self):
        resp = self.client.delete("/api/sessions/nonexistent-id")
        assert resp.status_code == 404

    def test_delete_session(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "delete test"},
        )
        session_id = create_resp.get_json()["id"]

        resp = self.client.delete(f"/api/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.get_json()["deleted"] == session_id

        # Verify gone
        get_resp = self.client.get(f"/api/sessions/{session_id}")
        assert get_resp.status_code == 404

    def test_rollover_session_carries_active_plan(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "roll test"},
        )
        session_id = create_resp.get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.update_active_goal(session_id, "Preserve this goal")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Resume here", status="doing")
        self.app.db.execute(
            "UPDATE sessions SET task_budget_remaining = 7 WHERE id = ?",
            (session_id,),
        )
        self.app.db.commit()

        resp = self.client.post(f"/api/sessions/{session_id}/rollover")

        assert resp.status_code == 201
        data = resp.get_json()
        assert data["source_session_id"] == session_id
        assert data["task_budget_remaining"] == 7
        assert data["active_plan"]["active_goal"] == "Preserve this goal"
        assert data["active_plan"]["next_item"]["content"] == "Resume here"
        assert data["active_plan"]["handoff"]["source_session_id"] == session_id
        assert data["active_plan"]["handoff"]["active_plan_id"] == active_plan["id"]

        source_session = self.client.get(f"/api/sessions/{session_id}").get_json()
        assert source_session["status"] == "rolled-over"
        assert source_session["rolled_over_to"] == data["id"]

    def test_rollover_session_rejects_active_run(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "roll active run"},
        )
        session_id = create_resp.get_json()["id"]
        self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello"},
        )

        resp = self.client.post(f"/api/sessions/{session_id}/rollover")
        assert resp.status_code == 409

    def test_rollover_session_prefers_valid_handoff_candidate_artifact(self):
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "roll artifact"},
        )
        session_id = create_resp.get_json()["id"]
        self.app.maintenance.create_artifact(
            session_id,
            "handoff-candidate",
            "Prepared handoff candidate for rollover",
            start_position=3,
            end_position=8,
        )

        resp = self.client.post(f"/api/sessions/{session_id}/rollover")

        assert resp.status_code == 201
        data = resp.get_json()
        handoff = data["active_plan"]["handoff"]
        assert handoff["source_session_id"] == session_id
        assert handoff["handoff_artifact_type"] == "handoff-candidate"
        assert handoff["handoff_artifact_summary"] == "Prepared handoff candidate for rollover"
        assert handoff["handoff_artifact_message_range"] == "3-8"

    def test_rollover_session_not_found(self):
        resp = self.client.post("/api/sessions/missing/rollover")
        assert resp.status_code == 404


class TestMessageRoutes:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.app.db = self.app.db
        self.client = self.app.test_client()

        # Create a session
        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "msg test"},
        )
        self.session_id = create_resp.get_json()["id"]

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_submit_message(self):
        resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "hello world"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["content"] == "hello world"
        assert data["role"] == "user"
        assert data["status"] == "queued"
        assert "message_id" in data
        assert "run_id" in data
        assert data["turn_number"] == 1

        run_row = self.app.db.execute(
            "SELECT max_turns FROM runs WHERE id = ?",
            (data["run_id"],),
        ).fetchone()
        assert run_row["max_turns"] is None

    def test_submit_message_no_content(self):
        resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={},
        )
        assert resp.status_code == 400

    def test_submit_message_invalid_role(self):
        resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "test", "role": "assistant"},
        )
        assert resp.status_code == 400

    def test_submit_message_not_found(self):
        resp = self.client.post(
            "/api/sessions/nonexistent-id/messages",
            json={"content": "test"},
        )
        assert resp.status_code == 404

    def test_submit_system_message(self):
        resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "system event", "role": "system"},
        )
        assert resp.status_code == 201
        assert resp.get_json()["role"] == "system"

    def test_submit_updates_token_count(self):
        # Submit a message
        self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "a" * 40},  # ~10 tokens
        )
        # Check session
        resp = self.client.get(f"/api/sessions/{self.session_id}")
        data = resp.get_json()
        assert data["token_count"] >= 10

    def test_list_messages_empty(self):
        resp = self.client.get(f"/api/sessions/{self.session_id}/messages")
        assert resp.status_code == 200
        assert resp.get_json()["messages"] == []

    def test_list_messages_after_submit(self):
        self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "first message"},
        )
        resp = self.client.get(f"/api/sessions/{self.session_id}/messages")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["archive_ready"] is False
        assert data["messages"][0]["archive_state"] is None

    def test_list_messages_includes_archive_ready_state(self):
        self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "first archived"},
        )
        self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "second archived"},
        )
        self.app.transcript.sync_archive_ready_ranges(
            self.session_id,
            [
                {
                    "start_position": 1,
                    "end_position": 2,
                    "source_ranges": [
                        {"artifact_type": "segment-summary", "start_position": 1, "end_position": 2},
                    ],
                },
            ],
            source_artifact_id="artifact-1",
        )

        resp = self.client.get(f"/api/sessions/{self.session_id}/messages")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["archive_ready"] is True
        assert data["messages"][1]["archive_ready"] is True
        assert data["messages"][0]["archive_state"]["metadata"]["range_start"] == 1
        assert data["messages"][1]["archive_state"]["metadata"]["range_end"] == 2

    def test_list_messages_filter_role(self):
        self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "user msg"},
        )
        self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "system msg", "role": "system"},
        )
        resp = self.client.get(
            f"/api/sessions/{self.session_id}/messages?role=user"
        )
        data = resp.get_json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["role"] == "user"

    def test_list_messages_defaults_to_latest_window(self):
        for index in range(60):
            self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": f"message {index + 1}"},
            )

        resp = self.client.get(f"/api/sessions/{self.session_id}/messages")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["messages"]) == 50
        assert data["messages"][0]["content"] == "message 11"
        assert data["messages"][-1]["content"] == "message 60"

    def test_list_messages_with_explicit_offset_preserves_forward_paging(self):
        for index in range(5):
            self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": f"message {index + 1}"},
            )

        resp = self.client.get(f"/api/sessions/{self.session_id}/messages?limit=2&offset=1")

        assert resp.status_code == 200
        data = resp.get_json()
        assert [item["content"] for item in data["messages"]] == ["message 2", "message 3"]

    def test_list_messages_not_found(self):
        resp = self.client.get("/api/sessions/nonexistent-id/messages")
        assert resp.status_code == 404

    def test_coerce_sqlite_text_param_stringifies_non_string_ids(self):
        class _WeirdId:
            def __str__(self):
                return "weird-session-id"

        assert _coerce_sqlite_text_param(_WeirdId(), "session_id") == "weird-session-id"

    def test_submit_to_non_active_session(self):
        # Delete the session first
        self.client.delete(f"/api/sessions/{self.session_id}")
        resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "test"},
        )
        assert resp.status_code == 404


class TestRunExecutionRoute:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path, start_background_workers=False)
        self.client = self.app.test_client()

        create_resp = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "label": "execute test"},
        )
        self.session_id = create_resp.get_json()["id"]

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_execute_run_not_found(self):
        resp = self.client.post(f"/api/sessions/{self.session_id}/runs/fake-run/execute")
        assert resp.status_code == 404

    def test_execute_queued_run(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "say hello"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        with patch.object(self.app.agent_runner, "execute_run") as execute_run:
            execute_run.return_value.run_id = run_id
            execute_run.return_value.session_id = self.session_id
            execute_run.return_value.status = "succeeded"
            execute_run.return_value.finish_reason = "stop"
            execute_run.return_value.text = "hello"
            execute_run.return_value.final_text = ""
            execute_run.return_value.transient_text = ""
            execute_run.return_value.provider_route = {
                "requested_provider": "llamacpp",
                "resolved_provider": "llamacpp",
            }
            execute_run.return_value.tool_results = []
            execute_run.return_value.input_tokens = 12
            execute_run.return_value.output_tokens = 4
            execute_run.return_value.interrupted = False
            execute_run.return_value.error = ""

            resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/execute")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["run_id"] == run_id
        assert data["status"] == "succeeded"
        assert data["text"] == "hello"
        assert data["final_text"] == ""
        assert data["transient_text"] == ""
        assert data["provider_route"] == {
            "requested_provider": "llamacpp",
            "resolved_provider": "llamacpp",
        }

    def test_execute_run_rejects_completed(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "say hello"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (run_id,))
        self.app.db.commit()

        resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/execute")
        assert resp.status_code == 409

    def test_native_transient_window_payload_round_trips(self):
        payload = {
            "origin": "watchdog",
            "artifact_type": "transient_error_window",
            "severity": "error",
            "category": "stream_error",
            "title": "Run stream disconnected",
            "summary": "The run stream dropped before completion.",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "session_id": self.session_id,
            "run_id": "run_stream_test",
        }

        create_resp = self.client.post(
            f"/api/sessions/{self.session_id}/windows",
            json={
                "title": "Run stream disconnected",
                "summary": "The run stream dropped before completion.",
                "source_type": "native",
                "native_type": "error_window",
                "payload": payload,
            },
        )

        assert create_resp.status_code == 201
        created = create_resp.get_json()
        assert created["source_type"] == "native"
        assert created["native_type"] == "error_window"
        assert created["payload"]["run_id"] == "run_stream_test"

        list_resp = self.client.get(f"/api/sessions/{self.session_id}/windows")
        assert list_resp.status_code == 200
        windows = list_resp.get_json()
        assert len(windows) == 1
        assert windows[0]["payload"]["artifact_type"] == "transient_error_window"

    def test_windows_route_invalid_session_id_returns_structured_sqlite_error_details(self):
        with patch.object(self.app.windows, "list_for_session", side_effect=sqlite3.InterfaceError("bad parameter or other API misuse")):
            resp = self.client.get(f"/api/sessions/{self.session_id}/windows")

        assert resp.status_code == 400
        payload = resp.get_json()
        assert payload["error"] == "invalid session_id parameter"
        assert payload["detail"]["route"] == "list_windows"
        assert payload["detail"]["exception_type"] == "InterfaceError"
        assert "bad parameter or other API misuse" in payload["detail"]["message"]

    def test_execute_failed_run_creates_native_error_window(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "fix the failed patch"},
        )
        assert submit_resp.status_code == 201
        run_id = submit_resp.get_json()["run_id"]

        provider = _FailingToolThenCrashProvider("echo", {})
        diagnostics_provider = _FakeDiagnosticsSuggestionProvider(
            "The model stopped after a failed schema-level echo call, so resume from the failed tool payload rather than re-running the whole task.",
            "Retry the run by inspecting the failed echo payload and issuing one schema-valid tool call for the exact next step.",
        )

        with (
            patch("api.agent.runner.create_provider", return_value=provider),
            patch("api.api.runtime_diagnostics.create_provider", return_value=diagnostics_provider),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
        ):
            execute_resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/execute")

        assert execute_resp.status_code == 200
        payload = execute_resp.get_json()
        assert payload["status"] == "failed"

        windows_resp = self.client.get(f"/api/sessions/{self.session_id}/windows")
        assert windows_resp.status_code == 200
        windows = windows_resp.get_json()
        assert len(windows) == 1

        window = windows[0]
        assert window["source_type"] == "native"
        assert window["native_type"] == "error_window"
        assert window["payload"]["origin"] == "watchdog"
        assert window["payload"]["run_id"] == run_id
        assert window["payload"]["category"] == "tool_schema_error"
        assert "echo" in window["summary"].lower()
        assert window["payload"]["suggested_direction"]["summary"].startswith("The model stopped after a failed schema-level echo call")
        assert "schema-valid tool call" in window["payload"]["suggested_direction"]["recovery_prompt"]

    def test_execute_run_returns_blocked_for_action_progress_blocked(self):
        self.app.config["LOOP_ACTION_DISCOVERY_BUDGET"] = 3
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "try building the report again"},
        )
        assert submit_resp.status_code == 201
        run_id = submit_resp.get_json()["run_id"]

        provider = _ThreeTurnDiscoveryProvider()

        with (
            patch("api.agent.runner.create_provider", return_value=provider),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_discovery_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
        ):
            execute_resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/execute")

        assert execute_resp.status_code == 200
        payload = execute_resp.get_json()
        assert payload["status"] == "blocked"
        assert payload["finish_reason"] == "action_progress_blocked"
        assert payload["error"].startswith("Blocked: action run exceeded the discovery budget")

        run_row = self.app.db.execute("SELECT status, error FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert run_row["status"] == "blocked"
        assert "files read 1" in run_row["error"]

    def test_execute_run_blocks_when_final_text_does_not_answer_prompt(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "fix the report"},
        )
        assert submit_resp.status_code == 201
        run_id = submit_resp.get_json()["run_id"]

        provider = _FakeTextProvider("I need to inspect the code before answering.")

        with (
            patch("api.agent.runner.create_provider", return_value=provider),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
        ):
            execute_resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/execute")

        assert execute_resp.status_code == 200
        payload = execute_resp.get_json()
        assert payload["status"] == "blocked"
        assert payload["finish_reason"] == "prompt_unanswered"
        assert payload["error"].startswith("Blocked: run ended without a clear answer to the last user prompt")
        assert payload["final_text"] == ""
        assert payload["text"] == ""
        assert payload["transient_text"] == "I need to inspect the code before answering."

        assistant_rows = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? AND run_id = ? AND role = 'assistant' ORDER BY position ASC",
            (self.session_id, run_id),
        ).fetchall()
        assert assistant_rows == []

    def test_execute_run_persists_only_final_answer_not_transient_progress_text(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "fix the report"},
        )
        assert submit_resp.status_code == 201
        run_id = submit_resp.get_json()["run_id"]

        provider = _ProgressThenValidationProvider(
            progress_text="Checking the report pipeline now.",
            final_text="I ran the targeted pytest command and it passed.",
        )

        with (
            patch("api.agent.runner.create_provider", return_value=provider),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_exec_validation_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
        ):
            execute_resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/execute")

        assert execute_resp.status_code == 200
        payload = execute_resp.get_json()
        assert payload["status"] == "succeeded"
        assert payload["text"] == "I ran the targeted pytest command and it passed."
        assert payload["final_text"] == "I ran the targeted pytest command and it passed."
        assert "Checking the report pipeline now." in payload["transient_text"]
        assert payload["transient_text"].endswith("I ran the targeted pytest command and it passed.")

        assistant_rows = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? AND run_id = ? AND role = 'assistant' ORDER BY position ASC",
            (self.session_id, run_id),
        ).fetchall()
        assert [row["content"] for row in assistant_rows] == ["I ran the targeted pytest command and it passed."]

        replay_resp = self.client.get(f"/api/sessions/{self.session_id}/runs/{run_id}/events")
        assert replay_resp.status_code == 200
        replay_events = replay_resp.get_json()["events"]
        assistant_final_events = [event for event in replay_events if event["type"] == "assistant_final"]
        assert len(assistant_final_events) == 1
        assert assistant_final_events[0]["data"] == {
            "status": "succeeded",
            "finish_reason": "stop",
            "final_text": "I ran the targeted pytest command and it passed.",
            "transient_text": "Checking the report pipeline now.I ran the targeted pytest command and it passed.",
            "transcript_persisted": True,
        }

    def test_execute_run_blocks_repeated_intent_fixation_before_generic_pending_action_giveup(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "push the branch"},
        )
        assert submit_resp.status_code == 201
        run_id = submit_resp.get_json()["run_id"]

        provider = _RepeatedPushIntentProvider()

        with (
            patch("api.agent.runner.create_provider", return_value=provider),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
        ):
            execute_resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/execute")

        assert execute_resp.status_code == 200
        payload = execute_resp.get_json()
        assert payload["status"] == "blocked"
        assert payload["finish_reason"] == "repeated_intent_blocked"
        assert payload["error"].startswith("Blocked: run kept restating the same planned action")
        assert payload["final_text"] == ""
        assert payload["transient_text"].endswith("Trying to push again.")

    def test_execute_run_blocked_packet_replays_withheld_transient_summary(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "fix the report"},
        )
        assert submit_resp.status_code == 201
        run_id = submit_resp.get_json()["run_id"]

        provider = _FakeTextProvider("I need to inspect the code before answering.")

        with (
            patch("api.agent.runner.create_provider", return_value=provider),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
        ):
            execute_resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/execute")

        assert execute_resp.status_code == 200
        payload = execute_resp.get_json()
        assert payload["run_id"] == run_id
        assert payload["session_id"] == self.session_id
        assert payload["status"] == "blocked"
        assert payload["finish_reason"] == "prompt_unanswered"
        assert payload["final_text"] == ""
        assert payload["text"] == ""
        assert payload["transient_text"] == "I need to inspect the code before answering."
        assert payload["error"].startswith("Blocked: run ended without a clear answer to the last user prompt.")

        assistant_rows = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? AND run_id = ? AND role = 'assistant' ORDER BY position ASC",
            (self.session_id, run_id),
        ).fetchall()
        assert assistant_rows == []

        replay_resp = self.client.get(f"/api/sessions/{self.session_id}/runs/{run_id}/events")
        assert replay_resp.status_code == 200
        replay_events = replay_resp.get_json()["events"]
        assistant_final_events = [event for event in replay_events if event["type"] == "assistant_final"]
        assert len(assistant_final_events) == 1
        assert assistant_final_events[0]["data"] == {
            "status": "blocked",
            "finish_reason": "prompt_unanswered",
            "final_text": "",
            "transient_text": "I need to inspect the code before answering.",
            "transcript_persisted": False,
        }

    def test_runtime_diagnostics_error_window_includes_failure_pivot_hint(self):
        run_id = "run_pivot_diag"
        self.app.db.execute(
            "INSERT INTO runs (id, session_id, status, turn_number) VALUES (?, ?, 'running', 1)",
            (run_id, self.session_id),
        )
        self.app.db.commit()

        self.app.run_manager.emit_stream_event(
            run_id,
            StreamEvent.tool_use("exec", {"command": "sqlite-utils hockey_lab.sqlite tables"}),
        )
        self.app.run_manager.stream_tool_result(
            run_id,
            "call_1",
            "exec",
            "execution_error",
            'Exit code: 1\nOutput:\nError: near "tables": syntax error',
            "exec exited with code 1",
            "exec.exit_nonzero",
        )
        self.app.run_manager.emit_stream_event(
            run_id,
            StreamEvent.tool_failure_pivot(
                "exec",
                "inspect_sqlite_db:sqlite_utils_cli:sqlite-utils:near_tables_syntax",
                2,
                "Switch method. Use Python's built-in sqlite3 module to inspect sqlite_master instead of retrying sqlite-utils.",
            ),
        )
        self.app.run_manager.fail_run(run_id, "exec exited with code 1")

        window = self.app.runtime_diagnostics.maybe_emit_run_error_window(self.session_id, run_id)

        assert window is not None
        payload = window["payload"]
        assert payload["suggested_direction"]["pivot_summary"].startswith("The loop already pivoted exec")
        assert "sqlite3 module" in payload["suggested_direction"]["pivot_hint"]
        assert payload["suggested_direction"]["recovery_prompt"] == payload["suggested_direction"]["pivot_hint"]

    def test_interrupt_queued_run(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "say hello"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/interrupt")

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["run_id"] == run_id
        assert payload["status"] == "interrupted"

        final_run = self.app.run_manager.get_run(run_id)
        assert final_run is not None
        assert final_run["status"] == "interrupted"

    def test_interrupt_running_run(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "say hello"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        self.app.run_manager.start_run(run_id)
        runtime = ConversationRuntime(
            session_id=self.session_id,
            model="test-model",
            provider="llamacpp",
            context_window=32768,
            db_conn=self.app.db,
            transcript_manager=self.app.transcript,
            event_logger=self.app.event_logger,
        )
        self.app.agent_runner._register_active_runtime(run_id, runtime)

        try:
            resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/interrupt")
        finally:
            self.app.agent_runner._clear_active_runtime(run_id, runtime)

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "interrupt_requested"
        assert runtime.is_interrupted is True

        current_run = self.app.run_manager.get_run(run_id)
        assert current_run is not None
        assert current_run["status"] == "running"

    def test_interrupt_running_run_cancels_active_provider(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "say hello"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        self.app.run_manager.start_run(run_id)
        runtime = ConversationRuntime(
            session_id=self.session_id,
            model="test-model",
            provider="llamacpp",
            context_window=32768,
            db_conn=self.app.db,
            transcript_manager=self.app.transcript,
            event_logger=self.app.event_logger,
        )

        class _CancelableProvider:
            def __init__(self):
                self.cancelled = False

            def cancel_active(self):
                self.cancelled = True

        provider = _CancelableProvider()
        self.app.agent_runner._register_active_runtime(run_id, runtime)
        self.app.agent_runner._register_active_provider(run_id, provider)

        try:
            resp = self.client.post(f"/api/sessions/{self.session_id}/runs/{run_id}/interrupt")
        finally:
            self.app.agent_runner._clear_active_runtime(run_id, runtime)

        assert resp.status_code == 200
        assert resp.get_json()["status"] == "interrupt_requested"
        assert provider.cancelled is True

    def test_execute_run_persists_interrupted_tool_result(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "use a tool"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        def create_interrupted_loop(runtime, provider, registry, **kwargs):
            loop = real_create_agent_loop(runtime, provider, registry, **kwargs)
            original_execute_all = loop.executor.execute_all

            def interrupted_execute_all(calls, **exec_kwargs):
                runtime._interrupted = True
                return original_execute_all(calls, **exec_kwargs)

            loop.executor.execute_all = interrupted_execute_all
            return loop

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", side_effect=create_interrupted_loop),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "interrupted"
        assert result.interrupted is True
        assert result.tool_results is not None
        assert result.tool_results[0]["status"] == "interrupted"

        final_run = self.app.run_manager.get_run(run_id)
        assert final_run is not None
        assert final_run["status"] == "interrupted"

        tool_messages = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? AND run_id = ? AND role = 'tool' ORDER BY position ASC",
            (self.session_id, run_id),
        ).fetchall()
        assert len(tool_messages) == 1
        payload = json.loads(tool_messages[0]["content"])
        tool_result = json.loads(payload["output"]["content"])
        assert tool_result["status"] == "interrupted"

    def test_execute_run_hides_tools_for_pure_architecture_prompt(self):
        self.app.db.execute(
            "UPDATE sessions SET provider = 'llamacpp' WHERE id = ?",
            (self.session_id,),
        )
        self.app.db.commit()

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={
                "content": "Design a local-first evaluation harness for OpenCloset with architecture, tradeoffs, and an implementation path.",
            },
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _CapturingTextProvider("Architecture answer.")
        captured: dict[str, object] = {}

        def create_capturing_loop(runtime, provider, registry, **kwargs):
            captured["max_tokens"] = kwargs["config"].max_tokens
            return real_create_agent_loop(runtime, provider, registry, **kwargs)

        with (
            patch("api.agent.runner.create_provider", return_value=provider),
            patch("api.agent.runner.create_agent_loop", side_effect=create_capturing_loop),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        assert captured["max_tokens"] == 224
        assert provider.seen_tools
        assert provider.seen_tools[0] == []
        assert provider.seen_messages
        assert "avoid ASCII diagrams, tables, and oversized lists" in provider.seen_messages[0][0]["content"]

    def test_execute_run_persists_partial_assistant_text_when_provider_crashes_mid_stream(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "say something useful"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        with (
            patch("api.agent.runner.create_provider", return_value=_PartialTextThenCrashProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "failed"
        assert result.error == "Response ended prematurely"

        final_run = self.app.run_manager.get_run(run_id)
        assert final_run is not None
        assert final_run["status"] == "failed"
        assert final_run["error"] == "Response ended prematurely"

        assistant_messages = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? AND run_id = ? AND role = 'assistant' ORDER BY position ASC",
            (self.session_id, run_id),
        ).fetchall()
        assert [row["content"] for row in assistant_messages] == ["Partial reply before crash."]

    def test_list_messages_keeps_latest_failed_turn_text_visible_after_partial_crash(self):
        for index in range(60):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": f"older message {index + 1}"},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "say something useful"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        with (
            patch("api.agent.runner.create_provider", return_value=_PartialTextThenCrashProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "failed"

        resp = self.client.get(f"/api/sessions/{self.session_id}/messages")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["messages"]) == 50
        assert data["messages"][-1]["content"] == "Partial reply before crash."

    def test_execute_run_applies_session_tool_policy_for_write(self):
        workspace_root = tempfile.mkdtemp()

        create_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "qwen3.6-27b",
                "label": "write policy",
                "tool_policy": {
                    "enabled_tools": ["read", "write", "edit"],
                    "allow_destructive_tools": ["write"],
                    "allowed_paths": [workspace_root],
                },
            },
        )
        session_id = create_resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "write a file"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        file_path = os.path.join(workspace_root, "note.txt")
        provider = _FakeTwoTurnToolProvider(
            "write",
            {"path": file_path, "content": "hello from tool policy"},
            final_text="write complete",
        )

        try:
            with patch("api.agent.runner.create_provider", return_value=provider):
                result = self.app.agent_runner.execute_run(session_id, run_id)

            assert result.status == "succeeded"
            assert result.tool_results is not None
            assert result.tool_results[0]["status"] == "success"
            assert os.path.exists(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                assert f.read() == "hello from tool policy"
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)
            os.rmdir(workspace_root)

    def test_execute_run_applies_session_tool_policy_for_edit(self):
        workspace_root = tempfile.mkdtemp()
        file_path = os.path.join(workspace_root, "note.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("alpha\nbeta\n")

        create_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "qwen3.6-27b",
                "label": "edit policy",
                "tool_policy": {
                    "enabled_tools": ["read", "write", "edit"],
                    "allow_destructive_tools": ["edit"],
                    "allowed_paths": [workspace_root],
                },
            },
        )
        session_id = create_resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "edit a file"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider(
            "edit",
            {
                "path": file_path,
                "edits": [{"oldText": "beta", "newText": "gamma"}],
            },
            final_text="edit complete",
        )

        try:
            with patch("api.agent.runner.create_provider", return_value=provider):
                result = self.app.agent_runner.execute_run(session_id, run_id)

            assert result.status == "succeeded"
            assert result.tool_results is not None
            assert result.tool_results[0]["status"] == "success"
            with open(file_path, "r", encoding="utf-8") as f:
                assert f.read() == "alpha\ngamma\n"
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)
            os.rmdir(workspace_root)

    def test_execute_run_applies_session_tool_policy_for_exec_workdir(self):
        workspace_root = tempfile.mkdtemp()
        allowed_root = os.path.join(workspace_root, "allowed")
        os.mkdir(allowed_root)

        create_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "qwen3.6-27b",
                "label": "exec policy",
                "tool_policy": {
                    "enabled_tools": ["read", "write", "edit", "exec"],
                    "allow_destructive_tools": [],
                    "allowed_paths": [allowed_root],
                },
            },
        )
        session_id = create_resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "run a command"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider(
            "exec",
            {"command": "cd", "workdir": allowed_root},
            final_text="exec complete",
        )

        try:
            with patch("api.agent.runner.create_provider", return_value=provider):
                result = self.app.agent_runner.execute_run(session_id, run_id)

            assert result.status == "succeeded"
            assert result.tool_results is not None
            assert result.tool_results[0]["status"] == "success"
        finally:
            os.rmdir(allowed_root)
            os.rmdir(workspace_root)

    def test_execute_run_legacy_default_tool_policy_is_permissive_for_exec(self):
        legacy_paths = [path for path in [self.app.config.get("WORKSPACE_ROOT", "")] if path]
        outside_root = tempfile.mkdtemp()

        create_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "qwen3.6-27b",
                "label": "legacy permissive exec",
                "tool_policy": {
                    "enabled_tools": ["read", "write", "edit", "exec"],
                    "allow_destructive_tools": [],
                    "allowed_paths": legacy_paths,
                },
            },
        )
        session_id = create_resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "run outside legacy scope"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider(
            "exec",
            {"command": "cd", "workdir": outside_root},
            final_text="legacy exec complete",
        )

        try:
            with patch("api.agent.runner.create_provider", return_value=provider):
                result = self.app.agent_runner.execute_run(session_id, run_id)

            assert result.status == "succeeded"
            assert result.tool_results is not None
            assert result.tool_results[0]["status"] == "success"
        finally:
            os.rmdir(outside_root)

    def test_execute_run_denies_exec_when_workdir_escapes_allowed_scope(self):
        workspace_root = tempfile.mkdtemp()
        allowed_root = os.path.join(workspace_root, "allowed")
        denied_root = os.path.join(workspace_root, "denied")
        os.mkdir(allowed_root)
        os.mkdir(denied_root)

        create_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "qwen3.6-27b",
                "label": "exec deny policy",
                "tool_policy": {
                    "enabled_tools": ["read", "write", "edit", "exec"],
                    "allow_destructive_tools": [],
                    "allowed_paths": [allowed_root],
                },
            },
        )
        session_id = create_resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "run a denied command"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider(
            "exec",
            {"command": "cd", "workdir": denied_root},
            final_text="exec denied complete",
        )

        try:
            with patch("api.agent.runner.create_provider", return_value=provider):
                result = self.app.agent_runner.execute_run(session_id, run_id)

            assert result.status == "succeeded"
            assert result.tool_results is not None
            assert result.tool_results[0]["status"] == "permission_denied"
            assert "Permission denied" in result.tool_results[0]["error"]
        finally:
            os.rmdir(denied_root)
            os.rmdir(allowed_root)
            os.rmdir(workspace_root)

    def test_execute_run_injects_rollover_handoff_via_input_pipeline(self):
        self.app.planning.update_active_goal(self.session_id, "Preserve goal")
        active_plan = self.app.planning.get_plan(self.session_id)
        self.app.planning.add_plan_item(self.session_id, active_plan["id"], "Resume next step", status="doing")

        rollover_resp = self.client.post(f"/api/sessions/{self.session_id}/rollover")
        assert rollover_resp.status_code == 201
        successor_id = rollover_resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{successor_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (successor_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(successor_id, run_id)

        assert result.status == "succeeded"
        system_message = captured["messages"][0]["content"]
        assert "Resuming after context rollover" in system_message
        assert "[Handoff resume]:" in system_message
        assert "source session id:" in system_message
        assert "Resume next step" in system_message
        assert self.app.planning.get_plan(successor_id)["handoff"] is None

    def test_execute_run_injects_workspace_context_into_prompt(self):
        workspace_resp = self.client.post(
            "/api/workspaces",
            json={"name": "OpenCloset", "description": "Main build workspace", "kind": "software"},
        )
        workspace_id = workspace_resp.get_json()["id"]
        project_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Prompt Wiring", "description": "Inject workspace context"},
        )
        project_id = project_resp.get_json()["id"]

        session_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "workspace prompt",
                "workspace_id": workspace_id,
                "build_project_id": project_id,
            },
        )
        session_id = session_resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(session_id, run_id)

        assert result.status == "succeeded"
        system_message = captured["messages"][0]["content"]
        assert "## Active Workspace" in system_message
        assert "**Workspace Name:** OpenCloset" in system_message
        assert "**Workspace Kind:** software" in system_message
        assert "**Workspace Status:** active" in system_message
        assert "Main build workspace" in system_message
        assert "## Active Build Project" in system_message
        assert "**Build Project Name:** Prompt Wiring" in system_message

    def test_execute_run_omits_workspace_section_when_session_unscoped(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        system_message = captured["messages"][0]["content"]
        assert "## Active Workspace" not in system_message

    def test_workspace_evidence_routes_create_and_list(self):
        workspace_resp = self.client.post(
            "/api/workspaces",
            json={"name": "Evidence Lab", "description": "Workspace evidence test"},
        )
        assert workspace_resp.status_code == 201
        workspace_id = workspace_resp.get_json()["id"]

        project_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Registry", "description": "Evidence project"},
        )
        assert project_resp.status_code == 201
        project_id = project_resp.get_json()["id"]

        session_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "evidence session",
                "workspace_id": workspace_id,
                "build_project_id": project_id,
            },
        )
        assert session_resp.status_code == 201
        session_id = session_resp.get_json()["id"]

        create_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/evidence",
            json={
                "title": "Regression proof",
                "summary": "Verified the shell loads real evidence rows.",
                "content": "npm run build passed after routing workspace views.",
                "evidence_type": "verification",
                "source_kind": "build",
                "session_id": session_id,
                "build_project_id": project_id,
                "tags": ["ui", "workspace"],
                "metadata": {"command": "npm run build"},
            },
        )
        assert create_resp.status_code == 201
        evidence = create_resp.get_json()
        assert evidence["title"] == "Regression proof"
        assert evidence["evidence_type"] == "verification"
        assert evidence["tags"] == ["ui", "workspace"]
        assert evidence["metadata"]["command"] == "npm run build"

        list_resp = self.client.get(f"/api/workspaces/{workspace_id}/evidence?evidence_type=verification")
        assert list_resp.status_code == 200
        payload = list_resp.get_json()
        assert payload["workspace_id"] == workspace_id
        assert len(payload["evidence"]) == 1
        assert payload["evidence"][0]["id"] == evidence["id"]

    def test_workspace_evidence_rejects_session_from_other_workspace(self):
        first_workspace = self.client.post(
            "/api/workspaces",
            json={"name": "A", "description": "one"},
        ).get_json()["id"]
        second_workspace = self.client.post(
            "/api/workspaces",
            json={"name": "B", "description": "two"},
        ).get_json()["id"]

        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "foreign session", "workspace_id": second_workspace},
        )
        session_id = session_resp.get_json()["id"]

        create_resp = self.client.post(
            f"/api/workspaces/{first_workspace}/evidence",
            json={
                "title": "Invalid reference",
                "summary": "Should fail",
                "session_id": session_id,
            },
        )
        assert create_resp.status_code == 400
        assert "session does not belong" in create_resp.get_json()["error"]

    def test_workspace_capture_routes_create_list_and_promote(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Capture Lab", "description": "Workspace capture test"},
        ).get_json()["id"]
        project_id = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Signals", "description": "Capture scope"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "capture session",
                "workspace_id": workspace_id,
                "build_project_id": project_id,
            },
        ).get_json()["id"]

        create_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/captures",
            json={
                "source": "manual",
                "event_type": "text",
                "content": "Observed a new blocker during workspace review.",
                "session_id": session_id,
                "build_project_id": project_id,
                "metadata": {"origin": "test"},
            },
        )
        assert create_resp.status_code == 201
        capture = create_resp.get_json()
        assert capture["workspace_id"] == workspace_id
        assert capture["status"] == "pending"

        list_resp = self.client.get(f"/api/workspaces/{workspace_id}/captures?status=pending")
        assert list_resp.status_code == 200
        payload = list_resp.get_json()
        assert len(payload["captures"]) == 1
        assert payload["captures"][0]["id"] == capture["id"]

        promote_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/captures/{capture['id']}/promote",
            json={"title": "Capture promoted", "summary": "Promoted from intake"},
        )
        assert promote_resp.status_code == 200
        promoted = promote_resp.get_json()
        assert promoted["capture"]["status"] == "processed"
        assert promoted["evidence"]["title"] == "Capture promoted"
        assert promoted["evidence"]["metadata"]["capture_id"] == capture["id"]

    def test_workspace_capture_rejects_foreign_session_reference(self):
        first_workspace = self.client.post(
            "/api/workspaces",
            json={"name": "Capture A", "description": "one"},
        ).get_json()["id"]
        second_workspace = self.client.post(
            "/api/workspaces",
            json={"name": "Capture B", "description": "two"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "other workspace session", "workspace_id": second_workspace},
        ).get_json()["id"]

        create_resp = self.client.post(
            f"/api/workspaces/{first_workspace}/captures",
            json={
                "source": "manual",
                "event_type": "text",
                "content": "Should fail",
                "session_id": session_id,
            },
        )
        assert create_resp.status_code == 400
        assert "session does not belong" in create_resp.get_json()["error"]

    def test_execute_run_uses_plan_get_active_tool(self):
        self.app.planning.update_active_goal(self.session_id, "Implement plan tools")

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "use the plan tool"},
        )
        assert submit_resp.status_code == 201

        self.client.patch(
            f"/api/sessions/{self.session_id}/tool-policy",
            json={"enabled_tools": ["plan_get_active"]},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider("plan_get_active", {}, final_text="plan read complete")
        with patch("api.agent.runner.create_provider", return_value=provider):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        assert result.tool_results is not None
        assert result.tool_results[0]["tool_name"] == "plan_get_active"
        assert "Implement plan tools" in result.tool_results[0]["content"]

    def test_execute_run_uses_plan_add_item_tool(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "add plan item"},
        )
        assert submit_resp.status_code == 201

        self.client.patch(
            f"/api/sessions/{self.session_id}/tool-policy",
            json={"enabled_tools": ["plan_add_item"]},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider(
            "plan_add_item",
            {"content": "Wire browser plan controls", "status": "doing"},
            final_text="plan item added",
        )
        with patch("api.agent.runner.create_provider", return_value=provider):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        assert result.tool_results is not None
        assert result.tool_results[0]["tool_name"] == "plan_add_item"
        plan = self.app.planning.get_plan(self.session_id)
        assert any(item["content"] == "Wire browser plan controls" for item in plan["items"])

    def test_execute_run_uses_plan_set_status_tool(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "pause the plan"},
        )
        assert submit_resp.status_code == 201

        self.client.patch(
            f"/api/sessions/{self.session_id}/tool-policy",
            json={"enabled_tools": ["plan_set_status"]},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider(
            "plan_set_status",
            {"status": "paused"},
            final_text="plan paused",
        )
        with patch("api.agent.runner.create_provider", return_value=provider):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        assert result.tool_results is not None
        assert result.tool_results[0]["tool_name"] == "plan_set_status"
        assert self.app.planning.get_plan(self.session_id)["status"] == "paused"

    def test_execute_run_injects_memory_context_into_prompt(self):
        date_label = self.app.memory._date_label()
        self.app.memory.append_session_entry(self.session_id, "Remember the last validated slice")
        self.app.memory.append_daily_entry("Today we landed the workspace ownership seam", date_label=date_label)

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_planning_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        system_message = captured["messages"][0]["content"]
        assert "## Session Diary" in system_message
        assert "Remember the last validated slice" in system_message
        assert f"## Daily Log ({date_label})" in system_message
        assert "Today we landed the workspace ownership seam" in system_message

    def test_execute_run_does_not_reinject_seen_memory_context(self):
        self.app.memory.append_session_entry(self.session_id, "Only inject this once")

        captured_messages = []

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured_messages.append(messages)
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        for content in ("first run", "second run"):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": content},
            )
            assert submit_resp.status_code == 201
            run_row = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            with (
                patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
                patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
                patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
                patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
                patch("api.agent.runner.register_planning_tools", side_effect=lambda registry, **kwargs: registry),
                patch("api.agent.runner.register_memory_tools", side_effect=lambda registry, **kwargs: registry),
                patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
            ):
                result = self.app.agent_runner.execute_run(self.session_id, run_row["id"])
            assert result.status == "succeeded"

        assert "Only inject this once" in captured_messages[0][0]["content"]
        assert "Only inject this once" not in captured_messages[1][0]["content"]

    def test_execute_run_uses_memory_search_tool(self):
        self.app.memory.append_session_entry(self.session_id, "Deterministic retrieval should stay boring first")

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "search memory"},
        )
        assert submit_resp.status_code == 201

        self.client.patch(
            f"/api/sessions/{self.session_id}/tool-policy",
            json={"enabled_tools": ["memory_search"]},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider(
            "memory_search",
            {"query": "boring", "limit": 3, "include_seen": True},
            final_text="memory searched",
        )
        with patch("api.agent.runner.create_provider", return_value=provider):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        assert result.tool_results is not None
        assert result.tool_results[0]["tool_name"] == "memory_search"
        assert "boring" in result.tool_results[0]["content"]
        assert "snippet" in result.tool_results[0]["content"]

    def test_execute_run_default_manifest_includes_plan_and_memory_tools(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        provider_tool_names = {tool["function"]["name"] for tool in captured["provider_tools"]}
        assert "list_dir" in provider_tool_names
        assert "memory_search" in provider_tool_names
        assert "plan_get_active" in provider_tool_names
        assert "plan_list_stored" in provider_tool_names
        assert "plan_add_item" in provider_tool_names

    def test_execute_run_runtime_policy_adds_plan_list_stored_for_older_session_policy(self):
        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        self.client.patch(
            f"/api/sessions/{self.session_id}/tool-policy",
            json={"enabled_tools": ["plan_get_active"]},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                _messages, provider_tools = build_prompt()
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        provider_tool_names = {tool["function"]["name"] for tool in captured["provider_tools"]}
        assert "list_dir" in provider_tool_names
        assert "plan_get_active" in provider_tool_names
        assert "plan_list_stored" in provider_tool_names

    def test_execute_run_memory_search_skips_prompt_injected_note_by_default(self):
        self.app.memory.append_session_entry(self.session_id, "Skip this because prompt already injected it")

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "search memory"},
        )
        assert submit_resp.status_code == 201

        self.client.patch(
            f"/api/sessions/{self.session_id}/tool-policy",
            json={"enabled_tools": ["memory_search"]},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        provider = _FakeTwoTurnToolProvider(
            "memory_search",
            {"query": "prompt", "limit": 3},
            final_text="memory searched",
        )
        with patch("api.agent.runner.create_provider", return_value=provider):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        assert result.tool_results is not None
        assert '"results": []' in result.tool_results[0]["content"]

    def test_execute_run_injects_latest_valid_maintenance_artifact(self):
        for content in ("older 1", "older 2", "older 3", "older 4", "recent 5", "recent 6"):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": content},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        self.app.maintenance.create_artifact(
            self.session_id,
            "segment-summary",
            "Segment summary (messages 1-4):\n- user[1]: older 1",
            start_position=1,
            end_position=4,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "compaction-marker",
            "Compaction marker for messages 1-4",
            metadata={
                "covered_ranges": [
                    {
                        "artifact_types": ["segment-summary", "micro-summary"],
                        "start_position": 1,
                        "end_position": 6,
                        "source_ranges": [
                            {"artifact_type": "segment-summary", "start_position": 1, "end_position": 4},
                            {"artifact_type": "micro-summary", "start_position": 5, "end_position": 6},
                        ],
                    },
                ],
            },
            start_position=1,
            end_position=6,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "decision-tool-digest",
            "Decision/tool digest:\n- Tool read [completed]: input {\"path\": \"/tmp/test.txt\"}",
            end_position=6,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: summarize the last exchange",
            start_position=5,
            end_position=6,
        )

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        system_message = captured["messages"][0]["content"]
        transcript_messages = captured["messages"][1:]
        assert "## Session Maintenance" in system_message
        assert "Segment summary (messages 1-4):" in system_message
        assert "Decision/tool digest:" in system_message
        assert "Derived micro-summary" in system_message
        assert "Derived segment-summary" in system_message
        assert "Derived decision-tool-digest" in system_message
        assert "summarize the last exchange" in system_message
        assert [message["content"] for message in transcript_messages] == ["continue"]

    def test_execute_run_updates_context_guard_with_compacted_token_usage(self):
        for content in (
            "older context one " * 8,
            "older context two " * 8,
            "older context three " * 8,
            "older context four " * 8,
            "recent context five",
            "recent context six",
        ):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": content},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        self.app.maintenance.create_artifact(
            self.session_id,
            "segment-summary",
            "Segment summary (messages 1-4):\n- user[1]: earlier compressed context",
            start_position=1,
            end_position=4,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "compaction-marker",
            "Compaction marker for messages 1-4",
            metadata={
                "covered_ranges": [
                    {
                        "artifact_types": ["segment-summary", "micro-summary"],
                        "start_position": 1,
                        "end_position": 6,
                        "source_ranges": [
                            {"artifact_type": "segment-summary", "start_position": 1, "end_position": 4},
                            {"artifact_type": "micro-summary", "start_position": 5, "end_position": 6},
                        ],
                    },
                ],
            },
            start_position=1,
            end_position=6,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "decision-tool-digest",
            "Decision/tool digest:\n- Next action: keep going",
            end_position=6,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: recent context",
            start_position=5,
            end_position=6,
        )

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201
        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                build_prompt()
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        guard = self.app.planning.get_plan(self.session_id)["context_guard"]
        assert guard["raw_tokens_used"] > guard["tokens_used"]
        assert guard["compaction_savings_tokens"] > 0
        assert guard["compacted_through_position"] == 6
        assert guard["compacted_range_count"] == 1
        assert guard["compacted_ranges"][0]["end_position"] == 6
        assert guard["compacted_ranges"][0]["source_ranges"] == [
            {"artifact_type": "segment-summary", "start_position": 1, "end_position": 4},
            {"artifact_type": "micro-summary", "start_position": 5, "end_position": 6},
        ]
        assert guard["rollover_threshold"] == 0
        assert self.app.planning.should_rollover(self.session_id) is False

    def test_execute_run_preserves_gaps_between_compacted_ranges(self):
        for content in (
            "older span one",
            "older span two",
            "gap three",
            "gap four",
            "recent span five",
            "recent span six",
        ):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": content},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        self.app.maintenance.create_artifact(
            self.session_id,
            "segment-summary",
            "Segment summary (messages 1-2):\n- user[1]: older span one",
            start_position=1,
            end_position=2,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: recent span five",
            start_position=5,
            end_position=6,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "compaction-marker",
            "Compaction marker for 2 covered ranges",
            metadata={
                "covered_ranges": [
                    {"artifact_type": "micro-summary", "start_position": 5, "end_position": 6},
                    {"artifact_type": "segment-summary", "start_position": 1, "end_position": 2},
                ],
            },
            start_position=1,
            end_position=6,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "decision-tool-digest",
            "Decision/tool digest:\n- Next action: keep the uncovered gap visible",
            end_position=6,
        )

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201
        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
                patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        transcript_messages = captured["messages"][1:]
        guard = self.app.planning.get_plan(self.session_id)["context_guard"]
        assert [message["content"] for message in transcript_messages] == ["gap three", "gap four", "continue"]
        assert guard["compacted_range_count"] == 2
        assert guard["compacted_ranges"][0]["start_position"] == 1
        assert guard["compacted_ranges"][1]["start_position"] == 5

    def test_execute_run_uses_worker_produced_non_prefix_segments(self):
        for index in range(1, 21):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": f"message {index}"},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        self.app.planning.update_active_goal(self.session_id, "Keep context compact")
        plan = self.app.planning.get_plan(self.session_id)
        self.app.planning.add_plan_item(self.session_id, plan["id"], "Resume after summary", status="doing")
        idle_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.app.db.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (idle_timestamp, self.session_id),
        )
        self.app.db.commit()

        first_poll = self.app.maintenance_worker.poll_once()
        assert any(artifact.artifact_type == "segment-summary" for artifact in first_poll.created_artifacts)

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201
        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        transcript_messages = captured["messages"][1:]
        guard = self.app.planning.get_plan(self.session_id)["context_guard"]
        assert [message["content"] for message in transcript_messages] == [
            "message 7",
            "message 8",
            "message 9",
            "message 10",
            "message 11",
            "message 12",
            "message 13",
            "message 14",
            "continue",
        ]
        assert guard["compacted_range_count"] == 2
        assert guard["compacted_ranges"][0]["end_position"] == 6
        assert guard["compacted_ranges"][1]["start_position"] == 15

    def test_execute_run_compacts_from_transcript_ranges_without_compaction_marker(self):
        for content in (
            "older one",
            "older two",
            "gap three",
            "gap four",
            "recent five",
            "recent six",
        ):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": content},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        self.app.maintenance.create_artifact(
            self.session_id,
            "segment-summary",
            "Segment summary (messages 1-2):\n- user[1]: older one",
            start_position=1,
            end_position=2,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: recent five",
            start_position=5,
            end_position=6,
        )
        self.app.transcript.sync_archive_ready_ranges(
            self.session_id,
            [
                {
                    "start_position": 1,
                    "end_position": 2,
                    "source_ranges": [
                        {"artifact_type": "segment-summary", "start_position": 1, "end_position": 2},
                    ],
                },
            ],
            source_artifact_id="archive-artifact-1",
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "decision-tool-digest",
            "Decision/tool digest:\n- Next action: keep the gap visible",
            end_position=6,
        )

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201
        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        system_message = captured["messages"][0]["content"]
        transcript_messages = captured["messages"][1:]
        guard = self.app.planning.get_plan(self.session_id)["context_guard"]
        assert "Derived segment-summary" in system_message
        assert "Segment summary (messages 1-2):" in system_message
        assert "Derived decision-tool-digest" in system_message
        assert "Decision/tool digest:" in system_message
        assert "Derived micro-summary" not in system_message
        assert "recent five" not in system_message
        assert [message["content"] for message in transcript_messages] == [
            "gap three",
            "gap four",
            "recent five",
            "recent six",
            "continue",
        ]
        assert guard["compacted_range_count"] == 1
        assert guard["compacted_ranges"][0]["start_position"] == 1
        assert guard["compacted_ranges"][0]["end_position"] == 2

    def test_execute_run_compacts_from_message_states_without_active_transcript_ranges(self):
        for content in (
            "older one",
            "older two",
            "gap three",
            "gap four",
        ):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": content},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        self.app.transcript.sync_archive_ready_ranges(
            self.session_id,
            [
                {
                    "start_position": 1,
                    "end_position": 2,
                    "source_ranges": [
                        {"artifact_type": "segment-summary", "start_position": 1, "end_position": 2},
                    ],
                },
            ],
            source_artifact_id="artifact-1",
        )
        self.app.db.execute(
            "UPDATE transcript_ranges SET status = 'stale' WHERE session_id = ? AND range_type = 'archive-ready'",
            (self.session_id,),
        )
        self.app.db.commit()

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201
        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
                patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        transcript_messages = captured["messages"][1:]
        guard = self.app.planning.get_plan(self.session_id)["context_guard"]
        assert [message["content"] for message in transcript_messages] == ["gap three", "gap four", "continue"]
        assert guard["compacted_range_count"] == 1
        assert guard["compacted_ranges"][0]["start_position"] == 1
        assert guard["compacted_ranges"][0]["end_position"] == 2

    def test_execute_run_compacts_from_summary_covered_message_states(self):
        for content in (
            "older one",
            "older two",
            "gap three",
            "gap four",
            "recent five",
            "recent six",
        ):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": content},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        self.app.maintenance.create_artifact(
            self.session_id,
            "segment-summary",
            "Segment summary (messages 1-2):\n- user[1]: older one",
            start_position=1,
            end_position=2,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: recent five",
            start_position=5,
            end_position=6,
        )
        self.app.transcript.sync_summary_covered_ranges(
            self.session_id,
            [
                {
                    "artifact_type": "segment-summary",
                    "start_position": 1,
                    "end_position": 2,
                    "source_ranges": [
                        {"artifact_type": "segment-summary", "start_position": 1, "end_position": 2},
                    ],
                },
                {
                    "artifact_type": "micro-summary",
                    "start_position": 5,
                    "end_position": 6,
                    "source_ranges": [
                        {"artifact_type": "micro-summary", "start_position": 5, "end_position": 6},
                    ],
                },
            ],
            source_artifact_id="summary-artifact-1",
        )
        self.app.db.execute(
            "UPDATE transcript_ranges SET status = 'stale' WHERE session_id = ? AND range_type = 'summary-covered'",
            (self.session_id,),
        )
        self.app.db.commit()

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201
        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        system_message = captured["messages"][0]["content"]
        transcript_messages = captured["messages"][1:]
        guard = self.app.planning.get_plan(self.session_id)["context_guard"]
        assert "Derived segment-summary" in system_message
        assert "Derived micro-summary" in system_message
        assert "recent five" in system_message
        assert [message["content"] for message in transcript_messages] == ["gap three", "gap four", "continue"]
        assert guard["compacted_range_count"] == 2
        assert guard["compacted_ranges"][0]["start_position"] == 1
        assert guard["compacted_ranges"][0]["end_position"] == 2
        assert guard["compacted_ranges"][1]["start_position"] == 5
        assert guard["compacted_ranges"][1]["end_position"] == 6

    def test_execute_run_omits_uncompacted_summaries_from_prompt(self):
        for content in (
            "older one",
            "older two",
            "recent three",
            "recent four",
        ):
            submit_resp = self.client.post(
                f"/api/sessions/{self.session_id}/messages",
                json={"content": content},
            )
            assert submit_resp.status_code == 201
            queued_run = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()

        self.app.maintenance.create_artifact(
            self.session_id,
            "segment-summary",
            "Segment summary (messages 1-2):\n- user[1]: older one",
            start_position=1,
            end_position=2,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: recent three",
            start_position=3,
            end_position=4,
        )
        self.app.maintenance.create_artifact(
            self.session_id,
            "decision-tool-digest",
            "Decision/tool digest:\n- Next action: keep the live transcript",
            end_position=4,
        )

        submit_resp = self.client.post(
            f"/api/sessions/{self.session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201
        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        run_id = run_row["id"]

        captured = {}

        class _CaptureLoop:
            def __init__(self):
                self.run_manager = None

            def run(self, build_prompt, existing_run_id, existing_turn_number):
                messages, provider_tools = build_prompt()
                captured["messages"] = messages
                captured["provider_tools"] = provider_tools
                self.run_manager.start_run(existing_run_id)
                self.run_manager.succeed_run(existing_run_id)
                return type(
                    "LoopResult",
                    (),
                    {
                        "error": "",
                        "finish_reason": "stop",
                        "text": "done",
                        "tool_results": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "interrupted": False,
                    },
                )()

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeToolProvider()),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", return_value=_CaptureLoop()),
        ):
            result = self.app.agent_runner.execute_run(self.session_id, run_id)

        assert result.status == "succeeded"
        system_message = captured["messages"][0]["content"]
        transcript_messages = captured["messages"][1:]
        assert "Derived decision-tool-digest" in system_message
        assert "Decision/tool digest:" in system_message
        assert "Derived segment-summary" not in system_message
        assert "Derived micro-summary" not in system_message
        assert [message["content"] for message in transcript_messages] == [
            "older one",
            "older two",
            "recent three",
            "recent four",
            "continue",
        ]

    def test_execute_run_pauses_and_marks_rollover_when_threshold_exceeded(self):
        self.app.config["ENABLE_ROLLOVER_GUARD"] = True
        self.app.planning.rollover_guard_enabled = True
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "tiny", "context_window": 16},
        )
        assert resp.status_code == 201
        session_id = resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        with (
            patch(
                "api.agent.runner.create_provider",
                return_value=_FakeTextProvider("Threshold pressure response. " * 6),
            ),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", side_effect=real_create_agent_loop),
        ):
            result = self.app.agent_runner.execute_run(session_id, run_id)

        assert result.status == "interrupted"
        assert result.interrupted is True
        assert self.app.planning.get_plan(session_id)["status"] == "paused"
        assert self.app.planning.should_rollover(session_id) is True

        event_types = [event["type"] for event in self.app.event_logger.get_session_events(session_id)]
        assert "context_warning" in event_types
        assert "rollover_triggered" in event_types

    def test_execute_run_does_not_pause_or_mark_rollover_when_guard_disabled(self):
        self.app.config["ENABLE_ROLLOVER_GUARD"] = False
        self.app.planning.rollover_guard_enabled = False
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "tiny", "context_window": 16},
        )
        assert resp.status_code == 201
        session_id = resp.get_json()["id"]

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        with (
            patch(
                "api.agent.runner.create_provider",
                return_value=_FakeTextProvider("Threshold pressure response. " * 6),
            ),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", side_effect=real_create_agent_loop),
        ):
            result = self.app.agent_runner.execute_run(session_id, run_id)

        assert result.status == "succeeded"
        assert result.interrupted is False
        assert self.app.planning.get_plan(session_id)["status"] != "paused"
        assert self.app.planning.should_rollover(session_id) is False

        guard = self.app.planning.get_plan(session_id)["context_guard"]
        assert guard["rollover_threshold"] == 0

        event_types = [event["type"] for event in self.app.event_logger.get_session_events(session_id)]
        assert "context_warning" not in event_types
        assert "rollover_triggered" not in event_types

    def test_execute_run_ignores_hydrated_history_when_compacted_prompt_is_small(self):
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "history", "context_window": 4000},
        )
        assert resp.status_code == 201
        session_id = resp.get_json()["id"]

        for idx in range(12):
            historical_run_id = f"hist-run-{idx}"
            self.app.db.execute(
                "INSERT INTO runs (id, session_id, status, turn_number) VALUES (?, ?, ?, ?)",
                (historical_run_id, session_id, "failed", idx + 1),
            )
            self.app.db.execute(
                """INSERT INTO messages
                   (id, session_id, run_id, role, content, position, token_estimate, persistent)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"hist-msg-{idx}",
                    session_id,
                    historical_run_id,
                    "assistant",
                    f"historical message {idx}",
                    idx + 1,
                    500,
                    1,
                ),
            )
        self.app.db.commit()

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "continue"},
        )
        assert submit_resp.status_code == 201

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        compacted_transcript = [
            Message(
                session_id=session_id,
                role="user",
                kind=MessageKind.TEXT,
                content="continue",
                token_estimate=20,
                persistent=True,
            )
        ]

        with (
            patch(
                "api.agent.runner.create_provider",
                return_value=_FakeTextProvider("Compacted prompt wins.", input_tokens=40, output_tokens=6),
            ),
            patch("api.agent.runner.create_default_registry", side_effect=lambda **kwargs: _make_echo_registry()),
            patch("api.agent.runner.register_filesystem_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.register_process_tools", side_effect=lambda registry, **kwargs: registry),
            patch("api.agent.runner.create_agent_loop", side_effect=real_create_agent_loop),
            patch.object(self.app.agent_runner, "_compact_transcript_for_prompt", return_value=compacted_transcript),
        ):
            result = self.app.agent_runner.execute_run(session_id, run_id)

        assert result.status == "succeeded"
        assert result.interrupted is False
        assert self.app.planning.get_plan(session_id)["status"] != "paused"
        event_types = [event["type"] for event in self.app.event_logger.get_session_events(session_id)]
        assert "context_warning" not in event_types
        assert "rollover_triggered" not in event_types
