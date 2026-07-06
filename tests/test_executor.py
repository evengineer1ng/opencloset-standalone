# Tests for ToolExecutor — sequential tool execution

from __future__ import annotations

import pytest
from api.tools.executor import (
    ToolExecutor,
    ToolCall,
    ToolResult,
    ToolBatchResult,
    ExecutionStatus,
    ERROR_CODE_EXEC_EXIT_NONZERO,
    ERROR_CODE_EXEC_RUNTIME_ERROR,
    ERROR_CODE_TOOL_INTERRUPTED,
    ERROR_CODE_TOOL_NOT_FOUND,
    ERROR_CODE_TOOL_PERMISSION_DENIED,
    ERROR_CODE_TOOL_VALIDATION_FAILED,
    create_default_executor,
)
from api.tools.registry import (
    ToolRegistry,
    ToolContract,
    build_tool,
    PermissionDecision,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_registry(**kwargs):
    """Create a minimal registry with some tools."""
    defaults = {
        "agent_type": "main",
        "trust_mode": "allowlist",
        "allowlist": ["echo", "read", "write", "danger"],
        "provider_capabilities": {"supports_tool_use": True},
    }
    defaults.update(kwargs)
    reg = ToolRegistry(**defaults)

    # Register test tools
    reg.register(build_tool(
        "echo",
        description="Echo input back",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
        execute=lambda args: args.get("text", ""),
        read_only=True,
        categories=["core"],
    ))

    reg.register(build_tool(
        "read",
        description="Read file",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer"},
            },
        },
        execute=lambda args: f"contents of {args.get('path', 'unknown')}",
        read_only=True,
        categories=["core"],
    ))

    reg.register(build_tool(
        "write",
        description="Write file",
        input_schema={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
        execute=lambda args: {"written": True, "path": args.get("path")},
        categories=["core"],
    ))

    reg.register(build_tool(
        "danger",
        description="Destructive action",
        input_schema={
            "type": "object",
            "required": ["target"],
            "properties": {"target": {"type": "string"}},
        },
        execute=lambda args: "deleted",
        destructive=True,
        categories=["core"],
    ))

    reg.register(build_tool(
        "serializer_test",
        description="Test custom serializer",
        input_schema={
            "type": "object",
            "properties": {"data": {"type": "string"}},
        },
        execute=lambda args: {"raw": args.get("data", "")},
        result_serializer=lambda raw: f"[SERIALIZED] {raw.get('raw', '')}",
        read_only=True,
        categories=["core"],
    ))
    reg.allowlist.add("serializer_test")

    return reg


# ---------------------------------------------------------------------------
# Single tool execution
# ---------------------------------------------------------------------------

class TestExecuteOne:
    def setup_method(self):
        self.reg = _make_registry()
        self.executor = ToolExecutor(self.reg)

    def test_success(self):
        call = ToolCall(name="echo", arguments={"text": "hello"})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.content == "hello"
        assert result.tool_name == "echo"

    def test_dict_result_serialized(self):
        call = ToolCall(name="write", arguments={"path": "/tmp/test", "content": "data"})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.SUCCESS
        assert '"written"' in result.content  # JSON serialized

    def test_custom_serializer(self):
        call = ToolCall(name="serializer_test", arguments={"data": "test_value"})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.content == "[SERIALIZED] test_value"

    def test_tool_not_found(self):
        call = ToolCall(name="nonexistent", arguments={})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.TOOL_NOT_FOUND
        assert "Unknown tool" in result.error
        assert result.error_code == ERROR_CODE_TOOL_NOT_FOUND

    def test_validation_missing_required(self):
        call = ToolCall(name="read", arguments={})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.VALIDATION_FAILED
        assert "path" in result.error
        assert result.error_code == ERROR_CODE_TOOL_VALIDATION_FAILED

    def test_validation_type_mismatch(self):
        call = ToolCall(name="read", arguments={"path": 123})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.VALIDATION_FAILED

    def test_malformed_arguments_surface_parse_error(self):
        call = ToolCall(name="read", arguments={}, parse_error="JSON parse failed: Expecting property name enclosed in double quotes")
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.VALIDATION_FAILED
        assert "Malformed tool arguments" in result.error
        assert "JSON parse failed" in result.error
        assert result.error_code == ERROR_CODE_TOOL_VALIDATION_FAILED

    def test_destructive_tool_asks(self):
        call = ToolCall(name="danger", arguments={"target": "important_file"})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.ASK_PENDING

    def test_destructive_tool_allowed_by_registry_policy(self):
        self.reg.destructive_allowlist.add("danger")
        call = ToolCall(name="danger", arguments={"target": "important_file"})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.content == "deleted"

    def test_permission_denied(self):
        # Tool registered but not in allowlist
        self.reg.register(build_tool("secret", categories=["core"], read_only=True))
        # "secret" is NOT in allowlist
        call = ToolCall(name="secret", arguments={})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.PERMISSION_DENIED
        assert result.error_code == ERROR_CODE_TOOL_PERMISSION_DENIED

    def test_tool_no_execute(self):
        self.reg.register(build_tool("stub", categories=["core"], read_only=True))
        self.reg.allowlist.add("stub")
        call = ToolCall(name="stub", arguments={})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.EXECUTION_ERROR
        assert "no execute function" in result.error

    def test_execution_error(self):
        self.reg.register(build_tool(
            "crash",
            execute=lambda args: 1 / 0,
            read_only=True,
            categories=["core"],
        ))
        self.reg.allowlist.add("crash")
        call = ToolCall(name="crash", arguments={})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.EXECUTION_ERROR
        assert "Execution error" in result.error

    def test_exec_nonzero_exit_is_execution_error(self):
        self.reg.register(build_tool(
            "exec",
            execute=lambda args: "Exit code: 1\nOutput:\nboom",
            read_only=True,
            categories=["core"],
        ))
        self.reg.allowlist.add("exec")
        call = ToolCall(name="exec", arguments={})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.EXECUTION_ERROR
        assert result.content == "Exit code: 1\nOutput:\nboom"
        assert result.error == "exit 1:\nboom"
        assert result.error_code == ERROR_CODE_EXEC_EXIT_NONZERO

    def test_process_error_string_is_execution_error(self):
        self.reg.register(build_tool(
            "process",
            execute=lambda args: "Error: Unknown process session 'abc123'",
            read_only=True,
            categories=["core"],
        ))
        self.reg.allowlist.add("process")
        call = ToolCall(name="process", arguments={})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.EXECUTION_ERROR
        assert result.error == "Error: Unknown process session 'abc123'"

    def test_exec_winerror_string_is_execution_error(self):
        self.reg.register(build_tool(
            "exec",
            execute=lambda args: "Error executing command: [WinError 2] The system cannot find the file specified",
            read_only=True,
            categories=["core"],
        ))
        self.reg.allowlist.add("exec")
        call = ToolCall(name="exec", arguments={})
        result = self.executor.execute_one(call)
        assert result.status == ExecutionStatus.EXECUTION_ERROR
        assert result.error == "Error executing command: [WinError 2] The system cannot find the file specified"
        assert result.error_code == ERROR_CODE_EXEC_RUNTIME_ERROR


# ---------------------------------------------------------------------------
# ASK approval hook
# ---------------------------------------------------------------------------

class TestAskApproval:
    def setup_method(self):
        self.reg = _make_registry()

    def test_approval_hook_allows(self):
        def approve(call, tool):
            return PermissionDecision.ALLOW

        executor = ToolExecutor(self.reg, ask_approval=approve)
        call = ToolCall(name="danger", arguments={"target": "file"})
        result = executor.execute_one(call)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.content == "deleted"

    def test_approval_hook_denies(self):
        def deny(call, tool):
            return PermissionDecision.DENY

        executor = ToolExecutor(self.reg, ask_approval=deny)
        call = ToolCall(name="danger", arguments={"target": "file"})
        result = executor.execute_one(call)
        assert result.status == ExecutionStatus.PERMISSION_DENIED

    def test_no_approval_hook_blocks(self):
        executor = ToolExecutor(self.reg)
        call = ToolCall(name="danger", arguments={"target": "file"})
        result = executor.execute_one(call)
        assert result.status == ExecutionStatus.ASK_PENDING


# ---------------------------------------------------------------------------
# Batch execution
# ---------------------------------------------------------------------------

class TestExecuteAll:
    def setup_method(self):
        self.reg = _make_registry()
        self.executor = ToolExecutor(self.reg)

    def test_empty_batch(self):
        result = self.executor.execute_all([])
        assert result.results == []
        assert result.all_succeeded is True

    def test_success_batch(self):
        calls = [
            ToolCall(name="echo", arguments={"text": "a"}),
            ToolCall(name="echo", arguments={"text": "b"}),
        ]
        result = self.executor.execute_all(calls)
        assert len(result.results) == 2
        assert result.all_succeeded is True
        assert result.success_count == 2

    def test_mixed_results(self):
        calls = [
            ToolCall(name="echo", arguments={"text": "ok"}),
            ToolCall(name="nonexistent", arguments={}),
        ]
        result = self.executor.execute_all(calls)
        assert len(result.results) == 2
        assert result.all_succeeded is False
        assert result.any_errors is True
        assert result.success_count == 1

    def test_ask_stops_batch(self):
        calls = [
            ToolCall(name="echo", arguments={"text": "ok"}),
            ToolCall(name="danger", arguments={"target": "file"}),
            ToolCall(name="echo", arguments={"text": "next"}),
        ]
        result = self.executor.execute_all(calls)
        assert len(result.results) == 2  # stops at ASK
        assert result.any_asks is True
        # Third call not executed
        assert result.results[1].status == ExecutionStatus.ASK_PENDING

    def test_max_calls_truncation(self):
        executor = ToolExecutor(self.reg, max_tool_calls_per_turn=2)
        calls = [
            ToolCall(name="echo", arguments={"text": str(i)})
            for i in range(5)
        ]
        result = executor.execute_all(calls)
        assert len(result.results) == 2  # truncated

    def test_to_dict(self):
        call = ToolCall(name="echo", arguments={"text": "hello"})
        result = self.executor.execute_one(call)
        d = result.to_dict()
        assert d["call_id"] == call.call_id
        assert d["tool_name"] == "echo"
        assert d["status"] == ExecutionStatus.SUCCESS
        assert d["error_code"] is None


# ---------------------------------------------------------------------------
# Interrupt check
# ---------------------------------------------------------------------------

class TestInterruptCheck:
    def setup_method(self):
        self.reg = _make_registry()

    def test_interrupt_stops_batch(self):
        state = {"count": 0}

        def interrupt_check():
            state["count"] += 1
            return state["count"] > 1  # interrupt after 1st call

        executor = ToolExecutor(self.reg, interrupt_check=interrupt_check)
        calls = [
            ToolCall(name="echo", arguments={"text": "one"}),
            ToolCall(name="echo", arguments={"text": "two"}),
            ToolCall(name="echo", arguments={"text": "three"}),
        ]
        result = executor.execute_all(calls)
        assert len(result.results) == 3
        assert result.results[0].status == ExecutionStatus.SUCCESS
        assert result.results[1].status == ExecutionStatus.INTERRUPTED
        assert result.results[2].status == ExecutionStatus.INTERRUPTED
        assert result.results[1].error_code == ERROR_CODE_TOOL_INTERRUPTED
        assert result.results[2].error_code == ERROR_CODE_TOOL_INTERRUPTED


# ---------------------------------------------------------------------------
# Runtime integration
# ---------------------------------------------------------------------------

class TestRuntimeIntegration:
    def setup_method(self):
        self.reg = _make_registry()

    def test_complete_tool_calls_in_runtime(self):
        # Mock runtime
        class MockRuntime:
            def __init__(self):
                self.active_tools = []
                self.is_interrupted = False

            def register_tool_call(self, tool_id):
                self.active_tools.append(tool_id)

            def complete_tool_call(self, tool_id):
                self.active_tools = [t for t in self.active_tools if t != tool_id]

        runtime = MockRuntime()
        # Pre-register tool calls
        calls = [
            ToolCall(name="echo", arguments={"text": "a"}),
            ToolCall(name="echo", arguments={"text": "b"}),
        ]
        for call in calls:
            runtime.register_tool_call(call.call_id)

        assert len(runtime.active_tools) == 2

        executor = ToolExecutor(self.reg, runtime=runtime)
        result = executor.execute_all(calls)

        # All tools should be completed
        assert len(runtime.active_tools) == 0

    def test_runtime_interrupt(self):
        class MockRuntime:
            def __init__(self):
                self.is_interrupted = False
                self.snapshot_result = {}

            def complete_tool_call(self, tool_id):
                pass

            def snapshot(self):
                return self.snapshot_result

        runtime = MockRuntime()
        runtime.is_interrupted = True

        executor = ToolExecutor(self.reg, runtime=runtime)
        calls = [ToolCall(name="echo", arguments={"text": "test"})]
        result = executor.execute_all(calls)
        assert result.results[0].status == ExecutionStatus.INTERRUPTED


# ---------------------------------------------------------------------------
# ToolBatchResult
# ---------------------------------------------------------------------------

class TestToolBatchResult:
    def test_counts(self):
        results = [
            ToolResult(call_id="1", tool_name="echo", status=ExecutionStatus.SUCCESS, content="ok"),
            ToolResult(call_id="2", tool_name="echo", status=ExecutionStatus.SUCCESS, content="ok"),
            ToolResult(call_id="3", tool_name="echo", status=ExecutionStatus.EXECUTION_ERROR, error="fail"),
        ]
        batch = ToolBatchResult(results=results)
        assert batch.completed_count == 3
        assert batch.success_count == 2
        assert batch.any_errors is True
        assert batch.all_succeeded is False

    def test_empty(self):
        batch = ToolBatchResult()
        assert batch.completed_count == 0
        assert batch.all_succeeded is True


# ---------------------------------------------------------------------------
# create_default_executor
# ---------------------------------------------------------------------------

class TestCreateDefaultExecutor:
    def test_defaults(self):
        reg = _make_registry()
        executor = create_default_executor(reg)
        assert executor.max_tool_calls_per_turn == 10
        assert executor.runtime is None
        assert executor.ask_approval is None

    def test_custom(self):
        reg = _make_registry()
        executor = create_default_executor(
            reg,
            max_tool_calls_per_turn=5,
            ask_approval=lambda c, t: PermissionDecision.ALLOW,
        )
        assert executor.max_tool_calls_per_turn == 5
        assert executor.ask_approval is not None
