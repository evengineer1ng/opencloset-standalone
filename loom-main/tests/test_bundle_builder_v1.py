from loom.baseline_prior_generator_v1 import build_baseline_prior
from loom.bundle_builder_v1 import build_bundle_artifact
from loom.bundle_context_overlay_v1 import build_bundle_context_record, summarize_bundle_context
from loom.placement_solver_v1 import solve_placement
from loom.wikipedia_index_loombit import parse_index_line


def _sample_records():
    row = parse_index_line(
        "12345:6789:2025 NBA Finals",
        snapshot="2026-06-01",
        source_ref="enwiki-index.txt",
    )
    prior = build_baseline_prior(
        row,
        llm_prior={"primary_region": "competition", "region_scores": {"competition": 1.0}},
    )
    result = solve_placement(row, prior)
    return result.records


def test_bundle_context_overlay_is_computed_from_primary_overlays():
    records = _sample_records()
    summary = summarize_bundle_context(records)
    context = build_bundle_context_record(records)
    assert context.y_overlay == "bundle_context"
    assert context.x_region == "competition"
    assert context.value == round(summary.context_value, 6)
    assert "importance" in context.meta["source_values"]


def test_bundle_builder_emits_bundle_receipt_and_context_slot():
    records = _sample_records()
    build = build_bundle_artifact(
        bundle_id="bundle:test:nba-finals",
        title="NBA Finals Bundle",
        records=records,
        artifact_refs={"importance": "maps/importance.png", "bundle_context": "maps/bundle_context.png"},
        snapshot="2026-06-01",
    )
    assert build.bundle.entries[8].overlay_id == "bundle_context"
    assert build.receipt.slot_scores["bundle_context"] == build.context_record.value
    assert build.bundle.meta["snapshot"] == "2026-06-01"
    assert build.receipt.artifact_refs["importance"] == "maps/importance.png"
    assert build.receipt.nesting_ready is True
