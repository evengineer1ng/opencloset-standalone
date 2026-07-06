from forkuniverse.compiler.compile import compile_universe
from forkuniverse.compiler.models import CreationRequest
from forkuniverse.compiler.normalize import build_universe_brief
from forkuniverse.ontology.registry import load_concept_registry
from forkuniverse.runtime.query import UniverseQueryRequest, compute_truth


def test_build_universe_brief_is_deterministic():
    request = CreationRequest(
        universe_title="Frontier Mirror",
        premise="A Wild West theme park populated by AI hosts and wealthy visitors starts destabilizing when memory glitches accumulate.",
        setting_kind="theme_park_western_scifi",
        time_period="near_future",
        story_mode="continuous",
        world_scale="district",
        starting_population=48,
        seed_mode="preset",
        preset_id="westworld_frontier",
        custom_seed="19383ybfgobblegork",
    )

    brief_a = build_universe_brief(request)
    brief_b = build_universe_brief(request)

    assert brief_a.canonical_seed == brief_b.canonical_seed
    assert brief_a.seed_hash == brief_b.seed_hash


def test_compile_universe_emits_expected_core_tables():
    request = CreationRequest(
        universe_title="Frontier Mirror",
        premise="A Wild West theme park populated by AI hosts and wealthy visitors starts destabilizing when memory glitches accumulate.",
        setting_kind="theme_park_western_scifi",
        time_period="near_future",
        story_mode="continuous",
        world_scale="district",
        starting_population=24,
        seed_mode="preset",
        preset_id="westworld_frontier",
        custom_seed="19383ybfgobblegork",
    )

    package = compile_universe(request)

    assert package.package_identity.canonical_seed == "19383ybfgobblegork"
    assert len(package.world_tables["characters"]) == 24
    assert "story_threads" in package.world_tables
    assert "predictions" in package.world_tables
    assert "coefficients" in package.world_tables
    assert package.world_tables["locations"]


def test_compute_truth_returns_broadcast_digest_shape():
    request = CreationRequest(
        universe_title="Frontier Mirror",
        premise="A Wild West theme park populated by AI hosts and wealthy visitors starts destabilizing when memory glitches accumulate.",
        setting_kind="theme_park_western_scifi",
        time_period="near_future",
        story_mode="continuous",
        world_scale="district",
        starting_population=24,
        seed_mode="preset",
        preset_id="westworld_frontier",
        custom_seed="19383ybfgobblegork",
        time_policy={
            "execution_model": "on_demand",
            "preset": "adaptive_medium",
            "world_seconds_per_real_second": 300.0,
        },
    )

    package = compile_universe(request)
    result = compute_truth(
        package,
        UniverseQueryRequest(
            universe_id=package.package_identity.universe_id,
            mode="broadcast_digest",
            now_timestamp="2026-06-11T20:00:00Z",
        ),
    )

    assert result.universe_id == package.package_identity.universe_id
    assert isinstance(result.events, list)
    assert isinstance(result.threads, list)
    assert result.query_metadata["execution_model"] == "on_demand"
    assert result.query_metadata["query_driven"] is True


def test_concept_registry_loads():
    registry = load_concept_registry()

    assert registry.registry_id == "forkuniverse_core_concepts"
    assert any(concept.concept_id == "love" for concept in registry.concepts)
    assert any(concept.concept_id == "debt" for concept in registry.concepts)


def test_ontology_domains_flow_into_brief_and_package():
    request = CreationRequest(
        universe_title="Haunted Coast",
        premise="A haunted coastal town where rumor, grief, debt, and weather shape everyday life.",
        setting_kind="haunted_coastal_town",
        time_period="modern",
        story_mode="continuous",
        world_scale="site",
        starting_population=12,
        seed_mode="custom",
        custom_seed="haunted-coast-1",
        ontology_domains=["rumor", "grief", "debt", "weather"],
    )

    brief = build_universe_brief(request)
    package = compile_universe(request)

    assert brief.selected_ontology_domains == ["rumor", "grief", "debt", "weather"]
    assert package.universe_brief["selected_ontology_domains"] == ["rumor", "grief", "debt", "weather"]


def test_selected_concepts_shape_threads_predictions_and_coefficients():
    request = CreationRequest(
        universe_title="Pressure Harbor",
        premise="A port city where love, debt, rumor, and grief reshape every alliance.",
        setting_kind="haunted_port_city",
        time_period="modern",
        story_mode="continuous",
        world_scale="site",
        starting_population=10,
        seed_mode="custom",
        custom_seed="pressure-harbor-7",
        ontology_domains=["love", "debt", "rumor", "grief"],
    )

    package = compile_universe(request)
    threads = package.world_tables["story_threads"]
    predictions = package.world_tables["predictions"]
    coefficients = package.world_tables["coefficients"]
    macro_axes = {row["axis_id"]: row for row in package.world_tables["macro_state"]}

    assert any(thread["domain"] == "love" for thread in threads)
    assert any(thread["domain"] == "debt" for thread in threads)
    assert any("confession" in thread["title"].lower() for thread in threads)
    assert any("repayment deadline" in thread["title"].lower() for thread in threads)

    claim_types = {row["claim_type"] for row in predictions}
    assert "romantic_union" in claim_types
    assert "default_risk" in claim_types
    assert "reputation_collapse" in claim_types
    assert "withdrawal_risk" in claim_types

    coefficient_names = {row["name"] for row in coefficients}
    assert "love_prediction_spawn_bias" in coefficient_names
    assert "debt_stress_bias" in coefficient_names
    assert "rumor_surface_bias" in coefficient_names
    assert "grief_memory_bias" in coefficient_names

    assert package.coefficient_profile["romantic"] > 0.4
    assert package.coefficient_profile["mystery"] > 0.2
    assert "love_pressure" in macro_axes
    assert "debt_pressure" in macro_axes


def test_selected_concepts_shape_relationships_and_obligations():
    request = CreationRequest(
        universe_title="Tender Credit",
        premise="A cramped city where love and debt are tangled in every deal and every promise.",
        setting_kind="dense_city",
        time_period="modern",
        story_mode="continuous",
        world_scale="site",
        starting_population=12,
        seed_mode="custom",
        custom_seed="tender-credit-3",
        ontology_domains=["love", "debt"],
    )

    package = compile_universe(request)
    relationships = package.world_tables["relationships"]
    obligations = package.world_tables["obligations"]

    assert relationships
    assert obligations

    avg_affection = sum(row["affection"] for row in relationships) / len(relationships)
    avg_dependency = sum(row["dependency"] for row in relationships) / len(relationships)
    avg_loyalty = sum(row["loyalty"] for row in relationships) / len(relationships)
    avg_fear = sum(row["fear"] for row in relationships) / len(relationships)

    assert avg_affection > 0.45
    assert avg_dependency > 0.45
    assert avg_loyalty > 0.45
    assert avg_fear > 0.3
    assert all("love" in row["concept_tags"] for row in relationships)
    assert all("debt" in row["concept_tags"] for row in relationships)

    obligation_types = {row["obligation_type"] for row in obligations}
    assert "debt_note" in obligation_types
    assert all(row["stakes"] >= 0.75 for row in obligations if row["obligation_type"] == "debt_note")
    assert all("debt" in row["pressure_tags"] for row in obligations if row["obligation_type"] == "debt_note")
    assert all(row["due_tick"] <= 120 + index * 10 for index, row in enumerate(obligations))


def test_selected_concepts_shape_character_seed_distributions():
    grief_request = CreationRequest(
        universe_title="Ash Harbor",
        premise="A harbor city still living inside the shadow of a recent mass loss.",
        setting_kind="harbor_city",
        time_period="modern",
        story_mode="continuous",
        world_scale="site",
        starting_population=16,
        seed_mode="custom",
        custom_seed="shared-seed-77",
        ontology_domains=["grief"],
    )
    ambition_request = CreationRequest(
        universe_title="Glass Ladder",
        premise="A harbor city obsessed with climbing, deals, and advancement.",
        setting_kind="harbor_city",
        time_period="modern",
        story_mode="continuous",
        world_scale="site",
        starting_population=16,
        seed_mode="custom",
        custom_seed="shared-seed-77",
        ontology_domains=["ambition"],
    )

    grief_package = compile_universe(grief_request)
    ambition_package = compile_universe(ambition_request)

    grief_chars = grief_package.world_tables["characters"]
    ambition_chars = ambition_package.world_tables["characters"]

    grief_loss = sum(row["fear_vector"]["loss"] for row in grief_chars) / len(grief_chars)
    grief_stress = sum(row["stress_profile"]["baseline"] for row in grief_chars) / len(grief_chars)
    grief_belonging = sum(row["desire_vector"]["belonging"] for row in grief_chars) / len(grief_chars)
    grief_mobility = sum(row["resource_state"]["mobility"] for row in grief_chars) / len(grief_chars)

    ambition_status = sum(row["desire_vector"]["status"] for row in ambition_chars) / len(ambition_chars)
    ambition_humiliation = sum(row["fear_vector"]["humiliation"] for row in ambition_chars) / len(ambition_chars)
    ambition_cash = sum(row["resource_state"]["cash"] for row in ambition_chars) / len(ambition_chars)
    ambition_mobility = sum(row["resource_state"]["mobility"] for row in ambition_chars) / len(ambition_chars)

    assert grief_loss > sum(row["fear_vector"]["loss"] for row in ambition_chars) / len(ambition_chars)
    assert grief_stress > sum(row["stress_profile"]["baseline"] for row in ambition_chars) / len(ambition_chars)
    assert grief_belonging > sum(row["desire_vector"]["belonging"] for row in ambition_chars) / len(ambition_chars)
    assert grief_mobility < ambition_mobility

    assert ambition_status > sum(row["desire_vector"]["status"] for row in grief_chars) / len(grief_chars)
    assert ambition_humiliation > sum(row["fear_vector"]["humiliation"] for row in grief_chars) / len(grief_chars)
    assert ambition_cash > sum(row["resource_state"]["cash"] for row in grief_chars) / len(grief_chars)

    assert all("mourning" in row["ledger_seed"]["myth_tags"] for row in grief_chars)
    assert all("carry_the_dead_forward" in row["ledger_seed"]["starting_promises"] for row in grief_chars)
    assert all("striver" in row["ledger_seed"]["myth_tags"] for row in ambition_chars)
    assert all("rise_at_any_cost" in row["ledger_seed"]["starting_promises"] for row in ambition_chars)
