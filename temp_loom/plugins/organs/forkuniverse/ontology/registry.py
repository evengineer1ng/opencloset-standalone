from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import ConceptRecord, ConceptRegistry


def default_registry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "forkuniverse_concepts.json"


def load_concept_registry(path: str | Path | None = None) -> ConceptRegistry:
    target = Path(path) if path is not None else default_registry_path()
    payload = json.loads(target.read_text(encoding="utf-8"))
    return ConceptRegistry.model_validate(payload)


def concept_map(registry: ConceptRegistry) -> dict[str, ConceptRecord]:
    return {concept.concept_id: concept for concept in registry.concepts}


def select_concepts(
    registry: ConceptRegistry,
    concept_ids: Iterable[str],
) -> list[ConceptRecord]:
    mapped = concept_map(registry)
    selected: list[ConceptRecord] = []
    seen: set[str] = set()
    for concept_id in concept_ids:
        key = concept_id.strip().lower()
        if not key or key in seen or key not in mapped:
            continue
        selected.append(mapped[key])
        seen.add(key)
    return selected


def merge_registries(
    base: ConceptRegistry,
    additions: Iterable[ConceptRecord],
    *,
    replace_existing: bool = False,
) -> ConceptRegistry:
    merged = concept_map(base)
    for concept in additions:
        if replace_existing or concept.concept_id not in merged:
            merged[concept.concept_id] = concept
    ordered = sorted(merged.values(), key=lambda item: item.concept_id)
    return ConceptRegistry(
        registry_id=base.registry_id,
        concepts=ordered,
    )


def write_concept_registry(registry: ConceptRegistry, path: str | Path) -> None:
    target = Path(path)
    target.write_text(
        json.dumps(registry.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
