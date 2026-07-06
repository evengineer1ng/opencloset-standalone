"""Deterministic placement solver from baseline prior plus source row."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .baseline_prior_generator_v1 import BaselinePrior
from .coordinate_record_v1 import CoordinateRecord, build_coordinate_record
from .placement_receipt_v1 import PlacementReceipt, build_receipt
from .x_region_schema_v1 import XRegion, default_x_regions
from .y_overlay_schema_v1 import YOverlay, default_y_overlays


def _tokens(text: str) -> List[str]:
    return [token for token in re.split(r"[^a-z0-9]+", str(text or "").lower()) if token]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _source_features(source_row: Dict[str, Any], prior: BaselinePrior) -> Dict[str, float]:
    title = str(source_row.get("title", ""))
    tokens = _tokens(title)
    namespace = str(source_row.get("namespace", ""))
    has_disambig = 1.0 if "(" in title and ")" in title else 0.0
    token_count = min(1.0, len(tokens) / 8.0)
    namespace_bonus = 0.25 if not namespace else 0.55
    offset = float(source_row.get("offset", 0) or 0)
    page_id = float(source_row.get("page_id", 0) or 0)
    offset_signal = 1.0 / (1.0 + (offset / 5_000_000.0))
    page_signal = min(1.0, (page_id % 10_000) / 10_000.0)
    region_conf = prior.region_scores.get(prior.primary_region, 0.0)
    return {
        "token_count": token_count,
        "namespace_bonus": namespace_bonus,
        "disambiguation": has_disambig,
        "offset_signal": offset_signal,
        "page_signal": page_signal,
        "region_confidence": region_conf,
    }


def _overlay_values(features: Dict[str, float], prior: BaselinePrior) -> Dict[str, float]:
    region_conf = features["region_confidence"]
    ambiguity = _clamp(features["disambiguation"] * 0.7 + max(0.0, 0.42 - region_conf))
    importance = _clamp(region_conf * 0.55 + features["namespace_bonus"] * 0.25 + features["token_count"] * 0.20)
    evidence_density = _clamp(features["token_count"] * 0.35 + features["namespace_bonus"] * 0.35 + 0.30)
    connectivity = _clamp(region_conf * 0.45 + features["token_count"] * 0.40 + features["page_signal"] * 0.15)
    activity = _clamp(features["offset_signal"] * 0.35 + features["token_count"] * 0.25 + 0.20 + features["disambiguation"] * 0.08)
    recency = _clamp(0.18 + features["offset_signal"] * 0.32 + (1.0 - features["disambiguation"]) * 0.10)
    confidence = _clamp(region_conf * 0.75 + (1.0 - ambiguity) * 0.25)
    retrieval_heat = _clamp(importance * 0.45 + confidence * 0.25 + connectivity * 0.20 + evidence_density * 0.10)
    return {
        "importance": importance,
        "evidence_density": evidence_density,
        "connectivity": connectivity,
        "activity": activity,
        "recency": recency,
        "confidence": confidence,
        "ambiguity": ambiguity,
        "retrieval_heat": retrieval_heat,
    }


@dataclass(frozen=True)
class PlacementResult:
    records: List[CoordinateRecord]
    receipt: PlacementReceipt


def solve_placement(
    source_row: Dict[str, Any],
    prior: BaselinePrior,
    *,
    regions: List[XRegion] | None = None,
    overlays: List[YOverlay] | None = None,
) -> PlacementResult:
    chosen_regions = regions or default_x_regions()
    region_index = {region.id: region.index for region in chosen_regions}
    chosen_overlays = overlays or default_y_overlays()
    features = _source_features(source_row, prior)
    values = _overlay_values(features, prior)
    evidence_pointer = f"{source_row.get('source_ref', '')}@{source_row.get('offset', 0)}"
    records: List[CoordinateRecord] = []
    for overlay in chosen_overlays:
        records.append(
            build_coordinate_record(
                concept_id=prior.concept_id,
                title=prior.title,
                x_region=prior.primary_region,
                x_index=region_index[prior.primary_region],
                y_overlay=overlay.id,
                y_index=overlay.index,
                value=values[overlay.id],
                evidence_pointer=evidence_pointer,
                baseline_score=prior.region_scores[prior.primary_region],
                source_score=features["namespace_bonus"],
                tags=list(source_row.get("tags", [])),
                meta={"source_snapshot": source_row.get("source_snapshot", "")},
            )
        )
    receipt = build_receipt(
        concept_id=prior.concept_id,
        title=prior.title,
        primary_region=prior.primary_region,
        baseline_top_score=prior.region_scores[prior.primary_region],
        source_features=features,
        overlay_values=values,
        notes=list(prior.notes),
    )
    return PlacementResult(records=records, receipt=receipt)
