"""F1 via the generic tape-replay source + the F1 lexicon — domain is data, not code."""

from __future__ import annotations

from oradio_engine.binding import build_transform
from oradio_engine.contract import NormalizedCandidate
from oradio_engine.shims.tape_replay import make_tape_replay

ROWS = [
    {"title": "a", "body": "a", "type": "f1", "priority": 0.7,
     "tags": ["actor:Verstappen", "action:overtake", "object:Norris", "valence:hype"]},
    {"title": "b", "body": "b", "type": "f1", "priority": 0.85,
     "tags": ["actor:Russell", "action:clock", "object:fastest_lap", "definite:1"]},
]


def test_tape_replay_emits_rows_in_order():
    organ = make_tape_replay("trackside", rows=ROWS)
    first = organ.observe(organ.advance(1))
    second = organ.observe(organ.advance(2))
    assert first[0].source == "trackside"
    assert any("Verstappen" in t for t in first[0].tags)
    assert any("Russell" in t for t in second[0].tags)


def test_grammar_narrates_overtake_and_fastest_no_domain_lexicon():
    # F1 carries NO style file — a general grammar + the shared English verb table speak it.
    t = build_transform("tape_to_speech", grammar="data/grammars/intern.json",
                        verbs="data/english/irregular_verbs.json")
    over = t(NormalizedCandidate("p1", "trackside", "", "", 0.7, 1.0, "f1", tuple(ROWS[0]["tags"])))
    assert over and "Norris" in over["text"] and " a Norris" not in over["text"]
    fast = t(NormalizedCandidate("p2", "trackside", "", "", 0.85, 1.0, "f1", tuple(ROWS[1]["tags"])))
    assert fast and "the fastest lap" in fast["text"]
