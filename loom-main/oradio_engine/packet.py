"""Canonical Loom query packet contract.

The packet is the handoff between the deterministic reasoner and any renderer/auditor.
It stays stdlib-only, JSON-serializable, and explicit about codec/version choices.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


PACKET_VERSION = "loom.answer.packet.v2"
CODEC_VERSION = "loom.timestamp.codec.v1"


@dataclass
class SourceRow:
    ref: str
    tape_id: str
    row_index: int
    actor: str
    action: str
    object: str
    lap: int
    priority: float
    valence: str
    source: str = ""
    source_domain: str = ""
    headline: str = ""
    thread: str = ""
    topic: str = ""
    time: str = ""
    kind: str = "event"
    source_kind: str = "source"
    tags: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceHit:
    ref: str
    row_ref: str
    score: float
    token_score: float
    priority_score: float
    chronology_score: float
    source_score: float
    matched_terms: List[str] = field(default_factory=list)
    native: bool = True
    clause: str = ""
    source_row: SourceRow | None = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QuestionTapeRow:
    ref: str
    actor: str
    action: str
    object: str
    query: str
    transform: str
    focus: str
    relation: float
    meaning: float
    confidence: float
    evidence_refs: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerTapeRow:
    ref: str
    actor: str
    action: str
    subject: str
    transform: str
    claim: str
    object: str
    valence: str
    confidence: float
    evidence_refs: List[str] = field(default_factory=list)
    relation_clause: str = ""
    boundary_clause: str = ""
    implication_clause: str = ""
    method: str = "deterministic_query_codec"
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RenderIntent:
    mode: str = "renderer_first"
    voice: str = "intern"
    playback: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CodecManifest:
    codec_version: str
    artifact_kind: str
    alphabet: str
    punctuation_map: Dict[str, int]
    style_presets: Dict[str, Dict[str, Any]]
    timing: Dict[str, Any]
    checksum: Dict[str, Any]
    visual_reserved: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RouteTraceStep:
    step_kind: str
    ref: str
    label: str
    score: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConceptCitation:
    citation_id: str
    label: str
    reason: str
    score: float
    route_refs: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceCitation:
    citation_id: str
    claim_glob: str
    evidence_refs: List[str] = field(default_factory=list)
    trail: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SynthesisPlan:
    template_id: str = ""
    renderer_voice: str = ""
    filled_slots: Dict[str, Any] = field(default_factory=dict)
    boundary_statement: str = ""
    citation_bindings: Dict[str, List[str]] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerPacket:
    packet_version: str
    codec_version: str
    query: str
    source_rows: List[SourceRow]
    question_tape: List[QuestionTapeRow]
    evidence_hits: List[EvidenceHit]
    answer_tape: List[AnswerTapeRow]
    render_intent: RenderIntent
    codec_manifest: CodecManifest
    synthesis: SynthesisPlan = field(default_factory=SynthesisPlan)
    concept_citations: List[ConceptCitation] = field(default_factory=list)
    evidence_citations: List[EvidenceCitation] = field(default_factory=list)
    route_trace: List[RouteTraceStep] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)
