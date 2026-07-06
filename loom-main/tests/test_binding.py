"""The binding layer — telemetry drives worlds, world actions drive effectors.

Two flagships, ONE machinery:
  - spatial house: presence -> the house speaks (inbound telemetry -> outbound effector)
  - Pokémon via video capture: capture (eyes) -> navigator (brain) -> gamepad (hands)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import load_oradio, load_oradio_file  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "spec", "examples")


def test_spatial_house_speaks_on_presence():
    eng = load_oradio({
        "oradio": "home",
        "world": {"organ": "neikos", "name": "home", "seed": 42},
        "telemetry": [{"source": "simulated_spatial_array", "name": "array",
                       "nodes": ["front_door", "kitchen", "bedroom"]}],
        "effectors": [{"kind": "voice", "name": "house_voice"}],
        "bindings": [{"from": "array", "to": "house_voice", "transform": "presence_to_speech"}],
    })
    eng.run(steps=4)
    voice = eng.organs["house_voice"]._inner  # unwrap LensedOrgan
    spoken = [r["text"] for r in voice.received]
    assert any("front_door" in s for s in spoken)
    assert any("kitchen" in s for s in spoken)
    assert len(voice.received) >= 3, "the house reacts to presence at each node"


def test_pokemon_capture_navigator_gamepad_loop():
    eng = load_oradio({
        "oradio": "scarlet",
        "world": {"organ": "navigator", "name": "nav"},
        "telemetry": [{"source": "video_capture_sim", "name": "cam", "frames": [
            {"scene": "starter_house"}, {"scene": "hallway"},
            {"scene": "front_door"}, {"scene": "outside"},
        ]}],
        "effectors": [{"kind": "gamepad", "name": "pad"}],
        "bindings": [
            {"from": "cam", "to": "nav", "transform": "frame_to_observation"},
            {"from": "nav", "to": "pad", "transform": "action_to_button"},
        ],
    })
    eng.run(steps=8)
    pad = eng.organs["pad"]._inner
    buttons = [r["button"] for r in pad.received]
    # eyes saw starter_house->'up', hallway->'up', front_door->'A', outside->'right'
    assert buttons, "the navigator drove the gamepad through the captured scenes"
    assert "up" in buttons and "A" in buttons, f"expected navigation presses, got {buttons}"


def test_navigator_is_deterministic_brain():
    spec = {
        "oradio": "scarlet",
        "world": {"organ": "navigator", "name": "nav"},
        "telemetry": [{"source": "video_capture_sim", "name": "cam",
                       "frames": [{"scene": "starter_house"}, {"scene": "front_door"}]}],
        "effectors": [{"kind": "gamepad", "name": "pad"}],
        "bindings": [
            {"from": "cam", "to": "nav", "transform": "frame_to_observation"},
            {"from": "nav", "to": "pad", "transform": "action_to_button"},
        ],
    }
    a = load_oradio(spec); a.run(steps=6)
    b = load_oradio(spec); b.run(steps=6)
    pa = [r["button"] for r in a.organs["pad"]._inner.received]
    pb = [r["button"] for r in b.organs["pad"]._inner.received]
    assert pa == pb, "same perceived scenes -> same actions (the brain is deterministic)"


def test_example_pokemon_artifact_runs():
    pytest.importorskip("yaml")  # examples are YAML
    eng = load_oradio_file(os.path.join(EXAMPLES, "pokemon-scarlet.oradio"))
    eng.run(steps=8)
    pad = eng.organs["pad"]._inner
    assert pad.received, "the example .oradio drove the gamepad"


def test_example_home_artifact_speaks():
    pytest.importorskip("yaml")  # examples are YAML
    eng = load_oradio_file(os.path.join(EXAMPLES, "home-region.oradio"))
    eng.run(steps=4)
    assert eng.organs["house_voice"]._inner.received, "the house spoke on presence"
