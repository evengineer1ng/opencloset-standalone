"""The Antenna — load many tapes, toggle each on/off, hear the mix.

Radio-OS's antenna idea, distilled: every SOURCE (a baked tape — F1 lap data, an RSS feed,
basketball play-by-play, your ring) is just events. The antenna interleaves the ENABLED sources
onto one stream, each event tagged with where it came from. Flip a source and its lane drops in or
out. A new feed type is just a new baker that writes a tape; the antenna already accepts it.

Pure stdlib. Heterogeneous by design: structured events (roles -> grammar/threads) and raw
headlines (no roles -> spoken verbatim) ride the same stream.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from oradio_engine.speech import roles_from_tags

Event = Dict[str, Any]


def load_tape(path: str) -> List[Event]:
    """A baked thin-wire tape (JSON list of rows) -> event dicts the booth/narrator consume."""
    rows = json.load(open(path, "r", encoding="utf-8"))
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    out: List[Event] = []
    for r in rows:
        e = roles_from_tags(r.get("tags", []))
        e["lap"] = next((t.split(":", 1)[1] for t in r.get("tags", []) if t.startswith("lap:")), "")
        e["priority"] = r.get("priority", 0.5)
        e["body"] = r.get("body") or r.get("title") or ""
        e["title"] = r.get("title") or ""
        out.append(e)
    return out


@dataclass
class Source:
    name: str
    events: List[Event]
    enabled: bool = True
    kind: str = ""

    @classmethod
    def from_tape(cls, name: str, path: str, *, kind: str = "", enabled: bool = True) -> "Source":
        return cls(name=name, events=load_tape(path), enabled=enabled, kind=kind)


@dataclass
class Antenna:
    sources: List[Source] = field(default_factory=list)

    def add(self, source: Source) -> "Antenna":
        self.sources.append(source)
        return self

    def toggle(self, name: str, on: Optional[bool] = None) -> None:
        for s in self.sources:
            if s.name == name:
                s.enabled = (not s.enabled) if on is None else bool(on)

    def names(self) -> List[str]:
        return [s.name for s in self.sources]

    def stream(self) -> List[Event]:
        """Interleave the ENABLED sources round-robin (the mix), tagging each event's source."""
        lanes = [(s.name, s.events) for s in self.sources if s.enabled]
        out: List[Event] = []
        if not lanes:
            return out
        for i in range(max(len(ev) for _, ev in lanes)):
            for name, ev in lanes:
                if i < len(ev):
                    e = dict(ev[i])
                    e["source"] = name
                    out.append(e)
        return out
