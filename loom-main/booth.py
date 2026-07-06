"""Headless booth engine — the whole instrument as ONE call, no UI.

Folds antenna (multi-tape mix) + threads + inquiry + color + the mixer faders into a single
render. Used by the web UI (loom_serve.py) and available to the CLI, so desktop, phone, and
script all drive the same engine. Endpoint layer (color uses the pluggable LLM).
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from oradio_engine.speech import Grammar
from oradio_engine.thread import narrate_salient

GRAMMARS_DIR = "data/grammars"
VERBS = "data/english/irregular_verbs.json"


def render_session(antenna, mixer, *, rules: Optional[list] = None, inquiry: Any = None
                   ) -> Tuple[List[Tuple[Any, str]], List[Tuple[str, str]]]:
    """(stories, questions) for the antenna's enabled tapes under the mixer's settings."""
    grammar = Grammar.from_file(f"{GRAMMARS_DIR}/{mixer.voice}.json", verbs=VERBS)
    events = antenna.stream()

    stories = narrate_salient(events, grammar, depth=mixer.depth, rules=rules,
                              min_priority=mixer.salience, flavour=mixer.flavour,
                              continuity=mixer.continuity)

    if mixer.color:                                   # guarded LLM flair (falls back to the mirror)
        from colorist import Colorist
        entities = set()
        for e in events:
            if e.get("actor"):
                entities.add(e["actor"])
            o = e.get("object", "")
            if o and o[:1].isupper():
                entities.add(o)
        c = Colorist(mixer.color_model)
        stories = [(lap, c.colorize(line, entities)) for lap, line in stories]

    questions: List[Tuple[str, str]] = []
    if mixer.curiosity and inquiry is not None:
        from oradio_engine.inquiry import investigate
        ev = [dict(e) for e in events]
        questions = investigate(ev, grammar, inquiry.ask(ev, curiosity=mixer.curiosity),
                                depth=mixer.depth, rules=rules)
    return stories, questions
