from loom.atlas_seed_v1 import build_seed_atlas, export_seed_tape
from loom.baseline_prior_generator_v1 import build_baseline_prior
from loom.llm_baseline_tape_v1 import build_llm_baseline_tape
from loom.thesaurus_bridge_v1 import score_with_thesaurus
from loom.wikipedia_index_loombit import parse_index_line


def test_seed_atlas_exports_region_tape():
    atlas = build_seed_atlas()
    tape = export_seed_tape(atlas)
    assert len(atlas) == len(tape)
    assert tape[0]["kind"] == "atlas_seed_region"
    assert "anchors" in tape[0]


def test_thesaurus_bridge_scores_synonym_neighbors():
    result = score_with_thesaurus("basketball playoff final")
    assert result.region_scores["competition"] > result.region_scores["identity"]
    assert "playoff" in result.matched_terms["competition"]


def test_llm_baseline_tape_emits_gradeable_rows():
    row = parse_index_line("12345:6789:Barbados", snapshot="2026-06-01", source_ref="enwiki-index.txt")
    tape = build_llm_baseline_tape([row], top_n=3)
    assert len(tape) == 1
    assert tape[0]["kind"] == "baseline_grading_request"
    assert len(tape[0]["payload"]["candidate_regions"]) == 3


def test_baseline_prior_blends_dictionary_thesaurus_and_llm():
    row = parse_index_line("12345:6789:Barbados", snapshot="2026-06-01", source_ref="enwiki-index.txt")
    prior = build_baseline_prior(
        row,
        llm_prior={"primary_region": "place", "region_scores": {"place": 0.9, "culture": 0.1}},
        dictionary_weight=0.34,
        thesaurus_weight=0.33,
        llm_weight=0.33,
    )
    assert prior.primary_region == "place"
    assert abs(sum(prior.region_scores.values()) - 1.0) < 1e-9
    assert any(note.startswith("thesaurus_weight=") for note in prior.notes)
