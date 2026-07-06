"""Generic tape-replay source — plays any pre-baked thin-wire tape (a JSON list of rows).

The clean pattern once a domain has a *bake* step (raw → thin-wire rows JSON, e.g.
tools/bake_f1.py): the runtime source carries NO domain code and NO heavy deps — it just reads
the tape and emits a row per poll. F1, the ring, anything pre-baked replays through this. Live
or recorded or computed, it's all the same once it's a tape.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from oradio_engine.live import LiveFeedOrgan


class TapeReplaySource:
    """Emits pre-built thin-wire rows in order, ``per_poll`` at a time."""

    def __init__(self, rows: List[Dict[str, Any]], *, per_poll: int = 1) -> None:
        if not rows:
            raise ValueError("tape_replay needs at least one row")
        self._rows = [dict(r) for r in rows]
        self._per_poll = max(1, int(per_poll))
        self._cursor = 0

    @property
    def remaining(self) -> int:
        return max(0, len(self._rows) - self._cursor)

    def poll(self) -> List[Dict[str, Any]]:
        if self._cursor >= len(self._rows):
            return []
        batch = self._rows[self._cursor:self._cursor + self._per_poll]
        self._cursor += len(batch)
        return [dict(r) for r in batch]


def make_tape_replay(name: str = "tape", *, path: Optional[str] = None,
                     rows: Optional[List[Dict[str, Any]]] = None, per_poll: int = 1, **_: Any) -> LiveFeedOrgan:
    if rows is None:
        if not path:
            raise ValueError("tape_replay needs a `path` to a rows JSON (or `rows`)")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("rows", data) if isinstance(data, dict) else data
    return LiveFeedOrgan(name, source=TapeReplaySource(rows, per_poll=per_poll))
