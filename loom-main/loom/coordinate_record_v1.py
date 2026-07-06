"""Canonical coordinate record for one concept on one overlay."""

from __future__ import annotations

import json
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class CoordinateRecord:
    concept_id: str
    title: str
    x_region: str
    x_index: int
    y_overlay: str
    y_index: int
    value: float
    checksum: str
    evidence_pointer: str = ""
    baseline_score: float = 0.0
    source_score: float = 0.0
    tags: tuple[str, ...] = field(default_factory=tuple)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        return payload


def stable_checksum(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"{zlib.crc32(encoded) & 0xFFFFFFFF:08x}"


def build_coordinate_record(
    *,
    concept_id: str,
    title: str,
    x_region: str,
    x_index: int,
    y_overlay: str,
    y_index: int,
    value: float,
    evidence_pointer: str = "",
    baseline_score: float = 0.0,
    source_score: float = 0.0,
    tags: List[str] | None = None,
    meta: Dict[str, Any] | None = None,
) -> CoordinateRecord:
    clean_value = max(0.0, min(1.0, float(value)))
    payload = {
        "concept_id": concept_id,
        "title": title,
        "x_region": x_region,
        "x_index": int(x_index),
        "y_overlay": y_overlay,
        "y_index": int(y_index),
        "value": round(clean_value, 6),
        "evidence_pointer": evidence_pointer,
        "baseline_score": round(float(baseline_score), 6),
        "source_score": round(float(source_score), 6),
        "tags": list(tags or []),
        "meta": meta or {},
    }
    checksum = stable_checksum(payload)
    return CoordinateRecord(checksum=checksum, **payload)
