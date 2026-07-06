from __future__ import annotations

import hashlib
import json
import random
from typing import Any, Dict, List, Optional

from forkuniverse.ontology.models import ConceptRecord
from forkuniverse.ontology.registry import load_concept_registry, select_concepts

from .models import CompiledWorldPackage, CompilerFill, CreationRequest, PackageIdentity
from .normalize import build_universe_brief


def _rng_for(seed: str, namespace: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{namespace}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _slug(text: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "_" for ch in text)
    while "__" in clean:
        clean = clean.replace("__", "_")
    return clean.strip("_") or "universe"


def _default_compiler_fill(request: CreationRequest) -> CompilerFill:
    base_org = request.setting_kind.replace("_", " ").title()
    return CompilerFill(
        setting_profile={
            "short_description": request.premise[:240],
            "social_logic": request.starting_context or "People and institutions strain against the world's premise.",
            "dominant_conflicts": [request.setting_kind, request.story_mode, "identity", "scarcity"],
        },
        naming_banks={
            "person_given": ["Alex", "June", "Sarah", "Jason", "Elias", "Mae"],
            "person_family": ["Vale", "Cross", "Mercer", "Hale", "Quinn", "Morrow"],
            "organization": [f"{base_org} Authority", f"{base_org} Works", f"{base_org} Office"],
            "district": ["North Quarter", "Old Spine", "Market Ring", "Outer Verge"],
        },
        role_archetypes=[
            {
                "archetype_id": "operator",
                "label": "Operator",
                "trait_bias": {"status_drive": 0.5, "curiosity": 0.6, "obedience": 0.2},
            },
            {
                "archetype_id": "worker",
                "label": "Worker",
                "trait_bias": {"status_drive": 0.3, "curiosity": 0.4, "obedience": 0.5},
            },
        ],
        institution_templates=[
            {"template_id": "inst_power", "label": f"{base_org} Authority", "type": "institution"},
            {"template_id": "inst_market", "label": f"{base_org} Exchange", "type": "market"},
        ],
        location_templates=[
            {"template_id": "loc_core", "label": "Core District", "location_type": "district"},
            {"template_id": "loc_margin", "label": "Outer Verge", "location_type": "district"},
        ],
        thread_templates=[
            {"template_id": "thr_status", "title": "Will someone rise too fast?", "domain": "status"},
            {"template_id": "thr_contract", "title": "Will a major obligation break?", "domain": "obligation"},
        ],
        prediction_templates=[
            {"template_id": "pred_status", "claim_type": "status_shift"},
            {"template_id": "pred_breach", "claim_type": "obligation_failure"},
        ],
        audio_tendencies={"default_signatures": ["meanwhile_transition", "dramatic_hush"]},
    )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _display_phrase(text: str) -> str:
    return text.replace("_", " ")


def _concept_pressure_biases(concepts: List[ConceptRecord]) -> Dict[str, float]:
    pressure = {
        "social": 0.0,
        "mystery": 0.0,
        "scarcity": 0.0,
        "institutional": 0.0,
        "romantic": 0.0,
        "entropy": 0.0,
    }
    category_biases = {
        "relationship_force": {"social": 0.08, "romantic": 0.18},
        "obligation": {"scarcity": 0.18, "institutional": 0.08},
        "memory_force": {"mystery": 0.16, "social": 0.06},
        "desire_force": {"entropy": 0.1, "social": 0.06},
        "status_force": {"social": 0.1, "institutional": 0.08},
    }
    tag_biases = {
        "fear": {"mystery": 0.04, "entropy": 0.04},
        "money": {"scarcity": 0.06},
        "reputation": {"social": 0.05},
        "memory": {"mystery": 0.04},
        "power": {"institutional": 0.05},
        "romance": {"romantic": 0.05},
        "loss": {"entropy": 0.05},
    }
    for concept in concepts:
        for axis, delta in category_biases.get(concept.category, {}).items():
            pressure[axis] += delta
        for tag in concept.tags:
            for axis, delta in tag_biases.get(tag, {}).items():
                pressure[axis] += delta
    return {axis: round(_clamp(value, 0.0, 0.35), 3) for axis, value in pressure.items()}


def _apply_pressure_biases(
    base_profile: Dict[str, float],
    pressure_biases: Dict[str, float],
) -> Dict[str, float]:
    adjusted = dict(base_profile)
    for axis, delta in pressure_biases.items():
        if axis in adjusted:
            adjusted[axis] = round(_clamp(float(adjusted[axis]) + delta), 3)
    return adjusted


def _concept_coefficient_profile(concepts: List[ConceptRecord]) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for concept in concepts:
        for name, value in concept.default_coefficients.items():
            totals[name] = totals.get(name, 0.0) + float(value)
            counts[name] = counts.get(name, 0) + 1
    return {
        name: round(totals[name] / counts[name], 3)
        for name in sorted(totals)
    }


def _thread_title_for(concept: ConceptRecord, thread_key: str) -> str:
    phrase = _display_phrase(thread_key)
    return f"Will {concept.label.lower()} force a {phrase}?"


def _pick_participants(
    rng: random.Random,
    characters: List[Dict[str, Any]],
    *,
    minimum: int = 1,
    maximum: int = 3,
) -> List[str]:
    if not characters:
        return []
    size = min(len(characters), max(minimum, rng.randint(minimum, maximum)))
    pool = list(range(len(characters)))
    rng.shuffle(pool)
    return [characters[index]["character_id"] for index in pool[:size]]


def _concept_character_biases(concepts: List[ConceptRecord]) -> Dict[str, Dict[str, float] | List[str]]:
    resource_biases = {"cash": 0.0, "housing_security": 0.0, "mobility": 0.0}
    desire_biases = {"freedom": 0.0, "belonging": 0.0, "status": 0.0}
    fear_biases = {"loss": 0.0, "humiliation": 0.0}
    stress_biases = {"baseline": 0.0, "volatility": 0.0}
    myth_tags: List[str] = []
    starting_promises: List[str] = []

    for concept in concepts:
        if concept.concept_id == "grief":
            desire_biases["belonging"] += 0.12
            fear_biases["loss"] += 0.26
            stress_biases["baseline"] += 0.18
            stress_biases["volatility"] += 0.14
            resource_biases["mobility"] -= 0.08
            myth_tags.append("mourning")
            starting_promises.append("carry_the_dead_forward")
        if concept.concept_id == "ambition":
            desire_biases["status"] += 0.28
            desire_biases["freedom"] += 0.08
            fear_biases["humiliation"] += 0.18
            stress_biases["baseline"] += 0.08
            stress_biases["volatility"] += 0.1
            resource_biases["cash"] += 1200.0
            resource_biases["mobility"] += 0.1
            myth_tags.append("striver")
            starting_promises.append("rise_at_any_cost")
        if concept.concept_id == "love":
            desire_biases["belonging"] += 0.2
            fear_biases["loss"] += 0.1
            stress_biases["baseline"] -= 0.04
            myth_tags.append("bonded")
            starting_promises.append("protect_someone")
        if concept.concept_id == "debt":
            resource_biases["cash"] -= 1800.0
            resource_biases["housing_security"] -= 0.1
            desire_biases["freedom"] += 0.1
            fear_biases["humiliation"] += 0.12
            stress_biases["baseline"] += 0.14
            stress_biases["volatility"] += 0.08
            myth_tags.append("indebted")
            starting_promises.append("settle_the_score")
        if concept.concept_id == "rumor":
            fear_biases["humiliation"] += 0.1
            stress_biases["volatility"] += 0.1
            myth_tags.append("watched")

    return {
        "resource_state": resource_biases,
        "desire_vector": desire_biases,
        "fear_vector": fear_biases,
        "stress_profile": stress_biases,
        "myth_tags": sorted(set(myth_tags)),
        "starting_promises": sorted(set(starting_promises)),
    }


def _generate_characters(
    canonical_seed: str,
    compiler_fill: CompilerFill,
    selected_concepts: List[ConceptRecord],
    total_characters: int,
    location_ids: List[str],
    organization_ids: List[str],
) -> List[Dict[str, Any]]:
    rng = _rng_for(canonical_seed, "characters")
    biases = _concept_character_biases(selected_concepts)
    given_names = compiler_fill.naming_banks.get("person_given") or ["Alex"]
    family_names = compiler_fill.naming_banks.get("person_family") or ["Vale"]
    archetypes = compiler_fill.role_archetypes or [{"archetype_id": "citizen", "label": "Citizen", "trait_bias": {}}]
    rows: List[Dict[str, Any]] = []
    for index in range(total_characters):
        archetype = archetypes[index % len(archetypes)]
        display_name = f"{rng.choice(given_names)} {rng.choice(family_names)}"
        home_location = location_ids[index % len(location_ids)] if location_ids else ""
        org_ids = [organization_ids[index % len(organization_ids)]] if organization_ids else []
        rows.append(
            {
                "character_id": f"char_{index + 1:04d}",
                "display_name": display_name,
                "archetype_id": archetype["archetype_id"],
                "origin_location_id": home_location,
                "home_location_id": home_location,
                "organization_ids": org_ids,
                "trait_vector": archetype.get("trait_bias", {}),
                "resource_state": {
                    "cash": round(max(0.0, rng.uniform(0, 10000) + float(biases["resource_state"]["cash"])), 2),
                    "housing_security": round(_clamp(rng.uniform(0.1, 1.0) + float(biases["resource_state"]["housing_security"])), 3),
                    "mobility": round(_clamp(rng.uniform(0.1, 1.0) + float(biases["resource_state"]["mobility"])), 3),
                },
                "desire_vector": {
                    "freedom": round(_clamp(rng.uniform(0.0, 1.0) + float(biases["desire_vector"]["freedom"])), 3),
                    "belonging": round(_clamp(rng.uniform(0.0, 1.0) + float(biases["desire_vector"]["belonging"])), 3),
                    "status": round(_clamp(rng.uniform(0.0, 1.0) + float(biases["desire_vector"]["status"])), 3),
                },
                "fear_vector": {
                    "loss": round(_clamp(rng.uniform(0.0, 1.0) + float(biases["fear_vector"]["loss"])), 3),
                    "humiliation": round(_clamp(rng.uniform(0.0, 1.0) + float(biases["fear_vector"]["humiliation"])), 3),
                },
                "stress_profile": {
                    "baseline": round(_clamp(rng.uniform(0.1, 0.8) + float(biases["stress_profile"]["baseline"])), 3),
                    "volatility": round(_clamp(rng.uniform(0.1, 0.9) + float(biases["stress_profile"]["volatility"])), 3),
                },
                "ledger_seed": {
                    "myth_tags": list(biases["myth_tags"]),
                    "starting_promises": list(biases["starting_promises"]),
                },
            }
        )
    return rows


def _concept_relationship_biases(concepts: List[ConceptRecord]) -> Dict[str, float]:
    biases = {
        "affection": 0.0,
        "trust": 0.0,
        "dependency": 0.0,
        "resentment": 0.0,
        "attraction": 0.0,
        "loyalty": 0.0,
        "fear": 0.0,
        "history_depth": 0.0,
    }
    for concept in concepts:
        if concept.concept_id == "love":
            biases["affection"] += 0.28
            biases["attraction"] += 0.22
            biases["loyalty"] += 0.18
            biases["trust"] += 0.1
            biases["dependency"] += 0.08
        if concept.concept_id == "debt":
            biases["dependency"] += 0.22
            biases["resentment"] += 0.14
            biases["fear"] += 0.12
            biases["trust"] -= 0.06
        if concept.concept_id == "rumor":
            biases["trust"] -= 0.14
            biases["fear"] += 0.08
            biases["history_depth"] += 0.06
        if concept.concept_id == "grief":
            biases["affection"] += 0.06
            biases["resentment"] += 0.08
            biases["history_depth"] += 0.14
        if concept.concept_id == "ambition":
            biases["resentment"] += 0.1
            biases["trust"] -= 0.04
    return {key: round(value, 3) for key, value in biases.items()}


def _build_relationships(
    brief_seed: str,
    characters: List[Dict[str, Any]],
    selected_concepts: List[ConceptRecord],
    starting_population: int,
) -> List[Dict[str, Any]]:
    rel_rng = _rng_for(brief_seed, "relationships")
    biases = _concept_relationship_biases(selected_concepts)
    relationship_count = max(0, min(len(characters) - 1, starting_population // 2))
    rows: List[Dict[str, Any]] = []
    for index in range(relationship_count):
        source = characters[index]
        target = characters[(index + 1) % len(characters)]
        rows.append(
            {
                "relationship_id": f"rel_{index + 1:04d}",
                "source_character_id": source["character_id"],
                "target_character_id": target["character_id"],
                "affection": round(_clamp(rel_rng.uniform(0.0, 1.0) + biases["affection"]), 3),
                "trust": round(_clamp(rel_rng.uniform(0.0, 1.0) + biases["trust"]), 3),
                "dependency": round(_clamp(rel_rng.uniform(0.0, 1.0) + biases["dependency"]), 3),
                "resentment": round(_clamp(rel_rng.uniform(0.0, 1.0) + biases["resentment"]), 3),
                "attraction": round(_clamp(rel_rng.uniform(0.0, 1.0) + biases["attraction"]), 3),
                "loyalty": round(_clamp(rel_rng.uniform(0.0, 1.0) + biases["loyalty"]), 3),
                "fear": round(_clamp(rel_rng.uniform(0.0, 1.0) + biases["fear"]), 3),
                "history_depth": round(_clamp(rel_rng.uniform(0.0, 1.0) + biases["history_depth"]), 3),
                "concept_tags": [concept.concept_id for concept in selected_concepts if concept.concept_id in {"love", "debt", "rumor", "grief", "ambition"}],
            }
        )
    return rows


def _concept_obligation_defaults(concepts: List[ConceptRecord]) -> Dict[str, Any]:
    obligation_type = "employment"
    stakes = 0.5
    failure_cost = 0.6
    success_reward = 0.3
    due_tick_delta = 0
    pressure_tags: List[str] = []

    for concept in concepts:
        if concept.concept_id == "debt":
            obligation_type = "debt_note"
            stakes += 0.25
            failure_cost += 0.18
            success_reward += 0.06
            due_tick_delta -= 25
            pressure_tags.extend(["debt", "collection"])
        if concept.concept_id == "love":
            success_reward += 0.08
            pressure_tags.append("devotion")
        if concept.concept_id == "rumor":
            failure_cost += 0.06
            pressure_tags.append("reputation")
        if concept.concept_id == "grief":
            due_tick_delta -= 10
            pressure_tags.append("mourning")

    return {
        "obligation_type": obligation_type,
        "stakes": round(_clamp(stakes), 3),
        "failure_cost": round(_clamp(failure_cost), 3),
        "success_reward": round(_clamp(success_reward), 3),
        "due_tick_delta": due_tick_delta,
        "pressure_tags": pressure_tags,
    }


def _build_obligations(
    characters: List[Dict[str, Any]],
    organization_rows: List[Dict[str, Any]],
    selected_concepts: List[ConceptRecord],
) -> List[Dict[str, Any]]:
    if not organization_rows:
        return []
    defaults = _concept_obligation_defaults(selected_concepts)
    rows: List[Dict[str, Any]] = []
    for index, character in enumerate(characters[: max(1, len(characters) // 3)]):
        obligation_type = defaults["obligation_type"] if index % 2 == 0 else "service_contract"
        rows.append(
            {
                "obligation_id": f"obl_{index + 1:04d}",
                "obligation_type": obligation_type,
                "holder_id": character["character_id"],
                "counterparty_id": organization_rows[index % len(organization_rows)]["organization_id"],
                "start_tick": 0,
                "due_tick": max(30, 120 + index * 10 + int(defaults["due_tick_delta"])),
                "stakes": defaults["stakes"],
                "failure_cost": defaults["failure_cost"],
                "success_reward": defaults["success_reward"],
                "status": "active",
                "pressure_tags": defaults["pressure_tags"],
            }
        )
    return rows


def _build_story_threads(
    brief_seed: str,
    thread_templates: List[Dict[str, Any]],
    selected_concepts: List[ConceptRecord],
    characters: List[Dict[str, Any]],
    target_count: int,
) -> List[Dict[str, Any]]:
    rng = _rng_for(brief_seed, "story_threads")
    templates: List[Dict[str, str]] = []
    for concept in selected_concepts:
        if concept.creates_threads:
            templates.append(
                {
                    "title": _thread_title_for(concept, concept.creates_threads[0]),
                    "domain": concept.concept_id,
                }
            )
    for concept in selected_concepts:
        for thread_key in concept.creates_threads[1:]:
            templates.append(
                {
                    "title": _thread_title_for(concept, thread_key),
                    "domain": concept.concept_id,
                }
            )
    templates.extend(
        {
            "title": tmpl.get("title", f"Thread {index + 1}"),
            "domain": tmpl.get("domain", "general"),
        }
        for index, tmpl in enumerate(thread_templates)
    )
    if not templates:
        templates.append({"title": "Will the world hold together?", "domain": "general"})

    rows: List[Dict[str, Any]] = []
    for index in range(max(1, target_count)):
        template = templates[index % len(templates)]
        concept = selected_concepts[index % len(selected_concepts)] if selected_concepts else None
        heat_bias = float(concept.default_coefficients.get("thread_heat_bias", 0.0)) if concept else 0.0
        rows.append(
            {
                "thread_id": f"thr_{index + 1:04d}",
                "title": template["title"],
                "domain": template["domain"],
                "participant_ids": _pick_participants(rng, characters),
                "status": "active",
                "confidence": round(_clamp(0.45 + heat_bias * 0.3 + rng.uniform(-0.05, 0.1)), 3),
                "urgency": round(_clamp(0.35 + heat_bias * 0.5 + rng.uniform(0.0, 0.2)), 3),
                "heat": round(_clamp(0.5 + heat_bias + rng.uniform(0.0, 0.2)), 3),
                "predicted_resolution_tick": 100 + index * 18 + int(rng.uniform(0, 24)),
                "source_event_ids": [],
            }
        )
    return rows


def _build_predictions(
    brief_seed: str,
    prediction_templates: List[Dict[str, Any]],
    selected_concepts: List[ConceptRecord],
    thread_rows: List[Dict[str, Any]],
    target_count: int,
) -> List[Dict[str, Any]]:
    rng = _rng_for(brief_seed, "predictions")
    templates: List[Dict[str, str]] = []
    for concept in selected_concepts:
        if concept.creates_predictions:
            templates.append(
                {
                    "claim_type": concept.creates_predictions[0],
                    "concept_id": concept.concept_id,
                }
            )
    for concept in selected_concepts:
        for claim_type in concept.creates_predictions[1:]:
            templates.append({"claim_type": claim_type, "concept_id": concept.concept_id})
    templates.extend(
        {"claim_type": tmpl.get("claim_type", "general_shift"), "concept_id": ""}
        for tmpl in prediction_templates
    )
    if not templates:
        templates.append({"claim_type": "general_shift", "concept_id": ""})

    rows: List[Dict[str, Any]] = []
    for index in range(max(1, target_count)):
        template = templates[index % len(templates)]
        target_thread = thread_rows[index % len(thread_rows)] if thread_rows else {}
        concept = next(
            (item for item in selected_concepts if item.concept_id == template["concept_id"]),
            None,
        )
        spawn_bias = float(concept.default_coefficients.get("prediction_spawn_bias", 0.0)) if concept else 0.0
        rows.append(
            {
                "prediction_id": f"pred_{index + 1:04d}",
                "predictor_type": "world",
                "predictor_id": "world",
                "target_type": "thread",
                "target_id": target_thread.get("thread_id", ""),
                "thread_id": target_thread.get("thread_id", ""),
                "claim_type": template["claim_type"],
                "confidence": round(_clamp(0.45 + spawn_bias + rng.uniform(-0.05, 0.15)), 3),
                "horizon_ticks": 90 + index * 10 + int(rng.uniform(0, 20)),
                "status": "open",
                "resolution_outcome": None,
            }
        )
    return rows


def _concept_macro_state_rows(
    selected_concepts: List[ConceptRecord],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for concept in selected_concepts:
        heat_bias = float(concept.default_coefficients.get("thread_heat_bias", 0.0))
        spawn_bias = float(concept.default_coefficients.get("prediction_spawn_bias", 0.0))
        baseline = round(_clamp(0.2 + heat_bias + spawn_bias), 3)
        rows.append(
            {
                "axis_id": f"{concept.concept_id}_pressure",
                "baseline": baseline,
                "current_value": baseline,
                "normalization_bias": round(heat_bias, 3),
                "drift_rate": round(spawn_bias, 3),
            }
        )
    return rows


def _concept_coefficient_rows(selected_concepts: List[ConceptRecord]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    index = 3
    for concept in selected_concepts:
        for name, value in sorted(concept.default_coefficients.items()):
            rows.append(
                {
                    "coefficient_id": f"coef_{index:04d}",
                    "scope": "concept",
                    "name": f"{concept.concept_id}_{name}",
                    "value": round(float(value), 3),
                    "description": f"Default {name} bias imported from the {concept.label} ontology record.",
                }
            )
            index += 1
    return rows


def compile_universe(
    request: CreationRequest,
    compiler_fill: Optional[CompilerFill] = None,
    *,
    ruleset_version: str = "v1",
) -> CompiledWorldPackage:
    brief = build_universe_brief(request)
    compiler_fill = compiler_fill or _default_compiler_fill(request)
    registry = load_concept_registry()
    selected_concepts = select_concepts(registry, brief.selected_ontology_domains)
    pressure_biases = _concept_pressure_biases(selected_concepts)
    coefficient_profile = _apply_pressure_biases(brief.pressure_profile, pressure_biases)
    coefficient_profile.update(_concept_coefficient_profile(selected_concepts))

    loc_templates = compiler_fill.location_templates or []
    org_templates = compiler_fill.institution_templates or []
    thread_templates = compiler_fill.thread_templates or []
    prediction_templates = compiler_fill.prediction_templates or []

    location_rows = [
        {
            "location_id": f"loc_{i + 1:04d}",
            "label": tmpl.get("label", f"Location {i + 1}"),
            "location_type": tmpl.get("location_type", "district"),
            "parent_location_id": "",
            "pressure_tags": [],
            "population_capacity": 50,
            "economic_heat": 0.5,
            "danger_heat": 0.3,
            "symbolic_weight": 0.5,
        }
        for i, tmpl in enumerate(loc_templates[: max(1, brief.population_targets.districts)])
    ]
    if not location_rows:
        location_rows = [
            {
                "location_id": "loc_0001",
                "label": "Core District",
                "location_type": "district",
                "parent_location_id": "",
                "pressure_tags": [],
                "population_capacity": request.starting_population,
                "economic_heat": 0.5,
                "danger_heat": 0.3,
                "symbolic_weight": 0.5,
            }
        ]

    organization_rows = [
        {
            "organization_id": f"org_{i + 1:04d}",
            "label": tmpl.get("label", f"Organization {i + 1}"),
            "type": tmpl.get("type", "institution"),
            "district_id": location_rows[i % len(location_rows)]["location_id"],
            "power_score": 0.5,
            "wealth_score": 0.5,
            "policy_profile": {},
            "member_ids": [],
            "tension_profile": {},
        }
        for i, tmpl in enumerate(org_templates[: brief.population_targets.organizations])
    ]

    characters = _generate_characters(
        brief.canonical_seed,
        compiler_fill,
        selected_concepts,
        request.starting_population,
        [row["location_id"] for row in location_rows],
        [row["organization_id"] for row in organization_rows],
    )

    relationship_rows = _build_relationships(
        brief.canonical_seed,
        characters,
        selected_concepts,
        request.starting_population,
    )

    obligation_rows = _build_obligations(
        characters,
        organization_rows,
        selected_concepts,
    )

    macro_state_rows = [
        {
            "axis_id": "social_pressure",
            "baseline": coefficient_profile["social"],
            "current_value": coefficient_profile["social"],
            "normalization_bias": pressure_biases["social"],
            "drift_rate": round(pressure_biases["social"] / 4.0, 3),
        },
        {
            "axis_id": "institutional_pressure",
            "baseline": coefficient_profile["institutional"],
            "current_value": coefficient_profile["institutional"],
            "normalization_bias": pressure_biases["institutional"],
            "drift_rate": round(pressure_biases["institutional"] / 4.0, 3),
        },
        {
            "axis_id": "entropy_pressure",
            "baseline": coefficient_profile["entropy"],
            "current_value": coefficient_profile["entropy"],
            "normalization_bias": pressure_biases["entropy"],
            "drift_rate": round(pressure_biases["entropy"] / 4.0, 3),
        },
    ]
    macro_state_rows.extend(_concept_macro_state_rows(selected_concepts))

    thread_rows = _build_story_threads(
        brief.canonical_seed,
        thread_templates,
        selected_concepts,
        characters,
        brief.population_targets.starting_threads,
    )

    prediction_rows = _build_predictions(
        brief.canonical_seed,
        prediction_templates,
        selected_concepts,
        thread_rows,
        brief.population_targets.starting_predictions,
    )

    memory_rows = [
        {
            "memory_id": "mem_0001",
            "memory_tier": "recent_history",
            "owner_type": "world",
            "owner_id": "world",
            "summary": request.starting_context or request.premise[:160],
            "source_event_ids": [],
            "decay_rate": 0.02,
            "myth_weight": 0.0,
        }
    ]

    coefficient_rows = [
        {
            "coefficient_id": "coef_0001",
            "scope": "global",
            "name": "thread_heat_decay",
            "value": 0.015,
            "description": "Per-tick natural heat decay for unresolved threads.",
        },
        {
            "coefficient_id": "coef_0002",
            "scope": "global",
            "name": "prediction_confidence_drift",
            "value": 0.005,
            "description": "Per-tick confidence drift toward uncertainty before resolution.",
        },
    ]
    coefficient_rows.extend(_concept_coefficient_rows(selected_concepts))

    identity = PackageIdentity(
        universe_id="fu_" + _slug(request.universe_title)[:24],
        universe_title=request.universe_title,
        ruleset_family=brief.ruleset_family,
        ruleset_version=ruleset_version,
        canonical_seed=brief.canonical_seed,
        seed_hash=brief.seed_hash,
    )

    return CompiledWorldPackage(
        package_identity=identity,
        universe_brief={
            "premise": request.premise,
            "story_mode": request.story_mode,
            "world_scale": request.world_scale,
            "starting_population": request.starting_population,
            "selected_ontology_domains": brief.selected_ontology_domains,
        },
        time_policy={
            "execution_model": brief.execution_model,
            "preset": brief.time_policy_preset,
            "world_seconds_per_real_second": brief.world_seconds_per_real_second,
            "real_seconds_to_world_tick": 60,
            "idle_tick_ratio": 0.25,
            "deep_sleep_mode": "epoch",
            "query_driven": brief.execution_model == "on_demand",
        },
        coefficient_profile=coefficient_profile,
        world_tables={
            "characters": characters,
            "relationships": relationship_rows,
            "organizations": organization_rows,
            "locations": location_rows,
            "obligations": obligation_rows,
            "macro_state": macro_state_rows,
            "story_threads": thread_rows,
            "predictions": prediction_rows,
            "memory_records": memory_rows,
            "coefficients": coefficient_rows,
        },
        compiler_fill=compiler_fill,
    )


def compiled_world_to_json(package: CompiledWorldPackage) -> str:
    return json.dumps(package.model_dump(mode="json"), indent=2, ensure_ascii=False)
