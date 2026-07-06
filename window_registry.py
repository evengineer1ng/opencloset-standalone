from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storage import ClosetStorage, TransientHistoryRecord, TransientWindowRecord


@dataclass
class TransientWindow:
    """In-memory representation of a transient window."""
    id: str
    title: str
    source_type: str  # "generated_html", "captured", "imported"
    native_type: str | None = None  # e.g. "iframe", "webview"
    html: str | None = None
    status: str = "open"  # "open", "closed"
    version: int = 1
    pinned: bool = False
    session_id: str | None = None
    message_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


class WindowRegistry:
    """Manages transient windows backed by ClosetStorage."""

    def __init__(self, storage: ClosetStorage):
        self._storage = storage

    def create_window(
        self,
        title: str,
        source_type: str,
        html: str | None = None,
        native_type: str | None = None,
        session_id: str | None = None,
        message_id: str | None = None,
    ) -> TransientWindow:
        """Create a new transient window and persist it."""
        record = self._storage.create_transient_window(
            title=title,
            source_type=source_type,
            html=html,
            native_type=native_type,
            session_id=session_id,
            message_id=message_id,
        )
        return self._record_to_window(record)

    def get_window(self, window_id: str) -> TransientWindow | None:
        """Retrieve a window by ID."""
        record = self._storage.get_transient_window(window_id)
        if record is None:
            return None
        return self._record_to_window(record)

    def update_window(
        self,
        window_id: str,
        title: str | None = None,
        html: str | None = None,
        pinned: bool | None = None,
    ) -> TransientWindow | None:
        """Update window fields. Returns updated window or None if not found."""
        record = self._storage.update_transient_window(
            window_id=window_id,
            title=title,
            html=html,
            pinned=pinned,
        )
        if record is None:
            return None
        return self._record_to_window(record)

    def mutate_html(
        self, window_id: str, new_html: str, note: str | None = None
    ) -> TransientWindow | None:
        """Replace HTML content, increment version, log history entry."""
        record = self._storage.mutate_transient_window(
            window_id=window_id,
            new_html=new_html,
            note=note,
        )
        if record is None:
            return None
        return self._record_to_window(record)

    def close_window(self, window_id: str) -> bool:
        """Mark window as closed. Returns True if window existed."""
        existing = self._storage.get_transient_window(window_id)
        if existing is None:
            return False
        self._storage.close_transient_window(window_id)
        return True

    def pin_window(self, window_id: str) -> TransientWindow | None:
        """Pin a window."""
        return self.update_window(window_id, pinned=True)

    def unpin_window(self, window_id: str) -> TransientWindow | None:
        """Unpin a window."""
        return self.update_window(window_id, pinned=False)

    def list_windows(
        self, session_id: str | None = None, status: str | None = None
    ) -> list[TransientWindow]:
        """List windows, optionally filtered by session or status."""
        records = self._storage.list_transient_windows(
            session_id=session_id, status=status
        )
        return [self._record_to_window(r) for r in records]

    def get_history(self, window_id: str) -> list[TransientHistoryRecord]:
        """Get mutation history for a window."""
        return self._storage.get_transient_window_history(window_id)

    def export_html(self, window_id: str) -> str | None:
        """Export current HTML content of a window."""
        record = self._storage.get_transient_window(window_id)
        if record is None or record.html is None:
            return None
        return record.html

    def cleanup_closed_windows(self, older_than_hours: int = 24) -> int:
        """Remove closed windows older than specified hours."""
        return self._storage.cleanup_closed_transient_windows(older_than_hours)

    # -- internal helpers --

    @staticmethod
    def _record_to_window(record: TransientWindowRecord) -> TransientWindow:
        return TransientWindow(
            id=record.id,
            title=record.title,
            source_type=record.source_type,
            native_type=record.native_type,
            html=record.html,
            status=record.status,
            version=record.version,
            pinned=bool(record.pinned),
            session_id=record.session_id,
            message_id=record.message_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
