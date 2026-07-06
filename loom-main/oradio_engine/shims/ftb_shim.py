"""FTB (From the Backmarker) shim — the competition/economy organ.

FTB is a PULL, DETERMINISTIC organ: a pure-math racing-management world (no LLM). The
headless seam is ``FTBSimulation.tick_simulation(state) -> List[SimEvent]`` over a
``SimState`` populated by ``WorldBuilder.generate_world`` (see plugins/ftb_game.py).

The shim observes an *ambient* world (no player team), so the calendar advances and the
league emits race results, contract expiries, financial stress, retirements, etc. — exactly
the "event generation from pure simulation" FTB was built for. FTB is passively emergent
(different seeds -> different seasons), like Oracle and ForkUniverse, unlike Neikos.

    advance(to_tick)  -> tick_simulation() per owed day; SimEvents -> bus candidates
    observe(delta)    -> normalize (priority is 0-100 -> /100)
    read_truth()      -> calendar + league/team counts
    apply_input(e)    -> v1 no-op (no operator seam without a player team)
    identity()        -> SimState.seed + DETERMINISTIC

FTB imports its siblings as ``from plugins import ...`` (namespace package), so the shim
puts the REPO ROOT on sys.path and imports ``plugins.ftb_game``. One clock tick =
``world_ticks_per_clock_tick`` sim days (default 7 = a week, so the weekly contract-expiry
sweep always fires).
"""

from __future__ import annotations

from typing import Any, Dict, List

from oradio_engine.contract import (
    Determinism,
    NormalizedCandidate,
    OrganIdentity,
    TickDelta,
    normalize_event,
)

# UI-routing events are not world truth — keep them off the bus.
_SKIP_EVENT_TYPES = {"ui_action", "ui"}


def _import_ftb():
    import os
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import plugins.ftb_game as ftb  # noqa: E402  (namespace package: from plugins import ...)

    return ftb


class FTBOrgan:
    """Adapts an ambient FTB ``SimState`` to ``SimulationOrgan``."""

    def __init__(self, name: str, state: Any, ftb_module: Any, world_ticks_per_clock_tick: int = 7) -> None:
        if world_ticks_per_clock_tick < 1:
            raise ValueError("world_ticks_per_clock_tick must be >= 1")
        self._name = name
        self._state = state
        self._ftb = ftb_module
        self._ratio = world_ticks_per_clock_tick

    # -- construction ----------------------------------------------------- #
    @classmethod
    def from_seed(cls, name: str, seed: int, *, world_ticks_per_clock_tick: int = 7) -> "FTBOrgan":
        ftb = _import_ftb()
        state = ftb.SimState()
        state.seed = int(seed)
        ftb.WorldBuilder.generate_world(state)
        return cls(name, state, ftb, world_ticks_per_clock_tick)

    # -- internals -------------------------------------------------------- #
    def _flat(self, ev: Any) -> Dict[str, Any]:
        etype = getattr(ev, "event_type", "") or ""
        category = getattr(ev, "category", "") or ""
        pr = min(1.0, float(getattr(ev, "priority", 0.0)) / 100.0)
        return {
            "title": (category or etype or "event").replace("_", " ").title(),
            "body": getattr(ev, "description", "") or "",
            "type": etype or category or "event",
            "priority": round(pr, 4),
            "heat": round(pr, 4),
            "ts": float(getattr(ev, "ts", 0)),
            "tags": ["ftb", etype, category],
        }

    # -- the five verbs --------------------------------------------------- #
    def identity(self) -> OrganIdentity:
        return OrganIdentity(
            name=self._name,
            determinism=Determinism.DETERMINISTIC,
            seed=getattr(self._state, "seed", None),
        )

    def advance(self, to_tick: int) -> TickDelta:
        ftb = self._ftb
        state = self._state
        from_tick = int(state.tick)
        target = to_tick * self._ratio
        flat_events: List[Dict[str, Any]] = []

        stalls = 0
        while state.tick < target:
            before = int(state.tick)
            events = ftb.FTBSimulation.tick_simulation(state)
            for ev in events:
                if getattr(ev, "event_type", "") in _SKIP_EVENT_TYPES:
                    continue
                flat_events.append(self._flat(ev))
            if int(state.tick) == before:
                stalls += 1
                if stalls > 2:  # blocked (shouldn't happen with no player team) — bail
                    break
            else:
                stalls = 0

        high = sum(1 for e in flat_events if e["priority"] >= 0.5)
        return TickDelta(
            from_tick=from_tick,
            to_tick=int(state.tick),
            events=flat_events,
            predictions=[],
            heat=min(1.0, high / 5.0),
            headline=f"Season {getattr(state, 'season_number', '?')}, Year {getattr(state, 'sim_year', '?')}",
        )

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        return [
            normalize_event(self._name, delta.to_tick, i, ev)
            for i, ev in enumerate(delta.events)
        ]

    def read_truth(self) -> Dict[str, Any]:
        state = self._state
        return {
            "tick": int(state.tick),
            "seed": getattr(state, "seed", None),
            "season": getattr(state, "season_number", None),
            "year": getattr(state, "sim_year", None),
            "day_of_year": getattr(state, "sim_day_of_year", None),
            "leagues": len(getattr(state, "leagues", {}) or {}),
            "teams": len(getattr(state, "teams", {}) or {}),
        }

    def apply_input(self, event: Dict[str, Any]) -> None:
        # v1: FTB has no simple operator seam without a player team. No-op (not an error).
        return
