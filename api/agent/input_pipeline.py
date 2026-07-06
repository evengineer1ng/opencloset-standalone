# Input Pipeline — preprocess input before model call
#
# Takes raw incoming input (user messages, system events, handoff injections,
# bridge captures) and normalizes it into a Message ready for the agent loop.
#
# Design principles (from Claude Code research):
# - Input pipeline before model call: normalize, attach context, run policy hooks,
#   shape tool allowlist
# - Early persistence of accepted user turns (crash-safe)
# - Normalized event types, not plain text
#
# Stages:
#   1. Normalize — convert raw input to a Message
#   2. Expand — resolve attachments, captures, media references
#   3. Policy — run validation hooks, permission checks
#   4. Tool allowlist — shape visible tools for this turn
#   5. Return processed Message + metadata for prompt builder

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from api.agent.engine import Message, MessageKind
from api.agent.guard import estimate_tokens

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input envelope
# ---------------------------------------------------------------------------

@dataclass
class RawInput:
    """Raw incoming input before pipeline processing."""
    text: str = ""
    role: str = "user"          # user | system | tool
    kind: str = MessageKind.TEXT
    attachments: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "chat"        # chat | cron | bridge | handoff | system


@dataclass
class PipelineResult:
    """Output from input pipeline — ready for prompt builder + runtime."""
    message: Message
    tool_allowlist: list[str] = field(default_factory=list)
    attachments_expanded: list[Message] = field(default_factory=list)
    policy_warnings: list[str] = field(default_factory=list)
    rejected: bool = False
    rejection_reason: str = ""


# ---------------------------------------------------------------------------
# Policy hooks (pluggable)
# ---------------------------------------------------------------------------

class PolicyHooks:
    """Pluggable validation + permission hooks.

    Each hook receives the raw input and can:
    - Pass silently (input is fine)
    - Append a warning (input proceeds with note)
    - Reject (input is blocked, with reason)

    Hooks are called in order; first rejection wins.
    """

    def __init__(self):
        self._hooks: list[Callable] = []

    def add(self, hook: Callable) -> None:
        """Add a policy hook.

        Signature: hook(raw_input: RawInput) -> None | str | RejectResult
        - None = pass
        - str = warning (added to policy_warnings)
        - RejectResult = rejection
        """
        self._hooks.append(hook)

    def evaluate(self, raw: RawInput) -> list[str]:
        """Run all hooks in order. Returns list of warnings.
        Raises InputRejected if any hook rejects."""
        warnings = []
        for hook in self._hooks:
            result = hook(raw)
            if isinstance(result, InputRejected):
                raise result
            elif isinstance(result, str):
                warnings.append(result)
        return warnings


@dataclass
class InputRejected:
    """Raised when a policy hook rejects the input."""
    reason: str


# ---------------------------------------------------------------------------
# Tool allowlist shaper
# ---------------------------------------------------------------------------

class ToolAllowlister:
    """Determine which tools are visible to the model for this turn.

    Filters based on:
    - Agent type (main, buddy, phone)
    - Trust mode (allowlist, full, sandbox)
    - Environment availability (is tool runnable?)
    - Provider capabilities (does model support tool_use?)
    - Explicit allowlist from config
    """

    def __init__(
        self,
        *,
        agent_type: str = "main",
        trust_mode: str = "allowlist",
        explicit_allowlist: list[str] | None = None,
        available_tools: list[str] | None = None,
        provider_capabilities: dict[str, Any] | None = None,
    ):
        self.agent_type = agent_type
        self.trust_mode = trust_mode
        self.explicit_allowlist = explicit_allowlist or []
        self.available_tools = available_tools or []
        self.provider_capabilities = provider_capabilities or {}

    def shape(self, raw: RawInput) -> list[str]:
        """Return the list of tool names visible to the model for this input."""

        # If provider doesn't support tool_use, return empty
        if not self.provider_capabilities.get("supports_tool_use", True):
            return []

        # Full access mode: all available tools
        if self.trust_mode == "full":
            return list(self.available_tools)

        # Sandbox mode: read-only tools only
        if self.trust_mode == "sandbox":
            return [
                t for t in self.available_tools
                if t in SANDBOX_SAFE_TOOLS
            ]

        # Allowlist mode: intersect explicit allowlist with available tools
        if self.explicit_allowlist:
            return [
                t for t in self.available_tools
                if t in self.explicit_allowlist
            ]

        # Default: all available tools
        return list(self.available_tools)


# Read-only tools safe for sandbox / untrusted mode
SANDBOX_SAFE_TOOLS = {
    "read",
    "memory_get",
    "memory_search",
}


# ---------------------------------------------------------------------------
# Attachment expansion
# ---------------------------------------------------------------------------

def expand_attachments(
    attachments: list[dict],
    *,
    session_id: str,
    run_id: str,
) -> list[Message]:
    """Expand attachment references into discrete tool-result messages.

    Each attachment becomes a tool message the model can consume, e.g.:
    - Image: description + file path
    - Audio: transcription text
    - File: content or summary
    - Capture bridge event: formatted packet

    Returns list of Message objects to prepend to the prompt context.
    """
    expanded = []

    for att in attachments:
        att_type = att.get("type", "text")
        content = att.get("content", "")
        media_path = att.get("media_path", "")
        description = att.get("description", "")

        msg = Message(
            role="tool",
            kind=MessageKind.TOOL_RESULT,
            session_id=session_id,
            run_id=run_id,
        )

        if att_type == "image":
            msg.content = (
                f'[Image attachment: "{description or media_path}"]\n'
                f'Path: {media_path}\n'
                f'Caption: {description}'
            )
        elif att_type == "audio":
            msg.content = (
                f'[Audio attachment: "{description or media_path}"]\n'
                f'Transcription: {content}'
            )
        elif att_type == "file":
            msg.content = (
                f'[File attachment: "{description or media_path}"]\n'
                f'Content preview: {content[:2000]}'
            )
        elif att_type == "capture":
            # Bridge capture event
            msg.content = (
                f'[Bridge capture]\n'
                f'Source: {att.get("source", "unknown")}\n'
                f'Event: {att.get("event_type", "text")}\n'
                f'Content: {content}'
            )
        else:
            msg.content = f"[Attachment: {att_type}] {content}"

        msg.token_estimate = estimate_tokens(msg.content)
        expanded.append(msg)

    return expanded


# ---------------------------------------------------------------------------
# System event normalization
# ---------------------------------------------------------------------------

SYSTEM_EVENT_PREFIXES = {
    "cron": "[Cron event]",
    "handoff": "[Handoff resume]",
    "bridge": "[Bridge capture]",
    "scheduler": "[Scheduler event]",
    "memory": "[Memory retrieval]",
    "file_change": "[File change]",
    "provider_notice": "[Provider notice]",
}


def normalize_system_event(text: str, source: str) -> str:
    """Wrap system event text with a typed prefix for model context."""
    prefix = SYSTEM_EVENT_PREFIXES.get(source, "[System event]")
    return f"{prefix}: {text}"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

class InputPipeline:
    """Process raw input into a normalized Message ready for the agent loop.

    Stages:
    1. Normalize raw input → Message
    2. Expand attachments → additional Messages
    3. Run policy hooks → warnings or rejection
    4. Shape tool allowlist → filtered tool list for this turn

    Usage:
        pipeline = InputPipeline(
            policy_hooks=hooks,
            tool_allowlister=allowlister,
        )
        result = pipeline.process(raw_input)
        if result.rejected:
            return  # block
        runtime.add_message(result.message)
    """

    def __init__(
        self,
        *,
        policy_hooks: PolicyHooks | None = None,
        tool_allowlister: ToolAllowlister | None = None,
    ):
        self.policy_hooks = policy_hooks or PolicyHooks()
        self.tool_allowlister = tool_allowlister or ToolAllowlister()

    def process(self, raw: RawInput, *, session_id: str, run_id: str = "") -> PipelineResult:
        """Run the full input pipeline. Returns PipelineResult."""

        # -- Stage 1: Normalize --
        msg = self._normalize(raw)

        # -- Stage 2: Expand attachments --
        attachments_expanded = []
        if raw.attachments:
            attachments_expanded = expand_attachments(
                raw.attachments,
                session_id=session_id,
                run_id=run_id,
            )

        # -- Stage 3: Policy hooks --
        policy_warnings = []
        rejected = False
        rejection_reason = ""

        try:
            policy_warnings = self.policy_hooks.evaluate(raw)
        except InputRejected as e:
            rejected = True
            rejection_reason = e.reason

        if rejected:
            return PipelineResult(
                message=msg,
                rejected=True,
                rejection_reason=rejection_reason,
            )

        # -- Stage 4: Tool allowlist --
        tool_allowlist = self.tool_allowlister.shape(raw)

        return PipelineResult(
            message=msg,
            tool_allowlist=tool_allowlist,
            attachments_expanded=attachments_expanded,
            policy_warnings=policy_warnings,
        )

    def _normalize(self, raw: RawInput) -> Message:
        """Convert raw input into a normalized Message."""

        # System events get wrapped prefix
        if raw.role == "system" and raw.kind == MessageKind.SYSTEM_EVENT:
            content = normalize_system_event(raw.text, raw.source)
        else:
            content = raw.text

        msg = Message(
            role=raw.role,
            kind=raw.kind,
            content=content,
        )

        # Token estimation
        msg.token_estimate = estimate_tokens(content)

        # Metadata passthrough (e.g., for policy hooks or analytics)
        if raw.metadata:
            msg.content += f"\n[_metadata: {raw.metadata}]"

        return msg


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def create_input_pipeline(
    *,
    agent_type: str = "main",
    trust_mode: str = "allowlist",
    tools_allow: list[str] | None = None,
    available_tools: list[str] | None = None,
    provider_capabilities: dict[str, Any] | None = None,
    policy_hooks: PolicyHooks | None = None,
) -> InputPipeline:
    """Create a configured InputPipeline ready to use."""

    allowlister = ToolAllowlister(
        agent_type=agent_type,
        trust_mode=trust_mode,
        explicit_allowlist=tools_allow,
        available_tools=available_tools,
        provider_capabilities=provider_capabilities,
    )

    return InputPipeline(
        policy_hooks=policy_hooks,
        tool_allowlister=allowlister,
    )
