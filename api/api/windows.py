# Transient Windows — backend manager and REST routes

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from api.api.session_validation import build_sqlite_param_error_payload, validate_session_route_scope
import logging
import sqlite3


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TransientWindowManager:
    """CRUD for transient_windows, versions, and history."""

    def __init__(self, db):
        self._db = db

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(
        self,
        session_id: str,
        title: str,
        html: str,
        *,
        source_type: str = "generated",
        native_type: str | None = None,
        payload: dict | None = None,
        capabilities: dict | None = None,
        summary: str = "",
    ) -> dict:
        wid = uuid.uuid4().hex
        now = self._now()
        caps = json.dumps(capabilities or {"network": False, "toolBridge": False, "storage": False, "media": False})
        flags = json.dumps({"pinned": False, "minimized": False, "stale": False, "saved": False})
        self._db.execute(
            """INSERT INTO transient_windows
               (id, session_id, title, source_type, native_type, html, payload, capabilities, state_flags,
                mutable, version, summary, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)""",
            (wid, session_id, title, source_type, native_type, html, json.dumps(payload) if payload is not None else None, caps, flags, summary, now, now),
        )
        self._db.commit()
        # Snapshot version 1
        self._db.execute(
            "INSERT INTO transient_window_versions (id, window_id, version, html, created_at) VALUES (?, ?, 1, ?, ?)",
            (uuid.uuid4().hex, wid, html, now),
        )
        self._db.commit()
        return self._get_record(wid)

    def get(self, window_id: str) -> dict | None:
        return self._get_record(window_id)

    def list_for_session(self, session_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM transient_windows WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update(
        self,
        window_id: str,
        *,
        html: str | None = None,
        title: str | None = None,
        payload: dict | None = None,
        state_flags: dict | None = None,
    ) -> dict | None:
        now = self._now()
        if html is not None:
            row = self._db.execute(
                "SELECT version FROM transient_windows WHERE id = ?", (window_id,)
            ).fetchone()
            if not row:
                return None
            new_version = row["version"] + 1
            self._db.execute(
                "UPDATE transient_windows SET html = ?, version = ?, updated_at = ? WHERE id = ?",
                (html, new_version, now, window_id),
            )
            self._db.commit()
            # Snapshot, prune old versions beyond 5
            self._db.execute(
                "INSERT INTO transient_window_versions (id, window_id, version, html, created_at) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, window_id, new_version, html, now),
            )
            self._db.execute(
                """DELETE FROM transient_window_versions WHERE window_id = ? AND id NOT IN (
                    SELECT id FROM transient_window_versions WHERE window_id = ?
                    ORDER BY version DESC LIMIT 5
                )""",
                (window_id, window_id),
            )
            self._db.commit()
        if title is not None:
            self._db.execute(
                "UPDATE transient_windows SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, window_id),
            )
            self._db.commit()
        if payload is not None:
            self._db.execute(
                "UPDATE transient_windows SET payload = ?, updated_at = ? WHERE id = ?",
                (json.dumps(payload), now, window_id),
            )
            self._db.commit()
        if state_flags is not None:
            existing = self._db.execute(
                "SELECT state_flags FROM transient_windows WHERE id = ?", (window_id,)
            ).fetchone()
            if existing:
                merged = {**json.loads(existing["state_flags"]), **state_flags}
                self._db.execute(
                    "UPDATE transient_windows SET state_flags = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(merged), now, window_id),
                )
                self._db.commit()
        return self._get_record(window_id)

    def close(self, window_id: str) -> bool:
        row = self._get_record(window_id)
        if not row:
            return False
        now = self._now()
        self._db.execute(
            "INSERT INTO transient_window_history (id, window_id, session_id, title, summary, created_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, window_id, row["session_id"], row["title"], row.get("summary", ""), row["created_at"], now),
        )
        self._db.execute("DELETE FROM transient_windows WHERE id = ?", (window_id,))
        self._db.commit()
        return True

    def history(self, session_id: str, limit: int = 20) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM transient_window_history WHERE session_id = ? ORDER BY closed_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_record(self, window_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT * FROM transient_windows WHERE id = ?", (window_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        d["capabilities"] = json.loads(d.get("capabilities") or "{}")
        d["state_flags"] = json.loads(d.get("state_flags") or "{}")
        d["payload"] = json.loads(d.get("payload") or "null")
        return d


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def init_transient_windows_table(db) -> None:
    """No-op: tables are created by schema.py SCHEMA_SQL on init_db()."""
    pass


def register_window_routes(app: Flask) -> None:

    @app.route("/api/sessions/<session_id>/windows", methods=["GET"])
    def list_windows(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="list_windows")
        if error_response:
            return error_response
        try:
            return jsonify(app.windows.list_for_session(session_id))
        except sqlite3.Error as exc:
            logger.exception("list_windows failed", extra={"session_id_preview": session_id[:120]})
            return jsonify(build_sqlite_param_error_payload(exc, field_name="session_id", route_name="list_windows", raw_value=session_id)), 400

    @app.route("/api/sessions/<session_id>/windows/history", methods=["GET"])
    def window_history(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="window_history")
        if error_response:
            return error_response
        limit = int(request.args.get("limit", 20))
        try:
            return jsonify(app.windows.history(session_id, limit=limit))
        except sqlite3.Error as exc:
            logger.exception("window_history failed", extra={"session_id_preview": session_id[:120], "limit": limit})
            return jsonify(build_sqlite_param_error_payload(exc, field_name="session_id", route_name="window_history", raw_value=session_id)), 400

    @app.route("/api/sessions/<session_id>/windows", methods=["POST"])
    def create_window(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="create_window")
        if error_response:
            return error_response
        body = request.get_json(force=True) or {}
        html = body.get("html", "")
        title = body.get("title", "Generated Window")
        summary = body.get("summary", "")
        capabilities = body.get("capabilities")
        source_type = body.get("source_type", "generated")
        native_type = body.get("native_type")
        payload = body.get("payload")
        try:
            record = app.windows.create(
                session_id,
                title,
                html,
                source_type=source_type,
                native_type=native_type,
                payload=payload,
                summary=summary,
                capabilities=capabilities,
            )
        except sqlite3.Error as exc:
            logger.exception(
                "create_window failed",
                extra={"session_id_preview": session_id[:120], "title_preview": str(title)[:120], "source_type": source_type},
            )
            return jsonify(build_sqlite_param_error_payload(exc, field_name="session_id", route_name="create_window", raw_value=session_id)), 400
        return jsonify(record), 201

    @app.route("/api/windows/<window_id>", methods=["GET"])
    def get_window(window_id: str):
        record = app.windows.get(window_id)
        if not record:
            return jsonify({"error": "not found"}), 404
        return jsonify(record)

    @app.route("/api/windows/<window_id>", methods=["PATCH"])
    def patch_window(window_id: str):
        body = request.get_json(force=True) or {}
        updated = app.windows.update(
            window_id,
            html=body.get("html"),
            title=body.get("title"),
            payload=body.get("payload"),
            state_flags=body.get("state_flags"),
        )
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify(updated)

    @app.route("/api/windows/<window_id>", methods=["DELETE"])
    def delete_window(window_id: str):
        ok = app.windows.close(window_id)
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})
