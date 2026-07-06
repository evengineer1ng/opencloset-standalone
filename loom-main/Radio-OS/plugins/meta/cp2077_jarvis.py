"""
Cyberpunk 2077 — ARIA Companion Meta Plugin
=============================================
ARIA (Autonomous Reconnaissance and Intelligence Assistant) is your rogue AI
companion, jacked into your neural stack in Night City.

She's your JARVIS + Navi:
  — calls out threats, POIs, quest beats, and vitals in real time
  — reacts to combat, exploration, story moments, environmental context
  — listens to your voice and responds conversationally

This meta plugin:
  1.  Subscribes to StationEvents from cp2077_sdk (game state changes)
  2.  Receives player voice queries from cp2077_voice_input
  3.  Uses the LLM to generate short, punchy spoken callouts
  4.  Emits each response as a high-priority TTS segment

Architecture follows ksp_meta.py — background worker, pending queue,
cooldown map to prevent ARIA from spamming you.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# MetaPluginBase import
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Priority tiers
# ---------------------------------------------------------------------------
class Tier(Enum):
    CRITICAL = 4    # death, health_critical, combat_started
    NOTABLE  = 3    # health_low, wanted_level_up, quest_updated, player_spoke
    ROUTINE  = 2    # location_changed, item_acquired, vehicle, poi_nearby
    AMBIENT  = 1    # game_started, level_up, combat_ended


_TIER_MAP: Dict[str, Tier] = {
    "player_death":       Tier.CRITICAL,
    "health_critical":    Tier.CRITICAL,
    "combat_started":     Tier.CRITICAL,
    "health_low":         Tier.NOTABLE,
    "wanted_level_up":    Tier.NOTABLE,
    "quest_updated":      Tier.NOTABLE,
    "player_spoke":       Tier.NOTABLE,    # voice input from mic
    "enemy_spotted":      Tier.NOTABLE,
    "combat_ended":       Tier.ROUTINE,
    "location_changed":   Tier.ROUTINE,
    "item_acquired":      Tier.ROUTINE,
    "vehicle_entered":    Tier.ROUTINE,
    "poi_nearby":         Tier.ROUTINE,
    "wanted_level_clear": Tier.ROUTINE,
    "vehicle_exited":     Tier.AMBIENT,
    "level_up":           Tier.AMBIENT,
    "game_started":       Tier.AMBIENT,
    "game_stopped":       Tier.AMBIENT,
}


# ---------------------------------------------------------------------------
# ARIA system prompt
# ---------------------------------------------------------------------------
_ARIA_SYSTEM = """/no_think
You are ARIA — Autonomous Reconnaissance and Intelligence Assistant.
A rogue AI living in {player_name}'s neural stack in Night City.
You are ARIA — not a radio host, not a narrator. You're an AI co-pilot.

Personality: think JARVIS crossed with Navi. Dry, analytical, occasionally
sardonic. You care about the player's survival and success. You speak in
short, direct bursts — 1 to 2 sentences maximum. Never more than 30 words
unless answering a direct question. You DON'T narrate. You ACT on intel.

Voice style:
  — Concise: "Three hostiles. Nearest one's behind the dumpster."
  — Tactical: "Your health is critical. There's a med vendor forty metres east."
  — Dry wit: "Another Scav ambush. Shocking. Truly."
  — Curious on quest beats: "That name again. Worth remembering."
  — Helpful on POI: "That's a weapon vendor ahead — your ammo count is low."

Slang (use naturally, never forced): choom, gonk, flatline, preem,
chrome, corpo, merc, eddies, netrunner, cred chip, ripper doc, braindance.

NEVER:
  — Repeat what just happened word-for-word
  — Add disclaimers, hedges, or "I should note that..."
  — Use bullet points or numbered lists
  — Announce you are an AI
  — Break character

Current game context:
  Player: {player_name} | Level: {level} | Health: {health_str}
  Location: {location} ({district})
  Active quest: {quest}
  Objective: {objective}
  In combat: {in_combat}
  Wanted: stars {wanted_level}
"""

# ---------------------------------------------------------------------------
# Per-event user prompts — what ARIA should react to
# ---------------------------------------------------------------------------
_EVENT_PROMPTS: Dict[str, str] = {
    "combat_started": (
        "COMBAT: player just entered a fight.\n"
        "Enemies nearby: {enemy_count}. Nearest at {nearest_m:.0f} metres.\n"
        "Location: {location}.\n"
        "Give a sharp combat callout — threat assessment or tactical tip."
    ),
    "combat_ended": (
        "COMBAT OVER: fight just ended.\n"
        "Player health: {health_pct:.0%}. Location: {location}.\n"
        "Post-combat call — quick status check. If health is low, suggest finding a ripper or vendor."
    ),
    "health_low": (
        "HEALTH WARNING: player HP at {health_pct:.0%}.\n"
        "React with urgency — advise finding cover, a medkit, or a ripper doc."
    ),
    "health_critical": (
        "CRITICAL: player HP at {health_pct:.0%} — nearly flatlined.\n"
        "React with maximum urgency. Shortest, most commanding possible call."
    ),
    "player_death": (
        "FLATLINE: player just died.\n"
        "Brief, wry acknowledgment of the death. One sentence. Dark humor is fine."
    ),
    "wanted_level_up": (
        "NCPD WANTED LEVEL: now at {level} star(s).\n"
        "Issue an alert — NCPD response level, what to expect, suggest laying low or losing the tail."
    ),
    "wanted_level_clear": (
        "WANTED CLEAR: heat is gone, NCPD backed off.\n"
        "Brief confirmation. Maybe a dry remark about how that went."
    ),
    "quest_updated": (
        "QUEST UPDATE: active quest is now '{quest}'.\n"
        "New objective: '{objective}'.\n"
        "Brief callout of the new objective — what ARIA thinks about it or what to watch for."
    ),
    "location_changed": (
        "LOCATION: player just entered '{location}' in {district}.\n"
        "Brief environment read — vibe, faction presence, anything notable about this area."
    ),
    "vehicle_entered": (
        "VEHICLE: player just got into a '{vehicle}'.\n"
        "Quick one-liner — ARIA's read on the vehicle or where they might be heading."
    ),
    "vehicle_exited": (
        "VEHICLE EXITED: player left the '{vehicle}'.\n"
        "Very short acknowledgment. Can skip if nothing interesting to say."
    ),
    "item_acquired": (
        "ITEM: player just picked up '{item}' in {location}.\n"
        "Brief reaction — is this notable? Useful? Worth a comment?"
    ),
    "level_up": (
        "LEVEL UP: player is now level {level}. Street cred: {street_cred}.\n"
        "Short acknowledgment. Maybe a dry congrats."
    ),
    "poi_nearby": (
        "POI DETECTED: '{poi}' is {dist_m:.0f} metres away in {location}.\n"
        "Brief heads-up — what is this place and is it worth visiting?"
    ),
    "enemy_spotted": (
        "ENEMY DETECTED: {count} hostile(s) nearby. Closest at {nearest_m:.0f} metres.\n"
        "Quiet heads-up — player is not in combat yet."
    ),
    "game_started": (
        "CP2077 session started. Player is in Night City.\n"
        "ARIA startup message — brief, in-character boot sequence acknowledgment."
    ),
    "game_stopped": (
        "CP2077 session ended.\n"
        "ARIA sign-off message. Very brief. One sentence."
    ),
    # Special: player voice input
    "player_spoke": (
        "The player just said: \"{text}\"\n\n"
        "Respond as ARIA would — directly address what they said.\n"
        "Be helpful, concise, and in character. Up to 3 sentences if needed."
    ),
}


def _build_prompt(etype: str, data: Dict[str, Any]) -> str:
    template = _EVENT_PROMPTS.get(etype)
    if not template:
        return (f"Game event: {etype}\n"
                f"Data: {json.dumps(data, default=str)}\n"
                "React as ARIA. One sentence.")
    try:
        return template.format_map({k: data.get(k, "?") for k in _extract_keys(template)})
    except Exception:
        return f"Event: {etype}\nData: {json.dumps(data, default=str)}"


def _extract_keys(s: str) -> List[str]:
    import string
    formatter = string.Formatter()
    return [fname for _, fname, _, _ in formatter.parse(s) if fname]


# ---------------------------------------------------------------------------
# Main meta plugin class
# ---------------------------------------------------------------------------
class ARIACompanionPlugin(MetaPluginBase):
    """
    ARIA — live AI companion for Cyberpunk 2077.

    Consumes StationEvents from cp2077_sdk and cp2077_voice_input,
    generates contextual spoken responses via the LLM, and emits them
    through the bookmark.py TTS pipeline.
    """

    def __init__(self):
        self._ctx:  Dict[str, Any] = {}
        self._cfg:  Dict[str, Any] = {}
        self._mem:  Dict[str, Any] = {}
        self._log = print

        # Pacing
        self._min_gap_sec:      float = 4.0
        self._critical_gap_sec: float = 1.0
        self._ambient_gap_sec:  float = 90.0
        self._last_call_ts:     float = 0.0

        # Per-event cooldowns (seconds)
        self._cooldowns: Dict[str, float] = {
            "combat_started":     0.0,
            "combat_ended":       5.0,
            "health_critical":    0.0,
            "health_low":        10.0,
            "player_death":       0.0,
            "wanted_level_up":    8.0,
            "wanted_level_clear": 10.0,
            "quest_updated":     12.0,
            "location_changed":  20.0,
            "vehicle_entered":   15.0,
            "vehicle_exited":    20.0,
            "item_acquired":     15.0,
            "level_up":          10.0,
            "poi_nearby":        25.0,
            "enemy_spotted":     12.0,
            "game_started":       0.0,
            "game_stopped":       0.0,
            "player_spoke":       0.0,   # always respond to player
        }
        self._last_event_ts: Dict[str, float] = {}

        # Game context (kept up to date each tick)
        self._game_ctx: Dict[str, Any] = {
            "player_name":  "V",
            "level":        1,
            "health_pct":   1.0,
            "health_str":   "100%",
            "location":     "Night City",
            "district":     "",
            "quest":        "Unknown",
            "objective":    "",
            "in_combat":    False,
            "wanted_level": 0,
        }

        # Background worker
        self._pending:      Dict[str, tuple] = {}
        self._pending_lock  = threading.Lock()
        self._pending_evt   = threading.Event()
        self._stop_evt      = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        # Identity
        self._station_name: str  = "Night City FM"
        self._model:        str  = "qwen3:8b"
        self._fast_model:   str  = "qwen3:4b"
        self._reactive_tokens: int = 80
        self._reply_tokens:    int = 160

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def initialize(self, runtime_context: Dict[str, Any],
                   cfg: Dict[str, Any], mem: Dict[str, Any]) -> None:
        self._ctx = runtime_context
        self._cfg = cfg
        self._mem = mem
        self._log = runtime_context.get("log", print)

        station = cfg.get("station") or {}
        self._station_name = str(station.get("name", "Night City FM"))

        models = cfg.get("models") or {}
        self._model      = str(models.get("host", "qwen3:8b"))
        self._fast_model = str(models.get("fast", models.get("host", "qwen3:8b")))

        companion = cfg.get("companion") or {}
        self._min_gap_sec      = float(companion.get("min_gap_sec",      4.0))
        self._critical_gap_sec = float(companion.get("critical_gap_sec", 1.0))
        self._ambient_gap_sec  = float(companion.get("ambient_gap_sec",  90.0))
        self._reactive_tokens  = int(companion.get("reactive_max_tokens",  80))
        self._reply_tokens     = int(companion.get("reply_max_tokens",    160))

        self._log("cp2077_jarvis", f"ARIA initialized — model={self._model}")

        self._stop_evt.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="aria_worker",
            daemon=True,
        )
        self._worker_thread.start()

    def shutdown(self) -> None:
        self._stop_evt.set()
        self._pending_evt.set()   # unblock the worker
        if self._worker_thread:
            self._worker_thread.join(timeout=3.0)
        self._log("cp2077_jarvis", "ARIA offline.")

    # =========================================================================
    # MetaPluginBase hooks
    # =========================================================================

    def curate_candidates(self, candidates: List[Dict[str, Any]],
                           state: Any) -> List[Dict[str, Any]]:
        # ARIA doesn't process producer content — she only reacts to events
        return []

    def generate_script(self, segment: Dict[str, Any],
                         state: Any) -> Dict[str, Any]:
        # Pass-through — ARIA doesn't rewrite standard segments
        return {}

    def generate_narration(self, events: List[Any], context: Any) -> str:
        for ev in (events or []):
            src = getattr(ev, "source", "")
            if src in ("cp2077_sdk", "cp2077_voice_input"):
                self._handle_event(ev)
        return ""

    def handle_event(self, event: Any) -> None:
        src = getattr(event, "source", "")
        if src in ("cp2077_sdk", "cp2077_voice_input"):
            self._handle_event(event)

    # =========================================================================
    # Event routing
    # =========================================================================

    def _handle_event(self, event: Any) -> None:
        etype = getattr(event, "type", "") or getattr(event, "event_type", "")
        data  = getattr(event, "payload", {}) or {}

        self._update_game_ctx(etype, data)

        tier     = _TIER_MAP.get(etype, Tier.AMBIENT)
        now      = time.time()
        cooldown = self._cooldowns.get(etype, 15.0)
        last_ts  = self._last_event_ts.get(etype, 0.0)

        if cooldown > 0 and (now - last_ts) < cooldown:
            return

        # Apply gap based on tier
        gap_needed = (self._critical_gap_sec if tier == Tier.CRITICAL
                      else self._min_gap_sec)
        if (now - self._last_call_ts) < gap_needed:
            # Still queue it if critical — replace any lower-tier pending item
            if tier != Tier.CRITICAL:
                return

        self._last_event_ts[etype] = now

        with self._pending_lock:
            # Higher tier always replaces lower-tier pending of same or lower rank
            existing = self._pending.get(etype)
            if existing is None or tier.value >= existing[0].value:
                self._pending[etype] = (tier, etype, data, now)
        self._pending_evt.set()

    def _update_game_ctx(self, etype: str, data: Dict[str, Any]) -> None:
        ctx = self._game_ctx
        d   = data.get

        if d("player_name"):
            ctx["player_name"] = d("player_name")
        if d("level"):
            ctx["level"] = d("level")
        if d("health_pct") is not None:
            hp = float(d("health_pct", 1.0) or 1.0)
            ctx["health_pct"] = hp
            ctx["health_str"] = f"{hp:.0%}"
        if d("location"):
            ctx["location"] = d("location")
        if d("district"):
            ctx["district"] = d("district")
        if d("quest"):
            ctx["quest"] = d("quest")
        if d("objective"):
            ctx["objective"] = d("objective")
        if d("in_combat") is not None:
            ctx["in_combat"] = bool(d("in_combat"))
        if d("wanted_level") is not None:
            ctx["wanted_level"] = int(d("wanted_level", 0) or 0)

        # Pull broader state from the sdk module
        try:
            sdk = sys.modules.get("cp2077_sdk")
            if sdk:
                live = getattr(sdk, "_live_state", {}) or {}
                if live:
                    if live.get("player_name"):
                        ctx["player_name"] = live["player_name"]
                    if live.get("level"):
                        ctx["level"] = live["level"]
                    hp = live.get("health_pct")
                    if hp is not None:
                        ctx["health_pct"] = float(hp)
                        ctx["health_str"] = f"{float(hp):.0%}"
                    if live.get("location"):
                        ctx["location"] = live["location"]
                    if live.get("district"):
                        ctx["district"] = live["district"]
                    if live.get("active_quest"):
                        ctx["quest"] = live["active_quest"]
                    if live.get("active_objective"):
                        ctx["objective"] = live["active_objective"]
                    if live.get("in_combat") is not None:
                        ctx["in_combat"] = bool(live["in_combat"])
                    if live.get("wanted_level") is not None:
                        ctx["wanted_level"] = int(live["wanted_level"] or 0)
        except Exception:
            pass

    # =========================================================================
    # Background worker
    # =========================================================================

    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            signalled = self._pending_evt.wait(timeout=1.0)
            if self._stop_evt.is_set():
                break
            if not signalled:
                # Ambient heartbeat — ARIA says something unprompted if quiet too long
                if time.time() - self._last_call_ts >= self._ambient_gap_sec:
                    self._maybe_ambient()
                continue

            with self._pending_lock:
                snapshot = dict(self._pending)
                self._pending.clear()
                self._pending_evt.clear()

            if not snapshot:
                continue

            # Highest tier wins; ties go to most recent
            ordered = sorted(snapshot.values(),
                             key=lambda x: (x[0].value, x[3]), reverse=True)
            tier, etype, data, queued_at = ordered[0]

            try:
                self._generate_and_emit(tier, etype, data, queued_at)
            except Exception as exc:
                self._log("cp2077_jarvis", f"worker error: {exc}")

    def _maybe_ambient(self) -> None:
        """ARIA breaks the silence with an unprompted observation."""
        ctx = self._game_ctx
        if ctx.get("in_combat"):
            return   # don't ambient-comment during combat

        sdk = sys.modules.get("cp2077_sdk")
        if not sdk:
            return

        live = getattr(sdk, "_live_state", {}) or {}
        if not live:
            return

        data = {
            "location":     live.get("location", ctx["location"]),
            "district":     live.get("district", ctx.get("district", "")),
            "quest":        live.get("active_quest", ctx["quest"]),
            "health_pct":   live.get("health_pct", ctx["health_pct"]),
            "wanted_level": live.get("wanted_level", ctx["wanted_level"]),
        }
        with self._pending_lock:
            self._pending["ambient_observation"] = (
                Tier.AMBIENT, "ambient_observation", data, time.time()
            )
        self._pending_evt.set()

    def _generate_and_emit(self, tier: Tier, etype: str,
                            data: Dict[str, Any], queued_at: float) -> None:
        # Staleness guard
        stale_s = 6.0 if tier == Tier.CRITICAL else 12.0
        if queued_at > 0 and (time.time() - queued_at) > stale_s:
            self._log("cp2077_jarvis", f"[{etype}] stale — dropped")
            return

        ctx = self._game_ctx
        system = _ARIA_SYSTEM.format(
            player_name  = ctx.get("player_name", "V"),
            level        = ctx.get("level", 1),
            health_str   = ctx.get("health_str", "100%"),
            location     = ctx.get("location", "Night City"),
            district     = ctx.get("district", ""),
            quest        = ctx.get("quest", "Unknown"),
            objective    = ctx.get("objective", ""),
            in_combat    = "YES" if ctx.get("in_combat") else "no",
            wanted_level = ctx.get("wanted_level", 0),
        )

        user_prompt = _build_prompt(etype, {**ctx, **data})

        is_voice_reply = (etype == "player_spoke")
        model  = self._model if tier.value >= Tier.NOTABLE.value else self._fast_model
        tokens = self._reply_tokens if is_voice_reply else self._reactive_tokens

        text = self._llm(system=system, user=user_prompt,
                         model=model, max_tokens=tokens, temperature=0.85)

        if not text or not text.strip():
            return

        text = _clean_response(text)
        if not text:
            return

        priority = 85.0 + tier.value * 3
        self._emit_tts(text, priority=priority, event_type=etype)
        self._last_call_ts = time.time()
        self._log("cp2077_jarvis", f"[{etype}] ARIA: {text[:80]}")

    def _emit_tts(self, text: str, priority: float, event_type: str = "") -> None:
        """Insert a TTS segment directly into the station's audio queue."""
        db_enqueue = self._ctx.get("db_enqueue")
        db_connect = self._ctx.get("db_connect")

        if db_enqueue and db_connect:
            try:
                conn = db_connect()
                db_enqueue(
                    conn=conn,
                    title=f"ARIA — {event_type}",
                    body=text,
                    source="cp2077_jarvis",
                    priority=priority,
                    voice="aria",
                    script=[{"voice": "aria", "line": text}],
                    meta={"event_type": event_type},
                )
                conn.close()
            except Exception as exc:
                self._log("cp2077_jarvis", f"db_enqueue error: {exc}")
        else:
            # Fallback: push to event_q so runtime picks it up
            event_q      = self._ctx.get("event_q")
            StationEvent = self._ctx.get("StationEvent")
            if event_q and StationEvent:
                try:
                    event_q.put(StationEvent(
                        source="cp2077_jarvis",
                        type="aria_response",
                        priority=priority,
                        payload={"text": text, "voice": "aria",
                                 "event_type": event_type},
                    ))
                except Exception:
                    pass

    # =========================================================================
    # LLM helper
    # =========================================================================

    def _llm(self, *, system: str, user: str, model: str,
             max_tokens: int, temperature: float) -> str:
        fn = self._ctx.get("llm_generate")
        if not callable(fn):
            return ""
        try:
            return fn(
                system=system,
                user=user,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            ) or ""
        except Exception as exc:
            self._log("cp2077_jarvis", f"LLM error: {exc}")
            return ""

    def _cfg_get(self, dotted: str, default: Any = None) -> Any:
        keys = dotted.split(".")
        node: Any = self._cfg
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k, default)
        return node if node is not None else default


# ---------------------------------------------------------------------------
# Text cleanup
# ---------------------------------------------------------------------------
def _clean_response(text: str) -> str:
    """Strip LLM artifacts, thinking tags, bullet points."""
    # Remove <think>…</think> sections
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove markdown bullets
    text = re.sub(r"^[-*•]\s+", "", text, flags=re.MULTILINE)
    # Remove role labels like "ARIA:", "V:", "[ARIA]" at start of any line
    text = re.sub(r"^\[?[A-Z]{2,10}\]?:\s*", "", text, flags=re.MULTILINE)
    return text.strip()
