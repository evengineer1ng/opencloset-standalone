#!/usr/bin/env python3
"""
Offline ontology harvester for ForkUniverse.

This is scaffolding, not a runtime dependency.
It uses public lexical sources to turn seed words into executable concept stubs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, List
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from forkuniverse.ontology.harvest import harvest_concept
from forkuniverse.ontology.models import ConceptRecord, ConceptRegistry
from forkuniverse.ontology.registry import (
    default_registry_path,
    load_concept_registry,
    merge_registries,
    write_concept_registry,
)


def load_seed_words(path: str | Path) -> List[str]:
    target = Path(path)
    lines = target.read_text(encoding="utf-8").splitlines()
    words: List[str] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        words.append(text)
    return words


def build_registry_from_words(
    words: Iterable[str],
    *,
    existing_registry: ConceptRegistry | None = None,
    replace_existing: bool = False,
) -> ConceptRegistry:
    concepts: List[ConceptRecord] = [harvest_concept(word) for word in words]
    if existing_registry is None:
        return ConceptRegistry(
            registry_id="forkuniverse_harvest_output",
            concepts=sorted(concepts, key=lambda item: item.concept_id),
        )
    return merge_registries(
        existing_registry,
        concepts,
        replace_existing=replace_existing,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ForkUniverse concept registries from lexical sources")
    parser.add_argument("words", nargs="*", help="Seed words to harvest")
    parser.add_argument("--word-file", type=str, default="", help="Optional text file of seed words")
    parser.add_argument("--base-registry", type=str, default="", help="Optional existing registry to merge into")
    parser.add_argument("--replace-existing", action="store_true", help="Replace existing concepts on concept_id match")
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output JSON path. Defaults to the base registry path if provided, otherwise stdout.",
    )
    args = parser.parse_args()

    words: List[str] = list(args.words)
    if args.word_file:
        words.extend(load_seed_words(args.word_file))
    if not words:
        default_words_path = Path(__file__).resolve().parents[1] / "data" / "forkuniverse_seed_concepts.txt"
        words.extend(load_seed_words(default_words_path))

    deduped_words: List[str] = []
    seen = set()
    for word in words:
        normalized = word.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped_words.append(word.strip())

    existing_registry = None
    if args.base_registry:
        existing_registry = load_concept_registry(args.base_registry)

    registry = build_registry_from_words(
        deduped_words,
        existing_registry=existing_registry,
        replace_existing=args.replace_existing,
    )

    text = json.dumps(registry.model_dump(mode="json"), indent=2, ensure_ascii=False)
    output_path = args.output
    if not output_path and args.base_registry:
        output_path = args.base_registry

    if output_path:
        write_concept_registry(registry, output_path)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
