import provisioning
from loom.loom_studio import LoomFormState, build_loom_descriptor, signal_catalog


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


class _TempConfig:
    def __init__(self, tmp_path):
        self._cfg = tmp_path / "config.json"
        self._orig = provisioning.global_config_path

    def __enter__(self):
        provisioning.global_config_path = lambda: self._cfg  # type: ignore[assignment]
        return self._cfg.parent

    def __exit__(self, *exc):
        provisioning.global_config_path = self._orig  # type: ignore[assignment]


def test_build_loom_descriptor_creates_voice_binding_for_spatial_signal():
    descriptor = build_loom_descriptor(_state())
    assert descriptor["oradio"] == "my_loom"
    assert descriptor["theme"] == "ribbon"
    assert descriptor["visual"]["base"]["mode"] == "builtin"
    assert descriptor["visual"]["tape"]["accumulation"] == "causal"
    assert len(descriptor["visual"]["tape"]["families"]) >= 12
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


def test_forkuniverse_creation_includes_required_time_period():
    """The bug: CreationRequest requires time_period; the studio never sent it, so the
    headline path crashed on load. Guard the field at the descriptor level."""
    descriptor = build_loom_descriptor(_state(enabled_signals=[]))
    assert descriptor["world"]["creation"]["time_period"]


def test_forkuniverse_optional_idea_knobs_are_wired():
    descriptor = build_loom_descriptor(
        _state(
            enabled_signals=[],
            genres=["horror"],
            tones=["dread", "tense"],
            location_flavor="a fog-bound cul-de-sac",
            starting_context="The dummy was found on the porch at dawn.",
        )
    )
    creation = descriptor["world"]["creation"]
    assert creation["genre_mix"] == {"horror": 1.0}
    assert creation["tone_mix"] == {"dread": 1.0, "tense": 1.0}
    assert creation["location_flavor"] == "a fog-bound cul-de-sac"
    assert creation["starting_context"].startswith("The dummy")


def test_signal_catalog_surfaces_runtime_inputs(tmp_path):
    with _TempConfig(tmp_path):
        catalog = {item["key"]: item for item in signal_catalog()}
        assert "simulated_spatial_array" in catalog
        assert catalog["pc_telemetry"]["sensitive"] is True
        assert "will ask on open" in catalog["pc_telemetry"]["status"]


def test_signal_catalog_surfaces_remembered_targets(tmp_path):
    with _TempConfig(tmp_path) as root:
        target = root / "moco.json"
        target.write_text("{}", encoding="utf-8")
        provisioning.save_antenna_target("moco", target)
        catalog = {item["key"]: item for item in signal_catalog()}
        assert "remembered target" in catalog["moco"]["status"]
        assert catalog["moco"]["remembered_target"] == str(target)


def test_build_loom_descriptor_accepts_custom_signal_params():
    descriptor = build_loom_descriptor(
        _state(
            world_kind="none",
            enabled_signals=[],
            voice_provider="none",
            custom_signal_kind="moco",
            custom_signal_name="body_motion",
            custom_signal_params_text='{"telemetry_path": "logs/moco.json"}',
        )
    )
    assert descriptor["telemetry"][0]["source"] == "moco"
    assert descriptor["telemetry"][0]["name"] == "body_motion"
    assert descriptor["telemetry"][0]["telemetry_path"] == "logs/moco.json"


def test_build_loom_descriptor_uses_remembered_signal_target(tmp_path):
    with _TempConfig(tmp_path) as root:
        db = root / "league.sqlite"
        db.write_text("stub", encoding="utf-8")
        provisioning.save_antenna_target("atl_league", db)
        descriptor = build_loom_descriptor(
            _state(
                world_kind="none",
                enabled_signals=["atl_league"],
                voice_provider="none",
                transient_enabled=False,
            )
        )
        assert descriptor["telemetry"][0]["db_path"] == str(db)


def test_build_loom_descriptor_can_inline_uploaded_transient_template(tmp_path):
    template = tmp_path / "surface.txt"
    template.write_text("CASE: {title}\n\n{body}", encoding="utf-8")
    descriptor = build_loom_descriptor(
        _state(
            transient_template_path=str(template),
            transient_body_template="",
        )
    )
    transient = descriptor["transient_surfaces"][0]
    assert transient["body_template"].startswith("CASE:")


def test_build_loom_descriptor_authors_custom_media_visual_base():
    descriptor = build_loom_descriptor(
        _state(
            loop_mode="custom",
            loop_path="C:/loops/organism.mp4",
        )
    )
    assert descriptor["theme"] == "C:/loops/organism.mp4"
    assert descriptor["visual"]["base"]["mode"] == "media"
    assert descriptor["visual"]["base"]["path"] == "C:/loops/organism.mp4"


def _forkuniverse_state(name, premise, genres):
    return _state(
        name=name,
        premise=premise,
        enabled_signals=[],
        voice_provider="none",
        transient_enabled=False,
        genres=genres,
    )


def test_headline_descriptor_loads_and_ticks_on_the_real_engine():
    """The regression that would have caught the crash: don't just check the dict shape —
    load the forkuniverse descriptor through the federation and run it."""
    from oradio_engine import load_oradio

    descriptor = build_loom_descriptor(
        _forkuniverse_state("Fear Street", "a goosebumps horror neighborhood where a cursed dummy stalks kids", ["horror"])
    )
    engine = load_oradio(descriptor)
    for _ in range(25):
        engine.tick()
    assert engine.bus, "a forkuniverse world should surface events over 25 ticks"


def test_premise_perturbs_the_world_at_a_fixed_seed():
    """Same seed (42), different premise + genre -> the bus must diverge. If only the seed
    mattered, the idea would be cosmetic."""
    from oradio_engine import load_oradio

    def run(state):
        engine = load_oradio(build_loom_descriptor(state))
        for _ in range(25):
            engine.tick()
        return [c.title for c in engine.bus]

    horror = run(_forkuniverse_state("Fear Street", "a goosebumps horror neighborhood where a cursed dummy stalks kids", ["horror"]))
    boardroom = run(_forkuniverse_state("Boardroom", "a tense corporate boardroom tracking quarterly earnings and hostile takeovers", ["drama"]))
    assert horror != boardroom, "the premise must perturb the world, not just the seed"
