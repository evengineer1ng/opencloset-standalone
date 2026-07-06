"""ForkUniverse shim — the first real organ on the federation bus.

ForkUniverse is a PULL, DETERMINISTIC organ: a universe is a pure function of
(canonical_seed, ticks). The shim maps the five engine verbs onto the verified
``UniverseState`` seam (see tests/test_forkuniverse_engine.py):

    advance(to_tick)  -> simulate_epoch(owed world ticks) + emit_truth_delta(from)
    observe(delta)    -> normalize new_events -> NormalizedCandidate (bus)
    read_truth()      -> digest + tick + active threads
    apply_input(e)    -> apply_operator_input(intent, magnitude, target_domain)
    identity()        -> canonical_seed + DETERMINISTIC

One federation clock tick = ``world_ticks_per_clock_tick`` universe ticks (the
real→sim cadence; mirrors the bridge's ``world_ticks_per_narration_tick``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from oradio_engine.contract import (
    Determinism,
    NormalizedCandidate,
    OrganIdentity,
    TickDelta,
    normalize_event,
)


class ForkUniverseOrgan:
    """Adapts a compiled ForkUniverse ``UniverseState`` to ``SimulationOrgan``."""

    def __init__(self, name: str, state: Any, world_ticks_per_clock_tick: int = 12) -> None:
        if world_ticks_per_clock_tick < 1:
            raise ValueError("world_ticks_per_clock_tick must be >= 1")
        self._name = name
        self._state = state
        self._ratio = world_ticks_per_clock_tick

    # -- construction ----------------------------------------------------- #
    @classmethod
    def from_request(
        cls,
        name: str,
        *,
        world_ticks_per_clock_tick: int = 12,
        **creation_kwargs: Any,
    ) -> "ForkUniverseOrgan":
        """Compile + load a universe from a CreationRequest's fields.

        Imports are local so the engine package has no hard dependency on
        ForkUniverse unless a station actually uses it.
        """
        from forkuniverse.compiler.compile import compile_universe
        from forkuniverse.compiler.models import CreationRequest
        from forkuniverse.engine.world_core import load_universe

        package = compile_universe(CreationRequest(**creation_kwargs))
        return cls(name, load_universe(package), world_ticks_per_clock_tick)

    # -- the five verbs --------------------------------------------------- #
    def identity(self) -> OrganIdentity:
        return OrganIdentity(
            name=self._name,
            determinism=Determinism.DETERMINISTIC,
            seed=self._state.canonical_seed,
        )

    def advance(self, to_tick: int) -> TickDelta:
        target_world = to_tick * self._ratio
        from_world = int(self._state.time.tick)
        owed = target_world - from_world
        if owed > 0:
            self._state.simulate_epoch(owed)
        delta = self._state.emit_truth_delta(from_tick=from_world)

        predictions: List[Dict[str, Any]] = []
        for row in getattr(delta, "opened_predictions", []) or []:
            predictions.append({**row, "_status": "opened"})
        for row in getattr(delta, "settled_predictions", []) or []:
            predictions.append({**row, "_status": "settled"})

        return TickDelta(
            from_tick=delta.from_tick,
            to_tick=delta.to_tick,
            events=list(delta.new_events),
            predictions=predictions,
            heat=float(delta.heat),
            headline=str(delta.headline),
        )

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        candidates: List[NormalizedCandidate] = []
        for i, event in enumerate(delta.events):
            # ForkUniverse events carry a world ``tick``; use it as the candidate ts
            # so airtime ordering follows world time, not wall-clock.
            enriched = dict(event)
            enriched.setdefault("ts", float(event.get("tick", delta.to_tick)))
            candidates.append(normalize_event(self._name, delta.to_tick, i, enriched))
        return candidates

    def read_truth(self) -> Dict[str, Any]:
        state = self._state
        return {
            "tick": int(state.time.tick),
            "universe_id": state.universe_id,
            "universe_title": getattr(state, "universe_title", None),
            "active_threads": state.active_thread_count(),
            "digest": state.digest(),
        }

    def apply_input(self, event: Dict[str, Any]) -> None:
        domain = event.get("target_domain") or event.get("domain")
        if not domain:
            return  # operator input without a target is a no-op, not an error
        self._state.apply_operator_input(
            intent=str(event.get("intent", "amplify")),
            magnitude=float(event.get("magnitude", 0.5)),
            target_domain=str(domain),
        )
