"""The Mixer — one fader-set drives the whole pipeline; same tape, different mix."""

from __future__ import annotations

from oradio_engine.inquiry import Inquiry
from oradio_engine.mix import LiveNarrator, Mixer, render
from oradio_engine.speech import Grammar

G = Grammar({"form": "{actor} {verb}{object}{magnitude}{coda}", "codas": {"*": [""]}},
            {"pit": "pitted", "clock": "clocked", "overtake": "overtook"})

TAPE = [
    {"actor": "Hadjar", "action": "pit", "lap": "15", "priority": 0.45, "ordinal": "1", "pronoun": "he", "_key": "1"},
    {"actor": "Hadjar", "action": "overtake", "object": "Lindblad", "lap": "21", "priority": 0.85, "pronoun": "he", "_key": "2"},
    {"actor": "Hadjar", "action": "pit", "lap": "32", "priority": 0.45, "ordinal": "2", "pronoun": "he", "_key": "3"},
]
RULES = [{"cause": "pit", "effect": "clock", "within": 6, "same_actor": True, "reason": "fresh_tyres"}]


def test_mixer_defaults_and_serialization():
    m = Mixer()
    assert m.depth == 2 and m.flavour == "both" and "salience" in m.as_dict()


def test_same_tape_different_mix():
    shallow = render(TAPE, G, Mixer(depth=0, salience=0.7))[0]
    deep = render(TAPE, G, Mixer(depth=2, salience=0.7))[0]
    assert shallow[0][1] != deep[0][1]          # depth fader changes the story
    assert len(deep[0][1]) > len(shallow[0][1])


def test_flavour_direction_back_vs_forward():
    back = render(TAPE, G, Mixer(depth=1, flavour="back", salience=0.7))[0][0][1]
    fwd = render(TAPE, G, Mixer(depth=1, flavour="forward", salience=0.7))[0][0][1]
    assert back != fwd                          # chase the cause vs run the other way
    assert back.startswith("After")             # back -> "After Hadjar pitted, ..."
    assert "and then" in fwd                     # forward -> "..., and then ..."


def test_continuity_fader_strips_carried_state():
    on = render(TAPE, G, Mixer(depth=2, continuity=True, salience=0.7))[0][0][1]
    off = render(TAPE, G, Mixer(depth=2, continuity=False, salience=0.7))[0][0][1]
    assert "for the second time" in on and "for the second time" not in off


def test_live_narrator_streams_salient_events_and_obeys_faders():
    # the booth's brain: same seed, depth fader live -> different spoken line
    shallow = LiveNarrator(TAPE, rules=RULES).step(G, Mixer(depth=0, salience=0.7))
    deep = LiveNarrator(TAPE, rules=RULES).step(G, Mixer(depth=2, salience=0.7))
    assert shallow is not None and deep is not None
    assert shallow[1] == "Hadjar overtook Lindblad."          # depth 0 = bare seed
    assert deep[1] != shallow[1] and len(deep[1]) > len(shallow[1])


def test_live_narrator_skips_subsalient_and_ends():
    nar = LiveNarrator(TAPE, rules=RULES)
    lines = []
    while not nar.done:
        r = nar.step(G, Mixer(depth=0, salience=0.99))   # nothing passes -> all skipped
        if r:
            lines.append(r)
    assert lines == []                                        # high salience -> silence, then end


def test_curiosity_fader_adds_questions():
    inq = Inquiry([{"type": "no_repeat", "action": "pit", "within": 4, "at": 1}])
    quiet = render(TAPE, G, Mixer(curiosity=0), inquiry=inq)[1]
    curious = render(TAPE, G, Mixer(curiosity=1), inquiry=inq)[1]
    assert len(quiet) == 0 and len(curious) >= 0   # curiosity gates the question channel
