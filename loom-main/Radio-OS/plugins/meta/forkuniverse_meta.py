"""
ForkUniverse — Meta Plugin
==========================

The station brain for a ForkUniverse station. It owns one authoritative
``UniverseState`` (the cold-layer simulation core), advances it on demand, and
collapses the resulting ``TruthDelta`` into a small set of spoken beats that the
Radio OS TTS pipeline can voice.

Architecture:

    forkuniverse engine (cold layer)
        -> forkuniverse_meta.process_input({"input_type": "tick"})  (this file)
        -> forkuniverse_feed routes segments to emit_candidate       (the feed)
        -> producer / host / tts_worker speak them

Design rules honored from the engine spec:

* The cold layer is the source of truth. This plugin interprets facts into
  beats; it never invents facts. Every beat carries the event/thread ids it came
  from in ``metadata`` so narration stays traceable.
* No hard LLM dependence. Beats are generated from deterministic templates over
  real engine state. An optional ``llm_generate`` pass can add texture when
  ``use_llm`` is set, but the truth always comes from the engine.
* The universe is persistent. The plugin loads a snapshot at startup if one
  exists and autosaves it, so a station resumes the same world across sessions
  (the "while you were gone" promise).

This plugin is self-contained: unlike a controller-backed station, it builds and
owns its own universe from the station config, so no separate game process is
required.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # Loadable inside Radio OS...
    from bookmark import MetaPluginBase
except ImportError:  # ...and standalone / under test.
    from abc import ABC, abstractmethod

    class MetaPluginBase(ABC):  # type: ignore[no-redef]
        @abstractmethod
        def initialize(self, runtime_context, cfg, mem): ...
        @abstractmethod
        def curate_candidates(self, candidates, state): ...
        @abstractmethod
        def generate_script(self, segment, state): ...
        @abstractmethod
        def generate_narration(self, events, context): ...
        @abstractmethod
        def delegate_decision(self, available_actions, state, identity, focus): ...
        @abstractmethod
        def shutdown(self): ...


from forkuniverse.compiler.compile import compile_universe
from forkuniverse.compiler.models import CreationRequest
from forkuniverse.engine.world_core import UniverseState


# ── Plugin identity ──────────────────────────────────────────────────────────
PLUGIN_NAME = "forkuniverse_meta"
PLUGIN_DESC = "ForkUniverse station meta plugin — advances a universe and narrates its truth"
IS_FEED = False


# ── Beat -> voice / priority / audio routing ─────────────────────────────────
# Voice keys must exist in the station manifest `voices:` block.
BEAT_VOICE: Dict[str, str] = {
    "cold_open": "host",
    "while_you_were_gone": "chronicler",
    "thread_resolution": "host",
    "breaking_development": "breaking",
    "open_thread": "narrator",
    "prediction_scorecard": "chronicler",
    "sign_off": "host",
}

BEAT_PRIORITY: Dict[str, float] = {
    "cold_open": 80.0,
    "while_you_were_gone": 88.0,
    "thread_resolution": 86.0,
    "breaking_development": 74.0,
    "open_thread": 58.0,
    "prediction_scorecard": 62.0,
    "sign_off": 70.0,
}

# Engine event families already carry audio signatures; beats map their own.
BEAT_AUDIO: Dict[str, str] = {
    "cold_open": "cold_open",
    "while_you_were_gone": "while_you_were_gone",
    "thread_resolution": "victory_release",
    "breaking_development": "stinger_reveal",
    "open_thread": "meanwhile_transition",
    "prediction_scorecard": "institutional_tension",
    "sign_off": "lonely_afterglow",
}

_RESOLUTION_PHRASE = {
    "affirmed": "and it came true",
    "broken": "and it fell apart",
    "faded": "and it quietly faded",
}


class ForkUniverseMetaPlugin(MetaPluginBase):
    """Station brain: owns a UniverseState, advances it, narrates the delta."""

    def __init__(self) -> None:
        self.ctx: Dict[str, Any] = {}
        self.cfg: Dict[str, Any] = {}
        self.mem: Dict[str, Any] = {}
        self.log_func = print
        self.state: Optional[UniverseState] = None
        self._fu_cfg: Dict[str, Any] = {}
        self._snapshot_path: Optional[Path] = None
        self._ticks_since_save = 0
        self._narration_ticks = 0
        self._last_spoken = ""

    # ── MetaPluginBase interface ─────────────────────────────────────────────

    def initialize(self, runtime_context: Dict[str, Any], cfg: Dict[str, Any], mem: Dict[str, Any]) -> None:
        self.ctx = runtime_context or {}
        self.cfg = cfg or {}
        self.mem = mem or {}
        self.log_func = self.ctx.get("log", print)
        self._fu_cfg = self.cfg.get("forkuniverse", {}) or {}
        self.state = self._load_or_compile_universe()
        self.log(
            PLUGIN_NAME,
            f"initialized universe '{self.state.universe_title}' "
            f"at tick {self.state.time.tick} ({len(self.state.characters)} characters)",
        )

    def curate_candidates(self, candidates: List[Any], state: Any) -> List[Any]:
        return candidates

    def generate_script(self, segment: Any, state: Any) -> Optional[str]:
        data = segment if isinstance(segment, dict) else getattr(segment, "__dict__", {})
        return data.get("text")

    def generate_narration(self, events: List[Any], context: Any) -> Optional[str]:
        if not events:
            return None
        return " ".join(str(getattr(e, "summary", e)) for e in events[:3])

    def delegate_decision(self, available_actions, state, identity, focus):
        return None

    def shutdown(self) -> None:
        self._save_snapshot()
        self.log(PLUGIN_NAME, "shut down (snapshot saved)")

    # ── The feed entry point ─────────────────────────────────────────────────

    def process_input(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Called by forkuniverse_feed. Returns a list of spoken segment dicts."""
        if self.state is None:
            return []
        kind = (event or {}).get("input_type", "tick")
        if kind == "session_start":
            return self._beats_session_start()
        if kind == "session_end":
            self._save_snapshot()
            return self._beats_session_end()
        return self._beats_tick(event or {})

    # ── Universe lifecycle ───────────────────────────────────────────────────

    def _load_or_compile_universe(self) -> UniverseState:
        snap = self._fu_cfg.get("snapshot_path")
        if snap:
            self._snapshot_path = self._resolve_path(snap)
            if self._snapshot_path.exists():
                try:
                    state = UniverseState.load_json(self._snapshot_path)
                    self.log(PLUGIN_NAME, f"resumed universe from {self._snapshot_path}")
                    return state
                except Exception as exc:  # corrupt snapshot -> recompile fresh
                    self.log(PLUGIN_NAME, f"snapshot load failed ({exc}); recompiling")
        return self._compile_from_cfg()

    def _compile_from_cfg(self) -> UniverseState:
        c = self._fu_cfg
        request = CreationRequest(
            universe_title=c.get("universe_title", "Fork Universe"),
            premise=c.get(
                "premise",
                "An ordinary place where love, debt, rumor, and grief reshape every day.",
            ),
            setting_kind=c.get("setting_kind", "modern_city"),
            time_period=c.get("time_period", "modern"),
            story_mode=c.get("story_mode", "continuous"),
            world_scale=c.get("world_scale", "district"),
            starting_population=int(c.get("starting_population", 24)),
            seed_mode=c.get("seed_mode", "custom"),
            custom_seed=c.get("seed", "forkuniverse-default"),
            ontology_domains=list(c.get("ontology_domains", ["love", "debt", "rumor", "grief"])),
        )
        state = UniverseState.from_compiled_package(compile_universe(request))
        self.log(PLUGIN_NAME, f"compiled fresh universe '{state.universe_title}'")
        return state

    def _resolve_path(self, raw: str) -> Path:
        path = Path(raw)
        if path.is_absolute():
            return path
        base = self.ctx.get("station_dir") or self.cfg.get("station_dir") or "."
        return Path(base) / path

    def _save_snapshot(self) -> None:
        if self.state is None or self._snapshot_path is None:
            return
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self.state.save_json(self._snapshot_path)
            self._ticks_since_save = 0
        except Exception as exc:
            self.log(PLUGIN_NAME, f"snapshot save failed: {exc}")

    # ── Advancement ──────────────────────────────────────────────────────────

    def _advance(self) -> Any:
        """Advance the universe and return the TruthDelta covering the step."""
        assert self.state is not None
        from_tick = self.state.time.tick
        if str(self._fu_cfg.get("advance_mode", "fixed")) == "elapsed":
            ratio = float(self.state.time.real_seconds_per_tick)
            elapsed = float(self._fu_cfg.get("elapsed_real_seconds", ratio))
            delta = self.state.compute_absence(elapsed)
        else:
            ticks = int(self._fu_cfg.get("world_ticks_per_narration_tick", 8))
            self.state.simulate_epoch(max(1, ticks))
            delta = self.state.emit_truth_delta(from_tick)

        self._narration_ticks += 1
        self._ticks_since_save += 1
        autosave_every = int(self._fu_cfg.get("autosave_every", 10))
        if self._snapshot_path is not None and self._ticks_since_save >= autosave_every:
            self._save_snapshot()
        return delta

    # ── Beat collapsing (the hot layer interprets; it never invents) ─────────

    def _beats_tick(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        delta = self._advance()
        max_segments = int(self._fu_cfg.get("max_segments_per_tick", 3))
        heat_floor = float(self._fu_cfg.get("heat_threshold", 0.4))
        beats: List[Dict[str, Any]] = []

        # Resolved threads are the strongest material — listeners came back for these.
        for thread in delta.resolved_threads:
            phrase = _RESOLUTION_PHRASE.get(thread.get("resolution_outcome"), "and it resolved")
            beats.append(
                self._beat(
                    "thread_resolution",
                    f"{thread.get('title')} — {phrase}.",
                    source_ids=[thread.get("thread_id")],
                    extra={"outcome": thread.get("resolution_outcome"), "heat": thread.get("heat")},
                    heat=float(thread.get("heat", 0.0)),
                )
            )

        # The current hottest open question = the breaking development / headline.
        hottest = next(
            (t for t in delta.thread_deltas if float(t.get("heat", 0.0)) >= heat_floor),
            None,
        )
        if hottest is not None:
            beats.append(
                self._beat(
                    "breaking_development",
                    f"Still developing: {hottest.get('title')}",
                    source_ids=[hottest.get("thread_id")],
                    extra={"heat": hottest.get("heat"), "urgency": hottest.get("urgency")},
                    heat=float(hottest.get("heat", 0.0)),
                )
            )

        # Prediction settlements: the world keeping score against itself.
        for pred in delta.settled_predictions[:2]:
            outcome = pred.get("resolution_outcome")
            line = (
                "The world saw that one coming."
                if outcome == "hit"
                else "The world was caught off guard."
            )
            beats.append(
                self._beat(
                    "prediction_scorecard",
                    line,
                    source_ids=[pred.get("prediction_id")],
                    extra={"outcome": outcome, "scorecard": delta.prediction_scorecard.get("calibration_score")},
                )
            )

        # New open questions, lowest priority, only if we have room.
        for thread in delta.opened_threads[:2]:
            beats.append(
                self._beat(
                    "open_thread",
                    f"A new question hangs over {self.state.universe_title}: {thread.get('title')}",
                    source_ids=[thread.get("thread_id")],
                )
            )

        beats.sort(key=lambda b: b["priority"], reverse=True)
        # Drop duplicate lines (pressure-spawned threads share templated titles)
        # and avoid repeating the exact headline we spoke last tick.
        deduped: List[Dict[str, Any]] = []
        seen = {self._last_spoken} if self._last_spoken else set()
        for beat in beats:
            if beat["text"] in seen:
                continue
            seen.add(beat["text"])
            deduped.append(beat)
        result = deduped[:max_segments]
        if result:
            self._last_spoken = result[0]["text"]
        return result

    def _beats_session_start(self) -> List[Dict[str, Any]]:
        assert self.state is not None
        title = self.state.universe_title
        tick = self.state.time.tick
        if tick > 0:
            text = (
                f"Welcome back to {title}. The world kept turning while you were away — "
                f"we are {tick} ticks deep, and there is news."
            )
            beat_type = "while_you_were_gone"
        else:
            text = (
                f"You are listening to {title}. A world compiled from a single premise, "
                f"now running on its own. Let us hear what it becomes."
            )
            beat_type = "cold_open"
        return [self._beat(beat_type, text, source_ids=[], extra={"tick": tick})]

    def _beats_session_end(self) -> List[Dict[str, Any]]:
        assert self.state is not None
        sc = self.state.predictions
        text = (
            f"That is {self.state.universe_title} for now. "
            f"{len(self.state.threads)} threads spun, "
            f"{sc.settled_hits + sc.settled_misses} predictions settled. "
            f"The world keeps going. We will be here when you return."
        )
        return [self._beat("sign_off", text, source_ids=[])]

    # ── Segment construction ─────────────────────────────────────────────────

    def _beat(
        self,
        beat_type: str,
        text: str,
        *,
        source_ids: List[Any],
        extra: Optional[Dict[str, Any]] = None,
        heat: float = 0.0,
    ) -> Dict[str, Any]:
        if self._fu_cfg.get("use_llm") and self.ctx.get("llm_generate"):
            text = self._llm_texture(beat_type, text) or text
        metadata = {
            "type": beat_type,
            "audio_signature": BEAT_AUDIO.get(beat_type, "meanwhile_transition"),
            "source_ids": [sid for sid in source_ids if sid],
            "universe_id": self.state.universe_id if self.state else "",
            "tick": self.state.time.tick if self.state else 0,
        }
        if extra:
            metadata.update(extra)
        return {
            "text": text,
            "voice": BEAT_VOICE.get(beat_type, "host"),
            "priority": BEAT_PRIORITY.get(beat_type, 50.0) + min(heat * 10.0, 10.0),
            "type": beat_type,
            "metadata": metadata,
        }

    def _llm_texture(self, beat_type: str, factual_line: str) -> Optional[str]:
        gen = self.ctx.get("llm_generate")
        if not gen:
            return None
        prompt = (
            "You are a radio narrator for a living simulated world. Rewrite the FACT below "
            "as one short, vivid spoken line. Do not add new facts. Under 30 words.\n"
            f"FACT: {factual_line}"
        )
        try:
            out = gen(prompt, max_tokens=60, temperature=0.6)
            return (out or "").strip() or None
        except Exception as exc:
            self.log(PLUGIN_NAME, f"llm texture error: {exc}")
            return None

    def log(self, channel: str, msg: str) -> None:
        try:
            self.log_func(channel, msg)
        except Exception:
            print(f"[{channel}] {msg}")


def create_meta_plugin() -> ForkUniverseMetaPlugin:
    return ForkUniverseMetaPlugin()
