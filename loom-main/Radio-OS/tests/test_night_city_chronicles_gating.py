"""
Targeted tests for Night City Chronicles gating and secondary voice selection.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.meta.nc_chronicles_meta import NightCityChroniclesMeta
from plugins.meta.nc_chronicles_meta import BroadcastStateMode
from plugins.nc_story_feed import _classify_broadcast_state, _resolve_heat_level, _should_trigger_synthesis


def _ctx(**overrides):
    base = {
        "player_name": "V",
        "level": 20,
        "lifepath": "streetkid",
        "location": "Kabuki",
        "district": "Watson",
        "quest": "Ghost Town",
        "objective": "Meet Panam",
        "playstyle_label": "Mercenary",
        "death_count": 0,
        "recent_chapters": "(none yet)",
        "open_threads": "No open threads yet.",
        "lore_fragment": "",
        "last_chapter_ending": "Night City never resolves cleanly.",
        "live_state_block": "Health: 18%\nChrome active: sandevistan",
    }
    base.update(overrides)
    return base


def test_force_tick_prefers_low_signal_idle_over_texture_only_narration():
    pending_events = [
        {"type": "vehicle_entered", "data": {"vehicle_name": "Quadra Type-66"}},
        {"type": "weapon_switched", "data": {"weapon_type": "pistol"}},
        {"type": "time_of_day_changed", "data": {"time_name": "dusk"}},
        {"type": "dialogue_started", "data": {"dialogue_npc": "Dex"}},
    ]

    decision = _should_trigger_synthesis(
        pending_events=pending_events,
        event_threshold=5,
        heat=0.0,
        force_tick=True,
        min_force_signal_score=3.25,
        min_distinct_event_types=2,
    )

    assert decision["should_write"] is False
    assert decision["low_signal_force"] is True


def test_mixed_real_signal_can_trigger_synthesis_without_priority_event():
    pending_events = [
        {"type": "location_changed", "data": {"location": "Kabuki", "district": "Watson"}},
        {"type": "street_cred_up", "data": {"street_cred": 23, "gained": 2}},
        {"type": "combat_ended", "data": {"location": "Kabuki", "kills_this_combat": 3, "headshots_this_combat": 1}},
        {"type": "hack_burst", "data": {"location": "Kabuki", "ram_current": 2, "ram_max": 10}},
        {"type": "health_recovered", "data": {"health_pct": 0.82}},
    ]

    decision = _should_trigger_synthesis(
        pending_events=pending_events,
        event_threshold=5,
        heat=0.0,
        force_tick=False,
        min_force_signal_score=3.25,
        min_distinct_event_types=2,
    )

    assert decision["should_write"] is True
    assert decision["signal_score"] > 5.0
    assert decision["distinct_signal_types"] >= 2


def test_secondary_voice_prefers_ripperdoc_when_trauma_is_the_story():
    meta = NightCityChroniclesMeta()
    meta._broadcast_state = BroadcastStateMode.ACTIVE
    selection = meta._select_secondary_voice(
        _ctx(),
        [
            {"type": "near_death", "data": {"location": "Kabuki", "health_pct": 0.12}},
            {"type": "sandevistan_activated", "data": {"location": "Kabuki"}},
        ],
    )

    assert selection is not None
    assert selection["voice"] == "ripperdoc"
    assert selection["topic"] == "trauma"


def test_secondary_voice_topic_memory_blocks_immediate_repeat():
    meta = NightCityChroniclesMeta()
    meta._broadcast_state = BroadcastStateMode.ACTIVE
    ctx = _ctx(quest="Automatic Love", objective="Find Evelyn")
    events = [{"type": "quest_updated", "data": {"quest": "Automatic Love", "objective": "Find Evelyn"}}]

    selection = meta._select_secondary_voice(ctx, events)
    assert selection is not None
    assert selection["voice"] == "fixer"

    meta._remember_secondary_beat(
        selection["voice"],
        selection["topic"],
        selection["evidence"],
        "That debt just got written in ink.",
    )

    repeated = meta._select_secondary_voice(ctx, events)
    assert repeated is None


def test_broadcast_state_classifies_clear_idle_vs_active():
    idle_decision = _should_trigger_synthesis(
        pending_events=[],
        event_threshold=5,
        heat=0.0,
        force_tick=False,
        min_force_signal_score=3.25,
        min_distinct_event_types=2,
    )
    assert _classify_broadcast_state([], {}, idle_decision, 0.0) == "IDLE"

    active_events = [
        {"type": "near_death", "data": {"location": "Kabuki", "health_pct": 0.12}},
        {"type": "combat_ended", "data": {"location": "Kabuki", "kills_this_combat": 4}},
    ]
    active_decision = _should_trigger_synthesis(
        pending_events=active_events,
        event_threshold=5,
        heat=4.5,
        force_tick=False,
        min_force_signal_score=3.25,
        min_distinct_event_types=2,
    )
    assert _classify_broadcast_state(active_events, {"in_combat": True}, active_decision, 4.5) == "ACTIVE"


def test_secondary_voice_is_silent_in_idle_mode():
    meta = NightCityChroniclesMeta()
    meta._broadcast_state = BroadcastStateMode.IDLE
    selection = meta._select_secondary_voice(
        _ctx(),
        [{"type": "quest_updated", "data": {"quest": "Automatic Love", "objective": "Find Evelyn"}}],
    )
    assert selection is None


def test_low_mode_prefers_netrunner_over_fixer_for_mere_movement():
    meta = NightCityChroniclesMeta()
    meta._broadcast_state = BroadcastStateMode.LOW
    selection = meta._select_secondary_voice(
        _ctx(),
        [{"type": "location_changed", "data": {"location": "Kabuki", "district": "Watson"}}],
    )
    assert selection is None


def test_heat_level_respects_idle_and_peak_moments():
    idle_decision = _should_trigger_synthesis(
        pending_events=[],
        event_threshold=5,
        heat=0.0,
        force_tick=False,
        min_force_signal_score=3.25,
        min_distinct_event_types=2,
    )
    assert _resolve_heat_level([], {}, idle_decision, 0.0, "IDLE") == 0

    hot_events = [{"type": "near_death", "data": {"location": "Kabuki", "health_pct": 0.08}}]
    hot_decision = _should_trigger_synthesis(
        pending_events=hot_events,
        event_threshold=5,
        heat=8.8,
        force_tick=False,
        min_force_signal_score=3.25,
        min_distinct_event_types=1,
    )
    assert _resolve_heat_level(hot_events, {"in_combat": True}, hot_decision, 8.8, "ACTIVE") == 5


def test_low_heat_blocks_exclamation_and_can_use_ellipsis():
    meta = NightCityChroniclesMeta()
    result = meta._apply_prosody_profile(
        "Signal's thin tonight. Still waiting.",
        voice_key="host",
        heat_level=1,
        signal_score=0.4,
    )

    assert "!" not in result.text
    assert "..." in result.text
    assert result.used_exclamation is False


def test_high_heat_fixer_can_split_and_spend_one_exclamation():
    meta = NightCityChroniclesMeta()
    result = meta._apply_prosody_profile(
        "The light goes green, move now, do not waste the opening because the call already landed.",
        voice_key="fixer",
        heat_level=5,
        signal_score=4.5,
    )

    assert 1 <= result.text.count("!") <= 2
    assert result.sentence_count_after >= result.sentence_count_before


def test_corpo_exclamation_requires_heat_five():
    meta = NightCityChroniclesMeta()
    cool_result = meta._apply_prosody_profile(
        "Operational containment has failed.",
        voice_key="corpo",
        heat_level=4,
        signal_score=4.0,
    )
    hot_result = meta._apply_prosody_profile(
        "Operational containment has failed.",
        voice_key="corpo",
        heat_level=5,
        signal_score=4.5,
    )

    assert "!" not in cool_result.text
    assert hot_result.text.endswith("!")


def test_exclamation_cooldown_and_similarity_suppression_block_repeat_hype():
    meta = NightCityChroniclesMeta()
    first = meta._apply_prosody_profile(
        "That call just landed. Move now.",
        voice_key="fixer",
        heat_level=5,
        signal_score=4.2,
    )
    meta._record_voice_output("fixer", first.text, first.used_exclamation, 5, 4.2)
    second = meta._apply_prosody_profile(
        "That call just landed. Move now.",
        voice_key="fixer",
        heat_level=5,
        signal_score=4.2,
    )

    assert first.used_exclamation is True
    assert second.used_exclamation is False
    assert second.similarity_blocked is True


def test_race_event_selector_drops_stale_and_keeps_best_current_event():
    meta = NightCityChroniclesMeta()
    meta._race_event_ttl_sec = 4
    meta._min_race_beat_gap = 8
    now = 1000.0

    selected = meta._select_fresh_race_events(
        [
            {"type": "ncm_overtake", "_race_observed_ts": now - 10, "data": {}},
            {"type": "ncm_sector_change", "_race_observed_ts": now, "data": {}},
            {"type": "ncm_incident", "_race_observed_ts": now - 1, "data": {}},
        ],
        now=now,
    )

    assert [event["type"] for event in selected] == ["ncm_incident"]


def test_race_event_selector_uses_cadence_without_requeueing_backlog():
    meta = NightCityChroniclesMeta()
    meta._race_event_ttl_sec = 4
    meta._min_race_beat_gap = 8
    meta._race_urgent_gap = 3
    now = 1000.0
    meta._last_race_beat_ts = now - 2

    selected = meta._select_fresh_race_events(
        [{"type": "ncm_speed_spike", "_race_observed_ts": now, "data": {}}],
        now=now,
    )

    assert selected == []


def test_race_finish_bypasses_cadence_and_stale_midbeats():
    meta = NightCityChroniclesMeta()
    meta._race_event_ttl_sec = 4
    meta._race_finish_ttl_sec = 35
    now = 1000.0
    meta._last_race_beat_ts = now - 1

    selected = meta._select_fresh_race_events(
        [
            {"type": "ncm_gap_change", "_race_observed_ts": now - 12, "data": {}},
            {"type": "ncm_race_finish", "_race_observed_ts": now - 10, "data": {"position": 2}},
        ],
        now=now,
    )

    assert [event["type"] for event in selected] == ["ncm_race_finish"]
