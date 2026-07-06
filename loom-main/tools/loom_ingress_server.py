#!/usr/bin/env python3
"""Run the local Loom ingress HTTP server."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oradio_engine.local_ingress_server import serve  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run a tiny local Loom ingress server.")
    ap.add_argument("--tapes", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8844)
    ap.add_argument("--loombit-index", default="")
    ap.add_argument("--loombit-dict", default="")
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--ollama-model", default="", help="Optional default local Ollama ingress model.")
    ap.add_argument("--ollama-timeout", type=int, default=90, help="Timeout for local Ollama ingress calls.")
    ap.add_argument("--atlas-dir", default="", help="Embedding atlas pack dir (doc_vectors.npy + atlas_meta.json) to enable POST /retrieve.")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return serve(
        host=args.host,
        port=args.port,
        tapes=args.tapes,
        loombit_index=args.loombit_index or None,
        loombit_dict=args.loombit_dict or None,
        enable_search=bool(args.search),
        ollama_model=args.ollama_model,
        ollama_timeout=args.ollama_timeout,
        atlas_dir=args.atlas_dir or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
