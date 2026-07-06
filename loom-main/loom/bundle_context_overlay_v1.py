"""Compute the ninth bundle-context slot from the eight primary overlays."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from .coordinate_record_v1 import CoordinateRecord, build_coordinate_record


PRIMARY_BUNDLE_OVERLAYS = (
    "importance",
    "evidence_density",
    "connectivity",
    "activity",
    "confidence",
    "retrieval_heat",
    "recency",
    "ambiguity",
)


@dataclass(frozen=True)
class BundleContextSummary:
    concept_id: str
    title: str
    x_region: str
    x_index: int
    context_value: float
    source_values: Dict[str, float]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def summarize_bundle_context(records: Iterable[CoordinateRecord]) -> BundleContextSummary:
    chosen = [record for record in records if record.y_overlay in PRIMARY_BUNDLE_OVERLAYS]
    if not chosen:
        raise ValueError("bundle context requires at least one primary overlay record")
    by_overlay = {record.y_overlay: record for record in chosen}
    first = chosen[0]
    values = {overlay_id: by_overlay[overlay_id].value for overlay_id in by_overlay}
    importance = values.get("importance", 0.0)
    confidence = values.get("confidence", 0.0)
    heat = values.get("retrieval_heat", 0.0)
    ambiguity = values.get("ambiguity", 0.0)
    connectivity = values.get("connectivity", 0.0)
    evidence = values.get("evidence_density", 0.0)
    context_value = _clamp(
        importance * 0.24
        + confidence * 0.22
        + heat * 0.18
        + connectivity * 0.14
        + evidence * 0.12
        + (1.0 - ambiguity) * 0.10
    )
    return BundleContextSummary(
        concept_id=first.concept_id,
        title=first.title,
        x_region=first.x_region,
        x_index=first.x_index,
        context_value=context_value,
        source_values=values,
    )


def build_bundle_context_record(records: Iterable[CoordinateRecord]) -> CoordinateRecord:
    summary = summarize_bundle_context(records)
    return build_coordinate_record(
        concept_id=summary.concept_id,
        title=summary.title,
        x_region=summary.x_region,
        x_index=summary.x_index,
        y_overlay="bundle_context",
        y_index=8,
        value=summary.context_value,
        evidence_pointer="bundle-context",
        baseline_score=summary.context_value,
        source_score=summary.context_value,
        tags=["bundle-context"],
        meta={"source_values": summary.source_values},
    )
