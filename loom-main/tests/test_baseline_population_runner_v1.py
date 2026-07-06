from loom.baseline_population_runner_v1 import (
    normalize_llm_labels,
    populate_baseline_from_rows,
)
from loom.wikipedia_index_loombit import parse_index_line


def _rows():
    return [
        parse_index_line("12345:6789:Barbados", snapshot="2026-06-01", source_ref="enwiki-index.txt"),
        parse_index_line("12346:6790:2025 NBA Finals", snapshot="2026-06-01", source_ref="enwiki-index.txt"),
        parse_index_line("12347:6791:Cat health", snapshot="2026-06-01", source_ref="enwiki-index.txt"),
    ]


def test_normalize_llm_labels_fills_missing_concepts():
    rows = _rows()
    batch = populate_baseline_from_rows(rows[:1], llm_labels={})
    concept_id = batch.llm_tape[0]["payload"]["concept_id"]
    normalized = normalize_llm_labels({}, batch.llm_tape)
    assert concept_id in normalized
    assert "primary_region" in normalized[concept_id]


def test_population_runner_builds_priors_records_and_overlay_cells():
    rows = _rows()
    labels = {
        rows[0]["id"]: {"primary_region": "place", "region_scores": {"place": 1.0}},
        rows[1]["id"]: {"primary_region": "competition", "region_scores": {"competition": 1.0}},
        rows[2]["id"]: {"primary_region": "survival", "region_scores": {"survival": 1.0}},
    }
    batch = populate_baseline_from_rows(rows, llm_labels=labels, width=256, height=256)
    assert batch.snapshot == "2026-06-01"
    assert len(batch.items) == 3
    assert batch.items[0].prior.primary_region == "place"
    assert batch.items[1].prior.primary_region == "competition"
    assert batch.items[2].prior.primary_region in {"organism", "survival"}
    assert batch.items[2].prior.region_scores["survival"] > 0.0
    assert "importance" in batch.overlay_cells
    assert len(batch.overlay_cells["importance"]) == 3
    assert len(batch.items[0].placement.records) > 0
    assert batch.items[1].placement.receipt.primary_region == "competition"
