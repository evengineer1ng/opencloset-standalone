# Agent Loop — first real end-to-end agent turn
#
# Drives the turn cycle:
#   begin_run → build_prompt → provider_call → normalize_tool_calls
#   → execute_tools → inject_tool_results → continue (loop)
#   → end_run
#
# Design principles (from Claude Code research):
# - Explicit turn loop, not buried in routes
# - Collect provider stream, then execute tools (V1 sequential)
# - Synthetic tool-result on interruption preserves message-chain validity
# - Optional max-turn budget exists only for explicit capped runs
# - Token-aware: check usage before continuing

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
import queue
import re as _re
import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4
from typing import Any, Generator

from api.api.events import StreamEvent
from api.agent.engine import ConversationRuntime, Message, MessageKind
from api.agent.governor import GovernorDecision, RunGovernor
from api.provider.base import Provider, ProviderEventType, ProviderResult, ToolCall as ProviderToolCall
from api.tools.executor import (
    ExecutionStatus,
    ToolBatchResult,
    ToolCall as ExecutorToolCall,
    ToolExecutor,
    ToolResult,
)
from api.tools.normalizer import ToolCallNormalizer
from api.tools.registry import PermissionDecision, ToolRegistry


def _coalesce_finish_reason(*reasons: str | None) -> str:
    """Pick the first meaningful finish reason, defaulting to 'completed'."""
    for reason in reasons:
        if reason and str(reason).strip():
            return str(reason)
    return "completed"

logger = logging.getLogger(__name__)


class ProviderStreamError(RuntimeError):
    """Provider stream failed after yielding a partial result."""

    def __init__(self, message: str, partial_result: ProviderResult) -> None:
        super().__init__(message)
        self.partial_result = partial_result


class ProviderStreamTimeoutError(ProviderStreamError):
    """Provider stream stopped producing events before completion."""

    def __init__(
        self,
        message: str,
        partial_result: ProviderResult,
        *,
        elapsed_s: float,
        threshold_s: float,
        last_event_type: str = "",
    ) -> None:
        super().__init__(message, partial_result)
        self.elapsed_s = elapsed_s
        self.threshold_s = threshold_s
        self.last_event_type = last_event_type


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_unclosed_transient_window(text: str) -> bool:
    """Return True if text has a <transient-window> open tag with no matching close."""
    open_count = len(_re.findall(r'<transient-window\b', text, _re.IGNORECASE))
    close_count = len(_re.findall(r'</transient-window>', text, _re.IGNORECASE))
    return open_count > close_count


def _has_completed_transient_window(text: str) -> bool:
    """Return True if text contains at least one complete transient-window block."""
    open_count = len(_re.findall(r'<transient-window\b', text, _re.IGNORECASE))
    close_count = len(_re.findall(r'</transient-window>', text, _re.IGNORECASE))
    return open_count > 0 and close_count > 0 and close_count >= open_count


def _close_unclosed_transient_windows(text: str) -> str:
    """Best-effort close any trailing transient-window tags after interruption."""
    missing = max(
        0,
        len(_re.findall(r'<transient-window\b', text, _re.IGNORECASE))
        - len(_re.findall(r'</transient-window>', text, _re.IGNORECASE)),
    )
    if missing <= 0:
        return text
    return text + ("</transient-window>" * missing)


# ---------------------------------------------------------------------------
# Loop configuration
# ---------------------------------------------------------------------------

_XML_TOOL_ATTEMPT_RE = _re.compile(
    r"<(?:exec|write|read|read_file|create_file|edit_file|tool_call|function_call|invoke|use_tool|tool|command)\b",
    _re.IGNORECASE,
)

_XML_TOOL_BLOCK_RE = _re.compile(
    r'(?P<exec><exec>(?P<exec_body>[\s\S]*?)</exec>)'
    r'|(?P<tool_call><tool_call\s+name=(?P<tool_q>[\'"])(?P<tool_name>.+?)(?P=tool_q)\s*>(?P<tool_body>[\s\S]*?)</tool_call>)'
    r'|(?P<invoke><invoke\s+name=(?P<invoke_q>[\'"])(?P<invoke_name>.+?)(?P=invoke_q)\s*>(?P<invoke_body>[\s\S]*?)</invoke>)',
    _re.IGNORECASE,
)
_XML_PLACEHOLDER_RE = _re.compile(
    r"\b(?:your command here|replace the placeholder|real_command|tool_name)\b|\{\s*\"arg\"\s*:\s*\"value\"\s*\}",
    _re.IGNORECASE,
)
_THINK_BLOCK_RE = _re.compile(r"<think>(?P<body>[\s\S]*?)</think>", _re.IGNORECASE)
_XML_ATTR_ASSIGNMENT_RE = _re.compile(
    r"(?P<key>[A-Za-z_][\w.-]*)\s*=\s*(?P<quote>['\"])(?P<value>[\s\S]*?)(?P=quote)",
    _re.IGNORECASE,
)
_XML_ATTR_TOOL_OPEN_RE = _re.compile(r"^<(?P<name>[A-Za-z_][\w-]*)\b", _re.IGNORECASE)

_EMPTY_THINK_ONLY_RE = _re.compile(r"^\s*<think>\s*</think>\s*$", _re.IGNORECASE)
_PENDING_ACTION_RE = _re.compile(
    r"\b(?:let me|now let me|i(?:'ll| will)|i(?:'m| am) going to|we(?:'ll| will)|we(?:'re| are) going to|try pushing again|try again|check the current state|commit and push|start it|add the key|fix permissions|starting|continuing|now\s+(?:pushing|checking|committing|verifying|fixing|trying|adding)|(?:pushing|checking|committing|verifying|fixing|trying|adding)\b(?:\s+to\b)?)\b",
    _re.IGNORECASE,
)
_CLOSURE_DEFERRAL_RE = _re.compile(
    r"\b(?:let me|i(?:'ll| will)|i(?:'m| am) going to|we(?:'ll| will)|we(?:'re| are) going to|need to\s+(?:inspect|check|verify|look|read|search|debug|investigate|try)|before answering)\b",
    _re.IGNORECASE,
)
_COMPLETION_MARKER_RE = _re.compile(
    r"\b(?:done|completed|fixed|resolved|passes|passed|verified|changed|updated|created|written|implemented|summary:|here(?:'s| is) the synthesis)\b",
    _re.IGNORECASE,
)
_BLOCKER_EXPLANATION_RE = _re.compile(
    r"\b(?:blocked|cannot|can't|unable|unavailable|missing|permission denied|not found|not available|doesn't exist|does not exist|placeholder|need(?:s)?\s+(?:your|you|a|an|the)|requires?\s+(?:approval|credentials|input|access)|waiting for approval|failed because)\b",
    _re.IGNORECASE,
)
_GIT_MUTATION_RE = _re.compile(
    r"^\s*git\s+(?:add|commit|amend|merge|rebase|cherry-pick|reset|revert|restore\b(?!.*--source)|checkout\b|switch\b|stash\b|tag\b|rm\b|mv\b|clean\b)",
    _re.IGNORECASE,
)
_INTERACTIVE_TIMEOUT_EXEC_RE = _re.compile(
    r"^\s*(?:git\s+push\b|gh\s+(?:auth\b|repo\b|pr\b))",
    _re.IGNORECASE,
)
_ACTION_REQUEST_RE = _re.compile(
    r"\b(?:get|make|fix|implement|wire|add|create|update|action|finish|complete|ship|build|building)\b",
    _re.IGNORECASE,
)
_NON_ACTION_REQUEST_RE = _re.compile(
    r"\b(?:plan|3-step plan|architecture|spec|design|research|summarize|summary|recommend|what should i|what should we|which should i|continue that existing plan|without redoing the whole intro)\b",
    _re.IGNORECASE,
)
_RESEARCH_SYNTHESIS_RE = _re.compile(
    r"\b(?:research|summarize|summary|source|sources|grounded|capabilities|missing piece)\b",
    _re.IGNORECASE,
)
_ACTIONABLE_ANSWER_RE = _re.compile(
    r"\b(?:first move:|first step:|step 1\b|here(?:'s| is) the (?:3-step )?plan|we should do first|two concrete capabilities|missing piece|current state:)\b",
    _re.IGNORECASE,
)
_DISCOVERY_EXEC_RE = _re.compile(
    r"^\s*(?:rg\b|grep\b|findstr\b|Get-ChildItem\b|dir\b|ls\b|git\s+status\b|git\s+diff\b|git\s+show\b|type\b|cat\b)",
    _re.IGNORECASE,
)
_VALIDATION_EXEC_RE = _re.compile(
    r"(?:^|\s)(?:pytest\b|python\s+-m\s+pytest\b|npm\s+run\s+(?:build|test|lint)\b|ruff\b|mypy\b|tsc\b)",
    _re.IGNORECASE,
)
_EDIT_RESULT_PATH_RE = _re.compile(r"Applied \d+ edit\(s\) to (?P<path>.+)$", _re.MULTILINE)
_WRITE_RESULT_PATH_RE = _re.compile(r"^Wrote file:\s*(?P<path>.+)$", _re.MULTILINE)
_PYTEST_PASSED_RE = _re.compile(r"=+\s+(?P<summary>\d+\s+passed(?:[^=\n]*)?)\s+=+", _re.IGNORECASE)
_EXPLICIT_FILE_PATH_RE = _re.compile(r"`(?P<path>(?:[A-Za-z]:\\|\.{0,2}[\\/]).+?\.[A-Za-z0-9_]+)`")
_GIT_STATUS_RE = _re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+status\b", _re.IGNORECASE)
_RECURSIVE_LIST_RE = _re.compile(
    r"(?:\bget-childitem\b|\bdir\b|\bls\b|\bfind\b).*(?:-recurse|/s\b|-r\b)",
    _re.IGNORECASE,
)


@dataclass
class ActionProgressLedger:
    action_mode: bool
    discovery_budget: int
    discovery_steps: int = 0
    files_read: int = 0
    symbols_found: int = 0
    files_modified: int = 0
    tests_run: int = 0
    artifacts_created: int = 0
    evidence: list[str] = field(default_factory=list)
    seen_discovery_signatures: set[str] = field(default_factory=set)

    def has_verifiable_progress(self) -> bool:
        return self.files_modified > 0 or self.tests_run > 0 or self.artifacts_created > 0

    def should_block_for_discovery_drift(self) -> bool:
        return self.action_mode and not self.has_verifiable_progress() and self.discovery_steps >= self.discovery_budget

    def record_evidence(self, item: str) -> None:
        if not item or item in self.evidence:
            return
        self.evidence.append(item)
        if len(self.evidence) > 5:
            self.evidence = self.evidence[-5:]

    def has_seen_discovery_signature(self, signature: str) -> bool:
        return bool(signature) and signature in self.seen_discovery_signatures

    def record_discovery_signature(self, signature: str) -> None:
        if signature:
            self.seen_discovery_signatures.add(signature)

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "discovery_steps": self.discovery_steps,
            "discovery_budget": self.discovery_budget,
            "files_read": self.files_read,
            "symbols_found": self.symbols_found,
            "files_modified": self.files_modified,
            "tests_run": self.tests_run,
            "artifacts_created": self.artifacts_created,
            "evidence": list(self.evidence),
        }


@dataclass
class PromptClosureDecision:
    answered: bool
    reason: str = ""
    blocker_message: str = ""
    next_required_action: str = ""


@dataclass
class LoopConfig:
    """Configuration for the agent turn loop."""
    max_tool_calls_per_turn: int = 10
    max_turns: int | None = None
    temperature: float = 0.7
    max_tokens: int = 4096
    stop_sequences: list[str] | None = None
    pause_threshold_pct: float | None = 85.0  # pause if context usage exceeds this
    max_consecutive_failure_batches: int = 6
    governor_max_attempts_per_subgoal: int = 4
    governor_duplicate_failure_threshold: int = 2
    governor_freeze_after_attempts: int = 3
    governor_max_pending_action_attempts: int = 2
    governor_repeated_intent_threshold: int = 2
    governor_max_recovery_attempts: int = 5
    provider_stream_idle_timeout_seconds: float | None = None
    action_discovery_budget: int = 5


# ---------------------------------------------------------------------------
# Loop result
# ---------------------------------------------------------------------------

@dataclass
class LoopResult:
    """Outcome of the agent turn loop."""
    text: str = ""
    thinking: str = ""
    tool_results: list[dict] = field(default_factory=list)
    turn_count: int = 0
    finish_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    interrupted: bool = False
    error: str = ""


class _DisplayStreamFilter:
    """Strip hidden reasoning and XML tool markup from live assistant deltas."""

    _PAIRED_TOOL_TAGS = {"exec", "tool_call", "invoke"}

    def __init__(self, known_tool_names: set[str] | None = None):
        self._buffer = ""
        self._known_tool_names = {name.lower() for name in (known_tool_names or set())}
        self._known_tool_names.update(self._PAIRED_TOOL_TAGS)

    def push(self, chunk: str) -> tuple[str, str]:
        if not chunk:
            return "", ""
        self._buffer += chunk
        return self._drain(final=False)

    def finish(self) -> tuple[str, str]:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> tuple[str, str]:
        visible_parts: list[str] = []
        thinking_parts: list[str] = []

        while self._buffer:
            lt_index = self._buffer.find("<")
            if lt_index == -1:
                visible_parts.append(self._buffer)
                self._buffer = ""
                break

            if lt_index > 0:
                visible_parts.append(self._buffer[:lt_index])
                self._buffer = self._buffer[lt_index:]

            lower_buffer = self._buffer.lower()
            if lower_buffer.startswith("<think>"):
                close_index = lower_buffer.find("</think>")
                if close_index == -1:
                    if final:
                        self._buffer = ""
                    break
                inner = self._buffer[len("<think>"):close_index].strip()
                if inner:
                    thinking_parts.append(inner)
                self._buffer = self._buffer[close_index + len("</think>"):]
                continue

            block_match = _XML_TOOL_BLOCK_RE.match(self._buffer)
            if block_match:
                self._buffer = self._buffer[block_match.end():]
                continue

            tag_match = _XML_ATTR_TOOL_OPEN_RE.match(self._buffer)
            if tag_match:
                tag_name = str(tag_match.group("name") or "").lower()
                if tag_name in self._PAIRED_TOOL_TAGS:
                    close_tag = f"</{tag_name}>"
                    close_index = lower_buffer.find(close_tag)
                    if close_index == -1:
                        if final:
                            self._buffer = ""
                        break
                    self._buffer = self._buffer[close_index + len(close_tag):]
                    continue
                if tag_name in self._known_tool_names:
                    gt_index = self._buffer.find(">")
                    if gt_index == -1:
                        if final:
                            self._buffer = ""
                        break
                    self._buffer = self._buffer[gt_index + 1:]
                    continue

            visible_parts.append(self._buffer[0])
            self._buffer = self._buffer[1:]

        return "".join(visible_parts), "\n\n".join(part for part in thinking_parts if part)


# ---------------------------------------------------------------------------
# Prompt builder callback
# ---------------------------------------------------------------------------

BuildPromptFn = Any
"""Callable that returns (messages: list[dict], tools: list[dict]) for the provider call.

The loop calls this before each provider invocation. The builder reads the
runtime message chain, assembles system + transcript + plan sections, and
returns OpenAI-compatible message dicts + tool definitions.

If available, the builder may also stash the estimated assembled prompt token
count on itself as `last_total_tokens`.
"""


# ---------------------------------------------------------------------------
# Agent Loop
# ---------------------------------------------------------------------------

class AgentLoop:
    """Drive a complete agent turn (or series of sub-turns) to completion.

    Flow per iteration:
    1. Build prompt (messages + tools) via caller-supplied builder
    2. Call provider, collect stream events
    3. If tool_calls present: normalize → execute → inject results → repeat
    4. If no tool_calls: append assistant message → done

    Interruption:
    - Checks runtime.is_interrupted before each provider call and tool exec
    - On interrupt: emits synthetic tool-results for in-flight calls
    - Ends run with status="interrupted"
    """

    def __init__(
        self,
        runtime: ConversationRuntime,
        provider: Provider,
        executor: ToolExecutor,
        normalizer: ToolCallNormalizer,
        config: LoopConfig | None = None,
        run_manager=None,
    ):
        self.runtime = runtime
        self.provider = provider
        self.executor = executor
        self.normalizer = normalizer
        self.config = config or LoopConfig()
        self.run_manager = run_manager

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def run(
        self,
        build_prompt: BuildPromptFn,
        *,
        existing_run_id: str | None = None,
        existing_turn_number: int | None = None,
    ) -> LoopResult:
        """Execute the agent loop until completion, interruption, or budget exhaustion.

        Args:
            build_prompt: Callable returning (messages, tools) for each iteration.

        Returns:
            LoopResult with accumulated text, tool results, and metadata.
        """
        # -- Begin run --
        if existing_run_id is not None or existing_turn_number is not None:
            run = self.runtime.begin_run(
                existing_run_id=existing_run_id,
                turn_number=existing_turn_number,
            )
        else:
            run = self.runtime.begin_run()
        logger.info(
            "Agent loop started: session=%s run=%s turn=%d",
            self.runtime.session_id,
            run.id,
            run.turn_number,
        )

        result = LoopResult()
        iteration = 0
        consecutive_failure_batches = 0
        serialization_safe_mode = False
        last_visible_assistant_turn_text = ""
        cumulative_visible_assistant_text = ""
        final_assistant_text = ""
        governor = RunGovernor(
            max_attempts_per_subgoal=self.config.governor_max_attempts_per_subgoal,
            duplicate_failure_threshold=self.config.governor_duplicate_failure_threshold,
            freeze_after_attempts=self.config.governor_freeze_after_attempts,
            max_pending_action_attempts=self.config.governor_max_pending_action_attempts,
            repeated_intent_threshold=self.config.governor_repeated_intent_threshold,
            max_recovery_attempts=self.config.governor_max_recovery_attempts,
        )
        window_continuation_bump: int | None = None
        window_continuation_attempted = False
        forced_synthesis_prompt_sent = False
        forced_action_pivot_prompt_sent = False
        hit_turn_limit = False
        assembled_prompt_tokens = 0
        last_user_prompt = self._last_user_text()
        progress_ledger = ActionProgressLedger(
            action_mode=self._requires_action_closure(last_user_prompt),
            discovery_budget=max(1, self.config.action_discovery_budget),
        )

        try:
            while self.config.max_turns is None or iteration < self.config.max_turns:
                last_user_prompt = self._last_user_text()
                # -- Interrupt check (before any work) --
                if self.runtime.is_interrupted:
                    result.interrupted = True
                    logger.info("Loop interrupted")
                    break

                # -- Build prompt --
                messages, tools = build_prompt()
                assembled_prompt_tokens = self._get_assembled_prompt_tokens(build_prompt)

                # -- Provider call --
                turn_result = self._call_provider(messages, tools, max_tokens_override=window_continuation_bump)
                turn_result.text, intercepted_calls = self._extract_xml_tool_calls(turn_result.text)
                turn_result.text, embedded_thinking = self._extract_embedded_thinking(turn_result.text)
                if embedded_thinking:
                    turn_result.thinking += embedded_thinking
                if intercepted_calls:
                    turn_result.tool_calls.extend(intercepted_calls)
                elif _XML_TOOL_ATTEMPT_RE.search(turn_result.text or ""):
                    logger.warning("Model emitted XML-like tool text but extraction failed; leaving text as-is")
                window_continuation_bump = None  # consume after use
                effective_prompt_tokens = self._effective_prompt_tokens(
                    assembled_prompt_tokens,
                    turn_result.input_tokens,
                )
                prompt_usage_pct = self._usage_pct_for_tokens(effective_prompt_tokens)

                # Some local reasoning models stream only reasoning content.
                # If the turn finished without tool calls and produced no normal
                # assistant text, preserve the reasoning as the visible reply.
                if (
                    not turn_result.text
                    and turn_result.thinking
                    and not turn_result.tool_calls
                ):
                    turn_result.text = turn_result.thinking
                    turn_result.thinking = ""

                if self._is_empty_think_only_text(turn_result.text):
                    turn_result.text = ""

                iteration += 1
                result.turn_count += 1

                invalid_xml_fallback = self._text_contains_invalid_xml_fallback(turn_result.text)
                suppress_visible_turn_text = invalid_xml_fallback
                pending_action_without_tool = (
                    not turn_result.tool_calls
                    and self._text_implies_pending_action(turn_result.text)
                )

                if invalid_xml_fallback and pending_action_without_tool:
                    governor_decision = governor.observe_pending_action(
                        last_text=turn_result.text,
                        invalid_xml_fallback=True,
                        last_user_prompt=last_user_prompt,
                    )
                    result.input_tokens = max(result.input_tokens, effective_prompt_tokens)
                    result.output_tokens += turn_result.output_tokens
                    result.finish_reason = turn_result.finish_reason
                    if self._apply_governor_decision(governor_decision, governor, result):
                        break
                    continue

                if not suppress_visible_turn_text:
                    result.text += turn_result.text
                    cumulative_visible_assistant_text = result.text
                result.thinking += turn_result.thinking
                result.input_tokens = max(result.input_tokens, effective_prompt_tokens)
                result.output_tokens += turn_result.output_tokens
                result.finish_reason = turn_result.finish_reason

                # -- Persist assistant text message --
                if turn_result.text and not suppress_visible_turn_text:
                    last_visible_assistant_turn_text = turn_result.text
                    self._append_assistant_message(turn_result.text, turn_result)

                # -- Truncation recovery --
                # A tool call cut off at max_tokens comes back as finish_reason="length"
                # with the incomplete call dropped by the provider (see llamacpp.py): the loop
                # sees no tool use and no text. Rather than silently lose the action (the old
                # "writes vanish" bug), bump the budget once and retry to COMPLETE it. Also still
                # covers unclosed transient-window text. Guarded to no-visible-text turns so a
                # legitimately long answer isn't regenerated/duplicated.
                if (
                    turn_result.finish_reason == "length"
                    and not turn_result.tool_calls
                    and not window_continuation_attempted
                    and (not turn_result.text.strip() or _has_unclosed_transient_window(result.text))
                ):
                    window_continuation_attempted = True
                    window_continuation_bump = min(max(self.config.max_tokens * 2, 8192), 16384)
                    logger.warning(
                        "Truncated turn (finish_reason=length, no usable tool call); retrying with max_tokens=%d",
                        window_continuation_bump,
                    )
                    continue

                if self.runtime.is_interrupted:
                    result.interrupted = True
                    if not result.finish_reason:
                        result.finish_reason = turn_result.finish_reason or "interrupted"
                    logger.info("Loop interrupted during provider stream")
                    if result.text and _has_unclosed_transient_window(result.text):
                        result.text = _close_unclosed_transient_windows(result.text)
                    break

                # -- Token budget check --
                if (
                    self.config.pause_threshold_pct is not None
                    and prompt_usage_pct >= self.config.pause_threshold_pct
                ):
                    logger.warning(
                        "Token usage %.1f%% exceeds pause threshold %.1f%%",
                        prompt_usage_pct,
                        self.config.pause_threshold_pct,
                    )
                    self.runtime.pause()
                    result.interrupted = True
                    result.finish_reason = "rollover_threshold"
                    break

                # -- No tool calls → done, waiting for user, or narrating --
                if not turn_result.tool_calls:
                    if pending_action_without_tool:
                        if (
                            not forced_synthesis_prompt_sent
                            and self._prompt_requests_research_synthesis(last_user_prompt)
                            and progress_ledger.files_read > 0
                        ):
                            self._inject_runtime_message(
                                role="user",
                                kind=MessageKind.TEXT,
                                content=(
                                    "You have already inspected enough local sources to answer this research request. "
                                    "Stop exploring and answer the user's prompt directly now. "
                                    "Provide the requested findings, cite the file paths you already read, "
                                    "and do not call more tools unless a source is genuinely missing."
                                ),
                            )
                            forced_synthesis_prompt_sent = True
                            continue
                        if (
                            not forced_action_pivot_prompt_sent
                            and progress_ledger.action_mode
                            and progress_ledger.discovery_steps > 0
                            and not progress_ledger.has_verifiable_progress()
                        ):
                            self._inject_runtime_message(
                                role="user",
                                kind=MessageKind.TEXT,
                                content=(
                                    "You already inspected the relevant seam. "
                                    "Do not announce another read or check. "
                                    "Emit exactly one concrete next tool call now for the smallest edit or verification step."
                                ),
                            )
                            forced_action_pivot_prompt_sent = True
                            continue
                        governor_decision = governor.observe_pending_action(
                            last_text=turn_result.text,
                            last_user_prompt=last_user_prompt,
                        )
                        if self._apply_governor_decision(governor_decision, governor, result):
                            break
                        continue
                    if (
                        not forced_action_pivot_prompt_sent
                        and progress_ledger.action_mode
                        and progress_ledger.discovery_steps > 0
                        and not progress_ledger.has_verifiable_progress()
                    ):
                        self._inject_runtime_message(
                            role="user",
                            kind=MessageKind.TEXT,
                            content=(
                                "You have already identified the relevant implementation seam. "
                                "Do not stop at diagnosis. Make the smallest justified change now, "
                                "then run the requested verification and report the completed result."
                            ),
                        )
                        forced_action_pivot_prompt_sent = True
                        continue
                    if progress_ledger.action_mode and progress_ledger.discovery_steps > 0 and not progress_ledger.has_verifiable_progress():
                        self._apply_action_progress_blocker(
                            result,
                            progress_ledger,
                            last_text=turn_result.text,
                        )
                        break
                    governor.reset_pending_action()
                    break

                # -- Tool calls present → normalize & execute --
                logger.info(
                    "Iteration %d: %d tool call(s) to execute",
                    iteration,
                    len(turn_result.tool_calls),
                )

                # Normalize provider calls → executor calls
                executor_calls = self.normalizer.normalize_batch(
                    turn_result.tool_calls,
                    validate_known=True,
                )

                if not executor_calls:
                    logger.warning("All tool calls failed normalization; stopping loop")
                    break

                # Register tool calls in runtime
                for ec in executor_calls:
                    self.runtime.register_tool_call(ec.call_id)

                original_call_order = [call.call_id for call in executor_calls]
                original_calls_by_id = {call.call_id: call for call in executor_calls}
                preflight_results: list[Any] = []
                if serialization_safe_mode:
                    executor_calls, preflight_results = self._apply_serialization_safe_mode(executor_calls)
                executor_calls, governor_results = governor.filter_calls(executor_calls)
                if governor_results:
                    preflight_results.extend(governor_results)
                executor_calls, vcs_policy_results = self._apply_vcs_intent_guard(executor_calls)
                if vcs_policy_results:
                    preflight_results.extend(vcs_policy_results)
                executor_calls, explicit_target_results = self._apply_explicit_target_guard(
                    executor_calls,
                    progress_ledger,
                    last_user_prompt=last_user_prompt,
                )
                if explicit_target_results:
                    preflight_results.extend(explicit_target_results)
                executor_calls, duplicate_discovery_results = self._apply_duplicate_discovery_guard(
                    executor_calls,
                    progress_ledger,
                )
                if duplicate_discovery_results:
                    preflight_results.extend(duplicate_discovery_results)

                # Stream tool_use events via run lifecycle
                # (handled in _execute_tools)

                # Execute tools
                batch = self.executor.execute_all(
                    executor_calls,
                    session_id=self.runtime.session_id,
                ) if executor_calls else ToolBatchResult(results=[])
                batch = self._retry_timeouts_as_background(batch, original_calls_by_id)

                if preflight_results:
                    combined_by_call_id = {
                        **{tool_result.call_id: tool_result for tool_result in batch.results},
                        **{tool_result.call_id: tool_result for tool_result in preflight_results},
                    }
                    ordered_results = [combined_by_call_id[call_id] for call_id in original_call_order if call_id in combined_by_call_id]
                    batch = ToolBatchResult(results=ordered_results)

                # -- Check for ASK → blocked --
                if batch.any_asks:
                    logger.info("Loop blocked on user approval (ASK)")
                    result.finish_reason = "approval_required"
                    break

                # -- Inject tool results into message chain --
                for tool_result in batch.results:
                    if self.run_manager and self.runtime.current_run:
                        status = tool_result.status.value if hasattr(tool_result.status, "value") else tool_result.status
                        self.run_manager.stream_tool_result(
                            self.runtime.current_run.id,
                            tool_result.call_id,
                            tool_result.tool_name,
                            status,
                            tool_result.content,
                            tool_result.error,
                            getattr(tool_result, "error_code", None),
                        )
                    self._inject_tool_result(tool_result)
                    result.tool_results.append(tool_result.to_dict())

                self._record_action_progress(progress_ledger, original_calls_by_id, batch.results)
                if progress_ledger.should_block_for_discovery_drift():
                    self._apply_action_progress_blocker(result, progress_ledger)
                    break

                first_discovery_batch_ready_to_act = (
                    not forced_action_pivot_prompt_sent
                    and progress_ledger.action_mode
                    and progress_ledger.discovery_steps == 1
                    and progress_ledger.files_read > 0
                    and not progress_ledger.has_verifiable_progress()
                    and batch.results
                    and all(
                        (original_calls_by_id.get(tool_result.call_id) is not None)
                        and original_calls_by_id[tool_result.call_id].name in {"read", "read_file", "list_dir", "grep_search", "file_search"}
                        for tool_result in batch.results
                    )
                )
                if first_discovery_batch_ready_to_act:
                    self._inject_runtime_message(
                        role="user",
                        kind=MessageKind.TEXT,
                        content=self._build_first_action_pivot_message(
                            last_user_prompt=last_user_prompt,
                            calls_by_id=original_calls_by_id,
                            batch_results=batch.results,
                        ),
                    )
                    forced_action_pivot_prompt_sent = True
                    continue

                if self._batch_has_failures(batch):
                    governor_decision = governor.observe_batch(original_calls_by_id, batch.results)
                    self._apply_governor_decision(governor_decision, governor, result)
                    consecutive_failure_batches += 1
                    if self._batch_has_external_blocker(batch):
                        result.error = self._summarize_external_blocker(batch)
                        result.finish_reason = "external_blocker"
                        break
                    if self._batch_has_unrequested_vcs_mutation(batch):
                        result.error = self._summarize_unrequested_vcs_mutation(batch)
                        result.finish_reason = "unrequested_vcs_mutation"
                        break
                    governor_recovery = governor.observe_recoverable_failures(
                        original_calls_by_id,
                        batch.results,
                        last_user_prompt=last_user_prompt,
                    )
                    if self._apply_governor_decision(governor_recovery, governor, result):
                        break
                    if governor_recovery.message_content and consecutive_failure_batches < self.config.max_consecutive_failure_batches:
                        continue
                    if consecutive_failure_batches >= 2 and self._batch_has_serialization_failures(batch):
                        serialization_safe_mode = True
                    if consecutive_failure_batches >= self.config.max_consecutive_failure_batches:
                        result.error = "Consecutive failure batches exceeded."
                        result.finish_reason = "consecutive_failures_exceeded"
                        break
                else:
                    consecutive_failure_batches = 0
                    governor.reset_recovery_attempts()

                # -- Interrupt check after tool execution --
                if self.runtime.is_interrupted:
                    result.interrupted = True
                    logger.info("Loop interrupted after tool execution")
                    break

                # -- Continue loop (provider will generate continuation) --
                # Next iteration will rebuild prompt with tool results included
            else:
                hit_turn_limit = True

        except ProviderStreamError as e:
            partial_result = e.partial_result
            safe_partial_text = self._sanitize_assistant_text_for_display(partial_result.text)
            result.text += safe_partial_text
            result.thinking += partial_result.thinking
            result.input_tokens = max(result.input_tokens, self._effective_prompt_tokens(
                assembled_prompt_tokens,
                partial_result.input_tokens,
            ))
            result.output_tokens += partial_result.output_tokens
            if partial_result.finish_reason:
                result.finish_reason = partial_result.finish_reason
            if safe_partial_text:
                last_visible_assistant_turn_text = safe_partial_text
                self._append_assistant_message(safe_partial_text, partial_result)
                self._persist_final_assistant_message(safe_partial_text)

            error_msg = str(e) if str(e) else type(e).__name__
            logger.error("Agent loop error after partial provider output: %s", error_msg)
            if isinstance(e, ProviderStreamTimeoutError):
                recovered_text = self._recover_from_provider_idle_timeout(
                    result,
                    ledger=progress_ledger,
                    partial_text=safe_partial_text,
                    last_user_prompt=self._last_user_text(),
                )
                if recovered_text:
                    synthetic_result = ProviderResult()
                    synthetic_result.text = recovered_text
                    result.text += recovered_text
                    result.finish_reason = "provider_stream_timeout_recovered"
                    result.error = ""
                    self._append_assistant_message(recovered_text, synthetic_result)
                    self._persist_final_assistant_message(recovered_text)
                    self._emit_assistant_final_summary(
                        status="succeeded",
                        finish_reason=result.finish_reason,
                        final_text=recovered_text,
                        transient_text=result.text,
                    )
                    self._finalize_run(status="succeeded")
                    return result
            result.error = error_msg
            if isinstance(e, ProviderStreamTimeoutError):
                result.finish_reason = "provider_stream_timeout"
            self._finalize_run(status="failed", error=error_msg)
            return result
        except Exception as e:
            error_msg = str(e) if str(e) else type(e).__name__
            logger.error("Agent loop error: %s", error_msg)
            result.error = error_msg
            if isinstance(e, ProviderStreamTimeoutError):
                result.finish_reason = "provider_stream_timeout"
            self._finalize_run(status="failed", error=error_msg)
            return result

        if hit_turn_limit:
            logger.warning("Loop stopped after reaching max_turns=%s", self.config.max_turns)
            result.interrupted = True
            result.finish_reason = "max_turns_reached"

        if not result.error and not result.interrupted and result.finish_reason not in {"action_progress_blocked", "approval_required"}:
            last_user_prompt = self._last_user_text()
            closure_decision = self._evaluate_prompt_closure(
                last_user_prompt=last_user_prompt,
                assistant_text=cumulative_visible_assistant_text or last_visible_assistant_turn_text,
                ledger=progress_ledger,
            )
            if not closure_decision.answered:
                self._apply_prompt_unanswered_blocker(
                    result,
                    ledger=progress_ledger,
                    last_user_prompt=last_user_prompt,
                    assistant_text=cumulative_visible_assistant_text or last_visible_assistant_turn_text,
                    decision=closure_decision,
                )
            elif last_visible_assistant_turn_text:
                self._persist_final_assistant_message(last_visible_assistant_turn_text)
                final_assistant_text = last_visible_assistant_turn_text

        if not result.interrupted:
            terminal_status = "succeeded"
            if result.finish_reason in {"action_progress_blocked", "prompt_unanswered", "repeated_intent_blocked"}:
                terminal_status = "blocked"
            elif result.error:
                terminal_status = "failed"
            self._emit_assistant_final_summary(
                status=terminal_status,
                finish_reason=result.finish_reason or ("completed" if terminal_status == "succeeded" else terminal_status),
                final_text=final_assistant_text,
                transient_text=result.text,
            )

        # -- End run --
        if result.finish_reason == "action_progress_blocked":
            self._finalize_run(status="blocked", error=result.error, error_code="action_progress_blocked")
        elif result.finish_reason == "repeated_intent_blocked":
            self._finalize_run(status="blocked", error=result.error, error_code="repeated_intent_blocked")
        elif result.finish_reason == "prompt_unanswered":
            self._finalize_run(status="blocked", error=result.error, error_code="prompt_unanswered")
        elif result.error and result.finish_reason in {
            "external_blocker",
            "consecutive_failures_exceeded",
            "unrequested_vcs_mutation",
        }:
            self._finalize_run(status="failed", error=result.error)
        elif result.interrupted:
            self._finalize_run(status="interrupted")
        elif result.error:
            self._finalize_run(status="failed", error=result.error)
        else:
            self._finalize_run(status="succeeded")

        logger.info(
            "Agent loop completed: iterations=%d text_len=%d tools=%d finish=%s",
            iteration,
            len(result.text),
            len(result.tool_results),
            result.finish_reason or "done",
        )

        return result

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _call_provider(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens_override: int | None = None,
    ) -> ProviderResult:
        """Call the provider and accumulate events into a ProviderResult."""
        effective_max_tokens = max_tokens_override if max_tokens_override is not None else self.config.max_tokens
        result = ProviderResult()
        stream_filter = _DisplayStreamFilter(self._known_tool_names())
        try:
            provider_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
            idle_timeout_s = self.config.provider_stream_idle_timeout_seconds
            poll_interval_s = None
            if idle_timeout_s is not None:
                poll_interval_s = min(0.25, max(0.01, idle_timeout_s / 4.0))

            def _pump_provider_events() -> None:
                try:
                    for streamed_event in self.provider.run_stream(
                        messages,
                        temperature=self.config.temperature,
                        max_tokens=effective_max_tokens,
                        stop_sequences=self.config.stop_sequences,
                        tools=tools,
                        interrupt_check=lambda: self.runtime.is_interrupted,
                    ):
                        provider_queue.put(("event", streamed_event))
                except BaseException as exc:  # pragma: no cover - exercised through caller behavior
                    provider_queue.put(("error", exc))
                finally:
                    provider_queue.put(("done", None))

            threading.Thread(target=_pump_provider_events, daemon=True).start()

            last_event_at = time.monotonic()
            last_event_type = ""

            while True:
                try:
                    if poll_interval_s is None:
                        queue_item = provider_queue.get()
                    else:
                        queue_item = provider_queue.get(timeout=poll_interval_s)
                except queue.Empty:
                    elapsed_s = time.monotonic() - last_event_at
                    if idle_timeout_s is None or elapsed_s < idle_timeout_s:
                        continue
                    self.runtime.request_interrupt()
                    try:
                        self.provider.cancel_active()
                    except Exception:
                        logger.debug("Provider cancel_active() failed during idle timeout cleanup", exc_info=True)
                    if self.run_manager and self.runtime.current_run:
                        self.run_manager.emit_stream_event(
                            self.runtime.current_run.id,
                            StreamEvent.provider_stream_timeout(
                                elapsed_s=elapsed_s,
                                threshold_s=idle_timeout_s,
                                last_event_type=last_event_type,
                            ),
                        )
                    raise ProviderStreamTimeoutError(
                        f"Provider stream produced no events for {elapsed_s:.1f}s (threshold {idle_timeout_s:.1f}s)",
                        result,
                        elapsed_s=elapsed_s,
                        threshold_s=idle_timeout_s,
                        last_event_type=last_event_type,
                    )

                kind, payload = queue_item
                if kind == "done":
                    break
                if kind == "error":
                    raise payload

                event = payload
                last_event_at = time.monotonic()
                last_event_type = event.type.value if hasattr(event.type, "value") else str(event.type)

                if event.type == ProviderEventType.TEXT_DELTA:
                    result.text += event.text
                    if self.run_manager and self.runtime.current_run:
                        visible_delta, thinking_delta = stream_filter.push(event.text)
                        if visible_delta:
                            self.run_manager.stream_text_delta(self.runtime.current_run.id, visible_delta)
                        if thinking_delta:
                            self.run_manager.stream_thinking_delta(self.runtime.current_run.id, thinking_delta)
                elif event.type == ProviderEventType.TOOL_USE:
                    if event.tool_call:
                        result.tool_calls.append(event.tool_call)
                        if self.run_manager and self.runtime.current_run:
                            normalized = self.normalizer.normalize(event.tool_call, validate_known=False)
                            input_data = normalized.executor_call.arguments if normalized.executor_call else {}
                            self.run_manager.stream_tool_use(
                                self.runtime.current_run.id,
                                event.tool_call.name,
                                input_data,
                            )
                elif event.type == ProviderEventType.THINKING_DELTA:
                    result.thinking += event.text
                    if self.run_manager and self.runtime.current_run:
                        self.run_manager.stream_thinking_delta(self.runtime.current_run.id, event.text)
                elif event.type == ProviderEventType.USAGE:
                    result.input_tokens = event.input_tokens
                    result.output_tokens = event.output_tokens
                    result.finish_reason = event.finish_reason
                    if self.run_manager and self.runtime.current_run:
                        self.run_manager.stream_usage(
                            self.runtime.current_run.id,
                            event.input_tokens,
                            event.output_tokens,
                        )
            if self.run_manager and self.runtime.current_run:
                trailing_visible, trailing_thinking = stream_filter.finish()
                if trailing_visible:
                    self.run_manager.stream_text_delta(self.runtime.current_run.id, trailing_visible)
                if trailing_thinking:
                    self.run_manager.stream_thinking_delta(self.runtime.current_run.id, trailing_thinking)
        except Exception as exc:
            error_msg = str(exc) if str(exc) else type(exc).__name__
            if result.text or result.thinking or result.tool_calls or result.input_tokens or result.output_tokens:
                raise ProviderStreamError(error_msg, result) from exc
            raise
        return result

    def _extract_xml_tool_calls(self, text: str) -> tuple[str, list[ProviderToolCall]]:
        """Convert XML-like tool text into real provider tool calls and strip it from the reply."""
        if not text:
            return "", []

        text, extracted = self._extract_attribute_xml_tool_calls(text)
        text, generic_paired_calls = self._extract_generic_paired_xml_tool_calls(text)
        extracted.extend(generic_paired_calls)
        cleaned_parts: list[str] = []
        last_end = 0

        for match in _XML_TOOL_BLOCK_RE.finditer(text):
            cleaned_parts.append(text[last_end:match.start()])
            last_end = match.end()
            raw_block = match.group(0)

            if match.group("exec") is not None:
                command = (match.group("exec_body") or "").strip()
                if not command or self._looks_like_placeholder_xml("exec", command):
                    cleaned_parts.append(raw_block)
                    continue
                tool_name = "exec"
                arguments = json.dumps({"command": command}, ensure_ascii=False)
            elif match.group("tool_call") is not None:
                tool_name = (match.group("tool_name") or "").strip()
                arguments = (match.group("tool_body") or "").strip() or "{}"
                if not tool_name or self._looks_like_placeholder_xml(tool_name, arguments):
                    cleaned_parts.append(raw_block)
                    continue
            else:
                tool_name = (match.group("invoke_name") or "").strip()
                arguments = (match.group("invoke_body") or "").strip() or "{}"
                if not tool_name or self._looks_like_placeholder_xml(tool_name, arguments):
                    cleaned_parts.append(raw_block)
                    continue

            extracted.append(
                ProviderToolCall(
                    id=f"xml_{uuid4().hex[:12]}",
                    name=tool_name,
                    arguments=arguments,
                )
            )

        cleaned_parts.append(text[last_end:])
        cleaned_text = "".join(cleaned_parts)
        cleaned_text = _re.sub(r"\n{3,}", "\n\n", cleaned_text).strip()
        return cleaned_text, extracted

    def _extract_generic_paired_xml_tool_calls(self, text: str) -> tuple[str, list[ProviderToolCall]]:
        if not text:
            return "", []

        extracted: list[ProviderToolCall] = []
        cleaned_parts: list[str] = []
        last_end = 0
        known_tool_names = self._known_tool_names()
        paired_tag_re = _re.compile(
            r"<(?P<name>[A-Za-z_][\w-]*)>(?P<body>[\s\S]*?)</(?P=name)>",
            _re.IGNORECASE,
        )

        for match in paired_tag_re.finditer(text):
            tool_name = str(match.group("name") or "").strip()
            lowered_name = tool_name.lower()
            if lowered_name in {"think", "exec", "tool_call", "invoke", "transient-window"}:
                continue
            if lowered_name not in known_tool_names:
                continue
            body = (match.group("body") or "").strip()
            if not body:
                continue
            arguments = self._coerce_generic_xml_tool_arguments(tool_name, body)
            if arguments is None:
                continue
            if self._looks_like_placeholder_xml(tool_name, json.dumps(arguments, ensure_ascii=False)):
                continue

            cleaned_parts.append(text[last_end:match.start()])
            last_end = match.end()
            extracted.append(
                ProviderToolCall(
                    id=f"xml_{uuid4().hex[:12]}",
                    name=tool_name,
                    arguments=json.dumps(arguments, ensure_ascii=False),
                )
            )

        if not extracted:
            return text, []

        cleaned_parts.append(text[last_end:])
        cleaned_text = "".join(cleaned_parts)
        cleaned_text = _re.sub(r"\n{3,}", "\n\n", cleaned_text).strip()
        return cleaned_text, extracted

    def _extract_attribute_xml_tool_calls(self, text: str) -> tuple[str, list[ProviderToolCall]]:
        if not text:
            return "", []

        extracted: list[ProviderToolCall] = []
        cleaned_parts: list[str] = []
        last_end = 0
        known_tool_names = self._known_tool_names()

        for match in _re.finditer(r"<(?P<name>[A-Za-z_][\w-]*)\b(?P<body>[^<>]*?)>", text, _re.IGNORECASE):
            tool_name = str(match.group("name") or "").strip()
            lowered_name = tool_name.lower()
            if lowered_name in {"think", "/think", "transient-window", "/transient-window"}:
                continue
            if lowered_name not in known_tool_names:
                continue
            if text[match.start():match.end()].startswith("</"):
                continue
            if lowered_name in {"exec", "tool_call", "invoke"} and match.group("body").strip() == "":
                continue

            arguments = self._parse_xml_attributes(match.group("body") or "")
            if not arguments:
                continue
            if self._looks_like_placeholder_xml(tool_name, json.dumps(arguments, ensure_ascii=False)):
                continue

            cleaned_parts.append(text[last_end:match.start()])
            last_end = match.end()
            extracted.append(
                ProviderToolCall(
                    id=f"xml_{uuid4().hex[:12]}",
                    name=tool_name,
                    arguments=json.dumps(arguments),
                )
            )

        if not extracted:
            return text, []

        cleaned_parts.append(text[last_end:])
        cleaned_text = "".join(cleaned_parts)
        return cleaned_text, extracted

    def _coerce_generic_xml_tool_arguments(self, tool_name: str, body: str) -> dict[str, Any] | None:
        stripped = (body or "").strip()
        if not stripped:
            return None
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed

        lowered_name = tool_name.lower()
        if lowered_name in {"read", "list_dir"}:
            return {"path": stripped}
        if lowered_name == "read_file":
            return {"filePath": stripped}
        if lowered_name == "exec":
            return {"command": stripped}
        return None

    def _is_empty_think_only_text(self, text: str) -> bool:
        return bool(text and _EMPTY_THINK_ONLY_RE.match(text))

    def _extract_embedded_thinking(self, text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        captured: list[str] = []

        def _replace(match: _re.Match[str]) -> str:
            body = str(match.group("body") or "").strip()
            if body:
                captured.append(body)
            return ""

        cleaned = _THINK_BLOCK_RE.sub(_replace, text)
        cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)
        if not cleaned.strip():
            cleaned = ""
        return cleaned, "\n\n".join(captured)

    def _known_tool_names(self) -> set[str]:
        registry = getattr(self.normalizer, "registry", None)
        tools = getattr(registry, "_tools", {}) if registry is not None else {}
        return {str(name).lower() for name in tools.keys()}

    def _parse_xml_attributes(self, raw_body: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for match in _XML_ATTR_ASSIGNMENT_RE.finditer(raw_body or ""):
            key = str(match.group("key") or "").strip()
            value = str(match.group("value") or "")
            if key:
                parsed[key] = value
        return parsed

    def _looks_like_placeholder_xml(self, tool_name: str, raw_body: str) -> bool:
        normalized_name = (tool_name or "").strip()
        normalized_body = (raw_body or "").strip()
        if not normalized_name or not normalized_body:
            return True
        if _XML_PLACEHOLDER_RE.search(normalized_name):
            return True
        if _XML_PLACEHOLDER_RE.search(normalized_body):
            return True
        return False

    def _text_contains_invalid_xml_fallback(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        if not _XML_TOOL_ATTEMPT_RE.search(normalized):
            return False
        # By the time this check runs, valid XML tool blocks have already been
        # extracted from the assistant text. Any remaining tool-like XML is an
        # unusable fallback attempt and should not be surfaced as normal chat.
        return True

    def _sanitize_assistant_text_for_display(self, text: str) -> str:
        normalized = text or ""
        normalized, _ = self._extract_embedded_thinking(normalized)
        normalized, _ = self._extract_attribute_xml_tool_calls(normalized)
        normalized, _ = self._extract_xml_tool_calls(normalized)
        normalized = _re.sub(r"\n{3,}", "\n\n", normalized).strip()
        return normalized

    def _text_implies_pending_action(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized or self._is_empty_think_only_text(normalized):
            return False
        if self._text_contains_actionable_answer(normalized):
            return False
        closure_view = self._closure_tail(normalized)
        if _COMPLETION_MARKER_RE.search(closure_view):
            return False
        if self._text_contains_invalid_xml_fallback(closure_view):
            return True
        return bool(_PENDING_ACTION_RE.search(closure_view))

    def _requires_action_closure(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        if _NON_ACTION_REQUEST_RE.search(normalized):
            return False
        return bool(_ACTION_REQUEST_RE.search(normalized))

    def _prompt_requests_research_synthesis(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        return bool(_RESEARCH_SYNTHESIS_RE.search(normalized))

    def _text_defers_closure(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        closure_view = self._closure_tail(normalized)
        if _COMPLETION_MARKER_RE.search(closure_view):
            return False
        if self._text_contains_actionable_answer(normalized):
            return False
        return bool(_PENDING_ACTION_RE.search(closure_view) or _CLOSURE_DEFERRAL_RE.search(closure_view))

    def _text_explains_blocker(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        return bool(_BLOCKER_EXPLANATION_RE.search(self._closure_tail(normalized)))

    def _text_contains_actionable_answer(self, text: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        return bool(_ACTIONABLE_ANSWER_RE.search(normalized))

    def _closure_tail(self, text: str) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return ""
        blocks = [block.strip() for block in _re.split(r"\n\s*\n", normalized) if block.strip()]
        if not blocks:
            return normalized[-500:]
        return blocks[-1][-800:]

    def _evaluate_prompt_closure(
        self,
        *,
        last_user_prompt: str,
        assistant_text: str,
        ledger: ActionProgressLedger,
    ) -> PromptClosureDecision:
        visible_text = (assistant_text or "").strip()
        next_required_action = (
            "Answer the user's last prompt directly. If more work is required first, make one verifiable step and then report the completed result or explicit blocker instead of narrating intent."
        )

        if not visible_text:
            reason = "no_visible_answer"
        elif _has_completed_transient_window(visible_text):
            return PromptClosureDecision(answered=True)
        elif self._text_defers_closure(visible_text):
            reason = "transient_progress_only"
        elif ledger.action_mode and not ledger.has_verifiable_progress() and not self._text_explains_blocker(visible_text):
            reason = "no_verifiable_progress"
        else:
            return PromptClosureDecision(answered=True)

        blocker_message = (
            "Blocked: run ended without a clear answer to the last user prompt. "
            f"Reason: {reason}. "
            f"Last user prompt: {last_user_prompt or 'Unavailable.'} "
            f"Last visible assistant text: {visible_text or 'None.'} "
            f"Next required action: {next_required_action}"
        )
        return PromptClosureDecision(
            answered=False,
            reason=reason,
            blocker_message=blocker_message,
            next_required_action=next_required_action,
        )

    def _record_action_progress(
        self,
        ledger: ActionProgressLedger,
        calls_by_id: dict[str, ExecutorToolCall],
        batch_results: list[ToolResult],
    ) -> None:
        if not ledger.action_mode:
            return
        for tool_result in batch_results:
            call = calls_by_id.get(tool_result.call_id)
            if call is None:
                continue
            if not self._tool_result_succeeded(tool_result):
                continue
            if self._tool_call_counts_as_mutation(call):
                ledger.files_modified += 1
                continue
            if self._tool_call_counts_as_validation(call):
                ledger.tests_run += 1
                ledger.record_evidence(self._summarize_progress_evidence(call, tool_result))
                continue
            if self._tool_call_counts_as_artifact_creation(call):
                ledger.artifacts_created += 1
                ledger.record_evidence(self._summarize_progress_evidence(call, tool_result))
                continue
            if self._tool_call_counts_as_discovery(call):
                ledger.discovery_steps += 1
                ledger.files_read += self._count_files_read(call)
                ledger.symbols_found += self._count_symbols_found(call, tool_result)
                ledger.record_discovery_signature(self._discovery_signature(call))
                ledger.record_evidence(self._summarize_progress_evidence(call, tool_result))

    def _tool_call_counts_as_discovery(self, call: ExecutorToolCall) -> bool:
        if call.name in {"read", "read_file", "file_search", "grep_search", "semantic_search", "list_dir"}:
            return True
        if call.name != "exec":
            return False
        return bool(_DISCOVERY_EXEC_RE.search(self._command_for_progress(call)))

    def _tool_call_counts_as_mutation(self, call: ExecutorToolCall) -> bool:
        if call.name in {"write", "edit", "apply_patch", "plan_update", "plan_set_status", "plan_add_item"}:
            return True
        if call.name != "exec":
            return False
        return bool(_GIT_MUTATION_RE.search(self._command_for_progress(call)))

    def _tool_call_counts_as_artifact_creation(self, call: ExecutorToolCall) -> bool:
        return call.name in {"create_file", "plan_create", "capture_create"}

    def _tool_call_counts_as_validation(self, call: ExecutorToolCall) -> bool:
        if call.name != "exec":
            return False
        return bool(_VALIDATION_EXEC_RE.search(self._command_for_progress(call)))

    def _command_for_progress(self, call: ExecutorToolCall) -> str:
        command = call.arguments.get("command")
        return str(command or "") if isinstance(command, str) else ""

    def _tool_result_succeeded(self, tool_result: ToolResult) -> bool:
        status = tool_result.status.value if hasattr(tool_result.status, "value") else str(tool_result.status)
        return status == "success"

    def _count_files_read(self, call: ExecutorToolCall) -> int:
        if call.name in {"read", "read_file"}:
            return 1
        command = self._command_for_progress(call).strip().lower()
        if command.startswith("type ") or command.startswith("cat "):
            return 1
        return 0

    def _count_symbols_found(self, call: ExecutorToolCall, tool_result: ToolResult) -> int:
        if call.name not in {"file_search", "grep_search", "semantic_search"} and not self._tool_call_counts_as_discovery(call):
            return 0
        detail = str(tool_result.content or "").strip()
        if not detail:
            return 0
        lines = [line for line in detail.splitlines() if line.strip()]
        return len(lines) if lines else 1

    def _summarize_progress_evidence(self, call: ExecutorToolCall, tool_result: ToolResult) -> str:
        location = ""
        for key in ("filePath", "path", "query", "includePattern", "command"):
            value = call.arguments.get(key)
            if isinstance(value, str) and value.strip():
                location = value.strip()
                break
        status = tool_result.status.value if hasattr(tool_result.status, "value") else str(tool_result.status)
        detail = str(tool_result.error or tool_result.content or "").strip().replace("\n", " ")
        if len(detail) > 120:
            detail = f"{detail[:117]}..."
        subject = f" on {location}" if location else ""
        if detail:
            return f"{call.name}{subject} -> {status}: {detail}"
        return f"{call.name}{subject} -> {status}"

    def _apply_duplicate_discovery_guard(
        self,
        executor_calls: list[ExecutorToolCall],
        ledger: ActionProgressLedger,
    ) -> tuple[list[ExecutorToolCall], list[ToolResult]]:
        if not ledger.action_mode or ledger.has_verifiable_progress():
            return executor_calls, []

        allowed: list[ExecutorToolCall] = []
        blocked: list[ToolResult] = []
        for call in executor_calls:
            if not self._tool_call_counts_as_discovery(call):
                allowed.append(call)
                continue
            signature = self._discovery_signature(call)
            if not signature or not ledger.has_seen_discovery_signature(signature):
                allowed.append(call)
                continue
            blocked.append(
                ToolResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status=ExecutionStatus.VALIDATION_FAILED,
                    content="",
                    error=self._duplicate_discovery_error(call),
                )
            )
        return allowed, blocked

    def _apply_explicit_target_guard(
        self,
        executor_calls: list[ExecutorToolCall],
        ledger: ActionProgressLedger,
        *,
        last_user_prompt: str,
    ) -> tuple[list[ExecutorToolCall], list[ToolResult]]:
        if not ledger.action_mode or ledger.has_verifiable_progress():
            return executor_calls, []

        explicit_target = self._extract_explicit_target_path(last_user_prompt)
        if not explicit_target:
            return executor_calls, []

        allowed: list[ExecutorToolCall] = []
        blocked: list[ToolResult] = []
        for call in executor_calls:
            if self._should_block_for_explicit_target(call, explicit_target):
                blocked.append(
                    ToolResult(
                        call_id=call.call_id,
                        tool_name=call.name,
                        status=ExecutionStatus.VALIDATION_FAILED,
                        content="",
                        error=(
                            f"Explicit-target guard blocked repo-wide discovery for '{explicit_target}'. "
                            "The user already named the file to change. "
                            "Do not widen into repository status or recursive file hunts. "
                            "Use the explicit target file directly for read/edit, then run the requested validation."
                        ),
                    )
                )
                continue
            allowed.append(call)
        return allowed, blocked

    def _discovery_signature(self, call: ExecutorToolCall) -> str:
        if call.name in {"read", "read_file", "list_dir"}:
            for key in ("path", "filePath"):
                value = call.arguments.get(key)
                if isinstance(value, str) and value.strip():
                    return f"{call.name}:{value.strip().lower()}"
        if call.name in {"file_search", "grep_search", "semantic_search"}:
            query = call.arguments.get("query")
            if isinstance(query, str) and query.strip():
                return f"{call.name}:{query.strip().lower()}"
        if call.name == "exec":
            command = self._command_for_progress(call).strip().lower()
            if command:
                return f"exec:{command}"
        return ""

    def _duplicate_discovery_error(self, call: ExecutorToolCall) -> str:
        target = ""
        for key in ("path", "filePath", "query"):
            value = call.arguments.get(key)
            if isinstance(value, str) and value.strip():
                target = value.strip()
                break
        target_hint = f" for '{target}'" if target else ""
        return (
            f"Duplicate discovery blocked: you already have a successful {call.name} result{target_hint} in this run. "
            "Use the existing result and move forward with the smallest edit or validation step instead of rereading the same seam."
        )

    def _extract_explicit_target_path(self, text: str) -> str:
        if not text:
            return ""
        match = _EXPLICIT_FILE_PATH_RE.search(text)
        if not match:
            return ""
        return str(match.group("path") or "").strip()

    def _should_block_for_explicit_target(self, call: ExecutorToolCall, explicit_target: str) -> bool:
        if call.name == "exec":
            command = self._command_for_progress(call).strip().lower()
            if not command:
                return False
            if _VALIDATION_EXEC_RE.search(command):
                return False
            if explicit_target.lower() in command:
                return False
            if _GIT_STATUS_RE.search(command):
                return True
            if _RECURSIVE_LIST_RE.search(command):
                return True
            if _DISCOVERY_EXEC_RE.search(command):
                return True
            return False
        if call.name in {"file_search", "grep_search", "semantic_search", "list_dir"}:
            return True
        if call.name in {"read", "read_file"}:
            for key in ("path", "filePath"):
                value = call.arguments.get(key)
                if isinstance(value, str) and value.strip():
                    return explicit_target.lower() != value.strip().lower()
        return False

    def _recover_from_provider_idle_timeout(
        self,
        result: LoopResult,
        *,
        ledger: ActionProgressLedger,
        partial_text: str,
        last_user_prompt: str,
    ) -> str:
        if partial_text.strip():
            return ""
        if not ledger.has_verifiable_progress():
            return ""

        successful_results = [
            tool_result
            for tool_result in result.tool_results
            if (
                str(
                    tool_result.get("status").value
                    if hasattr(tool_result.get("status"), "value")
                    else tool_result.get("status") or ""
                )
                == "success"
            )
        ]
        if not successful_results:
            return ""

        modified_paths: list[str] = []
        validation_summary = ""
        for tool_result in successful_results:
            tool_name = str(tool_result.get("tool_name") or "")
            content = str(tool_result.get("content") or "")
            if tool_name == "edit":
                match = _EDIT_RESULT_PATH_RE.search(content)
                if match:
                    modified_paths.append(match.group("path").strip())
            elif tool_name == "write":
                match = _WRITE_RESULT_PATH_RE.search(content)
                if match:
                    modified_paths.append(match.group("path").strip())
            elif tool_name == "exec" and not validation_summary:
                match = _PYTEST_PASSED_RE.search(content)
                if match:
                    validation_summary = match.group("summary").strip()

        summary_parts: list[str] = []
        if modified_paths:
            labels = [f"`{Path(path).name}`" for path in dict.fromkeys(modified_paths)]
            if len(labels) == 1:
                summary_parts.append(f"Updated {labels[0]}.")
            else:
                summary_parts.append(f"Updated {', '.join(labels[:2])}.")
        elif ledger.files_modified > 0:
            summary_parts.append("Applied the requested code change.")

        if validation_summary:
            summary_parts.append(f"Tests passed ({validation_summary}).")
        elif ledger.tests_run > 0:
            summary_parts.append("Validation completed successfully.")

        if not summary_parts:
            return ""

        prompt_lower = (last_user_prompt or "").lower()
        if "test" in prompt_lower and ledger.tests_run <= 0:
            return ""

        return " ".join(summary_parts)

    def _build_first_action_pivot_message(
        self,
        *,
        last_user_prompt: str,
        calls_by_id: dict[str, ExecutorToolCall],
        batch_results: list[ToolResult],
    ) -> str:
        target_path = ""
        for tool_result in batch_results:
            call = calls_by_id.get(tool_result.call_id)
            if call is None:
                continue
            for key in ("path", "filePath"):
                value = call.arguments.get(key)
                if isinstance(value, str) and value.strip():
                    target_path = value.strip()
                    break
            if target_path:
                break

        if target_path and self._requires_action_closure(last_user_prompt):
            target_name = Path(target_path).name or target_path
            return (
                f"You have already read `{target_name}`. "
                "Do not narrate or inspect more files. "
                f"Emit exactly one concrete tool call now. Use `edit` on `{target_path}` for the smallest requested change, "
                "and use `write` only if `edit` is impossible. "
                "If validation was requested, run one validation command after the edit."
            )

        return (
            "You have enough context for the next step. "
            "Do not narrate what you will inspect next. "
            "Emit exactly one concrete tool call now for the smallest edit or verification action."
        )

    def _apply_action_progress_blocker(
        self,
        result: LoopResult,
        ledger: ActionProgressLedger,
        *,
        last_text: str = "",
    ) -> None:
        next_required_action = (
            "Stop searching blindly; either act on the located implementation seam, run a validating check, or declare the missing file/plan/context explicitly to the user."
        )
        evidence_lines = "; ".join(ledger.evidence) if ledger.evidence else "No concrete discovery evidence captured."
        blocker_message = (
            "Blocked: action run exceeded the discovery budget without verifiable progress. "
            f"Progress ledger: discovery {ledger.discovery_steps}/{ledger.discovery_budget}, files read {ledger.files_read}, symbols found {ledger.symbols_found}, files modified {ledger.files_modified}, tests run {ledger.tests_run}, artifacts created {ledger.artifacts_created}. "
            f"Evidence: {evidence_lines} "
            f"Next required action: {next_required_action}"
        )
        result.error = blocker_message
        result.finish_reason = "action_progress_blocked"
        if last_text:
            result.text += "" if result.text.endswith(last_text) else ""
        if self.run_manager is not None and self.runtime.current_run is not None:
            self.run_manager.emit_stream_event(
                self.runtime.current_run.id,
                StreamEvent.action_progress_blocked(
                    message=blocker_message,
                    next_required_action=next_required_action,
                    **ledger.to_event_payload(),
                ),
            )

    def _apply_prompt_unanswered_blocker(
        self,
        result: LoopResult,
        ledger: ActionProgressLedger,
        *,
        last_user_prompt: str,
        assistant_text: str,
        decision: PromptClosureDecision,
    ) -> None:
        result.error = decision.blocker_message
        result.finish_reason = "prompt_unanswered"
        if self.run_manager is not None and self.runtime.current_run is not None:
            self.run_manager.emit_stream_event(
                self.runtime.current_run.id,
                StreamEvent.prompt_unanswered(
                    message=decision.blocker_message,
                    reason=decision.reason,
                    last_user_prompt=last_user_prompt,
                    assistant_text=assistant_text,
                    action_mode=ledger.action_mode,
                    files_modified=ledger.files_modified,
                    tests_run=ledger.tests_run,
                    artifacts_created=ledger.artifacts_created,
                    next_required_action=decision.next_required_action,
                ),
            )

    def _apply_governor_decision(self, decision: GovernorDecision, governor: RunGovernor, result: LoopResult) -> bool:
        if decision.message_content and decision.message_role:
            self._inject_runtime_message(
                role=decision.message_role,
                kind=MessageKind.TEXT if decision.message_kind == "text" else MessageKind.SYSTEM_EVENT,
                content=decision.message_content,
            )
        if decision.inject_capsule and decision.capsule is not None:
            self._inject_governor_capsule(governor.render_capsule(decision.capsule))
        if decision.failure_pivot is not None and self.run_manager is not None and self.runtime.current_run is not None:
            self.run_manager.emit_stream_event(
                self.runtime.current_run.id,
                StreamEvent.tool_failure_pivot(
                    decision.failure_pivot.tool_name,
                    decision.failure_pivot.repeated_pattern,
                    decision.failure_pivot.attempt_count,
                    decision.failure_pivot.pivot_hint,
                ),
            )
        if self.runtime.event_logger is not None and self.runtime.current_run is not None:
            payload: dict[str, Any] = {"snapshot": governor.snapshot()}
            if decision.inject_capsule and decision.capsule is not None:
                payload["capsule"] = {
                    "subgoal": decision.capsule.subgoal,
                    "attempt_count": decision.capsule.attempt_count,
                    "max_attempts": decision.capsule.max_attempts,
                    "failure_family": decision.capsule.failure_family,
                    "blocked_strategies": decision.capsule.blocked_strategies,
                    "facts": decision.capsule.facts,
                    "next_safe_action": decision.capsule.next_safe_action,
                    "recovery_note": decision.capsule.recovery_note,
                    "freeze_mutation": decision.capsule.freeze_mutation,
                }
            if decision.failure_pivot is not None:
                payload["failure_pivot"] = {
                    "tool_name": decision.failure_pivot.tool_name,
                    "repeated_pattern": decision.failure_pivot.repeated_pattern,
                    "attempt_count": decision.failure_pivot.attempt_count,
                    "pivot_hint": decision.failure_pivot.pivot_hint,
                }
            if decision.message_content:
                payload["message"] = {
                    "role": decision.message_role,
                    "content": decision.message_content,
                }
            self.runtime.event_logger.log(
                self.runtime.session_id,
                "run_governor_decision",
                payload,
                self.runtime.current_run.id,
            )
        if decision.terminal_reason:
            if decision.terminal_reason == "pending_action_giveup":
                result.interrupted = True
                if self.run_manager is not None and self.runtime.current_run is not None:
                    self.run_manager.emit_stream_event(
                        self.runtime.current_run.id,
                        StreamEvent.pending_action_giveup(
                            retries=int(decision.terminal_payload.get("retries", 0)),
                            last_text=str(decision.terminal_payload.get("last_text", "")),
                        ),
                    )
            elif decision.terminal_reason == "repeated_intent_blocked":
                if self.run_manager is not None and self.runtime.current_run is not None:
                    self.run_manager.emit_stream_event(
                        self.runtime.current_run.id,
                        StreamEvent.repeated_intent_blocked(
                            message=decision.terminal_error,
                            intent_signature=str(decision.terminal_payload.get("intent_signature", "")),
                            repeat_count=int(decision.terminal_payload.get("repeat_count", 0)),
                            last_text=str(decision.terminal_payload.get("last_text", "")),
                            next_required_action=str(decision.terminal_payload.get("next_required_action", "")),
                        ),
                    )
            result.error = decision.terminal_error
            result.finish_reason = decision.terminal_reason
            return True
        return False

    def _inject_runtime_message(self, *, role: str, kind: MessageKind, content: str) -> None:
        from api.agent.guard import estimate_tokens

        if not content:
            return
        recent_messages = self._recent_user_messages(limit=2) if role == "user" else self._recent_system_messages(limit=2)
        if any(content == message for message in recent_messages):
            return
        msg = Message(
            role=role,
            kind=kind,
            content=content,
            token_estimate=estimate_tokens(content),
            persistent=False,
        )
        self.runtime.add_message(msg)

    def _inject_governor_capsule(self, content: str) -> None:
        self._inject_runtime_message(role="system", kind=MessageKind.SYSTEM_EVENT, content=content)

    def _last_user_text(self) -> str:
        messages = getattr(self.runtime, "messages", None) or []
        for message in reversed(messages):
            if getattr(message, "role", None) == "user" and getattr(message, "persistent", None) is not False:
                return str(getattr(message, "content", "") or "")
        for message in reversed(messages):
            if getattr(message, "role", None) == "user":
                return str(getattr(message, "content", "") or "")
        return ""

    def _recent_user_messages(self, limit: int = 6) -> list[str]:
        messages = getattr(self.runtime, "messages", None) or []
        collected: list[str] = []
        for message in reversed(messages):
            if getattr(message, "role", None) != "user":
                continue
            collected.append(str(getattr(message, "content", "") or ""))
            if len(collected) >= limit:
                break
        return collected

    def _recent_system_messages(self, limit: int = 6) -> list[str]:
        messages = getattr(self.runtime, "messages", None) or []
        collected: list[str] = []
        for message in reversed(messages):
            if getattr(message, "role", None) != "system":
                continue
            collected.append(str(getattr(message, "content", "") or ""))
            if len(collected) >= limit:
                break
        return collected

    def _user_requested_push_only(self) -> bool:
        user_text = self._last_user_text().lower()
        if not user_text:
            return False
        if "push" not in user_text:
            return False
        blocked_intent_tokens = (
            "commit",
            "stage",
            "staged",
            "add ",
            "git add",
            "amend",
            "rebase",
            "merge",
            "reset",
            "stash",
            "tag",
        )
        return not any(token in user_text for token in blocked_intent_tokens)

    def _apply_vcs_intent_guard(self, calls: list[ExecutorToolCall]) -> tuple[list[ExecutorToolCall], list[Any]]:
        if not self._user_requested_push_only():
            return calls, []

        safe_calls: list[ExecutorToolCall] = []
        blocked_results: list[Any] = []
        for call in calls:
            if self._call_is_unrequested_git_mutation(call):
                blocked_results.append(self._blocked_unrequested_vcs_result(call))
                continue
            safe_calls.append(call)
        return safe_calls, blocked_results

    def _call_is_unrequested_git_mutation(self, call: ExecutorToolCall) -> bool:
        if call.name != "exec":
            return False
        command = call.arguments.get("command")
        if not isinstance(command, str):
            return False
        return bool(_GIT_MUTATION_RE.search(command))

    def _get_assembled_prompt_tokens(self, build_prompt: BuildPromptFn) -> int:
        raw_value = getattr(build_prompt, "last_total_tokens", 0)
        try:
            return max(0, int(raw_value or 0))
        except (TypeError, ValueError):
            return 0

    def _effective_prompt_tokens(
        self,
        assembled_prompt_tokens: int,
        provider_input_tokens: int,
    ) -> int:
        if assembled_prompt_tokens > 0 or provider_input_tokens > 0:
            return max(assembled_prompt_tokens, provider_input_tokens)

        raw_runtime_tokens = getattr(self.runtime, "token_count", 0)
        try:
            return max(0, int(raw_runtime_tokens or 0))
        except (TypeError, ValueError):
            return 0

    def _usage_pct_for_tokens(self, token_count: int) -> float:
        context_window = getattr(self.runtime, "context_window", 0)
        if not context_window:
            return 0.0
        return (token_count / context_window) * 100

    def _append_assistant_message(
        self,
        text: str,
        provider_result: ProviderResult,
    ) -> None:
        """Append assistant text to the in-memory chain without durably committing it yet."""
        from api.agent.guard import estimate_tokens

        msg = Message(
            role="assistant",
            kind=MessageKind.TEXT,
            content=text,
            token_estimate=estimate_tokens(text),
            persistent=False,
        )
        self.runtime.add_message(msg)

    def _persist_final_assistant_message(self, text: str) -> None:
        normalized = (text or "").strip()
        if not normalized:
            return

        messages = getattr(self.runtime, "messages", None) or []
        for message in reversed(messages):
            if getattr(message, "role", None) != "assistant":
                continue
            if str(getattr(message, "content", "") or "").strip() != normalized:
                continue
            if getattr(message, "persistent", None) is False:
                message.persistent = True
            break

        from api.agent.guard import estimate_tokens

        token_estimate = estimate_tokens(normalized)
        current_run = self.runtime.current_run
        if self.run_manager is not None and current_run is not None:
            self.run_manager.persist_assistant_message(
                self.runtime.session_id,
                current_run.id,
                normalized,
                token_estimate=token_estimate,
                persistent=True,
            )
            return

    def _emit_assistant_final_summary(
        self,
        *,
        status: str,
        finish_reason: str,
        final_text: str,
        transient_text: str,
    ) -> None:
        if self.run_manager is None or self.runtime.current_run is None:
            return
        if not final_text and not transient_text:
            return
        self.run_manager.emit_stream_event(
            self.runtime.current_run.id,
            StreamEvent.assistant_final(
                status=status,
                finish_reason=finish_reason,
                final_text=final_text,
                transient_text=transient_text,
                transcript_persisted=bool(final_text.strip()),
            ),
        )

    def _inject_tool_result(
        self,
        tool_result: Any,  # ToolResult from executor
    ) -> None:
        """Inject a tool execution result into the message chain as a tool message."""
        from api.agent.guard import estimate_tokens

        # Build tool result content for the model
        status = tool_result.status.value if hasattr(tool_result.status, "value") else tool_result.status
        content = json.dumps({
            "tool_id": tool_result.call_id,
            "tool_name": tool_result.tool_name,
            "status": status,
            "content": tool_result.content,
            "error": tool_result.error,
        }, ensure_ascii=False)

        msg = Message(
            role="tool",
            kind=MessageKind.TOOL_RESULT,
            content=content,
            token_estimate=estimate_tokens(content),
        )
        self.runtime.add_message(msg)
        self.runtime.complete_tool_call(tool_result.call_id)

    def _batch_has_failures(self, batch: ToolBatchResult) -> bool:
        for tool_result in batch.results:
            status = tool_result.status.value if hasattr(tool_result.status, "value") else tool_result.status
            if status not in {"success", "ask_pending"}:
                return True
        return False

    def _batch_has_serialization_failures(self, batch: ToolBatchResult) -> bool:
        for tool_result in batch.results:
            status = tool_result.status.value if hasattr(tool_result.status, "value") else tool_result.status
            if status != "validation_failed":
                continue
            detail = str(getattr(tool_result, "error", "") or getattr(tool_result, "content", "") or "").lower()
            if "malformed tool arguments" in detail or "json parse failed" in detail:
                return True
        return False

    def _batch_has_external_blocker(self, batch: ToolBatchResult) -> bool:
        return any(self._tool_result_is_external_blocker(tool_result) for tool_result in batch.results)

    def _summarize_external_blocker(self, batch: ToolBatchResult) -> str:
        for tool_result in batch.results:
            if not self._tool_result_is_external_blocker(tool_result):
                continue
            detail = (
                f"{getattr(tool_result, 'content', '') or ''}\n{getattr(tool_result, 'error', '') or ''}"
            ).strip()
            if detail:
                return detail.splitlines()[0][:300]
        return "External blocker encountered."

    def _retry_timeouts_as_background(
        self,
        batch: ToolBatchResult,
        original_calls: dict[str, ExecutorToolCall],
    ) -> ToolBatchResult:
        retry_calls: list[ExecutorToolCall] = []
        for tool_result in batch.results:
            if not self._tool_result_is_timeout(tool_result):
                continue
            original_call = original_calls.get(tool_result.call_id)
            if original_call is None:
                continue
            retry_arguments = dict(original_call.arguments)
            if retry_arguments.get("background") is True:
                continue
            if self._should_retry_timeout_interactively(original_call):
                retry_arguments["interactive"] = True
            else:
                retry_arguments["background"] = True
            retry_calls.append(
                ExecutorToolCall(
                    call_id=original_call.call_id,
                    name=original_call.name,
                    arguments=retry_arguments,
                    parse_error=original_call.parse_error,
                )
            )

        if not retry_calls:
            return batch

        retry_batch = self.executor.execute_all(
            retry_calls,
            session_id=self.runtime.session_id,
        )
        retried_by_id = {tool_result.call_id: tool_result for tool_result in retry_batch.results}
        return ToolBatchResult(
            results=[retried_by_id.get(tool_result.call_id, tool_result) for tool_result in batch.results]
        )

    def _should_retry_timeout_interactively(self, original_call: ExecutorToolCall) -> bool:
        if original_call.name != "exec":
            return False
        command = str((original_call.arguments or {}).get("command", "") or "").strip()
        if not command:
            return False
        return bool(_INTERACTIVE_TIMEOUT_EXEC_RE.search(command))

    def _tool_result_is_timeout(self, tool_result: Any) -> bool:
        status = tool_result.status.value if hasattr(tool_result.status, "value") else tool_result.status
        if status != "execution_error":
            return False
        detail = (
            f"{getattr(tool_result, 'error', '') or ''}\n{getattr(tool_result, 'content', '') or ''}"
        ).lower()
        return "timed out" in detail or "timeout" in detail

    def _tool_result_detail(self, tool_result: Any) -> str:
        return (
            f"{getattr(tool_result, 'content', '') or ''}\n{getattr(tool_result, 'error', '') or ''}"
        ).strip()

    def _tool_result_is_external_blocker(self, tool_result: Any) -> bool:
        status = tool_result.status.value if hasattr(tool_result.status, "value") else tool_result.status
        if status not in {"execution_error", "permission_denied"}:
            return False
        tool_name = str(getattr(tool_result, "tool_name", "") or "")
        if tool_name not in {"exec", "process"}:
            return False
        detail = (
            f"{getattr(tool_result, 'error', '') or ''}\n{getattr(tool_result, 'content', '') or ''}"
        ).lower()
        blocker_markers = (
            "permission denied (publickey)",
            "authentication failed",
            "repository not found",
            "api key",
            "access denied",
        )
        return any(marker in detail for marker in blocker_markers)

    def _tool_result_is_unrequested_vcs_mutation(self, tool_result: Any) -> bool:
        status = tool_result.status.value if hasattr(tool_result.status, "value") else tool_result.status
        if status not in {"validation_failed", "permission_denied"}:
            return False
        tool_name = str(getattr(tool_result, "tool_name", "") or "")
        if tool_name != "exec":
            return False
        detail = (
            f"{getattr(tool_result, 'error', '') or ''}\n{getattr(tool_result, 'content', '') or ''}"
        ).lower()
        return "push request does not authorize git mutations" in detail

    def _apply_serialization_safe_mode(self, calls: list[ExecutorToolCall]) -> tuple[list[ExecutorToolCall], list[Any]]:
        safe_calls: list[ExecutorToolCall] = []
        blocked_results: list[Any] = []

        for call in calls:
            if call.name == "write":
                safe_calls.append(self._force_base64_write_call(call))
                continue
            if call.name == "exec" and self._is_unsafe_exec_call(call):
                blocked_results.append(self._blocked_serialization_result(call))
                continue
            safe_calls.append(call)

        return safe_calls, blocked_results

    def _blocked_unrequested_vcs_result(self, call: ExecutorToolCall) -> Any:
        from api.tools.executor import ToolResult, ExecutionStatus

        command = str(call.arguments.get("command") or "").strip()
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status=ExecutionStatus.VALIDATION_FAILED,
            content="",
            error=(
                "Push request does not authorize git mutations such as staging, committing, or history edits. "
                f"Blocked command: {command}. Report the repo state and wait for explicit user approval before changing commits."
            ),
        )

    def _force_base64_write_call(self, call: ExecutorToolCall) -> ExecutorToolCall:
        content = call.arguments.get("content")
        if not isinstance(content, str):
            return call

        updated_arguments = dict(call.arguments)
        updated_arguments["content_base64"] = base64.b64encode(content.encode("utf-8")).decode("ascii")
        updated_arguments.pop("content", None)
        return ExecutorToolCall(
            call_id=call.call_id,
            name=call.name,
            arguments=updated_arguments,
            parse_error=call.parse_error,
        )

    def _is_unsafe_exec_call(self, call: ExecutorToolCall) -> bool:
        command = call.arguments.get("command")
        if not isinstance(command, str):
            return False
        stripped = command.strip()
        if not stripped:
            return False
        if "\n" in stripped or "\r" in stripped:
            return True
        if len(stripped) > 220:
            return True
        if any(token in stripped for token in ("@'", '"@', "| Out-File", "Set-Content", ">", "<<")):
            return True
        return not self._looks_like_script_runner_command(stripped)

    def _looks_like_script_runner_command(self, command: str) -> bool:
        return bool(_re.search(
            r"(?:^|\s)(?:python|py|node|pwsh|powershell|bash|sh|cmd|npm|npx)(?:\s+-m\s+[\w.:-]+|\s+run\s+[\w:-]+|\s+-File\s+\S+|\s+\S+\.(?:py|ps1|js|mjs|cjs|sh|bat|cmd))(?:\s|$)",
            command,
            _re.IGNORECASE,
        ))

    def _blocked_serialization_result(self, call: ExecutorToolCall) -> Any:
        from api.tools.executor import ToolResult, ExecutionStatus

        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status=ExecutionStatus.VALIDATION_FAILED,
            content="",
            error=(
                "Serialization-safe mode is active for this run. Raw multiline or shell-constructed exec payloads are disabled. "
                "Use a short single-line command that runs an existing script, or write the file via write with content_base64."
            ),
        )

    def _batch_has_unrequested_vcs_mutation(self, batch: ToolBatchResult) -> bool:
        return any(self._tool_result_is_unrequested_vcs_mutation(tool_result) for tool_result in batch.results)

    def _summarize_unrequested_vcs_mutation(self, batch: ToolBatchResult) -> str:
        for tool_result in reversed(batch.results):
            if self._tool_result_is_unrequested_vcs_mutation(tool_result):
                return str(getattr(tool_result, "error", "") or "Unrequested git mutation blocked.")
        return "Unrequested git mutation blocked."


    def _finalize_run(self, *, status: str, error: str | None = None, error_code: str | None = None) -> None:
        """Finalize the active run through the lifecycle authority when available."""
        current_run = self.runtime.current_run
        if current_run is None:
            return

        if self.run_manager is None:
            self.runtime.end_run(status=status, error=error)
            return

        self.runtime.persist.flush()
        current_run.status = status
        current_run.error = error

        if status == "succeeded":
            self.run_manager.succeed_run(current_run.id)
        elif status == "failed":
            self.run_manager.fail_run(current_run.id, error or "unknown")
        elif status == "blocked":
            self.run_manager.block_run(current_run.id, error or "unknown", error_code or "action_progress_blocked")
        elif status == "interrupted":
            self.run_manager.interrupt_run(current_run.id)
        else:
            self.runtime.end_run(status=status, error=error)
            return

        self.runtime.current_run = None


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def create_agent_loop(
    runtime: ConversationRuntime,
    provider: Provider,
    registry: ToolRegistry,
    *,
    config: LoopConfig | None = None,
    ask_approval=None,
    interrupt_check=None,
    max_tool_calls_per_turn: int = 10,
) -> AgentLoop:
    """Create a fully-wired AgentLoop ready to run.

    Assembles ToolExecutor and ToolCallNormalizer from the registry.
    """
    normalizer = ToolCallNormalizer(registry)

    executor = ToolExecutor(
        registry=registry,
        runtime=runtime,
        ask_approval=ask_approval,
        interrupt_check=interrupt_check,
        max_tool_calls_per_turn=max_tool_calls_per_turn,
    )

    return AgentLoop(
        runtime=runtime,
        provider=provider,
        executor=executor,
        normalizer=normalizer,
        config=config,
        run_manager=None,
    )
