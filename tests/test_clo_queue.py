from __future__ import annotations

import os
import tempfile
import time
from unittest.mock import patch

from api.api.app import create_app
from api.agent.runner import RunExecutionResult


class TestCloQueueRoutes:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()

        workspace = self.client.post("/api/workspaces", json={"name": "OpenCloset"}).get_json()
        self.workspace_id = workspace["id"]

        first = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "label": "UI Session", "workspace_id": self.workspace_id},
        ).get_json()
        second = self.client.post(
            "/api/sessions",
            json={"model": "qwen3.6-27b", "label": "Runtime Session", "workspace_id": self.workspace_id},
        ).get_json()
        self.session_a = first["id"]
        self.session_b = second["id"]

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_enqueue_reorder_cancel_and_patch_settings(self):
        first = self.client.post(
            "/api/clo-queue/items",
            json={"session_id": self.session_a, "content": "Document transient windows."},
        )
        second = self.client.post(
            "/api/clo-queue/items",
            json={"session_id": self.session_b, "content": "Implement deterministic error cards."},
        )

        assert first.status_code == 201
        assert second.status_code == 201

        queue_state = self.client.get("/api/clo-queue")
        assert queue_state.status_code == 200
        queued_items = queue_state.get_json()["queued_items"]
        assert [item["position"] for item in queued_items] == [1, 2]

        move = self.client.post(
            f"/api/clo-queue/items/{queued_items[1]['id']}/move",
            json={"direction": "up"},
        )
        assert move.status_code == 200

        reordered = self.client.get("/api/clo-queue").get_json()["queued_items"]
        assert reordered[0]["session_id"] == self.session_b
        assert reordered[0]["position"] == 1

        cancel = self.client.post(f"/api/clo-queue/items/{reordered[0]['id']}/cancel")
        assert cancel.status_code == 200
        assert cancel.get_json()["status"] == "cancelled"

        settings = self.client.patch("/api/clo-queue", json={"paused": True, "pause_on_error": False})
        assert settings.status_code == 200
        assert settings.get_json()["paused"] is True
        assert settings.get_json()["pause_on_error"] is False

    def test_queue_state_exposes_worker_health(self):
        state = self.client.get("/api/clo-queue")

        assert state.status_code == 200
        payload = state.get_json()
        assert payload["worker_alive"] is False
        assert payload["last_poll_at"] is None
        assert payload["last_error"] is None

    def test_worker_dispatches_and_completes_queue_item(self):
        create = self.client.post(
            "/api/clo-queue/items",
            json={"session_id": self.session_a, "content": "Write the report summary."},
        )
        assert create.status_code == 201

        from tests.test_api import _FakeTextProvider

        with patch("api.agent.runner.create_provider", return_value=_FakeTextProvider("Queue finished cleanly.")):
            result = self.app.clo_queue_worker.poll_once()

        assert result.dispatched_item_id == create.get_json()["id"]
        state = self.client.get("/api/clo-queue").get_json()
        assert state["running_item"] is None
        assert state["queued_items"] == []
        assert state["recent_items"][0]["status"] == "completed"
        assert state["recent_items"][0]["id"] == create.get_json()["id"]

        completed = self.app.db.execute(
            "SELECT status, result_summary FROM clo_queue_items WHERE id = ?",
            (create.get_json()["id"],),
        ).fetchone()
        assert completed["status"] == "completed"
        assert "Queue finished cleanly" in completed["result_summary"]

        run = self.app.db.execute(
            "SELECT max_turns FROM runs WHERE id = ?",
            (state["recent_items"][0]["run_id"],),
        ).fetchone()
        assert run["max_turns"] is None

    def test_worker_uses_queue_turn_budget_for_dispatched_runs(self):
        create = self.client.post(
            "/api/clo-queue/items",
            json={"session_id": self.session_a, "content": "Take a real run at this queue task."},
        )
        assert create.status_code == 201

        captured = {}

        class _CaptureLoop:
            run_manager = None

            def run(self, build_prompt, existing_run_id=None, existing_turn_number=None):
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

        def _capture_loop(runtime, provider, registry, **kwargs):
            captured["max_turns"] = kwargs["config"].max_turns
            return _CaptureLoop()

        from tests.test_api import _FakeTextProvider

        with (
            patch("api.agent.runner.create_provider", return_value=_FakeTextProvider("Queue finished cleanly.")),
            patch("api.agent.runner.create_agent_loop", side_effect=_capture_loop),
        ):
            result = self.app.clo_queue_worker.poll_once()

        assert result.dispatched_item_id == create.get_json()["id"]
        assert captured["max_turns"] is None

    def test_failed_queue_item_keeps_error_and_not_partial_assistant_summary(self):
        create = self.client.post(
            "/api/clo-queue/items",
            json={"session_id": self.session_a, "content": "Keep my original prompt visible if this fails."},
        )
        assert create.status_code == 201

        def _failed_run(session_id, run_id):
            return RunExecutionResult(
                run_id=run_id,
                session_id=session_id,
                status="failed",
                finish_reason="max_turns_reached",
                text="<think> partial assistant output</think>",
            )

        with patch.object(self.app.agent_runner, "execute_run", side_effect=_failed_run):
            result = self.app.clo_queue_worker.poll_once()

        assert result.dispatched_item_id == create.get_json()["id"]
        failed = self.app.db.execute(
            "SELECT status, error, result_summary, message_content FROM clo_queue_items WHERE id = ?",
            (create.get_json()["id"],),
        ).fetchone()
        assert failed["status"] == "failed"
        assert failed["error"] == "max_turns_reached"
        assert failed["result_summary"] is None
        assert failed["message_content"] == "Keep my original prompt visible if this fails."

    def test_worker_ignores_unrelated_active_run_when_dispatching(self):
        create = self.client.post(
            "/api/clo-queue/items",
            json={"session_id": self.session_a, "content": "Dispatch despite stale foreign run."},
        )
        assert create.status_code == 201

        self.app.db.execute(
            "INSERT INTO runs (id, session_id, status, turn_number) VALUES (?, ?, 'running', ?)",
            ("stale-foreign-run", self.session_b, 99),
        )
        self.app.db.commit()

        from tests.test_api import _FakeTextProvider

        with patch("api.agent.runner.create_provider", return_value=_FakeTextProvider("Queue finished cleanly.")):
            result = self.app.clo_queue_worker.poll_once()

        assert result.dispatched_item_id == create.get_json()["id"]
        state = self.client.get("/api/clo-queue").get_json()
        assert state["queued_items"] == []
        assert state["recent_items"][0]["status"] == "completed"

    def test_worker_pauses_queue_on_error(self):
        create = self.client.post(
            "/api/clo-queue/items",
            json={"session_id": self.session_a, "content": "Crash on purpose."},
        )
        assert create.status_code == 201

        with patch("api.agent.runner.create_provider", side_effect=RuntimeError("provider unavailable")):
            result = self.app.clo_queue_worker.poll_once()

        assert result.dispatched_item_id == create.get_json()["id"]
        state = self.client.get("/api/clo-queue").get_json()
        assert state["paused"] is True
        assert state["recent_items"][0]["status"] == "failed"
        failed = self.app.db.execute(
            "SELECT status, error FROM clo_queue_items WHERE id = ?",
            (create.get_json()["id"],),
        ).fetchone()
        assert failed["status"] == "failed"
        assert "provider unavailable" in failed["error"]

    def test_queue_stream_emits_initial_state_snapshot(self):
        create = self.client.post(
            "/api/clo-queue/items",
            json={"session_id": self.session_a, "content": "Prime the queue stream."},
        )
        assert create.status_code == 201

        response = self.client.get("/api/clo-queue/stream", buffered=False)

        assert response.status_code == 200
        assert response.content_type.startswith("text/event-stream")
        assert response.headers.get("Cache-Control") == "no-cache"
        assert response.headers.get("X-Accel-Buffering") == "no"

        first_chunk = next(iter(response.response))
        assert b"event: state" in first_chunk
        assert create.get_json()["id"].encode() in first_chunk
        response.close()


def test_background_worker_survives_poll_exception_and_records_health(monkeypatch, tmp_path):
    app = create_app(db_path=str(tmp_path / "clo-queue-health.db"))
    closed = {"value": False}

    class _FakeIsolatedWorker:
        def __init__(self):
            self.poll_interval_seconds = 0.01

        def poll_once(self):
            raise RuntimeError("boom")

    class _FakeApp:
        def __init__(self):
            self.clo_queue_worker = _FakeIsolatedWorker()

        def close(self):
            closed["value"] = True

    monkeypatch.setattr(
        "api.api.app.create_app",
        lambda db_path=None, start_background_workers=False: _FakeApp(),
    )

    try:
        app.clo_queue_worker.poll_interval_seconds = 0.01
        app.clo_queue_worker.start_background()
        time.sleep(0.05)

        assert app.clo_queue_worker._thread is not None
        assert app.clo_queue_worker._thread.is_alive()

        health = app.clo_queue_worker.get_health()
        assert health["worker_alive"] is True
        assert health["last_poll_at"] is not None
        assert health["last_error"] == "boom"
    finally:
        app.clo_queue_worker.stop_background(timeout=1.0)
        app.close()

    assert closed["value"] is True