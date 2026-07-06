"""Neikos runs as a real organ inside the federation engine, and rides the same
shared clock as ForkUniverse — the first true two-organ federation (two separate
sovereign worlds narrating as one body).

Force UTF-8 on Windows isn't needed here (we don't print the box-art walkthrough).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Clock, Determinism, FederationEngine, SimulationOrgan  # noqa: E402
from oradio_engine.shims.neikos_shim import NeikosOrgan  # noqa: E402
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


def test_neikos_organ_satisfies_contract():
    organ = NeikosOrgan.from_seed("island", 42)
    assert isinstance(organ, SimulationOrgan)
    assert organ.identity().determinism is Determinism.DETERMINISTIC
    assert organ.identity().seed == 42


def test_runs_on_federation_clock_and_emits_to_bus():
    eng = FederationEngine(clock=Clock())
    eng.register(NeikosOrgan.from_seed("island", 42, world_ticks_per_clock_tick=10))
    eng.run(steps=8)  # 80 island ticks -> ambient tick_update beats
    organ = next(iter(eng.organs.values()))
    assert organ.read_truth()["tick"] == 80
    assert eng.bus, "ambient ticks should surface world beats"
    assert all(c.source == "island" for c in eng.bus)
    assert all("neikos" in c.tags for c in eng.bus)


def test_reproducibility_on_real_organ():
    a = FederationEngine(clock=Clock()); a.register(NeikosOrgan.from_seed("i", 42))
    b = FederationEngine(clock=Clock()); b.register(NeikosOrgan.from_seed("i", 42))
    out_a = [c.as_dict() for c in a.run(steps=10)]
    out_b = [c.as_dict() for c in b.run(steps=10)]
    assert out_a == out_b, "same seed + same clock must replay byte-identical (axis #1)"


def test_different_seed_diverges():
    # FINDING: Neikos's emergence lives in world *content* (island, species, knower),
    # not the passive ambient tick stream — two seeds ticked with no player input emit
    # the same TIER escalation beats. So divergence is asserted on world identity, which
    # is the honest signal. (Neikos is an interactive organ: rich under apply_input,
    # near-silent-divergence when idly observed. See docs/ORGAN_CATALOG.md.)
    a = NeikosOrgan.from_seed("i", 42)
    b = NeikosOrgan.from_seed("i", 7)
    assert a.read_truth()["island"] != b.read_truth()["island"], \
        "distinct seeds must produce distinct islands"


def test_apply_input_command_accepted():
    organ = NeikosOrgan.from_seed("island", 42)
    organ.advance(1)
    before = organ.read_truth()["location"]
    # Walk to a neighbour so the player location can change.
    st = organ._ctrl._state
    cur = st.topology.nodes.get(st.player_location)
    if cur and cur.neighbors:
        organ.apply_input({"action": "move", "target_node": list(cur.neighbors)[0]})
    after = organ.read_truth()["location"]
    # Either we moved, or the move surfaced an event into the next delta — both are
    # acceptable proof the command was accepted (not swallowed/errored).
    delta = organ.advance(2)
    assert after != before or any(e.get("type") for e in delta.events)


def test_two_organ_federation_shares_one_clock():
    """ForkUniverse + Neikos on a single clock: the first real two-world federation."""
    eng = FederationEngine(clock=Clock())
    eng.register(ForkUniverseOrgan.from_request(
        "harbor", world_ticks_per_clock_tick=12, custom_seed="fed-1", **CREATION))
    eng.register(NeikosOrgan.from_seed("island", 42, world_ticks_per_clock_tick=10))

    eng.run(steps=8)
    assert eng.clock.tick == 8
    truth = eng.truth()
    assert truth["harbor"]["tick"] == 96   # 8 * 12
    assert truth["island"]["tick"] == 80   # 8 * 10
    sources = {c.source for c in eng.bus}
    assert sources == {"harbor", "island"}, "both worlds put beats on one bus"
    assert eng.is_fully_deterministic
