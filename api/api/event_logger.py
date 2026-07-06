# Event Logger — lightweight audit trail for agent lifecycle events
#
# Bridges Phase 0A gap: structured event log for debugging and observability.
# Decoupled from transcript (messages are for the model; events are for ops).
#
# Events:
#   - run_started, run_completed, run_failed
#   - tool_called, tool_completed, tool_failed
#   - context_warning, rollover_triggered
#   - session_created, session_deleted
#
# Stored in SQLite `agent_events` table.

from __future__ import annotations

import json
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Any

from api.api.events import StreamEvent, stream_event_to_dict
from api.db.schema import new_id

logger = logging.getLogger(__name__)

STREAM_EVENT_PREFIX = "stream."

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

AGENT_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS agent_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT,
    event_type TEXT NOT NULL,
    payload TEXT,  -- JSON object with event-specific fields
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session ON agent_events (session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON agent_events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_run ON agent_events (run_id);
"""


def init_events_table(db: sqlite3.Connection) -> None:
    """Create the agent_events table and indexes."""
    db.executescript(AGENT_EVENTS_SQL)
    db.commit()


# ---------------------------------------------------------------------------
# Event Logger
# ---------------------------------------------------------------------------

class EventLogger:
    """Log agent lifecycle events for debugging and observability.

    Usage:
        logger = EventLogger(db)
        logger.log(session_id, "run_started", {"run_id": run_id})
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self._enabled = True

    def log(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> str:
        """Log an event. Returns the event id."""
        if not self._enabled:
            return ""

        event_id = new_id()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        self.db.execute(
            """INSERT INTO agent_events (id, session_id, run_id, event_type, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                session_id,
                run_id,
                event_type,
                json.dumps(payload or {}),
                now,
            ),
        )
        self.db.commit()
        return event_id

    # -- Convenience methods for common event types --

    def log_run_started(self, session_id: str, run_id: str, turn_number: int) -> str:
        return self.log(session_id, "run_started", {"run_id": run_id, "turn_number": turn_number}, run_id)

    def log_run_completed(self, session_id: str, run_id: str, duration_ms: int = 0) -> str:
        return self.log(session_id, "run_completed", {"duration_ms": duration_ms}, run_id)

    def log_run_failed(self, session_id: str, run_id: str, error: str) -> str:
        return self.log(session_id, "run_failed", {"error": error}, run_id)

    def log_tool_called(self, session_id: str, run_id: str, tool_id: str, input_data: dict | None = None) -> str:
        return self.log(session_id, "tool_called", {"tool_id": tool_id, "input": input_data or {}}, run_id)

    def log_tool_completed(self, session_id: str, run_id: str, tool_id: str, duration_ms: int = 0) -> str:
        return self.log(session_id, "tool_completed", {"tool_id": tool_id, "duration_ms": duration_ms}, run_id)

    def log_tool_failed(self, session_id: str, run_id: str, tool_id: str, error: str) -> str:
        return self.log(session_id, "tool_failed", {"tool_id": tool_id, "error": error}, run_id)

    def log_stream_event(
        self,
        session_id: str,
        run_id: str,
        event: StreamEvent | dict[str, Any],
    ) -> str:
        event_dict = stream_event_to_dict(event)
        return self.log(
            session_id,
            f"{STREAM_EVENT_PREFIX}{event_dict['type']}",
            event_dict.get("data", {}),
            run_id,
        )

    def log_context_warning(self, session_id: str, tokens_used: int, threshold: int) -> str:
        return self.log(session_id, "context_warning", {"tokens_used": tokens_used, "threshold": threshold})

    def log_rollover_triggered(self, session_id: str, reason: str = "threshold") -> str:
        return self.log(session_id, "rollover_triggered", {"reason": reason})

    def log_session_created(self, session_id: str, label: str = "", model: str = "") -> str:
        return self.log(session_id, "session_created", {"label": label, "model": model})

    def log_session_deleted(self, session_id: str) -> str:
        return self.log(session_id, "session_deleted")

    def log_worker_report(self, session_id: str, worker_name: str, workspace_id: str, summary: str, payload: dict | None = None) -> str:
        from api.api.events import EVENT_WORKER_REPORT
        return self.log(session_id, EVENT_WORKER_REPORT, {"worker_name": worker_name, "workspace_id": workspace_id, "summary": summary, **(payload or {})})

    def log_pastime_started(self, session_id: str, pastime_id: str, pastime_key: str, workspace_id: str) -> str:
        from api.api.events import EVENT_PASTIME_STARTED
        return self.log(session_id, EVENT_PASTIME_STARTED, {"pastime_id": pastime_id, "pastime_key": pastime_key, "workspace_id": workspace_id})

    def log_pastime_completed(self, session_id: str, pastime_id: str, pastime_key: str, workspace_id: str, produced_output: bool = False) -> str:
        from api.api.events import EVENT_PASTIME_COMPLETED
        return self.log(session_id, EVENT_PASTIME_COMPLETED, {"pastime_id": pastime_id, "pastime_key": pastime_key, "workspace_id": workspace_id, "produced_output": produced_output})

    def log_reflection_note(self, session_id: str, note: str, scope: str = "session", workspace_id: str = "") -> str:
        from api.api.events import EVENT_REFLECTION_NOTE
        return self.log(session_id, EVENT_REFLECTION_NOTE, {"note": note, "scope": scope, "workspace_id": workspace_id})

    def log_thread_candidate(self, session_id: str, title: str, thread_type: str, scope: str, workspace_id: str) -> str:
        from api.api.events import EVENT_THREAD_CANDIDATE
        return self.log(session_id, EVENT_THREAD_CANDIDATE, {"title": title, "thread_type": thread_type, "scope": scope, "workspace_id": workspace_id})

    def log_bridge_capture(self, session_id: str, capture_id: str, source: str, event_type: str) -> str:
        from api.api.events import EVENT_BRIDGE_CAPTURE
        return self.log(session_id, EVENT_BRIDGE_CAPTURE, {"capture_id": capture_id, "source": source, "event_type": event_type})

    def log_workspace_activated(self, session_id: str, workspace_id: str, workspace_name: str = "") -> str:
        from api.api.events import EVENT_WORKSPACE_ACTIVATED
        return self.log(session_id, EVENT_WORKSPACE_ACTIVATED, {"workspace_id": workspace_id, "workspace_name": workspace_name})

    def log_proposal_created(self, session_id: str, proposal_id: str, proposal_type: str = "") -> str:
        from api.api.events import EVENT_PROPOSAL_CREATED
        return self.log(session_id, EVENT_PROPOSAL_CREATED, {"proposal_id": proposal_id, "proposal_type": proposal_type})

    # -- Query --

    def get_events(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query events for a session."""
        conditions = []
        params: list = [session_id]

        if run_id:
            conditions.append("AND run_id = ?")
            params.append(run_id)
        if event_type:
            conditions.append("AND event_type = ?")
            params.append(event_type)

        sql = f"""SELECT id, session_id, run_id, event_type, payload, created_at
                  FROM agent_events WHERE session_id = ? {' '.join(conditions)}
                  ORDER BY created_at DESC, rowid DESC LIMIT ?"""
        params.append(limit)

        rows = self.db.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "run_id": r["run_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]) if r["payload"] else {},
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_session_events(
        self,
        session_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT id, session_id, run_id, event_type, payload, created_at
               FROM agent_events WHERE session_id = ?
               ORDER BY created_at ASC, rowid ASC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "run_id": r["run_id"],
                "type": r["event_type"],
                "data": json.loads(r["payload"]) if r["payload"] else {},
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_stream_events(
        self,
        session_id: str,
        run_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT id, run_id, event_type, payload, created_at
               FROM agent_events
               WHERE session_id = ? AND run_id = ? AND event_type LIKE ?
               ORDER BY created_at ASC, rowid ASC LIMIT ?""",
            (session_id, run_id, f"{STREAM_EVENT_PREFIX}%", limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "type": r["event_type"][len(STREAM_EVENT_PREFIX):],
                "data": json.loads(r["payload"]) if r["payload"] else {},
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_run_events(
        self,
        session_id: str,
        run_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT id, run_id, event_type, payload, created_at
               FROM agent_events
               WHERE session_id = ? AND run_id = ?
               ORDER BY created_at ASC, rowid ASC LIMIT ?""",
            (session_id, run_id, limit),
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event_type = row["event_type"]
            event_data = json.loads(row["payload"]) if row["payload"] else {}
            if event_type.startswith(STREAM_EVENT_PREFIX):
                event_type = event_type[len(STREAM_EVENT_PREFIX):]
            events.append(
                {
                    "id": row["id"],
                    "run_id": row["run_id"],
                    "type": event_type,
                    "data": event_data,
                    "created_at": row["created_at"],
                }
            )
        return events

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False
