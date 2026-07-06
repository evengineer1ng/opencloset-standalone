"""The .loom building block: two fields (universe + connections) generate one runnable .oradio."""
from loom.dotloom import Connection, Loom, loom_to_oradio, load_loom_dict, universe_seed


def test_universe_seed_is_deterministic_and_distinct():
    assert universe_seed("hear animal thoughts") == universe_seed("hear animal thoughts")
    assert universe_seed("a quiet house") != universe_seed("a loud house")


def test_role_is_inferred_from_the_plugin_not_declared():
    assert Connection(plugin="neikos").role() == "world"          # a registered organ
    assert Connection(plugin="simulated_spatial_array").role() == "source"
    assert Connection(plugin="anything_unknown").role() == "source"   # default: just emit on the wire
    assert Connection(plugin="x", as_role="effector").role() == "effector"  # explicit override


def test_world_connection_inherits_the_universe_seed():
    o = loom_to_oradio(Loom.from_dict({"universe": "X", "connections": [{"plugin": "neikos"}]}))
    assert o["worlds"][0]["organ"] == "neikos"
    assert o["worlds"][0]["seed"] == universe_seed("X")          # the universe IS the seed
    assert o["intent"] == "X"                                    # carried for renderers


def test_one_loom_generates_one_oradio_that_runs():
    loom = {
        "universe": "a quiet house that notices me",
        "connections": [
            {"plugin": "simulated_spatial_array", "name": "array", "nodes": ["front_door", "kitchen"]},
        ],
    }
    oradio = load_loom_dict(loom)                                # 1:1 generation
    assert oradio["telemetry"][0]["source"] == "simulated_spatial_array"
    assert oradio["oradio"] == "a-quiet-house-that-notices"      # slug from the universe

    from oradio_engine.loader import open_oradio
    result = open_oradio(oradio)                                 # the existing engine runs it as-is
    assert result.ok, result.report.summary()
    result.engine.run(steps=6)
    assert len(result.engine.bus) > 0                            # the house is noticing


def test_a_github_connection_carries_its_coordinate_through():
    o = load_loom_dict({
        "universe": "hear animal thoughts",
        "connections": [{"plugin": "animalcries", "source": "github:evan/animalcries", "sha256": "abc"}],
    })
    node = o["telemetry"][0]
    assert node["source"] == "animalcries"
    assert node["plugin"] == "github:evan/animalcries" and node["sha256"] == "abc"
