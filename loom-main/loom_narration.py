#!/usr/bin/env python3
"""Tier 1 narration — deterministic, provider-free.

The forkuniverse bus surfaces abstract engine events (`thread_opened`,
`prediction_settled`, `contract_warning`). On their own they read the same in a
goosebumps neighborhood and a corporate boardroom. This module turns each beat
into a sentence grounded in *this* world by combining three things that ARE
specific to the loom:

  - the candidate ``body`` (carries the actual thread question / outcome),
  - the world's ``universe_title`` (so the line names this world),
  - the author's ``genre_mix`` / ``tone_mix`` (so the framing matches the idea).

It is deterministic and unit-testable: the same beat in the same world yields the
same line. This is the proof that you can *listen into* a loom with no LLM. The
optional Tier 2 LLM dressing layers genre prose on top and falls back to this.

The narrator is, in spirit, an OUTPUT/format plugin over the bus; it lives here
inline for now and can be extracted to a plugin later.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

# A short framing clause per genre, grounding the beat in {world}.
GENRE_ATMOSPHERE: Dict[str, str] = {
    "horror": "A chill moves through {world}",
    "thriller": "The tension coils in {world}",
    "mystery": "A question hangs over {world}",
    "drama": "The mood shifts across {world}",
    "comedy": "Something absurd ripples through {world}",
    "romance": "Hearts stir in {world}",
    "sci-fi": "The systems of {world} hum",
    "fantasy": "An old power turns beneath {world}",
}
DEFAULT_ATMOSPHERE = "Something moves in {world}"

# A noun the tone contributes to the frame (kept distinct from genre wording).
TONE_NOUN: Dict[str, str] = {
    "dread": "dread",
    "tense": "unease",
    "hopeful": "hope",
    "comedic": "mischief",
    "melancholic": "sorrow",
    "wondrous": "wonder",
}


def dominant_key(mix: Optional[Dict[str, float]]) -> Optional[str]:
    """The highest-weighted key, with a deterministic alphabetical tie-break."""
    if not mix:
        return None
    return max(sorted(mix), key=lambda key: mix[key])


def _strip_prefix(body: str) -> str:
    text = body.strip()
    text = re.sub(r"^New open question:\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


class Narrator:
    """Renders bus candidates into world-grounded, genre-framed sentences."""

    def __init__(
        self,
        universe_title: str,
        genre_mix: Optional[Dict[str, float]] = None,
        tone_mix: Optional[Dict[str, float]] = None,
    ) -> None:
        self.world = (universe_title or "this world").strip() or "this world"
        self.genre = dominant_key(genre_mix)
        self.tone = dominant_key(tone_mix)

    @classmethod
    def from_descriptor(cls, descriptor: Dict) -> "Narrator":
        world = descriptor.get("world") if isinstance(descriptor.get("world"), dict) else {}
        creation = world.get("creation") if isinstance(world.get("creation"), dict) else {}
        title = creation.get("universe_title") or descriptor.get("oradio") or "this world"
        return cls(
            universe_title=str(title),
            genre_mix=creation.get("genre_mix") if isinstance(creation.get("genre_mix"), dict) else None,
            tone_mix=creation.get("tone_mix") if isinstance(creation.get("tone_mix"), dict) else None,
        )

    def _frame(self) -> str:
        atmosphere = GENRE_ATMOSPHERE.get(self.genre or "", DEFAULT_ATMOSPHERE).format(world=self.world)
        if self.tone and self.tone in TONE_NOUN:
            return f"{atmosphere}, thick with {TONE_NOUN[self.tone]}"
        return atmosphere

    def _core(self, event_type: str, body: str) -> str:
        """The 'what happened', drawn from the candidate body so it stays grounded."""
        clean = _strip_prefix(body)
        if not clean:
            clean = event_type.replace("_", " ")
        if event_type == "thread_opened":
            return f"a new thread opens — {clean}"
        if event_type == "thread_resolved":
            return f"a thread closes — {clean}"
        if event_type == "prediction_opened":
            return f"a forecast is cast — {clean}"
        if event_type == "prediction_settled":
            return f"a forecast comes to pass — {clean}"
        if event_type == "obligation_breach":
            return f"a promise breaks — {clean}"
        if event_type in ("contract_warning", "contract_resolved"):
            return clean
        return clean

    def line(self, candidate) -> str:
        """Narrate one bus candidate. Accepts anything with ``type`` and ``body``."""
        event_type = getattr(candidate, "type", "") or ""
        body = getattr(candidate, "body", "") or getattr(candidate, "title", "") or ""
        core = self._core(event_type, body)
        return f"{self._frame()}: {core}"
