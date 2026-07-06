# Provider subsystem — multi-model bridge

"""
Provider abstraction for local LLM backends.

Providers return structured events (text deltas, tool_use blocks, thinking)
rather than plain text. This enables the agent loop to detect tool calls
mid-stream and execute them before continuing.

Supported backends:
- llama.cpp HTTP server
- Ollama

See `base.py` for the Provider interface and event types.
"""

from api.provider.base import (
    Provider,
    ProviderConfig,
    ProviderEvent,
    ProviderEventType,
    ProviderResult,
    ToolCall,
    create_provider,
)

try:
    from api.provider.openai import OpenAIProvider
except Exception:  # optional dependency / import path
    OpenAIProvider = None

__all__ = [
    "Provider",
    "ProviderConfig",
    "ProviderEvent",
    "ProviderEventType",
    "ProviderResult",
    "ToolCall",
    "create_provider",
]

if OpenAIProvider is not None:
    __all__.append("OpenAIProvider")
