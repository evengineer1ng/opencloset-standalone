"""Bake a finished F1 race into a thin-wire event tape (JSON), using the deterministic
event-detector. FastF1 (heavy) lives ONLY here — run once; the engine then replays the JSON
through the generic `tape_replay` source with no FastF1 at runtime. Tape philosophy, literal.

    python -m tools.bake_f1 --round 7 --out data/f1_barcelona_2026.json

Prints a non-spoiler summary only (counts + event types), never positions or the result.
"""
from __future__ import annotations

import argparse
import json
import os
import warnings
from collections import Counter


def _row(text: str, priority: float, tags: list) -> dict:
    return {"title": text, "body": text, "type": "f1", "priority": priority, "tags": tags}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    warnings.filterwarnings("ignore")
    import fastf1
    from oradio_engine.detect import overtakes, running_extrema

    os.makedirs("data/f1_cache", exist_ok=True)
    fastf1.Cache.enable_cache("data/f1_cache")
    session = fastf1.get_session(args.year, args.round, "R")
    session.load(laps=True, telemetry=False, weather=False, messages=False)
    laps = session.laps

    # abbreviation/number -> last name, for readable narration
    names: dict = {}
    try:
        for _, r in session.results.iterrows():
            names[str(r.get("Abbreviation"))] = r.get("LastName") or r.get("Abbreviation")
            names[str(r.get("DriverNumber"))] = r.get("LastName") or r.get("Abbreviation")
    except Exception:
        pass

    def nm(code) -> str:
        return str(names.get(str(code), code))

    max_lap = int(laps["LapNumber"].max())

    # per-lap position frames -> overtakes (relational events)
    frames = []
    for lap_no in range(1, max_lap + 1):
        lap_rows = laps[laps["LapNumber"] == lap_no]
        frame = {}
        for _, row in lap_rows.iterrows():
            pos = row["Position"]
            if pos == pos:  # not NaN
                frame[row["Driver"]] = int(pos)
        frames.append(frame)

    # which (driver, lap) involved a pit in/out — so we can reject pit-cycle "overtakes"
    pitted = set()
    for _, row in laps.iterrows():
        if row["PitInTime"] == row["PitInTime"] or row["PitOutTime"] == row["PitOutTime"]:
            pitted.add((row["Driver"], int(row["LapNumber"])))

    def pit_related(drv, lap) -> bool:
        return (drv, lap) in pitted or (drv, lap - 1) in pitted

    events = []  # (lap, priority, row)
    # SELECTION happens here (content determination): reject pit shuffles, keep battles near the
    # points, and promote lead changes. The narrator only ever sees what's worth saying.
    for step, gainer, loser in overtakes(frames):
        lap = step + 1
        if pit_related(gainer, lap) or pit_related(loser, lap):
            continue  # not an on-track pass — a pit cycle swapped them
        new_pos = frames[step].get(gainer)
        was_leading = frames[step - 1].get(loser) == 1
        if was_leading and new_pos == 1:                       # the lead changed hands on track
            events.append((lap, 0.97, _row(
                f"{nm(gainer)} takes the lead from {nm(loser)}", 0.97,
                ["f1", f"lap:{lap}", "actor:" + nm(gainer), "action:seize", "object:lead", "definite:1", "valence:hype"])))
            continue
        if new_pos is None or new_pos > 12:                    # ignore backmarker shuffling
            continue
        events.append((lap, 0.7, _row(
            f"{nm(gainer)} passes {nm(loser)} for P{new_pos}", 0.7,
            ["f1", f"lap:{lap}", "actor:" + nm(gainer), "action:overtake", "object:" + nm(loser), "valence:hype"])))

    # fastest lap: announce ONLY when the honour changes driver (not every micro-improvement)
    valid = laps[laps["LapTime"].notna() & laps["Time"].notna()].sort_values("Time")
    samples = [{"t": row["LapTime"].total_seconds(), "drv": row["Driver"], "lap": int(row["LapNumber"])}
               for _, row in valid.iterrows()]
    last_holder = None
    for _, s in running_extrema(samples, "t", mode="min"):
        if s["drv"] == last_holder:
            continue
        last_holder = s["drv"]
        lap = s["lap"]
        events.append((lap, 0.85, _row(
            f"{nm(s['drv'])} takes the fastest lap", 0.85,
            ["f1", f"lap:{lap}", "actor:" + nm(s["drv"]), "action:clock", "object:fastest_lap", "definite:1", "valence:hype"])))

    # pit stops — keep ALL of them as low-salience thread MATERIAL (a pit is the cause behind a
    # fresh-tyre fastest lap or an inherited lead). The threaded transcript seeds on the big
    # moments and pulls these in as causes; a flat reader can filter them out by priority.
    last_pit = {}
    for _, row in laps[laps["PitInTime"].notna()].sort_values("LapNumber").iterrows():
        drv, lap = nm(row["Driver"]), int(row["LapNumber"])
        if drv in last_pit and lap - last_pit[drv] <= 1:
            continue  # in/out-lap or duplicate timing = ONE stop (the bug the inquiry layer caught)
        last_pit[drv] = lap
        events.append((lap, 0.45, _row(
            f"{drv} pits", 0.45, ["f1", f"lap:{lap}", "actor:" + drv, "action:pit"])))

    events.sort(key=lambda e: (e[0], -e[1]))

    # STATE: stamp the ordinal (Nth time this actor did this action) onto each event, in order.
    # General (any domain), and it gives the narrator continuity ("pitted for the second time").
    seen = {}
    for _lap, _prio, row in events:
        actor = action = None
        for t in row["tags"]:
            if t.startswith("actor:"):
                actor = t.split(":", 1)[1]
            elif t.startswith("action:"):
                action = t.split(":", 1)[1]
        if actor and action:
            key = (actor, action)
            seen[key] = seen.get(key, 0) + 1
            row["tags"].append(f"ordinal:{seen[key]}")

    rows = [e[2] for e in events]

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)

    kinds = Counter(t.split(":", 1)[1] for r in rows for t in r["tags"] if t.startswith("action:"))
    print(f"baked {len(rows)} events -> {args.out}")
    print("event types:", dict(kinds))
    print("laps:", max_lap, "| drivers:", laps["Driver"].nunique())


if __name__ == "__main__":
    main()
