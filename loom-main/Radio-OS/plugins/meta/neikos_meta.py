"""
Neikos Expedition — Meta Plugin
================================

The station brain for Neikos: Hundred Islands.

This meta plugin bridges the game engine (plugins/neikos/) with Radio OS's
NPC voice pipeline. It does NOT generate world state — the Cold Layer handles
that. This plugin handles:

  - Pulling NPC dialogue triggers from the game controller
  - Routing each trigger to the correct voice profile (archetype → voice)
  - Generating contextual dialogue via LLM (presentation layer only)
  - Feeding produced segments back into the Radio OS scheduler

Architecture:
  NKController (game)  →  neikos_npc_feed  →  neikos_meta  →  TTS → audio

NPC types voiced through this station:
  - Trainers (battle challenges, post-battle commentary)
  - Faction liaisons (recruitment, lore drops)
  - The Hidden Knower (conspiracy revelation, 5–8 fragments)
  - System/Archivist voice (tier escalation announcements, fragment reads)
"""

from __future__ import annotations

import json
import time
import threading
from typing import Any, Dict, List, Optional

try:
    from bookmark import MetaPluginBase
except ImportError:
    from abc import ABC, abstractmethod
    class MetaPluginBase(ABC):
        @abstractmethod
        def initialize(self, runtime_context, cfg, mem): pass
        @abstractmethod
        def curate_candidates(self, candidates, state): pass
        @abstractmethod
        def generate_script(self, segment, state): pass
        @abstractmethod
        def generate_narration(self, events, context): pass
        @abstractmethod
        def delegate_decision(self, available_actions, state, identity, focus): pass
        @abstractmethod
        def shutdown(self): pass

try:
    from context_engine import format_context_for_prompt
except ImportError:
    format_context_for_prompt = lambda d, t: str(d)


# ── Plugin identity ──────────────────────────────────────────────────────────────
PLUGIN_NAME = "neikos_meta"
PLUGIN_DESC = "Neikos Expedition station meta plugin — NPC voice orchestration"
IS_FEED = False


# ── Archetype → voice key mapping (matches manifest.yaml voices section) ─────────
ARCHETYPE_VOICE_MAP: Dict[str, str] = {
    "ELDER":        "knower_elder",
    "SCIENTIST":    "knower_scientist",
    "REBEL":        "knower_rebel",
    "CARTOGRAPHER": "knower_cartographer",
    "GHOST":        "knower_ghost",
    "TRAINER":      "trainer_0",  # rotated by trainer_index % 5
    "FACTION_LEAGUE":      "faction_league",
    "FACTION_RESEARCH":    "faction_research",
    "FACTION_FERAL":       "faction_feral",
    "FACTION_CARTOGRAPHER": "faction_cartographer",
    "ARCHIVIST":    "host",
    "DEFAULT":      "host",
}

TIER_ESCALATION_LINES: Dict[int, str] = {
    2: "Anomalous behavior flagged. This island is under review.",
    3: "Administrative review initiated. Behavioral patterns logged.",
    4: "Containment protocols active. Subject monitoring elevated.",
    5: "Full disclosure mode engaged. You now know what this is.",
}


class NeikosMetaPlugin(MetaPluginBase):
    """
    Meta plugin for Neikos Expedition station.
    Orchestrates NPC voice generation and fragment narration.
    """

    def __init__(self):
        self.ctx: Dict[str, Any] = {}
        self.cfg: Dict[str, Any] = {}
        self.mem: Dict[str, Any] = {}
        self.log_func = print
        self._nk_controller = None
        self._last_tier: int = 1
        self._shutdown = False

    # ── MetaPluginBase interface ──────────────────────────────────────────────

    def initialize(self, runtime_context: Dict[str, Any], cfg: Dict[str, Any], mem: Dict[str, Any]) -> None:
        self.ctx = runtime_context
        self.cfg = cfg
        self.mem = mem
        self.log_func = runtime_context.get("log", print)
        self._try_attach_controller()
        self.log("neikos_meta", "NeikosMetaPlugin initialized")

    def curate_candidates(self, candidates: List[Any], state: Any) -> List[Any]:
        """Pass-through — neikos_npc_feed handles prioritization."""
        return candidates

    def generate_script(self, segment: Any, state: Any) -> Optional[str]:
        """Generate dialogue/narration for a segment via LLM."""
        if not segment:
            return None
        seg_type = getattr(segment, "type", None) or segment.get("type", "")

        if seg_type == "npc_dialogue":
            return self._generate_npc_dialogue(segment, state)
        elif seg_type == "fragment_read":
            return self._generate_fragment_read(segment, state)
        elif seg_type == "tier_escalation":
            return self._generate_tier_escalation(segment, state)
        elif seg_type == "battle_intro":
            return self._generate_battle_intro(segment, state)
        elif seg_type == "battle_result":
            return self._generate_battle_result(segment, state)
        return None

    def generate_narration(self, events: List[Any], context: Any) -> Optional[str]:
        """Generate ambient narration for a batch of events."""
        if not events:
            return None
        # Only narrate significant events — movement is silent
        significant = [e for e in events if getattr(e, "type", "") in (
            "anomaly_event", "fragment_discovered", "research_discovery",
            "gate_blocked", "memory_echo",
        )]
        if not significant:
            return None
        ctx_str = format_context_for_prompt(
            {"events": [str(e) for e in significant]},
            "neikos_narration"
        )
        prompt = self._build_narration_prompt(significant, context)
        try:
            return self._llm(prompt, max_tokens=80, temperature=0.6)
        except Exception as e:
            self.log("neikos_meta", f"Narration LLM error: {e}")
            return None

    def delegate_decision(self, available_actions: List[str], state: Any,
                          identity: str, focus: str) -> Optional[str]:
        """Delegate producer decisions. Not used in Neikos — game drives state."""
        return None

    def shutdown(self) -> None:
        self._shutdown = True
        self.log("neikos_meta", "NeikosMetaPlugin shut down")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _try_attach_controller(self):
        """Try to get the NKController from the plugin registry."""
        try:
            plugins = self.ctx.get("plugins", {})
            nk = plugins.get("neikos") or plugins.get("NeikosPlugin")
            if nk and hasattr(nk, "_controller"):
                self._nk_controller = nk._controller
                self.log("neikos_meta", "Attached to NKController")
        except Exception as e:
            self.log("neikos_meta", f"Could not attach to NKController: {e}")

    def _llm(self, prompt: str, max_tokens: int = 120, temperature: float = 0.65) -> str:
        gen = self.ctx.get("llm_generate")
        if not gen:
            return ""
        return gen(prompt, max_tokens=max_tokens, temperature=temperature)

    def _voice_for_archetype(self, archetype: str, trainer_index: int = 0) -> str:
        """Map archetype to manifest voice key."""
        if archetype == "TRAINER":
            return f"trainer_{trainer_index % 5}"
        return ARCHETYPE_VOICE_MAP.get(archetype, "host")

    def _voice_profile(self, archetype: str) -> Dict[str, Any]:
        """Get speed/pitch config for an archetype from manifest."""
        profiles = self.cfg.get("neikos", {}).get("voice_profiles", {})
        return profiles.get(archetype, profiles.get("DEFAULT", {"speed": 1.0, "pitch": 0}))

    # ── Dialogue generators ───────────────────────────────────────────────────

    def _generate_npc_dialogue(self, segment: Any, state: Any) -> Optional[str]:
        """Generate a line of NPC dialogue via LLM."""
        data = segment if isinstance(segment, dict) else vars(segment)
        npc_name    = data.get("npc_name", "NPC")
        archetype   = data.get("archetype", "DEFAULT")
        trigger     = data.get("trigger", "")           # 'greeting', 'fragment_N', 'battle_challenge'
        context_data = data.get("context", {})
        tier        = data.get("tier", 1)

        # Build a tight character prompt
        char_traits = {
            "ELDER":        "weary elder who has seen PROJECT HUNDRED in action; speaks slowly, carefully, avoids direct accusation",
            "SCIENTIST":    "field researcher who has documented anomalies; clinical, precise, unsettled beneath the surface",
            "REBEL":        "someone who tried to leave the island and couldn't; terse, doesn't waste words",
            "CARTOGRAPHER": "a Cartographer agent on island; deliberately vague, speaks in euphemisms about 'the project'",
            "GHOST":        "barely present, like a recording of a person; speaks in fragments and near-whispers",
            "TRAINER":      "competitive creature trainer; energetic, focused on the battle",
            "DEFAULT":      "neutral island inhabitant",
        }.get(archetype, "island resident")

        tier_note = ""
        if tier >= 4:
            tier_note = " The subject already knows much. You can be less careful."
        elif tier >= 3:
            tier_note = " The subject is asking questions. Be measured."

        prompt = (
            f"You are {npc_name}, a {char_traits}.{tier_note}\n"
            f"Situation: {trigger}\n"
            f"Context: {json.dumps(context_data, default=str)}\n\n"
            f"Write ONE line of in-character dialogue. "
            f"Maximum 30 words. No action descriptions. No quotes. "
            f"Speak as {npc_name}."
        )

        try:
            line = self._llm(prompt, max_tokens=60, temperature=0.7)
            return line.strip() if line else None
        except Exception as e:
            self.log("neikos_meta", f"NPC dialogue error: {e}")
            return None

    def _generate_fragment_read(self, segment: Any, state: Any) -> Optional[str]:
        """Return fragment body text for TTS — no LLM needed, text is in segment."""
        data = segment if isinstance(segment, dict) else vars(segment)
        body = data.get("body", "")
        if not body:
            return None
        # Strip [REDACTED] tokens for audio (they're visual-only)
        return body.replace("[REDACTED]", "... redacted ...").strip()

    def _generate_tier_escalation(self, segment: Any, state: Any) -> Optional[str]:
        """Return the tier escalation announcement line."""
        data = segment if isinstance(segment, dict) else vars(segment)
        new_tier = data.get("new_tier", 2)
        return TIER_ESCALATION_LINES.get(new_tier, f"Tier {new_tier} protocol active.")

    def _generate_battle_intro(self, segment: Any, state: Any) -> Optional[str]:
        """Generate a trainer battle challenge line."""
        data = segment if isinstance(segment, dict) else vars(segment)
        trainer_name   = data.get("trainer_name", "Trainer")
        archetype      = data.get("archetype", "TRAINER")
        trainer_index  = data.get("trainer_index", 0)

        prompt = (
            f"You are {trainer_name}, a creature trainer about to challenge someone to a battle.\n"
            f"Write ONE punchy battle challenge line. Under 20 words. No quotes."
        )
        try:
            return self._llm(prompt, max_tokens=40, temperature=0.75)
        except Exception:
            return f"{trainer_name} wants to battle!"

    def _generate_battle_result(self, segment: Any, state: Any) -> Optional[str]:
        """Generate a trainer's post-battle line."""
        data = segment if isinstance(segment, dict) else vars(segment)
        trainer_name = data.get("trainer_name", "Trainer")
        player_won   = data.get("player_won", True)

        if player_won:
            prompt = (
                f"You are {trainer_name}, a creature trainer who just lost a battle.\n"
                f"Write ONE short line — defeated, gracious or grudging. Under 20 words. No quotes."
            )
        else:
            prompt = (
                f"You are {trainer_name}, a creature trainer who just won a battle.\n"
                f"Write ONE short line — victorious, not gloating. Under 20 words. No quotes."
            )
        try:
            return self._llm(prompt, max_tokens=40, temperature=0.75)
        except Exception:
            return "Good battle." if player_won else "Better luck next time."

    def _build_narration_prompt(self, events: List[Any], context: Any) -> str:
        event_strs = "; ".join(str(e) for e in events[:3])
        return (
            f"You are the Archivist, an institutional voice monitoring this island.\n"
            f"Events just occurred: {event_strs}\n"
            f"Write ONE line of cold, precise observation. Under 20 words. No emotion. "
            f"Speak as if filing a report."
        )

    def log(self, channel: str, msg: str):
        if self.log_func:
            try:
                self.log_func(channel, msg)
            except Exception:
                print(f"[{channel}] {msg}")


# ── Plugin factory ───────────────────────────────────────────────────────────────
def create_meta_plugin() -> NeikosMetaPlugin:
    return NeikosMetaPlugin()
