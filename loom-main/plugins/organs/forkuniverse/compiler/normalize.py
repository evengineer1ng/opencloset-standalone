from __future__ import annotations

import hashlib
import json
from typing import Dict

from .models import CreationRequest, PopulationTargets, UniverseBrief
from forkuniverse.ontology.registry import load_concept_registry


_WORLD_SCALE_TARGETS = {
    "micro": {
        "major_characters": 2,
        "supporting_characters": 4,
        "organizations": 1,
        "districts": 1,
        "starting_threads": 2,
        "starting_predictions": 4,
    },
    "site": {
        "major_characters": 5,
        "supporting_characters": 10,
        "organizations": 2,
        "districts": 2,
        "starting_threads": 4,
        "starting_predictions": 8,
    },
    "district": {
        "major_characters": 12,
        "supporting_characters": 24,
        "organizations": 5,
        "districts": 4,
        "starting_threads": 8,
        "starting_predictions": 14,
    },
    "city": {
        "major_characters": 20,
        "supporting_characters": 60,
        "organizations": 10,
        "districts": 8,
        "starting_threads": 14,
        "starting_predictions": 24,
    },
    "regional": {
        "major_characters": 36,
        "supporting_characters": 120,
        "organizations": 18,
        "districts": 16,
        "starting_threads": 24,
        "starting_predictions": 40,
    },
}


def _stable_hash(payload: Dict[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical_seed_for_request(request: CreationRequest) -> str:
    if request.seed_mode in {"preset", "custom"} and request.custom_seed.strip():
        return request.custom_seed.strip()
    stable = {
        "premise": request.premise,
        "setting_kind": request.setting_kind,
        "time_period": request.time_period,
        "story_mode": request.story_mode,
        "world_scale": request.world_scale,
        "starting_population": request.starting_population,
        "preset_id": request.preset_id,
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]


def build_universe_brief(request: CreationRequest) -> UniverseBrief:
    registry = load_concept_registry()
    canonical_seed = canonical_seed_for_request(request)
    targets = dict(_WORLD_SCALE_TARGETS[request.world_scale])

    if request.starting_population > 0:
        major = max(2, min(request.starting_population, targets["major_characters"]))
        remaining = max(0, request.starting_population - major)
        targets["major_characters"] = major
        targets["supporting_characters"] = remaining

    pressure_profile = {
        "social": 0.5,
        "mystery": 0.2,
        "scarcity": request.constraints.economic_harshness,
        "institutional": request.constraints.institutional_density,
        "romantic": request.constraints.romance_ceiling,
        "entropy": request.constraints.entropy_rate,
    }

    if request.genre_mix:
        lower = {k.lower(): v for k, v in request.genre_mix.items()}
        pressure_profile["mystery"] = max(pressure_profile["mystery"], lower.get("mystery", 0.0))
        pressure_profile["romantic"] = max(pressure_profile["romantic"], lower.get("romance", 0.0))
        pressure_profile["social"] = max(pressure_profile["social"], lower.get("drama", 0.0))

    prompt_inputs = {
        "universe_title": request.universe_title,
        "premise": request.premise,
        "setting_kind": request.setting_kind,
        "time_period": request.time_period,
        "story_mode": request.story_mode,
        "location_flavor": request.location_flavor,
        "genre_mix": request.genre_mix,
        "tone_mix": request.tone_mix,
        "starting_context": request.starting_context,
        "constraints": request.constraints.model_dump(),
        "ontology_domains": request.ontology_domains,
        "available_concepts": [concept.concept_id for concept in registry.concepts],
    }

    ruleset_family = request.preset_id.strip() or request.setting_kind.strip()
    seed_hash = _stable_hash(
        {
            "canonical_seed": canonical_seed,
            "ruleset_family": ruleset_family,
            "request": request.model_dump(mode="json"),
        }
    )

    return UniverseBrief(
        ruleset_family=ruleset_family,
        canonical_seed=canonical_seed,
        seed_hash=seed_hash,
        execution_model=request.time_policy.execution_model,
        time_policy_preset=request.time_policy.preset,
        world_seconds_per_real_second=request.time_policy.world_seconds_per_real_second,
        selected_ontology_domains=list(request.ontology_domains),
        population_targets=PopulationTargets(**targets),
        pressure_profile=pressure_profile,
        compiler_prompt_inputs=prompt_inputs,
    )
