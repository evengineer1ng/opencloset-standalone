#!/usr/bin/env python3
"""Score a decoder against the loomspeech benchmark.

    python bench/score.py <manifest.jsonl> <predictions.jsonl>

predictions.jsonl: one {"id": N, "text": "..."} per line. Reports exact-line accuracy and
position-wise word accuracy. The task: recover the source text from the audio alone.
"""
from __future__ import annotations

import json
import sys


def load(path: str) -> dict:
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out[d["id"]] = d.get("text", "")
    return out


def norm(text: str) -> list:
    return ["".join(c for c in w if c.isalpha()) for w in str(text).lower().split() if any(ch.isalpha() for ch in w)]


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: score.py <manifest.jsonl> <predictions.jsonl>")
    gold, pred = load(sys.argv[1]), load(sys.argv[2])
    n, exact, w_tot, w_ok = len(gold), 0, 0, 0
    for i, g in gold.items():
        gw, pw = norm(g), norm(pred.get(i, ""))
        if gw == pw:
            exact += 1
        w_tot += len(gw)
        w_ok += sum(1 for a, b in zip(gw, pw) if a == b)
    print(f"pairs:               {n}")
    print(f"exact-line accuracy: {exact}/{n} = {100 * exact / max(1, n):.1f}%")
    print(f"word accuracy:       {w_ok}/{w_tot} = {100 * w_ok / max(1, w_tot):.1f}%")


if __name__ == "__main__":
    main()
