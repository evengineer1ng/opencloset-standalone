"""Neikos shim — the second real organ on the federation bus.

Neikos: Hundred Islands is a PULL, DETERMINISTIC organ driven by a controller with a
command queue in and a UI-event queue out (see tests/test_neikos_sim.py). The shim maps
the five verbs onto the verified ``NKController`` seam:

    advance(to_tick)  -> {"action":"advance","ticks":owed} ; buffer nk_ui_q events
    observe(delta)    -> normalize buffered UI events -> NormalizedCandidate (bus)
    read_truth()      -> read _state directly (avoids polluting the event queue)
    apply_input(e)    -> _handle_cmd(e)  (move / encounter / explore / battle / talk)
    identity()        -> _state.seed + DETERMINISTIC

UI events are buffered (not drained ad hoc) so events produced by interactions fold into
the *next* observed delta and nothing is lost or double-counted. One federation clock tick
= ``world_ticks_per_clock_tick`` island ticks (a tick_update fires every 10 island ticks,
so the default 10 surfaces one ambient world beat per clock tick).
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

# Ambient priority by Neikos UI event type (interaction beats outrank passive ticks).
_PRIORITY = {
    "tick_update": 0.20,
    "moved": 0.25,
    "encounter": 0.50,
    "fragment_discovered": 0.60,
    "memory_echo": 0.55,
    "battle_result": 0.65,
    "knower_dialogue": 0.70,
    "outcome_band": 0.45,
}


class NeikosOrgan:
    """Adapts a Neikos ``NKController`` to ``SimulationOrgan``."""

    def __init__(self, name: str, controller: Any, world_ticks_per_clock_tick: int = 10) -> None:
        if world_ticks_per_clock_tick < 1:
            raise ValueError("world_ticks_per_clock_tick must be >= 1")
        self._name = name
        self._ctrl = controller
        self._ratio = world_ticks_per_clock_tick
        self._pending: List[Dict[str, Any]] = []
        # Discard island-init events so the first advance's delta is about the advance,
        # not the cold open. (Drain the raw queue, don't buffer it.)
        q = self._ctrl._ui_q
        while not q.empty():
            q.get_nowait()

    # -- construction ----------------------------------------------------- #
    @classmethod
    def from_seed(
        cls,
        name: str,
        seed: int,
        *,
        world_ticks_per_clock_tick: int = 10,
        ngp_profile: Any = None,
    ) -> "NeikosOrgan":
        import os
        import queue
        import sys

        plugins_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "plugins"
        )
        if plugins_dir not in sys.path:
            sys.path.insert(0, plugins_dir)
        import neikos as nk  # noqa: E402

        runtime = {"nk_cmd_q": queue.Queue(), "nk_ui_q": queue.Queue()}
        controller = nk.NKController(runtime, {})
        controller.init_island(int(seed), ngp_profile=ngp_profile)
        return cls(name, controller, world_ticks_per_clock_tick)

    # -- internals -------------------------------------------------------- #
    def _drain(self) -> None:
        q = self._ctrl._ui_q
        while not q.empty():
            self._pending.append(q.get_nowait())

    def _summary(self, etype: str, data: Dict[str, Any]) -> str:
        if etype == "tick_update":
            return f"tier {data.get('current_tier')} at tick {data.get('tick')}"
        if etype == "encounter":
            sp = data.get("species", {}) or {}
            return f"{sp.get('name', 'a creature')} (lvl {data.get('level', '?')})"
        if etype == "moved":
            return f"moved to {data.get('node_id', '?')}"
        if etype in ("fragment_discovered", "memory_echo"):
            return str(data.get("description") or data.get("body") or data.get("title") or "")[:100]
        if etype == "battle_result":
            return f"{data.get('winner', '?')} won vs {data.get('opponent_name', '?')}"
        return str(data)[:100]

    def _flatten(self, ev: Dict[str, Any], to_tick: int) -> Dict[str, Any]:
        etype = ev.get("type", "event")
        data = ev.get("data", {}) or {}
        pr = _PRIORITY.get(etype, 0.30)
        return {
            "title": etype.replace("_", " ").title(),
            "body": self._summary(etype, data),
            "type": etype,
            "priority": pr,
            "heat": pr,
            "ts": float(data.get("tick", to_tick)),
            "tags": ["neikos", etype],
        }

    # -- the five verbs --------------------------------------------------- #
    def identity(self) -> OrganIdentity:
        return OrganIdentity(
            name=self._name,
            determinism=Determinism.DETERMINISTIC,
            seed=getattr(self._ctrl._state, "seed", None),
        )

    def advance(self, to_tick: int) -> TickDelta:
        state = self._ctrl._state
        from_tick = int(state.tick)
        owed = (to_tick * self._ratio) - from_tick
        if owed > 0:
            self._ctrl._handle_cmd({"action": "advance", "ticks": owed})
            self._drain()

        events = self._pending
        self._pending = []
        to = int(self._ctrl._state.tick)
        tier = getattr(self._ctrl._state, "current_tier", None)
        heat = (getattr(tier, "value", 1) / 5.0) if tier is not None else 0.0
        island = getattr(getattr(self._ctrl._state, "topology", None), "island_name", self._name)
        tier_name = getattr(tier, "name", "?")
        return TickDelta(
            from_tick=from_tick,
            to_tick=to,
            events=events,
            predictions=[],
            heat=heat,
            headline=f"{island} @ {tier_name}",
        )

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        return [
            normalize_event(self._name, delta.to_tick, i, self._flatten(ev, delta.to_tick))
            for i, ev in enumerate(delta.events)
        ]

    def read_truth(self) -> Dict[str, Any]:
        st = self._ctrl._state
        tier = getattr(st, "current_tier", None)
        return {
            "tick": int(st.tick),
            "seed": getattr(st, "seed", None),
            "island": getattr(getattr(st, "topology", None), "island_name", None),
            "tier": getattr(tier, "name", None),
            "location": getattr(st, "player_location", None),
        }

    def apply_input(self, event: Dict[str, Any]) -> None:
        if "action" not in event:
            return  # not a Neikos command (e.g. a generic ripple) — no-op, not an error
        self._ctrl._handle_cmd(dict(event))
        self._drain()
