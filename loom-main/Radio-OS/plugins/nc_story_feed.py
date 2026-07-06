"""
nc_story_feed.py — Night City Chronicles Story Feed
=====================================================

This feed plugin is the clock of the Chronicles station.

It does three things:
  1.  Polls the cp2077_sdk state file for game state updates and game events
  2.  Decides when enough has happened narratively to trigger a chapter
  3.  Calls ACTIVE_META_PLUGIN.process_input() with batched events + game state,
      then routes returned segments into the Radio OS TTS pipeline

It is deliberately *not* ARIA's cp2077_sdk plugin.
ARIA reacts to every event tactically.
The Chronicles feed accumulates events and synthesises them narratively.

Emit cadence:
  - Session recap fires on startup (handled by meta plugin's Timer)
  - A chapter fires when: >= N events have accumulated, OR N minutes have passed,
    OR a quest_updated / player_death event arrives (always chapter-worthy)
  - Secondary voice beats fire at ~45% probability after each chapter (meta plugin side)
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

PLUGIN_NAME  = "nc_story_feed"
PLUGIN_DESC  = "Night City Chronicles story ticker — drives chapter synthesis from CP2077 game state"
IS_FEED      = True

FEED_DEFAULTS: Dict[str, Any] = {
    "enabled":                True,
    "tick_sec":               12.0,
    "startup_delay":          10.0,
    "bible_path":             "",
    "session_event_threshold": 5,
    "force_chapter_sec":      900.0,
    "semantic_cooldown_sec":  90.0,
    "max_event_age_sec":      180.0,
    "min_force_signal_score": 3.25,
    "min_distinct_event_types": 2,
}
DEFAULT_FEED_CFG = FEED_DEFAULTS


# ---------------------------------------------------------------------------
# Game state reader — reads the CET bridge JSON file
# ---------------------------------------------------------------------------

_DEFAULT_STATE_FILE = os.path.join(
    os.path.expanduser("~"), "RadioOSBridge", "cp2077_state.json"
)

# Sim-mode state cycle used when CP2077 is not running
_SIM_STATES = [
    {"player_name":"V","level":5,"street_cred":12,"location":"Kabuki","district":"Watson",
     "quest":"The Pickup","active_objective":"Deal with Royce","in_combat":False,"wanted_level":0},
    {"player_name":"V","level":7,"street_cred":18,"location":"Northside","district":"Watson",
     "quest":"The Heist","active_objective":"Meet Dex at the Afterlife","in_combat":False,"wanted_level":0},
    {"player_name":"V","level":10,"street_cred":25,"location":"City Center","district":"Corpo Plaza",
     "quest":"Playing for Time","active_objective":"Talk to Takemura","in_combat":False,"wanted_level":1},
    {"player_name":"V","level":14,"street_cred":32,"location":"Japantown","district":"Westbrook",
     "quest":"Automatic Love","active_objective":"Find Evelyn","in_combat":True,"wanted_level":0},
    {"player_name":"V","level":18,"street_cred":41,"location":"Santo Domingo","district":"Arroyo",
     "quest":"Gimme Danger","active_objective":"Meet Takemura at the docks","in_combat":False,"wanted_level":0},
]

_SIM_EVENT_CYCLE = [
    {"type": "quest_updated",   "data": {"quest":"The Pickup","objective":"Deal with Royce"}},
    {"type": "level_up",        "data": {"level":7,"street_cred":18}},
    {"type": "combat_ended",    "data": {"location":"Kabuki"}},
    {"type": "location_changed","data": {"location":"Afterlife","district":"Watson"}},
    {"type": "quest_updated",   "data": {"quest":"The Heist","objective":"Meet Dex at the Afterlife"}},
    {"type": "location_changed","data": {"location":"Konpeki Plaza","district":"City Center"}},
    {"type": "player_death",    "data": {"location":"Konpeki Plaza"}},
    {"type": "quest_updated",   "data": {"quest":"Playing for Time","objective":"Talk to Takemura"}},
    {"type": "level_up",        "data": {"level":10,"street_cred":25}},
    {"type": "quest_updated",   "data": {"quest":"Automatic Love","objective":"Find Evelyn"}},
]

_HIGH_PRIORITY_TYPES = {
    "quest_updated",
    "player_death",
    "level_up",
    "game_started",
    "near_death",
    "kill_spree",
    "stealth_broken",
}

# Texture-only events still get recorded when something meaningful happens,
# but they should not count as enough evidence to force narration on their own.
_EVENT_SIGNAL_WEIGHTS: Dict[str, float] = {
    "quest_updated": 3.6,
    "level_up": 3.0,
    "player_death": 4.2,
    "game_started": 3.5,
    "combat_started": 1.0,
    "combat_ended": 1.2,
    "location_changed": 0.9,
    "street_cred_up": 1.0,
    "wanted_level_change": 1.0,
    "item_acquired": 0.0,
    "game_stopped": 0.5,
    "near_death": 3.8,
    "health_recovered": 0.8,
    "vehicle_entered": 0.0,
    "vehicle_exited": 0.0,
    "weapon_switched": 0.0,
    "sandevistan_activated": 1.5,
    "optical_camo_activated": 1.4,
    "berserk_activated": 1.5,
    "hack_burst": 1.8,
    "stealth_broken": 3.6,
    "stealth_takedown": 1.4,
    "kill_spree": 2.6,
    "eddies_windfall": 1.3,
    "eddies_splurge": 0.4,
    "time_of_day_changed": 0.0,
    "dialogue_started": 0.0,
    "ram_depleted": 1.5,
}


def _norm_token(value: Any) -> str:
    text = " ".join(str(value or "").strip().lower().split())
    return text[:48]


def _event_signature(event: Dict[str, Any]) -> str:
    """Return a stable semantic signature for lightweight duplicate suppression."""
    etype = str(event.get("type") or "")
    data = event.get("data") or {}
    keys = {
        "quest_updated": ("quest", "objective"),
        "location_changed": ("district", "location"),
        "street_cred_up": ("street_cred", "gained"),
        "wanted_level_change": ("wanted_level", "prev_wanted"),
        "combat_started": ("location", "weapon_type"),
        "combat_ended": ("location", "kills_this_combat"),
        "vehicle_entered": ("vehicle_name", "vehicle_type"),
        "vehicle_exited": ("vehicle_name", "location"),
        "weapon_switched": ("weapon_name", "weapon_type"),
        "dialogue_started": ("dialogue_npc", "active_quest"),
        "ncm_race_finish": ("track_name", "position"),
        "ncm_knockout_finish": ("track_name", "position"),
    }.get(
        etype,
        ("location", "district", "weapon_type"),
    )

    parts = [etype]
    for key in keys:
        val = data.get(key)
        if val not in (None, "", False):
            parts.append(_norm_token(val))
    return "|".join(parts)


def _event_signal_weight(event: Dict[str, Any]) -> float:
    """Score how much fresh narrative evidence an event actually carries."""
    etype = str(event.get("type") or "")
    data = event.get("data") or {}
    weight = _EVENT_SIGNAL_WEIGHTS.get(etype, 0.0)

    if etype == "street_cred_up":
        weight += min(0.8, int(data.get("gained") or 0) / 10.0)
    elif etype == "combat_ended":
        weight += min(1.2, int(data.get("kills_this_combat") or 0) * 0.35)
    elif etype == "kill_spree":
        weight += min(0.9, int(data.get("kills_this_combat") or 0) * 0.12)
    elif etype == "eddies_windfall":
        weight += min(1.0, int(data.get("gained") or 0) / 10000.0)
    elif etype == "eddies_splurge":
        weight += min(0.6, int(data.get("spent") or 0) / 8000.0)
    elif etype == "wanted_level_change":
        delta = abs(int(data.get("wanted_level") or 0) - int(data.get("prev_wanted") or 0))
        weight += min(1.0, delta * 0.7)
    elif etype == "location_changed" and data.get("district"):
        weight += 0.2

    return weight


def _summarize_pending_events(events: List[Dict[str, Any]], limit: int = 3) -> str:
    snippets: List[str] = []
    for event in events[-limit:]:
        etype = str(event.get("type") or "")
        data = event.get("data") or {}
        if etype == "location_changed":
            snippets.append(f"moved through {data.get('location') or 'the city'}")
        elif etype == "street_cred_up":
            snippets.append(f"street cred ticked up to {data.get('street_cred', '?')}")
        elif etype == "combat_ended":
            snippets.append(f"one fight ended in {data.get('location') or 'Night City'}")
        elif etype == "vehicle_entered":
            snippets.append(f"slid into {data.get('vehicle_name') or 'a ride'}")
        elif etype == "quest_updated":
            snippets.append(f"picked up {data.get('quest') or 'a new obligation'}")
        else:
            snippets.append(etype.replace("_", " "))
    return "; ".join(snippets)


def _should_trigger_synthesis(
    pending_events: List[Dict[str, Any]],
    event_threshold: int,
    heat: float,
    force_tick: bool,
    min_force_signal_score: float,
    min_distinct_event_types: int,
) -> Dict[str, Any]:
    signal_events = [ev for ev in pending_events if _event_signal_weight(ev) > 0.0]
    signal_types = {str(ev.get("type") or "") for ev in signal_events}
    signal_score = sum(_event_signal_weight(ev) for ev in signal_events)
    strongest_weight = max((_event_signal_weight(ev) for ev in signal_events), default=0.0)
    has_priority_event = any(str(ev.get("type") or "") in _HIGH_PRIORITY_TYPES for ev in pending_events)

    effective_thresh = max(1, event_threshold - int(heat // 3))
    min_signal_score = max(2.75, effective_thresh * 1.05)
    threshold_met = (
        len(signal_events) >= effective_thresh
        and signal_score >= min_signal_score
        and (len(signal_types) >= min_distinct_event_types or strongest_weight >= 2.5)
    )
    force_write = (
        force_tick
        and signal_score >= min_force_signal_score
        and (len(signal_types) >= min_distinct_event_types or strongest_weight >= 3.0)
    )
    low_signal_force = force_tick and bool(pending_events) and not (has_priority_event or threshold_met or force_write)

    return {
        "should_write": has_priority_event or threshold_met or force_write,
        "has_priority_event": has_priority_event,
        "effective_threshold": effective_thresh,
        "signal_score": signal_score,
        "signal_count": len(signal_events),
        "distinct_signal_types": len(signal_types),
        "low_signal_force": low_signal_force,
    }


def _classify_broadcast_state(
    pending_events: List[Dict[str, Any]],
    game_state: Dict[str, Any],
    decision: Dict[str, Any],
    heat: float,
) -> str:
    """
    Classify the current station density mode.

    IDLE:
      - no meaningful delta
      - no scene advancement should be implied
    LOW:
      - some signal exists, but not enough to behave like a live dramatic sequence
    ACTIVE:
      - strong delta, clear escalation, or genuinely chapter-worthy moment
    """
    if any(str(ev.get("type") or "").startswith("ncm_") for ev in pending_events):
        return "ACTIVE"

    signal_score = float(decision.get("signal_score") or 0.0)
    signal_count = int(decision.get("signal_count") or 0)
    strongest_weight = max((_event_signal_weight(ev) for ev in pending_events), default=0.0)
    has_priority = bool(decision.get("has_priority_event"))

    if (
        has_priority
        or heat >= 3.5
        or signal_score >= 4.0
        or strongest_weight >= 3.2
        or bool(game_state.get("in_combat"))
        or int(game_state.get("wanted_level") or 0) >= 3
    ):
        return "ACTIVE"

    if (
        signal_count == 0
        and signal_score < 0.75
        and heat < 0.75
        and not pending_events
    ):
        return "IDLE"

    if signal_count == 0 and signal_score < 0.75 and heat < 1.25:
        return "IDLE"

    return "LOW"


def _resolve_heat_level(
    pending_events: List[Dict[str, Any]],
    game_state: Dict[str, Any],
    decision: Dict[str, Any],
    heat: float,
    broadcast_state: str,
) -> int:
    """
    Collapse live state intensity into a discrete 0..5 delivery signal.

    This is intentionally heuristic and tunable: the goal is to separate
    stillness from motion, and spikes from ordinary active play.
    """
    event_types = {str(ev.get("type") or "") for ev in pending_events}
    signal_score = float(decision.get("signal_score") or 0.0)
    strongest_weight = max((_event_signal_weight(ev) for ev in pending_events), default=0.0)

    if not pending_events:
        if broadcast_state == "IDLE" and heat < 0.75:
            return 0
        if broadcast_state == "IDLE":
            return 1

    if decision.get("low_signal_force"):
        return 0 if broadcast_state == "IDLE" else 1

    heat_level = 0
    if broadcast_state == "LOW":
        heat_level = 1
    elif broadcast_state == "ACTIVE":
        heat_level = 3

    if signal_score >= 2.5 or strongest_weight >= 1.8:
        heat_level = max(heat_level, 2)
    if signal_score >= 4.0 or heat >= 3.8 or bool(game_state.get("in_combat")):
        heat_level = max(heat_level, 3)
    if (
        signal_score >= 5.5
        or heat >= 6.5
        or strongest_weight >= 3.5
        or int(game_state.get("wanted_level") or 0) >= 3
        or event_types.intersection({
            "combat_started",
            "near_death",
            "stealth_broken",
            "kill_spree",
            "sandevistan_activated",
            "berserk_activated",
            "ram_depleted",
        })
    ):
        heat_level = max(heat_level, 4)
    if event_types.intersection({"player_death", "near_death"}) or heat >= 8.5:
        heat_level = 5

    if broadcast_state == "IDLE":
        heat_level = min(1, heat_level)
    elif broadcast_state == "LOW":
        heat_level = min(2, max(1, heat_level))

    return max(0, min(5, heat_level))


class CP2077StateReader:
    """
    Reads cp2077_state.json written by the CET mod (or sim mode data).
    Tracks previous state to detect deltas and synthesise events.
    """

    def __init__(self, state_file: str, sim_mode: bool = False):
        self._file     = state_file or _DEFAULT_STATE_FILE
        self._sim_mode = sim_mode
        self._prev_state: Dict[str, Any] = {}
        self._sim_idx   = 0
        self._sim_evt_idx = 0
        self._sim_tick  = 0
        # Rate-limit guards
        self._last_near_death_ts:  float = 0.0
        self._last_hack_burst_ts:  float = 0.0
        self._kill_spree_seen:     set   = set()  # milestones already fired this combat

    def read_state(self) -> Optional[Dict[str, Any]]:
        if self._sim_mode:
            idx = (self._sim_tick // 3) % len(_SIM_STATES)
            self._sim_tick += 1
            return dict(_SIM_STATES[idx])
        if not os.path.exists(self._file):
            return None
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def delta_events(self, new_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Compare new state vs previous; emit narrative events for changes."""
        prev = self._prev_state
        events: List[Dict[str, Any]] = []
        now = time.time()

        if not prev:
            # First read — detect game_started; reset kill-spree milestone tracker
            self._kill_spree_seen = set()
            events.append({"type": "game_started", "data": dict(new_state)})
            self._prev_state = dict(new_state)
            return events

        # ── Core events ──────────────────────────────────────────────────────

        # Level up
        old_level = prev.get("level", 0)
        new_level = new_state.get("level", 0)
        if new_level and new_level > old_level:
            events.append({"type": "level_up", "data": {
                "level": new_level, "street_cred": new_state.get("street_cred", 0),
                **new_state
            }})

        # Quest change
        old_quest = prev.get("quest") or prev.get("active_quest")
        new_quest = new_state.get("quest") or new_state.get("active_quest")
        old_obj   = prev.get("active_objective") or prev.get("objective")
        new_obj   = new_state.get("active_objective") or new_state.get("objective")
        if new_quest and (new_quest != old_quest or new_obj != old_obj):
            events.append({"type": "quest_updated", "data": {
                "quest": new_quest, "objective": new_obj or "", **new_state
            }})

        # Location change (district-level)
        old_dist = prev.get("district", "")
        new_dist = new_state.get("district", "")
        if new_dist and new_dist != old_dist:
            events.append({"type": "location_changed", "data": {
                "location": new_state.get("location", "Night City"),
                "district": new_dist, **new_state
            }})

        # Death
        if new_state.get("just_died") or (
            prev.get("health_pct", 1.0) > 0 and new_state.get("health_pct", 1.0) <= 0
        ):
            events.append({"type": "player_death", "data": {
                "location": new_state.get("location", "Night City"), **new_state
            }})

        # Combat started / ended
        prev_combat = prev.get("in_combat", False)
        new_combat  = new_state.get("in_combat", False)
        if not prev_combat and new_combat:
            events.append({"type": "combat_started", "data": {
                "weapon_name": new_state.get("weapon_name", ""),
                "weapon_type": new_state.get("weapon_type", ""),
                "enemy_count": new_state.get("enemy_count", 0),
                **new_state
            }})
        if prev_combat and not new_combat:
            events.append({"type": "combat_ended", "data": {
                "kills_this_combat":     new_state.get("kills_this_combat", 0),
                "headshots_this_combat": new_state.get("headshots_this_combat", 0),
                "location":              new_state.get("location", "Night City"),
                **new_state
            }})
            # Reset kill-spree milestone tracker for next fight
            self._kill_spree_seen = set()

        # Near-death (rate-limited to once per 120s)
        prev_hp = float(prev.get("health_pct", 1.0))
        new_hp  = float(new_state.get("health_pct", 1.0))
        if new_hp > 0 and new_hp < 0.20 and prev_hp >= 0.25:
            if now - self._last_near_death_ts > 120:
                events.append({"type": "near_death", "data": {
                    "health_pct": new_hp,
                    "enemy_count": new_state.get("enemy_count", 0),
                    **new_state
                }})
                self._last_near_death_ts = now

        # Health recovered (was critical, now stable)
        if prev_hp < 0.30 and new_hp > 0.65:
            events.append({"type": "health_recovered", "data": {
                "health_pct": new_hp, **new_state
            }})

        # Street cred increase
        old_cred = prev.get("street_cred", 0)
        new_cred = new_state.get("street_cred", 0)
        if new_cred and new_cred > old_cred:
            events.append({"type": "street_cred_up", "data": {
                "street_cred": new_cred, "gained": new_cred - old_cred, **new_state
            }})

        # Wanted level change
        old_wanted = prev.get("wanted_level", 0)
        new_wanted = new_state.get("wanted_level", 0)
        if new_wanted != old_wanted:
            events.append({"type": "wanted_level_change", "data": {
                "wanted_level": new_wanted, "prev_wanted": old_wanted, **new_state
            }})

        # Coordinate-based movement fallback (when district fields blank)
        if not new_dist:
            old_x = float(prev.get("coords_x") or 0)
            old_y = float(prev.get("coords_y") or 0)
            new_x = float(new_state.get("coords_x") or 0)
            new_y = float(new_state.get("coords_y") or 0)
            if old_x and new_x:
                dist_moved = ((new_x - old_x) ** 2 + (new_y - old_y) ** 2) ** 0.5
                if dist_moved > 250:
                    events.append({"type": "location_changed", "data": {
                        "location": new_state.get("location") or "Night City",
                        "district": "Night City",
                        "coords_x": new_x, "coords_y": new_y,
                        **new_state
                    }})

        # ── Deep state events ─────────────────────────────────────────────────

        # Vehicle entered / exited
        prev_veh = prev.get("in_vehicle", False)
        new_veh  = new_state.get("in_vehicle", False)
        if not prev_veh and new_veh:
            events.append({"type": "vehicle_entered", "data": {
                "vehicle_name": new_state.get("vehicle_name", ""),
                "vehicle_type": new_state.get("vehicle_type", ""),
                **new_state
            }})
        if prev_veh and not new_veh:
            events.append({"type": "vehicle_exited", "data": {
                "vehicle_name": prev.get("vehicle_name", ""),
                **new_state
            }})

        # Weapon switched
        prev_wtype = prev.get("weapon_type", "none")
        new_wtype  = new_state.get("weapon_type", "none")
        if new_wtype and new_wtype != "none" and new_wtype != prev_wtype:
            events.append({"type": "weapon_switched", "data": {
                "weapon_name": new_state.get("weapon_name", ""),
                "weapon_type": new_wtype,
                "prev_weapon_type": prev_wtype,
                **new_state
            }})

        # Chrome activations
        if not prev.get("has_sandevistan") and new_state.get("has_sandevistan"):
            events.append({"type": "sandevistan_activated", "data": {
                "in_combat": new_combat, **new_state
            }})
        if not prev.get("has_optical_camo") and new_state.get("has_optical_camo"):
            events.append({"type": "optical_camo_activated", "data": {
                "in_combat": new_combat, **new_state
            }})
        if not prev.get("has_berserk") and new_state.get("has_berserk"):
            events.append({"type": "berserk_activated", "data": {
                "in_combat": new_combat, **new_state
            }})

        # Hack burst (RAM dropped >40% of max in one tick — netrunner unloading quickhacks)
        ram_max = float(new_state.get("ram_max") or 0)
        if ram_max > 0:
            prev_ram = float(prev.get("ram_current") or ram_max)
            new_ram  = float(new_state.get("ram_current") or ram_max)
            if (prev_ram - new_ram) / ram_max > 0.40:
                if now - self._last_hack_burst_ts > 45:
                    events.append({"type": "hack_burst", "data": {
                        "ram_current": new_ram, "ram_max": ram_max, **new_state
                    }})
                    self._last_hack_burst_ts = now

            # RAM depleted (was plenty, now near empty)
            prev_ram_pct = prev_ram / ram_max if ram_max else 1.0
            new_ram_pct  = new_ram / ram_max  if ram_max else 1.0
            if prev_ram_pct > 0.30 and new_ram_pct < 0.15:
                events.append({"type": "ram_depleted", "data": {
                    "ram_current": new_ram, "ram_max": ram_max, **new_state
                }})

        # Stealth broken (was crouching, now enemies spiked and wanted went up)
        prev_crouch  = prev.get("is_crouching", False)
        new_enemies  = int(new_state.get("enemy_count") or 0)
        prev_enemies = int(prev.get("enemy_count") or 0)
        if prev_crouch and new_combat and not prev_combat:
            # Entered combat while crouching = cover blown
            events.append({"type": "stealth_broken", "data": {
                "enemy_count": new_enemies, **new_state
            }})
        elif (prev_crouch and new_enemies > prev_enemies + 1
              and new_wanted > old_wanted):
            events.append({"type": "stealth_broken", "data": {
                "enemy_count": new_enemies, **new_state
            }})

        # Stealth takedown (was crouching, kill counter incremented)
        prev_kills = int(prev.get("kills_this_combat") or 0)
        new_kills  = int(new_state.get("kills_this_combat") or 0)
        if prev_crouch and new_kills > prev_kills:
            events.append({"type": "stealth_takedown", "data": {
                "kills_this_combat": new_kills,
                "weapon_type": new_state.get("weapon_type", ""),
                **new_state
            }})

        # Kill spree milestones (3, 5, 10)
        for milestone in (3, 5, 10):
            if new_kills >= milestone and milestone not in self._kill_spree_seen:
                self._kill_spree_seen.add(milestone)
                events.append({"type": "kill_spree", "data": {
                    "kills_this_combat":     new_kills,
                    "headshots_this_combat": int(new_state.get("headshots_this_combat") or 0),
                    "weapon_name":           new_state.get("weapon_name", ""),
                    "milestone":             milestone,
                    **new_state
                }})

        # Eddies windfall / splurge
        prev_eddies = int(prev.get("eddies") or 0)
        new_eddies  = int(new_state.get("eddies") or 0)
        delta_eddies = new_eddies - prev_eddies
        if delta_eddies >= 5000:
            events.append({"type": "eddies_windfall", "data": {
                "eddies": new_eddies, "gained": delta_eddies, **new_state
            }})
        elif delta_eddies <= -3000:
            events.append({"type": "eddies_splurge", "data": {
                "eddies": new_eddies, "spent": -delta_eddies, **new_state
            }})

        # Time of day crossing (dawn=5, noon=12, dusk=19, night=22)
        _TIME_NAMES = {5: "dawn", 12: "noon", 19: "dusk", 22: "night"}
        prev_hour = float(prev.get("game_hour") or 0)
        new_hour  = float(new_state.get("game_hour") or 0)
        for boundary, name in _TIME_NAMES.items():
            if prev_hour < boundary <= new_hour or (
                # wrap-around midnight
                prev_hour > new_hour and boundary <= new_hour
            ):
                events.append({"type": "time_of_day_changed", "data": {
                    "game_hour": new_hour, "time_name": name,
                    "weather": new_state.get("weather", ""),
                    **new_state
                }})
                break  # only one boundary per tick

        # Dialogue started
        if not prev.get("in_dialogue") and new_state.get("in_dialogue"):
            events.append({"type": "dialogue_started", "data": {
                "dialogue_npc": new_state.get("dialogue_npc", ""),
                "active_quest": new_state.get("active_quest", ""),
                **new_state
            }})

        self._prev_state = dict(new_state)
        return events

    def sim_events(self) -> List[Dict[str, Any]]:
        """Return one sim event per call for testing."""
        if not self._sim_mode or not _SIM_EVENT_CYCLE:
            return []
        idx = self._sim_evt_idx % len(_SIM_EVENT_CYCLE)
        self._sim_evt_idx += 1
        return [dict(_SIM_EVENT_CYCLE[idx])]


# ---------------------------------------------------------------------------
# Feed worker
# ---------------------------------------------------------------------------

def feed_worker(
    stop_event: threading.Event,
    mem: Dict[str, Any],
    payload: Dict[str, Any],
    runtime: Dict[str, Any] = None,
) -> None:
    """
    Main feed worker. Runs in a daemon thread managed by bookmark.py.

    Polls game state, accumulates narrative events, triggers chapter
    synthesis via the active meta plugin.
    """
    runtime = runtime or {}
    cfg     = payload or {}

    log            = runtime.get("log", lambda role, msg: print(f"[{role}] {msg}"))
    emit_candidate = runtime.get("emit_candidate")

    tick_sec              = float(cfg.get("tick_sec",                  FEED_DEFAULTS["tick_sec"]))
    startup_delay         = float(cfg.get("startup_delay",             FEED_DEFAULTS["startup_delay"]))
    event_threshold       = int(cfg.get("session_event_threshold",     FEED_DEFAULTS["session_event_threshold"]))
    force_chapter_sec     = float(cfg.get("force_chapter_sec",         FEED_DEFAULTS["force_chapter_sec"]))
    semantic_cooldown_sec = float(cfg.get("semantic_cooldown_sec",     FEED_DEFAULTS["semantic_cooldown_sec"]))
    max_event_age_sec     = float(cfg.get("max_event_age_sec",         FEED_DEFAULTS["max_event_age_sec"]))
    min_force_signal      = float(cfg.get("min_force_signal_score",    FEED_DEFAULTS["min_force_signal_score"]))
    min_distinct_types    = int(cfg.get("min_distinct_event_types",    FEED_DEFAULTS["min_distinct_event_types"]))

    # Resolve state file
    state_file = cfg.get("state_file", "") or _DEFAULT_STATE_FILE
    sim_mode   = bool(cfg.get("sim_mode", False))

    log("nc_story_feed", f"starting (tick={tick_sec}s, sim={sim_mode}, threshold={event_threshold})")

    # ── Startup delay ────────────────────────────────────────────────────────
    if stop_event.wait(startup_delay):
        return

    # ── Locate meta plugin ───────────────────────────────────────────────────
    meta = _resolve_meta(runtime, log)
    if not meta:
        log("nc_story_feed", "ERROR: no active meta plugin — feed cannot drive narration")
        return

    log("nc_story_feed", f"meta plugin: {type(meta).__name__}")

    reader            = CP2077StateReader(state_file, sim_mode)
    pending_records:  List[Dict[str, Any]] = []
    recent_signatures: Dict[str, float] = {}
    last_chapter_ts:   float = time.time()
    session_sim_tick:  int   = 0

    while not stop_event.is_set():
        tick_start = time.time()

        # ── Read game state ──────────────────────────────────────────────────
        game_state = reader.read_state()

        # ── Gather events ────────────────────────────────────────────────────
        if game_state:
            new_events = reader.delta_events(game_state)
        else:
            new_events = []

        # Sim mode also injects scripted events on a longer cycle
        if sim_mode and session_sim_tick % 8 == 0 and session_sim_tick > 0:
            new_events += reader.sim_events()
        session_sim_tick += 1

        now = time.time()
        recent_signatures = {
            sig: ts for sig, ts in recent_signatures.items()
            if now - ts < semantic_cooldown_sec
        }

        for event in new_events:
            sig = _event_signature(event)
            last_seen = recent_signatures.get(sig, 0.0)
            weight = _event_signal_weight(event)
            is_important = str(event.get("type") or "") in _HIGH_PRIORITY_TYPES or weight >= 2.5
            if last_seen and (now - last_seen) < semantic_cooldown_sec and not is_important:
                log("nc_story_feed", f"semantic cooldown skipped {event.get('type')} ({sig})")
                continue
            recent_signatures[sig] = now
            pending_records.append({
                "event": event,
                "queued_at": now,
                "signature": sig,
            })

        # Let major events linger longer, but age weak signals out so they don't
        # snowball into fake chapters five minutes later.
        fresh_records: List[Dict[str, Any]] = []
        for record in pending_records[-40:]:
            age = now - float(record.get("queued_at") or now)
            weight = _event_signal_weight(record.get("event") or {})
            if age <= max_event_age_sec or weight >= 2.5:
                fresh_records.append(record)
        pending_records = fresh_records
        pending_events = [record["event"] for record in pending_records]

        # ── Decide whether to trigger chapter synthesis ──────────────────────
        heat = _calculate_heat(game_state or {})
        force_tick = (time.time() - last_chapter_ts) >= force_chapter_sec
        decision = _should_trigger_synthesis(
            pending_events=pending_events,
            event_threshold=event_threshold,
            heat=heat,
            force_tick=force_tick,
            min_force_signal_score=min_force_signal,
            min_distinct_event_types=min_distinct_types,
        )
        broadcast_state = _classify_broadcast_state(
            pending_events=pending_events,
            game_state=game_state or {},
            decision=decision,
            heat=heat,
        )
        heat_level = _resolve_heat_level(
            pending_events=pending_events,
            game_state=game_state or {},
            decision=decision,
            heat=heat,
            broadcast_state=broadcast_state,
        )

        if decision["should_write"]:
            input_data = {
                "game_state": game_state or {},
                "events":     list(pending_events),
                "broadcast_state": broadcast_state,
                "heat_level": heat_level,
                "signal_score": decision["signal_score"],
                "signal_count": decision["signal_count"],
                "distinct_signal_types": decision["distinct_signal_types"],
            }
            try:
                segments = meta.process_input(input_data)
                _route_segments(segments, emit_candidate, log)
            except Exception as e:
                log("nc_story_feed", f"ERROR calling meta.process_input: {e}")

            pending_records.clear()
            last_chapter_ts = time.time()

        elif decision["low_signal_force"] and game_state:
            # The timer fired, but the evidence never got stronger than a few
            # scraps. Ask the meta plugin for a quieter idle line instead of
            # pretending a fresh development happened.
            try:
                idle_segs = meta.process_input({
                    "game_state": game_state,
                    "events": [],
                    "broadcast_state": broadcast_state,
                    "heat_level": heat_level,
                    "idle_reason": "low_signal",
                    "pending_event_summary": _summarize_pending_events(pending_events),
                    "signal_score": decision["signal_score"],
                })
                _route_segments(idle_segs, emit_candidate, log)
            except Exception as e:
                log("nc_story_feed", f"low-signal idle dispatch failed: {e}")

            pending_records = [
                record for record in pending_records
                if _event_signal_weight(record.get("event") or {}) >= 2.5
            ]
            last_chapter_ts = time.time()

        elif game_state:
            # Keep game state fresh AND pick up any background-generated segments
            # (idle beats / chapters complete in background threads and land in _output)
            try:
                idle_segs = meta.process_input({
                    "game_state": game_state,
                    "events": [],
                    "broadcast_state": broadcast_state,
                    "heat_level": heat_level,
                    "signal_score": decision["signal_score"],
                })
                _route_segments(idle_segs, emit_candidate, log)
            except Exception:
                pass

        # ── Wait until next tick ─────────────────────────────────────────────
        elapsed = time.time() - tick_start
        sleep_sec = max(0.0, tick_sec - elapsed)
        stop_event.wait(sleep_sec)

    log("nc_story_feed", "feed stopped")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _calculate_heat(state: Dict[str, Any]) -> float:
    """
    Score the current narrative heat from 0.0 upward.
    Used to dynamically lower the chapter-trigger event threshold:
      effective_threshold = max(1, base_threshold - int(heat // 3))
    So heat=9 on a 5-event base drops to threshold=2, giving near-instant reaction.
    """
    heat = 0.0
    if state.get("in_combat"):
        heat += 3.0
    enemy_count = int(state.get("enemy_count") or 0)
    heat += min(3.0, enemy_count * 0.4)
    hp = float(state.get("health_pct") or 1.0)
    if hp < 0.20:
        heat += 4.0
    elif hp < 0.40:
        heat += 2.0
    wanted = int(state.get("wanted_level") or 0)
    heat += wanted * 0.8
    if state.get("has_sandevistan"):
        heat += 2.0
    if state.get("has_optical_camo"):
        heat += 1.5
    if state.get("has_berserk"):
        heat += 2.0
    vehicle_speed = float(state.get("vehicle_speed") or 0)
    if vehicle_speed > 25:
        heat += 2.0
    kills = int(state.get("kills_this_combat") or 0)
    if kills >= 3:
        heat += 1.5
    ram_max = float(state.get("ram_max") or 0)
    if ram_max > 0:
        ram_pct = float(state.get("ram_current") or 0) / ram_max
        if ram_pct < 0.30:
            heat += 1.0  # actively burning RAM
    if state.get("in_dialogue"):
        heat -= 2.0  # pause — not a dramatic moment
    return max(0.0, heat)


def _resolve_meta(runtime: Dict[str, Any], log) -> Optional[Any]:
    """Try several runtime locations to find the active meta plugin."""
    # bookmark.py stores it as ACTIVE_META_PLUGIN (uppercase) in the runtime dict
    for key in ("ACTIVE_META_PLUGIN", "active_meta_plugin", "meta_plugin", "_meta_plugin"):
        candidate = runtime.get(key)
        if candidate is not None:
            return candidate

    # Try the plugin registry
    plugins = runtime.get("plugins") or {}
    for v in plugins.values():
        if hasattr(v, "process_input"):
            return v

    return None


def _route_segments(
    segments: List[Dict[str, Any]],
    emit_candidate,
    log,
) -> None:
    """Push generated narration segments into the Radio OS TTS pipeline."""
    if not segments:
        return

    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue

        # Preserve all producer pipeline fields the segment already carries.
        # _push_segment now stamps post_id / body / _literal / lead_voice so
        # the producer won't filter this candidate out.
        post_id = seg.get("post_id") or f"nc_{int(time.time() * 1000)}_{abs(hash(text)) % 100000:05d}"
        candidate = {
            "text":       text,
            "voice":      seg.get("voice", "host"),
            "priority":   seg.get("priority", 75),
            "source":     "nc_chronicles",
            "tags":       ["narrative", "chronicle"],
            # Producer pipeline fields
            "post_id":    post_id,
            "body":       seg.get("body", text),
            "title":      seg.get("title", "Night City Chronicles"),
            "lead_voice": seg.get("lead_voice", seg.get("voice", "host")),
            "_literal":   seg.get("_literal", seg.get("narrate_only", False)),
        }

        if emit_candidate:
            try:
                emit_candidate(candidate)
            except Exception as e:
                log("nc_story_feed", f"emit_candidate error: {e}")
        else:
            log("nc_story_feed", f"[NO EMIT] {text[:80]}…")
