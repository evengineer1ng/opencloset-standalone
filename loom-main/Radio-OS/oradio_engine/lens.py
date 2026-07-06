"""The lens — declared, composable interpretation that sits ON TOP of organs.

The deepest alignment: interpretation must not be organ-bound. An organ's ``observe()`` is
its world's NATIVE projection (only it knows its own event schema -> neutral candidates). The
**lens** is a *declared* pipeline of composable ops applied over that projection, per the
`.oradio`'s intent. The same world read through different lenses yields different broadcasts —
no organ code changes, and the lens is data in the artifact, not Python.

A lens is applied transparently via :class:`LensedOrgan` (wraps an organ; delegates the four
non-observe verbs; runs the lens over ``observe``). So nothing in the federation engine changes.

Declared in an `.oradio` as either a preset name (``lens: competition``) or an inline pipeline
(``lens: {ops: [{op: drop_types, types: [audio]}, {op: cap, n: 20}]}``). This is also how a
domain tames an organ emergently — e.g. FTB's pbp flood is a declared ``drop_types`` op, not a
patch to ftb_game.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Union

from oradio_engine.contract import NormalizedCandidate, TickDelta

# A lens op transforms one tick's candidate list into another.
LensOp = Callable[[List[NormalizedCandidate]], List[NormalizedCandidate]]


# --------------------------------------------------------------------------- #
# Composable ops (each factory returns a LensOp). Pure, order-independent of
# the engine; declared by name + params in the artifact.
# --------------------------------------------------------------------------- #
def drop_types(types: List[str]) -> LensOp:
    s = set(types)
    return lambda cands: [c for c in cands if c.type not in s]


def keep_types(types: List[str]) -> LensOp:
    s = set(types)
    return lambda cands: [c for c in cands if c.type in s]


def floor_priority(min: float) -> LensOp:
    return lambda cands: [c for c in cands if c.priority >= min]


def boost(types: List[str], to: float) -> LensOp:
    s = set(types)
    return lambda cands: [replace(c, priority=to) if c.type in s else c for c in cands]


def retag(add: List[str]) -> LensOp:
    extra = tuple(add)
    return lambda cands: [replace(c, tags=tuple(c.tags) + extra) for c in cands]


def cap(n: int) -> LensOp:
    return lambda cands: sorted(cands, key=lambda c: c.priority, reverse=True)[:n]


OP_BUILDERS: Dict[str, Callable[..., LensOp]] = {
    "drop_types": drop_types,
    "keep_types": keep_types,
    "floor_priority": floor_priority,
    "boost": boost,
    "retag": retag,
    "cap": cap,
}


@dataclass
class Lens:
    """An ordered, declared pipeline of ops."""

    name: str = "identity"
    ops: List[LensOp] = field(default_factory=list)

    def apply(self, source: str, candidates: List[NormalizedCandidate]) -> List[NormalizedCandidate]:
        for op in self.ops:
            candidates = op(candidates)
        return candidates


# Named presets — small, meaningful starting points; an artifact can also declare ops inline.
def _preset(name: str) -> Lens:
    if name in ("identity", "raw", ""):
        return Lens("identity", [])
    if name == "competition":  # competition feeds: signal over chatter
        return Lens("competition", [floor_priority(0.3), cap(25)])
    if name == "headline":  # only the loudest few
        return Lens("headline", [cap(8)])
    raise KeyError(f"unknown lens preset {name!r}")


def build_lens(spec: Union[None, str, Dict[str, Any]]) -> Lens:
    """Build a Lens from a declared spec: ``None`` | preset name | ``{name?, ops:[...]}``."""
    if spec is None:
        return Lens("identity", [])
    if isinstance(spec, str):
        return _preset(spec)
    if isinstance(spec, dict):
        ops: List[LensOp] = []
        for op_spec in spec.get("ops", []):
            params = dict(op_spec)
            op_name = params.pop("op")
            if op_name not in OP_BUILDERS:
                raise KeyError(f"unknown lens op {op_name!r}")
            ops.append(OP_BUILDERS[op_name](**params))
        return Lens(spec.get("name", "custom"), ops)
    raise TypeError(f"lens spec must be None, str, or dict, got {type(spec)}")


class LensedOrgan:
    """Wraps an organ so its native projection passes through a declared lens.

    Transparent: identity/advance/read_truth/apply_input delegate unchanged (so predictions
    still flow to the evidence service); only ``observe`` is filtered through the lens.
    """

    def __init__(self, inner: Any, lens: Optional[Lens] = None) -> None:
        self._inner = inner
        self._lens = lens or Lens("identity", [])

    def identity(self):
        return self._inner.identity()

    def advance(self, to_tick: int) -> TickDelta:
        return self._inner.advance(to_tick)

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        native = self._inner.observe(delta)
        return self._lens.apply(self._inner.identity().name, native)

    def read_truth(self) -> Dict[str, Any]:
        return self._inner.read_truth()

    def apply_input(self, event: Dict[str, Any]) -> None:
        self._inner.apply_input(event)
