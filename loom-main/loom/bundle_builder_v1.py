"""Build a real bundle artifact from eight overlay maps plus one computed context map."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from .bundle_context_overlay_v1 import build_bundle_context_record
from .bundle_receipt_v1 import BundleReceipt, build_bundle_receipt
from .bundle_slot_schema_v1 import BundleSlot, default_bundle_slots, slot_by_overlay
from .coordinate_record_v1 import CoordinateRecord
from .loom_bundle_schema_v1 import (
    LoomBundle,
    LoomBundleEntry,
    build_bundle,
    build_bundle_entry,
    default_bundle_overlay,
)


@dataclass(frozen=True)
class BundleBuildResult:
    bundle: LoomBundle
    receipt: BundleReceipt
    context_record: CoordinateRecord


def _record_by_overlay(records: Iterable[CoordinateRecord]) -> Dict[str, CoordinateRecord]:
    mapping: Dict[str, CoordinateRecord] = {}
    for record in records:
        mapping[record.y_overlay] = record
    return mapping


def build_bundle_artifact(
    *,
    bundle_id: str,
    title: str,
    records: List[CoordinateRecord],
    artifact_refs: Dict[str, str],
    snapshot: str = "",
    slots: List[BundleSlot] | None = None,
) -> BundleBuildResult:
    chosen_slots = slots or default_bundle_slots()
    overlay_slots = slot_by_overlay(chosen_slots)
    context_record = build_bundle_context_record(records)
    by_overlay = _record_by_overlay(list(records) + [context_record])
    missing = [overlay_id for overlay_id in overlay_slots if overlay_id not in by_overlay]
    if missing:
        raise ValueError(f"missing overlay records for bundle build: {', '.join(sorted(missing))}")
    entries: List[LoomBundleEntry] = []
    slot_scores: Dict[str, float] = {}
    resolved_refs: Dict[str, str] = {}
    for overlay_id, slot in sorted(overlay_slots.items(), key=lambda item: item[1].index):
        record = by_overlay[overlay_id]
        artifact_ref = artifact_refs.get(overlay_id, f"maps/{overlay_id}.png")
        entries.append(
            build_bundle_entry(
                slot=slot,
                artifact_ref=artifact_ref,
                score=record.value,
                child_checksum=record.checksum,
                meta={
                    "concept_id": record.concept_id,
                    "title": record.title,
                    "overlay": overlay_id,
                },
            )
        )
        slot_scores[overlay_id] = record.value
        resolved_refs[overlay_id] = artifact_ref
    first = records[0]
    bundle = build_bundle(
        bundle_id=bundle_id,
        title=title,
        x_region=first.x_region,
        x_index=first.x_index,
        bundle_overlay=default_bundle_overlay(),
        entries=entries,
        meta={"snapshot": snapshot, "context_checksum": context_record.checksum},
    )
    receipt = build_bundle_receipt(
        bundle_id=bundle_id,
        title=title,
        x_region=first.x_region,
        bundle_overlay=default_bundle_overlay(),
        slot_scores=slot_scores,
        artifact_refs=resolved_refs,
        notes=[
            "fixed-slot-v1",
            "bundle-context-computed",
            "future-bundle-of-bundles-allowed",
        ],
    )
    return BundleBuildResult(bundle=bundle, receipt=receipt, context_record=context_record)
