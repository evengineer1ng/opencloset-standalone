#!/usr/bin/env python3
"""Populate a baseline coordinate-map batch from a Wikipedia index slice."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loom.baseline_population_runner_v1 import populate_baseline_from_wikipedia_index  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Populate a baseline coordinate-map batch from a Wikipedia index slice.")
    ap.add_argument("source")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--max-entries", type=int, default=128)
    ap.add_argument("--labels-json", default="")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--dictionary-weight", type=float, default=0.33)
    ap.add_argument("--thesaurus-weight", type=float, default=0.33)
    ap.add_argument("--llm-weight", type=float, default=0.34)
    return ap


def _load_labels(path_text: str) -> dict[str, dict]:
    if not path_text:
        return {}
    return json.loads(Path(path_text).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    labels = _load_labels(args.labels_json)
    batch = populate_baseline_from_wikipedia_index(
        args.source,
        max_entries=args.max_entries,
        llm_labels=labels,
        width=args.width,
        height=args.height,
        dictionary_weight=args.dictionary_weight,
        thesaurus_weight=args.thesaurus_weight,
        llm_weight=args.llm_weight,
    )
    llm_tape_path = outdir / "llm_baseline_tape.json"
    batch_path = outdir / "baseline_population_batch.json"
    llm_tape_path.write_text(json.dumps(batch.llm_tape, indent=2, ensure_ascii=False), encoding="utf-8")
    batch_path.write_text(json.dumps(batch.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "snapshot": batch.snapshot,
                "rows": len(batch.source_rows),
                "items": len(batch.items),
                "llm_tape_path": str(llm_tape_path),
                "batch_path": str(batch_path),
                "overlays": sorted(batch.overlay_cells.keys()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
