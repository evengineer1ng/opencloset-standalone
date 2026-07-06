from __future__ import annotations

import json
from typing import Any

from api.agent.substrate_router import SubstrateRouteDecision, SubstrateRouter, load_provider_capabilities
from api.provider.base import ProviderConfig, ProviderResult, create_provider


class ExecutionSupportService:
    def __init__(self, app) -> None:
        self.app = app

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.app.db.execute(
            "SELECT id, label, model, provider, context_window, token_count, tool_policy, workspace_id, build_project_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def load_run_input_envelope(self, run_id: str) -> dict[str, Any]:
        manager = getattr(self.app, "run_inputs", None)
        if manager is None:
            return {"run_id": run_id, "attachments": [], "metadata": {}}
        loaded = manager.load(run_id)
        return loaded if isinstance(loaded, dict) else {"run_id": run_id, "attachments": [], "metadata": {}}

    def get_latest_user_message(self, session_id: str, run_id: str) -> str:
        row = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? AND run_id = ? AND role IN ('user', 'system') ORDER BY position DESC LIMIT 1",
            (session_id, run_id),
        ).fetchone()
        return str(row["content"] or "") if row else ""

    def resolve_substrate_route(
        self,
        session: dict[str, Any],
        run_id: str,
        *,
        input_envelope: dict[str, Any] | None = None,
        message_text: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        requested_provider: str | None = None,
        requested_model: str | None = None,
    ) -> SubstrateRouteDecision:
        envelope = input_envelope or self.load_run_input_envelope(run_id)
        effective_message_text = message_text if message_text is not None else self.get_latest_user_message(str(session["id"]), run_id)
        effective_attachments = attachments if attachments is not None else envelope.get("attachments")
        router = SubstrateRouter(self.app.db)
        return router.route(
            str(requested_provider or session.get("provider") or "auto"),
            str(requested_model or session.get("model") or ""),
            message_text=effective_message_text,
            attachments=effective_attachments if isinstance(effective_attachments, list) else None,
        )

    def resolve_provider_config(self, backend: str, model_name: str) -> ProviderConfig:
        default_timeout = float(self.app.config.get("PROVIDER_TIMEOUT_SECONDS", 120.0))
        defaults = {
            "llamacpp": {
                "server_url": self.app.config.get("LLAMACPP_SERVER_URL", "http://127.0.0.1:8080"),
                "model_name": model_name,
                "timeout": default_timeout,
                "api_key": "",
                "options": {
                    "cache_prompt": bool(self.app.config.get("LLAMACPP_CACHE_PROMPT", True)),
                    "n_cache_reuse": int(self.app.config.get("LLAMACPP_N_CACHE_REUSE", 256)),
                    "reasoning_format": str(self.app.config.get("LLAMACPP_REASONING_FORMAT", "none")),
                    "reasoning_budget": int(self.app.config.get("LLAMACPP_REASONING_BUDGET", 0)),
                    "chat_template_kwargs": {
                        "enable_thinking": bool(self.app.config.get("LLAMACPP_ENABLE_THINKING", False)),
                    },
                },
            },
            "ollama": {
                "server_url": self.app.config.get("OLLAMA_SERVER_URL", "http://127.0.0.1:11434"),
                "model_name": model_name,
                "timeout": default_timeout,
                "api_key": "",
                "options": {},
            },
            "openai": {
                "server_url": self.app.config.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "model_name": model_name or self.app.config.get("OPENAI_MODEL", "gpt-4.1-mini"),
                "timeout": default_timeout,
                "api_key": self.app.config.get("OPENAI_API_KEY", ""),
                "options": {},
            },
        }
        if backend not in defaults:
            raise ValueError(f"Unsupported provider backend: {backend}")

        resolved = dict(defaults[backend])
        try:
            row = self.app.db.execute(
                """
                SELECT id, kind, base_url, model_name, timeout_sec, enabled, api_key, capabilities
                FROM providers
                WHERE id = ? OR kind = ?
                ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (backend, backend, backend),
            ).fetchone()
        except Exception:
            row = None

        if row:
            if "enabled" in row.keys() and not bool(row["enabled"]):
                raise ValueError(f"Provider backend is disabled: {backend}")
            if row["base_url"]:
                resolved["server_url"] = row["base_url"]
            if row["model_name"]:
                resolved["model_name"] = model_name or row["model_name"]
            if row["timeout_sec"]:
                resolved["timeout"] = float(row["timeout_sec"])
            if row["api_key"]:
                resolved["api_key"] = row["api_key"]

        resolved_capabilities = load_provider_capabilities(
            row["capabilities"] if row and "capabilities" in row.keys() else None,
            backend,
        )
        resolved.setdefault("options", {})
        resolved["options"] = dict(resolved["options"])
        resolved["options"]["capabilities"] = resolved_capabilities

        if backend == "openai" and not resolved["api_key"]:
            raise ValueError(
                "OpenAI provider is selected but no API key is configured. Set OPENAI_API_KEY or store api_key on the openai provider record."
            )

        return ProviderConfig(
            server_url=str(resolved["server_url"]),
            model_name=str(resolved["model_name"]),
            timeout=float(resolved["timeout"]),
            api_key=str(resolved["api_key"]),
            options=dict(resolved.get("options") or {}),
        )

    def run_read_only_messages(
        self,
        session: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        run_id: str,
        requested_provider: str | None = None,
        requested_model: str | None = None,
        message_text: str = "",
        attachments: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 768,
    ) -> tuple[ProviderResult, dict[str, Any]]:
        route = self.resolve_substrate_route(
            session,
            run_id,
            message_text=message_text,
            attachments=attachments,
            requested_provider=requested_provider,
            requested_model=requested_model,
        )
        provider = create_provider(
            str(route.resolved_provider),
            self.resolve_provider_config(str(route.resolved_provider), str(route.resolved_model)),
        )
        result = provider.run(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=[],
        )
        return result, route.to_payload()

    def build_claw_runtime_prompt(
        self,
        session: dict[str, Any],
        run_id: str,
        *,
        input_envelope: dict[str, Any] | None = None,
    ) -> str:
        session_id = str(session["id"])
        envelope = input_envelope or self.load_run_input_envelope(run_id)
        latest_user_message = self.get_latest_user_message(session_id, run_id).strip()
        attachments = envelope.get("attachments") if isinstance(envelope, dict) else []
        metadata = envelope.get("metadata") if isinstance(envelope, dict) else {}

        sections: list[str] = []

        workspace_id = str(session.get("workspace_id") or "").strip()
        build_project_id = str(session.get("build_project_id") or "").strip()
        context_lines: list[str] = []
        if workspace_id:
            context_lines.append(f"workspace_id: {workspace_id}")
        if build_project_id:
            context_lines.append(f"build_project_id: {build_project_id}")

        planning = getattr(self.app, "planning", None)
        if planning is not None:
            plan = planning.get_plan(session_id) or {}
            plan_title = str(plan.get("title") or "").strip()
            active_goal = str(plan.get("active_goal") or "").strip()
            next_item = str(plan.get("next_item") or "").strip()
            handoff = str(plan.get("handoff") or "").strip()
            if plan_title:
                context_lines.append(f"plan_title: {plan_title}")
            if active_goal:
                context_lines.append(f"active_goal: {active_goal}")
            if next_item:
                context_lines.append(f"next_item: {next_item}")
            if handoff:
                context_lines.append(f"handoff: {handoff}")

        if context_lines:
            sections.append("OpenCloset orchestration context:\n" + "\n".join(f"- {line}" for line in context_lines))

        if isinstance(attachments, list) and attachments:
            attachment_lines = []
            for attachment in attachments[:8]:
                if not isinstance(attachment, dict):
                    continue
                attachment_type = str(attachment.get("type") or "file").strip() or "file"
                attachment_name = str(attachment.get("name") or attachment.get("path") or attachment.get("title") or "attachment").strip()
                attachment_lines.append(f"- {attachment_type}: {attachment_name}")
            if attachment_lines:
                sections.append(
                    "OpenCloset notes that attachments are present on this run. "
                    "The first Claw CLI bridge does not forward attachment binaries directly, so treat these as referenced context unless separately available:\n"
                    + "\n".join(attachment_lines)
                )

        if isinstance(metadata, dict) and metadata:
            capture_ids = metadata.get("capture_ids")
            if isinstance(capture_ids, list) and capture_ids:
                sections.append("OpenCloset capture references: " + ", ".join(str(item) for item in capture_ids[:10]))

        sections.append("User message:\n" + (latest_user_message or "Continue the current task."))
        return "\n\n".join(section for section in sections if section.strip())