"""Oracle Kingdom shim — the belief/decree/ripple organ.

Oracle Kingdom is a PULL, DETERMINISTIC organ with a lower-level seam than the others:
there is no controller, you drive the building blocks directly (as tools/oracle_sim.py
does). The shim governs ONE kingdom with an autonomous AI Oracle and maps the five verbs
onto the verified API:

    advance(to_tick)  -> per owed tick: SimulationEngine.advance_tick(state, SeededRNG(seed+tick), tc)
                         every `decree_interval` ticks the Oracle speaks (propagate_decree)
    observe(delta)    -> normalize SimEvents (+ the decree speech) -> NormalizedCandidate
    read_truth()      -> kingdom health / era / legitimacy / faith snapshot
    apply_input(e)    -> make the Oracle speak now (the player/another organ AS the Oracle)
    identity()        -> kingdom seed + DETERMINISTIC

Determinism law matches the others: every tick draws from SeededRNG(seed + tick).
One federation clock tick = `world_ticks_per_clock_tick` kingdom ticks.
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


def _import_oracle():
    import os
    import sys

    plugins_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "plugins"
    )
    if plugins_dir not in sys.path:
        sys.path.insert(0, plugins_dir)
    import oracle_kingdom as ok  # noqa: E402

    return ok


class OracleKingdomOrgan:
    """Adapts a single AI-governed Oracle Kingdom to ``SimulationOrgan``."""

    def __init__(
        self,
        name: str,
        kingdom: Any,
        ok_module: Any,
        world_ticks_per_clock_tick: int = 15,
        decree_interval: int = 15,
    ) -> None:
        if world_ticks_per_clock_tick < 1:
            raise ValueError("world_ticks_per_clock_tick must be >= 1")
        self._name = name
        self._k = kingdom
        self._ok = ok_module
        self._ratio = world_ticks_per_clock_tick
        self._decree_interval = max(1, decree_interval)
        self._time_config = ok_module.TimeConfig()
        self._force_decree = False

    # -- construction ----------------------------------------------------- #
    @classmethod
    def from_seed(
        cls,
        name: str,
        seed: int,
        *,
        world_ticks_per_clock_tick: int = 15,
        decree_interval: int = 15,
    ) -> "OracleKingdomOrgan":
        ok = _import_oracle()
        kingdom = ok.WorldBuilder.build_kingdom(kingdom_id=name, seed=int(seed), is_player=False)
        if getattr(kingdom, "seed", None) is None:
            kingdom.seed = int(seed)
        return cls(name, kingdom, ok, world_ticks_per_clock_tick, decree_interval)

    # -- internals -------------------------------------------------------- #
    def _issue_decree(self, tick: int) -> Optional[Dict[str, Any]]:
        """Deterministically pick + propagate a decree. Returns a flat bus event."""
        ok = self._ok
        k = self._k
        rng = ok.SeededRNG(k.seed + tick).fork(f"decree_{k.kingdom_id}")
        options = ok.SpeechGenerator.generate_decree_options(k, rng, count=4)
        if not options:
            return None
        # Deterministic choice: strongest propagation, tiebroken by option_id.
        chosen = max(options, key=lambda o: (getattr(o, "propagation_magnitude", 0.0), o.option_id))
        ok.PropagationEngine.propagate_decree(k, chosen, rng)
        k.decree_history.append(
            ok.DecreeRecord(
                decree_id=chosen.option_id,
                tick=k.tick,
                text=chosen.text,
                tone=chosen.tone.name,
                mode=chosen.mode.name,
                policy_vector=dict(chosen.policy_vector),
            )
        )
        ok.MythMemory.tick_memory(k, rng)
        mag = float(getattr(chosen, "propagation_magnitude", 0.5))
        return {
            "title": f"Decree ({chosen.tone.name})",
            "body": chosen.text,
            "type": "decree",
            "priority": min(1.0, 0.6 + mag * 0.4),  # the Oracle speaking is high-signal
            "heat": min(1.0, 0.6 + mag * 0.4),
            "ts": float(k.tick),
            "tags": ["oracle", "decree", chosen.tone.name],
        }

    @staticmethod
    def _event_to_flat(ev: Any) -> Dict[str, Any]:
        d = ev.to_dict() if hasattr(ev, "to_dict") else dict(ev)
        severity = float(d.get("severity", 0.0))
        urgency = float(d.get("urgency", 0.0))
        priority = max(severity, urgency) / 100.0
        return {
            "title": str(d.get("kind", "event")).replace("_", " ").title(),
            "body": str(d.get("description", "")),
            "type": str(d.get("kind", "event")),
            "priority": round(priority, 4),
            "heat": round(priority, 4),
            "ts": float(d.get("tick", 0)),
            "tags": ["oracle", str(d.get("domain", "")).lower(), str(d.get("kind", "")).lower()],
        }

    # -- the five verbs --------------------------------------------------- #
    def identity(self) -> OrganIdentity:
        return OrganIdentity(
            name=self._name,
            determinism=Determinism.DETERMINISTIC,
            seed=getattr(self._k, "seed", None),
        )

    def advance(self, to_tick: int) -> TickDelta:
        ok = self._ok
        k = self._k
        from_tick = int(k.tick)
        target = to_tick * self._ratio
        flat_events: List[Dict[str, Any]] = []

        if self._force_decree:
            decree = self._issue_decree(int(k.tick) + 1)
            if decree:
                flat_events.append(decree)
            self._force_decree = False

        while k.tick < target:
            next_tick = int(k.tick) + 1
            rng = ok.SeededRNG(k.seed + next_tick)
            sim_events = ok.SimulationEngine.advance_tick(k, rng, self._time_config)
            for ev in sim_events:
                flat_events.append(self._event_to_flat(ev))
            if next_tick % self._decree_interval == 0:
                decree = self._issue_decree(next_tick)
                if decree:
                    flat_events.append(decree)

        return TickDelta(
            from_tick=from_tick,
            to_tick=int(k.tick),
            events=flat_events,
            predictions=[],
            heat=min(1.0, len([e for e in flat_events if e["type"] == "decree"]) / 2.0),
            headline=f"{getattr(k, 'name', self._name)} @ {self._era_name()}",
        )

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        return [
            normalize_event(self._name, delta.to_tick, i, ev)
            for i, ev in enumerate(delta.events)
        ]

    def _era_name(self) -> str:
        era = getattr(self._k, "current_era", None)
        return getattr(era, "name", str(era)) if era is not None else "?"

    def read_truth(self) -> Dict[str, Any]:
        k = self._k
        health = getattr(getattr(k, "health", None), "composite", None)
        pol = getattr(k, "political", None)
        belief = getattr(k, "belief", None)
        return {
            "tick": int(k.tick),
            "seed": getattr(k, "seed", None),
            "name": getattr(k, "name", self._name),
            "era": self._era_name(),
            "health": round(health, 1) if health is not None else None,
            "legitimacy": round(getattr(pol, "legitimacy", 0.0), 1) if pol else None,
            "corruption": round(getattr(pol, "corruption", 0.0), 1) if pol else None,
            "public_faith": round(getattr(belief, "public_faith", 0.0), 1) if belief else None,
            "decrees": len(getattr(k, "decree_history", [])),
            "events": len(getattr(k, "event_history", [])),
        }

    def apply_input(self, event: Dict[str, Any]) -> None:
        # Any non-empty input makes the Oracle speak on the next advance — the
        # player (or another organ) acting AS the Oracle.
        if event:
            self._force_decree = True
