from __future__ import annotations

from typing import Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConceptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    affects: List[str] = Field(default_factory=list)
    creates_events: List[str] = Field(default_factory=list)
    creates_threads: List[str] = Field(default_factory=list)
    creates_predictions: List[str] = Field(default_factory=list)
    decays_with: List[str] = Field(default_factory=list)
    intensifies_with: List[str] = Field(default_factory=list)
    resolution_modes: List[str] = Field(default_factory=list)
    failure_modes: List[str] = Field(default_factory=list)
    radio_surfaces: List[str] = Field(default_factory=list)
    default_coefficients: Dict[str, float] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class ConceptRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["forkuniverse.concept_registry.v1"] = (
        "forkuniverse.concept_registry.v1"
    )
    registry_id: str = Field(min_length=1, max_length=120)
    concepts: List[ConceptRecord]
