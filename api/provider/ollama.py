# Ollama provider
#
# Connects to a running Ollama server (default http://127.0.0.1:11434)
# via its /api/chat endpoint with streaming.

from __future__ import annotations

import json
import time
from threading import Lock
import uuid
from typing import Any, Generator

import requests

from api.provider.base import (
    Provider,
    ProviderConfig,
    ProviderEvent,
    ProviderEventType,
    ToolCall,
)


class OllamaProvider(Provider):
    """Provider for Ollama server.

    Expects a running Ollama daemon (default port 11434).
    Uses /api/chat endpoint with streaming.
    """

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        base = (self.config.server_url or "http://127.0.0.1:11434").rstrip("/")
        self._url = f"{base}/api/chat"
        self._timeout = self.config.timeout
        self._active_response = None
        self._active_response_lock = Lock()

    def cancel_active(self) -> None:
        with self._active_response_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def format_messages(self, messages: list[dict]) -> list[dict]:
        """Convert OpenAI-compatible messages to Ollama format.

        Ollama uses role + content; tool_calls are passed separately.
        """
        ollama_msgs = []
        for message in messages:
            ollama_msgs.append({"role": message["role"], "content": message.get("content") or ""})
        return ollama_msgs

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
        """Stream from Ollama, yielding structured events."""

        ollama_tools = None
        if tools:
            ollama_tools = []
            for tool in tools:
                ollama_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.get("function", {}).get("name", ""),
                            "description": tool.get("function", {}).get("description", ""),
                            "parameters": tool.get("function", {}).get("parameters", {}),
                        },
                    }
                )

        body = {
            "model": self.config.model_name or "llama3",
            "messages": self.format_messages(messages),
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if stop_sequences:
            body["options"]["stop"] = stop_sequences
        if ollama_tools:
            body["tools"] = ollama_tools

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        start_time = time.time()
        input_token_estimate = _count_tokens(messages)

        if interrupt_check and interrupt_check():
            yield ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=input_token_estimate,
                output_tokens=0,
                finish_reason="interrupted",
            )
            return

        resp = None
        try:
            resp = requests.post(
                self._url,
                json=body,
                headers=headers,
                stream=True,
                timeout=(min(10.0, self._timeout), self._timeout),
            )
        except requests.Timeout as exc:
            raise TimeoutError(f"Provider timed out after {self._timeout}s") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

        try:
            with self._active_response_lock:
                self._active_response = resp
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Ollama server error {resp.status_code}: {resp.text[:500]}"
                )

            output_tokens = 0
            pending_tool_args: dict[str, str] = {}
            pending_tool_name: dict[str, str] = {}
            pending_tool_ids: set[str] = set()
            last_chunk: dict[str, Any] = {}

            try:
                for line in resp.iter_lines(decode_unicode=True):
                    if interrupt_check and interrupt_check():
                        yield ProviderEvent(
                            type=ProviderEventType.USAGE,
                            input_tokens=last_chunk.get("prompt_eval_count", input_token_estimate),
                            output_tokens=last_chunk.get("eval_count", output_tokens),
                            finish_reason="interrupted",
                        )
                        return

                    if time.time() - start_time > self._timeout:
                        raise TimeoutError(f"Provider timed out after {self._timeout}s")

                    line = line.strip()
                    if not line:
                        continue

                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    last_chunk = chunk

                    message = chunk.get("message", {})
                    content = message.get("content", "")
                    if content:
                        output_tokens += 1
                        yield ProviderEvent(
                            type=ProviderEventType.TEXT_DELTA,
                            text=content,
                        )

                    tool_calls = message.get("tool_calls", [])
                    for tool_call in tool_calls:
                        func = tool_call.get("function", {})
                        tool_name = func.get("name", "")
                        tool_args_delta = func.get("arguments", "")
                        tool_call_id = tool_call.get("id", f"call_{uuid.uuid4().hex[:8]}")

                        pending_tool_args.setdefault(tool_call_id, "")
                        pending_tool_args[tool_call_id] += tool_args_delta
                        if tool_name:
                            pending_tool_name[tool_call_id] = tool_name
                        pending_tool_ids.add(tool_call_id)

                    if chunk.get("done") or chunk.get("done_reason"):
                        for tool_call_id in pending_tool_ids:
                            yield ProviderEvent(
                                type=ProviderEventType.TOOL_USE,
                                tool_call=ToolCall(
                                    id=tool_call_id,
                                    name=pending_tool_name.get(tool_call_id, ""),
                                    arguments=pending_tool_args.get(tool_call_id, "{}"),
                                ),
                            )
                        pending_tool_ids.clear()

                    thinking = message.get("thinking", "")
                    if thinking:
                        output_tokens += 1
                        yield ProviderEvent(
                            type=ProviderEventType.THINKING_DELTA,
                            text=thinking,
                        )
            except Exception:
                if interrupt_check and interrupt_check():
                    yield ProviderEvent(
                        type=ProviderEventType.USAGE,
                        input_tokens=last_chunk.get("prompt_eval_count", input_token_estimate),
                        output_tokens=last_chunk.get("eval_count", output_tokens),
                        finish_reason="interrupted",
                    )
                    return
                raise

            yield ProviderEvent(
                type=ProviderEventType.USAGE,
                input_tokens=last_chunk.get("prompt_eval_count", input_token_estimate),
                output_tokens=last_chunk.get("eval_count", output_tokens),
                finish_reason=last_chunk.get("done_reason", "stop"),
            )
        finally:
            with self._active_response_lock:
                self._active_response = None
            if resp is not None:
                resp.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_tokens(messages: list[dict]) -> int:
    """Rough token estimate from message list (4 chars ~= 1 token)."""
    total_chars = sum(
        len(message.get("content", "") or "")
        + len(json.dumps(message.get("tool_calls", []) or []))
        for message in messages
    )
    return max(1, total_chars // 4)
