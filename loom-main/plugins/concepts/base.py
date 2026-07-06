"""Core contract for Loom/OpenClaw concept bricks.

This is intentionally small and stdlib-only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


CONCEPT_API_VERSION = "loom.concept.v1"


@dataclass
class ConceptManifest:
    api_version: str = CONCEPT_API_VERSION
    id: str = ""
    kind: str = ""
    version: str = "0.1.0"
    deterministic: bool = True
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)
    provides: List[str] = field(default_factory=list)
    side_effects: List[str] = field(default_factory=list)
    ui_slots: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    description: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PacketEnvelope:
    packet_type: str
    packet_version: str
    payload: Dict[str, Any]
    trace_id: str = ""
    parent_trace_id: str = ""
    refs: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationIssue:
    code: str
    message: str
    field: str = ""
    severity: str = "error"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Receipt:
    receipt_id: str
    brick_id: str
    kind: str
    label: str
    refs: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BrickResult:
    ok: bool
    output_packet: PacketEnvelope | None = None
    receipts: List[Receipt] = field(default_factory=list)
    issues: List[ValidationIssue] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "output_packet": self.output_packet.as_dict() if self.output_packet else None,
            "receipts": [item.as_dict() for item in self.receipts],
            "issues": [item.as_dict() for item in self.issues],
            "meta": dict(self.meta),
        }


def validate_manifest(manifest: ConceptManifest) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if manifest.api_version != CONCEPT_API_VERSION:
        issues.append(
            ValidationIssue(
                code="bad_api_version",
                message=f"expected api_version={CONCEPT_API_VERSION}",
                field="api_version",
            )
        )
    if not manifest.id or manifest.id.count(".") < 2:
        issues.append(
            ValidationIssue(
                code="bad_id",
                message="concept id should look like loom.family.name",
                field="id",
            )
        )
    if not manifest.kind:
        issues.append(
            ValidationIssue(
                code="missing_kind",
                message="kind is required",
                field="kind",
            )
        )
    if not manifest.inputs:
        issues.append(
            ValidationIssue(
                code="missing_inputs",
                message="at least one input packet type is required",
                field="inputs",
            )
        )
    if not manifest.outputs:
        issues.append(
            ValidationIssue(
                code="missing_outputs",
                message="at least one output packet type is required",
                field="outputs",
            )
        )
    return issues
