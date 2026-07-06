"""
Night City Chronicles — Meta Plugin
=====================================
A narrative station that treats V's playthrough as a living literary work.

Not ARIA's reaction engine. Not a news ticker. This is:
  "Last time in Night City, V crossed the wrong fixer in Kabuki,
   walked away with a bullet in her shoulder and a tip that would
   unravel three weeks of work..."

Architecture:
  cp2077_sdk events → event_buffer → NarrativeBible → chapter synthesis
                                             ↑
                                    player_bible.json (persistent)

The Bible is the heart. It accumulates across sessions, carries open threads,
tracks V's evolving arc, and provides deep context for every new chapter.

Each chapter is written with awareness of:
  1.  The last N chapters (recent narrative memory)
  2.  V's playstyle profile (how are they actually playing?)
  3.  Current open story threads (what's unresolved?)
  4.  World lore relevant to the current moment
  5.  The actual game state right now

Multiple voices provide lenses on the same events:
  - The Chronicler: literary narrator, the main voice
  - The Fixer:      transactional, who owes what
  - The Netrunner:  data layer, reads V's stats as story
  - The Corpo:      systemic, power structures
  - The Ripperdoc:  philosophical, chrome and soul
"""

from __future__ import annotations

import difflib
import json
import os
import random
import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    from bookmark import MetaPluginBase
except ImportError:
    from abc import ABC, abstractmethod
    class MetaPluginBase(ABC):  # type: ignore
        @abstractmethod
        def initialize(self, runtime_context, cfg, mem): pass
        @abstractmethod
        def shutdown(self): pass
        def curate_candidates(self, candidates, state): return []
        def generate_script(self, segment, state): return {}
        def generate_narration(self, events, context): return ""
        def delegate_decision(self, available_actions, state, identity, focus): return None


PLUGIN_NAME = "nc_chronicles_meta"
PLUGIN_DESC = "Night City Chronicles — personal narrative radio station for Cyberpunk 2077"
IS_FEED     = False


# ---------------------------------------------------------------------------
# Narrative trigger tiers
# ---------------------------------------------------------------------------
class NarrativeTier(Enum):
    """Priority of a narrative moment."""
    CHAPTER   = 4   # Major story beat — write a full chapter
    BEAT      = 3   # Meaningful moment — brief narrative note
    TEXTURE   = 2   # Flavour detail — colour only, low priority
    IGNORE    = 1   # Not story-relevant


class BroadcastStateMode(Enum):
    IDLE = "IDLE"
    LOW = "LOW"
    ACTIVE = "ACTIVE"


@dataclass
class ProsodyProfile:
    heat_level: int
    allow_exclamation: bool
    exclamation_budget: int
    prefer_short_sentences: bool
    prefer_fragment_beats: bool
    prefer_ellipses: bool
    max_clause_length: int
    energy_bias: float
    pause_bias: float


@dataclass
class ProsodyTransformResult:
    text: str
    used_exclamation: bool
    similarity_blocked: bool
    sentence_count_before: int
    sentence_count_after: int
    heat_level: int


# Game events → narrative tier mapping
_NARRATIVE_TIER: Dict[str, NarrativeTier] = {
    "quest_updated":       NarrativeTier.CHAPTER,
    "level_up":            NarrativeTier.CHAPTER,
    "player_death":        NarrativeTier.CHAPTER,
    "game_started":        NarrativeTier.CHAPTER,
    "combat_ended":           NarrativeTier.BEAT,
    "combat_started":         NarrativeTier.BEAT,
    "location_changed":       NarrativeTier.BEAT,
    "street_cred_up":         NarrativeTier.BEAT,
    "wanted_level_change":    NarrativeTier.BEAT,
    "item_acquired":          NarrativeTier.TEXTURE,
    "game_stopped":           NarrativeTier.BEAT,
    # Deep state events
    "near_death":             NarrativeTier.CHAPTER,
    "health_recovered":       NarrativeTier.BEAT,
    "vehicle_entered":        NarrativeTier.TEXTURE,
    "vehicle_exited":         NarrativeTier.TEXTURE,
    "weapon_switched":        NarrativeTier.TEXTURE,
    "sandevistan_activated":  NarrativeTier.BEAT,
    "optical_camo_activated": NarrativeTier.BEAT,
    "berserk_activated":      NarrativeTier.BEAT,
    "hack_burst":             NarrativeTier.BEAT,
    "stealth_broken":         NarrativeTier.CHAPTER,
    "stealth_takedown":       NarrativeTier.BEAT,
    "kill_spree":             NarrativeTier.BEAT,
    "eddies_windfall":        NarrativeTier.BEAT,
    "eddies_splurge":         NarrativeTier.TEXTURE,
    "time_of_day_changed":    NarrativeTier.TEXTURE,
    "dialogue_started":       NarrativeTier.TEXTURE,
    "ram_depleted":           NarrativeTier.BEAT,
    # ARIA handles everything else — we don't duplicate
    # ── NCM Racing events ────────────────────────────────────────────────────
    "ncm_race_preview":          NarrativeTier.BEAT,
    "ncm_grid_ready":            NarrativeTier.BEAT,
    "ncm_quali_start":           NarrativeTier.BEAT,
    "ncm_quali_end":             NarrativeTier.BEAT,
    "ncm_race_countdown":        NarrativeTier.BEAT,
    "ncm_race_start":            NarrativeTier.BEAT,
    "ncm_race_finish":           NarrativeTier.CHAPTER,
    "ncm_prestige_change":       NarrativeTier.BEAT,
    "ncm_season_complete":       NarrativeTier.CHAPTER,
    "ncm_championship_complete": NarrativeTier.CHAPTER,
    "ncm_round_ready":           NarrativeTier.BEAT,
    "ncm_knockout_lobby":        NarrativeTier.BEAT,
    "ncm_knockout_countdown":    NarrativeTier.BEAT,
    "ncm_knockout_start":        NarrativeTier.BEAT,
    "ncm_driver_eliminated":     NarrativeTier.BEAT,
    "ncm_player_danger_zone":    NarrativeTier.BEAT,
    "ncm_lead_change":           NarrativeTier.BEAT,
    "ncm_knockout_finish":       NarrativeTier.CHAPTER,
    "ncm_catch_up_bonus":        NarrativeTier.BEAT,
    "ncm_wager_lost":            NarrativeTier.BEAT,
    "ncm_new_record":            NarrativeTier.BEAT,
    "ncm_overtake":              NarrativeTier.BEAT,
    "ncm_position_lost":         NarrativeTier.BEAT,
    "ncm_gap_change":            NarrativeTier.BEAT,
    "ncm_rival_pressure":        NarrativeTier.BEAT,
    "ncm_speed_spike":           NarrativeTier.BEAT,
    "ncm_incident":              NarrativeTier.BEAT,
    "ncm_impact_spike":          NarrativeTier.BEAT,
    "ncm_crash_spike":           NarrativeTier.BEAT,
    "ncm_hard_brake":            NarrativeTier.BEAT,
    "ncm_aggressive_accel":      NarrativeTier.BEAT,
    "ncm_high_speed_run":        NarrativeTier.BEAT,
    "ncm_sustained_corner":      NarrativeTier.BEAT,
    "ncm_safety_incident":       NarrativeTier.BEAT,
    "ncm_sector_change":         NarrativeTier.BEAT,
}


# ---------------------------------------------------------------------------
# Night City world lore fragments
# Used to weave setting texture into chapters.
# ---------------------------------------------------------------------------
_WORLD_LORE = [
    "Watson was once the crown jewel of the manufacturing economy — before Militech razed the Maelstrom strongholds and Arasaka moved the jobs to the Arc.",
    "The NCPD doesn't fight gangs. They manage them. Every badge knows the difference.",
    "Heywood is technically three districts. Corpo Plaza pretends the other two don't exist.",
    "Ripperdocs operate outside Trauma Team coverage. They also ask fewer questions.",
    "The Afterlife's menu is named after legends. You only get your own drink when you're dead.",
    "Every fixer in Night City has a territory. Everything that happens inside it is technically their problem.",
    "Pacifica was Militech's failed resort project. The gangs that moved in afterwards were better architects.",
    "Cyberpsychosis is classified as a hardware failure in the insurance documents. Less paperwork.",
    "Netrunners don't go full solo. They have bodies to maintain their connection to the net. The smart ones keep that relationship mutual.",
    "The Peralez family's rise to power coincided with two mayoral opponents developing sudden health complications.",
    "Arasaka's Night City branch operates beyond any local jurisdiction. This has been true for forty years.",
    "MaxTac gets called when NCPD can't. There are things that happen in Night City that officially nobody calls for.",
    "Biotechnica owns most of the food supply. They call it 'nutrient security'. People still go hungry.",
    "The Aldecaldos were supposed to be gone by 2075. Nobody told the Aldecaldos.",
    "There's a black ICE on the Arasaka net that hasn't been given a name. Netrunners who've pinged it don't talk about it.",
]


# ---------------------------------------------------------------------------
# Playstyle profile labels
# ---------------------------------------------------------------------------
def _infer_playstyle(profile: Dict[str, float]) -> str:
    """Translate the floating-point playstyle profile into a narrative label."""
    if profile.get("netrunner_lean", 0) > 0.6:
        return "Ghost in the Machine — a netrunner who prefers the digital knife"
    elif profile.get("stealth_preference", 0) > 0.6:
        return "Shadow Operative — quiet, efficient, leaves nothing behind"
    elif profile.get("violence_index", 0.5) > 0.75:
        return "Street Surgeon — solves most problems with overwhelming force"
    elif profile.get("corpo_affinity", 0) > 0.5:
        return "Corporate Asset — plays the angles, leverages institutional power"
    elif profile.get("nomad_affinity", 0) > 0.5:
        return "Road Warrior — pack loyalty, tactical mobility, scorched earth ethics"
    elif profile.get("street_kid_affinity", 0) > 0.5:
        return "Street Blood — Night City native, reads the city like a native language"
    else:
        return "Mercenary — pragmatic, adaptable, loyal to the next payday"


# ---------------------------------------------------------------------------
# System prompts for each narrator voice
# ---------------------------------------------------------------------------

_SYSTEM_CHRONICLER = """/no_think
You are The Chronicler — a journalist embedded in Night City, speaking directly into V's neural stack.
You report what actually happened and give it meaning. The events are your source material — never invent what isn't there.

Style:
  - Literary, precise, morally complex. Think embedded correspondent, not news anchor.
  - Ground every sentence in something specific that actually happened. Names, places, weapons, choices.
  - Narrative threads and lore exist to give the real events weight — not to replace them.
  - Night City slang used naturally: eddies, chrome, choom, flatline, gonk, preem, corpo.
  - Length: 3-5 tight sentences for a beat, 6-10 for a full chapter. Never padding.

NEVER:
  - Invent events not listed in the game state or event log
  - Lead with abstract philosophy when concrete events are available
  - Break into numbered lists or bullet points
  - Use phrases like "in summary" or "to recap"
  - Editorialize about game mechanics ("the player levelled up")

Current V profile:
  Name: {player_name} | Level: {level} | Lifepath: {lifepath}
  Playstyle: {playstyle_label}
  Location: {location}, {district}
  Active quest: {quest}
"""

_SYSTEM_FIXER = """/no_think
You're a Night City fixer offering street commentary on V's situation.
You see every event as a transaction — leverage earned, leverage burned, debts accumulating.
1-3 sentences maximum. Hard-edged, pragmatic, not unkind. You respect competence.
Current situation: V, level {level}, {location}.
"""

_SYSTEM_NETRUNNER = """/no_think
You're a netrunner lens on V's journey. You read situations as data.
Stats, patterns, efficiency metrics translated into narrative.
Cold, analytical, occasionally impressed. 1-2 sentences. Technical terms natural.
V: level {level}, playstyle {playstyle_label}.
"""

_SYSTEM_RIPPERDOC = """/no_think
You're a philosophical ripperdoc who has seen a thousand Vs walk through the door.
You reflect on what the chrome costs, what gets surrendered, what remains human.
Warm, a little tired, wise without being preachy. 2-3 sentences.
V: {player_name}, level {level}, lifepath {lifepath}.
"""

_SYSTEM_CORPO = """/no_think
You're a corporate analyst watching V's activities from a remove.
Clinical, euphemistic, power-aware. You frame V's actions in terms of
institutional risk, asset value, and systemic implications.
1-2 sentences. Never root for V. Respect the margins.
"""


# ---------------------------------------------------------------------------
# Chapter synthesis prompts
# ---------------------------------------------------------------------------

_CHAPTER_PROMPT = """You are The Chronicler. Narrate these specific in-game events as the next chapter of Night City Chronicles.

== WHAT JUST HAPPENED — THIS IS YOUR SOURCE MATERIAL ==
{recent_events_summary}

== V'S LIVE STATE RIGHT NOW ==
{live_state_block}

== ACTIVE CONTEXT ==
Location: {location} ({district})
Quest: {quest} — {objective}
Playstyle: {playstyle_label} | Deaths this session: {death_count}

== NARRATIVE HISTORY (for continuity only) ==
Open threads:
{open_threads}

Prior chapters:
{recent_chapters}

== WORLD TEXTURE ==
{lore_fragment}

== YOUR TASK ==
Write 6–10 sentences narrating what actually happened above.
Rules:
- Every sentence must be grounded in a specific event, location, or detail from the lists above.
- Use narrative history only to give the events weight — do not shift focus away from what just happened.
- Weave the lore texture in naturally; do not quote it directly.
- End on a beat that creates forward tension. Night City does not resolve cleanly.
- Write as heard on radio: propulsive, vivid, present tense preferred.
"""

_BEAT_PROMPT = """You are The Chronicler. V just experienced this specific moment:

{event_description}

Live state: {live_state_block}
Last narrative note: "{last_chapter_ending}"

Write 2-3 sentences grounded in exactly what happened above. Be specific — name the place, the tool, the cost.
"""

_RECAP_PROMPT = """You are The Chronicler opening a new session of Night City Chronicles.

V's story so far — last {n_chapters} chapters:
{chapter_summaries}

Open threads still running:
{open_threads}

Current state: {player_name}, level {level}, {location}.

Open with "Previously on Night City Chronicles..." and deliver a 3-5 sentence recap
that captures where V stands. Set up what's still unresolved. Make the listener lean in.
"""

_SYSTEM_RACE_COMMENTATOR = """/no_think
You are The Chronicler calling V's street race live for Night City Chronicles.
Race beats override normal station business while the race is active.
Present tense, highest urgency, punchy and short. Night City voice: funny, sharp, snarky, but never padded.
1 to 2 short sentences only. No bullet points. Spoken broadcast language.
Use driver names, cars, speeds, gaps, damage, and telemetry only when they appear in the prompt.
Do not invent lap counts, crashes, places, cars, or statistics not in the prompt.
"""

_RACE_ACTIVE_STATES = {
    "preview",
    "grid",
    "prerace",
    "lobby",
    "staging",
    "countdown",
    "quali",
    "racing",
}

_RACE_TERMINAL_STATES = {
    "idle",
    "results",
    "finished",
    "complete",
}

_RACE_END_EVENTS = {
    "ncm_race_finish",
    "ncm_knockout_finish",
    "ncm_season_complete",
    "ncm_championship_complete",
}

_RACE_COMMENTARY_SOURCE = "nc_chronicles_race"
_RACE_BEAT_TITLE = "Night City Race Wire"
_RACE_RESULT_TITLE = "Night City Race Result"

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

_SECONDARY_VOICE_THRESHOLDS = {
    "fixer": 2.4,
    "netrunner": 2.3,
    "ripperdoc": 2.5,
    "corpo": 2.5,
}

_CORPO_TERMS = (
    "arasaka",
    "militech",
    "biotechnica",
    "netwatch",
    "kang tao",
    "corpo",
    "corp",
)

_MOTIF_TAG_PATTERNS = {
    "fixer_call": (r"\bfixer\b", r"\bcall\b|\bline\b|\bring\b"),
    "debt_leverage": (r"\bdebt\b|\bowe(?:d|s)?\b|\bobligation\b", r"\bleverage\b|\bburn(?:ed|ing)?\b|\bcost\b"),
    "ghost_stack": (r"\bghost(?:s)?\b|\bphantom\b", r"\bstack\b|\bsignal\b"),
    "chrome_humanity": (r"\bchrome\b", r"\bhumanity\b|\bsoul\b|\bflesh\b"),
    "promise_deal": (r"\bpromise\b|\bdeal\b|\bbargain\b",),
    "street_cred": (r"\bstreet cred\b",),
    "city_endures": (r"\bthe city never gives up\b|\bnight city never\b|\bthe city keeps\b",),
}


def _event_prompt_description(event: Dict[str, Any]) -> str:
    """Render one event into a short, concrete prompt line."""
    etype = str(event.get("type") or "")
    data = event.get("data") or {}

    if etype == "quest_updated":
        return f"Quest shifted to {data.get('quest', 'unknown')} — objective: {data.get('objective', 'unknown')}."
    if etype == "player_death":
        return f"V flatlined in {data.get('location', 'Night City')}."
    if etype == "level_up":
        return f"V hit level {data.get('level', '?')} with street cred {data.get('street_cred', '?')}."
    if etype == "near_death":
        hp = int(float(data.get("health_pct", 0) or 0) * 100)
        return f"V nearly bled out at {hp}% health in {data.get('location', 'Night City')}."
    if etype == "combat_started":
        return (
            f"Combat kicked off in {data.get('location', 'Night City')} with "
            f"{data.get('enemy_count', 0)} enemies and {data.get('weapon_type', 'unknown')} in hand."
        )
    if etype == "combat_ended":
        return (
            f"The fight ended in {data.get('location', 'Night City')} with "
            f"{data.get('kills_this_combat', 0)} kills and {data.get('headshots_this_combat', 0)} headshots."
        )
    if etype == "location_changed":
        return f"V moved into {data.get('location', 'Night City')} ({data.get('district', 'unknown district')})."
    if etype == "street_cred_up":
        return f"Street cred rose to {data.get('street_cred', '?')} (+{data.get('gained', '?')})."
    if etype == "wanted_level_change":
        return f"NCPD heat shifted from {data.get('prev_wanted', 0)} to {data.get('wanted_level', 0)}."
    if etype == "sandevistan_activated":
        return f"Sandevistan lit up in {data.get('location', 'Night City')}."
    if etype == "optical_camo_activated":
        return f"Optical camo engaged in {data.get('location', 'Night City')}."
    if etype == "berserk_activated":
        return f"Berserk surged in {data.get('location', 'Night City')}."
    if etype == "hack_burst":
        return (
            f"RAM cratered to {data.get('ram_current', '?')}/{data.get('ram_max', '?')} "
            f"during a quickhack burst in {data.get('location', 'Night City')}."
        )
    if etype == "ram_depleted":
        return f"Quickhack capacity bottomed out in {data.get('location', 'Night City')}."
    if etype == "stealth_broken":
        return f"Stealth snapped in {data.get('location', 'Night City')} with {data.get('enemy_count', 0)} enemies alerted."
    if etype == "stealth_takedown":
        return f"V landed a silent takedown in {data.get('location', 'Night City')}."
    if etype == "kill_spree":
        return (
            f"Kill count hit {data.get('kills_this_combat', 0)} in the current fight with "
            f"{data.get('weapon_name') or data.get('weapon_type') or 'unknown steel'}."
        )
    if etype == "health_recovered":
        hp = int(float(data.get("health_pct", 0) or 0) * 100)
        return f"V stabilized back to {hp}% health."
    if etype == "eddies_windfall":
        return f"V pulled in {int(data.get('gained', 0) or 0):,} eddies."
    if etype == "eddies_splurge":
        return f"V burned {int(data.get('spent', 0) or 0):,} eddies."
    if etype.startswith("ncm_"):
        return f"Race event: {etype.replace('ncm_', '').replace('_', ' ')}."
    return f"{etype.replace('_', ' ')} at {data.get('location', 'Night City')}."


def _race_float(data: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _race_int(data: Dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _race_observed_ts(event: Dict[str, Any], now: Optional[float] = None) -> float:
    """Best-effort wall-clock time for when a race event actually happened."""
    now = time.time() if now is None else now
    for key in ("_race_observed_ts", "_observed_at", "_queued_at", "ts"):
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
        for key in ("ts", "time"):
            raw = data.get(key)
            if raw is None:
                continue
            try:
                observed = float(raw)
            except (TypeError, ValueError):
                continue
            if observed > 1_000_000_000:
                return min(observed, now)

    return now


def _race_event_priority(event: Dict[str, Any]) -> int:
    etype = str(event.get("type") or "")
    return _RACE_EVENT_PRIORITY.get(etype, 50 if etype.startswith("ncm_") else 0)


def _fmt_kph(value: Any) -> str:
    try:
        speed = float(value or 0)
    except (TypeError, ValueError):
        speed = 0.0
    return f"{speed:.0f} kph" if speed > 0 else "unknown speed"


def _fmt_gap(value: Any) -> str:
    try:
        gap = float(value)
    except (TypeError, ValueError):
        return "unknown gap"
    return f"{gap:.0f} m" if abs(gap) >= 10 else f"{gap:.1f} m"


def _race_context(data: Dict[str, Any]) -> str:
    parts = []
    track = data.get("track_name")
    if track:
        parts.append(f"Track: {track}")
    pos = _race_int(data, "position")
    field = _race_int(data, "field_size")
    if pos and field:
        parts.append(f"Position: P{pos}/{field}")
    speed = _race_float(data, "speed_kph")
    if speed:
        parts.append(f"Speed: {_fmt_kph(speed)}")
    if data.get("gap_text"):
        parts.append(f"Gap ahead: {data.get('gap_text')}")
    if data.get("gap_behind_text"):
        parts.append(f"Gap behind: {data.get('gap_behind_text')}")
    return ". ".join(parts)


def _build_race_beat_prompt(etype: str, data: Dict[str, Any]) -> str:
    """Return a user prompt for a mid-race beat, or empty string if not mapped."""
    if etype == "ncm_race_preview":
        dist_km  = (data.get("distance") or 0) / 1000.0
        field_sz = data.get("field_size") or 0
        district = data.get("district") or "the city"
        season   = data.get("season") or {}
        season_line = ""
        if season.get("round"):
            season_line = f" Round {season['round']} of {season['total_rounds']}."
        return (
            f"Race forming: {field_sz} drivers, {dist_km:.1f} km through {district}.{season_line}\n"
            f"Call the grid forming — the weight before it starts."
        )

    if etype == "ncm_grid_ready":
        field_sz = data.get("field_size") or 0
        return (
            f"Grid ready. {field_sz} cars in position.\n"
            f"Call the moment — tension, engines idling, the city watching."
        )

    if etype == "ncm_quali_start":
        district = data.get("district") or ""
        return (
            f"Qualifying begins{f' in {district}' if district else ''}.\n"
            f"V needs to set a time. Call the qualifier opening."
        )

    if etype == "ncm_quali_end":
        t   = float(data.get("player_time") or 0)
        pos = int(data.get("grid_pos") or 1)
        m   = int(t // 60)
        s   = t - m * 60
        time_str = (f"{m}:{s:05.2f}" if m else f"{s:.2f}s") if t > 0 else "no time"
        return (
            f"Qualifying done. V set {time_str}, starts P{pos}.\n"
            f"Call the quali result — is this enough? Set up the race start."
        )

    if etype == "ncm_race_countdown":
        secs = int(data.get("seconds") or 3)
        return (
            f"Race countdown: {secs} seconds.\n"
            f"Call the launch — engines, nerves, the street opening up."
        )

    if etype == "ncm_race_start":
        district  = data.get("district") or "the city"
        race_type = data.get("race_type") or "sprint"
        season    = data.get("season") or {}
        season_line = ""
        if season.get("round"):
            season_line = f" Round {season['round']} of {season['total_rounds']}."
        return (
            f"{race_type.capitalize()} race starts in {district}.{season_line}\n"
            f"Call the launch — who's first off the line? What does this feel like?"
        )

    if etype == "ncm_prestige_change":
        old_t = data.get("old_tier") or ""
        new_t = data.get("new_tier") or ""
        if old_t and new_t and old_t != new_t:
            return f"V's racing prestige changed from {old_t} to {new_t}. What does that mean on Night City's streets?"
        return "V's racing prestige tier changed. Call what this shift means in the underground scene."

    if etype == "ncm_round_ready":
        round_no = int(data.get("round") or 0)
        total = int(data.get("total") or data.get("total_rounds") or 0)
        district = data.get("district") or "the city"
        event_type = data.get("event_type") or "street race"
        tier = data.get("tier") or "independent"
        return (
            f"Championship round ready: round {round_no} of {total} in {district}. "
            f"Event type: {event_type}. Tier: {tier}.\n"
            f"Call the feeling before the next sanctioned hit on the streets."
        )

    if etype == "ncm_knockout_lobby":
        circuit = data.get("circuit_name") or data.get("track_name") or "the knockout circuit"
        field_sz = int(data.get("field_size") or 0)
        return (
            f"Knockout lobby is live on {circuit}. Field size: {field_sz} drivers.\n"
            f"Call the pressure before elimination starts."
        )

    if etype == "ncm_knockout_countdown":
        secs = int(data.get("seconds") or 3)
        circuit = data.get("track_name") or "the knockout circuit"
        return (
            f"Knockout countdown on {circuit}: {secs} seconds.\n"
            f"Call the launch like one bad move means extinction."
        )

    if etype == "ncm_knockout_start":
        circuit = data.get("track_name") or "the knockout circuit"
        remaining = int(data.get("remaining") or data.get("field_size") or 0)
        return (
            f"Knockout race started on {circuit}. {remaining} drivers still alive.\n"
            f"Call the opening burst and the immediate danger."
        )

    if etype == "ncm_driver_eliminated":
        name = data.get("name") or "a driver"
        remaining = int(data.get("remaining") or 0)
        if data.get("is_player") or data.get("isPlayer"):
            return (
                f"V just got eliminated. {remaining} drivers remain.\n"
                f"Call the collapse in one sharp beat."
            )
        return (
            f"{name} just got dropped from the knockout field. {remaining} drivers remain.\n"
            f"Call the cut and what it does to the pack."
        )

    if etype == "ncm_player_danger_zone":
        pos = int(data.get("position") or 0)
        total = int(data.get("total") or 0)
        time_to_elim = float(data.get("time_to_elim") or 0)
        return (
            f"V is in the danger zone: P{pos} of {total}, {time_to_elim:.1f}s to the next elimination.\n"
            f"Call the threat closing in right now."
        )

    if etype == "ncm_lead_change":
        leader = data.get("new_leader") or "someone else"
        return (
            f"Lead change. {leader} is up front now.\n"
            f"Call the swing in momentum."
        )

    if etype == "ncm_catch_up_bonus":
        amount = int(data.get("amount") or 0)
        streak = int(data.get("streak") or 0)
        return (
            f"Catch-up bonus landed: {amount} rep, streak {streak}.\n"
            f"Call how the underground rewards survival."
        )

    if etype == "ncm_wager_lost":
        amount = int(data.get("amount") or 0)
        return (
            f"V lost the wager. {amount} eddies gone.\n"
            f"Call the financial sting in Night City terms."
        )

    if etype == "ncm_new_record":
        route_key = data.get("route_key") or data.get("routeKey") or "the route"
        t = float(data.get("time") or 0)
        driver = data.get("driver_name") or data.get("driverName") or "V"
        return (
            f"New record on {route_key}: {driver} posted {t:.2f}s.\n"
            f"Call why the streets will remember that time."
        )

    if etype == "ncm_overtake":
        old_pos = _race_int(data, "old_position")
        pos = _race_int(data, "position")
        opponent = data.get("opponent") or data.get("behind_name") or "the car behind"
        context = _race_context(data)
        return (
            f"Overtake: V moved from P{old_pos} to P{pos}, putting {opponent} behind. {context}.\n"
            f"Call the pass in 1-2 short, urgent sentences. Wit is welcome; do not invent causes."
        )

    if etype == "ncm_position_lost":
        old_pos = _race_int(data, "old_position")
        pos = _race_int(data, "position")
        opponent = data.get("opponent") or data.get("ahead_name") or "another driver"
        context = _race_context(data)
        return (
            f"Position lost: V slipped from P{old_pos} to P{pos}; {opponent} is ahead now. {context}.\n"
            f"Call the loss fast and sharp. No invented crash unless the prompt says crash."
        )

    if etype in ("ncm_gap_change", "ncm_rival_pressure"):
        relation = data.get("relation") or "ahead"
        opponent = data.get("opponent") or data.get("ahead_name") or data.get("behind_name") or "the rival"
        old_gap = _fmt_gap(data.get("old_gap_m"))
        gap = _fmt_gap(data.get("gap_m"))
        delta = _fmt_gap(data.get("gap_delta_m"))
        direction = data.get("direction") or "moving"
        context = _race_context(data)
        return (
            f"1v1 telemetry: {opponent} {relation}. Gap moved from {old_gap} to {gap}; delta {delta}, direction {direction}. {context}.\n"
            f"Call the pressure in 1-2 short sentences."
        )

    if etype == "ncm_speed_spike":
        speed = _fmt_kph(data.get("speed_kph"))
        old_speed = _fmt_kph(data.get("old_speed_kph"))
        gear = data.get("gear")
        rpm = _race_float(data, "rpm")
        throttle = _race_float(data, "throttle")
        context = _race_context(data)
        return (
            f"Speed spike: V jumped from {old_speed} to {speed}. Gear: {gear}. RPM: {rpm:.2f}. Throttle: {throttle:.2f}. {context}.\n"
            f"Call the pace like the street just got shorter."
        )

    if etype == "ncm_incident":
        kind = data.get("kind") or "incident"
        drop = _race_float(data, "speed_drop_kph")
        health_drop = _race_float(data, "health_drop_pct")
        health = data.get("vehicle_health")
        context = _race_context(data)
        return (
            f"Race incident: {kind}. Speed drop: {drop:.0f} kph. Vehicle health drop: {health_drop:.1f}%. Current health: {health}. {context}.\n"
            f"Call the hit or slowdown urgently, but do not invent who caused it."
        )

    if etype in ("ncm_impact_spike", "ncm_crash_spike"):
        delta_v = _race_float(data, "delta_v") or _race_float(data, "deltaV")
        speed = _fmt_kph(data.get("speed_kph") or data.get("speed"))
        label = data.get("label") or ("crash" if etype == "ncm_crash_spike" else "impact")
        return (
            f"{label.capitalize()} spike: delta-v {delta_v:.1f} m/s at {speed}.\n"
            f"Call the contact in one or two fast sentences. No invented culprit."
        )

    if etype == "ncm_hard_brake":
        g = _race_float(data, "g")
        speed = _fmt_kph(data.get("speed_kph") or data.get("speed"))
        return (
            f"Hard brake: {g:.2f}g at {speed}.\n"
            f"Call the stop like V just asked physics for mercy."
        )

    if etype == "ncm_aggressive_accel":
        g = _race_float(data, "g")
        speed = _fmt_kph(data.get("speed_kph") or data.get("speed"))
        return (
            f"Aggressive acceleration: {g:.2f}g at {speed}.\n"
            f"Call the launch, short and hungry."
        )

    if etype == "ncm_high_speed_run":
        speed = _fmt_kph(data.get("speed_kph") or data.get("speed"))
        return (
            f"High-speed run: V is carrying {speed}.\n"
            f"Call the pace with one clean jab."
        )

    if etype == "ncm_sustained_corner":
        lat_g = _race_float(data, "lat_g") or _race_float(data, "latG")
        speed = _fmt_kph(data.get("speed_kph") or data.get("speed"))
        return (
            f"Sustained cornering: lateral load {lat_g:.2f}g at {speed}.\n"
            f"Call the corner without inventing a drift or crash."
        )

    if etype == "ncm_safety_incident":
        label = data.get("label") or "safety incident"
        points = _race_int(data, "points")
        total = _race_int(data, "total_incident_points") or _race_int(data, "totalIncidentPoints")
        return (
            f"Safety incident recorded: {label}, {points} points, {total} total incident points.\n"
            f"Call the penalty like the streets keep receipts."
        )

    if etype == "ncm_sector_change":
        sector = _race_int(data, "sector")
        lap = _race_int(data, "lap")
        context = _race_context(data)
        lap_line = f" Lap {lap}." if lap else ""
        return (
            f"Sector update: V moved into sector {sector}.{lap_line} Progress {data.get('progress', 0)}. {context}.\n"
            f"Call the rhythm shift in one short line."
        )

    return ""


def _build_race_chapter_prompt(data: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Build the user prompt for a post-race Chronicle chapter."""
    pos        = int(data.get("position") or 0)
    t          = float(data.get("player_time") or 0)
    payout     = int(data.get("payout") or 0)
    rep        = int(data.get("rep_gain") or 0)
    is_dnf     = bool(data.get("is_dnf"))
    is_clean   = bool(data.get("is_clean"))
    wager_won  = bool(data.get("wager_won"))
    wager_lost = bool(data.get("wager_lost"))
    wager_amt  = int(data.get("wager_amount") or 0)
    bounty     = bool(data.get("bounty_resolved"))

    season     = data.get("season") or {}
    champ_pos  = season.get("champ_pos")
    champ_pts  = season.get("champ_pts")

    m = int(t // 60); s = t - m * 60
    time_str = (f"{m}:{s:05.2f}" if m else f"{s:.2f}s") if t > 0 else "DNF"

    # Winner from results table
    winner_line = ""
    results = data.get("results") or []
    if results:
        winner = next((r for r in results if r.get("position") == 1), None)
        if winner and not winner.get("isPlayer"):
            faction = winner.get("factionName") or ""
            winner_line = f"\nRace winner: {winner['name']}{f' ({faction})' if faction else ''}."

    stakes_line = ""
    if wager_won:
        stakes_line = f"\nV took the wager — ₽{wager_amt:,} collected."
    elif wager_lost:
        stakes_line = f"\nV lost the wager — ₽{wager_amt:,} gone."
    if bounty:
        stakes_line += " Bounty resolved."

    champ_line = ""
    if champ_pos:
        champ_line = (
            f"\nChampionship: P{champ_pos} with {champ_pts} pts "
            f"— Round {season.get('round')}/{season.get('total_rounds')}."
        )

    result_word = "DNF" if is_dnf else f"P{pos}"
    clean_note  = " Clean race." if is_clean and not is_dnf else ""
    player_name = ctx.get("player_name", "V")
    location    = ctx.get("location", "Night City")

    return (
        f"Night City Chronicles — race entry.\n\n"
        f"{player_name} finished {result_word} — {time_str}.{clean_note}"
        f"{winner_line}{stakes_line}{champ_line}\n"
        f"Rep: +{rep}. Payout: ₽{payout:,}.\n\n"
        f"Write a 3-4 sentence race close grounded in these real results. "
        f"The race happened in {location}. Name the result, the stakes, what it cost or earned V "
        f"in reputation and story terms. End on something unresolved — Night City never closes cleanly."
    )


# ---------------------------------------------------------------------------
# NarrativeBible — the living document of V's story
# ---------------------------------------------------------------------------

class NarrativeBible:
    """
    Reads, maintains, and writes player_bible.json.

    Thread-safe read/write with a simple RLock.
    """

    def __init__(self, bible_path: str, log=None):
        self._path  = bible_path
        self._lock  = threading.RLock()
        self._log   = log or (lambda *a: None)
        self._data: Dict[str, Any] = {}
        self._load()

    # ── I/O ────────────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                self._log("nc_bible", f"loaded bible ({len(self._data.get('chapters', []))} chapters)")
            except Exception as e:
                self._log("nc_bible", f"WARN: could not load bible: {e} — starting fresh")
                self._data = self._empty_bible()
        else:
            self._data = self._empty_bible()

    def _save(self):
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            self._log("nc_bible", f"ERROR: could not save bible: {e}")

    @staticmethod
    def _empty_bible() -> Dict[str, Any]:
        import time as _t
        return {
            "_meta": {
                "version": 2, "schema": "night_city_chronicles_bible",
                "created": _t.time(), "last_updated": _t.time(),
                "total_sessions": 0, "total_chapters": 0,
            },
            "v": {
                "name": "V", "lifepath": None, "gender_voice": "unknown",
                "level": 1, "street_cred": 0,
                "playstyle_profile": {
                    "primary": "unknown", "violence_index": 0.5,
                    "stealth_preference": 0.0, "netrunner_lean": 0.0,
                    "corpo_affinity": 0.0, "street_kid_affinity": 0.0,
                    "nomad_affinity": 0.0, "last_evaluated_at_event": 0,
                },
                "attributes": {"body":1,"reflexes":1,"technical_ability":1,"intelligence":1,"cool":1},
                "current_location": "Night City", "current_district": "unknown",
                "active_quest": None, "active_objective": None,
                "session_death_count": 0, "total_death_count": 0,
            },
            "narrative_arc": {
                "current_act": "prologue",
                "open_threads": [],
                "closed_threads": [],
                "tracked_relationships": {},
            },
            "chapters": [],
            "raw_events": [],
            "world_state": {
                "districts_visited": [], "factions_engaged": [],
                "quests_completed": [], "quests_in_progress": [],
                "key_npcs_encountered": [], "items_of_significance": [],
            },
            "session_log": [],
        }

    # ── Accessors ───────────────────────────────────────────────────────────

    def get_v(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._data.get("v", {}))

    def get_chapters(self, last_n: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            chapters = self._data.get("chapters", [])
            return deepcopy(chapters[-last_n:])

    def get_open_threads(self) -> List[str]:
        with self._lock:
            return list(self._data.get("narrative_arc", {}).get("open_threads", []))

    def get_world_state(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._data.get("world_state", {}))

    def total_chapters(self) -> int:
        with self._lock:
            return len(self._data.get("chapters", []))

    # ── Mutators ────────────────────────────────────────────────────────────

    def update_v_from_game_state(self, gs: Dict[str, Any]) -> None:
        """Merge live game state into the V profile."""
        with self._lock:
            v = self._data.setdefault("v", {})

            # Direct fields
            if gs.get("player_name"):
                v["name"] = gs["player_name"]
            if gs.get("level"):
                v["level"] = int(gs["level"])
            if gs.get("street_cred") is not None:
                v["street_cred"] = int(gs["street_cred"])
            if gs.get("location"):
                v["current_location"] = gs["location"]
            if gs.get("district"):
                v["current_district"] = gs["district"]
            if gs.get("active_quest") or gs.get("quest"):
                v["active_quest"] = gs.get("active_quest") or gs.get("quest")
            if gs.get("objective") or gs.get("active_objective"):
                v["active_objective"] = gs.get("objective") or gs.get("active_objective")

            # Track district visits
            dist = gs.get("district", "")
            if dist and dist not in self._data.get("world_state", {}).get("districts_visited", []):
                self._data.setdefault("world_state", {}).setdefault("districts_visited", []).append(dist)

            # Track quests in progress
            quest = v.get("active_quest")
            if quest:
                wqs = self._data.setdefault("world_state", {}).setdefault("quests_in_progress", [])
                if quest not in wqs:
                    wqs.append(quest)

            self._data["_meta"]["last_updated"] = time.time()

    def record_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """Append a raw event to the log for chapter synthesis context."""
        with self._lock:
            entry = {
                "type": event_type,
                "ts": time.time(),
                "data": {k: v for k, v in event_data.items() if k not in ("_raw", "_full")},
            }
            self._data.setdefault("raw_events", []).append(entry)
            # Keep only last 100 raw events to prevent unbounded growth
            self._data["raw_events"] = self._data["raw_events"][-100:]

            # Special handling for deaths
            if event_type == "player_death":
                v = self._data.setdefault("v", {})
                v["session_death_count"] = v.get("session_death_count", 0) + 1
                v["total_death_count"]   = v.get("total_death_count", 0) + 1

    def add_chapter(self, chapter_text: str, theme_hints: List[str], game_state: Dict[str, Any]) -> int:
        """Append a new chapter and return its ID."""
        with self._lock:
            chapters = self._data.setdefault("chapters", [])
            chapter_id = len(chapters) + 1
            chapter = {
                "chapter_id": chapter_id,
                "timestamp":  time.time(),
                "narrative":  chapter_text,
                "theme_hints": theme_hints,
                "game_state_snapshot": {
                    "level":    game_state.get("level", 1),
                    "location": game_state.get("location", "Night City"),
                    "district": game_state.get("district", ""),
                    "quest":    game_state.get("quest") or game_state.get("active_quest"),
                },
            }
            chapters.append(chapter)
            self._data["_meta"]["total_chapters"] = chapter_id
            self._data["_meta"]["last_updated"]   = time.time()
            self._save()
            return chapter_id

    def update_playstyle(self, event_type: str) -> None:
        """Nudge the playstyle profile based on observed events."""
        with self._lock:
            profile = self._data.setdefault("v", {}).setdefault("playstyle_profile", {})

            if event_type == "combat_ended":
                profile["violence_index"] = min(1.0, profile.get("violence_index", 0.5) + 0.05)
            elif event_type == "combat_ended_stealthy":
                profile["stealth_preference"] = min(1.0, profile.get("stealth_preference", 0.0) + 0.08)
                profile["violence_index"]      = max(0.0, profile.get("violence_index", 0.5) - 0.03)
            elif event_type == "quickhack_used":
                profile["netrunner_lean"] = min(1.0, profile.get("netrunner_lean", 0.0) + 0.06)

            profile["last_evaluated_at_event"] = profile.get("last_evaluated_at_event", 0) + 1
            # Infer primary
            profile["primary"] = _infer_playstyle(profile)

    def add_thread(self, thread: str) -> None:
        with self._lock:
            threads = self._data.setdefault("narrative_arc", {}).setdefault("open_threads", [])
            if thread not in threads:
                threads.append(thread)
                if len(threads) > 8:
                    threads.pop(0)  # age oldest thread out

    def close_thread(self, thread: str) -> None:
        with self._lock:
            arc = self._data.setdefault("narrative_arc", {})
            threads = arc.setdefault("open_threads", [])
            closed  = arc.setdefault("closed_threads", [])
            if thread in threads:
                threads.remove(thread)
                closed.append({"thread": thread, "closed_at": time.time()})
                if len(closed) > 20:
                    closed.pop(0)

    def start_session(self) -> None:
        with self._lock:
            meta = self._data.setdefault("_meta", {})
            meta["total_sessions"] = meta.get("total_sessions", 0) + 1
            v = self._data.setdefault("v", {})
            v["session_death_count"] = 0
            log = self._data.setdefault("session_log", [])
            log.append({"session": meta["total_sessions"], "started_at": time.time()})
            if len(log) > 50:
                log.pop(0)
            self._save()

    def recent_events_summary(self, n: int = 10) -> str:
        """Return a brief text summary of the last N raw events."""
        with self._lock:
            events = self._data.get("raw_events", [])[-n:]
        lines = []
        for ev in events:
            etype = ev.get("type", "?")
            data  = ev.get("data", {})
            if etype == "quest_updated":
                lines.append(f"Quest updated → {data.get('quest','?')}: {data.get('objective','?')}")
            elif etype == "player_death":
                lines.append(f"V flatlined at {data.get('location','?')}")
            elif etype == "level_up":
                lines.append(f"Level up → {data.get('level','?')} (street cred {data.get('street_cred','?')})")
            elif etype == "location_changed":
                lines.append(f"Moved to {data.get('location','?')} ({data.get('district','?')})")
            elif etype == "combat_started":
                lines.append(f"Combat started at {data.get('location','?')} — {data.get('enemy_count',0)} enemies, weapon: {data.get('weapon_type','unknown')}")
            elif etype == "combat_ended":
                kills  = data.get("kills_this_combat", 0)
                hs     = data.get("headshots_this_combat", 0)
                suffix = f" ({kills} kills, {hs} headshots)" if kills else ""
                lines.append(f"Combat ended at {data.get('location','?')}{suffix}")
            elif etype == "near_death":
                lines.append(f"V nearly died — health at {int(float(data.get('health_pct',0))*100)}%, {data.get('enemy_count',0)} enemies still active")
            elif etype == "health_recovered":
                lines.append(f"V stabilised — back to {int(float(data.get('health_pct',0))*100)}% health")
            elif etype == "vehicle_entered":
                lines.append(f"Got into {data.get('vehicle_name','a vehicle')} ({data.get('vehicle_type','')}) at {data.get('location','?')}")
            elif etype == "vehicle_exited":
                lines.append(f"Left vehicle at {data.get('location','?')}")
            elif etype == "weapon_switched":
                lines.append(f"Switched to {data.get('weapon_name','?')} ({data.get('weapon_type','?')})")
            elif etype == "sandevistan_activated":
                lines.append(f"Sandevistan activated — time dilation in {data.get('location','?')}")
            elif etype == "optical_camo_activated":
                lines.append(f"Optical camo active — V went invisible at {data.get('location','?')}")
            elif etype == "berserk_activated":
                lines.append(f"Berserk cyberware active — V is supercharged at {data.get('location','?')}")
            elif etype == "hack_burst":
                ram_cur = data.get("ram_current", 0)
                ram_max = data.get("ram_max", 1)
                lines.append(f"Netrunner burst — RAM dropped to {ram_cur:.0f}/{ram_max:.0f} at {data.get('location','?')}")
            elif etype == "ram_depleted":
                lines.append(f"RAM depleted — V's quickhack capacity exhausted at {data.get('location','?')}")
            elif etype == "stealth_broken":
                lines.append(f"Stealth broken — V detected by {data.get('enemy_count',0)} enemies in {data.get('location','?')}")
            elif etype == "stealth_takedown":
                lines.append(f"Stealth takedown — silent kill #{data.get('kills_this_combat',0)} in {data.get('location','?')}")
            elif etype == "kill_spree":
                lines.append(f"Kill spree — {data.get('kills_this_combat',0)} kills ({data.get('headshots_this_combat',0)} headshots) with {data.get('weapon_name','?')}")
            elif etype == "eddies_windfall":
                lines.append(f"Windfall — V gained \u20bd{data.get('gained',0):,} eddies (total \u20bd{data.get('eddies',0):,})")
            elif etype == "eddies_splurge":
                lines.append(f"Spent \u20bd{data.get('spent',0):,} eddies at {data.get('location','?')}")
            elif etype == "time_of_day_changed":
                lines.append(f"{data.get('time_name','?').capitalize()} in Night City — {data.get('weather','')}")
            elif etype == "dialogue_started":
                npc = data.get("dialogue_npc") or "someone"
                lines.append(f"V is talking to {npc} ({data.get('active_quest','')})")
            elif etype == "street_cred_up":
                lines.append(f"Street cred up to {data.get('street_cred','?')} (+{data.get('gained','?')})")
            elif etype == "wanted_level_change":
                new_w = data.get("wanted_level", 0)
                old_w = data.get("prev_wanted", 0)
                tag   = "NCPD heat rising" if new_w > old_w else "heat dropped"
                lines.append(f"Wanted level {old_w}→{new_w} ({tag}) at {data.get('location','?')}")
            elif etype == "item_acquired":
                lines.append(f"Acquired: {data.get('item','?')} in {data.get('location','?')}")
            elif etype == "ncm_race_preview":
                district = data.get("district") or "unknown district"
                dist_km  = (data.get("distance") or 0) / 1000.0
                field_sz = data.get("field_size") or 0
                lines.append(f"Race forming in {district} — {field_sz} drivers, {dist_km:.1f} km")
            elif etype == "ncm_grid_ready":
                lines.append(f"Grid ready — {data.get('field_size', 0)} cars lined up")
            elif etype == "ncm_quali_start":
                lines.append(f"Qualifying started in {data.get('district', 'unknown')}")
            elif etype == "ncm_quali_end":
                t   = float(data.get("player_time") or 0)
                pos = int(data.get("grid_pos") or 1)
                m   = int(t // 60); s = t - m * 60
                time_str = (f"{m}:{s:05.2f}" if m else f"{s:.2f}s") if t > 0 else "no time"
                lines.append(f"Qualifying done: V set {time_str}, starts P{pos}")
            elif etype == "ncm_race_countdown":
                lines.append(f"Race countdown: {data.get('seconds', 3)}s to launch")
            elif etype == "ncm_race_start":
                district = data.get("district") or "Night City"
                race_type = data.get("race_type") or "sprint"
                lines.append(f"Race started — {race_type} through {district}")
            elif etype == "ncm_race_finish":
                pos    = int(data.get("position") or 0)
                is_dnf = bool(data.get("is_dnf"))
                payout = int(data.get("payout") or 0)
                rep    = int(data.get("rep_gain") or 0)
                result = "DNF" if is_dnf else f"P{pos}"
                lines.append(f"Race finished: V {result} — ₽{payout:,} payout, +{rep} rep")
            elif etype == "ncm_prestige_change":
                old_t = data.get("old_tier") or "?"
                new_t = data.get("new_tier") or "?"
                lines.append(f"Racing prestige changed: {old_t} -> {new_t}")
            elif etype == "ncm_season_complete":
                season_no = int(data.get("season") or 0)
                sname = data.get("season_name") or (f"Season {season_no}" if season_no else "season")
                rep_bonus = int(data.get("rep_bonus") or data.get("repBonus") or 0)
                eddies = int(data.get("eddies") or 0)
                lines.append(f"Season complete: {sname} - +{rep_bonus} rep, {eddies:,} eddies")
            elif etype == "ncm_championship_complete":
                pos   = int(data.get("final_position") or data.get("champ_pos") or 0)
                champ = bool(data.get("champion")) or (pos == 1 and pos > 0)
                lines.append(f"Championship complete: V P{pos}{'  — CHAMPION' if champ else ''}")
            elif etype == "ncm_round_ready":
                lines.append(
                    f"Round ready: {data.get('round', '?')}/{data.get('total', data.get('total_rounds', '?'))} "
                    f"in {data.get('district', '?')} ({data.get('event_type', 'race')})"
                )
            elif etype == "ncm_knockout_lobby":
                lines.append(
                    f"Knockout lobby: {data.get('circuit_name', data.get('track_name', '?'))} "
                    f"with {data.get('field_size', 0)} drivers"
                )
            elif etype == "ncm_knockout_countdown":
                lines.append(f"Knockout countdown: {data.get('seconds', 3)}s")
            elif etype == "ncm_knockout_start":
                lines.append(
                    f"Knockout started on {data.get('track_name', '?')} "
                    f"with {data.get('field_size', data.get('remaining', 0))} drivers"
                )
            elif etype == "ncm_driver_eliminated":
                name = data.get("name") or "a driver"
                if data.get("is_player") or data.get("isPlayer"):
                    lines.append("Knockout elimination: V got cut from the field")
                else:
                    lines.append(f"Knockout elimination: {name} dropped - {data.get('remaining', 0)} remain")
            elif etype == "ncm_player_danger_zone":
                lines.append(
                    f"Danger zone: V P{data.get('position', '?')} of {data.get('total', '?')} "
                    f"with {float(data.get('time_to_elim', 0) or 0):.1f}s to survive"
                )
            elif etype == "ncm_lead_change":
                lines.append(f"Lead change: {data.get('new_leader', 'someone')} to the front")
            elif etype == "ncm_knockout_finish":
                pos = int(data.get("player_position") or data.get("position") or 0)
                payout = int(data.get("payout") or 0)
                rep = int(data.get("rep_gain") or 0)
                lines.append(f"Knockout finished: V P{pos} - {payout:,} eddies, +{rep} rep")
            elif etype == "ncm_catch_up_bonus":
                lines.append(f"Catch-up bonus: +{data.get('amount', 0)} (streak {data.get('streak', 0)})")
            elif etype == "ncm_wager_lost":
                lines.append(f"Wager lost: {int(data.get('amount', 0) or 0):,} eddies")
            elif etype == "ncm_new_record":
                lines.append(
                    f"New record: {data.get('driver_name', data.get('driverName', 'V'))} "
                    f"did {float(data.get('time', 0) or 0):.2f}s on {data.get('route_key', data.get('routeKey', '?'))}"
                )
            else:
                lines.append(f"{etype}: {json.dumps(data, default=str)[:60]}")
        return "\n".join(lines) if lines else "No recorded events yet this session."


# ---------------------------------------------------------------------------
# Main meta plugin
# ---------------------------------------------------------------------------

class NightCityChroniclesMeta(MetaPluginBase):
    """
    Night City Chronicles meta plugin.

    Maintains the narrative bible and generates literary chapters
    as the player's Cyberpunk 2077 session unfolds.
    """

    def __init__(self):
        self._ctx:  Dict[str, Any] = {}
        self._cfg:  Dict[str, Any] = {}
        self._mem:  Dict[str, Any] = {}
        self._log = print

        self._bible: Optional[NarrativeBible] = None
        self._model:       str = "qwen3:8b"
        self._fast_model:  str = "qwen3:8b"

        # Chronicle config (from manifest)
        self._context_window:     int   = 3
        self._min_chapter_gap:    float = 45.0
        self._min_beat_gap:       float = 20.0
        self._session_recap_delay:float = 8.0
        self._chapter_max_tokens: int   = 520
        self._beat_max_tokens:    int   = 120
        self._recap_max_tokens:   int   = 300
        self._secondary_chance:   float = 0.45
        self._weave_lore:         bool  = True
        self._low_signal_idle_gap: float = 120.0
        self._secondary_topic_cooldown: float = 300.0
        self._secondary_memory_window: float = 900.0
        self._motif_window_sec: float = 900.0
        self._min_race_beat_gap: float = 8.0
        self._race_urgent_gap: float = 3.0
        self._race_event_ttl_sec: float = 8.0
        self._race_finish_ttl_sec: float = 35.0
        self._race_flush_audio_queue: bool = True

        # State
        self._last_chapter_ts: float = 0.0
        self._last_beat_ts: float = 0.0
        self._last_race_beat_ts: float = 0.0
        self._pending_chapter_events: List[Dict[str, Any]] = []
        self._chapter_pending_lock = threading.Lock()
        self._session_started: bool = False
        self._broadcast_state: BroadcastStateMode = BroadcastStateMode.IDLE
        self._current_heat_level: int = 0
        self._current_signal_score: float = 0.0

        # Voice name → voice key (from manifest voices)
        self._voice_keys: Dict[str, str] = {
            "chronicler": "host",
            "fixer":      "fixer",
            "netrunner":  "netrunner",
            "corpo":      "corpo",
            "ripperdoc":  "ripperdoc",
        }

        # Threading
        self._stop_evt   = threading.Event()
        self._work_evt   = threading.Event()
        self._worker_thr: Optional[threading.Thread] = None

        # Output queue for generated segments
        self._output: List[Dict[str, Any]] = []
        self._output_lock = threading.Lock()

        # Set when cold_open() successfully returns LLM content so the
        # session-recap timer doesn't also fire an intro beat.
        self._cold_open_done: bool = False

        # Guard: only one session recap per boot (game_started + session_recap
        # both call _generate_session_recap; this flag prevents double-fire)
        self._recap_done: bool = False

        # Track last time any segment was emitted, for idle beat throttling
        self._last_output_ts: float = 0.0
        self._idle_beat_interval: float = 120.0  # overridden by chronicle.idle_beat_interval_sec
        self._last_low_signal_idle_ts: float = 0.0

        # Deduplication: rolling buffer of (timestamp, normalised_body) for
        # near-duplicate suppression within a short recency window
        self._recent_bodies: List[Tuple[float, str]] = []
        self._dedup_window_sec: float = 300.0    # suppress near-dupes within 5 minutes
        self._dedup_similarity_threshold: float = 0.62  # SequenceMatcher ratio above which we skip
        self._recent_motif_hits: List[Tuple[float, str]] = []
        self._prosody_output_index: int = 0
        self._voice_output_history: Dict[str, List[Dict[str, Any]]] = {
            "host": [],
            "fixer": [],
            "netrunner": [],
            "ripperdoc": [],
            "corpo": [],
        }
        self._prosody_logs: List[Dict[str, Any]] = []
        self._exclamation_cooldown_outputs: int = 2
        self._exclamation_window_outputs: int = 10
        self._max_exclamatory_in_window: int = 3
        self._prosody_similarity_threshold: float = 0.85
        self._min_event_delta_for_energy: float = 1.25
        self._prosody_log_limit: int = 100
        self._secondary_history: Dict[str, List[Dict[str, Any]]] = {
            "fixer": [],
            "netrunner": [],
            "ripperdoc": [],
            "corpo": [],
        }

        # Race mode — set while a race is active; suspends normal chapter gen
        self._race_mode: bool = False
        self._race_result: Dict[str, Any] = {}

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def initialize(self, runtime_context: Dict[str, Any],
                   cfg: Dict[str, Any], mem: Dict[str, Any]) -> None:
        self._ctx = runtime_context
        self._cfg = cfg
        self._mem = mem
        self._log = runtime_context.get("log", print)

        models = cfg.get("models") or {}
        self._model      = str(models.get("host",    "qwen3:8b"))
        self._fast_model = str(models.get("fast",    self._model))

        chron = cfg.get("chronicle") or {}
        self._context_window       = int(chron.get("context_window_chapters",  3))
        self._min_chapter_gap      = float(chron.get("min_chapter_gap_sec",     45.0))
        self._min_beat_gap         = float(chron.get("min_beat_gap_sec",        20.0))
        self._session_recap_delay  = float(chron.get("session_recap_delay_sec",  8.0))
        self._chapter_max_tokens   = int(chron.get("chapter_max_tokens",       520))
        self._beat_max_tokens      = int(chron.get("beat_max_tokens",          120))
        self._recap_max_tokens     = int(chron.get("recap_max_tokens",         300))
        self._secondary_chance     = float(chron.get("secondary_voice_chance",   0.45))
        self._weave_lore           = bool(chron.get("weave_world_lore",         True))
        self._idle_beat_interval   = float(chron.get("idle_beat_interval_sec",  120.0))
        self._low_signal_idle_gap  = float(chron.get("low_signal_idle_gap_sec", 120.0))
        self._secondary_topic_cooldown = float(chron.get("secondary_topic_cooldown_sec", 300.0))
        self._secondary_memory_window  = float(chron.get("secondary_memory_window_sec", 900.0))
        self._motif_window_sec    = float(chron.get("motif_window_sec", 900.0))
        self._min_race_beat_gap   = float(chron.get("race_beat_gap_sec", 8.0))
        self._race_urgent_gap     = float(chron.get("race_urgent_gap_sec", 3.0))
        self._race_event_ttl_sec  = float(chron.get("race_event_ttl_sec", 8.0))
        self._race_finish_ttl_sec = float(chron.get("race_finish_ttl_sec", 35.0))
        self._race_flush_audio_queue = bool(chron.get("race_flush_audio_queue", True))
        prosody = cfg.get("prosody") or {}
        self._exclamation_cooldown_outputs = int(prosody.get("exclamation_cooldown_outputs", 2))
        self._exclamation_window_outputs = int(prosody.get("exclamation_window_outputs", 10))
        self._max_exclamatory_in_window = int(prosody.get("max_exclamatory_in_window", 3))
        self._prosody_similarity_threshold = float(prosody.get("similarity_threshold", 0.85))
        self._min_event_delta_for_energy = float(prosody.get("min_event_delta_for_energy", 1.25))
        self._prosody_log_limit = int(prosody.get("log_limit", 100))

        # Locate the bible — check in order: env var > cfg > STATION_DIR env > cwd
        _station_dir = (
            os.environ.get("STATION_DIR")
            or runtime_context.get("station_dir", "")
            or os.getcwd()
        )
        bible_path = (
            os.environ.get("NC_BIBLE_PATH")
            or cfg.get("feeds", {}).get("nc_story_feed", {}).get("bible_path", "")
            or os.path.join(_station_dir, "player_bible.json")
        )
        self._bible = NarrativeBible(bible_path, log=self._log)
        self._bible.start_session()

        self._log("nc_chronicles", f"Night City Chronicles online — bible at {bible_path}")
        self._log("nc_chronicles", f"Chapter count: {self._bible.total_chapters()}")

        # Start background worker
        self._stop_evt.clear()
        self._worker_thr = threading.Thread(
            target=self._worker_loop, name="nc_chronicles_worker", daemon=True
        )
        self._worker_thr.start()

        # Schedule session recap after startup delay
        threading.Timer(self._session_recap_delay, self._schedule_session_recap).start()

    def shutdown(self) -> None:
        self._stop_evt.set()
        self._work_evt.set()
        if self._worker_thr:
            self._worker_thr.join(timeout=4.0)
        self._log("nc_chronicles", "Night City Chronicles — signing off.")

    # =========================================================================
    # MetaPluginBase hooks
    # =========================================================================

    def curate_candidates(self, candidates: List[Dict], state: Any) -> List[Dict]:
        """Pass through our own narration candidates for the producer to enqueue."""
        return [c for c in candidates if c.get("source") == "nc_chronicles"]

    def generate_script(self, segment: Dict, state: Any) -> Dict:
        """Turn a narration segment into a host packet for TTS rendering."""
        body = (segment.get("body") or segment.get("text") or "").strip()
        if body:
            return {
                "host_intro": body,
                "panel": [],
                "host_takeaway": "",
            }
        return segment

    def generate_narration(self, events: List[Dict], context: Any) -> str:
        return ""

    def delegate_decision(self, available_actions: List, state: Any,
                           identity: Any, focus: Any) -> Optional[Any]:
        return None

    def process_input(self, input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Called by nc_story_feed (and ncm_race_feed) on each tick.
        Returns any ready narration segments and clears the output buffer.
        """
        broadcast_state = str(input_data.get("broadcast_state") or "").strip().upper()
        if broadcast_state:
            self._sync_broadcast_state(broadcast_state)

        heat_level_raw = input_data.get("heat_level")
        if heat_level_raw is not None:
            try:
                self._current_heat_level = max(0, min(5, int(heat_level_raw)))
            except (TypeError, ValueError):
                pass

        signal_score_raw = input_data.get("signal_score")
        if signal_score_raw is not None:
            try:
                self._current_signal_score = max(0.0, float(signal_score_raw))
            except (TypeError, ValueError):
                pass

        # Authoritative race-state sync from NCM feed ("idle", "racing", etc.)
        race_state = input_data.get("race_state")
        if race_state:
            self._sync_race_state(race_state)

        # Ingest game state if provided
        game_state = input_data.get("game_state") or {}
        if game_state and self._bible:
            self._bible.update_v_from_game_state(game_state)

        # Ingest events
        new_events: List[Dict] = input_data.get("events") or []
        now = time.time()
        if not new_events and self._broadcast_state == BroadcastStateMode.IDLE and heat_level_raw is None:
            self._current_heat_level = 0
        if not new_events and self._broadcast_state == BroadcastStateMode.IDLE and signal_score_raw is None:
            self._current_signal_score = 0.0
        for ev in new_events:
            annotated = dict(ev)
            annotated.setdefault("_queued_at", now)
            annotated.setdefault("_heat_level", self._current_heat_level)
            annotated.setdefault("_signal_score", self._current_signal_score)
            annotated.setdefault("_broadcast_state", self._broadcast_state.value)
            if str(annotated.get("type") or "").startswith("ncm_"):
                annotated.setdefault("_race_observed_ts", _race_observed_ts(annotated, now))
            self._ingest_event(annotated)

        idle_reason = str(input_data.get("idle_reason") or "")
        pending_event_summary = str(input_data.get("pending_event_summary") or "")

        # Low-signal idle — the feed hit a forced checkpoint but the evidence
        # never turned into a real beat or chapter. Admit the quiet instead of
        # pretending the city moved.
        if not new_events and idle_reason == "low_signal" and self._cold_open_done:
            now = time.time()
            if (now - self._last_low_signal_idle_ts) >= self._low_signal_idle_gap:
                self._last_low_signal_idle_ts = now
                self._last_output_ts = now
                threading.Thread(
                    target=self._gen_idle_beat,
                    kwargs={
                        "idle_reason": idle_reason,
                        "pending_summary": pending_event_summary,
                    },
                    daemon=True,
                ).start()

        # Idle beat — no events, enough time has passed, generate atmospheric narration
        if not new_events and self._cold_open_done:
            now = time.time()
            if not idle_reason and (now - self._last_output_ts) >= self._idle_beat_interval:
                self._last_output_ts = now  # prevent re-entry while LLM runs
                threading.Thread(target=self._gen_idle_beat, daemon=True).start()

        # Return any segments that have been queued by the worker
        with self._output_lock:
            ready = list(self._output)
            self._output.clear()

        if ready:
            self._last_output_ts = time.time()

        return ready

    def supports_streaming(self) -> bool:
        return False

    def cold_open(self) -> Optional[str]:
        """
        LLM-generated station opening returned to bookmark.py for immediate enqueue.
        When the bible is empty this is the first transmission. Otherwise it's a recap.
        """
        if not self._bible or not self._ctx:
            return None
        ctx = self._build_context()
        if not ctx:
            return None

        chapters = self._bible.get_chapters(last_n=3)
        system_prompt = _SYSTEM_CHRONICLER.format(**ctx)

        if not chapters:
            user_prompt = (
                f"Night City Chronicles — first transmission.\n\n"
                f"V ({ctx['player_name']}) is just beginning their story. "
                f"Lifepath: {ctx['lifepath']}. Currently in {ctx['location']}.\n\n"
                f"Open the Chronicles for the very first time. 3-4 sentences that establish "
                f"the weight of what Night City holds without knowing V's specifics yet. "
                f"No events have happened. Set the tone. Make the listener lean in."
            )
        else:
            threads_text = (
                "\n".join(f"- {t}" for t in self._bible.get_open_threads())
                or "None yet."
            )
            chapter_summaries = "\n".join(
                f"[Chapter {c['chapter_id']}]\n{c['narrative']}" for c in chapters
            )
            user_prompt = _RECAP_PROMPT.format(
                n_chapters=len(chapters),
                chapter_summaries=chapter_summaries,
                open_threads=threads_text,
                player_name=ctx["player_name"],
                level=ctx["level"],
                location=ctx["location"],
            )

        text = self._llm(system_prompt, user_prompt, self._model, self._recap_max_tokens)
        if text and text.strip():
            self._cold_open_done = True
            return text.strip()
        return None

    # =========================================================================
    # Internal — event ingestion
    # =========================================================================

    def _sync_race_state(self, race_state: str) -> None:
        """Authoritative race mode sync from the NCM feed's Racing.getState() poll."""
        normalized = str(race_state or "").strip().lower()
        active = normalized in _RACE_ACTIVE_STATES and normalized not in _RACE_TERMINAL_STATES
        if active != self._race_mode:
            self._race_mode = active
            self._log("nc_chronicles", f"Race mode {'entered' if active else 'exited'} (state={normalized})")
            if not active:
                self._flush_pending_race_events(f"race state {normalized or 'idle'}")
                self._flush_queued_race_outputs(f"race state {normalized or 'idle'}", include_audio=True)
        if active:
            self._broadcast_state = BroadcastStateMode.ACTIVE

    def _sync_broadcast_state(self, mode: str) -> None:
        """Accept feed-side state classification and treat it as a real mode switch."""
        try:
            new_mode = BroadcastStateMode[mode]
        except KeyError:
            return

        if self._race_mode and new_mode != BroadcastStateMode.ACTIVE:
            new_mode = BroadcastStateMode.ACTIVE

        if new_mode != self._broadcast_state:
            self._broadcast_state = new_mode
            self._log("nc_chronicles", f"Broadcast state -> {new_mode.value}")

    def _flush_pending_race_events(self, reason: str, keep_results: bool = True) -> int:
        """Drop queued mid-race beats that are already too old to call live."""
        with self._chapter_pending_lock:
            before = len(self._pending_chapter_events)
            self._pending_chapter_events = [
                event for event in self._pending_chapter_events
                if not str(event.get("type") or "").startswith("ncm_")
                or (keep_results and str(event.get("type") or "") in _RACE_END_EVENTS)
            ]
            removed = before - len(self._pending_chapter_events)

        if removed:
            self._log("nc_chronicles", f"flushed {removed} stale pending race event(s): {reason}")
        return removed

    def _flush_queued_race_outputs(self, reason: str, include_audio: bool = False) -> int:
        """Mark old unplayed race beat segments done so only the newest call survives."""
        removed = 0
        db_connect_fn = self._ctx.get("db_connect")
        if db_connect_fn:
            conn = None
            try:
                conn = db_connect_fn()
                cur = conn.execute(
                    """
                    UPDATE segments
                    SET status='done'
                    WHERE status='queued'
                      AND COALESCE(source,'')=?
                      AND COALESCE(title,'')=?;
                    """,
                    (_RACE_COMMENTARY_SOURCE, _RACE_BEAT_TITLE),
                )
                removed += int(getattr(cur, "rowcount", 0) or 0)
                conn.commit()
            except Exception as exc:
                self._log("nc_chronicles", f"race output flush failed: {exc}")
            finally:
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass

        if include_audio and self._race_flush_audio_queue:
            removed += self._drop_buffered_race_audio()

        if removed:
            self._log("nc_chronicles", f"flushed {removed} queued race beat(s): {reason}")
        return removed

    def _drop_buffered_race_audio(self) -> int:
        audio_q = self._ctx.get("audio_queue")
        if not audio_q or not hasattr(audio_q, "mutex") or not hasattr(audio_q, "queue"):
            return 0

        def is_race_beat(item: Any) -> bool:
            seg = getattr(item, "seg", None)
            if not isinstance(seg, dict) and isinstance(item, dict):
                seg = item.get("seg") or item
            if not isinstance(seg, dict):
                return False
            return (
                (seg.get("source") or "") == _RACE_COMMENTARY_SOURCE
                and (seg.get("title") or "") == _RACE_BEAT_TITLE
            )

        try:
            with audio_q.mutex:
                kept = [item for item in list(audio_q.queue) if not is_race_beat(item)]
                removed = len(audio_q.queue) - len(kept)
                if removed:
                    audio_q.queue.clear()
                    audio_q.queue.extend(kept)
                    audio_q.not_full.notify_all()
                return removed
        except Exception as exc:
            self._log("nc_chronicles", f"race audio buffer flush failed: {exc}")
            return 0

    def _select_fresh_race_events(
        self,
        events: List[Dict[str, Any]],
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Keep one current race event and discard the backlog."""
        now = time.time() if now is None else now
        race_events = [event for event in events if str(event.get("type") or "").startswith("ncm_")]
        if not race_events:
            return []

        final_events: List[Dict[str, Any]] = []
        live_events: List[Dict[str, Any]] = []
        stale_count = 0
        for event in race_events:
            etype = str(event.get("type") or "")
            observed = _race_observed_ts(event, now)
            event["_race_observed_ts"] = observed
            ttl = self._race_finish_ttl_sec if etype in _RACE_END_EVENTS else self._race_event_ttl_sec
            if now - observed > ttl:
                stale_count += 1
                continue
            if etype in _RACE_END_EVENTS:
                final_events.append(event)
            else:
                live_events.append(event)

        if stale_count:
            self._log("nc_chronicles", f"dropped {stale_count} stale race event(s) before narration")

        if final_events:
            final_events.sort(key=lambda ev: (_race_event_priority(ev), _race_observed_ts(ev, now)), reverse=True)
            return final_events[:1]

        if not live_events:
            return []

        live_events.sort(key=lambda ev: (_race_event_priority(ev), _race_observed_ts(ev, now)), reverse=True)
        chosen = live_events[0]
        priority = _race_event_priority(chosen)
        required_gap = self._race_urgent_gap if priority >= 84 else self._min_race_beat_gap
        elapsed = now - self._last_race_beat_ts
        if self._last_race_beat_ts > 0 and elapsed < required_gap:
            self._log(
                "nc_chronicles",
                f"race cadence dropped {len(live_events)} fresh event(s); next beat in {required_gap - elapsed:.1f}s",
            )
            return []

        return [chosen]

    def _ingest_event(self, event: Dict[str, Any]) -> None:
        etype     = event.get("type", "unknown")
        tier      = _NARRATIVE_TIER.get(etype, NarrativeTier.IGNORE)

        if tier == NarrativeTier.IGNORE:
            return

        # Track race mode via events as a secondary signal.
        if etype in _RACE_END_EVENTS:
            self._flush_pending_race_events(str(etype))
            self._flush_queued_race_outputs(str(etype), include_audio=True)
            self._race_mode = False
            if etype in ("ncm_race_finish", "ncm_knockout_finish"):
                self._race_result = event.get("data", {})
        elif etype.startswith("ncm_") and etype not in ("ncm_prestige_change", "ncm_season_complete", "ncm_championship_complete"):
            self._race_mode = True

        if self._bible:
            self._bible.record_event(etype, event.get("data", event))
            self._bible.update_playstyle(etype)

        if tier in (NarrativeTier.CHAPTER, NarrativeTier.BEAT):
            with self._chapter_pending_lock:
                self._pending_chapter_events.append(event)
            self._work_evt.set()

    # =========================================================================
    # Internal — session recap
    # =========================================================================

    def _schedule_session_recap(self) -> None:
        if self._stop_evt.is_set():
            return
        if self._bible and self._bible.total_chapters() == 0:
            # Brand new game — skip recap, do an intro beat instead
            self._queue_intro_beat()
            return
        # Wake the worker with a special recap request
        with self._chapter_pending_lock:
            self._pending_chapter_events.insert(0, {"type": "session_recap", "data": {}})
        self._work_evt.set()

    def _queue_intro_beat(self) -> None:
        """Queue an LLM-generated intro for a brand-new chronicles session."""
        if self._cold_open_done:
            return  # cold_open() already handled the opening
        threading.Thread(target=self._gen_intro_beat, daemon=True).start()

    def _gen_intro_beat(self) -> None:
        ctx = self._build_context()
        if not ctx:
            return
        system_prompt = _SYSTEM_CHRONICLER.format(**ctx)
        user_prompt = (
            f"Night City Chronicles — first transmission.\n\n"
            f"V ({ctx['player_name']}) is just beginning their story. "
            f"Lifepath: {ctx['lifepath']}. Currently in {ctx['location']}.\n\n"
            f"Open the Chronicles for the very first time. 3-4 sentences that establish "
            f"the weight of what Night City holds without knowing V's specifics yet. "
            f"No events have happened. Set the tone."
        )
        text = self._llm(system_prompt, user_prompt, self._model, self._recap_max_tokens)
        if text and text.strip():
            self._push_segment(text.strip(), voice="host", priority=72, heat_level=1, signal_score=0.5)

    def _gen_idle_beat(self, idle_reason: str = "", pending_summary: str = "") -> None:
        """Generate an atmospheric beat when the station has been silent too long."""
        ctx = self._build_context()
        if not ctx:
            return
        chapters = self._bible.get_chapters(last_n=2) if self._bible else []
        system_prompt = _SYSTEM_CHRONICLER.format(**ctx)
        if idle_reason == "low_signal":
            length_rule = "1 short sentence max." if self._broadcast_state == BroadcastStateMode.IDLE else "1-2 sentences."
            user_prompt = (
                f"The telemetry feed stayed thin. These scraps came through, but none of them add up to a fresh chapter:\n"
                f"{pending_summary or 'Just ambient movement and weak signal.'}\n\n"
                f"Give {length_rule} that admit the city is between moves. No fake breakthrough, "
                f"no new development, no recap. Just uncertainty and pressure hanging in the air."
            )
        elif chapters:
            last = chapters[-1]
            user_prompt = (
                f"V ({ctx['player_name']}) is somewhere in Night City. No new events to report.\n\n"
                f"The last chapter recorded: {last.get('narrative', '')[:300]}\n\n"
                f"Give a single atmospheric observation — the city breathing, time passing, "
                f"a fragment of what it means to be V right now. 2-3 sentences. "
                f"No recap, no questions. Just the moment."
            )
        else:
            user_prompt = (
                f"Night City. The Chronicles are listening but V hasn't done anything yet.\n\n"
                f"Give a single atmospheric line about the city waiting — the weight of "
                f"what might happen. 1-2 sentences. Set the mood."
            )
        text = self._llm(system_prompt, user_prompt, self._model, self._beat_max_tokens)
        if text and text.strip():
            idle_heat = 0 if idle_reason == "low_signal" or self._broadcast_state == BroadcastStateMode.IDLE else 1
            idle_signal = min(0.9, self._current_signal_score if self._current_signal_score > 0 else 0.35)
            self._push_segment(
                text.strip(),
                voice="host",
                priority=60,
                heat_level=idle_heat,
                signal_score=idle_signal,
            )
            self._last_output_ts = time.time()

    # =========================================================================
    # Internal — background worker
    # =========================================================================

    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            triggered = self._work_evt.wait(timeout=5.0)
            self._work_evt.clear()

            if self._stop_evt.is_set():
                break
            if not triggered:
                continue

            # Drain pending events
            with self._chapter_pending_lock:
                events = list(self._pending_chapter_events)
                self._pending_chapter_events.clear()

            if not events:
                continue

            has_ncm = any(e.get("type", "").startswith("ncm_") for e in events)

            # During race mode: drop non-NCM events — normal narration is suspended
            if self._race_mode and not has_ncm:
                continue
            if has_ncm:
                events = [e for e in events if e.get("type", "").startswith("ncm_")]
                events = self._select_fresh_race_events(events)
                if not events:
                    continue

            anchor = self._pick_anchor(events)
            anchor_type = anchor.get("type", "unknown")
            anchor_tier = _NARRATIVE_TIER.get(anchor_type, NarrativeTier.IGNORE)

            # IDLE is a real mode, not just a vibe prompt. In idle, the station
            # should not turn weak deltas into scene narration.
            if (
                self._broadcast_state == BroadcastStateMode.IDLE
                and anchor_tier != NarrativeTier.CHAPTER
                and not has_ncm
            ):
                continue

            # Enforce separate gaps for beats vs chapters. A small beat should
            # not suppress a later chapter-worthy event.
            if anchor_tier == NarrativeTier.CHAPTER:
                required_gap = self._min_chapter_gap
                last_gap_ts = self._last_chapter_ts
            elif anchor_tier == NarrativeTier.BEAT:
                required_gap = self._min_beat_gap
                last_gap_ts = self._last_beat_ts
            else:
                required_gap = 0.0
                last_gap_ts = 0.0

            elapsed = time.time() - last_gap_ts
            if (
                not has_ncm
                and required_gap > 0
                and elapsed < required_gap
                and not any(e.get("type") in ("session_recap", "game_started") for e in events)
                and anchor_tier != NarrativeTier.CHAPTER
            ):
                # Re-queue and wait
                with self._chapter_pending_lock:
                    self._pending_chapter_events = events + self._pending_chapter_events
                # Re-trigger after delay
                threading.Timer(
                    required_gap - elapsed + 1,
                    self._work_evt.set
                ).start()
                continue

            try:
                if anchor_type == "session_recap":
                    if not self._recap_done:
                        self._recap_done = True
                        self._generate_session_recap()
                elif anchor_type == "game_started":
                    # cold_open() already handled the opening; only recap if it
                    # didn't run (no bible chapters and cold_open was skipped)
                    if not self._recap_done and not self._cold_open_done:
                        self._recap_done = True
                        self._generate_session_recap()
                elif anchor_type in ("ncm_race_finish", "ncm_knockout_finish", "ncm_season_complete", "ncm_championship_complete"):
                    self._last_chapter_ts = time.time()
                    self._generate_race_chapter(anchor)
                elif anchor_type.startswith("ncm_"):
                    now = time.time()
                    self._last_beat_ts = now
                    self._last_race_beat_ts = now
                    self._generate_race_beat(anchor, events)
                elif anchor_tier == NarrativeTier.BEAT:
                    self._last_beat_ts = time.time()
                    self._generate_beat(anchor, events)
                else:
                    self._last_chapter_ts = time.time()
                    self._generate_chapter(anchor, events)
            except Exception as e:
                self._log("nc_chronicles", f"ERROR generating chapter: {e}")

    def _pick_anchor(self, events: List[Dict]) -> Dict:
        """Pick the highest-tier event from a batch."""
        tier_order = [NarrativeTier.CHAPTER, NarrativeTier.BEAT, NarrativeTier.TEXTURE]
        for tier in tier_order:
            for ev in events:
                if _NARRATIVE_TIER.get(ev.get("type", ""), NarrativeTier.IGNORE) == tier:
                    return ev
        return events[0]

    # =========================================================================
    # Internal — chapter generation
    # =========================================================================

    def _build_context(self) -> Dict[str, Any]:
        """Assemble context dict for prompt formatting."""
        if not self._bible:
            return {}
        v          = self._bible.get_v()
        chapters   = self._bible.get_chapters(last_n=self._context_window)
        threads    = self._bible.get_open_threads()
        world      = self._bible.get_world_state()
        playstyle  = v.get("playstyle_profile", {})

        recent_chapters_text = ""
        for ch in reversed(chapters):
            recent_chapters_text += f"\n[Chapter {ch['chapter_id']}]\n{ch['narrative']}\n"

        threads_text = "\n".join(f"- {t}" for t in threads) if threads else "No open threads yet."
        lore = random.choice(_WORLD_LORE) if self._weave_lore else ""

        last_ending = ""
        if chapters:
            last_text = chapters[-1].get("narrative", "")
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', last_text) if s.strip()]
            last_ending = sentences[-1] if sentences else ""

        return {
            "player_name":         v.get("name", "V"),
            "level":               v.get("level", 1),
            "lifepath":            v.get("lifepath") or "unknown",
            "location":            v.get("current_location", "Night City"),
            "district":            v.get("current_district", ""),
            "quest":               v.get("active_quest") or "unknown",
            "objective":           v.get("active_objective") or "",
            "playstyle_label":     playstyle.get("primary", "Mercenary"),
            "death_count":         v.get("session_death_count", 0),
            "recent_chapters":     recent_chapters_text or "(none yet)",
            "open_threads":        threads_text,
            "lore_fragment":       lore,
            "last_chapter_ending": last_ending,
            "live_state_block":    "(no live state)",  # filled in by _generate_chapter
            "broadcast_state":     self._broadcast_state.value,
            "heat_level":          self._current_heat_level,
        }

    @staticmethod
    def _format_live_state(state: Dict[str, Any]) -> str:
        """Format raw game state dict into a concise block for the LLM prompt."""
        if not state:
            return "(no live state available)"
        lines = []
        hp = float(state.get("health_pct") or 1.0)
        lines.append(f"Health: {int(hp * 100)}%")
        if state.get("in_combat"):
            ec = state.get("enemy_count", 0)
            nd = state.get("nearest_enemy_m", 0)
            lines.append(f"IN COMBAT — {ec} enemies, nearest {nd:.0f}m")
        wname = state.get("weapon_name") or ""
        wtype = state.get("weapon_type") or "none"
        if wname and wname != "" and wtype != "none":
            lines.append(f"Weapon: {wname} ({wtype})")
        elif wtype != "none":
            lines.append(f"Weapon type: {wtype}")
        wanted = int(state.get("wanted_level") or 0)
        if wanted:
            lines.append(f"Wanted: {'★' * wanted}")
        chrome = [k.replace("has_", "") for k in ("has_sandevistan", "has_optical_camo", "has_berserk") if state.get(k)]
        if chrome:
            lines.append(f"Chrome active: {', '.join(chrome)}")
        ram_max = float(state.get("ram_max") or 0)
        if ram_max > 0:
            ram_cur = float(state.get("ram_current") or 0)
            lines.append(f"RAM: {ram_cur:.0f}/{ram_max:.0f}")
        if state.get("in_vehicle"):
            spd = float(state.get("vehicle_speed") or 0)
            vn  = state.get("vehicle_name") or state.get("vehicle_type") or "vehicle"
            lines.append(f"In vehicle: {vn}{f' @ {spd:.0f} m/s' if spd > 1 else ''}")
        moves = [k.replace("is_", "") for k in ("is_crouching", "is_sprinting", "is_airborne", "is_swimming", "is_climbing") if state.get(k)]
        if moves:
            lines.append(f"Movement: {', '.join(moves)}")
        if state.get("in_dialogue"):
            npc = state.get("dialogue_npc") or "unknown NPC"
            lines.append(f"In dialogue with: {npc}")
        kills = int(state.get("kills_this_combat") or 0)
        hs    = int(state.get("headshots_this_combat") or 0)
        if kills:
            lines.append(f"Kills this fight: {kills} ({hs} headshots)")
        eddies = int(state.get("eddies") or 0)
        if eddies:
            lines.append(f"Eddies: ₿{eddies:,}")
        weather = state.get("weather") or ""
        hour    = state.get("game_hour")
        if hour is not None:
            h = int(float(hour))
            m = int((float(hour) % 1) * 60)
            time_str = f"{h:02d}:{m:02d}"
            rain = " (raining)" if state.get("is_raining") else ""
            lines.append(f"In-game time: {time_str}{rain} {weather}".strip())
        return "\n".join(lines) if lines else "(no meaningful live state)"

    def _llm(self, system: str, user: str, model: str, max_tokens: int) -> Optional[str]:
        """Call the runtime LLM and return text, or None on failure."""
        llm_fn = self._ctx.get("llm_generate")
        if not llm_fn:
            self._log("nc_chronicles", "WARN: no llm_generate in runtime context")
            return None
        try:
            response = llm_fn(
                user,          # prompt
                system,        # system
                model,         # model
                max_tokens,    # num_predict
                0.72,          # temperature
            )
            if isinstance(response, dict):
                return response.get("response") or response.get("text") or ""
            return str(response) if response else None
        except Exception as e:
            self._log("nc_chronicles", f"LLM error: {e}")
            return None

    def _generate_beat(self, anchor: Dict[str, Any], all_events: List[Dict[str, Any]]) -> None:
        """Generate a short host beat for a meaningful but non-chapter moment."""
        ctx = self._build_context()
        if not ctx:
            return

        live_state = anchor.get("data", {})
        if not live_state:
            for ev in reversed(all_events):
                if ev.get("data"):
                    live_state = ev["data"]
                    break

        ctx["live_state_block"] = self._format_live_state(live_state)
        related_lines = [
            _event_prompt_description(ev)
            for ev in all_events
            if ev is not anchor
        ]
        event_description = _event_prompt_description(anchor)
        if related_lines:
            event_description += "\nRelated context:\n- " + "\n- ".join(related_lines[:2])

        mode_guidance = ""
        if self._broadcast_state == BroadcastStateMode.LOW:
            mode_guidance = (
                "\nBroadcast state is LOW. Keep this sparse and factual. "
                "Do not imply a major turn in V's story if the evidence is only a small shift."
            )

        user_prompt = _BEAT_PROMPT.format(
            event_description=event_description,
            live_state_block=ctx["live_state_block"],
            last_chapter_ending=ctx["last_chapter_ending"] or "No chapter ending recorded yet.",
        ) + mode_guidance
        system_prompt = _SYSTEM_CHRONICLER.format(**ctx)
        beat_text = self._llm(system_prompt, user_prompt, self._fast_model, self._beat_max_tokens)
        if not beat_text or not beat_text.strip():
            return

        clean = beat_text.strip()
        beat_heat, beat_signal = self._resolve_event_prosody(all_events, anchor.get("_heat_level"), anchor.get("_signal_score"))
        self._push_segment(clean, voice="host", priority=74, heat_level=beat_heat, signal_score=beat_signal)
        if random.random() < (self._secondary_chance * 0.45):
            threading.Timer(1.2, self._generate_secondary_beat, args=(ctx, list(all_events))).start()
        self._log("nc_chronicles", f"Beat emitted for {anchor.get('type', 'unknown')}")

    def _generate_chapter(self, anchor: Dict, all_events: List[Dict]) -> None:
        ctx = self._build_context()
        if not ctx:
            return

        events_summary = self._bible.recent_events_summary(n=12) if self._bible else ""

        # Pull live game state from the anchor event's data (it carries **new_state)
        live_state = anchor.get("data", {})
        # Fall back to the most recent event with meaningful state
        if not live_state:
            for ev in reversed(all_events):
                if ev.get("data"):
                    live_state = ev["data"]
                    break
        ctx["live_state_block"] = self._format_live_state(live_state)

        # Build chapter prompt
        mode_guidance = ""
        if self._broadcast_state == BroadcastStateMode.LOW:
            mode_guidance = (
                "\nBroadcast state is LOW. Do not inflate routine movement into a sweeping scene. "
                "If the evidence is modest, keep the chapter compressed and grounded."
            )
        user_prompt = _CHAPTER_PROMPT.format(
            recent_chapters=ctx["recent_chapters"],
            open_threads=ctx["open_threads"],
            player_name=ctx["player_name"],
            level=ctx["level"],
            lifepath=ctx["lifepath"],
            location=ctx["location"],
            district=ctx["district"],
            quest=ctx["quest"],
            objective=ctx["objective"],
            playstyle_label=ctx["playstyle_label"],
            death_count=ctx["death_count"],
            recent_events_summary=events_summary,
            lore_fragment=ctx["lore_fragment"],
            live_state_block=ctx["live_state_block"],
        ) + mode_guidance

        system_prompt = _SYSTEM_CHRONICLER.format(**ctx)

        chapter_text = self._llm(system_prompt, user_prompt, self._model, self._chapter_max_tokens)
        if not chapter_text:
            return

        chapter_text = chapter_text.strip()

        # Save to bible
        theme_hints = self._infer_themes(anchor.get("type", ""), ctx)
        chapter_id  = self._bible.add_chapter(chapter_text, theme_hints, ctx) if self._bible else 0

        # Auto-open a narrative thread based on new content
        self._auto_thread(anchor, ctx, chapter_text)

        # Push main chapter narration
        chapter_heat, chapter_signal = self._resolve_event_prosody(all_events, anchor.get("_heat_level"), anchor.get("_signal_score"))
        self._push_segment(
            chapter_text,
            voice="host",
            priority=80,
            heat_level=chapter_heat,
            signal_score=chapter_signal,
        )

        # Optionally add a secondary voice lens (pass event context so voices react to what happened)
        if random.random() < self._secondary_chance:
            threading.Timer(1.5, self._generate_secondary_beat, args=(ctx, list(all_events))).start()

        self._log("nc_chronicles", f"Chapter {chapter_id} written — {len(chapter_text)} chars")

    def _secondary_recent_memory(self, voice_key: str) -> str:
        now = time.time()
        history = [
            item for item in self._secondary_history.get(voice_key, [])
            if now - float(item.get("ts") or 0.0) < self._secondary_memory_window
        ]
        self._secondary_history[voice_key] = history[-4:]
        if not history:
            return "None yet."
        return "\n".join(
            f"- {item.get('topic', 'recent lane')}: {item.get('evidence', 'already covered')}"
            for item in history[-2:]
        )

    def _remember_secondary_beat(
        self,
        voice_key: str,
        topic: str,
        evidence_lines: List[str],
        text: str,
    ) -> None:
        history = self._secondary_history.setdefault(voice_key, [])
        history.append({
            "ts": time.time(),
            "topic": topic,
            "evidence": "; ".join(evidence_lines[:2])[:180],
            "text": text[:220],
        })
        self._secondary_history[voice_key] = history[-4:]

    def _select_secondary_voice(
        self,
        ctx: Dict[str, Any],
        events: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Pick a secondary commentator only when the evidence supports one."""
        if not events:
            return None
        if self._broadcast_state == BroadcastStateMode.IDLE:
            return None

        now = time.time()
        batches: Dict[str, Dict[str, Any]] = {
            "fixer": {
                "system": _SYSTEM_FIXER,
                "score": 0.0,
                "topic_scores": {},
                "evidence": [],
                "guidance": {
                    "obligation": "Call the obligation, leverage, or debt now in play.",
                    "reputation": "Call what the street will remember and who is taking note.",
                    "eddies": "Call what got earned, burned, or turned into leverage.",
                    "movement": "Call whether the movement looks disciplined or rootless.",
                    "liability": "Call the trust cost or street liability exposed here.",
                },
            },
            "netrunner": {
                "system": _SYSTEM_NETRUNNER,
                "score": 0.0,
                "topic_scores": {},
                "evidence": [],
                "guidance": {
                    "network_load": "Interpret the RAM load and quickhack posture.",
                    "stealth_pattern": "Interpret the signal pattern of concealment or exposure.",
                    "routing": "Interpret route discipline, repositioning, and movement efficiency.",
                    "combat_pattern": "Interpret the tactical pattern behind the fight.",
                    "speed_spike": "Interpret the timing spike and execution window.",
                },
            },
            "ripperdoc": {
                "system": _SYSTEM_RIPPERDOC,
                "score": 0.0,
                "topic_scores": {},
                "evidence": [],
                "guidance": {
                    "trauma": "Talk about injury, survivability, and what nearly broke.",
                    "recovery": "Talk about stabilization, strain, and how recovery reads.",
                    "chrome_load": "Talk about the body burden and chrome usage here.",
                    "wear_and_tear": "Talk about accumulated damage and surgical cost.",
                },
            },
            "corpo": {
                "system": _SYSTEM_CORPO,
                "score": 0.0,
                "topic_scores": {},
                "evidence": [],
                "guidance": {
                    "exposure": "Frame the institutional exposure or security implications.",
                    "market_signal": "Frame the movement in terms of asset value or signal.",
                    "asset_value": "Frame the result as value creation or volatility.",
                    "volatility": "Frame the instability and risk concentration.",
                },
            },
        }

        def add(voice: str, topic: str, delta: float, line: str) -> None:
            slot = batches[voice]
            slot["score"] += delta
            slot["topic_scores"][topic] = slot["topic_scores"].get(topic, 0.0) + delta
            if line not in slot["evidence"]:
                slot["evidence"].append(line)

        for event in events:
            etype = str(event.get("type") or "")
            data = event.get("data") or {}
            location = data.get("location") or ctx.get("location") or "Night City"
            district = (data.get("district") or ctx.get("district") or "").lower()
            quest_blob = " ".join(
                str(part or "") for part in (
                    data.get("quest"),
                    data.get("objective"),
                    ctx.get("quest"),
                    ctx.get("objective"),
                    district,
                    location,
                )
            ).lower()
            is_corpo_scene = any(term in quest_blob for term in _CORPO_TERMS) or "corpo plaza" in district

            if etype == "quest_updated":
                add("fixer", "obligation", 2.4, f"New obligation: {data.get('quest') or ctx.get('quest')}.")
                if is_corpo_scene:
                    add("corpo", "exposure", 2.4, f"Corporate-adjacent movement around {data.get('quest') or ctx.get('quest')}.")
            elif etype == "street_cred_up":
                add("fixer", "reputation", 1.7, f"Street cred moved to {data.get('street_cred', '?')}.")
                add("corpo", "market_signal", 1.0, f"Street signal ticked upward to {data.get('street_cred', '?')}.")
            elif etype in ("eddies_windfall", "eddies_splurge"):
                amount = int(data.get("gained") or data.get("spent") or 0)
                label = "earned" if etype == "eddies_windfall" else "burned"
                add("fixer", "eddies", 1.8, f"V {label} {amount:,} eddies.")
                add("corpo", "market_signal", 1.1, f"Cashflow shifted by {amount:,} eddies.")
            elif etype == "location_changed":
                add("fixer", "movement", 0.8, f"Route shifted into {location}.")
                add("netrunner", "routing", 1.0, f"Movement rerouted through {location}.")
            elif etype in ("vehicle_entered", "vehicle_exited"):
                add("netrunner", "routing", 0.7, f"Transit posture changed around {location}.")
            elif etype == "combat_started":
                add("netrunner", "combat_pattern", 1.1, f"Engagement opened in {location}.")
                add("ripperdoc", "wear_and_tear", 0.9, f"The body got committed to a fight in {location}.")
            elif etype == "combat_ended":
                kills = int(data.get("kills_this_combat") or 0)
                add("netrunner", "combat_pattern", 1.0 + min(0.8, kills * 0.2), f"Fight closed with {kills} kills in {location}.")
                add("ripperdoc", "wear_and_tear", 1.0, f"Another fight left its marks in {location}.")
            elif etype in ("hack_burst", "ram_depleted"):
                add("netrunner", "network_load", 2.4, f"RAM load spiked hard in {location}.")
                add("ripperdoc", "chrome_load", 1.0, f"Neural load got pushed in {location}.")
            elif etype in ("optical_camo_activated", "stealth_takedown", "stealth_broken"):
                add("netrunner", "stealth_pattern", 1.8, f"Signal discipline changed in {location}.")
                if etype == "stealth_broken":
                    add("fixer", "liability", 1.4, f"Exposure widened in {location}.")
            elif etype in ("sandevistan_activated", "berserk_activated"):
                add("netrunner", "speed_spike", 1.4, f"Tempo spike hit in {location}.")
                add("ripperdoc", "chrome_load", 1.8, f"Chrome load surged in {location}.")
            elif etype in ("player_death", "near_death"):
                add("ripperdoc", "trauma", 2.8, f"V nearly died in {location}.")
                add("fixer", "liability", 1.3, f"A flatline risk just hit the board in {location}.")
                add("corpo", "volatility", 1.2, f"Operational volatility spiked in {location}.")
            elif etype == "health_recovered":
                add("ripperdoc", "recovery", 1.5, "Recovery pattern showed up after the damage.")
            elif etype == "wanted_level_change":
                add("corpo", "exposure", 1.7, f"Security exposure shifted to wanted level {data.get('wanted_level', 0)}.")
                add("netrunner", "combat_pattern", 0.8, "Heat profile changed mid-run.")
            elif etype == "kill_spree":
                add("fixer", "reputation", 1.6, f"The body count climbed to {data.get('kills_this_combat', 0)}.")
                add("netrunner", "combat_pattern", 1.3, "Aggression curve spiked hard.")
                add("ripperdoc", "wear_and_tear", 1.0, "Sustained violence is writing itself onto the body.")
            elif etype.startswith("ncm_"):
                add("fixer", "reputation", 1.6, f"Street racing signal moved with {etype.replace('ncm_', '').replace('_', ' ')}.")
                add("corpo", "asset_value", 1.8, "Racing result shifted V's perceived value.")

        candidates: List[Dict[str, Any]] = []
        low_mode_rules = {
            "fixer": {"topics": {"obligation", "reputation", "eddies", "liability"}, "min_score": 3.2},
            "netrunner": {"topics": {"network_load", "stealth_pattern", "routing", "combat_pattern", "speed_spike"}, "min_score": 2.4},
            "ripperdoc": {"topics": {"trauma", "recovery", "chrome_load"}, "min_score": 3.3},
            "corpo": {"topics": {"exposure", "asset_value", "volatility", "market_signal"}, "min_score": 3.3},
        }
        for voice_key, slot in batches.items():
            if not slot["topic_scores"]:
                continue
            topic = max(slot["topic_scores"], key=slot["topic_scores"].get)
            score = float(slot["score"])
            recent_history = self._secondary_history.get(voice_key, [])
            recent_same_topic = any(
                item.get("topic") == topic and (now - float(item.get("ts") or 0.0)) < self._secondary_topic_cooldown
                for item in recent_history
            )
            if recent_same_topic:
                score -= 1.2

            min_score = _SECONDARY_VOICE_THRESHOLDS[voice_key]
            if self._broadcast_state == BroadcastStateMode.LOW:
                rule = low_mode_rules[voice_key]
                if topic not in rule["topics"]:
                    continue
                min_score = max(min_score, float(rule["min_score"]))

            if score < min_score:
                continue

            candidates.append({
                "voice": voice_key,
                "topic": topic,
                "score": score,
                "system": slot["system"],
                "guidance": slot["guidance"].get(topic, "React to the evidence with a fresh angle."),
                "evidence": slot["evidence"][:3],
            })

        if not candidates:
            return None

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def _generate_secondary_beat(self, ctx: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
        """Add a brief secondary voice commentary only when a lane has evidence."""
        if self._stop_evt.is_set():
            return

        selection = self._select_secondary_voice(ctx, events)
        if not selection:
            self._log("nc_chronicles", "secondary voice skipped — no fresh evidence lane")
            return

        voice_key = selection["voice"]
        system_prompt = selection["system"].format(**ctx)
        evidence_block = "\n".join(f"- {line}" for line in selection["evidence"])
        recent_memory = self._secondary_recent_memory(voice_key)
        user_prompt = (
            f"Fresh evidence from this session:\n{evidence_block}\n\n"
            f"Focus lane: {selection['topic']}. {selection['guidance']}\n\n"
            f"Your recent commentary memory:\n{recent_memory}\n\n"
            f"React only to the evidence above. Do not default to generic stock phrasing, "
            f"and do not repeat the same lane you just used unless the evidence forces it."
        )

        text = self._llm(system_prompt, user_prompt, self._fast_model, self._beat_max_tokens)
        if text and text.strip():
            clean = text.strip()
            beat_heat, beat_signal = self._resolve_event_prosody(events)
            self._push_segment(
                clean,
                voice=voice_key,
                priority=68,
                heat_level=beat_heat,
                signal_score=beat_signal,
            )
            self._remember_secondary_beat(voice_key, selection["topic"], selection["evidence"], clean)

    def _generate_race_beat(self, anchor: Dict, all_events: List[Dict]) -> None:
        """Lightweight commentary for a mid-race NCM lifecycle event. No Bible chapter."""
        etype = anchor.get("type", "")
        data  = anchor.get("data", {})
        self._flush_queued_race_outputs(f"new live race beat {etype}", include_audio=True)
        user_prompt = _build_race_beat_prompt(etype, data)
        if not user_prompt:
            self._log("nc_chronicles", f"no prompt mapped for {etype} — skipping")
            return
        ctx = self._build_context()
        if not ctx:
            return
        system_prompt = _SYSTEM_RACE_COMMENTATOR
        text = self._llm(system_prompt, user_prompt, self._fast_model, self._beat_max_tokens)
        if text and text.strip():
            age = time.time() - _race_observed_ts(anchor)
            if age > self._race_event_ttl_sec:
                self._log("nc_chronicles", f"Race beat expired during generation for {etype} ({age:.1f}s old)")
                return
            race_heat, race_signal = self._resolve_event_prosody(all_events, anchor.get("_heat_level"), anchor.get("_signal_score"))
            self._push_segment(
                text.strip(),
                voice="host",
                priority=99,
                heat_level=5,
                signal_score=max(9.0, race_signal),
                source=_RACE_COMMENTARY_SOURCE,
                title=_RACE_BEAT_TITLE,
            )
            self._log("nc_chronicles", f"Race beat emitted for {etype}")

    def _generate_race_chapter(self, anchor: Dict) -> None:
        """Write a full Chronicle chapter from a race result and save it to the Bible."""
        data = anchor.get("data") or self._race_result
        ctx  = self._build_context()
        if not ctx:
            return
        etype = anchor.get("type", "ncm_race_finish")

        if etype == "ncm_season_complete":
            season_no = int(data.get("season") or 0)
            name = data.get("season_name") or (f"Season {season_no}" if season_no else "the season")
            rep_bonus = int(data.get("rep_bonus") or data.get("repBonus") or 0)
            eddies = int(data.get("eddies") or 0)
            win_pct = float(data.get("win_pct") or data.get("winPct") or 0)
            user_prompt = (
                f"Night City Chronicles — season complete.\n\n"
                f"{ctx['player_name']} just closed out {name}. "
                f"Season rewards: +{rep_bonus} rep, {eddies} eddies, win rate {win_pct:.2f}.\n\n"
                f"Write a 4-5 sentence literary chronicle entry about what the season built, "
                f"what it cost, and what V carries forward into the next chapter of the streets. "
                f"Location: {ctx['location']}."
            )
        elif etype == "ncm_championship_complete":
            pos = int(data.get("final_position") or data.get("champ_pos") or 0)
            champion = bool(data.get("champion")) or (pos == 1 and pos > 0)
            tier = data.get("tier") or "the league"
            user_prompt = (
                f"Night City Chronicles — championship complete.\n\n"
                f"{ctx['player_name']} ended the championship in P{pos} in {tier}."
                f"{' CHAMPION.' if champion else ''}\n\n"
                f"Write a 4-6 sentence literary entry: the weight of the whole arc, "
                f"what V is now vs what they were, what Night City thinks of them."
            )
        elif etype == "ncm_knockout_finish":
            pos = int(data.get("player_position") or data.get("position") or 0)
            payout = int(data.get("payout") or 0)
            rep = int(data.get("rep_gain") or 0)
            t = float(data.get("race_time") or 0)
            player_won = bool(data.get("player_won")) or (pos == 1 and pos > 0)
            track_name = data.get("track_name") or ctx.get("location") or "the knockout circuit"
            m = int(t // 60)
            s = t - m * 60
            time_str = (f"{m}:{s:05.2f}" if m else f"{s:.2f}s") if t > 0 else "unknown time"
            user_prompt = (
                f"Night City Chronicles — knockout result.\n\n"
                f"{ctx['player_name']} finished P{pos} on {track_name} in {time_str}. "
                f"{'V won the whole knockout.' if player_won else 'V survived until the field closed in.'} "
                f"Payout: {payout} eddies. Rep: +{rep}.\n\n"
                f"Write a 3-4 sentence race close about elimination pressure, what V endured, "
                f"and how a knockout finish changes their standing on the streets."
            )
        else:
            user_prompt = _build_race_chapter_prompt(data, ctx)

        system_prompt = _SYSTEM_CHRONICLER.format(**ctx)
        chapter_text  = self._llm(system_prompt, user_prompt, self._model, self._chapter_max_tokens)
        if not chapter_text or not chapter_text.strip():
            return

        chapter_text = chapter_text.strip()

        # Determine narrative themes
        is_dnf = bool(data.get("is_dnf"))
        themes = ["racing", "night_city_streets"]
        if is_dnf:
            themes.append("unfinished_business")
        elif data.get("wager_won"):
            themes.append("night_city_economy")
        if data.get("bounty_resolved"):
            themes.append("consequences")
        if etype == "ncm_knockout_finish":
            themes.append("survival")
            if bool(data.get("player_won")) or int(data.get("player_position") or 0) == 1:
                themes.append("prestige")

        chapter_id = self._bible.add_chapter(chapter_text, themes, ctx) if self._bible else 0

        # Open a thread from the result
        if self._bible:
            location = ctx.get("location", "the city")
            if is_dnf:
                self._bible.add_thread(
                    f"V left a race unfinished in {location} — something's owed."
                )
            elif etype == "ncm_knockout_finish" and int(data.get("player_position") or 0) > 0:
                self._bible.add_thread(
                    f"V survived the knockout on {data.get('track_name') or location} — elimination leaves scars."
                )
            elif data.get("wager_won"):
                self._bible.add_thread(
                    f"V won a wager on the streets — reputation is currency in Night City."
                )
            elif data.get("bounty_resolved"):
                self._bible.add_thread(
                    f"Bounty closed after the race in {location} — but these things leave marks."
                )

        race_heat, race_signal = self._resolve_event_prosody([anchor], anchor.get("_heat_level"), anchor.get("_signal_score"))
        target_heat = 5 if etype in ("ncm_race_finish", "ncm_knockout_finish", "ncm_season_complete", "ncm_championship_complete") else max(4, race_heat)
        self._flush_queued_race_outputs(f"race result {etype}", include_audio=True)
        self._push_segment(
            chapter_text,
            voice="host",
            priority=99,
            heat_level=target_heat,
            signal_score=max(9.0, race_signal),
            source=_RACE_COMMENTARY_SOURCE,
            title=_RACE_RESULT_TITLE,
        )
        self._log("nc_chronicles", f"Race chapter {chapter_id} written — {len(chapter_text)} chars")

    def _generate_session_recap(self) -> None:
        """Generate a 'Previously on Night City Chronicles…' opening."""
        if not self._bible:
            return

        chapters = self._bible.get_chapters(last_n=3)
        if not chapters:
            self._queue_intro_beat()
            return

        ctx = self._build_context()

        chapter_summaries = ""
        for ch in chapters:
            chapter_summaries += f"\n[Chapter {ch['chapter_id']}]\n{ch['narrative']}\n"

        threads_text = "\n".join(f"- {t}" for t in self._bible.get_open_threads()) or "None yet."

        user_prompt = _RECAP_PROMPT.format(
            n_chapters=len(chapters),
            chapter_summaries=chapter_summaries,
            open_threads=threads_text,
            player_name=ctx["player_name"],
            level=ctx["level"],
            location=ctx["location"],
        )
        system_prompt = _SYSTEM_CHRONICLER.format(**ctx)

        recap_text = self._llm(system_prompt, user_prompt, self._model, self._recap_max_tokens)
        if recap_text and recap_text.strip():
            self._push_segment(recap_text.strip(), voice="host", priority=85, heat_level=1, signal_score=1.0)
            self._log("nc_chronicles", "Session recap delivered")

    # =========================================================================
    # Internal — thread / theme inference
    # =========================================================================

    def _infer_themes(self, event_type: str, ctx: Dict) -> List[str]:
        themes = []
        if event_type == "player_death":
            themes.append("mortality")
        if event_type == "quest_updated":
            themes.append("obligation")
        if event_type == "near_death":
            themes.append("mortality")
            themes.append("cost_of_survival")
        if event_type in ("sandevistan_activated", "berserk_activated"):
            themes.append("chrome_and_soul")
        if event_type == "stealth_broken":
            themes.append("exposure")
            themes.append("consequences")
        if event_type == "stealth_takedown":
            themes.append("invisibility_as_power")
        if event_type == "kill_spree":
            themes.append("violence_as_language")
        if event_type == "hack_burst":
            themes.append("netrunner_edge")
        if event_type == "eddies_windfall":
            themes.append("night_city_economy")
        if "corpo" in (ctx.get("quest") or "").lower():
            themes.append("corporate_shadow")
        if ctx.get("death_count", 0) > 2:
            themes.append("resilience")
        playstyle = ctx.get("playstyle_label", "")
        if "force" in playstyle.lower() or "Surgeon" in playstyle:
            themes.append("violence_as_language")
        elif "Ghost" in playstyle or "Shadow" in playstyle:
            themes.append("invisibility_as_power")
        return themes

    def _auto_thread(self, anchor: Dict, ctx: Dict, chapter_text: str) -> None:
        """Heuristically open or close narrative threads based on events."""
        if not self._bible:
            return

        event_type = anchor.get("type", "")
        quest      = ctx.get("quest", "")
        location   = ctx.get("location", "Night City")

        if event_type == "quest_updated" and quest:
            # Open a thread for the new quest if it looks main-story
            keywords = ["Silverhand", "Arasaka", "Relic", "Johnny", "Smasher", "Alt"]
            if any(k.lower() in quest.lower() for k in keywords):
                self._bible.add_thread(f"The {quest} thread — Relic complications")
            else:
                self._bible.add_thread(f"{quest} — what V agreed to do and at what cost")

        if event_type == "player_death":
            self._bible.add_thread(f"V flatlined in {location} — and came back. That should mean something.")

        if event_type == "level_up" and ctx.get("level", 1) % 10 == 0:
            self._bible.add_thread(
                f"Level {ctx['level']} — V is no longer who they were when they started this."
            )

        if event_type == "near_death":
            self._bible.add_thread(
                f"V nearly bled out in {location} — what is it costing them to keep going?"
            )

        if event_type == "stealth_broken":
            d = anchor.get("data", {})
            self._bible.add_thread(
                f"Cover blown in {location} with {d.get('enemy_count', 0)} witnesses — who saw V's face?"
            )

        if event_type == "kill_spree":
            d        = anchor.get("data", {})
            milestone = d.get("milestone", d.get("kills_this_combat", 0))
            weapon   = d.get("weapon_name") or d.get("weapon_type") or "unknown weapon"
            self._bible.add_thread(
                f"{milestone} bodies in {location} with a {weapon} — Night City is watching."
            )

        if event_type == "sandevistan_activated":
            self._bible.add_thread(
                f"V slowed the world to a crawl in {location} — chrome and consequence."
            )

    # =========================================================================
    # Internal — output
    # =========================================================================

    def _extract_motif_tags(self, norm_text: str) -> List[str]:
        tags: List[str] = []
        for tag, patterns in _MOTIF_TAG_PATTERNS.items():
            if all(re.search(pattern, norm_text) for pattern in patterns):
                tags.append(tag)
        return tags

    def _motif_cooldown_blocks(self, norm_text: str) -> bool:
        """
        Suppress repeated stock structures more aggressively when the station is
        quiet. This is intentionally semantic-lightweight rather than exact-word.
        """
        now_t = time.time()
        self._recent_motif_hits = [
            (ts, tag) for ts, tag in self._recent_motif_hits
            if now_t - ts < self._motif_window_sec
        ]
        tags = self._extract_motif_tags(norm_text)
        if not tags or self._broadcast_state == BroadcastStateMode.ACTIVE:
            for tag in tags:
                self._recent_motif_hits.append((now_t, tag))
            return False

        recent_counts: Dict[str, int] = {}
        for _, tag in self._recent_motif_hits:
            recent_counts[tag] = recent_counts.get(tag, 0) + 1

        repeat_limit = 0 if self._broadcast_state == BroadcastStateMode.IDLE else 1
        repeated = [tag for tag in tags if recent_counts.get(tag, 0) > repeat_limit]
        if repeated:
            self._log("nc_chronicles", f"motif cooldown blocked: {', '.join(repeated)}")
            return True

        for tag in tags:
            self._recent_motif_hits.append((now_t, tag))
        return False

    def _resolve_segment_heat(
        self,
        heat_level: Optional[int] = None,
        signal_score: Optional[float] = None,
    ) -> Tuple[int, float]:
        resolved_heat = self._current_heat_level if heat_level is None else int(heat_level)
        resolved_signal = self._current_signal_score if signal_score is None else float(signal_score)
        return max(0, min(5, resolved_heat)), max(0.0, resolved_signal)

    def _resolve_event_prosody(
        self,
        events: List[Dict[str, Any]],
        fallback_heat: Optional[int] = None,
        fallback_signal: Optional[float] = None,
    ) -> Tuple[int, float]:
        heat = fallback_heat
        signal = fallback_signal
        for event in events:
            if heat is None and event.get("_heat_level") is not None:
                heat = int(event.get("_heat_level") or 0)
            elif event.get("_heat_level") is not None:
                heat = max(int(heat or 0), int(event.get("_heat_level") or 0))

            if signal is None and event.get("_signal_score") is not None:
                signal = float(event.get("_signal_score") or 0.0)
            elif event.get("_signal_score") is not None:
                signal = max(float(signal or 0.0), float(event.get("_signal_score") or 0.0))

        return self._resolve_segment_heat(heat, signal)

    def _build_prosody_profile(
        self,
        heat_level: int,
        voice_key: str,
        signal_score: float,
    ) -> ProsodyProfile:
        base = {
            0: dict(
                allow_exclamation=False,
                exclamation_budget=0,
                prefer_short_sentences=True,
                prefer_fragment_beats=False,
                prefer_ellipses=False,
                max_clause_length=10,
                energy_bias=0.0,
                pause_bias=0.6,
            ),
            1: dict(
                allow_exclamation=False,
                exclamation_budget=0,
                prefer_short_sentences=True,
                prefer_fragment_beats=False,
                prefer_ellipses=True,
                max_clause_length=12,
                energy_bias=0.1,
                pause_bias=0.6,
            ),
            2: dict(
                allow_exclamation=False,
                exclamation_budget=0,
                prefer_short_sentences=False,
                prefer_fragment_beats=True,
                prefer_ellipses=True,
                max_clause_length=16,
                energy_bias=0.25,
                pause_bias=0.5,
            ),
            3: dict(
                allow_exclamation=True,
                exclamation_budget=1,
                prefer_short_sentences=True,
                prefer_fragment_beats=True,
                prefer_ellipses=False,
                max_clause_length=14,
                energy_bias=0.45,
                pause_bias=0.4,
            ),
            4: dict(
                allow_exclamation=True,
                exclamation_budget=1,
                prefer_short_sentences=True,
                prefer_fragment_beats=True,
                prefer_ellipses=False,
                max_clause_length=12,
                energy_bias=0.7,
                pause_bias=0.3,
            ),
            5: dict(
                allow_exclamation=True,
                exclamation_budget=2,
                prefer_short_sentences=True,
                prefer_fragment_beats=True,
                prefer_ellipses=False,
                max_clause_length=8,
                energy_bias=1.0,
                pause_bias=0.2,
            ),
        }[max(0, min(5, heat_level))]
        profile = ProsodyProfile(heat_level=heat_level, **base)

        if signal_score < self._min_event_delta_for_energy:
            profile.allow_exclamation = False
            profile.exclamation_budget = 0
            profile.prefer_fragment_beats = False

        if voice_key == "host":
            profile.energy_bias *= 0.9
            if heat_level < 4 and signal_score < (self._min_event_delta_for_energy + 1.0):
                profile.allow_exclamation = False
                profile.exclamation_budget = 0
        elif voice_key == "fixer":
            profile.energy_bias = min(1.0, profile.energy_bias + 0.12)
            profile.pause_bias = max(0.15, profile.pause_bias - 0.05)
        elif voice_key == "corpo":
            profile.allow_exclamation = heat_level >= 5 and signal_score >= self._min_event_delta_for_energy
            profile.exclamation_budget = 1 if profile.allow_exclamation else 0
            profile.prefer_fragment_beats = False
            profile.max_clause_length = max(profile.max_clause_length, 14)
            profile.energy_bias *= 0.45
            profile.pause_bias = min(0.75, profile.pause_bias + 0.2)
        elif voice_key == "ripperdoc":
            profile.allow_exclamation = profile.allow_exclamation and heat_level >= 4
            profile.exclamation_budget = min(profile.exclamation_budget, 1 if heat_level >= 4 else 0)
            profile.energy_bias *= 0.65
            profile.pause_bias = min(0.8, profile.pause_bias + 0.12)
        elif voice_key == "netrunner":
            profile.allow_exclamation = profile.allow_exclamation and heat_level >= 4
            profile.exclamation_budget = min(profile.exclamation_budget, 1 if heat_level >= 4 else 0)
            profile.energy_bias *= 0.78
            profile.max_clause_length = max(10, min(profile.max_clause_length, 12))

        return profile

    @staticmethod
    def _normalize_similarity_text(text: str) -> str:
        norm = text.lower().replace("...", " ")
        norm = re.sub(r"[!?,.;:]+", " ", norm)
        norm = re.sub(r"\s+", " ", norm)
        return norm.strip()

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        cleaned = re.sub(r"\s+", " ", text.replace("\n", " ").strip())
        if not cleaned:
            return []
        return [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]

    @staticmethod
    def _sentence_terminal(sentence: str) -> str:
        stripped = sentence.strip()
        if stripped.endswith("?"):
            return "?"
        if stripped.endswith("!"):
            return "!"
        if stripped.endswith("..."):
            return "..."
        return "."

    @staticmethod
    def _finish_sentence(sentence: str, terminal: str) -> str:
        body = re.sub(r"[.!?]+$", "", sentence.strip()).strip(" ,;:")
        if not body:
            return ""
        return f"{body}{terminal}"

    def _prune_voice_output_history(self, voice_key: str) -> List[Dict[str, Any]]:
        history = self._voice_output_history.setdefault(voice_key, [])
        min_index = self._prosody_output_index - max(self._exclamation_window_outputs, 12) - 2
        trimmed = [item for item in history if int(item.get("index", -999)) >= min_index]
        self._voice_output_history[voice_key] = trimmed[-16:]
        return self._voice_output_history[voice_key]

    def _split_long_sentence(self, sentence: str, max_words: int) -> List[str]:
        terminal = self._sentence_terminal(sentence)
        body = re.sub(r"[.!?]+$", "", sentence.strip()).strip(" ,;:")
        if not body:
            return []
        if len(body.split()) <= max_words:
            return [self._finish_sentence(body, terminal if terminal in ("?", "!") else ".")]

        raw_parts = [
            part.strip(" ,;:")
            for part in re.split(r"\s*[;:]\s+|\s*,\s+", body)
            if part.strip(" ,;:")
        ]
        if not raw_parts:
            raw_parts = [body]

        chunks: List[str] = []
        for part in raw_parts:
            remaining = part.strip()
            while remaining:
                words = remaining.split()
                if len(words) <= max_words:
                    chunks.append(remaining)
                    break
                cut = max_words
                for idx in range(min(len(words) - 1, max_words), max(max_words // 2, 1), -1):
                    token = words[idx].lower().strip(",;:")
                    if token in {"and", "but", "while", "because", "when", "where", "then", "before", "after", "so"}:
                        cut = idx
                        break
                head = " ".join(words[:cut]).strip(" ,;:")
                tail = " ".join(words[cut:]).strip(" ,;:")
                tail = re.sub(
                    r"^(and|but|while|because|when|where|then|before|after|so)\s+",
                    "",
                    tail,
                    flags=re.IGNORECASE,
                )
                if head:
                    chunks.append(head)
                remaining = tail

        finalized: List[str] = []
        for idx, chunk in enumerate(chunks):
            last_terminal = terminal if idx == len(chunks) - 1 and terminal in ("?", "!") else "."
            finalized.append(self._finish_sentence(chunk, last_terminal))
        return [chunk for chunk in finalized if chunk]

    def _fragmentize_sentence(self, sentence: str) -> List[str]:
        body = re.sub(r"[.!?]+$", "", sentence.strip()).strip(" ,;:")
        parts = [part.strip(" ,;:") for part in re.split(r"\s*,\s*", body) if part.strip(" ,;:")]
        if len(parts) != 3:
            return [sentence.strip()]
        if not all(1 <= len(part.split()) <= 4 for part in parts):
            return [sentence.strip()]
        return [self._finish_sentence(part, ".") for part in parts]

    def _apply_low_heat_ellipsis(self, sentences: List[str], profile: ProsodyProfile) -> List[str]:
        if not profile.prefer_ellipses or not sentences:
            return sentences
        if any("..." in sentence for sentence in sentences):
            return sentences
        updated = list(sentences)
        first = updated[0]
        if len(re.sub(r"[.!?]+$", "", first).split()) <= max(6, profile.max_clause_length // 2):
            updated[0] = self._finish_sentence(first, "...")
        elif len(updated) == 1 and len(first.split()) <= profile.max_clause_length:
            updated[0] = self._finish_sentence(first, "...")
        return updated

    def _score_exclamation_candidate(self, sentence: str, voice_key: str) -> float:
        body = re.sub(r"[.!?]+$", "", sentence.strip()).lower()
        words = body.split()
        if not words:
            return -1.0

        score = 0.0
        if len(words) <= 5:
            score += 1.2
        elif len(words) <= 9:
            score += 0.6
        elif len(words) > 14:
            score -= 0.8

        common_terms = (
            "move", "now", "go", "run", "watch", "too late", "not anymore",
            "green", "there", "signal spike", "warning", "danger", "real",
        )
        voice_terms = {
            "host": ("not anymore", "it starts", "the light goes green", "now it", "that changes"),
            "fixer": ("don't", "do not", "waste", "clean it up", "move", "too much noise", "that call"),
            "netrunner": ("detected", "there", "spike", "breach", "route", "threat", "packet"),
            "ripperdoc": ("that hit", "body", "blood", "overload", "burn", "cost", "fracture"),
            "corpo": ("containment", "breach", "failed", "critical", "exposure", "instability"),
        }
        if any(term in body for term in common_terms):
            score += 0.8
        if any(term in body for term in voice_terms.get(voice_key, ())):
            score += 0.8
        if re.match(r"^(move|run|go|watch|hold|clean|look|get|stay|keep|don't|do not)\b", body):
            score += 1.0
        if body.endswith(("now", "there", "again")):
            score += 0.5
        if voice_key == "corpo" and not any(term in body for term in voice_terms["corpo"]):
            score -= 1.0
        return score

    def _exclamation_allowed_by_history(self, voice_key: str) -> bool:
        history = self._prune_voice_output_history(voice_key)
        if any(
            item.get("used_exclamation")
            and (self._prosody_output_index - int(item.get("index") or 0)) <= self._exclamation_cooldown_outputs
            for item in history
        ):
            return False
        recent_window = [
            item for item in history
            if (self._prosody_output_index - int(item.get("index") or 0)) < self._exclamation_window_outputs
        ]
        used_recently = sum(1 for item in recent_window if item.get("used_exclamation"))
        return used_recently < self._max_exclamatory_in_window

    def _record_voice_output(
        self,
        voice_key: str,
        text: str,
        used_exclamation: bool,
        heat_level: int,
        signal_score: float,
    ) -> None:
        history = self._prune_voice_output_history(voice_key)
        history.append({
            "index": self._prosody_output_index,
            "text": text,
            "norm": self._normalize_similarity_text(text),
            "used_exclamation": used_exclamation,
            "heat_level": heat_level,
            "signal_score": signal_score,
            "ts": time.time(),
        })
        self._voice_output_history[voice_key] = history[-16:]
        self._prosody_output_index += 1

    def _log_prosody_decision(
        self,
        voice_key: str,
        original_text: str,
        result: ProsodyTransformResult,
        signal_score: float,
    ) -> None:
        entry = {
            "plugin": voice_key,
            "heat_level": result.heat_level,
            "original_text": original_text,
            "transformed_text": result.text,
            "used_exclamation": result.used_exclamation,
            "sentence_count_before": result.sentence_count_before,
            "sentence_count_after": result.sentence_count_after,
            "state_delta": round(signal_score, 3),
            "similarity_blocked": result.similarity_blocked,
        }
        self._prosody_logs.append(entry)
        self._prosody_logs = self._prosody_logs[-self._prosody_log_limit:]
        if (
            result.used_exclamation
            or result.similarity_blocked
            or result.sentence_count_after != result.sentence_count_before
        ):
            self._log(
                "nc_chronicles",
                f"prosody[{voice_key}] heat={result.heat_level} excl={int(result.used_exclamation)} "
                f"split={result.sentence_count_before}->{result.sentence_count_after} "
                f"sim_block={int(result.similarity_blocked)}",
            )

    def _apply_prosody_profile(
        self,
        text: str,
        voice_key: str,
        heat_level: Optional[int] = None,
        signal_score: Optional[float] = None,
    ) -> ProsodyTransformResult:
        clean = re.sub(r"\s+", " ", text.replace("\n", " ").strip())
        if not clean:
            return ProsodyTransformResult("", False, False, 0, 0, 0)

        resolved_heat, resolved_signal = self._resolve_segment_heat(heat_level, signal_score)
        profile = self._build_prosody_profile(resolved_heat, voice_key, resolved_signal)
        history = self._prune_voice_output_history(voice_key)
        norm = self._normalize_similarity_text(clean)
        similarity = max(
            (
                difflib.SequenceMatcher(None, norm, str(item.get("norm") or ""), autojunk=False).ratio()
                for item in history
                if item.get("norm")
            ),
            default=0.0,
        )
        similarity_blocked = similarity >= self._prosody_similarity_threshold
        if similarity_blocked:
            profile.allow_exclamation = False
            profile.exclamation_budget = 0
            profile.prefer_fragment_beats = False
            profile.prefer_ellipses = False

        clean = clean.replace("!", ".")
        if profile.heat_level >= 3 or not profile.prefer_ellipses:
            clean = re.sub(r"\.{3,}", ".", clean)

        before = self._split_sentences(clean)
        sentence_count_before = len(before) or 1

        reshaped: List[str] = []
        for sentence in before or [clean]:
            if profile.prefer_fragment_beats and profile.heat_level >= 4:
                fragments = self._fragmentize_sentence(sentence)
                if len(fragments) > 1:
                    reshaped.extend(fragments)
                    continue
            if len(re.sub(r"[.!?]+$", "", sentence).split()) > profile.max_clause_length:
                reshaped.extend(self._split_long_sentence(sentence, profile.max_clause_length))
            else:
                terminal = self._sentence_terminal(sentence)
                if terminal not in ("?", "!", "..."):
                    terminal = "."
                reshaped.append(self._finish_sentence(sentence, terminal))

        reshaped = [sentence for sentence in reshaped if sentence]
        reshaped = self._apply_low_heat_ellipsis(reshaped, profile)

        used_exclamation = False
        if profile.allow_exclamation and profile.exclamation_budget > 0 and self._exclamation_allowed_by_history(voice_key):
            candidate_thresholds = {
                "host": 1.1,
                "fixer": 0.9,
                "netrunner": 1.2,
                "ripperdoc": 1.3,
                "corpo": 1.7,
            }
            scores = [
                (idx, self._score_exclamation_candidate(sentence, voice_key))
                for idx, sentence in enumerate(reshaped)
            ]
            scores.sort(key=lambda item: item[1], reverse=True)
            for idx, score in scores[: profile.exclamation_budget]:
                if score < candidate_thresholds.get(voice_key, 1.0):
                    continue
                reshaped[idx] = self._finish_sentence(reshaped[idx], "!")
                used_exclamation = True
        else:
            reshaped = [
                self._finish_sentence(
                    sentence,
                    "?" if sentence.strip().endswith("?") else ("..." if sentence.strip().endswith("...") else "."),
                )
                for sentence in reshaped
            ]

        transformed = " ".join(sentence.strip() for sentence in reshaped if sentence.strip())
        transformed = re.sub(r"\s+([,.!?])", r"\1", transformed).strip()
        sentence_count_after = len(self._split_sentences(transformed)) or 1

        return ProsodyTransformResult(
            text=transformed,
            used_exclamation=used_exclamation,
            similarity_blocked=similarity_blocked,
            sentence_count_before=sentence_count_before,
            sentence_count_after=sentence_count_after,
            heat_level=resolved_heat,
        )

    def _push_segment(
        self,
        text: str,
        voice: str = "host",
        priority: int = 75,
        heat_level: Optional[int] = None,
        signal_score: Optional[float] = None,
        source: str = "nc_chronicles",
        title: str = "Night City Chronicles",
    ) -> None:
        """Enqueue a narration segment directly to the station DB (same as iracing_meta)."""
        if not text or not text.strip():
            return
        original_text = text.strip()
        voice_key = self._voice_keys.get(voice, "host")
        resolved_heat, resolved_signal = self._resolve_segment_heat(heat_level, signal_score)
        transformed = self._apply_prosody_profile(
            text=original_text,
            voice_key=voice_key,
            heat_level=resolved_heat,
            signal_score=resolved_signal,
        )
        text = transformed.text.strip()
        if not text:
            return
        self._log_prosody_decision(voice_key, original_text, transformed, resolved_signal)

        # ── Deduplication layer 1: recency similarity guard ──────────────────
        # Expire old entries from the rolling buffer
        now_t = time.time()
        self._recent_bodies = [
            (ts, body) for ts, body in self._recent_bodies
            if now_t - ts < self._dedup_window_sec
        ]
        # Normalise: lowercase, collapse whitespace
        norm = " ".join(text.lower().split())
        for _, prev_norm in self._recent_bodies:
            ratio = difflib.SequenceMatcher(None, norm, prev_norm, autojunk=False).ratio()
            if ratio >= self._dedup_similarity_threshold:
                self._log("nc_chronicles", f"dedup: skipped near-duplicate (ratio={ratio:.2f})")
                return
        if self._motif_cooldown_blocks(norm):
            return
        self._recent_bodies.append((now_t, norm))

        # ── Deduplication layer 2: content-hash id (DB INSERT OR IGNORE) ─────
        # id is deterministic from text content so the DB blocks exact dupes permanently
        sha1_fn = self._ctx.get("sha1")
        content_key = f"nc-body-{norm[:128]}"
        seg_id  = sha1_fn(content_key) if sha1_fn else content_key
        # post_id uses voice + text so same text in different voices stays distinct
        post_id = sha1_fn(f"nc-{voice_key}-{norm[:128]}") if sha1_fn else seg_id

        seg = {
            "id":         seg_id,
            "post_id":    post_id,
            "source":     source,
            "event_type": "narrate",
            "title":      title,
            "body":       text,
            "priority":   float(priority),
            "lead_voice": voice_key,
            "heat_level": resolved_heat,
            "signal_score": resolved_signal,
            "script": [
                {"type": "speech", "voice_id": voice_key, "text": text, "speaker": voice_key}
            ],
        }

        db_enqueue_fn = self._ctx.get("db_enqueue_segment")
        db_connect_fn = self._ctx.get("db_connect")
        if db_enqueue_fn and db_connect_fn:
            try:
                conn = db_connect_fn()
                inserted = db_enqueue_fn(conn, seg)
                if not inserted:
                    self._log("nc_chronicles", "dedup: DB blocked exact duplicate (same id)")
                    return
                self._record_voice_output(
                    voice_key=voice_key,
                    text=text,
                    used_exclamation=transformed.used_exclamation,
                    heat_level=resolved_heat,
                    signal_score=resolved_signal,
                )
                return
            except Exception as exc:
                self._log("nc_chronicles", f"db_enqueue error: {exc}")

        # Fallback: emit_candidate so producer can pick it up
        emit = self._ctx.get("emit_candidate")
        if emit:
            seg["_literal"] = True
            try:
                emit(seg)
                self._record_voice_output(
                    voice_key=voice_key,
                    text=text,
                    used_exclamation=transformed.used_exclamation,
                    heat_level=resolved_heat,
                    signal_score=resolved_signal,
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def get_meta_plugin() -> NightCityChroniclesMeta:
    return NightCityChroniclesMeta()
