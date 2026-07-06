"""FTB runs as a real organ inside the federation — the competition/economy world.

Ambient (no player team): the calendar advances and the league emits race results,
contract expiries, financial stress. FTB is passively emergent (seed -> different season).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Clock, Determinism, FederationEngine, SimulationOrgan  # noqa: E402
from oradio_engine.shims.ftb_shim import FTBOrgan  # noqa: E402


def _organ(name="grid", seed=42, ratio=7):
    return FTBOrgan.from_seed(name, seed, world_ticks_per_clock_tick=ratio)


def test_ftb_organ_satisfies_contract():
    organ = _organ()
    assert isinstance(organ, SimulationOrgan)
    assert organ.identity().determinism is Determinism.DETERMINISTIC
    assert organ.read_truth()["leagues"] > 0, "world should be populated"


def test_runs_on_federation_clock_and_emits():
    eng = FederationEngine(clock=Clock())
    eng.register(_organ(ratio=7))
    eng.run(steps=12)  # 84 sim days
    organ = next(iter(eng.organs.values()))
    assert organ.read_truth()["tick"] == 84
    assert eng.bus, "an ambient FTB season should surface world events over 84 days"
    assert all(c.source == "grid" for c in eng.bus)


@pytest.mark.xfail(
    reason="FINDING (2026-06-12): FTB leaks determinism despite its 'seed for deterministic "
    "replay' docstring. Two same-seed runs diverge in event VOLUME (not just values). Causes: "
    "(1) unseeded global random.* in ftb_game.py (random.shuffle ai_teams ~4660, random.random "
    "poaching ~4698/4801) bypassing state.get_rng; (2) wall-clock live play-by-play "
    "(_live_pbp_start_ts = time.time() ~9267, 2s/event) makes pbp volume depend on real time. "
    "Fix deferred to the engine-iterate phase (route those paths through state.get_rng; gate or "
    "tick-pace live pbp). The harness caught this — that's the point.",
    strict=True,
)
def test_reproducibility_axis_1():
    a = FederationEngine(clock=Clock()); a.register(_organ(seed=42))
    b = FederationEngine(clock=Clock()); b.register(_organ(seed=42))
    out_a = [c.as_dict() for c in a.run(steps=10)]
    out_b = [c.as_dict() for c in b.run(steps=10)]
    assert out_a == out_b, "same seed must replay byte-identical (axis #1)"


def test_different_seed_diverges_axis_5():
    a = FederationEngine(clock=Clock()); a.register(_organ(seed=42))
    b = FederationEngine(clock=Clock()); b.register(_organ(seed=7))
    out_a = [c.body for c in a.run(steps=10)]
    out_b = [c.body for c in b.run(steps=10)]
    assert out_a != out_b, "distinct seeds must produce distinct seasons (emergence)"
