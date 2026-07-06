from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from storage import StorageManager, TransientWindowRecord


class WindowBridge:
    """Bridge between chat messages and transient windows.

    Detects generated HTML blocks in assistant messages, creates transient
    windows for them, and links them back to the originating message/session.
    """

    def __init__(self, storage: StorageManager):
        self._storage = storage

    def process_message_for_windows(
        self, session_id: str, message_id: str, content: str
    ) -> list[TransientWindowRecord]:
        """Scan message content for HTML blocks and create transient windows.

        Looks for content between <html>...</html> or between fenced code
        blocks marked as html (```html ... ```).
        """
        html_blocks = self._extract_html_blocks(content)
        windows: list[TransientWindowRecord] = []

        for idx, html in enumerate(html_blocks):
            title = f"Generated HTML #{idx + 1}"
            window = self._storage.create_transient_window(
                title=title,
                source_type="generated_html",
                native_type="iframe",
                html=html,
                session_id=session_id,
                message_id=message_id,
            )
            windows.append(window)

        return windows

    def _extract_html_blocks(self, content: str) -> list[str]:
        """Extract HTML blocks from message content.

        Handles:
        - Full <html>...</html> documents
        - Fenced ```html ... ``` blocks
        - Fenced ``` ... ``` blocks containing <html> or <div> at root
        """
        blocks: list[str] = []

        # Check for fenced html blocks
        import re

        # ```html ... ```
        fenced_html = re.findall(r"```html\n(.*?)```", content, re.DOTALL)
        blocks.extend(fenced_html)

        # ``` ... ``` containing <html> or starting with <
        fenced_generic = re.findall(r"```\n(.*?)```", content, re.DOTALL)
        for block in fenced_generic:
            stripped = block.strip()
            if stripped.startswith("<") and block not in fenced_html:
                blocks.append(block)

        # Bare <html>...</html> (not inside a fence)
        bare_html = re.findall(r"<html[^>]*>(.*?)</html>", content, re.DOTALL)
        for block in bare_html:
            full = f"<html>{block}</html>"
            if full not in blocks:
                blocks.append(full)

        return blocks

    def get_windows_for_message(
        self, message_id: str
    ) -> list[TransientWindowRecord]:
        """List all transient windows created from a specific message."""
        return self._storage.list_transient_windows(message_id=message_id)

    def get_windows_for_session(
        self, session_id: str, status: Optional[str] = None
    ) -> list[TransientWindowRecord]:
        """List all transient windows for a session."""
        return self._storage.list_transient_windows(
            session_id=session_id, status=status
        )
