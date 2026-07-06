from __future__ import annotations

from pathlib import Path

from tools.atlas_courtroom import (
    CourtroomConfig,
    CourtroomHarness,
    agreement_score,
    build_judge_ruling,
    classify_status,
    compare_claim_sets,
    extract_admitted_claims,
)


def test_extract_admitted_claims_dedupes_answer_and_evidence():
    packet = {
        "answer_tape": [{"claim": "Aurora Borealis is the top atlas match"}],
        "evidence_citations": [
            {"claim_glob": "Aurora Borealis is the top atlas match"},
            {"claim_glob": "Magnetosphere context appears in the support trail"},
        ],
        "meta": {
            "meaning_text": "Aurora Borealis is the top atlas match [1]. Magnetosphere context appears in the support trail [2]."
        },
    }
    admitted = extract_admitted_claims(packet)
    assert "Aurora Borealis is the top atlas match" in admitted
    assert any("Magnetosphere context" in claim for claim in admitted)
    assert len(admitted) >= 2


def test_compare_claim_sets_marks_unsupported_claims():
    admitted = ["Leclerc won the race", "The support trail points to lap 16"]
    comparison = compare_claim_sets(
        "Leclerc won the race. Verstappen crashed on lap 3.",
        admitted,
    )
    assert comparison["supported_claim_count"] == 1
    assert comparison["unsupported_claim_count"] == 1
    assert comparison["unsupported_claim_rate"] == 0.5


def test_agreement_score_rewards_overlap():
    score = agreement_score("Leclerc won the race.", ["Leclerc won the race", "lap 16 support"])
    assert score > 0.8


def test_classify_status_uses_confidence_and_evidence():
    assert classify_status(0.81, 2, "Strong support") == "supported"
    assert classify_status(0.61, 2, "Some support") == "partial"
    assert classify_status(0.41, 2, "Weak support") == "inconclusive"
    assert classify_status(0.81, 0, "No support") == "unsupported"


def test_build_judge_ruling_separates_material_from_definition():
    packet = {
        "question_tape": [{"focus": "love", "transform": "define"}],
        "source_rows": [
            {
                "ref": "atlas:what-is-love#lap1:row0",
                "kind": "atlas_hit",
                "actor": "Love",
                "object": "Love is a culture concept",
                "thread": "culture",
                "priority": 0.688,
                "raw": {"atlas_title": "Love", "atlas_region": "culture", "atlas_score": 0.688},
            },
            {
                "ref": "atlas:what-is-love#lap2:row1",
                "kind": "atlas_hit",
                "actor": "Affection",
                "object": "Affection is a culture concept",
                "thread": "culture",
                "priority": 0.597,
                "raw": {"atlas_title": "Affection", "atlas_region": "culture", "atlas_score": 0.597},
            },
        ],
        "meta": {"confidence": {"total": 0.94}},
    }
    ruling = build_judge_ruling("what is love?", packet)
    assert ruling["status"] == "partial"
    assert ruling["admitted_findings"]
    assert ruling["unresolved_points"]


def test_run_query_uses_atlas_authority_for_final_metrics(monkeypatch, tmp_path: Path):
    config = CourtroomConfig(
        tapes=None,
        atlas_dir=None,
        loombit_index=None,
        loombit_dict=None,
        enable_search=False,
        target_confidence=0.72,
        ingress_model="",
        raw_model="mock-raw",
        outgress_model="mock-out",
    )
    harness = CourtroomHarness(config)

    atlas_packet = {
        "answer_tape": [{"claim": "Leclerc won the race"}],
        "question_tape": [{"focus": "race", "transform": "summary"}],
        "evidence_hits": [{"ref": "lap16"}],
        "evidence_citations": [{"claim_glob": "Leclerc won the race"}],
        "source_rows": [
            {
                "ref": "atlas:who-won#lap1:row0",
                "kind": "atlas_hit",
                "actor": "Leclerc",
                "object": "Leclerc won the race",
                "thread": "sports",
                "priority": 0.91,
                "raw": {"atlas_title": "Leclerc", "atlas_region": "sports", "atlas_score": 0.91},
            }
        ],
        "meta": {
            "meaning_text": "Leclerc won the race [1].",
            "evidence_text": "[1] lap16",
            "confidence": {"total": 0.84, "relation": 0.76, "meaning": 0.79},
            "ingress": {"accepted_query": "who won the race"},
        },
    }

    monkeypatch.setattr(harness, "ingress_payload", lambda query: None)
    monkeypatch.setattr(harness, "atlas_retrieve", lambda query: {"hits": [], "relations": {}})
    monkeypatch.setattr(harness, "packet_for_query", lambda query, translator_payload, atlas_payload: (atlas_packet, 12))
    monkeypatch.setattr(
        harness,
        "raw_llm_answer",
        lambda query: {
            "answer": "Leclerc won the race. Verstappen also won a sprint.",
            "confidence": 0.73,
            "claims": ["Leclerc won the race", "Verstappen also won a sprint"],
            "usage": {"latency_ms": 20, "prompt_tokens": 10, "completion_tokens": 14, "model": "mock-raw"},
        },
    )
    monkeypatch.setattr(
        harness,
        "outgress_answer",
        lambda query, ruling: {
            "answer": "Leclerc won the race.",
            "usage": {"latency_ms": 22, "prompt_tokens": 12, "completion_tokens": 8, "model": "mock-out"},
        },
    )

    result = harness.run_query("who won the race?")
    assert result["status"] == "supported"
    assert result["final_status"] == "supported"
    assert result["comparison"]["unsupported_claim_count"] == 2
    assert result["final_compare"]["unsupported_claim_count"] == 1
    assert result["metrics"]["unsupported_claim_rate"] == 1.0
