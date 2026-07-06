"""The guarded colorist — adds flair, rejects hallucination, falls back to the mirror."""

from __future__ import annotations

from colorist import Colorist, introduces_unsupported

ENTITIES = {"Hamilton", "Norris", "Verstappen", "Leclerc"}
LINE = "Hamilton overtook Norris"


def test_guard_flags_invented_specifics():
    assert introduces_unsupported("Hamilton overtook Norris for second place", LINE, ENTITIES)  # position
    assert introduces_unsupported("Hamilton overtook Norris into Turn 5", LINE, ENTITIES)        # turn
    assert introduces_unsupported("Hamilton's Ferrari swept past Norris", LINE, ENTITIES)        # team
    assert introduces_unsupported("Hamilton overtook Leclerc", LINE, ENTITIES)                   # other driver
    assert introduces_unsupported("Hamilton overtook Norris in car #44", LINE, ENTITIES)         # car number
    assert introduces_unsupported("Hamilton won the soccer match", LINE, ENTITIES)               # wrong sport
    assert introduces_unsupported("It was a 4-2 thrashing for Hamilton", LINE, ENTITIES)         # invented score


def test_guard_passes_safe_flair():
    assert not introduces_unsupported("Hamilton brilliantly swept past Norris", LINE, ENTITIES)


def test_colorize_keeps_safe_flair():
    out = Colorist("x").colorize(LINE, ENTITIES, gen=lambda p: "Hamilton brilliantly swept past Norris")
    assert out == "Hamilton brilliantly swept past Norris"


def test_colorize_falls_back_on_hallucination():
    out = Colorist("x").colorize(LINE, ENTITIES, gen=lambda p: "Verstappen grabs second place in his Red Bull")
    assert out == LINE          # guard rejected -> mirror floor


def test_colorize_falls_back_on_error():
    def boom(p):
        raise RuntimeError("no model")
    assert Colorist("x").colorize(LINE, ENTITIES, gen=boom) == LINE
