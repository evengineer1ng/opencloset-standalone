"""The Index — derivable addressing for simulation data (store the generator, not the output).

Named for the owner's acrostic EP *Index* (the 3-layer work that preceded *.D.O.G.*). The album
is the existence-proof: a 3-letter seed + a few rules deterministically *generate and address* an
infinitely deep structure where every position is derivable — so you store the word, not the
54 minutes. The Index applies that to the engine.

The whole thing is three pieces (the same triple as the album):

    Address          a derivable coordinate, e.g. ("t", 144, "pred", 3) — NOT a stored id
    Index.resolve()  recompute the element from (seed, generator, address) — never stored
                     (= ForkUniverse's compute_absence, generalized to ALL engine data)
    gate(level, t)   the rising bar (99 → 99.9 → 99.999…), a derivable schedule like bitcoin halving

The FUNNEL is the address tree: ascending = an address moving up; "what's surfaced" = the addresses
whose *derived* score clears the rising gate. It scales because the Index is O(seeds + rules) ≈ KB,
never O(content): a 10GB shelf of `.oradio`s is addressable for ~free, and a query materializes only
the slice it touches (idle = zero, the calculator-not-daemon property).

This is engine/club *substrate*, not a payload in the `.oradio` (the file stays seed + rules). At
library scale the thing that "indexes `.oradio`s" is a recursive `.oradio` (see oradio composition) —
the Index is what keeps that recursion cheap. NO new unit of simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

Address = Tuple[Any, ...]

# A generator derives the element at an address from the seed. Pure + deterministic:
# resolve(seed, address) must always return the same element for the same inputs.
Generator = Callable[[Any, Address], Any]


@dataclass
class Index:
    """Stores a seed + a generator. The content is *derived* on demand, never enumerated."""

    seed: Any
    generator: Generator
    _cache: Dict[Address, Any] = field(default_factory=dict, repr=False)
    calls: int = field(default=0, repr=False)  # generator invocations (to prove laziness)

    def resolve(self, address: Address, *, cache: bool = False) -> Any:
        address = tuple(address)
        if cache and address in self._cache:
            return self._cache[address]
        self.calls += 1
        element = self.generator(self.seed, address)
        if cache:
            self._cache[address] = element
        return element


# --------------------------------------------------------------------------- #
# The rising gate — an ever-tightening, derivable threshold toward (but never) 1.0.
# Gains a "nine" per funnel level and another every ``ticks_per_nine`` ticks.
# gate(0,0)=0.9  gate(1,0)=0.99  gate(2,0)=0.999  gate(0,T)=0.99 …  (the noise floor it never beats)
# --------------------------------------------------------------------------- #
def gate(level: int, t: int = 0, *, ticks_per_nine: int = 50) -> float:
    nines = 1 + max(0, level) + (max(0, t) // ticks_per_nine)
    return 1.0 - 10.0 ** (-nines)


def funnel(
    index: Index,
    addresses: List[Address],
    score_of: Callable[[Any], float],
    *,
    level: int,
    t: int = 0,
) -> List[Address]:
    """Keep the addresses whose *derived* score clears the rising gate. The funnel is a walk,
    not a store: nothing is kept around — each candidate is resolved, scored, and tested."""
    bar = gate(level, t)
    return [a for a in addresses if score_of(index.resolve(a)) >= bar]


def lineage(address: Address, parent: Callable[[Address], Optional[Address]]) -> List[Address]:
    """The path from a deep address up to the seed — the album's 'read the first letters to
    collapse a layer' move. O(depth), no siblings materialized."""
    path: List[Address] = [tuple(address)]
    current: Optional[Address] = tuple(address)
    while True:
        current = parent(current)
        if current is None:
            break
        path.append(tuple(current))
    return path
