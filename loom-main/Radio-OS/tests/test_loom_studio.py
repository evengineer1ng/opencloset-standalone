import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loom_studio import LoomFormState, build_loom_descriptor


def _state(**overrides):
    base = LoomFormState(
        name="my loom",
        world_kind="forkuniverse",
        seed=42,
        premise="A strange little world.",
        enabled_signals=["simulated_spatial_array"],
        spatial_nodes=["front_door", "living_room"],
        loop_mode="builtin",
        builtin_theme="ribbon",
        loop_path="",
        voice_provider="kokoro",
        voice_assignments={"host": "af_sarah", "analyst": "am_adam"},
        transient_enabled=True,
        transient_title="Glimpse",
        transient_min_priority=0.6,
        transient_body_template="{title}\n\n{body}",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_build_loom_descriptor_creates_voice_binding_for_spatial_signal():
    descriptor = build_loom_descriptor(_state())
    assert descriptor["oradio"] == "my_loom"
    assert descriptor["theme"] == "ribbon"
    assert {"kind": "voice", "name": "loom_voice"} in descriptor["effectors"]
    assert any(binding["transform"] == "presence_to_speech" for binding in descriptor["bindings"])
    assert "voice" in descriptor["surfaces"]
    assert "transient" in descriptor["surfaces"]


def test_build_loom_descriptor_allows_telemetry_only_worlds():
    descriptor = build_loom_descriptor(_state(world_kind="none", enabled_signals=["pc_telemetry"], voice_provider="none"))
    assert "world" not in descriptor
    assert descriptor["telemetry"][0]["source"] == "pc_telemetry"


def test_build_loom_descriptor_requires_world_or_signal():
    try:
        build_loom_descriptor(_state(world_kind="none", enabled_signals=[]))
    except ValueError as exc:
        assert "at least one world or one signal" in str(exc)
    else:
        raise AssertionError("expected a ValueError when no world and no signal are declared")
