"""ForkUniverse authoritative cold-layer simulation core.

This module is intentionally a single large simulation file. The mature sims in
this repo (From the Backmarker, Oracle Kingdom, Neikos) all converged toward one
authoritative state model with a true tick seam, deterministic RNG discipline, a
causal ledger, and a hot-layer export surface. ForkUniverse follows that lineage
on purpose rather than scattering the coupling across many tiny modules too early.

Lineage of the pieces below:

* Oracle Kingdom -> :class:`SeededRNG` forked namespaces, :class:`CausalLedger`,
  absence reconstruction (:meth:`UniverseState.compute_absence`), decree ripple
  (:meth:`UniverseState.apply_operator_input`).
* From the Backmarker -> authoritative sim core, obligation/economy pressure,
  event generation from state, action-application seam.
* Neikos -> deterministic world derivation from seed, ledger normalization,
  memory promotion / mythologization.
* ATL / ATLFM -> state -> prediction -> event -> resolution -> updated model, with
  calibration tracking so the world can be wrong and surprised.

Determinism contract
--------------------
Every stochastic decision in a tick is drawn from a stream keyed by
``(canonical_seed, namespace, tick)`` -- never from accumulated RNG state. This
guarantees the critical property that ForkUniverse needs as an on-demand truth
engine: advancing one tick at a time produces the *same* world as reconstructing
an absence of N ticks in one batch. ``simulate_epoch`` and ``advance_tick`` are
therefore two views on the same deterministic function of (seed, prior state).
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from forkuniverse.compiler.models import CompiledWorldPackage


# ---------------------------------------------------------------------------
# Seed kernel
# ---------------------------------------------------------------------------


class SeededRNG:
    """Forked-namespace deterministic RNG (Oracle Kingdom / Neikos pattern).

    A single canonical seed spawns independent, reproducible random streams per
    subsystem and per tick. Two engines built from the same seed that take the
    same number of ticks observe identical streams, regardless of wall-clock or
    of whether they advanced one tick at a time or in a batch.
    """

    def __init__(self, canonical_seed: str) -> None:
        self.canonical_seed = canonical_seed

    def stream(self, namespace: str, tick: int) -> random.Random:
        digest = hashlib.sha256(
            f"{self.canonical_seed}:{namespace}:{tick}".encode("utf-8")
        ).hexdigest()
        return random.Random(int(digest[:16], 16))

    def derive(self, *parts: Any) -> random.Random:
        key = ":".join(str(part) for part in parts)
        digest = hashlib.sha256(f"{self.canonical_seed}:{key}".encode("utf-8")).hexdigest()
        return random.Random(int(digest[:16], 16))


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _stable_digest(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Runtime state objects
# ---------------------------------------------------------------------------


@dataclass
class TimeState:
    """World clock. The tick is a unit of simulation computation, not a timer."""

    tick: int = 0
    epoch: int = 0
    world_seconds_per_real_second: float = 60.0
    real_seconds_per_tick: float = 60.0
    last_computed_tick: int = 0
    mode: str = "active"  # active | idle | deep_sleep

    def ticks_owed(self, elapsed_real_seconds: float) -> int:
        if self.real_seconds_per_tick <= 0:
            return 0
        return int(max(0.0, elapsed_real_seconds) // self.real_seconds_per_tick)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick": self.tick,
            "epoch": self.epoch,
            "world_seconds_per_real_second": self.world_seconds_per_real_second,
            "real_seconds_per_tick": self.real_seconds_per_tick,
            "last_computed_tick": self.last_computed_tick,
            "mode": self.mode,
        }


@dataclass
class CharacterLedger:
    """Accumulated identity history. Identity persists from what happened, not
    from anyone deciding a character feels important."""

    wins: int = 0
    losses: int = 0
    promises_made: List[str] = field(default_factory=list)
    promises_broken: List[str] = field(default_factory=list)
    threads_touched: List[str] = field(default_factory=list)
    predictions_about: List[str] = field(default_factory=list)
    major_event_ids: List[str] = field(default_factory=list)
    myth_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wins": self.wins,
            "losses": self.losses,
            "promises_made": list(self.promises_made),
            "promises_broken": list(self.promises_broken),
            "threads_touched": sorted(set(self.threads_touched)),
            "predictions_about": sorted(set(self.predictions_about)),
            "major_event_ids": list(self.major_event_ids),
            "myth_tags": sorted(set(self.myth_tags)),
        }


@dataclass
class CharacterState:
    character_id: str
    display_name: str
    archetype_id: str = ""
    home_location_id: str = ""
    organization_ids: List[str] = field(default_factory=list)
    trait_vector: Dict[str, float] = field(default_factory=dict)
    resource_state: Dict[str, float] = field(default_factory=dict)
    desire_vector: Dict[str, float] = field(default_factory=dict)
    fear_vector: Dict[str, float] = field(default_factory=dict)
    stress_profile: Dict[str, float] = field(default_factory=dict)
    stress: float = 0.3
    ledger: CharacterLedger = field(default_factory=CharacterLedger)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "character_id": self.character_id,
            "display_name": self.display_name,
            "archetype_id": self.archetype_id,
            "home_location_id": self.home_location_id,
            "organization_ids": list(self.organization_ids),
            "trait_vector": dict(self.trait_vector),
            "resource_state": dict(self.resource_state),
            "desire_vector": dict(self.desire_vector),
            "fear_vector": dict(self.fear_vector),
            "stress_profile": dict(self.stress_profile),
            "stress": round(self.stress, 4),
            "ledger": self.ledger.to_dict(),
        }


@dataclass
class RelationshipState:
    relationship_id: str
    source_character_id: str
    target_character_id: str
    axes: Dict[str, float] = field(default_factory=dict)
    concept_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_character_id": self.source_character_id,
            "target_character_id": self.target_character_id,
            "axes": {k: round(v, 4) for k, v in self.axes.items()},
            "concept_tags": list(self.concept_tags),
        }


@dataclass
class InstitutionState:
    organization_id: str
    label: str
    type: str = "institution"
    district_id: str = ""
    power_score: float = 0.5
    wealth_score: float = 0.5
    legitimacy: float = 0.6
    member_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "label": self.label,
            "type": self.type,
            "district_id": self.district_id,
            "power_score": round(self.power_score, 4),
            "wealth_score": round(self.wealth_score, 4),
            "legitimacy": round(self.legitimacy, 4),
            "member_ids": list(self.member_ids),
        }


@dataclass
class LocationState:
    location_id: str
    label: str
    location_type: str = "district"
    economic_heat: float = 0.5
    danger_heat: float = 0.3
    symbolic_weight: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "location_id": self.location_id,
            "label": self.label,
            "location_type": self.location_type,
            "economic_heat": round(self.economic_heat, 4),
            "danger_heat": round(self.danger_heat, 4),
            "symbolic_weight": round(self.symbolic_weight, 4),
        }


@dataclass
class ObligationState:
    obligation_id: str
    obligation_type: str
    holder_id: str
    counterparty_id: str
    start_tick: int = 0
    due_tick: int = 100
    stakes: float = 0.5
    failure_cost: float = 0.5
    success_reward: float = 0.3
    status: str = "active"  # active | warned | met | breached
    pressure_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "obligation_type": self.obligation_type,
            "holder_id": self.holder_id,
            "counterparty_id": self.counterparty_id,
            "start_tick": self.start_tick,
            "due_tick": self.due_tick,
            "stakes": round(self.stakes, 4),
            "failure_cost": round(self.failure_cost, 4),
            "success_reward": round(self.success_reward, 4),
            "status": self.status,
            "pressure_tags": list(self.pressure_tags),
        }


@dataclass
class StoryThread:
    """A first-class unresolved question. Listeners return for these, not raw state."""

    thread_id: str
    title: str
    domain: str = "general"
    participant_ids: List[str] = field(default_factory=list)
    status: str = "active"  # active | heated | cooled | resolved | mythologized
    confidence: float = 0.5
    urgency: float = 0.35
    heat: float = 0.5
    opened_tick: int = 0
    predicted_resolution_tick: int = 100
    resolved_tick: Optional[int] = None
    resolution_outcome: Optional[str] = None  # affirmed | broken | faded
    source_event_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "title": self.title,
            "domain": self.domain,
            "participant_ids": list(self.participant_ids),
            "status": self.status,
            "confidence": round(self.confidence, 4),
            "urgency": round(self.urgency, 4),
            "heat": round(self.heat, 4),
            "opened_tick": self.opened_tick,
            "predicted_resolution_tick": self.predicted_resolution_tick,
            "resolved_tick": self.resolved_tick,
            "resolution_outcome": self.resolution_outcome,
            "source_event_ids": list(self.source_event_ids),
        }


@dataclass
class Prediction:
    prediction_id: str
    claim_type: str
    predictor_id: str = "world"
    target_type: str = "thread"
    target_id: str = ""
    thread_id: str = ""
    confidence: float = 0.5
    opened_tick: int = 0
    horizon_ticks: int = 90
    status: str = "open"  # open | resolved
    resolution_outcome: Optional[str] = None  # hit | miss
    calibration_error: Optional[float] = None

    @property
    def resolves_tick(self) -> int:
        return self.opened_tick + self.horizon_ticks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "claim_type": self.claim_type,
            "predictor_id": self.predictor_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "thread_id": self.thread_id,
            "confidence": round(self.confidence, 4),
            "opened_tick": self.opened_tick,
            "horizon_ticks": self.horizon_ticks,
            "resolves_tick": self.resolves_tick,
            "status": self.status,
            "resolution_outcome": self.resolution_outcome,
            "calibration_error": (
                round(self.calibration_error, 4)
                if self.calibration_error is not None
                else None
            ),
        }


@dataclass
class PredictionBook:
    """Forward claims and their later settlement, plus running calibration."""

    predictions: Dict[str, Prediction] = field(default_factory=dict)
    settled_hits: int = 0
    settled_misses: int = 0
    calibration_error_total: float = 0.0

    def open_items(self) -> List[Prediction]:
        return [p for p in self.predictions.values() if p.status == "open"]

    def add(self, prediction: Prediction) -> None:
        self.predictions[prediction.prediction_id] = prediction

    @property
    def settled_count(self) -> int:
        return self.settled_hits + self.settled_misses

    @property
    def brier_like_score(self) -> float:
        if self.settled_count == 0:
            return 0.0
        return round(self.calibration_error_total / self.settled_count, 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "predictions": [p.to_dict() for p in self.predictions.values()],
            "settled_hits": self.settled_hits,
            "settled_misses": self.settled_misses,
            "calibration_score": self.brier_like_score,
        }


@dataclass
class MemoryRecord:
    memory_id: str
    memory_tier: str  # immediate | recent_history | historical_record | mythology
    owner_type: str
    owner_id: str
    summary: str
    salience: float = 0.5
    decay_rate: float = 0.02
    myth_weight: float = 0.0
    source_event_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_tier": self.memory_tier,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "summary": self.summary,
            "salience": round(self.salience, 4),
            "decay_rate": round(self.decay_rate, 4),
            "myth_weight": round(self.myth_weight, 4),
            "source_event_ids": list(self.source_event_ids),
        }


@dataclass
class WorldMemory:
    """Tiered memory: immediate -> recent_history -> historical_record -> mythology."""

    records: List[MemoryRecord] = field(default_factory=list)
    _counter: int = 0

    TIER_ORDER = ["immediate", "recent_history", "historical_record", "mythology"]

    def next_id(self) -> str:
        self._counter += 1
        return f"mem_{self._counter:05d}"

    def remember(
        self,
        *,
        summary: str,
        owner_type: str = "world",
        owner_id: str = "world",
        salience: float = 0.6,
        myth_weight: float = 0.0,
        source_event_ids: Optional[List[str]] = None,
    ) -> MemoryRecord:
        record = MemoryRecord(
            memory_id=self.next_id(),
            memory_tier="immediate",
            owner_type=owner_type,
            owner_id=owner_id,
            summary=summary,
            salience=salience,
            myth_weight=myth_weight,
            source_event_ids=list(source_event_ids or []),
        )
        self.records.append(record)
        return record

    def to_dict(self) -> Dict[str, Any]:
        return {"records": [record.to_dict() for record in self.records]}


@dataclass
class MacroAxis:
    axis_id: str
    baseline: float
    current_value: float
    normalization_bias: float = 0.0
    drift_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis_id": self.axis_id,
            "baseline": round(self.baseline, 4),
            "current_value": round(self.current_value, 4),
            "normalization_bias": round(self.normalization_bias, 4),
            "drift_rate": round(self.drift_rate, 4),
        }


@dataclass
class WorldEvent:
    """Small, typed, evidence-rich. The cold layer is the source of truth; the
    hot layer may interpret these, never invent new ones."""

    event_id: str
    tick: int
    family: str
    event_type: str
    summary: str
    subject_ids: List[str] = field(default_factory=list)
    location_id: str = ""
    changes: Dict[str, Any] = field(default_factory=dict)
    pressure_delta: float = 0.0
    audio_signature: str = "meanwhile_transition"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tick": self.tick,
            "family": self.family,
            "event_type": self.event_type,
            "summary": self.summary,
            "subject_ids": list(self.subject_ids),
            "location_id": self.location_id,
            "changes": dict(self.changes),
            "pressure_delta": round(self.pressure_delta, 4),
            "audio_signature": self.audio_signature,
        }


@dataclass
class CausalLedger:
    """Append-only factual record. Stays factual; interpretation lives elsewhere."""

    events: List[WorldEvent] = field(default_factory=list)
    _counter: int = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"evt_{self._counter:06d}"

    def record(self, event: WorldEvent) -> WorldEvent:
        self.events.append(event)
        return event

    def since(self, tick_exclusive: int) -> List[WorldEvent]:
        return [event for event in self.events if event.tick > tick_exclusive]

    def to_dict(self) -> Dict[str, Any]:
        return {"events": [event.to_dict() for event in self.events]}


# Event family -> Radio OS Studio audio intent.
_AUDIO_SIGNATURE_BY_FAMILY = {
    "obligation_risk": "institutional_tension",
    "contract_resolved": "victory_release",
    "resource_loss": "dramatic_hush",
    "thread_opened": "stinger_reveal",
    "thread_heated": "walk_and_talk",
    "thread_cooled": "lonely_afterglow",
    "thread_resolved": "victory_release",
    "prediction_opened": "meanwhile_transition",
    "prediction_settled": "stinger_reveal",
    "operator_input": "institutional_tension",
    "myth_echo": "while_you_were_gone",
}


@dataclass
class TruthDelta:
    """Hot-layer export surface. What the query/broadcast/radio layer consumes."""

    universe_id: str
    from_tick: int
    to_tick: int
    headline: str
    heat: float
    new_events: List[Dict[str, Any]]
    thread_deltas: List[Dict[str, Any]]
    opened_threads: List[Dict[str, Any]]
    resolved_threads: List[Dict[str, Any]]
    settled_predictions: List[Dict[str, Any]]
    opened_predictions: List[Dict[str, Any]]
    prediction_scorecard: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "universe_id": self.universe_id,
            "from_tick": self.from_tick,
            "to_tick": self.to_tick,
            "headline": self.headline,
            "heat": round(self.heat, 4),
            "new_events": self.new_events,
            "thread_deltas": self.thread_deltas,
            "opened_threads": self.opened_threads,
            "resolved_threads": self.resolved_threads,
            "settled_predictions": self.settled_predictions,
            "opened_predictions": self.opened_predictions,
            "prediction_scorecard": self.prediction_scorecard,
        }


# ---------------------------------------------------------------------------
# Authoritative universe state + tick engine
# ---------------------------------------------------------------------------


# Tuning coefficients with engine defaults. The compiled package's `coefficients`
# table and `coefficient_profile` override these per-universe.
_DEFAULT_COEFFICIENTS = {
    "thread_heat_decay": 0.015,
    "prediction_confidence_drift": 0.005,
    "macro_reversion_rate": 0.04,
    "thread_spawn_threshold": 0.72,
    "thread_spawn_chance": 0.08,
    "active_thread_budget": 24,
    "obligation_warn_window": 12,
    "stress_relief_rate": 0.03,
    "entropy_pressure_gain": 0.05,
}

_THREAD_DOMAIN_AXIS = {
    "love": "love_pressure",
    "debt": "debt_pressure",
    "rumor": "rumor_pressure",
    "grief": "grief_pressure",
    "ambition": "ambition_pressure",
}


@dataclass
class UniverseState:
    """The single authoritative runtime model for one Fork Universe.

    Ingest a :class:`CompiledWorldPackage` with :meth:`from_compiled_package`,
    then advance it with :meth:`advance_tick` / :meth:`simulate_epoch` /
    :meth:`compute_absence`, apply operator influence with
    :meth:`apply_operator_input`, and export a hot-layer delta with
    :meth:`emit_truth_delta`.
    """

    universe_id: str
    universe_title: str
    canonical_seed: str
    ruleset_version: str
    time: TimeState
    rng: SeededRNG
    coefficients: Dict[str, float]
    macro_axes: Dict[str, MacroAxis]
    characters: Dict[str, CharacterState]
    relationships: Dict[str, RelationshipState]
    institutions: Dict[str, InstitutionState]
    locations: Dict[str, LocationState]
    obligations: Dict[str, ObligationState]
    threads: Dict[str, StoryThread]
    predictions: PredictionBook
    memory: WorldMemory
    ledger: CausalLedger
    _thread_counter: int = 0
    _prediction_counter: int = 0

    # -- ingestion ---------------------------------------------------------

    @classmethod
    def from_compiled_package(cls, package: CompiledWorldPackage) -> "UniverseState":
        identity = package.package_identity
        tables = package.world_tables
        time_policy = package.time_policy

        time = TimeState(
            world_seconds_per_real_second=float(
                time_policy.get("world_seconds_per_real_second", 60.0)
            ),
            real_seconds_per_tick=float(time_policy.get("real_seconds_to_world_tick", 60)),
        )

        coefficients = dict(_DEFAULT_COEFFICIENTS)
        for row in tables.get("coefficients", []):
            if row.get("scope") == "global":
                name = row.get("name")
                if name in coefficients:
                    coefficients[name] = float(row.get("value", coefficients[name]))

        macro_axes = {
            row["axis_id"]: MacroAxis(
                axis_id=row["axis_id"],
                baseline=float(row.get("baseline", 0.0)),
                current_value=float(row.get("current_value", row.get("baseline", 0.0))),
                normalization_bias=float(row.get("normalization_bias", 0.0)),
                drift_rate=float(row.get("drift_rate", 0.0)),
            )
            for row in tables.get("macro_state", [])
        }

        characters: Dict[str, CharacterState] = {}
        for row in tables.get("characters", []):
            stress_profile = row.get("stress_profile", {}) or {}
            char = CharacterState(
                character_id=row["character_id"],
                display_name=row.get("display_name", row["character_id"]),
                archetype_id=row.get("archetype_id", ""),
                home_location_id=row.get("home_location_id", ""),
                organization_ids=list(row.get("organization_ids", [])),
                trait_vector=dict(row.get("trait_vector", {})),
                resource_state=dict(row.get("resource_state", {})),
                desire_vector=dict(row.get("desire_vector", {})),
                fear_vector=dict(row.get("fear_vector", {})),
                stress_profile=dict(stress_profile),
                stress=float(stress_profile.get("baseline", 0.3)),
            )
            ledger_seed = row.get("ledger_seed", {}) or {}
            char.ledger.myth_tags = list(ledger_seed.get("myth_tags", []))
            char.ledger.promises_made = list(ledger_seed.get("starting_promises", []))
            characters[char.character_id] = char

        relationships = {
            row["relationship_id"]: RelationshipState(
                relationship_id=row["relationship_id"],
                source_character_id=row["source_character_id"],
                target_character_id=row["target_character_id"],
                axes={
                    key: float(value)
                    for key, value in row.items()
                    if key
                    in {
                        "affection",
                        "trust",
                        "dependency",
                        "resentment",
                        "attraction",
                        "loyalty",
                        "fear",
                        "history_depth",
                    }
                },
                concept_tags=list(row.get("concept_tags", [])),
            )
            for row in tables.get("relationships", [])
        }

        institutions = {
            row["organization_id"]: InstitutionState(
                organization_id=row["organization_id"],
                label=row.get("label", row["organization_id"]),
                type=row.get("type", "institution"),
                district_id=row.get("district_id", ""),
                power_score=float(row.get("power_score", 0.5)),
                wealth_score=float(row.get("wealth_score", 0.5)),
                member_ids=list(row.get("member_ids", [])),
            )
            for row in tables.get("organizations", [])
        }

        locations = {
            row["location_id"]: LocationState(
                location_id=row["location_id"],
                label=row.get("label", row["location_id"]),
                location_type=row.get("location_type", "district"),
                economic_heat=float(row.get("economic_heat", 0.5)),
                danger_heat=float(row.get("danger_heat", 0.3)),
                symbolic_weight=float(row.get("symbolic_weight", 0.5)),
            )
            for row in tables.get("locations", [])
        }

        obligations = {
            row["obligation_id"]: ObligationState(
                obligation_id=row["obligation_id"],
                obligation_type=row.get("obligation_type", "obligation"),
                holder_id=row.get("holder_id", ""),
                counterparty_id=row.get("counterparty_id", ""),
                start_tick=int(row.get("start_tick", 0)),
                due_tick=int(row.get("due_tick", 100)),
                stakes=float(row.get("stakes", 0.5)),
                failure_cost=float(row.get("failure_cost", 0.5)),
                success_reward=float(row.get("success_reward", 0.3)),
                status=row.get("status", "active"),
                pressure_tags=list(row.get("pressure_tags", [])),
            )
            for row in tables.get("obligations", [])
        }

        threads: Dict[str, StoryThread] = {}
        thread_counter = 0
        for row in tables.get("story_threads", []):
            thread_counter += 1
            threads[row["thread_id"]] = StoryThread(
                thread_id=row["thread_id"],
                title=row.get("title", "Unnamed thread"),
                domain=row.get("domain", "general"),
                participant_ids=list(row.get("participant_ids", [])),
                status=row.get("status", "active"),
                confidence=float(row.get("confidence", 0.5)),
                urgency=float(row.get("urgency", 0.35)),
                heat=float(row.get("heat", 0.5)),
                opened_tick=0,
                predicted_resolution_tick=int(row.get("predicted_resolution_tick", 100)),
                source_event_ids=list(row.get("source_event_ids", [])),
            )

        prediction_book = PredictionBook()
        prediction_counter = 0
        for row in tables.get("predictions", []):
            prediction_counter += 1
            prediction_book.add(
                Prediction(
                    prediction_id=row["prediction_id"],
                    claim_type=row.get("claim_type", "general_shift"),
                    predictor_id=row.get("predictor_id", "world"),
                    target_type=row.get("target_type", "thread"),
                    target_id=row.get("target_id", ""),
                    thread_id=row.get("thread_id", ""),
                    confidence=float(row.get("confidence", 0.5)),
                    opened_tick=0,
                    horizon_ticks=int(row.get("horizon_ticks", 90)),
                    status=row.get("status", "open"),
                    resolution_outcome=row.get("resolution_outcome"),
                )
            )

        memory = WorldMemory()
        for row in tables.get("memory_records", []):
            memory._counter += 1
            memory.records.append(
                MemoryRecord(
                    memory_id=row.get("memory_id", memory.next_id()),
                    memory_tier=row.get("memory_tier", "recent_history"),
                    owner_type=row.get("owner_type", "world"),
                    owner_id=row.get("owner_id", "world"),
                    summary=row.get("summary", ""),
                    salience=float(row.get("salience", 0.5)),
                    decay_rate=float(row.get("decay_rate", 0.02)),
                    myth_weight=float(row.get("myth_weight", 0.0)),
                    source_event_ids=list(row.get("source_event_ids", [])),
                )
            )

        return cls(
            universe_id=identity.universe_id,
            universe_title=identity.universe_title,
            canonical_seed=identity.canonical_seed,
            ruleset_version=identity.ruleset_version,
            time=time,
            rng=SeededRNG(identity.canonical_seed),
            coefficients=coefficients,
            macro_axes=macro_axes,
            characters=characters,
            relationships=relationships,
            institutions=institutions,
            locations=locations,
            obligations=obligations,
            threads=threads,
            predictions=prediction_book,
            memory=memory,
            ledger=CausalLedger(),
            _thread_counter=thread_counter,
            _prediction_counter=prediction_counter,
        )

    @classmethod
    def from_snapshot(cls, snapshot: Dict[str, Any]) -> "UniverseState":
        """Rebuild authoritative state from :meth:`to_snapshot` output.

        The persisted snapshot *is* the truth once written: reloading reproduces
        the serialized state exactly (``digest`` round-trips), and a reloaded
        universe continues deterministically. Snapshots round-trip through JSON
        losslessly for the persisted precision.
        """

        def _counter_from(ids: List[str]) -> int:
            best = 0
            for identifier in ids:
                tail = identifier.rsplit("_", 1)[-1]
                if tail.isdigit():
                    best = max(best, int(tail))
            return best

        time_row = snapshot.get("time", {})
        time = TimeState(
            tick=int(time_row.get("tick", 0)),
            epoch=int(time_row.get("epoch", 0)),
            world_seconds_per_real_second=float(time_row.get("world_seconds_per_real_second", 60.0)),
            real_seconds_per_tick=float(time_row.get("real_seconds_per_tick", 60.0)),
            last_computed_tick=int(time_row.get("last_computed_tick", 0)),
            mode=time_row.get("mode", "active"),
        )

        macro_axes = {
            row["axis_id"]: MacroAxis(
                axis_id=row["axis_id"],
                baseline=float(row["baseline"]),
                current_value=float(row["current_value"]),
                normalization_bias=float(row.get("normalization_bias", 0.0)),
                drift_rate=float(row.get("drift_rate", 0.0)),
            )
            for row in snapshot.get("macro_axes", [])
        }

        characters: Dict[str, CharacterState] = {}
        for row in snapshot.get("characters", []):
            ledger_row = row.get("ledger", {})
            ledger = CharacterLedger(
                wins=int(ledger_row.get("wins", 0)),
                losses=int(ledger_row.get("losses", 0)),
                promises_made=list(ledger_row.get("promises_made", [])),
                promises_broken=list(ledger_row.get("promises_broken", [])),
                threads_touched=list(ledger_row.get("threads_touched", [])),
                predictions_about=list(ledger_row.get("predictions_about", [])),
                major_event_ids=list(ledger_row.get("major_event_ids", [])),
                myth_tags=list(ledger_row.get("myth_tags", [])),
            )
            characters[row["character_id"]] = CharacterState(
                character_id=row["character_id"],
                display_name=row.get("display_name", row["character_id"]),
                archetype_id=row.get("archetype_id", ""),
                home_location_id=row.get("home_location_id", ""),
                organization_ids=list(row.get("organization_ids", [])),
                trait_vector=dict(row.get("trait_vector", {})),
                resource_state=dict(row.get("resource_state", {})),
                desire_vector=dict(row.get("desire_vector", {})),
                fear_vector=dict(row.get("fear_vector", {})),
                stress_profile=dict(row.get("stress_profile", {})),
                stress=float(row.get("stress", 0.3)),
                ledger=ledger,
            )

        relationships = {
            row["relationship_id"]: RelationshipState(
                relationship_id=row["relationship_id"],
                source_character_id=row["source_character_id"],
                target_character_id=row["target_character_id"],
                axes={key: float(value) for key, value in row.get("axes", {}).items()},
                concept_tags=list(row.get("concept_tags", [])),
            )
            for row in snapshot.get("relationships", [])
        }

        institutions = {
            row["organization_id"]: InstitutionState(
                organization_id=row["organization_id"],
                label=row.get("label", row["organization_id"]),
                type=row.get("type", "institution"),
                district_id=row.get("district_id", ""),
                power_score=float(row.get("power_score", 0.5)),
                wealth_score=float(row.get("wealth_score", 0.5)),
                legitimacy=float(row.get("legitimacy", 0.6)),
                member_ids=list(row.get("member_ids", [])),
            )
            for row in snapshot.get("institutions", [])
        }

        locations = {
            row["location_id"]: LocationState(
                location_id=row["location_id"],
                label=row.get("label", row["location_id"]),
                location_type=row.get("location_type", "district"),
                economic_heat=float(row.get("economic_heat", 0.5)),
                danger_heat=float(row.get("danger_heat", 0.3)),
                symbolic_weight=float(row.get("symbolic_weight", 0.5)),
            )
            for row in snapshot.get("locations", [])
        }

        obligations = {
            row["obligation_id"]: ObligationState(
                obligation_id=row["obligation_id"],
                obligation_type=row.get("obligation_type", "obligation"),
                holder_id=row.get("holder_id", ""),
                counterparty_id=row.get("counterparty_id", ""),
                start_tick=int(row.get("start_tick", 0)),
                due_tick=int(row.get("due_tick", 100)),
                stakes=float(row.get("stakes", 0.5)),
                failure_cost=float(row.get("failure_cost", 0.5)),
                success_reward=float(row.get("success_reward", 0.3)),
                status=row.get("status", "active"),
                pressure_tags=list(row.get("pressure_tags", [])),
            )
            for row in snapshot.get("obligations", [])
        }

        threads = {
            row["thread_id"]: StoryThread(
                thread_id=row["thread_id"],
                title=row.get("title", "Unnamed thread"),
                domain=row.get("domain", "general"),
                participant_ids=list(row.get("participant_ids", [])),
                status=row.get("status", "active"),
                confidence=float(row.get("confidence", 0.5)),
                urgency=float(row.get("urgency", 0.35)),
                heat=float(row.get("heat", 0.5)),
                opened_tick=int(row.get("opened_tick", 0)),
                predicted_resolution_tick=int(row.get("predicted_resolution_tick", 100)),
                resolved_tick=row.get("resolved_tick"),
                resolution_outcome=row.get("resolution_outcome"),
                source_event_ids=list(row.get("source_event_ids", [])),
            )
            for row in snapshot.get("threads", [])
        }

        prediction_book = PredictionBook()
        predictions_row = snapshot.get("predictions", {})
        for row in predictions_row.get("predictions", []):
            calibration = row.get("calibration_error")
            prediction = Prediction(
                prediction_id=row["prediction_id"],
                claim_type=row.get("claim_type", "general_shift"),
                predictor_id=row.get("predictor_id", "world"),
                target_type=row.get("target_type", "thread"),
                target_id=row.get("target_id", ""),
                thread_id=row.get("thread_id", ""),
                confidence=float(row.get("confidence", 0.5)),
                opened_tick=int(row.get("opened_tick", 0)),
                horizon_ticks=int(row.get("horizon_ticks", 90)),
                status=row.get("status", "open"),
                resolution_outcome=row.get("resolution_outcome"),
                calibration_error=float(calibration) if calibration is not None else None,
            )
            prediction_book.add(prediction)
        prediction_book.settled_hits = int(predictions_row.get("settled_hits", 0))
        prediction_book.settled_misses = int(predictions_row.get("settled_misses", 0))
        prediction_book.calibration_error_total = sum(
            p.calibration_error
            for p in prediction_book.predictions.values()
            if p.calibration_error is not None
        )

        memory = WorldMemory()
        for row in snapshot.get("memory", {}).get("records", []):
            memory.records.append(
                MemoryRecord(
                    memory_id=row["memory_id"],
                    memory_tier=row.get("memory_tier", "recent_history"),
                    owner_type=row.get("owner_type", "world"),
                    owner_id=row.get("owner_id", "world"),
                    summary=row.get("summary", ""),
                    salience=float(row.get("salience", 0.5)),
                    decay_rate=float(row.get("decay_rate", 0.02)),
                    myth_weight=float(row.get("myth_weight", 0.0)),
                    source_event_ids=list(row.get("source_event_ids", [])),
                )
            )
        memory._counter = _counter_from([r.memory_id for r in memory.records])

        ledger = CausalLedger()
        for row in snapshot.get("ledger", {}).get("events", []):
            ledger.events.append(
                WorldEvent(
                    event_id=row["event_id"],
                    tick=int(row.get("tick", 0)),
                    family=row.get("family", "world"),
                    event_type=row.get("event_type", "world"),
                    summary=row.get("summary", ""),
                    subject_ids=list(row.get("subject_ids", [])),
                    location_id=row.get("location_id", ""),
                    changes=dict(row.get("changes", {})),
                    pressure_delta=float(row.get("pressure_delta", 0.0)),
                    audio_signature=row.get("audio_signature", "meanwhile_transition"),
                )
            )
        ledger._counter = _counter_from([e.event_id for e in ledger.events])

        return cls(
            universe_id=snapshot["universe_id"],
            universe_title=snapshot.get("universe_title", snapshot["universe_id"]),
            canonical_seed=snapshot["canonical_seed"],
            ruleset_version=snapshot.get("ruleset_version", "v1"),
            time=time,
            rng=SeededRNG(snapshot["canonical_seed"]),
            coefficients=dict(_DEFAULT_COEFFICIENTS, **snapshot.get("coefficients", {})),
            macro_axes=macro_axes,
            characters=characters,
            relationships=relationships,
            institutions=institutions,
            locations=locations,
            obligations=obligations,
            threads=threads,
            predictions=prediction_book,
            memory=memory,
            ledger=ledger,
            _thread_counter=_counter_from(list(threads.keys())),
            _prediction_counter=_counter_from(list(prediction_book.predictions.keys())),
        )

    def save_json(self, path: "str | Any") -> None:
        """Persist all six layers (world_state, event_ledger, character_ledger,
        story_memory, thread_book, prediction_book) to a single JSON document."""
        from pathlib import Path

        Path(path).write_text(
            json.dumps(self.to_snapshot(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load_json(cls, path: "str | Any") -> "UniverseState":
        from pathlib import Path

        return cls.from_snapshot(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- helpers -----------------------------------------------------------

    def coefficient(self, name: str) -> float:
        return float(self.coefficients.get(name, _DEFAULT_COEFFICIENTS.get(name, 0.0)))

    def _axis_for_domain(self, domain: str) -> Optional[MacroAxis]:
        axis_id = _THREAD_DOMAIN_AXIS.get(domain)
        if axis_id and axis_id in self.macro_axes:
            return self.macro_axes[axis_id]
        return self.macro_axes.get("social_pressure")

    def _emit(
        self,
        tick: int,
        family: str,
        event_type: str,
        summary: str,
        *,
        subject_ids: Optional[List[str]] = None,
        location_id: str = "",
        changes: Optional[Dict[str, Any]] = None,
        pressure_delta: float = 0.0,
    ) -> WorldEvent:
        event = WorldEvent(
            event_id=self.ledger.next_id(),
            tick=tick,
            family=family,
            event_type=event_type,
            summary=summary,
            subject_ids=list(subject_ids or []),
            location_id=location_id,
            changes=dict(changes or {}),
            pressure_delta=pressure_delta,
            audio_signature=_AUDIO_SIGNATURE_BY_FAMILY.get(family, "meanwhile_transition"),
        )
        # Attribute the event to participating characters' ledgers.
        for subject_id in event.subject_ids:
            char = self.characters.get(subject_id)
            if char is not None and pressure_delta != 0.0:
                char.ledger.major_event_ids.append(event.event_id)
        return self.ledger.record(event)

    def _new_thread_id(self) -> str:
        self._thread_counter += 1
        return f"thr_{self._thread_counter:04d}"

    def _new_prediction_id(self) -> str:
        self._prediction_counter += 1
        return f"pred_{self._prediction_counter:04d}"

    # -- tick subsystems ---------------------------------------------------

    def _advance_macro(self, tick: int) -> None:
        reversion = self.coefficient("macro_reversion_rate")
        for axis in self.macro_axes.values():
            target = axis.baseline + axis.normalization_bias
            axis.current_value += (target - axis.current_value) * reversion
            axis.current_value += axis.drift_rate * 0.25
            axis.current_value = _clamp(axis.current_value)

    def _advance_obligations(self, tick: int) -> None:
        warn_window = int(self.coefficient("obligation_warn_window"))
        rng = self.rng.stream("obligations", tick)
        for obligation in self.obligations.values():
            if obligation.status in {"met", "breached"}:
                continue
            ticks_remaining = obligation.due_tick - tick
            if 0 < ticks_remaining <= warn_window and obligation.status == "active":
                obligation.status = "warned"
                self._emit(
                    tick,
                    "obligation_risk",
                    "contract_warning",
                    f"A {obligation.obligation_type.replace('_', ' ')} comes due soon.",
                    subject_ids=[obligation.holder_id],
                    changes={"obligation_id": obligation.obligation_id, "due_tick": obligation.due_tick},
                    pressure_delta=round(obligation.stakes * 0.3, 4),
                )
            if ticks_remaining <= 0:
                self._resolve_obligation(tick, obligation, rng)

    def _resolve_obligation(self, tick: int, obligation: ObligationState, rng: random.Random) -> None:
        holder = self.characters.get(obligation.holder_id)
        capacity = 0.5
        if holder is not None:
            cash = float(holder.resource_state.get("cash", 0.0))
            housing = float(holder.resource_state.get("housing_security", 0.5))
            capacity = _clamp(0.35 + min(cash / 8000.0, 0.4) + housing * 0.25 - holder.stress * 0.2)
        success_p = _clamp(capacity * (1.0 - obligation.stakes * 0.5) + 0.1)
        if rng.random() <= success_p:
            obligation.status = "met"
            if holder is not None:
                holder.ledger.wins += 1
                holder.stress = _clamp(holder.stress - obligation.success_reward * 0.2)
            self._emit(
                tick,
                "contract_resolved",
                "contract_resolved",
                f"A {obligation.obligation_type.replace('_', ' ')} was honored.",
                subject_ids=[obligation.holder_id],
                changes={"obligation_id": obligation.obligation_id, "outcome": "met"},
                pressure_delta=round(-obligation.stakes * 0.2, 4),
            )
        else:
            obligation.status = "breached"
            if holder is not None:
                holder.ledger.losses += 1
                holder.ledger.promises_broken.append(obligation.obligation_id)
                holder.stress = _clamp(holder.stress + obligation.failure_cost * 0.3)
                holder.resource_state["cash"] = max(
                    0.0, float(holder.resource_state.get("cash", 0.0)) - obligation.failure_cost * 2000.0
                )
            event = self._emit(
                tick,
                "resource_loss",
                "obligation_breach",
                f"A {obligation.obligation_type.replace('_', ' ')} was broken.",
                subject_ids=[obligation.holder_id],
                changes={"obligation_id": obligation.obligation_id, "outcome": "breached"},
                pressure_delta=round(obligation.failure_cost * 0.5, 4),
            )
            # Entropy opens a thread.
            self._spawn_thread(
                tick,
                domain="debt" if "debt" in obligation.pressure_tags else "obligation",
                title=f"Will the fallout from a broken {obligation.obligation_type.replace('_', ' ')} spread?",
                participant_ids=[obligation.holder_id, obligation.counterparty_id],
                heat=_clamp(0.55 + obligation.failure_cost * 0.3),
                source_event_id=event.event_id,
            )
            entropy = self.macro_axes.get("entropy_pressure")
            if entropy is not None:
                entropy.current_value = _clamp(
                    entropy.current_value + self.coefficient("entropy_pressure_gain")
                )

    def _advance_threads(self, tick: int) -> None:
        decay = self.coefficient("thread_heat_decay")
        rng = self.rng.stream("threads", tick)
        for thread in list(self.threads.values()):
            if thread.status in {"resolved", "mythologized"}:
                continue
            axis = self._axis_for_domain(thread.domain)
            pressure = axis.current_value if axis is not None else 0.4
            prior_heat = thread.heat
            # Heat drifts down naturally, up under matching macro pressure.
            thread.heat = _clamp(thread.heat - decay + (pressure - 0.5) * 0.06)
            # Urgency climbs as the predicted resolution approaches.
            span = max(1, thread.predicted_resolution_tick - thread.opened_tick)
            progress = _clamp((tick - thread.opened_tick) / span)
            thread.urgency = _clamp(0.2 + progress * 0.7 + (pressure - 0.5) * 0.1)

            if thread.heat - prior_heat > 0.04 and thread.status != "heated":
                thread.status = "heated"
                self._emit(
                    tick,
                    "thread_heated",
                    "thread_heated",
                    f"{thread.title} is gaining momentum.",
                    subject_ids=list(thread.participant_ids),
                    changes={"thread_id": thread.thread_id, "heat": round(thread.heat, 4)},
                    pressure_delta=round(thread.heat - prior_heat, 4),
                )
            elif prior_heat - thread.heat > 0.04 and thread.status == "heated":
                thread.status = "cooled"
                self._emit(
                    tick,
                    "thread_cooled",
                    "thread_cooled",
                    f"{thread.title} is losing momentum.",
                    subject_ids=list(thread.participant_ids),
                    changes={"thread_id": thread.thread_id, "heat": round(thread.heat, 4)},
                    pressure_delta=round(thread.heat - prior_heat, 4),
                )

            if tick >= thread.predicted_resolution_tick:
                self._resolve_thread(tick, thread, rng)

    def _resolve_thread(self, tick: int, thread: StoryThread, rng: random.Random) -> None:
        affirm_p = _clamp(thread.confidence * 0.6 + thread.heat * 0.3)
        roll = rng.random()
        if thread.heat < 0.25:
            outcome = "faded"
        elif roll <= affirm_p:
            outcome = "affirmed"
        else:
            outcome = "broken"
        thread.status = "resolved"
        thread.resolved_tick = tick
        thread.resolution_outcome = outcome
        for participant_id in thread.participant_ids:
            char = self.characters.get(participant_id)
            if char is not None:
                char.ledger.threads_touched.append(thread.thread_id)
        event = self._emit(
            tick,
            "thread_resolved",
            "thread_resolved",
            f"{thread.title} resolved: {outcome}.",
            subject_ids=list(thread.participant_ids),
            changes={"thread_id": thread.thread_id, "outcome": outcome},
            pressure_delta=round(thread.heat * 0.4, 4),
        )
        # High-heat resolutions become mythology.
        if thread.heat >= 0.7 or outcome == "broken":
            thread.status = "mythologized"
            self.memory.remember(
                summary=f"The world still talks about how {thread.title.lower()}",
                salience=_clamp(0.6 + thread.heat * 0.3),
                myth_weight=_clamp(0.4 + thread.heat * 0.4),
                source_event_ids=[event.event_id],
            )
            for participant_id in thread.participant_ids:
                char = self.characters.get(participant_id)
                if char is not None and thread.domain not in char.ledger.myth_tags:
                    char.ledger.myth_tags.append(thread.domain)

    def _spawn_thread(
        self,
        tick: int,
        *,
        domain: str,
        title: str,
        participant_ids: List[str],
        heat: float,
        source_event_id: str = "",
    ) -> StoryThread:
        thread = StoryThread(
            thread_id=self._new_thread_id(),
            title=title,
            domain=domain,
            participant_ids=list(participant_ids),
            status="active",
            confidence=_clamp(0.45 + heat * 0.2),
            urgency=0.3,
            heat=_clamp(heat),
            opened_tick=tick,
            predicted_resolution_tick=tick + 60 + (self._thread_counter % 24),
            source_event_ids=[source_event_id] if source_event_id else [],
        )
        self.threads[thread.thread_id] = thread
        self._emit(
            tick,
            "thread_opened",
            "thread_opened",
            f"New open question: {title}",
            subject_ids=list(participant_ids),
            changes={"thread_id": thread.thread_id, "domain": domain},
            pressure_delta=round(heat * 0.3, 4),
        )
        # A new thread invites a forward prediction.
        self._open_prediction_for_thread(tick, thread)
        return thread

    def active_thread_count(self) -> int:
        return sum(
            1
            for thread in self.threads.values()
            if thread.status in {"active", "heated", "cooled"}
        )

    def spawn_threads_from_pressure(self, tick: int) -> None:
        threshold = self.coefficient("thread_spawn_threshold")
        chance = self.coefficient("thread_spawn_chance")
        budget = int(self.coefficient("active_thread_budget"))
        rng = self.rng.stream("spawn", tick)
        # Ambient pressure spawning respects the broadcast quota: once the active
        # thread set is at budget, the world stops manufacturing new open
        # questions from pressure alone. Causal consequences (e.g. an obligation
        # breach) still open threads -- those route through _spawn_thread directly.
        for axis in self.macro_axes.values():
            if self.active_thread_count() >= budget:
                break
            if not axis.axis_id.endswith("_pressure"):
                continue
            if axis.current_value < threshold:
                continue
            if rng.random() > chance:
                continue
            domain = axis.axis_id[: -len("_pressure")]
            participants = self._pick_participants(rng, count=2)
            self._spawn_thread(
                tick,
                domain=domain,
                title=f"Will rising {domain.replace('_', ' ')} pressure force a public reckoning?",
                participant_ids=participants,
                heat=_clamp(axis.current_value),
            )

    def _pick_participants(self, rng: random.Random, count: int = 2) -> List[str]:
        ids = sorted(self.characters.keys())
        if not ids:
            return []
        rng.shuffle(ids)
        return ids[: min(count, len(ids))]

    def _open_prediction_for_thread(self, tick: int, thread: StoryThread) -> Prediction:
        prediction = Prediction(
            prediction_id=self._new_prediction_id(),
            claim_type=f"{thread.domain}_resolution",
            predictor_id="world",
            target_type="thread",
            target_id=thread.thread_id,
            thread_id=thread.thread_id,
            confidence=_clamp(0.4 + thread.heat * 0.3),
            opened_tick=tick,
            horizon_ticks=max(12, thread.predicted_resolution_tick - tick),
            status="open",
        )
        self.predictions.add(prediction)
        self._emit(
            tick,
            "prediction_opened",
            "prediction_opened",
            f"The world expects {thread.title.lower()}",
            subject_ids=list(thread.participant_ids),
            changes={"prediction_id": prediction.prediction_id, "thread_id": thread.thread_id},
        )
        return prediction

    def resolve_predictions(self, tick: int) -> None:
        drift = self.coefficient("prediction_confidence_drift")
        rng = self.rng.stream("predictions", tick)
        for prediction in self.predictions.open_items():
            # Confidence drifts toward uncertainty before settlement.
            prediction.confidence = _clamp(prediction.confidence - drift, 0.05, 0.95)
            if tick < prediction.resolves_tick:
                continue
            outcome, truth_value = self._score_prediction(prediction, rng)
            prediction.status = "resolved"
            prediction.resolution_outcome = outcome
            error = (prediction.confidence - truth_value) ** 2
            prediction.calibration_error = error
            self.predictions.calibration_error_total += error
            if outcome == "hit":
                self.predictions.settled_hits += 1
            else:
                self.predictions.settled_misses += 1
            # Calibration feeds institutional legitimacy / world self-belief.
            self._apply_prediction_feedback(outcome)
            self._emit(
                tick,
                "prediction_settled",
                "prediction_settled",
                f"A forward call settled: {outcome}.",
                changes={
                    "prediction_id": prediction.prediction_id,
                    "outcome": outcome,
                    "calibration_error": round(error, 4),
                },
                pressure_delta=round((1.0 - truth_value) * 0.2, 4),
            )

    def _score_prediction(self, prediction: Prediction, rng: random.Random) -> Tuple[str, float]:
        thread = self.threads.get(prediction.thread_id)
        if thread is not None and thread.status in {"resolved", "mythologized"}:
            truth_value = 1.0 if thread.resolution_outcome == "affirmed" else 0.0
        else:
            # Thread still open at horizon: the world's expectation went unmet in time.
            truth_value = 1.0 if rng.random() <= prediction.confidence else 0.0
        # A "hit" means the realized truth agreed with the confident direction.
        predicted_true = prediction.confidence >= 0.5
        outcome = "hit" if (truth_value >= 0.5) == predicted_true else "miss"
        return outcome, truth_value

    def _apply_prediction_feedback(self, outcome: str) -> None:
        legitimacy_axis = self.macro_axes.get("institutional_pressure")
        delta = 0.01 if outcome == "hit" else -0.015
        for institution in self.institutions.values():
            institution.legitimacy = _clamp(institution.legitimacy + delta)
        if legitimacy_axis is not None:
            legitimacy_axis.current_value = _clamp(legitimacy_axis.current_value - delta)

    def decay_or_promote_memory(self, tick: int) -> None:
        survivors: List[MemoryRecord] = []
        order = WorldMemory.TIER_ORDER
        for record in self.memory.records:
            record.salience = _clamp(record.salience - record.decay_rate)
            # Strongly mythic memories climb tiers; faint ones sink and fall away.
            if record.myth_weight >= 0.6:
                idx = order.index(record.memory_tier) if record.memory_tier in order else 1
                record.memory_tier = order[min(idx + 1, len(order) - 1)]
                record.salience = _clamp(record.salience + 0.05)
            elif record.salience <= 0.05 and record.myth_weight < 0.2:
                # Forgotten: dropped from active memory.
                continue
            survivors.append(record)
        self.memory.records = survivors

    # -- the tick seam -----------------------------------------------------

    def advance_tick(self) -> int:
        """Advance the world by exactly one tick. The atomic computation step."""
        self.time.tick += 1
        tick = self.time.tick
        self._advance_macro(tick)
        self._advance_obligations(tick)
        self._advance_threads(tick)
        self.spawn_threads_from_pressure(tick)
        self.resolve_predictions(tick)
        self.decay_or_promote_memory(tick)
        self.time.last_computed_tick = tick
        return tick

    def simulate_epoch(self, ticks: int) -> int:
        """Advance many ticks. Equivalent to that many single advances."""
        for _ in range(max(0, ticks)):
            self.advance_tick()
        return self.time.tick

    def compute_absence(self, elapsed_real_seconds: float) -> TruthDelta:
        """While-you-were-gone reconstruction. Convert elapsed real time into owed
        ticks, advance, and return the delta covering the absence."""
        from_tick = self.time.tick
        owed = self.time.ticks_owed(elapsed_real_seconds)
        if owed > 1:
            self.time.mode = "deep_sleep"
            self.time.epoch += 1
        self.simulate_epoch(owed)
        self.time.mode = "active"
        return self.emit_truth_delta(from_tick)

    # -- operator input seam ----------------------------------------------

    def apply_operator_input(
        self,
        *,
        intent: str,
        magnitude: float = 0.5,
        target_domain: str = "social",
        note: str = "",
    ) -> WorldEvent:
        """Interpreted decree / influence (Oracle Kingdom ripple model).

        The operator does not issue hard commands; they apply pressure that the
        world reinterprets. Intent nudges a macro axis, which then shapes which
        threads heat and which predictions the world forms on subsequent ticks.
        """
        tick = self.time.tick
        magnitude = _clamp(magnitude, -1.0, 1.0)
        axis = self.macro_axes.get(f"{target_domain}_pressure") or self.macro_axes.get(
            "social_pressure"
        )
        signed = magnitude if intent in {"amplify", "provoke", "escalate"} else -magnitude
        if axis is not None:
            axis.current_value = _clamp(axis.current_value + signed * 0.2)
            axis.normalization_bias = _clamp(
                axis.normalization_bias + signed * 0.05, -0.5, 0.5
            )
        return self._emit(
            tick,
            "operator_input",
            "operator_input",
            note or f"Operator applied {intent} pressure to {target_domain}.",
            changes={"intent": intent, "magnitude": magnitude, "target_domain": target_domain},
            pressure_delta=round(signed * 0.2, 4),
        )

    # -- hot-layer export --------------------------------------------------

    def emit_truth_delta(self, from_tick: int) -> TruthDelta:
        """Export everything that changed after ``from_tick`` for the query /
        broadcast / Radio OS layer to consume. The hot layer interprets these
        facts; it never invents new ones."""
        new_events = self.ledger.since(from_tick)
        opened_threads = [
            t for t in self.threads.values() if t.opened_tick > from_tick
        ]
        resolved_threads = [
            t
            for t in self.threads.values()
            if t.resolved_tick is not None and t.resolved_tick > from_tick
        ]
        active_threads = sorted(
            (t for t in self.threads.values() if t.status in {"active", "heated", "cooled"}),
            key=lambda t: (t.heat, t.urgency),
            reverse=True,
        )
        settled = [
            p
            for p in self.predictions.predictions.values()
            if p.status == "resolved" and p.opened_tick <= from_tick < p.resolves_tick
        ]
        opened_predictions = [
            p for p in self.predictions.predictions.values() if p.opened_tick > from_tick
        ]

        if resolved_threads:
            top = max(resolved_threads, key=lambda t: t.heat)
            headline = f"{top.title} ({top.resolution_outcome})"
            heat = top.heat
        elif active_threads:
            headline = active_threads[0].title
            heat = active_threads[0].heat
        else:
            headline = "The world is quiet for now."
            heat = 0.0

        return TruthDelta(
            universe_id=self.universe_id,
            from_tick=from_tick,
            to_tick=self.time.tick,
            headline=headline,
            heat=heat,
            new_events=[event.to_dict() for event in new_events],
            thread_deltas=[t.to_dict() for t in active_threads[:8]],
            opened_threads=[t.to_dict() for t in opened_threads],
            resolved_threads=[t.to_dict() for t in resolved_threads],
            settled_predictions=[p.to_dict() for p in settled],
            opened_predictions=[p.to_dict() for p in opened_predictions],
            prediction_scorecard=self.predictions.to_dict(),
        )

    # -- serialization / determinism --------------------------------------

    def to_snapshot(self) -> Dict[str, Any]:
        return {
            "universe_id": self.universe_id,
            "universe_title": self.universe_title,
            "canonical_seed": self.canonical_seed,
            "ruleset_version": self.ruleset_version,
            "time": self.time.to_dict(),
            "coefficients": {k: round(v, 6) for k, v in sorted(self.coefficients.items())},
            "macro_axes": [a.to_dict() for a in sorted(self.macro_axes.values(), key=lambda a: a.axis_id)],
            "characters": [c.to_dict() for c in sorted(self.characters.values(), key=lambda c: c.character_id)],
            "relationships": [r.to_dict() for r in sorted(self.relationships.values(), key=lambda r: r.relationship_id)],
            "institutions": [i.to_dict() for i in sorted(self.institutions.values(), key=lambda i: i.organization_id)],
            "locations": [l.to_dict() for l in sorted(self.locations.values(), key=lambda l: l.location_id)],
            "obligations": [o.to_dict() for o in sorted(self.obligations.values(), key=lambda o: o.obligation_id)],
            "threads": [t.to_dict() for t in sorted(self.threads.values(), key=lambda t: t.thread_id)],
            "predictions": self.predictions.to_dict(),
            "memory": self.memory.to_dict(),
            "ledger": self.ledger.to_dict(),
        }

    def digest(self) -> str:
        return _stable_digest(self.to_snapshot())


def load_universe(package: CompiledWorldPackage) -> UniverseState:
    """Convenience entry point: compiled package -> live runtime state."""
    return UniverseState.from_compiled_package(package)
