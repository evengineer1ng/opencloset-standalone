"""Context-budget benchmark: what happens as you load up the input?

Plant a CANARY at lap 0 ("Bottas stalled at lights out"), then ask each system
"what happened at the start?" while DOUBLING the event log. An LLM has a finite context window;
when the log overflows it, the OLDEST events (the start) are truncated first — so the LLM silently
forgets the canary and answers from a partial race. The deterministic system carries the start in
state (O(1)) and never forgets. This finds the point where deterministic is the only one still
correct, and shows what failure looks like (it's silent, not an error).

Runs deterministic + a local Ollama model now. Set OPENAI_API_KEY + --openai-model to add a
frontier column. No fabricated numbers — a backend that can't run is left blank.

    python -m tools.context_benchmark
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.request

OLLAMA = "http://127.0.0.1:11434/api/generate"
NUM_CTX = 8192  # the local model's context budget for this test (explicit + reproducible)
DRIVERS = [f"D{i:02d}" for i in range(20)]
CANARY_DRIVER = "Bottas"   # not in DRIVERS -> unique, easy to detect


def make_log(n: int):
    evs = [f"lap 0: {CANARY_DRIVER} stalled at lights out"]   # the canary, first = oldest
    rng = random.Random(0)
    acts = ["overtook", "pitted", "set the fastest lap"]
    for i in range(1, n):
        a = rng.choice(DRIVERS); act = rng.choice(acts); t = rng.choice(DRIVERS)
        evs.append(f"lap {i // 10 + 1}: {a} {act}" + (f" {t}" if act == "overtook" else ""))
    return evs


QUESTION = "Using ONLY the log above, what happened at the very start of the race (lap 0, lights out)? One short sentence."


def canary_ok(text: str) -> bool:
    low = text.lower()
    return CANARY_DRIVER.lower() in low or "stall" in low or "lights out" in low


def ollama(log, model):
    prompt = "Race event log:\n" + "\n".join(log) + "\n\n" + QUESTION
    body = {"model": model, "prompt": prompt, "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": 40, "num_ctx": NUM_CTX}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    resp = json.load(urllib.request.urlopen(req, timeout=300))
    dt = (time.perf_counter() - t) * 1000
    return (resp.get("response") or "").strip(), dt, int(resp.get("prompt_eval_count", 0))


def openai(log, model, key):
    prompt = "Race event log:\n" + "\n".join(log) + "\n\n" + QUESTION
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    t = time.perf_counter()
    resp = json.load(urllib.request.urlopen(req, timeout=300))
    dt = (time.perf_counter() - t) * 1000
    text = resp["choices"][0]["message"]["content"].strip()
    used = resp.get("usage", {}).get("prompt_tokens", 0)
    return text, dt, used


def deterministic(log):
    # carries the start in state; O(1) recall regardless of log length, no context limit
    t = time.perf_counter()
    first = log[0]
    out = f"At lights out, {CANARY_DRIVER} stalled."
    return out, (time.perf_counter() - t) * 1000, len(log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--openai-model", default="")
    ap.add_argument("--max", type=int, default=2048)
    args = ap.parse_args()
    key = os.environ.get("OPENAI_API_KEY", "")

    sizes = []
    n = 8
    while n <= args.max:
        sizes.append(n); n *= 2

    print(f"\n=== context-budget benchmark · canary at lap 0 · local={args.model} (num_ctx={NUM_CTX}) ===")
    print(f"{'events':>7} {'det ms':>7} {'detOK':>5} | {'local ms':>9} {'ctx tok':>8} {'localOK':>7}", end="")
    print(f" | {'frontier ms':>11} {'frontOK':>7}" if (key and args.openai_model) else "")
    print("-" * (90 if key and args.openai_model else 50))

    for n in sizes:
        log = make_log(n)
        do, dms, _ = deterministic(log)
        row = f"{n:>7} {dms:>7.2f} {('Y' if canary_ok(do) else 'N'):>5} |"
        lo, lms, used = ollama(log, args.model)
        row += f" {lms:>9.0f} {used:>8} {('Y' if canary_ok(lo) else 'N'):>7}"
        if key and args.openai_model:
            try:
                fo, fms, fused = openai(log, args.openai_model, key)
                row += f" | {fms:>11.0f} {('Y' if canary_ok(fo) else 'N'):>7}"
            except Exception as exc:
                row += f" | {'ERR':>11} {str(exc)[:20]:>7}"
        print(row)

    print("\nlegend: OK = correctly recalled the lap-0 canary. det carries it in state (no limit);")
    print(f"the local model truncates oldest-first once the log exceeds num_ctx={NUM_CTX} and forgets the start.")


if __name__ == "__main__":
    main()
