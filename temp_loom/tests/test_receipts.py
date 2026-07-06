"""Receipts — the bold words, made falsifiable.

- "faithful by construction": fuzz thousands of random rows; the deterministic renderer must NEVER
  introduce a name or number the row didn't contain.
- "deterministic": same tape -> byte-identical output, across calls and across fresh instances.
"""
from __future__ import annotations

import random

from oradio_engine.speech import Grammar, number_to_words

UNIVERSE = ["Hamilton", "Norris", "Verstappen", "Leclerc", "Russell", "Piastri",
            "Gasly", "Hadjar", "Bottas", "Stroll", "Sainz", "Albon"]
VERBS = {"overtake": "overtook", "pit": "pitted", "clock": "clocked", "rise": "rose", "settle": "settled"}
SPEC = {"persona": "test", "opener": "Hear ye! ",
        "form": "{opener}{transition}{actor} {verb}{object}{magnitude}{coda}",
        "transitions": ["", "Then ", "And "], "article": True,
        "codas": {"hype": ["!", " — huge!"], "alarm": [" — uh oh."], "*": ["."]}}


def _names_in(text):
    return {d for d in UNIVERSE if d in text}


def test_renderer_never_introduces_an_entity_the_row_did_not_have():
    g = Grammar(SPEC, VERBS)
    rng = random.Random(0)
    violations = []
    for i in range(4000):
        roles = {"actor": rng.choice(UNIVERSE), "action": rng.choice(list(VERBS))}
        if rng.random() < 0.5:
            roles["object"] = rng.choice(UNIVERSE)
        if rng.random() < 0.4:
            roles["magnitude"] = str(rng.randint(1, 300)); roles["unit"] = "bpm"
        if rng.random() < 0.5:
            roles["valence"] = rng.choice(["hype", "alarm", ""])
        line = g.line(roles, key=str(i))
        allowed = {roles["actor"]} | ({roles["object"]} if roles.get("object") else set())
        leaked = _names_in(line) - allowed
        if leaked:
            violations.append((roles, line, leaked))
    assert violations == []          # 4000 random rows, zero invented names


def test_renderer_only_states_the_number_it_was_given():
    g = Grammar(SPEC, VERBS)
    rng = random.Random(1)
    for i in range(1000):
        mag = rng.randint(0, 999)
        line = g.line({"actor": "Russell", "action": "rise", "magnitude": str(mag), "unit": "bpm"}, key=str(i))
        assert number_to_words(mag) in line          # the given number is present...
        for other in (mag + 1, mag + 7, mag * 2 + 3):
            if number_to_words(other) != number_to_words(mag):
                assert f" {number_to_words(other)} " not in f" {line} "   # ...and no other


def test_deterministic_byte_identical():
    rows = [{"actor": "Hamilton", "action": "overtake", "object": "Norris", "valence": "hype", "_key": "a"},
            {"actor": "Russell", "action": "clock", "object": "fastest lap", "definite": "1", "_key": "b"},
            {"actor": "Hamilton", "action": "pit", "ordinal": "2", "_key": "c"}]
    a = Grammar(SPEC, VERBS).narrate(rows)
    for _ in range(50):
        assert Grammar(dict(SPEC), dict(VERBS)).narrate(rows) == a   # stable across fresh instances
