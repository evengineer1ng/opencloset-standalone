from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from oradio_engine.query_codec import export_packet, query_tapes_via_ingress


def _sample_tape(tmp_path: Path) -> Path:
    tape = [
        {"actor": "Hamilton", "action": "pit", "object": "soft tyres", "lap": 5, "priority": 0.7, "valence": "calm"},
        {"actor": "Leclerc", "action": "overtake", "object": "Russell", "lap": 8, "priority": 0.86, "valence": "hype"},
        {"actor": "Leclerc", "action": "win", "object": "the race", "lap": 16, "priority": 0.98, "valence": "hype"},
    ]
    path = tmp_path / "f1.tape.json"
    path.write_text(json.dumps(tape), encoding="utf-8")
    return path


def test_ingress_path_is_deterministic(tmp_path: Path):
    tape_path = _sample_tape(tmp_path)
    query = "okay, from the tape only, who actually ended up winning this race?"
    packet_a = export_packet(query_tapes_via_ingress(tape_path, query, enable_search=False))
    packet_b = export_packet(query_tapes_via_ingress(tape_path, query, enable_search=False))
    assert packet_a == packet_b
    assert packet_a["query"] == query
    assert packet_a["meta"]["engine_query"].lower().startswith("who")
    assert "from_tape" not in packet_a["meta"]["engine_query"]
    assert packet_a["meta"]["ingress"]["accepted_candidate"]["source"] == "heuristic"


def test_translator_candidates_are_judged_against_tape_coverage(tmp_path: Path):
    tape_path = _sample_tape(tmp_path)
    payload = {
        "candidates": [
            {
                "source": "tiny-llm",
                "normalized_query": "rank telemetry coefficient",
                "transform": "rank",
                "focus": "telemetry coefficient",
                "entities": ["telemetry coefficient"],
                "confidence": 0.95,
                "retrieval_plan": ["match_entities", "rank"],
            },
            {
                "source": "tiny-llm",
                "normalized_query": "who won the race",
                "transform": "rank",
                "focus": "won the race",
                "entities": ["Leclerc"],
                "confidence": 0.72,
                "retrieval_plan": ["match_entities", "score_evidence"],
            },
        ]
    }
    packet = export_packet(query_tapes_via_ingress(tape_path, "who won the race?", translator_payload=payload, enable_search=False))
    ingress = packet["meta"]["ingress"]
    assert ingress["accepted_query"] == "who won the race"
    assert ingress["arbitration"]["winner_source"] == "tiny-llm"
    assert "Leclerc" in packet["meta"]["meaning_text"]


def test_ingress_packet_preserves_original_query_and_engine_query(tmp_path: Path):
    tape_path = _sample_tape(tmp_path)
    packet = export_packet(query_tapes_via_ingress(tape_path, "Why did Leclerc win?", enable_search=False))
    ingress = packet["meta"]["ingress"]
    assert ingress["original_query"] == "Why did Leclerc win?"
    assert packet["query"] == "Why did Leclerc win?"
    assert packet["meta"]["engine_query"]
    assert ingress["accepted_candidate"]["transform"] in {"causal", "summary"}


def test_ingress_can_use_loombit_route_hints(tmp_path: Path):
    from loom.loombit import build_index_payload, compile_object

    tape_path = _sample_tape(tmp_path)
    root_payload = build_index_payload(
        [
            {
                "id": "sports-basketball",
                "path": "sports.basketball.loombit",
                "class": "loombit_index",
                "topic": "basketball finals winner",
                "summary": "winner and race score trail",
                "bucket": "sports",
                "gradient": "sports/basketball",
                "tags": ["basketball", "winner"],
            },
            {
                "id": "weather",
                "path": "weather.loombit",
                "class": "loombit_index",
                "topic": "weather report",
                "summary": "wind and rain",
                "bucket": "weather",
                "gradient": "weather/regional",
                "tags": ["forecast"],
            },
        ],
        title="root",
    )
    index_path = tmp_path / "root.index.loombit"
    index_path.write_bytes(compile_object(root_payload))
    payload = {
        "candidates": [
            {
                "source": "tiny-llm",
                "normalized_query": "who won the race",
                "transform": "rank",
                "focus": "won the race",
                "entities": ["Leclerc"],
                "confidence": 0.72,
                "gradient_bucket": "sports",
                "aim_tokens": ["winner", "basketball"],
                "preferred_paths": ["sports"],
                "retrieval_plan": ["match_entities", "score_evidence"],
            }
        ]
    }
    packet = export_packet(
        query_tapes_via_ingress(
            tape_path,
            "who won the race?",
            translator_payload=payload,
            enable_search=False,
            loombit_index=index_path,
        )
    )
    route = packet["meta"]["ingress"]["arbitration"]["route"]
    assert route["ranked"][0]["id"] == "sports-basketball"


def test_translator_added_latest_is_removed_when_human_did_not_ask(tmp_path: Path):
    tape_path = _sample_tape(tmp_path)
    payload = {
        "candidates": [
            {
                "source": "tiny-llm",
                "normalized_query": "who won the race",
                "transform": "actor_action",
                "focus": "race winner",
                "entities": ["race"],
                "time_scope": "latest",
                "constraints": ["latest"],
                "confidence": 0.9,
            }
        ]
    }
    packet = export_packet(
        query_tapes_via_ingress(
            tape_path,
            "who won the race?",
            translator_payload=payload,
            enable_search=False,
        )
    )
    accepted = packet["meta"]["ingress"]["accepted_candidate"]
    assert accepted["time_scope"] == ""
    assert "latest" not in packet["meta"]["engine_query"].lower()
    assert "time_scope:latest" in accepted["meta"]["sanitized_removed"]


def test_ingress_can_merge_atlas_rows_into_answer_path(tmp_path: Path):
    tape_path = _sample_tape(tmp_path)
    atlas_payload = {
        "hits": [
            {"title": "Aurora Borealis", "region": "discovery", "score": 0.93},
            {"title": "Polar Light", "region": "discovery", "score": 0.61},
        ],
        "relations": {
            "knowledge": ["Aurora Borealis", "Ionosphere"],
            "exploration": ["Magnetosphere"],
        },
    }
    with patch("oradio_engine.query_codec.impl.answer_once") as mock_answer:
        mock_answer.return_value = (
            {
                "meaning": "Aurora Borealis is the top atlas match.",
                "evidence_text": "atlas matched Aurora Borealis",
                "confidence": {"total": 0.82, "top_evidence": []},
                "diagnostics": {"answer_form": {"transform": "summary", "subject": "Aurora Borealis", "claim": "Aurora Borealis is the top atlas match."}},
                "evidence": [],
            },
            [],
            [],
            type("Conf", (), {"total": 0.82, "relation": 0.7, "meaning": 0.76, "evidence": [], "words": []})(),
            [],
        )
        packet = export_packet(
            query_tapes_via_ingress(
                tape_path,
                "what causes the northern lights?",
                enable_search=False,
                atlas_payload=atlas_payload,
            )
        )
    answer_rows = mock_answer.call_args.args[1]
    assert any(row.source_kind == "atlas" for row in answer_rows)
    assert packet["meta"]["atlas"]["rows_added"] >= 2
    assert packet["meta"]["rows_loaded_atlas"] >= 2
