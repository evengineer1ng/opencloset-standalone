"""Read the tape instead of hearing it — dump an .oradio's spoken lines to a .txt, in order.

No player, no audio, no model: the engine ticks the tape to exhaustion and the voice effectors'
spoken rows are written as text. Deterministic, so the transcript is the same every run.

    python -m tools.transcript --oradio spec/examples/f1.oradio --out transcripts/f1.txt
"""
from __future__ import annotations

import argparse
import os
import time

from oradio_engine.club import Club
from oradio_engine.loader import open_oradio


def _lap_of(cand) -> str:
    for tag in cand.tags:
        if isinstance(tag, str) and tag.startswith("lap:"):
            return tag.split(":", 1)[1]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oradio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-ticks", type=int, default=20000)
    args = ap.parse_args()

    t0 = time.perf_counter()
    res = open_oradio(args.oradio, club=Club())
    if not res.ok or res.engine is None:
        raise SystemExit(f"could not open {args.oradio}: {res.report.summary()}")
    eng = res.engine

    lines = []
    current_lap = ""
    idle = 0
    for _ in range(args.max_ticks):
        produced = eng.tick(1)
        if not produced:
            idle += 1
            if idle >= 3:
                break
            continue
        idle = 0
        # write spoken rows FIRST (they answer the PRIOR tick's source row -> correct lap),
        # then advance current_lap from this tick's source rows.
        for c in produced:
            if c.type == "spoken":
                if lines and lines[-1].split("] ", 1)[-1] == c.body:
                    continue  # guard against a back-to-back identical line
                prefix = f"[lap {current_lap}] " if current_lap else ""
                lines.append(f"{prefix}{c.body}")
        for c in produced:
            if c.type != "spoken":
                lap = _lap_of(c)
                if lap:
                    current_lap = lap

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(f"# transcript of {res.name}\n\n")
        f.write("\n".join(lines) + "\n")

    elapsed = time.perf_counter() - t0
    print(f"wrote {len(lines)} lines -> {args.out}")
    print(f"elapsed: {elapsed * 1000:.0f} ms  ({len(lines) / elapsed:.0f} lines/sec)")


if __name__ == "__main__":
    main()
