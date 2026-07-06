from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from oradio_engine.local_ingress_server import LoomIngressApp


def _sample_tape(tmp_path: Path) -> Path:
    tape = [
        {"actor": "Hamilton", "action": "pit", "object": "soft tyres", "lap": 5, "priority": 0.7, "valence": "calm"},
        {"actor": "Leclerc", "action": "overtake", "object": "Russell", "lap": 8, "priority": 0.86, "valence": "hype"},
        {"actor": "Leclerc", "action": "win", "object": "the race", "lap": 16, "priority": 0.98, "valence": "hype"},
    ]
    path = tmp_path / "f1.tape.json"
    path.write_text(json.dumps(tape), encoding="utf-8")
    return path


def test_local_ingress_app_ticket_answer_and_evidence(tmp_path: Path):
    tape = _sample_tape(tmp_path)
    app = LoomIngressApp(tapes=tape, enable_search=False)
    ticket = app.build_ticket({"query": "who won the race?"})
    assert ticket["accepted_query"]
    answer = app.build_answer({"query": "who won the race?", "enable_search": False})
    assert answer["synthesis"]["template_id"]
    evidence_id = answer["evidence_citations"][0]["citation_id"]
    evidence = app.get_evidence(evidence_id)
    assert evidence["citation_id"] == evidence_id


def test_local_ingress_app_can_generate_translator_payload_from_ollama(tmp_path: Path):
    tape = _sample_tape(tmp_path)
    app = LoomIngressApp(tapes=tape, enable_search=False, ollama_model="rnj-1:8b")
    fake = {
        "candidates": [
            {
                "source": "ollama",
                "normalized_query": "who won the race",
                "transform": "rank",
                "focus": "race winner",
                "entities": ["Leclerc"],
                "confidence": 0.91,
            }
        ],
        "_ollama": {"model": "rnj-1:8b"},
    }
    with patch("oradio_engine.local_ingress_server.generate_candidates", return_value=fake) as mock_generate:
        ticket = app.build_ticket({"query": "okay so i was wondering who actually ended up winning this race"})
    assert ticket["translator_payload"]["_ollama"]["model"] == "rnj-1:8b"
    assert ticket["accepted_query"] == "who won the race"
    mock_generate.assert_called_once()


def test_local_ingress_app_unifies_atlas_into_answer_path(tmp_path: Path):
    tape = _sample_tape(tmp_path)
    app = LoomIngressApp(tapes=tape, enable_search=False, atlas_dir="atlas-pack")
    atlas_payload = {
        "query": "what causes northern lights",
        "hits": [{"title": "Aurora Borealis", "region": "discovery", "score": 0.94}],
        "relations": {"knowledge": ["Aurora Borealis", "Ionosphere"]},
    }
    with patch.object(app, "atlas_retrieve", return_value=atlas_payload):
        answer = app.build_answer({"query": "what causes the northern lights?", "enable_search": False})
    assert answer["atlas_retrieval"]["hits"][0]["title"] == "Aurora Borealis"
    assert answer["meta"]["atlas"]["hit_count"] == 1
    assert any(row["source_kind"] == "atlas" for row in answer["source_rows"])
