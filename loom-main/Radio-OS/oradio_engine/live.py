"""Push/live organs + the intake tape — the determinism boundary made concrete.

Pull organs (ForkUniverse, Neikos, Oracle, FTB) are a total function of (seed, ticks);
the engine *calls* them. Push/live organs (ATL/League, MoCo) run in the outside world and
*emit*; the engine can't recompute their inputs. The honest reconciliation
(docs/SIMULATION_ENGINE.md §3) is:

    world(t) = f(seed, tape[0..t])

A live organ RECORDS what arrived into an immutable, timestamped **intake tape**. The live
intake happens once (nondeterministic, stamped); everything downstream is a pure function of
the tape. Replaying the tape reproduces the world byte-for-byte. The line between live and
deterministic is the recording boundary — never blurred, always both.

This module is the shared seam ATL and MoCo both plug into. A real adapter only implements
``LiveSource.poll()`` (ATL: read its REST API / league.sqlite; MoCo: read the classified
motion-intent stream). Everything else — recording, replay, normalization, determinism
accounting — is here and is tested locally with a scripted source (no real feed required).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from oradio_engine.contract import (
    Determinism,
    NormalizedCandidate,
    OrganIdentity,
    TickDelta,
    normalize_event,
)


@dataclass(frozen=True)
class TapeEntry:
    """One recorded live observation, stamped with the tick it arrived at."""

    tick: int
    index: int
    raw: Dict[str, Any]


@dataclass
class IntakeTape:
    """An immutable-by-discipline log of live observations. The deterministic core
    reads only this; it never re-polls the live world."""

    entries: List[TapeEntry] = field(default_factory=list)

    def record(self, tick: int, raws: List[Dict[str, Any]]) -> List[TapeEntry]:
        recorded = []
        base = len(self.entries)
        for i, raw in enumerate(raws):
            entry = TapeEntry(tick=tick, index=base + i, raw=dict(raw))
            self.entries.append(entry)
            recorded.append(entry)
        return recorded

    def at(self, tick: int) -> List[Dict[str, Any]]:
        return [e.raw for e in self.entries if e.tick == tick]

    def to_list(self) -> List[Dict[str, Any]]:
        return [{"tick": e.tick, "index": e.index, "raw": e.raw} for e in self.entries]

    @classmethod
    def from_list(cls, rows: List[Dict[str, Any]]) -> "IntakeTape":
        tape = cls()
        for row in rows:
            tape.entries.append(
                TapeEntry(tick=int(row["tick"]), index=int(row["index"]), raw=dict(row["raw"]))
            )
        return tape


class ScriptedSource:
    """A LiveSource that replays a fixed list of observation batches (or single dicts).

    The stand-in for any real adapter while it's stubbed — a real `pc_telemetry` /
    `ring_telemetry` source swaps its `poll()` for OS metrics / the COLMI R02 BLE feed; nothing
    downstream changes.
    """

    def __init__(self, events: List[Any]) -> None:
        self._events = list(events)
        self._i = 0

    def poll(self) -> List[Dict[str, Any]]:
        if self._i >= len(self._events):
            return []
        e = self._events[self._i]
        self._i += 1
        return list(e) if isinstance(e, list) else [e]


@runtime_checkable
class LiveSource(Protocol):
    """The only thing a real push/live adapter must implement.

    ``poll()`` returns the raw observations available *right now* (since the last poll).
    ATL: new league events / standings deltas. MoCo: classified motion-intent frames.
    """

    def poll(self) -> List[Dict[str, Any]]:
        ...


class LiveFeedOrgan:
    """A push/live ``SimulationOrgan`` backed by a source (record mode) or a tape (replay).

    Record mode (``source`` given): each advance tick polls the source and records to the
    tape — this is the once-only live intake. Replay mode (``tape`` given, no source): each
    advance tick reads the recorded slice — deterministic reconstruction. Same organ, same
    bus, proving the live/deterministic boundary.
    """

    def __init__(
        self,
        name: str,
        *,
        source: Optional[LiveSource] = None,
        tape: Optional[IntakeTape] = None,
        normalizer: Callable[[str, int, int, Dict[str, Any]], NormalizedCandidate] = normalize_event,
    ) -> None:
        if source is None and tape is None:
            raise ValueError("a LiveFeedOrgan needs a source (record) or a tape (replay)")
        self._name = name
        self._source = source
        self._tape = tape if tape is not None else IntakeTape()
        self._replay = source is None
        self._normalizer = normalizer
        self._tick = 0

    @classmethod
    def replay_from(cls, name: str, tape: IntakeTape, **kw: Any) -> "LiveFeedOrgan":
        return cls(name, source=None, tape=tape, **kw)

    @property
    def tape(self) -> IntakeTape:
        return self._tape

    # -- the five verbs --------------------------------------------------- #
    def identity(self) -> OrganIdentity:
        # Live by nature: a live source cannot be recomputed, only recorded/replayed.
        # (A replay organ is still classed LIVE — its determinism comes from the tape,
        # not from being a total function of a seed.)
        return OrganIdentity(name=self._name, determinism=Determinism.LIVE, seed=None)

    def advance(self, to_tick: int) -> TickDelta:
        frm = self._tick
        events: List[Dict[str, Any]] = []
        for t in range(frm + 1, to_tick + 1):
            if self._replay:
                batch = self._tape.at(t)
            else:
                batch = list(self._source.poll())
                self._tape.record(t, batch)
            for raw in batch:
                ev = dict(raw)
                ev.setdefault("ts", float(t))
                events.append(ev)
        self._tick = to_tick
        return TickDelta(
            from_tick=frm,
            to_tick=to_tick,
            events=events,
            predictions=[],
            heat=min(1.0, len(events) / 10.0),
            headline=f"{self._name}: {len(events)} live observations",
        )

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        return [
            self._normalizer(self._name, delta.to_tick, i, ev)
            for i, ev in enumerate(delta.events)
        ]

    def read_truth(self) -> Dict[str, Any]:
        return {
            "tick": self._tick,
            "mode": "replay" if self._replay else "live",
            "recorded": len(self._tape.entries),
        }

    def apply_input(self, event: Dict[str, Any]) -> None:
        # Live organs observe the world; operator input doesn't rewrite a live feed.
        return
