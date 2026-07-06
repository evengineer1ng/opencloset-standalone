"""Event detection — deterministic continuous→discrete. Domain-agnostic, no ML, pure stdlib.

Telemetry is continuous (speed, heart rate, track position); nothing is narratable until a
*notable* thing is detected. These four primitives turn a stream of samples into the discrete
events the speech kernel can speak. An F1 overtake and a heart-rate zone crossing are the same
shapes — only the data differs. This is the deterministic analysis layer: it makes telemetry
*mean* something without a model.

Each primitive returns plain indices/tuples; turning those into role-bearing rows is the
domain plugin's job (see tools/bake_f1.py). Pure stdlib so it stays reusable everywhere.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

Sample = Dict[str, Any]


def threshold_crossings(
    samples: Sequence[Sample], key: str, level: float,
    *, direction: str = "up", hysteresis: float = 0.0,
) -> List[Tuple[int, Sample]]:
    """Indices where ``key`` crosses ``level`` (with hysteresis so it re-arms cleanly).
    e.g. heart rate crossing 120 bpm 'up', or a gap dropping under 1.0s 'down'."""
    out: List[Tuple[int, Sample]] = []
    prev = None
    armed = True
    for i, s in enumerate(samples):
        raw = s.get(key)
        if raw is None:
            continue
        v = float(raw)
        if prev is not None:
            if direction == "up" and armed and prev < level <= v:
                out.append((i, s)); armed = False
            elif direction == "down" and armed and prev > level >= v:
                out.append((i, s)); armed = False
            if direction == "up" and v < level - hysteresis:
                armed = True
            elif direction == "down" and v > level + hysteresis:
                armed = True
        prev = v
    return out


def running_extrema(samples: Sequence[Sample], key: str, *, mode: str = "max") -> List[Tuple[int, Sample]]:
    """Indices where a new running max/min is set — e.g. a new fastest lap (mode='min')."""
    out: List[Tuple[int, Sample]] = []
    best = None
    for i, s in enumerate(samples):
        raw = s.get(key)
        if raw is None:
            continue
        v = float(raw)
        if best is None or (mode == "max" and v > best) or (mode == "min" and v < best):
            best = v
            out.append((i, s))
    return out


def overtakes(frames: Sequence[Dict[Any, int]]) -> List[Tuple[int, Any, Any]]:
    """Relational events from ranked frames. ``frames[i]`` maps entity -> position (1=best)
    at step i. Yields ``(step, gainer, loser)`` for every pair whose order flipped — one
    overtake per pair, so a double-pass in one step is two events."""
    out: List[Tuple[int, Any, Any]] = []
    for i in range(1, len(frames)):
        prev, cur = frames[i - 1], frames[i]
        shared = [e for e in cur if e in prev]
        for a in shared:
            for b in shared:
                if a is b:
                    continue
                if prev[a] > prev[b] and cur[a] < cur[b]:   # a was behind b, now ahead
                    out.append((i, a, b))
    return out


def milestones(samples: Sequence[Sample], key: str, step: float) -> List[Tuple[int, Sample]]:
    """Indices where a cumulative ``key`` crosses each multiple of ``step`` — e.g. every
    1000 steps walked, or each lap completed."""
    out: List[Tuple[int, Sample]] = []
    last = 0
    for i, s in enumerate(samples):
        raw = s.get(key)
        if raw is None:
            continue
        m = int(float(raw) // step)
        if m > last:
            out.append((i, s))
            last = m
    return out
