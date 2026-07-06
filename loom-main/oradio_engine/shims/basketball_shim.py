"""Basketball play-by-play — a replay source over a finished game's event log.

A finished NBA game *is* a fixed, ordered event log: 500-ish plays, each stamped with a
period + clock. That is exactly the engine's intake-tape shape (``docs`` / live.py §3):
the game already happened, so there is nothing to *poll* live — there is a tape to
*replay*. Each engine tick surfaces the next play(s) as normalized candidates on the bus;
the same bus the voice surface narrates and the visual morph engine paints.

The rows are intentionally score-free in ``title``/``body`` (ESPN keeps the running score in
separate ``homeScore``/``awayScore`` fields, carried here as data for a scoreboard surface but
never folded into the narratable text) — so a viewer watches the game unfold without the
result being spelled out ahead of the moment.

Pure stdlib: reads a local JSON the ESPN summary endpoint produced. No network at runtime,
no heavy deps — ``import oradio_engine`` stays stdlib+PyYAML (tests/test_engine_purity.py).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from oradio_engine.live import LiveFeedOrgan, LiveSource


# Tag vocabularies chosen to land on existing visual-morph families
# (oradio_engine/visual_tape.py VISUAL_FAMILY_RULES): a made basket ripples,
# a three blooms / embers, a turnover or foul glitches. The play drives the paint.
def _play_to_event(play: Dict[str, Any]) -> Dict[str, Any]:
    ptype = (play.get("type") or {}).get("text") or "play"
    text = play.get("text") or play.get("shortDescription") or ptype
    period = (play.get("period") or {}).get("number")
    clock = (play.get("clock") or {}).get("displayValue") or ""
    team = (play.get("team") or {}).get("abbreviation") or ""
    scoring = bool(play.get("scoringPlay"))
    score_value = int(play.get("scoreValue") or 0)
    low = f"{ptype} {text}".lower()

    priority = 0.4
    if scoring:
        priority = 0.6 + 0.1 * min(score_value, 3)
    if "dunk" in low:
        priority = max(priority, 0.85)
    if "foul" in low or "turnover" in low:
        priority = max(priority, 0.5)

    tags: List[str] = ["nba", f"q{period}"]
    if team:
        tags.append(team.lower())
    if scoring:
        tags += ["make", "impact", "wave"]          # -> ripples
        if score_value >= 3:
            tags += ["heat", "flare"]                # -> embers / bloom
        if "dunk" in low:
            tags += ["conflict"]                     # -> embers / glitch
    else:
        if "miss" in low:
            tags.append("miss")
        if "foul" in low or "turnover" in low:
            tags += ["rupture", "pressure"]          # -> glitch

    return {
        "title": f"Q{period} {clock} · {ptype}".strip(),
        "body": str(text),
        "type": "play",
        "priority": round(min(1.0, priority), 2),
        "tags": tags,
        # data fields (for a scoreboard surface) — deliberately NOT in title/body:
        "period": period,
        "clock": clock,
        "team": team,
        "scoring": scoring,
        "score_value": score_value,
        "home_score": play.get("homeScore"),
        "away_score": play.get("awayScore"),
        "play_id": play.get("id"),
    }


def load_plays(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    plays = data.get("plays") if isinstance(data, dict) else data
    return list(plays or [])


class BasketballPlayByPlaySource:
    """A LiveSource that replays a finished game's plays, ``per_poll`` at a time."""

    def __init__(self, plays: List[Dict[str, Any]], *, per_poll: int = 1) -> None:
        if not plays:
            raise ValueError("basketball_pbp needs at least one play to replay")
        self._events = [_play_to_event(p) for p in plays]
        self._per_poll = max(1, int(per_poll))
        self._cursor = 0

    @property
    def remaining(self) -> int:
        return max(0, len(self._events) - self._cursor)

    def poll(self) -> List[Dict[str, Any]]:
        if self._cursor >= len(self._events):
            return []
        batch = self._events[self._cursor : self._cursor + self._per_poll]
        self._cursor += len(batch)
        return [dict(e) for e in batch]


def make_basketball_pbp(
    name: str = "court",
    *,
    path: Optional[str] = None,
    plays: Optional[List[Dict[str, Any]]] = None,
    per_poll: int = 1,
    **_: Any,
) -> LiveFeedOrgan:
    """Build a play-by-play organ. Give it a saved summary JSON ``path`` (preferred) or
    raw ``plays``. ``per_poll`` compresses N plays into one tick when you want the game to
    move faster than one play per beat."""
    if plays is None:
        if not path:
            raise ValueError("basketball_pbp needs a `path` to a saved play-by-play JSON (or `plays`)")
        plays = load_plays(path)
    source = BasketballPlayByPlaySource(plays, per_poll=per_poll)
    return LiveFeedOrgan(name, source=source)
