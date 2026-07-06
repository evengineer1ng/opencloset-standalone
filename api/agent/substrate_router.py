from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_FRONTIER_MODEL_RE = re.compile(r"(?i)(gpt|o1|o3|claude|gemini|sonnet|haiku|opus)")
_TOOL_CAPABLE_TASK_RE = re.compile(
    r"\b(?:fix|implement|patch|edit|write|change|update|debug|diagnose|investigate|inspect|run|test|build|verify|benchmark|measure|profile|refactor|search|read|open|check|use\s+tools?|tool\s+use|repo|codebase|workspace|file|command|terminal)\b",
    re.IGNORECASE,
)


def looks_like_frontier_model_name(model_name: str) -> bool:
    return bool(_FRONTIER_MODEL_RE.search(str(model_name or "").strip()))


def default_provider_capabilities(provider_kind: str) -> dict[str, Any]:
    provider_kind = str(provider_kind or "").strip().lower()
    local = provider_kind in {"llamacpp", "ollama"}
    remote = provider_kind == "openai"
    return {
        "supports_tool_use": True,
        "supports_vision": remote,
        "supports_audio_input": remote,
        "frontier": remote,
        "locality": "local" if local else "remote" if remote else "unknown",
        "latency_tier": "local" if local else "frontier" if remote else "unknown",
    }


def load_provider_capabilities(raw_capabilities: Any, provider_kind: str) -> dict[str, Any]:
    capabilities = default_provider_capabilities(provider_kind)
    if raw_capabilities in (None, ""):
        return capabilities

    parsed: Any = raw_capabilities
    if isinstance(raw_capabilities, str):
        try:
            parsed = json.loads(raw_capabilities)
        except json.JSONDecodeError:
            return capabilities

    if not isinstance(parsed, dict):
        return capabilities

    for key, value in parsed.items():
        if key not in capabilities and not isinstance(value, (bool, int, float, str)):
            continue
        capabilities[key] = value
    return capabilities


@dataclass(frozen=True)
class SubstrateRouteDecision:
    requested_provider: str
    resolved_provider: str
    requested_model: str
    resolved_model: str
    route_reason: str
    used_auto_routing: bool
    provider_capabilities: dict[str, Any]
    task_profile: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "requested_provider": self.requested_provider,
            "resolved_provider": self.resolved_provider,
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "route_reason": self.route_reason,
            "used_auto_routing": self.used_auto_routing,
            "provider_capabilities": dict(self.provider_capabilities),
            "task_profile": dict(self.task_profile),
        }


class SubstrateRouter:
    def __init__(self, db) -> None:
        self.db = db

    def route(
        self,
        requested_provider: str,
        requested_model: str,
        *,
        message_text: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> SubstrateRouteDecision:
        requested_provider = str(requested_provider or "auto").strip().lower()
        requested_model = str(requested_model or "").strip()
        task_profile = self._build_task_profile(message_text=message_text, attachments=attachments)

        if requested_provider != "auto":
            row = self._get_provider_row(requested_provider)
            provider_kind = row["kind"] if row else requested_provider
            capabilities = load_provider_capabilities(row["capabilities"] if row else None, provider_kind)
            resolved_model = requested_model or (str(row["model_name"] or "").strip() if row else "")
            return SubstrateRouteDecision(
                requested_provider=requested_provider,
                resolved_provider=requested_provider,
                requested_model=requested_model,
                resolved_model=resolved_model,
                route_reason="session_provider_pinned",
                used_auto_routing=False,
                provider_capabilities=capabilities,
                task_profile=task_profile,
            )

        candidates = self._list_enabled_providers()
        if not candidates:
            raise ValueError("No enabled providers are configured for auto routing.")

        requires_vision = bool(task_profile["requires_vision"])
        requires_audio = bool(task_profile["requires_audio"])
        needs_frontier_tools = bool(task_profile["needs_frontier_tools"])
        frontier_requested = looks_like_frontier_model_name(requested_model)

        chosen = None
        route_reason = ""

        if requires_vision or requires_audio:
            chosen = self._pick_capable_provider(candidates, requires_vision=requires_vision, requires_audio=requires_audio)
            if chosen is not None:
                route_reason = "multimodal_capability_match"

        if chosen is None and frontier_requested:
            chosen = self._pick_frontier_provider(candidates)
            if chosen is not None:
                route_reason = "frontier_model_requested"
            else:
                raise ValueError(
                    f"Session requests frontier model '{requested_model}' but no enabled frontier provider is available for auto routing."
                )

        if chosen is None and needs_frontier_tools:
            chosen = self._pick_frontier_provider(candidates)
            if chosen is not None:
                route_reason = "tool_capability_match"

        if chosen is None:
            chosen = self._pick_local_default(candidates)
            route_reason = "local_default"

        capabilities = load_provider_capabilities(chosen.get("capabilities"), str(chosen.get("kind") or chosen.get("id") or ""))
        chosen_model = str(chosen.get("model_name") or "").strip()
        if not requested_model:
            resolved_model = chosen_model
        elif capabilities.get("frontier"):
            resolved_model = requested_model if frontier_requested else chosen_model
        else:
            resolved_model = requested_model
        return SubstrateRouteDecision(
            requested_provider="auto",
            resolved_provider=str(chosen["id"]),
            requested_model=requested_model,
            resolved_model=resolved_model,
            route_reason=route_reason,
            used_auto_routing=True,
            provider_capabilities=capabilities,
            task_profile=task_profile,
        )

    def _get_provider_row(self, provider_id: str):
        return self.db.execute(
            """
            SELECT id, kind, model_name, enabled, capabilities
            FROM providers
            WHERE id = ? OR kind = ?
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (provider_id, provider_id, provider_id),
        ).fetchone()

    def _list_enabled_providers(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT id, kind, model_name, enabled, capabilities
            FROM providers
            WHERE enabled = 1
            ORDER BY CASE id
                WHEN 'llamacpp' THEN 0
                WHEN 'ollama' THEN 1
                WHEN 'openai' THEN 2
                ELSE 99
            END, id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _build_task_profile(*, message_text: str, attachments: list[dict[str, Any]] | None) -> dict[str, Any]:
        normalized_text = str(message_text or "").strip()
        attachment_types = sorted(
            {
                str((attachment or {}).get("type") or "text").strip().lower()
                for attachment in (attachments or [])
                if isinstance(attachment, dict)
            }
        )
        return {
            "attachment_types": attachment_types,
            "requires_vision": "image" in attachment_types,
            "requires_audio": "audio" in attachment_types,
            "needs_frontier_tools": bool(_TOOL_CAPABLE_TASK_RE.search(normalized_text)),
            "message_length": len(normalized_text),
        }

    @staticmethod
    def _pick_capable_provider(
        candidates: list[dict[str, Any]],
        *,
        requires_vision: bool,
        requires_audio: bool,
    ) -> dict[str, Any] | None:
        for candidate in candidates:
            capabilities = load_provider_capabilities(candidate.get("capabilities"), str(candidate.get("kind") or candidate.get("id") or ""))
            if requires_vision and not capabilities.get("supports_vision"):
                continue
            if requires_audio and not capabilities.get("supports_audio_input"):
                continue
            return candidate
        return None

    @staticmethod
    def _pick_frontier_provider(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        for candidate in candidates:
            capabilities = load_provider_capabilities(candidate.get("capabilities"), str(candidate.get("kind") or candidate.get("id") or ""))
            if capabilities.get("frontier"):
                return candidate
        return None

    @staticmethod
    def _pick_local_default(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        for preferred_id in ("llamacpp", "ollama"):
            for candidate in candidates:
                if candidate.get("id") == preferred_id:
                    return candidate
        return candidates[0]