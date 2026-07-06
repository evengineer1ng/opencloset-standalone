"""The thread-puller — the loom. Smartness as causal reach (depth), domain-agnostic."""

from __future__ import annotations

from oradio_engine.speech import Grammar
from oradio_engine.thread import actor_chains, narrate_salient, weave

VERBS = {"overtake": "overtook", "clock": "clocked", "pit": "pitted", "rise": "rose", "settle": "settled"}
G = Grammar({"form": "{actor} {verb}{object}{magnitude}{coda}", "codas": {"*": [""]}}, VERBS)

F1 = [
    {"actor": "Hamilton", "action": "pit", "pronoun": "he", "_key": "1"},
    {"actor": "Verstappen", "action": "overtake", "object": "Norris", "_key": "2"},
    {"actor": "Hamilton", "action": "clock", "object": "fastest lap", "definite": "1", "pronoun": "he", "_key": "3"},
    {"actor": "Hamilton", "action": "overtake", "object": "Russell", "pronoun": "he", "_key": "4"},
]


def test_actor_chains_links_same_actor_only():
    prev, nxt = actor_chains(F1)
    assert prev[2] == 0 and nxt[2] == 3      # Hamilton: 0 -> 2 -> 3
    assert prev[1] is None and nxt[1] is None  # Verstappen stands alone


def test_depth_zero_is_the_bare_event():
    assert weave(F1, 2, G, depth=0) == "Hamilton clocked the fastest lap."


def test_each_hop_earns_a_clause_by_a_link():
    d0 = weave(F1, 2, G, depth=0)
    d1 = weave(F1, 2, G, depth=1)
    d2 = weave(F1, 2, G, depth=2)
    assert d1.startswith("After Hamilton pitted,") and "he clocked the fastest lap" in d1
    assert "and then he overtook Russell" in d2
    assert len(d2) > len(d1) > len(d0)       # verbosity grows, but every clause is on the thread


def test_thread_is_domain_agnostic():
    heart = [
        {"actor": "your heart", "action": "rise", "magnitude": "118", "unit": "bpm", "pronoun": "it", "_key": "1"},
        {"actor": "your heart", "action": "settle", "magnitude": "72", "unit": "bpm", "pronoun": "it", "_key": "2"},
    ]
    d1 = weave(heart, 1, G, depth=1)
    assert d1.startswith("After your heart rose to one hundred eighteen bpm,")
    assert "it settled to seventy-two bpm" in d1   # same code, no domain knowledge


def test_deterministic():
    assert weave(F1, 2, G, depth=2) == weave(F1, 2, G, depth=2)


# --- #2 typed causal edges, #1 salience-seeded threading + dedup --------------------------- #
RULES = [{"cause": "pit", "effect": "clock", "within": 6, "same_actor": True, "phrase": "On fresh tyres, "}]


def test_typed_edge_folds_cause_into_a_causal_phrase():
    events = [
        {"actor": "Hamilton", "action": "pit", "lap": "20", "priority": 0.45, "pronoun": "he", "_key": "1"},
        {"actor": "Hamilton", "action": "clock", "object": "fastest lap", "definite": "1",
         "lap": "23", "priority": 0.85, "pronoun": "he", "_key": "2"},
    ]
    # typed (real causality) — the pit is folded into a phrase, not stated as a bare clause
    assert weave(events, 1, G, depth=1, rules=RULES) == "On fresh tyres, Hamilton clocked the fastest lap."


def test_salience_seeds_and_dedups_into_one_story():
    events = [
        {"actor": "A", "action": "pit", "lap": "10", "priority": 0.45, "_key": "1"},
        {"actor": "A", "action": "clock", "object": "fastest lap", "definite": "1",
         "lap": "12", "priority": 0.85, "pronoun": "he", "_key": "2"},
        {"actor": "A", "action": "overtake", "object": "B", "lap": "13", "priority": 0.7, "pronoun": "he", "_key": "3"},
    ]
    stories = narrate_salient(events, G, depth=2, rules=RULES, min_priority=0.7)
    assert len(stories) == 1                       # seed pulls cause+consequence -> ONE story, not 3 lines
    assert "On fresh tyres" in stories[0][1] and "and then" in stories[0][1]
