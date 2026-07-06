"""ATL / League shim — the PUSH/live evidence organ.

ATL is a running league server that persists everything to ``league.sqlite``. So the organ
does NOT import wanda's monolith or run docker — it's a PUSH source that polls the DB
(read-only) for new rows since a cursor, exactly the antenna model. It surfaces:

  - **event candidates** from append-only event tables (dev_runtime_events, timeline_posts),
  - **gradable predictions** from claim tables (ml_promotion_recommendations) into the
    evidence/calibration service.

Determinism class = LIVE: polled rows are recorded to an ``IntakeTape`` so a run can be
replayed byte-for-byte (the live/deterministic boundary). The real ATL server is the source;
the adapter is just `poll()` over its emitted truth.

    advance(to_tick)  -> poll new rows since cursor; record to tape; events + predictions
    observe(delta)    -> normalize event rows -> NormalizedCandidate
    read_truth()      -> cursor positions + counts
    apply_input(e)    -> no-op (you don't rewrite a live league)
    identity()        -> LIVE

Config is schema-driven (below) so it tracks the real league.sqlite columns and is easy to
extend to more tables (research_threads, ml_descendant_hypotheses, backtest_results...).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from oradio_engine.contract import (
    Determinism,
    NormalizedCandidate,
    OrganIdentity,
    TickDelta,
    normalize_event,
)
from oradio_engine.live import IntakeTape

# table -> column mapping for event candidates (append-only, integer id ascending)
EVENT_TABLES: Dict[str, Dict[str, str]] = {
    "dev_runtime_events": {"id": "id", "ts": "created_at", "title": "title", "body": "details", "type": "event_type"},
    "timeline_posts": {"id": "id", "ts": "created_at", "title": "title", "body": "observation", "type": "category"},
}

# table -> column mapping for gradable claims (predictions)
PREDICTION_TABLES: Dict[str, Dict[str, str]] = {
    "ml_promotion_recommendations": {
        "id": "id", "ts": "created_at", "claim": "candidate_name", "verdict": "recommendation",
        "rationale": "rationale",
    },
}

# a recommendation is a forward claim; map its strength to a confidence.
_VERDICT_CONFIDENCE = {"promote": 0.8, "ship": 0.8, "hold": 0.3, "reject": 0.1, "kill": 0.1}


class ATLOrgan:
    """Adapts ATL's ``league.sqlite`` to a PUSH/live ``SimulationOrgan``."""

    def __init__(
        self,
        name: str,
        *,
        db_path: Optional[str] = None,
        tape: Optional[IntakeTape] = None,
        cursors: Optional[Dict[str, int]] = None,
    ) -> None:
        self._name = name
        self._db_path = db_path
        self._tape = tape if tape is not None else IntakeTape()
        self._replay = db_path is None
        self._cursors: Dict[str, int] = cursors or {}
        self._tick = 0

    @classmethod
    def from_db(cls, name: str, db_path: str) -> "ATLOrgan":
        return cls(name, db_path=db_path)

    @classmethod
    def replay_from(cls, name: str, tape: IntakeTape) -> "ATLOrgan":
        return cls(name, db_path=None, tape=tape)

    @property
    def tape(self) -> IntakeTape:
        return self._tape

    # -- polling ---------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def _table_exists(self, cur: sqlite3.Cursor, table: str) -> bool:
        return cur.execute(
            "select 1 from sqlite_master where type='table' and name=?", (table,)
        ).fetchone() is not None

    def _poll_all(self) -> List[Dict[str, Any]]:
        """Read every new row past the cursor in each configured table. Each returned dict
        is already flat (candidate- or prediction-shaped) and carries ``_atl_kind`` so
        replay can split them without re-querying."""
        raws: List[Dict[str, Any]] = []
        con = self._connect()
        try:
            cur = con.cursor()
            for table, m in EVENT_TABLES.items():
                if not self._table_exists(cur, table):
                    continue
                last = self._cursors.get(table, 0)
                rows = cur.execute(
                    f'select * from "{table}" where "{m["id"]}" > ? order by "{m["id"]}" asc',
                    (last,),
                ).fetchall()
                for row in rows:
                    rid = int(row[m["id"]])
                    self._cursors[table] = max(self._cursors.get(table, 0), rid)
                    raws.append({
                        "_atl_kind": "event",
                        "title": str(row[m["title"]] or table),
                        "body": str(row[m["body"]] or ""),
                        "type": str(row[m["type"]] or "event"),
                        "priority": 0.4,
                        "ts": str(row[m["ts"]] or ""),
                        "tags": ["atl", table, str(row[m["type"]] or "")],
                    })
            for table, m in PREDICTION_TABLES.items():
                if not self._table_exists(cur, table):
                    continue
                last = self._cursors.get(table, 0)
                rows = cur.execute(
                    f'select * from "{table}" where "{m["id"]}" > ? order by "{m["id"]}" asc',
                    (last,),
                ).fetchall()
                for row in rows:
                    rid = int(row[m["id"]])
                    self._cursors[table] = max(self._cursors.get(table, 0), rid)
                    verdict = str(row[m["verdict"]] or "").lower()
                    raws.append({
                        "_atl_kind": "prediction",
                        "prediction_id": f"{table}:{rid}",
                        "claim_type": verdict or "recommendation",
                        "confidence": _VERDICT_CONFIDENCE.get(verdict, 0.5),
                        "status": "open",
                        "title": f"Recommendation: {row[m['claim']]}",
                        "body": str(row[m["rationale"]] or ""),
                        "ts": str(row[m["ts"]] or ""),
                    })
        finally:
            con.close()
        return raws

    # -- the five verbs --------------------------------------------------- #
    def identity(self) -> OrganIdentity:
        return OrganIdentity(name=self._name, determinism=Determinism.LIVE, seed=None)

    def advance(self, to_tick: int) -> TickDelta:
        frm = self._tick
        collected: List[Dict[str, Any]] = []
        for t in range(frm + 1, to_tick + 1):
            if self._replay:
                batch = self._tape.at(t)
            else:
                batch = self._poll_all()
                self._tape.record(t, batch)
            collected.extend(batch)
        self._tick = to_tick

        events = [r for r in collected if r.get("_atl_kind") == "event"]
        predictions = [
            {k: v for k, v in r.items() if k != "_atl_kind"}
            for r in collected
            if r.get("_atl_kind") == "prediction"
        ]
        return TickDelta(
            from_tick=frm,
            to_tick=to_tick,
            events=events,
            predictions=predictions,
            heat=min(1.0, len(events) / 10.0),
            headline=f"{self._name}: {len(events)} league events, {len(predictions)} claims",
        )

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        return [normalize_event(self._name, delta.to_tick, i, ev) for i, ev in enumerate(delta.events)]

    def read_truth(self) -> Dict[str, Any]:
        return {
            "tick": self._tick,
            "mode": "replay" if self._replay else "live",
            "cursors": dict(self._cursors),
            "recorded": len(self._tape.entries),
        }

    def apply_input(self, event: Dict[str, Any]) -> None:
        return
