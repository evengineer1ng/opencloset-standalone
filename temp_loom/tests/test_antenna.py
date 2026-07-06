"""The antenna — many tapes, toggle on/off, interleaved mix; heterogeneous events coexist."""

from __future__ import annotations

from oradio_engine.antenna import Antenna, Source
from oradio_engine.mix import LiveNarrator, Mixer
from oradio_engine.speech import Grammar

RACE = [{"action": "pit", "actor": "X", "priority": 0.9, "_key": "a1"},
        {"action": "clock", "actor": "X", "priority": 0.9, "_key": "a2"}]
NEWS = [{"title": "Breaking F1 news", "body": "Breaking F1 news", "priority": 0.9, "_key": "b1"}]


def test_stream_interleaves_enabled_sources_round_robin():
    ant = Antenna().add(Source("race", RACE)).add(Source("news", NEWS))
    s = ant.stream()
    assert [e["source"] for e in s] == ["race", "news", "race"]   # race0, news0, race1
    assert s[0]["action"] == "pit" and s[1]["title"] == "Breaking F1 news"


def test_toggle_drops_a_lane_live():
    ant = Antenna().add(Source("race", RACE)).add(Source("news", NEWS))
    ant.toggle("news", False)
    s = ant.stream()
    assert len(s) == 2 and all(e["source"] == "race" for e in s)


def test_the_mix_weaves_related_news_into_the_same_thread():
    # THE MIX: one thread pulled from all tapes, linked by the entity it names
    G = Grammar({"form": "{actor} {verb}{object}{magnitude}{coda}", "codas": {"*": [""]}}, {"clock": "clocked"})
    events = [
        {"action": "clock", "actor": "Hamilton", "object": "fastest lap", "definite": "1",
         "priority": 0.9, "lap": "13", "_key": "r"},
        {"body": "Hamilton wins again in Barcelona", "priority": 0.6, "_key": "n1"},   # names Hamilton
        {"body": "Generic paddock gossip", "priority": 0.6, "_key": "n2"},             # names nobody
    ]
    nar = LiveNarrator(events)
    _lap, line = nar.step(G, Mixer(depth=0, salience=0.5))
    assert "Hamilton clocked the fastest lap" in line
    assert "meanwhile, Hamilton wins again in Barcelona" in line   # race + news, ONE thread
    assert nar.step(G, Mixer(depth=0, salience=0.5)) is None        # unlinked gossip dropped
