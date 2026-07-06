"""Tier 1 narration: deterministic, provider-free, world-grounded.

These tests prove the "listen into" claim without any LLM — a goosebumps loom
must narrate recognizably different lines than a boardroom loom, and the same
beat must always render the same line.
"""
from types import SimpleNamespace

from loom.loom_studio import LoomFormState, build_loom_descriptor
from loom_narration import Narrator, dominant_key


def _beat(event_type, body):
    return SimpleNamespace(type=event_type, body=body, title=event_type)


def test_dominant_key_is_deterministic_on_ties():
    # equal weights -> alphabetical tie-break, never random
    assert dominant_key({"horror": 1.0, "drama": 1.0}) == "drama"
    assert dominant_key({}) is None
    assert dominant_key(None) is None


def test_line_grounds_in_world_and_genre():
    narrator = Narrator("Fear Street", genre_mix={"horror": 1.0}, tone_mix={"dread": 1.0})
    line = narrator.line(_beat("thread_opened", "New open question: Will the fallout spread?"))
    assert "Fear Street" in line
    assert "dread" in line
    assert "Will the fallout spread?" in line  # grounded in the beat body
    assert "New open question:" not in line  # prefix cleaned


def test_line_is_deterministic():
    narrator = Narrator("Fear Street", genre_mix={"horror": 1.0})
    beat = _beat("prediction_settled", "A forward call settled: hit.")
    assert narrator.line(beat) == narrator.line(beat)


def test_different_genre_frames_diverge():
    horror = Narrator("X", genre_mix={"horror": 1.0}).line(_beat("thread_opened", "a question"))
    comedy = Narrator("X", genre_mix={"comedy": 1.0}).line(_beat("thread_opened", "a question"))
    assert horror != comedy


def _loom(name, premise, genres):
    return LoomFormState(
        name=name, world_kind="forkuniverse", seed=42, premise=premise,
        enabled_signals=[], spatial_nodes=[], loop_mode="builtin", builtin_theme="ribbon",
        loop_path="", voice_provider="none", voice_assignments={}, transient_enabled=False,
        transient_title="", transient_min_priority=0.6, transient_body_template="",
        genres=genres, tones=["dread"] if genres == ["horror"] else [],
    )


def test_end_to_end_goosebumps_narrates_differently_than_boardroom():
    """North-star Tier 1 check: two real loom runs, narrated, must read differently."""
    from oradio_engine import load_oradio

    def narrate_run(state):
        descriptor = build_loom_descriptor(state)
        engine = load_oradio(descriptor)
        for _ in range(25):
            engine.tick()
        narrator = Narrator.from_descriptor(descriptor)
        return [narrator.line(c) for c in engine.bus]

    horror = narrate_run(_loom("Fear Street", "a goosebumps horror neighborhood where a cursed dummy stalks kids", ["horror"]))
    boardroom = narrate_run(_loom("Boardroom", "a tense corporate boardroom tracking quarterly earnings", ["drama"]))

    assert horror, "the horror loom should narrate at least one line"
    assert horror != boardroom, "the two looms must narrate recognizably different lines"
    # Each names its own world.
    assert any("Fear Street" in line for line in horror)
    assert any("Boardroom" in line for line in boardroom)
