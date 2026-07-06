from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loom.text_loombit import convert_text_file  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Convert a large text file into chunked loombit artifacts.")
    ap.add_argument("source", help="Path to a UTF-8 text file")
    ap.add_argument("-o", "--outdir", default="", help="Output directory for the loombit family")
    ap.add_argument("--chunk-chars", type=int, default=12000, help="Maximum characters per text chunk")
    ap.add_argument("--title", default="", help="Optional title embedded in the text index/chunks")
    ap.add_argument("--prefix", default="", help="Artifact filename prefix")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = convert_text_file(
        args.source,
        outdir=args.outdir or None,
        chunk_chars=args.chunk_chars,
        title=args.title,
        prefix=args.prefix,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
