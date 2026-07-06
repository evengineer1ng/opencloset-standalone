from __future__ import annotations

import json
import sqlite3
from typing import Any

from api.db.schema import new_id


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _string_field(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def normalize_attachments(raw_attachments: Any) -> list[dict[str, Any]]:
    if raw_attachments in (None, ""):
        return []
    if not isinstance(raw_attachments, list):
        raise ValueError("attachments must be a list of objects")

    normalized: list[dict[str, Any]] = []
    for index, attachment in enumerate(raw_attachments):
        if not isinstance(attachment, dict):
            raise ValueError(f"attachments[{index}] must be an object")
        attachment_type = str(attachment.get("type") or "text").strip().lower()
        if not attachment_type:
            raise ValueError(f"attachments[{index}].type must be a non-empty string")

        normalized_attachment: dict[str, Any] = {
            "type": attachment_type,
            "content": _string_field(attachment.get("content")),
            "media_path": _string_field(attachment.get("media_path") or attachment.get("media_url")),
            "description": _string_field(attachment.get("description") or attachment.get("label")),
        }

        for passthrough_key in ("source", "event_type", "capture_id", "attachment_id"):
            passthrough_value = attachment.get(passthrough_key)
            if passthrough_value is not None:
                normalized_attachment[passthrough_key] = _string_field(passthrough_value)

        metadata = attachment.get("metadata")
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise ValueError(f"attachments[{index}].metadata must be an object")
            normalized_attachment["metadata"] = dict(metadata)

        normalized.append(normalized_attachment)

    return normalized


def _capture_to_attachment(row: sqlite3.Row) -> dict[str, Any]:
    event_type = str(row["event_type"] or "text").strip().lower()
    metadata = _json_object(json.loads(row["metadata"]) if row["metadata"] else {})
    attachment_type = "capture"
    if event_type == "image":
        attachment_type = "image"
    elif event_type == "audio":
        attachment_type = "audio"
    elif event_type in {"text", "note"}:
        attachment_type = "text"

    description = str(metadata.get("title") or metadata.get("label") or metadata.get("summary") or row["source"] or "").strip()
    return {
        "type": attachment_type,
        "content": str(row["content"] or ""),
        "media_path": str(row["media_url"] or ""),
        "description": description,
        "source": str(row["source"] or "capture"),
        "event_type": event_type,
        "capture_id": str(row["id"]),
        "metadata": metadata,
    }


class RunInputManager:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def store(
        self,
        session_id: str,
        run_id: str,
        *,
        role: str = "user",
        attachments: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_attachments = normalize_attachments(attachments)
        normalized_metadata = dict(metadata or {})
        envelope_id = new_id()
        self.db.execute(
            """
            INSERT INTO run_input_envelopes (id, session_id, run_id, role, attachments, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                role = excluded.role,
                attachments = excluded.attachments,
                metadata = excluded.metadata,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                envelope_id,
                session_id,
                run_id,
                role,
                json.dumps(normalized_attachments),
                json.dumps(normalized_metadata),
            ),
        )
        return {
            "session_id": session_id,
            "run_id": run_id,
            "role": role,
            "attachments": normalized_attachments,
            "metadata": normalized_metadata,
        }

    def load(self, run_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT session_id, run_id, role, attachments, metadata FROM run_input_envelopes WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            return {
                "session_id": "",
                "run_id": run_id,
                "role": "user",
                "attachments": [],
                "metadata": {},
            }
        return {
            "session_id": str(row["session_id"] or ""),
            "run_id": str(row["run_id"] or run_id),
            "role": str(row["role"] or "user"),
            "attachments": normalize_attachments(json.loads(row["attachments"]) if row["attachments"] else []),
            "metadata": _json_object(json.loads(row["metadata"]) if row["metadata"] else {}),
        }

    def materialize_capture_attachments(
        self,
        *,
        session_id: str,
        workspace_id: str | None,
        capture_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not capture_ids:
            return []

        normalized_ids = [str(capture_id).strip() for capture_id in capture_ids if str(capture_id).strip()]
        if not normalized_ids:
            return []

        placeholders = ", ".join("?" for _ in normalized_ids)
        rows = self.db.execute(
            f"""
            SELECT id, workspace_id, session_id, source, event_type, content, media_url, metadata, status
            FROM captures
            WHERE id IN ({placeholders})
            """,
            tuple(normalized_ids),
        ).fetchall()
        if len(rows) != len(set(normalized_ids)):
            found_ids = {str(row["id"]) for row in rows}
            missing = sorted(set(normalized_ids) - found_ids)
            raise ValueError(f"unknown capture id(s): {', '.join(missing)}")

        attachments: list[dict[str, Any]] = []
        for row in rows:
            row_workspace_id = str(row["workspace_id"] or "") or None
            row_session_id = str(row["session_id"] or "") or None
            if workspace_id and row_workspace_id and row_workspace_id != workspace_id:
                raise ValueError(f"capture {row['id']} does not belong to this workspace")
            if row_session_id and row_session_id not in {session_id, None} and row_workspace_id != workspace_id:
                raise ValueError(f"capture {row['id']} does not belong to this session scope")
            attachments.append(_capture_to_attachment(row))
        return attachments

    def attach_captures_to_run(self, run_id: str, capture_ids: list[str]) -> None:
        normalized_ids = [str(capture_id).strip() for capture_id in capture_ids if str(capture_id).strip()]
        if not normalized_ids:
            return
        placeholders = ", ".join("?" for _ in normalized_ids)
        self.db.execute(
            f"""
            UPDATE captures
            SET run_id = ?,
                status = CASE WHEN status = 'pending' THEN 'routed' ELSE status END,
                processed_at = CASE WHEN status = 'pending' THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now') ELSE processed_at END
            WHERE id IN ({placeholders})
            """,
            (run_id, *normalized_ids),
        )