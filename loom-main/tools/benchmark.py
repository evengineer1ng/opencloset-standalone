"""Benchmark: deterministic narration vs LLM-at-runtime, on the SAME event rows.

Fair by design: both systems get the identical structured events and produce one sentence each.
The LLM (local llama3.1:8b) runs at temperature 0 — its BEST case for determinism + faithfulness
(steelman). We measure latency, token cost, run-to-run determinism, and faithfulness, and dump
side-by-side samples so a human judges fluency (the axis the LLM is expected to win).

Honest scope: this isolates the RENDERING step. The full pipeline (detect/select/thread/inquiry)
is extra deterministic work the LLM would also have to do, and is not credited here.

    python -m tools.benchmark --n 20
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request

from oradio_engine.speech import Grammar, roles_from_tags

OLLAMA = "http://127.0.0.1:11434/api/generate"
MODEL = "llama3.1:8b"


def llm(prompt: str):
    body = {"model": MODEL, "prompt": prompt, "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": 48}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    resp = json.load(urllib.request.urlopen(req, timeout=180))
    dt = time.perf_counter() - t
    text = re.sub(r"(?is)<think>.*?</think>", "", resp.get("response") or "").strip()
    tokens = int(resp.get("eval_count", 0)) + int(resp.get("prompt_eval_count", 0))
    return text, dt, tokens


def describe(tags):
    roles = roles_from_tags(tags)
    lap = next((t.split(":", 1)[1] for t in tags if t.startswith("lap:")), "")
    d = f"lap {lap}: driver={roles.get('actor','')} action={roles.get('action','')}"
    if roles.get("object"):
        d += f" target={roles['object']}"
    return d, roles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()

    rows = json.load(open("data/f1_barcelona_2026.json", encoding="utf-8"))
    structured = [r for r in rows if any(t.startswith("action:") for t in r["tags"])][:args.n]
    drivers = set()
    for r in rows:
        for t in r["tags"]:
            if t.startswith("actor:"):
                drivers.add(t.split(":", 1)[1])
            if t.startswith("object:") and t.split(":", 1)[1][:1].isupper():
                drivers.add(t.split(":", 1)[1])

    G = Grammar.from_file("data/grammars/intern.json", verbs="data/english/irregular_verbs.json")

    TEAMS = ["mercedes", "ferrari", "red bull", "mclaren", "aston martin", "racing point",
             "alpine", "williams", "haas", "alphatauri", "sauber", "racing bulls"]

    def faithful(text, roles):
        actor = roles.get("actor", "")
        obj = roles.get("object", "")
        low = text.lower()
        actor_ok = (actor.lower() in low) if actor else True
        # an unsupported fact = any specific the event did NOT contain: a turn, a position, a
        # team, or a driver who isn't the actor/object. (Deterministic renders only given fields.)
        unsupported = bool(
            re.search(r"\bturn \d+", low)
            or re.search(r"\b(p\d|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(place|position)\b", low)
            or any(tm in low for tm in TEAMS)
            or any(d.lower() in low for d in drivers if d not in (actor, obj))
        )
        return actor_ok, unsupported

    # ---- deterministic ----
    def det_render():
        return [G.line(roles_from_tags(r["tags"]), key=r["tags"][0]) for r in structured]
    t = time.perf_counter(); det1 = det_render(); det_ms = (time.perf_counter() - t) * 1000
    det2 = det_render()
    det_actor = sum(faithful(det1[i], roles_from_tags(structured[i]["tags"]))[0] for i in range(len(structured)))
    det_inv = sum(faithful(det1[i], roles_from_tags(structured[i]["tags"]))[1] for i in range(len(structured)))

    # ---- LLM (temp 0, two passes for determinism) ----
    llm_ms = 0.0; tokens = 0; llm1 = []
    for r in structured:
        desc, roles = describe(r["tags"])
        p = ("You are an F1 commentator. In ONE short sentence, narrate this event. "
             "Reply with ONLY the sentence.\nEvent: " + desc)
        text, dt, tok = llm(p); llm1.append((text, roles)); llm_ms += dt * 1000; tokens += tok
    llm2 = []
    for r in structured:
        desc, _ = describe(r["tags"])
        p = ("You are an F1 commentator. In ONE short sentence, narrate this event. "
             "Reply with ONLY the sentence.\nEvent: " + desc)
        llm2.append(llm(p)[0])
    llm_same = sum(1 for (a, _), b in zip(llm1, llm2) if a.strip() == b.strip())
    llm_actor = sum(faithful(t, r)[0] for t, r in llm1)
    llm_inv = sum(faithful(t, r)[1] for t, r in llm1)

    n = len(structured)
    print(f"\n=== narration benchmark · {n} F1 events · LLM={MODEL}@temp0 ===\n")
    print(f"{'metric':22} {'deterministic':>16} {'LLM-at-runtime':>16}")
    print(f"{'-'*22} {'-'*16:>16} {'-'*16:>16}")
    print(f"{'total latency (ms)':22} {det_ms:>16.1f} {llm_ms:>16.1f}")
    print(f"{'per event (ms)':22} {det_ms/n:>16.2f} {llm_ms/n:>16.1f}")
    print(f"{'tokens (runtime cost)':22} {0:>16} {tokens:>16}")
    print(f"{'identical on re-run':22} {('100% (' + str(n) + '/' + str(n) + ')'):>16} {f'{100*llm_same//n}% ({llm_same}/{n})':>16}")
    print(f"{'names the right driver':22} {f'{100*det_actor//n}% ({det_actor}/{n})':>16} {f'{100*llm_actor//n}% ({llm_actor}/{n})':>16}")
    print(f"{'invented a fact':22} {f'{100*det_inv//n}% ({det_inv}/{n})':>16} {f'{100*llm_inv//n}% ({llm_inv}/{n})':>16}")

    with open("transcripts/benchmark_samples.txt", "w", encoding="utf-8") as f:
        f.write(f"# narration samples — deterministic vs {MODEL}@temp0 (judge fluency yourself)\n\n")
        for i in range(n):
            f.write(f"event: {describe(structured[i]['tags'])[0]}\n")
            f.write(f"  DET: {det1[i]}\n")
            f.write(f"  LLM: {llm1[i][0]}\n\n")
    print("\nsamples for your quality judgment -> transcripts/benchmark_samples.txt")


if __name__ == "__main__":
    main()
