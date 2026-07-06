"""
Cyberpunk 2077 Game State Feed Plugin
======================================
Reads live game state exported by the CET (Cyber Engine Tweaks) Lua bridge mod
and emits StationEvents for ARIA — your in-game AI companion.

HOW IT WORKS
  1. Install the CET mod at stations/Cyberpunk2077FM/cet_mod/RadioOSBridge.lua
     into your CP2077 CET autorun folder.
  2. The mod writes a JSON file every ~0.5s:
       %USERPROFILE%\\RadioOSBridge\\cp2077_state.json
  3. This plugin polls that file, diffs the state, and emits events.

SIMULATION MODE
  Set sim_mode: true in the manifest feed config for testing without CP2077.
  ARIA will receive simulated game events so you can verify voice and LLM output.

EVENTS EMITTED (source = "cp2077_sdk")
  combat_started       — player entered combat
  combat_ended         — player left combat
  health_low           — player HP below threshold
  health_critical      — player HP extremely low (< 15%)
  player_death         — player flatlined
  wanted_level_up      — NCPD wanted level increased
  wanted_level_clear   — wanted level dropped to 0
  quest_updated        — active quest / objective changed
  location_changed     — player entered a new district or named area
  item_acquired        — notable item added to inventory
  vehicle_entered      — player entered a vehicle
  vehicle_exited       — player left a vehicle
  level_up             — player leveled up
  poi_nearby           — a point of interest is close
  enemy_spotted        — enemy detected in player vicinity
  game_started         — CP2077 session detected (state file appeared)
  game_stopped         — state file disappeared (game closed)
"""

from __future__ import annotations

import json
import os
import sys
import time
import random
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------
PLUGIN_NAME = "cp2077_sdk"
PLUGIN_DESC = "Live Cyberpunk 2077 game state feed — emits events for ARIA companion."
IS_FEED     = True

FEED_DEFAULTS: Dict[str, Any] = {
    "enabled":                  False,
    "state_file":               "",          # empty → auto-detect default path
    "poll_hz":                  2,
    "sim_mode":                 False,
    "announce_combat_start":    True,
    "announce_combat_end":      True,
    "announce_health_low":      True,
    "health_low_threshold":     0.35,
    "announce_wanted_level":    True,
    "announce_location_change": True,
    "announce_quest_update":    True,
    "announce_item_found":      True,
    "announce_enemy_spotted":   False,
    "announce_vehicle_entered": True,
    "announce_death":           True,
    "announce_level_up":        True,
    "announce_poi_nearby":      True,
    "poi_nearby_radius":        150,
}

# ---------------------------------------------------------------------------
# Module-level shared state (readable by cp2077_jarvis without runtime dict)
# ---------------------------------------------------------------------------
_live_state: Dict[str, Any] = {}
_agent_event_q: "Optional[Any]" = None   # cp2077_jarvis can inject events here


# ---------------------------------------------------------------------------
# Default state file path
# ---------------------------------------------------------------------------
def _default_state_file() -> str:
    """Returns the expected CET bridge output path on Windows."""
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(profile, "RadioOSBridge", "cp2077_state.json")


# ---------------------------------------------------------------------------
# Game snapshot dataclass
# ---------------------------------------------------------------------------
@dataclass
class CP2077State:
    # Player
    player_name:      str   = "V"
    health_pct:       float = 1.0        # 0.0 – 1.0
    is_alive:         bool  = True
    level:            int   = 1
    street_cred:      int   = 0

    # Combat
    in_combat:        bool  = False
    enemy_count:      int   = 0
    nearest_enemy_m:  float = 999.0

    # Wanted
    wanted_level:     int   = 0          # 0–5

    # Location
    district:         str   = ""
    location:         str   = ""
    coords_x:         float = 0.0
    coords_y:         float = 0.0

    # Quest
    active_quest:     str   = ""
    active_objective: str   = ""

    # Vehicle
    in_vehicle:       bool  = False
    vehicle_name:     str   = ""

    # Inventory / world
    last_item:        str   = ""
    nearby_poi:       str   = ""
    nearby_poi_dist:  float = 999.0

    # Timestamp from CET
    ts:               float = 0.0


def _parse_state(raw: Dict[str, Any]) -> CP2077State:
    s = CP2077State()
    p = raw.get
    s.player_name      = str(p("player_name", "V") or "V")
    s.health_pct       = float(p("health_pct", 1.0) or 1.0)
    s.is_alive         = bool(p("is_alive", True))
    s.level            = int(p("level", 1) or 1)
    s.street_cred      = int(p("street_cred", 0) or 0)
    s.in_combat        = bool(p("in_combat", False))
    s.enemy_count      = int(p("enemy_count", 0) or 0)
    s.nearest_enemy_m  = float(p("nearest_enemy_m", 999.0) or 999.0)
    s.wanted_level     = int(p("wanted_level", 0) or 0)
    s.district         = str(p("district", "") or "")
    s.location         = str(p("location", "") or "")
    s.coords_x         = float(p("coords_x", 0.0) or 0.0)
    s.coords_y         = float(p("coords_y", 0.0) or 0.0)
    s.active_quest     = str(p("active_quest", "") or "")
    s.active_objective = str(p("active_objective", "") or "")
    s.in_vehicle       = bool(p("in_vehicle", False))
    s.vehicle_name     = str(p("vehicle_name", "") or "")
    s.last_item        = str(p("last_item", "") or "")
    s.nearby_poi       = str(p("nearby_poi", "") or "")
    s.nearby_poi_dist  = float(p("nearby_poi_dist", 999.0) or 999.0)
    s.ts               = float(p("ts", 0.0) or 0.0)
    return s


# ---------------------------------------------------------------------------
# Event priority map
# ---------------------------------------------------------------------------
_EVENT_PRIORITY: Dict[str, float] = {
    "player_death":         100.0,
    "health_critical":       95.0,
    "combat_started":        88.0,
    "health_low":            82.0,
    "wanted_level_up":       78.0,
    "enemy_spotted":         72.0,
    "quest_updated":         70.0,
    "combat_ended":          66.0,
    "location_changed":      62.0,
    "vehicle_entered":       58.0,
    "vehicle_exited":        56.0,
    "item_acquired":         54.0,
    "poi_nearby":            52.0,
    "wanted_level_clear":    50.0,
    "level_up":              50.0,
    "game_started":          48.0,
    "game_stopped":          45.0,
}


# ---------------------------------------------------------------------------
# Delta detector — compares previous snapshot to current
# ---------------------------------------------------------------------------
class DeltaDetector:
    def __init__(self, cfg: Dict[str, Any]):
        self._cfg  = cfg
        self._prev: Optional[CP2077State] = None
        self._last_item_seen:    str   = ""
        self._prev_alive:        bool  = True

    def diff(self, curr: CP2077State) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        prev = self._prev

        if prev is None:
            # First observation — treat as fresh game start
            events.append({"type": "game_started", "data": asdict(curr)})
            self._prev = curr
            self._prev_alive = curr.is_alive
            self._last_item_seen = curr.last_item
            return events

        cfg = self._cfg

        # ── Death ────────────────────────────────────────────────────────────
        if cfg.get("announce_death", True):
            if self._prev_alive and not curr.is_alive:
                events.append({"type": "player_death", "data": asdict(curr)})
        self._prev_alive = curr.is_alive

        # Continue comparing only if alive
        if not curr.is_alive:
            self._prev = curr
            return events

        # ── Health ───────────────────────────────────────────────────────────
        threshold = float(cfg.get("health_low_threshold", 0.35))
        if cfg.get("announce_health_low", True):
            if curr.health_pct < 0.15 and prev.health_pct >= 0.15:
                events.append({"type": "health_critical",
                                "data": {"health_pct": curr.health_pct}})
            elif curr.health_pct < threshold and prev.health_pct >= threshold:
                events.append({"type": "health_low",
                                "data": {"health_pct": curr.health_pct}})

        # ── Combat ───────────────────────────────────────────────────────────
        if cfg.get("announce_combat_start", True):
            if curr.in_combat and not prev.in_combat:
                events.append({"type": "combat_started",
                                "data": {"enemy_count": curr.enemy_count,
                                         "location": curr.location,
                                         "nearest_m": curr.nearest_enemy_m}})
        if cfg.get("announce_combat_end", True):
            if not curr.in_combat and prev.in_combat:
                events.append({"type": "combat_ended",
                                "data": {"location": curr.location,
                                         "health_pct": curr.health_pct}})

        # ── Wanted level ─────────────────────────────────────────────────────
        if cfg.get("announce_wanted_level", True):
            if curr.wanted_level > prev.wanted_level and curr.wanted_level > 0:
                events.append({"type": "wanted_level_up",
                                "data": {"level": curr.wanted_level}})
            elif curr.wanted_level == 0 and prev.wanted_level > 0:
                events.append({"type": "wanted_level_clear", "data": {}})

        # ── Location ─────────────────────────────────────────────────────────
        if cfg.get("announce_location_change", True):
            if curr.location and curr.location != prev.location:
                events.append({"type": "location_changed",
                                "data": {"location": curr.location,
                                         "district": curr.district,
                                         "prev_location": prev.location}})

        # ── Quest ────────────────────────────────────────────────────────────
        if cfg.get("announce_quest_update", True):
            quest_changed = (curr.active_quest != prev.active_quest)
            obj_changed   = (curr.active_objective != prev.active_objective
                             and curr.active_objective)
            if quest_changed or obj_changed:
                events.append({"type": "quest_updated",
                                "data": {"quest": curr.active_quest,
                                         "objective": curr.active_objective,
                                         "prev_quest": prev.active_quest}})

        # ── Vehicle ──────────────────────────────────────────────────────────
        if cfg.get("announce_vehicle_entered", True):
            if curr.in_vehicle and not prev.in_vehicle:
                events.append({"type": "vehicle_entered",
                                "data": {"vehicle": curr.vehicle_name}})
            elif not curr.in_vehicle and prev.in_vehicle:
                events.append({"type": "vehicle_exited",
                                "data": {"vehicle": prev.vehicle_name}})

        # ── Item ─────────────────────────────────────────────────────────────
        if cfg.get("announce_item_found", True):
            if curr.last_item and curr.last_item != self._last_item_seen:
                events.append({"type": "item_acquired",
                                "data": {"item": curr.last_item,
                                         "location": curr.location}})
                self._last_item_seen = curr.last_item

        # ── Level up ─────────────────────────────────────────────────────────
        if cfg.get("announce_level_up", True):
            if curr.level > prev.level:
                events.append({"type": "level_up",
                                "data": {"level": curr.level,
                                         "street_cred": curr.street_cred}})

        # ── POI nearby ───────────────────────────────────────────────────────
        radius = float(cfg.get("poi_nearby_radius", 150))
        if cfg.get("announce_poi_nearby", True):
            if (curr.nearby_poi and curr.nearby_poi != prev.nearby_poi
                    and curr.nearby_poi_dist <= radius):
                events.append({"type": "poi_nearby",
                                "data": {"poi": curr.nearby_poi,
                                         "dist_m": curr.nearby_poi_dist,
                                         "location": curr.location}})

        # ── Enemy spotted ────────────────────────────────────────────────────
        if cfg.get("announce_enemy_spotted", False):
            if curr.enemy_count > 0 and prev.enemy_count == 0 and not curr.in_combat:
                events.append({"type": "enemy_spotted",
                                "data": {"count": curr.enemy_count,
                                         "nearest_m": curr.nearest_enemy_m}})

        self._prev = curr
        return events


# ---------------------------------------------------------------------------
# Simulation mode — generates a plausible fake game session
# ---------------------------------------------------------------------------
_SIM_LOCATIONS = [
    ("Watson", "Kabuki"),
    ("Westbrook", "Japantown"),
    ("City Center", "Corpo Plaza"),
    ("Heywood", "The Glen"),
    ("Badlands", "Rocky Ridge"),
    ("Santo Domingo", "Rancho Coronado"),
    ("Pacifica", "West Wind Estate"),
]

_SIM_QUESTS = [
    ("The Heist", "Meet Dex at Afterlife"),
    ("Ghost Town", "Follow Panam to the camp"),
    ("Life During Wartime", "Reach the crash site"),
    ("Double Life", "Investigate Evelyn's past"),
    ("Down on the Street", "Meet Hanako at Embers"),
]

_SIM_ITEMS = [
    "Iconic Weapon: Skippy",
    "Rare Cyberware: Gorilla Arms",
    "Epic: Sandevistan Mk.5",
    "Shard: Encrypted corpo memo",
    "Legendary: Spider Murphy's jacket",
]

class SimulationDriver:
    """Generates fake CP2077 game states for development testing."""

    def __init__(self):
        self._t:      float = time.time()
        self._state   = CP2077State()
        self._state.player_name = "V (Simulated)"
        self._phase   = "roaming"
        self._phase_t = time.time()
        self._loc_idx = 0
        self._quest_idx = 0

    def tick(self) -> CP2077State:
        now = time.time()
        elapsed = now - self._phase_t
        s = self._state

        if self._phase == "roaming":
            s.health_pct = min(1.0, s.health_pct + 0.005)
            s.in_combat  = False
            if elapsed > random.uniform(8, 20):
                self._phase   = random.choice(["combat", "travel", "quest"])
                self._phase_t = now

        elif self._phase == "combat":
            s.in_combat    = True
            s.enemy_count  = random.randint(2, 6)
            s.health_pct   = max(0.0, s.health_pct - random.uniform(0.01, 0.06))
            s.nearest_enemy_m = random.uniform(2.0, 30.0)
            if elapsed > random.uniform(6, 14):
                self._phase   = "post_combat"
                self._phase_t = now
                s.wanted_level = random.choice([0, 0, 1, 2])

        elif self._phase == "post_combat":
            s.in_combat   = False
            s.enemy_count = 0
            if elapsed > 3:
                self._phase   = "roaming"
                self._phase_t = now

        elif self._phase == "travel":
            s.in_vehicle = True
            s.vehicle_name = random.choice(["Arch Nazare", "Kusanagi CT-3X", "Porsche 930 Turbo"])
            if elapsed > random.uniform(5, 12):
                self._loc_idx = (self._loc_idx + 1) % len(_SIM_LOCATIONS)
                district, loc = _SIM_LOCATIONS[self._loc_idx]
                s.district = district
                s.location = loc
                s.in_vehicle = False
                # random POI
                if random.random() > 0.5:
                    s.nearby_poi      = random.choice(["NCPD Scanner Hustle", "Hidden Stash", "Cyberpsycho Sighting"])
                    s.nearby_poi_dist = random.uniform(40, 140)
                self._phase   = "roaming"
                self._phase_t = now

        elif self._phase == "quest":
            qi = random.randint(0, len(_SIM_QUESTS) - 1)
            s.active_quest, s.active_objective = _SIM_QUESTS[qi]
            # random item
            if random.random() > 0.7:
                s.last_item = random.choice(_SIM_ITEMS)
            if elapsed > random.uniform(5, 10):
                self._phase   = "roaming"
                self._phase_t = now

        # Level up occasionally
        if random.random() > 0.998:
            s.level += 1
            s.street_cred = min(50, s.street_cred + random.randint(1, 3))

        s.is_alive = s.health_pct > 0.0
        if not s.is_alive:
            # Respawn
            s.health_pct  = 1.0
            s.is_alive    = True
            s.in_combat   = False
            s.enemy_count = 0

        s.ts = now
        return s


# ---------------------------------------------------------------------------
# Feed worker — entry point called by bookmark.py
# ---------------------------------------------------------------------------
def feed_worker(cfg: Dict[str, Any], runtime: Dict[str, Any]) -> None:
    """
    Main feed loop.  bookmark.py runs this in a daemon thread.

    cfg      — feed config merged from manifest + FEED_DEFAULTS
    runtime  — dict containing event_q, StationEvent, log, etc.
    """
    global _live_state

    event_q      = runtime.get("event_q")
    StationEvent = runtime.get("StationEvent")
    log          = runtime.get("log", print)

    if event_q is None or StationEvent is None:
        log("cp2077_sdk", "ERROR: runtime missing event_q or StationEvent — aborting feed")
        return

    poll_hz  = float(cfg.get("poll_hz", 2))
    sleep_s  = max(0.1, 1.0 / poll_hz)
    sim_mode = bool(cfg.get("sim_mode", False))

    state_file = (str(cfg.get("state_file", "") or "").strip()
                  or _default_state_file())

    log("cp2077_sdk", f"Starting — state_file={state_file} sim={sim_mode} poll={poll_hz}Hz")

    detector = DeltaDetector(cfg)
    sim      = SimulationDriver() if sim_mode else None
    game_was_running = False

    while True:
        try:
            if sim_mode:
                state = sim.tick()
            else:
                state = _read_state_file(state_file)

            if state is None:
                # Game isn't running (file absent/stale)
                if game_was_running:
                    _push(event_q, StationEvent, "game_stopped",
                          {"reason": "state file disappeared"}, 45.0)
                    detector = DeltaDetector(cfg)   # reset diff state
                    game_was_running = False
                    log("cp2077_sdk", "CP2077 session ended.")
                time.sleep(sleep_s)
                continue

            if not game_was_running:
                game_was_running = True
                log("cp2077_sdk", "CP2077 session detected.")

            # Update shared live state
            _live_state.update(asdict(state))

            # Detect deltas and emit events
            for ev in detector.diff(state):
                priority = _EVENT_PRIORITY.get(ev["type"], 55.0)
                _push(event_q, StationEvent, ev["type"], ev["data"], priority)

        except Exception as exc:
            log("cp2077_sdk", f"feed_worker tick error: {exc}")

        time.sleep(sleep_s)


def _read_state_file(path: str) -> Optional[CP2077State]:
    """Returns parsed state or None if file is missing/stale/unreadable."""
    try:
        if not os.path.exists(path):
            return None
        mtime = os.path.getmtime(path)
        # Treat a file not updated in 10s as stale (game paused or closed)
        if time.time() - mtime > 10.0:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return _parse_state(raw)
    except Exception:
        return None


def _push(event_q: Any, StationEvent: Any, etype: str,
          data: Dict[str, Any], priority: float) -> None:
    try:
        evt = StationEvent(
            source="cp2077_sdk",
            type=etype,
            priority=priority,
            payload=data,
        )
        event_q.put(evt)
    except Exception:
        pass
