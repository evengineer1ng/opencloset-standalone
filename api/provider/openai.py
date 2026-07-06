# OpenAI API provider
#
# Connects to api.openai.com (or any OpenAI-compatible endpoint)
# using the official openai Python SDK with streaming.

from __future__ import annotations

import json
import time
from threading import Lock
import uuid
from typing import Generator

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised via runtime path
    OpenAI = None

from api.provider.base import (
    Provider,
    ProviderConfig,
    ProviderEvent,
    ProviderEventType,
    ToolCall,
    coalesce_finish_reason,
)


class OpenAIProvider(Provider):
    """Provider for OpenAI API (gpt-4o, gpt-4o-mini, o1, etc.).

    Uses the official openai SDK with streaming SSE.
    """

    def __init__(self, config: ProviderConfig | None = None):
        if OpenAI is None:
            raise RuntimeError(
                "OpenAI provider requires the 'openai' package. Install dependencies from opencloset/requirements.txt."
            )
        self.config = config or ProviderConfig()

        # Build client â allow custom base_url for OpenAI-compatible endpoints
        kwargs: dict = {}
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.server_url:
            kwargs["base_url"] = self.config.server_url.rstrip("/")

        self._client = OpenAI(**kwargs) if kwargs else OpenAI()
        self._timeout = self.config.timeout
        self._active_stream = None
        self._active_stream_lock = Lock()

    def cancel_active(self) -> None:
        with self._active_stream_lock:
            stream = self._active_stream
        if stream is not None:
            try:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass

    def _create_stream_with_fallback(self, body: dict):
        current_body = dict(body)
        for _ in range(3):
            try:
                return self._client.chat.completions.create(**current_body)
            except Exception as exc:
                next_body = _build_retry_body_for_openai_error(exc, current_body)
                if next_body is None:
                    raise
                current_body = next_body
        return self._client.chat.completions.create(**current_body)

    def run_stream(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        tools: list[dict] | None = None,
        interrupt_check=None,
    ) -> Generator[ProviderEvent, None, None]:
        """Stream from OpenAI API, yielding structured events."""

        model = self.config.model_name or "gpt-4o-mini"
        formatted_messages = self.format_messages(messages)

        body: dict = {
            "model": model,
            "messages": formatted_messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stop_sequences:
            body["stop"] = stop_sequences
        if tools:
            body["tools"] = _format_tools_for_openai(tools)

        start_time = time.time()
        input_token_estimate = _count_tokens(formatted_messages)

        if interrupt_check and interrupt_check():
            yield ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=input_token_estimate,
                output_tokens=0,
                finish_reason="interrupted",
            )
            return

        stream = None
        try:
            stream = self._create_stream_with_fallback(body)
        except Exception as exc:
            raise RuntimeError(f"OpenAI request failed: {exc}") from exc

        try:
            with self._active_stream_lock:
                self._active_stream = stream

            output_tokens = 0
            usage = {}
            finish_reason = ""
            pending_tool_args: dict[str, str] = {}
            pending_tool_name: dict[str, str] = {}
            pending_tool_ids: dict[str, str] = {}

            try:
                for chunk in stream:
                    if interrupt_check and interrupt_check():
                        yield ProviderEvent(
                            type=ProviderEventType.USAGE,
                            input_tokens=usage.get("prompt_tokens", input_token_estimate),
                            output_tokens=usage.get("completion_tokens", output_tokens),
                            finish_reason="interrupted",
                        )
                        return

                    # Check timeout
                    if time.time() - start_time > self._timeout:
                        raise TimeoutError(
                            f"Provider timed out after {self._timeout}s"
                        )

                    choices = chunk.choices
                    for choice in choices:
                        delta = choice.delta
                        if choice.finish_reason:
                            finish_reason = choice.finish_reason

                        # -- Text delta --
                        content = getattr(delta, "content", None) or ""
                        if content:
                            output_tokens += 1
                            yield ProviderEvent(
                                type=ProviderEventType.TEXT_DELTA,
                                text=content,
                            )

                        # -- Tool calls --
                        tool_calls = getattr(delta, "tool_calls", None) or []
                        for tc in tool_calls:
                            index = tc.index
                            key = str(index)
                            tool_id = tc.id or pending_tool_ids.get(key) or f"call_{uuid.uuid4().hex[:8]}"
                            func = tc.function or {}
                            tool_name = getattr(func, "name", "") or ""
                            tool_args_delta = getattr(func, "arguments", "") or ""

                            pending_tool_ids[key] = tool_id
                            pending_tool_name.setdefault(key, "")
                            pending_tool_args.setdefault(key, "")
                            if tool_name:
                                pending_tool_name[key] += tool_name
                            if tool_args_delta:
                                pending_tool_args[key] += tool_args_delta

                        if finish_reason == "tool_calls" and pending_tool_ids:
                            for key, tool_id in pending_tool_ids.items():
                                yield ProviderEvent(
                                    type=ProviderEventType.TOOL_USE,
                                    tool_call=ToolCall(
                                        id=tool_id,
                                        name=pending_tool_name.get(key, ""),
                                        arguments=pending_tool_args.get(key, "{}") or "{}",
                                    ),
                                )
                            pending_tool_ids.clear()
                            pending_tool_name.clear()
                            pending_tool_args.clear()

                        # -- Thinking (if model emits it) --
                        reasoning = getattr(delta, "reasoning_content", None) or ""
                        if reasoning:
                            output_tokens += 1
                            yield ProviderEvent(
                                type=ProviderEventType.THINKING_DELTA,
                                text=reasoning,
                            )

                    # -- Usage from final chunk --
                    if chunk.usage:
                        usage = {
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                        }
                        yield ProviderEvent(
                            type=ProviderEventType.USAGE,
                            input_tokens=usage.get("prompt_tokens", input_token_estimate),
                            output_tokens=usage.get("completion_tokens", output_tokens),
                            finish_reason=finish_reason or "stop",
                        )
            except Exception:
                if interrupt_check and interrupt_check():
                    yield ProviderEvent(
                        type=ProviderEventType.USAGE,
                        input_tokens=usage.get("prompt_tokens", input_token_estimate),
                        output_tokens=usage.get("completion_tokens", output_tokens),
                        finish_reason="interrupted",
                    )
                    return
                raise

            # Flush any remaining pending tool calls
            if pending_tool_ids:
                for key, tool_id in pending_tool_ids.items():
                    yield ProviderEvent(
                        type=ProviderEventType.TOOL_USE,
                        tool_call=ToolCall(
                            id=tool_id,
                            name=pending_tool_name.get(key, ""),
                            arguments=pending_tool_args.get(key, "{}") or "{}",
                        ),
                    )

            # Final usage if not provided in stream
            if not usage:
                yield ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=input_token_estimate,
                    output_tokens=output_tokens,
                    finish_reason=finish_reason or "stop",
                )
        finally:
            with self._active_stream_lock:
                self._active_stream = None

    def format_messages(self, messages: list[dict]) -> list[dict]:
        formatted: list[dict] = []

        for message in messages:
            role = message.get("role")
            if role != "tool":
                formatted.append(dict(message))
                continue

            tool_payload = _parse_tool_result_payload(message.get("content", ""))
            tool_call_id = str(tool_payload.get("tool_id") or message.get("tool_call_id") or f"call_{uuid.uuid4().hex[:8]}")
            tool_name = str(tool_payload.get("tool_name") or "tool")

            if not _previous_message_has_tool_call(formatted, tool_call_id):
                formatted.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                )

            tool_message = dict(message)
            tool_message["tool_call_id"] = tool_call_id
            formatted.append(tool_message)

        return formatted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_tokens(messages: list[dict]) -> int:
    """Rough token estimate from message list (4 chars â 1 token)."""
    total_chars = sum(
        len(m.get("content", "") or "")
        + len(json.dumps(m.get("tool_calls", []) or []))
        for m in messages
    )
    return max(1, total_chars // 4)


def _format_tools_for_openai(tools: list[dict]) -> list[dict]:
    formatted: list[dict] = []

    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        parameters = function.get("parameters")
        if not isinstance(parameters, dict) or not parameters:
            parameters = {"type": "object", "properties": {}}

        formatted.append(
            {
                "type": "function",
                "function": {
                    "name": str(function.get("name") or ""),
                    "description": str(function.get("description") or ""),
                    "parameters": parameters,
                },
            }
        )

    return formatted


def _should_retry_with_max_completion_tokens(exc: Exception, body: dict) -> bool:
    if "max_tokens" not in body:
        return False

    message = str(exc)
    return (
        "Unsupported parameter: 'max_tokens'" in message
        and "max_completion_tokens" in message
    )


def _parse_tool_result_payload(raw_content: object) -> dict:
    if not isinstance(raw_content, str):
        return {}
    try:
        payload = json.loads(raw_content)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _previous_message_has_tool_call(messages: list[dict], tool_call_id: str) -> bool:
    if not messages:
        return False
    previous = messages[-1]
    if previous.get("role") != "assistant":
        return False
    tool_calls = previous.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        return False
    return any(isinstance(tool_call, dict) and tool_call.get("id") == tool_call_id for tool_call in tool_calls)


def _build_retry_body_for_openai_error(exc: Exception, body: dict) -> dict | None:
    if _should_retry_with_max_completion_tokens(exc, body):
        retry_body = dict(body)
        retry_body["max_completion_tokens"] = retry_body.pop("max_tokens")
        return retry_body

    message = str(exc)
    if (
        "Unsupported value: 'temperature'" in message
        and "Only the default (1) value is supported." in message
        and body.get("temperature") != 1
    ):
        retry_body = dict(body)
        retry_body["temperature"] = 1
        return retry_body

    return None
