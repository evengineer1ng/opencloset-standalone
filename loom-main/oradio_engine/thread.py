"""The thread-puller — the loom. `to loom = pull a thread and trace it`.

Smartness is causal REACH, not verbosity: how far we trace the event graph from a salient seed.
``depth`` is the dial; every hop adds a clause EARNED by a link.

Two kinds of causal edge:
  - TYPED (declared, domain): a rule says cause-action -> effect-action within N laps, carrying a
    causal PHRASE ("On fresh tyres, ") that folds the cause into an insight. This is real causality.
  - SEQUENTIAL (free, general): same-actor adjacency, a cheap proxy ("After X pitted, he ...").

SALIENCE-SEEDED: a real transcript pulls threads only from important seeds and marks events a
thread consumes, so the flat list of rows becomes a handful of traced stories — verbosity spent as
a budget allocated by salience, not raw volume.

Deterministic, domain-agnostic mechanism (the rules are domain data, passed in). Pure stdlib.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from oradio_engine.speech import Grammar, _capitalize_sentences

Event = Dict[str, Any]
Edge = Optional[Tuple[int, str]]  # (other_index, causal_phrase or "")


def actor_chains(events: Sequence[Event]) -> Tuple[List[Optional[int]], List[Optional[int]]]:
    """Previous/next event index with the SAME actor — the free sequential thread."""
    n = len(events)
    prev: List[Optional[int]] = [None] * n
    nxt: List[Optional[int]] = [None] * n
    last: Dict[str, int] = {}
    for i, e in enumerate(events):
        actor = e.get("actor")
        if not actor:
            continue
        if actor in last:
            prev[i] = last[actor]
            nxt[last[actor]] = i
        last[actor] = i
    return prev, nxt


def _within(events: Sequence[Event], j: int, i: int, within: int) -> bool:
    try:
        return abs(int(events[i].get("lap")) - int(events[j].get("lap"))) <= within
    except (TypeError, ValueError):
        return True


def build_edges(events: Sequence[Event], rules: Optional[List[Dict[str, Any]]] = None
                ) -> Tuple[List[Edge], List[Edge]]:
    """Causal graph over the tape. Typed rules first (real causality + phrase), then same-actor
    sequence as the free fallback. Returns (cause_of[i], effect_of[i])."""
    rules = rules or []
    n = len(events)
    cause: List[Edge] = [None] * n
    for i, e in enumerate(events):
        for r in rules:
            if e.get("action") != r.get("effect"):
                continue
            for j in range(i - 1, -1, -1):
                c = events[j]
                if c.get("action") != r.get("cause"):
                    continue
                if r.get("same_actor") and c.get("actor") != e.get("actor"):
                    continue
                if r.get("cross_actor") and c.get("actor") == e.get("actor"):
                    continue
                if not _within(events, j, i, r.get("within", 9999)):
                    continue
                cause[i] = (j, r.get("reason") or r.get("phrase", ""))  # reason TOKEN (domain); phrasing resolves in the grammar
                break
            if cause[i]:
                break
    prev, _ = actor_chains(events)
    for i in range(n):
        if cause[i] is None and prev[i] is not None:
            cause[i] = (prev[i], "")          # sequential fallback, generic phrasing
    effect: List[Edge] = [None] * n
    for i, c in enumerate(cause):
        if c and effect[c[0]] is None:
            effect[c[0]] = (i, c[1])
    return cause, effect


def _view(e: Event, continuity: bool) -> Event:
    """Drop carried state (the continuity fader) when the mixer turns continuity down."""
    if continuity or "ordinal" not in e:
        return e
    v = dict(e)
    v.pop("ordinal", None)
    return v


def pull(cause: List[Edge], effect: List[Edge], seed: int, depth: int, *, flavour: str = "both"
         ) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str]]]:
    # flavour = DIRECTION of the pull: chase the cause (back), the consequence (forward), or split.
    if flavour == "back":
        cause_hops, fwd_hops = depth, 0
    elif flavour == "forward":
        cause_hops, fwd_hops = 0, depth
    else:
        cause_hops, fwd_hops = (depth + 1) // 2, depth // 2
    causes: List[Tuple[int, str]] = []
    cur = seed
    for _ in range(cause_hops):
        ce = cause[cur]
        if not ce:
            break
        causes.append(ce)
        cur = ce[0]
    causes.reverse()
    fwds: List[Tuple[int, str]] = []
    cur = seed
    for _ in range(fwd_hops):
        fe = effect[cur]
        if not fe:
            break
        fwds.append(fe)
        cur = fe[0]
    return causes, fwds


def weave(events: Sequence[Event], seed: int, grammar: Grammar, *, depth: int = 0,
          flavour: str = "both", continuity: bool = True,
          cause: Optional[List[Edge]] = None, effect: Optional[List[Edge]] = None,
          rules: Optional[List[Dict[str, Any]]] = None) -> str:
    if cause is None or effect is None:
        cause, effect = build_edges(events, rules)
    causes, fwds = pull(cause, effect, seed, depth, flavour=flavour)
    seed_actor = events[seed].get("actor")

    def cl(i: int, subject: Optional[str] = None) -> str:
        return grammar.clause(_view(events[i], continuity), subject=subject)

    # the immediate cause, if TYPED, folds into a phrase ("On fresh tyres, ...") instead of a clause.
    # The reason TOKEN comes from the domain rule; the GRAMMAR phrases it in its own voice. (A literal
    # phrase in the rule still works as a fallback — backward compatible.)
    lead_phrase = ""
    rendered = list(causes)
    if rendered and rendered[-1][1]:
        token = rendered[-1][1]
        lead_phrase = grammar.reason_phrase(token) or (token if " " in token else "")
        if lead_phrase:
            rendered = rendered[:-1]

    parts: List[str] = []
    for j, _ph in rendered:
        parts.append("After " + cl(j) + ", ")

    seed_subject = None
    if not lead_phrase and rendered and events[rendered[-1][0]].get("actor") == seed_actor:
        seed_subject = events[seed].get("pronoun")
    parts.append(lead_phrase + cl(seed, subject=seed_subject))

    last_actor = seed_actor
    for k, _ph in fwds:
        e = events[k]
        subject = e.get("pronoun") if (e.get("actor") == last_actor and e.get("pronoun")) else None
        parts.append(", and then " + cl(k, subject=subject))
        last_actor = e.get("actor")

    text = _capitalize_sentences("".join(parts).strip())
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return text


def narrate_salient(events: Sequence[Event], grammar: Grammar, *, depth: int = 2,
                    rules: Optional[List[Dict[str, Any]]] = None, min_priority: float = 0.7,
                    flavour: str = "both", continuity: bool = True) -> List[Tuple[Any, str]]:
    """Thread only the salient seeds; mark consumed events so nothing is told twice. Returns
    (lap, line) pairs — the flat tape becomes a few traced stories."""
    cause, effect = build_edges(events, rules)
    consumed = set()
    out: List[Tuple[Any, str]] = []
    for i, e in enumerate(events):
        if float(e.get("priority", 0)) < min_priority or i in consumed:
            continue
        causes, fwds = pull(cause, effect, i, depth, flavour=flavour)
        out.append((e.get("lap"), weave(events, i, grammar, depth=depth, flavour=flavour,
                                        continuity=continuity, cause=cause, effect=effect)))
        consumed.add(i)
        for j, _ in causes:
            consumed.add(j)
        for k, _ in fwds:
            consumed.add(k)
    return out
