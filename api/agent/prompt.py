# Prompt Builder — compositional prompt assembly
#
# Assembles the full prompt for the model call by composing sections:
#   1. Base identity (system prompt / persona)
#   2. Session handoff (from rollover, if resuming)
#   3. Retrieved memory (semantic search results)
#   4. Tool manifest (definitions for visible tools)
#   5. Environment facts (OS, shell, workspace, etc.)
#   6. Run-mode modifiers (fresh start, continuation, etc.)
#
# Design principles (from Claude Code research):
# - Compositional prompt builder: base + handoff + memory + tool manifest
#   + environment + run-mode modifiers
# - Provider-agnostic assembly; provider handles final formatting
# - Token-aware: sections can be truncated or dropped to fit budget
# - Bootstrap target: ~4k tokens (system ~2k + handoff ~1k + memory ~1k)

from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

from api.agent.engine import Message, MessageKind
from api.agent.guard import estimate_tokens

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_PROMPT_CHARS = 2000
TOOL_RESULT_PROMPT_HEAD_CHARS = 1400
TOOL_RESULT_PROMPT_TAIL_CHARS = 400


# ---------------------------------------------------------------------------
# Section priorities (lower = higher priority, kept longer under pressure)
# ---------------------------------------------------------------------------

PRIORITY = {
    "base_identity": 1,       # always keep — the agent's soul
    "tool_manifest": 2,       # model needs to know available tools
    "environment": 3,         # context for tool usage
    "workspace": 4,           # durable domain context for current session
    "behavior_patches": 5,    # approved behavioral constraints for this turn
    "run_mode": 5,            # small, informs behavior
    "window_policy": 5,       # runtime recommendation for chat vs transient window
    "continuity": 6,          # recent thread state, should survive longer than raw transcript
    "plan": 7,                # active plan slice — actionable context
    "handoff": 8,             # important but can be trimmed
    "maintenance": 9,         # derived session context, kept before memory
    "memory": 10,             # retrieved context, most expendable
    "transcript": 11,         # message history — truncated first
}


# ---------------------------------------------------------------------------
# Prompt section
# ---------------------------------------------------------------------------

@dataclass
class PromptSection:
    """A single compositional section of the prompt."""
    name: str
    text: str
    priority: int = 100       # lower = higher priority
    role: str = "system"      # system | user | assistant | tool
    token_estimate: int = 0


# ---------------------------------------------------------------------------
# Prompt result
# ---------------------------------------------------------------------------

@dataclass
class PromptResult:
    """Final assembled prompt ready for provider."""
    messages: list[dict]          # list of {role, content} dicts for provider
    sections: list[PromptSection]  # individual sections (for diagnostics)
    total_tokens: int             # estimated total token count
    truncated_sections: list[str]  # sections that were trimmed or dropped
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plan slice builder
# ---------------------------------------------------------------------------

def build_plan_section(plan_data: dict[str, Any]) -> str:
    """Format the active plan slice for injection into the system prompt.

    Injects only actionable fields (active_goal, want_to_know), not the
    full plan record. Keeps token footprint small.

    Expected keys in plan_data:
        - active_goal: str
        - want_to_know: list[str]
        - next_item: dict | None (optional)
        - handoff: dict | None (optional)

    Returns formatted markdown text.
    """
    lines = []

    active_goal = plan_data.get("active_goal", "")
    want_to_know = plan_data.get("want_to_know", [])
    next_item = plan_data.get("next_item", None)
    handoff = plan_data.get("handoff", None)

    lines.append("## Active Plan")
    lines.append("")

    if active_goal:
        lines.append(f"**Active Goal:** {active_goal}")
        lines.append("")

    if want_to_know:
        lines.append("**Need To Know:**")
        for item in want_to_know:
            lines.append(f"- {item}")
        lines.append("")

    if next_item and isinstance(next_item, dict):
        lines.append(f"**Next Action:** {next_item.get('content', '')}")
        if next_item.get("status"):
            lines.append(f"**Next Action Status:** {next_item['status']}")
        lines.append("")

    if handoff and isinstance(handoff, dict):
        lines.append("**Session Context (from handoff):**")
        for key, value in handoff.items():
            if value is not None:
                lines.append(f"- **{key}**: {value}")
        lines.append("")

    return "\n".join(lines)


def build_maintenance_section(artifacts: list[dict[str, Any]]) -> str:
    """Format valid maintenance artifacts for prompt injection."""
    if not artifacts:
        return ""

    lines = ["## Session Maintenance"]
    lines.append("")

    for artifact in artifacts:
        artifact_type = artifact.get("artifact_type", "artifact")
        start_position = artifact.get("start_position")
        end_position = artifact.get("end_position")
        coverage = []
        if start_position is not None:
            coverage.append(f"from message {start_position}")
        if end_position is not None:
            coverage.append(f"through message {end_position}")
        coverage_text = f" ({' '.join(coverage)})" if coverage else ""
        lines.append(f"**Derived {artifact_type}**{coverage_text}")
        lines.append(artifact.get("content", ""))
        lines.append("")

    return "\n".join(lines).rstrip()


def build_workspace_section(workspace_data: dict[str, Any]) -> str:
    """Format active workspace and build-project context for prompt injection."""
    if not workspace_data:
        return ""

    workspace = workspace_data.get("workspace")
    build_project = workspace_data.get("build_project")

    if not isinstance(workspace, dict) or not workspace:
        return ""

    lines = ["## Active Workspace", ""]
    lines.append(f"**Workspace Name:** {workspace.get('name', '')}")

    kind = workspace.get("kind")
    if kind:
        lines.append(f"**Workspace Kind:** {kind}")

    status = workspace.get("status")
    if status:
        lines.append(f"**Workspace Status:** {status}")

    description = workspace.get("description")
    if description:
        lines.append("")
        lines.append("**Workspace Description:**")
        lines.append(description)

    if isinstance(build_project, dict) and build_project:
        lines.append("")
        lines.append("## Active Build Project")
        lines.append("")
        lines.append(f"**Build Project Name:** {build_project.get('name', '')}")
        project_status = build_project.get("status")
        if project_status:
            lines.append(f"**Build Project Status:** {project_status}")
        project_description = build_project.get("description")
        if project_description:
            lines.append("")
            lines.append("**Build Project Description:**")
            lines.append(project_description)

    lines.append("")
    return "\n".join(lines)


def build_continuity_section(continuity_data: dict[str, Any]) -> str:
    """Format a compact, high-priority continuity slice from recent session state."""
    if not continuity_data:
        return ""

    lines = ["## Session Continuity", ""]

    rolling_synopsis = continuity_data.get("rolling_synopsis")
    if rolling_synopsis:
        lines.append("**Rolling Synopsis:**")
        lines.append(str(rolling_synopsis))
        lines.append("")

    latest_user = continuity_data.get("latest_user")
    if latest_user:
        lines.append("**Latest User Request:**")
        lines.append(str(latest_user))
        lines.append("")

    latest_assistant = continuity_data.get("latest_assistant")
    if latest_assistant:
        lines.append("**Latest Assistant State:**")
        lines.append(str(latest_assistant))
        lines.append("")

    latest_tool = continuity_data.get("latest_tool")
    if latest_tool:
        lines.append("**Latest Tool/Runtime Result:**")
        lines.append(str(latest_tool))
        lines.append("")

    open_threads = continuity_data.get("open_threads") or []
    if open_threads:
        lines.append("**Open Threads To Preserve:**")
        for item in open_threads:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines).rstrip()


def build_behavior_patches_section(behavior_patches: list[dict[str, Any]]) -> str:
    """Format approved behavior patches into a compact prompt section."""
    if not behavior_patches:
        return ""

    lines = ["## Approved Behavior Patches", "", "Apply these behavioral adjustments for this turn:"]
    for patch in behavior_patches:
        patch_text = str(patch.get("patch") or patch.get("patch_text") or "").strip()
        if not patch_text:
            continue
        title = str(patch.get("title") or "").strip()
        scope = str(patch.get("scope") or "").strip()
        prefix_bits = []
        if title:
            prefix_bits.append(title)
        if scope:
            prefix_bits.append(f"scope={scope}")
        prefix = f" ({', '.join(prefix_bits)})" if prefix_bits else ""
        lines.append(f"- {patch_text}{prefix}")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Tool manifest assembler
# ---------------------------------------------------------------------------

def build_tool_manifest(tools: list[dict], *, mode: str = "full") -> str:
    """Format tool name+description index for the system prompt.

    Full schemas are sent separately in the provider tools parameter — only
    the reference list belongs here to avoid duplicating token-heavy schemas.
    """
    if not tools:
        return ""

    lines = ["## Available Tools", ""]
    for tool in tools:
        name = tool.get("name", "unknown")
        desc = tool.get("description", "")
        if mode == "compact":
            lines.append(f"- **{name}**")
        else:
            lines.append(f"- **{name}**: {desc}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Environment facts
# ---------------------------------------------------------------------------

def build_environment_facts(env: dict[str, Any]) -> str:
    """Build environment facts block from runtime metadata."""
    lines = ["## Environment"]
    lines.append("")
    for key, value in env.items():
        lines.append(f"- **{key}**: `{value}`")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run-mode modifiers
# ---------------------------------------------------------------------------

RUN_MODE_TEXTS = {
    "fresh": "This is a fresh session with no prior context.",
    "continuation": "Continuing from a prior run. Tool results and intermediate state may be pending.",
    "rollover": "Resuming after context rollover. Handoff context is provided above.",
    "budgeted": "Operating under a task budget constraint. Be concise.",
}


def build_run_mode_section(run_mode: str, extra: str = "") -> str:
    """Build run-mode modifier text."""
    base = RUN_MODE_TEXTS.get(run_mode, f"Run mode: {run_mode}")
    if extra:
        base += f" {extra}"
    return base


WINDOW_DECISION_TEXT_ONLY = "text_only"
WINDOW_DECISION_TEXT_PLUS_OFFER = "text_plus_offer"
WINDOW_DECISION_TEXT_PLUS_WINDOW = "text_plus_window"

_WINDOW_TEXT_ONLY_PATTERNS = (
    r"\bjust text\b",
    r"\btext only\b",
    r"\bplain text\b",
    r"\bno window\b",
    r"\bwithout (a )?window\b",
    r"\bjust answer\b",
)
_WINDOW_CONVERSATIONAL_PATTERNS = (
    r"\bthank(s| you)?\b",
    r"\bhello\b",
    r"\bhi\b",
    r"\bhow are you\b",
)
_WINDOW_COMPARISON_TERMS = (
    "compare",
    "comparison",
    "versus",
    " vs ",
    "tradeoff",
    "rank",
    "ranking",
    "top ",
    "best ",
    "options",
)
_WINDOW_LEGIBILITY_TERMS = (
    "timeline",
    "diff",
    "dependency",
    "state",
    "status",
    "error",
    "debug",
    "trace",
    "plan",
    "report",
    "diagram",
    "matrix",
    "map",
)
_WINDOW_INTERACTION_TERMS = (
    "filter",
    "toggle",
    "sort",
    "drill",
    "checklist",
    "explore",
    "interactive",
)
_WINDOW_PERSISTENCE_TERMS = (
    "save",
    "artifact",
    "workspace",
    "project",
    "lab",
    "board",
)
_WINDOW_VISUAL_TERMS = (
    "dashboard",
    "table",
    "chart",
    "graph",
    "visual",
    "visualize",
    "window",
)


def score_transient_window_policy(
    latest_user_text: str,
    *,
    open_window_count: int = 0,
    pinned_window_count: int = 0,
) -> dict[str, Any]:
    text = (latest_user_text or "").strip()
    normalized = f" {text.lower()} "
    score = 0
    reasons: list[str] = []

    if any(re.search(pattern, normalized) for pattern in _WINDOW_TEXT_ONLY_PATTERNS):
        score -= 6
        reasons.append("user explicitly asked for text-only output (-6)")

    if any(term in normalized for term in _WINDOW_COMPARISON_TERMS):
        score += 3
        reasons.append("comparison cues strongly suggest a structured view (+3)")

    if any(term in normalized for term in _WINDOW_LEGIBILITY_TERMS):
        score += 2
        reasons.append("legibility cues suggest a visual companion (+2)")

    if any(term in normalized for term in _WINDOW_INTERACTION_TERMS):
        score += 2
        reasons.append("interaction cues suggest filters, toggles, or drilldowns (+2)")

    if any(term in normalized for term in _WINDOW_PERSISTENCE_TERMS):
        score += 1
        reasons.append("request looks like a likely persistence candidate (+1)")

    if any(term in normalized for term in _WINDOW_VISUAL_TERMS):
        score += 2
        reasons.append("user is explicitly asking for visual or window-like structure (+2)")

    if len(text) >= 180 or text.count("\n") >= 2:
        score += 1
        reasons.append("request is dense enough that structure may reduce cognitive load (+1)")

    if len(text) <= 80 and score == 0:
        score -= 2
        reasons.append("short request with no strong structure cues; default to chat (-2)")

    if any(re.search(pattern, normalized) for pattern in _WINDOW_CONVERSATIONAL_PATTERNS):
        score -= 2
        reasons.append("conversational phrasing favors plain chat over UI (+2 chat bias)")

    if open_window_count >= 3:
        score -= 1
        reasons.append(f"{open_window_count} windows are already open, so lean toward restraint (-1)")
    if open_window_count >= 5:
        score -= 2
        reasons.append("window saturation is high; prefer restraint unless structure is clearly worth it (-2)")

    if pinned_window_count >= 2:
        score -= 1
        reasons.append(f"{pinned_window_count} pinned window(s) already need attention (-1)")

    if score >= 4:
        recommendation = WINDOW_DECISION_TEXT_PLUS_WINDOW
    elif score >= 2:
        recommendation = WINDOW_DECISION_TEXT_PLUS_OFFER
    else:
        recommendation = WINDOW_DECISION_TEXT_ONLY

    return {
        "recommendation": recommendation,
        "score": score,
        "reasons": reasons,
        "open_window_count": open_window_count,
        "pinned_window_count": pinned_window_count,
        "latest_user_text": text,
    }


def build_window_policy_section(policy: dict[str, Any]) -> str:
    recommendation = str(policy.get("recommendation") or WINDOW_DECISION_TEXT_ONLY)
    score = int(policy.get("score") or 0)
    open_window_count = int(policy.get("open_window_count") or 0)
    reasons = policy.get("reasons") or []
    reason_suffix = f" ({reasons[0]})" if reasons else ""
    return f"Window heuristic: {recommendation} (score {score}, {open_window_count} open){reason_suffix}."


# ---------------------------------------------------------------------------
# Token-aware truncation
# ---------------------------------------------------------------------------

def truncate_to_budget(
    sections: list[PromptSection],
    *,
    budget_tokens: int,
    transcript_tokens: int,
    overhead_tokens: int = 50,
) -> tuple[list[PromptSection], list[str]]:
    """Trim sections to fit within token budget.

    Budget = context_window - transcript_tokens - overhead.
    Sections are trimmed from lowest priority upward until budget is met.

    Returns (pruned_sections, list_of_truncated_names).
    """
    available = max(0, budget_tokens - transcript_tokens - overhead_tokens)

    # Sort by priority (highest priority first = lowest number)
    sorted_sections = sorted(sections, key=lambda s: s.priority)

    total = sum(s.token_estimate for s in sorted_sections)

    if total <= available:
        return sorted_sections, []

    truncated = []
    # Greedy removal from lowest priority
    while total > available and sorted_sections:
        # Find lowest priority section
        worst = max(sorted_sections, key=lambda s: s.priority)
        total -= worst.token_estimate
        truncated.append(worst.name)
        sorted_sections.remove(worst)

    # If still over budget after removing sections, trim text of remaining
    if total > available and sorted_sections:
        # Truncate the lowest priority remaining section
        worst = max(sorted_sections, key=lambda s: s.priority)
        excess = total - available
        chars_to_cut = excess * 4  # rough: 4 chars per token
        original = worst.text
        worst.text = original[:-chars_to_cut] + "\n\n[... truncated due to token budget ...]"
        worst.token_estimate = estimate_tokens(worst.text)
        if worst.name not in truncated:
            truncated.append(worst.name)

    return sorted_sections, truncated


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

class PromptBuilder:
    """Compositional prompt builder for model calls.

    Assembles sections in priority order, respects token budget,
    and returns a list of {role, content} dicts ready for the provider.

    Usage:
        builder = PromptBuilder(
            base_identity=system_prompt_text,
            env_facts={"os": "windows", "shell": "powershell", ...},
        )
        result = builder.assemble(
            runtime=runtime,
            handoff_text=handoff,           # optional, from rollover
            memory_results=memory_hits,     # optional, from memory_search
            tool_defs=visible_tools,        # from input_pipeline tool allowlist
            run_mode="fresh",
        )
    """

    def __init__(
        self,
        *,
        base_identity: str = "",
        env_facts: dict[str, Any] | None = None,
        context_window: int = 65536,
    ):
        self.base_identity = base_identity
        self.env_facts = env_facts or {}
        self.context_window = context_window

    def assemble(
        self,
        *,
        transcript: list[Message],
        handoff_text: str = "",
        maintenance_artifacts: list[dict[str, Any]] | None = None,
        behavior_patches: list[dict[str, Any]] | None = None,
        memory_results: str = "",
        tool_defs: list[dict] | None = None,
        tool_manifest_mode: str = "full",
        workspace_data: dict[str, Any] | None = None,
        continuity_data: dict[str, Any] | None = None,
        plan_data: dict[str, Any] | None = None,
        window_policy: dict[str, Any] | None = None,
        run_mode: str = "fresh",
        run_mode_extra: str = "",
        context_window: int | None = None,
        max_tokens: int = 4096,
    ) -> PromptResult:
        """Assemble the full prompt. Returns PromptResult."""

        context_window = context_window or self.context_window
        # Reserve max_tokens for the response budget.
        safe_context = max(4096, context_window - max_tokens)
        sections: list[PromptSection] = []

        # -- Section 1: Base identity (always first, highest priority) --
        if self.base_identity:
            sections.append(PromptSection(
                name="base_identity",
                text=self.base_identity,
                priority=PRIORITY["base_identity"],
                role="system",
                token_estimate=estimate_tokens(self.base_identity),
            ))

        # -- Section 2: Tool manifest --
        if tool_defs:
            manifest_text = build_tool_manifest(tool_defs, mode=tool_manifest_mode)
            sections.append(PromptSection(
                name="tool_manifest",
                text=manifest_text,
                priority=PRIORITY["tool_manifest"],
                role="system",
                token_estimate=estimate_tokens(manifest_text),
            ))

        # -- Section 3: Environment facts --
        if self.env_facts:
            env_text = build_environment_facts(self.env_facts)
            sections.append(PromptSection(
                name="environment",
                text=env_text,
                priority=PRIORITY["environment"],
                role="system",
                token_estimate=estimate_tokens(env_text),
            ))

        # -- Section 4: Active workspace context --
        if workspace_data:
            workspace_text = build_workspace_section(workspace_data)
            if workspace_text.strip():
                sections.append(PromptSection(
                    name="workspace",
                    text=workspace_text,
                    priority=PRIORITY["workspace"],
                    role="system",
                    token_estimate=estimate_tokens(workspace_text),
                ))

        # -- Section 4a: Approved behavior patches --
        if behavior_patches:
            behavior_text = build_behavior_patches_section(behavior_patches)
            if behavior_text.strip():
                sections.append(PromptSection(
                    name="behavior_patches",
                    text=behavior_text,
                    priority=PRIORITY["behavior_patches"],
                    role="system",
                    token_estimate=estimate_tokens(behavior_text),
                ))

        # -- Section 4b: Session continuity slice --
        if continuity_data:
            continuity_text = build_continuity_section(continuity_data)
            if continuity_text.strip():
                sections.append(PromptSection(
                    name="continuity",
                    text=continuity_text,
                    priority=PRIORITY["continuity"],
                    role="system",
                    token_estimate=estimate_tokens(continuity_text),
                ))

        # -- Section 5: Run-mode modifiers --
        if run_mode:
            mode_text = build_run_mode_section(run_mode, run_mode_extra)
            sections.append(PromptSection(
                name="run_mode",
                text=mode_text,
                priority=PRIORITY["run_mode"],
                role="system",
                token_estimate=estimate_tokens(mode_text),
            ))

        # -- Section 5b: Runtime transient-window recommendation --
        if window_policy:
            window_policy_text = build_window_policy_section(window_policy)
            sections.append(PromptSection(
                name="window_policy",
                text=window_policy_text,
                priority=PRIORITY["window_policy"],
                role="system",
                token_estimate=estimate_tokens(window_policy_text),
            ))

        # -- Section 6: Active plan slice --
        if plan_data:
            plan_text = build_plan_section(plan_data)
            if plan_text.strip():
                sections.append(PromptSection(
                    name="plan",
                    text=plan_text,
                    priority=PRIORITY["plan"],
                    role="system",
                    token_estimate=estimate_tokens(plan_text),
                ))

        # -- Section 7: Handoff (from rollover) --
        if handoff_text:
            sections.append(PromptSection(
                name="handoff",
                text=handoff_text,
                priority=PRIORITY["handoff"],
                role="system",
                token_estimate=estimate_tokens(handoff_text),
            ))

        # -- Section 8: Maintenance artifacts --
        if maintenance_artifacts:
            maintenance_text = build_maintenance_section(maintenance_artifacts)
            if maintenance_text.strip():
                sections.append(PromptSection(
                    name="maintenance",
                    text=maintenance_text,
                    priority=PRIORITY["maintenance"],
                    role="system",
                    token_estimate=estimate_tokens(maintenance_text),
                ))

        # -- Section 9: Retrieved memory --
        if memory_results:
            sections.append(PromptSection(
                name="memory",
                text=memory_results,
                priority=PRIORITY["memory"],
                role="system",
                token_estimate=estimate_tokens(memory_results),
            ))

        # -- Combine system sections into one system message --
        system_parts = [s.text for s in sections if s.role == "system"]
        system_text = "\n".join(system_parts) if system_parts else ""

        transcript = self._prepare_transcript_for_prompt(transcript)

        # -- Calculate transcript token budget --
        transcript_tokens = sum(m.token_estimate for m in transcript)
        system_tokens = estimate_tokens(system_text)

        # -- Apply truncation if needed --
        truncated_names = []
        warnings = []

        if system_tokens + transcript_tokens > safe_context:
            # Try to trim system sections first
            pruned, truncated_names = truncate_to_budget(
                sections,
                budget_tokens=safe_context,
                transcript_tokens=transcript_tokens,
            )
            system_parts = [s.text for s in pruned if s.role == "system"]
            system_text = "\n".join(system_parts) if system_parts else ""
            if truncated_names:
                warnings.append(f"Prompt truncated: dropped sections {truncated_names}")

            # If still over budget, truncate transcript from the oldest end
            remaining_system_tokens = estimate_tokens(system_text)
            if remaining_system_tokens + transcript_tokens > safe_context:
                available_for_transcript = max(0, safe_context - remaining_system_tokens)
                while transcript_tokens > available_for_transcript and len(transcript) > 1:
                    dropped = transcript.pop(0)
                    transcript_tokens -= dropped.token_estimate
                    warnings.append(
                        f"Transcript truncated: dropped {dropped.role} message ({dropped.token_estimate} tokens)"
                    )
                # If a single message still exceeds the budget (e.g. a massive tool result
                # that survived summarization), hard-cap its content so we never send a
                # prompt that will be rejected by the provider.
                if len(transcript) == 1 and transcript_tokens > available_for_transcript:
                    msg = transcript[0]
                    cap_chars = max(400, available_for_transcript * 4)
                    capped = msg.content[:cap_chars] + f"\n[...prompt:emergency-cap: message exceeded context budget...]"
                    transcript[0] = replace(msg, content=capped, token_estimate=estimate_tokens(capped))
                    transcript_tokens = transcript[0].token_estimate
                    warnings.append(f"Transcript truncated: emergency-capped single {msg.role} message")

        # -- Build final message list --
        messages = []

        # System message (combined sections)
        if system_text:
            messages.append({"role": "system", "content": system_text})

        # Transcript messages
        for msg in transcript:
            messages.append({"role": msg.role, "content": msg.content})

        total_tokens = estimate_tokens(system_text) + transcript_tokens

        return PromptResult(
            messages=messages,
            sections=sections,
            total_tokens=total_tokens,
            truncated_sections=truncated_names,
            warnings=warnings,
        )

    def _prepare_transcript_for_prompt(self, transcript: list[Message]) -> list[Message]:
        prepared: list[Message] = []
        for message in transcript:
            if message.role != "tool":
                prepared.append(message)
                continue

            content = self._summarize_tool_message_content(message.content)
            prepared.append(replace(
                message,
                content=content,
                token_estimate=estimate_tokens(content),
            ))
        return prepared

    @staticmethod
    def _summarize_tool_message_content(raw_content: str) -> str:
        if not raw_content:
            return raw_content

        try:
            payload = json.loads(raw_content)
        except (TypeError, json.JSONDecodeError):
            return PromptBuilder._truncate_tool_output_text(raw_content)

        if not isinstance(payload, dict):
            return PromptBuilder._truncate_tool_output_text(raw_content)

        # Handle flat format: {"tool_id": ..., "content": "..."}
        content = payload.get("content")
        if isinstance(content, str):
            truncated = PromptBuilder._truncate_tool_output_text(content)
            if truncated != content:
                payload["content"] = truncated
                payload["content_truncated_for_prompt"] = True

        # Handle runtime_tool wrapper: {"tool_name": "runtime_tool", "output": {"content": "..."}}
        output = payload.get("output")
        if isinstance(output, dict):
            inner = output.get("content")
            if isinstance(inner, str):
                truncated = PromptBuilder._truncate_tool_output_text(inner)
                if truncated != inner:
                    output["content"] = truncated
                    output["content_truncated_for_prompt"] = True

        result = json.dumps(payload, ensure_ascii=False)
        # Final hard cap: if still oversized after all truncation, truncate the raw JSON
        return PromptBuilder._truncate_tool_output_text(result) if len(result) > MAX_TOOL_RESULT_PROMPT_CHARS * 3 else result

    @staticmethod
    def _truncate_tool_output_text(text: str) -> str:
        if len(text) <= MAX_TOOL_RESULT_PROMPT_CHARS:
            return text

        head = text[:TOOL_RESULT_PROMPT_HEAD_CHARS]
        tail = text[-TOOL_RESULT_PROMPT_TAIL_CHARS:]
        omitted = len(text) - (len(head) + len(tail))
        return (
            f"{head}\n\n"
            f"[... tool output truncated for prompt; omitted {omitted} chars ...]\n\n"
            f"{tail}"
        )

    def get_bootstrap_estimate(
        self,
        *,
        handoff_text: str = "",
        memory_results: str = "",
        workspace_data: dict[str, Any] | None = None,
        plan_data: dict[str, Any] | None = None,
        window_policy: dict[str, Any] | None = None,
    ) -> int:
        """Estimate token count for a fresh session bootstrap (no transcript).

        Useful for pre-flight checks before creating a new session.
        """
        system_tokens = estimate_tokens(self.base_identity)
        system_tokens += estimate_tokens(build_environment_facts(self.env_facts))
        if workspace_data:
            system_tokens += estimate_tokens(build_workspace_section(workspace_data))
        if plan_data:
            system_tokens += estimate_tokens(build_plan_section(plan_data))
        if window_policy:
            system_tokens += estimate_tokens(build_window_policy_section(window_policy))
        if handoff_text:
            system_tokens += estimate_tokens(handoff_text)
        if memory_results:
            system_tokens += estimate_tokens(memory_results)
        return system_tokens + 50  # overhead

    # Hard caps to prevent large plan documents from overflowing context
    _MAX_PLAN_GOAL_CHARS = 3000
    _MAX_PLAN_WTK_ITEMS = 15
    _MAX_PLAN_WTK_ITEM_CHARS = 300

    @classmethod
    def extract_plan_slice(cls, plan_record: dict[str, Any]) -> dict[str, Any]:
        """Extract the actionable plan slice from a full PlanningManager record.

        Returns only the fields needed for prompt injection:
        active_goal, want_to_know, and handoff (if present).

        Usage:
            plan = planning_manager.get_plan(session_id)
            plan_data = PromptBuilder.extract_plan_slice(plan)
            result = builder.assemble(transcript=..., plan_data=plan_data)
        """
        result: dict[str, Any] = {}
        goal = plan_record.get("active_goal")
        if goal:
            goal = str(goal)
            if len(goal) > cls._MAX_PLAN_GOAL_CHARS:
                goal = goal[:cls._MAX_PLAN_GOAL_CHARS] + "\n[... truncated — full plan available via read tool ...]"
            result["active_goal"] = goal
        wtk = plan_record.get("want_to_know")
        if wtk:
            items = list(wtk)[:cls._MAX_PLAN_WTK_ITEMS]
            items = [str(item)[:cls._MAX_PLAN_WTK_ITEM_CHARS] for item in items]
            result["want_to_know"] = items
        handoff = plan_record.get("handoff")
        if handoff and isinstance(handoff, dict):
            result["handoff"] = handoff
        next_item = plan_record.get("next_item")
        if next_item and isinstance(next_item, dict):
            result["next_item"] = next_item
        return result if result else None


# ---------------------------------------------------------------------------
# Convenience: build default environment facts
# ---------------------------------------------------------------------------

import os
import platform
import sys


def _detect_default_shell() -> str:
    shell = os.environ.get("SHELL", "").strip()
    if shell:
        return shell
    if sys.platform == "win32":
        return "powershell"
    return "unknown"


def default_env_facts(workspace: str = "") -> dict[str, Any]:
    """Build default environment facts from runtime."""
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "shell": _detect_default_shell(),
        "python": sys.version.split()[0],
        "workspace": workspace or os.getcwd(),
    }
