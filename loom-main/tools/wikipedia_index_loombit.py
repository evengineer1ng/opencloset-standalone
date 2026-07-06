#!/usr/bin/env python3
"""Compile a Wikipedia multistream index file into loombit shard banks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loom.wikipedia_index_loombit import compile_wikipedia_index  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Compile a Wikipedia multistream index into loombit shard banks.")
    ap.add_argument("source")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--snapshot", default="")
    ap.add_argument("--max-entries", type=int, default=0)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = compile_wikipedia_index(
        args.source,
        outdir=args.out,
        snapshot=args.snapshot,
        max_entries=args.max_entries or None,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
