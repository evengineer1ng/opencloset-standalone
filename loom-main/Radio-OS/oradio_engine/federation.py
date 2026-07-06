"""The federation runtime — one shared clock + one event bus over sovereign organs.

This is the decoder loop. It owns no world truth itself; it advances a shared clock,
asks each registered organ to advance to that tick, collects their hot-layer deltas,
normalizes them onto a single candidate bus, and (optionally, gated) lets one organ's
output become another's input — the cross-organ ripple surface that testing explores
(docs/SIMULATION_ENGINE.md; CONVERGENCE §H).

Determinism contract: with ripple disabled and only DETERMINISTIC organs, two engines
built the same way and ticked the same way produce byte-identical candidate streams
(benchmark axis #1, "Reproducibility").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from oradio_engine.binding import Binding
from oradio_engine.contract import (
    Determinism,
    NormalizedCandidate,
    SimulationOrgan,
    TickDelta,
)
from oradio_engine.evidence import EvidenceService


@dataclass
class Clock:
    """The shared federation clock. ``tick`` is the integer step all organs align to."""

    tick: int = 0
    real_seconds_per_tick: float = 1.0

    def advance(self, ticks: int = 1) -> int:
        if ticks < 0:
            raise ValueError("clock only moves forward")
        self.tick += ticks
        return self.tick


# A ripple policy decides whether candidates from one organ are fed as input to others.
# Signature: (target_organ_name, candidate) -> optional event dict to apply_input.
RipplePolicy = Callable[[str, NormalizedCandidate], Optional[Dict]]


@dataclass
class FederationEngine:
    """Runs a federation of organs on one clock and one bus."""

    clock: Clock = field(default_factory=Clock)
    organs: Dict[str, SimulationOrgan] = field(default_factory=dict)
    bus: List[NormalizedCandidate] = field(default_factory=list)
    ripple: Optional[RipplePolicy] = None  # default OFF (strict isolation) per owner #4
    evidence: Optional[EvidenceService] = None  # attach to grade organs' predictions
    bindings: List[Binding] = field(default_factory=list)  # declared telemetry->world->effector routes

    def register(self, organ: SimulationOrgan) -> None:
        name = organ.identity().name
        if name in self.organs:
            raise ValueError(f"organ {name!r} already registered")
        self.organs[name] = organ

    def tick(self, ticks: int = 1) -> List[NormalizedCandidate]:
        """Advance the shared clock and return the candidates produced this step.

        Organs are advanced in a stable order (registration order) so the bus is
        deterministic when the organs are. Returns only this step's candidates; the
        full history accumulates on :attr:`bus`.
        """

        target = self.clock.advance(ticks)
        produced: List[NormalizedCandidate] = []

        for name, organ in self.organs.items():
            delta: TickDelta = organ.advance(target)
            candidates = organ.observe(delta)
            produced.extend(candidates)
            if self.evidence is not None and delta.predictions:
                self.evidence.ingest(name, delta.predictions, target)

        # Declared bindings: route this step's candidates into target apply_input (telemetry
        # drives worlds; world actions drive effectors). Lands on the next step — no within-step
        # ordering games — so the eyes->brain->hands loop runs one stage per tick.
        for binding in self.bindings:
            target = self.organs.get(binding.target)
            if target is None:
                continue
            for cand in produced:
                if cand.source != binding.source:
                    continue
                event = binding.transform(cand)
                if event is not None:
                    target.apply_input(event)

        # Cross-organ ripple (gated). Applied after all organs advanced, so a step's
        # ripples land on the *next* step — no within-step ordering games.
        if self.ripple is not None:
            for cand in produced:
                for name, organ in self.organs.items():
                    if name == cand.source:
                        continue
                    event = self.ripple(name, cand)
                    if event is not None:
                        organ.apply_input(event)

        self.bus.extend(produced)
        return produced

    def run(self, steps: int, ticks_per_step: int = 1) -> List[NormalizedCandidate]:
        for _ in range(steps):
            self.tick(ticks_per_step)
        return list(self.bus)

    def truth(self) -> Dict[str, Dict]:
        """Hot-layer snapshot of every organ — what the voice/visuals read."""

        return {name: organ.read_truth() for name, organ in self.organs.items()}

    @property
    def is_fully_deterministic(self) -> bool:
        return all(
            o.identity().determinism is Determinism.DETERMINISTIC
            for o in self.organs.values()
        )
