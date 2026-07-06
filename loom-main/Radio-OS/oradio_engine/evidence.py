"""The evidence / calibration service — ATL's foundational contribution, generalized.

"Surface evidence programmatically and scientifically" = the prediction -> resolution ->
**calibration** loop (benchmark axis #6, the honesty engine). A world that forms claims and
is *scored* on them is a simulation; one that doesn't is a dashboard.

This service is organ-agnostic. It normalizes any organ's prediction rows to one shape and
tracks open/resolved claims + running calibration (hit-rate, Brier score, mean calibration
error). ForkUniverse already emits real predictions with exactly these fields, so this is
built and tested against a runnable organ; ATL later becomes a richer *provider* of the same
contract (genome scores, promotions, research questions as gradable claims).

The normalized prediction shape mirrors ForkUniverse's ``Prediction`` (the reference):
``confidence`` in [0,1], ``status`` open|resolved, ``outcome`` hit|miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Prediction:
    """A normalized forward claim and its (optional) settlement."""

    pred_id: str
    source: str
    claim_type: str
    confidence: float
    opened_tick: int
    resolves_tick: Optional[int]
    status: str  # open | resolved
    outcome: Optional[str]  # hit | miss | None
    calibration_error: Optional[float]

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved" or self.outcome in ("hit", "miss")


def normalize_prediction(source: str, tick: int, index: int, row: Dict[str, Any]) -> Prediction:
    pid = str(row.get("prediction_id") or f"{source}:{row.get('opened_tick', tick)}:{index}")
    outcome = row.get("resolution_outcome") or row.get("outcome")
    status = row.get("status", "open")
    if outcome in ("hit", "miss"):
        status = "resolved"
    return Prediction(
        pred_id=pid,
        source=source,
        claim_type=str(row.get("claim_type", row.get("type", "claim"))),
        confidence=float(row.get("confidence", 0.5)),
        opened_tick=int(row.get("opened_tick", tick)),
        resolves_tick=row.get("resolves_tick"),
        status=status,
        outcome=outcome,
        calibration_error=row.get("calibration_error"),
    )


class EvidenceService:
    """Tracks predictions across all organs and computes running calibration.

    Idempotent on re-ingestion: organs re-surface settled predictions each tick, so a
    pred_id is recorded once (resolved supersedes open).
    """

    def __init__(self) -> None:
        self._open: Dict[str, Prediction] = {}
        self._resolved: Dict[str, Prediction] = {}

    def ingest(self, source: str, prediction_rows: List[Dict[str, Any]], tick: int) -> None:
        for i, row in enumerate(prediction_rows):
            p = normalize_prediction(source, tick, i, row)
            if p.is_resolved:
                self._resolved[p.pred_id] = p
                self._open.pop(p.pred_id, None)
            elif p.pred_id not in self._resolved:
                self._open[p.pred_id] = p

    # -- queries ---------------------------------------------------------- #
    @property
    def open_count(self) -> int:
        return len(self._open)

    @property
    def resolved_count(self) -> int:
        return len(self._resolved)

    def scorecard(self) -> Dict[str, Any]:
        graded = [p for p in self._resolved.values() if p.outcome in ("hit", "miss")]
        hits = sum(1 for p in graded if p.outcome == "hit")
        misses = sum(1 for p in graded if p.outcome == "miss")
        n = hits + misses
        # Brier: mean squared error of confidence vs realized 0/1 outcome (lower is better).
        brier = (
            sum((p.confidence - (1.0 if p.outcome == "hit" else 0.0)) ** 2 for p in graded) / n
            if n
            else None
        )
        errs = [p.calibration_error for p in graded if p.calibration_error is not None]
        return {
            "open": len(self._open),
            "resolved": len(self._resolved),
            "graded": n,
            "hits": hits,
            "misses": misses,
            "hit_rate": (hits / n) if n else None,
            "brier_score": round(brier, 4) if brier is not None else None,
            "mean_calibration_error": round(sum(errs) / len(errs), 4) if errs else None,
        }

    def by_source(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for p in list(self._open.values()) + list(self._resolved.values()):
            d = out.setdefault(p.source, {"open": 0, "resolved": 0})
            d["resolved" if p.is_resolved else "open"] += 1
        return out
