"""Stable Y-axis overlay schema for coordinate-mapped loompixels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class YOverlay:
    id: str
    label: str
    index: int
    minimum: float
    maximum: float
    description: str


def default_y_overlays() -> List[YOverlay]:
    return [
        YOverlay("importance", "Importance", 0, 0.0, 1.0, "How central the concept appears within the current corpus slice."),
        YOverlay("evidence_density", "Evidence Density", 1, 0.0, 1.0, "How much supporting structure or metadata is present."),
        YOverlay("connectivity", "Connectivity", 2, 0.0, 1.0, "How broadly the concept appears connected to neighboring concepts."),
        YOverlay("activity", "Activity", 3, 0.0, 1.0, "How operationally active or event-like the concept appears."),
        YOverlay("recency", "Recency", 4, 0.0, 1.0, "How time-bound or present-tense the concept appears."),
        YOverlay("confidence", "Confidence", 5, 0.0, 1.0, "How confident the placement pipeline is in the classification."),
        YOverlay("ambiguity", "Ambiguity", 6, 0.0, 1.0, "How polysemous or overloaded the concept appears."),
        YOverlay("retrieval_heat", "Retrieval Heat", 7, 0.0, 1.0, "How attractive the concept should be as a routing hotspot."),
    ]


def overlay_map(overlays: List[YOverlay] | None = None) -> Dict[str, YOverlay]:
    chosen = overlays or default_y_overlays()
    return {overlay.id: overlay for overlay in chosen}
