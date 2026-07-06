"""Deterministic baseline prior generation from dictionary and optional LLM hints."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

from .atlas_seed_v1 import SeedAtlasRegion, build_seed_atlas
from .thesaurus_bridge_v1 import score_with_thesaurus
from .x_region_schema_v1 import XRegion, default_x_regions


@dataclass(frozen=True)
class BaselinePrior:
    concept_id: str
    title: str
    primary_region: str
    region_scores: Dict[str, float]
    llm_weight: float
    dictionary_weight: float
    notes: tuple[str, ...]


def _tokens(text: str) -> List[str]:
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
    return [token for token in re.split(r"[^a-z0-9]+", raw.lower()) if token]


def _normalize(scores: Dict[str, float], region_ids: List[str]) -> Dict[str, float]:
    total = sum(max(0.0, scores.get(region_id, 0.0)) for region_id in region_ids)
    if total <= 0:
        even = 1.0 / max(1, len(region_ids))
        return {region_id: even for region_id in region_ids}
    return {region_id: max(0.0, scores.get(region_id, 0.0)) / total for region_id in region_ids}


def _dictionary_scores(title: str, namespace: str, regions: List[XRegion]) -> Dict[str, float]:
    text_tokens = _tokens(title) + _tokens(namespace)
    scores = {region.id: 0.05 for region in regions}
    for region in regions:
        for keyword in region.keywords:
            if keyword in text_tokens:
                scores[region.id] += 1.0
        for alias in region.aliases:
            if alias in text_tokens:
                scores[region.id] += 0.65
    if namespace:
        lowered = namespace.lower()
        if lowered in ("category", "template", "module", "portal"):
            scores["context"] += 0.8
        if lowered in ("file", "media", "mediawiki"):
            scores["media"] += 0.7
    if any(token in text_tokens for token in ("list", "index", "timeline", "outline")):
        scores["context"] += 0.9
    if "(" in title and ")" in title:
        scores["context"] += 0.35
    return scores


def _llm_scores(raw_prior: Dict[str, Any] | None, region_ids: List[str]) -> Dict[str, float]:
    scores = {region_id: 0.0 for region_id in region_ids}
    if not raw_prior:
        return scores
    raw_scores = raw_prior.get("region_scores") if isinstance(raw_prior, dict) else None
    if isinstance(raw_scores, dict):
        for key, value in raw_scores.items():
            if key in scores:
                scores[key] = max(0.0, float(value))
    primary = raw_prior.get("primary_region") if isinstance(raw_prior, dict) else ""
    if primary in scores:
        scores[primary] += 0.35
    secondary = raw_prior.get("secondary_regions") if isinstance(raw_prior, dict) else []
    if isinstance(secondary, list):
        for idx, region_id in enumerate(secondary):
            if region_id in scores:
                scores[region_id] += max(0.0, 0.18 - idx * 0.04)
    return scores


def _thesaurus_scores(
    title: str,
    *,
    atlas: List[SeedAtlasRegion] | None,
    region_ids: List[str],
) -> Dict[str, float]:
    result = score_with_thesaurus(title, atlas=atlas)
    return {region_id: max(0.0, result.region_scores.get(region_id, 0.0)) for region_id in region_ids}


def build_baseline_prior(
    source_row: Dict[str, Any],
    *,
    llm_prior: Dict[str, Any] | None = None,
    llm_weight: float = 0.34,
    dictionary_weight: float = 0.33,
    thesaurus_weight: float = 0.33,
    regions: List[XRegion] | None = None,
    atlas: List[SeedAtlasRegion] | None = None,
) -> BaselinePrior:
    chosen_regions = regions or default_x_regions()
    chosen_atlas = atlas or build_seed_atlas(chosen_regions)
    region_ids = [region.id for region in chosen_regions]
    title = str(source_row.get("title", ""))
    namespace = str(source_row.get("namespace", ""))
    concept_id = str(source_row.get("id") or source_row.get("normalized_title") or title)
    dict_scores = _normalize(_dictionary_scores(title, namespace, chosen_regions), region_ids)
    llm_norm = _normalize(_llm_scores(llm_prior, region_ids), region_ids)
    thesaurus_norm = _normalize(_thesaurus_scores(title, atlas=chosen_atlas, region_ids=region_ids), region_ids)
    dw = max(0.0, float(dictionary_weight))
    lw = max(0.0, float(llm_weight))
    tw = max(0.0, float(thesaurus_weight))
    denom = dw + lw + tw or 1.0
    scores = {
        region_id: (
            dict_scores[region_id] * dw
            + llm_norm[region_id] * lw
            + thesaurus_norm[region_id] * tw
        ) / denom
        for region_id in region_ids
    }
    primary = max(region_ids, key=lambda region_id: (scores[region_id], -region_ids.index(region_id)))
    notes = [
        f"dictionary_weight={dw:.2f}",
        f"thesaurus_weight={tw:.2f}",
        f"llm_weight={lw:.2f}",
        f"namespace={namespace or 'article'}",
    ]
    if llm_prior:
        notes.append("llm_prior_supplied")
    return BaselinePrior(
        concept_id=concept_id,
        title=title,
        primary_region=primary,
        region_scores=scores,
        llm_weight=lw,
        dictionary_weight=dw,
        notes=tuple(notes),
    )
