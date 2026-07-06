from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
import threading
import tempfile
import uuid
import shutil
from typing import Any

from flask import jsonify, request

from api.db.schema import DELEGATION_TASK_STATUSES, new_id

logger = logging.getLogger(__name__)

READ_ONLY_WORKER_NAME = "readonly-delegate"

TASK_TYPE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "summarize": {"authority_mode": "read_only", "default_substrate_id": "local_readonly"},
    "review": {"authority_mode": "read_only", "default_substrate_id": "local_readonly"},
    "audit": {"authority_mode": "read_only", "default_substrate_id": "local_readonly"},
    "proposal": {"authority_mode": "read_only", "default_substrate_id": "local_readonly"},
    "plan": {"authority_mode": "read_only", "default_substrate_id": "local_readonly"},
    "inspect": {"authority_mode": "tool_exec", "default_substrate_id": "codex_cli"},
    "implement": {"authority_mode": "mutation", "default_substrate_id": "codex_cli"},
    "verify": {"authority_mode": "tool_exec", "default_substrate_id": "codex_cli"},
}

BUDGET_FIELDS = {
    "max_input_tokens",
    "max_output_tokens",
    "max_cost_usd",
    "max_duration_seconds",
}

DELEGATION_POLICY_MODES = {"manual", "suggest", "auto"}
DELEGATION_POLICY_ROUTE_FIELDS = {
    "preferred_substrate_id",
    "fallback_substrate_ids",
    "auto_delegate",
    "budget",
}


def _default_delegation_policy() -> dict[str, Any]:
    build_fallbacks = ["claude_code", "copilot_cli"]
    return {
        "mode": "manual",
        "max_live_tasks": 2,
        "default_budget": {},
        "task_routes": {
            task_type: {
                "preferred_substrate_id": str(definition["default_substrate_id"]),
                "fallback_substrate_ids": (
                    list(build_fallbacks)
                    if task_type in {"inspect", "implement", "verify"}
                    else []
                ),
                "auto_delegate": task_type in {"inspect", "implement", "verify"},
                "budget": {},
            }
            for task_type, definition in TASK_TYPE_DEFINITIONS.items()
        },
    }


def _loads_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _loads_json_value(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _normalize_budget(raw_budget: dict[str, Any] | None) -> dict[str, Any]:
    if raw_budget is None:
        return {}
    if not isinstance(raw_budget, dict):
        raise ValueError("budget must be an object")
    unknown = set(raw_budget) - BUDGET_FIELDS
    if unknown:
        raise ValueError(f"unknown budget field(s): {', '.join(sorted(unknown))}")

    normalized: dict[str, Any] = {}
    for key, value in raw_budget.items():
        if value in (None, ""):
            continue
        if key in {"max_input_tokens", "max_output_tokens", "max_duration_seconds"}:
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"budget.{key} must be a positive integer")
            normalized[key] = value
        elif key == "max_cost_usd":
            if not isinstance(value, (int, float)) or float(value) <= 0:
                raise ValueError("budget.max_cost_usd must be a positive number")
            normalized[key] = float(value)
    return normalized


def _json_object_text(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _deep_copy_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _normalize_delegation_policy(raw_policy: dict[str, Any] | None, *, current_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _deep_copy_jsonable(current_policy or _default_delegation_policy())
    if raw_policy is None:
        return base
    if not isinstance(raw_policy, dict):
        raise ValueError("delegation_policy must be an object")

    unknown = set(raw_policy) - {"mode", "max_live_tasks", "default_budget", "task_routes"}
    if unknown:
        raise ValueError(f"unknown delegation_policy field(s): {', '.join(sorted(unknown))}")

    if "mode" in raw_policy:
        mode = str(raw_policy.get("mode") or "").strip().lower()
        if mode not in DELEGATION_POLICY_MODES:
            raise ValueError(f"delegation_policy.mode must be one of: {', '.join(sorted(DELEGATION_POLICY_MODES))}")
        base["mode"] = mode

    if "max_live_tasks" in raw_policy:
        value = raw_policy.get("max_live_tasks")
        if not isinstance(value, int) or value <= 0:
            raise ValueError("delegation_policy.max_live_tasks must be a positive integer")
        base["max_live_tasks"] = value

    if "default_budget" in raw_policy:
        base["default_budget"] = _normalize_budget(raw_policy.get("default_budget"))

    if "task_routes" in raw_policy:
        raw_routes = raw_policy.get("task_routes")
        if not isinstance(raw_routes, dict):
            raise ValueError("delegation_policy.task_routes must be an object")
        merged_routes = dict(base.get("task_routes") or {})
        for task_type, route in raw_routes.items():
            normalized_task_type = str(task_type or "").strip().lower()
            if normalized_task_type not in TASK_TYPE_DEFINITIONS:
                raise ValueError(f"unknown delegation_policy.task_routes task type: {normalized_task_type}")
            if not isinstance(route, dict):
                raise ValueError(f"delegation_policy.task_routes.{normalized_task_type} must be an object")
            unknown_route_fields = set(route) - DELEGATION_POLICY_ROUTE_FIELDS
            if unknown_route_fields:
                raise ValueError(
                    f"unknown delegation_policy.task_routes.{normalized_task_type} field(s): {', '.join(sorted(unknown_route_fields))}"
                )

            current_route = dict(merged_routes.get(normalized_task_type) or {})
            if "preferred_substrate_id" in route:
                preferred_substrate_id = str(route.get("preferred_substrate_id") or "").strip()
                if not preferred_substrate_id:
                    raise ValueError(f"delegation_policy.task_routes.{normalized_task_type}.preferred_substrate_id is required")
                current_route["preferred_substrate_id"] = preferred_substrate_id
            if "fallback_substrate_ids" in route:
                fallback_substrate_ids = route.get("fallback_substrate_ids")
                if not isinstance(fallback_substrate_ids, list) or not all(isinstance(item, str) and item.strip() for item in fallback_substrate_ids):
                    raise ValueError(
                        f"delegation_policy.task_routes.{normalized_task_type}.fallback_substrate_ids must be a list of non-empty strings"
                    )
                current_route["fallback_substrate_ids"] = list(dict.fromkeys(item.strip() for item in fallback_substrate_ids))
            if "auto_delegate" in route:
                if not isinstance(route.get("auto_delegate"), bool):
                    raise ValueError(f"delegation_policy.task_routes.{normalized_task_type}.auto_delegate must be a boolean")
                current_route["auto_delegate"] = bool(route.get("auto_delegate"))
            if "budget" in route:
                current_route["budget"] = _normalize_budget(route.get("budget"))
            merged_routes[normalized_task_type] = current_route
        base["task_routes"] = merged_routes

    return base


def _load_delegation_policy(app, session_id: str) -> dict[str, Any] | None:
    row = app.db.execute("SELECT delegation_policy FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    if not row["delegation_policy"]:
        return _default_delegation_policy()
    try:
        stored = json.loads(row["delegation_policy"])
    except (TypeError, json.JSONDecodeError):
        return _default_delegation_policy()
    return _normalize_delegation_policy(stored)


def _store_delegation_policy(app, session_id: str, policy: dict[str, Any]) -> None:
    app.db.execute(
        "UPDATE sessions SET delegation_policy = ?, updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) WHERE id = ?",
        (json.dumps(policy), session_id),
    )
    app.db.commit()


def _resolve_command(configured: str | None, *candidate_names: str) -> str | None:
    candidates = [str(configured or "").strip(), *candidate_names]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if os.path.exists(candidate):
            return candidate
    return None


def _probe_copilot_cli(gh_command: str | None) -> tuple[bool, str]:
    if not gh_command:
        return False, "missing_binary"
    try:
        result = subprocess.run(
            [gh_command, "copilot", "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "probe_timed_out"
    except Exception:
        return False, "probe_failed"
    output = str(result.stdout or "").lower()
    if result.returncode == 0:
        return True, "ready"
    if "not installed" in output:
        return False, "not_installed"
    return False, "unavailable"


def _get_cached_substrate_catalog(app) -> dict[str, dict[str, Any]]:
    now = time.time()
    ttl_seconds = float(app.config.get("DELEGATION_SUBSTRATE_CACHE_SECONDS", 20.0))
    cached = getattr(app, "_delegation_substrate_catalog_cache", None)
    if isinstance(cached, dict):
        cached_at = float(cached.get("cached_at") or 0.0)
        catalog = cached.get("catalog")
        if isinstance(catalog, dict) and now - cached_at < ttl_seconds:
            return _deep_copy_jsonable(catalog)
    catalog = _build_delegation_substrate_catalog(app)
    setattr(
        app,
        "_delegation_substrate_catalog_cache",
        {"cached_at": now, "catalog": _deep_copy_jsonable(catalog)},
    )
    return catalog


def _build_delegation_substrate_catalog(app) -> dict[str, dict[str, Any]]:
    codex_cmd = _resolve_command(
        str(app.config.get("CODEX_CLI_COMMAND", os.environ.get("OPENCLOSET_CODEX_CLI_COMMAND", "codex")) or "codex"),
        "codex.exe",
        "codex",
    )
    claude_cmd = _resolve_command(
        str(app.config.get("CLAUDE_CODE_COMMAND", os.environ.get("OPENCLOSET_CLAUDE_CODE_COMMAND", "claude")) or "claude"),
        "claude",
        "claude-code",
    )
    gh_cmd = _resolve_command(
        str(app.config.get("COPILOT_CLI_COMMAND", os.environ.get("OPENCLOSET_COPILOT_CLI_COMMAND", "gh")) or "gh"),
        "gh.exe",
        "gh",
    )
    copilot_ready, copilot_health = _probe_copilot_cli(gh_cmd)
    return {
        "local_readonly": {
            "id": "local_readonly",
            "label": "Local Read-Only Worker",
            "description": "Cheap local synthesis for analysis-only worker tasks.",
            "family": "local",
            "execution_mode": "read_only_model",
            "worker_name": READ_ONLY_WORKER_NAME,
            "supports_tool_use": False,
            "supports_mutation": False,
            "frontier": False,
            "available": True,
            "dispatchable": True,
            "health_status": "ready",
        },
        "codex_cli": {
            "id": "codex_cli",
            "label": "Codex Builder",
            "description": "Primary frontier coding worker using Codex CLI exec mode.",
            "family": "codex",
            "execution_mode": "codex_cli",
            "worker_name": "codex-build-delegate",
            "supports_tool_use": True,
            "supports_mutation": True,
            "frontier": True,
            "available": bool(codex_cmd),
            "dispatchable": bool(codex_cmd),
            "health_status": "ready" if codex_cmd else "missing_binary",
            "command": codex_cmd,
        },
        "claude_code": {
            "id": "claude_code",
            "label": "Claude Code Builder",
            "description": "Frontier fallback builder through a local Claude Code CLI.",
            "family": "claude",
            "execution_mode": "claude_code_cli",
            "worker_name": "claude-code-delegate",
            "supports_tool_use": True,
            "supports_mutation": True,
            "frontier": True,
            "available": bool(claude_cmd),
            "dispatchable": bool(claude_cmd),
            "health_status": "ready" if claude_cmd else "missing_binary",
            "command": claude_cmd,
        },
        "copilot_cli": {
            "id": "copilot_cli",
            "label": "Copilot Builder",
            "description": "Frontier fallback builder routed through the GitHub Copilot CLI.",
            "family": "copilot",
            "execution_mode": "copilot_cli",
            "worker_name": "copilot-build-delegate",
            "supports_tool_use": True,
            "supports_mutation": True,
            "frontier": True,
            "available": bool(gh_cmd),
            "dispatchable": bool(gh_cmd) and copilot_ready,
            "health_status": copilot_health,
            "command": gh_cmd,
        },
    }


class DelegationManager:
    def __init__(self, app) -> None:
        self.app = app
        self.db = app.db

    def list_substrates(self) -> list[dict[str, Any]]:
        return list(_get_cached_substrate_catalog(self.app).values())

    def get_substrate(self, substrate_id: str | None) -> dict[str, Any] | None:
        if not substrate_id:
            return None
        return _get_cached_substrate_catalog(self.app).get(str(substrate_id).strip())

    def get_policy(self, session_id: str) -> dict[str, Any] | None:
        return _load_delegation_policy(self.app, session_id)

    def create_task(
        self,
        session_id: str,
        *,
        task_type: str,
        instruction: str,
        title: str = "",
        substrate_id: str | None = None,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        budget: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_type = str(task_type or "").strip().lower()
        task_definition = TASK_TYPE_DEFINITIONS.get(task_type)
        if task_definition is None:
            raise ValueError(f"task_type must be one of: {', '.join(sorted(TASK_TYPE_DEFINITIONS))}")
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ValueError("instruction is required")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        normalized_budget = _normalize_budget(budget)

        session = self.db.execute(
            "SELECT id, workspace_id, provider, model, delegation_policy FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not session:
            raise ValueError("session not found")

        policy = _normalize_delegation_policy(_loads_json_object(session["delegation_policy"]))
        route_policy = dict((policy.get("task_routes") or {}).get(task_type) or {})
        live_task_count = self.db.execute(
            "SELECT COUNT(*) AS cnt FROM delegation_tasks WHERE session_id = ? AND status IN ('queued', 'running')",
            (session_id,),
        ).fetchone()["cnt"]
        max_live_tasks = int(policy.get("max_live_tasks") or 2)
        if live_task_count >= max_live_tasks:
            raise ValueError(f"delegation queue is full for this session (max_live_tasks={max_live_tasks})")

        effective_budget = dict(policy.get("default_budget") or {})
        route_budget = route_policy.get("budget")
        if isinstance(route_budget, dict):
            effective_budget.update(route_budget)
        effective_budget.update(normalized_budget)

        preferred_substrate_id = str(route_policy.get("preferred_substrate_id") or task_definition["default_substrate_id"]).strip()
        fallback_substrate_ids = [
            str(item).strip()
            for item in route_policy.get("fallback_substrate_ids", [])
            if str(item).strip()
        ]
        candidate_substrate_ids = [str(substrate_id or preferred_substrate_id).strip(), *fallback_substrate_ids]
        resolved_substrate_id = ""
        substrate = None
        for candidate_substrate_id in candidate_substrate_ids:
            if not candidate_substrate_id:
                continue
            candidate = self.get_substrate(candidate_substrate_id)
            if candidate is None:
                continue
            if not candidate.get("dispatchable", False):
                substrate = candidate
                continue
            resolved_substrate_id = candidate_substrate_id
            substrate = candidate
            break
        if not resolved_substrate_id:
            resolved_substrate_id = str(substrate_id or preferred_substrate_id).strip()
        substrate = self.get_substrate(resolved_substrate_id)
        if substrate is None:
            raise ValueError(f"unknown delegation substrate: {resolved_substrate_id}")
        if not substrate.get("dispatchable", False):
            raise ValueError(f"delegation substrate is not available: {resolved_substrate_id}")

        task_id = new_id()
        now_expr = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        self.db.execute(
            f"""
            INSERT INTO delegation_tasks (
                id, session_id, workspace_id, task_type, substrate_id, authority_mode, title, instruction, status,
                requested_provider, requested_model, budget, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, {now_expr}, {now_expr})
            """,
            (
                task_id,
                session_id,
                session["workspace_id"],
                task_type,
                resolved_substrate_id,
                str(task_definition["authority_mode"]),
                str(title or "").strip(),
                instruction,
                str(requested_provider or session["provider"] or "").strip() or None,
                str(requested_model or session["model"] or "").strip() or None,
                _json_object_text(effective_budget),
                json.dumps(metadata or {}),
            ),
        )
        payload = self.get_task(task_id)
        self.app.event_logger.log(
            session_id,
            "delegation_requested",
            {
                "delegation_id": task_id,
                "task_type": payload["task_type"],
                "substrate_id": payload["substrate_id"],
                "authority_mode": payload["authority_mode"],
                "title": payload["title"],
                "requested_provider": payload["requested_provider"],
                "requested_model": payload["requested_model"],
            },
            None,
        )
        self.db.commit()
        return payload

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """
            SELECT id, session_id, workspace_id, task_type, substrate_id, authority_mode, title, instruction, status,
                   requested_provider, requested_model, provider_route, worker_name,
                   budget, result_text, result_summary, result_payload, input_tokens, output_tokens, duration_ms, error, metadata,
                   created_at, started_at, completed_at, updated_at
            FROM delegation_tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return self._serialize_row(row)

    def list_tasks(self, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT id, session_id, workspace_id, task_type, substrate_id, authority_mode, title, instruction, status,
                   requested_provider, requested_model, provider_route, worker_name,
                   budget, result_text, result_summary, result_payload, input_tokens, output_tokens, duration_ms, error, metadata,
                   created_at, started_at, completed_at, updated_at
            FROM delegation_tasks
            WHERE session_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [self._serialize_row(row) for row in rows]

    def get_next_queued_task(self) -> dict[str, Any] | None:
        row = self.db.execute(
            """
            SELECT id, session_id, workspace_id, task_type, substrate_id, authority_mode, title, instruction, status,
                   requested_provider, requested_model, provider_route, worker_name,
                   budget, result_text, result_summary, result_payload, input_tokens, output_tokens, duration_ms, error, metadata,
                   created_at, started_at, completed_at, updated_at
            FROM delegation_tasks
            WHERE status = 'queued'
            ORDER BY created_at ASC, rowid ASC
            LIMIT 1
            """
        ).fetchone()
        return self._serialize_row(row) if row else None

    def mark_running(self, task_id: str, *, provider_route: dict[str, Any], worker_name: str) -> dict[str, Any]:
        self.db.execute(
            """
            UPDATE delegation_tasks
            SET status = 'running',
                provider_route = ?,
                worker_name = ?,
                started_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (json.dumps(provider_route), worker_name, task_id),
        )
        return self.get_task(task_id) or {}

    def complete_task(
        self,
        task_id: str,
        *,
        status: str = "completed",
        result_text: str,
        result_summary: str,
        result_payload: dict[str, Any] | None,
        provider_route: dict[str, Any],
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        normalized_status = str(status or "completed").strip().lower()
        if normalized_status not in {"completed", "blocked"}:
            raise ValueError(f"unsupported delegation completion status: {status}")
        self.db.execute(
            """
            UPDATE delegation_tasks
            SET status = ?,
                provider_route = ?,
                input_tokens = ?,
                output_tokens = ?,
                duration_ms = ?,
                result_text = ?,
                result_summary = ?,
                result_payload = ?,
                error = NULL,
                completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (
                normalized_status,
                json.dumps(provider_route),
                input_tokens,
                output_tokens,
                duration_ms,
                result_text,
                result_summary,
                json.dumps(result_payload or {}),
                task_id,
            ),
        )
        return self.get_task(task_id) or {}

    def fail_task(self, task_id: str, *, error: str, provider_route: dict[str, Any] | None = None) -> dict[str, Any]:
        self.db.execute(
            """
            UPDATE delegation_tasks
            SET status = 'failed',
                provider_route = COALESCE(?, provider_route),
                error = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (json.dumps(provider_route) if provider_route is not None else None, error, task_id),
        )
        return self.get_task(task_id) or {}

    @staticmethod
    def _serialize_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "session_id": str(row["session_id"]),
            "workspace_id": str(row["workspace_id"] or "") or None,
            "task_type": str(row["task_type"]),
            "substrate_id": str(row["substrate_id"] or "") or None,
            "authority_mode": str(row["authority_mode"] or "") or None,
            "title": str(row["title"] or ""),
            "instruction": str(row["instruction"] or ""),
            "status": str(row["status"]),
            "requested_provider": str(row["requested_provider"] or "") or None,
            "requested_model": str(row["requested_model"] or "") or None,
            "budget": _loads_json_object(row["budget"]),
            "provider_route": _loads_json_value(row["provider_route"]),
            "worker_name": str(row["worker_name"] or "") or None,
            "result_text": str(row["result_text"] or "") or None,
            "result_summary": str(row["result_summary"] or "") or None,
            "result_payload": _loads_json_object(row["result_payload"]),
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "duration_ms": row["duration_ms"],
            "error": str(row["error"] or "") or None,
            "metadata": _loads_json_object(row["metadata"]),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "updated_at": row["updated_at"],
        }


class DelegationWorker:
    def __init__(self, app, *, poll_interval_seconds: float = 2.0) -> None:
        self.app = app
        self.poll_interval_seconds = poll_interval_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def poll_once(self) -> dict[str, Any] | None:
        task = self.app.delegations.get_next_queued_task()
        if not task:
            return None
        return self.execute_task(task["id"])

    def execute_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.app.delegations.get_task(task_id)
        if not task:
            return None
        if task.get("status") != "queued":
            return task
        substrate = self.app.delegations.get_substrate(task.get("substrate_id"))
        if substrate is None:
            failed = self.app.delegations.fail_task(task["id"], error="delegation substrate not found")
            self.app.db.commit()
            return failed

        session = self.app.db.execute(
            "SELECT id, label, provider, model, workspace_id FROM sessions WHERE id = ?",
            (task["session_id"],),
        ).fetchone()
        if not session:
            payload = self.app.delegations.fail_task(task["id"], error="session not found")
            self.app.db.commit()
            return payload

        route = self._resolve_provider_route(dict(session), task, substrate)

        running = self.app.delegations.mark_running(task["id"], provider_route=route, worker_name=str(substrate["worker_name"]))
        self.app.event_logger.log(
            task["session_id"],
            "delegation_started",
            {
                "delegation_id": task["id"],
                "task_type": task["task_type"],
                "substrate_id": task.get("substrate_id"),
                "worker_name": str(substrate["worker_name"]),
                "provider_route": route,
            },
            None,
        )

        try:
            execution = self._execute_task(dict(session), task, substrate)
            completion_status = self._completion_status_for_execution(substrate, execution)
            completed = self.app.delegations.complete_task(
                task["id"],
                status=completion_status,
                result_text=execution["result_text"],
                result_summary=execution["result_summary"],
                result_payload=execution["result_payload"],
                provider_route=execution["provider_route"],
                input_tokens=execution["input_tokens"],
                output_tokens=execution["output_tokens"],
                duration_ms=execution["duration_ms"],
            )
            self.app.event_logger.log(
                task["session_id"],
                "delegation_completed" if completion_status == "completed" else "delegation_blocked",
                {
                    "delegation_id": task["id"],
                    "task_type": task["task_type"],
                    "substrate_id": task.get("substrate_id"),
                    "worker_name": str(substrate["worker_name"]),
                    "provider_route": execution["provider_route"],
                    "result_summary": execution["result_summary"],
                },
                None,
            )
            self.app.event_logger.log(
                task["session_id"],
                "worker_report",
                {
                    "worker_name": str(substrate["worker_name"]),
                    "delegation_id": task["id"],
                    "task_type": task["task_type"],
                    "substrate_id": task.get("substrate_id"),
                    "summary": execution["result_summary"],
                    "result_payload": execution["result_payload"],
                },
                None,
            )
            self.app.db.commit()
            return completed
        except Exception as exc:
            logger.exception("delegation task failed")
            failed = self.app.delegations.fail_task(task["id"], error=str(exc), provider_route=route)
            self.app.event_logger.log(
                task["session_id"],
                "delegation_failed",
                {
                    "delegation_id": task["id"],
                    "task_type": task["task_type"],
                    "substrate_id": task.get("substrate_id"),
                    "worker_name": str(substrate["worker_name"]),
                    "provider_route": route,
                    "error": str(exc),
                },
                None,
            )
            self.app.db.commit()
            return failed

    @staticmethod
    def _completion_status_for_execution(substrate: dict[str, Any], execution: dict[str, Any]) -> str:
        execution_mode = str(substrate.get("execution_mode") or "").strip().lower()
        exit_status = str(((execution.get("result_payload") or {}).get("exit_status") or "")).strip().lower()
        if execution_mode in {"codex_cli", "claude_code_cli", "copilot_cli"} and exit_status in {"partial", "blocked"}:
            return "blocked"
        return "completed"

    def run_forever(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("delegation worker poll failed")
            self._stop_event.wait(self.poll_interval_seconds)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_background_loop, name="opencloset-delegation-worker", daemon=True)
        self._thread.start()

    def stop_background(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run_background_loop(self) -> None:
        from api.api.app import create_app

        isolated_app = create_app(db_path=self.app.config["DB_PATH"], start_background_workers=False)
        isolated_worker = isolated_app.delegation_worker
        isolated_worker.poll_interval_seconds = self.poll_interval_seconds
        try:
            while not self._stop_event.is_set():
                try:
                    isolated_worker.poll_once()
                except Exception:
                    logger.exception("delegation background poll failed")
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            isolated_app.close()

    def _resolve_provider_route(
        self,
        session: dict[str, Any],
        task: dict[str, Any],
        substrate: dict[str, Any],
    ) -> dict[str, Any]:
        if substrate["execution_mode"] != "read_only_model":
            return {
                "delegation_substrate_id": substrate["id"],
                "resolved_provider": str(task.get("requested_provider") or session.get("provider") or "auto"),
                "resolved_model": str(task.get("requested_model") or session.get("model") or ""),
                "route_reason": "delegation_substrate_selected",
                "used_auto_routing": False,
            }
        return self.app.execution_support.resolve_substrate_route(
            session,
            f"delegation:{task['id']}",
            message_text=str(task.get("instruction") or ""),
            attachments=None,
            requested_provider=str(task.get("requested_provider") or session["provider"] or "llamacpp"),
            requested_model=str(task.get("requested_model") or session["model"] or ""),
        ).to_payload()

    def _execute_task(
        self,
        session: dict[str, Any],
        task: dict[str, Any],
        substrate: dict[str, Any],
    ) -> dict[str, Any]:
        if substrate["execution_mode"] == "read_only_model":
            return self._execute_read_only_task(session, task)
        if substrate["execution_mode"] == "codex_cli":
            return self._execute_codex_task(session, task, substrate)
        if substrate["execution_mode"] in {"claude_code_cli", "copilot_cli"}:
            return self._execute_external_cli_task(session, task, substrate)
        raise ValueError(f"unsupported delegation execution_mode: {substrate['execution_mode']}")

    def _execute_read_only_task(self, session: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        result, route = self.app.execution_support.run_read_only_messages(
            session,
            self._build_messages(session, task),
            run_id=f"delegation:{task['id']}",
            requested_provider=str(task.get("requested_provider") or session["provider"] or "llamacpp"),
            requested_model=str(task.get("requested_model") or session["model"] or ""),
            message_text=str(task.get("instruction") or ""),
            attachments=None,
            temperature=float(self.app.config.get("LOOP_TEMPERATURE", 0.2)),
            max_tokens=int(task.get("budget", {}).get("max_output_tokens") or self.app.config.get("DELEGATION_MAX_TOKENS", 768)),
        )
        raw_text = str(result.text or "").strip()
        payload = self._coerce_result_payload(raw_text, task_type=str(task["task_type"]))
        return {
            "result_text": raw_text,
            "result_summary": self._summary_from_payload(payload, raw_text),
            "result_payload": payload,
            "provider_route": route,
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    def _execute_openclaw_task(
        self,
        session: dict[str, Any],
        task: dict[str, Any],
        substrate: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        prompt_text = self._build_openclaw_build_prompt(session, task)
        command = self._build_openclaw_command(session, task, substrate, prompt_text)

        process = subprocess.Popen(
            command,
            cwd=self.app.config.get("WORKSPACE_ROOT"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        raw_lines: list[str] = []
        assistant_chunks: list[str] = []
        final_text = ""
        input_tokens = 0
        output_tokens = 0

        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.rstrip("\r\n")
            if stripped:
                raw_lines.append(stripped)
            payload = self._try_parse_json_line(stripped)
            if payload is None:
                continue
            event_type = str(payload.get("type") or payload.get("event") or "").strip()
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            if event_type in {"stream.assistant_delta", "assistant_delta", "text_delta"}:
                text = str(data.get("text") or "")
                if text:
                    assistant_chunks.append(text)
                continue
            if event_type in {"stream.usage", "usage"}:
                input_tokens = int(data.get("input_tokens") or input_tokens or 0)
                output_tokens = int(data.get("output_tokens") or output_tokens or 0)
                continue
            extracted = self._extract_final_text(payload)
            if extracted:
                final_text = extracted

        timeout_seconds = float(task.get("budget", {}).get("max_duration_seconds") or self.app.config.get("OPENCLAW_AGENT_TIMEOUT_SECONDS", 1800.0))
        process.wait(timeout=timeout_seconds)
        raw_output = "\n".join(raw_lines).strip()
        if not final_text and raw_output:
            payload = self._try_parse_json_document(raw_output)
            if payload is not None:
                final_text = self._extract_final_text(payload)

        raw_text = final_text or "".join(assistant_chunks).strip() or raw_output
        if process.returncode not in (0, None):
            raise RuntimeError(raw_text or f"openclaw exited with code {process.returncode}")

        payload = self._coerce_result_payload(raw_text, task_type=str(task["task_type"]))
        return {
            "result_text": self._render_payload_text(payload, fallback=raw_text),
            "result_summary": self._summary_from_payload(payload, raw_text),
            "result_payload": payload,
            "provider_route": {
                "delegation_substrate_id": substrate["id"],
                "resolved_provider": str(task.get("requested_provider") or session.get("provider") or "auto"),
                "resolved_model": str(task.get("requested_model") or session.get("model") or ""),
                "route_reason": "delegated_build_subagent",
                "used_auto_routing": False,
            },
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    def _execute_codex_task(
        self,
        session: dict[str, Any],
        task: dict[str, Any],
        substrate: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        prompt_text = self._build_openclaw_build_prompt(session, task)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as schema_file:
            json.dump(
                {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "files_touched": {"type": "array", "items": {"type": "string"}},
                        "commands_run": {"type": "array", "items": {"type": "string"}},
                        "tests_run": {"type": "array", "items": {"type": "string"}},
                        "tests_passed": {"type": "array", "items": {"type": "string"}},
                        "open_questions": {"type": "array", "items": {"type": "string"}},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "patch_summary": {"type": "string"},
                        "exit_status": {"type": "string"},
                    },
                    "required": ["summary", "files_touched", "commands_run", "tests_run", "tests_passed", "open_questions", "risks", "patch_summary", "exit_status"],
                },
                schema_file,
            )
            schema_path = schema_file.name
        with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as output_file:
            output_path = output_file.name

        try:
            command = self._build_codex_command(session, task, substrate, schema_path=schema_path, output_path=output_path)
            process = subprocess.Popen(
                command,
                cwd=self.app.config.get("WORKSPACE_ROOT"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            input_tokens = 0
            output_tokens = 0
            timeout_seconds = float(task.get("budget", {}).get("max_duration_seconds") or self.app.config.get("OPENCLAW_AGENT_TIMEOUT_SECONDS", 1800.0))
            stdout_text, _ = process.communicate(input=prompt_text, timeout=timeout_seconds)
            raw_lines = []
            for line in str(stdout_text or "").splitlines():
                stripped = line.rstrip("\r\n")
                if stripped:
                    raw_lines.append(stripped)
                payload = self._try_parse_json_line(stripped)
                if payload is None:
                    continue
                event_type = str(payload.get("type") or payload.get("event") or "").strip().lower()
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                if "usage" in event_type:
                    input_tokens = int(data.get("input_tokens") or input_tokens or 0)
                    output_tokens = int(data.get("output_tokens") or output_tokens or 0)
            raw_output = "\n".join(raw_lines).strip()
            try:
                with open(output_path, "r", encoding="utf-8", errors="replace") as handle:
                    final_text = handle.read().strip()
            except OSError:
                final_text = ""
            raw_text = final_text or raw_output
            if process.returncode not in (0, None):
                raise RuntimeError(raw_text or f"codex exited with code {process.returncode}")
            payload = self._coerce_result_payload(raw_text, task_type=str(task["task_type"]))
            return {
                "result_text": self._render_payload_text(payload, fallback=raw_text),
                "result_summary": self._summary_from_payload(payload, raw_text),
                "result_payload": payload,
                "provider_route": {
                    "delegation_substrate_id": substrate["id"],
                    "resolved_provider": str(task.get("requested_provider") or session.get("provider") or "openai"),
                    "resolved_model": str(task.get("requested_model") or session.get("model") or ""),
                    "route_reason": "delegated_codex_subagent",
                    "used_auto_routing": False,
                },
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        finally:
            try:
                os.unlink(schema_path)
            except OSError:
                pass
            try:
                os.unlink(output_path)
            except OSError:
                pass

    def _execute_external_cli_task(
        self,
        session: dict[str, Any],
        task: dict[str, Any],
        substrate: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        prompt_text = self._build_openclaw_build_prompt(session, task)
        command = self._build_external_cli_command(session, task, substrate, prompt_text)
        process = subprocess.Popen(
            command,
            cwd=self.app.config.get("WORKSPACE_ROOT"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        raw_output, _ = process.communicate(timeout=float(task.get("budget", {}).get("max_duration_seconds") or self.app.config.get("OPENCLAW_AGENT_TIMEOUT_SECONDS", 1800.0)))
        raw_text = str(raw_output or "").strip()
        if process.returncode not in (0, None):
            raise RuntimeError(raw_text or f"{substrate['id']} exited with code {process.returncode}")
        payload = self._coerce_result_payload(raw_text, task_type=str(task["task_type"]))
        return {
            "result_text": self._render_payload_text(payload, fallback=raw_text),
            "result_summary": self._summary_from_payload(payload, raw_text),
            "result_payload": payload,
            "provider_route": {
                "delegation_substrate_id": substrate["id"],
                "resolved_provider": str(task.get("requested_provider") or session.get("provider") or "auto"),
                "resolved_model": str(task.get("requested_model") or session.get("model") or ""),
                "route_reason": f"delegated_{substrate['id']}_subagent",
                "used_auto_routing": False,
            },
            "input_tokens": 0,
            "output_tokens": 0,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    def _build_messages(self, session: dict[str, Any], task: dict[str, Any]) -> list[dict[str, str]]:
        task_metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        transcript_rows = self.app.db.execute(
            """
            SELECT role, content
            FROM messages
            WHERE session_id = ? AND persistent = 1
            ORDER BY position DESC
            LIMIT 8
            """,
            (task["session_id"],),
        ).fetchall()
        transcript_rows = list(reversed(transcript_rows))
        transcript_excerpt = "\n".join(
            f"{row['role']}: {str(row['content'] or '').strip()}"
            for row in transcript_rows
            if str(row["content"] or "").strip()
        )
        capture_context = self._build_capture_context(
            workspace_id=str(session.get("workspace_id") or "") or None,
            session_id=str(task["session_id"]),
            capture_ids=task_metadata.get("capture_ids"),
        )
        evidence_context = self._build_evidence_context(
            workspace_id=str(session.get("workspace_id") or "") or None,
            session_id=str(task["session_id"]),
            evidence_ids=task_metadata.get("evidence_ids"),
        )
        metadata_context = self._build_metadata_context(task_metadata)
        context_sections = [
            f"Recent transcript:\n{transcript_excerpt or '[no recent transcript]'}",
            capture_context,
            evidence_context,
            metadata_context,
        ]
        context_block = "\n\n".join(section for section in context_sections if section)
        user_content = (
            f"Delegation task type: {task['task_type']}\n"
            f"Session label: {session.get('label') or ''}\n"
            f"Instruction: {task['instruction']}\n\n"
            f"{context_block}\n\n"
            "Produce a read-only result. Do not claim to have executed tools, changed files, or mutated external state."
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are a delegated read-only OpenCloset worker. "
                    "You may analyze, review, summarize, and draft proposals, but you must not claim tool use or edits. "
                    "Return only the requested result text."
                ),
            },
            {"role": "user", "content": user_content},
        ]

    def _build_openclaw_build_prompt(self, session: dict[str, Any], task: dict[str, Any]) -> str:
        task_metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        budget = task.get("budget") if isinstance(task.get("budget"), dict) else {}
        transcript_rows = self.app.db.execute(
            """
            SELECT role, content
            FROM messages
            WHERE session_id = ? AND persistent = 1
            ORDER BY position DESC
            LIMIT 10
            """,
            (task["session_id"],),
        ).fetchall()
        transcript_rows = list(reversed(transcript_rows))
        transcript_excerpt = "\n".join(
            f"{row['role']}: {self._trim_context_text(str(row['content'] or ''), 320)}"
            for row in transcript_rows
            if str(row["content"] or "").strip()
        )
        authority = str(task.get("authority_mode") or "tool_exec")
        mutation_rule = (
            "You may read, edit, write, and execute commands when needed to complete the task."
            if authority == "mutation"
            else "You may inspect and execute safe verification commands, but do not mutate files unless explicitly allowed."
        )
        budget_lines = [f"- {key}: {value}" for key, value in budget.items()]
        metadata_lines = [f"- {key}: {value}" for key, value in task_metadata.items() if isinstance(value, (str, int, float)) and str(value).strip()]
        return "\n\n".join(
            section for section in [
                f"You are an OpenCloset delegated build worker operating through {self._worker_prompt_label(task)}.",
                f"Task type: {task['task_type']}\nAuthority mode: {authority}\nSession label: {session.get('label') or ''}",
                f"Instruction:\n{task['instruction']}",
                f"Recent transcript:\n{transcript_excerpt or '[no recent transcript]'}",
                ("Budget:\n" + "\n".join(budget_lines)) if budget_lines else "",
                ("Delegation metadata:\n" + "\n".join(metadata_lines)) if metadata_lines else "",
                mutation_rule,
                (
                    "Return a single JSON object only. Do not wrap it in markdown fences or commentary. Use these keys exactly: "
                    "summary, files_touched, commands_run, tests_run, tests_passed, open_questions, risks, patch_summary, exit_status. "
                    "Use arrays for list fields. Use exit_status values success, partial, failed, or blocked."
                ),
            ] if section
        )

    def _worker_prompt_label(self, task: dict[str, Any]) -> str:
        substrate_id = str(task.get("substrate_id") or "").strip()
        substrate = self.app.delegations.get_substrate(substrate_id)
        if substrate and substrate.get("label"):
            return str(substrate["label"])
        return "a delegated worker"

    def _build_openclaw_command(
        self,
        session: dict[str, Any],
        task: dict[str, Any],
        substrate: dict[str, Any],
        prompt_text: str,
    ) -> list[str]:
        command = [
            str(substrate["command"]),
            "agent",
            "--json",
            "--session-id",
            self._openclaw_session_key(str(task["id"])),
            "--message",
            prompt_text,
        ]
        if bool(substrate.get("use_local_mode", False)):
            command.append("--local")
        model_arg = self._build_model_arg(
            str(task.get("requested_provider") or session.get("provider") or ""),
            str(task.get("requested_model") or session.get("model") or ""),
        )
        if model_arg:
            command.extend(["--model", model_arg])
        duration = int(task.get("budget", {}).get("max_duration_seconds") or self.app.config.get("OPENCLAW_AGENT_TIMEOUT_SECONDS", 1800.0))
        if duration > 0:
            command.extend(["--timeout", str(duration)])
        return command

    def _build_codex_command(
        self,
        session: dict[str, Any],
        task: dict[str, Any],
        substrate: dict[str, Any],
        *,
        schema_path: str,
        output_path: str,
    ) -> list[str]:
        command = [
            str(substrate["command"]),
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(self.app.config.get("WORKSPACE_ROOT")),
            "--output-schema",
            schema_path,
            "-o",
            output_path,
            "-",
        ]
        model_arg = str(task.get("requested_model") or session.get("model") or "").strip()
        if model_arg:
            command.extend(["-m", model_arg])
        return command

    def _build_external_cli_command(
        self,
        session: dict[str, Any],
        task: dict[str, Any],
        substrate: dict[str, Any],
        prompt_text: str,
    ) -> list[str]:
        prompt = prompt_text
        if substrate["id"] == "claude_code":
            return [str(substrate["command"]), "-p", prompt]
        if substrate["id"] == "copilot_cli":
            return [str(substrate["command"]), "copilot", "-p", prompt]
        raise ValueError(f"unsupported external delegation substrate: {substrate['id']}")

    @staticmethod
    def _extract_json_object_text(raw_text: str) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                payload, end_index = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return text[index:index + end_index].strip()
        return text

    @staticmethod
    def _build_model_arg(provider: str, model: str) -> str:
        provider = str(provider or "").strip()
        model = str(model or "").strip()
        if not model:
            return ""
        if "/" in model or not provider:
            return model
        return f"{provider}/{model}"

    @staticmethod
    def _openclaw_session_key(task_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencloset-delegation:{task_id}"))

    @staticmethod
    def _try_parse_json_line(line: str) -> dict | None:
        candidate = str(line or "").strip()
        if not candidate or candidate[0] not in "[{":
            return None
        return DelegationWorker._try_parse_json_document(candidate)

    @staticmethod
    def _try_parse_json_document(text: str) -> dict | None:
        candidate = str(text or "").strip()
        if not candidate or candidate[0] not in "[{":
            return None
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _extract_final_text(cls, payload: dict) -> str:
        candidates = [
            payload.get("final_text"),
            payload.get("finalText"),
            payload.get("text"),
            payload.get("content"),
        ]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([data.get("final_text"), data.get("finalText"), data.get("text"), data.get("content")])
        result = payload.get("result")
        if isinstance(result, dict):
            candidates.extend([
                result.get("text"),
                result.get("content"),
                result.get("final_text"),
                result.get("finalAssistantVisibleText"),
                result.get("finalAssistantRawText"),
            ])
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    def _coerce_result_payload(self, raw_text: str, *, task_type: str) -> dict[str, Any]:
        parsed = _loads_json_value(self._extract_json_object_text(raw_text))
        if isinstance(parsed, dict):
            payload = dict(parsed)
        else:
            payload = {"summary": self._summarize_result(raw_text)}
        summary = str(payload.get("summary") or "").strip() or self._summarize_result(raw_text)
        patch_summary = str(payload.get("patch_summary") or "").strip()
        exit_status = str(payload.get("exit_status") or "").strip().lower()
        if exit_status not in {"success", "partial", "failed", "blocked"}:
            exit_status = "success" if task_type in {"summarize", "review", "audit", "proposal", "plan"} else "partial"

        def _string_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(item).strip() for item in value if str(item).strip()]

        return {
            "summary": summary,
            "files_touched": _string_list(payload.get("files_touched")),
            "commands_run": _string_list(payload.get("commands_run")),
            "tests_run": _string_list(payload.get("tests_run")),
            "tests_passed": _string_list(payload.get("tests_passed")),
            "open_questions": _string_list(payload.get("open_questions")),
            "risks": _string_list(payload.get("risks")),
            "patch_summary": patch_summary,
            "exit_status": exit_status,
        }

    def _summary_from_payload(self, payload: dict[str, Any], raw_text: str) -> str:
        summary = str(payload.get("summary") or "").strip()
        return summary or self._summarize_result(raw_text)

    def _render_payload_text(self, payload: dict[str, Any], *, fallback: str) -> str:
        summary = str(payload.get("summary") or "").strip()
        patch_summary = str(payload.get("patch_summary") or "").strip()
        risks = payload.get("risks") if isinstance(payload.get("risks"), list) else []
        lines = [line for line in [summary, patch_summary] if line]
        if risks:
            lines.append("Risks: " + "; ".join(str(item).strip() for item in risks if str(item).strip()))
        return "\n".join(lines).strip() or fallback

    @staticmethod
    def _summarize_result(text: str) -> str:
        compact = " ".join(str(text or "").split())
        if not compact:
            return "Delegated worker returned no text."
        if len(compact) <= 220:
            return compact
        return f"{compact[:217]}..."

    def _build_capture_context(
        self,
        *,
        workspace_id: str | None,
        session_id: str,
        capture_ids: Any,
    ) -> str:
        normalized_ids = self._normalize_id_list(capture_ids)
        if not workspace_id or not normalized_ids:
            return ""

        placeholders = ", ".join("?" for _ in normalized_ids)
        rows = self.app.db.execute(
            f"""
            SELECT id, source, event_type, content, media_url, metadata
            FROM captures
            WHERE workspace_id = ? AND id IN ({placeholders})
            ORDER BY received_at DESC, rowid DESC
            """,
            (workspace_id, *normalized_ids),
        ).fetchall()

        entries: list[str] = []
        for row in rows:
            metadata = _loads_json_object(row["metadata"])
            if row["id"] not in normalized_ids:
                continue
            title = str(metadata.get("title") or metadata.get("label") or row["event_type"] or "capture").strip()
            summary = self._trim_context_text(str(row["content"] or ""), 280)
            media_url = str(row["media_url"] or "").strip()
            entry = f"- {title} [{row['id']}] ({row['event_type']} via {row['source']}): {summary or '[no content]'}"
            if media_url:
                entry += f" Media: {media_url}."
            entries.append(entry)

        if not entries:
            return ""
        return "Referenced captures:\n" + "\n".join(entries)

    def _build_evidence_context(
        self,
        *,
        workspace_id: str | None,
        session_id: str,
        evidence_ids: Any,
    ) -> str:
        normalized_ids = self._normalize_id_list(evidence_ids)
        if not workspace_id or not normalized_ids:
            return ""

        placeholders = ", ".join("?" for _ in normalized_ids)
        rows = self.app.db.execute(
            f"""
            SELECT id, evidence_type, title, summary, content, source_kind, tags, metadata
            FROM workspace_evidence
            WHERE workspace_id = ? AND id IN ({placeholders})
            ORDER BY updated_at DESC, rowid DESC
            """,
            (workspace_id, *normalized_ids),
        ).fetchall()

        entries: list[str] = []
        for row in rows:
            metadata = _loads_json_object(row["metadata"])
            tags = []
            raw_tags = _loads_json_value(row["tags"]) if row["tags"] else []
            if isinstance(raw_tags, list):
                tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
            summary = self._trim_context_text(str(row["summary"] or row["content"] or ""), 280)
            entry = f"- {row['title']} [{row['id']}] ({row['evidence_type']} via {row['source_kind']}): {summary or '[no summary]'}"
            if tags:
                entry += f" Tags: {', '.join(tags)}."
            capture_id = str(metadata.get("capture_id") or "").strip()
            if capture_id:
                entry += f" Source capture: {capture_id}."
            entries.append(entry)

        if not entries:
            return ""
        return "Referenced evidence:\n" + "\n".join(entries)

    @staticmethod
    def _build_metadata_context(task_metadata: dict[str, Any]) -> str:
        summary_bits: list[str] = []
        for key in ("goal", "request_origin", "note"):
            value = str(task_metadata.get(key) or "").strip()
            if value:
                summary_bits.append(f"{key}: {value}")
        if not summary_bits:
            return ""
        return "Delegation metadata:\n" + "\n".join(f"- {bit}" for bit in summary_bits)

    @staticmethod
    def _normalize_id_list(raw_ids: Any) -> list[str]:
        if not isinstance(raw_ids, list):
            return []
        return [str(value).strip() for value in raw_ids if str(value).strip()]

    @staticmethod
    def _trim_context_text(value: str, limit: int) -> str:
        compact = " ".join(str(value or "").split())
        if len(compact) <= limit:
            return compact
        return f"{compact[:limit - 3]}..."


def register_delegation_routes(app) -> None:
    from api.api.session_validation import validate_session_route_scope

    @app.route("/api/delegation/substrates", methods=["GET"])
    def list_delegation_substrates():
        return jsonify({"substrates": app.delegations.list_substrates()})

    @app.route("/api/sessions/<session_id>/delegation-policy", methods=["GET"])
    def get_session_delegation_policy(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="get_session_delegation_policy")
        if error_response:
            return error_response
        policy = _load_delegation_policy(app, session_id)
        if policy is None:
            return jsonify({"error": "session not found"}), 404
        return jsonify({"session_id": session_id, "delegation_policy": policy})

    @app.route("/api/sessions/<session_id>/delegation-policy", methods=["PATCH"])
    def patch_session_delegation_policy(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="patch_session_delegation_policy")
        if error_response:
            return error_response
        existing = _load_delegation_policy(app, session_id)
        if existing is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        try:
            policy = _normalize_delegation_policy(payload, current_policy=existing)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        _store_delegation_policy(app, session_id, policy)
        return jsonify({"session_id": session_id, "delegation_policy": policy})

    @app.route("/api/sessions/<session_id>/delegations", methods=["GET"])
    def list_delegations(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="list_delegations")
        if error_response:
            return error_response
        limit = request.args.get("limit", 100, type=int)
        return jsonify({"session_id": session_id, "tasks": app.delegations.list_tasks(session_id, limit=limit)})

    @app.route("/api/sessions/<session_id>/delegations", methods=["POST"])
    def create_delegation(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="create_delegation")
        if error_response:
            return error_response
        payload = request.get_json(silent=True) or {}
        try:
            task = app.delegations.create_task(
                session_id,
                task_type=payload.get("task_type"),
                instruction=payload.get("instruction"),
                title=payload.get("title") or "",
                substrate_id=payload.get("substrate_id"),
                requested_provider=payload.get("provider"),
                requested_model=payload.get("model"),
                budget=payload.get("budget") if payload.get("budget") is not None else None,
                metadata=payload.get("metadata") if payload.get("metadata") is not None else None,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(task), 201
