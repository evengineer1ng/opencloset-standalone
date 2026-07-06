"""Deterministic visual coordinates for the Loom's causal tape."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

from oradio_engine.index import Address, Index


def _digest(seed: Any, address: Address) -> bytes:
    return hashlib.sha256(f"{seed}:{address}".encode("utf-8")).digest()


def _unit_interval(raw: bytes, offset: int) -> float:
    return int.from_bytes(raw[offset:offset + 4], "big") / 0xFFFFFFFF


def _visual_point(seed: Any, address: Address) -> Dict[str, float]:
    raw = _digest(seed, address)
    return {
        "u": _unit_interval(raw, 0),
        "v": _unit_interval(raw, 4),
        "w": _unit_interval(raw, 8),
        "z": _unit_interval(raw, 12),
    }


@dataclass
class VisualIndex:
    """A small deterministic resolver for visual addresses."""

    seed: Any
    _index: Index = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._index = Index(self.seed, _visual_point)

    def resolve(self, address: Address) -> Dict[str, float]:
        return self._index.resolve(tuple(address), cache=True)

    def particle(self, tick: int, idx: int) -> Dict[str, float]:
        return self.resolve(("t", int(tick), "particle", int(idx)))

    def ripple(self, tick: int, seq: int) -> Dict[str, float]:
        return self.resolve(("t", int(tick), "ripple", int(seq)))

    def color(self, tick: int, channel: str) -> Dict[str, float]:
        return self.resolve(("t", int(tick), "color", str(channel)))


def visual_seed(descriptor: Dict[str, Any]) -> str:
    visual = descriptor.get("visual") if isinstance(descriptor.get("visual"), dict) else {}
    tape = visual.get("tape") if isinstance(visual.get("tape"), dict) else {}
    configured = str(tape.get("seed") or "").strip()
    if configured:
        return configured
    world = descriptor.get("world") if isinstance(descriptor.get("world"), dict) else {}
    name = str(descriptor.get("oradio") or world.get("name") or "loom")
    seed = world.get("seed", "0")
    return f"{name}:{seed}"
