# Tests for provider subsystem

from __future__ import annotations

import json
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import requests
from api.provider.base import (
    Provider,
    ProviderConfig,
    ProviderEvent,
    ProviderEventType,
    ProviderResult,
    ToolCall,
    create_provider,
)
from api.provider.llamacpp import LlamaCppProvider
from api.provider.openai import OpenAIProvider
from api.provider.ollama import OllamaProvider


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestProviderEvent:
    def test_text_delta(self):
        e = ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="hello")
        assert e.type == ProviderEventType.TEXT_DELTA
        assert e.text == "hello"

    def test_tool_use(self):
        tc = ToolCall(id="call_1", name="read", arguments='{"path":"test.txt"}')
        e = ProviderEvent(type=ProviderEventType.TOOL_USE, tool_call=tc)
        assert e.tool_call.name == "read"
        assert e.tool_call.arguments == '{"path":"test.txt"}'

    def test_usage(self):
        e = ProviderEvent(
            type=ProviderEventType.USAGE,
            input_tokens=100,
            output_tokens=50,
            finish_reason="stop",
        )
        assert e.input_tokens == 100
        assert e.output_tokens == 50


class TestProviderResult:
    def test_default(self):
        r = ProviderResult()
        assert r.text == ""
        assert r.tool_calls == []
        assert r.input_tokens == 0

    def test_with_data(self):
        r = ProviderResult(
            text="hello",
            tool_calls=[ToolCall(id="c1", name="read", arguments="{}")],
            input_tokens=10,
            output_tokens=5,
        )
        assert len(r.tool_calls) == 1
        assert r.finish_reason == ""


class TestProviderConfig:
    def test_defaults(self):
        c = ProviderConfig()
        assert c.server_url == ""
        assert c.timeout == 300.0

    def test_custom(self):
        c = ProviderConfig(server_url="http://localhost:9000", timeout=60)
        assert c.server_url == "http://localhost:9000"
        assert c.timeout == 60


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateProvider:
    def test_llamacpp(self):
        p = create_provider("llamacpp")
        assert isinstance(p, LlamaCppProvider)
        assert p._url == "http://127.0.0.1:8080/v1/chat/completions"

    def test_ollama(self):
        p = create_provider("ollama")
        assert isinstance(p, OllamaProvider)
        assert p._url == "http://127.0.0.1:11434/api/chat"

    def test_with_config(self):
        cfg = ProviderConfig(server_url="http://localhost:9999", model_name="test")
        p = create_provider("llamacpp", config=cfg)
        assert p._url == "http://localhost:9999/v1/chat/completions"
        assert p.config.model_name == "test"

    def test_unknown_backend(self):
        with pytest.raises(ValueError, match="Unknown"):
            create_provider("unknown")


# ---------------------------------------------------------------------------
# Mock streaming — test run() accumulation logic
# ---------------------------------------------------------------------------


class MockProvider(Provider):
    """Provider that yields predefined events for testing."""

    def __init__(self, events: list[ProviderEvent]):
        self._events = events

    def run_stream(
        self,
        messages,
        *,
        temperature=0.7,
        max_tokens=4096,
        stop_sequences=None,
        tools=None,
        interrupt_check=None,
    ) -> Generator[ProviderEvent, None, None]:
        for e in self._events:
            yield e


class TestRunAccumulation:
    def test_text_only(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Hello"),
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=" world"),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=10,
                output_tokens=2,
                finish_reason="stop",
            ),
        ]
        provider = MockProvider(events)
        result = provider.run([])

        assert result.text == "Hello world"
        assert result.tool_calls == []
        assert result.input_tokens == 10
        assert result.output_tokens == 2
        assert result.finish_reason == "stop"

    def test_text_and_tool_calls(self):
        events = [
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Let me"),
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ToolCall(id="c1", name="read", arguments='{"path":"a.txt"}'),
            ),
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text=" read the file"),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=20,
                output_tokens=10,
                finish_reason="tool_calls",
            ),
        ]
        provider = MockProvider(events)
        result = provider.run([])

        assert result.text == "Let me read the file"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "read"
        assert result.finish_reason == "tool_calls"

    def test_multiple_tool_calls(self):
        events = [
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ToolCall(id="c1", name="read", arguments='{"path":"a.txt"}'),
            ),
            ProviderEvent(
                type=ProviderEventType.TOOL_USE,
                tool_call=ToolCall(id="c2", name="write", arguments='{"path":"b.txt","content":"x"}'),
            ),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=10,
                output_tokens=5,
                finish_reason="tool_calls",
            ),
        ]
        provider = MockProvider(events)
        result = provider.run([])

        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "read"
        assert result.tool_calls[1].name == "write"

    def test_thinking(self):
        events = [
            ProviderEvent(type=ProviderEventType.THINKING_DELTA, text="Reasoning..."),
            ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="Answer"),
            ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=5,
                output_tokens=3,
            ),
        ]
        provider = MockProvider(events)
        result = provider.run([])

        assert result.thinking == "Reasoning..."
        assert result.text == "Answer"

    def test_empty_stream(self):
        provider = MockProvider([])
        result = provider.run([])
        assert result.text == ""
        assert result.tool_calls == []


# ---------------------------------------------------------------------------
# llama.cpp provider — URL and config
# ---------------------------------------------------------------------------


class TestLlamaCppProvider:
    def test_default_url(self):
        p = LlamaCppProvider()
        assert p._url == "http://127.0.0.1:8080/v1/chat/completions"

    def test_custom_url(self):
        cfg = ProviderConfig(server_url="http://localhost:9000")
        p = LlamaCppProvider(cfg)
        assert p._url == "http://localhost:9000/v1/chat/completions"

    def test_trailing_stripped(self):
        cfg = ProviderConfig(server_url="http://localhost:9000/")
        p = LlamaCppProvider(cfg)
        assert p._url == "http://localhost:9000/v1/chat/completions"

    def test_accumulates_fragmented_tool_calls(self):
        p = LlamaCppProvider(ProviderConfig(model_name="qwen3.6-27b"))

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read","arguments":"{\\"path\\""}}]},"finish_reason":""}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":":\\"a.txt\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":4}}',
            'data: [DONE]',
        ]
        response.close.return_value = None

        with patch("api.provider.llamacpp.requests.post", return_value=response):
            events = list(p.run_stream([{"role": "user", "content": "read it"}], tools=[{"type": "function", "function": {"name": "read", "description": "Read", "parameters": {}}}]))

        tool_events = [e for e in events if e.type == ProviderEventType.TOOL_USE]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.name == "read"
        assert tool_events[0].tool_call.arguments == '{"path":"a.txt"}'

    def test_waits_for_complete_tool_arguments_before_emitting(self):
        p = LlamaCppProvider(ProviderConfig(model_name="qwen3.6-27b"))

        first_chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "write",
                                    "arguments": '{"path":"a.py","content":"print(\\"hi\\")',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        second_chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "arguments": '\nprint(\\"bye\\")"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 6},
        }

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = [
            f"data: {json.dumps(first_chunk)}",
            f"data: {json.dumps(second_chunk)}",
            "data: [DONE]",
        ]
        response.close.return_value = None

        with patch("api.provider.llamacpp.requests.post", return_value=response):
            events = list(
                p.run_stream(
                    [{"role": "user", "content": "write it"}],
                    tools=[{"type": "function", "function": {"name": "write", "description": "Write", "parameters": {}}}],
                )
            )

        tool_events = [e for e in events if e.type == ProviderEventType.TOOL_USE]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.arguments == '{"path":"a.py","content":"print(\\"hi\\")\nprint(\\"bye\\")"}'

    def test_emits_completed_tool_call_even_when_finish_reason_is_stop(self):
        p = LlamaCppProvider(ProviderConfig(model_name="qwen3.6-27b"))

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"exec","arguments":"{\\"command\\":\\"git status\\"}"}}]},"finish_reason":""}]}',
            'data: {"choices":[{"delta":{"content":"I checked it."},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":6}}',
            'data: [DONE]',
        ]
        response.close.return_value = None

        with patch("api.provider.llamacpp.requests.post", return_value=response):
            events = list(
                p.run_stream(
                    [{"role": "user", "content": "check git"}],
                    tools=[{"type": "function", "function": {"name": "exec", "description": "Exec", "parameters": {}}}],
                )
            )

        tool_events = [e for e in events if e.type == ProviderEventType.TOOL_USE]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.name == "exec"
        assert tool_events[0].tool_call.arguments == '{"command":"git status"}'

    def test_closes_response_on_stream_error(self):
        p = LlamaCppProvider(ProviderConfig(model_name="qwen3.6-27b"))

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.side_effect = RuntimeError("stream failed")
        response.close.return_value = None

        with patch("api.provider.llamacpp.requests.post", return_value=response):
            with pytest.raises(RuntimeError, match="stream failed"):
                list(p.run_stream([{"role": "user", "content": "hello"}]))

        response.close.assert_called_once()

    def test_interrupt_during_stream_error_returns_usage_event(self):
        p = LlamaCppProvider(ProviderConfig(model_name="qwen3.6-27b"))

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.side_effect = RuntimeError("stream interrupted")
        response.close.return_value = None

        interrupts = iter([False, True])
        with patch("api.provider.llamacpp.requests.post", return_value=response):
            events = list(
                p.run_stream(
                    [{"role": "user", "content": "hello"}],
                    interrupt_check=lambda: next(interrupts),
                )
            )

        assert len(events) == 1
        assert events[0].type == ProviderEventType.USAGE
        assert events[0].finish_reason == "interrupted"

    def test_translates_post_timeout(self):
        p = LlamaCppProvider(ProviderConfig(timeout=12))

        with patch("api.provider.llamacpp.requests.post", side_effect=requests.Timeout("timed out")):
            with pytest.raises(TimeoutError, match="12"):
                list(p.run_stream([{"role": "user", "content": "hello"}]))

    def test_sends_llamacpp_request_tuning_options(self):
        p = LlamaCppProvider(
            ProviderConfig(
                model_name="qwen3.6-27b",
                options={
                    "cache_prompt": True,
                    "n_cache_reuse": 256,
                    "reasoning_format": "none",
                    "reasoning_budget": 0,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
        )

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":8,"completion_tokens":1}}',
            'data: [DONE]',
        ]
        response.close.return_value = None

        with patch("api.provider.llamacpp.requests.post", return_value=response) as mock_post:
            events = list(p.run_stream([{"role": "user", "content": "hello"}]))

        assert [event.type for event in events] == [ProviderEventType.TEXT_DELTA, ProviderEventType.USAGE]
        payload = mock_post.call_args.kwargs["json"]
        assert payload["cache_prompt"] is True
        assert payload["n_cache_reuse"] == 256
        assert payload["reasoning_format"] == "none"
        assert payload["reasoning_budget"] == 0
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}


# ---------------------------------------------------------------------------
# Ollama provider — format messages
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    def test_default_url(self):
        p = OllamaProvider()
        assert p._url == "http://127.0.0.1:11434/api/chat"

    def test_format_messages(self):
        p = OllamaProvider()
        msgs = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}],
            },
        ]
        formatted = p.format_messages(msgs)
        assert len(formatted) == 3
        assert formatted[0]["role"] == "system"
        assert formatted[0]["content"] == "be helpful"
        # tool_calls stripped out in Ollama format
        assert "tool_calls" not in formatted[2]

    def test_format_empty_content(self):
        p = OllamaProvider()
        msgs = [{"role": "user", "content": None}]
        formatted = p.format_messages(msgs)
        assert formatted[0]["content"] == ""

    def test_closes_response_on_stream_error(self):
        p = OllamaProvider(ProviderConfig(model_name="llama3"))

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.side_effect = RuntimeError("stream failed")
        response.close.return_value = None

        with patch("api.provider.ollama.requests.post", return_value=response):
            with pytest.raises(RuntimeError, match="stream failed"):
                list(p.run_stream([{"role": "user", "content": "hello"}]))

        response.close.assert_called_once()

    def test_interrupt_during_stream_error_returns_usage_event(self):
        p = OllamaProvider(ProviderConfig(model_name="llama3"))

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.side_effect = RuntimeError("stream interrupted")
        response.close.return_value = None

        interrupts = iter([False, True])
        with patch("api.provider.ollama.requests.post", return_value=response):
            events = list(
                p.run_stream(
                    [{"role": "user", "content": "hello"}],
                    interrupt_check=lambda: next(interrupts),
                )
            )

        assert len(events) == 1
        assert events[0].type == ProviderEventType.USAGE
        assert events[0].finish_reason == "interrupted"

    def test_translates_post_timeout(self):
        p = OllamaProvider(ProviderConfig(timeout=9))

        with patch("api.provider.ollama.requests.post", side_effect=requests.Timeout("timed out")):
            with pytest.raises(TimeoutError, match="9"):
                list(p.run_stream([{"role": "user", "content": "hello"}]))

    def test_request_includes_tools_stop_and_auth_header(self):
        p = OllamaProvider(
            ProviderConfig(
                server_url="http://localhost:11434/",
                model_name="llama3",
                api_key="secret-key",
                timeout=15,
            )
        )

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = [
            '{"message":{"content":"done"},"done":true,"prompt_eval_count":7,"eval_count":3,"done_reason":"stop"}',
        ]
        response.close.return_value = None

        with patch("api.provider.ollama.requests.post", return_value=response) as post_mock:
            events = list(
                p.run_stream(
                    [{"role": "user", "content": "hello"}],
                    stop_sequences=["STOP"],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "read",
                                "description": "Read file",
                                "parameters": {"type": "object"},
                            },
                        }
                    ],
                )
            )

        call_kwargs = post_mock.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer secret-key"
        assert call_kwargs["timeout"] == (10.0, 15)
        assert call_kwargs["json"]["options"]["stop"] == ["STOP"]
        assert call_kwargs["json"]["tools"][0]["function"]["name"] == "read"
        assert [event.type for event in events] == [ProviderEventType.TEXT_DELTA, ProviderEventType.USAGE]

    def test_interrupt_before_request_returns_usage_event(self):
        p = OllamaProvider(ProviderConfig(model_name="llama3"))

        with patch("api.provider.ollama.requests.post") as post_mock:
            events = list(
                p.run_stream(
                    [{"role": "user", "content": "hello world"}],
                    interrupt_check=lambda: True,
                )
            )

        post_mock.assert_not_called()
        assert len(events) == 1
        assert events[0].type == ProviderEventType.USAGE
        assert events[0].finish_reason == "interrupted"
        assert events[0].output_tokens == 0

    def test_interrupt_mid_stream_uses_last_chunk_usage(self):
        p = OllamaProvider(ProviderConfig(model_name="llama3"))

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = [
            '{"message":{"content":"partial"},"prompt_eval_count":11,"eval_count":2}',
            '{"message":{"content":"ignored"},"prompt_eval_count":11,"eval_count":3}',
        ]
        response.close.return_value = None

        interrupts = iter([False, False, True])
        with patch("api.provider.ollama.requests.post", return_value=response):
            events = list(
                p.run_stream(
                    [{"role": "user", "content": "hello"}],
                    interrupt_check=lambda: next(interrupts),
                )
            )

        assert [event.type for event in events] == [ProviderEventType.TEXT_DELTA, ProviderEventType.USAGE]
        assert events[-1].finish_reason == "interrupted"
        assert events[-1].input_tokens == 11
        assert events[-1].output_tokens == 2

    def test_non_200_response_raises_runtime_error(self):
        p = OllamaProvider(ProviderConfig(model_name="llama3"))

        response = MagicMock()
        response.status_code = 500
        response.text = "server blew up"
        response.close.return_value = None

        with patch("api.provider.ollama.requests.post", return_value=response):
            with pytest.raises(RuntimeError, match="500"):
                list(p.run_stream([{"role": "user", "content": "hello"}]))

        response.close.assert_called_once()

    def test_emits_tool_and_thinking_events_from_stream(self):
        p = OllamaProvider(ProviderConfig(model_name="llama3"))

        response = MagicMock()
        response.status_code = 200
        response.iter_lines.return_value = [
            'not-json',
            json.dumps({
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "read", "arguments": '{"path"'},
                        }
                    ]
                }
            }),
            json.dumps({
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"arguments": ':"note.txt"}'},
                        }
                    ],
                    "thinking": "reasoning",
                },
                "done": True,
                "prompt_eval_count": 9,
                "eval_count": 4,
                "done_reason": "tool_calls",
            }),
        ]
        response.close.return_value = None

        with patch("api.provider.ollama.requests.post", return_value=response):
            events = list(p.run_stream([{"role": "user", "content": "use a tool"}]))

        assert [event.type for event in events] == [ProviderEventType.TOOL_USE, ProviderEventType.THINKING_DELTA, ProviderEventType.USAGE]
        assert events[0].tool_call is not None
        assert events[0].tool_call.name == "read"
        assert events[0].tool_call.arguments == '{"path":"note.txt"}'
        assert events[1].text == "reasoning"
        assert events[2].finish_reason == "tool_calls"


class TestOpenAIProvider:
    def test_formats_orphan_tool_messages_into_openai_compatible_chain(self):
        provider = OpenAIProvider(ProviderConfig(model_name="gpt-4.1-mini", api_key="secret-key"))

        formatted = provider.format_messages(
            [
                {"role": "user", "content": "hello"},
                {
                    "role": "tool",
                    "content": json.dumps(
                        {
                            "tool_id": "call_123",
                            "tool_name": "plan_create",
                            "status": "success",
                            "content": "done",
                            "error": None,
                        }
                    ),
                },
            ]
        )

        assert formatted[0]["role"] == "user"
        assert formatted[1]["role"] == "assistant"
        assert formatted[1]["tool_calls"][0]["id"] == "call_123"
        assert formatted[1]["tool_calls"][0]["function"]["name"] == "plan_create"
        assert formatted[2]["role"] == "tool"
        assert formatted[2]["tool_call_id"] == "call_123"

    def test_normalizes_tools_before_openai_request(self):
        provider = OpenAIProvider(ProviderConfig(model_name="gpt-4.1-mini", api_key="secret-key"))

        final_stream = iter(
            [
                MagicMock(
                    choices=[MagicMock(delta=MagicMock(content="ok", tool_calls=[], reasoning_content=None), finish_reason="stop")],
                    usage=MagicMock(prompt_tokens=11, completion_tokens=2),
                )
            ]
        )

        provider._client = MagicMock()
        provider._client.chat.completions.create.return_value = final_stream

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                    "strict": True,
                },
                "extra": "ignored",
            }
        ]

        list(provider.run_stream([{"role": "user", "content": "hello"}], tools=tools))

        kwargs = provider._client.chat.completions.create.call_args.kwargs
        assert kwargs["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ]

    def test_retries_with_max_completion_tokens_when_model_rejects_max_tokens(self):
        provider = OpenAIProvider(ProviderConfig(model_name="gpt-5.5", api_key="secret-key"))

        final_stream = iter(
            [
                MagicMock(
                    choices=[MagicMock(delta=MagicMock(content="ok", tool_calls=[], reasoning_content=None), finish_reason="stop")],
                    usage=MagicMock(prompt_tokens=11, completion_tokens=2),
                )
            ]
        )

        calls: list[dict] = []

        def create_side_effect(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError(
                    "Error code: 400 - {'error': {'message': \"Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.\"}}"
                )
            return final_stream

        provider._client = MagicMock()
        provider._client.chat.completions.create.side_effect = create_side_effect

        events = list(provider.run_stream([{"role": "user", "content": "hello"}], max_tokens=32))

        assert len(calls) == 2
        assert calls[0]["max_tokens"] == 32
        assert "max_completion_tokens" not in calls[0]
        assert calls[1]["max_completion_tokens"] == 32
        assert "max_tokens" not in calls[1]
        assert [event.type for event in events] == [ProviderEventType.TEXT_DELTA, ProviderEventType.USAGE]

    def test_retries_with_default_temperature_when_model_rejects_custom_temperature(self):
        provider = OpenAIProvider(ProviderConfig(model_name="gpt-5.5", api_key="secret-key"))

        final_stream = iter(
            [
                MagicMock(
                    choices=[MagicMock(delta=MagicMock(content="ok", tool_calls=[], reasoning_content=None), finish_reason="stop")],
                    usage=MagicMock(prompt_tokens=9, completion_tokens=2),
                )
            ]
        )

        calls: list[dict] = []

        def create_side_effect(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError(
                    "Error code: 400 - {'error': {'message': \"Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported.\"}}"
                )
            return final_stream

        provider._client = MagicMock()
        provider._client.chat.completions.create.side_effect = create_side_effect

        events = list(provider.run_stream([{"role": "user", "content": "hello"}], temperature=0.2, max_tokens=32))

        assert len(calls) == 2
        assert calls[0]["temperature"] == 0.2
        assert calls[1]["temperature"] == 1
        assert [event.type for event in events] == [ProviderEventType.TEXT_DELTA, ProviderEventType.USAGE]

    def test_does_not_retry_unrelated_openai_errors(self):
        provider = OpenAIProvider(ProviderConfig(model_name="gpt-4.1-mini", api_key="secret-key"))
        provider._client = MagicMock()
        provider._client.chat.completions.create.side_effect = RuntimeError("Error code: 401 - unauthorized")

        with pytest.raises(RuntimeError, match="unauthorized"):
            list(provider.run_stream([{"role": "user", "content": "hello"}], max_tokens=32))


# ---------------------------------------------------------------------------
# Provider run_stream interface contract
# ---------------------------------------------------------------------------


class TestProviderInterface:
    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            Provider()

    def test_subclass_must_implement_run_stream(self):
        class BrokenProvider(Provider):
            pass

        with pytest.raises(TypeError):
            BrokenProvider()

    def test_format_messages_passthrough(self):
        class MinimalProvider(Provider):
            def run_stream(self, messages, **kwargs):
                yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text="ok")

        p = MinimalProvider()
        msgs = [{"role": "user", "content": "hi"}]
        assert p.format_messages(msgs) == msgs
