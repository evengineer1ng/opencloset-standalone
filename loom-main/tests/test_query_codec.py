from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from oradio_engine.codec import codec_manifest_dict, decode_audio, encode_audio
from oradio_engine.packet import CODEC_VERSION, PACKET_VERSION
from oradio_engine.query_codec import export_packet, query_tapes, render_evidence, render_meaning


ROOT = Path(__file__).resolve().parents[2]


def _sample_tape(tmp_path: Path) -> Path:
    tape = [
        {"actor": "Hamilton", "action": "pit", "object": "soft tyres", "lap": 5, "priority": 0.7, "valence": "calm"},
        {"actor": "Leclerc", "action": "overtake", "object": "Russell", "lap": 8, "priority": 0.86, "valence": "hype"},
        {"actor": "Leclerc", "action": "win", "object": "the race", "lap": 16, "priority": 0.98, "valence": "hype"},
    ]
    path = tmp_path / "f1.tape.json"
    path.write_text(json.dumps(tape), encoding="utf-8")
    return path


def _load_root_wrapper():
    spec = importlib.util.spec_from_file_location("oradio_tape_synth_v2", ROOT / "oradio_tape_synth_v2.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _extract_embedded_spec(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    start = text.index("/* OFFICIAL_CODEC_SPEC_START */")
    end = text.index("/* OFFICIAL_CODEC_SPEC_END */")
    block = text[start:end]
    marker = "const OFFICIAL_CODEC_SPEC="
    begin = block.index(marker) + len(marker)
    finish = block.index(";\nconst OFFICIAL_PACKET_VERSION")
    return json.loads(block[begin:finish])


def test_query_packet_is_deterministic(tmp_path: Path):
    tape_path = _sample_tape(tmp_path)
    packet_a = export_packet(query_tapes(tape_path, "who won?", enable_search=False))
    packet_b = export_packet(query_tapes(tape_path, "who won?", enable_search=False))
    assert packet_a == packet_b
    assert packet_a["packet_version"] == PACKET_VERSION
    assert packet_a["codec_version"] == CODEC_VERSION


def test_root_wrapper_matches_package_api(tmp_path: Path):
    tape_path = _sample_tape(tmp_path)
    packet = export_packet(query_tapes(tape_path, "who won?", enable_search=False))
    wrapper = _load_root_wrapper()
    compat = wrapper.build_packet(tape_path, "who won?", enable_search=False)
    assert compat == packet


def test_renderers_and_audio_codec_roundtrip(tmp_path: Path):
    tape_path = _sample_tape(tmp_path)
    packet = query_tapes(tape_path, "who won the race?", enable_search=False)
    meaning = render_meaning(packet)
    evidence = render_evidence(packet)
    assert "Leclerc" in meaning
    assert "lap" in evidence
    assert packet.synthesis.template_id
    assert packet.evidence_citations
    artifact = encode_audio(packet)
    decoded = decode_audio(artifact)
    assert decoded["text"] == artifact["text"]
    assert artifact["codec_version"] == CODEC_VERSION


def test_search_repair_can_be_enabled_and_disabled(tmp_path: Path, monkeypatch):
    tape_path = _sample_tape(tmp_path)
    from oradio_engine import query_codec_impl as impl

    def fake_search(query: str, round_no: int, item_type: str, cfg: dict):
        item = {
            "actor": "duckduckgo",
            "action": "return",
            "object": f"search result for {query}",
            "lap": round_no * 100 + 1,
            "priority": 0.8,
            "valence": "calm",
            "source": "https://duckduckgo.com",
            "source_domain": "duckduckgo.com",
            "kind": "search_result",
            "thread": f"search:{query}",
        }
        return [impl.normalize_row(item, 0, "search:test", "search")]

    monkeypatch.setattr(impl, "ddg_search_tape", fake_search)
    forced_cfg = {"target_confidence": 0.99, "max_search_rounds": 1}
    with_search = query_tapes(tape_path, "what is telemetry coefficient", cfg=forced_cfg)
    without_search = query_tapes(tape_path, "what is telemetry coefficient", enable_search=False, cfg=forced_cfg)
    assert with_search.meta["search_log"]
    assert not without_search.meta["search_log"]
    assert any(row.source_kind == "search" for row in with_search.source_rows) is False
    assert with_search.meta["rows_after_search"] > without_search.meta["rows_after_search"]


def test_embedded_browser_specs_match_package_manifest():
    expected = codec_manifest_dict()
    booth = _extract_embedded_spec(ROOT / "booth-timestamp-pitch.html")
    decoder = _extract_embedded_spec(ROOT / "loom_timestamp_decoder.html")
    assert booth == expected
    assert decoder == expected
