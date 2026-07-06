# Provider abstraction — structured streaming interface
#
# Design principle (from Claude Code research):
#   Provider returns structured events, not just final text.
#   Tool_use blocks are detected mid-stream so the agent loop can
#   execute tools before asking the model to continue.

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generator


# ---------------------------------------------------------------------------
# Structured event types
# ---------------------------------------------------------------------------


class ProviderEventType(str, Enum):
    """Kinds of events a provider yields during streaming."""
    TEXT_DELTA = "text_delta"       # incremental assistant text
    TOOL_USE = "tool_use"            # model emitted a tool call
    THINKING_DELTA = "thinking_delta"  # model thinking/reasoning block
    USAGE = "usage"                  # final token usage stats


@dataclass
class ToolCall:
    """A single tool call emitted by the model."""
    id: str
    name: str
    arguments: str  # JSON string; parsed by agent loop


@dataclass
class ProviderEvent:
    """A single structured event from the provider stream."""
    type: ProviderEventType
    text: str = ""                 # for TEXT_DELTA, THINKING_DELTA
    tool_call: ToolCall | None = None  # for TOOL_USE
    input_tokens: int = 0
    output_tokens: int = 0        # for USAGE
    finish_reason: str = ""


@dataclass
class ProviderResult:
    """Final accumulated result after a provider call completes."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""


@dataclass
class ProviderConfig:
    """Connection / environment config for a provider backend."""
    server_url: str = ""              # e.g. "http://127.0.0.1:8080"
    model_name: str = ""              # e.g. "qwen3.6-27b"
    timeout: float = 300.0            # per-request timeout in seconds
    api_key: str = ""                 # optional auth
    options: dict[str, Any] = field(default_factory=dict)  # backend-specific request options


InterruptCheck = Callable[[], bool]


def coalesce_finish_reason(*reasons: str | None) -> str:
    """Return the first non-empty finish reason, defaulting to 'completed'."""
    for reason in reasons:
        if reason and str(reason).strip():
            return str(reason)
    return "completed"


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class Provider(ABC):
    """Abstract provider interface.

    Providers convert the OpenCloset `Message` chain into the format expected
    by the backend, stream structured events, and accumulate a `ProviderResult`.
    """

    @abstractmethod
    def run_stream(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        tools: list[dict] | None = None,
        interrupt_check: InterruptCheck | None = None,
    ) -> Generator[ProviderEvent, None, None]:
        """Yield structured events from the model stream.

        Args:
            messages: OpenAI-compatible message list (role + content + tool_calls).
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            stop_sequences: Sequences that stop generation.
            tools: Tool definitions for tool-use models (OpenAI format).
        """
        ...

    def run(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        tools: list[dict] | None = None,
        interrupt_check: InterruptCheck | None = None,
    ) -> ProviderResult:
        """Convenience: consume stream and return accumulated result."""
        result = ProviderResult()
        for event in self.run_stream(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop_sequences=stop_sequences,
            tools=tools,
            interrupt_check=interrupt_check,
        ):
            if event.type == ProviderEventType.TEXT_DELTA:
                result.text += event.text
            elif event.type == ProviderEventType.TOOL_USE:
                if event.tool_call:
                    result.tool_calls.append(event.tool_call)
            elif event.type == ProviderEventType.THINKING_DELTA:
                result.thinking += event.text
            elif event.type == ProviderEventType.USAGE:
                result.input_tokens = event.input_tokens
                result.output_tokens = event.output_tokens
                result.finish_reason = event.finish_reason
        return result

    def format_messages(self, messages: list[dict]) -> list[dict]:
        """Convert OpenAI-compatible messages to backend-specific format.

        Default passthrough; subclasses override for non-OpenAI backends.
        """
        return list(messages)

    def cancel_active(self) -> None:
        """Best-effort cooperative cancel for an in-flight request."""
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_provider(backend: str, config: ProviderConfig | None = None) -> Provider:
    """Create a provider instance by backend name.

    Supported backends: "llamacpp", "ollama".
    """
    config = config or ProviderConfig()

    if backend == "llamacpp":
        from api.provider.llamacpp import LlamaCppProvider
        return LlamaCppProvider(config)
    elif backend == "ollama":
        from api.provider.ollama import OllamaProvider
        return OllamaProvider(config)
    elif backend == "openai":
        from api.provider.openai import OpenAIProvider
        return OpenAIProvider(config)
    else:
        raise ValueError(f"Unknown provider backend: {backend!r}")
