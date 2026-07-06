from loom.bundle_receipt_v1 import build_bundle_receipt
from loom.bundle_slot_schema_v1 import default_bundle_slots, slot_by_overlay
from loom.coordinate_record_v1 import build_coordinate_record
from loom.loom_bundle_schema_v1 import build_bundle, build_bundle_entry, default_bundle_overlay


def test_bundle_slot_schema_is_fixed_3x3():
    slots = default_bundle_slots()
    assert len(slots) == 9
    assert slots[4].slot_id == "center"
    assert slot_by_overlay(slots)["confidence"].slot_id == "center"
    assert slot_by_overlay(slots)["bundle_context"].slot_id == "bottom_right"


def test_bundle_schema_builds_deterministically():
    slots = default_bundle_slots()
    entries = []
    for idx, slot in enumerate(slots):
        entries.append(
            build_bundle_entry(
                slot=slot,
                artifact_ref=f"maps/{slot.overlay_id}.png",
                score=0.1 + idx * 0.08,
                child_checksum=f"{idx:08x}",
                meta={"kind": "overlay-map"},
            )
        )
    bundle_a = build_bundle(
        bundle_id="bundle:test:competition",
        title="Competition Bundle",
        x_region="competition",
        x_index=4,
        bundle_overlay=default_bundle_overlay(),
        entries=entries,
        meta={"snapshot": "2026-06-01"},
    )
    bundle_b = build_bundle(
        bundle_id="bundle:test:competition",
        title="Competition Bundle",
        x_region="competition",
        x_index=4,
        bundle_overlay=default_bundle_overlay(),
        entries=entries,
        meta={"snapshot": "2026-06-01"},
    )
    assert bundle_a.to_dict() == bundle_b.to_dict()
    assert bundle_a.nesting_hint == "bundle-of-bundles-ready"
    assert bundle_a.entries[8].overlay_id == "bundle_context"


def test_bundle_receipt_tracks_slot_scores_and_future_nesting():
    receipt = build_bundle_receipt(
        bundle_id="bundle:test:place",
        title="Place Bundle",
        x_region="place",
        bundle_overlay=default_bundle_overlay(),
        slot_scores={"importance": 0.82, "confidence": 0.76, "bundle_context": 0.91},
        artifact_refs={"importance": "maps/importance.png", "confidence": "maps/confidence.png"},
        notes=["fixed-slot-v1", "future-bundle-of-bundles-allowed"],
    )
    assert receipt.nesting_ready is True
    assert receipt.slot_scores["bundle_context"] == 0.91
    assert receipt.artifact_refs["confidence"] == "maps/confidence.png"
