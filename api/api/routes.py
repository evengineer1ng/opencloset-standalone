# REST API routes — Session management + message submission
#
# Endpoints:
#   POST   /api/sessions          — Create session
#   GET    /api/sessions          — List sessions
#   GET    /api/sessions/<id>     — Get session
#   DELETE /api/sessions/<id>     — Delete session
#   POST   /api/sessions/<id>/messages — Submit user message

from __future__ import annotations

import json
import os
import sqlite3
import requests
import re
import threading
import time

from flask import Flask, jsonify, request, send_file

from api.api.session_validation import (
    build_sqlite_param_error_payload,
    coerce_sqlite_text_param,
    validate_session_route_scope,
)
from api.api.delegation import _load_delegation_policy, _normalize_delegation_policy
from api.agent.substrate_router import load_provider_capabilities, looks_like_frontier_model_name
from api.api.deletion import delete_session_data
from api.api.rollover import RolloverConflictError, SessionNotFoundError, create_rollover_successor
from api.db.schema import new_id
from api.tools.process import MAX_OUTPUT_BYTES, exec_process
from api.tool_policy import default_tool_policy, normalize_policy_allowed_paths


_FRONTIER_MODEL_RE = re.compile(r"^(gpt-|o[1345](?:-|$)|claude|gemini)", re.IGNORECASE)


def _spawn_runtime_diagnostics(app: Flask, session_id: str, run_id: str) -> None:
    def _worker() -> None:
        try:
            app.runtime_diagnostics.maybe_emit_run_error_window(session_id, run_id)
        except Exception:
            return

    threading.Thread(target=_worker, daemon=True).start()


def _default_tool_policy(app: Flask) -> dict[str, list[str]]:
    return default_tool_policy(app.config)


def _normalize_tool_policy(
    app: Flask,
    raw_policy,
    *,
    current_policy: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    if raw_policy is None:
        return current_policy or _default_tool_policy(app)
    if not isinstance(raw_policy, dict):
        raise ValueError("tool_policy must be an object")

    unknown_keys = set(raw_policy) - {"enabled_tools", "allow_destructive_tools", "allowed_paths"}
    if unknown_keys:
        raise ValueError(f"unknown tool_policy field(s): {', '.join(sorted(unknown_keys))}")

    policy = dict(current_policy or _default_tool_policy(app))

    if "enabled_tools" in raw_policy:
        enabled_tools = raw_policy["enabled_tools"]
        if not isinstance(enabled_tools, list) or not all(isinstance(name, str) and name for name in enabled_tools):
            raise ValueError("tool_policy.enabled_tools must be a list of non-empty strings")
        policy["enabled_tools"] = list(dict.fromkeys(enabled_tools))

    if "allow_destructive_tools" in raw_policy:
        destructive_tools = raw_policy["allow_destructive_tools"]
        if not isinstance(destructive_tools, list) or not all(isinstance(name, str) and name for name in destructive_tools):
            raise ValueError("tool_policy.allow_destructive_tools must be a list of non-empty strings")
        policy["allow_destructive_tools"] = list(dict.fromkeys(destructive_tools))

    if "allowed_paths" in raw_policy:
        allowed_paths = raw_policy["allowed_paths"]
        if allowed_paths is None:
            policy["allowed_paths"] = []
        elif not isinstance(allowed_paths, list) or not all(isinstance(path, str) and path for path in allowed_paths):
            raise ValueError("tool_policy.allowed_paths must be a list of non-empty strings")
        else:
            policy["allowed_paths"] = list(dict.fromkeys(allowed_paths))

    policy["allowed_paths"] = normalize_policy_allowed_paths(app.config, policy.get("allowed_paths"))

    enabled_set = set(policy["enabled_tools"])
    if any(name not in enabled_set for name in policy["allow_destructive_tools"]):
        raise ValueError("destructive tools must also be present in enabled_tools")

    return policy


def _load_tool_policy(app: Flask, session_id: str) -> dict[str, list[str]] | None:
    row = app.db.execute("SELECT tool_policy FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    if not row["tool_policy"]:
        return _default_tool_policy(app)
    try:
        stored = json.loads(row["tool_policy"])
    except (TypeError, json.JSONDecodeError):
        return _default_tool_policy(app)
    return _normalize_tool_policy(app, stored)


def _read_process_output_text(output_path: str | None) -> str:
    if not output_path or not os.path.exists(output_path):
        return ""

    try:
        with open(output_path, "r", encoding="utf-8", errors="replace") as handle:
            output = handle.read()
    except OSError:
        return ""

    if len(output) > MAX_OUTPUT_BYTES:
        return output[:MAX_OUTPUT_BYTES] + "\n\n--- [TRUNCATED] ---"
    return output


def _get_process_snapshot(app: Flask, process_session_id: str) -> dict[str, object] | None:
    store = getattr(app, "process_store", None)
    if store is None:
        return None

    handle = store.get(process_session_id)
    popen = store.get_popen(process_session_id) if handle else None
    if not handle or not popen:
        return None

    return_code = popen.poll()
    if return_code is not None:
        handle.return_code = return_code
        handle.terminated = True

    output = _read_process_output_text(handle.output_path)
    snapshot: dict[str, object] = {
        "session_id": handle.session_id,
        "command": handle.command,
        "workdir": handle.workdir,
        "pid": handle.pid,
        "interactive": bool(handle.interactive),
        "status": "completed" if handle.terminated else "running",
        "return_code": handle.return_code,
        "elapsed_seconds": round(max(0.0, time.time() - handle.start_time), 2),
        "output": output,
    }

    if handle.terminated:
        store.cleanup(process_session_id)

    return snapshot


def _process_error_status(message: str) -> int:
    lowered = message.lower()
    if "unknown process" in lowered or "handle lost" in lowered:
        return 404
    if "already completed" in lowered or "cannot write" in lowered or "cannot send" in lowered:
        return 409
    return 400


def _store_tool_policy(app: Flask, session_id: str, policy: dict[str, list[str]]) -> None:
    app.db.execute(
        "UPDATE sessions SET tool_policy = ?, updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) WHERE id = ?",
        (json.dumps(policy), session_id),
    )
    app.db.commit()


def _normalize_metadata_object(raw_metadata) -> dict[str, object]:
    if raw_metadata in (None, ""):
        return {}
    if not isinstance(raw_metadata, dict):
        raise ValueError("metadata must be an object")
    return dict(raw_metadata)


def _normalize_capture_ids(raw_capture_ids) -> list[str]:
    if raw_capture_ids in (None, ""):
        return []
    if not isinstance(raw_capture_ids, list):
        raise ValueError("capture_ids must be a list of strings")
    normalized = [str(capture_id).strip() for capture_id in raw_capture_ids if str(capture_id).strip()]
    return list(dict.fromkeys(normalized))


def _json_safe_provider_route(value):
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    return None


def _serialize_provider_row(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "base_url": row["base_url"],
        "model_name": row["model_name"],
        "timeout_sec": row["timeout_sec"],
        "enabled": bool(row["enabled"]),
        "capabilities": load_provider_capabilities(row["capabilities"], row["kind"]),
        "has_api_key": bool(row["api_key"]),
        "last_health_status": row["last_health_status"],
        "last_health_at": row["last_health_at"],
    }


def _get_provider_row(app: Flask, provider_id: str):
    return app.db.execute(
        """
        SELECT id, kind, base_url, model_name, timeout_sec, enabled, capabilities, api_key, last_health_status, last_health_at
        FROM providers
        WHERE id = ? OR kind = ?
        ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (provider_id, provider_id, provider_id),
    ).fetchone()


def _provider_exists(app: Flask, provider_id: str) -> bool:
    return _get_provider_row(app, provider_id) is not None


def _coerce_sqlite_text_param(value, field_name: str) -> str:
    return coerce_sqlite_text_param(value, field_name)


def _looks_like_frontier_model_name(model: str) -> bool:
    normalized = str(model or "").strip()
    return bool(normalized and looks_like_frontier_model_name(normalized))


def _validate_session_model_provider_pair(provider: str, model: str) -> str | None:
    if provider in {"llamacpp", "ollama"} and _looks_like_frontier_model_name(model):
        return (
            f"model '{model}' looks like a frontier model but provider '{provider}' is local; "
            "switch the provider to openai or choose a local model id"
        )
    return None


def _discover_provider_models(row) -> tuple[list[str], str | None]:
    models: list[str] = []
    error: str | None = None
    endpoint = f"{str(row['base_url']).rstrip('/')}/models"
    headers = {}
    if row["api_key"]:
        headers["Authorization"] = f"Bearer {row['api_key']}"

    try:
        response = requests.get(endpoint, headers=headers or None, timeout=float(row["timeout_sec"] or 30))
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            for item in payload["data"]:
                if isinstance(item, dict):
                    model_id = item.get("id") or item.get("model") or item.get("name")
                    if isinstance(model_id, str) and model_id.strip():
                        models.append(model_id.strip())
        elif isinstance(payload, dict) and isinstance(payload.get("models"), list):
            for item in payload["models"]:
                if isinstance(item, str) and item.strip():
                    models.append(item.strip())
                elif isinstance(item, dict):
                    model_id = item.get("id") or item.get("name")
                    if isinstance(model_id, str) and model_id.strip():
                        models.append(model_id.strip())
    except (requests.RequestException, ValueError) as exc:
        error = str(exc)

    configured = str(row["model_name"] or "").strip()
    if configured:
        models.append(configured)

    deduped: list[str] = []
    seen: set[str] = set()
    for model in models:
      if model not in seen:
        seen.add(model)
        deduped.append(model)

    return deduped, error


def register_routes(app: Flask) -> None:
    """Register all REST API routes on the Flask app."""

    @app.route("/api/health", methods=["GET"])
    def api_health():
        return jsonify({
            "status": "ok",
            "db_path": app.config.get("DB_PATH"),
            "websockets_enabled": bool(app.config.get("RUNTIME_WEBSOCKETS_ENABLED", False)),
        })

    @app.route("/api/providers", methods=["GET"])
    def list_providers():
        rows = app.db.execute(
            """
            SELECT id, kind, base_url, model_name, timeout_sec, enabled, capabilities, api_key, last_health_status, last_health_at
            FROM providers
            ORDER BY id ASC
            """
        ).fetchall()
        return jsonify({"providers": [_serialize_provider_row(row) for row in rows]})

    @app.route("/api/providers/<provider_id>/models", methods=["GET"])
    def list_provider_models(provider_id: str):
        row = _get_provider_row(app, provider_id)
        if not row:
            return jsonify({"error": "provider not found"}), 404

        models, error = _discover_provider_models(row)
        return jsonify({
            "provider_id": row["id"],
            "models": models,
            "discovered": error is None,
            "error": error,
        })

    @app.route("/api/providers/<provider_id>", methods=["PATCH"])
    def patch_provider(provider_id: str):
        row = _get_provider_row(app, provider_id)
        if not row:
            return jsonify({"error": "provider not found"}), 404

        data = request.get_json(silent=True) or {}
        allowed_fields = {"base_url", "model_name", "timeout_sec", "enabled", "api_key", "capabilities"}
        unknown = set(data) - allowed_fields
        if unknown:
            return jsonify({"error": f"unknown provider field(s): {', '.join(sorted(unknown))}"}), 400

        base_url = str(data.get("base_url", row["base_url"]) or "").strip()
        model_name = str(data.get("model_name", row["model_name"]) or "").strip()
        timeout_sec = data.get("timeout_sec", row["timeout_sec"])
        enabled = data.get("enabled", bool(row["enabled"]))
        api_key = data["api_key"] if "api_key" in data else row["api_key"]
        capabilities = data.get("capabilities") if "capabilities" in data else load_provider_capabilities(row["capabilities"], row["kind"])

        if not isinstance(timeout_sec, int) or timeout_sec <= 0:
            return jsonify({"error": "timeout_sec must be a positive integer"}), 400
        if not isinstance(enabled, bool):
            return jsonify({"error": "enabled must be a boolean"}), 400
        if api_key is not None and not isinstance(api_key, str):
            return jsonify({"error": "api_key must be a string or null"}), 400
        if not isinstance(capabilities, dict):
            return jsonify({"error": "capabilities must be an object"}), 400
        if not base_url:
            return jsonify({"error": "base_url is required"}), 400

        app.db.execute(
            """
            UPDATE providers
            SET base_url = ?, model_name = ?, timeout_sec = ?, enabled = ?, capabilities = ?, api_key = ?
            WHERE id = ?
            """,
            (base_url, model_name, timeout_sec, 1 if enabled else 0, json.dumps(capabilities), api_key, row["id"]),
        )
        app.db.commit()

        updated = app.db.execute(
            """
            SELECT id, kind, base_url, model_name, timeout_sec, enabled, capabilities, api_key, last_health_status, last_health_at
            FROM providers
            WHERE id = ?
            """,
            (row["id"],),
        ).fetchone()
        return jsonify(_serialize_provider_row(updated))

    # -----------------------------------------------------------------------
    # Sessions
    # -----------------------------------------------------------------------

    @app.route("/api/sessions", methods=["POST"])
    def create_session():
        """Create a new session.

        Request body (JSON):
            {
                "label": "optional name",
                "model": "required model id",
                "provider": "llamacpp | ollama | openai | auto (default auto)",
                "context_window": 65536
            }
        """
        data = request.get_json(silent=True) or {}

        model = data.get("model")
        if not model:
            return jsonify({"error": "model is required"}), 400

        session_id = new_id()
        label = data.get("label", "")
        provider = data.get("provider", "auto")
        if not isinstance(provider, str) or provider not in {"llamacpp", "ollama", "openai", "auto"}:
            return jsonify({"error": "provider must be one of: llamacpp, ollama, openai, auto"}), 400
        if provider != "auto" and not _provider_exists(app, provider):
            return jsonify({"error": f"provider is not configured: {provider}"}), 409
        mismatch_error = _validate_session_model_provider_pair(provider, str(model))
        if mismatch_error:
            return jsonify({"error": mismatch_error}), 400
        context_window = data.get("context_window", 65536)
        workspace_id = data.get("workspace_id")
        build_project_id = data.get("build_project_id")
        try:
            tool_policy = _normalize_tool_policy(app, data.get("tool_policy"))
            delegation_policy = _normalize_delegation_policy(data.get("delegation_policy"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        # Validate workspace / project FKs
        db = app.db
        if workspace_id is not None:
            ws = app.workspaces.get_workspace(workspace_id)
            if not ws:
                return jsonify({"error": "workspace not found"}), 404
        if build_project_id is not None:
            proj = app.workspaces.get_build_project(build_project_id)
            if not proj:
                return jsonify({"error": "build project not found"}), 404
            if workspace_id is not None and proj["workspace_id"] != workspace_id:
                return jsonify({"error": "project does not belong to the given workspace"}), 400

        db.execute(
            """INSERT INTO sessions (id, label, model, provider, context_window, workspace_id, build_project_id, tool_policy, delegation_policy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                label,
                model,
                provider,
                context_window,
                workspace_id,
                build_project_id,
                json.dumps(tool_policy),
                json.dumps(delegation_policy),
            ),
        )
        db.commit()

        app.event_logger.log_session_created(session_id, label=label, model=model)
        app.planning.bootstrap_session(session_id, workspace_id=workspace_id, build_project_id=build_project_id)

        return jsonify({
            "id": session_id,
            "label": label,
            "model": model,
            "provider": provider,
            "context_window": context_window,
            "status": "active",
            "workspace_id": workspace_id,
            "build_project_id": build_project_id,
            "tool_policy": tool_policy,
            "delegation_policy": delegation_policy,
        }), 201

    @app.route("/api/sessions", methods=["GET"])
    def list_sessions():
        """List all sessions. Optional query: ?status=active"""
        db = app.db
        status_filter = request.args.get("status")

        if status_filter:
            rows = db.execute(
                "SELECT id, label, model, provider, status, token_count, context_window, "
                "workspace_id, build_project_id, created_at "
                "FROM sessions WHERE status = ? ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, label, model, provider, status, token_count, context_window, "
                "workspace_id, build_project_id, created_at "
                "FROM sessions ORDER BY created_at DESC"
            ).fetchall()

        sessions = []
        for r in rows:
            sessions.append({
                "id": r["id"],
                "label": r["label"],
                "model": r["model"],
                "provider": r["provider"],
                "status": r["status"],
                "token_count": r["token_count"],
                "context_window": r["context_window"],
                "workspace_id": r["workspace_id"],
                "build_project_id": r["build_project_id"],
                "created_at": r["created_at"],
            })

        return jsonify({"sessions": sessions})

    @app.route("/api/sessions/<session_id>", methods=["GET"])
    def get_session(session_id: str):
        """Get session details including recent messages."""
        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="get_session")
        if error_response:
            return error_response
        row = db.execute(
            "SELECT id, label, model, provider, status, token_count, context_window, "
            "task_budget_remaining, rolled_over_to, workspace_id, build_project_id, tool_policy, delegation_policy, created_at, updated_at "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

        if not row:
            return jsonify({"error": "session not found"}), 404

        # Get message count
        msg_count = db.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()["cnt"]

        # Get current run
        current_run = db.execute(
            "SELECT id, status, turn_number FROM runs "
            "WHERE session_id = ? AND status = 'running' LIMIT 1",
            (session_id,),
        ).fetchone()

        return jsonify({
            "id": row["id"],
            "label": row["label"],
            "model": row["model"],
            "provider": row["provider"],
            "status": row["status"],
            "token_count": row["token_count"],
            "context_window": row["context_window"],
            "task_budget_remaining": row["task_budget_remaining"],
            "rolled_over_to": row["rolled_over_to"],
            "workspace_id": row["workspace_id"],
            "build_project_id": row["build_project_id"],
            "tool_policy": _load_tool_policy(app, session_id),
            "delegation_policy": _load_delegation_policy(app, session_id),
            "message_count": msg_count,
            "current_run": {
                "id": current_run["id"],
                "status": current_run["status"],
                "turn_number": current_run["turn_number"],
            } if current_run else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })

    @app.route("/api/sessions/<session_id>", methods=["PATCH"])
    def patch_session(session_id: str):
        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="patch_session")
        if error_response:
            return error_response
        row = db.execute(
            "SELECT id, label, model, provider, workspace_id, build_project_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "session not found"}), 404

        running = db.execute(
            "SELECT id FROM runs WHERE session_id = ? AND status = 'running' LIMIT 1",
            (session_id,),
        ).fetchone()
        if running:
            return jsonify({"error": "cannot patch session with active run"}), 409

        data = request.get_json(silent=True) or {}
        allowed_fields = {"label", "model", "provider", "build_project_id"}
        unknown = set(data) - allowed_fields
        if unknown:
            return jsonify({"error": f"unknown session field(s): {', '.join(sorted(unknown))}"}), 400

        label = str(data.get("label", row["label"]) or "").strip()
        model = str(data.get("model", row["model"]) or "").strip()
        provider = str(data.get("provider", row["provider"]) or "").strip()
        build_project_id = data.get("build_project_id", row["build_project_id"])

        if not model:
            return jsonify({"error": "model is required"}), 400
        if provider not in {"llamacpp", "ollama", "openai", "auto"}:
            return jsonify({"error": "provider must be one of: llamacpp, ollama, openai, auto"}), 400
        if provider != "auto" and not _provider_exists(app, provider):
            return jsonify({"error": f"provider is not configured: {provider}"}), 409
        mismatch_error = _validate_session_model_provider_pair(provider, model)
        if mismatch_error:
            return jsonify({"error": mismatch_error}), 400

        if build_project_id is not None:
            proj = app.workspaces.get_build_project(build_project_id)
            if not proj:
                return jsonify({"error": "build project not found"}), 404
            if row["workspace_id"] is not None and proj["workspace_id"] != row["workspace_id"]:
                return jsonify({"error": "project does not belong to the session workspace"}), 400

        db.execute(
            """
            UPDATE sessions
            SET label = ?, model = ?, provider = ?, build_project_id = ?,
                updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            WHERE id = ?
            """,
            (label, model, provider, build_project_id, session_id),
        )
        db.commit()

        return get_session(session_id)

    @app.route("/api/sessions/<session_id>/tool-policy", methods=["GET"])
    def get_session_tool_policy(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="get_session_tool_policy")
        if error_response:
            return error_response
        policy = _load_tool_policy(app, session_id)
        if policy is None:
            return jsonify({"error": "session not found"}), 404
        return jsonify({"session_id": session_id, "tool_policy": policy})

    @app.route("/api/sessions/<session_id>/tool-policy", methods=["PATCH"])
    def patch_session_tool_policy(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="patch_session_tool_policy")
        if error_response:
            return error_response
        existing = _load_tool_policy(app, session_id)
        if existing is None:
            return jsonify({"error": "session not found"}), 404
        data = request.get_json(silent=True) or {}
        try:
            policy = _normalize_tool_policy(app, data, current_policy=existing)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        _store_tool_policy(app, session_id, policy)
        return jsonify({"session_id": session_id, "tool_policy": policy})

    @app.route("/api/sessions/<session_id>/events", methods=["GET"])
    def get_session_events(session_id: str):
        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="get_session_events")
        if error_response:
            return error_response

        limit = request.args.get("limit", 1000, type=int)
        return jsonify({
            "session_id": session_id,
            "events": app.event_logger.get_session_events(session_id, limit=limit),
        })

    @app.route("/api/sessions/<session_id>/rollover", methods=["POST"])
    def rollover_session(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="rollover_session")
        if error_response:
            return error_response
        data = request.get_json(silent=True) or {}
        try:
            result = create_rollover_successor(
                app,
                session_id,
                label=data.get("label"),
                task_budget_remaining=data.get("task_budget_remaining"),
            )
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except RolloverConflictError as exc:
            error_text = str(exc).lower()
            if "active run" in error_text:
                return jsonify({"error": "cannot roll over session with active run"}), 409
            if "not active" in error_text:
                return jsonify({"error": f"session is {error_text.split('session is ', 1)[1]}"}), 409
            return jsonify({"error": str(exc)}), 409

        return jsonify(result.to_dict()), 201

    @app.route("/api/sessions/<session_id>", methods=["DELETE"])
    def delete_session(session_id: str):
        """Delete a session and all associated data."""
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="delete_session")
        if error_response:
            return error_response
        if not delete_session_data(app.db, session_id):
            return jsonify({"error": "session not found"}), 404

        return jsonify({"deleted": session_id})

    # -----------------------------------------------------------------------
    # Messages
    # -----------------------------------------------------------------------

    @app.route("/api/sessions/<session_id>/messages", methods=["POST"])
    def submit_message(session_id: str):
        """Submit a user message for processing.

        Request body (JSON):
            {
                "content": "user text",
                "role": "user | system (default user)",
                "attachments": [{...}],  # optional
                "capture_ids": ["cap_..."],  # optional
                "metadata": { ... }  # optional
            }

        Returns the queued message and triggers agent loop processing.
        The agent loop (Phase 4) will pick up the message and run a turn.
        """
        data = request.get_json(silent=True) or {}

        content = data.get("content", "")
        if not content:
            return jsonify({"error": "content is required"}), 400

        role = data.get("role", "user")
        if role not in ("user", "system"):
            return jsonify({"error": "role must be 'user' or 'system'"}), 400

        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="submit_message")
        if error_response:
            return error_response

        # Check session exists
        session = db.execute(
            "SELECT id, status, workspace_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return jsonify({"error": "session not found"}), 404
        if session["status"] != "active":
            return jsonify({"error": f"session is {session['status']}, not active"}), 409

        try:
            metadata = _normalize_metadata_object(data.get("metadata"))
            capture_ids = _normalize_capture_ids(data.get("capture_ids"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        # Create a run for this turn (crash-safe: persist before model execution)
        run_id = new_id()
        max_turns = app.config.get("LOOP_MAX_TURNS")
        turn_number = db.execute(
            "SELECT COALESCE(MAX(turn_number), 0) AS tn FROM runs WHERE session_id = ?",
            (session_id,),
        ).fetchone()["tn"] + 1

        db.execute(
            """INSERT INTO runs (id, session_id, status, turn_number, max_turns)
               VALUES (?, ?, 'queued', ?, ?)""",
            (run_id, session_id, turn_number, max_turns),
        )

        combined_attachments: list[dict[str, object]] = []
        try:
            capture_attachments = app.run_inputs.materialize_capture_attachments(
                session_id=session_id,
                workspace_id=str(session["workspace_id"] or "") or None,
                capture_ids=capture_ids,
            )
            combined_attachments = list(data.get("attachments") or []) + capture_attachments
            input_metadata = dict(metadata)
            if capture_ids:
                input_metadata["capture_ids"] = capture_ids
            if combined_attachments or input_metadata:
                app.run_inputs.store(
                    session_id,
                    run_id,
                    role=role,
                    attachments=combined_attachments,
                    metadata=input_metadata,
                )
                if capture_ids:
                    app.run_inputs.attach_captures_to_run(run_id, capture_ids)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        # Persist user message via TranscriptManager (single authoritative writer)
        msg_id = app.transcript.submit_user_message(
            session_id, run_id, content, role=role,
        )
        position = app.transcript.get_message_count(session_id)

        # Update session updated_at
        db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (session_id,),
        )

        # Log queue event; RunManager logs actual run start on queued -> running.
        app.event_logger.log(
            session_id,
            "run_queued",
            {"run_id": run_id, "turn_number": turn_number},
            run_id,
        )

        db.commit()

        return jsonify({
            "message_id": msg_id,
            "run_id": run_id,
            "turn_number": turn_number,
            "session_id": session_id,
            "role": role,
            "content": content,
            "position": position,
            "status": "queued",
            "attachments": combined_attachments,
            "metadata": metadata,
            "capture_ids": capture_ids,
        }), 201

    @app.route("/api/sessions/<session_id>/attachments", methods=["POST"])
    def upload_session_attachments(session_id: str):
        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="upload_session_attachments")
        if error_response:
            return error_response

        session = db.execute(
            "SELECT id, status, workspace_id, build_project_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not session:
            return jsonify({"error": "session not found"}), 404
        if session["status"] != "active":
            return jsonify({"error": f"session is {session['status']}, not active"}), 409

        uploads = request.files.getlist("files")
        if not uploads:
            return jsonify({"error": "files are required"}), 400

        attachments: list[dict[str, object]] = []
        capture_ids: list[str] = []
        for upload in uploads:
            if not getattr(upload, "filename", ""):
                continue
            attachment = app.session_attachments.store_upload(session, upload)
            attachments.append(attachment)
            capture_id = str(attachment.get("capture_id") or "").strip()
            if capture_id:
                capture_ids.append(capture_id)

        if not attachments:
            return jsonify({"error": "no valid files were uploaded"}), 400

        db.commit()
        return jsonify({
            "session_id": session_id,
            "attachments": attachments,
            "capture_ids": capture_ids,
        }), 201

    @app.route("/api/sessions/<session_id>/attachments", methods=["GET"])
    def list_session_attachments(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="list_session_attachments")
        if error_response:
            return error_response

        session = app.db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            return jsonify({"error": "session not found"}), 404

        attachments = app.session_attachments.list_attachments(session_id)
        return jsonify({"session_id": session_id, "attachments": attachments})

    @app.route("/api/sessions/<session_id>/attachments/<attachment_id>", methods=["GET"])
    def get_session_attachment(session_id: str, attachment_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="get_session_attachment")
        if error_response:
            return error_response

        attachment = app.session_attachments.get_attachment(session_id, attachment_id)
        if not attachment:
            return jsonify({"error": "attachment not found"}), 404
        return jsonify(attachment)

    @app.route("/api/sessions/<session_id>/attachments/<attachment_id>/download", methods=["GET"])
    def download_session_attachment(session_id: str, attachment_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="download_session_attachment")
        if error_response:
            return error_response

        row = app.session_attachments.get_attachment_record(session_id, attachment_id)
        if not row:
            return jsonify({"error": "attachment not found"}), 404
        return send_file(
            str(row["storage_path"]),
            mimetype=str(row["mime_type"] or "application/octet-stream"),
            as_attachment=True,
            download_name=str(row["file_name"] or attachment_id),
        )

    @app.route("/api/sessions/<session_id>/attachments/<attachment_id>/delivery", methods=["PATCH"])
    def update_session_attachment_delivery(session_id: str, attachment_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="update_session_attachment_delivery")
        if error_response:
            return error_response

        data = request.get_json(silent=True) or {}
        status = str(data.get("status") or "").strip()
        if not status:
            return jsonify({"error": "status is required"}), 400

        metadata_update = data.get("metadata")
        if metadata_update is not None and not isinstance(metadata_update, dict):
            return jsonify({"error": "metadata must be an object"}), 400

        attachment = app.session_attachments.update_delivery_status(
            session_id,
            attachment_id,
            status=status,
            device_id=str(data.get("device_id") or "").strip() or None,
            note=str(data.get("note") or "").strip() or None,
            metadata_update=metadata_update,
        )
        if not attachment:
            return jsonify({"error": "attachment not found"}), 404

        app.db.commit()
        return jsonify(attachment)

    @app.route("/api/sessions/<session_id>/messages", methods=["GET"])
    def list_messages(session_id: str):
        """List messages for a session.

        Query params:
            ?limit=20&offset=0
            ?role=user (filter)
        """
        db = app.db
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="list_messages")
        if error_response:
            return error_response

        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        offset_provided = "offset" in request.args
        role_filter = request.args.get("role")

        if role_filter and not offset_provided:
            rows = db.execute(
                "SELECT id, role, content, position, token_estimate, created_at "
                "FROM ("
                "  SELECT id, role, content, position, token_estimate, created_at "
                "  FROM messages WHERE session_id = ? AND role = ? "
                "  ORDER BY position DESC LIMIT ?"
                ") ORDER BY position ASC",
                (session_id, role_filter, limit),
            ).fetchall()
        elif role_filter:
            rows = db.execute(
                "SELECT id, role, content, position, token_estimate, created_at "
                "FROM messages WHERE session_id = ? AND role = ? "
                "ORDER BY position ASC LIMIT ? OFFSET ?",
                (session_id, role_filter, limit, offset),
            ).fetchall()
        elif not offset_provided:
            rows = db.execute(
                "SELECT id, role, content, position, token_estimate, created_at "
                "FROM ("
                "  SELECT id, role, content, position, token_estimate, created_at "
                "  FROM messages WHERE session_id = ? "
                "  ORDER BY position DESC LIMIT ?"
                ") ORDER BY position ASC",
                (session_id, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, role, content, position, token_estimate, created_at "
                "FROM messages WHERE session_id = ? "
                "ORDER BY position ASC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()

        archive_ready_states = {
            item["message_id"]: item
            for item in app.transcript.list_message_states(
                session_id,
                state_type="archive-ready",
                status="active",
                limit=5000,
            )
        }
        messages = []
        for r in rows:
            archive_state = archive_ready_states.get(r["id"])
            messages.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "position": r["position"],
                "token_estimate": r["token_estimate"],
                "created_at": r["created_at"],
                "archive_ready": archive_state is not None,
                "archive_state": archive_state,
            })

        return jsonify({"messages": messages, "session_id": session_id})

    @app.route("/api/sessions/<session_id>/runs/<run_id>/execute", methods=["POST"])
    def execute_run(session_id: str, run_id: str):
        """Execute one queued run synchronously through the local agent loop."""
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="execute_run")
        if error_response:
            return error_response

        run = app.run_manager.get_run(run_id)
        if not run or run["session_id"] != session_id:
            return jsonify({"error": "run not found"}), 404
        if run["status"] not in ("queued", "running"):
            return jsonify({"error": f"run is {run['status']}, not executable"}), 409

        try:
            result = app.execution_runtime.execute_run(session_id, run_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception as exc:
            try:
                refreshed = app.run_manager.get_run(run_id)
                if refreshed and refreshed["status"] in ("queued", "running"):
                    app.run_manager.fail_run(run_id, str(exc) or type(exc).__name__)
                _spawn_runtime_diagnostics(app, session_id, run_id)
            except Exception:
                pass
            return jsonify({"error": str(exc) or type(exc).__name__}), 500

        _spawn_runtime_diagnostics(app, session_id, run_id)

        return jsonify({
            "run_id": result.run_id,
            "session_id": result.session_id,
            "status": result.status,
            "finish_reason": result.finish_reason,
            "text": result.text,
            "final_text": result.final_text,
            "transient_text": result.transient_text,
            "provider_route": _json_safe_provider_route(result.provider_route),
            "tool_results": result.tool_results or [],
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "interrupted": result.interrupted,
            "error": result.error,
        })

    @app.route("/api/sessions/<session_id>/runs/<run_id>", methods=["GET"])
    def get_run(session_id: str, run_id: str):
        """Return durable run status plus a small synthesized result view."""
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="get_run")
        if error_response:
            return error_response

        run = app.run_manager.get_run(run_id)
        if not run or run["session_id"] != session_id:
            return jsonify({"error": "run not found"}), 404

        tool_rows = app.db.execute(
            """
            SELECT id, tool_name, status, error, output
            FROM tool_invocations
            WHERE run_id = ?
            ORDER BY started_at ASC, completed_at ASC, id ASC
            """,
            (run_id,),
        ).fetchall()
        tool_results = []
        for row in tool_rows:
            output_data = None
            content = ""
            if row["output"]:
                try:
                    output_data = json.loads(row["output"])
                except (TypeError, json.JSONDecodeError):
                    output_data = row["output"]
                if isinstance(output_data, dict):
                    content = str(output_data.get("content") or "")
                elif isinstance(output_data, str):
                    content = output_data
            tool_results.append(
                {
                    "tool_id": row["id"],
                    "tool_name": row["tool_name"],
                    "status": row["status"],
                    "content": content,
                    "output": output_data,
                    "error": row["error"],
                }
            )

        stream_events = app.event_logger.get_run_events(session_id, run_id)
        final_event = None
        usage_event = None
        for event in reversed(stream_events):
            if usage_event is None and event.get("type") == "usage":
                usage_event = event
            if final_event is None and event.get("type") == "assistant_final":
                final_event = event
            if final_event and usage_event:
                break

        assistant_row = app.db.execute(
            """
            SELECT content
            FROM messages
            WHERE run_id = ? AND role = 'assistant'
            ORDER BY position DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        final_text = str(assistant_row["content"] or "") if assistant_row else ""
        transient_text = final_text
        finish_reason = ""
        input_tokens = 0
        output_tokens = 0
        transcript_persisted = bool(final_text.strip())
        if isinstance(final_event, dict):
            data = final_event.get("data") or {}
            if not final_text:
                final_text = str(data.get("final_text") or "")
            transient_text = str(data.get("transient_text") or transient_text or final_text)
            finish_reason = str(data.get("finish_reason") or "")
            transcript_persisted = bool(data.get("transcript_persisted", transcript_persisted))
        if isinstance(usage_event, dict):
            data = usage_event.get("data") or {}
            input_tokens = int(data.get("input_tokens") or 0)
            output_tokens = int(data.get("output_tokens") or 0)

        return jsonify(
            {
                "run_id": run["id"],
                "session_id": run["session_id"],
                "status": run["status"],
                "turn_number": run["turn_number"],
                "max_turns": run["max_turns"],
                "error": run["error"],
                "created_at": run["created_at"],
                "completed_at": run["completed_at"],
                "finish_reason": finish_reason,
                "text": final_text if transcript_persisted else "",
                "final_text": final_text if transcript_persisted else "",
                "transient_text": transient_text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "tool_results": tool_results,
            }
        )

    @app.route("/api/sessions/<session_id>/runs/<run_id>/interrupt", methods=["POST"])
    def interrupt_run(session_id: str, run_id: str):
        """Interrupt a queued run or request cooperative interruption for a running run."""
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="interrupt_run")
        if error_response:
            return error_response

        run = app.run_manager.get_run(run_id)
        if not run or run["session_id"] != session_id:
            return jsonify({"error": "run not found"}), 404
        if run["status"] == "queued":
            app.run_manager.interrupt_run(run_id)
            interrupted = app.run_manager.get_run(run_id)
            return jsonify({
                "run_id": run_id,
                "session_id": session_id,
                "status": interrupted["status"] if interrupted else "interrupted",
            })
        if run["status"] == "running":
            if app.execution_runtime.request_interrupt(run_id):
                return jsonify({
                    "run_id": run_id,
                    "session_id": session_id,
                    "status": "interrupt_requested",
                })
            refreshed = app.run_manager.get_run(run_id)
            if refreshed and refreshed["status"] == "interrupted":
                return jsonify({
                    "run_id": run_id,
                    "session_id": session_id,
                    "status": "interrupted",
                })
            return jsonify({"error": "run is running but has no active execution to interrupt"}), 409
        return jsonify({"error": f"run is {run['status']}, not interruptible"}), 409

    @app.route("/api/processes/<process_session_id>", methods=["GET"])
    def get_process(process_session_id: str):
        snapshot = _get_process_snapshot(app, process_session_id)
        if snapshot is None:
            return jsonify({"error": "process not found"}), 404
        return jsonify(snapshot)

    @app.route("/api/processes/<process_session_id>/input", methods=["POST"])
    def send_process_input(process_session_id: str):
        payload = request.get_json(silent=True) or {}
        data = payload.get("data")
        keys = payload.get("keys")
        submit = bool(payload.get("submit", False))

        if data is not None and not isinstance(data, str):
            return jsonify({"error": "data must be a string"}), 400
        if keys is not None and not isinstance(keys, str):
            return jsonify({"error": "keys must be a string"}), 400
        if data is None and not keys and not submit:
            return jsonify({"error": "provide data, keys, or submit"}), 400

        store = getattr(app, "process_store", None)
        action_results: list[str] = []

        if data is not None:
            result = exec_process(
                {"action": "write", "sessionId": process_session_id, "data": data},
                store=store,
            )
            if result.startswith("Error:") or "already completed" in result.lower():
                return jsonify({"error": result}), _process_error_status(result)
            action_results.append(result)

        if keys:
            result = exec_process(
                {"action": "send-keys", "sessionId": process_session_id, "keys": keys},
                store=store,
            )
            if result.startswith("Error:") or "already completed" in result.lower():
                return jsonify({"error": result}), _process_error_status(result)
            action_results.append(result)

        if submit:
            result = exec_process(
                {"action": "send-keys", "sessionId": process_session_id, "keys": "Enter"},
                store=store,
            )
            if result.startswith("Error:") or "already completed" in result.lower():
                return jsonify({"error": result}), _process_error_status(result)
            action_results.append(result)

        snapshot = _get_process_snapshot(app, process_session_id)
        return jsonify({
            "session_id": process_session_id,
            "results": action_results,
            "process": snapshot,
        })

    @app.route("/api/processes/<process_session_id>/terminate", methods=["POST"])
    def terminate_process(process_session_id: str):
        result = exec_process(
            {"action": "kill", "sessionId": process_session_id},
            store=getattr(app, "process_store", None),
        )
        if result.startswith("Error:"):
            return jsonify({"error": result}), _process_error_status(result)

        snapshot = _get_process_snapshot(app, process_session_id)
        return jsonify({
            "session_id": process_session_id,
            "result": result,
            "process": snapshot,
        })
