# SSE Streaming endpoint - real-time agent output
#
# Server-Sent Events for streaming provider output back to the client.
# Events: text_delta, tool_use, thinking_delta, usage, done, error
#
# The streaming endpoint queues the request; the agent loop consumes
# events from a per-run queue and yields them via SSE.

from __future__ import annotations

import json
import queue
import threading
from typing import Generator

from flask import Flask, Response, jsonify, request, stream_with_context

from api.api.events import StreamEvent, stream_event_to_dict
from api.api.session_validation import validate_session_route_scope


# ---------------------------------------------------------------------------
# Run-level event queue
# ---------------------------------------------------------------------------


class EventQueueStore:
    """Thread-safe per-run event queues for SSE streaming."""

    def __init__(self, *, maxsize: int = 10000) -> None:
        self._queues: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        self._maxsize = max(1, int(maxsize))

    def get_queue(self, run_id: str) -> queue.Queue:
        """Get or create an event queue for a run."""
        with self._lock:
            if run_id not in self._queues:
                self._queues[run_id] = queue.Queue(maxsize=self._maxsize)
            return self._queues[run_id]

    def _offer(self, q: queue.Queue, item) -> None:
        try:
            q.put(item, block=False)
            return
        except queue.Full:
            pass

        try:
            q.get_nowait()
        except queue.Empty:
            return

        try:
            q.put(item, block=False)
        except queue.Full:
            # If another producer filled the slot first, drop this transient event.
            return

    def enqueue(self, run_id: str, event: StreamEvent | dict) -> None:
        """Push an event into the run's queue."""
        q = self.get_queue(run_id)
        self._offer(q, stream_event_to_dict(event))

    def complete(self, run_id: str) -> None:
        """Signal stream completion by enqueuing None (sentinel)."""
        q = self.get_queue(run_id)
        self._offer(q, None)

    def cleanup(self, run_id: str) -> None:
        """Remove the queue after streaming finishes."""
        with self._lock:
            self._queues.pop(run_id, None)


# ---------------------------------------------------------------------------
# SSE event formatting
# ---------------------------------------------------------------------------


def format_sse(event: StreamEvent | dict) -> str:
    """Format a dict as an SSE message string."""
    event_dict = stream_event_to_dict(event)
    lines = []
    lines.append(f"event: {event_dict.get('type', 'message')}")
    lines.append(f"data: {json.dumps(event_dict.get('data', {}))}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def sse_keepalive() -> str:
    """Emit an SSE comment to keep long-lived streams open."""
    return ": keep-alive\n\n"


def sse_stream(run_id: str, event_store: EventQueueStore) -> Generator[str, None, None]:
    """Generator that yields SSE events from the run's event queue."""
    q = event_store.get_queue(run_id)
    try:
        while True:
            try:
                event = q.get(timeout=5)
            except queue.Empty:
                # Long model/tool runs can go quiet for multiple seconds.
                # Keep the stream alive instead of fabricating a failure.
                yield sse_keepalive()
                continue
            if event is None:
                break
            yield format_sse(event)
    finally:
        event_store.cleanup(run_id)


def replay_sse_stream(events: list[dict]) -> Generator[str, None, None]:
    """Generator that yields stored replay events using the same SSE format."""
    for event in events:
        yield format_sse(event)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_streaming(app: Flask, event_store: EventQueueStore) -> None:
    """Register the SSE streaming endpoint."""

    @app.route("/api/sessions/<session_id>/stream", methods=["GET"])
    def stream_session(session_id: str):
        """Stream real-time events for the current active run in a session.

        Query params:
            run_id: Specific run to stream (defaults to latest active run).

        Returns an SSE stream of events until the run completes.
        """
        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="stream_session")
        if error_response:
            return error_response

        run_id = request.args.get("run_id")
        if not run_id:
            row = db.execute(
                "SELECT id FROM runs WHERE session_id = ? AND status IN ('queued', 'running') "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row:
                run_id = row["id"]
            else:
                return Response(
                    format_sse(StreamEvent.error("No active run found for this session")),
                    mimetype="text/event-stream",
                )

        event_store.get_queue(run_id)

        return Response(
            stream_with_context(sse_stream(run_id, event_store)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.route("/api/sessions/<session_id>/runs/<run_id>/stream", methods=["GET"])
    def stream_run(session_id: str, run_id: str):
        """Stream real-time events for a specific run."""
        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="stream_run")
        if error_response:
            return error_response

        row = db.execute(
            "SELECT id, status FROM runs WHERE id = ? AND session_id = ?",
            (run_id, session_id),
        ).fetchone()
        if not row:
            return Response(
                format_sse(StreamEvent.error("Run not found for this session")),
                mimetype="text/event-stream",
            )

        replay = request.args.get("replay", "0") in {"1", "true", "yes"}
        if replay:
            if row["status"] in ("queued", "running"):
                return Response(
                    format_sse(
                        StreamEvent.error(
                            "Replay is only available after run completion",
                            code="replay_unavailable",
                        )
                    ),
                    mimetype="text/event-stream",
                )

            events = app.event_logger.get_run_events(session_id, run_id)
            return Response(
                stream_with_context(replay_sse_stream(events)),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        event_store.get_queue(run_id)

        return Response(
            stream_with_context(sse_stream(run_id, event_store)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.route("/api/sessions/<session_id>/runs/<run_id>/events", methods=["GET"])
    def replay_run_events(session_id: str, run_id: str):
        """Return persisted run events for replay/debugging."""
        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="replay_run_events")
        if error_response:
            return error_response

        row = db.execute(
            "SELECT id FROM runs WHERE id = ? AND session_id = ?",
            (run_id, session_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "run not found"}), 404

        limit = request.args.get("limit", 1000, type=int)
        return jsonify(
            {
                "session_id": session_id,
                "run_id": run_id,
                "events": app.event_logger.get_run_events(session_id, run_id, limit=limit),
            }
        )
