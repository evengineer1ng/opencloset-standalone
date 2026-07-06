"""Fixed 3x3 slot schema for loom bundles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class BundleSlot:
    slot_id: str
    row: int
    col: int
    index: int
    label: str
    overlay_id: str
    description: str


def default_bundle_slots() -> List[BundleSlot]:
    return [
        BundleSlot("top_left", 0, 0, 0, "Importance", "importance", "How central the mapped concepts are."),
        BundleSlot("top_center", 0, 1, 1, "Evidence Density", "evidence_density", "How much support structure the map has."),
        BundleSlot("top_right", 0, 2, 2, "Connectivity", "connectivity", "How broadly connected the mapped concepts are."),
        BundleSlot("mid_left", 1, 0, 3, "Activity", "activity", "How event-like or operational the mapped concepts are."),
        BundleSlot("center", 1, 1, 4, "Confidence", "confidence", "How stable the placement engine believes the map is."),
        BundleSlot("mid_right", 1, 2, 5, "Retrieval Heat", "retrieval_heat", "How strongly the map should attract routing."),
        BundleSlot("bottom_left", 2, 0, 6, "Recency", "recency", "How present-tense or time-bound the mapped concepts are."),
        BundleSlot("bottom_center", 2, 1, 7, "Ambiguity", "ambiguity", "How polysemous or overloaded the mapped concepts are."),
        BundleSlot("bottom_right", 2, 2, 8, "Bundle Context", "bundle_context", "A dedicated bundle-level summary/control surface."),
    ]


def slot_map(slots: List[BundleSlot] | None = None) -> Dict[str, BundleSlot]:
    chosen = slots or default_bundle_slots()
    return {slot.slot_id: slot for slot in chosen}


def slot_by_overlay(slots: List[BundleSlot] | None = None) -> Dict[str, BundleSlot]:
    chosen = slots or default_bundle_slots()
    return {slot.overlay_id: slot for slot in chosen}
