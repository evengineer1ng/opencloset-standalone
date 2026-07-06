"""The engine ⇄ organ contract — five verbs, one normalized candidate.

This is the discovered (not invented) interface: the mature sims in this repo all
converged on forked-namespace deterministic RNG keyed by ``(seed, namespace, tick)``,
a causal ledger, a true tick seam, absence reconstruction, an operator-input seam, and
a hot-layer export. Those collapse into the contract below.

Nothing here is domain-specific. A shim is a thin adapter from a sovereign backend to
:class:`SimulationOrgan`; see docs/SIMULATION_ENGINE.md §8 for the per-organ mapping.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


class Determinism(str, Enum):
    """Whether a world is a total function of its inputs.

    DETERMINISTIC: ``world(t) = f(seed, tape[0..t])`` is total — the future can be
    *computed* (real time-travel). LIVE: intake cannot be fabricated, so the future
    can only be *projected* as a graded prediction. The line is never blurred; it is
    the literal recording boundary (docs/SIMULATION_ENGINE.md §3).
    """

    DETERMINISTIC = "deterministic"
    LIVE = "live"


@dataclass(frozen=True)
class OrganIdentity:
    """Who an organ is and what time-behaviour it may legally offer."""

    name: str
    determinism: Determinism
    seed: Optional[object] = None  # int (ForkUniverse uses a canonical string seed)

    @property
    def can_compute_future(self) -> bool:
        return self.determinism is Determinism.DETERMINISTIC


@dataclass(frozen=True)
class NormalizedCandidate:
    """The repo's locked normalized candidate — the "blood" on the federation bus.

    Shape is intentionally identical to the antenna contract
    (``plugins/antenna_bridge.py``) so existing signal-heat / broadcast-grammar
    machinery consumes organ output unchanged.
    """

    post_id: str
    source: str
    title: str
    body: str
    priority: float
    ts: float
    type: str = "event"
    tags: tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "post_id": self.post_id,
            "source": self.source,
            "title": self.title,
            "body": self.body,
            "priority": self.priority,
            "ts": self.ts,
            "type": self.type,
            "tags": list(self.tags),
        }
        return d


@dataclass
class TickDelta:
    """What an organ produced when advanced — the hot-layer export of one step.

    Mirrors ForkUniverse's ``compute_absence`` delta (events / predictions / heat /
    headline) so that shim is near-trivial, while staying generic enough for any organ.
    """

    from_tick: int
    to_tick: int
    events: List[Dict[str, Any]] = field(default_factory=list)
    predictions: List[Dict[str, Any]] = field(default_factory=list)
    heat: float = 0.0
    headline: str = ""

    @property
    def ticks_advanced(self) -> int:
        return self.to_tick - self.from_tick


@runtime_checkable
class SimulationOrgan(Protocol):
    """The five-verb contract every organ (or its shim) implements."""

    def identity(self) -> OrganIdentity:
        """Stable identity + determinism class. Pure; safe to call any time."""
        ...

    def advance(self, to_tick: int) -> TickDelta:
        """Advance the world to ``to_tick`` (engine clock). Pull organs compute owed
        ticks here; push organs may treat this as a no-op and surface state in
        :meth:`observe`. Deterministic organs MUST be replay-stable: same identity +
        same tick sequence ⇒ identical deltas."""

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        """Turn the latest delta (and/or self-emitted state) into bus candidates.
        The default normalizer :func:`normalize_event` covers the common case."""

    def read_truth(self) -> Dict[str, Any]:
        """Hot-layer snapshot of current world state for narration / visuals."""

    def apply_input(self, event: Dict[str, Any]) -> None:
        """Accept external telemetry / operator action / cross-organ ripple."""


def normalize_event(source: str, tick: int, index: int, event: Dict[str, Any]) -> NormalizedCandidate:
    """Default event → :class:`NormalizedCandidate` mapping.

    Looks for the common keys organs already emit (``title``/``headline``,
    ``summary``/``body``, ``heat``/``pressure_delta``/``priority``). Domain-specific
    shims may override :meth:`SimulationOrgan.observe` instead of using this.
    """

    title = str(event.get("title") or event.get("headline") or event.get("event_type") or "untitled")
    body = str(event.get("summary") or event.get("body") or "")
    priority = float(
        event.get("priority", event.get("heat", abs(float(event.get("pressure_delta", 0.0)))))
    )
    raw_tags = event.get("tags") or []
    tags = tuple(str(t) for t in raw_tags) if isinstance(raw_tags, (list, tuple)) else ()
    return NormalizedCandidate(
        post_id=f"{source}:{tick}:{index}",
        source=source,
        title=title,
        body=body,
        priority=priority,
        ts=event.get("ts", _time.time()),
        type=str(event.get("type", event.get("event_type", "event"))),
        tags=tags,
    )
