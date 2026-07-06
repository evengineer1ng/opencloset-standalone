"""
Antenna (Bridge / Scout) — Radio OS feed plugin.

The generic reader for the "scout" model: a shim (see the shim generator) is placed by the operator
into some scope — a project folder, a game's bridge dir, a log location — and writes normalized
observations to a RadioOSBridge folder. This plugin reads that folder and emits them.

Generalized from cp2077_sdk.py, which already reads %USERPROFILE%\\RadioOSBridge\\cp2077_state.json:
a game mod (a shim) writes state, this antenna reads it. Here that pattern becomes source-agnostic.

LOCKED CONTRACT — the antenna OBSERVES, it does not narrate:
    * It emits RAW, TYPED observations (file_changed, test_failed, field_changed, ...) with a
      timestamp and a `priority` HEAT HINT, and nothing more.
    * It NEVER decides what is worth airtime. Ranking by signal heat is the orchestrator's job
      (the meta-plugin); narration is the host's job. Three separate jobs.
    * SILENCE is the default, valid state. "online, no signal" is the most common reading when a
      scout is dropped into a random folder — so this stays quiet rather than inventing events.

Two transports (either or both, auto-detected):
    * stream   — the shim appends one JSON observation per line to `events.jsonl`; we tail it.
    * snapshot — the shim writes the latest `state.json`; we diff it into field_changed observations.

Observation line schema (all optional; missing fields are tolerated):
    {"type": "test_failed", "title": "...", "body": "...", "priority": 80, "ts": 169..., "tags": [], "id": "..."}

Dependency-free (stdlib) and standalone-testable: parse_observation_line / diff_state /
read_new_lines / poll_bridge are pure and unit-tested without the runtime.
"""
import json
import os
import time
import hashlib
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_NAME = "antenna_bridge"
PLUGIN_DESC = "Generic scout/bridge antenna — emits raw typed observations a shim drops into a RadioOSBridge folder."
IS_FEED = True
FEED_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "plugin": "antenna_bridge",
    "source": "scout",            # logical source name (and default RadioOSBridge subfolder)
    "bridge_dir": "",             # explicit folder; empty → %USERPROFILE%/RadioOSBridge/<source>
    "mode": "auto",               # auto | stream | snapshot
    "events_file": "events.jsonl",
    "state_file": "state.json",
    "ignore_keys": [],            # snapshot keys to never diff (noise)
    "poll_sec": 3,
    "priority": 60,               # default heat hint passed through on observations
    "max_per_poll": 25,
    "idle_log_sec": 120,          # how often to log "online, no signal"
}


def _now_ts() -> float:
    return time.time()


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:16]


def _short(value: Any, limit: int = 120) -> str:
    try:
        s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except Exception:
        s = str(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def observation_to_candidate(obs: Dict[str, Any], source: str, default_priority: float) -> Dict[str, Any]:
    """Normalize one raw observation into a Radio OS candidate. `priority` is a HEAT HINT only —
    this function does not rank or decide airtime."""
    otype = str(obs.get("type") or "observation").strip() or "observation"
    title = str(obs.get("title") or otype.replace("_", " ")).strip()
    body = str(obs.get("body") or "").strip()
    try:
        pr = float(obs.get("priority")) if obs.get("priority") not in (None, "") else default_priority
    except (ValueError, TypeError):
        pr = default_priority
    try:
        ts = float(obs.get("ts")) if obs.get("ts") not in (None, "") else _now_ts()
    except (ValueError, TypeError):
        ts = _now_ts()
    raw_id = str(obs.get("id") or _sha1(f"{source}|{otype}|{title}|{body[:120]}|{ts}"))
    extra_tags = [str(t) for t in obs.get("tags", []) if isinstance(obs.get("tags"), list)]
    return {
        "post_id": f"{source}:{raw_id}", "source": source,
        "title": title[:300], "body": body[:2000],
        "priority": pr, "ts": ts, "type": otype, "tags": [source, otype, *extra_tags][:8],
    }


def parse_observation_line(raw_line: str, source: str, default_priority: float) -> Optional[Dict[str, Any]]:
    """Parse one events.jsonl line → candidate. Tolerates plain text (treated as a log_line)."""
    line = (raw_line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        obj = {"type": "log_line", "body": line}
    if not isinstance(obj, dict):
        obj = {"type": "log_line", "body": _short(obj, 2000)}
    return observation_to_candidate(obj, source, default_priority)


def diff_state(prev: Optional[Dict[str, Any]], curr: Dict[str, Any], source: str,
               default_priority: float, ignore_keys: Tuple[str, ...] = ()) -> List[Dict[str, Any]]:
    """Diff a snapshot vs the previous one → field_added / field_changed observations.
    The antenna only reports that something changed; it does not judge whether it matters."""
    out: List[Dict[str, Any]] = []
    if not isinstance(curr, dict):
        return out
    prev = prev or {}
    ignore = set(ignore_keys or ())
    for key, value in curr.items():
        if key in ignore:
            continue
        if key not in prev:
            out.append(observation_to_candidate(
                {"type": "field_added", "title": f"{key} appeared", "body": _short(value),
                 "priority": default_priority, "id": f"{key}={_short(value)}"}, source, default_priority))
        elif prev.get(key) != value:
            out.append(observation_to_candidate(
                {"type": "field_changed", "title": f"{key} changed",
                 "body": f"{_short(prev.get(key))} → {_short(value)}",
                 "priority": default_priority, "id": f"{key}={_short(value)}"}, source, default_priority))
    return out


def read_new_lines(path: str, offset: int) -> Tuple[List[str], int]:
    """Tail an append-only file from `offset` (bytes). Returns (complete lines, new offset).
    Robust to truncation/rotation (offset reset) and partial trailing lines (left for next poll)."""
    if not os.path.isfile(path):
        return [], offset
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if size < offset:
        offset = 0  # file was truncated or rotated
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    nl = data.rfind(b"\n")
    if nl == -1:
        return [], offset  # no complete line yet
    complete = data[: nl + 1]
    new_offset = offset + len(complete)
    text = complete.decode("utf-8", "replace")
    lines = [s for s in (ln.rstrip("\r") for ln in text.split("\n")) if s.strip()]
    return lines, new_offset


def _read_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception:
        return None


def resolve_bridge_dir(cfg: Dict[str, Any]) -> str:
    explicit = str(cfg.get("bridge_dir") or "").strip()
    if explicit:
        return explicit
    source = str(cfg.get("source") or "scout")
    root = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(root, "RadioOSBridge", source)


def poll_bridge(ctx: Dict[str, Any], cfg: Dict[str, Any], bridge_dir: str, emit, log=None) -> Dict[str, Any]:
    """One read cycle: tail events.jsonl and/or diff state.json, emit candidates, update ctx.
    Pure-ish (only side effect is `emit`), so it is unit-testable without the runtime loop."""
    source = str(cfg.get("source") or "scout")
    default_pr = float(cfg.get("priority", 60))
    mode = str(cfg.get("mode") or "auto")
    max_per = int(cfg.get("max_per_poll", 25))
    emitted = 0

    if mode in ("auto", "stream"):
        events_path = os.path.join(bridge_dir, str(cfg.get("events_file") or "events.jsonl"))
        lines, new_off = read_new_lines(events_path, int(ctx.get("offset", 0)))
        ctx["offset"] = new_off
        for ln in lines[:max_per]:
            cand = parse_observation_line(ln, source, default_pr)
            if cand:
                emit(cand)
                emitted += 1

    if mode in ("auto", "snapshot"):
        state_path = os.path.join(bridge_dir, str(cfg.get("state_file") or "state.json"))
        curr = _read_json(state_path)
        if isinstance(curr, dict):
            if ctx.get("prev_state") is not None:  # first snapshot establishes baseline silently
                ignore = tuple(cfg.get("ignore_keys") or ())
                for cand in diff_state(ctx["prev_state"], curr, source, default_pr, ignore)[:max_per]:
                    emit(cand)
                    emitted += 1
            ctx["prev_state"] = curr

    now = _now_ts()
    if emitted:
        ctx["last_emit"] = now
        if log:
            log("bridge", f"  → {emitted} observation(s) from {source}")
    else:
        idle = int(cfg.get("idle_log_sec", 120))
        if log and now - float(ctx.get("last_log", 0)) >= idle:
            ctx["last_log"] = now
            log("bridge", f"  · {source}: online, no signal")
    return ctx


def feed_worker(stop_event, mem, payload, runtime) -> None:
    """Radio OS feed entry point: watch a scout's bridge folder, emit observations forever."""
    log = runtime.get("log", lambda *a: None)
    emit = runtime.get("emit_candidate", lambda c: None)
    cfg = {**FEED_DEFAULTS, **(payload or {})}
    bridge_dir = resolve_bridge_dir(cfg)
    poll = max(1, int(cfg.get("poll_sec", 3)))
    log("bridge", f"📡 Scout online → {bridge_dir} (source={cfg.get('source')}, mode={cfg.get('mode')})")
    if not os.path.isdir(bridge_dir):
        log("bridge", f"  · bridge folder not present yet — waiting for a shim to write to {bridge_dir}")
    ctx: Dict[str, Any] = {"offset": 0, "prev_state": None, "last_emit": 0.0, "last_log": 0.0}
    while not stop_event.is_set():
        try:
            ctx = poll_bridge(ctx, cfg, bridge_dir, emit, log)
        except Exception as e:  # noqa: BLE001 — a scout must never take the station down
            log("bridge", f"  ! poll error: {e}")
        for _ in range(poll):
            if stop_event.is_set():
                break
            time.sleep(1)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = {**FEED_DEFAULTS, "source": sys.argv[1] if len(sys.argv) > 1 else "scout",
           "bridge_dir": sys.argv[2] if len(sys.argv) > 2 else "", "idle_log_sec": 5}
    bdir = resolve_bridge_dir(cfg)
    print(f"watching bridge: {bdir} (source={cfg['source']}) — Ctrl-C to stop")
    ctx = {"offset": 0, "prev_state": None, "last_emit": 0.0, "last_log": 0.0}
    try:
        while True:
            ctx = poll_bridge(ctx, cfg, bdir, lambda c: print("OBS", c["type"], "|", c["title"], "| pr", c["priority"]),
                              log=lambda tag, msg: print(f"[{tag}] {msg}"))
            time.sleep(int(cfg["poll_sec"]))
    except KeyboardInterrupt:
        print("stopped")
