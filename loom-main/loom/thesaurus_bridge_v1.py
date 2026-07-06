"""Thesaurus-style bridge expansion for baseline atlas scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from .atlas_seed_v1 import SeedAtlasRegion, build_seed_atlas


@dataclass(frozen=True)
class ThesaurusBridgeResult:
    concept: str
    region_scores: Dict[str, float]
    matched_terms: Dict[str, List[str]]


DEFAULT_SYNONYM_MAP: Dict[str, List[str]] = {
    "competition": ["contest", "sport", "playoff", "race", "final"],
    "identity": ["self", "persona", "reputation", "character", "biography"],
    "place": ["location", "island", "country", "city", "territory"],
    "survival": ["health", "safety", "climate", "food", "shelter"],
    "exploration": ["mission", "voyage", "expedition", "journey", "discovery"],
    "control": ["system", "management", "command", "govern", "regulation"],
    "context": ["overview", "background", "index", "meta", "list"],
    "culture": ["art", "music", "religion", "story", "language"],
    "science": ["theory", "research", "equation", "experiment", "physics"],
    "commerce": ["market", "trade", "economy", "business", "price"],
}


def _tokens(text: str) -> List[str]:
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
    return [token for token in re.split(r"[^a-z0-9]+", raw.lower()) if token]


def _normalize(scores: Dict[str, float]) -> Dict[str, float]:
    total = sum(max(0.0, value) for value in scores.values())
    if total <= 0:
        even = 1.0 / max(1, len(scores))
        return {key: even for key in scores}
    return {key: max(0.0, value) / total for key, value in scores.items()}


def score_with_thesaurus(
    concept: str,
    *,
    atlas: List[SeedAtlasRegion] | None = None,
    synonym_map: Dict[str, List[str]] | None = None,
) -> ThesaurusBridgeResult:
    chosen = atlas or build_seed_atlas()
    synonyms = synonym_map or DEFAULT_SYNONYM_MAP
    tokens = _tokens(concept)
    scores = {region.region_id: 0.05 for region in chosen}
    matched: Dict[str, List[str]] = {region.region_id: [] for region in chosen}
    for region in chosen:
        region_terms = set(region.anchors) | set(region.aliases)
        for anchor in region.anchors:
            region_terms.update(synonyms.get(anchor, ()))
        for token in tokens:
            if token in region_terms:
                scores[region.region_id] += 1.0
                matched[region.region_id].append(token)
            else:
                for term, bridged in synonyms.items():
                    if token == term and (term in region.anchors or term in region.aliases):
                        scores[region.region_id] += 0.55
                        matched[region.region_id].append(token)
                    elif token in bridged and term in region_terms:
                        scores[region.region_id] += 0.45
                        matched[region.region_id].append(token)
    return ThesaurusBridgeResult(
        concept=concept,
        region_scores=_normalize(scores),
        matched_terms={key: sorted(set(values)) for key, values in matched.items() if values},
    )
