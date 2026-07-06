from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from api.db.schema import new_id


TEXT_PREVIEW_BYTES = 4096
APK_MIME_TYPES = {
    "application/vnd.android.package-archive",
    "application/octet-stream",
}


def delete_session_attachment_files(db: sqlite3.Connection, session_id: str) -> None:
    rows = db.execute(
        "SELECT storage_path FROM session_attachments WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    for row in rows:
        storage_path = str(row["storage_path"] or "").strip()
        if not storage_path:
            continue
        try:
            Path(storage_path).unlink(missing_ok=True)
        except OSError:
            continue


class SessionAttachmentManager:
    def __init__(self, app) -> None:
        self.app = app
        self.db = app.db
        self.upload_root = Path(app.config["UPLOAD_ROOT"])
        self.upload_root.mkdir(parents=True, exist_ok=True)

    def store_upload(self, session_row, upload: FileStorage) -> dict[str, Any]:
        attachment_id = new_id()
        attachment_type = self._infer_attachment_type(upload)
        original_name = secure_filename(upload.filename or "") or f"attachment-{attachment_id}"
        storage_path = self._allocate_storage_path(session_row["id"], attachment_id, original_name)
        upload.save(storage_path)
        size_bytes = storage_path.stat().st_size if storage_path.exists() else 0
        mime_type = str(upload.mimetype or "application/octet-stream")
        preview_text = self._build_preview(storage_path, attachment_type, original_name, mime_type, size_bytes)
        metadata = {
            "attachment_id": attachment_id,
            "file_name": original_name,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "artifact_kind": self._infer_artifact_kind(original_name, mime_type),
            "preview_text": preview_text,
            "delivery": {"status": "pending"},
        }

        capture_id = None
        if session_row["workspace_id"]:
            capture_id = self.app.workspaces.create_workspace_capture(
                str(session_row["workspace_id"]),
                source="chat_upload",
                event_type=attachment_type,
                content=preview_text,
                media_url=str(storage_path),
                metadata=metadata,
                session_id=str(session_row["id"]),
                build_project_id=str(session_row["build_project_id"] or "") or None,
                status="pending",
            )

        self.db.execute(
            """
            INSERT INTO session_attachments (
                id, session_id, workspace_id, build_project_id, capture_id, attachment_type,
                file_name, mime_type, size_bytes, storage_path, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attachment_id,
                str(session_row["id"]),
                str(session_row["workspace_id"] or "") or None,
                str(session_row["build_project_id"] or "") or None,
                capture_id,
                attachment_type,
                original_name,
                mime_type,
                size_bytes,
                str(storage_path),
                json.dumps(metadata),
            ),
        )

        return self._build_attachment_payload(
            session_id=str(session_row["id"]),
            workspace_id=str(session_row["workspace_id"] or "") or None,
            build_project_id=str(session_row["build_project_id"] or "") or None,
            attachment_id=attachment_id,
            capture_id=capture_id,
            attachment_type=attachment_type,
            file_name=original_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            metadata=metadata,
            created_at=None,
        )

    def list_attachments(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT id, session_id, workspace_id, build_project_id, capture_id, attachment_type,
                   file_name, mime_type, size_bytes, metadata, created_at
            FROM session_attachments
            WHERE session_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (session_id,),
        ).fetchall()
        return [self._row_to_attachment_payload(row) for row in rows]

    def get_attachment(self, session_id: str, attachment_id: str) -> dict[str, Any] | None:
        row = self.get_attachment_record(session_id, attachment_id)
        return self._row_to_attachment_payload(row) if row else None

    def get_attachment_record(self, session_id: str, attachment_id: str):
        return self.db.execute(
            """
            SELECT id, session_id, workspace_id, build_project_id, capture_id, attachment_type,
                   file_name, mime_type, size_bytes, storage_path, metadata, created_at
            FROM session_attachments
            WHERE session_id = ? AND id = ?
            """,
            (session_id, attachment_id),
        ).fetchone()

    def update_delivery_status(
        self,
        session_id: str,
        attachment_id: str,
        *,
        status: str,
        device_id: str | None = None,
        note: str | None = None,
        metadata_update: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = self.get_attachment_record(session_id, attachment_id)
        if not row:
            return None

        metadata = self._load_metadata(row["metadata"])
        delivery = dict(metadata.get("delivery") or {})
        delivery["status"] = status
        delivery["updated_at"] = self._now()
        if device_id:
            delivery["device_id"] = device_id
        if note:
            delivery["note"] = note
        if metadata_update:
            delivery.update(metadata_update)
        metadata["delivery"] = delivery

        self.db.execute(
            "UPDATE session_attachments SET metadata = ? WHERE id = ?",
            (json.dumps(metadata), attachment_id),
        )

        workspace_id = str(row["workspace_id"] or "")
        capture_id = str(row["capture_id"] or "")
        if workspace_id and capture_id:
            capture = self.app.workspaces.get_workspace_capture(workspace_id, capture_id)
            if capture:
                capture_metadata = dict(capture.get("metadata") or {})
                capture_metadata["delivery"] = delivery
                self.app.workspaces.update_workspace_capture(
                    workspace_id,
                    capture_id,
                    metadata=capture_metadata,
                    status=status,
                    processed_at=delivery["updated_at"],
                )

        return self.get_attachment(session_id, attachment_id)

    def _allocate_storage_path(self, session_id: str, attachment_id: str, file_name: str) -> Path:
        extension = Path(file_name).suffix
        session_dir = self.upload_root / str(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir / f"{attachment_id}{extension}"

    def _row_to_attachment_payload(self, row) -> dict[str, Any]:
        metadata = self._load_metadata(row["metadata"])
        return self._build_attachment_payload(
            session_id=str(row["session_id"]),
            workspace_id=str(row["workspace_id"] or "") or None,
            build_project_id=str(row["build_project_id"] or "") or None,
            attachment_id=str(row["id"]),
            capture_id=str(row["capture_id"] or "") or None,
            attachment_type=str(row["attachment_type"] or "file"),
            file_name=str(row["file_name"] or "attachment"),
            mime_type=str(row["mime_type"] or "application/octet-stream"),
            size_bytes=int(row["size_bytes"] or 0),
            metadata=metadata,
            created_at=str(row["created_at"] or "") or None,
        )

    def _build_attachment_payload(
        self,
        *,
        session_id: str,
        workspace_id: str | None,
        build_project_id: str | None,
        attachment_id: str,
        capture_id: str | None,
        attachment_type: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        metadata: dict[str, Any],
        created_at: str | None,
    ) -> dict[str, Any]:
        urls = self._attachment_urls(session_id, attachment_id)
        return {
            "attachment_id": attachment_id,
            "capture_id": capture_id,
            "session_id": session_id,
            "workspace_id": workspace_id,
            "build_project_id": build_project_id,
            "type": attachment_type,
            "description": file_name,
            "content": str(metadata.get("preview_text") or ""),
            "media_path": urls["download_url"],
            "attachment_url": urls["attachment_url"],
            "download_url": urls["download_url"],
            "delivery_url": urls["delivery_url"],
            "file_name": file_name,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "created_at": created_at,
            "metadata": metadata,
        }

    @staticmethod
    def _attachment_urls(session_id: str, attachment_id: str) -> dict[str, str]:
        base_path = f"/api/sessions/{session_id}/attachments/{attachment_id}"
        return {
            "attachment_url": base_path,
            "download_url": f"{base_path}/download",
            "delivery_url": f"{base_path}/delivery",
        }

    @staticmethod
    def _infer_artifact_kind(file_name: str, mime_type: str) -> str:
        extension = Path(file_name).suffix.lower()
        normalized_mime = str(mime_type or "").lower()
        if extension == ".apk" or normalized_mime in APK_MIME_TYPES and extension == ".apk":
            return "apk"
        return "binary"

    @staticmethod
    def _load_metadata(raw_metadata: Any) -> dict[str, Any]:
        if isinstance(raw_metadata, dict):
            return dict(raw_metadata)
        if not raw_metadata:
            return {}
        try:
            loaded = json.loads(raw_metadata)
        except (TypeError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _infer_attachment_type(upload: FileStorage) -> str:
        mime_type = str(upload.mimetype or "").lower()
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("audio/"):
            return "audio"
        return "file"

    @staticmethod
    def _build_preview(storage_path: Path, attachment_type: str, file_name: str, mime_type: str, size_bytes: int) -> str:
        if attachment_type == "file" and SessionAttachmentManager._is_text_like(file_name, mime_type):
            try:
                with storage_path.open("r", encoding="utf-8", errors="replace") as handle:
                    preview = handle.read(TEXT_PREVIEW_BYTES)
            except OSError:
                preview = ""
            compact = preview.strip()
            return compact if compact else f"{file_name} ({mime_type or 'text/plain'}, {size_bytes} bytes)"
        return f"{file_name} ({mime_type or 'application/octet-stream'}, {size_bytes} bytes)"

    @staticmethod
    def _is_text_like(file_name: str, mime_type: str) -> bool:
        if mime_type.startswith("text/"):
            return True
        extension = Path(file_name).suffix.lower().lstrip(".")
        return extension in {"txt", "md", "json", "csv", "ts", "tsx", "js", "jsx", "py", "yml", "yaml", "html", "css"}