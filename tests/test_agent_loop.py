# Tests for AgentLoop — provider → normalize → execute → continue

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
from api.agent.engine import ConversationRuntime, Message, MessageKind
from api.api.events import StreamEvent
from api.agent.loop import (
    AgentLoop,
    LoopConfig,
    LoopResult,
    create_agent_loop,
)
from api.provider.base import (
    Provider,
    ProviderEvent,
    ProviderEventType,
    ProviderResult,
    ToolCall as ProviderToolCall,
)
from api.tools.executor import ExecutionStatus, ToolExecutor, ToolCall as ExecutorToolCall, ToolResult
from api.tools.normalizer import ToolCallNormalizer
from api.tools.registry import ToolRegistry, build_tool


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

class MockProvider(Provider):
    """Provider that yields pre-programmed events.

    Overrides both run_stream and run so call_count is incremented exactly once.
    """

    def __init__(self, events: list[ProviderEvent]):
        self._events = events
        self.call_count = 0

    def _collect_events(self) -> ProviderResult:
        result = ProviderResult()
        for ev in self._events:
            if ev.type == ProviderEventType.TEXT_DELTA:
                result.text += ev.text or ""
            elif ev.type == ProviderEventType.THINKING_DELTA:
                result.thinking += ev.text or ""
            elif ev.type == ProviderEventType.TOOL_USE:
                if ev.tool_call:
                    result.tool_calls.append(ev.tool_call)
            elif ev.type == ProviderEventType.USAGE:
                result.input_tokens = ev.input_tokens or 0
                result.output_tokens = ev.output_tokens or 0
                result.finish_reason = ev.finish_reason or "stop"
        return result

    def run_stream(self, messages, **kwargs):
        self.call_count += 1
        yield from self._events

    def run(self, messages, **kwargs):
        return self._collect_events()


class CrashingProvider(Provider):
    def __init__(self, events: list[ProviderEvent], error_message: str = "Response ended prematurely"):
        self._events = events
        self.error_message = error_message

    def run_stream(self, messages, **kwargs):
        for ev in self._events:
            yield ev
        raise RuntimeError(self.error_message)


def _make_registry():
    reg = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["echo"],
        provider_capabilities={"supports_tool_use": True},
    )
    reg.register(build_tool(
        "echo",
        description="Echo text back",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
        execute=lambda args: args.get("text", ""),
        read_only=True,
        categories=["core"],
    ))
    return reg


def _make_discovery_registry():
    reg = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["file_search", "read_file", "grep_search"],
        provider_capabilities={"supports_tool_use": True},
    )
    reg.register(build_tool(
        "file_search",
        description="Find files",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        execute=lambda args: "",
        read_only=True,
        categories=["core"],
    ))
    reg.register(build_tool(
        "read_file",
        description="Read a file",
        input_schema={
            "type": "object",
            "required": ["filePath"],
            "properties": {"filePath": {"type": "string"}},
        },
        execute=lambda args: "",
        read_only=True,
        categories=["core"],
    ))
    reg.register(build_tool(
        "grep_search",
        description="Search text",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        execute=lambda args: "title_generation\nsession_update_title",
        read_only=True,
        categories=["core"],
    ))
    return reg


def _make_exec_error_registry():
    reg = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["exec"],
        provider_capabilities={"supports_tool_use": True},
    )
    reg.register(build_tool(
        "exec",
        description="Run a command",
        input_schema={
            "type": "object",
            "required": ["command"],
            "properties": {"command": {"type": "string"}},
        },
        execute=lambda args: "Exit code: 1\nOutput:\nboom",
        categories=["core"],
    ))
    return reg


def _make_exec_timeout_registry():
    reg = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["exec"],
        provider_capabilities={"supports_tool_use": True},
    )
    def _run_exec(args):
        if args.get("background") is True:
            return "Started background session: proc_123"
        return "Command timed out after 300s.\ncommand: scp -r stuff\nFor long-running work, use background=True."
    reg.register(build_tool(
        "exec",
        description="Run a command",
        input_schema={
            "type": "object",
            "required": ["command"],
            "properties": {"command": {"type": "string"}},
        },
        execute=_run_exec,
        categories=["core"],
    ))
    return reg


def _make_exec_validation_registry():
    reg = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["exec"],
        provider_capabilities={"supports_tool_use": True},
    )
    reg.register(build_tool(
        "exec",
        description="Run a command",
        input_schema={
            "type": "object",
            "required": ["command"],
            "properties": {"command": {"type": "string"}},
        },
        execute=lambda args: "should not run",
        categories=["core"],
    ))
    return reg


def _make_mock_runtime():
    """Create a mock ConversationRuntime for loop tests."""
    runtime = MagicMock(spec=ConversationRuntime)
    runtime.session_id = "test-session-001"
    runtime.is_interrupted = False
    runtime.is_paused = False
    runtime.usage_pct = 0.0
    runtime.transcript = None
    runtime.messages = []
    runtime.current_run = None
    runtime.event_logger = None
    runtime.persist = MagicMock()
    runtime.persist.flush = MagicMock()

    def mock_begin_run(*, existing_run_id=None, turn_number=None):
        run = MagicMock()
        run.id = existing_run_id or "test-run-001"
        run.turn_number = turn_number or 1
        runtime.current_run = run
        return run

    runtime.begin_run = MagicMock(side_effect=mock_begin_run)
    runtime.end_run = MagicMock()
    def _mock_request_interrupt():
        runtime.is_interrupted = True
    runtime.request_interrupt = MagicMock(side_effect=_mock_request_interrupt)

    def _mock_add_message(msg, *, run_id=None):
        runtime.messages.append(msg)
        return msg

    runtime.add_message = MagicMock(side_effect=_mock_add_message)
    runtime.pause = MagicMock()
    runtime.register_tool_call = MagicMock()
    return runtime


# ---------------------------------------------------------------------------
# Loop: text-only response (no tool calls)
# ---------------------------------------------------------------------------

class TestTextOnlyResponse:
    def test_text_only_completes(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Hello "),
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="world."),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=10,
                output_tokens=5,
                finish_reason="stop",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        def build_prompt():
            return [], None

        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(build_prompt)

        assert result.text == "Hello world."
        assert result.turn_count == 1
        assert result.finish_reason == "stop"
        assert result.input_tokens == 10
        assert result.output_tokens == 5
        assert result.tool_results == []
        assert not result.interrupted

    def test_existing_run_id_is_adopted(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Hello."),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=4,
                output_tokens=2,
                finish_reason="stop",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        loop = AgentLoop(runtime, provider, executor, normalizer)
        loop.run(lambda: ([], None), existing_run_id="queued-run-1", existing_turn_number=3)

        runtime.begin_run.assert_called_once_with(existing_run_id="queued-run-1", turn_number=3)

    def test_success_delegates_to_run_manager(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Hello."),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=4,
                output_tokens=2,
                finish_reason="stop",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        run_manager = MagicMock()

        loop = AgentLoop(runtime, provider, executor, normalizer, run_manager=run_manager)
        loop.run(lambda: ([], None))

        run_manager.succeed_run.assert_called_once_with("test-run-001")
        runtime.end_run.assert_not_called()

    def test_pause_threshold_interrupts_run(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Hello."),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=84,
                output_tokens=2,
                finish_reason="stop",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        runtime.context_window = 100
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        loop = AgentLoop(runtime, provider, executor, normalizer, config=LoopConfig(pause_threshold_pct=85.0))

        def build_prompt():
            build_prompt.last_total_tokens = 90
            return [], None

        result = loop.run(build_prompt)

        runtime.pause.assert_called_once_with()
        assert result.interrupted is True
        assert result.finish_reason == "rollover_threshold"

    def test_pause_threshold_ignores_historical_runtime_usage_when_prompt_is_small(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Hello."),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=20,
                output_tokens=2,
                finish_reason="stop",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        runtime.context_window = 100
        runtime.token_count = 500
        runtime.usage_pct = 500.0
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        loop = AgentLoop(runtime, provider, executor, normalizer, config=LoopConfig(pause_threshold_pct=85.0))

        def build_prompt():
            build_prompt.last_total_tokens = 20
            return [], None

        result = loop.run(build_prompt)

        runtime.pause.assert_not_called()
        assert result.interrupted is False
        assert result.finish_reason == "stop"

    def test_pause_threshold_can_be_disabled(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Hello."),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=95,
                output_tokens=2,
                finish_reason="stop",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        runtime.context_window = 100
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        loop = AgentLoop(runtime, provider, executor, normalizer, config=LoopConfig(pause_threshold_pct=None))

        def build_prompt():
            build_prompt.last_total_tokens = 99
            return [], None

        result = loop.run(build_prompt)

        runtime.pause.assert_not_called()
        assert result.interrupted is False
        assert result.finish_reason == "stop"

    def test_fresh_action_narration_triggers_nudge_before_any_tool_results(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="I'll verify the SSH connection first, then pull everything down.",
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=6,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="echo",
                        arguments='{"text": "verified"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert len(result.tool_results) == 1
        assert result.finish_reason != "pending_action_without_tool_call"
        assert result.error == ""
        coaching_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(coaching_messages) == 1
        assert "tool call" in coaching_messages[0].lower()

    def test_first_discovery_batch_injects_targeted_edit_pivot_for_file_action(self):
        registry = _make_discovery_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(
            Message(role="user", kind=MessageKind.TEXT, content="Update task_app.py and run the tests.")
        )
        target_path = r"D:\tmp\task_app.py"

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_read",
                        name="read_file",
                        arguments=json.dumps({"filePath": target_path}),
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=5,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=3,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        loop.run(lambda: ([], None))

        coaching_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert any("Use `edit` on" in message for message in coaching_messages)
        assert any(target_path in message for message in coaching_messages)

    def test_progress_update_without_tool_call_triggers_nudge(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="Starting the rsync mirror. This will take a minute or two for ~276 MB:",
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=6,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="echo",
                        arguments='{"text": "mirroring"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Mirror started."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert len(result.tool_results) == 1
        assert result.finish_reason != "pending_action_without_tool_call"
        assert result.error == ""
        coaching_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(coaching_messages) == 1
        assert "tool call" in coaching_messages[0].lower()

    def test_mixed_report_then_next_action_triggers_nudge(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text=(
                        "Here's what's on the server (~276 MB total):\n\n"
                        "| Directory | Size | Contents |\n"
                        "|---|---|---|\n"
                        "| spaybot/ | 165 MB | Trading bot source |\n\n"
                        "Starting the rsync mirror. This will take a minute or two for ~276 MB:"
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=6,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="echo",
                        arguments='{"text": "mirror-started"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Mirror started."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert len(result.tool_results) == 1
        assert result.finish_reason != "pending_action_without_tool_call"
        assert result.error == ""
        assert "Starting the rsync mirror." in result.text
        coaching_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(coaching_messages) == 1
        assert "tool call" in coaching_messages[0].lower()

    def test_repeated_fresh_action_narration_stalls_honestly(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="Let me check the SSH connection first.",
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=6,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="I'll pull the files down now.",
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=6,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(governor_max_pending_action_attempts=1),
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert result.interrupted is True
        assert result.finish_reason == "pending_action_giveup"
        assert result.error in (None, "")
        coaching_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(coaching_messages) == 1
        assert "tool call" in coaching_messages[0].lower()

    def test_bare_progressive_action_after_tool_result_stalls_honestly(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="echo",
                        arguments='{"text": "checked"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="Status confirmed. Now pushing to origin.",
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=5,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(governor_max_pending_action_attempts=0),
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert len(result.tool_results) == 1
        assert result.finish_reason == "pending_action_giveup"
        assert result.interrupted is True
        assert result.error in (None, "")
        assert result.text.endswith("Now pushing to origin.")

    def test_push_only_request_blocks_unrequested_git_commit(self):
        registry = _make_exec_validation_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages = [
            Message(role="user", content="git push this repo"),
        ]

        events = [
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name="exec",
                    arguments='{"command": "git commit -m \\"Update agent loop\\"", "workdir": "D:\\\\openclaw\\\\opencloset"}',
                ),
            ),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=10,
                output_tokens=4,
                finish_reason="tool_calls",
            ),
        ]

        provider = MockProvider(events)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "unrequested_vcs_mutation"
        assert "Push request does not authorize git mutations" in result.error
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["status"] == "validation_failed"
        assert "git commit" in (result.tool_results[0]["error"] or "")


# ---------------------------------------------------------------------------
# Loop: tool call → execute → continue
# ---------------------------------------------------------------------------

class TestToolCallLoop:
    def test_single_tool_call_then_stop(self):
        """Provider emits text + tool call on turn 1, then text only on turn 2."""
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        turn_count = [0]

        def build_prompt():
            turn_count[0] += 1
            return [], None

        # Turn 1: text + tool call
        events_turn1 = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Calling tool..."),
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name="echo",
                    arguments='{"text": "test"}',
                ),
            ),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=20,
                output_tokens=10,
                finish_reason="tool_calls",
            ),
        ]

        # Turn 2: text only (continuation after tool result)
        events_turn2 = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=" Got result."),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=30,
                output_tokens=8,
                finish_reason="stop",
            ),
        ]

        class MultiTurnProvider(Provider):
            def __init__(self):
                self.call_count = 0
            def run_stream(self, messages, **kwargs):
                self.call_count += 1
                events = events_turn1 if turn_count[0] == 1 else events_turn2
                for event in events:
                    yield event
            def run(self, messages, **kwargs):
                events = events_turn1 if turn_count[0] == 1 else events_turn2
                return MockProvider(events)._collect_events()

        provider = MultiTurnProvider()
        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(build_prompt)

        assert turn_count[0] == 2  # two iterations
        assert "Calling tool..." in result.text
        assert "Got result." in result.text
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["tool_name"] == "echo"
        assert result.finish_reason == "stop"

    def test_completed_tool_batch_streams_tool_result(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        run_manager = MagicMock()

        turn_count = [0]

        def build_prompt():
            turn_count[0] += 1
            return [], None

        events_turn1 = [
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name="echo",
                    arguments='{"text": "test"}',
                ),
            ),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=20,
                output_tokens=10,
                finish_reason="tool_calls",
            ),
        ]
        events_turn2 = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="done"),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=10,
                output_tokens=5,
                finish_reason="stop",
            ),
        ]

        class MultiTurnProvider(Provider):
            def run_stream(self, messages, **kwargs):
                events = events_turn1 if turn_count[0] == 1 else events_turn2
                for event in events:
                    yield event

        provider = MultiTurnProvider()
        loop = AgentLoop(runtime, provider, executor, normalizer, run_manager=run_manager)
        result = loop.run(build_prompt)

        assert result.finish_reason == "stop"
        run_manager.stream_tool_use.assert_called_once()
        run_manager.stream_tool_result.assert_called_once_with(
            "test-run-001",
            "call_1",
            "echo",
            "success",
            "test",
            None,
            None,
        )

    def test_no_tools_means_one_iteration(self):
        """If provider never emits tool calls, loop runs exactly once."""
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=5,
                output_tokens=3,
                finish_reason="stop",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        prompt_calls = [0]
        def build_prompt():
            prompt_calls[0] += 1
            return [], None

        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(build_prompt)

        assert prompt_calls[0] == 1
        assert result.turn_count == 1

    def test_xml_exec_text_is_intercepted_as_a_real_tool_call(self):
        registry = _make_exec_validation_registry()
        registry._tools["exec"].execute = lambda args: f"ran: {args['command']}"
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="<exec>dir</exec>"),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert result.tool_results[0]["tool_name"] == "exec"
        assert result.tool_results[0]["content"] == "ran: dir"
        assert result.text == "Done."

    def test_mixed_narration_and_xml_exec_still_executes_tool_call(self):
        registry = _make_exec_validation_registry()
        registry._tools["exec"].execute = lambda args: f"ran: {args['command']}"
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text=(
                        "I'll inspect the repo and summarize the findings.\n\n"
                        "<exec>Get-ChildItem -Path \"D:\\openclaw\\opencloset\" -Recurse -Include \"*eval*\" -File | Select-Object FullName</exec>"
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=18,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="I found the eval files and can summarize them now."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=8,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["tool_name"] == "exec"
        assert "Get-ChildItem" in result.tool_results[0]["content"]
        assert "I'll inspect the repo and summarize the findings." in result.text
        assert result.text.endswith("I found the eval files and can summarize them now.")

    def test_placeholder_xml_exec_text_is_not_executed_and_retries_for_real_command(self):
        registry = _make_exec_validation_registry()
        registry._tools["exec"].execute = lambda args: f"ran: {args['command']}"
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text=(
                        "You described an action but did not generate a tool call. "
                        "Execute it now using the XML fallback — do not narrate further:\n"
                        "<exec>your command here</exec>\n"
                        "Replace the placeholder with the exact command."
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=8,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="<exec>git status -sb</exec>"),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=3,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["tool_name"] == "exec"
        assert result.tool_results[0]["content"] == "ran: git status -sb"
        assert result.text == "Done."
        assert all(tool_result["content"] != "ran: your command here" for tool_result in result.tool_results)

    def test_placeholder_xml_exec_text_counts_as_pending_action_when_retries_disabled(self):
        registry = _make_exec_validation_registry()
        registry._tools["exec"].execute = lambda args: f"ran: {args['command']}"
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="<exec>your command here</exec>"),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(governor_max_pending_action_attempts=0),
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 1
        assert result.finish_reason == "pending_action_giveup"
        assert result.interrupted is True
        assert result.error in (None, "")
        assert result.tool_results == []
        assert result.text == ""

    def test_attribute_style_exec_xml_executes_as_real_tool_call(self):
        registry = _make_exec_validation_registry()
        registry._tools["exec"].execute = lambda args: f"ran: {args['command']}"
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text='<exec command="git rm --cached opencloset/handoff_next_session.md" workdir="D:\\openclaw">',
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=3,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert result.error == ""
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["tool_name"] == "exec"
        assert result.tool_results[0]["content"] == "ran: git rm --cached opencloset/handoff_next_session.md"
        assert result.text == "Done."

    def test_stream_hides_embedded_think_and_xml_markup(self):
        registry = _make_exec_validation_registry()
        registry._tools["exec"].execute = lambda args: f"ran: {args['command']}"
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        run_manager = MagicMock()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="Loader written. <think>checking loader contents</think>",
                ),
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text='<exec command="git status -sb" workdir="D:\\openclaw">',
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=5,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=3,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer, run_manager=run_manager)

        result = loop.run(lambda: ([], None))

        streamed_text = "".join(call.args[1] for call in run_manager.stream_text_delta.call_args_list)
        streamed_thinking = "".join(call.args[1] for call in run_manager.stream_thinking_delta.call_args_list)

        assert result.text == "Loader written. Done."
        assert streamed_text == "Loader written. Done."
        assert "<think>" not in streamed_text
        assert "<exec" not in streamed_text
        assert streamed_thinking == "checking loader contents"

    def test_pending_action_coaching_is_internal_only(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Let me finish this now."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=3,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        loop.run(lambda: ([], None))

        internal_messages = [message for message in runtime.messages if getattr(message, "content", "").startswith("You described an action")]
        assert len(internal_messages) == 1
        assert internal_messages[0].persistent is False
        assert "Do not use XML attributes like command= or workdir=" in internal_messages[0].content

    def test_empty_think_only_text_is_not_persisted_before_tool_continuation(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="<think>\n\n</think>\n\n"),
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="echo",
                        arguments='{"text": "ok"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(lambda: ([], None))

        assistant_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "assistant"
        ]
        assert assistant_messages == ["Done."]
        assert result.text == "Done."

    def test_pending_action_with_no_retries_stops_before_turn_budget_exhaustion(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="echo",
                        arguments='{"text": "one"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="echo",
                        arguments='{"text": "two"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="Let me try a different approach.",
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=6,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_3",
                        name="echo",
                        arguments='{"text": "three"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=13,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="Let me try another command.",
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=14,
                    output_tokens=6,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Does that help?"),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=15,
                    output_tokens=5,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(max_turns=4, governor_max_pending_action_attempts=0),
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert result.turn_count == 3
        assert result.finish_reason == "pending_action_giveup"
        assert result.error in (None, "")
        assert result.interrupted is True
        assert len(result.tool_results) == 2

    def test_repeated_tool_failures_inject_recovery_nudge(self):
        registry = _make_exec_error_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments='{"command": "bad one"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="exec",
                        arguments='{"command": "bad two"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="What should I do next?"),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=5,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(max_consecutive_failure_batches=2),
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert len(result.tool_results) == 2
        assert result.tool_results[0]["status"] == "execution_error"
        assert result.interrupted is False
        assert result.error == "Consecutive failure batches exceeded."
        assert result.finish_reason == "consecutive_failures_exceeded"
        recovery_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(recovery_messages) == 1
        assert "tool failure" in recovery_messages[0].lower() or "failed" in recovery_messages[0].lower()

    def test_repeated_sqlite_failures_inject_governor_capsule(self):
        registry = _make_exec_error_registry()

        def _run_exec(args):
            return 'Exit code: 1\nOutput:\nError: near "tables": syntax error'

        registry._tools["exec"].execute = _run_exec
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments='{"command": "sqlite-utils hockey_lab.sqlite tables"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="exec",
                        arguments='{"command": "sqlite-utils hockey_lab.sqlite tables"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="I should switch methods."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(max_consecutive_failure_batches=4, governor_max_recovery_attempts=3),
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert result.finish_reason == "stop"
        system_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "system"
        ]
        assert len(system_messages) == 1
        assert "Run Governor Recovery Capsule" in system_messages[0]
        assert "inspect_sqlite_db" in system_messages[0]
        assert "Python's built-in sqlite3 module" in system_messages[0]

    def test_repeated_sqlite_failures_emit_tool_failure_pivot_stream_event(self):
        registry = _make_exec_error_registry()

        def _run_exec(args):
            return 'Exit code: 1\nOutput:\nError: near "tables": syntax error'

        registry._tools["exec"].execute = _run_exec
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        run_manager = MagicMock()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments='{"command": "sqlite-utils hockey_lab.sqlite tables"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="exec",
                        arguments='{"command": "sqlite-utils hockey_lab.sqlite tables"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="I should switch methods."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(max_consecutive_failure_batches=4, governor_max_recovery_attempts=3),
            run_manager=run_manager,
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"
        pivot_call = run_manager.emit_stream_event.call_args_list[0]
        assert pivot_call.args[0] == "test-run-001"
        pivot_event = pivot_call.args[1]
        assert pivot_event.type == "tool_failure_pivot"
        assert pivot_event.data["tool_name"] == "exec"
        assert pivot_event.data["repeated_pattern"] == "inspect_sqlite_db:sqlite_utils_cli:sqlite-utils:near_tables_syntax"
        assert pivot_event.data["attempt_count"] == 2
        assert "Python's built-in sqlite3 module" in pivot_event.data["pivot_hint"]

    def test_host_key_failure_injects_fix_prompt_and_allows_retry(self):
        registry = _make_exec_error_registry()
        push_attempts = {"count": 0}

        def _run_exec(args):
            command = args.get("command", "")
            if "ssh-keyscan github.com" in command:
                return "Exit code: 0\nElapsed: 0.10s\nOutput:\nAdded github.com to known_hosts"
            if "git push origin master" in command:
                push_attempts["count"] += 1
                if push_attempts["count"] == 1:
                    return (
                        "Exit code: 1\nOutput:\nHost key verification failed.\n"
                        "fatal: Could not read from remote repository.\n"
                        "Please make sure you have the correct access rights\n"
                    )
                return "Exit code: 0\nElapsed: 0.50s\nOutput:\nEverything up-to-date"
            return "Exit code: 1\nOutput:\nunexpected command"

        registry._tools["exec"].execute = _run_exec
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Trying push."),
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments='{"command": "git push origin master"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="exec",
                        arguments='{"command": "ssh-keyscan github.com >> ~/.ssh/known_hosts"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_3",
                        name="exec",
                        arguments='{"command": "git push origin master"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Push completed."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=13,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(lambda: ([], None))

        assert provider.call_count == 4
        assert result.interrupted is False
        assert result.error == ""
        assert result.finish_reason == "stop"
        assert len(result.tool_results) == 3
        assert result.tool_results[0]["status"] == "execution_error"
        assert result.tool_results[1]["status"] == "success"
        assert result.tool_results[2]["status"] == "success"
        recovery_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(recovery_messages) == 1
        assert "ssh-keyscan github.com" in recovery_messages[0]
        assert "retry the same git push" in recovery_messages[0].lower()

    def test_missing_sqlite_cli_injects_install_guidance(self):
        registry = _make_exec_error_registry()

        def _run_exec(args):
            command = args.get("command", "")
            if "sqlite3" in command:
                return "Error executing command: [WinError 2] The system cannot find the file specified: sqlite3"
            return "Exit code: 1\nOutput:\nunexpected command"

        registry._tools["exec"].execute = _run_exec
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_sqlite_1",
                        name="exec",
                        arguments='{"command": "sqlite3 hockey_lab.sqlite \"select 1;\""}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="retrying"),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=2,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(max_consecutive_failure_batches=2),
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"
        recovery_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(recovery_messages) == 1
        assert "sqlite cli is missing" in recovery_messages[0].lower()
        assert "winget install --exact --id sqlite.sqlite" in recovery_messages[0].lower()
        assert "do not replace this with a one-off python sqlite script" in recovery_messages[0].lower()

    def test_repeated_host_key_failure_escalates_to_ssh_debug_prompt(self):
        registry = _make_exec_error_registry()
        push_attempts = {"count": 0}

        def _run_exec(args):
            command = args.get("command", "")
            if "ssh-keyscan github.com" in command:
                return "Exit code: 0\nElapsed: 0.10s\nOutput:\nAdded github.com to known_hosts"
            if "ssh -vT git@github.com" in command:
                return "Exit code: 0\nElapsed: 0.15s\nOutput:\ndebug probe"
            if "git push origin master" in command:
                push_attempts["count"] += 1
                if push_attempts["count"] <= 2:
                    return (
                        "Exit code: 1\nOutput:\nHost key verification failed.\n"
                        "fatal: Could not read from remote repository.\n"
                        "Please make sure you have the correct access rights\n"
                    )
                return "Exit code: 0\nElapsed: 0.50s\nOutput:\nEverything up-to-date"
            return "Exit code: 1\nOutput:\nunexpected command"

        registry._tools["exec"].execute = _run_exec
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments='{"command": "git push origin master"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="exec",
                        arguments='{"command": "ssh-keyscan github.com >> ~/.ssh/known_hosts"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_3",
                        name="exec",
                        arguments='{"command": "git push origin master"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_4",
                        name="exec",
                        arguments='{"command": "ssh -vT git@github.com"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=13,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_5",
                        name="exec",
                        arguments='{"command": "git push origin master"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=14,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Push completed."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=15,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(lambda: ([], None))

        assert provider.call_count == 6
        assert result.finish_reason == "stop"
        assert result.error == ""
        recovery_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(recovery_messages) >= 1
        assert "ssh-keyscan github.com" in recovery_messages[0]

    def test_pending_action_after_tool_result_fails_instead_of_succeeding(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="echo",
                        arguments='{"text": "ok"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Good. Now let me commit and push."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(governor_max_pending_action_attempts=0),
        )
        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert result.finish_reason == "pending_action_giveup"
        assert result.interrupted is True
        assert result.error in (None, "")
        assert result.text.endswith("Good. Now let me commit and push.")

    def test_pending_action_giveup_emits_stream_event_and_interrupts_run(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        run_manager = MagicMock()

        provider_turns = [
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Now pushing to origin."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(governor_max_pending_action_attempts=0),
            run_manager=run_manager,
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "pending_action_giveup"
        assert result.interrupted is True
        run_manager.emit_stream_event.assert_called_once_with(
            "test-run-001",
            StreamEvent.pending_action_giveup(1, "Now pushing to origin."),
        )
        run_manager.interrupt_run.assert_called_once_with("test-run-001")

    def test_repeated_intent_blocks_earlier_than_generic_pending_action_giveup(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        run_manager = MagicMock()

        provider_turns = [
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Now pushing to origin."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Trying to push again."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(governor_max_pending_action_attempts=3, governor_repeated_intent_threshold=2),
            run_manager=run_manager,
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert result.finish_reason == "repeated_intent_blocked"
        assert result.interrupted is False
        assert result.error.startswith("Blocked: run kept restating the same planned action")
        run_manager.emit_stream_event.assert_any_call(
            "test-run-001",
            StreamEvent.repeated_intent_blocked(
                message=result.error,
                intent_signature="push",
                repeat_count=2,
                last_text="Trying to push again.",
                next_required_action="Stop restating the same planned action. Either emit the concrete next tool call now, or state the exact blocker or missing input instead of repeating intent.",
            ),
        )
        run_manager.block_run.assert_called_once_with(
            "test-run-001",
            result.error,
            "repeated_intent_blocked",
        )

    def test_action_mode_discovery_only_run_fails_with_typed_blocker(self):
        registry = _make_discovery_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(Message(role="user", kind=MessageKind.TEXT, content="get the auto titling working", token_estimate=6))
        run_manager = MagicMock()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_1", name="file_search", arguments='{"query": "app.py"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_2", name="read_file", arguments='{"filePath": "api/api/app.py"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_3", name="grep_search", arguments='{"query": "title generation"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=12, output_tokens=4, finish_reason="tool_calls"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(action_discovery_budget=3),
            run_manager=run_manager,
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert result.finish_reason == "action_progress_blocked"
        assert result.error.startswith("Blocked: action run exceeded the discovery budget")
        assert "files read 1" in result.error
        assert "symbols found 2" in result.error
        assert "file_search on app.py" in result.error
        assert "declare the missing file/plan/context explicitly" in result.error
        run_manager.emit_stream_event.assert_any_call(
            "test-run-001",
            StreamEvent.action_progress_blocked(
                message=result.error,
                discovery_steps=3,
                discovery_budget=3,
                files_read=1,
                symbols_found=2,
                files_modified=0,
                tests_run=0,
                artifacts_created=0,
                evidence=[
                    "file_search on app.py -> success",
                    "read_file on api/api/app.py -> success",
                    "grep_search on title generation -> success: title_generation session_update_title",
                ],
                next_required_action="Stop searching blindly; either act on the located implementation seam, run a validating check, or declare the missing file/plan/context explicitly to the user.",
            ),
        )
        run_manager.block_run.assert_called_once_with(
            "test-run-001",
            result.error,
            "action_progress_blocked",
        )

    def test_action_mode_transient_progress_without_answer_blocks_and_stays_nonpersistent(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(Message(role="user", kind=MessageKind.TEXT, content="fix the report", token_estimate=4))
        run_manager = MagicMock()

        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="I need to inspect the code before answering."),
            ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="stop"),
        ]

        provider = MockProvider(events)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            run_manager=run_manager,
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "prompt_unanswered"
        assert result.error.startswith("Blocked: run ended without a clear answer to the last user prompt")
        assistant_messages = [message for message in runtime.messages if message.role == "assistant"]
        assert len(assistant_messages) == 1
        assert assistant_messages[0].persistent is False
        run_manager.emit_stream_event.assert_any_call(
            "test-run-001",
            StreamEvent.prompt_unanswered(
                message=result.error,
                reason="transient_progress_only",
                last_user_prompt="fix the report",
                assistant_text="I need to inspect the code before answering.",
                action_mode=True,
                files_modified=0,
                tests_run=0,
                artifacts_created=0,
                next_required_action="Answer the user's last prompt directly. If more work is required first, make one verifiable step and then report the completed result or explicit blocker instead of narrating intent.",
            ),
        )
        run_manager.block_run.assert_called_once_with(
            "test-run-001",
            result.error,
            "prompt_unanswered",
        )

    def test_blocker_explanation_accepts_does_not_exist_language(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(Message(role="user", kind=MessageKind.TEXT, content="verify the command", token_estimate=3))

        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="`definitely_not_a_real_command_xyz` doesn't exist on this system, so it is unavailable."),
            ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="stop"),
        ]

        provider = MockProvider(events)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"

    def test_plan_answer_with_trailing_deferral_still_counts_as_answered(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(
            Message(
                role="user",
                kind=MessageKind.TEXT,
                content="Continue that existing plan and tell me what we should do first, without redoing the whole intro.",
                token_estimate=18,
            )
        )
        run_manager = MagicMock()

        events = [
            ProviderEvent(
                type=ProviderEventType.TEXT_DELTA,
                text=(
                    "Here's the 3-step plan:\n"
                    "Step 1: Run the existing eval suite end-to-end.\n"
                    "Step 2: Wire the real runtime target.\n"
                    "Step 3: Add scoring and reporting.\n\n"
                    "First move: run the existing suite end-to-end.\n\n"
                    "Let me package this up as a proposal."
                ),
            ),
            ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=12, finish_reason="stop"),
        ]

        provider = MockProvider(events)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            run_manager=run_manager,
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"
        assert result.error == ""
        run_manager.succeed_run.assert_called_once_with("test-run-001")

    def test_research_prompt_pivots_to_synthesis_after_real_source_read(self):
        registry = _make_discovery_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(
            Message(
                role="user",
                kind=MessageKind.TEXT,
                content="Research the harness and summarize two concrete capabilities plus one missing piece with sources.",
                token_estimate=16,
            )
        )
        run_manager = MagicMock()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_1", name="read_file", arguments='{"filePath":"evals/README.md"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Now let me read more files before I answer."),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=4, finish_reason="stop"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text=(
                        "Two concrete capabilities: suite runs write trace/report artifacts, and scenario YAMLs encode observable checks. "
                        "Missing piece: judge mode is still optional rather than a hard required evaluator pass. "
                        "Sources: evals/README.md and evals/runner.py."
                    ),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=12, output_tokens=10, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            run_manager=run_manager,
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert result.finish_reason == "stop"
        assert "Two concrete capabilities" in result.text
        run_manager.succeed_run.assert_called_once_with("test-run-001")
        assert result.error == ""

    def test_first_read_only_batch_in_action_mode_injects_act_now_nudge(self):
        registry = _make_discovery_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(
            Message(
                role="user",
                kind=MessageKind.TEXT,
                content="In task_app.py, add summarize_tasks and run the tests.",
                token_estimate=12,
            )
        )

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_1", name="read_file", arguments='{"filePath":"task_app.py"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_2", name="read_file", arguments='{"filePath":"tests/test_task_app.py"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=12, output_tokens=3, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert result.finish_reason in {"stop", "action_progress_blocked"}
        coaching_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert any(
            ("Do not narrate what you will inspect next." in message)
            or ("You have already read `task_app.py`." in message)
            or ("Use `edit` on `task_app.py`" in message)
            for message in coaching_messages
        )

    def test_duplicate_read_of_same_file_is_blocked_and_model_can_pivot_to_edit(self):
        registry = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["read_file", "edit"],
            provider_capabilities={"supports_tool_use": True},
        )
        registry.register(build_tool(
            "read_file",
            description="Read a file",
            input_schema={
                "type": "object",
                "required": ["filePath"],
                "properties": {"filePath": {"type": "string"}},
            },
            execute=lambda args: "def list_tasks(tasks):\n    return tasks\n",
            read_only=True,
            categories=["core"],
        ))
        registry.register(build_tool(
            "edit",
            description="Edit a file",
            input_schema={
                "type": "object",
                "required": ["path", "edits"],
                "properties": {
                    "path": {"type": "string"},
                    "edits": {"type": "array"},
                },
            },
            execute=lambda args: f"Applied 1 edit(s) to {args['path']}",
            categories=["core"],
        ))
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(
            Message(
                role="user",
                kind=MessageKind.TEXT,
                content="In task_app.py, add summarize_tasks and run the tests.",
                token_estimate=12,
            )
        )

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_1", name="read_file", arguments='{"filePath":"task_app.py"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_2", name="read_file", arguments='{"filePath":"task_app.py"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_3",
                        name="edit",
                        arguments='{"path":"task_app.py","edits":[{"oldText":"return tasks","newText":"return tasks"}]}',
                    ),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=12, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=13, output_tokens=3, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count in {2, 4}
        assert result.finish_reason in {"stop", "action_progress_blocked"}
        assert len(result.tool_results) >= 2
        assert result.tool_results[0]["status"] == "success"
        assert result.tool_results[1]["status"] == "validation_failed"
        assert "Duplicate discovery blocked" in result.tool_results[1]["error"]
        if len(result.tool_results) > 2:
            assert result.tool_results[2]["status"] == "success"

    def test_explicit_target_guard_blocks_git_status_repo_widening(self):
        registry = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["exec", "edit"],
            provider_capabilities={"supports_tool_use": True},
        )
        registry.register(build_tool(
            "exec",
            description="Run a command",
            input_schema={
                "type": "object",
                "required": ["command"],
                "properties": {"command": {"type": "string"}},
            },
            execute=lambda args: f"ran {args['command']}",
            categories=["core"],
        ))
        registry.register(build_tool(
            "edit",
            description="Edit a file",
            input_schema={
                "type": "object",
                "required": ["path", "edits"],
                "properties": {
                    "path": {"type": "string"},
                    "edits": {"type": "array"},
                },
            },
            execute=lambda args: f"Applied 1 edit(s) to {args['path']}",
            categories=["core"],
        ))
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        target_path = r"D:\tmp\task_app.py"
        runtime.messages.append(
            Message(
                role="user",
                kind=MessageKind.TEXT,
                content=f"In `{target_path}`, add summarize_tasks and run the tests.",
                token_estimate=12,
            )
        )

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_1", name="exec", arguments='{"command":"git -C D:\\\\openclaw\\\\opencloset status"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="edit",
                        arguments=json.dumps({"path": target_path, "edits": [{"oldText": "before", "newText": "after"}]}),
                    ),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=12, output_tokens=3, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert result.finish_reason == "stop"
        assert result.tool_results[0]["status"] == "validation_failed"
        assert "Explicit-target guard blocked repo-wide discovery" in result.tool_results[0]["error"]
        assert result.tool_results[1]["status"] == "success"

    def test_explicit_target_guard_blocks_broad_exec_listing_that_ignores_named_file(self):
        registry = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["exec", "edit"],
            provider_capabilities={"supports_tool_use": True},
        )
        registry.register(build_tool(
            "exec",
            description="Run a command",
            input_schema={
                "type": "object",
                "required": ["command"],
                "properties": {"command": {"type": "string"}},
            },
            execute=lambda args: f"ran {args['command']}",
            categories=["core"],
        ))
        registry.register(build_tool(
            "edit",
            description="Edit a file",
            input_schema={
                "type": "object",
                "required": ["path", "edits"],
                "properties": {
                    "path": {"type": "string"},
                    "edits": {"type": "array"},
                },
            },
            execute=lambda args: f"Applied 1 edit(s) to {args['path']}",
            categories=["core"],
        ))
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        target_path = r"D:\tmp\task_app.py"
        runtime.messages.append(
            Message(
                role="user",
                kind=MessageKind.TEXT,
                content=f"In `{target_path}`, add summarize_tasks and run the tests.",
                token_estimate=12,
            )
        )

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments=json.dumps(
                            {"command": "Get-ChildItem -Path D:\\openclaw\\opencloset | Select-Object FullName, Length | ConvertTo-Json"}
                        ),
                    ),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="edit",
                        arguments=json.dumps({"path": target_path, "edits": [{"oldText": "before", "newText": "after"}]}),
                    ),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=12, output_tokens=3, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert result.finish_reason == "stop"
        assert result.tool_results[0]["status"] == "validation_failed"
        assert "Explicit-target guard blocked repo-wide discovery" in result.tool_results[0]["error"]
        assert result.tool_results[1]["status"] == "success"

    def test_generic_paired_xml_read_tag_becomes_tool_call(self):
        registry = _make_discovery_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text="<read_file>task_app.py</read_file>",
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="stop"),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Done."),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=3, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"
        assert provider.call_count == 2
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["tool_name"] == "read_file"

    def test_completed_transient_window_counts_as_answered_prompt(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(Message(role="user", kind=MessageKind.TEXT, content="report window please", token_estimate=4))

        events = [
            ProviderEvent(
                type=ProviderEventType.TEXT_DELTA,
                text=(
                    "Here is the report window.\n\n"
                    "<transient-window id=\"abc123\" title=\"Report\" summary=\"Test summary\">"
                    "<!DOCTYPE html><html><body>ok</body></html>"
                    "</transient-window>"
                ),
            ),
            ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=12, finish_reason="stop"),
        ]

        provider = MockProvider(events)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"
        assert result.error == ""
        assert "<transient-window" in result.text

    def test_completed_transient_window_in_earlier_turn_still_counts_as_answered_prompt(self):
        registry = _make_exec_validation_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(Message(role="user", kind=MessageKind.TEXT, content="report window please", token_estimate=4))

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_1", name="exec", arguments='{"command":"dir"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TEXT_DELTA,
                    text=(
                        "Here is the report window.\n\n"
                        "<transient-window id=\"abc123\" title=\"Report\" summary=\"Test summary\">"
                        "<!DOCTYPE html><html><body>ok</body></html>"
                        "</transient-window>\n\n"
                        "The report window is ready."
                    ),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=16, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"
        assert result.error == ""
        assert "<transient-window" in result.text
        assert "The report window is ready." in result.text

    def test_early_progress_narration_does_not_block_later_substantive_answer(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(Message(role="user", kind=MessageKind.TEXT, content="summarize the harness", token_estimate=4))

        events = [
            ProviderEvent(
                type=ProviderEventType.TEXT_DELTA,
                text=(
                    "I'll inspect the repo sources first.\n\n"
                    "Now let me read the key files.\n\n"
                    "Here is the synthesis:\n\n"
                    "Capability 1: Deterministic rule checks.\n"
                    "Capability 2: Optional judge scoring.\n"
                    "Missing piece: historical comparison across runs."
                ),
            ),
            ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=20, finish_reason="stop"),
        ]

        provider = MockProvider(events)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"
        assert result.error == ""
        assert "Capability 1" in result.text

    def test_last_user_text_prefers_durable_user_message_over_recovery_prompt(self):
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(Message(role="user", kind=MessageKind.TEXT, content="original request", token_estimate=2))
        runtime.messages.append(
            Message(
                role="user",
                kind=MessageKind.TEXT,
                content="temporary recovery prompt",
                token_estimate=3,
                persistent=False,
            )
        )

        loop = AgentLoop(
            runtime,
            MockProvider([]),
            executor,
            normalizer,
        )

        assert loop._last_user_text() == "original request"

    def test_non_action_discovery_run_can_complete_without_blocker(self):
        registry = _make_discovery_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()
        runtime.messages.append(Message(role="user", kind=MessageKind.TEXT, content="where is app.py", token_estimate=4))

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_1", name="file_search", arguments='{"query": "app.py"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="I found the likely app files to inspect next."),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=4, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(action_discovery_budget=1),
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "stop"
        assert result.error == ""
        assert result.text == "I found the likely app files to inspect next."

    def test_validation_failures_do_not_coach_when_failure_recovery_disabled(self):
        registry = _make_exec_validation_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments='{}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Let me inspect the directory."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(governor_max_pending_action_attempts=0, governor_max_recovery_attempts=0),
        )

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["status"] == "validation_failed"
        recovery_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert recovery_messages == []

    def test_repeated_malformed_write_calls_inject_serialization_recovery_nudge(self):
        registry = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["write"],
            provider_capabilities={"supports_tool_use": True},
        )
        registry.register(build_tool(
            "write",
            description="Write a file",
            input_schema={
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
            execute=lambda args: "ok",
            categories=["core"],
        ))
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        bad_write_args = '{"path":"D:\\openclaw\\tmp\\query.py","content":"line1\nline2"'
        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="write",
                        arguments=bad_write_args,
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_2",
                        name="write",
                        arguments=bad_write_args,
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Switching strategy."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=12,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 3
        assert len(result.tool_results) == 2
        recovery_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(recovery_messages) == 1
        assert "arguments were malformed" in recovery_messages[0].lower()

    def test_serialization_safe_mode_blocks_multiline_exec_after_repeated_malformed_write_calls(self):
        registry = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["write", "exec"],
            provider_capabilities={"supports_tool_use": True},
        )
        registry.register(build_tool(
            "write",
            description="Write a file",
            input_schema={
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "content_base64": {"type": "string"},
                },
            },
            execute=lambda args: "ok",
            categories=["core"],
        ))
        registry.register(build_tool(
            "exec",
            description="Run a command",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "script_content": {"type": "string"},
                    "script_content_base64": {"type": "string"},
                    "runner": {"type": "string"},
                },
            },
            execute=lambda args: "promoted" if args.get("script_content_base64") else "should not run",
            categories=["core"],
        ))
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        bad_write_args = '{"path":"D:\\openclaw\\tmp\\query.py","content":"line1\nline2"'
        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_1", name="write", arguments=bad_write_args),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=10, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_2", name="write", arguments=bad_write_args),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=11, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(id="call_3", name="exec", arguments='{"command":"Write-Host hi\nSet-Content file.txt test"}'),
                ),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=12, output_tokens=4, finish_reason="tool_calls"),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Switched to safer path."),
                ProviderEvent(type=ProviderEventType.USAGE, input_tokens=13, output_tokens=4, finish_reason="stop"),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 4
        assert len(result.tool_results) == 3
        assert result.tool_results[2]["status"] == "success"
        assert result.tool_results[2]["content"] == "promoted"
        assert result.tool_results[2]["error"] is None
        recovery_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert len(recovery_messages) == 1
        assert "arguments were malformed" in recovery_messages[0].lower()

    def test_timeout_failures_inject_background_recovery_nudge(self):
        registry = _make_exec_timeout_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments='{"command": "scp -r stuff"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Should I retry?"),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["status"] == "success"
        assert result.tool_results[0]["content"] == "Started background session: proc_123"
        recovery_messages = [
            call.args[0].content
            for call in runtime.add_message.call_args_list
            if getattr(call.args[0], "role", None) == "user"
        ]
        assert recovery_messages == []

    def test_git_push_timeout_retries_as_interactive_session(self):
        registry = _make_exec_timeout_registry()

        def _run_exec(args):
            if args.get("interactive") is True:
                return "Interactive process started.\nsession_id: proc_gitpush\npid: 123\ncommand: git push origin master\nworkdir: D:/openclaw\nUse 'process' with action=log for live output, action=write for text input, action=send-keys for Enter/Tab/Escape/Ctrl+C, and action=kill to terminate."
            if args.get("background") is True:
                return "Started background session: proc_123"
            return "Command timed out after 300s.\ncommand: git push origin master\nFor long-running work, use background=True."

        registry._tools["exec"].execute = _run_exec
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        runtime = _make_mock_runtime()

        provider_turns = [
            [
                ProviderEvent(
                    type=ProviderEventType.TOOL_USE,
                    tool_call=ProviderToolCall(
                        id="call_1",
                        name="exec",
                        arguments='{"command": "git push origin master"}',
                    ),
                ),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=4,
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Waiting for push."),
                ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=11,
                    output_tokens=4,
                    finish_reason="stop",
                ),
            ],
        ]

        class SequencedProvider(Provider):
            def __init__(self, turns):
                self.turns = turns
                self.call_count = 0

            def run_stream(self, messages, **kwargs):
                current = self.turns[self.call_count]
                self.call_count += 1
                for event in current:
                    yield event

        provider = SequencedProvider(provider_turns)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        result = loop.run(lambda: ([], None))

        assert provider.call_count == 2
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["status"] == "success"
        assert "Interactive process started." in result.tool_results[0]["content"]
        assert "session_id: proc_gitpush" in result.tool_results[0]["content"]


# ---------------------------------------------------------------------------
# Loop: interruption
# ---------------------------------------------------------------------------

class TestInterruption:
    def test_interrupt_at_start(self):
        """If runtime is interrupted before loop starts, it exits immediately."""
        events = [ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="x")]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        runtime.is_interrupted = True

        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(lambda: ([], None))

        assert result.interrupted is True
        assert result.turn_count == 0
        assert provider.call_count == 0

    def test_interrupt_during_loop(self):
        """Provider emits tool call, runtime is interrupted after tool exec."""
        events_turn1 = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Step 1"),
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name="echo",
                    arguments='{"text": "a"}',
                ),
            ),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=10,
                output_tokens=5,
                finish_reason="tool_calls",
            ),
        ]

        provider = MockProvider(events_turn1)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)

        # Set interrupt after first tool execution
        executor = ToolExecutor(registry, runtime=runtime)

        interrupt_after = [False]
        original_execute_all = executor.execute_all

        def patched_execute_all(calls, **kwargs):
            result = original_execute_all(calls, **kwargs)
            interrupt_after[0] = True
            runtime.is_interrupted = True
            return result

        executor.execute_all = patched_execute_all

        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(lambda: ([], None))

        assert result.interrupted is True
        assert "Step 1" in result.text

    def test_interrupt_during_provider_stream(self):
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry, runtime=runtime)
        run_manager = MagicMock()

        class InterruptingProvider(Provider):
            def run_stream(self, messages, **kwargs):
                yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Step 1")
                runtime.is_interrupted = True
                yield ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=10,
                    output_tokens=1,
                    finish_reason="interrupted",
                )

        loop = AgentLoop(runtime, InterruptingProvider(), executor, normalizer, run_manager=run_manager)
        result = loop.run(lambda: ([], None))

        assert result.interrupted is True
        assert result.finish_reason == "interrupted"
        assert result.text == "Step 1"
        run_manager.interrupt_run.assert_called_once_with("test-run-001")

    def test_interrupt_delegates_to_run_manager(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Step 1"),
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name="echo",
                    arguments='{"text": "a"}',
                ),
            ),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=10,
                output_tokens=5,
                finish_reason="tool_calls",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry, runtime=runtime)
        run_manager = MagicMock()

        original_execute_all = executor.execute_all

        def patched_execute_all(calls, **kwargs):
            result = original_execute_all(calls, **kwargs)
            runtime.is_interrupted = True
            return result

        executor.execute_all = patched_execute_all

        loop = AgentLoop(runtime, provider, executor, normalizer, run_manager=run_manager)
        result = loop.run(lambda: ([], None))

        assert result.interrupted is True
        run_manager.interrupt_run.assert_called_once_with("test-run-001")
        runtime.end_run.assert_not_called()

    def test_provider_stream_idle_timeout_fails_run_cleanly(self):
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry, runtime=runtime)

        class HungProvider(Provider):
            def __init__(self):
                self.cancelled = False

            def run_stream(self, messages, **kwargs):
                time.sleep(0.08)
                if False:
                    yield None

            def cancel_active(self) -> None:
                self.cancelled = True

        provider = HungProvider()

        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(provider_stream_idle_timeout_seconds=0.02),
        )
        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "provider_stream_timeout"
        assert result.error.startswith("Provider stream produced no events")
        assert result.interrupted is False
        runtime.request_interrupt.assert_called_once()
        assert provider.cancelled is True
        runtime.end_run.assert_called_once()
        assert runtime.end_run.call_args.kwargs["status"] == "failed"

    def test_provider_stream_idle_timeout_emits_stream_event(self):
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry, runtime=runtime)
        run_manager = MagicMock()

        class HungProvider(Provider):
            def run_stream(self, messages, **kwargs):
                time.sleep(0.08)
                if False:
                    yield None

        loop = AgentLoop(
            runtime,
            HungProvider(),
            executor,
            normalizer,
            config=LoopConfig(provider_stream_idle_timeout_seconds=0.02),
            run_manager=run_manager,
        )
        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "provider_stream_timeout"
        emitted = run_manager.emit_stream_event.call_args_list[0]
        assert emitted.args[0] == "test-run-001"
        timeout_event = emitted.args[1]
        assert timeout_event.type == "provider_stream_timeout"
        assert timeout_event.data["threshold_s"] == pytest.approx(0.02, rel=0.2)
        run_manager.fail_run.assert_called_once_with("test-run-001", result.error)

    def test_provider_stream_idle_timeout_recovers_when_tool_work_is_complete(self):
        runtime = _make_mock_runtime()
        runtime.messages.append(
            Message(role="user", kind=MessageKind.TEXT, content="Update task_app.py and run the tests.")
        )
        registry = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["edit", "exec"],
            provider_capabilities={"supports_tool_use": True},
        )
        registry.register(build_tool(
            "edit",
            description="Edit a file",
            input_schema={
                "type": "object",
                "required": ["path", "edits"],
                "properties": {
                    "path": {"type": "string"},
                    "edits": {"type": "array"},
                },
            },
            execute=lambda args: f"Applied 1 edit(s) to {args['path']}",
            read_only=False,
            categories=["core"],
        ))
        registry.register(build_tool(
            "exec",
            description="Run a command",
            input_schema={
                "type": "object",
                "required": ["command"],
                "properties": {"command": {"type": "string"}},
            },
            execute=lambda _args: (
                "Exit code: 0\n"
                "============================== 3 passed in 0.05s ==============================\n"
            ),
            read_only=False,
            categories=["core"],
        ))
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry, runtime=runtime)
        target_path = r"D:\tmp\task_app.py"

        class TimeoutAfterCompletedToolsProvider(Provider):
            def __init__(self):
                self.call_count = 0
                self.cancelled = False

            def run_stream(self, messages, **kwargs):
                self.call_count += 1
                if self.call_count == 1:
                    yield ProviderEvent(
                        type=ProviderEventType.TOOL_USE,
                        tool_call=ProviderToolCall(
                            id="call_edit",
                            name="edit",
                            arguments=json.dumps(
                                {
                                    "path": target_path,
                                    "edits": [{"oldText": "before", "newText": "after"}],
                                }
                            ),
                        ),
                    )
                    yield ProviderEvent(
                        type=ProviderEventType.USAGE,
                        input_tokens=10,
                        output_tokens=5,
                        finish_reason="tool_calls",
                    )
                    return
                if self.call_count == 2:
                    yield ProviderEvent(
                        type=ProviderEventType.TOOL_USE,
                        tool_call=ProviderToolCall(
                            id="call_exec",
                            name="exec",
                            arguments=json.dumps({"command": "python -m pytest -v"}),
                        ),
                    )
                    yield ProviderEvent(
                        type=ProviderEventType.USAGE,
                        input_tokens=8,
                        output_tokens=4,
                        finish_reason="tool_calls",
                    )
                    return
                time.sleep(0.08)
                if False:
                    yield None

            def cancel_active(self) -> None:
                self.cancelled = True

        provider = TimeoutAfterCompletedToolsProvider()
        loop = AgentLoop(
            runtime,
            provider,
            executor,
            normalizer,
            config=LoopConfig(provider_stream_idle_timeout_seconds=0.02),
        )

        result = loop.run(lambda: ([], None))

        assert result.finish_reason == "provider_stream_timeout_recovered"
        assert result.error == ""
        assert "Updated `task_app.py`." in result.text
        assert "Tests passed (3 passed in 0.05s)." in result.text
        assert provider.cancelled is True
        runtime.end_run.assert_called_once()
        assert runtime.end_run.call_args.kwargs["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Loop: max turns
# ---------------------------------------------------------------------------

class TestMaxTurns:
    def test_max_turns_stops_loop(self):
        """Explicit max_turns still stops the loop when a capped run asks for it."""
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Iter "),
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name="echo",
                    arguments='{"text": "x"}',
                ),
            ),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=5,
                output_tokens=3,
                finish_reason="tool_calls",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        config = LoopConfig(max_turns=2)

        loop = AgentLoop(runtime, provider, executor, normalizer, config=config)
        result = loop.run(lambda: ([], None))

        assert result.turn_count == 2
        # Provider called 2 times (one per iteration)
        assert provider.call_count == 2
        assert result.interrupted is True
        assert result.finish_reason == "max_turns_reached"


class TestPartialProviderFailure:
    def test_partial_text_is_preserved_when_provider_stream_crashes(self):
        provider = CrashingProvider([
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Partial reply before crash."),
        ])
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        loop = AgentLoop(runtime, provider, executor, normalizer, config=LoopConfig())
        result = loop.run(lambda: ([{"role": "user", "content": "hello"}], None))

        assert result.error == "Response ended prematurely"
        assert result.text == "Partial reply before crash."
        persisted = runtime.add_message.call_args[0][0]
        assert persisted.role == "assistant"
        assert persisted.content == "Partial reply before crash."


# ---------------------------------------------------------------------------
# Loop: unknown tool dropped
# ---------------------------------------------------------------------------

class TestUnknownToolDropped:
    def test_unknown_tool_stops_loop(self):
        """If all tool calls are unknown, normalization drops them all → loop stops."""
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Try tool"),
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ProviderToolCall(
                    id="call_1",
                    name="nonexistent",
                    arguments="{}",
                ),
            ),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=5,
                output_tokens=3,
                finish_reason="tool_calls",
            ),
        ]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(lambda: ([], None))

        assert result.turn_count == 1  # stopped after first iteration
        assert result.tool_results == []


# ---------------------------------------------------------------------------
# create_agent_loop factory
# ---------------------------------------------------------------------------

class TestCreateAgentLoop:
    def test_factory(self):
        runtime = _make_mock_runtime()
        registry = _make_registry()
        provider = MockProvider([])

        loop = create_agent_loop(
            runtime,
            provider,
            registry,
            max_tool_calls_per_turn=5,
        )
        assert loop.runtime is runtime
        assert loop.provider is provider
        assert loop.executor.max_tool_calls_per_turn == 5
        assert isinstance(loop.normalizer, ToolCallNormalizer)

    def test_factory_with_config(self):
        runtime = _make_mock_runtime()
        registry = _make_registry()
        provider = MockProvider([])
        config = LoopConfig(max_turns=10, temperature=0.3)

        loop = create_agent_loop(runtime, provider, registry, config=config)
        assert loop.config.max_turns == 10
        assert loop.config.temperature == 0.3


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_build_prompt_error_ends_run(self):
        """If build_prompt raises, loop ends with failed status."""
        events = [ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="x")]
        provider = MockProvider(events)
        runtime = _make_mock_runtime()
        registry = _make_registry()
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)

        def bad_build_prompt():
            raise ValueError("prompt error")

        loop = AgentLoop(runtime, provider, executor, normalizer)
        result = loop.run(bad_build_prompt)

        assert result.error == "prompt error"
        assert result.turn_count == 0


class TestLoopResult:
    def test_default_values(self):
        result = LoopResult()
        assert result.text == ""
        assert result.thinking == ""
        assert result.tool_results == []
        assert result.turn_count == 0
        assert result.finish_reason == ""
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert not result.interrupted
        assert result.error == ""

    def test_inject_tool_result(self):
        runtime = _make_mock_runtime()
        registry = _make_registry()
        provider = MockProvider([])
        normalizer = ToolCallNormalizer(registry)
        executor = ToolExecutor(registry)
        loop = AgentLoop(runtime, provider, executor, normalizer)

        tr = MagicMock(spec=ToolResult)
        tr.call_id = "call_test"
        tr.tool_name = "echo"
        tr.status = ExecutionStatus.SUCCESS
        tr.content = "hello"
        tr.error = None
        loop._inject_tool_result(tr)
        assert runtime.add_message.called
        injected = runtime.add_message.call_args.args[0]
        assert '"status": "success"' in injected.content
