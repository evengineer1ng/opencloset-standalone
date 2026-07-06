"""Optional local Ollama translator for ingress candidate briefs."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict


OLLAMA_URL = "http://127.0.0.1:11434/api/generate"


def _extract_json(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {"candidates": []}
    try:
        return json.loads(raw)
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {"candidates": []}
    return json.loads(match.group(0))


def build_prompt(query: str, index_summary: Dict[str, Any] | None = None) -> str:
    topology = json.dumps(index_summary or {}, ensure_ascii=False)
    return (
        "You are an ingress translator for a deterministic query engine.\n"
        "Return ONLY valid JSON.\n"
        "Do not answer the user question.\n"
        "Produce 1 to 3 candidate machine-intent briefs.\n"
        "Each candidate must use this shape:\n"
        "{"
        "\"source\":\"ollama\","
        "\"normalized_query\":\"...\","
        "\"transform\":\"summary|define|actor_action|causal|sequence|time|count|rank|evaluation|interpretive\","
        "\"focus\":\"...\","
        "\"entities\":[\"...\"],"
        "\"time_scope\":\"...\","
        "\"constraints\":[\"...\"],"
        "\"ambiguities\":[\"...\"],"
        "\"retrieval_plan\":[\"...\"],"
        "\"confidence\":0.0,"
        "\"gradient_bucket\":\"...\","
        "\"aim_tokens\":[\"...\"],"
        "\"preferred_paths\":[\"...\"]"
        "}\n"
        "Top-level loombit index preview:\n"
        f"{topology}\n"
        "Use `gradient_bucket`, `aim_tokens`, and `preferred_paths` only as coarse routing hints.\n"
        "Prefer hints that match the provided index preview.\n"
        "User query:\n"
        f"{query}\n"
        "Output JSON object with key `candidates`."
    )


def generate_candidates(
    query: str,
    *,
    model: str = "tinyllama:1.1b",
    index_summary: Dict[str, Any] | None = None,
    temperature: float = 0.0,
    timeout: int = 90,
) -> Dict[str, Any]:
    payload = {
        "model": model,
        "prompt": build_prompt(query, index_summary=index_summary),
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
        },
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "candidates": [],
            "_ollama": {
                "model": model,
                "error": f"http_{exc.code}",
                "detail": detail[:400],
            },
        }
    except Exception as exc:
        return {
            "candidates": [],
            "_ollama": {
                "model": model,
                "error": type(exc).__name__,
                "detail": str(exc)[:400],
            },
        }

    response = body.get("response") or ""
    parsed = _extract_json(response)
    if "candidates" not in parsed or not isinstance(parsed.get("candidates"), list):
        parsed = {"candidates": []}
    parsed["_ollama"] = {
        "model": model,
        "done": body.get("done", True),
        "eval_count": body.get("eval_count"),
    }
    return parsed
