#!/usr/bin/env python3
"""Run the Loom translator/judge ingress seam against tape data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loom.loombit_route import summarize_index
from oradio_engine.ollama_ingress import generate_candidates
from oradio_engine.query_codec import export_packet, query_tapes_via_ingress, render_evidence, render_meaning


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Loom ingress seam: human query -> judged machine intent -> deterministic packet")
    ap.add_argument("--tapes", required=True, help="Folder or file containing tape rows")
    ap.add_argument("--query", required=True, help="Raw human query")
    ap.add_argument("--translator-json", default="", help="Optional JSON file containing translator candidate brief(s)")
    ap.add_argument("--ollama-model", default="", help="Optional local Ollama model for translator candidates")
    ap.add_argument("--loombit-index", default="", help="Optional root loombit index for routing hints")
    ap.add_argument("--loombit-dict", default="", help="Optional .ldict for the loombit index")
    ap.add_argument("--no-search", action="store_true", help="Disable confidence-repair search")
    ap.add_argument("--json", action="store_true", help="Print the full packet as JSON")
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()
    payload = _load_json(args.translator_json) if args.translator_json else None
    if args.ollama_model:
        summary = summarize_index(args.loombit_index, dict_path=args.loombit_dict) if args.loombit_index else None
        payload = generate_candidates(args.query, model=args.ollama_model, index_summary=summary)
    packet = query_tapes_via_ingress(
        args.tapes,
        args.query,
        translator_payload=payload,
        enable_search=not args.no_search,
        loombit_index=args.loombit_index or None,
        loombit_dict=args.loombit_dict or None,
    )
    if args.json:
        print(json.dumps(export_packet(packet), indent=2))
        return 0

    print("\nINGRESS\n" + "-" * 72)
    ingress = packet.meta.get("ingress", {})
    print("human query:", ingress.get("original_query") or args.query)
    print("engine query:", ingress.get("accepted_query") or packet.meta.get("engine_query") or "")
    print("winner:", ((ingress.get("arbitration") or {}).get("winner_source")) or "unknown")
    print("\nMEANING\n" + "-" * 72)
    print(render_meaning(packet))
    print("\nEVIDENCE\n" + "-" * 72)
    print(render_evidence(packet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
