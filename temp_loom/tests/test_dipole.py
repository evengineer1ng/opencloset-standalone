"""Dipole meter tests: declared poles, deterministic readings, and pole flips."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import (  # noqa: E402
    Clock,
    Determinism,
    DipoleDecl,
    DipoleMeter,
    FederationEngine,
    OradioDescriptor,
    OrganIdentity,
    TickDelta,
    load_oradio,
    normalize_event,
)


class SwingOrgan:
    """A tiny deterministic organ that alternates between the up and down poles."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._tick = 0

    def identity(self):
        return OrganIdentity(self._name, Determinism.DETERMINISTIC, 1)

    def advance(self, to_tick: int):
        events = []
        for tick in range(self._tick + 1, to_tick + 1):
            if tick % 2:
                events.append({"title": "sky push", "type": "sky", "priority": 1.0, "ts": float(tick)})
            else:
                events.append({"title": "ground pull", "type": "ground", "priority": 1.0, "ts": float(tick)})
        previous = self._tick
        self._tick = to_tick
        return TickDelta(from_tick=previous, to_tick=to_tick, events=events)

    def observe(self, delta):
        return [normalize_event(self._name, delta.to_tick, i, event) for i, event in enumerate(delta.events)]

    def read_truth(self):
        return {"tick": self._tick}

    def apply_input(self, event):
        return None


def test_descriptor_parses_declared_dipole():
    descriptor = OradioDescriptor.from_dict({
        "oradio": "metered",
        "world": {"organ": "neikos", "seed": 42},
        "dipole": {
            "name": "compass",
            "up": {"types": ["presence"], "sources": ["array"]},
            "down": {"types": ["spoken"], "tags": ["voice"]},
        },
    })
    assert descriptor.dipole is not None
    assert descriptor.dipole.name == "compass"
    assert descriptor.dipole.up.types == ("presence",)
    assert descriptor.dipole.down.tags == ("voice",)


def test_dipole_meter_emits_deterministic_pole_flips():
    decl = DipoleDecl.from_dict({
        "name": "metronome",
        "up": {"types": ["sky"]},
        "down": {"types": ["ground"]},
    })
    assert decl is not None

    def build():
        engine = FederationEngine(clock=Clock(), dipole=DipoleMeter(decl))
        engine.register(SwingOrgan("swing"))
        return engine

    a = build()
    b = build()
    out_a = [cand.as_dict() for cand in a.run(steps=4)]
    out_b = [cand.as_dict() for cand in b.run(steps=4)]
    assert out_a == out_b
    assert a.dipole_history == b.dipole_history

    flips = [cand for cand in a.bus if cand.type == "pole_flip"]
    assert len(flips) == 3
    assert [reading["direction"] for reading in a.dipole_history] == [1, -1, 1, -1]
    assert [reading["flipped"] for reading in a.dipole_history] == [False, True, True, True]


def test_real_oradio_dipole_replays_identically():
    spec = {
        "oradio": "home-region-metered",
        "world": {"organ": "neikos", "seed": 42},
        "telemetry": [{
            "source": "simulated_spatial_array",
            "name": "array",
            "nodes": ["front_door", "living_room", "kitchen"],
        }],
        "effectors": [{"kind": "voice", "name": "house_voice"}],
        "bindings": [{"from": "array", "to": "house_voice", "transform": "presence_to_speech"}],
        "dipole": {
            "name": "compass",
            "up": {"types": ["presence", "tier_escalated"]},
            "down": {"types": ["spoken", "tick_update"]},
        },
    }
    a = load_oradio(spec)
    b = load_oradio(spec)
    a.run(steps=6)
    b.run(steps=6)

    assert a.dipole_history == b.dipole_history
    flips_a = [cand.as_dict() for cand in a.bus if cand.type == "pole_flip"]
    flips_b = [cand.as_dict() for cand in b.bus if cand.type == "pole_flip"]
    assert flips_a == flips_b
