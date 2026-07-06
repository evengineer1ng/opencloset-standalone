"""End-to-end tests for the ForkUniverse Radio OS bridge.

These exercise the meta plugin + feed plugin contract without requiring the full
Radio OS runtime (bookmark). The plugins live in plugins/ (not a package), so we
load them by path.
"""

import importlib.util
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(module_name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(module_name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def meta_mod():
    return _load("forkuniverse_meta", "plugins/meta/forkuniverse_meta.py")


@pytest.fixture(scope="module")
def feed_mod():
    return _load("forkuniverse_feed", "plugins/forkuniverse_feed.py")


def _cfg(tmp_path, **overrides):
    fu = {
        "universe_title": "Pressure Harbor",
        "premise": "A port city where love, debt, rumor, and grief reshape every alliance.",
        "setting_kind": "haunted_port_city",
        "world_scale": "district",
        "starting_population": 24,
        "seed": "bridge-harbor-1",
        "ontology_domains": ["love", "debt", "rumor", "grief"],
        "world_ticks_per_narration_tick": 12,
        "snapshot_path": str(tmp_path / "universe.json"),
        "autosave_every": 2,
    }
    fu.update(overrides)
    return {"forkuniverse": fu}


def _init_plugin(meta_mod, tmp_path, **overrides):
    plugin = meta_mod.create_meta_plugin()
    plugin.initialize({"log": lambda *a: None}, _cfg(tmp_path, **overrides), {})
    return plugin


def test_meta_compiles_universe_on_init(meta_mod, tmp_path):
    plugin = _init_plugin(meta_mod, tmp_path)
    assert plugin.state is not None
    assert plugin.state.universe_title == "Pressure Harbor"
    assert len(plugin.state.characters) == 24


def test_session_start_is_cold_open_on_fresh_world(meta_mod, tmp_path):
    plugin = _init_plugin(meta_mod, tmp_path)
    beats = plugin.process_input({"input_type": "session_start"})
    assert len(beats) == 1
    assert beats[0]["type"] == "cold_open"
    assert beats[0]["voice"] in {"host", "narrator", "breaking", "chronicler"}
    assert beats[0]["text"]


def test_tick_advances_world_and_returns_voiced_beats(meta_mod, tmp_path):
    plugin = _init_plugin(meta_mod, tmp_path, world_ticks_per_narration_tick=20)
    before = plugin.state.time.tick
    beats = plugin.process_input({"input_type": "tick"})
    assert plugin.state.time.tick == before + 20
    # Beats are well-formed segment dicts.
    for beat in beats:
        assert beat["text"]
        assert beat["voice"] in {"host", "narrator", "breaking", "chronicler"}
        assert isinstance(beat["priority"], float)
        # Every beat is traceable back to engine state.
        assert "source_ids" in beat["metadata"]
        assert "audio_signature" in beat["metadata"]
    assert len(beats) <= plugin._fu_cfg.get("max_segments_per_tick", 3)


def test_beats_are_capped_by_max_segments(meta_mod, tmp_path):
    plugin = _init_plugin(meta_mod, tmp_path, max_segments_per_tick=1, world_ticks_per_narration_tick=40)
    # Advance a few narration ticks so plenty of material accumulates.
    for _ in range(5):
        beats = plugin.process_input({"input_type": "tick"})
        assert len(beats) <= 1


def test_autosave_persists_snapshot_and_resumes(meta_mod, tmp_path):
    snap = tmp_path / "universe.json"
    plugin = _init_plugin(meta_mod, tmp_path, autosave_every=1, world_ticks_per_narration_tick=10)
    plugin.process_input({"input_type": "tick"})
    assert snap.exists()
    tick_after = plugin.state.time.tick

    # A second plugin pointed at the same snapshot resumes the same world.
    resumed = _init_plugin(meta_mod, tmp_path, world_ticks_per_narration_tick=10)
    assert resumed.state.time.tick == tick_after
    assert resumed.state.digest() == plugin.state.digest()
    # session_start on a resumed world is a while-you-were-gone beat.
    beats = resumed.process_input({"input_type": "session_start"})
    assert beats[0]["type"] == "while_you_were_gone"


def test_feed_routes_beats_to_emit_candidate(meta_mod, feed_mod, tmp_path):
    plugin = _init_plugin(meta_mod, tmp_path, world_ticks_per_narration_tick=25)
    emitted = []
    runtime = {
        "log": lambda *a: None,
        "emit_candidate": emitted.append,
        "ACTIVE_META_PLUGIN": plugin,
    }
    stop = threading.Event()
    # Drive the feed once, fast: stop it almost immediately so the loop runs
    # session_start + at most a tick or two, then session_end.
    stop.set()
    feed_mod.feed_worker(stop, {}, {"tick_sec": 0.01, "startup_delay": 0.0}, runtime)

    # With stop pre-set, feed_worker returns after session_start routing.
    # Drive a tick manually through the same routing path instead:
    beats = plugin.process_input({"input_type": "tick"})
    routed = feed_mod._route_segments(beats, emitted.append, None, lambda *a: None)
    assert routed == len([b for b in beats if b["text"]])
    for cand in emitted:
        assert cand["source"] == "forkuniverse_feed"
        assert cand["body"]
        assert cand["_literal"] is True
        assert cand["voice"]
