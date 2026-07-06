"""
Tests for the MT/NCM race feed fallback synthesizer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.ncm_race_feed import (
    _DirectMteRaceSynth,
    _LiveRaceSynth,
    _prepare_race_events,
    _resolve_race_heat_level,
    _resolve_race_state_paths,
)


class TestDirectMteRaceSynth:
    def test_synthesizes_sprint_lifecycle_from_cp_state(self):
        synth = _DirectMteRaceSynth()

        events, race_state, should_forward = synth.process({
            "mte_race_mode": "sprint",
            "mte_race_state": "countdown",
            "mte_race_time": 3,
            "mte_race_field_size": 8,
            "mte_track_name": "Dockyard Dash",
            "district": "Santo Domingo",
        })
        assert should_forward is True
        assert race_state == "countdown"
        assert [e["type"] for e in events] == ["ncm_race_countdown"]

        events, race_state, should_forward = synth.process({
            "mte_race_mode": "sprint",
            "mte_race_state": "racing",
            "mte_race_time": 12.5,
            "mte_race_position": 4,
            "mte_race_field_size": 8,
            "mte_track_name": "Dockyard Dash",
            "district": "Santo Domingo",
        })
        assert should_forward is True
        assert race_state == "racing"
        assert [e["type"] for e in events] == ["ncm_race_start"]

        events, race_state, should_forward = synth.process({
            "district": "Santo Domingo",
        })
        assert should_forward is True
        assert race_state == "idle"
        assert [e["type"] for e in events] == ["ncm_race_finish"]
        assert events[0]["data"]["position"] == 4

    def test_synthesizes_knockout_beats_and_finish_from_cp_state(self):
        synth = _DirectMteRaceSynth()

        events, _, _ = synth.process({
            "mte_race_mode": "knockout",
            "mte_race_state": "lobby",
            "mte_race_field_size": 7,
            "mte_knockout_remaining": 7,
            "mte_track_name": "Harbor Circuit",
        })
        assert [e["type"] for e in events] == ["ncm_knockout_lobby"]

        events, _, _ = synth.process({
            "mte_race_mode": "knockout",
            "mte_race_state": "countdown",
            "mte_race_time": 3,
            "mte_race_field_size": 7,
            "mte_knockout_remaining": 7,
            "mte_track_name": "Harbor Circuit",
        })
        assert [e["type"] for e in events] == ["ncm_knockout_countdown"]

        events, _, _ = synth.process({
            "mte_race_mode": "knockout",
            "mte_race_state": "racing",
            "mte_race_time": 20,
            "mte_race_position": 3,
            "mte_race_field_size": 7,
            "mte_knockout_remaining": 7,
            "mte_knockout_danger": False,
            "mte_track_name": "Harbor Circuit",
        })
        assert [e["type"] for e in events] == ["ncm_knockout_start"]

        events, _, _ = synth.process({
            "mte_race_mode": "knockout",
            "mte_race_state": "racing",
            "mte_race_time": 31,
            "mte_race_position": 5,
            "mte_race_field_size": 6,
            "mte_knockout_remaining": 6,
            "mte_knockout_danger": True,
            "mte_knockout_elim_cd": 4.2,
            "mte_track_name": "Harbor Circuit",
        })
        assert [e["type"] for e in events] == [
            "ncm_driver_eliminated",
            "ncm_player_danger_zone",
        ]

        events, race_state, should_forward = synth.process({})
        assert should_forward is True
        assert race_state == "idle"
        assert [e["type"] for e in events] == ["ncm_knockout_finish"]
        assert events[0]["data"]["position"] == 5


class TestLiveRaceSynth:
    def test_rich_sidecar_generates_overtake_gap_speed_and_incident_beats(self):
        synth = _LiveRaceSynth()

        assert synth.process({
            "race_state": "racing",
            "live": {
                "mode": "sprint",
                "timer": 10.0,
                "track_name": "Dockyard Dash",
                "field_size": 8,
                "estimated_position": 4,
                "gap_text": "+70m",
                "gap_behind_text": "20m behind",
                "telemetry": {"speed_kph": 110, "vehicle_health": 0.95},
                "player": {"position": 4, "name": "V", "isPlayer": True, "distanceAlong": 1000, "sectorIndex": 2},
                "ahead": {"position": 3, "name": "Mox Runner", "distanceAlong": 1070},
                "behind": {"position": 5, "name": "Tyger Tail", "distanceAlong": 980},
            },
        }) == []

        events = synth.process({
            "race_state": "racing",
            "live": {
                "mode": "sprint",
                "timer": 12.0,
                "track_name": "Dockyard Dash",
                "field_size": 8,
                "estimated_position": 3,
                "gap_text": "+25m",
                "gap_behind_text": "8m behind",
                "telemetry": {"speed_kph": 190, "vehicle_health": 0.86, "long_g": -1.2, "lat_g": 0.4},
                "player": {"position": 3, "name": "V", "isPlayer": True, "distanceAlong": 1100, "sectorIndex": 2},
                "ahead": {"position": 2, "name": "Valentino Redline", "distanceAlong": 1125},
                "behind": {"position": 4, "name": "Mox Runner", "distanceAlong": 1092},
            },
        })

        event_types = [event["type"] for event in events]
        assert event_types == [
            "ncm_incident",
            "ncm_overtake",
            "ncm_gap_change",
            "ncm_speed_spike",
        ]
        assert events[1]["data"]["opponent"] == "Mox Runner"
        assert events[2]["data"]["gap_m"] == 25.0
        assert events[3]["data"]["threshold_kph"] == 180

    def test_active_race_state_resolves_to_max_heat_without_events(self):
        assert _resolve_race_heat_level([], "racing") == 5
        assert _resolve_race_heat_level([], "results") == 0


def test_radioos_sidecar_path_also_checks_mte_sidecar():
    paths = _resolve_race_state_paths(
        "D:/SteamLibrary/steamapps/common/Cyberpunk 2077/bin/x64/plugins/cyber_engine_tweaks/mods/RadioOSBridge/ncm_race_state.json"
    )

    assert paths[0].endswith("RadioOSBridge/ncm_race_state.json")
    assert any(path.endswith("MT_Ecosystem/ncm_race_state.json") for path in paths)


def test_prepare_race_events_drops_stale_midrace_but_keeps_finish():
    now = 1000.0
    prepared = _prepare_race_events(
        [
            {"type": "ncm_speed_spike", "_race_observed_ts": now - 10, "data": {}},
            {"type": "ncm_overtake", "_race_observed_ts": now - 1, "data": {}},
            {"type": "ncm_sector_change", "_race_observed_ts": now, "data": {}},
            {"type": "ncm_race_finish", "_race_observed_ts": now - 20, "data": {}},
        ],
        now=now,
        max_age_sec=4,
        max_events_per_tick=1,
    )

    assert [event["type"] for event in prepared] == ["ncm_race_finish", "ncm_overtake"]
