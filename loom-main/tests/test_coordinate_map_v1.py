from loom.baseline_prior_generator_v1 import build_baseline_prior
from loom.loom_pixel_render_v1 import render_overlay_cells
from loom.placement_solver_v1 import solve_placement
from loom.wikipedia_index_loombit import parse_index_line
from loom.x_region_schema_v1 import default_x_regions
from loom.y_overlay_schema_v1 import default_y_overlays


def test_baseline_blends_dictionary_and_llm_prior():
    row = parse_index_line(
        "12345:6789:Barbados",
        snapshot="2026-06-01",
        source_ref="enwiki-index.txt",
    )
    prior = build_baseline_prior(
        row,
        llm_prior={
            "primary_region": "place",
            "region_scores": {"place": 0.9, "culture": 0.1},
            "secondary_regions": ["culture"],
        },
        llm_weight=0.5,
        dictionary_weight=0.5,
    )
    assert prior.primary_region == "place"
    assert abs(sum(prior.region_scores.values()) - 1.0) < 1e-9
    assert prior.region_scores["place"] > prior.region_scores["culture"]


def test_placement_solver_emits_one_record_per_overlay():
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
    overlays = default_y_overlays()
    assert len(result.records) == len(overlays)
    assert result.receipt.primary_region == "competition"
    heat = next(record for record in result.records if record.y_overlay == "retrieval_heat")
    ambiguity = next(record for record in result.records if record.y_overlay == "ambiguity")
    assert 0.0 <= heat.value <= 1.0
    assert 0.0 <= ambiguity.value <= 1.0
    assert heat.x_region == "competition"


def test_loom_pixel_render_is_deterministic_for_overlay():
    row = parse_index_line(
        "555:42:Oracle Kingdom",
        snapshot="2026-06-01",
        source_ref="enwiki-index.txt",
    )
    prior = build_baseline_prior(
        row,
        llm_prior={"primary_region": "exploration", "region_scores": {"exploration": 0.8, "culture": 0.2}},
    )
    result = solve_placement(row, prior)
    cells_a = render_overlay_cells(
        result.records,
        overlay_id="importance",
        region_count=len(default_x_regions()),
        width=512,
        height=512,
    )
    cells_b = render_overlay_cells(
        result.records,
        overlay_id="importance",
        region_count=len(default_x_regions()),
        width=512,
        height=512,
    )
    assert [cell.to_dict() for cell in cells_a] == [cell.to_dict() for cell in cells_b]
    assert cells_a[0].x_region == "exploration"
