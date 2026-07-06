"""Observation — the STORED half. Reality's responses are recorded, never derived.

The Index (`index.py`) derives hypotheses + coordinates. It must NOT derive *evidence*. What the
world actually did is an observation: it happens once, outside the engine, and is stored immutably.
Deriving it would close the loop — a universe grading its own homework, theory masquerading as
evidence. ATL's whole identity is evidence outranking theory; this module is that line in code.

    Index        → derives a hypothesis (claim + confidence + coordinate)   [derivable]
    Reality      → answers it                                               [outside]
    ObservationLog → records the answer                                     [STORED]
    grade()      → JOINS derived claim with stored evidence                 [the funnel]

A claim reality has not answered stays OPEN — never assumed. (The deterministic intake-tape in
`live.py` is the live-source version of this same store; an ObservationLog is the general form.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from oradio_engine.index import Address, Index, gate


@dataclass
class ObservationLog:
    """Append-only store of what actually happened, keyed by address. Not derivable."""

    _observed: Dict[Address, Any] = field(default_factory=dict)

    def record(self, address: Address, outcome: Any) -> None:
        # observed once; reality doesn't get re-rolled. (First write wins.)
        self._observed.setdefault(tuple(address), outcome)

    def observed(self, address: Address) -> Optional[Any]:
        return self._observed.get(tuple(address))

    def __len__(self) -> int:
        return len(self._observed)


def grade(
    index: Index,
    addresses: List[Address],
    log: ObservationLog,
    *,
    predicts_key: str = "predicts",
    confidence_key: str = "confidence",
    level: int = 0,
    t: int = 0,
) -> Dict[str, Any]:
    """Join DERIVED hypotheses (Index) with STORED evidence (log).

    For each address: derive the claim; look up reality's recorded answer. If reality hasn't
    answered, the claim is OPEN — it is never scored on a derived outcome. Evidence outranks theory.
    """
    open_count = 0
    hits = 0
    misses = 0
    brier_terms: List[float] = []

    for address in addresses:
        claim = index.resolve(address)                      # derived
        outcome = log.observed(address)                     # stored (or None)
        if outcome is None:
            open_count += 1
            continue
        hit = outcome == claim[predicts_key]
        hits += int(hit)
        misses += int(not hit)
        brier_terms.append((float(claim[confidence_key]) - (1.0 if hit else 0.0)) ** 2)

    n = hits + misses
    return {
        "open": open_count,                  # claims reality hasn't answered (NOT assumed)
        "resolved": n,                       # claims graded against stored evidence
        "hits": hits,
        "misses": misses,
        "hit_rate": (hits / n) if n else None,
        "brier": round(sum(brier_terms) / n, 4) if n else None,
        "gate": gate(level, t),              # the rising bar this season must clear
    }


def frontier_threshold(scores: List[float], keep_fraction: float) -> float:
    """The bar that keeps the top ``keep_fraction`` of the pool — a PERCENTILE of reality, not an
    absolute schedule. The standard rises because the *survivors get better*, not because a clock
    ticks. This is the frontier: 'better than the pool you're competing in right now.'"""
    if not scores:
        return 0.0
    ordered = sorted(scores)
    cut = min(len(ordered) - 1, int((1.0 - keep_fraction) * len(ordered)))
    return ordered[cut]


def evolutionary_funnel(performances: Dict[Any, float], *, keep_fraction: float, seasons: int):
    """Survival under increasing standards. Each season the bar = the frontier of the *current*
    survivors, so weak genomes die and the bar rises endogenously — 'reality keeps failing to kill
    them.' Returns one record per season: {season, bar, survivors}. (The bar is observed evidence,
    never a derived outcome — see the loop above.)"""
    pool = dict(performances)
    history = []
    for s in range(1, seasons + 1):
        if not pool:
            break
        bar = frontier_threshold(list(pool.values()), keep_fraction)
        pool = {k: v for k, v in pool.items() if v > bar}   # only those above the frontier survive
        history.append({"season": s, "bar": round(bar, 4), "survivors": len(pool)})
    return history
