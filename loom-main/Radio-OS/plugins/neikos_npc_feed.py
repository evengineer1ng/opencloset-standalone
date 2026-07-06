"""
Neikos NPC Feed
================

A Radio OS feed plugin that watches the NKController for dialogue triggers
and emits segments for the Neikos meta plugin to voice.

Dialogue triggers come from:
  - Player initiating NPC dialogue (POST /api/dialogue)
  - Player talking to the Knower (POST /api/knower_dialogue)
  - Battle start / battle result events
  - Tier escalation events (tier change detected in island state)
  - Fragment discovery events (auto-read on discovery)

Each trigger becomes a candidate segment with a voice assignment.

PLUGIN_NAME must match the key in manifest.yaml feeds section.
"""

from __future__ import annotations

import time
import threading
from typing import Any, Dict, List, Optional

# ── Plugin metadata ──────────────────────────────────────────────────────────────
PLUGIN_NAME = "neikos_npc_feed"
PLUGIN_DESC = "Neikos NPC dialogue trigger feed"
IS_FEED = True

FEED_DEFAULTS: Dict[str, Any] = {
    "tick_sec": 5.0,
    "startup_delay": 3.0,
}


class NeikosNPCFeed:
    """
    Polls the NKController for pending dialogue events and emits them
    as Radio OS candidate segments.
    """

    def __init__(self):
        self._runtime: Dict[str, Any] = {}
        self._cfg: Dict[str, Any] = {}
        self._controller = None
        self._last_tier: int = 0
        self._last_tick: int = -1
        self._pending: List[Dict] = []
        self._lock = threading.Lock()

    # ── Radio OS feed interface ───────────────────────────────────────────────

    def initialize(self, runtime_context: Dict[str, Any], cfg: Dict[str, Any]) -> None:
        self._runtime = runtime_context
        self._cfg = cfg
        self._try_attach_controller()

    def get_candidates(self) -> List[Dict[str, Any]]:
        """Called by Radio OS scheduler. Returns pending dialogue segments."""
        self._try_attach_controller()
        self._poll_controller()

        with self._lock:
            candidates = list(self._pending)
            self._pending.clear()

        return candidates

    def shutdown(self) -> None:
        pass

    # ── Internal ─────────────────────────────────────────────────────────────

    def _try_attach_controller(self):
        if self._controller is not None:
            return
        try:
            # Try to get from plugin registry
            plugins = self._runtime.get("plugins", {})
            nk = plugins.get("neikos") or plugins.get("NeikosPlugin")
            if nk and hasattr(nk, "_controller"):
                self._controller = nk._controller
        except Exception:
            pass

        # Fallback: try direct import
        if self._controller is None:
            try:
                from plugins.neikos import NKController
                # NKController is a singleton if the plugin is running
                if hasattr(NKController, "_instance"):
                    self._controller = NKController._instance
            except Exception:
                pass

    def _poll_controller(self):
        """Check the controller for new events since last tick."""
        if self._controller is None:
            return

        try:
            state = self._controller._state
            if state is None:
                return

            # Detect tier escalation
            current_tier = getattr(state, "containment_tier", None)
            if current_tier is not None:
                tier_val = current_tier.value if hasattr(current_tier, "value") else int(current_tier)
                if tier_val > self._last_tier and self._last_tier > 0:
                    self._emit_tier_escalation(tier_val)
                if self._last_tier == 0:
                    self._last_tier = tier_val
                else:
                    self._last_tier = tier_val

            # Check event queue for new dialogue-relevant events
            events = getattr(state, "events", [])
            current_tick = getattr(state, "tick", 0)

            if current_tick <= self._last_tick:
                return
            self._last_tick = current_tick

            # Scan recent events for dialogue triggers
            for evt in events[-10:]:
                evt_type = getattr(evt, "event_type", None) or evt.get("event_type", "")
                evt_tick = getattr(evt, "tick", 0) or evt.get("tick", 0)
                if evt_tick <= self._last_tick - 10:
                    continue  # too old

                if evt_type == "fragment_discovered":
                    self._emit_fragment_read(evt)
                elif evt_type == "battle_result":
                    self._emit_battle_result(evt)
                elif evt_type == "knower_dialogue":
                    self._emit_knower_line(evt)
                elif evt_type == "anomaly_event":
                    self._emit_anomaly_narration(evt)

        except Exception as e:
            pass  # Feed must never crash the station

    def _emit_tier_escalation(self, new_tier: int):
        with self._lock:
            self._pending.append({
                "type": "tier_escalation",
                "new_tier": new_tier,
                "priority": 95,
                "voice": "host",
                "source": PLUGIN_NAME,
            })

    def _emit_fragment_read(self, evt):
        """Emit a fragment read segment when a fragment is discovered."""
        frag_data = getattr(evt, "data", {}) or evt.get("data", {})
        frag_type = frag_data.get("fragment_type", "RESEARCH_NOTE")
        frag_title = frag_data.get("title", "")
        frag_body = frag_data.get("body", "")
        if not frag_body:
            return
        with self._lock:
            self._pending.append({
                "type": "fragment_read",
                "fragment_type": frag_type,
                "title": frag_title,
                "body": frag_body,
                "priority": 70,
                "voice": "narrator",
                "source": PLUGIN_NAME,
            })

    def _emit_battle_result(self, evt):
        data = getattr(evt, "data", {}) or evt.get("data", {})
        with self._lock:
            self._pending.append({
                "type": "battle_result",
                "trainer_name": data.get("opponent_name", "Trainer"),
                "player_won": data.get("winner", "") == "player",
                "archetype": "TRAINER",
                "priority": 75,
                "voice": "trainer_0",
                "source": PLUGIN_NAME,
            })

    def _emit_knower_line(self, evt):
        data = getattr(evt, "data", {}) or evt.get("data", {})
        archetype = data.get("archetype", "ELDER")
        voice_map = {
            "ELDER": "knower_elder",
            "SCIENTIST": "knower_scientist",
            "REBEL": "knower_rebel",
            "CARTOGRAPHER": "knower_cartographer",
            "GHOST": "knower_ghost",
        }
        with self._lock:
            self._pending.append({
                "type": "npc_dialogue",
                "npc_name": data.get("knower_name", "The Knower"),
                "archetype": archetype,
                "trigger": f"knower_fragment_{data.get('fragment_index', 0)}",
                "context": data,
                "tier": data.get("tier", 1),
                "priority": 88,
                "voice": voice_map.get(archetype, "knower_elder"),
                "source": PLUGIN_NAME,
            })

    def _emit_anomaly_narration(self, evt):
        data = getattr(evt, "data", {}) or evt.get("data", {})
        with self._lock:
            self._pending.append({
                "type": "npc_dialogue",
                "npc_name": "Archivist",
                "archetype": "ARCHIVIST",
                "trigger": "anomaly_detected",
                "context": data,
                "tier": data.get("tier", 1),
                "priority": 60,
                "voice": "host",
                "source": PLUGIN_NAME,
            })


# ── Plugin factory ────────────────────────────────────────────────────────────────
def register_feed(runtime_context: Dict[str, Any], cfg: Dict[str, Any]) -> NeikosNPCFeed:
    feed = NeikosNPCFeed()
    feed.initialize(runtime_context, cfg)
    return feed
