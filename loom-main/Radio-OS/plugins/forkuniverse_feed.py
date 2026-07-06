"""
forkuniverse_feed.py — the station's CHECK-IN CLOCK for a ForkUniverse universe.

ForkUniverse is an on-demand truth calculator, not a daemon. A universe is deterministic data — a
seed + laws (see the station manifest's `forkuniverse:` block) — that can regenerate any tick ON
COMMAND. Nobody "drives" it; you ASK it. This feed is only the cadence at which Radio OS checks in:
each beat it asks the universe for the truth owed since last asked, and narrates the answer.

Ownership stays clean: the universe's laws + state live in ForkUniverse (the meta plugin holds a
handle and calls forkuniverse.engine); Radio OS owns only the asking and the playhead. Close
ForkUniverse Studio and the universe persists as data — the calculator regenerates it on call.
(Antenna model: ForkUniverse is a PULL source — the antenna calls the calculator instead of observing
a push daemon; elapsed-real-time → owed ticks is what gives Radio OS its world-continuity for free.)

Each check-in:
  1. ask ACTIVE_META_PLUGIN.process_input({"input_type": "tick"})   ("what's true now?")
  2. the meta plugin computes the owed truth delta and returns spoken beats
  3. route each beat as a candidate via runtime["emit_candidate"]
  4. producer -> host -> tts_worker speak them

It holds no universe state of its own — it is a pure clock. Mirrors the oracle_court_feed contract.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

PLUGIN_NAME = "forkuniverse_feed"
PLUGIN_DESC = "Check-in clock: asks a ForkUniverse universe for owed truth and narrates it"
IS_FEED = True

FEED_DEFAULTS = {
    "enabled": True,
    "tick_sec": 10.0,       # narration tick interval (seconds)
    "startup_delay": 6.0,   # wait for the meta plugin to initialize its universe
}
DEFAULT_FEED_CFG = FEED_DEFAULTS


def feed_worker(
    stop_event: threading.Event,
    mem: Dict[str, Any],
    payload: Dict[str, Any],
    runtime: Optional[Dict[str, Any]] = None,
) -> None:
    runtime = runtime or {}
    cfg = payload or {}
    log = runtime.get("log", lambda role, msg: print(f"[{role}] {msg}"))
    emit_candidate = runtime.get("emit_candidate")
    ui_q = runtime.get("ui_q")

    tick_sec = float(cfg.get("tick_sec", 10.0))
    startup_delay = float(cfg.get("startup_delay", 6.0))
    log(PLUGIN_NAME, f"starting (tick_sec={tick_sec}, startup_delay={startup_delay})")

    if stop_event.wait(startup_delay):
        return

    meta_plugin = _resolve_meta_plugin(runtime)
    while meta_plugin is None and not stop_event.is_set():
        log(PLUGIN_NAME, "waiting for active meta plugin...")
        if stop_event.wait(5.0):
            return
        meta_plugin = _resolve_meta_plugin(runtime)
    if meta_plugin is None:
        return

    _route_segments(_safe_process(meta_plugin, "session_start", log), emit_candidate, ui_q, log)

    tick_count = 0
    while not stop_event.is_set():
        if stop_event.wait(tick_sec):
            break
        tick_count += 1
        try:
            segments = _safe_process(meta_plugin, "tick", log)
            emitted = _route_segments(segments, emit_candidate, ui_q, log)
            if emitted or tick_count <= 5 or tick_count % 10 == 0:
                log(PLUGIN_NAME, f"tick {tick_count}: {len(segments)} segs, emitted={emitted}")
        except Exception as exc:
            log(PLUGIN_NAME, f"tick error: {type(exc).__name__}: {exc}")

    _route_segments(_safe_process(meta_plugin, "session_end", log), emit_candidate, ui_q, log)
    log(PLUGIN_NAME, f"stopped after {tick_count} ticks")


# ── internals ────────────────────────────────────────────────────────────────


def _safe_process(meta_plugin: Any, input_type: str, log) -> List[Dict[str, Any]]:
    try:
        segments = meta_plugin.process_input({"input_type": input_type, "timestamp": time.time()})
        return segments or []
    except Exception as exc:
        log(PLUGIN_NAME, f"{input_type} error: {type(exc).__name__}: {exc}")
        return []


def _route_segments(
    segments: List[Dict[str, Any]],
    emit_candidate,
    ui_q,
    log,
) -> int:
    emitted = 0
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = (seg.get("text") or "").strip()
        voice = seg.get("voice", "host")
        priority = seg.get("priority", 50.0)
        metadata = seg.get("metadata", {})

        # System / textless segments go to the UI queue only.
        if voice == "_system" or not text:
            if ui_q and metadata:
                try:
                    ui_q.put({"type": "segment", "metadata": metadata})
                except Exception:
                    pass
            continue

        if emit_candidate and text:
            emit_candidate(
                {
                    "source": PLUGIN_NAME,
                    "title": "",
                    "body": text,
                    "voice": voice,
                    "lead_voice": voice,
                    "priority": priority,
                    "type": seg.get("type", "narration"),
                    "_literal": True,  # body is final text — skip host LLM rewrite
                    "event_type": metadata.get("type", "narration"),
                    "metadata": metadata,
                }
            )
            emitted += 1
    return emitted


def _resolve_meta_plugin(runtime: Dict[str, Any]) -> Optional[Any]:
    mp = runtime.get("ACTIVE_META_PLUGIN")
    if mp and hasattr(mp, "process_input"):
        return mp
    try:
        import bookmark

        mp = getattr(bookmark, "ACTIVE_META_PLUGIN", None)
        if mp and hasattr(mp, "process_input"):
            return mp
        sr = getattr(bookmark, "shared_runtime", None)
        if isinstance(sr, dict):
            mp = sr.get("ACTIVE_META_PLUGIN")
            if mp and hasattr(mp, "process_input"):
                return mp
    except Exception:
        pass
    return None
