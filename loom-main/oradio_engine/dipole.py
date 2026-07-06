"""Declared dipoles over the bus: track opposing poles as pure per-tick arithmetic.

An author declares what counts as the + pole and what counts as the - pole, then the
meter reduces a tick's candidates into four scalars:

    up       = sum(priority where pole > 0)
    down     = sum(priority where pole < 0)
    net      = up - down
    tension  = min(up, down)

This is intentionally small and deterministic. The only declared part is the matcher;
everything else is arithmetic over the normalized bus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

from oradio_engine.contract import NormalizedCandidate


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


@dataclass(frozen=True)
class PoleMatcher:
    """A declared selector for one pole of the dipole."""

    types: Tuple[str, ...] = ()
    sources: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "PoleMatcher":
        data = data or {}
        return cls(
            types=tuple(str(v) for v in data.get("types", []) or []),
            sources=tuple(str(v) for v in data.get("sources", []) or []),
            tags=tuple(str(v) for v in data.get("tags", []) or []),
        )

    def matches(self, cand: NormalizedCandidate) -> bool:
        if self.types and cand.type not in self.types:
            return False
        if self.sources and cand.source not in self.sources:
            return False
        if self.tags and not set(self.tags).intersection(cand.tags):
            return False
        return bool(self.types or self.sources or self.tags)


@dataclass(frozen=True)
class DipoleDecl:
    """The author-declared poles."""

    name: str = "dipole"
    up: PoleMatcher = field(default_factory=PoleMatcher)
    down: PoleMatcher = field(default_factory=PoleMatcher)

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "DipoleDecl | None":
        if not data:
            return None
        return cls(
            name=str(data.get("name") or "dipole"),
            up=PoleMatcher.from_dict(data.get("up")),
            down=PoleMatcher.from_dict(data.get("down")),
        )


@dataclass(frozen=True)
class DipoleReading:
    tick: int
    up: float
    down: float
    net: float
    tension: float
    flipped: bool
    direction: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tick": self.tick,
            "up": self.up,
            "down": self.down,
            "net": self.net,
            "tension": self.tension,
            "flipped": self.flipped,
            "direction": self.direction,
        }


@dataclass
class DipoleMeter:
    """A replay-stable meter over produced bus candidates."""

    decl: DipoleDecl
    history: List[DipoleReading] = field(default_factory=list)
    _last_direction: int = 0

    def classify(self, cand: NormalizedCandidate) -> int:
        up = self.decl.up.matches(cand)
        down = self.decl.down.matches(cand)
        if up and not down:
            return 1
        if down and not up:
            return -1
        return 0

    def measure(self, tick: int, candidates: Iterable[NormalizedCandidate]) -> DipoleReading:
        up = 0.0
        down = 0.0
        for cand in candidates:
            pole = self.classify(cand)
            if pole > 0:
                up += float(cand.priority)
            elif pole < 0:
                down += float(cand.priority)
        up = round(up, 6)
        down = round(down, 6)
        net = round(up - down, 6)
        tension = round(min(up, down), 6)
        direction = _sign(net)
        flipped = (
            direction != 0
            and self._last_direction != 0
            and direction != self._last_direction
        )
        reading = DipoleReading(
            tick=tick,
            up=up,
            down=down,
            net=net,
            tension=tension,
            flipped=flipped,
            direction=direction,
        )
        self.history.append(reading)
        if direction != 0:
            self._last_direction = direction
        return reading

    def flip_candidate(self, reading: DipoleReading) -> NormalizedCandidate | None:
        if not reading.flipped:
            return None
        direction = "up" if reading.direction > 0 else "down"
        return NormalizedCandidate(
            post_id=f"{self.decl.name}:{reading.tick}",
            source=self.decl.name,
            title=f"pole flip to {direction}",
            body=f"net={reading.net:.6f} tension={reading.tension:.6f}",
            priority=max(abs(reading.net), reading.tension),
            ts=float(reading.tick),
            type="pole_flip",
            tags=("dipole", direction),
        )
