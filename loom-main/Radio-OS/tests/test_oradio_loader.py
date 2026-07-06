"""The alignment spine: an `.oradio` is a DECLARATION the engine decodes — and the lens is
a declared, composable layer over any world (not organ-bound).

No per-domain Python: every case here is a descriptor dict / file, decoded by load_oradio.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import OradioDescriptor, load_oradio, load_oradio_file  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def test_descriptor_parses_the_owner_approved_shape():
    d = OradioDescriptor.from_dict({
        "oradio": "demo",
        "world": {"organ": "neikos", "seed": 7},
        "telemetry": [{"source": "moco", "binds": "intent -> action"}],
        "lens": "identity",
        "surfaces": ["voice"],
        "club": ["llm", "voices"],
    })
    assert d.name == "demo"
    assert d.worlds[0].organ == "neikos" and d.worlds[0].params["seed"] == 7
    assert d.telemetry[0].source == "moco" and d.telemetry[0].binds == "intent -> action"
    assert d.surfaces == ["voice"] and "llm" in d.club


def test_a_world_is_just_a_declaration():
    eng = load_oradio({"oradio": "isle", "world": {"organ": "neikos", "name": "isle", "seed": 42}})
    eng.run(steps=5)
    assert eng.truth()["isle"]["tick"] == 50
    assert any(c.source == "isle" for c in eng.bus)


def test_lens_is_declared_and_composable_same_world_different_read():
    base = {"oradio": "k", "world": {"organ": "neikos", "name": "k", "seed": 42}}
    raw = load_oradio({**base, "lens": "identity"}); raw.run(steps=5)
    # neikos ambient tick_updates are low priority (0.2); a declared floor drops them.
    filtered = load_oradio({**base, "lens": {"ops": [{"op": "floor_priority", "min": 0.5}]}})
    filtered.run(steps=5)
    assert len(raw.bus) > len(filtered.bus), "the declared lens changed interpretation, not the organ"
    assert all(c.priority >= 0.5 for c in filtered.bus)


def test_multi_world_oradio():
    eng = load_oradio({
        "oradio": "twin",
        "worlds": [
            {"organ": "neikos", "name": "isle_a", "seed": 1},
            {"organ": "neikos", "name": "isle_b", "seed": 2},
        ],
    })
    eng.run(steps=4)
    assert {"isle_a", "isle_b"} <= set(eng.truth().keys())


def test_spatial_array_telemetry_emerges_with_no_hardware():
    # "can the Loom + club empower your .oradio to listen to your spatial array" — yes, declared.
    eng = load_oradio({
        "oradio": "home",
        "world": {"organ": "neikos", "name": "home", "seed": 42},
        "telemetry": [{"source": "simulated_spatial_array", "name": "array",
                       "nodes": ["front_door", "living_room", "kitchen"]}],
    })
    eng.run(steps=3)
    presence = [c for c in eng.bus if c.type == "presence"]
    assert len(presence) == 3, "the array senses presence each tick"
    assert [c.title for c in presence] == ["front_door", "living_room", "kitchen"]
    assert any(c.source == "home" for c in eng.bus), "the world runs alongside the array"


@pytest.mark.slow
def test_ftb_flood_tamed_by_declared_lens_not_a_patch():
    # The FTB pbp flood (finding B) handled by a DECLARED lens — ftb_game.py untouched.
    eng = load_oradio({
        "oradio": "ladder",
        "world": {"organ": "ftb", "name": "grid", "seed": 42, "ratio": 7},
        "lens": {"ops": [{"op": "drop_types", "types": ["audio", "outcome"]}, {"op": "cap", "n": 20}]},
    })
    eng.run(steps=2)
    assert len(eng.bus) <= 40, "cap(20)/tick bounds the bus despite FTB's flood"
    assert not any(c.type in ("audio", "outcome") for c in eng.bus)


def test_example_artifacts_load_and_run():
    yaml = pytest.importorskip("yaml")  # examples are YAML
    eng = load_oradio_file(os.path.join(EXAMPLES, "home-region.oradio"))
    eng.run(steps=3)
    assert any(c.type == "presence" for c in eng.bus)
