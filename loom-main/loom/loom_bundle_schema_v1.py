"""Bundle schema for grouping nine loom-pixel maps into a higher-order map."""

from __future__ import annotations

import zlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .bundle_slot_schema_v1 import BundleSlot, default_bundle_slots


@dataclass(frozen=True)
class LoomBundleEntry:
    slot_id: str
    overlay_id: str
    label: str
    artifact_ref: str
    plot_x: int
    plot_y: int
    score: float
    child_checksum: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoomBundle:
    bundle_id: str
    title: str
    x_region: str
    x_index: int
    bundle_overlay: str
    entries: List[LoomBundleEntry]
    checksum: str
    nesting_hint: str = "bundle-of-bundles-ready"
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "title": self.title,
            "x_region": self.x_region,
            "x_index": self.x_index,
            "bundle_overlay": self.bundle_overlay,
            "entries": [entry.to_dict() for entry in self.entries],
            "checksum": self.checksum,
            "nesting_hint": self.nesting_hint,
            "meta": dict(self.meta),
        }


def stable_bundle_checksum(payload: Dict[str, Any]) -> str:
    import json

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"{zlib.crc32(encoded) & 0xFFFFFFFF:08x}"


def build_bundle_entry(
    *,
    slot: BundleSlot,
    artifact_ref: str,
    score: float,
    child_checksum: str,
    meta: Dict[str, Any] | None = None,
) -> LoomBundleEntry:
    return LoomBundleEntry(
        slot_id=slot.slot_id,
        overlay_id=slot.overlay_id,
        label=slot.label,
        artifact_ref=artifact_ref,
        plot_x=slot.col,
        plot_y=slot.row,
        score=max(0.0, min(1.0, float(score))),
        child_checksum=str(child_checksum or ""),
        meta=meta or {},
    )


def build_bundle(
    *,
    bundle_id: str,
    title: str,
    x_region: str,
    x_index: int,
    bundle_overlay: str,
    entries: List[LoomBundleEntry],
    meta: Dict[str, Any] | None = None,
) -> LoomBundle:
    payload = {
        "bundle_id": bundle_id,
        "title": title,
        "x_region": x_region,
        "x_index": int(x_index),
        "bundle_overlay": bundle_overlay,
        "entries": [entry.to_dict() for entry in entries],
        "nesting_hint": "bundle-of-bundles-ready",
        "meta": meta or {},
    }
    checksum = stable_bundle_checksum(payload)
    return LoomBundle(
        bundle_id=bundle_id,
        title=title,
        x_region=x_region,
        x_index=int(x_index),
        bundle_overlay=bundle_overlay,
        entries=list(entries),
        checksum=checksum,
        nesting_hint="bundle-of-bundles-ready",
        meta=meta or {},
    )


def default_bundle_overlay() -> str:
    return "bundle_control"


def bundle_slots() -> List[BundleSlot]:
    return default_bundle_slots()
