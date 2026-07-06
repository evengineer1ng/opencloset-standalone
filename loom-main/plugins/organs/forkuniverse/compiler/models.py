from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    violence_ceiling: float = Field(default=0.4, ge=0.0, le=1.0)
    romance_ceiling: float = Field(default=0.4, ge=0.0, le=1.0)
    absurdity_ceiling: float = Field(default=0.2, ge=0.0, le=1.0)
    institutional_density: float = Field(default=0.5, ge=0.0, le=1.0)
    economic_harshness: float = Field(default=0.5, ge=0.0, le=1.0)
    entropy_rate: float = Field(default=0.5, ge=0.0, le=1.0)


class TimePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_model: Literal["on_demand", "realtime_daemon", "hybrid"] = "on_demand"
    preset: Literal[
        "fixed_slow",
        "fixed_medium",
        "fixed_fast",
        "adaptive_light",
        "adaptive_medium",
        "adaptive_heavy",
    ] = "adaptive_medium"
    world_seconds_per_real_second: float = Field(default=60.0, gt=0.0)


class CreationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["forkuniverse.creation_request.v1"] = (
        "forkuniverse.creation_request.v1"
    )
    universe_title: str = Field(min_length=1, max_length=120)
    premise: str = Field(min_length=10, max_length=4000)
    setting_kind: str = Field(min_length=1, max_length=80)
    time_period: str = Field(min_length=1, max_length=80)
    story_mode: Literal["episodic", "longform", "continuous"]
    world_scale: Literal["micro", "site", "district", "city", "regional"]
    starting_population: int = Field(ge=2, le=1000)
    seed_mode: Literal["preset", "custom", "random"]
    preset_id: str = Field(default="", max_length=120)
    custom_seed: str = Field(default="", max_length=200)
    location_flavor: str = Field(default="", max_length=240)
    genre_mix: Dict[str, float] = Field(default_factory=dict)
    tone_mix: Dict[str, float] = Field(default_factory=dict)
    starting_context: str = Field(default="", max_length=2000)
    operator_insert_mode: Literal["observer", "participant", "hidden_force", "oracle_like"] = (
        "observer"
    )
    operator_role_hint: str = Field(default="", max_length=240)
    ontology_domains: List[str] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    time_policy: TimePolicyRequest = Field(default_factory=TimePolicyRequest)


class PopulationTargets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    major_characters: int
    supporting_characters: int
    organizations: int
    districts: int
    starting_threads: int
    starting_predictions: int


class UniverseBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["forkuniverse.universe_brief.v1"] = (
        "forkuniverse.universe_brief.v1"
    )
    ruleset_family: str
    canonical_seed: str
    seed_hash: str
    execution_model: str
    time_policy_preset: str
    world_seconds_per_real_second: float
    selected_ontology_domains: List[str]
    population_targets: PopulationTargets
    pressure_profile: Dict[str, float]
    compiler_prompt_inputs: Dict[str, Any]


class CompilerFill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["forkuniverse.compiler_fill.v1"] = "forkuniverse.compiler_fill.v1"
    setting_profile: Dict[str, Any] = Field(default_factory=dict)
    naming_banks: Dict[str, List[str]] = Field(default_factory=dict)
    role_archetypes: List[Dict[str, Any]] = Field(default_factory=list)
    institution_templates: List[Dict[str, Any]] = Field(default_factory=list)
    location_templates: List[Dict[str, Any]] = Field(default_factory=list)
    character_seeds: List[Dict[str, Any]] = Field(default_factory=list)
    relationship_templates: List[Dict[str, Any]] = Field(default_factory=list)
    thread_templates: List[Dict[str, Any]] = Field(default_factory=list)
    prediction_templates: List[Dict[str, Any]] = Field(default_factory=list)
    audio_tendencies: Dict[str, Any] = Field(default_factory=dict)


class PackageIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: str
    universe_title: str
    ruleset_family: str
    ruleset_version: str
    canonical_seed: str
    seed_hash: str


class CompiledWorldPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["forkuniverse.compiled_world_package.v1"] = (
        "forkuniverse.compiled_world_package.v1"
    )
    package_identity: PackageIdentity
    universe_brief: Dict[str, Any]
    time_policy: Dict[str, Any]
    coefficient_profile: Dict[str, float]
    world_tables: Dict[str, List[Dict[str, Any]]]
    compiler_fill: Optional[CompilerFill] = None
