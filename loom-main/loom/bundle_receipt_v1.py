"""Receipts for bundle-level map assembly."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class BundleReceipt:
    bundle_id: str
    title: str
    x_region: str
    bundle_overlay: str
    slot_scores: Dict[str, float]
    artifact_refs: Dict[str, str]
    notes: tuple[str, ...]
    nesting_ready: bool = True

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["notes"] = list(self.notes)
        return payload


def build_bundle_receipt(
    *,
    bundle_id: str,
    title: str,
    x_region: str,
    bundle_overlay: str,
    slot_scores: Dict[str, float],
    artifact_refs: Dict[str, str],
    notes: List[str] | None = None,
) -> BundleReceipt:
    return BundleReceipt(
        bundle_id=bundle_id,
        title=title,
        x_region=x_region,
        bundle_overlay=bundle_overlay,
        slot_scores={key: round(float(value), 6) for key, value in sorted(slot_scores.items())},
        artifact_refs=dict(sorted(artifact_refs.items())),
        notes=tuple(notes or []),
        nesting_ready=True,
    )
