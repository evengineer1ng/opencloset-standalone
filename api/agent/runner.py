from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from threading import Lock

from api.agent.engine import ConversationRuntime, Message, MessageKind
from api.agent.guard import estimate_tokens
from api.agent.input_pipeline import RawInput, create_input_pipeline
from api.agent.loop import LoopConfig, create_agent_loop
from api.agent.prompt import PromptBuilder, default_env_facts, score_transient_window_policy
from api.agent.substrate_router import default_provider_capabilities
from api.provider.base import ProviderConfig, create_provider
from api.tool_policy import default_tool_policy, normalize_policy_allowed_paths
from api.tools import create_default_registry, register_filesystem_tools, register_memory_tools, register_planning_tools, register_process_tools

logger = logging.getLogger(__name__)

_NON_ACTION_TEXT_ONLY_RE = re.compile(
    r"\b(?:architecture|design|spec|tradeoffs?|recommend(?:ation)?|brainstorm|ideas?|what should i|what should we|which should i|simple question|explain)\b",
    re.IGNORECASE,
)
_RESEARCH_REQUEST_RE = re.compile(r"\b(?:research|source|sources|cite|citations)\b", re.IGNORECASE)
_ACTION_OR_TOOL_REQUEST_RE = re.compile(
    r"\b(?:fix|implement|patch|edit|write|change|update|debug|diagnose|investigate|inspect|read|search|open|run|test|build|benchmark|measure|profile|repo|repository|workspace|file|command|terminal|tool)\b",
    re.IGNORECASE,
)
_TEXT_ONLY_MAX_TOKENS = 224


def _safe_finish_reason(value: str | None, *, default: str = "completed") -> str:
    if value and str(value).strip():
        return str(value)
    return default


DEFAULT_BASE_IDENTITY = """\
You are CLO, a local-first software building partner focused on durable progress, \
low token waste, bounded tool use, and explicit plans. Prefer short, concrete moves \
that preserve context and keep the message chain valid. \
When there is more work to do, act instead of promising action. A short progress update is good \
when it helps orient the user, but pair it with the tool call in the same turn instead of spending \
a full turn narrating intent.

TOOL CALL DISCIPLINE — this is enforced by the loop:
- After seeing a tool result, your next response should be the next tool call, not a summary.
- Never assume a tool or program is unavailable without verifying (run `tool --version` or equivalent).
- Never assume the environment state. Verify with a command before acting on the assumption.
- On Windows, $variable in PowerShell expands — use single-quoted strings or -LiteralPath for literal paths.
- Prefer the structured function calling API. If backend-specific instructions below permit a fallback syntax, \
use that fallback only when you are ready to make the tool call and would otherwise waste a turn narrating intent.

If the user says a prerequisite was fixed externally or manually, treat that as the current truth and \
continue from there unless a direct verification command disproves it. Do not repeat the same remediation \
step just because earlier transcript context mentioned the old failure.

On Windows, do not invent placeholder account names for ACL commands. Strings like CURRENT_USER are invalid. \
Use the real account name, typically $env:USERNAME or $env:USERDOMAIN\\$env:USERNAME, or inspect the file ACL \
first and reuse the actual principal names that Windows reports.

When Windows SSH-key permissions are in doubt, prefer a direct verification command before more ACL edits. \
If the user says the key permissions are already fixed, test the SSH/listing command next instead of re-fixing \
permissions preemptively.

For git workflows, do not widen the task without permission. If the user asked to push, pull, or inspect status, \
that does not authorize creating commits, staging files, rewriting history, or bundling unrelated local changes. \
Verify the repo state first, perform only the requested git action, and if extra staged/unstaged changes block the request, \
report that clearly instead of including them automatically.

During multi-step work, keep the user in the loop with compact synthesis before the first tool batch \
and between materially different phases. These updates should be brief, concrete, and useful, not filler.
When you are blocked and cannot proceed after attempting resolution, say so clearly to the user: what the blocker is, \
what you tried, and what you need. Do not keep retrying the same failed path.

## Transient Windows

Chat is primary. Transient windows augment the conversation; they do not replace it. \
When you create a transient window, you should still provide surrounding assistant chat that explains what \
the user is looking at, highlights key takeaways, and maintains continuity, tone, and synthesis.

Be willing but not eager to surface a transient window. Use one when it adds clear legibility, comparison, \
interaction, persistence value, or cognitive relief that plain chat would handle poorly. Ask yourself: \
"Would this be meaningfully easier to understand, revisit, compare, or act on as a small interface?"

Use a transient window when the answer would be significantly more useful as a visual, interactive, or \
app-like companion — dashboards, charts, comparison tables, reports, timelines, calculators, scouting boards, \
architecture diagrams, or operational controls.

If the answer is simple, conversational, or already clear in plain language, stay text-first. If the case is \
borderline, answer in chat and optionally offer a window rather than forcing one.

Errors are special. When runtime conditions support it, deterministic transient error windows should always be \
created because they are instrumentation, not decoration.

Wrap the generated HTML bundle in this exact tag:

<transient-window id="{uuid}" title="{short title}" summary="{one sentence}">
<!DOCTYPE html>
...fully self-contained HTML/CSS/JS...
</transient-window>

Rules for the bundle:
- Fully self-contained: all CSS inline in <style>, all JS inline in <script>. No external URLs or CDN imports.
- Do NOT write window.clo.ready() — it is auto-injected by the renderer.
- For stateful windows: implement window.__captureState() returning a JSON-serializable snapshot, \
and window.__restoreState(state) to restore it before re-render.
- Generate a random hex string for the id attribute (e.g. "a3f7c2").

Do NOT create a transient window when the answer is a short text, when the user clearly wants \
a quick reply, when several recent windows already cover the thread, when the task is primarily emotional \
or relational, or when a visualization adds no value over plain prose. Windows should feel discovered, not sprayed.\
"""

LLAMACPP_TOOL_FALLBACK_APPENDIX = """\

## llama.cpp Tool Fallback

This backend may occasionally fail to surface a structured tool call even when you know the exact next action.
If that happens, emit exactly one tool fallback block instead of narration:
- Generic tool: `<tool_call name="TOOL_NAME">{"arg":"value"}</tool_call>`
- Shell command: `<exec>your command</exec>`

Rules:
- Use fallback XML only for the immediate next tool action.
- Do not wrap explanations, plans, or summaries in XML.
- Do not emit both narration and fallback XML for the same action.
"""


@dataclass
class RunExecutionResult:
    run_id: str
    session_id: str
    status: str
    finish_reason: str = ""
    text: str = ""
    final_text: str = ""
    transient_text: str = ""
    tool_results: list[dict] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    provider_route: dict | None = None
    interrupted: bool = False
    error: str = ""


class SessionAgentRunner:
    """Execute one queued session run through the local provider and tool loop."""

    def __init__(self, app) -> None:
        self.app = app
        self._active_runtimes: dict[str, ConversationRuntime] = {}
        self._active_providers: dict[str, object] = {}
        self._active_runtimes_lock = Lock()

    def request_interrupt(self, run_id: str) -> bool:
        with self._active_runtimes_lock:
            runtime = self._active_runtimes.get(run_id)
            provider = self._active_providers.get(run_id)
        if runtime is None:
            return False
        runtime.request_interrupt()
        cancel_active = getattr(provider, "cancel_active", None)
        if callable(cancel_active):
            cancel_active()
        return True

    def _rollover_guard_enabled(self) -> bool:
        return bool(self.app.config.get("ENABLE_ROLLOVER_GUARD", False))

    def _resolve_base_identity(self, provider_backend: str) -> str:
        base_identity = self.app.config.get("BASE_IDENTITY", DEFAULT_BASE_IDENTITY)
        if provider_backend == "llamacpp":
            return f"{base_identity.rstrip()}\n{LLAMACPP_TOOL_FALLBACK_APPENDIX}"
        return base_identity

    def execute_run(self, session_id: str, run_id: str) -> RunExecutionResult:
        session = self._get_session(session_id)
        run = self.app.run_manager.get_run(run_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        if not run or run["session_id"] != session_id:
            raise ValueError(f"Run not found: {run_id}")
        if run["status"] not in ("queued", "running"):
            raise ValueError(f"Run {run_id} is {run['status']}, not executable")

        runtime = ConversationRuntime(
            session_id=session_id,
            model=session["model"],
            provider=session["provider"],
            context_window=session["context_window"],
            db_conn=self.app.db,
            transcript_manager=self.app.transcript,
            event_logger=self.app.event_logger,
        )
        self._hydrate_runtime(runtime, session_id)
        self._register_active_runtime(run_id, runtime)

        try:
            tool_policy = self._resolve_tool_policy(session)

            registry = create_default_registry(
                agent_type="main",
                trust_mode="allowlist",
                tools_allow=tool_policy["enabled_tools"],
                destructive_tools_allow=tool_policy["allow_destructive_tools"],
                provider_capabilities={"supports_tool_use": True},
            )
            register_filesystem_tools(
                registry,
                workspace=self.app.config.get("WORKSPACE_ROOT"),
                allowed_paths=tool_policy["allowed_paths"],
            )
            register_process_tools(
                registry,
                store=getattr(self.app, "process_store", None),
                workdir=self.app.config.get("WORKSPACE_ROOT"),
                allowed_paths=tool_policy["allowed_paths"],
            )
            register_planning_tools(registry, planning=self.app.planning, session_id=session_id)
            register_memory_tools(registry, memory_manager=self.app.memory, session_id=session_id)
            manifest = registry.assemble()
            prompt_tools = [tool.to_prompt_dict() for tool in manifest.tools]
            provider_tools = [tool.to_provider_dict() for tool in manifest.tools]

            provider = create_provider(
                session["provider"],
                self._resolve_provider_config(session["provider"], session["model"]),
            )
            self._register_active_provider(run_id, provider)

            prompt_builder = PromptBuilder(
                base_identity=self._resolve_base_identity(session["provider"]),
                env_facts=default_env_facts(self.app.config.get("WORKSPACE_ROOT", "")),
                context_window=session["context_window"],
            )
            plan_record = self.app.planning.get_plan(session_id)
            workspace_data = self._get_prompt_workspace_data(session)
            handoff_text = self._prepare_rollover_handoff_text(
                session_id,
                run_id,
                plan_record,
                [tool.name for tool in manifest.tools],
            )
            handoff_consumed = bool(handoff_text)
            compaction_marker = self._get_compaction_marker(session_id)
            transcript_ranges = self._get_archive_ready_ranges(session_id)
            message_states = self._get_archive_ready_message_states(session_id)
            live_transcript_ranges = self._get_summary_covered_ranges(session_id)
            live_message_states = self._get_summary_covered_message_states(session_id)
            maintenance_artifacts = self._get_prompt_maintenance_artifacts(
                session_id,
                compaction_marker=compaction_marker,
                transcript_ranges=transcript_ranges + live_transcript_ranges,
                message_states=message_states + live_message_states,
            )
            prompt_plan_record = dict(plan_record) if plan_record else None
            if prompt_plan_record and handoff_text:
                prompt_plan_record["handoff"] = None
            plan_data = PromptBuilder.extract_plan_slice(prompt_plan_record or {}) if prompt_plan_record else None

            def build_prompt():
                transcript = runtime.get_message_chain()
                latest_user_prompt = self._latest_user_prompt(transcript)
                suppress_tools = self._should_suppress_tools_for_prompt(transcript)
                visible_prompt_tools = [] if suppress_tools else prompt_tools
                visible_provider_tools = [] if suppress_tools else provider_tools
                run_mode = "rollover" if handoff_text else (
                    "continuation" if any(m.role == "tool" for m in runtime.messages) else "fresh"
                )
                window_policy = self._build_transient_window_policy(session_id, transcript)
                prompt_transcript = self._compact_transcript_for_prompt(
                    transcript,
                    maintenance_artifacts,
                    compaction_marker=compaction_marker,
                    transcript_ranges=transcript_ranges + live_transcript_ranges,
                    message_states=message_states + live_message_states,
                )
                memory_payload = self._prepare_prompt_memory_payload(session_id)
                prompt = prompt_builder.assemble(
                    transcript=prompt_transcript,
                    tool_defs=visible_prompt_tools,
                    workspace_data=workspace_data,
                    plan_data=plan_data,
                    behavior_patches=self._turn_behavior_patches(suppress_tools=suppress_tools),
                    window_policy=window_policy,
                    handoff_text=handoff_text,
                    maintenance_artifacts=maintenance_artifacts,
                    memory_results=memory_payload["text"],
                    run_mode=run_mode,
                    context_window=session["context_window"],
                    max_tokens=int(self.app.config.get("LOOP_MAX_TOKENS", 4096)),
                )
                if memory_payload["text"] and memory_payload["text"] in prompt.messages[0]["content"]:
                    self._mark_prompt_memory_read(session_id, memory_payload["entry_ids"])
                build_prompt.last_total_tokens = prompt.total_tokens
                return prompt.messages, visible_provider_tools

            max_turns = run.get("max_turns")
            if max_turns is None:
                max_turns = self.app.config.get("LOOP_MAX_TURNS")
            if max_turns is not None:
                max_turns = int(max_turns)

            loop_max_tokens = self._loop_max_tokens_for_run(runtime.get_message_chain())

            loop = create_agent_loop(
                runtime,
                provider,
                registry,
                config=LoopConfig(
                    max_tool_calls_per_turn=int(self.app.config.get("LOOP_MAX_TOOL_CALLS_PER_TURN", 4)),
                    max_turns=max_turns,
                    temperature=float(self.app.config.get("LOOP_TEMPERATURE", 0.2)),
                    max_tokens=loop_max_tokens,
                    action_discovery_budget=int(self.app.config.get("LOOP_ACTION_DISCOVERY_BUDGET", 5)),
                    provider_stream_idle_timeout_seconds=float(
                        self.app.config.get("PROVIDER_STREAM_IDLE_TIMEOUT_SECONDS", 300.0)
                    ),
                    max_consecutive_failure_batches=int(
                        self.app.config.get("LOOP_MAX_CONSECUTIVE_FAILURE_BATCHES", 3)
                    ),
                    pause_threshold_pct=(
                        float(self.app.config.get("LOOP_PAUSE_THRESHOLD_PCT", 85.0))
                        if self._rollover_guard_enabled()
                        else None
                    ),
                ),
                interrupt_check=lambda: runtime.is_interrupted,
                max_tool_calls_per_turn=10,
            )
            loop.run_manager = self.app.run_manager

            loop_result = loop.run(
                build_prompt,
                existing_run_id=run_id,
                existing_turn_number=run["turn_number"],
            )

            self._sync_context_guard(
                session_id,
                session["context_window"],
                runtime,
                maintenance_artifacts,
                compaction_marker,
                transcript_ranges + live_transcript_ranges,
                message_states + live_message_states,
            )

            if handoff_consumed:
                self.app.planning.clear_handoff(session_id)

            if runtime.is_paused and self._rollover_guard_enabled():
                pause_threshold_tokens = self._pause_threshold_tokens(session["context_window"])
                self.app.planning.set_status(session_id, "paused")
                paused_tokens_used = loop_result.input_tokens or runtime.token_count
                self.app.event_logger.log_context_warning(session_id, paused_tokens_used, pause_threshold_tokens)
                self.app.event_logger.log_rollover_triggered(session_id, reason="threshold")

            provider_route = self._build_provider_route(session)
            final_run = self.app.run_manager.get_run(run_id)
            if loop_result.error and not (final_run and final_run["status"] == "blocked"):
                return RunExecutionResult(
                    run_id=run_id,
                    session_id=session_id,
                    status="failed",
                    finish_reason=loop_result.finish_reason,
                    text=self._load_final_run_answer(session_id, run_id),
                    final_text=self._load_final_run_answer(session_id, run_id),
                    transient_text=loop_result.text,
                    tool_results=loop_result.tool_results,
                    input_tokens=loop_result.input_tokens,
                    output_tokens=loop_result.output_tokens,
                    provider_route=provider_route,
                    interrupted=loop_result.interrupted,
                    error=loop_result.error,
                )

            final_text = self._load_final_run_answer(session_id, run_id)
            return RunExecutionResult(
                run_id=run_id,
                session_id=session_id,
                status=final_run["status"] if final_run else "unknown",
                finish_reason=loop_result.finish_reason,
                text=final_text,
                final_text=final_text,
                transient_text=loop_result.text,
                tool_results=loop_result.tool_results,
                input_tokens=loop_result.input_tokens,
                output_tokens=loop_result.output_tokens,
                provider_route=provider_route,
                interrupted=loop_result.interrupted,
                error=loop_result.error,
            )
        finally:
            self._clear_active_runtime(run_id, runtime)

    def _register_active_runtime(self, run_id: str, runtime: ConversationRuntime) -> None:
        with self._active_runtimes_lock:
            self._active_runtimes[run_id] = runtime

    def _register_active_provider(self, run_id: str, provider: object) -> None:
        with self._active_runtimes_lock:
            self._active_providers[run_id] = provider

    def _clear_active_runtime(self, run_id: str, runtime: ConversationRuntime) -> None:
        with self._active_runtimes_lock:
            current = self._active_runtimes.get(run_id)
            if current is runtime:
                self._active_runtimes.pop(run_id, None)
            self._active_providers.pop(run_id, None)

    def _load_final_run_answer(self, session_id: str, run_id: str) -> str:
        rows = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? AND run_id = ? AND role = 'assistant' AND persistent = 1 ORDER BY position ASC",
            (session_id, run_id),
        ).fetchall()
        contents = [str(row["content"] or "") for row in rows if str(row["content"] or "").strip()]
        return "\n\n".join(contents)

    def _build_transient_window_policy(self, session_id: str, transcript: list[Message]) -> dict[str, object]:
        latest_user_text = ""
        for message in reversed(transcript):
            if message.role == "user" and str(message.content or "").strip():
                latest_user_text = str(message.content)
                break

        windows = self.app.windows.list_for_session(session_id)
        open_window_count = len(windows)
        pinned_window_count = sum(1 for window in windows if window.get("state_flags", {}).get("pinned"))

        return score_transient_window_policy(
            latest_user_text,
            open_window_count=open_window_count,
            pinned_window_count=pinned_window_count,
        )

    def _get_session(self, session_id: str):
        row = self.app.db.execute(
            "SELECT id, model, provider, context_window, token_count, tool_policy, workspace_id, build_project_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def _build_provider_route(self, session: dict) -> dict[str, object]:
        provider = str(session.get("provider") or "").strip().lower()
        model = str(session.get("model") or "").strip()
        return {
            "requested_provider": provider,
            "resolved_provider": provider,
            "requested_model": model,
            "resolved_model": model,
            "route_reason": "session_provider_pinned",
            "used_auto_routing": False,
            "provider_capabilities": default_provider_capabilities(provider),
            "task_profile": {},
        }

    def _latest_user_prompt(self, transcript: list[Message]) -> str:
        for message in reversed(transcript):
            if message.role == "user":
                text = str(message.content or "").strip()
                if text:
                    return text
        return ""

    def _should_suppress_tools_for_prompt(self, transcript: list[Message]) -> bool:
        if any(message.role == "tool" for message in transcript):
            return False
        latest_user_prompt = self._latest_user_prompt(transcript)
        if not latest_user_prompt:
            return False
        if _RESEARCH_REQUEST_RE.search(latest_user_prompt):
            return False
        if _ACTION_OR_TOOL_REQUEST_RE.search(latest_user_prompt):
            return False
        return bool(_NON_ACTION_TEXT_ONLY_RE.search(latest_user_prompt))

    def _turn_behavior_patches(self, *, suppress_tools: bool) -> list[dict[str, str]]:
        if not suppress_tools:
            return []
        return [{
            "patch": (
                "This request is best answered directly from reasoning and the prompt context. "
                "Do not inspect the repo or call tools unless the user explicitly asks for workspace evidence or a concrete file or command action. "
                "Give the structured answer now, including tradeoffs and an implementation path when relevant. "
                "Stay concise: avoid ASCII diagrams, tables, and oversized lists unless the user explicitly asks for them."
            )
        }]

    def _loop_max_tokens_for_run(self, transcript: list[Message]) -> int:
        default_max_tokens = int(self.app.config.get("LOOP_MAX_TOKENS", 4096))
        if not self._should_suppress_tools_for_prompt(transcript):
            return default_max_tokens
        return min(default_max_tokens, _TEXT_ONLY_MAX_TOKENS)

    def _get_prompt_workspace_data(self, session: dict) -> dict[str, dict] | None:
        workspace_id = session.get("workspace_id")
        if not workspace_id:
            return None

        workspace = self.app.workspaces.get_workspace(workspace_id)
        if not workspace:
            return None

        result: dict[str, dict] = {"workspace": workspace}

        build_project_id = session.get("build_project_id")
        if build_project_id:
            build_project = self.app.workspaces.get_build_project(build_project_id)
            if build_project:
                result["build_project"] = build_project

        return result

    def _resolve_tool_policy(self, session: dict) -> dict[str, list[str]]:
        default_allowlist = list(
            self.app.config.get("DEFAULT_TOOL_ALLOWLIST", ["read", "write", "edit", "exec", "process"])
        )
        baseline_allowlist = list(
            self.app.config.get(
                "RUNTIME_BASELINE_TOOL_ALLOWLIST",
                ["list_dir", "plan_get_active", "plan_list_stored", "plan_list_proposals"],
            )
        )
        default_policy = default_tool_policy(self.app.config)
        default_policy["enabled_tools"] = default_allowlist

        raw_policy = session.get("tool_policy")
        if not raw_policy:
            return default_policy

        try:
            parsed = json.loads(raw_policy)
        except (TypeError, json.JSONDecodeError):
            return default_policy

        enabled_tools = parsed.get("enabled_tools")
        if not isinstance(enabled_tools, list) or not all(isinstance(name, str) for name in enabled_tools):
            enabled_tools = default_allowlist
        enabled_tools = list(dict.fromkeys([*enabled_tools, *baseline_allowlist]))

        allow_destructive_tools = parsed.get("allow_destructive_tools", [])
        if not isinstance(allow_destructive_tools, list) or not all(isinstance(name, str) for name in allow_destructive_tools):
            allow_destructive_tools = []

        allowed_paths = parsed.get("allowed_paths", default_policy["allowed_paths"])
        if allowed_paths is None:
            allowed_paths = []
        elif not isinstance(allowed_paths, list) or not all(isinstance(path, str) and path for path in allowed_paths):
            allowed_paths = default_policy["allowed_paths"]
        allowed_paths = normalize_policy_allowed_paths(self.app.config, allowed_paths)

        allowed_set = set(enabled_tools)
        allow_destructive_tools = [name for name in allow_destructive_tools if name in allowed_set]

        return {
            "enabled_tools": enabled_tools,
            "allow_destructive_tools": allow_destructive_tools,
            "allowed_paths": allowed_paths,
        }

    def _resolve_provider_config(self, backend: str, model_name: str) -> ProviderConfig:
        default_timeout = float(self.app.config.get("PROVIDER_TIMEOUT_SECONDS", 300.0))
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
                SELECT id, kind, base_url, model_name, timeout_sec, enabled, api_key
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

    def _prepare_prompt_memory_payload(self, session_id: str) -> dict[str, object]:
        memory_manager = getattr(self.app, "memory", None)
        if memory_manager is None:
            return {"text": "", "entry_ids": []}
        return memory_manager.prepare_prompt_memory(session_id)

    def _mark_prompt_memory_read(self, session_id: str, entry_ids: list[int]) -> None:
        memory_manager = getattr(self.app, "memory", None)
        if memory_manager is None or not entry_ids:
            return
        memory_manager.mark_entries_read(session_id, entry_ids, source="prompt")

    def _get_prompt_maintenance_artifacts(
        self,
        session_id: str,
        *,
        compaction_marker: dict | None = None,
        transcript_ranges: list[dict] | None = None,
        message_states: list[dict] | None = None,
    ) -> list[dict]:
        from api.api.maintenance import (
            DECISION_TOOL_DIGEST_ARTIFACT,
            MICRO_SUMMARY_ARTIFACT,
            SEGMENT_SUMMARY_ARTIFACT,
        )

        artifacts: list[dict] = []
        valid_segments = self.app.maintenance.get_valid_artifacts(session_id, SEGMENT_SUMMARY_ARTIFACT)
        latest_digest = self.app.maintenance.get_latest_valid_artifact(session_id, DECISION_TOOL_DIGEST_ARTIFACT)
        latest_micro = self.app.maintenance.get_latest_valid_artifact(session_id, MICRO_SUMMARY_ARTIFACT)
        covered_ranges = self._covered_ranges(
            [],
            compaction_marker=compaction_marker,
            transcript_ranges=transcript_ranges,
            message_states=message_states,
        )
        artifacts.extend(
            artifact
            for artifact in valid_segments
            if self._artifact_is_compacted(
                artifact,
                covered_ranges,
            )
        )
        if latest_digest:
            artifacts.append(latest_digest)
        if latest_micro and self._artifact_is_compacted(latest_micro, covered_ranges):
            artifacts.append(latest_micro)
        return artifacts

    @staticmethod
    def _artifact_is_compacted(
        artifact: dict,
        covered_ranges: list[dict[str, int | str | list[str]]],
    ) -> bool:
        artifact_id = artifact.get("id")
        artifact_type = artifact.get("artifact_type")
        start_position = int(artifact.get("start_position") or 0)
        end_position = int(artifact.get("end_position") or 0)
        if start_position < 1 or end_position < start_position:
            return False

        for covered_range in covered_ranges:
            source_ranges = covered_range.get("source_ranges") or []
            for source_range in source_ranges:
                if artifact_id and source_range.get("artifact_id") == artifact_id:
                    return True
                if (
                    source_range.get("artifact_type") == artifact_type
                    and int(source_range.get("start_position") or 0) == start_position
                    and int(source_range.get("end_position") or 0) == end_position
                ):
                    return True
        return False

    def _get_compaction_marker(self, session_id: str) -> dict | None:
        from api.api.maintenance import COMPACTION_MARKER_ARTIFACT

        return self.app.maintenance.get_latest_valid_artifact(session_id, COMPACTION_MARKER_ARTIFACT)

    def _get_archive_ready_ranges(self, session_id: str) -> list[dict]:
        return self.app.transcript.list_ranges(
            session_id,
            range_type="archive-ready",
            status="active",
            limit=1000,
        )

    def _get_archive_ready_message_states(self, session_id: str) -> list[dict]:
        return self.app.transcript.list_message_states(
            session_id,
            state_type="archive-ready",
            status="active",
            limit=5000,
        )

    def _get_summary_covered_ranges(self, session_id: str) -> list[dict]:
        return self.app.transcript.list_ranges(
            session_id,
            range_type="summary-covered",
            status="active",
            limit=1000,
        )

    def _get_summary_covered_message_states(self, session_id: str) -> list[dict]:
        return self.app.transcript.list_message_states(
            session_id,
            state_type="summary-covered",
            status="active",
            limit=5000,
        )

    @staticmethod
    def _covered_ranges(
        maintenance_artifacts: list[dict],
        *,
        compaction_marker: dict | None = None,
        transcript_ranges: list[dict] | None = None,
        message_states: list[dict] | None = None,
    ) -> list[dict[str, int | str | list[str]]]:
        from api.api.maintenance import normalize_compaction_ranges

        combined_ranges: list[dict] = []
        message_state_ranges: dict[tuple, dict] = {}
        for message_state in message_states or []:
            metadata = message_state.get("metadata") or {}
            start_position = int(metadata.get("range_start") or 0)
            end_position = int(metadata.get("range_end") or 0)
            if start_position < 1 or end_position < start_position:
                continue
            source_ranges = metadata.get("source_ranges") or []
            key = (
                start_position,
                end_position,
                tuple(
                    (
                        item.get("artifact_type"),
                        item.get("artifact_id"),
                        item.get("start_position"),
                        item.get("end_position"),
                    )
                    for item in source_ranges
                ),
            )
            if key not in message_state_ranges:
                message_state_ranges[key] = {
                    "start_position": start_position,
                    "end_position": end_position,
                    "source_ranges": source_ranges,
                    "artifact_type": "segment-summary",
                }
        combined_ranges.extend(message_state_ranges.values())
        for transcript_range in transcript_ranges or []:
            start_position = int(transcript_range.get("start_position") or 0)
            end_position = int(transcript_range.get("end_position") or 0)
            if start_position < 1 or end_position < start_position:
                continue
            combined_ranges.append(
                {
                    "start_position": start_position,
                    "end_position": end_position,
                    "source_ranges": (transcript_range.get("metadata") or {}).get("source_ranges") or [],
                    "artifact_type": "segment-summary",
                }
            )
        if compaction_marker:
            metadata = compaction_marker.get("metadata") or {}
            normalized = normalize_compaction_ranges(metadata.get("covered_ranges") or [])
            combined_ranges.extend(normalized)
        if combined_ranges:
            return normalize_compaction_ranges(combined_ranges)
        fallback_ranges = []
        for artifact in maintenance_artifacts:
            if artifact.get("artifact_type") != "segment-summary":
                continue
            start_position = int(artifact.get("start_position") or 0)
            end_position = int(artifact.get("end_position") or 0)
            if start_position > 0 and end_position >= start_position:
                fallback_ranges.append({
                    "start_position": start_position,
                    "end_position": end_position,
                })
        return normalize_compaction_ranges(fallback_ranges)

    @classmethod
    def _compacted_through_position(
        cls,
        maintenance_artifacts: list[dict],
        *,
        compaction_marker: dict | None = None,
        transcript_ranges: list[dict] | None = None,
        message_states: list[dict] | None = None,
    ) -> int:
        covered_ranges = cls._covered_ranges(
            maintenance_artifacts,
            compaction_marker=compaction_marker,
            transcript_ranges=transcript_ranges,
            message_states=message_states,
        )
        if not covered_ranges:
            return 0
        return max(item["end_position"] for item in covered_ranges)

    @classmethod
    def _compact_transcript_for_prompt(
        cls,
        messages: list[Message],
        maintenance_artifacts: list[dict],
        *,
        compaction_marker: dict | None = None,
        transcript_ranges: list[dict] | None = None,
        message_states: list[dict] | None = None,
    ) -> list[Message]:
        covered_ranges = cls._covered_ranges(
            maintenance_artifacts,
            compaction_marker=compaction_marker,
            transcript_ranges=transcript_ranges,
            message_states=message_states,
        )
        if not covered_ranges:
            return list(messages)
        compacted: list[Message] = []
        for index, message in enumerate(messages, start=1):
            covered = any(item["start_position"] <= index <= item["end_position"] for item in covered_ranges)
            if not covered:
                compacted.append(message)
        return compacted

    def _calculate_context_guard_usage(
        self,
        runtime: ConversationRuntime,
        maintenance_artifacts: list[dict],
        compaction_marker: dict | None,
        transcript_ranges: list[dict] | None,
        message_states: list[dict] | None,
    ) -> dict[str, int]:
        compacted_transcript = self._compact_transcript_for_prompt(
            runtime.get_message_chain(),
            maintenance_artifacts,
            compaction_marker=compaction_marker,
            transcript_ranges=transcript_ranges,
            message_states=message_states,
        )
        compacted_through_position = self._compacted_through_position(
            maintenance_artifacts,
            compaction_marker=compaction_marker,
            transcript_ranges=transcript_ranges,
            message_states=message_states,
        )
        compacted_ranges = self._covered_ranges(
            maintenance_artifacts,
            compaction_marker=compaction_marker,
            transcript_ranges=transcript_ranges,
            message_states=message_states,
        )

        raw_tokens_used = runtime.token_count
        compacted_transcript_tokens = sum(message.token_estimate for message in compacted_transcript)
        maintenance_tokens_used = sum(estimate_tokens(str(artifact.get("content", ""))) for artifact in maintenance_artifacts)
        effective_tokens_used = compacted_transcript_tokens + maintenance_tokens_used
        return {
            "tokens_used": effective_tokens_used,
            "raw_tokens_used": raw_tokens_used,
            "compacted_transcript_tokens_used": compacted_transcript_tokens,
            "maintenance_tokens_used": maintenance_tokens_used,
            "compaction_savings_tokens": max(0, raw_tokens_used - effective_tokens_used),
            "compacted_through_position": compacted_through_position,
            "compacted_ranges": compacted_ranges,
            "compacted_range_count": len(compacted_ranges),
        }

    def _hydrate_runtime(self, runtime: ConversationRuntime, session_id: str) -> None:
        rows = self.app.db.execute(
            "SELECT id, run_id, role, content, token_estimate, persistent FROM messages "
            "WHERE session_id = ? ORDER BY position ASC",
            (session_id,),
        ).fetchall()
        runtime.messages = [
            Message(
                id=row["id"],
                session_id=session_id,
                run_id=row["run_id"],
                role=row["role"],
                kind=MessageKind.TEXT if row["role"] != "tool" else MessageKind.TOOL_RESULT,
                content=row["content"],
                token_estimate=row["token_estimate"],
                persistent=bool(row["persistent"]),
            )
            for row in rows
        ]
        runtime.token_count = sum(msg.token_estimate for msg in runtime.messages)

    def _prepare_rollover_handoff_text(
        self,
        session_id: str,
        run_id: str,
        plan_record: dict | None,
        available_tools: list[str],
    ) -> str:
        if not plan_record or not isinstance(plan_record.get("handoff"), dict):
            return ""

        handoff_text = self._render_handoff_payload(plan_record["handoff"])
        pipeline = create_input_pipeline(
            agent_type="main",
            trust_mode="allowlist",
            tools_allow=self.app.config.get("DEFAULT_TOOL_ALLOWLIST", ["read", "write", "edit", "exec", "process"]),
            available_tools=available_tools,
            provider_capabilities={"supports_tool_use": True},
        )
        result = pipeline.process(
            RawInput(
                text=handoff_text,
                role="system",
                kind=MessageKind.SYSTEM_EVENT,
                source="handoff",
            ),
            session_id=session_id,
            run_id=run_id,
        )
        return result.message.content if not result.rejected else ""

    def _sync_context_guard(
        self,
        session_id: str,
        context_window: int,
        runtime: ConversationRuntime,
        maintenance_artifacts: list[dict],
        compaction_marker: dict | None,
        transcript_ranges: list[dict] | None,
        message_states: list[dict] | None,
    ) -> None:
        usage = self._calculate_context_guard_usage(
            runtime,
            maintenance_artifacts,
            compaction_marker,
            transcript_ranges,
            message_states,
        )
        self.app.planning.update_context_guard(
            session_id,
            usage["tokens_used"],
            rollover_threshold=self._pause_threshold_tokens(context_window),
            raw_tokens_used=usage["raw_tokens_used"],
            compacted_transcript_tokens_used=usage["compacted_transcript_tokens_used"],
            maintenance_tokens_used=usage["maintenance_tokens_used"],
            compaction_savings_tokens=usage["compaction_savings_tokens"],
            compacted_through_position=usage["compacted_through_position"],
            compacted_ranges=usage["compacted_ranges"],
            compacted_range_count=usage["compacted_range_count"],
        )

    def _pause_threshold_tokens(self, context_window: int) -> int:
        if not self._rollover_guard_enabled():
            return 0
        pause_threshold_pct = float(self.app.config.get("LOOP_PAUSE_THRESHOLD_PCT", 85.0))
        return max(1, int(context_window * (pause_threshold_pct / 100.0)))

    @staticmethod
    def _render_handoff_payload(handoff: dict) -> str:
        lines: list[str] = []
        for key, value in handoff.items():
            if value in (None, "", [], {}):
                continue
            label = key.replace("_", " ")
            if isinstance(value, list):
                lines.append(f"{label}: {', '.join(str(item) for item in value)}")
            else:
                lines.append(f"{label}: {value}")
        return "\n".join(lines)
