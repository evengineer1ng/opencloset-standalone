"""Inquiry — the tape asks questions about itself. Questions are the genome of threads.

A QUESTION is born where reality deviates from an EXPECTATION. You can't ask without an
expectation; challenging it = pulling the thread to check the tape. More active expectations ->
more deviations noticed -> more questions -> more threads. That is the CURIOSITY DIAL.

This layer is a DECLARATION, not a pile of domain rules (the same lesson as grammar): the
expectation TYPES are general — "an actor shouldn't repeat an action", "an effect should follow a
cause", "a streak shouldn't be undone" — and a domain (F1, rings, markets) INSTANTIATES them with
parameters (data/inquiry/*.json). The fourth tiny declaration:

    .loom = what world · .oradio = how it runs · grammar = how it speaks · inquiry = what it wonders

Deterministic; kin to EvidenceService (a prediction is an assumption the tape grades). Templated
questions are free; a genuinely novel one is an LLM-authored template at compile time, runtime pure.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from oradio_engine.speech import Grammar
from oradio_engine.thread import build_edges, weave

Event = Dict[str, Any]


def _lap(e: Event) -> Optional[int]:
    try:
        return int(e.get("lap"))
    except (TypeError, ValueError):
        return None


@dataclass
class Question:
    about: int          # the event index the question concerns (a thread seed)
    kind: str
    text: str
    salience: float = 0.9


# --- general expectation TYPES (domain-agnostic; a declaration instantiates them) ----------- #
def _no_repeat(events: Sequence[Event], spec: Dict[str, Any]) -> List[Question]:
    """An actor should not repeat ``action`` within ``within`` laps. (Your double-pit.)"""
    action, within = spec["action"], spec.get("within", 4)
    out: List[Question] = []
    last: Dict[str, Optional[int]] = {}
    for i, e in enumerate(events):
        if e.get("action") != action:
            continue
        actor, lap = e.get("actor"), _lap(e)
        prev = last.get(actor)
        if prev is not None and lap is not None and lap - prev <= within:
            gap = lap - prev
            out.append(Question(i, "no_repeat",
                                f"Why did {actor} {action} again only {gap} lap{'s' if gap != 1 else ''} after the last?"))
        last[actor] = lap if lap is not None else prev
    return out


def _expect_cause(events: Sequence[Event], spec: Dict[str, Any]) -> List[Question]:
    """An ``effect`` should follow a recent same-actor ``cause``; the effect with no cause -> question."""
    effect, cause, within = spec["effect"], spec["cause"], spec.get("within", 6)
    out: List[Question] = []
    for i, e in enumerate(events):
        if e.get("action") != effect:
            continue
        actor, lap = e.get("actor"), _lap(e)
        has_cause = any(
            c.get("action") == cause and c.get("actor") == actor and _lap(c) is not None
            and lap is not None and 0 <= lap - _lap(c) <= within
            for c in events[:i])
        if not has_cause:
            out.append(Question(i, "expect_cause",
                                f"How did {actor} manage the {effect} with no recent {cause}?"))
    return out


def _streak_then(events: Sequence[Event], spec: Dict[str, Any]) -> List[Question]:
    """A streak of ``action_a`` (>= ``count``) shouldn't be immediately undone by ``action_b``
    (a charge that stalls at the pit window). Deviation -> question."""
    action_a, action_b = spec["action_a"], spec["action_b"]
    count, within = spec.get("count", 2), spec.get("within", 2)
    out: List[Question] = []
    streak: Dict[str, Tuple[int, Optional[int]]] = {}  # actor -> (run length, last action_a lap)
    for i, e in enumerate(events):
        actor, act, lap = e.get("actor"), e.get("action"), _lap(e)
        if act == action_a:
            run, _ = streak.get(actor, (0, None))
            streak[actor] = (run + 1, lap)
        elif act == action_b:
            run, last_lap = streak.get(actor, (0, None))
            if run >= count and last_lap is not None and lap is not None and lap - last_lap <= within:
                out.append(Question(i, "streak_then",
                                    f"Did {actor}'s run of {run} {action_a}s stall when they {action_b} on lap {lap}?"))
            streak[actor] = (0, None)
    return out


EXPECTATION_TYPES: Dict[str, Callable[[Sequence[Event], Dict[str, Any]], List[Question]]] = {
    "no_repeat": _no_repeat,
    "expect_cause": _expect_cause,
    "streak_then": _streak_then,
}


class Inquiry:
    """An inquiry declaration: a list of expectation instances. ``level`` is the curiosity dial —
    each expectation has an ``at`` level; turn the dial up to activate deeper expectations."""

    def __init__(self, expectations: List[Dict[str, Any]]) -> None:
        self.expectations = expectations

    @classmethod
    def from_file(cls, path: str) -> "Inquiry":
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    def ask(self, events: Sequence[Event], *, curiosity: int = 99) -> List[Question]:
        out: List[Question] = []
        for spec in self.expectations:
            if spec.get("at", 1) > curiosity:
                continue
            fn = EXPECTATION_TYPES.get(spec.get("type"))
            if fn:
                out.extend(fn(events, spec))
        out.sort(key=lambda q: q.about)
        return out


def investigate(events: Sequence[Event], grammar: Grammar, questions: Sequence[Question], *,
                depth: int = 2, rules: Optional[List[Dict[str, Any]]] = None) -> List[Tuple[str, str]]:
    """Pull the thread around each question to (try to) answer it: (question, traced context).
    If the thread holds no cause, the question stands unanswered — which fingers the detector."""
    cause, effect = build_edges(events, rules)
    return [(q.text, weave(events, q.about, grammar, depth=depth, cause=cause, effect=effect))
            for q in questions]
