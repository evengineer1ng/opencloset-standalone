#!/usr/bin/env python3
"""Lap Review — the engineer's debrief. Garage 61 shows the trace; loom says what it means.

Input: a Night City Motorsports session JSON (race_telemetry.lua writes these to
`<mod>/telemetry/sessions/*.json`). It persists lap/sector times, top/avg speed, incidents,
and a progress trace (distanceAlong over t). We reconstruct a speed trace (Δdistance/Δt),
run loom's deterministic detector (oradio_engine.detect) to turn the continuous trace into
discrete events, and narrate a debrief — deterministic, no model.

    python integrations/ncm/lap_review.py [session.json]   # prints the debrief

(With no arg it reviews a baked example matching the real schema — the same one the portal
shows in REPLAY mode.)
"""
from __future__ import annotations

import json
import math
import os
import sys

# Reuse loom's detector brick (the F1 continuous→discrete primitives). Repo root is two up.
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
try:
    from oradio_engine.detect import threshold_crossings, running_extrema
except Exception:  # standalone fallback so this file runs anywhere
    def threshold_crossings(samples, key, level, *, direction="down", hysteresis=0.0):
        out, prev, armed = [], None, True
        for i, s in enumerate(samples):
            v = s.get(key)
            if v is None:
                continue
            v = float(v)
            if prev is not None:
                if direction == "down" and armed and prev > level >= v:
                    out.append((i, s)); armed = False
                elif direction == "up" and armed and prev < level <= v:
                    out.append((i, s)); armed = False
                if direction == "down" and v > level + hysteresis:
                    armed = True
                elif direction == "up" and v < level - hysteresis:
                    armed = True
            prev = v
        return out

    def running_extrema(samples, key, *, mode="max"):
        out, best = [], None
        for i, s in enumerate(samples):
            v = s.get(key)
            if v is None:
                continue
            v = float(v)
            if best is None or (mode == "max" and v > best) or (mode == "min" and v < best):
                best = v; out.append((i, s))
        return out

MS_TO_KPH = 3.6


def _fmt(seconds):
    if seconds is None:
        return "—"
    m, s = divmod(float(seconds), 60.0)
    return (f"{int(m)}:{s:05.2f}" if m else f"{s:.2f}s")


def _player_laps(session):
    return [lp for lp in (session.get("laps") or []) if lp.get("isPlayer", True) and lp.get("lapTime")]


def _speed_trace(session):
    """Reconstruct speed (km/h) over distance from the persisted progress trace."""
    samples = (session.get("traces") or {}).get("progressSamples") or []
    trace = []
    prev = None
    for s in samples:
        t, d = s.get("t"), s.get("playerDistanceAlong")
        if t is None or d is None:
            continue
        if prev is not None:
            dt, dd = t - prev[0], d - prev[1]
            if dt > 1e-3:
                trace.append({"t": t, "dist": d, "speed": max(0.0, dd / dt) * MS_TO_KPH})
        prev = (t, d)
    return trace


def review(session):
    """Return {summary, laps, trace, debrief[]} — the data the portal renders + the spoken debrief."""
    route = session.get("routeDisplayName") or session.get("routeId") or "Unknown route"
    laps = _player_laps(session)
    trace = _speed_trace(session)
    debrief = []

    if not laps:
        return {"summary": {"route": route}, "laps": [], "trace": trace,
                "debrief": [f"{route}: no completed laps on record yet."]}

    best = min(laps, key=lambda lp: lp["lapTime"])
    best_no = best.get("lapNumber", laps.index(best) + 1)
    debrief.append(f"{route} — {len(laps)} lap{'s' if len(laps) != 1 else ''}. "
                   f"Your best was lap {best_no}, {_fmt(best['lapTime'])}.")

    # sector story: where the best lap found / left time, vs your best in each sector
    sector_keys = ["sector1Time", "sector2Time", "sector3Time"]
    sect_best = {k: min((lp[k] for lp in laps if lp.get(k)), default=None) for k in sector_keys}
    deltas = [(i + 1, best.get(k), sect_best.get(k)) for i, k in enumerate(sector_keys)
              if best.get(k) and sect_best.get(k)]
    if deltas:
        worst = max(deltas, key=lambda d: d[1] - d[2])
        gap = worst[1] - worst[2]
        if gap > 0.05:
            debrief.append(f"S{worst[0]} is where it's hiding — you've gone {gap:.2f}s quicker there on another lap. "
                           f"String your sectors together and the lap drops.")
        else:
            debrief.append("Your best lap stitched all three sectors near your sector bests — that's a clean one.")

    # progression
    if len(laps) >= 2:
        drop = laps[0]["lapTime"] - laps[-1]["lapTime"]
        if drop > 0.1:
            debrief.append(f"You brought it down {drop:.1f}s from lap 1 to lap {laps[-1].get('lapNumber', len(laps))} — building into it.")
        elif drop < -0.1:
            debrief.append(f"You drifted {abs(drop):.1f}s slower than your opener — tiring, or chasing the setup the wrong way.")

    # incidents
    offs = sum(int(lp.get("offRouteCount", 0) or 0) for lp in laps)
    hits = sum(int(lp.get("majorCollisionCount", 0) or 0) for lp in laps)
    if offs or hits:
        bits = []
        if offs:
            bits.append(f"{offs} off-route")
        if hits:
            bits.append(f"{hits} contact")
        debrief.append("Tidy it up: " + ", ".join(bits) + ". Clean that and the time comes for free.")
    else:
        debrief.append("Clean session — no offs, no contact. The pace is real.")

    # speed + corners, from the reconstructed trace via the detector brick
    if trace:
        top_idx = running_extrema(trace, "speed", mode="max")
        top = trace[top_idx[-1][0]]["speed"] if top_idx else max(s["speed"] for s in trace)
        corner_level = 0.6 * top
        corners = threshold_crossings(trace, "speed", corner_level, direction="down", hysteresis=top * 0.08)
        slowest = min((s["speed"] for s in trace), default=0.0)
        debrief.append(f"Top speed {top:.0f} km/h. {len(corners)} heavy braking zone"
                       f"{'s' if len(corners) != 1 else ''}; slowest point {slowest:.0f} km/h — "
                       f"that's the corner that pays.")

    return {
        "summary": {"route": route, "bestLap": best["lapTime"], "laps": len(laps),
                    "best": _fmt(best["lapTime"])},
        "laps": [{"lap": lp.get("lapNumber", i + 1), "time": lp["lapTime"], "fmt": _fmt(lp["lapTime"]),
                  "s1": lp.get("sector1Time"), "s2": lp.get("sector2Time"), "s3": lp.get("sector3Time"),
                  "offs": lp.get("offRouteCount", 0), "best": lp is best}
                 for i, lp in enumerate(laps)],
        "trace": trace,
        "debrief": debrief,
    }


def example_session():
    """A baked session matching race_telemetry.lua's schema — REPLAY + tests."""
    # synth a progress trace: distance climbs, dips through two corners (speed varies)
    samples, d, t = [], 0.0, 0.0
    profile = [52, 56, 60, 38, 30, 44, 58, 61, 35, 28, 40, 55, 59, 50]  # m/s-ish, two slow points
    for v in profile:
        d += v * 0.5
        t += 0.5
        samples.append({"t": round(t, 2), "playerDistanceAlong": round(d, 1), "playerSector": 1 + (len(samples) // 5)})
    return {
        "sessionId": "sess_example",
        "routeId": "watson_loop",
        "routeDisplayName": "Watson Northside Loop",
        "lapCount": 3,
        "bestLap": 102.4,
        "recordFlags": {"sessionBestLap": True},
        "laps": [
            {"isPlayer": True, "lapNumber": 1, "lapTime": 106.2, "sector1Time": 34.1, "sector2Time": 40.0,
             "sector3Time": 32.1, "valid": True, "topSpeed": 58.0, "averageSpeed": 41.0, "offRouteCount": 1},
            {"isPlayer": True, "lapNumber": 2, "lapTime": 103.8, "sector1Time": 33.6, "sector2Time": 38.7,
             "sector3Time": 31.5, "valid": True, "topSpeed": 60.2, "averageSpeed": 42.3, "offRouteCount": 0},
            {"isPlayer": True, "lapNumber": 3, "lapTime": 102.4, "sector1Time": 33.4, "sector2Time": 38.2,
             "sector3Time": 30.8, "valid": True, "topSpeed": 61.0, "averageSpeed": 43.0, "offRouteCount": 0},
        ],
        "incidents": {"events": [{"name": "off_route", "t": 41.2, "data": {"lap": 1}}]},
        "traces": {"sampleRateHz": 2.0, "progressSamples": samples},
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    session = json.load(open(path, encoding="utf-8")) if path else example_session()
    out = review(session)
    print(f"=== Engineer's Debrief — {out['summary'].get('route')} ===\n")
    for line in out["debrief"]:
        print("  • " + line)
    print(f"\n  laps: " + ", ".join(f"L{l['lap']} {l['fmt']}{' *' if l['best'] else ''}" for l in out["laps"]))
    print(f"  trace: {len(out['trace'])} speed samples reconstructed from the progress trace")


if __name__ == "__main__":
    main()
