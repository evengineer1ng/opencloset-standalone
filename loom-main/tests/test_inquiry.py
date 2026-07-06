"""Inquiry — general expectation TYPES, instantiated by a DECLARATION; curiosity is the dial."""

from __future__ import annotations

from oradio_engine.inquiry import Inquiry, _no_repeat, _streak_then, investigate
from oradio_engine.speech import Grammar

G = Grammar({"form": "{actor} {verb}{object}{magnitude}{coda}", "codas": {"*": [""]}},
            {"pit": "pitted", "clock": "clocked", "overtake": "overtook"})

DOUBLE_PIT = [
    {"actor": "Lindblad", "action": "pit", "lap": "29", "_key": "1"},
    {"actor": "Verstappen", "action": "pit", "lap": "30", "_key": "2"},
    {"actor": "Lindblad", "action": "pit", "lap": "31", "_key": "3"},   # 2 laps after his last
]
MIX = [
    {"actor": "A", "action": "pit", "lap": "10", "_key": "1"},
    {"actor": "A", "action": "pit", "lap": "12", "_key": "2"},          # pit double (level 1)
    {"actor": "B", "action": "clock", "lap": "20", "_key": "3"},
    {"actor": "B", "action": "clock", "lap": "21", "_key": "4"},        # clock double (level 2)
]


def test_no_repeat_type_fires_on_quick_double():
    qs = _no_repeat(DOUBLE_PIT, {"action": "pit", "within": 4})
    assert len(qs) == 1 and qs[0].about == 2 and "Lindblad" in qs[0].text


def test_declaration_instantiates_general_types():
    qs = Inquiry([{"type": "no_repeat", "action": "pit", "within": 4}]).ask(DOUBLE_PIT)
    assert len(qs) == 1   # the SAME general type would serve rings/markets with different params


def test_curiosity_dial_activates_deeper_expectations():
    inq = Inquiry([
        {"type": "no_repeat", "action": "pit", "within": 4, "at": 1},
        {"type": "no_repeat", "action": "clock", "within": 3, "at": 2},
    ])
    low = inq.ask(MIX, curiosity=1)    # only level-1 expectation
    high = inq.ask(MIX, curiosity=2)   # deeper expectation also active
    assert len(low) == 1 and len(high) == 2


def test_streak_then_detects_a_stalled_charge():
    events = [
        {"actor": "Hadjar", "action": "overtake", "object": "Gasly", "lap": "6", "_key": "1"},
        {"actor": "Hadjar", "action": "overtake", "object": "Colapinto", "lap": "7", "_key": "2"},
        {"actor": "Hadjar", "action": "pit", "lap": "8", "_key": "3"},
    ]
    qs = _streak_then(events, {"action_a": "overtake", "count": 2, "action_b": "pit", "within": 2})
    assert len(qs) == 1 and "Hadjar" in qs[0].text and "stall" in qs[0].text


def test_investigate_pairs_question_with_traced_thread():
    qs = Inquiry([{"type": "no_repeat", "action": "pit", "within": 4}]).ask(DOUBLE_PIT)
    answered = investigate(DOUBLE_PIT, G, qs, depth=2)
    assert len(answered) == 1
    question, thread = answered[0]
    assert question.endswith("?") and "Lindblad" in thread
