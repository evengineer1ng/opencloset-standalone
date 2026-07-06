# ToolExecutor — sequential post-turn tool execution
#
# V1: execute tool calls one-by-one after the model emits them in a turn.
# Architecture designed for future StreamingToolExecutor (parallel + streaming).
#
# Flow per tool call:
#   1. Find tool contract in registry
#   2. Validate input (schema + semantic)
#   3. Check permissions (Layer 2: allow/ask/deny)
#   4. Execute tool function
#   5. Serialize result back for continuation payload
#
# Integrates with ConversationRuntime for tool-call tracking, interrupt
# handling, and message-chain validity.

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from api.tools.registry import (
    PermissionDecision,
    ToolContract,
    ToolManifest,
    ToolRegistry,
    ValidationResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured error codes
# ---------------------------------------------------------------------------

ERROR_CODE_EXEC_EXIT_NONZERO = "exec.exit_nonzero"
ERROR_CODE_EXEC_TIMEOUT = "exec.timeout"
ERROR_CODE_EXEC_RUNTIME_ERROR = "exec.runtime_error"
ERROR_CODE_PROCESS_EXIT_NONZERO = "process.exit_nonzero"
ERROR_CODE_PROCESS_TIMEOUT = "process.timeout"
ERROR_CODE_PROCESS_RUNTIME_ERROR = "process.runtime_error"
ERROR_CODE_TOOL_VALIDATION_FAILED = "tool.validation_failed"
ERROR_CODE_TOOL_NOT_FOUND = "tool.not_found"
ERROR_CODE_TOOL_PERMISSION_DENIED = "tool.permission_denied"
ERROR_CODE_TOOL_EXECUTION_ERROR = "tool.execution_error"
ERROR_CODE_TOOL_INTERRUPTED = "tool.interrupted"


# ---------------------------------------------------------------------------
# Execution status
# ---------------------------------------------------------------------------

class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    VALIDATION_FAILED = "validation_failed"
    PERMISSION_DENIED = "permission_denied"
    ASK_PENDING = "ask_pending"
    EXECUTION_ERROR = "execution_error"
    INTERRUPTED = "interrupted"
    TOOL_NOT_FOUND = "tool_not_found"


# ---------------------------------------------------------------------------
# Tool call — emitted by the model, parsed from provider output
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A tool invocation request from the model."""
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None


# ---------------------------------------------------------------------------
# Tool result — back to the model for continuation
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Result of a tool invocation, ready for continuation payload."""
    call_id: str = ""
    tool_name: str = ""
    status: str = ExecutionStatus.SUCCESS
    content: str = ""
    error: str | None = None
    error_code: str | None = None
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for DB / continuation payload."""
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "content": self.content,
            "error": self.error,
            "error_code": self.error_code,
            "token_estimate": self.token_estimate,
        }


# ---------------------------------------------------------------------------
# Batch execution result
# ---------------------------------------------------------------------------

@dataclass
class ToolBatchResult:
    """Aggregate result of executing a batch of tool calls."""
    results: list[ToolResult] = field(default_factory=list)

    @property
    def completed_count(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return sum(
            1 for r in self.results
            if r.status == ExecutionStatus.SUCCESS
        )

    @property
    def all_succeeded(self) -> bool:
        if not self.results:
            return True
        return all(r.status == ExecutionStatus.SUCCESS for r in self.results)

    @property
    def any_asks(self) -> bool:
        return any(r.status == ExecutionStatus.ASK_PENDING for r in self.results)

    @property
    def any_denied(self) -> bool:
        return any(r.status == ExecutionStatus.PERMISSION_DENIED for r in self.results)

    @property
    def any_errors(self) -> bool:
        return any(
            r.status in (ExecutionStatus.EXECUTION_ERROR, ExecutionStatus.INTERRUPTED, ExecutionStatus.TOOL_NOT_FOUND)
            for r in self.results
        )


# ---------------------------------------------------------------------------
# Callbacks (optional, set by caller)
# ---------------------------------------------------------------------------

AskApprovalCallback = Callable[[ToolCall, ToolContract], PermissionDecision]
"""Called when a tool needs user approval. Return ALLOW or DENY."""


# ---------------------------------------------------------------------------
# ToolExecutor
# ---------------------------------------------------------------------------

class ToolExecutor:
    """Sequential tool executor. V1 — tools run one-by-one after model call.

    Architecture designed for future StreamingToolExecutor:
    - execute_all() returns a batch result, not raw values
    - execute_one() is the atomic unit, swappable for concurrent version
    - callbacks are pluggable (ask approval, interrupt check)
    - result serialization uses ToolContract.result_serializer when defined

    Dependencies:
    - ToolRegistry: Layer 2 permissions, input validation, tool lookup
    - ConversationRuntime: tool-call tracking, interrupt signals, message injection
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        runtime=None,  # ConversationRuntime (optional)
        ask_approval: AskApprovalCallback | None = None,
        interrupt_check: Callable[[], bool] | None = None,
        max_tool_calls_per_turn: int = 10,
    ):
        self.registry = registry
        self.runtime = runtime
        self.ask_approval = ask_approval
        self.interrupt_check = interrupt_check
        self.max_tool_calls_per_turn = max_tool_calls_per_turn

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def execute_all(
        self,
        calls: list[ToolCall],
        *,
        session_id: str = "",
    ) -> ToolBatchResult:
        """Execute a batch of tool calls sequentially.

        Returns ToolBatchResult with per-call results, aggregate status,
        and flags for asks/denials/errors.

        Stops early if:
        - Maximum tool calls per turn reached
        - Runtime interrupt detected
        - ASK pending (blocked on user approval)
        """
        if not calls:
            return ToolBatchResult(results=[])

        # Safety cap
        if len(calls) > self.max_tool_calls_per_turn:
            logger.warning(
                "Tool call count %d exceeds max (%d); truncating batch",
                len(calls),
                self.max_tool_calls_per_turn,
            )
            calls = calls[: self.max_tool_calls_per_turn]

        results: list[ToolResult] = []

        for call in calls:
            # Interrupt check before each call
            if self._check_interrupt():
                # Record remaining calls as interrupted
                for remaining in calls[len(results):]:
                    results.append(self._interrupted_result(remaining))

                self._finalize_batch(results)
                return ToolBatchResult(results=results)

            result = self.execute_one(call, session_id=session_id)
            results.append(result)

            # ASK: blocked on user approval, stop batch
            if result.status == ExecutionStatus.ASK_PENDING:
                self._finalize_batch(results)
                return ToolBatchResult(results=results)

        self._finalize_batch(results)

        return ToolBatchResult(results=results)

    def execute_one(
        self,
        call: ToolCall,
        *,
        session_id: str = "",
    ) -> ToolResult:
        """Execute a single tool call. The atomic unit of tool execution.

        Steps:
        1. Look up tool contract in registry
        2. Validate input (schema + semantic)
        3. Check permission (Layer 2)
        4. Handle ASK (approval hook)
        5. Execute tool function
        6. Serialize result

        Returns ToolResult ready for continuation payload.
        """
        # -- Step 1: Look up tool contract --
        tool = self.registry._tools.get(call.name)
        if tool is None:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status=ExecutionStatus.TOOL_NOT_FOUND,
                content="",
                error=f"Unknown tool: {call.name}",
                error_code=ERROR_CODE_TOOL_NOT_FOUND,
            )

        if call.parse_error:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status=ExecutionStatus.VALIDATION_FAILED,
                content="",
                error=f"Malformed tool arguments: {call.parse_error}",
                error_code=ERROR_CODE_TOOL_VALIDATION_FAILED,
            )

        # -- Step 2: Validate input --
        validation = self.registry.validate_input(call.name, call.arguments)
        if not validation.ok:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status=ExecutionStatus.VALIDATION_FAILED,
                content="",
                error="; ".join(validation.errors),
                error_code=ERROR_CODE_TOOL_VALIDATION_FAILED,
            )

        # -- Step 3: Check permission --
        permission = self.registry.check_permission(
            call.name,
            input_data=call.arguments,
            session_id=session_id,
        )

        # -- Step 4: Handle permission decision --
        if permission == PermissionDecision.DENY:
            _path_keys = ("path", "file_path", "workdir", "directory")
            _denied_path = next(
                (str(call.arguments[k]) for k in _path_keys if call.arguments.get(k)),
                None,
            )
            if _denied_path:
                _error = (
                    f"Permission denied for tool '{call.name}': '{_denied_path}' is outside the "
                    f"allowed workspace. To request access, call plan_propose_change with "
                    f"proposal_type='grant_path_access' and payload={{\"path\": \"{_denied_path}\"}}."
                )
            else:
                _error = f"Permission denied for tool: {call.name}"
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status=ExecutionStatus.PERMISSION_DENIED,
                content="",
                error=_error,
                error_code=ERROR_CODE_TOOL_PERMISSION_DENIED,
            )

        if permission == PermissionDecision.ASK:
            # Try approval hook
            if self.ask_approval:
                decision = self.ask_approval(call, tool)
                if decision == PermissionDecision.DENY:
                    return ToolResult(
                        call_id=call.call_id,
                        tool_name=call.name,
                        status=ExecutionStatus.PERMISSION_DENIED,
                        content="",
                        error=f"User denied tool: {call.name}",
                        error_code=ERROR_CODE_TOOL_PERMISSION_DENIED,
                    )
                # ALLOW → proceed to execute
            else:
                # No hook → blocked, return ASK_PENDING
                return ToolResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status=ExecutionStatus.ASK_PENDING,
                    content="",
                    error=f"Approval required for tool: {call.name}",
                )

        # -- Step 5: Execute tool function --
        if tool.execute is None:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status=ExecutionStatus.EXECUTION_ERROR,
                content="",
                error=f"Tool {call.name} has no execute function",
                error_code=ERROR_CODE_TOOL_EXECUTION_ERROR,
            )

        try:
            execute_args = call.arguments
            if call.name in {"exec", "process"}:
                execute_args = dict(call.arguments)
                if self.runtime is not None:
                    runtime_session_id = getattr(self.runtime, "session_id", "")
                    current_run = getattr(self.runtime, "current_run", None)
                    if runtime_session_id:
                        execute_args.setdefault("_runtime_session_id", str(runtime_session_id))
                    if current_run is not None and getattr(current_run, "id", None):
                        execute_args.setdefault("_run_id", str(current_run.id))
            raw_result = tool.execute(execute_args)
        except Exception as e:
            error_msg = str(e) if str(e) else type(e).__name__
            logger.error("Tool %s execution failed: %s", call.name, error_msg)
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status=ExecutionStatus.EXECUTION_ERROR,
                content="",
                error=f"Execution error in {call.name}: {error_msg}",
                error_code=ERROR_CODE_TOOL_EXECUTION_ERROR,
            )

        # -- Step 6: Serialize result --
        content, token_estimate = self._serialize_result(
            raw_result, tool, call.name
        )
        status, error, error_code = self._infer_execution_status(call.name, content)

        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status=status,
            content=content,
            error=error,
            error_code=error_code,
            token_estimate=token_estimate,
        )

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _serialize_result(
        self,
        raw: Any,
        tool: ToolContract,
        tool_name: str,
    ) -> tuple[str, int]:
        """Serialize tool output to a string for the model.

        Uses tool.result_serializer if defined; otherwise stringifies raw value.
        Returns (content, token_estimate).
        """
        if tool.result_serializer is not None:
            try:
                serialized = tool.result_serializer(raw)
                if isinstance(serialized, str):
                    return serialized, self._estimate_tokens(serialized)
                # If serializer returns non-string, stringify it
                return str(serialized), self._estimate_tokens(str(serialized))
            except Exception as e:
                logger.warning("Result serializer error for %s: %s", tool_name, e)
                content = str(raw)
        elif isinstance(raw, str):
            content = raw
        elif isinstance(raw, dict):
            import json
            content = json.dumps(raw, ensure_ascii=False)
        elif raw is None:
            content = ""
        else:
            content = str(raw)

        return content, self._estimate_tokens(content)

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate for result content."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _infer_execution_status(
        self,
        tool_name: str,
        content: str,
    ) -> tuple[ExecutionStatus, str | None, str | None]:
        """Infer command-style failures that are encoded in tool output text."""
        if tool_name not in {"exec", "process"}:
            return ExecutionStatus.SUCCESS, None, None

        normalized = (content or "").strip()
        if not normalized:
            return ExecutionStatus.SUCCESS, None, None

        runtime_error_code = ERROR_CODE_EXEC_RUNTIME_ERROR if tool_name == "exec" else ERROR_CODE_PROCESS_RUNTIME_ERROR
        timeout_error_code = ERROR_CODE_EXEC_TIMEOUT if tool_name == "exec" else ERROR_CODE_PROCESS_TIMEOUT
        exit_nonzero_error_code = ERROR_CODE_EXEC_EXIT_NONZERO if tool_name == "exec" else ERROR_CODE_PROCESS_EXIT_NONZERO

        if normalized.startswith("Error:"):
            return ExecutionStatus.EXECUTION_ERROR, normalized[:600], runtime_error_code

        if normalized.startswith("Error executing command:"):
            return ExecutionStatus.EXECUTION_ERROR, normalized[:600], ERROR_CODE_EXEC_RUNTIME_ERROR

        if normalized.startswith("Error executing process:"):
            return ExecutionStatus.EXECUTION_ERROR, normalized[:600], ERROR_CODE_PROCESS_RUNTIME_ERROR

        if normalized.startswith("Command timed out after"):
            return ExecutionStatus.EXECUTION_ERROR, normalized[:600], timeout_error_code

        exit_code_match = re.search(r"^Exit code:\s*(-?\d+)", normalized, re.MULTILINE)
        if exit_code_match:
            exit_code = int(exit_code_match.group(1))
            if exit_code != 0:
                return ExecutionStatus.EXECUTION_ERROR, self._build_failure_summary(normalized, exit_code), exit_nonzero_error_code

        return_code_match = re.search(r"^return_code:\s*(-?\d+)", normalized, re.MULTILINE)
        if return_code_match:
            return_code = int(return_code_match.group(1))
            if return_code != 0:
                return ExecutionStatus.EXECUTION_ERROR, self._build_failure_summary(normalized, return_code), exit_nonzero_error_code

        return ExecutionStatus.SUCCESS, None, None

    def _build_failure_summary(self, content: str, exit_code: int) -> str:
        """Extract the actual error output from command content into a diagnostic summary."""
        lines = content.splitlines()
        output_idx = next(
            (i + 1 for i, ln in enumerate(lines) if ln.strip().lower().startswith("output:")),
            None,
        )
        if output_idx is not None:
            output_lines = [ln for ln in lines[output_idx:] if ln.strip()]
        else:
            skip = ("exit code:", "return_code:", "elapsed:", "output:", "process ")
            output_lines = [
                ln for ln in lines
                if ln.strip() and not any(ln.lower().startswith(p) for p in skip)
            ]
        if output_lines:
            tail = "\n".join(output_lines[-10:])
            if len(tail) > 600:
                tail = "..." + tail[-597:]
            return f"exit {exit_code}:\n{tail}"
        return f"exit {exit_code} — no stdout/stderr captured"

    def _check_interrupt(self) -> bool:
        """Check if execution should be interrupted."""
        if self.interrupt_check and self.interrupt_check():
            return True
        if self.runtime and self.runtime.is_interrupted:
            return True
        return False

    def _interrupted_result(self, call: ToolCall) -> ToolResult:
        """Create a ToolResult for a call that was interrupted."""
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status=ExecutionStatus.INTERRUPTED,
            content="",
            error="Run interrupted before tool completed",
            error_code=ERROR_CODE_TOOL_INTERRUPTED,
        )

    def _finalize_batch(self, results: list[ToolResult]) -> None:
        """Post-batch cleanup: update runtime tracking."""
        if not self.runtime:
            return

        for result in results:
            self.runtime.complete_tool_call(result.call_id)

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Executor state for diagnostics."""
        return {
            "registry_tools": len(self.registry.all_tools),
            "max_tool_calls_per_turn": self.max_tool_calls_per_turn,
            "runtime": self.runtime.snapshot() if self.runtime else None,
        }


# ---------------------------------------------------------------------------
# Convenience: create default executor
# ---------------------------------------------------------------------------

def create_default_executor(
    registry: ToolRegistry,
    *,
    runtime=None,
    ask_approval: AskApprovalCallback | None = None,
    interrupt_check: Callable[[], bool] | None = None,
    max_tool_calls_per_turn: int = 10,
) -> ToolExecutor:
    """Create a ToolExecutor with standard configuration."""
    return ToolExecutor(
        registry=registry,
        runtime=runtime,
        ask_approval=ask_approval,
        interrupt_check=interrupt_check,
        max_tool_calls_per_turn=max_tool_calls_per_turn,
    )
