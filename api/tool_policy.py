from __future__ import annotations

import os
from typing import Any, Mapping


def _dedupe_non_empty(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def default_tool_policy(config: Mapping[str, Any]) -> dict[str, list[str]]:
    return {
        "enabled_tools": list(config.get("DEFAULT_TOOL_ALLOWLIST", ["read", "write", "edit", "exec", "process"])),
        "allow_destructive_tools": [],
        "allowed_paths": [],
    }


def legacy_default_allowed_paths(config: Mapping[str, Any]) -> list[str]:
    workspace = str(config.get("WORKSPACE_ROOT", "") or "").strip()
    memory_root = os.path.join(os.path.dirname(workspace), "memory") if workspace else ""
    return _dedupe_non_empty([workspace, memory_root])


def normalize_policy_allowed_paths(
    config: Mapping[str, Any],
    allowed_paths: list[str] | None,
) -> list[str]:
    normalized = _dedupe_non_empty([str(path).strip() for path in allowed_paths or [] if str(path).strip()])
    if not normalized:
        return []

    workspace = str(config.get("WORKSPACE_ROOT", "") or "").strip()
    workspace_only = [workspace] if workspace else []
    legacy_default = legacy_default_allowed_paths(config)

    # Older sessions stored an implicit workspace-scoped default. Treat that
    # legacy default as unrestricted so enabled tools remain permissive unless
    # the session explicitly narrows allowed_paths.
    if normalized == workspace_only or normalized == legacy_default:
        return []

    return normalized