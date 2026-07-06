"""The basketball play-by-play replay source — thin-wire shape + engine integration."""

from __future__ import annotations

from oradio_engine.shims.basketball_shim import (
    BasketballPlayByPlaySource,
    _play_to_event,
    make_basketball_pbp,
)


_PLAYS = [
    {"type": {"text": "Jumpball"}, "text": "A vs B (tip-off)", "period": {"number": 1},
     "clock": {"displayValue": "12:00"}, "team": {"abbreviation": "NY"}, "id": "1"},
    {"type": {"text": "Made Three Point Jumper"}, "text": "Player makes 26-foot three", "period": {"number": 1},
     "clock": {"displayValue": "11:40"}, "team": {"abbreviation": "SA"},
     "scoringPlay": True, "scoreValue": 3, "homeScore": 3, "awayScore": 0, "id": "2"},
    {"type": {"text": "Turnover"}, "text": "Bad pass", "period": {"number": 1},
     "clock": {"displayValue": "11:20"}, "team": {"abbreviation": "NY"}, "id": "3"},
]


def test_play_maps_to_thin_wire_without_score_in_text():
    ev = _play_to_event(_PLAYS[1])
    # the five thin-wire fields are present
    assert {"title", "body", "type", "priority", "tags"} <= ev.keys()
    # a made three is high priority and fires bloom/embers + ripple tags
    assert ev["priority"] >= 0.85
    assert {"make", "impact", "wave", "heat", "flare"} <= set(ev["tags"])
    # the running score is carried as data, never folded into narratable text
    assert "3" not in ev["title"]
    assert str(ev["home_score"]) not in ev["body"]


def test_turnover_fires_glitch_tags():
    ev = _play_to_event(_PLAYS[2])
    assert {"rupture", "pressure"} <= set(ev["tags"])


def test_source_replays_in_order_then_stops():
    src = BasketballPlayByPlaySource(_PLAYS)
    titles = []
    for _ in range(len(_PLAYS) + 2):
        titles.extend(p["title"] for p in src.poll())
    assert len(titles) == len(_PLAYS)          # exhausts, then yields nothing
    assert titles[0].startswith("Q1 12:00")    # tip-off first, order preserved
    assert src.remaining == 0


def test_per_poll_batches_plays():
    src = BasketballPlayByPlaySource(_PLAYS, per_poll=2)
    first = src.poll()
    assert len(first) == 2


def test_builds_a_live_organ_via_factory():
    organ = make_basketball_pbp("court", plays=_PLAYS)
    delta = organ.advance(1)
    cands = organ.observe(delta)
    assert cands and cands[0].source == "court"
    assert cands[0].type == "play"


# --- the two broadcast transforms (the pluggable engine, not the file formats) --------- #
from oradio_engine.binding import build_transform
from oradio_engine.contract import NormalizedCandidate


def _play_candidate(body="Player makes 26-foot three", ctype="play"):
    return NormalizedCandidate(post_id="court:1:0", source="court", title="Q1 11:40 · play",
                               body=body, priority=0.9, ts=1.0, type=ctype, tags=())


def test_play_to_call_speaks_the_play_verbatim():
    call = build_transform("play_to_call")
    assert call(_play_candidate())["text"] == "Player makes 26-foot three"
    assert call(_play_candidate(ctype="presence")) is None  # only acts on plays


def test_play_to_mindset_degrades_gracefully_without_endpoint():
    mind = build_transform("play_to_mindset", intent="the vibes", endpoint="", model="llama3")
    out = mind(_play_candidate())
    assert out is not None and "Player makes 26-foot three" in out["text"]
    assert "awaiting llm" in out["text"]                      # clearly marked stub
    assert mind(_play_candidate(ctype="action")) is None
