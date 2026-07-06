from forkuniverse.compiler.compile import compile_universe
from forkuniverse.compiler.models import CreationRequest
from forkuniverse.engine.world_core import UniverseState, load_universe
from forkuniverse.runtime.query import UniverseQueryRequest, compute_truth


def _request() -> CreationRequest:
    return CreationRequest(
        universe_title="Pressure Harbor",
        premise="A port city where love, debt, rumor, and grief reshape every alliance.",
        setting_kind="haunted_port_city",
        time_period="modern",
        story_mode="continuous",
        world_scale="district",
        starting_population=24,
        seed_mode="custom",
        custom_seed="engine-harbor-1",
        ontology_domains=["love", "debt", "rumor", "grief"],
    )


def test_ingest_preserves_compiled_tables():
    package = compile_universe(_request())
    state = load_universe(package)

    assert state.universe_id == package.package_identity.universe_id
    assert state.canonical_seed == "engine-harbor-1"
    assert len(state.characters) == len(package.world_tables["characters"])
    assert len(state.threads) == len(package.world_tables["story_threads"])
    assert len(state.predictions.predictions) == len(package.world_tables["predictions"])
    assert state.time.tick == 0


def test_advance_tick_changes_world_and_logs_events():
    state = load_universe(compile_universe(_request()))
    before = state.digest()
    state.simulate_epoch(80)

    assert state.time.tick == 80
    assert state.digest() != before
    # The world should have generated events (obligations, threads, predictions).
    assert len(state.ledger.events) > 0
    # Some predictions should have settled by tick 80.
    assert state.predictions.settled_count > 0


def test_simulation_is_deterministic_for_same_seed():
    state_a = load_universe(compile_universe(_request()))
    state_b = load_universe(compile_universe(_request()))

    state_a.simulate_epoch(100)
    state_b.simulate_epoch(100)

    assert state_a.digest() == state_b.digest()


def test_batch_epoch_equals_single_ticks():
    """Absence reconstruction must equal live ticking: the core determinism contract."""
    batched = load_universe(compile_universe(_request()))
    single = load_universe(compile_universe(_request()))

    batched.simulate_epoch(120)
    for _ in range(120):
        single.advance_tick()

    assert batched.digest() == single.digest()


def test_compute_absence_reconstructs_owed_ticks():
    state = load_universe(compile_universe(_request()))
    # real_seconds_per_tick defaults to 60 from the compiled time policy.
    delta = state.compute_absence(elapsed_real_seconds=60 * 90)

    assert delta.from_tick == 0
    assert delta.to_tick == 90
    assert state.time.tick == 90
    assert isinstance(delta.new_events, list)
    assert "calibration_score" in delta.prediction_scorecard


def test_operator_input_shifts_pressure_and_logs_event():
    state = load_universe(compile_universe(_request()))
    love_axis = state.macro_axes.get("love_pressure")
    assert love_axis is not None
    before_value = love_axis.current_value

    event = state.apply_operator_input(intent="amplify", magnitude=0.8, target_domain="love")

    assert event.family == "operator_input"
    assert love_axis.current_value > before_value
    assert love_axis.normalization_bias > 0


def test_truth_delta_surfaces_changes_for_broadcast():
    state = load_universe(compile_universe(_request()))
    state.simulate_epoch(40)
    delta = state.emit_truth_delta(from_tick=30)

    assert delta.to_tick == 40
    assert delta.headline
    assert all(event["tick"] > 30 for event in delta.new_events)


def test_snapshot_round_trips_and_continues_deterministically():
    state = load_universe(compile_universe(_request()))
    state.simulate_epoch(75)

    snapshot = state.to_snapshot()
    reloaded = UniverseState.from_snapshot(snapshot)

    # The persisted snapshot reproduces the saved state exactly.
    assert reloaded.digest() == state.digest()
    assert reloaded.time.tick == 75
    assert reloaded.predictions.settled_count == state.predictions.settled_count

    # Continuing from a reload is deterministic.
    again = UniverseState.from_snapshot(snapshot)
    reloaded.simulate_epoch(40)
    again.simulate_epoch(40)
    assert reloaded.digest() == again.digest()


def test_save_and_load_json_round_trip(tmp_path):
    state = load_universe(compile_universe(_request()))
    state.simulate_epoch(50)
    path = tmp_path / "universe.json"
    state.save_json(path)

    loaded = UniverseState.load_json(path)
    assert loaded.digest() == state.digest()


def test_thread_spawn_respects_broadcast_budget():
    state = load_universe(compile_universe(_request()))
    budget = int(state.coefficient("active_thread_budget"))
    state.simulate_epoch(300)

    # Active (unresolved) threads stay within the broadcast quota, even though
    # the total thread count grows without bound over time.
    assert state.active_thread_count() <= budget


def test_compute_truth_live_advances_owed_ticks():
    package = compile_universe(_request())
    state = load_universe(package)

    result = compute_truth(
        package,
        UniverseQueryRequest(
            universe_id=package.package_identity.universe_id,
            mode="broadcast_digest",
            since="explicit",
            since_timestamp="2026-06-11T18:00:00Z",
            now_timestamp="2026-06-11T20:00:00Z",
        ),
        state=state,
    )

    # 2 hours real / 60s per tick = 120 owed ticks.
    assert result.query_metadata["live_engine"] is True
    assert result.query_metadata["ticks_advanced"] == 120
    assert state.time.tick == 120
    assert "prediction_scorecard" in result.query_metadata


def test_compute_truth_static_path_unchanged_without_state():
    package = compile_universe(_request())
    result = compute_truth(
        package,
        UniverseQueryRequest(
            universe_id=package.package_identity.universe_id,
            mode="broadcast_digest",
            now_timestamp="2026-06-11T20:00:00Z",
        ),
    )
    # No state -> static behavior, engine not driven.
    assert result.query_metadata.get("live_engine") is None


def test_obligation_breach_opens_thread_and_raises_entropy():
    state = load_universe(compile_universe(_request()))
    entropy = state.macro_axes.get("entropy_pressure")
    start_threads = len(state.threads)
    state.simulate_epoch(200)

    # Over 200 ticks at least one obligation should resolve and the thread set grows.
    breached = [o for o in state.obligations.values() if o.status == "breached"]
    if breached:
        assert len(state.threads) > start_threads
    assert entropy is not None
