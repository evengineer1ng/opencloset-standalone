# Persistent planning schema + API
#
# Evolves the earlier per-session scaffold into a small plan rolodex:
# a session can keep multiple stored plans while one is active at a time.
# The active plan now also carries ordered plan items and revision snapshots.

from __future__ import annotations

import json
import sqlite3
from typing import Any

from api.db.schema import new_id


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

LEGACY_SESSION_PLANS_SQL = """
CREATE TABLE IF NOT EXISTS session_plans (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    active_goal TEXT,
    want_to_know TEXT,
    context_guard TEXT,
    handoff TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

PLANS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    active_goal TEXT,
    want_to_know TEXT,
    handoff TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    workspace_id TEXT,
    build_project_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_plans_session ON plans(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_plans_workspace ON plans(workspace_id);
"""

SESSION_PLAN_STATE_SQL = """
CREATE TABLE IF NOT EXISTS session_plan_state (
    session_id TEXT PRIMARY KEY,
    active_plan_id TEXT,
    context_guard TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (active_plan_id) REFERENCES plans(id)
);
"""

PLAN_ITEMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plan_items (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo',
    position INTEGER NOT NULL,
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES plans(id)
);

CREATE INDEX IF NOT EXISTS idx_plan_items_plan ON plan_items(plan_id, position);
"""

PLAN_REVISIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plan_revisions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (plan_id) REFERENCES plans(id)
);

CREATE INDEX IF NOT EXISTS idx_plan_revisions_plan ON plan_revisions(plan_id, created_at);
"""

PLAN_PROPOSALS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plan_proposals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    plan_id TEXT,
    proposal_type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    proposed_by TEXT NOT NULL DEFAULT 'unknown',
    accepted_by TEXT,
    rejected_by TEXT,
    resolution_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (plan_id) REFERENCES plans(id)
);

CREATE INDEX IF NOT EXISTS idx_plan_proposals_session ON plan_proposals(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_plan_proposals_plan ON plan_proposals(plan_id, created_at);
CREATE INDEX IF NOT EXISTS idx_plan_proposals_status ON plan_proposals(status, created_at);
"""

PLAN_STATUSES = {"active", "paused", "completed", "archived", "superseded"}
PLAN_ITEM_STATUSES = {"todo", "doing", "done", "blocked", "deferred"}
PLAN_PROPOSAL_TYPES = {"create_plan", "activate_plan", "archive_plan", "reorder_item", "add_item", "grant_path_access"}
PLAN_PROPOSAL_STATUSES = {"pending", "accepted", "rejected"}


def init_planning_table(db: sqlite3.Connection) -> None:
    """Create planning tables and migrate legacy session_plans rows."""
    db.execute(LEGACY_SESSION_PLANS_SQL)
    db.executescript(PLANS_TABLE_SQL)
    db.executescript(SESSION_PLAN_STATE_SQL)
    db.executescript(PLAN_ITEMS_TABLE_SQL)
    db.executescript(PLAN_REVISIONS_TABLE_SQL)
    db.executescript(PLAN_PROPOSALS_TABLE_SQL)
    _migrate_legacy_session_plans(db)

    # Phase 0D migration: add workspace columns to plans
    _migrations = [
        "ALTER TABLE plans ADD COLUMN workspace_id TEXT",
        "ALTER TABLE plans ADD COLUMN build_project_id TEXT",
    ]
    for stmt in _migrations:
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    db.commit()


def _migrate_legacy_session_plans(db: sqlite3.Connection) -> None:
    rows = db.execute(
        """SELECT sp.id, sp.session_id, sp.active_goal, sp.want_to_know, sp.context_guard,
                  sp.handoff, sp.status, sp.created_at, sp.updated_at
           FROM session_plans sp
           LEFT JOIN session_plan_state sps ON sps.session_id = sp.session_id
           WHERE sps.session_id IS NULL"""
    ).fetchall()

    for row in rows:
        db.execute(
            """INSERT OR IGNORE INTO plans
               (id, session_id, title, active_goal, want_to_know, handoff, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                row["id"],
                row["session_id"],
                "Session Plan",
                row["active_goal"] or "",
                row["want_to_know"] or json.dumps([]),
                row["handoff"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        db.execute(
            """INSERT OR IGNORE INTO session_plan_state
               (session_id, active_plan_id, context_guard, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                row["session_id"],
                row["id"],
                row["context_guard"] or json.dumps({"tokens_used": 0, "rollover_threshold": 58000}),
                row["status"] or "active",
                row["created_at"],
                row["updated_at"],
            ),
        )


# ---------------------------------------------------------------------------
# Planning manager
# ---------------------------------------------------------------------------

class PlanningManager:
    """Manage per-session plan rolodex state with one active plan."""

    def __init__(self, db: sqlite3.Connection, event_logger=None, workspaces=None) -> None:
        self.db = db
        self.event_logger = event_logger
        self.workspaces = workspaces
        self.rollover_guard_enabled = True

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _ensure_session_state(self, session_id: str) -> bool:
        session_row = self.db.execute(
            "SELECT id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not session_row:
            return False

        row = self.db.execute(
            "SELECT session_id FROM session_plan_state WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row:
            return True

        now = self._now()
        try:
            self.db.execute(
                """INSERT INTO session_plan_state
                   (session_id, active_plan_id, context_guard, status, created_at, updated_at)
                   VALUES (?, NULL, ?, 'active', ?, ?)""",
                (session_id, json.dumps({"tokens_used": 0, "rollover_threshold": 58000}), now, now),
            )
        except sqlite3.IntegrityError:
            # Another request/thread won the bootstrap race. If the row now
            # exists, treat initialization as successful instead of surfacing
            # a duplicate-key error back through unrelated read paths.
            row = self.db.execute(
                "SELECT session_id FROM session_plan_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                return True
            raise
        self.db.commit()
        return True

    def _get_session_scope(self, session_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT id, workspace_id, build_project_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def _can_session_access_plan(self, session_id: str, row) -> bool:
        if row["session_id"] == session_id:
            return True

        session_scope = self._get_session_scope(session_id)
        if not session_scope:
            return False

        session_workspace_id = session_scope.get("workspace_id")
        if not session_workspace_id or row["workspace_id"] != session_workspace_id:
            return False

        plan_project_id = row["build_project_id"]
        if not plan_project_id:
            return True

        return plan_project_id == session_scope.get("build_project_id")

    def _get_accessible_plan_row(self, session_id: str, plan_id: str):
        row = self.db.execute(
            """SELECT id, session_id, title, active_goal, want_to_know, handoff, status,
                      workspace_id, build_project_id, created_at, updated_at
               FROM plans WHERE id = ?""",
            (plan_id,),
        ).fetchone()
        if not row or not self._can_session_access_plan(session_id, row):
            return None
        return row

    def _list_accessible_plan_rows(self, session_id: str):
        session_scope = self._get_session_scope(session_id)
        if not session_scope:
            return []

        workspace_id = session_scope.get("workspace_id")
        build_project_id = session_scope.get("build_project_id")
        if not workspace_id:
            return self.db.execute(
                """SELECT id, session_id, title, active_goal, want_to_know, handoff, status,
                          workspace_id, build_project_id, created_at, updated_at
                   FROM plans WHERE session_id = ? ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()

        if build_project_id:
            rows = self.db.execute(
                """SELECT id, session_id, title, active_goal, want_to_know, handoff, status,
                          workspace_id, build_project_id, created_at, updated_at
                   FROM plans
                   WHERE session_id = ?
                      OR (workspace_id = ? AND (build_project_id IS NULL OR build_project_id = ?))
                   ORDER BY created_at ASC""",
                (session_id, workspace_id, build_project_id),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT id, session_id, title, active_goal, want_to_know, handoff, status,
                          workspace_id, build_project_id, created_at, updated_at
                   FROM plans
                   WHERE session_id = ?
                      OR (workspace_id = ? AND build_project_id IS NULL)
                   ORDER BY created_at ASC""",
                (session_id, workspace_id),
            ).fetchall()

        unique_rows: list[Any] = []
        seen_ids: set[str] = set()
        for row in rows:
            if row["id"] in seen_ids:
                continue
            seen_ids.add(row["id"])
            unique_rows.append(row)
        return unique_rows

    def _resolve_plan_scope(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
        build_project_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        session_scope = self._get_session_scope(session_id)
        if not session_scope:
            return workspace_id, build_project_id

        resolved_workspace_id = workspace_id if workspace_id is not None else session_scope.get("workspace_id")
        resolved_build_project_id = (
            build_project_id if build_project_id is not None else session_scope.get("build_project_id")
        )
        return resolved_workspace_id, resolved_build_project_id

    def bootstrap_session(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
        build_project_id: str | None = None,
    ) -> str:
        if not self._ensure_session_state(session_id):
            raise ValueError("session not found")
        active_plan = self.get_plan(session_id)
        if active_plan:
            return active_plan["id"]
        return self.create_plan(
            session_id,
            title="Session Plan",
            active_goal="",
            want_to_know=[],
            activate=True,
            activation_reason="bootstrap",
            workspace_id=workspace_id,
            build_project_id=build_project_id,
        )

    def get_active_plan_id(self, session_id: str) -> str | None:
        if not self._ensure_session_state(session_id):
            return None
        row = self.db.execute(
            "SELECT active_plan_id FROM session_plan_state WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["active_plan_id"] if row else None

    def _plan_row_to_dict(self, row, *, active_plan_id: str | None) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "title": row["title"],
            "active_goal": row["active_goal"],
            "want_to_know": json.loads(row["want_to_know"]) if row["want_to_know"] else [],
            "handoff": json.loads(row["handoff"]) if row["handoff"] else None,
            "plan_status": row["status"],
            "workspace_id": row["workspace_id"] if "workspace_id" in row.keys() else None,
            "build_project_id": row["build_project_id"] if "build_project_id" in row.keys() else None,
            "is_active": row["id"] == active_plan_id,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_plan_by_id(self, session_id: str, plan_id: str) -> dict | None:
        if not self._ensure_session_state(session_id):
            return None
        row = self._get_accessible_plan_row(session_id, plan_id)
        if not row:
            return None
        return self._plan_row_to_dict(row, active_plan_id=self.get_active_plan_id(session_id))

    def list_plans(
        self,
        session_id: str,
        *,
        status: str | None = None,
        query: str | None = None,
        active_only: bool | None = None,
    ) -> list[dict[str, Any]]:
        if not self._ensure_session_state(session_id):
            return []
        if status is not None:
            self._validate_plan_status(status)
        active_plan_id = self.get_active_plan_id(session_id)
        rows = self._list_accessible_plan_rows(session_id)
        plans = [self._plan_row_to_dict(row, active_plan_id=active_plan_id) for row in rows]
        normalized_query = (query or "").strip().lower()
        filtered: list[dict[str, Any]] = []
        for plan in plans:
            if status is not None and plan["plan_status"] != status:
                continue
            if active_only is not None and plan["is_active"] is not active_only:
                continue
            if normalized_query:
                haystacks = [plan.get("title") or "", plan.get("active_goal") or ""]
                haystacks.extend(plan.get("want_to_know") or [])
                if not any(normalized_query in value.lower() for value in haystacks if isinstance(value, str)):
                    continue
            filtered.append(plan)
        return filtered

    def list_plans_by_workspace(self, workspace_id: str) -> list[dict[str, Any]]:
        """List all plans associated with a workspace."""
        rows = self.db.execute(
            """SELECT id, session_id, title, active_goal, want_to_know, handoff, status,
                      workspace_id, build_project_id, created_at, updated_at
               FROM plans WHERE workspace_id = ? ORDER BY created_at ASC""",
            (workspace_id,),
        ).fetchall()
        return [self._plan_row_to_dict(row, active_plan_id=None) for row in rows]

    def _validate_item_status(self, status: str) -> None:
        if status not in PLAN_ITEM_STATUSES:
            raise ValueError(
                f"Invalid plan item status '{status}'; must be one of {tuple(sorted(PLAN_ITEM_STATUSES))}"
            )

    def _validate_plan_status(self, status: str) -> None:
        if status not in PLAN_STATUSES:
            raise ValueError(f"Invalid plan status '{status}'; must be one of {tuple(sorted(PLAN_STATUSES))}")

    def _validate_proposal_type(self, proposal_type: str) -> None:
        if proposal_type not in PLAN_PROPOSAL_TYPES:
            raise ValueError(
                f"Invalid plan proposal type '{proposal_type}'; must be one of {tuple(sorted(PLAN_PROPOSAL_TYPES))}"
            )

    def _validate_proposal_status(self, status: str) -> None:
        if status not in PLAN_PROPOSAL_STATUSES:
            raise ValueError(
                f"Invalid plan proposal status '{status}'; must be one of {tuple(sorted(PLAN_PROPOSAL_STATUSES))}"
            )

    def _plan_item_row_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "plan_id": row["plan_id"],
            "content": row["content"],
            "status": row["status"],
            "position": row["position"],
            "archived": row["archived_at"] is not None,
            "archived_at": row["archived_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_plan_items(
        self,
        session_id: str,
        plan_id: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        target_plan_id = plan_id or self.get_active_plan_id(session_id)
        if not target_plan_id or not self.get_plan_by_id(session_id, target_plan_id):
            return []

        if include_archived:
            rows = self.db.execute(
                """SELECT id, plan_id, content, status, position, archived_at, created_at, updated_at
                   FROM plan_items WHERE plan_id = ? ORDER BY position ASC, created_at ASC""",
                (target_plan_id,),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT id, plan_id, content, status, position, archived_at, created_at, updated_at
                   FROM plan_items WHERE plan_id = ? AND archived_at IS NULL
                   ORDER BY position ASC, created_at ASC""",
                (target_plan_id,),
            ).fetchall()
        return [self._plan_item_row_to_dict(row) for row in rows]

    @staticmethod
    def _compute_next_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        for item in items:
            if item["status"] != "done" and not item["archived"]:
                return item
        return None

    def _record_plan_revision(self, session_id: str, plan_id: str, change_type: str, summary: str) -> None:
        plan = self.get_plan_by_id(session_id, plan_id)
        if not plan:
            return
        items = self.list_plan_items(session_id, plan_id, include_archived=True)
        snapshot = {
            "plan": plan,
            "items": items,
            "next_item": self._compute_next_item(items),
        }
        self.db.execute(
            """INSERT INTO plan_revisions
               (id, session_id, plan_id, change_type, summary, snapshot, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(),
                session_id,
                plan_id,
                change_type,
                summary,
                json.dumps(snapshot),
                self._now(),
            ),
        )
        self.db.commit()

    @staticmethod
    def _build_revision_diff(
        current_snapshot: dict[str, Any],
        previous_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        current_plan = current_snapshot.get("plan") or {}
        previous_plan = (previous_snapshot or {}).get("plan") or {}
        changed_fields = [
            field
            for field in ("title", "active_goal", "want_to_know", "handoff", "plan_status")
            if current_plan.get(field) != previous_plan.get(field)
        ]

        current_items = {item["id"]: item for item in current_snapshot.get("items") or []}
        previous_items = {item["id"]: item for item in (previous_snapshot or {}).get("items") or []}

        item_ids_added = sorted(item_id for item_id in current_items if item_id not in previous_items)
        item_ids_removed = sorted(item_id for item_id in previous_items if item_id not in current_items)
        item_ids_updated = sorted(
            item_id
            for item_id in current_items
            if item_id in previous_items
            and any(
                current_items[item_id].get(field) != previous_items[item_id].get(field)
                for field in ("content", "status", "position", "archived", "archived_at")
            )
        )

        return {
            "changed_fields": changed_fields,
            "item_ids_added": item_ids_added,
            "item_ids_removed": item_ids_removed,
            "item_ids_updated": item_ids_updated,
            "next_item_changed": current_snapshot.get("next_item") != (previous_snapshot or {}).get("next_item"),
        }

    def list_plan_revisions(self, session_id: str, plan_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self.get_plan_by_id(session_id, plan_id):
            return []
        rows = self.db.execute(
            """SELECT id, session_id, plan_id, change_type, summary, snapshot, created_at
               FROM plan_revisions WHERE session_id = ? AND plan_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (session_id, plan_id, limit),
        ).fetchall()
        parsed_rows = [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "plan_id": row["plan_id"],
                "change_type": row["change_type"],
                "summary": row["summary"],
                "snapshot": json.loads(row["snapshot"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        for index, revision in enumerate(parsed_rows):
            previous_snapshot = parsed_rows[index + 1]["snapshot"] if index + 1 < len(parsed_rows) else None
            revision["diff"] = self._build_revision_diff(revision["snapshot"], previous_snapshot)
        return parsed_rows

    def create_plan(
        self,
        session_id: str,
        active_goal: str = "",
        want_to_know: list[str] | None = None,
        *,
        title: str = "",
        handoff: dict[str, Any] | None = None,
        activate: bool = True,
        activation_reason: str = "create",
        workspace_id: str | None = None,
        build_project_id: str | None = None,
    ) -> str:
        if not self._ensure_session_state(session_id):
            raise ValueError("session not found")
        workspace_id, build_project_id = self._resolve_plan_scope(
            session_id,
            workspace_id=workspace_id,
            build_project_id=build_project_id,
        )
        now = self._now()
        plan_id = new_id()
        self.db.execute(
            """INSERT INTO plans
               (id, session_id, title, active_goal, want_to_know, handoff, status,
                workspace_id, build_project_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
            (
                plan_id,
                session_id,
                title,
                active_goal,
                json.dumps(want_to_know or []),
                json.dumps(handoff) if handoff is not None else None,
                workspace_id,
                build_project_id,
                now,
                now,
            ),
        )
        self.db.commit()

        if activate or not self.get_active_plan_id(session_id):
            self.activate_plan(session_id, plan_id, reason=activation_reason)

        self._record_plan_revision(session_id, plan_id, "plan_created", title or active_goal or "plan created")
        return plan_id

    def get_plan(self, session_id: str) -> dict | None:
        if not self._ensure_session_state(session_id):
            return None
        row = self.db.execute(
            """SELECT s.session_id, s.active_plan_id, s.context_guard, s.status,
                      p.id, p.title, p.active_goal, p.want_to_know, p.handoff,
                      p.status AS plan_status, p.workspace_id, p.build_project_id,
                      p.created_at, p.updated_at
               FROM session_plan_state s
               LEFT JOIN plans p ON p.id = s.active_plan_id
               WHERE s.session_id = ?""",
            (session_id,),
        ).fetchone()
        if not row or not row["active_plan_id"]:
            return None

        items = self.list_plan_items(session_id, row["id"])
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "title": row["title"],
            "active_goal": row["active_goal"],
            "want_to_know": json.loads(row["want_to_know"]) if row["want_to_know"] else [],
            "context_guard": json.loads(row["context_guard"]) if row["context_guard"] else {},
            "handoff": json.loads(row["handoff"]) if row["handoff"] else None,
            "status": row["status"],
            "workspace_id": row["workspace_id"],
            "build_project_id": row["build_project_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "plan_status": row["plan_status"],
            "items": items,
            "next_item": self._compute_next_item(items),
        }

    def activate_plan(self, session_id: str, plan_id: str, *, reason: str = "user") -> dict:
        if not self._ensure_session_state(session_id):
            raise ValueError("session not found")
        plan = self.get_plan_by_id(session_id, plan_id)
        if not plan:
            raise ValueError("plan not found for session")
        if plan["plan_status"] in {"archived", "superseded"}:
            raise ValueError("cannot activate archived or superseded plan")

        previous_plan_id = self.get_active_plan_id(session_id)
        self.db.execute(
            "UPDATE session_plan_state SET active_plan_id = ?, updated_at = ? WHERE session_id = ?",
            (plan_id, self._now(), session_id),
        )
        self.db.commit()

        if self.event_logger and previous_plan_id != plan_id:
            self.event_logger.log(
                session_id,
                "plan_activated",
                {
                    "plan_id": plan_id,
                    "previous_plan_id": previous_plan_id,
                    "title": plan["title"],
                    "reason": reason,
                },
            )
        return self.get_plan(session_id)

    def update_plan(
        self,
        session_id: str,
        plan_id: str,
        *,
        title: str | None = None,
        active_goal: str | None = None,
        want_to_know: list[str] | None = None,
        handoff: dict[str, Any] | None | object = ...,  # Ellipsis means no change.
        status: str | None = None,
    ) -> dict[str, Any]:
        if not self._ensure_session_state(session_id):
            raise ValueError("session not found")
        plan = self.get_plan_by_id(session_id, plan_id)
        if not plan:
            raise ValueError("plan not found for session")

        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if active_goal is not None:
            updates["active_goal"] = active_goal
        if want_to_know is not None:
            updates["want_to_know"] = json.dumps(want_to_know)
        if handoff is not ...:
            updates["handoff"] = json.dumps(handoff) if handoff is not None else None
        if status is not None:
            self._validate_plan_status(status)
            if status in {"archived", "superseded"}:
                active_row = self.db.execute(
                    "SELECT session_id FROM session_plan_state WHERE active_plan_id = ? LIMIT 1",
                    (plan_id,),
                ).fetchone()
                if active_row:
                    raise ValueError("cannot archive or supersede an active plan")
            updates["status"] = status

        if not updates:
            return plan

        params = list(updates.values())
        params.append(self._now())
        params.append(plan_id)
        assignments = [f"{column} = ?" for column in updates]
        assignments.append("updated_at = ?")

        self.db.execute(f"UPDATE plans SET {', '.join(assignments)} WHERE id = ?", params)
        self.db.commit()
        self._record_plan_revision(session_id, plan_id, "plan_updated", ", ".join(sorted(updates.keys())))
        return self.get_plan_by_id(session_id, plan_id)

    def list_plan_activation_history(
        self,
        session_id: str,
        plan_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not self.get_plan_by_id(session_id, plan_id):
            return []

        rows = self.db.execute(
            """SELECT session_id, payload, created_at
               FROM agent_events
               WHERE event_type = 'plan_activated'
               ORDER BY created_at DESC, rowid DESC"""
        ).fetchall()

        history: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload"]) if row["payload"] else {}
            if payload.get("plan_id") != plan_id:
                continue
            history.append(
                {
                    "session_id": row["session_id"],
                    "plan_id": payload.get("plan_id"),
                    "previous_plan_id": payload.get("previous_plan_id"),
                    "title": payload.get("title", ""),
                    "reason": payload.get("reason", ""),
                    "created_at": row["created_at"],
                }
            )
            if len(history) >= limit:
                break
        return history

    def _resolve_proposal_plan_id(
        self,
        session_id: str,
        proposal_type: str,
        payload: dict[str, Any],
        explicit_plan_id: str | None,
    ) -> str | None:
        plan_id = explicit_plan_id or payload.get("plan_id")
        if proposal_type in {"activate_plan", "archive_plan"}:
            if not isinstance(plan_id, str) or not plan_id:
                raise ValueError("plan_id is required")
        if proposal_type in {"add_item", "reorder_item"} and (not isinstance(plan_id, str) or not plan_id):
            plan_id = self.get_active_plan_id(session_id)
            if not plan_id:
                raise ValueError("no active plan for session")
        if isinstance(plan_id, str) and plan_id and not self.get_plan_by_id(session_id, plan_id):
            raise ValueError("plan not found for session")
        return plan_id if isinstance(plan_id, str) and plan_id else None

    def _normalize_proposal_payload(
        self,
        session_id: str,
        proposal_type: str,
        payload: dict[str, Any],
        *,
        plan_id: str | None,
    ) -> tuple[str | None, dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        resolved_plan_id = self._resolve_proposal_plan_id(session_id, proposal_type, payload, plan_id)
        normalized = dict(payload)
        if resolved_plan_id:
            normalized["plan_id"] = resolved_plan_id

        if proposal_type == "create_plan":
            title = normalized.get("title", "")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("payload.title is required")
            active_goal = normalized.get("active_goal", "")
            if not isinstance(active_goal, str):
                raise ValueError("payload.active_goal must be a string")
            want_to_know = normalized.get("want_to_know", [])
            if want_to_know is not None and (
                not isinstance(want_to_know, list) or not all(isinstance(item, str) for item in want_to_know)
            ):
                raise ValueError("payload.want_to_know must be a list of strings")
            activate = normalized.get("activate", False)
            if not isinstance(activate, bool):
                raise ValueError("payload.activate must be a boolean")
            normalized["title"] = title.strip()
            normalized["active_goal"] = active_goal
            normalized["want_to_know"] = want_to_know or []
            normalized["activate"] = activate
            return None, normalized

        if proposal_type == "activate_plan":
            return resolved_plan_id, {"plan_id": resolved_plan_id}

        if proposal_type == "archive_plan":
            return resolved_plan_id, {"plan_id": resolved_plan_id}

        if proposal_type == "add_item":
            content = normalized.get("content", "")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("payload.content is required")
            status = normalized.get("status", "todo")
            self._validate_item_status(status)
            position = normalized.get("position")
            if position is not None and (not isinstance(position, int) or position < 1):
                raise ValueError("payload.position must be a positive integer")
            return resolved_plan_id, {
                "plan_id": resolved_plan_id,
                "content": content.strip(),
                "status": status,
                "position": position,
            }

        if proposal_type == "reorder_item":
            item_id = normalized.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError("payload.item_id is required")
            position = normalized.get("position")
            if not isinstance(position, int) or position < 1:
                raise ValueError("payload.position must be a positive integer")
            items = self.list_plan_items(session_id, resolved_plan_id, include_archived=True)
            if not any(item["id"] == item_id for item in items):
                raise ValueError("plan item not found")
            return resolved_plan_id, {
                "plan_id": resolved_plan_id,
                "item_id": item_id,
                "position": position,
            }

        if proposal_type == "grant_path_access":
            path = str(payload.get("path", "")).strip()
            if not path:
                raise ValueError("payload.path is required")
            return None, {
                "path": path,
                "reason": str(payload.get("reason", "")).strip(),
            }

        raise ValueError("unsupported proposal type")

    def _proposal_row_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "plan_id": row["plan_id"],
            "proposal_type": row["proposal_type"],
            "summary": row["summary"],
            "payload": json.loads(row["payload"]) if row["payload"] else {},
            "status": row["status"],
            "proposed_by": row["proposed_by"],
            "accepted_by": row["accepted_by"],
            "rejected_by": row["rejected_by"],
            "resolution_note": row["resolution_note"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "resolved_at": row["resolved_at"],
        }

    def get_plan_proposal(self, session_id: str, proposal_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT id, session_id, plan_id, proposal_type, summary, payload, status,
                      proposed_by, accepted_by, rejected_by, resolution_note,
                      created_at, updated_at, resolved_at
               FROM plan_proposals WHERE session_id = ? AND id = ?""",
            (session_id, proposal_id),
        ).fetchone()
        return self._proposal_row_to_dict(row) if row else None

    def list_plan_proposals(
        self,
        session_id: str,
        *,
        status: str | None = None,
        plan_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not self._ensure_session_state(session_id):
            return []
        conditions = ["session_id = ?"]
        params: list[Any] = [session_id]
        if status is not None:
            self._validate_proposal_status(status)
            conditions.append("status = ?")
            params.append(status)
        if plan_id is not None:
            if not self.get_plan_by_id(session_id, plan_id):
                return []
            conditions.append("plan_id = ?")
            params.append(plan_id)
        params.append(limit)
        rows = self.db.execute(
            f"""SELECT id, session_id, plan_id, proposal_type, summary, payload, status,
                       proposed_by, accepted_by, rejected_by, resolution_note,
                       created_at, updated_at, resolved_at
                FROM plan_proposals WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            params,
        ).fetchall()
        return [self._proposal_row_to_dict(row) for row in rows]

    def submit_plan_proposal(
        self,
        session_id: str,
        proposal_type: str,
        payload: dict[str, Any],
        *,
        plan_id: str | None = None,
        summary: str = "",
        proposed_by: str = "buddy",
    ) -> dict[str, Any]:
        if not self._ensure_session_state(session_id):
            raise ValueError("session not found")
        self._validate_proposal_type(proposal_type)
        resolved_plan_id, normalized_payload = self._normalize_proposal_payload(
            session_id,
            proposal_type,
            payload,
            plan_id=plan_id,
        )
        now = self._now()
        proposal_id = new_id()
        proposal_summary = summary or proposal_type.replace("_", " ")
        self.db.execute(
            """INSERT INTO plan_proposals
               (id, session_id, plan_id, proposal_type, summary, payload, status, proposed_by,
                accepted_by, rejected_by, resolution_note, created_at, updated_at, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, NULL, ?, ?, NULL)""",
            (
                proposal_id,
                session_id,
                resolved_plan_id,
                proposal_type,
                proposal_summary,
                json.dumps(normalized_payload),
                proposed_by,
                now,
                now,
            ),
        )
        self.db.commit()
        if self.event_logger:
            self.event_logger.log(
                session_id,
                "plan_proposal_submitted",
                {
                    "proposal_id": proposal_id,
                    "plan_id": resolved_plan_id,
                    "proposal_type": proposal_type,
                    "proposed_by": proposed_by,
                },
            )
        proposal = self.get_plan_proposal(session_id, proposal_id)
        if proposal is None:
            raise ValueError("plan proposal not found after insert")
        self._capture_plan_proposal_activity(
            session_id,
            proposal,
            lifecycle_state="pending",
            actor=proposed_by,
            capture_status="pending",
        )
        return proposal

    def _capture_plan_proposal_activity(
        self,
        session_id: str,
        proposal: dict[str, Any],
        *,
        lifecycle_state: str,
        actor: str,
        capture_status: str,
        resolution_note: str = "",
    ) -> None:
        if not self.workspaces:
            return
        summary = proposal.get("summary") or proposal.get("proposal_type") or "plan proposal"
        if lifecycle_state == "pending":
            content = f"Plan proposal queued for review: {summary}"
        elif lifecycle_state == "accepted":
            content = f"Plan proposal accepted: {summary}"
        else:
            content = f"Plan proposal rejected: {summary}"
        self.workspaces.create_workspace_activity_capture(
            session_id=session_id,
            source="planning_review",
            event_type="plan_proposal",
            content=content,
            metadata={
                "proposal_id": proposal["id"],
                "proposal_type": proposal["proposal_type"],
                "proposal_status": lifecycle_state,
                "plan_id": proposal.get("plan_id"),
                "actor": actor,
                "summary": proposal.get("summary"),
                "payload": proposal.get("payload") or {},
                "resolution_note": resolution_note,
            },
            status=capture_status,
        )

    def _apply_plan_proposal(self, session_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        payload = proposal["payload"]
        proposal_type = proposal["proposal_type"]
        plan_id = proposal.get("plan_id")

        if proposal_type == "create_plan":
            created_plan_id = self.create_plan(
                session_id,
                title=payload["title"],
                active_goal=payload.get("active_goal", ""),
                want_to_know=payload.get("want_to_know", []),
                activate=payload.get("activate", False),
                activation_reason="proposal_accepted",
            )
            return {"plan": self.get_plan_by_id(session_id, created_plan_id)}

        if proposal_type == "activate_plan":
            return {"plan": self.activate_plan(session_id, plan_id, reason="proposal_accepted")}

        if proposal_type == "archive_plan":
            return {"plan": self.update_plan(session_id, plan_id, status="archived")}

        if proposal_type == "add_item":
            item = self.add_plan_item(
                session_id,
                plan_id,
                payload["content"],
                status=payload.get("status", "todo"),
                position=payload.get("position"),
            )
            return {"plan_id": plan_id, "item": item}

        if proposal_type == "reorder_item":
            item = self.update_plan_item(
                session_id,
                plan_id,
                payload["item_id"],
                position=payload["position"],
            )
            return {"plan_id": plan_id, "item": item}

        if proposal_type == "grant_path_access":
            path = payload["path"]
            row = self.db.execute(
                "SELECT tool_policy FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            stored = json.loads(row["tool_policy"]) if row and row["tool_policy"] else {}
            allowed = stored.get("allowed_paths", [])
            if path not in allowed:
                allowed = list(allowed) + [path]
            stored["allowed_paths"] = allowed
            self.db.execute(
                "UPDATE sessions SET tool_policy = ?, updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) WHERE id = ?",
                (json.dumps(stored), session_id),
            )
            self.db.commit()
            return {"granted_path": path}

        raise ValueError("unsupported proposal type")

    def accept_plan_proposal(
        self,
        session_id: str,
        proposal_id: str,
        *,
        accepted_by: str = "clo",
    ) -> dict[str, Any]:
        proposal = self.get_plan_proposal(session_id, proposal_id)
        if not proposal:
            raise ValueError("plan proposal not found")
        if proposal["status"] != "pending":
            raise ValueError("plan proposal is not pending")
        result = self._apply_plan_proposal(session_id, proposal)
        now = self._now()
        self.db.execute(
            """UPDATE plan_proposals
               SET status = 'accepted', accepted_by = ?, updated_at = ?, resolved_at = ?
               WHERE id = ?""",
            (accepted_by, now, now, proposal_id),
        )
        self.db.commit()
        updated = self.get_plan_proposal(session_id, proposal_id)
        if self.event_logger:
            self.event_logger.log(
                session_id,
                "plan_proposal_accepted",
                {
                    "proposal_id": proposal_id,
                    "plan_id": updated.get("plan_id") if updated else proposal.get("plan_id"),
                    "proposal_type": proposal["proposal_type"],
                    "accepted_by": accepted_by,
                },
            )
        if updated:
            self._capture_plan_proposal_activity(
                session_id,
                updated,
                lifecycle_state="accepted",
                actor=accepted_by,
                capture_status="processed",
            )
        return {"proposal": updated, "result": result}

    def reject_plan_proposal(
        self,
        session_id: str,
        proposal_id: str,
        *,
        rejected_by: str = "clo",
        resolution_note: str = "",
    ) -> dict[str, Any]:
        proposal = self.get_plan_proposal(session_id, proposal_id)
        if not proposal:
            raise ValueError("plan proposal not found")
        if proposal["status"] != "pending":
            raise ValueError("plan proposal is not pending")
        now = self._now()
        self.db.execute(
            """UPDATE plan_proposals
               SET status = 'rejected', rejected_by = ?, resolution_note = ?, updated_at = ?, resolved_at = ?
               WHERE id = ?""",
            (rejected_by, resolution_note, now, now, proposal_id),
        )
        self.db.commit()
        updated = self.get_plan_proposal(session_id, proposal_id)
        if self.event_logger:
            self.event_logger.log(
                session_id,
                "plan_proposal_rejected",
                {
                    "proposal_id": proposal_id,
                    "plan_id": updated.get("plan_id") if updated else proposal.get("plan_id"),
                    "proposal_type": proposal["proposal_type"],
                    "rejected_by": rejected_by,
                },
            )
        if updated:
            self._capture_plan_proposal_activity(
                session_id,
                updated,
                lifecycle_state="rejected",
                actor=rejected_by,
                capture_status="processed",
                resolution_note=resolution_note,
            )
        return {"proposal": updated}

    def _update_active_plan_fields(self, session_id: str, **fields: Any) -> None:
        active_plan_id = self.get_active_plan_id(session_id)
        if not active_plan_id:
            raise ValueError("no active plan for session")

        updates = []
        params: list[Any] = []
        for column, value in fields.items():
            updates.append(f"{column} = ?")
            params.append(value)
        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(active_plan_id)

        self.db.execute(f"UPDATE plans SET {', '.join(updates)} WHERE id = ?", params)
        self.db.commit()
        self._record_plan_revision(session_id, active_plan_id, "plan_updated", ", ".join(sorted(fields.keys())))

    def update_active_goal(self, session_id: str, active_goal: str) -> None:
        self._update_active_plan_fields(session_id, active_goal=active_goal)

    def update_title(self, session_id: str, title: str) -> None:
        self._update_active_plan_fields(session_id, title=title)

    def update_want_to_know(self, session_id: str, items: list[str]) -> None:
        self._update_active_plan_fields(session_id, want_to_know=json.dumps(items))

    def add_want_to_know(self, session_id: str, item: str) -> None:
        plan = self.get_plan(session_id)
        if not plan:
            return
        items = plan["want_to_know"]
        if item not in items:
            items.append(item)
        self.update_want_to_know(session_id, items)

    def remove_want_to_know(self, session_id: str, item: str) -> None:
        plan = self.get_plan(session_id)
        if not plan:
            return
        items = plan["want_to_know"]
        if item in items:
            items.remove(item)
        self.update_want_to_know(session_id, items)

    def update_context_guard(self, session_id: str, tokens_used: int, **extra: Any) -> None:
        self._ensure_session_state(session_id)
        plan = self.get_plan(session_id)
        guard = plan["context_guard"] if plan else {}
        guard["tokens_used"] = tokens_used
        guard.update(extra)
        self.db.execute(
            "UPDATE session_plan_state SET context_guard = ?, updated_at = ? WHERE session_id = ?",
            (json.dumps(guard), self._now(), session_id),
        )
        self.db.commit()

    def set_handoff(self, session_id: str, handoff_data: dict) -> None:
        self._update_active_plan_fields(session_id, handoff=json.dumps(handoff_data))

    def clear_handoff(self, session_id: str) -> None:
        self._update_active_plan_fields(session_id, handoff=None)

    def set_status(self, session_id: str, status: str) -> None:
        valid = ("active", "paused", "rolled-over")
        if status not in valid:
            raise ValueError(f"Invalid plan status '{status}'; must be one of {valid}")
        self.db.execute(
            "UPDATE session_plan_state SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, self._now(), session_id),
        )
        self.db.commit()

    def add_plan_item(
        self,
        session_id: str,
        plan_id: str,
        content: str,
        *,
        status: str = "todo",
        position: int | None = None,
    ) -> dict[str, Any]:
        if not self.get_plan_by_id(session_id, plan_id):
            raise ValueError("plan not found for session")
        if not content:
            raise ValueError("content is required")
        self._validate_item_status(status)

        max_row = self.db.execute(
            "SELECT COALESCE(MAX(position), 0) AS max_pos FROM plan_items WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        target_position = max_row["max_pos"] + 1 if position is None else max(1, position)
        if position is not None:
            self.db.execute(
                "UPDATE plan_items SET position = position + 1 WHERE plan_id = ? AND position >= ?",
                (plan_id, target_position),
            )

        item_id = new_id()
        now = self._now()
        self.db.execute(
            """INSERT INTO plan_items
               (id, plan_id, content, status, position, archived_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
            (item_id, plan_id, content, status, target_position, now, now),
        )
        self.db.commit()
        self._record_plan_revision(session_id, plan_id, "item_added", content)

        row = self.db.execute(
            """SELECT id, plan_id, content, status, position, archived_at, created_at, updated_at
               FROM plan_items WHERE id = ?""",
            (item_id,),
        ).fetchone()
        return self._plan_item_row_to_dict(row)

    def update_plan_item(
        self,
        session_id: str,
        plan_id: str,
        item_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        position: int | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        if not self.get_plan_by_id(session_id, plan_id):
            raise ValueError("plan not found for session")
        row = self.db.execute(
            """SELECT id, plan_id, content, status, position, archived_at, created_at, updated_at
               FROM plan_items WHERE id = ? AND plan_id = ?""",
            (item_id, plan_id),
        ).fetchone()
        if not row:
            raise ValueError("plan item not found")

        updates = []
        params: list[Any] = []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if status is not None:
            self._validate_item_status(status)
            updates.append("status = ?")
            params.append(status)
        if archived is not None:
            updates.append("archived_at = ?")
            params.append(self._now() if archived else None)

        current_position = row["position"]
        if position is not None:
            target_position = max(1, position)
            count_row = self.db.execute(
                "SELECT COUNT(*) AS cnt FROM plan_items WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            target_position = min(target_position, count_row["cnt"])
            if target_position != current_position:
                if target_position < current_position:
                    self.db.execute(
                        """UPDATE plan_items SET position = position + 1
                           WHERE plan_id = ? AND id != ? AND position >= ? AND position < ?""",
                        (plan_id, item_id, target_position, current_position),
                    )
                else:
                    self.db.execute(
                        """UPDATE plan_items SET position = position - 1
                           WHERE plan_id = ? AND id != ? AND position <= ? AND position > ?""",
                        (plan_id, item_id, target_position, current_position),
                    )
                updates.append("position = ?")
                params.append(target_position)

        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(item_id)

        self.db.execute(f"UPDATE plan_items SET {', '.join(updates)} WHERE id = ?", params)
        self.db.commit()
        self._record_plan_revision(session_id, plan_id, "item_updated", item_id)

        row = self.db.execute(
            """SELECT id, plan_id, content, status, position, archived_at, created_at, updated_at
               FROM plan_items WHERE id = ?""",
            (item_id,),
        ).fetchone()
        return self._plan_item_row_to_dict(row)

    def build_rollover_handoff(self, session_id: str) -> dict[str, Any] | None:
        plan = self.get_plan(session_id)
        if not plan:
            return None

        handoff: dict[str, Any] = {
            "source_session_id": session_id,
            "source_plan_id": plan["id"],
            "active_plan_id": plan["id"],
        }
        if plan.get("active_goal"):
            handoff["active_goal"] = plan["active_goal"]
        if plan.get("want_to_know"):
            handoff["want_to_know"] = list(plan["want_to_know"])
        next_item = plan.get("next_item")
        if next_item:
            handoff["next_action"] = next_item.get("content")
            handoff["next_item_id"] = next_item.get("id")
            handoff["next_item_status"] = next_item.get("status")
        previous_handoff = plan.get("handoff")
        if previous_handoff:
            handoff["previous_handoff"] = previous_handoff
        return handoff

    def rollover_plan(self, source_session_id: str, target_session_id: str) -> dict | None:
        source_plan = self.get_plan(source_session_id)
        if not source_plan:
            self.bootstrap_session(target_session_id)
            return self.get_plan(target_session_id)

        handoff = self.build_rollover_handoff(source_session_id)
        target_plan_id = self.create_plan(
            target_session_id,
            title=source_plan.get("title", "Session Plan"),
            active_goal=source_plan.get("active_goal", ""),
            want_to_know=source_plan.get("want_to_know", []),
            handoff=handoff,
            activate=True,
            activation_reason="rollover",
            workspace_id=source_plan.get("workspace_id"),
            build_project_id=source_plan.get("build_project_id"),
        )

        for item in self.list_plan_items(source_session_id, source_plan["id"], include_archived=True):
            cloned_item = self.add_plan_item(
                target_session_id,
                target_plan_id,
                item["content"],
                status=item["status"],
                position=item["position"],
            )
            if item["archived"]:
                self.update_plan_item(
                    target_session_id,
                    target_plan_id,
                    cloned_item["id"],
                    archived=True,
                )

        self.set_status(source_session_id, "rolled-over")
        return self.get_plan(target_session_id)

    def should_rollover(self, session_id: str) -> bool:
        if not self.rollover_guard_enabled:
            return False
        plan = self.get_plan(session_id)
        if not plan:
            return False
        guard = plan["context_guard"]
        threshold = guard.get("rollover_threshold", 58000) or 0
        if threshold <= 0:
            return False
        return guard.get("tokens_used", 0) >= threshold

    def delete_plan(self, session_id: str, plan_id: str | None = None) -> bool:
        target_plan_id = plan_id or self.get_active_plan_id(session_id)
        if not target_plan_id:
            return False
        row = self.db.execute(
            "SELECT id FROM plans WHERE session_id = ? AND id = ?",
            (session_id, target_plan_id),
        ).fetchone()
        if not row:
            return False

        remaining = self.db.execute(
            "SELECT id FROM plans WHERE session_id = ? AND id != ? ORDER BY created_at DESC LIMIT 1",
            (session_id, target_plan_id),
        ).fetchone()
        next_active_id = remaining["id"] if remaining else None

        self.db.execute(
            "UPDATE session_plan_state SET active_plan_id = ?, updated_at = ? WHERE session_id = ?",
            (next_active_id, self._now(), session_id),
        )
        self.db.execute("DELETE FROM plan_proposals WHERE plan_id = ?", (target_plan_id,))
        self.db.execute("DELETE FROM plan_revisions WHERE plan_id = ?", (target_plan_id,))
        self.db.execute("DELETE FROM plan_items WHERE plan_id = ?", (target_plan_id,))
        self.db.execute("DELETE FROM plans WHERE session_id = ? AND id = ?", (session_id, target_plan_id))
        self.db.commit()
        return True


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------

def register_planning_routes(app) -> None:
    """Register planning API endpoints."""

    from flask import jsonify, request

    @app.route("/api/sessions/<session_id>/plans", methods=["GET"])
    def list_plans(session_id: str):
        status = request.args.get("status")
        query = request.args.get("q")
        active_arg = request.args.get("active")
        active_only = None
        if active_arg is not None:
            active_only = active_arg.lower() in {"1", "true", "yes"}
        try:
            plans = app.planning.list_plans(session_id, status=status, query=query, active_only=active_only)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"plans": plans})

    @app.route("/api/sessions/<session_id>/plans", methods=["POST"])
    def create_plan(session_id: str):
        data = request.get_json(silent=True) or {}
        try:
            plan_id = app.planning.create_plan(
                session_id,
                active_goal=data.get("active_goal", ""),
                want_to_know=data.get("want_to_know"),
                title=data.get("title", ""),
                handoff=data.get("handoff"),
                activate=data.get("activate", True),
                activation_reason="create",
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify(app.planning.get_plan_by_id(session_id, plan_id)), 201

    @app.route("/api/sessions/<session_id>/plans/<plan_id>/activate", methods=["POST"])
    def activate_plan(session_id: str, plan_id: str):
        try:
            plan = app.planning.activate_plan(session_id, plan_id, reason="user")
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if "not found" in message else 400
            return jsonify({"error": message}), status_code
        return jsonify(plan)

    @app.route("/api/sessions/<session_id>/plans/<plan_id>", methods=["PATCH"])
    def patch_plan_by_id(session_id: str, plan_id: str):
        data = request.get_json(silent=True) or {}
        try:
            plan = app.planning.update_plan(
                session_id,
                plan_id,
                title=data.get("title"),
                active_goal=data.get("active_goal"),
                want_to_know=data.get("want_to_know"),
                handoff=data["handoff"] if "handoff" in data else ...,
                status=data.get("status"),
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if "not found" in message else 400
            return jsonify({"error": message}), status_code
        return jsonify(plan)

    @app.route("/api/sessions/<session_id>/plans/<plan_id>/items", methods=["GET"])
    def list_plan_items(session_id: str, plan_id: str):
        if not app.planning.get_plan_by_id(session_id, plan_id):
            return jsonify({"error": "plan not found for session"}), 404
        include_archived = request.args.get("include_archived", "0") in {"1", "true", "yes"}
        return jsonify({
            "plan_id": plan_id,
            "items": app.planning.list_plan_items(session_id, plan_id, include_archived=include_archived),
        })

    @app.route("/api/sessions/<session_id>/plans/<plan_id>/items", methods=["POST"])
    def add_plan_item(session_id: str, plan_id: str):
        data = request.get_json(silent=True) or {}
        try:
            item = app.planning.add_plan_item(
                session_id,
                plan_id,
                data.get("content", ""),
                status=data.get("status", "todo"),
                position=data.get("position"),
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if "plan not found" in message else 400
            return jsonify({"error": message}), status_code
        return jsonify(item), 201

    @app.route("/api/sessions/<session_id>/plans/<plan_id>/items/<item_id>", methods=["PATCH"])
    def patch_plan_item(session_id: str, plan_id: str, item_id: str):
        data = request.get_json(silent=True) or {}
        try:
            item = app.planning.update_plan_item(
                session_id,
                plan_id,
                item_id,
                content=data.get("content"),
                status=data.get("status"),
                position=data.get("position"),
                archived=data.get("archived"),
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if "not found" in message else 400
            return jsonify({"error": message}), status_code
        return jsonify(item)

    @app.route("/api/sessions/<session_id>/plans/<plan_id>/revisions", methods=["GET"])
    def list_plan_revisions(session_id: str, plan_id: str):
        if not app.planning.get_plan_by_id(session_id, plan_id):
            return jsonify({"error": "plan not found for session"}), 404
        limit = request.args.get("limit", 50, type=int)
        return jsonify({
            "plan_id": plan_id,
            "revisions": app.planning.list_plan_revisions(session_id, plan_id, limit=limit),
        })

    @app.route("/api/sessions/<session_id>/plan-proposals", methods=["GET"])
    def list_plan_proposals(session_id: str):
        status = request.args.get("status")
        plan_id = request.args.get("plan_id")
        limit = request.args.get("limit", 100, type=int)
        try:
            proposals = app.planning.list_plan_proposals(session_id, status=status, plan_id=plan_id, limit=limit)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc) or type(exc).__name__}), 500
        return jsonify({"session_id": session_id, "proposals": proposals})

    @app.route("/api/sessions/<session_id>/plan-proposals", methods=["POST"])
    def submit_plan_proposal(session_id: str):
        data = request.get_json(silent=True) or {}
        try:
            proposal = app.planning.submit_plan_proposal(
                session_id,
                data.get("proposal_type", ""),
                data.get("payload") or {},
                plan_id=data.get("plan_id"),
                summary=data.get("summary", ""),
                proposed_by=data.get("proposed_by", "user"),
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if "not found" in message else 400
            return jsonify({"error": message}), status_code
        return jsonify(proposal), 201

    @app.route("/api/sessions/<session_id>/plan-proposals/<proposal_id>/accept", methods=["POST"])
    def accept_plan_proposal(session_id: str, proposal_id: str):
        data = request.get_json(silent=True) or {}
        try:
            result = app.planning.accept_plan_proposal(
                session_id,
                proposal_id,
                accepted_by=data.get("accepted_by", "clo"),
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if "not found" in message else 400
            return jsonify({"error": message}), status_code
        return jsonify(result)

    @app.route("/api/sessions/<session_id>/plan-proposals/<proposal_id>/reject", methods=["POST"])
    def reject_plan_proposal(session_id: str, proposal_id: str):
        data = request.get_json(silent=True) or {}
        try:
            result = app.planning.reject_plan_proposal(
                session_id,
                proposal_id,
                rejected_by=data.get("rejected_by", "clo"),
                resolution_note=data.get("resolution_note", ""),
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if "not found" in message else 400
            return jsonify({"error": message}), status_code
        return jsonify(result)

    @app.route("/api/sessions/<session_id>/plans/<plan_id>/activation-history", methods=["GET"])
    def list_plan_activation_history(session_id: str, plan_id: str):
        if not app.planning.get_plan_by_id(session_id, plan_id):
            return jsonify({"error": "plan not found for session"}), 404
        limit = request.args.get("limit", 50, type=int)
        return jsonify({
            "plan_id": plan_id,
            "history": app.planning.list_plan_activation_history(session_id, plan_id, limit=limit),
        })

    @app.route("/api/sessions/<session_id>/plans/<plan_id>", methods=["DELETE"])
    def delete_plan(session_id: str, plan_id: str):
        if not app.planning.delete_plan(session_id, plan_id):
            return jsonify({"error": "plan not found for session"}), 404
        next_plan = app.planning.get_plan(session_id)
        return jsonify({
            "deleted": plan_id,
            "session_id": session_id,
            "active_plan_id": next_plan["id"] if next_plan else None,
        })

    @app.route("/api/sessions/<session_id>/plan", methods=["GET"])
    def get_plan(session_id: str):
        plan = app.planning.get_plan(session_id)
        if not plan:
            return jsonify({"error": "no plan found for session"}), 404
        return jsonify(plan)

    @app.route("/api/sessions/<session_id>/plan", methods=["PATCH"])
    def patch_plan(session_id: str):
        data = request.get_json(silent=True) or {}
        plan = app.planning.get_plan(session_id)
        if not plan:
            return jsonify({"error": "no plan found for session"}), 404

        if "active_goal" in data:
            app.planning.update_active_goal(session_id, data["active_goal"])
        if "title" in data:
            app.planning.update_title(session_id, data["title"])
        if "want_to_know" in data:
            app.planning.update_want_to_know(session_id, data["want_to_know"])
        if "context_guard" in data:
            cg = data["context_guard"]
            tokens = cg.get("tokens_used", plan["context_guard"].get("tokens_used", 0))
            app.planning.update_context_guard(session_id, tokens, **{k: v for k, v in cg.items() if k != "tokens_used"})
        if "handoff" in data:
            if data["handoff"] is None:
                app.planning.clear_handoff(session_id)
            else:
                app.planning.set_handoff(session_id, data["handoff"])
        if "status" in data:
            app.planning.set_status(session_id, data["status"])
        return jsonify(app.planning.get_plan(session_id))

    @app.route("/api/sessions/<session_id>/plan/want_to_know", methods=["POST"])
    def add_wtk(session_id: str):
        data = request.get_json(silent=True) or {}
        item = data.get("item", "")
        if not item:
            return jsonify({"error": "item is required"}), 400
        app.planning.add_want_to_know(session_id, item)
        return jsonify(app.planning.get_plan(session_id))

    @app.route("/api/sessions/<session_id>/plan/rollover-check", methods=["GET"])
    def check_rollover(session_id: str):
        should = app.planning.should_rollover(session_id)
        plan = app.planning.get_plan(session_id)
        return jsonify({
            "should_rollover": should,
            "tokens_used": plan["context_guard"].get("tokens_used", 0) if plan else 0,
            "threshold": plan["context_guard"].get("rollover_threshold", 58000) if plan else 58000,
        })

    @app.route("/api/workspaces/<workspace_id>/plans", methods=["GET"])
    def list_workspace_plans(workspace_id: str):
        plans = app.planning.list_plans_by_workspace(workspace_id)
        return jsonify({"workspace_id": workspace_id, "plans": plans})
