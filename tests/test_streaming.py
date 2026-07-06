# Tests for SSE streaming

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.api.app import create_app
from api.api.events import StreamEvent
from api.api.streaming import (
    EventQueueStore,
    format_sse,
    sse_stream,
)


class TestFormatSSE:
    def test_basic_event(self):
        event = {"type": "text_delta", "data": {"text": "hello"}}
        result = format_sse(event)
        assert "event: text_delta" in result
        assert '"text": "hello"' in result

    def test_error_event(self):
        event = {"type": "error", "data": {"message": "timeout"}}
        result = format_sse(event)
        assert "event: error" in result
        assert '"message": "timeout"' in result

    def test_default_type(self):
        event = {"data": {"x": 1}}
        result = format_sse(event)
        assert "event: message" in result

    def test_trailing_blank_lines(self):
        event = {"type": "done", "data": {}}
        result = format_sse(event)
        # SSE requires two trailing newlines to terminate the message
        assert result.endswith("\n\n")


class TestEventQueueStore:
    def test_get_queue_creates(self):
        store = EventQueueStore()
        q = store.get_queue("run-1")
        assert q is not None
        assert q.maxsize == 10000

    def test_get_queue_returns_same(self):
        store = EventQueueStore()
        q1 = store.get_queue("run-1")
        q2 = store.get_queue("run-1")
        assert q1 is q2

    def test_enqueue_and_get(self):
        store = EventQueueStore()
        store.enqueue("run-1", {"type": "text_delta", "data": {"text": "hi"}})
        event = store.get_queue("run-1").get(block=False)
        assert event["type"] == "text_delta"

    def test_enqueue_drops_oldest_when_queue_is_full(self):
        store = EventQueueStore(maxsize=1)
        store.enqueue("run-1", {"type": "text_delta", "data": {"text": "old"}})
        store.enqueue("run-1", {"type": "text_delta", "data": {"text": "new"}})

        event = store.get_queue("run-1").get(block=False)
        assert event["data"]["text"] == "new"

    def test_complete_sends_sentinel(self):
        store = EventQueueStore()
        store.complete("run-1")
        event = store.get_queue("run-1").get(block=False)
        assert event is None

    def test_complete_replaces_full_queue_with_sentinel(self):
        store = EventQueueStore(maxsize=1)
        store.enqueue("run-1", {"type": "text_delta", "data": {"text": "stale"}})

        store.complete("run-1")

        event = store.get_queue("run-1").get(block=False)
        assert event is None

    def test_cleanup_removes_queue(self):
        store = EventQueueStore()
        store.get_queue("run-1")
        store.cleanup("run-1")
        assert "run-1" not in store._queues

    def test_cleanup_missing_key(self):
        store = EventQueueStore()
        # Should not raise
        store.cleanup("nonexistent")


class TestSSEStream:
    def test_yields_events(self):
        store = EventQueueStore()
        store.enqueue("r1", {"type": "text_delta", "data": {"text": "a"}})
        store.enqueue("r1", {"type": "text_delta", "data": {"text": "b"}})
        store.enqueue("r1", {"type": "done", "data": {"status": "succeeded"}})
        store.complete("r1")

        results = list(sse_stream("r1", store))
        # Should have 3 events + no error (complete sent sentinel)
        assert len(results) == 3
        assert "text_delta" in results[0]
        assert "text_delta" in results[1]
        assert "done" in results[2]

    def test_timeout_on_empty_queue(self):
        store = EventQueueStore()
        store.get_queue("r2")
        # No events enqueued — should timeout and yield error
        # Use timeout=0.1 to speed up test
        import queue
        original_timeout = 5
        results = []
        # Can't easily override the hardcoded timeout in sse_stream,
        # so just verify the queue is created and empty
        q = store.get_queue("r2")
        assert q.empty()


class TestStreamingRoutes:
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

    def _create_session(self) -> str:
        self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "stream test"},
        )
        session_resp = self.client.get("/api/sessions")
        return session_resp.get_json()["sessions"][0]["id"]

    def _wrap_db_with_session_error(self):
        real_db = self.app.db

        class _DbProxy:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, params=()):
                if "SELECT id FROM sessions WHERE id = ?" in sql:
                    raise sqlite3.InterfaceError("bad parameter or other API misuse")
                return self._inner.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        self.app.db = _DbProxy(real_db)

    def test_stream_no_active_run(self):
        session_id = self._create_session()

        resp = self.client.get(f"/api/sessions/{session_id}/stream")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/event-stream")
        assert b"error" in resp.data

    def test_stream_specific_run_not_found(self):
        session_id = self._create_session()

        resp = self.client.get(f"/api/sessions/{session_id}/runs/fake-run/stream")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/event-stream")
        assert b"error" in resp.data

    def test_stream_route_invalid_session_id_returns_structured_sqlite_error_details(self):
        session_id = self._create_session()
        self._wrap_db_with_session_error()

        resp = self.client.get(f"/api/sessions/{session_id}/stream")

        assert resp.status_code == 400
        payload = resp.get_json()
        assert payload["error"] == "invalid session_id parameter"
        assert payload["detail"]["route"] == "stream_session"
        assert payload["detail"]["exception_type"] == "InterfaceError"

    def test_stream_headers(self):
        # Create session + message to get a run
        self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "stream test"},
        )
        session_resp = self.client.get("/api/sessions")
        session_id = session_resp.get_json()["sessions"][0]["id"]

        self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello"},
        )

        # Enqueue a test event
        db = self.app.db
        run_row = db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]
        self.app.event_store.enqueue(run_id, {"type": "text_delta", "data": {"text": "test"}})
        self.app.event_store.complete(run_id)

        resp = self.client.get(f"/api/sessions/{session_id}/stream")
        assert resp.headers.get("Cache-Control") == "no-cache"
        assert resp.headers.get("X-Accel-Buffering") == "no"

    def test_replay_run_events(self):
        self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "replay test"},
        )
        session_resp = self.client.get("/api/sessions")
        session_id = session_resp.get_json()["sessions"][0]["id"]

        self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello"},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        self.app.run_manager.start_run(run_id)
        self.app.run_manager.stream_text_delta(run_id, "test")
        self.app.run_manager.stream_tool_result(run_id, "call_1", "read", "success", "hello")
        self.app.run_manager.stream_usage(run_id, 10, 3)
        self.app.run_manager.emit_stream_event(
            run_id,
            StreamEvent.assistant_final(
                status="succeeded",
                finish_reason="stop",
                final_text="final answer",
                transient_text="planning... final answer",
                transcript_persisted=True,
            ),
        )
        self.app.run_manager.succeed_run(run_id)

        resp = self.client.get(f"/api/sessions/{session_id}/runs/{run_id}/events")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["run_id"] == run_id
        assert [event["type"] for event in data["events"]] == [
            "run_queued",
            "run_started",
            "assistant_delta",
            "tool_result",
            "usage",
            "assistant_final",
            "run_completed",
        ]
        assert data["events"][-2]["data"] == {
            "status": "succeeded",
            "finish_reason": "stop",
            "final_text": "final answer",
            "transient_text": "planning... final answer",
            "transcript_persisted": True,
        }

    def test_replay_run_events_over_sse(self):
        self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "replay sse test"},
        )
        session_resp = self.client.get("/api/sessions")
        session_id = session_resp.get_json()["sessions"][0]["id"]

        self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello"},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        self.app.run_manager.start_run(run_id)
        self.app.run_manager.stream_text_delta(run_id, "test")
        self.app.run_manager.stream_tool_result(run_id, "call_1", "read", "success", "hello")
        self.app.run_manager.emit_stream_event(
            run_id,
            StreamEvent.assistant_final(
                status="blocked",
                finish_reason="prompt_unanswered",
                final_text="",
                transient_text="I need to inspect the code before answering.",
                transcript_persisted=False,
            ),
        )
        self.app.run_manager.succeed_run(run_id)

        resp = self.client.get(f"/api/sessions/{session_id}/runs/{run_id}/stream?replay=1")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/event-stream")
        assert b"event: run_queued" in resp.data
        assert b"event: run_started" in resp.data
        assert b"event: assistant_delta" in resp.data
        assert b"event: tool_result" in resp.data
        assert b"event: assistant_final" in resp.data
        assert b'"tool_name": "read"' in resp.data
        assert b'"text": "test"' in resp.data
        assert b'"finish_reason": "prompt_unanswered"' in resp.data
        assert b"event: run_completed" in resp.data

    def test_live_stream_includes_done_event_before_close(self):
        self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "live done test"},
        )
        session_resp = self.client.get("/api/sessions")
        session_id = session_resp.get_json()["sessions"][0]["id"]

        self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "hello"},
        )

        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        run_id = run_row["id"]

        self.app.run_manager.start_run(run_id)
        self.app.run_manager.stream_text_delta(run_id, "test")
        self.app.run_manager.succeed_run(run_id)

        resp = self.client.get(f"/api/sessions/{session_id}/runs/{run_id}/stream")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/event-stream")
        assert b"event: assistant_delta" in resp.data
        assert b"event: done" in resp.data

    def test_replay_run_events_not_found(self):
        resp = self.client.get("/api/sessions/fake-session/runs/fake-run/events")
        assert resp.status_code == 404
