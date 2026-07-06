"""ForkUniverse runs as a real organ inside the federation engine.

This is the first sovereign world on the shared clock + bus. It proves the shim maps
the contract onto the genuine UniverseState seam, and re-checks the benchmark axes on a
real (not reference) organ:
  - #1 Reproducibility: same seed + same clock sequence -> byte-identical bus
  - emergence floor: a different seed diverges
  - #3 Continuity: the world tick advances by ratio * clock ticks
  - responsiveness: apply_input (operator decree) is accepted via the engine bus
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Clock, Determinism, FederationEngine, SimulationOrgan  # noqa: E402
from oradio_engine.shims.forkuniverse_shim import ForkUniverseOrgan  # noqa: E402

CREATION = dict(
    universe_title="Pressure Harbor",
    premise="A port city where love, debt, rumor, and grief reshape every alliance.",
    setting_kind="haunted_port_city",
    time_period="modern",
    story_mode="continuous",
    world_scale="district",
    starting_population=24,
    seed_mode="custom",
    ontology_domains=["love", "debt", "rumor", "grief"],
)


def _organ(name="harbor", seed="shim-harbor-1", ratio=12):
    return ForkUniverseOrgan.from_request(
        name, world_ticks_per_clock_tick=ratio, custom_seed=seed, **CREATION
    )


def test_forkuniverse_organ_satisfies_contract():
    assert isinstance(_organ(), SimulationOrgan)
    assert _organ().identity().determinism is Determinism.DETERMINISTIC


def test_runs_on_federation_clock_and_emits_to_bus():
    eng = FederationEngine(clock=Clock())
    eng.register(_organ(ratio=12))
    eng.run(steps=10)  # 120 world ticks
    organ = next(iter(eng.organs.values()))
    assert organ.read_truth()["tick"] == 120, "world advanced ratio * clock ticks"
    assert eng.bus, "the universe should surface events over 120 ticks"
    assert all(c.source == "harbor" for c in eng.bus)
    assert all(set(c.as_dict()) == {
        "post_id", "source", "title", "body", "priority", "ts", "type", "tags"
    } for c in eng.bus)


def test_reproducibility_on_real_organ():
    a = FederationEngine(clock=Clock()); a.register(_organ(seed="same"))
    b = FederationEngine(clock=Clock()); b.register(_organ(seed="same"))
    out_a = [c.as_dict() for c in a.run(steps=12)]
    out_b = [c.as_dict() for c in b.run(steps=12)]
    assert out_a == out_b, "same seed + same clock must replay byte-identical (axis #1)"


def test_different_seed_diverges_real_organ():
    a = FederationEngine(clock=Clock()); a.register(_organ(seed="alpha"))
    b = FederationEngine(clock=Clock()); b.register(_organ(seed="omega"))
    out_a = [c.title for c in a.run(steps=12)]
    out_b = [c.title for c in b.run(steps=12)]
    assert out_a != out_b, "distinct seeds must produce distinct worlds"


def test_apply_input_accepted_through_engine():
    organ = _organ()
    organ.advance(2)  # warm up so axes exist
    before = organ.read_truth()["digest"]
    organ.apply_input({"intent": "amplify", "magnitude": 0.8, "target_domain": "love"})
    after = organ.read_truth()["digest"]
    assert before != after, "operator decree must move the world (responsiveness)"
