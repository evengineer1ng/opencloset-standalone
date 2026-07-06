"""Template concept brick for Loom/OpenClaw.

Copy this file to start a new brick.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import (
    BrickResult,
    ConceptManifest,
    PacketEnvelope,
    Receipt,
    ValidationIssue,
    validate_manifest,
)


CONCEPT = ConceptManifest(
    id="loom.example.template",
    kind="renderer",
    version="0.1.0",
    deterministic=True,
    inputs=["loom.example.input.v1"],
    outputs=["loom.example.output.v1"],
    requires=[],
    provides=["example.template"],
    side_effects=[],
    ui_slots=[],
    tags=["template"],
    description="Template brick. Replace with the real concept.",
)


def inspect() -> Dict[str, Any]:
    return CONCEPT.as_dict()


def validate(input_packet: PacketEnvelope, context: Dict[str, Any]) -> List[ValidationIssue]:
    issues = validate_manifest(CONCEPT)
    if input_packet.packet_type not in CONCEPT.inputs:
        issues.append(
            ValidationIssue(
                code="wrong_packet_type",
                message=f"expected one of {CONCEPT.inputs}, got {input_packet.packet_type}",
                field="packet_type",
            )
        )
    if not isinstance(input_packet.payload, dict):
        issues.append(
            ValidationIssue(
                code="bad_payload",
                message="payload must be a dict",
                field="payload",
            )
        )
    return issues


def run(input_packet: PacketEnvelope, context: Dict[str, Any]) -> BrickResult:
    issues = validate(input_packet, context)
    if issues:
        return BrickResult(ok=False, issues=issues)

    output_payload = {
        "echo": dict(input_packet.payload),
        "handled_by": CONCEPT.id,
    }
    output_packet = PacketEnvelope(
        packet_type=CONCEPT.outputs[0],
        packet_version=CONCEPT.outputs[0],
        trace_id=input_packet.trace_id,
        parent_trace_id=input_packet.parent_trace_id or input_packet.trace_id,
        payload=output_payload,
        refs=list(input_packet.refs),
        meta={"brick_id": CONCEPT.id},
    )
    return BrickResult(
        ok=True,
        output_packet=output_packet,
        receipts=receipts(output_packet),
    )


def receipts(output_packet: PacketEnvelope) -> List[Receipt]:
    return [
        Receipt(
            receipt_id="template-run-001",
            brick_id=CONCEPT.id,
            kind="run_summary",
            label="template brick emitted an output packet",
            refs=[output_packet.packet_type],
            data={
                "packet_version": output_packet.packet_version,
                "payload_keys": sorted(output_packet.payload.keys()),
            },
        )
    ]
