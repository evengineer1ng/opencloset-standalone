"""
ncm_race_feed.py - MT/NCM race bridge feed
==========================================

Primary mode:
  Poll ncm_race_state.json written by the MT_Ecosystem Lua sidecar.

Fallback mode:
  When that sidecar is missing, infer lower-fidelity race lifecycle events from
  the mte_* fields embedded directly in cp2077_state.json by the currently
  installed bridge.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_NAME = "ncm_race_feed"
PLUGIN_DESC = "NCM racing bridge - delivers MT_Ecosystem race events to Night City Chronicles"
IS_FEED = True

FEED_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "tick_sec": 0.8,
    "state_file": "",
    "cp_state_file": "",
    "max_event_age_sec": 5.0,
    "max_events_per_tick": 2,
}
DEFAULT_FEED_CFG = FEED_DEFAULTS

_DEFAULT_RACE_FILE = os.path.join(
    os.path.expanduser("~"), "RadioOSBridge", "ncm_race_state.json"
)
_DEFAULT_CP_STATE_FILE = os.path.join(
    os.path.expanduser("~"), "RadioOSBridge", "cp2077_state.json"
)

_EVENT_TYPE_MAP: Dict[str, str] = {
    "RacePreview": "ncm_race_preview",
    "GridReady": "ncm_grid_ready",
    "QualiStart": "ncm_quali_start",
    "QualiEnd": "ncm_quali_end",
    "RaceCountdown": "ncm_race_countdown",
    "RaceStart": "ncm_race_start",
    "RaceFinished": "ncm_race_finish",
    "PrestigeChange": "ncm_prestige_change",
    "SeasonComplete": "ncm_season_complete",
    "ChampionshipComplete": "ncm_championship_complete",
    "RoundReady": "ncm_round_ready",
    "KnockoutLobby": "ncm_knockout_lobby",
    "KnockoutCountdown": "ncm_knockout_countdown",
    "KnockoutStart": "ncm_knockout_start",
    "DriverEliminated": "ncm_driver_eliminated",
    "PlayerInDangerZone": "ncm_player_danger_zone",
    "LeadChange": "ncm_lead_change",
    "KnockoutFinished": "ncm_knockout_finish",
    "CatchUpBonus": "ncm_catch_up_bonus",
    "WagerLost": "ncm_wager_lost",
    "NewRecord": "ncm_new_record",
    "ImpactSpike": "ncm_impact_spike",
    "CrashSpike": "ncm_crash_spike",
    "HardBrake": "ncm_hard_brake",
    "AggressiveAccel": "ncm_aggressive_accel",
    "HighSpeedRun": "ncm_high_speed_run",
    "SustainedCorner": "ncm_sustained_corner",
    "SafetyIncidentRecorded": "ncm_safety_incident",
}

_FINISH_STATES = {"results", "finished", "complete"}
_ACTIVE_RACE_STATES = {
    "preview",
    "grid",
    "prerace",
    "lobby",
    "staging",
    "countdown",
    "quali",
    "racing",
    "results",
    "finished",
    "complete",
}
_MAX_RACE_HEAT_EVENTS = {
    "ncm_race_start",
    "ncm_knockout_start",
    "ncm_race_countdown",
    "ncm_knockout_countdown",
    "ncm_driver_eliminated",
    "ncm_player_danger_zone",
    "ncm_lead_change",
    "ncm_overtake",
    "ncm_position_lost",
    "ncm_gap_change",
    "ncm_rival_pressure",
    "ncm_speed_spike",
    "ncm_incident",
    "ncm_impact_spike",
    "ncm_crash_spike",
    "ncm_hard_brake",
    "ncm_aggressive_accel",
    "ncm_high_speed_run",
    "ncm_sustained_corner",
    "ncm_safety_incident",
    "ncm_sector_change",
}

_RACE_END_EVENTS = {
    "ncm_race_finish",
    "ncm_knockout_finish",
    "ncm_season_complete",
    "ncm_championship_complete",
}

_RACE_EVENT_PRIORITY: Dict[str, int] = {
    "ncm_race_finish": 110,
    "ncm_knockout_finish": 110,
    "ncm_season_complete": 110,
    "ncm_championship_complete": 110,
    "ncm_race_start": 95,
    "ncm_knockout_start": 95,
    "ncm_player_danger_zone": 92,
    "ncm_incident": 90,
    "ncm_crash_spike": 90,
    "ncm_impact_spike": 88,
    "ncm_overtake": 86,
    "ncm_position_lost": 86,
    "ncm_lead_change": 84,
    "ncm_driver_eliminated": 82,
    "ncm_race_countdown": 78,
    "ncm_knockout_countdown": 78,
    "ncm_gap_change": 72,
    "ncm_rival_pressure": 72,
    "ncm_safety_incident": 70,
    "ncm_hard_brake": 66,
    "ncm_aggressive_accel": 64,
    "ncm_high_speed_run": 62,
    "ncm_speed_spike": 60,
    "ncm_sustained_corner": 54,
    "ncm_sector_change": 38,
}


def _resolve_race_heat_level(events: List[Dict[str, Any]], race_state: str) -> int:
    normalized_state = str(race_state or "").strip().lower()
    event_types = {str(event.get("type") or "") for event in events}
    if event_types.intersection(_MAX_RACE_HEAT_EVENTS):
        return 5
    if event_types.intersection({
        "ncm_race_finish",
        "ncm_knockout_finish",
        "ncm_season_complete",
        "ncm_championship_complete",
        "ncm_lead_change",
        "ncm_new_record",
    }):
        return 5
    if normalized_state in _ACTIVE_RACE_STATES and normalized_state not in _FINISH_STATES:
        return 5
    if events:
        return 5 if any(t.startswith("ncm_") for t in event_types) else 2
    return 0


def _race_event_priority(event: Dict[str, Any]) -> int:
    etype = str(event.get("type") or "")
    return _RACE_EVENT_PRIORITY.get(etype, 50 if etype.startswith("ncm_") else 0)


def _race_observed_ts(event: Dict[str, Any], now: float) -> float:
    for key in ("_race_observed_ts", "_observed_at", "ts"):
        raw = event.get(key)
        if raw is None:
            continue
        try:
            observed = float(raw)
        except (TypeError, ValueError):
            continue
        if observed <= 0:
            continue
        if observed > now + 2.0:
            return now
        return observed

    data = event.get("data") or {}
    if isinstance(data, dict):
        raw = data.get("ts")
        try:
            observed = float(raw)
        except (TypeError, ValueError):
            observed = 0.0
        if observed > 1_000_000_000:
            return min(observed, now)

    return now


def _prepare_race_events(
    events: List[Dict[str, Any]],
    *,
    now: Optional[float] = None,
    max_age_sec: float = 5.0,
    max_events_per_tick: int = 2,
) -> List[Dict[str, Any]]:
    """Stamp, stale-filter, and cap a sidecar batch before it reaches the LLM."""
    now = time.time() if now is None else now
    stamped: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        etype = str(event.get("type") or "")
        if not etype:
            continue
        event = dict(event)
        observed = _race_observed_ts(event, now)
        event["_race_observed_ts"] = observed
        if etype not in _RACE_END_EVENTS and now - observed > max_age_sec:
            continue
        stamped.append(event)

    if not stamped:
        return []

    final_events = [event for event in stamped if str(event.get("type") or "") in _RACE_END_EVENTS]
    live_events = [event for event in stamped if str(event.get("type") or "") not in _RACE_END_EVENTS]
    live_events.sort(key=lambda ev: (_race_event_priority(ev), _race_observed_ts(ev, now)), reverse=True)

    limit = max(1, int(max_events_per_tick or 1))
    return final_events + live_events[:limit]


class _LiveRaceSynth:
    """Generate punchy live race deltas from the rich ncm_race_state sidecar."""

    SPEED_BANDS_KPH = (120, 180, 240, 300)

    def __init__(self) -> None:
        self._prev: Dict[str, Any] = {}

    def process(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        live = raw.get("live") if isinstance(raw.get("live"), dict) else {}
        race_state = str(raw.get("race_state") or "idle").strip().lower()
        active = race_state in _ACTIVE_RACE_STATES and race_state not in _FINISH_STATES
        snap = self._snapshot(live, race_state)

        if not active:
            self._prev = {}
            return []

        prev = self._prev
        self._prev = snap
        if not prev:
            return []

        events: List[Dict[str, Any]] = []
        if race_state == "racing":
            incident = self._incident_event(prev, snap)
            if incident:
                events.append(incident)

            position = self._position_event(prev, snap)
            if position:
                events.append(position)

            gap = self._gap_event(prev, snap)
            if gap:
                events.append(gap)

        if race_state in {"racing", "quali"}:
            speed = self._speed_event(prev, snap)
            if speed:
                events.append(speed)

        if race_state == "racing" and not events:
            sector = self._sector_event(prev, snap)
            if sector:
                events.append(sector)

        return events

    def _snapshot(self, live: Dict[str, Any], race_state: str) -> Dict[str, Any]:
        standings = live.get("standings") if isinstance(live.get("standings"), list) else []
        player, ahead, behind = _extract_player_pack(live, standings)
        telemetry = live.get("telemetry") if isinstance(live.get("telemetry"), dict) else {}
        position = _safe_int(
            _first_present(player, "position", "rank", default=live.get("estimated_position"))
        )

        return {
            "race_state": race_state,
            "mode": str(live.get("mode") or ""),
            "track_name": str(live.get("track_name") or ""),
            "timer": _safe_float(live.get("timer")),
            "field_size": _safe_int(live.get("field_size") or len(standings)),
            "position": position,
            "player": player,
            "ahead": ahead,
            "behind": behind,
            "ahead_name": _driver_name(ahead),
            "behind_name": _driver_name(behind),
            "ahead_gap_m": _gap_m(player, ahead, "ahead"),
            "behind_gap_m": _gap_m(player, behind, "behind"),
            "gap_text": str(live.get("gap_text") or ""),
            "gap_behind_text": str(live.get("gap_behind_text") or ""),
            "speed_kph": _speed_kph(player, telemetry),
            "vehicle_health": _safe_float(
                _first_present(telemetry, "vehicle_health", "vehicleHealth"), -1.0
            ),
            "throttle": _safe_float(telemetry.get("throttle")),
            "brake": _safe_float(telemetry.get("brake")),
            "gear": _safe_int(telemetry.get("gear")),
            "rpm": _safe_float(telemetry.get("rpm")),
            "long_g": _safe_float(_first_present(telemetry, "long_g", "longG")),
            "lat_g": _safe_float(_first_present(telemetry, "lat_g", "latG")),
            "progress": _safe_float(_first_present(player, "progress", "completion")),
            "sector_index": _safe_int(_first_present(player, "sectorIndex", "sector_index")),
            "current_lap": _safe_int(_first_present(player, "currentLap", "current_lap", "lap")),
        }

    def _base_data(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "mode": snap["mode"],
            "track_name": snap["track_name"],
            "race_time": snap["timer"],
            "field_size": snap["field_size"],
            "position": snap["position"],
            "speed_kph": round(float(snap["speed_kph"] or 0.0), 1),
            "gap_text": snap["gap_text"],
            "gap_behind_text": snap["gap_behind_text"],
            "ahead_name": snap["ahead_name"],
            "behind_name": snap["behind_name"],
        }

    def _position_event(self, prev: Dict[str, Any], snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prev_pos = _safe_int(prev.get("position"))
        pos = _safe_int(snap.get("position"))
        if prev_pos <= 0 or pos <= 0 or prev_pos == pos:
            return None

        gained = pos < prev_pos
        data = self._base_data(snap)
        data.update({
            "old_position": prev_pos,
            "position_delta": prev_pos - pos,
            "opponent": snap["behind_name"] if gained else snap["ahead_name"],
            "ahead_gap_m": snap["ahead_gap_m"],
            "behind_gap_m": snap["behind_gap_m"],
        })
        return {"type": "ncm_overtake" if gained else "ncm_position_lost", "data": data}

    def _gap_event(self, prev: Dict[str, Any], snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ahead_gap = snap.get("ahead_gap_m")
        prev_ahead_gap = prev.get("ahead_gap_m")
        if ahead_gap is not None and prev_ahead_gap is not None and _safe_int(snap.get("position")) > 1:
            delta = float(prev_ahead_gap) - float(ahead_gap)
            crossed_close = float(prev_ahead_gap) >= 30.0 and float(ahead_gap) < 30.0
            if abs(delta) >= 18.0 or crossed_close:
                data = self._base_data(snap)
                data.update({
                    "relation": "ahead",
                    "opponent": snap["ahead_name"],
                    "old_gap_m": round(float(prev_ahead_gap), 1),
                    "gap_m": round(float(ahead_gap), 1),
                    "gap_delta_m": round(delta, 1),
                    "direction": "closing" if delta > 0 else "falling_back",
                })
                return {"type": "ncm_gap_change", "data": data}

        behind_gap = snap.get("behind_gap_m")
        prev_behind_gap = prev.get("behind_gap_m")
        if behind_gap is not None and prev_behind_gap is not None:
            delta = float(prev_behind_gap) - float(behind_gap)
            crossed_pressure = float(prev_behind_gap) >= 25.0 and float(behind_gap) < 25.0
            if delta >= 15.0 or crossed_pressure:
                data = self._base_data(snap)
                data.update({
                    "relation": "behind",
                    "opponent": snap["behind_name"],
                    "old_gap_m": round(float(prev_behind_gap), 1),
                    "gap_m": round(float(behind_gap), 1),
                    "gap_delta_m": round(delta, 1),
                    "direction": "under_pressure",
                })
                return {"type": "ncm_rival_pressure", "data": data}

        return None

    def _speed_event(self, prev: Dict[str, Any], snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prev_speed = float(prev.get("speed_kph") or 0.0)
        speed = float(snap.get("speed_kph") or 0.0)
        prev_band = self._speed_band(prev_speed)
        band = self._speed_band(speed)
        if band > prev_band or speed - prev_speed >= 45.0:
            data = self._base_data(snap)
            data.update({
                "old_speed_kph": round(prev_speed, 1),
                "speed_delta_kph": round(speed - prev_speed, 1),
                "threshold_kph": band,
                "throttle": snap["throttle"],
                "gear": snap["gear"],
                "rpm": snap["rpm"],
            })
            return {"type": "ncm_speed_spike", "data": data}
        return None

    def _incident_event(self, prev: Dict[str, Any], snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prev_health = float(prev.get("vehicle_health") or -1.0)
        health = float(snap.get("vehicle_health") or -1.0)
        prev_speed = float(prev.get("speed_kph") or 0.0)
        speed = float(snap.get("speed_kph") or 0.0)
        health_drop = prev_health - health if prev_health >= 0 and health >= 0 else 0.0
        speed_drop = prev_speed - speed
        if health_drop < 0.035 and speed_drop < 70.0:
            return None

        data = self._base_data(snap)
        data.update({
            "kind": "contact" if health_drop >= 0.035 else "speed_drop",
            "old_speed_kph": round(prev_speed, 1),
            "speed_drop_kph": round(max(0.0, speed_drop), 1),
            "vehicle_health": round(health, 3) if health >= 0 else None,
            "health_drop_pct": round(max(0.0, health_drop) * 100.0, 1),
            "long_g": snap["long_g"],
            "lat_g": snap["lat_g"],
        })
        return {"type": "ncm_incident", "data": data}

    def _sector_event(self, prev: Dict[str, Any], snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prev_sector = _safe_int(prev.get("sector_index"))
        sector = _safe_int(snap.get("sector_index"))
        prev_lap = _safe_int(prev.get("current_lap"))
        lap = _safe_int(snap.get("current_lap"))
        if sector <= 0 and lap <= 0:
            return None
        if sector == prev_sector and lap == prev_lap:
            return None

        data = self._base_data(snap)
        data.update({
            "old_sector": prev_sector,
            "sector": sector,
            "old_lap": prev_lap,
            "lap": lap,
            "progress": round(float(snap.get("progress") or 0.0), 4),
        })
        return {"type": "ncm_sector_change", "data": data}

    def _speed_band(self, speed_kph: float) -> int:
        band = 0
        for threshold in self.SPEED_BANDS_KPH:
            if speed_kph >= threshold:
                band = threshold
        return band


class _DirectMteRaceSynth:
    """Infer race lifecycle events from cp2077_state.json mte_* fields."""

    def __init__(self) -> None:
        self.last_track: int = -1
        self._prev: Dict[str, Any] = {}

    def process(self, raw: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, bool]:
        mode = str(raw.get("mte_race_mode") or "").strip().lower()
        state = str(raw.get("mte_race_state") or "idle").strip().lower()
        if not mode:
            state = "idle"

        current = {
            "mode": mode,
            "state": state,
            "position": _safe_int(raw.get("mte_race_position")),
            "field_size": _safe_int(raw.get("mte_race_field_size")),
            "race_time": _safe_float(raw.get("mte_race_time")),
            "track_name": str(raw.get("mte_track_name") or "").strip(),
            "remaining": _safe_int(raw.get("mte_knockout_remaining")),
            "danger": bool(raw.get("mte_knockout_danger")),
            "elim_cd": _safe_float(raw.get("mte_knockout_elim_cd")),
            "phase": str(raw.get("mte_knockout_phase") or "").strip().lower(),
            "district": str(raw.get("district") or raw.get("location") or "").strip(),
            "finish_emitted": False,
        }

        prev = dict(self._prev)
        prev_active = bool(prev.get("mode"))
        active = bool(current["mode"])
        events: List[Dict[str, Any]] = []

        if (
            prev_active
            and active
            and prev.get("mode") != current["mode"]
            and not prev.get("finish_emitted")
        ):
            events.append(self._finish_event(prev))
            prev["finish_emitted"] = True

        if current["mode"] == "sprint":
            events.extend(self._sprint_events(prev, current))
        elif current["mode"] == "knockout":
            events.extend(self._knockout_events(prev, current))

        if active and current["state"] in _FINISH_STATES:
            if not prev.get("finish_emitted"):
                events.append(self._finish_event(current))
            current["finish_emitted"] = True
        elif prev_active and not active:
            if not prev.get("finish_emitted"):
                events.append(self._finish_event(prev))
            current["finish_emitted"] = True
        else:
            current["finish_emitted"] = bool(prev.get("finish_emitted") and active)

        self._prev = current
        should_forward = active or prev_active or bool(events)
        return events, current["state"], should_forward

    def _sprint_events(
        self,
        prev: Dict[str, Any],
        current: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        prev_state = str(prev.get("state") or "")
        state = current["state"]

        if state == "prerace" and prev_state != "prerace":
            events.append({
                "type": "ncm_grid_ready",
                "data": {
                    "field_size": current["field_size"],
                    "track_name": current["track_name"],
                    "district": current["district"],
                },
            })

        if state == "quali" and prev_state != "quali":
            events.append({
                "type": "ncm_quali_start",
                "data": {
                    "district": current["district"],
                    "track_name": current["track_name"],
                    "race_type": "sprint",
                },
            })

        if prev_state == "quali" and state != "quali":
            events.append({
                "type": "ncm_quali_end",
                "data": {
                    "player_time": _safe_float(prev.get("race_time")),
                    "grid_pos": current["position"] or _safe_int(prev.get("position"), 1),
                    "track_name": current["track_name"] or str(prev.get("track_name") or ""),
                },
            })

        if state == "countdown" and prev_state != "countdown":
            events.append({
                "type": "ncm_race_countdown",
                "data": {
                    "seconds": max(1, round(current["race_time"])) or 3,
                    "track_name": current["track_name"],
                    "district": current["district"],
                },
            })

        if state == "racing" and prev_state != "racing":
            events.append({
                "type": "ncm_race_start",
                "data": {
                    "district": current["district"],
                    "field_size": current["field_size"],
                    "track_name": current["track_name"],
                    "race_type": "sprint",
                },
            })

        return events

    def _knockout_events(
        self,
        prev: Dict[str, Any],
        current: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        prev_state = str(prev.get("state") or "")
        state = current["state"]

        if state == "lobby" and prev_state != "lobby":
            events.append({
                "type": "ncm_knockout_lobby",
                "data": {
                    "circuit_name": current["track_name"],
                    "field_size": current["field_size"] or current["remaining"],
                },
            })

        if state == "countdown" and prev_state != "countdown":
            events.append({
                "type": "ncm_knockout_countdown",
                "data": {
                    "seconds": max(1, round(current["race_time"])) or 3,
                    "track_name": current["track_name"],
                    "field_size": current["field_size"] or current["remaining"],
                },
            })

        if state == "racing" and prev_state != "racing":
            events.append({
                "type": "ncm_knockout_start",
                "data": {
                    "track_name": current["track_name"],
                    "field_size": current["field_size"] or current["remaining"],
                    "remaining": current["remaining"],
                },
            })

        prev_remaining = _safe_int(prev.get("remaining"))
        if (
            prev_state == "racing"
            and state == "racing"
            and prev_remaining > 0
            and current["remaining"] > 0
            and current["remaining"] < prev_remaining
        ):
            events.append({
                "type": "ncm_driver_eliminated",
                "data": {
                    "remaining": current["remaining"],
                    "position": current["position"],
                    "track_name": current["track_name"],
                },
            })

        if not bool(prev.get("danger")) and current["danger"]:
            events.append({
                "type": "ncm_player_danger_zone",
                "data": {
                    "position": current["position"],
                    "total": current["remaining"] or current["field_size"],
                    "time_to_elim": current["elim_cd"],
                    "track_name": current["track_name"],
                },
            })

        prev_pos = _safe_int(prev.get("position"))
        if prev_state == "racing" and state == "racing" and prev_pos > 1 and current["position"] == 1:
            events.append({
                "type": "ncm_lead_change",
                "data": {
                    "new_leader": "V",
                    "new_leader_id": "player",
                    "track_name": current["track_name"],
                },
            })

        return events

    def _finish_event(self, state: Dict[str, Any]) -> Dict[str, Any]:
        mode = str(state.get("mode") or "")
        position = _safe_int(state.get("position"))
        track_name = str(state.get("track_name") or "")
        field_size = _safe_int(state.get("field_size"))

        if mode == "knockout":
            return {
                "type": "ncm_knockout_finish",
                "data": {
                    "player_position": position,
                    "position": position,
                    "player_won": position == 1 and position > 0,
                    "race_time": _safe_float(state.get("race_time")),
                    "field_size": field_size or _safe_int(state.get("remaining")),
                    "remaining": _safe_int(state.get("remaining")),
                    "track_name": track_name,
                    "payout": 0,
                    "rep_gain": 0,
                },
            }

        return {
            "type": "ncm_race_finish",
            "data": {
                "position": position,
                "player_time": _safe_float(state.get("race_time")),
                "field_size": field_size,
                "track_name": track_name,
                "payout": 0,
                "rep_gain": 0,
                "is_dnf": position <= 0,
            },
        }


def feed_worker(
    stop_event: threading.Event,
    mem: Dict[str, Any],
    payload: Dict[str, Any],
    runtime: Dict[str, Any] = None,
) -> None:
    """
    Main feed worker. Polls a rich sidecar when present and falls back to the
    direct cp2077_state.json bridge when only mte_* state is available.
    """
    runtime = runtime or {}
    cfg = payload or {}
    log = runtime.get("log", lambda role, msg: print(f"[{role}] {msg}"))

    tick_sec = float(cfg.get("tick_sec", FEED_DEFAULTS["tick_sec"]))
    state_file = cfg.get("state_file", "") or _DEFAULT_RACE_FILE
    state_files = _resolve_race_state_paths(state_file)
    cp_state_file = _resolve_cp_state_path(state_file, cfg.get("cp_state_file", ""))
    max_event_age_sec = float(cfg.get("max_event_age_sec", FEED_DEFAULTS["max_event_age_sec"]))
    max_events_per_tick = int(cfg.get("max_events_per_tick", FEED_DEFAULTS["max_events_per_tick"]))

    log(
        "ncm_race_feed",
        f"starting (ncm_files={state_files}, cp_file={cp_state_file}, tick={tick_sec}s)",
    )

    meta: Optional[Any] = _resolve_meta(runtime, log)
    if not meta:
        log("ncm_race_feed", "meta plugin not yet available - will retry each tick")

    last_sidecar_track_by_file: Dict[str, int] = {}
    live_synth = _LiveRaceSynth()
    direct = _DirectMteRaceSynth()
    using_direct = False

    while not stop_event.is_set():
        stop_event.wait(tick_sec)
        if stop_event.is_set():
            break

        if meta is None:
            meta = _resolve_meta(runtime, log)
            if meta is None:
                continue

        active_state_file = _first_existing_path(state_files)
        if active_state_file:
            raw = _read_json(active_state_file)
            if raw is None:
                continue

            file_seq = _safe_int(raw.get("seq"))
            file_ts = _safe_int(raw.get("ts"))
            track_val = file_seq if file_seq > 0 else file_ts
            last_sidecar_track = last_sidecar_track_by_file.get(active_state_file, -1)
            if track_val <= last_sidecar_track:
                continue
            last_sidecar_track_by_file[active_state_file] = track_val

            if using_direct:
                log("ncm_race_feed", f"rich ncm sidecar detected at {active_state_file} - switching from direct fallback")
                using_direct = False

            pending = raw.get("pending_events") or []
            race_state = str(raw.get("race_state", "idle"))
            events: List[Dict[str, Any]] = []
            for raw_ev in pending:
                ncm_type = raw_ev.get("type", "")
                ro_type = _EVENT_TYPE_MAP.get(ncm_type, f"ncm_{str(ncm_type).lower()}")
                events.append({
                    "type": ro_type,
                    "data": raw_ev.get("data", {}),
                    "_race_observed_ts": _safe_float(raw_ev.get("ts"), time.time()),
                    "_race_seq": track_val,
                })
            events.extend(live_synth.process(raw))
            events = _prepare_race_events(
                events,
                max_age_sec=max_event_age_sec,
                max_events_per_tick=max_events_per_tick,
            )

            if events:
                log("ncm_race_feed", f"forwarding {len(events)} rich event(s): {[e['type'] for e in events]}")

            try:
                heat_level = _resolve_race_heat_level(events, race_state)
                signal_score = 9.0 if heat_level >= 5 else float(max(heat_level, len(events)))
                meta.process_input({
                    "events": events,
                    "race_state": race_state,
                    "game_state": {"race_live": raw.get("live", {})},
                    "broadcast_state": "ACTIVE" if heat_level >= 3 else "IDLE",
                    "heat_level": heat_level,
                    "signal_score": signal_score,
                })
            except Exception as exc:
                log("ncm_race_feed", f"process_input error: {exc}")
            continue

        raw = _read_json(cp_state_file)
        if raw is None:
            continue

        track_val = _safe_int(raw.get("ts"))
        if track_val <= direct.last_track:
            continue
        direct.last_track = track_val

        if not using_direct:
            log("ncm_race_feed", "ncm sidecar missing - using direct mte_* fallback from cp2077_state.json")
            using_direct = True

        events, race_state, should_forward = direct.process(raw)
        if not should_forward:
            continue
        events = _prepare_race_events(
            events,
            max_age_sec=max_event_age_sec,
            max_events_per_tick=max_events_per_tick,
        )

        if events:
            log("ncm_race_feed", f"forwarding {len(events)} fallback event(s): {[e['type'] for e in events]}")

        try:
            heat_level = _resolve_race_heat_level(events, race_state)
            signal_score = 9.0 if heat_level >= 5 else float(max(heat_level, len(events)))
            meta.process_input({
                "events": events,
                "race_state": race_state,
                "game_state": raw,
                "broadcast_state": "ACTIVE" if heat_level >= 3 else "IDLE",
                "heat_level": heat_level,
                "signal_score": signal_score,
            })
        except Exception as exc:
            log("ncm_race_feed", f"process_input error: {exc}")

    log("ncm_race_feed", "feed stopped")


def _resolve_meta(runtime: Dict[str, Any], log) -> Optional[Any]:
    """Try several runtime keys to find the active meta plugin."""
    for key in ("ACTIVE_META_PLUGIN", "active_meta_plugin", "meta_plugin"):
        candidate = runtime.get(key)
        if candidate is not None:
            return candidate
    for plugin in (runtime.get("meta_plugins") or []):
        if type(plugin).__name__ == "NightCityChroniclesMeta":
            return plugin
    return None


def _resolve_cp_state_path(state_file: str, override: str) -> str:
    if override:
        return override
    if state_file:
        return os.path.join(os.path.dirname(state_file), "cp2077_state.json")
    return _DEFAULT_CP_STATE_FILE


def _resolve_race_state_paths(state_file: str) -> List[str]:
    paths: List[str] = []

    def add(path: str) -> None:
        if path and path not in paths:
            paths.append(path)

    add(state_file or _DEFAULT_RACE_FILE)
    add(_radioos_to_mte_sidecar(state_file or _DEFAULT_RACE_FILE))
    add(_DEFAULT_RACE_FILE)
    return paths


def _radioos_to_mte_sidecar(path: str) -> str:
    if not path:
        return ""
    candidate = path.replace("\\mods\\RadioOSBridge\\", "\\mods\\MT_Ecosystem\\")
    candidate = candidate.replace("/mods/RadioOSBridge/", "/mods/MT_Ecosystem/")
    return candidate


def _first_existing_path(paths: List[str]) -> str:
    for path in paths:
        if path and os.path.exists(path):
            return path
    return ""


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_present(source: Optional[Dict[str, Any]], *keys: str, default: Any = None) -> Any:
    if not isinstance(source, dict):
        return default
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return default


def _dict_or_none(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) and bool(value) else None


def _extract_player_pack(
    live: Dict[str, Any],
    standings: List[Any],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    player = _dict_or_none(live.get("player"))
    ahead = _dict_or_none(live.get("ahead"))
    behind = _dict_or_none(live.get("behind"))
    player_index = -1

    if not player:
        for idx, entry in enumerate(standings):
            if isinstance(entry, dict) and (entry.get("isPlayer") is True or entry.get("is_player") is True):
                player = entry
                player_index = idx
                break
    else:
        for idx, entry in enumerate(standings):
            if entry is player:
                player_index = idx
                break
            if (
                isinstance(entry, dict)
                and (entry.get("isPlayer") is True or entry.get("is_player") is True)
            ):
                player_index = idx
                break

    if player_index < 0:
        pos = _safe_int(_first_present(player, "position", default=live.get("estimated_position")))
        if 0 < pos <= len(standings):
            player_index = pos - 1
            if not player and isinstance(standings[player_index], dict):
                player = standings[player_index]

    if not ahead and 0 < player_index < len(standings) and isinstance(standings[player_index - 1], dict):
        ahead = standings[player_index - 1]
    if not behind and 0 <= player_index < len(standings) - 1 and isinstance(standings[player_index + 1], dict):
        behind = standings[player_index + 1]

    return player or {}, ahead, behind


def _driver_name(entry: Optional[Dict[str, Any]]) -> str:
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("name") or entry.get("displayName") or entry.get("driverId") or entry.get("id") or "")


def _driver_number(entry: Optional[Dict[str, Any]], *keys: str) -> Optional[float]:
    if not isinstance(entry, dict):
        return None
    value = _first_present(entry, *keys)
    if value is None:
        return None
    return _safe_float(value)


def _speed_kph(player: Dict[str, Any], telemetry: Dict[str, Any]) -> float:
    direct = _first_present(player, "speedKph", "speed_kph")
    if direct is not None:
        return _safe_float(direct)
    direct = _first_present(telemetry, "speed_kph", "speedKph")
    if direct is not None:
        return _safe_float(direct)

    speed_mps = _first_present(player, "speedMps", "speed_mps", "speed")
    if speed_mps is not None:
        return _safe_float(speed_mps) * 3.6
    speed_mps = _first_present(telemetry, "speed_mps", "speedMps", "speed")
    return _safe_float(speed_mps) * 3.6


def _gap_m(
    player: Optional[Dict[str, Any]],
    other: Optional[Dict[str, Any]],
    relation: str,
) -> Optional[float]:
    if not isinstance(player, dict) or not isinstance(other, dict):
        return None

    player_distance = _driver_number(player, "distanceAlong", "distance_along")
    other_distance = _driver_number(other, "distanceAlong", "distance_along")
    if player_distance is not None and other_distance is not None:
        gap = other_distance - player_distance if relation == "ahead" else player_distance - other_distance
        return max(0.0, gap)

    player_remaining = _driver_number(player, "remainingDistance", "remaining_distance", "distToFinish", "dist_to_finish")
    other_remaining = _driver_number(other, "remainingDistance", "remaining_distance", "distToFinish", "dist_to_finish")
    if player_remaining is not None and other_remaining is not None:
        gap = player_remaining - other_remaining if relation == "ahead" else other_remaining - player_remaining
        return max(0.0, gap)

    return None
