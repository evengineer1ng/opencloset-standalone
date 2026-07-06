from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from opencloset.storage import MessageRecord, ProviderRecord


class ProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ProviderHealth:
    status: str
    detail: str
    model_count: int


@dataclass
class ProviderReply:
    text: str
    input_tokens_est: int
    output_tokens_est: int


class ChatProvider(Protocol):
    async def health_check(self, provider: ProviderRecord) -> ProviderHealth:
        ...

    async def create_reply(
        self,
        provider: ProviderRecord,
        system_prompt: str,
        messages: list[MessageRecord],
    ) -> ProviderReply:
        ...


class OpenAICompatibleProvider:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def health_check(self, provider: ProviderRecord) -> ProviderHealth:
        endpoint = f"{provider.base_url.rstrip('/')}/models"
        headers = {}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        try:
            async with httpx.AsyncClient(timeout=provider.timeout_sec, transport=self.transport) as client:
                response = await client.get(endpoint, headers=headers if headers else None)
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", "Provider health check timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_unreachable", "Provider health check could not reach the backend.") from exc

        if response.status_code == 503:
            raise ProviderError("model_loading", "Provider is up but still loading the model.")
        if response.status_code >= 400:
            raise ProviderError("provider_bad_response", f"Provider returned HTTP {response.status_code}.")
        payload = response.json()
        model_count = len(payload.get("data", [])) if isinstance(payload, dict) else 0
        return ProviderHealth(status="ok", detail="Provider reachable.", model_count=model_count)

    async def create_reply(
        self,
        provider: ProviderRecord,
        system_prompt: str,
        messages: list[MessageRecord],
    ) -> ProviderReply:
        endpoint = f"{provider.base_url.rstrip('/')}/chat/completions"
        payload_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            if message.role in {"user", "assistant"}:
                payload_messages.append({"role": message.role, "content": message.content})

        payload = {
            "model": provider.model_name,
            "messages": payload_messages,
            "temperature": 0.7,
            "stream": False,
        }
        headers = {}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        try:
            async with httpx.AsyncClient(timeout=provider.timeout_sec, transport=self.transport) as client:
                response = await client.post(endpoint, json=payload, headers=headers if headers else None)
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", "Model request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_unreachable", "Model backend is unreachable.") from exc

        if response.status_code == 503:
            raise ProviderError("model_loading", "Model is still loading.")
        if response.status_code >= 400:
            raise ProviderError("provider_bad_response", f"Provider returned HTTP {response.status_code}.")

        body = response.json()
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("provider_bad_response", "Provider reply did not include choices.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("provider_bad_response", "Provider reply did not include assistant text.")
        usage = body.get("usage", {})
        input_tokens = int(usage.get("prompt_tokens", estimate_tokens_from_messages(payload_messages)))
        output_tokens = int(usage.get("completion_tokens", estimate_tokens(content)))
        return ProviderReply(
            text=content.strip(),
            input_tokens_est=input_tokens,
            output_tokens_est=output_tokens,
        )


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def estimate_tokens_from_messages(messages: list[dict[str, Any]]) -> int:
    total_chars = sum(len(str(message.get("content", ""))) for message in messages)
    return estimate_tokens("x" * total_chars)

