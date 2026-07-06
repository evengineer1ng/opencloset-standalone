"""The headless booth engine — antenna + threads in one call (no LLM needed for the core)."""

from __future__ import annotations

from booth import render_session
from oradio_engine.antenna import Antenna, Source
from oradio_engine.mix import Mixer

RACE = [{"action": "overtake", "actor": "Hamilton", "object": "Norris", "priority": 0.9, "lap": "1", "_key": "1"},
        {"action": "pit", "actor": "Hamilton", "priority": 0.9, "lap": "3", "ordinal": "2", "_key": "2"}]
NEWS = [{"title": "Hamilton leads the standings", "body": "Hamilton leads the standings", "priority": 0.9, "_key": "n"}]


def test_render_session_threads_the_antenna():
    ant = Antenna().add(Source("race", RACE)).add(Source("news", NEWS))
    stories, questions = render_session(ant, Mixer(depth=2, salience=0.5))   # color/curiosity off -> no LLM
    assert stories and any("Hamilton" in line for _, line in stories)
    assert questions == []


def test_toggling_a_source_drops_it():
    ant = Antenna().add(Source("race", RACE)).add(Source("news", NEWS))
    ant.toggle("news", False)
    stories, _ = render_session(ant, Mixer(depth=1, salience=0.5))
    assert stories and not any("standings" in line for _, line in stories)   # news lane gone
