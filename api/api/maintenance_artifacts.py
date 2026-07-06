from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MICRO_SUMMARY_ARTIFACT = "micro-summary"
SESSION_SYNOPSIS_ARTIFACT = "session-synopsis"
SEGMENT_SUMMARY_ARTIFACT = "segment-summary"
COMPACTION_MARKER_ARTIFACT = "compaction-marker"
DECISION_TOOL_DIGEST_ARTIFACT = "decision-tool-digest"
HANDOFF_CANDIDATE_ARTIFACT = "handoff-candidate"
TRANSCRIPT_ARCHIVE_CANDIDATE_ARTIFACT = "transcript-archive-candidate"
VALID_ARTIFACT_STATUS = "valid"
STALE_ARTIFACT_STATUS = "stale"
DRAFT_ARTIFACT_STATUS = "draft"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS maintenance_artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'valid',
    content TEXT NOT NULL DEFAULT '',
    metadata TEXT,
    start_position INTEGER,
    end_position INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_maintenance_artifacts_session_type
ON maintenance_artifacts(session_id, artifact_type, status, created_at DESC);
"""


def init_maintenance_table(db) -> None:
    db.executescript(SCHEMA_SQL)
    db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_compaction_ranges(raw_ranges: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not raw_ranges:
        return []

    normalized_input: list[dict[str, Any]] = []
    for item in raw_ranges:
        start_position = int(item.get("start_position") or 0)
        end_position = int(item.get("end_position") or 0)
        if start_position < 1 or end_position < start_position:
            continue

        artifact_types: list[str] = []
        for artifact_type in item.get("artifact_types") or []:
            if artifact_type and artifact_type not in artifact_types:
                artifact_types.append(str(artifact_type))
        single_artifact_type = item.get("artifact_type")
        if single_artifact_type and single_artifact_type not in artifact_types:
            artifact_types.append(str(single_artifact_type))

        source_ranges: list[dict[str, Any]] = []
        for source_item in item.get("source_ranges") or []:
            source_start = int(source_item.get("start_position") or 0)
            source_end = int(source_item.get("end_position") or 0)
            if source_start < 1 or source_end < source_start:
                continue
            normalized_source = {
                "start_position": source_start,
                "end_position": source_end,
            }
            source_artifact_type = source_item.get("artifact_type")
            if source_artifact_type:
                normalized_source["artifact_type"] = str(source_artifact_type)
            source_artifact_id = source_item.get("artifact_id")
            if source_artifact_id:
                normalized_source["artifact_id"] = str(source_artifact_id)
            if normalized_source not in source_ranges:
                source_ranges.append(normalized_source)
        if not source_ranges:
            fallback_source = {
                "start_position": start_position,
                "end_position": end_position,
            }
            if len(artifact_types) == 1:
                fallback_source["artifact_type"] = artifact_types[0]
            normalized_source_id = item.get("artifact_id")
            if normalized_source_id:
                fallback_source["artifact_id"] = str(normalized_source_id)
            source_ranges.append(fallback_source)

        normalized_input.append(
            {
                "start_position": start_position,
                "end_position": end_position,
                "artifact_types": artifact_types,
                "source_ranges": source_ranges,
            }
        )

    normalized_input.sort(key=lambda item: (item["start_position"], item["end_position"]))
    merged: list[dict[str, Any]] = []
    for item in normalized_input:
        if not merged or item["start_position"] > merged[-1]["end_position"] + 1:
            merged.append(
                {
                    "start_position": item["start_position"],
                    "end_position": item["end_position"],
                    "artifact_types": list(item["artifact_types"]),
                    "source_ranges": list(item["source_ranges"]),
                }
            )
            continue

        merged_item = merged[-1]
        merged_item["end_position"] = max(merged_item["end_position"], item["end_position"])
        for artifact_type in item["artifact_types"]:
            if artifact_type not in merged_item["artifact_types"]:
                merged_item["artifact_types"].append(artifact_type)
        for source_range in item["source_ranges"]:
            if source_range not in merged_item["source_ranges"]:
                merged_item["source_ranges"].append(source_range)

    for item in merged:
        artifact_types = item.pop("artifact_types")
        item["source_ranges"] = sorted(
            item["source_ranges"],
            key=lambda source: (source["start_position"], source["end_position"], source.get("artifact_type", "")),
        )
        if len(artifact_types) == 1:
            item["artifact_type"] = artifact_types[0]
        elif artifact_types:
            item["artifact_types"] = artifact_types
    return merged


@dataclass
class MaintenanceArtifact:
    id: str
    session_id: str
    artifact_type: str
    status: str
    content: str
    metadata: dict[str, Any]
    start_position: int | None
    end_position: int | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "artifact_type": self.artifact_type,
            "status": self.status,
            "content": self.content,
            "metadata": self.metadata,
            "start_position": self.start_position,
            "end_position": self.end_position,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class MaintenancePollResult:
    checked_sessions: int
    created_artifacts: list[MaintenanceArtifact]


class MaintenanceManager:
    def __init__(self, db) -> None:
        self.db = db

    def list_artifacts(
        self,
        session_id: str,
        *,
        artifact_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conditions = ["session_id = ?"]
        params: list[Any] = [session_id]
        if artifact_type:
            conditions.append("artifact_type = ?")
            params.append(artifact_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        params.append(limit)
        rows = self.db.execute(
            f"""SELECT id, session_id, artifact_type, status, content, metadata,
                       start_position, end_position, created_at, updated_at
                FROM maintenance_artifacts
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?""",
            tuple(params),
        ).fetchall()
        return [self._row_to_artifact(row).to_dict() for row in rows]

    def get_latest_valid_artifact(self, session_id: str, artifact_type: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT id, session_id, artifact_type, status, content, metadata,
                      start_position, end_position, created_at, updated_at
               FROM maintenance_artifacts
               WHERE session_id = ? AND artifact_type = ? AND status = ?
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (session_id, artifact_type, VALID_ARTIFACT_STATUS),
        ).fetchone()
        return self._row_to_artifact(row).to_dict() if row else None

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT id, session_id, artifact_type, status, content, metadata,
                      start_position, end_position, created_at, updated_at
               FROM maintenance_artifacts
               WHERE id = ?""",
            (artifact_id,),
        ).fetchone()
        return self._row_to_artifact(row).to_dict() if row else None

    def get_valid_artifacts(self, session_id: str, artifact_type: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT id, session_id, artifact_type, status, content, metadata,
                      start_position, end_position, created_at, updated_at
               FROM maintenance_artifacts
               WHERE session_id = ? AND artifact_type = ? AND status = ?
               ORDER BY start_position ASC, end_position ASC, created_at ASC, rowid ASC
               LIMIT ?""",
            (session_id, artifact_type, VALID_ARTIFACT_STATUS, limit),
        ).fetchall()
        return [self._row_to_artifact(row).to_dict() for row in rows]

    def create_artifact(
        self,
        session_id: str,
        artifact_type: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        start_position: int | None = None,
        end_position: int | None = None,
        status: str = VALID_ARTIFACT_STATUS,
    ) -> dict[str, Any]:
        return self._insert_artifact(
            session_id,
            artifact_type,
            status=status,
            content=content,
            metadata=metadata,
            start_position=start_position,
            end_position=end_position,
        )

    def create_draft_artifact(
        self,
        session_id: str,
        artifact_type: str,
        *,
        content: str = "",
        metadata: dict[str, Any] | None = None,
        start_position: int | None = None,
        end_position: int | None = None,
    ) -> dict[str, Any]:
        return self._insert_artifact(
            session_id,
            artifact_type,
            status=DRAFT_ARTIFACT_STATUS,
            content=content,
            metadata=metadata,
            start_position=start_position,
            end_position=end_position,
        )

    def finalize_draft_artifact(
        self,
        artifact_id: str,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        start_position: int | None = None,
        end_position: int | None = None,
    ) -> dict[str, Any]:
        existing = self.get_artifact(artifact_id)
        if not existing:
            raise ValueError(f"Unknown maintenance artifact: {artifact_id}")
        if existing["status"] != DRAFT_ARTIFACT_STATUS:
            raise ValueError(f"Artifact {artifact_id} is not a draft")

        now = _now()
        self._stale_prior_valid_artifacts(
            existing["session_id"],
            existing["artifact_type"],
            now,
            start_position=start_position,
            end_position=end_position,
            exclude_artifact_id=artifact_id,
        )
        self.db.execute(
            """UPDATE maintenance_artifacts
               SET status = ?, content = ?, metadata = ?, start_position = ?, end_position = ?, updated_at = ?
               WHERE id = ?""",
            (
                VALID_ARTIFACT_STATUS,
                content,
                json.dumps(metadata or {}),
                start_position,
                end_position,
                now,
                artifact_id,
            ),
        )
        self.db.commit()
        artifact = self.get_artifact(artifact_id)
        if artifact is None:
            raise ValueError(f"Failed to reload maintenance artifact: {artifact_id}")
        return artifact

    def _insert_artifact(
        self,
        session_id: str,
        artifact_type: str,
        *,
        status: str,
        content: str,
        metadata: dict[str, Any] | None,
        start_position: int | None,
        end_position: int | None,
    ) -> dict[str, Any]:
        from api.db.schema import new_id

        artifact_id = new_id()
        now = _now()
        if status == VALID_ARTIFACT_STATUS:
            self._stale_prior_valid_artifacts(
                session_id,
                artifact_type,
                now,
                start_position=start_position,
                end_position=end_position,
            )
        elif status == DRAFT_ARTIFACT_STATUS:
            self.db.execute(
                """UPDATE maintenance_artifacts
                   SET status = ?, updated_at = ?
                   WHERE session_id = ? AND artifact_type = ? AND status = ?""",
                (STALE_ARTIFACT_STATUS, now, session_id, artifact_type, DRAFT_ARTIFACT_STATUS),
            )
        self.db.execute(
            """INSERT INTO maintenance_artifacts
               (id, session_id, artifact_type, status, content, metadata, start_position, end_position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id,
                session_id,
                artifact_type,
                status,
                content,
                json.dumps(metadata or {}),
                start_position,
                end_position,
                now,
                now,
            ),
        )
        self.db.commit()
        artifact = self.get_artifact(artifact_id)
        if artifact is None:
            raise ValueError(f"Failed to reload maintenance artifact: {artifact_id}")
        return artifact

    def _stale_prior_valid_artifacts(
        self,
        session_id: str,
        artifact_type: str,
        now: str,
        *,
        start_position: int | None = None,
        end_position: int | None = None,
        exclude_artifact_id: str | None = None,
    ) -> None:
        params: list[Any]
        if artifact_type == SEGMENT_SUMMARY_ARTIFACT and start_position is not None and end_position is not None:
            query = """UPDATE maintenance_artifacts
                       SET status = ?, updated_at = ?
                       WHERE session_id = ? AND artifact_type = ? AND status = ?
                         AND start_position IS NOT NULL AND end_position IS NOT NULL
                         AND NOT (end_position < ? OR start_position > ?)"""
            params = [
                STALE_ARTIFACT_STATUS,
                now,
                session_id,
                artifact_type,
                VALID_ARTIFACT_STATUS,
                start_position,
                end_position,
            ]
        else:
            query = """UPDATE maintenance_artifacts
                       SET status = ?, updated_at = ?
                       WHERE session_id = ? AND artifact_type = ? AND status = ?"""
            params = [
                STALE_ARTIFACT_STATUS,
                now,
                session_id,
                artifact_type,
                VALID_ARTIFACT_STATUS,
            ]
        if exclude_artifact_id:
            query += " AND id != ?"
            params.append(exclude_artifact_id)
        self.db.execute(query, tuple(params))

    @staticmethod
    def _row_to_artifact(row) -> MaintenanceArtifact:
        return MaintenanceArtifact(
            id=row["id"],
            session_id=row["session_id"],
            artifact_type=row["artifact_type"],
            status=row["status"],
            content=row["content"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            start_position=row["start_position"],
            end_position=row["end_position"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


__all__ = [
    "COMPACTION_MARKER_ARTIFACT",
    "DECISION_TOOL_DIGEST_ARTIFACT",
    "DRAFT_ARTIFACT_STATUS",
    "HANDOFF_CANDIDATE_ARTIFACT",
    "MICRO_SUMMARY_ARTIFACT",
    "SESSION_SYNOPSIS_ARTIFACT",
    "MaintenanceArtifact",
    "MaintenanceManager",
    "MaintenancePollResult",
    "SEGMENT_SUMMARY_ARTIFACT",
    "STALE_ARTIFACT_STATUS",
    "TRANSCRIPT_ARCHIVE_CANDIDATE_ARTIFACT",
    "VALID_ARTIFACT_STATUS",
    "_parse_timestamp",
    "init_maintenance_table",
    "normalize_compaction_ranges",
]
