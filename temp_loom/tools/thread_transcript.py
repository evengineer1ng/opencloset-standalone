"""Threaded transcript — the loom over a whole tape. Salience-seeded + causal threads + dedup,
so a flat list of events becomes a handful of traced stories.

    python -m tools.thread_transcript --tape data/f1_barcelona_2026.json \
        --grammar data/grammars/intern.json --verbs data/english/irregular_verbs.json \
        --rules data/f1_causal_rules.json --depth 2 --out transcripts/f1_threaded.txt

No player, no model. Deterministic, milliseconds.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from oradio_engine.speech import Grammar, roles_from_tags
from oradio_engine.thread import narrate_salient


def _event(row: dict) -> dict:
    roles = roles_from_tags(row.get("tags", []))
    lap = ""
    for tag in row.get("tags", []):
        if isinstance(tag, str) and tag.startswith("lap:"):
            lap = tag.split(":", 1)[1]
    roles["lap"] = lap
    roles["priority"] = row.get("priority", 0.5)
    return roles


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--grammar", required=True)
    ap.add_argument("--verbs", default="")
    ap.add_argument("--rules", default="")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--min-priority", type=float, default=0.7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    t0 = time.perf_counter()
    rows = json.load(open(args.tape, encoding="utf-8"))
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    events = [_event(r) for r in rows]
    grammar = Grammar.from_file(args.grammar, verbs=args.verbs or None)
    rules = json.load(open(args.rules, encoding="utf-8")) if args.rules else None

    stories = narrate_salient(events, grammar, depth=args.depth, rules=rules, min_priority=args.min_priority)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for lap, line in stories:
            f.write((f"[lap {lap}] " if lap else "") + line + "\n")

    elapsed = time.perf_counter() - t0
    print(f"{len(events)} events -> {len(stories)} traced stories  (depth {args.depth})")
    print(f"wrote {args.out}  in {elapsed * 1000:.0f} ms")


if __name__ == "__main__":
    main()
