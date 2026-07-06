"""One pluggable LLM client — not wired to any single provider. Configured by env.

Default is your local Ollama. Set LOOM_LLM=openai to use ANY OpenAI-compatible endpoint
(OpenAI, Groq, Together, OpenRouter, a local llama.cpp/vLLM --server, ...). Every authoring/
benchmark tool and the colorist route through this, so nothing is hardcoded to one machine.

The deterministic engine never depends on this — it's an endpoint-layer helper.

env:
  LOOM_LLM           ollama (default) | openai
  LOOM_LLM_MODEL     default model id (any call can still override)
  LOOM_LLM_ENDPOINT  ollama generate url   (default http://127.0.0.1:11434/api/generate)
  LOOM_LLM_BASE      openai base url        (default https://api.openai.com/v1)
  LOOM_LLM_KEY       openai api key         (falls back to OPENAI_API_KEY)
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional, Tuple


def provider() -> str:
    return os.environ.get("LOOM_LLM", "ollama").strip().lower()


def default_model() -> str:
    return os.environ.get("LOOM_LLM_MODEL") or ("gpt-4o-mini" if provider() == "openai" else "qwen3:8b")


def _post(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _ollama(prompt, model, temperature, num_predict, think):
    url = os.environ.get("LOOM_LLM_ENDPOINT", "http://127.0.0.1:11434/api/generate")
    r = _post(url, {"model": model, "prompt": prompt, "stream": False, "think": think,
                    "options": {"temperature": temperature, "num_predict": num_predict}}, {})
    return (r.get("response") or ""), int(r.get("eval_count", 0)) + int(r.get("prompt_eval_count", 0))


def _openai(prompt, model, temperature, num_predict, think):
    base = os.environ.get("LOOM_LLM_BASE", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LOOM_LLM_KEY") or os.environ.get("OPENAI_API_KEY", "")
    r = _post(base + "/chat/completions",
              {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": temperature, "max_tokens": num_predict},
              {"Authorization": f"Bearer {key}"})
    return r["choices"][0]["message"]["content"], int(r.get("usage", {}).get("total_tokens", 0))


def complete(prompt: str, *, model: Optional[str] = None, temperature: float = 0.4,
             num_predict: int = 400, think: bool = False) -> Tuple[str, int]:
    """Return (text, tokens). `text` has any <think> block stripped. Provider chosen by env."""
    fn = _openai if provider() in ("openai", "openai-compatible") else _ollama
    text, tokens = fn(prompt, model or default_model(), temperature, num_predict, think)
    return re.sub(r"(?is)<think>.*?</think>", "", text or "").strip(), tokens
