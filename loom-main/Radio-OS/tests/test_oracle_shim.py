"""Oracle Kingdom runs as a real organ inside the federation.

Oracle's signature property is the "pebble -> destiny" divergence (different Oracle
builds/seeds -> meaningfully different kingdoms), so unlike Neikos its bus output IS
seed-emergent. We assert that here on the real organ.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Clock, Determinism, FederationEngine, SimulationOrgan  # noqa: E402
from oradio_engine.shims.oracle_shim import OracleKingdomOrgan  # noqa: E402


def _organ(name="kingdom", seed=42, ratio=15):
    return OracleKingdomOrgan.from_seed(name, seed, world_ticks_per_clock_tick=ratio)


def test_oracle_organ_satisfies_contract():
    organ = _organ()
    assert isinstance(organ, SimulationOrgan)
    assert organ.identity().determinism is Determinism.DETERMINISTIC


def test_runs_on_federation_clock_and_speaks():
    eng = FederationEngine(clock=Clock())
    eng.register(_organ(ratio=15))
    eng.run(steps=10)  # 150 ticks -> ~10 decrees + sim events
    organ = next(iter(eng.organs.values()))
    assert organ.read_truth()["tick"] == 150
    assert eng.bus, "the kingdom should surface events/decrees"
    decrees = [c for c in eng.bus if c.type == "decree"]
    assert decrees, "the Oracle should have spoken at least once"
    assert all(c.source == "kingdom" for c in eng.bus)


def test_reproducibility_axis_1():
    a = FederationEngine(clock=Clock()); a.register(_organ(seed=42))
    b = FederationEngine(clock=Clock()); b.register(_organ(seed=42))
    out_a = [c.as_dict() for c in a.run(steps=12)]
    out_b = [c.as_dict() for c in b.run(steps=12)]
    assert out_a == out_b, "same seed must replay byte-identical (axis #1)"


def test_pebble_to_destiny_divergence_axis_5():
    # Oracle IS passively emergent: different seeds -> different decree/event streams.
    a = FederationEngine(clock=Clock()); a.register(_organ(seed=42))
    b = FederationEngine(clock=Clock()); b.register(_organ(seed=7))
    out_a = [c.body for c in a.run(steps=12)]
    out_b = [c.body for c in b.run(steps=12)]
    assert out_a != out_b, "distinct seeds must diverge (emergence)"


def test_apply_input_makes_oracle_speak():
    organ = _organ(ratio=15)
    organ.advance(1)
    organ.apply_input({"intent": "speak"})
    delta = organ.advance(2)
    assert any(e["type"] == "decree" for e in delta.events), \
        "apply_input should force a decree on the next advance"
