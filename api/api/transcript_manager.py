from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any

from api.db.schema import new_id

logger = logging.getLogger(__name__)

TRANSCRIPT_RANGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transcript_ranges (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    range_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    start_position INTEGER NOT NULL,
    end_position INTEGER NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_transcript_ranges_session_type
ON transcript_ranges(session_id, range_type, status, start_position, end_position);
"""

TRANSCRIPT_MESSAGE_STATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transcript_message_states (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    state_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE INDEX IF NOT EXISTS idx_transcript_message_states_session_type
ON transcript_message_states(session_id, state_type, status, message_id);
"""

ARCHIVE_READY_RANGE_TYPE = "archive-ready"
SUMMARY_COVERED_RANGE_TYPE = "summary-covered"
ACTIVE_RANGE_STATUS = "active"
STALE_RANGE_STATUS = "stale"
ARCHIVE_READY_MESSAGE_STATE_TYPE = "archive-ready"
SUMMARY_COVERED_MESSAGE_STATE_TYPE = "summary-covered"


def init_transcript_range_table(db: sqlite3.Connection) -> None:
    db.executescript(TRANSCRIPT_RANGE_SCHEMA_SQL)
    db.commit()


def init_transcript_message_state_table(db: sqlite3.Connection) -> None:
    db.executescript(TRANSCRIPT_MESSAGE_STATE_SCHEMA_SQL)
    db.commit()


@dataclass
class TranscriptEvent:
    """A single event to be persisted to the transcript."""

    session_id: str
    run_id: str
    role: str
    content: str
    token_estimate: int = 0
    persistent: bool = True
    metadata: dict[str, Any] | None = None


class TranscriptManager:
    """Single authoritative writer for transcript persistence.

    Owns transcript row ordering, token tracking, and tool-result mirroring so
    callers do not write directly to `messages`.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self._lock = threading.Lock()
        init_transcript_range_table(self.db)
        init_transcript_message_state_table(self.db)

    def _persist(self, event: TranscriptEvent) -> str:
        """Persist one transcript event under the manager lock."""
        msg_id = new_id()
        row = self.db.execute(
            "SELECT COALESCE(MAX(position), 0) AS pos FROM messages WHERE session_id = ?",
            (event.session_id,),
        ).fetchone()
        position = row["pos"] + 1

        self.db.execute(
            """INSERT INTO messages
               (id, session_id, run_id, role, content, position, token_estimate, persistent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id,
                event.session_id,
                event.run_id,
                event.role,
                event.content,
                position,
                event.token_estimate,
                1 if event.persistent else 0,
            ),
        )
        self.db.execute(
            "UPDATE sessions SET token_count = token_count + ? WHERE id = ?",
            (event.token_estimate, event.session_id),
        )
        return msg_id

    def update_token_count(self, session_id: str, delta: int) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE sessions SET token_count = token_count + ? WHERE id = ?",
                (delta, session_id),
            )
            self.db.commit()

    def get_token_count(self, session_id: str) -> int:
        row = self.db.execute(
            "SELECT token_count FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return row["token_count"] if row else 0

    def submit_user_message(
        self,
        session_id: str,
        run_id: str,
        content: str,
        role: str = "user",
        token_estimate: int = 0,
    ) -> str:
        if token_estimate == 0:
            token_estimate = max(1, len(content) // 4)

        with self._lock:
            msg_id = self._persist(
                TranscriptEvent(
                    session_id=session_id,
                    run_id=run_id,
                    role=role,
                    content=content,
                    token_estimate=token_estimate,
                    persistent=True,
                )
            )
            self.db.commit()
            return msg_id

    def commit_assistant_message(
        self,
        session_id: str,
        run_id: str,
        content: str,
        token_estimate: int = 0,
        *,
        persistent: bool = True,
    ) -> str:
        if token_estimate == 0:
            token_estimate = max(1, len(content) // 3)

        with self._lock:
            msg_id = self._persist(
                TranscriptEvent(
                    session_id=session_id,
                    run_id=run_id,
                    role="assistant",
                    content=content,
                    token_estimate=token_estimate,
                    persistent=persistent,
                )
            )
            self.db.commit()
            return msg_id

    def log_tool_invocation(
        self,
        session_id: str,
        run_id: str,
        tool_name: str,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        *,
        status: str = "completed",
        error: str | None = None,
        transcript_persistent: bool = True,
    ) -> str:
        """Persist tool invocation audit row and transcript representation.

        Returns the tool invocation id.
        """
        payload: dict[str, Any] = {
            "tool_name": tool_name,
            "input": input_data or {},
            "status": status,
        }
        if output_data is not None:
            payload["output"] = output_data
        if error is not None:
            payload["error"] = error

        content = json.dumps(payload)
        token_estimate = max(1, len(content) // 4)

        with self._lock:
            self._persist(
                TranscriptEvent(
                    session_id=session_id,
                    run_id=run_id,
                    role="tool",
                    content=content,
                    token_estimate=token_estimate,
                    persistent=transcript_persistent,
                    metadata=payload,
                )
            )

            invocation_id = new_id()
            now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            self.db.execute(
                f"""INSERT INTO tool_invocations
                    (id, session_id, run_id, tool_name, input, output, status, error, started_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, {now_sql}, {now_sql})""",
                (
                    invocation_id,
                    session_id,
                    run_id,
                    tool_name,
                    json.dumps(input_data or {}),
                    json.dumps(output_data) if output_data is not None else None,
                    status,
                    error,
                ),
            )
            self.db.commit()
            return invocation_id

    def log_system_event(
        self,
        session_id: str,
        run_id: str,
        content: str,
        token_estimate: int = 0,
    ) -> str:
        with self._lock:
            msg_id = self._persist(
                TranscriptEvent(
                    session_id=session_id,
                    run_id=run_id,
                    role="system",
                    content=content,
                    token_estimate=token_estimate,
                    persistent=True,
                )
            )
            self.db.commit()
            return msg_id

    def get_messages(
        self,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        role_filter: str | None = None,
    ) -> list[dict]:
        if role_filter:
            rows = self.db.execute(
                "SELECT id, role, content, position, token_estimate, created_at "
                "FROM messages WHERE session_id = ? AND role = ? "
                "ORDER BY position ASC LIMIT ? OFFSET ?",
                (session_id, role_filter, limit, offset),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, role, content, position, token_estimate, created_at "
                "FROM messages WHERE session_id = ? "
                "ORDER BY position ASC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()

        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "position": r["position"],
                "token_estimate": r["token_estimate"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_message_count(self, session_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["cnt"]

    def get_transcript(self, session_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, role, content, position, token_estimate, created_at "
            "FROM messages WHERE session_id = ? ORDER BY position ASC",
            (session_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "position": r["position"],
                "token_estimate": r["token_estimate"],
            }
            for r in rows
        ]

    def list_message_states(
        self,
        session_id: str,
        *,
        state_type: str | None = None,
        status: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        conditions = ["session_id = ?"]
        params: list[Any] = [session_id]
        if state_type:
            conditions.append("state_type = ?")
            params.append(state_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        params.append(limit)
        rows = self.db.execute(
            f"""SELECT id, session_id, message_id, state_type, status, metadata, created_at, updated_at
                FROM transcript_message_states
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at ASC, rowid ASC
                LIMIT ?""",
            tuple(params),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "message_id": row["message_id"],
                "state_type": row["state_type"],
                "status": row["status"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_ranges(
        self,
        session_id: str,
        *,
        range_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        conditions = ["session_id = ?"]
        params: list[Any] = [session_id]
        if range_type:
            conditions.append("range_type = ?")
            params.append(range_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        params.append(limit)
        rows = self.db.execute(
            f"""SELECT id, session_id, range_type, status, start_position, end_position, metadata, created_at, updated_at
                FROM transcript_ranges
                WHERE {' AND '.join(conditions)}
                ORDER BY start_position ASC, end_position ASC, created_at ASC, rowid ASC
                LIMIT ?""",
            tuple(params),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "range_type": row["range_type"],
                "status": row["status"],
                "start_position": row["start_position"],
                "end_position": row["end_position"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def sync_archive_ready_ranges(
        self,
        session_id: str,
        ranges: list[dict[str, Any]],
        *,
        source_artifact_id: str | None = None,
    ) -> list[dict[str, Any]]:
        synced_ranges = self._sync_ranges(
            session_id,
            ARCHIVE_READY_RANGE_TYPE,
            ranges,
            source_artifact_id=source_artifact_id,
        )
        self._sync_message_states_for_range_type(
            session_id,
            ARCHIVE_READY_RANGE_TYPE,
            ARCHIVE_READY_MESSAGE_STATE_TYPE,
        )
        return synced_ranges

    def sync_summary_covered_ranges(
        self,
        session_id: str,
        ranges: list[dict[str, Any]],
        *,
        source_artifact_id: str | None = None,
    ) -> list[dict[str, Any]]:
        synced_ranges = self._sync_ranges(
            session_id,
            SUMMARY_COVERED_RANGE_TYPE,
            ranges,
            source_artifact_id=source_artifact_id,
        )
        self._sync_message_states_for_range_type(
            session_id,
            SUMMARY_COVERED_RANGE_TYPE,
            SUMMARY_COVERED_MESSAGE_STATE_TYPE,
        )
        return synced_ranges

    def _sync_ranges(
        self,
        session_id: str,
        range_type: str,
        ranges: list[dict[str, Any]],
        *,
        source_artifact_id: str | None = None,
    ) -> list[dict[str, Any]]:
        now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        normalized_ranges = []
        for item in ranges:
            start_position = int(item.get("start_position") or 0)
            end_position = int(item.get("end_position") or 0)
            if start_position < 1 or end_position < start_position:
                continue
            normalized_ranges.append(
                {
                    "start_position": start_position,
                    "end_position": end_position,
                    "metadata": {
                        "source_ranges": item.get("source_ranges") or [],
                        "source_artifact_id": source_artifact_id,
                    },
                }
            )

        with self._lock:
            active_ranges = self.list_ranges(
                session_id,
                range_type=range_type,
                status=ACTIVE_RANGE_STATUS,
                limit=1000,
            )
            stale_ids = []
            matched_ids: set[str] = set()
            for existing in active_ranges:
                existing_key = (
                    existing["start_position"],
                    existing["end_position"],
                    existing.get("metadata", {}).get("source_ranges") or [],
                )
                matched = False
                for item in normalized_ranges:
                    item_key = (
                        item["start_position"],
                        item["end_position"],
                        item["metadata"].get("source_ranges") or [],
                    )
                    if item_key == existing_key:
                        matched = True
                        matched_ids.add(existing["id"])
                        break
                if not matched:
                    stale_ids.append(existing["id"])

            for range_id in stale_ids:
                self.db.execute(
                    f"UPDATE transcript_ranges SET status = ?, updated_at = {now_sql} WHERE id = ?",
                    (STALE_RANGE_STATUS, range_id),
                )

            created = []
            for item in normalized_ranges:
                existing = next(
                    (
                        row for row in active_ranges
                        if row["start_position"] == item["start_position"]
                        and row["end_position"] == item["end_position"]
                        and (row.get("metadata", {}).get("source_ranges") or []) == (item["metadata"].get("source_ranges") or [])
                    ),
                    None,
                )
                if existing:
                    continue

                range_id = new_id()
                metadata_json = json.dumps(item["metadata"])
                self.db.execute(
                    f"""INSERT INTO transcript_ranges
                        (id, session_id, range_type, status, start_position, end_position, metadata, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, {now_sql}, {now_sql})""",
                    (
                        range_id,
                        session_id,
                        range_type,
                        ACTIVE_RANGE_STATUS,
                        item["start_position"],
                        item["end_position"],
                        metadata_json,
                    ),
                )
                created.append(range_id)
            self.db.commit()
        return self.list_ranges(session_id, range_type=range_type, limit=1000)

    def _sync_message_states_for_range_type(
        self,
        session_id: str,
        range_type: str,
        state_type: str,
    ) -> list[dict[str, Any]]:
        now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        with self._lock:
            active_ranges = self.list_ranges(
                session_id,
                range_type=range_type,
                status=ACTIVE_RANGE_STATUS,
                limit=1000,
            )
            target_states: dict[str, dict[str, Any]] = {}
            for transcript_range in active_ranges:
                metadata = transcript_range.get("metadata") or {}
                source_ranges = metadata.get("source_ranges") or []
                rows = self.db.execute(
                    """SELECT id, position FROM messages
                       WHERE session_id = ? AND position BETWEEN ? AND ?
                       ORDER BY position ASC""",
                    (
                        session_id,
                        transcript_range["start_position"],
                        transcript_range["end_position"],
                    ),
                ).fetchall()
                for row in rows:
                    target_states[row["id"]] = {
                        "range_id": transcript_range["id"],
                        "range_start": transcript_range["start_position"],
                        "range_end": transcript_range["end_position"],
                        "source_ranges": source_ranges,
                    }

            active_states = self.list_message_states(
                session_id,
                state_type=state_type,
                status=ACTIVE_RANGE_STATUS,
                limit=5000,
            )
            for state in active_states:
                target = target_states.get(state["message_id"])
                current_metadata = state.get("metadata") or {}
                if not target:
                    self.db.execute(
                        f"UPDATE transcript_message_states SET status = ?, updated_at = {now_sql} WHERE id = ?",
                        (STALE_RANGE_STATUS, state["id"]),
                    )
                    continue
                target_metadata = {
                    "range_id": target["range_id"],
                    "range_start": target["range_start"],
                    "range_end": target["range_end"],
                    "source_ranges": target["source_ranges"],
                }
                if current_metadata != target_metadata:
                    self.db.execute(
                        """UPDATE transcript_message_states
                           SET metadata = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                           WHERE id = ?""",
                        (json.dumps(target_metadata), state["id"]),
                    )

            active_by_message = {
                state["message_id"]: state
                for state in self.list_message_states(
                    session_id,
                    state_type=state_type,
                    status=ACTIVE_RANGE_STATUS,
                    limit=5000,
                )
            }
            for message_id, target in target_states.items():
                if message_id in active_by_message:
                    continue
                self.db.execute(
                    f"""INSERT INTO transcript_message_states
                        (id, session_id, message_id, state_type, status, metadata, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, {now_sql}, {now_sql})""",
                    (
                        new_id(),
                        session_id,
                        message_id,
                        state_type,
                        ACTIVE_RANGE_STATUS,
                        json.dumps(
                            {
                                "range_id": target["range_id"],
                                "range_start": target["range_start"],
                                "range_end": target["range_end"],
                                "source_ranges": target["source_ranges"],
                            }
                        ),
                    ),
                )
            self.db.commit()
        return self.list_message_states(
            session_id,
            state_type=state_type,
            limit=5000,
        )
