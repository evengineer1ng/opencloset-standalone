from __future__ import annotations

import json
import sqlite3
from typing import Any

from flask import jsonify, request

from api.api.session_validation import validate_session_route_scope
from api.db.schema import new_id


BEHAVIOR_FEEDBACK_SIGNALS = {"up", "down", "promote"}
BEHAVIOR_PATCH_SCOPES = {"chat", "build_project", "workspace", "global"}


def init_behavior_tables(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS behavior_feedback_events (
            id               TEXT PRIMARY KEY,
            session_id       TEXT NOT NULL,
            workspace_id     TEXT,
            build_project_id TEXT,
            message_id       TEXT NOT NULL,
            signal           TEXT NOT NULL,
            message_preview  TEXT NOT NULL DEFAULT '',
            traits           TEXT NOT NULL DEFAULT '[]',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_feedback_session_message
        ON behavior_feedback_events(session_id, message_id);

        CREATE INDEX IF NOT EXISTS idx_behavior_feedback_workspace
        ON behavior_feedback_events(workspace_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_behavior_feedback_build_project
        ON behavior_feedback_events(build_project_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_behavior_feedback_session_created
        ON behavior_feedback_events(session_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS behavior_patches (
            id               TEXT PRIMARY KEY,
            session_id       TEXT NOT NULL,
            workspace_id     TEXT,
            build_project_id TEXT,
            scope            TEXT NOT NULL,
            scope_id         TEXT NOT NULL,
            rule_key         TEXT NOT NULL,
            title            TEXT NOT NULL DEFAULT '',
            patch_text       TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'active',
            created_by       TEXT NOT NULL DEFAULT 'user',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_patches_scope_rule_active
        ON behavior_patches(scope, scope_id, rule_key, status);

        CREATE INDEX IF NOT EXISTS idx_behavior_patches_session_created
        ON behavior_patches(session_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS behavior_proposal_dismissals (
            id               TEXT PRIMARY KEY,
            session_id       TEXT NOT NULL,
            rule_key         TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_dismissals_session_rule
        ON behavior_proposal_dismissals(session_id, rule_key);
        """
    )
    db.commit()


def _json_array_of_strings(raw_value: Any, field_name: str) -> list[str]:
    if raw_value in (None, ""):
        return []
    if not isinstance(raw_value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    result: list[str] = []
    for index, value in enumerate(raw_value):
        if not isinstance(value, str):
            raise ValueError(f"{field_name}[{index}] must be a string")
        trimmed = value.strip()
        if trimmed:
            result.append(trimmed)
    return result


class BehaviorManager:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _session_context(self, session_id: str) -> dict[str, str | None]:
        row = self.db.execute(
            "SELECT id, workspace_id, build_project_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            raise ValueError("session not found")
        return {
            "session_id": str(row["id"]),
            "workspace_id": str(row["workspace_id"] or "") or None,
            "build_project_id": str(row["build_project_id"] or "") or None,
        }

    @staticmethod
    def _loads_list(raw_value: Any) -> list[str]:
        try:
            loaded = json.loads(raw_value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(loaded, list):
            return []
        return [str(item) for item in loaded if str(item).strip()]

    def _feedback_row_to_record(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "session_id": str(row["session_id"]),
            "workspace_id": str(row["workspace_id"] or "") or None,
            "build_project_id": str(row["build_project_id"] or "") or None,
            "message_id": str(row["message_id"]),
            "signal": str(row["signal"]),
            "message_preview": str(row["message_preview"] or ""),
            "traits": self._loads_list(row["traits"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def _patch_row_to_record(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "session_id": str(row["session_id"]),
            "workspace_id": str(row["workspace_id"] or "") or None,
            "build_project_id": str(row["build_project_id"] or "") or None,
            "scope": str(row["scope"]),
            "scope_id": str(row["scope_id"]),
            "rule_key": str(row["rule_key"]),
            "title": str(row["title"] or ""),
            "patch": str(row["patch_text"] or ""),
            "status": str(row["status"]),
            "created_by": str(row["created_by"] or "user"),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_feedback(self, session_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        context = self._session_context(session_id)
        params: list[Any] = [session_id]
        clauses = ["session_id = ?"]

        build_project_id = context["build_project_id"]
        if build_project_id:
            clauses.append("build_project_id = ?")
            params.append(build_project_id)

        workspace_id = context["workspace_id"]
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)

        params.append(int(limit))
        rows = self.db.execute(
            f"""
            SELECT id, session_id, workspace_id, build_project_id, message_id, signal,
                   message_preview, traits, created_at, updated_at
            FROM behavior_feedback_events
            WHERE {' OR '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [self._feedback_row_to_record(row) for row in rows]

    def upsert_feedback(
        self,
        session_id: str,
        *,
        message_id: str,
        signal: str,
        message_preview: str,
        traits: list[str],
    ) -> dict[str, Any]:
        if signal not in BEHAVIOR_FEEDBACK_SIGNALS:
            raise ValueError(f"signal must be one of {tuple(sorted(BEHAVIOR_FEEDBACK_SIGNALS))}")
        if not message_id or not str(message_id).strip():
            raise ValueError("message_id is required")

        context = self._session_context(session_id)
        now = self._now()
        existing = self.db.execute(
            "SELECT id, created_at FROM behavior_feedback_events WHERE session_id = ? AND message_id = ?",
            (session_id, str(message_id).strip()),
        ).fetchone()
        feedback_id = str(existing["id"]) if existing else new_id()
        created_at = str(existing["created_at"]) if existing else now

        self.db.execute(
            """
            INSERT INTO behavior_feedback_events (
                id, session_id, workspace_id, build_project_id, message_id, signal,
                message_preview, traits, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, message_id) DO UPDATE SET
                signal = excluded.signal,
                message_preview = excluded.message_preview,
                traits = excluded.traits,
                workspace_id = excluded.workspace_id,
                build_project_id = excluded.build_project_id,
                updated_at = excluded.updated_at
            """,
            (
                feedback_id,
                session_id,
                context["workspace_id"],
                context["build_project_id"],
                str(message_id).strip(),
                signal,
                str(message_preview or ""),
                json.dumps(_json_array_of_strings(traits, "traits")),
                created_at,
                now,
            ),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT * FROM behavior_feedback_events WHERE session_id = ? AND message_id = ?",
            (session_id, str(message_id).strip()),
        ).fetchone()
        return self._feedback_row_to_record(row)

    def delete_feedback(self, session_id: str, message_id: str) -> bool:
        self._session_context(session_id)
        cursor = self.db.execute(
            "DELETE FROM behavior_feedback_events WHERE session_id = ? AND message_id = ?",
            (session_id, str(message_id).strip()),
        )
        self.db.commit()
        return bool(cursor.rowcount)

    def list_applicable_patches(self, session_id: str) -> list[dict[str, Any]]:
        context = self._session_context(session_id)
        scope_checks: list[tuple[str, Any]] = [("chat", session_id), ("global", "global")]
        if context["build_project_id"]:
            scope_checks.append(("build_project", context["build_project_id"]))
        if context["workspace_id"]:
            scope_checks.append(("workspace", context["workspace_id"]))

        clauses: list[str] = []
        params: list[Any] = []
        for scope, scope_id in scope_checks:
            clauses.append("(scope = ? AND scope_id = ?)")
            params.extend([scope, scope_id])

        rows = self.db.execute(
            f"""
            SELECT *
            FROM behavior_patches
            WHERE status = 'active' AND ({' OR '.join(clauses)})
            ORDER BY created_at ASC
            """,
            tuple(params),
        ).fetchall()
        return [self._patch_row_to_record(row) for row in rows]

    def apply_patch(
        self,
        session_id: str,
        *,
        rule_key: str,
        title: str,
        patch_text: str,
        scope: str,
        scope_id: str,
        created_by: str = "user",
    ) -> dict[str, Any]:
        if scope not in BEHAVIOR_PATCH_SCOPES:
            raise ValueError(f"scope must be one of {tuple(sorted(BEHAVIOR_PATCH_SCOPES))}")
        if not str(rule_key or "").strip():
            raise ValueError("rule_key is required")
        if not str(patch_text or "").strip():
            raise ValueError("patch is required")

        context = self._session_context(session_id)
        now = self._now()
        normalized_scope_id = str(scope_id or "").strip()
        if scope == "chat":
            normalized_scope_id = session_id
        elif scope == "build_project" and not normalized_scope_id:
            normalized_scope_id = str(context["build_project_id"] or "")
        elif scope == "workspace" and not normalized_scope_id:
            normalized_scope_id = str(context["workspace_id"] or "")
        elif scope == "global":
            normalized_scope_id = "global"
        if not normalized_scope_id:
            raise ValueError("scope_id is required for this scope")

        existing = self.db.execute(
            "SELECT id, created_at FROM behavior_patches WHERE scope = ? AND scope_id = ? AND rule_key = ? AND status = 'active'",
            (scope, normalized_scope_id, str(rule_key).strip()),
        ).fetchone()
        patch_id = str(existing["id"]) if existing else new_id()
        created_at = str(existing["created_at"]) if existing else now

        self.db.execute(
            """
            INSERT INTO behavior_patches (
                id, session_id, workspace_id, build_project_id, scope, scope_id, rule_key,
                title, patch_text, status, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT(scope, scope_id, rule_key, status) DO UPDATE SET
                title = excluded.title,
                patch_text = excluded.patch_text,
                session_id = excluded.session_id,
                workspace_id = excluded.workspace_id,
                build_project_id = excluded.build_project_id,
                created_by = excluded.created_by,
                updated_at = excluded.updated_at
            """,
            (
                patch_id,
                session_id,
                context["workspace_id"],
                context["build_project_id"],
                scope,
                normalized_scope_id,
                str(rule_key).strip(),
                str(title or ""),
                str(patch_text).strip(),
                str(created_by or "user"),
                created_at,
                now,
            ),
        )
        self.clear_dismissal(session_id, str(rule_key).strip())
        self.db.commit()
        row = self.db.execute(
            "SELECT * FROM behavior_patches WHERE scope = ? AND scope_id = ? AND rule_key = ? AND status = 'active'",
            (scope, normalized_scope_id, str(rule_key).strip()),
        ).fetchone()
        return self._patch_row_to_record(row)

    def list_dismissals(self, session_id: str) -> list[dict[str, Any]]:
        self._session_context(session_id)
        rows = self.db.execute(
            "SELECT session_id, rule_key, created_at, updated_at FROM behavior_proposal_dismissals WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [
            {
                "session_id": str(row["session_id"]),
                "rule_key": str(row["rule_key"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def dismiss_proposal(self, session_id: str, rule_key: str) -> dict[str, Any]:
        self._session_context(session_id)
        normalized_rule_key = str(rule_key or "").strip()
        if not normalized_rule_key:
            raise ValueError("rule_key is required")
        now = self._now()
        existing = self.db.execute(
            "SELECT id, created_at FROM behavior_proposal_dismissals WHERE session_id = ? AND rule_key = ?",
            (session_id, normalized_rule_key),
        ).fetchone()
        dismissal_id = str(existing["id"]) if existing else new_id()
        created_at = str(existing["created_at"]) if existing else now
        self.db.execute(
            """
            INSERT INTO behavior_proposal_dismissals (id, session_id, rule_key, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, rule_key) DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (dismissal_id, session_id, normalized_rule_key, created_at, now),
        )
        self.db.commit()
        return {
            "session_id": session_id,
            "rule_key": normalized_rule_key,
            "created_at": created_at,
            "updated_at": now,
        }

    def clear_dismissal(self, session_id: str, rule_key: str) -> None:
        self.db.execute(
            "DELETE FROM behavior_proposal_dismissals WHERE session_id = ? AND rule_key = ?",
            (session_id, rule_key),
        )

    def get_session_state(self, session_id: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "feedback": self.list_feedback(session_id),
            "patches": self.list_applicable_patches(session_id),
            "dismissals": self.list_dismissals(session_id),
        }


def register_behavior_routes(app) -> None:
    @app.route("/api/sessions/<session_id>/behavior-state", methods=["GET"])
    def get_behavior_state(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="get_behavior_state")
        if error_response:
            return error_response
        return jsonify(app.behavior.get_session_state(session_id))

    @app.route("/api/sessions/<session_id>/behavior-feedback/<message_id>", methods=["PUT"])
    def upsert_behavior_feedback(session_id: str, message_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="upsert_behavior_feedback")
        if error_response:
            return error_response

        data = request.get_json(silent=True) or {}
        try:
            record = app.behavior.upsert_feedback(
                session_id,
                message_id=str(message_id or "").strip(),
                signal=str(data.get("signal") or "").strip(),
                message_preview=str(data.get("message_preview") or ""),
                traits=_json_array_of_strings(data.get("traits"), "traits"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"feedback": record})

    @app.route("/api/sessions/<session_id>/behavior-feedback/<message_id>", methods=["DELETE"])
    def delete_behavior_feedback(session_id: str, message_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="delete_behavior_feedback")
        if error_response:
            return error_response
        deleted = app.behavior.delete_feedback(session_id, str(message_id or "").strip())
        return jsonify({"deleted": deleted, "message_id": str(message_id or "").strip()})

    @app.route("/api/sessions/<session_id>/behavior-patches", methods=["POST"])
    def apply_behavior_patch(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="apply_behavior_patch")
        if error_response:
            return error_response

        data = request.get_json(silent=True) or {}
        try:
            patch_record = app.behavior.apply_patch(
                session_id,
                rule_key=str(data.get("rule_key") or "").strip(),
                title=str(data.get("title") or ""),
                patch_text=str(data.get("patch") or "").strip(),
                scope=str(data.get("scope") or "").strip(),
                scope_id=str(data.get("scope_id") or "").strip(),
                created_by=str(data.get("created_by") or "user"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"patch": patch_record})

    @app.route("/api/sessions/<session_id>/behavior-proposals/<rule_key>/dismiss", methods=["POST"])
    def dismiss_behavior_proposal(session_id: str, rule_key: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="dismiss_behavior_proposal")
        if error_response:
            return error_response
        try:
            dismissal = app.behavior.dismiss_proposal(session_id, str(rule_key or "").strip())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"dismissal": dismissal})