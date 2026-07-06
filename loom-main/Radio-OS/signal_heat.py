"""Signal Heat — the emergent-airtime engine for Radio OS.

The antenna emits raw observations with a `priority` HEAT HINT. That hint, on its own, is a static
number: it can rank a single batch, but it cannot make "the hot world speak." This module turns the
hint into a **time-decayed quantity of source activity** so airtime becomes *emergent*, exactly as the
vision describes:

    A source that just lit up earns airtime; a source that went quiet recedes. Silence is valid.

Three jobs stay separate (per the locked antenna contract): the antenna OBSERVES, this engine RANKS by
heat, the host NARRATES what matters. This file does only the middle job.

Design (locked, same spirit as broadcast_grammar.py):
    * Pure functions, stdlib only, standalone-testable. No runtime import, no preserved file touched.
    * State is a plain dict the caller stores in `mem` (mirrors mem["_broadcast_grammar_state"]):
          {"sources": {src: {"heat": float, "last_ts": float}}, "seen": [post_id, ...]}
    * Heat decays exponentially by a per-source half-life. Reads are lazy (decay is computed from
      last_ts at read time) so callers can't desync the model.
    * Authoring lives in the meta-plugin spec: a global `signal_heat` block + per-source `heat`
      overrides (gain / quiet_floor / interrupt_threshold / half_life_sec). The antenna stays dumb;
      "what's worth airtime" is shaped here.
"""
from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Dict, List, Optional


# Global defaults. Per-source overrides may set any of these keys under spec.sources[src]["heat"].
DEFAULT_HEAT_CONFIG: Dict[str, Any] = {
    "half_life_sec": 1800.0,      # how fast a source cools (30 min → half the heat)
    "gain": 1.0,                  # loudness multiplier on this source's contribution
    "quiet_floor": 0.05,          # below this normalized heat a source is silent / ineligible
    "interrupt_threshold": 0.60,  # at/above this normalized heat a source may interrupt
    "max_heat": 100.0,            # cap (normalization denominator)
    "weights": {"self": 0.4, "heat": 0.6},  # blend of a candidate's own priority vs its source heat
}

# The subset that is meaningfully per-source (what the Studio authoring surfaces).
SOURCE_HEAT_KEYS = ("gain", "half_life_sec", "quiet_floor", "interrupt_threshold", "max_heat")


def now_ts() -> float:
    return time.time()


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def default_heat_config() -> Dict[str, Any]:
    """A fresh copy of the global defaults (for spec generation / authoring)."""
    return deepcopy(DEFAULT_HEAT_CONFIG)


def default_source_heat() -> Dict[str, Any]:
    """The per-source `heat` block written into each source of a generated spec."""
    return {
        "gain": DEFAULT_HEAT_CONFIG["gain"],
        "half_life_sec": DEFAULT_HEAT_CONFIG["half_life_sec"],
        "quiet_floor": DEFAULT_HEAT_CONFIG["quiet_floor"],
        "interrupt_threshold": DEFAULT_HEAT_CONFIG["interrupt_threshold"],
    }


def normalize_heat_config(global_raw: Optional[Dict[str, Any]],
                          per_source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge an authored global `signal_heat` block + per-source `heat` overrides into a normalized
    config. `per_source` is the spec's `sources` map; each source's override lives under its `heat`."""
    cfg = deepcopy(DEFAULT_HEAT_CONFIG)
    incoming = global_raw if isinstance(global_raw, dict) else {}
    for key in ("half_life_sec", "gain", "quiet_floor", "interrupt_threshold", "max_heat"):
        if key in incoming and incoming[key] not in (None, ""):
            cfg[key] = _float(incoming[key], cfg[key])
    weights = incoming.get("weights") if isinstance(incoming.get("weights"), dict) else {}
    cfg["weights"] = {
        "self": _float(weights.get("self"), DEFAULT_HEAT_CONFIG["weights"]["self"]),
        "heat": _float(weights.get("heat"), DEFAULT_HEAT_CONFIG["weights"]["heat"]),
    }
    sources: Dict[str, Dict[str, Any]] = {}
    if isinstance(per_source, dict):
        for src, sspec in per_source.items():
            block = sspec.get("heat") if isinstance(sspec, dict) else None
            if isinstance(block, dict):
                sources[src] = {k: _float(block[k], cfg[k]) for k in SOURCE_HEAT_KEYS if k in block and block[k] not in (None, "")}
    cfg["sources"] = sources
    return cfg


def _src_val(cfg: Dict[str, Any], source: str, key: str) -> float:
    src_over = cfg.get("sources", {}).get(source, {}) if isinstance(cfg.get("sources"), dict) else {}
    if key in src_over:
        return _float(src_over[key], _float(cfg.get(key), DEFAULT_HEAT_CONFIG[key]))
    return _float(cfg.get(key), DEFAULT_HEAT_CONFIG[key])


# ---------------------------------------------------------------------------
# Heat math
# ---------------------------------------------------------------------------
def _decayed(heat: float, last_ts: float, now: float, half_life: float) -> float:
    """Exponential decay of `heat` from `last_ts` to `now`. half_life<=0 → no decay."""
    if heat <= 0:
        return 0.0
    if half_life <= 0:
        return heat
    dt = max(0.0, now - last_ts)
    if dt == 0:
        return heat
    return heat * (0.5 ** (dt / half_life))


def _contribution(candidate: Dict[str, Any]) -> float:
    """An observation's raw heat contribution = its priority hint (0..100)."""
    return _float(candidate.get("priority", candidate.get("heur", 50.0)), 50.0)


def bump_heat(state: Dict[str, Any], candidate: Dict[str, Any], cfg: Dict[str, Any], now: float) -> float:
    """Fold a new observation into its source's heat (decay-to-now, then add gain*contribution, cap).
    Returns the source's new raw heat. Call ONCE per genuinely new observation."""
    source = str(candidate.get("source") or "feed")
    sources = state.setdefault("sources", {})
    s = sources.setdefault(source, {"heat": 0.0, "last_ts": now})
    half_life = _src_val(cfg, source, "half_life_sec")
    s["heat"] = _decayed(_float(s.get("heat"), 0.0), _float(s.get("last_ts"), now), now, half_life)
    s["last_ts"] = now
    add = _src_val(cfg, source, "gain") * _contribution(candidate)
    s["heat"] = min(s["heat"] + add, _src_val(cfg, source, "max_heat"))
    return s["heat"]


def decay_heat(state: Dict[str, Any], now: float, cfg: Dict[str, Any]) -> None:
    """Mutably decay every known source to `now` (housekeeping; reads are lazy regardless)."""
    for source, s in (state.get("sources") or {}).items():
        half_life = _src_val(cfg, source, "half_life_sec")
        s["heat"] = _decayed(_float(s.get("heat"), 0.0), _float(s.get("last_ts"), now), now, half_life)
        s["last_ts"] = now


def source_heat(state: Dict[str, Any], source: str, cfg: Dict[str, Any], now: float) -> float:
    """Current NORMALIZED heat (0..1) of a source, lazily decayed to `now`."""
    s = (state.get("sources") or {}).get(source)
    if not s:
        return 0.0
    half_life = _src_val(cfg, source, "half_life_sec")
    raw = _decayed(_float(s.get("heat"), 0.0), _float(s.get("last_ts"), now), now, half_life)
    max_heat = _src_val(cfg, source, "max_heat") or 1.0
    return min(raw / max_heat, 1.0)


def hottest_source(state: Dict[str, Any], cfg: Dict[str, Any], now: float) -> Optional[str]:
    sources = state.get("sources") or {}
    if not sources:
        return None
    return max(sources.keys(), key=lambda src: source_heat(state, src, cfg, now))


def heat_snapshot(state: Dict[str, Any], cfg: Dict[str, Any], now: float) -> Dict[str, float]:
    """Per-source normalized heat — for telemetry / 'what's dominating' surfaces."""
    return {src: round(source_heat(state, src, cfg, now), 4) for src in (state.get("sources") or {})}


def is_silent(state: Dict[str, Any], cfg: Dict[str, Any], now: float) -> bool:
    """True when no source is above its quiet floor — dead air is a valid state; do not invent events."""
    sources = state.get("sources") or {}
    for src in sources:
        if source_heat(state, src, cfg, now) >= _src_val(cfg, src, "quiet_floor"):
            return False
    return True


def rank_candidates(candidates: List[Dict[str, Any]], state: Dict[str, Any], cfg: Dict[str, Any],
                    now: float) -> List[Dict[str, Any]]:
    """Rank candidates by a blend of their own priority and their source's live heat. Sources below
    their quiet floor are dropped (receded). Each survivor is annotated with real `heat` + `interrupt`.
    Does not mutate the inputs (returns new dicts)."""
    w = cfg.get("weights", DEFAULT_HEAT_CONFIG["weights"])
    w_self = _float(w.get("self"), 0.4)
    w_heat = _float(w.get("heat"), 0.6)
    scored: List[tuple] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        source = str(c.get("source") or "feed")
        sh = source_heat(state, source, cfg, now)
        if sh < _src_val(cfg, source, "quiet_floor"):
            continue  # this world has gone quiet; it recedes rather than narrate cold air
        pr_norm = min(_contribution(c) / 100.0, 1.0)
        score = w_self * pr_norm + w_heat * sh
        out = dict(c)
        out["heat"] = round(sh, 4)
        out["interrupt"] = sh >= _src_val(cfg, source, "interrupt_threshold")
        out["heat_score"] = round(score, 4)
        scored.append((score, out))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [out for _score, out in scored]


# ---------------------------------------------------------------------------
# Standalone smoke / self-test (pytest-free; the venv has no pytest)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    cfg = normalize_heat_config({}, {})
    st: Dict[str, Any] = {}
    t0 = 1_000_000.0
    bump_heat(st, {"source": "trading", "priority": 80}, cfg, t0)
    h_now = source_heat(st, "trading", cfg, t0)
    h_halflife = source_heat(st, "trading", cfg, t0 + cfg["half_life_sec"])
    print(f"trading heat now={h_now:.3f}  after one half-life={h_halflife:.3f}  (should ~halve)")
    assert abs(h_halflife - h_now / 2) < 1e-6, "decay should halve at the half-life"

    bump_heat(st, {"source": "weather", "priority": 30}, cfg, t0)
    ranked = rank_candidates(
        [{"source": "weather", "priority": 95, "post_id": "w1"},
         {"source": "trading", "priority": 40, "post_id": "t1"}],
        st, cfg, t0,
    )
    print("ranked sources:", [c["source"] for c in ranked])
    assert ranked[0]["source"] == "trading", "hot source should outrank a higher-priority cold source"
    assert ranked[0]["interrupt"] is True, "an 0.8-heat source should be interrupt-eligible"

    cold = source_heat(st, "weather", cfg, t0 + 10 * cfg["half_life_sec"])
    print(f"weather heat after 10 half-lives={cold:.4f}  silent={is_silent(st, cfg, t0 + 10 * cfg['half_life_sec'])}")
    print("signal_heat self-test OK")
