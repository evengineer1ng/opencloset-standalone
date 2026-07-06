# llama.cpp HTTP server provider
#
# Connects to a running llama.cpp server (--host 127.0.0.1 --port 8080)
# via its OpenAI-compatible /v1/chat/completions endpoint.

from __future__ import annotations

import json
import time
from threading import Lock
import uuid
from typing import Generator

import requests

from api.provider.base import (
    Provider,
    ProviderConfig,
    ProviderEvent,
    ProviderEventType,
    ToolCall,
)


class LlamaCppProvider(Provider):
    """Provider for llama.cpp HTTP server.

    Expects a running llama.cpp server with:
      --host 127.0.0.1 --port 8080 --chat-template ...

    Uses the OpenAI-compatible /v1/chat/completions endpoint.
    """

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        base = (self.config.server_url or "http://127.0.0.1:8080").rstrip("/")
        self._url = f"{base}/v1/chat/completions"
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
        """Stream from llama.cpp server, yielding structured events."""

        body = {
            "model": self.config.model_name or "default",
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        options = self.config.options or {}
        if "cache_prompt" in options:
            body["cache_prompt"] = bool(options["cache_prompt"])
        if "n_cache_reuse" in options and options["n_cache_reuse"] is not None:
            body["n_cache_reuse"] = int(options["n_cache_reuse"])
        if options.get("reasoning_format"):
            body["reasoning_format"] = str(options["reasoning_format"])
        if options.get("chat_template_kwargs"):
            body["chat_template_kwargs"] = dict(options["chat_template_kwargs"])
        if "reasoning_budget" in options and options["reasoning_budget"] is not None:
            body["reasoning_budget"] = int(options["reasoning_budget"])
        if stop_sequences:
            body["stop"] = stop_sequences
        if tools:
            body["tools"] = tools

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
            raise RuntimeError(f"llama.cpp request failed: {exc}") from exc

        try:
            with self._active_response_lock:
                self._active_response = resp
            if resp.status_code != 200:
                body_text = resp.text[:500]
                if resp.status_code == 400 and "exceeds" in body_text and "context" in body_text:
                    raise RuntimeError(
                        f"Prompt too large for model context ({resp.status_code}): {body_text}"
                    )
                raise RuntimeError(
                    f"llama.cpp server error {resp.status_code}: {body_text}"
                )

            output_tokens = 0
            usage = {}
            finish_reason = ""
            pending_tool_args: dict[str, str] = {}
            pending_tool_name: dict[str, str] = {}
            pending_tool_ids: dict[str, str] = {}

            def _flush_completed_tool_calls() -> list[str]:
                completed_keys = [
                    key for key in pending_tool_ids
                    if pending_tool_name.get(key, "").strip()
                    and _is_tool_arguments_complete(pending_tool_args.get(key, "{}") or "{}")
                ]
                for key in completed_keys:
                    tool_id = pending_tool_ids[key]
                    yield ProviderEvent(
                        type=ProviderEventType.TOOL_USE,
                        tool_call=ToolCall(
                            id=tool_id,
                            name=pending_tool_name.get(key, ""),
                            arguments=pending_tool_args.get(key, "{}") or "{}",
                        ),
                    )
                return completed_keys

            try:
                for line in resp.iter_lines(decode_unicode=True):
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

                    line = line.strip()
                    if not line:
                        continue

                    # llama.cpp JSONL: each line is a complete JSON object
                    if line == "data: [DONE]":
                        break

                    if line.startswith("data: "):
                        json_str = line[6:]
                    else:
                        json_str = line

                    try:
                        chunk = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    for choice in choices:
                        delta = choice.get("delta", {})
                        finish_reason = choice.get("finish_reason", "")

                        # -- Text delta --
                        content = delta.get("content", "")
                        if content:
                            output_tokens += 1
                            yield ProviderEvent(
                                type=ProviderEventType.TEXT_DELTA,
                                text=content,
                            )

                        # -- Tool calls --
                        tool_calls = delta.get("tool_calls", [])
                        for tc in tool_calls:
                            index = tc.get("index")
                            key = str(index) if index is not None else tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
                            tool_id = tc.get("id") or pending_tool_ids.get(key) or f"call_{uuid.uuid4().hex[:8]}"
                            func = tc.get("function", {})
                            tool_name = func.get("name", "")
                            tool_args_delta = func.get("arguments", "")

                            pending_tool_ids[key] = tool_id
                            pending_tool_name.setdefault(key, "")
                            pending_tool_args.setdefault(key, "")
                            if tool_name:
                                pending_tool_name[key] += tool_name
                            if tool_args_delta:
                                pending_tool_args[key] += tool_args_delta

                        if pending_tool_ids:
                            completed_keys = yield from _flush_completed_tool_calls()
                            for key in completed_keys:
                                pending_tool_ids.pop(key, None)
                                pending_tool_name.pop(key, None)
                                pending_tool_args.pop(key, None)

                        # -- Thinking (if model emits it) --
                        # Some models put reasoning in a special field
                        reasoning = delta.get("reasoning_content", "")
                        if reasoning:
                            output_tokens += 1
                            yield ProviderEvent(
                                type=ProviderEventType.THINKING_DELTA,
                                text=reasoning,
                            )

                    # -- Usage from final chunk --
                    usage = chunk.get("usage", {})
                    if usage:
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

            if pending_tool_ids:
                # Emit only tool calls with complete JSON. If finish_reason is
                # "length" (hit max_tokens mid-call), the JSON will be truncated
                # and malformed — emitting it causes normalizer parse errors that
                # look like silent tool failures. Incomplete calls are dropped so
                # the loop sees no tool use and can recover via nudge or retry.
                yield from _flush_completed_tool_calls()

            # Final usage if not provided in stream
            if not usage:
                yield ProviderEvent(
                    type=ProviderEventType.USAGE,
                    input_tokens=input_token_estimate,
                    output_tokens=output_tokens,
                    finish_reason=finish_reason or "stop",
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
    """Rough token estimate from message list (4 chars ≈ 1 token)."""
    total_chars = sum(
        len(m.get("content", "") or "")
        + len(json.dumps(m.get("tool_calls", []) or []))
        for m in messages
    )
    return max(1, total_chars // 4)


def _is_tool_arguments_complete(raw_args: str) -> bool:
    text = (raw_args or "").strip()
    if not text:
        return False

    depth_curly = 0
    depth_square = 0
    in_string = False
    escape_next = False

    for char in text:
        if in_string:
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth_curly += 1
        elif char == "}":
            depth_curly -= 1
            if depth_curly < 0:
                return False
        elif char == "[":
            depth_square += 1
        elif char == "]":
            depth_square -= 1
            if depth_square < 0:
                return False

    return not in_string and depth_curly == 0 and depth_square == 0
