"""Placement receipts for coordinate-map computations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class PlacementReceipt:
    concept_id: str
    title: str
    primary_region: str
    baseline_top_score: float
    source_features: Dict[str, float]
    overlay_values: Dict[str, float]
    notes: tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["notes"] = list(self.notes)
        return payload


def build_receipt(
    *,
    concept_id: str,
    title: str,
    primary_region: str,
    baseline_top_score: float,
    source_features: Dict[str, float],
    overlay_values: Dict[str, float],
    notes: List[str] | None = None,
) -> PlacementReceipt:
    return PlacementReceipt(
        concept_id=concept_id,
        title=title,
        primary_region=primary_region,
        baseline_top_score=round(float(baseline_top_score), 6),
        source_features={key: round(float(value), 6) for key, value in sorted(source_features.items())},
        overlay_values={key: round(float(value), 6) for key, value in sorted(overlay_values.items())},
        notes=tuple(notes or []),
    )
