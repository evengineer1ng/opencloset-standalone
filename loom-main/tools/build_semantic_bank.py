#!/usr/bin/env python3
"""Build a deterministic semantic bank sidecar for atlas recall."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from loom.wikipedia_index_loombit import normalize_title, parse_index_line
from bench.atlas_recall.semantic_bank_v1 import write_semantic_bank

ATLAS = HERE / "bench" / "atlas_recall"
CORPUS = ATLAS / "corpus_src" / "enwiki-20260601-pages-articles-multistream-index.txt"
LABELS = ATLAS / "corpus_labels_27b.json"
OUTDIR = ATLAS / "compiled" / "semantic_bank_v1"


def main() -> None:
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    rows = []
    with CORPUS.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            leaf = parse_index_line(text, snapshot="2026-06-01", source_ref=CORPUS.name)
            rows.append(
                {
                    "concept_id": leaf["id"],
                    "title": leaf["title"],
                    "normalized_title": normalize_title(leaf["title"]),
                    "label": labels.get(leaf["id"]),
                }
            )
    OUTDIR.mkdir(parents=True, exist_ok=True)
    manifest = write_semantic_bank(
        rows,
        bank_path=OUTDIR / "semantic_bank_v1.sbk",
        manifest_path=OUTDIR / "semantic_bank_v1.manifest.json",
    )
    print(json.dumps({"outdir": str(OUTDIR), "count": manifest["count"], "dim": manifest["dim"]}, indent=2))


if __name__ == "__main__":
    main()
