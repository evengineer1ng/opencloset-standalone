# Workspace + Build Project manager
#
# Provides workspace grouping and build-project tracking as durable
# abstractions above sessions and plans.  No UI wiring, no scheduler work.

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from api.api.deletion import delete_session_data
from api.db.schema import PROJECT_DELIVERY_STATUSES, new_id


DEFAULT_WORKSPACE_PASTIMES: tuple[dict[str, Any], ...] = (
    {
        "key": "handoff-review",
        "title": "Handoff Review",
        "description": "Review prepared handoff packets for paused sessions.",
        "pastime_type": "operational",
        "source_kind": "worker",
        "candidate_type": "handoff_review",
        "priority": 90,
        "cooldown_seconds": 300,
        "compute_cost": 1,
        "metadata": {"worker_name": "handoff-review-clerk", "signal_type": "handoff_ready"},
    },
    {
        "key": "backlog-review",
        "title": "Backlog Review",
        "description": "Review blocked, deferred, or oversized plan backlogs.",
        "pastime_type": "operational",
        "source_kind": "worker",
        "candidate_type": "backlog_review",
        "priority": 80,
        "cooldown_seconds": 600,
        "compute_cost": 1,
        "metadata": {"worker_name": "backlog-review-clerk", "signal_type": "backlog_review_needed"},
    },
    {
        "key": "context-review",
        "title": "Context Review",
        "description": "Review sessions approaching rollover pressure before they hard-stop.",
        "pastime_type": "operational",
        "source_kind": "worker",
        "candidate_type": "context_review",
        "priority": 88,
        "cooldown_seconds": 300,
        "compute_cost": 1,
        "metadata": {"worker_name": "context-guard-clerk", "signal_type": "context_review_needed"},
    },
    {
        "key": "fresh-eyes-thread-pulling",
        "title": "Fresh-Eyes Thread Pulling",
        "description": "Run a reflective fresh-eyes pass that surfaces missingness threads for later review.",
        "pastime_type": "reflective",
        "source_kind": "pastime",
        "candidate_type": "fresh_eyes_thread_pull",
        "priority": 42,
        "cooldown_seconds": 3600,
        "compute_cost": 6,
        "metadata": {"worker_name": "fresh-eyes-clerk", "signal_type": "thread_pulling_ready", "target_scale": "workspace"},
    },
)

WORKSPACE_PASTIME_STATUSES = {"enabled", "paused", "archived"}
WORKSPACE_ATTENTION_MODES = {"active", "warm", "background", "parked", "paused"}
DEFAULT_ALLOWED_PASTIME_TYPES = [
    "maintenance",
    "operational",
    "reflective",
    "preparatory",
    "autonomous_execution",
]
ATTENTION_MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "active": {
        "baseline_priority": 70,
        "current_attention_level": 80,
        "max_idle_budget": 8,
        "allowed_pastime_types": ["maintenance", "operational", "reflective", "preparatory", "autonomous_execution"],
        "notification_threshold": "significant",
        "freshness_target": "daily",
    },
    "warm": {
        "baseline_priority": 50,
        "current_attention_level": 50,
        "max_idle_budget": 6,
        "allowed_pastime_types": ["maintenance", "operational", "reflective"],
        "notification_threshold": "significant",
        "freshness_target": "weekly",
    },
    "background": {
        "baseline_priority": 25,
        "current_attention_level": 25,
        "max_idle_budget": 2,
        "allowed_pastime_types": ["maintenance", "operational"],
        "notification_threshold": "quiet",
        "freshness_target": "weekly",
    },
    "parked": {
        "baseline_priority": 10,
        "current_attention_level": 10,
        "max_idle_budget": 1,
        "allowed_pastime_types": ["maintenance"],
        "notification_threshold": "silent",
        "freshness_target": "monthly",
    },
    "paused": {
        "baseline_priority": 0,
        "current_attention_level": 0,
        "max_idle_budget": 0,
        "allowed_pastime_types": [],
        "notification_threshold": "silent",
        "freshness_target": "manual",
    },
}
ATTENTION_DIFF_LABELS = {
    "mode": "Mode",
    "baseline_priority": "Baseline priority",
    "current_attention_level": "Current attention",
    "max_idle_budget": "Max idle budget",
    "allowed_pastime_types": "Allowed pastime types",
    "notification_threshold": "Notification threshold",
    "freshness_target": "Freshness target",
    "review_at": "Review date",
    "expires_at": "Expiration date",
    "user_rationale": "User rationale",
}
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


# ---------------------------------------------------------------------------
# Workspace manager
# ---------------------------------------------------------------------------

class WorkspaceManager:
    """CRUD for workspaces and build-projects."""

    def __init__(self, db: sqlite3.Connection, event_logger=None, upload_root: str | None = None) -> None:
        self.db = db
        self.event_logger = event_logger
        root = Path(upload_root) if upload_root else Path(__file__).resolve().parent.parent / "data" / "uploads"
        self.delivery_root = root / "project-deliveries"
        self.delivery_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _get_workspace_row(self, workspace_id: str):
        return self.db.execute(
            "SELECT id, name, description, status, kind, created_at, updated_at "
            "FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()

    # -- Workspaces --

    def create_workspace(self, name: str, description: str = "", kind: str = "general") -> str:
        """Create a workspace. Returns workspace_id."""
        ws_id = new_id()
        now = self._now()
        self.db.execute(
            """INSERT INTO workspaces (id, name, description, kind, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ws_id, name, description, kind, now, now),
        )
        self.db.commit()
        self._ensure_default_pastimes(ws_id)
        self._ensure_workspace_attention_profile(ws_id)
        return ws_id

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        row = self._get_workspace_row(workspace_id)
        if not row:
            return None
        workspace = dict(row)
        workspace["attention_profile"] = self.get_workspace_attention_profile(workspace_id)
        return workspace

    def list_workspaces(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.db.execute(
                "SELECT id, name, description, status, kind, created_at, updated_at "
                "FROM workspaces WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, name, description, status, kind, created_at, updated_at "
                "FROM workspaces ORDER BY created_at DESC"
            ).fetchall()
        workspaces: list[dict[str, Any]] = []
        for row in rows:
            workspace = dict(row)
            workspace["attention_profile"] = self.get_workspace_attention_profile(workspace["id"])
            workspaces.append(workspace)
        return workspaces

    def update_workspace(self, workspace_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update workspace fields (name, description, status, kind). Returns updated row."""
        allowed = {"name", "description", "status", "kind"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_workspace(workspace_id)

        ws = self.get_workspace(workspace_id)
        if not ws:
            return None

        clauses = []
        values = []
        for k, v in updates.items():
            clauses.append(f"{k} = ?")
            values.append(v)
        now = self._now()
        clauses.append("updated_at = ?")
        values.append(now)
        values.append(workspace_id)

        self.db.execute(
            f"UPDATE workspaces SET {', '.join(clauses)} WHERE id = ?",
            values,
        )
        self.db.commit()
        return self.get_workspace(workspace_id)

    def archive_workspace(self, workspace_id: str) -> None:
        self.update_workspace(workspace_id, status="archived")

    def delete_workspace(self, workspace_id: str) -> bool:
        if not self.verify_workspace(workspace_id):
            return False
        session_rows = self.db.execute(
            "SELECT id FROM sessions WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
        for row in session_rows:
            delete_session_data(self.db, row["id"])
        self.db.execute("DELETE FROM workspace_evidence WHERE workspace_id = ?", (workspace_id,))
        self.db.execute("DELETE FROM captures WHERE workspace_id = ?", (workspace_id,))
        self.db.execute("DELETE FROM workspace_pastimes WHERE workspace_id = ?", (workspace_id,))
        self.db.execute("DELETE FROM workspace_attention_profiles WHERE workspace_id = ?", (workspace_id,))
        self.db.execute("DELETE FROM workspace_signals WHERE workspace_id = ?", (workspace_id,))
        self.db.execute("DELETE FROM scheduler_jobs WHERE workspace_id = ?", (workspace_id,))
        self.db.execute("DELETE FROM build_projects WHERE workspace_id = ?", (workspace_id,))
        self.db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        self.db.commit()
        return True

    # -- Workspace attention profiles --

    def _default_workspace_attention_profile(self, workspace_id: str) -> dict[str, Any]:
        return {
            "workspace_id": workspace_id,
            "baseline_priority": 50,
            "current_attention_level": 50,
            "mode": "warm",
            "max_idle_budget": 60,
            "allowed_pastime_types": list(DEFAULT_ALLOWED_PASTIME_TYPES),
            "notification_threshold": "significant",
            "freshness_target": "weekly",
            "review_at": None,
            "expires_at": None,
            "user_rationale": "",
        }

    def _ensure_workspace_attention_profile(self, workspace_id: str) -> None:
        if not self.verify_workspace(workspace_id):
            return
        existing = self.db.execute(
            "SELECT id FROM workspace_attention_profiles WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if existing:
            return
        profile = self._default_workspace_attention_profile(workspace_id)
        now = self._now()
        self.db.execute(
            """INSERT INTO workspace_attention_profiles
               (id, workspace_id, baseline_priority, current_attention_level, mode,
                max_idle_budget, allowed_pastime_types, notification_threshold,
                freshness_target, review_at, expires_at, user_rationale, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(),
                workspace_id,
                int(profile["baseline_priority"]),
                int(profile["current_attention_level"]),
                str(profile["mode"]),
                int(profile["max_idle_budget"]),
                json.dumps(profile["allowed_pastime_types"]),
                str(profile["notification_threshold"]),
                str(profile["freshness_target"]),
                profile["review_at"],
                profile["expires_at"],
                str(profile["user_rationale"]),
                now,
                now,
            ),
        )
        self.db.commit()

    def get_workspace_attention_profile(self, workspace_id: str) -> dict[str, Any] | None:
        if not self.verify_workspace(workspace_id):
            return None
        self._ensure_workspace_attention_profile(workspace_id)
        row = self.db.execute(
            """SELECT id, workspace_id, baseline_priority, current_attention_level, mode,
                      max_idle_budget, allowed_pastime_types, notification_threshold,
                      freshness_target, review_at, expires_at, user_rationale,
                      created_at, updated_at
               FROM workspace_attention_profiles WHERE workspace_id = ?""",
            (workspace_id,),
        ).fetchone()
        return self._workspace_attention_profile_row_to_dict(row) if row else None

    def update_workspace_attention_profile(self, workspace_id: str, **fields: Any) -> dict[str, Any] | None:
        if not self.verify_workspace(workspace_id):
            return None
        profile = self.get_workspace_attention_profile(workspace_id)
        if not profile:
            return None
        allowed = {
            "baseline_priority",
            "current_attention_level",
            "mode",
            "max_idle_budget",
            "allowed_pastime_types",
            "notification_threshold",
            "freshness_target",
            "review_at",
            "expires_at",
            "user_rationale",
        }
        updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
        if not updates:
            return profile
        if "mode" in updates and str(updates["mode"]).lower() not in WORKSPACE_ATTENTION_MODES:
            raise ValueError("invalid workspace attention mode")
        if "allowed_pastime_types" in updates:
            allowed_pastime_types = updates["allowed_pastime_types"]
            if not isinstance(allowed_pastime_types, list) or not all(isinstance(item, str) and item.strip() for item in allowed_pastime_types):
                raise ValueError("allowed_pastime_types must be a list of strings")

        clauses = []
        values: list[Any] = []
        for key, value in updates.items():
            clauses.append(f"{key} = ?")
            if key == "mode":
                values.append(str(value).lower())
            elif key == "allowed_pastime_types":
                values.append(json.dumps(value))
            elif key in {"baseline_priority", "current_attention_level", "max_idle_budget"}:
                values.append(int(value))
            else:
                values.append(value)
        clauses.append("updated_at = ?")
        values.append(self._now())
        values.append(workspace_id)
        self.db.execute(
            f"UPDATE workspace_attention_profiles SET {', '.join(clauses)} WHERE workspace_id = ?",
            values,
        )
        self.db.commit()
        return self.get_workspace_attention_profile(workspace_id)

    def compile_workspace_attention_instruction(
        self,
        workspace_id: str,
        instruction: str,
        *,
        apply: bool = True,
    ) -> dict[str, Any]:
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            raise ValueError("workspace not found")

        normalized_instruction = instruction.strip()
        if not normalized_instruction:
            raise ValueError("instruction is required")

        current_profile = self.get_workspace_attention_profile(workspace_id)
        if not current_profile:
            raise ValueError("workspace attention profile not found")

        compiled_profile = dict(current_profile)
        text = normalized_instruction.lower()
        reasons: dict[str, str] = {}

        mode = self._detect_attention_mode(text)
        if mode is None:
            percent_hint = self._extract_attention_percent(normalized_instruction, workspace["name"])
            if percent_hint is not None:
                mode = self._mode_from_percent(percent_hint)
        if mode:
            defaults = ATTENTION_MODE_DEFAULTS.get(mode, {})
            compiled_profile["mode"] = mode
            reasons["mode"] = f"Detected {mode} attention intent"
            for field, value in defaults.items():
                compiled_profile[field] = list(value) if isinstance(value, list) else value
                reasons.setdefault(field, f"Applied {mode} mode defaults")

        percent = self._extract_attention_percent(normalized_instruction, workspace["name"])
        if percent is not None:
            compiled_profile["baseline_priority"] = percent
            compiled_profile["current_attention_level"] = percent
            reasons["baseline_priority"] = f"Parsed explicit {percent}% weighting for {workspace['name']}"
            reasons["current_attention_level"] = f"Parsed explicit {percent}% weighting for {workspace['name']}"

        pastime_types = self._extract_allowed_pastime_types(text, mode)
        if pastime_types is not None:
            compiled_profile["allowed_pastime_types"] = pastime_types
            reasons["allowed_pastime_types"] = "Derived allowed pastime types from instruction"

        threshold = self._detect_notification_threshold(text)
        if threshold:
            compiled_profile["notification_threshold"] = threshold
            reasons["notification_threshold"] = "Detected notification sensitivity in instruction"

        freshness_target = self._detect_freshness_target(text)
        if freshness_target:
            compiled_profile["freshness_target"] = freshness_target
            reasons["freshness_target"] = "Detected freshness target in instruction"

        schedule_window = self._detect_attention_window(text)
        if schedule_window is not None:
            compiled_profile["review_at"] = schedule_window
            compiled_profile["expires_at"] = schedule_window
            reasons["review_at"] = "Detected temporary attention window"
            reasons["expires_at"] = "Detected temporary attention window"

        explicit_budget = self._detect_max_idle_budget(text)
        if explicit_budget is not None:
            compiled_profile["max_idle_budget"] = explicit_budget
            reasons["max_idle_budget"] = "Derived idle budget from instruction"

        compiled_profile["user_rationale"] = normalized_instruction
        reasons["user_rationale"] = "Stored source instruction as durable rationale"

        diff = self._build_attention_policy_diff(current_profile, compiled_profile, reasons)
        if apply and diff:
            updates = {entry["field"]: compiled_profile[entry["field"]] for entry in diff}
            updated_profile = self.update_workspace_attention_profile(workspace_id, **updates)
        else:
            updated_profile = compiled_profile

        return {
            "workspace_id": workspace_id,
            "workspace_name": workspace["name"],
            "instruction": normalized_instruction,
            "applied": apply,
            "profile": updated_profile,
            "diff": diff,
            "scheduler_effects": self._build_scheduler_effects(updated_profile),
        }

    def _detect_attention_mode(self, text: str) -> str | None:
        if re.search(r"\b(paused?|stop all scheduled work|no scheduled work)\b", text):
            return "paused"
        if re.search(r"\b(park(ed)?|preserve state|no active work)\b", text):
            return "parked"
        if re.search(r"\b(background|keep .* low|minimal interruption|low priority)\b", text):
            return "background"
        if re.search(r"\b(warm|keep .* warm)\b", text):
            return "warm"
        if re.search(r"\b(active|prioriti[sz]e|focus on|turn .* up|foreground priority)\b", text):
            return "active"
        return None

    def _extract_attention_percent(self, instruction: str, workspace_name: str) -> int | None:
        escaped_name = re.escape(workspace_name)
        patterns = [
            rf"{escaped_name}.{{0,40}}?(\d{{1,3}})%",
            rf"(\d{{1,3}})%.{{0,40}}?{escaped_name}",
        ]
        for pattern in patterns:
            match = re.search(pattern, instruction, flags=re.IGNORECASE | re.DOTALL)
            if match:
                return max(0, min(100, int(match.group(1))))
        percentages = re.findall(r"(\d{1,3})%", instruction)
        if len(percentages) == 1:
            return max(0, min(100, int(percentages[0])))
        return None

    def _mode_from_percent(self, percent: int) -> str:
        if percent <= 0:
            return "paused"
        if percent < 15:
            return "parked"
        if percent < 40:
            return "background"
        if percent < 70:
            return "warm"
        return "active"

    def _extract_allowed_pastime_types(self, text: str, mode: str | None) -> list[str] | None:
        if re.search(r"maintenance only|unless it'?s maintenance|unless it is maintenance", text):
            return ["maintenance"]
        if re.search(r"no exploration|pause background exploration|stop exploration", text):
            base = list(ATTENTION_MODE_DEFAULTS.get(mode or "warm", {}).get("allowed_pastime_types", DEFAULT_ALLOWED_PASTIME_TYPES))
            return [item for item in base if item not in {"reflective", "autonomous_execution"}]
        if re.search(r"summari[sz]e only|summaries only", text):
            return ["operational"]
        return None

    def _detect_notification_threshold(self, text: str) -> str | None:
        if re.search(r"notify me immediately|immediately|right away|as soon as", text):
            return "immediate"
        if re.search(r"no user interruption|don'?t interrupt|do not interrupt|silent", text):
            return "silent"
        if re.search(r"quiet|low-noise|minimal interruption", text):
            return "quiet"
        if re.search(r"significant|noteworthy|important", text):
            return "significant"
        return None

    def _detect_freshness_target(self, text: str) -> str | None:
        if re.search(r"hourly|keep .* fresh|fresh feeds", text):
            return "daily"
        if re.search(r"daily|each day|every day", text):
            return "daily"
        if re.search(r"weekly|this week|each week|every week", text):
            return "weekly"
        if re.search(r"monthly|each month|every month", text):
            return "monthly"
        if re.search(r"manual", text):
            return "manual"
        return None

    def _detect_attention_window(self, text: str) -> str | None:
        match = re.search(r"for (?:the )?next (\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve) (hours|hour|days|day|weeks|week|months|month)", text)
        if not match:
            return None
        raw_amount = match.group(1)
        amount = int(raw_amount) if raw_amount.isdigit() else NUMBER_WORDS.get(raw_amount, 0)
        if amount <= 0:
            return None
        unit = match.group(2)
        delta_map = {
            "hour": timedelta(hours=amount),
            "hours": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "days": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "weeks": timedelta(weeks=amount),
            "month": timedelta(days=amount * 30),
            "months": timedelta(days=amount * 30),
        }
        window = datetime.now(timezone.utc) + delta_map[unit]
        return window.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _detect_max_idle_budget(self, text: str) -> int | None:
        if re.search(r"maintenance only|unless it'?s maintenance|unless it is maintenance", text):
            return 1
        if re.search(r"background|low priority|minimal interruption", text):
            return 2
        if re.search(r"warm", text):
            return 6
        if re.search(r"prioriti[sz]e|turn .* up|active", text):
            return 8
        if re.search(r"paused?|no scheduled work", text):
            return 0
        return None

    def _build_attention_policy_diff(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        reasons: dict[str, str],
    ) -> list[dict[str, Any]]:
        diff: list[dict[str, Any]] = []
        for field, label in ATTENTION_DIFF_LABELS.items():
            if before.get(field) == after.get(field):
                continue
            diff.append(
                {
                    "field": field,
                    "label": label,
                    "before": before.get(field),
                    "after": after.get(field),
                    "reason": reasons.get(field, "Updated by compiled policy"),
                }
            )
        return diff

    def _build_scheduler_effects(self, profile: dict[str, Any]) -> dict[str, Any]:
        mode = str(profile.get("mode") or "warm")
        if mode == "paused":
            mode_summary = "All background candidates are ineligible until resumed."
        elif mode == "parked":
            mode_summary = "Only maintenance-style work should remain eligible."
        elif mode == "background":
            mode_summary = "Low-cost operational work is preferred; reflective work is deprioritized."
        elif mode == "active":
            mode_summary = "Operational and reflective work receive the strongest ranking bias."
        else:
            mode_summary = "Operational work stays warm with moderate reflective activity."
        return {
            "mode_summary": mode_summary,
            "allowed_pastime_types": list(profile.get("allowed_pastime_types") or []),
            "max_idle_budget": int(profile.get("max_idle_budget") or 0),
            "baseline_priority": int(profile.get("baseline_priority") or 0),
            "current_attention_level": int(profile.get("current_attention_level") or 0),
            "notification_threshold": str(profile.get("notification_threshold") or "significant"),
            "freshness_target": str(profile.get("freshness_target") or "weekly"),
        }

    def _workspace_attention_profile_row_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "baseline_priority": row["baseline_priority"],
            "current_attention_level": row["current_attention_level"],
            "mode": row["mode"],
            "max_idle_budget": row["max_idle_budget"],
            "allowed_pastime_types": json.loads(row["allowed_pastime_types"] or "[]"),
            "notification_threshold": row["notification_threshold"],
            "freshness_target": row["freshness_target"],
            "review_at": row["review_at"],
            "expires_at": row["expires_at"],
            "user_rationale": row["user_rationale"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- Workspace pastimes --

    def _ensure_default_pastimes(self, workspace_id: str) -> None:
        if not self.verify_workspace(workspace_id):
            return
        for definition in DEFAULT_WORKSPACE_PASTIMES:
            existing = self.db.execute(
                "SELECT id FROM workspace_pastimes WHERE workspace_id = ? AND key = ?",
                (workspace_id, definition["key"]),
            ).fetchone()
            if existing:
                continue
            pastime_id = new_id()
            now = self._now()
            self.db.execute(
                """INSERT INTO workspace_pastimes
                   (id, workspace_id, key, title, description, pastime_type, source_kind, candidate_type,
                    status, priority, cooldown_seconds, compute_cost, metadata, config,
                    created_at, updated_at, last_selected_at, last_completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'enabled', ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                (
                    pastime_id,
                    workspace_id,
                    definition["key"],
                    definition["title"],
                    definition.get("description", ""),
                    definition.get("pastime_type", "operational"),
                    definition.get("source_kind", "worker"),
                    definition.get("candidate_type"),
                    int(definition.get("priority", 50)),
                    int(definition.get("cooldown_seconds", 300)),
                    int(definition.get("compute_cost", 1)),
                    json.dumps(definition.get("metadata") or {}),
                    json.dumps(definition.get("config") or {}),
                    now,
                    now,
                ),
            )
        self.db.commit()

    def get_workspace_pastime(self, workspace_id: str, pastime_id: str) -> dict[str, Any] | None:
        self._ensure_default_pastimes(workspace_id)
        row = self.db.execute(
            """SELECT id, workspace_id, key, title, description, pastime_type, source_kind, candidate_type,
                      status, priority, cooldown_seconds, compute_cost, metadata, config,
                      created_at, updated_at, last_selected_at, last_completed_at
               FROM workspace_pastimes WHERE workspace_id = ? AND id = ?""",
            (workspace_id, pastime_id),
        ).fetchone()
        return self._workspace_pastime_row_to_dict(row) if row else None

    def list_workspace_pastimes(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not self.verify_workspace(workspace_id):
            return []
        self._ensure_default_pastimes(workspace_id)
        clauses = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if status:
            clauses.append("status = ?")
            values.append(status)
        values.append(max(1, min(limit, 200)))
        rows = self.db.execute(
            f"""SELECT id, workspace_id, key, title, description, pastime_type, source_kind, candidate_type,
                       status, priority, cooldown_seconds, compute_cost, metadata, config,
                       created_at, updated_at, last_selected_at, last_completed_at
                FROM workspace_pastimes
                WHERE {' AND '.join(clauses)}
                ORDER BY priority DESC, updated_at DESC, created_at DESC LIMIT ?""",
            values,
        ).fetchall()
        return [self._workspace_pastime_row_to_dict(row) for row in rows]

    def update_workspace_pastime(self, workspace_id: str, pastime_id: str, **fields: Any) -> dict[str, Any] | None:
        pastime = self.get_workspace_pastime(workspace_id, pastime_id)
        if not pastime:
            return None
        allowed = {
            "title",
            "description",
            "pastime_type",
            "source_kind",
            "candidate_type",
            "status",
            "priority",
            "cooldown_seconds",
            "compute_cost",
            "metadata",
            "config",
            "last_selected_at",
            "last_completed_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
        if not updates:
            return pastime
        if "status" in updates and str(updates["status"]) not in WORKSPACE_PASTIME_STATUSES:
            raise ValueError("invalid workspace pastime status")

        clauses = []
        values: list[Any] = []
        for key, value in updates.items():
            clauses.append(f"{key} = ?")
            if key in {"metadata", "config"} and isinstance(value, dict):
                values.append(json.dumps(value))
            else:
                values.append(value)
        clauses.append("updated_at = ?")
        values.append(self._now())
        values.append(pastime_id)
        self.db.execute(
            f"UPDATE workspace_pastimes SET {', '.join(clauses)} WHERE id = ?",
            values,
        )
        self.db.commit()
        return self.get_workspace_pastime(workspace_id, pastime_id)

    def mark_workspace_pastime_selected(
        self,
        workspace_id: str,
        pastime_id: str,
        *,
        selected_at: str | None = None,
    ) -> dict[str, Any] | None:
        return self.update_workspace_pastime(
            workspace_id,
            pastime_id,
            last_selected_at=selected_at or self._now(),
        )

    def _workspace_pastime_row_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "key": row["key"],
            "title": row["title"],
            "description": row["description"],
            "pastime_type": row["pastime_type"],
            "source_kind": row["source_kind"],
            "candidate_type": row["candidate_type"],
            "status": row["status"],
            "priority": row["priority"],
            "cooldown_seconds": row["cooldown_seconds"],
            "compute_cost": row["compute_cost"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "config": json.loads(row["config"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_selected_at": row["last_selected_at"],
            "last_completed_at": row["last_completed_at"],
        }

    # -- Build projects --

    def create_build_project(
        self, workspace_id: str, name: str, description: str = ""
    ) -> str:
        """Create a build project within a workspace. Returns project_id."""
        ws = self.get_workspace(workspace_id)
        if not ws:
            raise ValueError("workspace not found")
        proj_id = new_id()
        now = self._now()
        self.db.execute(
            """INSERT INTO build_projects (id, workspace_id, name, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (proj_id, workspace_id, name, description, now, now),
        )
        self.db.commit()
        return proj_id

    def get_build_project(self, project_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT id, workspace_id, name, description, status, created_at, updated_at "
            "FROM build_projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def list_build_projects(
        self, workspace_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        ws = self.get_workspace(workspace_id)
        if not ws:
            return []
        if status:
            rows = self.db.execute(
                "SELECT id, workspace_id, name, description, status, created_at, updated_at "
                "FROM build_projects WHERE workspace_id = ? AND status = ? "
                "ORDER BY created_at DESC",
                (workspace_id, status),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, workspace_id, name, description, status, created_at, updated_at "
                "FROM build_projects WHERE workspace_id = ? "
                "ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_build_project(
        self, project_id: str, **fields: Any
    ) -> dict[str, Any] | None:
        """Update build project fields (name, description, status). Returns updated row."""
        allowed = {"name", "description", "status"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_build_project(project_id)

        proj = self.get_build_project(project_id)
        if not proj:
            return None

        clauses = []
        values = []
        for k, v in updates.items():
            clauses.append(f"{k} = ?")
            values.append(v)
        now = self._now()
        clauses.append("updated_at = ?")
        values.append(now)
        values.append(project_id)

        self.db.execute(
            f"UPDATE build_projects SET {', '.join(clauses)} WHERE id = ?",
            values,
        )
        self.db.commit()
        return self.get_build_project(project_id)

    def delete_build_project(self, workspace_id: str, project_id: str) -> bool:
        project = self.get_build_project(project_id)
        if not project or project["workspace_id"] != workspace_id:
            return False
        session_rows = self.db.execute(
            "SELECT id FROM sessions WHERE build_project_id = ?",
            (project_id,),
        ).fetchall()
        for row in session_rows:
            delete_session_data(self.db, row["id"])
        delivery_rows = self.db.execute(
            "SELECT storage_path FROM project_deliveries WHERE build_project_id = ?",
            (project_id,),
        ).fetchall()
        for row in delivery_rows:
            storage_path = str(row["storage_path"] or "").strip()
            if storage_path:
                try:
                    Path(storage_path).unlink(missing_ok=True)
                except OSError:
                    pass
        self.db.execute("DELETE FROM project_deliveries WHERE build_project_id = ?", (project_id,))
        self.db.execute("DELETE FROM workspace_evidence WHERE build_project_id = ?", (project_id,))
        self.db.execute("DELETE FROM captures WHERE build_project_id = ?", (project_id,))
        self.db.execute("DELETE FROM build_projects WHERE id = ?", (project_id,))
        self.db.commit()
        return True

    # -- Project deliveries --

    def publish_project_delivery(
        self,
        workspace_id: str,
        project_id: str,
        upload: FileStorage,
        *,
        target_device_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.get_build_project(project_id)
        if not project or project["workspace_id"] != workspace_id:
            raise ValueError("project does not belong to this workspace")

        if session_id:
            session_row = self.db.execute(
                "SELECT id, workspace_id, build_project_id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not session_row:
                raise ValueError("session not found")
            if session_row["workspace_id"] != workspace_id:
                raise ValueError("session does not belong to this workspace")
            if session_row["build_project_id"] and session_row["build_project_id"] != project_id:
                raise ValueError("session does not belong to this build project")

        delivery_id = new_id()
        file_name = secure_filename(upload.filename or "") or f"delivery-{delivery_id}"
        storage_path = self._allocate_delivery_storage_path(project_id, delivery_id, file_name)
        upload.save(storage_path)
        size_bytes = storage_path.stat().st_size if storage_path.exists() else 0
        mime_type = str(upload.mimetype or "application/octet-stream")
        artifact_kind = self._infer_artifact_kind(file_name, mime_type)
        merged_metadata = dict(metadata or {})
        merged_metadata.update(
            {
                "delivery_id": delivery_id,
                "file_name": file_name,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "artifact_kind": artifact_kind,
                "target_device_id": target_device_id,
            }
        )

        capture_id = self.create_workspace_capture(
            workspace_id,
            source="project_delivery",
            event_type=f"{artifact_kind}_delivery",
            content=self._delivery_capture_content(file_name, artifact_kind, target_device_id, status="ready"),
            media_url=str(storage_path),
            metadata=merged_metadata,
            session_id=session_id,
            build_project_id=project_id,
            status="pending",
        )

        now = self._now()
        self.db.execute(
            """
            INSERT INTO project_deliveries (
                id, workspace_id, build_project_id, session_id, capture_id, target_device_id,
                artifact_kind, file_name, mime_type, size_bytes, storage_path, status,
                metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delivery_id,
                workspace_id,
                project_id,
                session_id,
                capture_id,
                target_device_id,
                artifact_kind,
                file_name,
                mime_type,
                size_bytes,
                str(storage_path),
                "ready",
                json.dumps(merged_metadata),
                now,
                now,
            ),
        )
        self.db.commit()
        delivery = self.get_project_delivery(workspace_id, delivery_id)
        if not delivery:
            raise ValueError("delivery did not persist")
        return delivery

    def publish_project_delivery_path(
        self,
        workspace_id: str,
        project_id: str,
        artifact_path: str | Path,
        *,
        target_device_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = Path(artifact_path)
        if not path.exists() or not path.is_file():
            raise ValueError("artifact path not found")
        mime_type = "application/vnd.android.package-archive" if path.suffix.lower() == ".apk" else "application/octet-stream"
        with path.open("rb") as stream:
            upload = FileStorage(stream=stream, filename=path.name, content_type=mime_type)
            return self.publish_project_delivery(
                workspace_id,
                project_id,
                upload,
                target_device_id=target_device_id,
                session_id=session_id,
                metadata=metadata,
            )

    def list_project_deliveries(
        self,
        workspace_id: str,
        project_id: str,
        *,
        target_device_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        project = self.get_build_project(project_id)
        if not project or project["workspace_id"] != workspace_id:
            return []
        clauses = ["workspace_id = ?", "build_project_id = ?"]
        values: list[Any] = [workspace_id, project_id]
        if target_device_id:
            clauses.append("target_device_id = ?")
            values.append(target_device_id)
        if status:
            clauses.append("status = ?")
            values.append(status)
        values.append(max(1, min(limit, 200)))
        rows = self.db.execute(
            f"""
            SELECT id, workspace_id, build_project_id, session_id, capture_id, target_device_id,
                   artifact_kind, file_name, mime_type, size_bytes, status, metadata,
                   created_at, updated_at, downloaded_at, installed_at
            FROM project_deliveries
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            values,
        ).fetchall()
        return [self._project_delivery_row_to_dict(row) for row in rows]

    def list_device_deliveries(
        self,
        workspace_id: str,
        device_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not self.verify_workspace(workspace_id):
            return []
        clauses = ["workspace_id = ?", "(target_device_id IS NULL OR target_device_id = '' OR target_device_id = ?)"]
        values: list[Any] = [workspace_id, device_id]
        if status:
            clauses.append("status = ?")
            values.append(status)
        values.append(max(1, min(limit, 200)))
        rows = self.db.execute(
            f"""
            SELECT id, workspace_id, build_project_id, session_id, capture_id, target_device_id,
                   artifact_kind, file_name, mime_type, size_bytes, status, metadata,
                   created_at, updated_at, downloaded_at, installed_at
            FROM project_deliveries
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            values,
        ).fetchall()
        return [self._project_delivery_row_to_dict(row) for row in rows]

    def build_mobile_bootstrap(
        self,
        *,
        device_id: str,
        workspace_status: str | None = None,
        capture_limit: int = 20,
        delivery_limit: int = 20,
        session_limit: int = 12,
        event_limit: int = 40,
    ) -> dict[str, Any]:
        workspaces = self.list_workspaces(status=workspace_status)
        workspace_ids = [str(workspace["id"]) for workspace in workspaces]
        projects_by_workspace = {
            workspace_id: self.list_build_projects(workspace_id)
            for workspace_id in workspace_ids
        }
        deliveries_by_workspace = {
            workspace_id: self.list_device_deliveries(
                workspace_id,
                device_id,
                status=None,
                limit=delivery_limit,
            )
            for workspace_id in workspace_ids
        }
        sessions_by_workspace: dict[str, list[dict[str, Any]]] = {
            workspace_id: []
            for workspace_id in workspace_ids
        }

        recent_captures: list[dict[str, Any]] = []
        recent_session_events: list[dict[str, Any]] = []
        if workspace_ids:
            placeholders = ", ".join("?" for _ in workspace_ids)
            session_rows = self.db.execute(
                f"""
                SELECT s.id, s.label, s.model, s.provider, s.status, s.token_count, s.context_window,
                       s.workspace_id, s.build_project_id, s.created_at, s.updated_at,
                       r.id AS current_run_id, r.status AS current_run_status, r.turn_number AS current_run_turn_number
                FROM sessions s
                LEFT JOIN runs r ON r.session_id = s.id AND r.status = 'running'
                WHERE s.workspace_id IN ({placeholders})
                ORDER BY s.updated_at DESC, s.created_at DESC
                """,
                workspace_ids,
            ).fetchall()
            session_counts: dict[str, int] = {}
            bounded_session_limit = max(1, min(session_limit, 100))
            for row in session_rows:
                workspace_id = str(row["workspace_id"] or "")
                if not workspace_id:
                    continue
                if session_counts.get(workspace_id, 0) >= bounded_session_limit:
                    continue
                sessions_by_workspace.setdefault(workspace_id, []).append(self._mobile_session_row_to_dict(row))
                session_counts[workspace_id] = session_counts.get(workspace_id, 0) + 1

            event_rows = self.db.execute(
                f"""
                SELECT e.id, e.session_id, s.workspace_id, s.build_project_id, e.run_id,
                       e.event_type, e.payload, e.created_at
                FROM agent_events e
                JOIN sessions s ON s.id = e.session_id
                WHERE s.workspace_id IN ({placeholders})
                ORDER BY e.created_at DESC, e.rowid DESC
                LIMIT ?
                """,
                (*workspace_ids, max(1, min(event_limit, 200))),
            ).fetchall()
            recent_session_events = [self._mobile_session_event_row_to_dict(row) for row in event_rows]

            rows = self.db.execute(
                f"""
                SELECT id, workspace_id, build_project_id, session_id, run_id, source, event_type, content,
                       media_url, metadata, status, received_at, processed_at
                FROM captures
                WHERE workspace_id IN ({placeholders})
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (*workspace_ids, max(1, min(capture_limit, 100))),
            ).fetchall()
            recent_captures = [self._workspace_capture_row_to_dict(row) for row in rows]

        return {
            "device_id": device_id,
            "workspaces": workspaces,
            "projects_by_workspace": projects_by_workspace,
            "deliveries_by_workspace": deliveries_by_workspace,
            "sessions_by_workspace": sessions_by_workspace,
            "recent_captures": recent_captures,
            "recent_session_events": recent_session_events,
            "generated_at": self._now(),
        }

    def get_project_delivery(self, workspace_id: str, delivery_id: str) -> dict[str, Any] | None:
        row = self.get_project_delivery_record(workspace_id, delivery_id)
        return self._project_delivery_row_to_dict(row) if row else None

    def get_project_delivery_record(self, workspace_id: str, delivery_id: str):
        return self.db.execute(
            """
            SELECT id, workspace_id, build_project_id, session_id, capture_id, target_device_id,
                   artifact_kind, file_name, mime_type, size_bytes, storage_path, status, metadata,
                   created_at, updated_at, downloaded_at, installed_at
            FROM project_deliveries
            WHERE workspace_id = ? AND id = ?
            """,
            (workspace_id, delivery_id),
        ).fetchone()

    def update_project_delivery(
        self,
        workspace_id: str,
        delivery_id: str,
        *,
        status: str,
        device_id: str | None = None,
        note: str | None = None,
        metadata_update: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = self.get_project_delivery_record(workspace_id, delivery_id)
        if not row:
            return None

        metadata = self._json_object(row["metadata"])
        if metadata_update:
            metadata.update(metadata_update)
        if device_id:
            metadata["device_id"] = device_id
        if note:
            metadata["note"] = note

        now = self._now()
        downloaded_at = row["downloaded_at"]
        installed_at = row["installed_at"]
        if status in {"downloaded", "installed"} and not downloaded_at:
            downloaded_at = now
        if status == "installed":
            installed_at = now

        self.db.execute(
            """
            UPDATE project_deliveries
            SET status = ?,
                target_device_id = COALESCE(?, target_device_id),
                metadata = ?,
                updated_at = ?,
                downloaded_at = ?,
                installed_at = ?
            WHERE id = ?
            """,
            (
                status,
                device_id,
                json.dumps(metadata),
                now,
                downloaded_at,
                installed_at,
                delivery_id,
            ),
        )

        capture_id = str(row["capture_id"] or "")
        if capture_id:
            capture_status = self._capture_status_for_delivery(status)
            capture_content = self._delivery_capture_content(
                str(row["file_name"] or "delivery"),
                str(row["artifact_kind"] or "binary"),
                device_id or str(row["target_device_id"] or "") or None,
                status=status,
                note=note,
            )
            self.update_workspace_capture(
                workspace_id,
                capture_id,
                content=capture_content,
                metadata=metadata,
                status=capture_status,
                processed_at=now if capture_status != "pending" else None,
            )

        self.db.commit()
        return self.get_project_delivery(workspace_id, delivery_id)

    def _allocate_delivery_storage_path(self, project_id: str, delivery_id: str, file_name: str) -> Path:
        extension = Path(file_name).suffix
        project_dir = self.delivery_root / str(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir / f"{delivery_id}{extension}"

    def _project_delivery_row_to_dict(self, row) -> dict[str, Any]:
        metadata = self._json_object(row["metadata"])
        delivery_id = str(row["id"])
        workspace_id = str(row["workspace_id"])
        return {
            "id": delivery_id,
            "workspace_id": workspace_id,
            "build_project_id": row["build_project_id"],
            "session_id": row["session_id"],
            "capture_id": row["capture_id"],
            "target_device_id": row["target_device_id"],
            "artifact_kind": row["artifact_kind"],
            "file_name": row["file_name"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "status": row["status"],
            "metadata": metadata,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "downloaded_at": row["downloaded_at"],
            "installed_at": row["installed_at"],
            "download_url": f"/api/workspaces/{workspace_id}/deliveries/{delivery_id}/download",
            "ack_url": f"/api/workspaces/{workspace_id}/deliveries/{delivery_id}",
        }

    def _mobile_session_row_to_dict(self, row) -> dict[str, Any]:
        current_run = None
        if row["current_run_id"]:
            current_run = {
                "id": row["current_run_id"],
                "status": row["current_run_status"],
                "turn_number": row["current_run_turn_number"],
            }
        return {
            "id": row["id"],
            "label": row["label"],
            "model": row["model"],
            "provider": row["provider"],
            "status": row["status"],
            "token_count": row["token_count"],
            "context_window": row["context_window"],
            "workspace_id": row["workspace_id"],
            "build_project_id": row["build_project_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "current_run": current_run,
        }

    def _mobile_session_event_row_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "workspace_id": row["workspace_id"],
            "build_project_id": row["build_project_id"],
            "run_id": row["run_id"],
            "type": row["event_type"],
            "data": self._json_object(row["payload"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _infer_artifact_kind(file_name: str, mime_type: str) -> str:
        extension = Path(file_name).suffix.lower()
        if extension == ".apk" or str(mime_type or "").lower() == "application/vnd.android.package-archive":
            return "apk"
        return "binary"

    @staticmethod
    def _capture_status_for_delivery(status: str) -> str:
        if status in {"failed", "cancelled", "expired"}:
            return "failed"
        if status in {"downloaded", "installed"}:
            return "processed"
        return "pending"

    @staticmethod
    def _delivery_capture_content(
        file_name: str,
        artifact_kind: str,
        device_id: str | None,
        *,
        status: str,
        note: str | None = None,
    ) -> str:
        target = device_id or "unscoped-device"
        line = f"{artifact_kind.upper()} delivery {status}: {file_name} -> {target}"
        if note:
            line += f" ({note})"
        return line

    @staticmethod
    def _json_object(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if not raw:
            return {}
        try:
            loaded = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    # -- Cross-reference helpers --

    def verify_workspace(self, workspace_id: str) -> bool:
        """Return True if workspace exists."""
        return self._get_workspace_row(workspace_id) is not None

    def verify_project(self, project_id: str) -> dict[str, Any] | None:
        """Return project row if exists, else None."""
        return self.get_build_project(project_id)

    def get_project_workspace(self, project_id: str) -> str | None:
        """Return workspace_id for a given project, or None."""
        proj = self.get_build_project(project_id)
        return proj["workspace_id"] if proj else None

    # -- Workspace evidence --

    def create_workspace_evidence(
        self,
        workspace_id: str,
        *,
        title: str,
        summary: str,
        evidence_type: str = "note",
        content: str = "",
        source_kind: str = "note",
        status: str = "active",
        session_id: str | None = None,
        build_project_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if not self.verify_workspace(workspace_id):
            raise ValueError("workspace not found")
        if build_project_id:
            project = self.get_build_project(build_project_id)
            if not project or project["workspace_id"] != workspace_id:
                raise ValueError("project does not belong to this workspace")
        if session_id:
            session = self.db.execute(
                "SELECT id, workspace_id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not session:
                raise ValueError("session not found")
            if session["workspace_id"] != workspace_id:
                raise ValueError("session does not belong to this workspace")

        evidence_id = new_id()
        now = self._now()
        self.db.execute(
            """INSERT INTO workspace_evidence
               (id, workspace_id, session_id, build_project_id, evidence_type, title, summary,
                content, source_kind, status, tags, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_id,
                workspace_id,
                session_id,
                build_project_id,
                evidence_type,
                title,
                summary,
                content,
                source_kind,
                status,
                json.dumps(tags or []),
                json.dumps(metadata or {}),
                now,
                now,
            ),
        )
        self.db.commit()
        return evidence_id

    def get_workspace_evidence(self, workspace_id: str, evidence_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT id, workspace_id, session_id, build_project_id, evidence_type, title, summary,
                      content, source_kind, status, tags, metadata, created_at, updated_at
               FROM workspace_evidence WHERE workspace_id = ? AND id = ?""",
            (workspace_id, evidence_id),
        ).fetchone()
        return self._workspace_evidence_row_to_dict(row) if row else None

    def list_workspace_evidence(
        self,
        workspace_id: str,
        *,
        evidence_type: str | None = None,
        status: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not self.verify_workspace(workspace_id):
            return []

        clauses = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if evidence_type:
            clauses.append("evidence_type = ?")
            values.append(evidence_type)
        if status:
            clauses.append("status = ?")
            values.append(status)
        if session_id:
            clauses.append("session_id = ?")
            values.append(session_id)
        values.append(max(1, min(limit, 200)))

        rows = self.db.execute(
            f"""SELECT id, workspace_id, session_id, build_project_id, evidence_type, title, summary,
                       content, source_kind, status, tags, metadata, created_at, updated_at
                FROM workspace_evidence
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC, created_at DESC LIMIT ?""",
            values,
        ).fetchall()
        return [self._workspace_evidence_row_to_dict(row) for row in rows]

    def _workspace_evidence_row_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "session_id": row["session_id"],
            "build_project_id": row["build_project_id"],
            "evidence_type": row["evidence_type"],
            "title": row["title"],
            "summary": row["summary"],
            "content": row["content"],
            "source_kind": row["source_kind"],
            "status": row["status"],
            "tags": json.loads(row["tags"] or "[]"),
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- Workspace captures --

    def create_workspace_capture(
        self,
        workspace_id: str,
        *,
        source: str,
        event_type: str,
        content: str,
        media_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
        build_project_id: str | None = None,
        status: str = "pending",
    ) -> str:
        if not self.verify_workspace(workspace_id):
            raise ValueError("workspace not found")
        session_row = None
        if session_id:
            session_row = self.db.execute(
                "SELECT id, workspace_id, build_project_id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not session_row:
                raise ValueError("session not found")
            if session_row["workspace_id"] != workspace_id:
                raise ValueError("session does not belong to this workspace")
        if build_project_id:
            project = self.get_build_project(build_project_id)
            if not project or project["workspace_id"] != workspace_id:
                raise ValueError("project does not belong to this workspace")
        elif session_row and session_row["build_project_id"]:
            build_project_id = session_row["build_project_id"]

        capture_id = new_id()
        self.db.execute(
            """INSERT INTO captures
               (id, workspace_id, build_project_id, session_id, source, event_type, content, media_url, metadata, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                capture_id,
                workspace_id,
                build_project_id,
                session_id,
                source,
                event_type,
                content,
                media_url,
                json.dumps(metadata or {}),
                status,
            ),
        )
        self.db.commit()
        return capture_id

    def create_workspace_activity_capture(
        self,
        *,
        source: str,
        event_type: str,
        content: str,
        workspace_id: str | None = None,
        session_id: str | None = None,
        build_project_id: str | None = None,
        media_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> dict[str, Any] | None:
        resolved_workspace_id = workspace_id
        resolved_build_project_id = build_project_id
        if session_id:
            session_row = self.db.execute(
                "SELECT id, workspace_id, build_project_id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not session_row:
                return None
            resolved_workspace_id = resolved_workspace_id or session_row["workspace_id"]
            resolved_build_project_id = resolved_build_project_id or session_row["build_project_id"]
        if not resolved_workspace_id:
            return None
        capture_id = self.create_workspace_capture(
            resolved_workspace_id,
            source=source,
            event_type=event_type,
            content=content,
            media_url=media_url,
            metadata=metadata,
            session_id=session_id,
            build_project_id=resolved_build_project_id,
            status=status,
        )
        return self.get_workspace_capture(resolved_workspace_id, capture_id)

    def get_workspace_capture(self, workspace_id: str, capture_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT id, workspace_id, build_project_id, session_id, run_id, source, event_type, content,
                      media_url, metadata, status, received_at, processed_at
               FROM captures WHERE workspace_id = ? AND id = ?""",
            (workspace_id, capture_id),
        ).fetchone()
        return self._workspace_capture_row_to_dict(row) if row else None

    def update_workspace_capture(
        self,
        workspace_id: str,
        capture_id: str,
        **fields: Any,
    ) -> dict[str, Any] | None:
        capture = self.get_workspace_capture(workspace_id, capture_id)
        if not capture:
            return None
        allowed = {"content", "media_url", "metadata", "status", "processed_at"}
        updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
        if not updates:
            return capture

        clauses = []
        values: list[Any] = []
        for key, value in updates.items():
            clauses.append(f"{key} = ?")
            if key == "metadata" and isinstance(value, dict):
                values.append(json.dumps(value))
            else:
                values.append(value)
        values.append(capture_id)
        self.db.execute(
            f"UPDATE captures SET {', '.join(clauses)} WHERE id = ?",
            values,
        )
        self.db.commit()
        return self.get_workspace_capture(workspace_id, capture_id)

    def list_workspace_captures(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not self.verify_workspace(workspace_id):
            return []
        clauses = ["workspace_id = ?"]
        values: list[Any] = [workspace_id]
        if status:
          clauses.append("status = ?")
          values.append(status)
        if session_id:
          clauses.append("session_id = ?")
          values.append(session_id)
        values.append(max(1, min(limit, 200)))
        rows = self.db.execute(
            f"""SELECT id, workspace_id, build_project_id, session_id, run_id, source, event_type, content,
                       media_url, metadata, status, received_at, processed_at
                FROM captures
                WHERE {' AND '.join(clauses)}
                ORDER BY received_at DESC LIMIT ?""",
            values,
        ).fetchall()
        return [self._workspace_capture_row_to_dict(row) for row in rows]

    def promote_capture_to_evidence(
        self,
        workspace_id: str,
        capture_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        evidence_type: str = "capture",
        source_kind: str = "capture",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        capture = self.get_workspace_capture(workspace_id, capture_id)
        if not capture:
            raise ValueError("workspace capture not found")
        evidence_id = self.create_workspace_evidence(
            workspace_id,
            title=title or f"Capture: {capture['event_type']}",
            summary=summary or capture["content"][:180],
            evidence_type=evidence_type,
            content=capture["content"],
            source_kind=source_kind,
            session_id=capture["session_id"],
            build_project_id=capture["build_project_id"],
            tags=tags,
            metadata={
                "capture_id": capture_id,
                "capture_source": capture["source"],
                "capture_event_type": capture["event_type"],
            },
        )
        now = self._now()
        metadata = dict(capture.get("metadata") or {})
        metadata.update({"promoted_evidence_id": evidence_id})
        self.db.execute(
            "UPDATE captures SET status = ?, processed_at = ?, metadata = ? WHERE id = ?",
            ("processed", now, json.dumps(metadata), capture_id),
        )
        self.db.commit()
        evidence = self.get_workspace_evidence(workspace_id, evidence_id)
        updated_capture = self.get_workspace_capture(workspace_id, capture_id)
        if not evidence or not updated_capture:
            raise ValueError("capture promotion did not persist correctly")
        return {"capture": updated_capture, "evidence": evidence}

    def _workspace_capture_row_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "build_project_id": row["build_project_id"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "source": row["source"],
            "event_type": row["event_type"],
            "content": row["content"],
            "media_url": row["media_url"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "status": row["status"],
            "received_at": row["received_at"],
            "processed_at": row["processed_at"],
        }


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_workspace_routes(app: Flask) -> None:
    """Register workspace + build-project REST routes."""
    wm: WorkspaceManager = app.workspaces  # type: ignore

    def _mobile_project_root() -> Path:
        configured = str(app.config.get("MOBILE_PROJECT_ROOT") or "").strip()
        if configured:
            return Path(configured)
        return Path(__file__).resolve().parents[2] / "mobile"

    def _resolve_mobile_variant(requested_variant: str | None) -> tuple[str, str]:
        normalized = str(requested_variant or "debug").strip().lower()
        if normalized == "release":
            return "release", ":app:assembleRelease"
        return "debug", ":app:assembleDebug"

    def _mobile_build_env() -> dict[str, str]:
        env = os.environ.copy()
        if os.name != "nt":
            return env

        local_app_data = env.get("LOCALAPPDATA", "")
        java_home = Path(env.get("JAVA_HOME", "") or Path(local_app_data) / "Programs" / "Java" / "temurin-17")
        android_sdk = Path(env.get("ANDROID_SDK_ROOT", "") or Path(local_app_data) / "Android" / "Sdk")

        if java_home.exists():
            env["JAVA_HOME"] = str(java_home)
        if android_sdk.exists():
            env.setdefault("ANDROID_SDK_ROOT", str(android_sdk))
            env.setdefault("ANDROID_HOME", str(android_sdk))

        path_parts = env.get("PATH", "").split(os.pathsep)
        prepend: list[str] = []
        for candidate in [
            java_home / "bin",
            android_sdk / "platform-tools",
            android_sdk / "cmdline-tools" / "latest" / "bin",
        ]:
            candidate_str = str(candidate)
            if candidate.exists() and candidate_str not in path_parts:
                prepend.append(candidate_str)
        if prepend:
            env["PATH"] = os.pathsep.join([*prepend, env.get("PATH", "")])
        return env

    def _resolve_built_apk_path(mobile_root: Path, variant: str) -> Path:
        output_dir = mobile_root / "app" / "build" / "outputs" / "apk" / variant
        metadata_path = output_dir / "output-metadata.json"
        if metadata_path.exists():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"failed to read APK output metadata: {exc}") from exc
            elements = payload.get("elements") or []
            if elements and isinstance(elements[0], dict):
                output_file = str(elements[0].get("outputFile") or "").strip()
                if output_file:
                    candidate = output_dir / output_file
                    if candidate.exists():
                        return candidate

        candidates = sorted(output_dir.glob("*.apk"), key=lambda entry: entry.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]
        raise RuntimeError(f"built APK not found under {output_dir}")

    def _run_mobile_build_and_resolve_artifact(variant: str) -> tuple[Path, str]:
        mobile_root = _mobile_project_root()
        if not mobile_root.exists():
            raise RuntimeError(f"mobile project root not found: {mobile_root}")

        normalized_variant, gradle_task = _resolve_mobile_variant(variant)
        gradle_wrapper = mobile_root / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if not gradle_wrapper.exists():
            raise RuntimeError(f"Gradle wrapper not found at {gradle_wrapper}")

        completed = subprocess.run(
            [str(gradle_wrapper), gradle_task],
            cwd=str(mobile_root),
            capture_output=True,
            text=True,
            env=_mobile_build_env(),
            check=False,
        )
        build_output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
        if completed.returncode != 0:
            excerpt = "\n".join(build_output.splitlines()[-40:]) if build_output else "no build output captured"
            raise RuntimeError(f"mobile build failed for {normalized_variant}:\n{excerpt}")

        artifact_path = _resolve_built_apk_path(mobile_root, normalized_variant)
        return artifact_path, build_output

    # -- Workspaces --

    @app.route("/api/workspaces", methods=["POST"])
    def create_workspace():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "")
        if not name:
            return jsonify({"error": "name is required"}), 400
        ws_id = wm.create_workspace(
            name=name,
            description=data.get("description", ""),
            kind=data.get("kind", "general"),
        )
        ws = wm.get_workspace(ws_id)
        return jsonify(ws), 201

    @app.route("/api/workspaces", methods=["GET"])
    def list_workspaces():
        status = request.args.get("status")
        workspaces = wm.list_workspaces(status=status)
        return jsonify({"workspaces": workspaces})

    @app.route("/api/workspaces/<workspace_id>", methods=["GET"])
    def get_workspace(workspace_id: str):
        ws = wm.get_workspace(workspace_id)
        if not ws:
            return jsonify({"error": "workspace not found"}), 404
        return jsonify(ws)

    @app.route("/api/workspaces/<workspace_id>", methods=["PATCH"])
    def update_workspace(workspace_id: str):
        data = request.get_json(silent=True) or {}
        ws = wm.update_workspace(workspace_id, **data)
        if not ws:
            return jsonify({"error": "workspace not found"}), 404
        return jsonify(ws)

    @app.route("/api/workspaces/<workspace_id>", methods=["DELETE"])
    def delete_workspace(workspace_id: str):
        if not wm.delete_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        return jsonify({"deleted": workspace_id})

    @app.route("/api/workspaces/<workspace_id>/attention", methods=["GET"])
    def get_workspace_attention_profile(workspace_id: str):
        profile = wm.get_workspace_attention_profile(workspace_id)
        if not profile:
            return jsonify({"error": "workspace not found"}), 404
        return jsonify(profile)

    @app.route("/api/workspaces/<workspace_id>/attention", methods=["PATCH"])
    def update_workspace_attention_profile(workspace_id: str):
        data = request.get_json(silent=True) or {}
        try:
            profile = wm.update_workspace_attention_profile(workspace_id, **data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not profile:
            return jsonify({"error": "workspace not found"}), 404
        return jsonify(profile)

    @app.route("/api/workspaces/<workspace_id>/attention/compile", methods=["POST"])
    def compile_workspace_attention_profile(workspace_id: str):
        data = request.get_json(silent=True) or {}
        instruction = str(data.get("instruction") or "").strip()
        apply = bool(data.get("apply", True))
        try:
            result = wm.compile_workspace_attention_instruction(workspace_id, instruction, apply=apply)
        except ValueError as exc:
            message = str(exc)
            if message == "workspace not found":
                return jsonify({"error": message}), 404
            return jsonify({"error": message}), 400
        return jsonify(result)

    # -- Build projects --

    @app.route("/api/workspaces/<workspace_id>/projects", methods=["POST"])
    def create_build_project(workspace_id: str):
        data = request.get_json(silent=True) or {}
        name = data.get("name", "")
        if not name:
            return jsonify({"error": "name is required"}), 400
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        try:
            proj_id = wm.create_build_project(
                workspace_id,
                name=name,
                description=data.get("description", ""),
            )
        except ValueError:
            return jsonify({"error": "workspace not found"}), 404
        proj = wm.get_build_project(proj_id)
        return jsonify(proj), 201

    @app.route("/api/workspaces/<workspace_id>/projects", methods=["GET"])
    def list_build_projects(workspace_id: str):
        status = request.args.get("status")
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        projects = wm.list_build_projects(workspace_id, status=status)
        return jsonify({"build_projects": projects})

    @app.route("/api/workspaces/<workspace_id>/projects/<project_id>", methods=["GET"])
    def get_build_project(workspace_id: str, project_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        proj = wm.get_build_project(project_id)
        if not proj:
            return jsonify({"error": "project not found"}), 404
        if proj["workspace_id"] != workspace_id:
            return jsonify({"error": "project does not belong to this workspace"}), 400
        return jsonify(proj)

    @app.route("/api/workspaces/<workspace_id>/projects/<project_id>", methods=["PATCH"])
    def update_build_project(workspace_id: str, project_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        data = request.get_json(silent=True) or {}
        proj = wm.update_build_project(project_id, **data)
        if not proj:
            return jsonify({"error": "project not found"}), 404
        if proj["workspace_id"] != workspace_id:
            return jsonify({"error": "project does not belong to this workspace"}), 400
        return jsonify(proj)

    @app.route("/api/workspaces/<workspace_id>/projects/<project_id>", methods=["DELETE"])
    def delete_build_project(workspace_id: str, project_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        if not wm.delete_build_project(workspace_id, project_id):
            return jsonify({"error": "project not found"}), 404
        return jsonify({"deleted": project_id, "workspace_id": workspace_id})

    @app.route("/api/workspaces/<workspace_id>/projects/<project_id>/deliveries", methods=["POST"])
    def publish_project_delivery(workspace_id: str, project_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        project = wm.get_build_project(project_id)
        if not project:
            return jsonify({"error": "project not found"}), 404
        if project["workspace_id"] != workspace_id:
            return jsonify({"error": "project does not belong to this workspace"}), 400

        upload = request.files.get("file")
        if upload is None:
            uploads = request.files.getlist("files")
            upload = uploads[0] if uploads else None
        if upload is None or not getattr(upload, "filename", ""):
            return jsonify({"error": "file is required"}), 400

        metadata: dict[str, Any] = {}
        raw_metadata = request.form.get("metadata")
        if raw_metadata:
            try:
                parsed_metadata = json.loads(raw_metadata)
            except json.JSONDecodeError:
                return jsonify({"error": "metadata must be valid JSON"}), 400
            if not isinstance(parsed_metadata, dict):
                return jsonify({"error": "metadata must decode to an object"}), 400
            metadata = parsed_metadata

        try:
            delivery = wm.publish_project_delivery(
                workspace_id,
                project_id,
                upload,
                target_device_id=str(request.form.get("device_id") or "").strip() or None,
                session_id=str(request.form.get("session_id") or "").strip() or None,
                metadata=metadata,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(delivery), 201

    @app.route("/api/workspaces/<workspace_id>/projects/<project_id>/deliveries", methods=["GET"])
    def list_project_deliveries(workspace_id: str, project_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        project = wm.get_build_project(project_id)
        if not project:
            return jsonify({"error": "project not found"}), 404
        if project["workspace_id"] != workspace_id:
            return jsonify({"error": "project does not belong to this workspace"}), 400
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        deliveries = wm.list_project_deliveries(
            workspace_id,
            project_id,
            target_device_id=request.args.get("device_id"),
            status=request.args.get("status"),
            limit=limit,
        )
        return jsonify({"workspace_id": workspace_id, "build_project_id": project_id, "deliveries": deliveries})

    @app.route("/api/workspaces/<workspace_id>/projects/<project_id>/deliveries/build", methods=["POST"])
    def build_project_delivery(workspace_id: str, project_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        project = wm.get_build_project(project_id)
        if not project:
            return jsonify({"error": "project not found"}), 404
        if project["workspace_id"] != workspace_id:
            return jsonify({"error": "project does not belong to this workspace"}), 400

        data = request.get_json(silent=True) or {}
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            return jsonify({"error": "metadata must be an object"}), 400

        requested_variant = str(data.get("variant") or metadata.get("build_variant") or "debug").strip().lower()
        normalized_variant, _ = _resolve_mobile_variant(requested_variant)
        target_device_id = str(data.get("device_id") or "").strip() or None
        session_id = str(data.get("session_id") or "").strip() or None

        try:
            artifact_path, build_output = _run_mobile_build_and_resolve_artifact(normalized_variant)
            merged_metadata = dict(metadata)
            merged_metadata.setdefault("release_channel", normalized_variant)
            merged_metadata["build_variant"] = normalized_variant
            merged_metadata["build_source"] = "desktop-browser"
            merged_metadata["uploaded_from"] = "opencloset_browser_build"
            merged_metadata["produced_at"] = wm._now()
            merged_metadata["artifact_name"] = artifact_path.name
            delivery = wm.publish_project_delivery_path(
                workspace_id,
                project_id,
                artifact_path,
                target_device_id=target_device_id,
                session_id=session_id,
                metadata=merged_metadata,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500

        return jsonify(
            {
                "delivery": delivery,
                "build": {
                    "variant": normalized_variant,
                    "artifact_path": str(artifact_path),
                    "output": build_output,
                },
            }
        ), 201

    @app.route("/api/workspaces/<workspace_id>/devices/<device_id>/deliveries", methods=["GET"])
    def list_device_deliveries(workspace_id: str, device_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        deliveries = wm.list_device_deliveries(
            workspace_id,
            device_id,
            status=request.args.get("status") or "ready",
            limit=limit,
        )
        return jsonify({"workspace_id": workspace_id, "device_id": device_id, "deliveries": deliveries})

    @app.route("/api/workspaces/<workspace_id>/deliveries/<delivery_id>", methods=["GET"])
    def get_project_delivery(workspace_id: str, delivery_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        delivery = wm.get_project_delivery(workspace_id, delivery_id)
        if not delivery:
            return jsonify({"error": "delivery not found"}), 404
        return jsonify(delivery)

    @app.route("/api/workspaces/<workspace_id>/deliveries/<delivery_id>/download", methods=["GET"])
    def download_project_delivery(workspace_id: str, delivery_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        row = wm.get_project_delivery_record(workspace_id, delivery_id)
        if not row:
            return jsonify({"error": "delivery not found"}), 404
        return send_file(
            str(row["storage_path"]),
            mimetype=str(row["mime_type"] or "application/octet-stream"),
            as_attachment=True,
            download_name=str(row["file_name"] or delivery_id),
        )

    @app.route("/api/workspaces/<workspace_id>/deliveries/<delivery_id>", methods=["PATCH"])
    def update_project_delivery(workspace_id: str, delivery_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        data = request.get_json(silent=True) or {}
        status = str(data.get("status") or "").strip()
        if not status:
            return jsonify({"error": "status is required"}), 400
        if status not in PROJECT_DELIVERY_STATUSES:
            return jsonify({"error": f"status must be one of: {', '.join(sorted(PROJECT_DELIVERY_STATUSES))}"}), 400
        metadata_update = data.get("metadata")
        if metadata_update is not None and not isinstance(metadata_update, dict):
            return jsonify({"error": "metadata must be an object"}), 400
        delivery = wm.update_project_delivery(
            workspace_id,
            delivery_id,
            status=status,
            device_id=str(data.get("device_id") or "").strip() or None,
            note=str(data.get("note") or "").strip() or None,
            metadata_update=metadata_update,
        )
        if not delivery:
            return jsonify({"error": "delivery not found"}), 404
        return jsonify(delivery)

    @app.route("/api/mobile/bootstrap", methods=["GET"])
    def mobile_bootstrap():
        device_id = str(request.args.get("device_id") or "").strip()
        if not device_id:
            return jsonify({"error": "device_id is required"}), 400
        try:
            capture_limit = int(request.args.get("capture_limit", "20"))
            delivery_limit = int(request.args.get("delivery_limit", "20"))
            session_limit = int(request.args.get("session_limit", "12"))
            event_limit = int(request.args.get("event_limit", "40"))
        except ValueError:
            return jsonify({"error": "capture_limit, delivery_limit, session_limit, and event_limit must be integers"}), 400
        payload = wm.build_mobile_bootstrap(
            device_id=device_id,
            workspace_status=request.args.get("workspace_status"),
            capture_limit=capture_limit,
            delivery_limit=delivery_limit,
            session_limit=session_limit,
            event_limit=event_limit,
        )
        return jsonify(payload)

    @app.route("/api/workspaces/<workspace_id>/pastimes", methods=["GET"])
    def list_workspace_pastimes(workspace_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        pastimes = wm.list_workspace_pastimes(
            workspace_id,
            status=request.args.get("status"),
            limit=limit,
        )
        return jsonify({"workspace_id": workspace_id, "pastimes": pastimes})

    @app.route("/api/workspaces/<workspace_id>/pastimes/<pastime_id>", methods=["PATCH"])
    def update_workspace_pastime(workspace_id: str, pastime_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        data = request.get_json(silent=True) or {}
        try:
            pastime = wm.update_workspace_pastime(workspace_id, pastime_id, **data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not pastime:
            return jsonify({"error": "workspace pastime not found"}), 404
        return jsonify(pastime)

    # -- Workspace evidence --

    @app.route("/api/workspaces/<workspace_id>/evidence", methods=["POST"])
    def create_workspace_evidence(workspace_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        data = request.get_json(silent=True) or {}
        title = str(data.get("title") or "").strip()
        summary = str(data.get("summary") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        if not summary:
            return jsonify({"error": "summary is required"}), 400
        try:
            evidence_id = wm.create_workspace_evidence(
                workspace_id,
                title=title,
                summary=summary,
                evidence_type=str(data.get("evidence_type") or "note"),
                content=str(data.get("content") or ""),
                source_kind=str(data.get("source_kind") or "note"),
                status=str(data.get("status") or "active"),
                session_id=data.get("session_id"),
                build_project_id=data.get("build_project_id"),
                tags=data.get("tags") if isinstance(data.get("tags"), list) else None,
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        evidence = wm.get_workspace_evidence(workspace_id, evidence_id)
        return jsonify(evidence), 201

    @app.route("/api/workspaces/<workspace_id>/evidence", methods=["GET"])
    def list_workspace_evidence(workspace_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        evidence = wm.list_workspace_evidence(
            workspace_id,
            evidence_type=request.args.get("evidence_type"),
            status=request.args.get("status"),
            session_id=request.args.get("session_id"),
            limit=limit,
        )
        return jsonify({"workspace_id": workspace_id, "evidence": evidence})

    @app.route("/api/workspaces/<workspace_id>/captures", methods=["POST"])
    def create_workspace_capture(workspace_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        data = request.get_json(silent=True) or {}
        source = str(data.get("source") or "manual").strip()
        event_type = str(data.get("event_type") or "text").strip()
        content = str(data.get("content") or "").strip()
        if not content:
            return jsonify({"error": "content is required"}), 400
        try:
            capture_id = wm.create_workspace_capture(
                workspace_id,
                source=source,
                event_type=event_type,
                content=content,
                media_url=data.get("media_url"),
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
                session_id=data.get("session_id"),
                build_project_id=data.get("build_project_id"),
                status=str(data.get("status") or "pending"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        capture = wm.get_workspace_capture(workspace_id, capture_id)
        return jsonify(capture), 201

    @app.route("/api/workspaces/<workspace_id>/captures", methods=["GET"])
    def list_workspace_captures(workspace_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        captures = wm.list_workspace_captures(
            workspace_id,
            status=request.args.get("status"),
            session_id=request.args.get("session_id"),
            limit=limit,
        )
        return jsonify({"workspace_id": workspace_id, "captures": captures})

    @app.route("/api/workspaces/<workspace_id>/captures/<capture_id>/promote", methods=["POST"])
    def promote_workspace_capture(workspace_id: str, capture_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        data = request.get_json(silent=True) or {}
        try:
            result = wm.promote_capture_to_evidence(
                workspace_id,
                capture_id,
                title=data.get("title"),
                summary=data.get("summary"),
                evidence_type=str(data.get("evidence_type") or "capture"),
                source_kind=str(data.get("source_kind") or "capture"),
                tags=data.get("tags") if isinstance(data.get("tags"), list) else None,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    # -- Plans by workspace --

    @app.route("/api/workspaces/<workspace_id>/plans", methods=["GET"])
    def list_plans_by_workspace(workspace_id: str):
        if not wm.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        plans = app.planning.list_plans_by_workspace(workspace_id)  # type: ignore
        return jsonify({"plans": plans})
