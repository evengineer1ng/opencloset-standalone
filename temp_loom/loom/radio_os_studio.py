#!/usr/bin/env python3
"""
Radio OS Studio - native .oradio station builder.

Studio authors portable station organisms. It may inspect a live source while
building, but it is not the station runner. Playback belongs to the .oradio
kernel/player layer: bookmark.py is the kernel, shell_bookmark.py is the
library/iTunes-style manager, and the exported .oradio is the media artifact.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # tkinter is an optional system package (python3-tk); absent in headless/CI
    tk = filedialog = messagebox = ttk = None

import yaml


BASE_DIR = Path(__file__).resolve().parent
STATIONS_DIR = BASE_DIR / "stations"
PLUGINS_DIR = BASE_DIR / "plugins"
META_PLUGINS_DIR = PLUGINS_DIR / "meta"
EXPORTS_DIR = BASE_DIR / "exports"
VOICES_DIR = BASE_DIR / "voices"
SFX_DIR = BASE_DIR / "sfx"
RUNTIME_PATH = BASE_DIR / "bookmark.py"


UI = {
    "bg": "#101114",
    "panel": "#17191f",
    "panel_2": "#20232b",
    "surface": "#0b0c0f",
    "line": "#30343d",
    "text": "#f1f3f5",
    "muted": "#a6adb8",
    "accent": "#69d2e7",
    "good": "#52d273",
    "warn": "#f4c95d",
    "bad": "#ff5c78",
}

FONT_H1 = ("Segoe UI", 20, "bold")
FONT_H2 = ("Segoe UI", 14, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 10)


def read_yaml(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def read_global_config() -> Dict[str, Any]:
    if os.name == "nt":
        root = Path(os.getenv("APPDATA", str(Path.home()))) / "RadioOS"
    else:
        root = Path.home() / ".radioOS"
    return read_json(root / "config.json", {}) or {}


def import_module_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def station_id_from_text(raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in raw.strip())
    cleaned = cleaned.strip("_-")
    return cleaned or "UntitledStation"


def parse_endpoint_lines(raw: str) -> List[Dict[str, str]]:
    endpoints: List[Dict[str, str]] = []
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if "|" in text:
            source, path = [part.strip() for part in text.split("|", 1)]
            source = source or path.strip("/").replace("/", "_") or "root"
        else:
            path = text
            source = path.strip("/").replace("/", "_") or "root"
        endpoints.append({"path": path or "/", "source": source})
    return endpoints


def endpoints_to_text(endpoints: Any) -> str:
    if not isinstance(endpoints, list):
        return ""
    lines: List[str] = []
    for item in endpoints:
        if isinstance(item, dict):
            path = str(item.get("path", "/")).strip() or "/"
            source = str(item.get("source") or "").strip()
            lines.append(f"{source}|{path}" if source else path)
        else:
            lines.append(str(item))
    return "\n".join(lines)


def find_antenna_feed(manifest: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    feeds = manifest.get("feeds") or {}
    if not isinstance(feeds, dict):
        return None, {}
    for key, cfg in feeds.items():
        if isinstance(cfg, dict) and cfg.get("plugin") == "antenna_http":
            return str(key), cfg
    return None, {}


# ---------------------------------------------------------------------------
# Feed/antenna ecosystem (ported from the shell_bookmark new-station wizard).
# Data + presets are duplicated here (preservation rule: never import shell_bookmark).
# Discovery is STATIC (ast) — we read plugin metadata without executing ~60 plugins,
# which is safer for an authoring tool than the wizard's exec-based discovery.
# ---------------------------------------------------------------------------
FEED_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "reddit": {"enabled": True, "subreddits": [], "poll_sec": 30, "limit": 20, "priority": 60,
               "burst_delay": 0.2, "seen_ttl_sec": 3600},
    "markets": {"enabled": True, "symbols": [], "poll_sec": 15, "breakout_pct": 0.2, "priority": 90},
    "portfolio_event": {"enabled": True, "mode": "hyperliquid", "user_address": "", "poll_sec": 6,
                        "min_emit_gap_sec": 20, "min_equity_delta_frac": 0.003, "big_equity_delta_frac": 0.015,
                        "positions_change_priority": 95, "equity_change_priority": 93, "big_move_priority": 98,
                        "base_url": "https://api.hyperliquid.xyz"},
    "rss": {"enabled": True, "urls": [], "poll_sec": 180, "priority": 72},
    "bluesky": {"enabled": True, "hashtags": [], "poll_sec": 60, "limit": 20, "priority": 70},
    "document": {"enabled": True, "files": [], "poll_sec": 2.5, "announce_cooldown_sec": 600},
}

SHOW_FORMATS: List[Dict[str, str]] = [
    {"slug": "talk_radio", "label": "Talk Radio", "grammar": "casual_podcast", "description": "Host-led commentary with opinions and callbacks."},
    {"slug": "news_desk", "label": "News Desk", "grammar": "news_desk", "description": "Clean headlines, pivots, and live updates."},
    {"slug": "sports_broadcast", "label": "Sports Broadcast", "grammar": "sports_broadcast", "description": "Momentum, stakes, rivalries, and action."},
    {"slug": "morning_show", "label": "Morning Show", "grammar": "casual_podcast", "description": "Warm, energetic, and conversational."},
    {"slug": "podcast", "label": "Podcast", "grammar": "casual_podcast", "description": "Longer arcs, callbacks, and personality."},
    {"slug": "roundtable", "label": "Roundtable", "grammar": "news_desk", "description": "Multiple voices comparing angles."},
    {"slug": "mission_control", "label": "Mission Control", "grammar": "mission_control", "description": "Operational hand-offs and channel switching."},
    {"slug": "documentary_narrator", "label": "Documentary Narrator", "grammar": "news_desk", "description": "Measured narration with context and consequence."},
    {"slug": "public_radio", "label": "Public Radio", "grammar": "news_desk", "description": "Calm, informative, and human."},
    {"slug": "late_night_host", "label": "Late Night Host", "grammar": "casual_podcast", "description": "Dry, playful, and reflective."},
    {"slug": "field_correspondent", "label": "Field Correspondent", "grammar": "news_desk", "description": "Live reports from the edge of the action."},
    {"slug": "research_desk", "label": "Research Desk", "grammar": "mission_control", "description": "Questions, evidence, and developing hypotheses."},
    {"slug": "league_office", "label": "League Office", "grammar": "sports_broadcast", "description": "Authority, standings, rules, and stakes."},
    {"slug": "storyteller", "label": "Storyteller", "grammar": "casual_podcast", "description": "Narrative arcs and character meaning."},
    {"slug": "personal_companion", "label": "Personal Companion", "grammar": "casual_podcast", "description": "Gentle context, reminders, and continuity."},
    {"slug": "custom_format", "label": "Custom Format", "grammar": "news_desk", "description": "A station format you define by tags and cast."},
]

CAST_FORMATS = [
    "solo_host",
    "co_hosts",
    "panel",
    "host_plus_analyst",
    "news_desk_plus_field_reporter",
    "roundtable",
]

TAG_SHELVES: List[Tuple[str, List[Dict[str, str]]]] = [
    ("Format", [
        {"emoji": "🎙", "label": "Radio Personality", "slug": "radio_personality", "category": "format", "description": "Leans into host presence and station identity."},
        {"emoji": "📰", "label": "Breaking News", "slug": "breaking_news", "category": "format", "description": "Prioritizes fresh developments and clear pivots."},
        {"emoji": "🧪", "label": "Research", "slug": "research", "category": "format", "description": "Frames events as evidence and experiments."},
        {"emoji": "🏛", "label": "Classical", "slug": "classical", "category": "format", "description": "Formal, composed, and historically minded."},
    ]),
    ("Voice", [
        {"emoji": "🧠", "label": "Analysis", "slug": "analysis", "category": "voice", "description": "Explains implications instead of only describing events."},
        {"emoji": "🎭", "label": "Drama", "slug": "drama", "category": "voice", "description": "Finds tension, characters, and emotional stakes."},
        {"emoji": "🪶", "label": "Poetic", "slug": "poetic", "category": "voice", "description": "Uses lyrical framing without losing facts."},
        {"emoji": "🧑‍🏫", "label": "Educational", "slug": "educational", "category": "voice", "description": "Teaches as it narrates."},
    ]),
    ("Lens", [
        {"emoji": "🏀", "label": "Competition", "slug": "competition", "category": "lens", "description": "Tracks winners, losers, pressure, and momentum."},
        {"emoji": "📈", "label": "Momentum", "slug": "momentum", "category": "lens", "description": "Notices acceleration, cooling, streaks, and reversals."},
        {"emoji": "🔍", "label": "Mystery", "slug": "mystery", "category": "lens", "description": "Treats unknowns as clues."},
        {"emoji": "🧩", "label": "Puzzle-Solving", "slug": "puzzle_solving", "category": "lens", "description": "Connects pieces into an explanation."},
    ]),
    ("Energy", [
        {"emoji": "⚡", "label": "Urgency", "slug": "urgency", "category": "energy", "description": "Moves quickly when heat or priority rises."},
        {"emoji": "🧘", "label": "Calm", "slug": "calm", "category": "energy", "description": "Keeps the station steady and grounded."},
        {"emoji": "🔥", "label": "Hype", "slug": "hype", "category": "energy", "description": "Amplifies big moments and excitement."},
        {"emoji": "🚀", "label": "Exploration", "slug": "exploration", "category": "energy", "description": "Treats events like discoveries."},
    ]),
    ("Humor", [
        {"emoji": "😏", "label": "Dry Humor", "slug": "dry_humor", "category": "humor", "description": "Adds restrained wit and sharp asides."},
        {"emoji": "🕵", "label": "Noir", "slug": "noir", "category": "weird", "description": "Hard-boiled atmosphere and suspicious details."},
        {"emoji": "🪄", "label": "Magical", "slug": "magical", "category": "weird", "description": "Frames systems as strange, enchanted machinery."},
        {"emoji": "🌧", "label": "Melancholy", "slug": "melancholy", "category": "energy", "description": "Finds loss, fatigue, and bittersweet texture."},
    ]),
]

TAG_CATALOG: List[Dict[str, Any]] = [tag for _shelf, tags in TAG_SHELVES for tag in tags]
TAG_BY_SLUG = {str(tag["slug"]): tag for tag in TAG_CATALOG}
FORMAT_BY_SLUG = {str(fmt["slug"]): fmt for fmt in SHOW_FORMATS}
TAG_STRENGTHS = {1: 0.45, 2: 0.7, 3: 1.0}
CUSTOM_TAG_BLOCKLIST = (
    "ignore prior",
    "ignore previous",
    "reveal secret",
    "reveal hidden",
    "system prompt",
    "developer message",
    "bypass",
    "jailbreak",
    "act as openai",
    "impersonate",
)

CUSTOM_TAG_SUGGESTION_RULES: List[Tuple[Tuple[str, ...], List[Dict[str, str]]]] = [
    (("shakespeare", "bard", "elizabethan"), [
        {"emoji": "🎭", "label": "Shakespearean", "slug": "shakespearean", "category": "custom_voice", "description": "Frames events with theatrical, bard-like wit."},
        {"emoji": "🪶", "label": "Poetic", "slug": "poetic", "category": "voice", "description": "Uses lyrical framing without losing facts."},
        {"emoji": "🏛", "label": "Classical", "slug": "classical", "category": "format", "description": "Formal, composed, and historically minded."},
    ]),
    (("pirate", "voyage", "sailor", "captain"), [
        {"emoji": "🏴‍☠️", "label": "Washed-Up Pirate Broadcaster", "slug": "washed_up_pirate_broadcaster", "category": "custom_voice", "description": "Frames events like an aging pirate recounting dangerous voyages."},
        {"emoji": "🧭", "label": "Adventure", "slug": "adventure", "category": "lens", "description": "Treats events like a voyage through unknown waters."},
    ]),
    (("noir", "detective", "mystery", "crime"), [
        {"emoji": "🕵", "label": "Noir", "slug": "noir", "category": "weird", "description": "Hard-boiled atmosphere and suspicious details."},
        {"emoji": "🔍", "label": "Mystery", "slug": "mystery", "category": "lens", "description": "Treats unknowns as clues."},
    ]),
    (("teacher", "teach", "professor", "classroom"), [
        {"emoji": "🧑‍🏫", "label": "Educational", "slug": "educational", "category": "voice", "description": "Teaches as it narrates."},
        {"emoji": "🧠", "label": "Analysis", "slug": "analysis", "category": "voice", "description": "Explains implications instead of only describing events."},
    ]),
]

PRESET_ROLES = [
    "host", "engineer", "skeptic", "macro", "optimist", "coach", "analyst", "stats_guru",
    "hype", "moderator", "narrator", "risk_manager", "execution_specialist", "news_anchor",
]
PRESET_TRAITS = [
    "calm", "smart", "technical", "precise", "critical", "grounded", "contextual", "broad",
    "energetic", "constructive", "motivational", "long_term", "blunt", "curious", "skeptical",
    "disciplined", "measured", "creative", "engaging", "data_driven",
]
PRESET_FOCUS = [
    "flow", "continuity", "systems", "signals", "risk", "failure_modes", "regimes", "liquidity",
    "opportunity", "growth", "discipline", "milestones", "positioning", "execution", "orderflow",
    "volatility", "macro", "narrative", "pacing", "big_plays", "metrics", "trends", "strategy", "news",
]
DEFAULT_CHARSET: Dict[str, Dict[str, Any]] = {
    "host": {"role": "host", "traits": ["calm", "smart"], "focus": ["flow", "continuity"]},
    "engineer": {"role": "engineer", "traits": ["technical", "precise"], "focus": ["systems", "signals"]},
    "skeptic": {"role": "skeptic", "traits": ["critical", "grounded"], "focus": ["risk", "failure_modes"]},
    "macro": {"role": "macro", "traits": ["contextual", "broad"], "focus": ["regimes", "liquidity"]},
    "optimist": {"role": "optimist", "traits": ["energetic", "constructive"], "focus": ["opportunity", "growth"]},
    "coach": {"role": "coach", "traits": ["motivational", "long_term"], "focus": ["discipline", "milestones"]},
}

_PLUGIN_META_NAMES = {"PLUGIN_NAME", "PLUGIN_DESC", "IS_FEED", "FEED_DEFAULTS", "DEFAULT_FEED_CFG", "DEFAULT_CONFIG"}


def _read_plugin_meta(path: Path) -> Optional[Dict[str, Any]]:
    """Statically read a plugin's metadata (no execution) via ast: PLUGIN_NAME / PLUGIN_DESC /
    IS_FEED / FEED_DEFAULTS|DEFAULT_FEED_CFG|DEFAULT_CONFIG. Returns None if unparseable."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    meta: Dict[str, Any] = {"name": path.stem, "display": path.stem, "desc": "", "is_feed": True, "defaults": None}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id not in _PLUGIN_META_NAMES:
                continue
            try:
                value = ast.literal_eval(node.value)
            except Exception:
                value = None
            key = target.id
            if key == "PLUGIN_NAME" and isinstance(value, str):
                meta["display"] = value
            elif key == "PLUGIN_DESC" and isinstance(value, str):
                meta["desc"] = value
            elif key == "IS_FEED" and value is not None:
                meta["is_feed"] = bool(value)
            elif key in ("FEED_DEFAULTS", "DEFAULT_FEED_CFG", "DEFAULT_CONFIG") and isinstance(value, dict) and meta["defaults"] is None:
                meta["defaults"] = value
    return meta


def discover_feed_plugins(plugins_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Discover installed FEED (antenna) plugins by static metadata scan. Non-feed plugins
    (IS_FEED = False) are excluded. This is the new Studio's read-only equivalent of the
    wizard's discover_plugins() — it surfaces the whole antenna roster (rss, reddit, document,
    markets, social, game/sim SDKs, antenna_http, ...) for multi-feed authoring."""
    base = plugins_dir or PLUGINS_DIR
    out: Dict[str, Dict[str, Any]] = {}
    if not base.exists():
        return out
    for path in sorted(base.glob("*.py")):
        meta = _read_plugin_meta(path)
        if meta and meta.get("is_feed", True):
            out[meta["name"]] = meta
    return out


def feed_default_config(name: str, plugins: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Seed config for a feed: plugin-declared defaults, else a known template, else minimal."""
    plugins = plugins if plugins is not None else discover_feed_plugins()
    info = plugins.get(name, {})
    if isinstance(info.get("defaults"), dict):
        cfg = deepcopy_json(info["defaults"])
    elif name in FEED_TEMPLATES:
        cfg = deepcopy_json(FEED_TEMPLATES[name])
    else:
        cfg = {}
    cfg.setdefault("enabled", True)
    cfg.setdefault("plugin", name)
    return cfg


def default_manifest(station_id: str, station_name: str, host: str) -> Dict[str, Any]:
    cfg = read_global_config()
    default_models = cfg.get("default_models", {}) if isinstance(cfg.get("default_models"), dict) else {}
    default_voices = cfg.get("default_voices", {}) if isinstance(cfg.get("default_voices"), dict) else {}
    feed_key = "source"
    return {
        "station": {
            "id": station_id,
            "name": station_name or station_id,
            "host": host or "Host",
            "category": "Live media organism",
            "logo": "",
        },
        "meta_plugin": "generated",
        "meta_plugin_spec": "meta_plugin_spec.json",
        "llm": {
            "provider": default_models.get("provider", "ollama"),
            "endpoint": default_models.get("llm_endpoint", "http://127.0.0.1:11434/api/generate"),
        },
        "models": {
            "producer": default_models.get("producer_model", ""),
            "host": default_models.get("host_model", ""),
            "navigator": default_models.get("navigator_model", ""),
            "embedding": default_models.get("embedding_model", ""),
        },
        "embedding": {"enabled": False},
        "audio": {
            "voices_provider": default_voices.get("provider", "kokoro"),
            "piper_bin": default_voices.get("piper_bin", ""),
            "master_volume": 0.85,
        },
        "voices": {"host": default_voices.get("voice_host", "")},
        "characters": {
            "host": {
                "role": "host",
                "traits": ["clear", "curious"],
                "focus": ["continuity", "what-it-means"],
            }
        },
        "feeds": {
            feed_key: {
                "enabled": False,
                "plugin": "antenna_http",
                "base_url": "",
                "endpoints": [],
                "poll_sec": 45,
                "priority": 70,
                "max_items_per_poll": 12,
                "write_signature": True,
            }
        },
        "mix": {"weights": {feed_key: 1.0}},
        "scheduler": {"source_quotas": {feed_key: 6}},
        "paths": {"db": "station.sqlite", "memory": "station_memory.json"},
    }


def portable_warnings_for_manifest(manifest: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    voices = manifest.get("voices") if isinstance(manifest.get("voices"), dict) else {}
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    path_fields = {"audio.piper_bin": audio.get("piper_bin", "")}
    path_fields.update({f"voices.{key}": value for key, value in voices.items()})
    for label, value in path_fields.items():
        if isinstance(value, str) and value and os.path.isabs(value):
            warnings.append(f"{label} is an absolute path and must be embedded or remapped for standalone playback.")
    if not (META_PLUGINS_DIR / "generated.py").exists():
        warnings.append("generated station voice runtime file is missing from this Radio OS checkout.")
    return warnings


def resolve_voice_file(name: str, station_dir: Optional[Path] = None) -> Optional[Path]:
    """Locate a voice file using the same search order the bookmark.py kernel uses:
    station dir -> Studio voices dir -> RADIO_OS_VOICES (global). Absolute paths win."""
    name = str(name or "").strip()
    if not name:
        return None
    cands: List[Path] = []
    if os.path.isabs(name):
        cands.append(Path(name))
    if station_dir:
        cands.append(station_dir / name)
    cands.append(VOICES_DIR / name)
    env_voices = os.environ.get("RADIO_OS_VOICES", "")
    if env_voices:
        cands.append(Path(env_voices) / name)
    for cand in cands:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def resolve_sfx_file(name: str, station_dir: Optional[Path] = None) -> Optional[Path]:
    """Locate a local SFX/stinger/bed file: station dir -> Studio sfx dir -> RADIO_OS_SFX.
    Absolute paths win. Remote/sourced SFX (e.g. Freesound) are declared, not resolved here."""
    name = str(name or "").strip()
    if not name:
        return None
    cands: List[Path] = []
    if os.path.isabs(name):
        cands.append(Path(name))
    if station_dir:
        cands.append(station_dir / name)
    cands.append(SFX_DIR / name)
    env_sfx = os.environ.get("RADIO_OS_SFX", "")
    if env_sfx:
        cands.append(Path(env_sfx) / name)
    for cand in cands:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def resolve_art_file(name: str, station_dir: Optional[Path] = None) -> Optional[Path]:
    """Locate station art/wallpaper files. Absolute paths win; otherwise station dir then repo root."""
    name = str(name or "").strip()
    if not name:
        return None
    cands: List[Path] = []
    if os.path.isabs(name):
        cands.append(Path(name))
    if station_dir:
        cands.append(station_dir / name)
    cands.append(BASE_DIR / name)
    for cand in cands:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def declared_art_assets(manifest: Dict[str, Any]) -> List[Dict[str, str]]:
    art = manifest.get("art") if isinstance(manifest.get("art"), dict) else {}
    global_bg = art.get("global_bg") if isinstance(art.get("global_bg"), dict) else {}
    kind = str(global_bg.get("type") or "color").strip().lower()
    path = str(global_bg.get("path") or "").strip()
    if kind in {"image", "video"} and path:
        return [{"role": "global_bg", "type": kind, "path": path}]
    return []


def declared_sfx(manifest: Dict[str, Any]) -> List[Dict[str, str]]:
    """The SFX entries a station declares under manifest['production']['sfx']."""
    production = manifest.get("production") if isinstance(manifest.get("production"), dict) else {}
    raw = production.get("sfx") if isinstance(production.get("sfx"), list) else []
    out: List[Dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict) and str(item.get("ref") or "").strip():
            out.append({
                "tag": str(item.get("tag") or "").strip(),
                "ref": str(item.get("ref") or "").strip(),
                "source": str(item.get("source") or "local").strip().lower() or "local",
            })
    return out


def production_spec(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the event-aware production layer (plan §3): per-event stinger/bed/voice/
    interrupt/cooldown rules, plus scripted interstitials. Interstitials are deterministic TTS
    *between* LLM segments (station IDs, time checks, bumpers) — seasoning, never the narration."""
    production = manifest.get("production") if isinstance(manifest.get("production"), dict) else {}
    rules_raw = production.get("rules") if isinstance(production.get("rules"), list) else []
    inter_raw = production.get("interstitials") if isinstance(production.get("interstitials"), list) else []
    rules: List[Dict[str, Any]] = []
    for r in rules_raw:
        if not isinstance(r, dict) or not str(r.get("event") or "").strip():
            continue
        rules.append({
            "event": str(r.get("event")).strip(),
            "stinger": str(r.get("stinger") or "").strip(),
            "bed": str(r.get("bed") or "").strip(),
            "voice": str(r.get("voice") or "").strip(),
            "priority": str(r.get("priority") or "normal").strip().lower(),
            "interrupt": bool(r.get("interrupt", False)),
            "cooldown_sec": int(r.get("cooldown_sec") or 0),
            "position": str(r.get("position") or "before").strip().lower(),  # before | after
        })
    interstitials: List[Dict[str, Any]] = []
    for i in inter_raw:
        if not isinstance(i, dict) or not str(i.get("kind") or "").strip():
            continue
        interstitials.append({
            "kind": str(i.get("kind")).strip(),
            "text": str(i.get("text") or "").strip(),
            "every_sec": int(i.get("every_sec") or 0),
            "source": str(i.get("source") or "scripted").strip().lower(),  # deterministic seasoning
        })
    return {"rules": rules, "interstitials": interstitials}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def station_requirements(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """The capability contract a .oradio declares. Live LLM narration is the medium
    (required, machine-provisioned via club membership), not a deterministic fallback.
    Small portable assets (voices) are bundled; per-OS/big assets are resolved on target."""
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    voices = manifest.get("voices") if isinstance(manifest.get("voices"), dict) else {}
    models = manifest.get("models") if isinstance(manifest.get("models"), dict) else {}
    llm = manifest.get("llm") if isinstance(manifest.get("llm"), dict) else {}
    voice_provider = str(audio.get("voices_provider") or "kokoro")
    return {
        "narration": "live_llm",  # never a deterministic fallback
        "llm": {
            "provider": str(llm.get("provider") or "ollama"),
            "endpoint": llm.get("endpoint", ""),
            "models": {k: v for k, v in models.items() if v},
            "required": True,
            "provisioning": "machine (club membership)",
        },
        "voices": {
            "provider": voice_provider,
            "refs": {role: str(val) for role, val in voices.items() if val},
            "resolve": "bundle",
        },
        "piper": {
            "needed": voice_provider == "piper" or bool(audio.get("piper_bin")),
            "resolve": "machine-cache",  # per-OS binary, located/fetched on the target
            "version": "auto",
        },
        "sfx": {  # production seasoning (local bundled or sourced/fetched) — NOT the medium, never blocks
            "resolve": "bundle-or-fetch",
            "declared": declared_sfx(manifest),
        },
        "production": production_spec(manifest),  # event rules + scripted interstitials
    }


def collect_station_assets(
    manifest: Dict[str, Any],
    station_dir: Optional[Path] = None,
    sourced: Optional[Dict[str, Path]] = None,
) -> Tuple[List[Tuple[Path, str]], Dict[str, Any]]:
    """Resolve + bundle small portable assets (Piper voices, SFX) and build the resolved lockfile.
    `sourced` maps an sfx tag -> a pre-fetched local file (e.g. from sfx_sourcing) so remote SFX can
    be bundled too. Returns (zip payloads, lock dict). Unbundleable refs are recorded, not fatal."""
    sourced = sourced or {}
    payloads: List[Tuple[Path, str]] = []
    seen: set[str] = set()
    lock: Dict[str, Any] = {
        "lock_version": "0.1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "narration": "live_llm",
        "voices": [],
        "piper": {},
        "llm": {},
        "sfx": [], 
        "art": [],
        "unresolved": [],
    }
    voices = manifest.get("voices") if isinstance(manifest.get("voices"), dict) else {}
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    voice_provider = str(audio.get("voices_provider") or "kokoro")
    for role, raw in voices.items():
        ref = str(raw or "").strip()
        if not ref:
            continue
        resolved = resolve_voice_file(ref, station_dir)
        looks_like_file = ref.lower().endswith((".onnx", ".wav")) or ("/" in ref or "\\" in ref) or os.path.isabs(ref)
        if resolved is not None:
            arcname = f"assets/voices/{resolved.name}"
            if arcname not in seen:
                payloads.append((resolved, arcname))
                seen.add(arcname)
                cfg = Path(str(resolved) + ".json")  # piper voices ship a sidecar .onnx.json
                if cfg.is_file():
                    payloads.append((cfg, arcname + ".json"))
            lock["voices"].append({
                "role": role, "ref": ref, "bundled": True, "arcname": arcname,
                "bytes": resolved.stat().st_size, "sha256": _sha256(resolved),
            })
        else:
            lock["voices"].append({
                "role": role, "ref": ref, "bundled": False,
                "resolve": "machine-cache" if looks_like_file else "provider",
            })
            if looks_like_file:
                lock["unresolved"].append(f"voice '{role}' -> {ref} (file not found to bundle)")
    lock["piper"] = {
        "needed": voice_provider == "piper" or bool(audio.get("piper_bin")),
        "bundled": False,  # per-OS binary resolved/fetched on the target machine
        "resolve": "machine-cache",
        "configured_bin": str(audio.get("piper_bin") or ""),
    }
    llm = manifest.get("llm") if isinstance(manifest.get("llm"), dict) else {}
    models = manifest.get("models") if isinstance(manifest.get("models"), dict) else {}
    lock["llm"] = {
        "provider": str(llm.get("provider") or "ollama"),
        "endpoint": llm.get("endpoint", ""),
        "models": {k: v for k, v in models.items() if v},
        "required": True,
        "provisioning": "machine (club membership)",
    }
    # SFX / stingers / beds — production seasoning. Local files are bundled; remote-sourced
    # entries (e.g. Freesound) are declared for fetch. Missing SFX never blocks broadcast.
    for item in declared_sfx(manifest):
        tag, ref, source = item["tag"], item["ref"], item["source"]
        if source != "local":
            fetched = sourced.get(tag)
            if fetched is not None and Path(fetched).is_file():
                fp = Path(fetched)
                arcname = f"assets/sfx/{fp.name}"
                if arcname not in seen:
                    payloads.append((fp, arcname))
                    seen.add(arcname)
                lock["sfx"].append({
                    "tag": tag, "ref": ref, "bundled": True, "arcname": arcname,
                    "bytes": fp.stat().st_size, "sha256": _sha256(fp), "sourced": source,
                })
            else:
                lock["sfx"].append({"tag": tag, "ref": ref, "bundled": False, "resolve": "fetch", "provider": source})
            continue
        resolved = resolve_sfx_file(ref, station_dir)
        if resolved is not None:
            arcname = f"assets/sfx/{resolved.name}"
            if arcname not in seen:
                payloads.append((resolved, arcname))
                seen.add(arcname)
            lock["sfx"].append({
                "tag": tag, "ref": ref, "bundled": True, "arcname": arcname,
                "bytes": resolved.stat().st_size, "sha256": _sha256(resolved),
            })
        else:
            lock["sfx"].append({"tag": tag, "ref": ref, "bundled": False, "resolve": "fetch"})
            lock["unresolved"].append(f"sfx '{tag}' -> {ref} (file not found to bundle)")
    # Production rules + interstitials. Validate stinger/bed references against declared SFX tags;
    # a dangling reference is a non-blocking warning (seasoning, not the medium).
    spec = production_spec(manifest)
    lock["production"] = spec
    declared_tags = {item["tag"] for item in declared_sfx(manifest) if item["tag"]}
    for rule in spec["rules"]:
        for slot in ("stinger", "bed"):
            tag = rule.get(slot)
            if tag and tag not in declared_tags:
                lock["unresolved"].append(
                    f"production rule '{rule['event']}' references undeclared sfx tag '{tag}' ({slot})"
                )
    for item in declared_art_assets(manifest):
        role, kind, ref = item["role"], item["type"], item["path"]
        resolved = resolve_art_file(ref, station_dir)
        if resolved is not None:
            arcname = f"assets/art/{resolved.name}"
            if arcname not in seen:
                payloads.append((resolved, arcname))
                seen.add(arcname)
            lock["art"].append({
                "role": role,
                "type": kind,
                "ref": ref,
                "bundled": True,
                "arcname": arcname,
                "bytes": resolved.stat().st_size,
                "sha256": _sha256(resolved),
            })
        else:
            lock["art"].append({"role": role, "type": kind, "ref": ref, "bundled": False, "resolve": "machine-cache"})
            lock["unresolved"].append(f"art '{role}' -> {ref} (file not found to bundle)")
    return payloads, lock


def package_descriptor_for_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    station = manifest.get("station", {}) if isinstance(manifest.get("station"), dict) else {}
    station_id = str(station.get("id") or "UntitledStation")
    return {
        "format": "oradio",
        "format_version": "0.1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "station_id": station_id,
        "station_name": str(station.get("name") or station_id),
        "entry": {
            "manifest": "manifest.yaml",
            "signature": "signature.json",
            "meta_plugin_spec": "meta_plugin_spec.json",
            "requirements": "requirements.json",
            "requirements_lock": "requirements.lock.json",
        },
        "kernel": {
            "name": "bookmark.py",
            "role": "oradio playback kernel",
            "embedded_in_this_export": False,
        },
        "library": {
            "name": "shell_bookmark.py",
            "role": "optional library/player manager",
            "required_for_artifact": False,
        },
        "requirements": station_requirements(manifest),
        "portable_warnings": portable_warnings_for_manifest(manifest),
    }


def plugin_payloads_for_manifest(manifest: Dict[str, Any]) -> List[Tuple[Path, str]]:
    payloads: List[Tuple[Path, str]] = []
    feeds = manifest.get("feeds") if isinstance(manifest.get("feeds"), dict) else {}
    seen: set[Path] = set()
    for cfg in feeds.values():
        if not isinstance(cfg, dict):
            continue
        plugin = str(cfg.get("plugin") or "").strip()
        if not plugin:
            continue
        path = PLUGINS_DIR / f"{plugin}.py"
        if path.exists() and path not in seen:
            payloads.append((path, f"plugins/{path.name}"))
            seen.add(path)
    meta_plugin = str(manifest.get("meta_plugin") or "").strip()
    if meta_plugin:
        meta_path = META_PLUGINS_DIR / f"{meta_plugin}.py"
        if meta_path.exists() and meta_path not in seen:
            payloads.append((meta_path, f"plugins/meta/{meta_path.name}"))
            seen.add(meta_path)
        if meta_plugin == "generated":
            grammar_path = BASE_DIR / "broadcast_grammar.py"
            if grammar_path.exists() and grammar_path not in seen:
                payloads.append((grammar_path, "broadcast_grammar.py"))
                seen.add(grammar_path)
    return payloads


def write_oradio_package(
    target: Path,
    manifest: Dict[str, Any],
    *,
    signature: Any = None,
    spec: Any = None,
    assets: Optional[List[Tuple[Path, str]]] = None,
    lock: Optional[Dict[str, Any]] = None,
    station_dir: Optional[Path] = None,
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if assets is None or lock is None:
        assets, lock = collect_station_assets(manifest, station_dir)
    packaged_manifest = manifest_for_package(manifest, lock)
    package = package_descriptor_for_manifest(manifest)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("oradio.json", json.dumps(package, indent=2, ensure_ascii=False))
        zf.writestr("manifest.yaml", yaml.safe_dump(packaged_manifest, sort_keys=False, allow_unicode=True))
        zf.writestr("requirements.json", json.dumps(station_requirements(manifest), indent=2, ensure_ascii=False))
        zf.writestr("requirements.lock.json", json.dumps(lock, indent=2, ensure_ascii=False))
        if signature is not None:
            zf.writestr("signature.json", json.dumps(signature, indent=2, ensure_ascii=False))
        if spec is not None:
            zf.writestr("meta_plugin_spec.json", json.dumps(spec, indent=2, ensure_ascii=False))
        for src, arcname in plugin_payloads_for_manifest(manifest):
            if src.exists():
                zf.write(src, arcname)
        for src, arcname in assets:
            if src.exists():
                zf.write(src, arcname)
    return target


def manifest_for_package(manifest: Dict[str, Any], lock: Dict[str, Any]) -> Dict[str, Any]:
    packaged = deepcopy_json(manifest)
    voices = packaged.get("voices") if isinstance(packaged.get("voices"), dict) else {}
    packaged["voices"] = voices
    for item in lock.get("voices", []) if isinstance(lock.get("voices"), list) else []:
        if not isinstance(item, dict) or not item.get("bundled"):
            continue
        role = str(item.get("role") or "").strip()
        arcname = str(item.get("arcname") or "").strip()
        if role and arcname:
            voices[role] = arcname
    art = packaged.get("art") if isinstance(packaged.get("art"), dict) else {}
    packaged["art"] = art
    global_bg = art.get("global_bg") if isinstance(art.get("global_bg"), dict) else {}
    for item in lock.get("art", []) if isinstance(lock.get("art"), list) else []:
        if not isinstance(item, dict) or item.get("role") != "global_bg" or not item.get("bundled"):
            continue
        arcname = str(item.get("arcname") or "").strip()
        if arcname:
            art.setdefault("global_bg", global_bg)
            art["global_bg"]["path"] = arcname
    return packaged


def build_oradio_preview_data(manifest: Dict[str, Any], station_dir: Optional[Path]) -> Dict[str, Any]:
    assets, lock = collect_station_assets(manifest, station_dir)
    return {
        "oradio": package_descriptor_for_manifest(manifest),
        "requirements": station_requirements(manifest),
        "resolution_preview": {
            "bundled_assets": [arcname for _src, arcname in assets],
            "lock": lock,
        },
    }


def simulator_readiness() -> Dict[str, Any]:
    """Report whether the Studio Simulator will run with live LLM narration or the deterministic
    authoring scaffold. The Simulator is a *creator* tool, so it MAY run LLM-free (the offline
    authoring scaffold — one of the two honest roles for deterministic text); this only reports
    the mode, it never blocks. The listener-facing artifact still requires live LLM (club membership)."""
    try:
        import provisioning
        status = provisioning.provisioning_status()
        return {
            "live_llm": bool(status.get("ready")),
            "provider": status.get("provider"),
            "error": status.get("error"),
        }
    except Exception as exc:
        return {"live_llm": False, "provider": None, "error": str(exc)}


def broadcast_grammar_demo(spec: Dict[str, Any]) -> List[str]:
    """Run a small transition sequence through the generated station-voice template path.
    This is a Studio authoring preview: no fake live source, no fake listener runtime."""
    generated = import_module_from(META_PLUGINS_DIR / "generated.py", "studio_generated_meta_demo")
    plugin = generated.GeneratedMetaPlugin()
    plugin.spec = spec if isinstance(spec, dict) else {}
    plugin.context = {}
    plugin.mem = {}
    samples = [
        {
            "source": "weather",
            "event_type": "forecast",
            "title": "Rain moving in",
            "body": "A light weather update is active in the background.",
            "priority": 30,
        },
        {
            "source": "coding_harness",
            "event_type": "test_failed",
            "title": "Harness failed",
            "body": "The coding harness became active after a failing test run.",
            "priority": 45,
        },
        {
            "source": "operations",
            "event_type": "breaking_event",
            "title": "Ops alert",
            "body": "A higher-priority operations event is ready for immediate follow-up.",
            "priority": 90,
        },
        {
            "source": "coding_harness",
            "event_type": "followup",
            "title": "Returning to the harness",
            "body": "The coding thread has follow-up context after the interruption.",
            "priority": 62,
        },
    ]
    mem: Dict[str, Any] = {}
    lines = ["Broadcast Grammar demo", "weather -> coding -> operations -> coding", ""]
    for sample in samples:
        pkt = plugin.generate_script(sample, mem)
        transition = pkt.get("transition_request") if isinstance(pkt, dict) else {}
        reason = transition.get("transition_reason", "none") if isinstance(transition, dict) else "none"
        lines.append(f"[{sample['source']} | {reason}] {pkt.get('host_intro', '').strip()}")
        if pkt.get("summary"):
            lines.append(f"  {pkt['summary']}")
    return lines


@dataclass
class Project:
    station_id: str
    path: Path
    manifest: Dict[str, Any]

    @property
    def name(self) -> str:
        station = self.manifest.get("station") or {}
        return str(station.get("name") or self.station_id)


def station_relative_path(station_dir: Path, value: Any, default_name: str) -> Path:
    raw = str(value or default_name).strip() or default_name
    path = Path(raw)
    if path.is_absolute():
        return path
    return station_dir / path


class PreviewProcess:
    """Builder-only simulator process using the real bookmark.py kernel."""

    def __init__(self, events: "queue.Queue[str]"):
        self.events = events
        self.proc: Optional[subprocess.Popen[str]] = None
        self.project: Optional[Project] = None
        self._log_file: Any = None
        self._thread: Optional[threading.Thread] = None

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def launch(self, project: Project) -> None:
        if not RUNTIME_PATH.exists():
            raise FileNotFoundError(f"Playback kernel not found: {RUNTIME_PATH}")

        self.stop(quiet=True)
        project.path.mkdir(parents=True, exist_ok=True)
        manifest = project.manifest or read_yaml(project.path / "manifest.yaml")
        paths = manifest.get("paths") if isinstance(manifest.get("paths"), dict) else {}

        env = os.environ.copy()
        env["STATION_DIR"] = str(project.path)
        env["STATION_DB_PATH"] = str(station_relative_path(project.path, paths.get("db"), "station.sqlite"))
        env["STATION_MEMORY_PATH"] = str(station_relative_path(project.path, paths.get("memory"), "station_memory.json"))
        env["RADIO_OS_ROOT"] = str(BASE_DIR)
        env["RADIO_OS_PLUGINS"] = str(PLUGINS_DIR)
        env["RADIO_OS_VOICES"] = str(VOICES_DIR)
        env["RADIO_OS_HEADLESS"] = "1"
        env["RADIO_OS_STUDIO_PREVIEW"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        global_cfg = read_global_config()
        env_vars = global_cfg.get("environment", {}) if isinstance(global_cfg.get("environment"), dict) else {}
        for var_name, var_value in env_vars.items():
            if isinstance(var_name, str) and isinstance(var_value, str) and var_value.strip():
                env[var_name] = var_value

        default_models = global_cfg.get("default_models", {}) if isinstance(global_cfg.get("default_models"), dict) else {}
        openai_key = str(default_models.get("openai_api_key", "")).strip()
        if openai_key and "OPENAI_API_KEY" not in env:
            env["OPENAI_API_KEY"] = openai_key

        visual_cfg = global_cfg.get("visual_models", {}) if isinstance(global_cfg.get("visual_models"), dict) else {}
        station_visual = manifest.get("visual_models") if isinstance(manifest.get("visual_models"), dict) else {}
        visual_cfg = {**visual_cfg, **station_visual}
        if visual_cfg:
            env["VISUAL_MODEL_TYPE"] = str(visual_cfg.get("model_type", "local"))
            env["VISUAL_MODEL_LOCAL"] = str(visual_cfg.get("local_model", ""))
            env["VISUAL_MODEL_API_PROVIDER"] = str(visual_cfg.get("api_provider", ""))
            env["VISUAL_MODEL_API_MODEL"] = str(visual_cfg.get("api_model", ""))
            env["VISUAL_MODEL_API_KEY"] = str(visual_cfg.get("api_key", ""))
            env["VISUAL_MODEL_API_ENDPOINT"] = str(visual_cfg.get("api_endpoint", ""))
            env["VISUAL_MODEL_MAX_IMAGE_SIZE"] = str(visual_cfg.get("max_image_size", "1024"))
            env["VISUAL_MODEL_IMAGE_QUALITY"] = str(visual_cfg.get("image_quality", "85"))

        audio_dir = project.path / ".audio_pipe"
        audio_dir.mkdir(parents=True, exist_ok=True)
        log_path = project.path / "runtime.log"
        self._log_file = log_path.open("a", encoding="utf-8", errors="ignore")
        self._log_file.write(f"\n\n===== STUDIO PREVIEW {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        self._log_file.flush()

        kwargs: Dict[str, Any] = {
            "cwd": str(BASE_DIR),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if sys.platform == "win32" and not os.environ.get("RADIO_OS_SHOW_CONSOLE"):
            try:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            except Exception:
                pass

        self.proc = subprocess.Popen([sys.executable, "-u", str(RUNTIME_PATH)], **kwargs)
        self.project = project
        self.events.put(f"Studio preview started for {project.station_id} (PID {self.proc.pid})")
        self._thread = threading.Thread(target=self._capture_output, args=(self.proc,), daemon=True)
        self._thread.start()

    def stop(self, *, quiet: bool = False) -> None:
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self._thread and self._thread.is_alive():
            try:
                self._thread.join(timeout=1)
            except Exception:
                pass
        try:
            if self._log_file:
                self._log_file.flush()
                self._log_file.close()
        except Exception:
            pass
        self.proc = None
        self.project = None
        self._log_file = None
        self._thread = None
        if not quiet:
            self.events.put("Studio preview stopped")

    def _capture_output(self, proc: subprocess.Popen[str]) -> None:
        try:
            if not proc.stdout:
                return
            while True:
                line = proc.stdout.readline()
                if line:
                    self._write_log(line)
                    self.events.put(line.rstrip())
                    continue
                if proc.poll() is not None:
                    break
            remaining = proc.stdout.read() if proc.stdout else ""
            if remaining:
                self._write_log(remaining)
                for line in remaining.splitlines():
                    self.events.put(line)
            self.events.put(f"Studio preview exited with code {proc.poll()}")
        except Exception as exc:
            self.events.put(f"Studio preview log capture failed: {exc}")

    def _write_log(self, text: str) -> None:
        try:
            if self._log_file:
                self._log_file.write(text)
                self._log_file.flush()
        except Exception:
            pass


def load_projects() -> List[Project]:
    projects: List[Project] = []
    if not STATIONS_DIR.exists():
        return projects
    for child in sorted(STATIONS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.yaml"
        if not manifest_path.exists():
            continue
        projects.append(Project(child.name, child, read_yaml(manifest_path)))
    return projects


def deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def slugify_label(raw: Any, fallback: str = "custom") -> str:
    text = str(raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or fallback


def title_from_slug(slug: str) -> str:
    return str(slug or "").replace("_", " ").strip().title()


def tag_strength(level: int) -> float:
    return TAG_STRENGTHS.get(int(level or 0), 0.0)


def is_safe_custom_tag(raw: str) -> bool:
    text = str(raw or "").strip().lower()
    if not text or len(text) > 80:
        return False
    if any(blocked in text for blocked in CUSTOM_TAG_BLOCKLIST):
        return False
    if any(token in text for token in ("```", "{{", "}}", "<system", "</system", "$(", "&&")):
        return False
    return True


def infer_custom_tag(raw: str) -> Dict[str, Any]:
    label = " ".join(str(raw or "").strip().split())
    if not is_safe_custom_tag(label):
        raise ValueError("That tag reads like an instruction instead of a station descriptor.")
    suggestions = custom_tag_suggestions(label, include_create=False)
    for suggestion in suggestions:
        if slugify_label(suggestion.get("label")) == slugify_label(label):
            out = deepcopy_json(suggestion)
            out.setdefault("strength", 0.7)
            out["source"] = "user"
            return out
    if suggestions and suggestions[0].get("source") == "suggested":
        out = deepcopy_json(suggestions[0])
        out.setdefault("strength", 0.7)
        out["source"] = "user"
        return out
    display_label = label if any(ch.isupper() for ch in label) else label.title()
    lower = label.lower()
    emoji = "✨"
    category = "custom_voice"
    if "pirate" in lower:
        emoji = "🏴‍☠️"
    elif "shakespeare" in lower or "poet" in lower:
        emoji = "🎭"
        category = "custom_voice"
    elif "detective" in lower or "noir" in lower:
        emoji = "🕵"
        category = "custom_voice"
    elif "research" in lower or "science" in lower:
        emoji = "🧪"
        category = "custom_lens"
    elif "sports" in lower or "league" in lower:
        emoji = "🏀"
        category = "custom_lens"
    elif "calm" in lower:
        emoji = "🧘"
        category = "custom_energy"
    return {
        "label": display_label[:80],
        "slug": slugify_label(label, "custom_tag"),
        "emoji": emoji,
        "category": category,
        "description": f"Frames events through a {display_label} station flavor.",
        "strength": 0.7,
        "source": "user",
    }


def custom_tag_suggestions(raw: str, *, include_create: bool = True) -> List[Dict[str, Any]]:
    query = str(raw or "").strip().lower()
    if not query:
        return []
    suggestions: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: Dict[str, Any], source: str = "system") -> None:
        label = str(item.get("label") or title_from_slug(str(item.get("slug") or ""))).strip()
        if not label:
            return
        slug = slugify_label(item.get("slug") or label, "tag")
        if slug in seen:
            return
        seen.add(slug)
        suggestions.append({
            "emoji": str(item.get("emoji") or "✨"),
            "label": label,
            "slug": slug,
            "category": str(item.get("category") or "custom_voice"),
            "description": str(item.get("description") or f"Frames events through a {label} station flavor."),
            "strength": float(item.get("strength", 0.7) or 0.7),
            "source": source,
        })

    for keywords, items in CUSTOM_TAG_SUGGESTION_RULES:
        if any(word in query for word in keywords):
            for item in items:
                add(item, "suggested")
    for tag in TAG_CATALOG:
        hay = f"{tag.get('label', '')} {tag.get('slug', '')} {tag.get('category', '')}".lower()
        if query in hay:
            add(tag, "system")
    if include_create and is_safe_custom_tag(raw):
        add({
            "emoji": infer_custom_tag_emoji(raw),
            "label": f'Create "{str(raw).strip()}"',
            "slug": f"create_{slugify_label(raw, 'tag')}",
            "category": "create",
            "description": f"Create a custom station descriptor from {raw}.",
        }, "action")
    return suggestions[:5]


def infer_custom_tag_emoji(raw: str) -> str:
    text = str(raw or "").lower()
    if "pirate" in text:
        return "🏴‍☠️"
    if "shakespeare" in text or "poet" in text:
        return "🎭"
    if "detective" in text or "noir" in text:
        return "🕵"
    if "research" in text or "science" in text:
        return "🧪"
    if "sports" in text or "league" in text:
        return "🏀"
    if "calm" in text:
        return "🧘"
    return "✨"


def default_meta_profile(station_name: str = "My Station Voice", host: str = "Host") -> Dict[str, Any]:
    return {
        "version": 1,
        "display_name": station_name or "My Station Voice",
        "show_format": {"primary": "news_desk", "secondary": []},
        "cast": {
            "format": "solo_host",
            "characters": [
                {
                    "id": "host_1",
                    "name": host or "Host",
                    "role": "Host",
                    "bio": "The main voice of the station.",
                    "traits": ["clear", "curious"],
                    "relationship": "Guides the listener through the station world.",
                    "airtime_weight": 1.0,
                }
            ],
        },
        "tags": [],
        "behavior": {
            "avoid_raw_event_dumping": True,
            "talk_about_sources_without_impersonating_them": True,
            "compress_repetition": True,
            "preserve_station_identity": True,
        },
    }


def normalize_meta_profile(profile: Optional[Dict[str, Any]], station_name: str = "Station", host: str = "Host") -> Dict[str, Any]:
    base = default_meta_profile(station_name, host)
    raw = profile if isinstance(profile, dict) else {}
    out = deepcopy_json(base)
    out.update({k: deepcopy_json(v) for k, v in raw.items() if k not in {"show_format", "cast", "tags", "behavior"}})
    show_format = raw.get("show_format") if isinstance(raw.get("show_format"), dict) else {}
    primary = slugify_label(show_format.get("primary") or out["show_format"]["primary"], "news_desk")
    if primary not in FORMAT_BY_SLUG:
        primary = "custom_format"
    secondary = show_format.get("secondary") if isinstance(show_format.get("secondary"), list) else []
    out["show_format"] = {
        "primary": primary,
        "secondary": [slugify_label(x) for x in secondary if slugify_label(x) in FORMAT_BY_SLUG and slugify_label(x) != primary],
    }
    cast = raw.get("cast") if isinstance(raw.get("cast"), dict) else {}
    characters = cast.get("characters") if isinstance(cast.get("characters"), list) else out["cast"]["characters"]
    clean_chars: List[Dict[str, Any]] = []
    for index, char in enumerate(characters):
        if not isinstance(char, dict):
            continue
        name = str(char.get("name") or ("Host" if index == 0 else f"Voice {index + 1}")).strip()
        role = str(char.get("role") or ("Host" if index == 0 else "On-Air Voice")).strip()
        traits = char.get("traits") if isinstance(char.get("traits"), list) else str(char.get("traits") or "").split(",")
        clean_chars.append({
            "id": slugify_label(char.get("id") or name or f"voice_{index + 1}", f"voice_{index + 1}"),
            "name": name,
            "role": role,
            "bio": str(char.get("bio") or "").strip(),
            "traits": [slugify_label(t) for t in traits if str(t).strip()],
            "relationship": str(char.get("relationship") or "").strip(),
            "airtime_weight": max(0.0, min(1.0, float(char.get("airtime_weight", 1.0 if index == 0 else 0.35) or 0.0))),
        })
    if not clean_chars:
        clean_chars = out["cast"]["characters"]
    out["cast"] = {"format": slugify_label(cast.get("format") or out["cast"]["format"], "solo_host"), "characters": clean_chars}
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    clean_tags: List[Dict[str, Any]] = []
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        label = str(tag.get("label") or title_from_slug(str(tag.get("slug") or ""))).strip()
        if not label:
            continue
        source = str(tag.get("source") or "system")
        if source == "user" and not is_safe_custom_tag(label):
            continue
        slug = slugify_label(tag.get("slug") or label, "tag")
        clean_tags.append({
            "slug": slug,
            "label": label[:80],
            "emoji": str(tag.get("emoji") or TAG_BY_SLUG.get(slug, {}).get("emoji") or "✨"),
            "category": str(tag.get("category") or TAG_BY_SLUG.get(slug, {}).get("category") or "custom_voice"),
            "description": str(tag.get("description") or TAG_BY_SLUG.get(slug, {}).get("description") or ""),
            "strength": max(0.0, min(1.0, float(tag.get("strength", 0.7) or 0.0))),
            "source": source,
        })
    out["tags"] = clean_tags
    behavior = raw.get("behavior") if isinstance(raw.get("behavior"), dict) else {}
    out["behavior"].update({k: bool(v) for k, v in behavior.items()})
    return out


def characters_from_meta_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = normalize_meta_profile(profile)
    chars: Dict[str, Any] = {}
    for char in profile["cast"]["characters"]:
        chars[char["id"]] = {
            "role": char["role"],
            "bio": char.get("bio", ""),
            "traits": char.get("traits", []),
            "focus": [tag["slug"] for tag in profile.get("tags", [])[:6]],
            "airtime_weight": char.get("airtime_weight", 0.5),
        }
    return chars


def meta_profile_from_manifest_and_spec(manifest: Dict[str, Any], spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    station = manifest.get("station") if isinstance(manifest.get("station"), dict) else {}
    if isinstance(spec, dict) and isinstance(spec.get("meta_profile"), dict):
        return normalize_meta_profile(spec["meta_profile"], station.get("name") or "Station", station.get("host") or "Host")
    profile = default_meta_profile(station.get("name") or "Station", station.get("host") or "Host")
    chars = manifest.get("characters") if isinstance(manifest.get("characters"), dict) else {}
    if chars:
        profile["cast"]["characters"] = []
        for index, (cid, cfg) in enumerate(chars.items()):
            cfg = cfg if isinstance(cfg, dict) else {}
            profile["cast"]["characters"].append({
                "id": slugify_label(cid, f"voice_{index + 1}"),
                "name": title_from_slug(cid),
                "role": str(cfg.get("role") or title_from_slug(cid)),
                "bio": str(cfg.get("bio") or ""),
                "traits": cfg.get("traits") if isinstance(cfg.get("traits"), list) else [],
                "relationship": str(cfg.get("relationship") or ""),
                "airtime_weight": float(cfg.get("airtime_weight", 1.0 if index == 0 else 0.35) or 0.0),
            })
        profile["cast"]["format"] = "solo_host" if len(profile["cast"]["characters"]) == 1 else "co_hosts"
    if isinstance(spec, dict):
        grammar = spec.get("broadcast_grammar") if isinstance(spec.get("broadcast_grammar"), dict) else {}
        style = str(grammar.get("style") or "")
        for fmt in SHOW_FORMATS:
            if fmt["grammar"] == style:
                profile["show_format"]["primary"] = fmt["slug"]
                break
    return normalize_meta_profile(profile, station.get("name") or "Station", station.get("host") or "Host")


def compile_meta_profile_to_spec(
    profile: Dict[str, Any],
    *,
    base_spec: Optional[Dict[str, Any]] = None,
    station_name: str = "Station",
) -> Dict[str, Any]:
    profile = normalize_meta_profile(profile, station_name)
    spec = deepcopy_json(base_spec or {})
    primary = profile["show_format"]["primary"]
    fmt = FORMAT_BY_SLUG.get(primary, FORMAT_BY_SLUG["news_desk"])
    secondary = [FORMAT_BY_SLUG[s] for s in profile["show_format"].get("secondary", []) if s in FORMAT_BY_SLUG]
    secondary_labels = [item["label"] for item in secondary]
    cast_lines = []
    for char in profile["cast"]["characters"]:
        traits = ", ".join(char.get("traits", [])) or "clear"
        bio = f" Bio: {char['bio']}" if char.get("bio") else ""
        cast_lines.append(f"- {char['name']} ({char['role']}): {traits}.{bio}")
    tag_lines = [
        f"{tag['emoji']} {tag['label']} ({tag['category']}, strength {tag['strength']:.2f})"
        for tag in profile.get("tags", [])
    ]
    spec["station"] = station_name
    spec["meta_profile"] = profile
    spec["voices"] = [char["id"] for char in profile["cast"]["characters"]] or ["host"]
    spec["tone"] = "\n".join([
        f"You are hosting {station_name} as a {fmt['label']} station.",
        ("Secondary format flavors: " + ", ".join(secondary_labels) + ".") if secondary_labels else "Secondary format flavors: none.",
        "The user is casting a show, not writing prompts; preserve the station identity in every segment.",
        "Who is on the air:",
        *cast_lines,
        "Station instincts:",
        *(tag_lines or ["- Clear, grounded, listener-first narration."]),
        "Behavior:",
        "- Avoid raw event dumping; interpret what changed and why it matters.",
        "- Talk about sources without impersonating feeds, harnesses, sensors, or source systems.",
        "- Compress repetition and keep continuity across topic changes.",
        "- Never invent facts or claim hidden data.",
    ])
    grammar = spec.get("broadcast_grammar") if isinstance(spec.get("broadcast_grammar"), dict) else {}
    grammar.update({
        "style": fmt["grammar"],
        "transition_style": fmt["grammar"],
        "recap_behavior": "brief" if not any(t["slug"] == "documentary_narrator" for t in profile.get("tags", [])) else "contextual",
        "callback_behavior": "return with one sentence of context",
        "segment_pacing": "high_energy" if any(t["slug"] in {"hype", "urgency"} for t in profile.get("tags", [])) else "steady",
        "urgency_handling": "interrupt for high-priority changes",
        "secondary_formats": [item["slug"] for item in secondary],
        "format_blend": [fmt["slug"]] + [item["slug"] for item in secondary],
    })
    spec["broadcast_grammar"] = grammar
    spec.setdefault("sources", {})
    return spec


def int_var(var: tk.StringVar, default: int) -> int:
    try:
        return int(float(var.get()))
    except Exception:
        return default


def float_var(var: tk.StringVar, default: float) -> float:
    try:
        return float(var.get())
    except Exception:
        return default


class StudioApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Radio OS Studio")
        self.root.geometry("1480x900")
        self.root.configure(bg=UI["bg"])
        self.root.minsize(1180, 760)

        self.projects: List[Project] = []
        self.project: Optional[Project] = None
        self.signature: Optional[Dict[str, Any]] = None
        self.last_export_path: Optional[Path] = None
        self.preview_queue: "queue.Queue[str]" = queue.Queue()
        self.preview = PreviewProcess(self.preview_queue)
        self.tag_levels: Dict[str, int] = {}
        self.custom_voice_tags: Dict[str, Dict[str, Any]] = {}
        self.tag_buttons: Dict[str, tk.Button] = {}
        self.format_buttons: Dict[str, tk.Button] = {}
        self.secondary_format_vars: Dict[str, tk.BooleanVar] = {}

        self._build_style()
        self._build_layout()
        self.reload_projects()
        self.root.after(500, self._tick_preview)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=UI["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=UI["panel"], foreground=UI["text"], padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", UI["panel_2"])])
        style.configure("TCombobox", fieldbackground=UI["surface"], background=UI["panel"], foreground=UI["text"])

    def _build_layout(self) -> None:
        top = tk.Frame(self.root, bg=UI["bg"], height=58)
        top.pack(fill="x", side="top")
        tk.Label(top, text="Radio OS Studio", font=FONT_H1, fg=UI["text"], bg=UI["bg"]).pack(side="left", padx=18)
        self.status_var = tk.StringVar(value="Build .oradio stations")
        tk.Label(top, textvariable=self.status_var, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left", padx=8)

        tk.Button(top, text="New .oradio", command=self.new_project, bg=UI["accent"], fg="#000", relief="flat").pack(
            side="right", padx=(8, 18), pady=12
        )
        tk.Button(top, text="Refresh", command=self.reload_projects, bg=UI["panel"], fg=UI["text"], relief="flat").pack(
            side="right", padx=4, pady=12
        )

        body = tk.Frame(self.root, bg=UI["bg"])
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=UI["panel"], width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left, text="Drafts", font=FONT_H2, fg=UI["text"], bg=UI["panel"]).pack(anchor="w", padx=14, pady=(14, 8))
        self.project_list = tk.Listbox(
            left,
            bg=UI["surface"],
            fg=UI["text"],
            selectbackground=UI["accent"],
            selectforeground="#000",
            relief="flat",
            font=FONT_BODY,
            exportselection=False,
        )
        self.project_list.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.project_list.bind("<<ListboxSelect>>", lambda _e: self._select_project_from_list())

        self.project_detail = tk.Text(left, height=12, bg=UI["panel_2"], fg=UI["muted"], relief="flat", font=FONT_SMALL, wrap="word")
        self.project_detail.pack(fill="x", padx=12, pady=(0, 12))
        self.project_detail.configure(state="disabled")

        main = tk.Frame(body, bg=UI["bg"])
        main.pack(side="left", fill="both", expand=True, padx=14, pady=(0, 14))

        identity = tk.LabelFrame(main, text="Station organism", fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw")
        identity.pack(fill="x", pady=(0, 10))
        self.station_id_var = tk.StringVar()
        self.station_name_var = tk.StringVar()
        self.host_var = tk.StringVar()
        self.category_var = tk.StringVar()
        self._entry_row(identity, 0, "ID", self.station_id_var, 20)
        self._entry_row(identity, 0, "Name", self.station_name_var, 34, col=2)
        self._entry_row(identity, 1, "Host", self.host_var, 20)
        self._entry_row(identity, 1, "Category", self.category_var, 34, col=2)

        self.nb = ttk.Notebook(main)
        self.nb.pack(fill="both", expand=True)
        self.tab_source = tk.Frame(self.nb, bg=UI["bg"])
        self.tab_feeds = tk.Frame(self.nb, bg=UI["bg"])
        self.tab_meaning = tk.Frame(self.nb, bg=UI["bg"])
        self.tab_production = tk.Frame(self.nb, bg=UI["bg"])
        self.tab_preview = tk.Frame(self.nb, bg=UI["bg"])
        self.tab_export = tk.Frame(self.nb, bg=UI["bg"])
        self.nb.add(self.tab_source, text="1 Source")
        self.nb.add(self.tab_feeds, text="2 Listening")
        self.nb.add(self.tab_meaning, text="3 Who's On The Air?")
        self.nb.add(self.tab_production, text="4 Production")
        self.nb.add(self.tab_preview, text="5 Simulator")
        self.nb.add(self.tab_export, text="6 .oradio")

        self._build_source_tab()
        self._build_feeds_tab()
        self._build_meaning_tab()
        self._build_production_tab()
        self._build_preview_tab()
        self._build_export_tab()

        footer = tk.Frame(main, bg=UI["bg"])
        footer.pack(fill="x", pady=(10, 0))
        tk.Button(footer, text="Save Draft", command=self.save_all, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="right", padx=4)
        tk.Button(footer, text="Start Simulator", command=self.start_preview, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(
            side="right", padx=4
        )
        tk.Button(footer, text="Export .oradio", command=self.export_oradio, bg=UI["accent"], fg="#000", relief="flat").pack(side="right", padx=4)

    def _entry_row(self, parent: tk.Widget, row: int, label: str, var: tk.StringVar, width: int, col: int = 0) -> None:
        tk.Label(parent, text=label, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).grid(row=row, column=col, sticky="w", padx=(12, 6), pady=7)
        tk.Entry(parent, textvariable=var, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", width=width).grid(
            row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=7
        )
        parent.grid_columnconfigure(col + 1, weight=1)

    def _build_source_tab(self) -> None:
        pane = self.tab_source
        top = tk.Frame(pane, bg=UI["bg"])
        top.pack(fill="x", padx=10, pady=10)

        self.antenna_enabled_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top,
            text="HTTP/JSON antenna",
            variable=self.antenna_enabled_var,
            bg=UI["bg"],
            fg=UI["text"],
            selectcolor=UI["panel"],
            activebackground=UI["bg"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))

        self.feed_key_var = tk.StringVar(value="source")
        self.base_url_var = tk.StringVar(value="")
        self.poll_sec_var = tk.StringVar(value="45")
        self.priority_var = tk.StringVar(value="70")
        self.max_items_var = tk.StringVar(value="12")
        self._entry_row(top, 1, "Feed key", self.feed_key_var, 20)
        self._entry_row(top, 1, "Base URL", self.base_url_var, 48, col=2)
        self._entry_row(top, 2, "Poll sec", self.poll_sec_var, 10)
        self._entry_row(top, 2, "Priority", self.priority_var, 10, col=2)
        self._entry_row(top, 2, "Max items", self.max_items_var, 10, col=4)

        split = tk.PanedWindow(pane, orient="horizontal", bg=UI["bg"], sashwidth=6, bd=0)
        split.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        left = tk.Frame(split, bg=UI["bg"])
        right = tk.Frame(split, bg=UI["bg"])
        split.add(left, minsize=360)
        split.add(right, minsize=440)

        tk.Label(left, text="Endpoints (source|path)", font=FONT_H2, fg=UI["text"], bg=UI["bg"]).pack(anchor="w")
        self.endpoints_text = tk.Text(left, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO, height=16)
        self.endpoints_text.pack(fill="both", expand=True, pady=(8, 8))
        btns = tk.Frame(left, bg=UI["bg"])
        btns.pack(fill="x")
        tk.Button(btns, text="Profile Source", command=self.profile_source, bg=UI["accent"], fg="#000", relief="flat").pack(side="left", padx=(0, 6))
        tk.Button(btns, text="Save Source", command=self.save_manifest, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left")

        tk.Label(right, text="Signature Profile", font=FONT_H2, fg=UI["text"], bg=UI["bg"]).pack(anchor="w")
        self.signature_text = tk.Text(right, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO)
        self.signature_text.pack(fill="both", expand=True, pady=(8, 0))

    def _build_feeds_tab(self) -> None:
        """Multi-feed authoring (ported from the OG StationWizard): the antenna roster + a feeds
        editor (add any discovered plugin), a cast/characters editor (role presets), and mix/
        scheduler weights. JSON-backed to match the spec/production editors; discovery-driven adds."""
        pane = self.tab_feeds
        self._discovered_feeds = discover_feed_plugins()

        left = tk.Frame(pane, bg=UI["panel"], width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="Antenna roster", font=FONT_H2, fg=UI["text"], bg=UI["panel"]).pack(anchor="w", padx=12, pady=(12, 2))
        tk.Label(left, text=f"{len(self._discovered_feeds)} feed plugins installed", font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).pack(anchor="w", padx=12)
        self.feed_roster = tk.Listbox(left, bg=UI["surface"], fg=UI["text"], selectbackground=UI["accent"], selectforeground="#000", relief="flat", font=FONT_SMALL, exportselection=False)
        self.feed_roster.pack(fill="both", expand=True, padx=10, pady=8)
        for name in sorted(self._discovered_feeds):
            self.feed_roster.insert("end", name)
        self.feed_roster.bind("<<ListboxSelect>>", lambda _e: self._show_feed_desc())
        self.feed_desc_var = tk.StringVar(value="Select a plugin to see its description.")
        tk.Label(left, textvariable=self.feed_desc_var, font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"], wraplength=270, justify="left", anchor="nw").pack(fill="x", padx=12, pady=(0, 12))

        right = tk.Frame(pane, bg=UI["bg"])
        right.pack(side="left", fill="both", expand=True)

        feeds_box = tk.LabelFrame(right, text="Feeds  ·  beyond HTTP — rss · reddit · document · markets · social · game/sim SDKs", fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw")
        feeds_box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        addrow = tk.Frame(feeds_box, bg=UI["bg"])
        addrow.pack(fill="x", padx=8, pady=6)
        tk.Label(addrow, text="Add feed:", font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left", padx=(2, 4))
        self.add_feed_var = tk.StringVar()
        self.add_feed_combo = ttk.Combobox(addrow, textvariable=self.add_feed_var, values=sorted(self._discovered_feeds), width=24, state="readonly")
        self.add_feed_combo.pack(side="left", padx=4)
        tk.Button(addrow, text="Add", command=self._add_feed, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        tk.Button(addrow, text="Format JSON", command=lambda: self._format_json_editor(self.feeds_text), bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        tk.Label(addrow, text="(the HTTP antenna stays on the Source tab; this manages additional feeds)", font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left", padx=8)
        self.feeds_text = tk.Text(feeds_box, height=12, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO, wrap="none")
        self.feeds_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        mix_box = tk.LabelFrame(right, text="Mix & Scheduler  ·  {\"weights\": {feed: 0.0-1.0}, \"quotas\": {feed: int}}", fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw")
        mix_box.pack(fill="x", padx=10, pady=(6, 10))
        tk.Button(mix_box, text="Format JSON", command=lambda: self._format_json_editor(self.mix_text), bg=UI["panel"], fg=UI["text"], relief="flat").pack(anchor="w", padx=8, pady=(6, 0))
        self.mix_text = tk.Text(mix_box, height=6, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO, wrap="none")
        self.mix_text.pack(fill="x", padx=8, pady=8)

    def _show_feed_desc(self) -> None:
        sel = self.feed_roster.curselection()
        if not sel:
            return
        name = self.feed_roster.get(sel[0])
        info = self._discovered_feeds.get(name, {})
        self.feed_desc_var.set(f"{name} — {info.get('desc') or 'no description'}")

    def _format_json_editor(self, widget: tk.Text) -> None:
        try:
            raw = self._text(widget)
            parsed = json.loads(raw) if raw else {}
            self._set_text(widget, json.dumps(parsed, indent=2, ensure_ascii=False) if parsed else "")
        except Exception as exc:
            messagebox.showerror("JSON", str(exc))

    def _add_feed(self) -> None:
        name = self.add_feed_var.get().strip()
        if not name:
            return
        try:
            feeds = json.loads(self._text(self.feeds_text) or "{}")
            if not isinstance(feeds, dict):
                raise ValueError("Feeds editor must be a JSON object {feed_key: config}.")
        except Exception as exc:
            messagebox.showerror("Add feed", str(exc))
            return
        cfg = feed_default_config(name, self._discovered_feeds)
        cfg["enabled"] = True
        key = name
        i = 2
        while key in feeds:
            key = f"{name}_{i}"
            i += 1
        feeds[key] = cfg
        self._set_text(self.feeds_text, json.dumps(feeds, indent=2, ensure_ascii=False))

    def _add_character(self) -> None:
        role = self.add_role_var.get().strip()
        if not role:
            return
        try:
            cast = json.loads(self._text(self.cast_text) or "{}")
            if not isinstance(cast, dict):
                raise ValueError("Cast editor must be a JSON object {name: character}.")
        except Exception as exc:
            messagebox.showerror("Add role", str(exc))
            return
        cast[role] = deepcopy_json(DEFAULT_CHARSET.get(role, {"role": role, "traits": [], "focus": []}))
        self._set_text(self.cast_text, json.dumps(cast, indent=2, ensure_ascii=False))

    def _cast_card(
        self,
        parent: tk.Widget,
        title: str,
        name_var: tk.StringVar,
        role_var: tk.StringVar,
        bio_var: tk.StringVar,
        traits_var: tk.StringVar,
        weight_var: tk.StringVar,
    ) -> tk.Frame:
        card = tk.LabelFrame(parent, text=title, fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw")
        rows = [
            ("Name", name_var, 24),
            ("Role", role_var, 24),
            ("Short bio", bio_var, 36),
            ("Voice traits", traits_var, 36),
            ("Airtime", weight_var, 8),
        ]
        for row, (label, var, width) in enumerate(rows):
            tk.Label(card, text=label, fg=UI["muted"], bg=UI["bg"], font=FONT_SMALL).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            tk.Entry(card, textvariable=var, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", width=width).grid(
                row=row, column=1, sticky="ew", padx=8, pady=4
            )
        card.grid_columnconfigure(1, weight=1)
        return card

    def select_show_format(self, slug: str) -> None:
        self.show_format_var.set(slug)
        if slug in self.secondary_format_vars:
            self.secondary_format_vars[slug].set(False)
        self.refresh_voice_ui()

    def cycle_tag(self, slug: str) -> None:
        current = int(self.tag_levels.get(slug, 0) or 0)
        nxt = (current + 1) % 4
        if nxt:
            self.tag_levels[slug] = nxt
        else:
            self.tag_levels.pop(slug, None)
        self.refresh_voice_ui()

    def refresh_voice_ui(self) -> None:
        selected_format = self.show_format_var.get() if hasattr(self, "show_format_var") else "news_desk"
        if selected_format in getattr(self, "secondary_format_vars", {}):
            self.secondary_format_vars[selected_format].set(False)
        for slug, btn in getattr(self, "format_buttons", {}).items():
            active = slug == selected_format
            try:
                btn.configure(bg=UI["accent"] if active else UI["panel"], fg="#000" if active else UI["text"])
            except tk.TclError:
                pass
        for slug, btn in list(getattr(self, "tag_buttons", {}).items()):
            tag = TAG_BY_SLUG.get(slug) or self.custom_voice_tags.get(slug, {})
            level = int(self.tag_levels.get(slug, 0) or 0)
            meter = "●" * level + "○" * (3 - level)
            label = f"{tag.get('emoji', '✨')} {tag.get('label', title_from_slug(slug))} {meter}"
            try:
                btn.configure(text=label, bg=UI["panel_2"] if level else UI["panel"], fg=UI["accent"] if level else UI["text"])
            except tk.TclError:
                self.tag_buttons.pop(slug, None)
        self.refresh_custom_tag_buttons()

    def refresh_custom_tag_buttons(self) -> None:
        frame = getattr(self, "custom_tags_frame", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        for slug, tag in self.custom_voice_tags.items():
            btn = tk.Button(frame, text="", command=lambda s=slug: self.cycle_tag(s), bg=UI["panel"], fg=UI["text"], relief="flat")
            btn.pack(side="left", padx=4, pady=2)
            self.tag_buttons[slug] = btn
        for slug in list(self.custom_voice_tags):
            btn = self.tag_buttons.get(slug)
            if btn:
                tag = self.custom_voice_tags[slug]
                level = int(self.tag_levels.get(slug, 0) or 0)
                meter = "●" * level + "○" * (3 - level)
                btn.configure(text=f"{tag.get('emoji', '✨')} {tag.get('label', title_from_slug(slug))} {meter}", bg=UI["panel_2"] if level else UI["panel"], fg=UI["accent"] if level else UI["text"])

    def update_custom_tag_suggestions(self) -> None:
        raw = self.custom_tag_var.get().strip() if hasattr(self, "custom_tag_var") else ""
        if not raw:
            self.custom_suggestions_var.set("Try: shakespeare, noir detective, washed-up pirate broadcaster")
            return
        suggestions = []
        for item in custom_tag_suggestions(raw):
            if item.get("category") == "create":
                suggestions.append(f"➕ {item['label']}")
            else:
                suggestions.append(f"{item['emoji']} {item['label']}")
        if not suggestions:
            suggestions = ["That looks like an instruction, not a station descriptor."]
        self.custom_suggestions_var.set("   ".join(suggestions))

    def add_custom_tag(self) -> None:
        raw = self.custom_tag_var.get().strip()
        if not raw:
            return
        try:
            tag = infer_custom_tag(raw)
        except Exception as exc:
            messagebox.showwarning("Custom Tag", str(exc))
            return
        self.custom_voice_tags[tag["slug"]] = tag
        self.tag_levels[tag["slug"]] = 2
        self.custom_tag_var.set("")
        self.refresh_voice_ui()

    def current_meta_profile_from_form(self) -> Dict[str, Any]:
        station_name = self.station_name_var.get().strip() or self.current_station_id()
        host_name = self.host_name_var.get().strip() or self.host_var.get().strip() or "Host"
        tags: List[Dict[str, Any]] = []
        for slug, level in sorted(self.tag_levels.items()):
            if not level:
                continue
            src = self.custom_voice_tags.get(slug) or TAG_BY_SLUG.get(slug)
            if not src:
                continue
            tag = deepcopy_json(src)
            tag["strength"] = tag_strength(level)
            tag["source"] = tag.get("source") or "system"
            tags.append(tag)
        characters = [
            {
                "id": "host_1",
                "name": host_name,
                "role": self.host_role_var.get().strip() or "Host",
                "bio": self.host_bio_var.get().strip(),
                "traits": [slugify_label(t) for t in self.host_traits_var.get().split(",") if t.strip()],
                "relationship": "Main on-air guide for the station.",
                "airtime_weight": float_var(self.host_weight_var, 0.7),
            }
        ]
        if self.cohost_name_var.get().strip():
            characters.append({
                "id": "cohost_1",
                "name": self.cohost_name_var.get().strip(),
                "role": self.cohost_role_var.get().strip() or "Analyst",
                "bio": self.cohost_bio_var.get().strip(),
                "traits": [slugify_label(t) for t in self.cohost_traits_var.get().split(",") if t.strip()],
                "relationship": "Adds a second perspective on the station world.",
                "airtime_weight": float_var(self.cohost_weight_var, 0.3),
            })
        profile = {
            "version": 1,
            "display_name": f"{station_name} Voice",
            "show_format": {
                "primary": self.show_format_var.get() or "news_desk",
                "secondary": [
                    slug for slug, var in self.secondary_format_vars.items()
                    if bool(var.get()) and slug != (self.show_format_var.get() or "news_desk")
                ],
            },
            "cast": {"format": self.cast_format_var.get() or "solo_host", "characters": characters},
            "tags": tags,
            "behavior": {
                "avoid_raw_event_dumping": True,
                "talk_about_sources_without_impersonating_them": True,
                "compress_repetition": True,
                "preserve_station_identity": True,
            },
        }
        return normalize_meta_profile(profile, station_name, host_name)

    def load_meta_profile_into_form(self, profile: Dict[str, Any]) -> None:
        profile = normalize_meta_profile(profile, self.station_name_var.get().strip() or self.current_station_id(), self.host_var.get().strip() or "Host")
        self.show_format_var.set(profile["show_format"]["primary"])
        for slug, var in self.secondary_format_vars.items():
            var.set(slug in profile["show_format"].get("secondary", []))
        self.cast_format_var.set(profile["cast"]["format"])
        self.tag_levels = {}
        self.custom_voice_tags = {}
        for tag in profile.get("tags", []):
            slug = tag["slug"]
            level = 3 if float(tag.get("strength", 0)) >= 0.9 else 2 if float(tag.get("strength", 0)) >= 0.6 else 1
            self.tag_levels[slug] = level
            if tag.get("source") == "user" or slug not in TAG_BY_SLUG:
                self.custom_voice_tags[slug] = tag
        chars = profile["cast"]["characters"]
        host = chars[0] if chars else default_meta_profile()["cast"]["characters"][0]
        self.host_name_var.set(host.get("name", "Host"))
        self.host_role_var.set(host.get("role", "Host"))
        self.host_bio_var.set(host.get("bio", ""))
        self.host_traits_var.set(", ".join(host.get("traits", [])))
        self.host_weight_var.set(str(host.get("airtime_weight", 0.7)))
        cohost = chars[1] if len(chars) > 1 else {}
        self.cohost_name_var.set(cohost.get("name", ""))
        self.cohost_role_var.set(cohost.get("role", "Analyst"))
        self.cohost_bio_var.set(cohost.get("bio", ""))
        self.cohost_traits_var.set(", ".join(cohost.get("traits", ["storyteller", "human_drama"]) if cohost else ["storyteller", "human_drama"]))
        self.cohost_weight_var.set(str(cohost.get("airtime_weight", 0.3) if cohost else 0.3))
        self._set_text(self.cast_text, json.dumps(characters_from_meta_profile(profile), indent=2, ensure_ascii=False))
        self.refresh_voice_ui()

    def compile_station_voice(self, *, update_editor: bool = True) -> Dict[str, Any]:
        profile = self.current_meta_profile_from_form()
        base_spec = {}
        try:
            base_spec = json.loads(self._text(self.spec_text) or "{}")
            if not isinstance(base_spec, dict):
                base_spec = {}
        except Exception:
            base_spec = {}
        station_name = self.station_name_var.get().strip() or self.current_station_id()
        spec = compile_meta_profile_to_spec(profile, base_spec=base_spec, station_name=station_name)
        if update_editor:
            self._set_text(self.spec_text, json.dumps(spec, indent=2, ensure_ascii=False))
            self._set_text(self.cast_text, json.dumps(characters_from_meta_profile(profile), indent=2, ensure_ascii=False))
        return spec

    def save_station_voice(self) -> bool:
        try:
            self.compile_station_voice(update_editor=True)
            ok = self.save_spec()
            self.status_var.set("Saved station voice")
            return ok
        except Exception as exc:
            messagebox.showerror("Save Station Voice", str(exc))
            return False

    def tune_sample_voice(self) -> None:
        try:
            self.compile_station_voice(update_editor=True)
            self.show_transition_demo()
        except Exception as exc:
            messagebox.showerror("Try This Voice", str(exc))

    def _build_meaning_tab(self) -> None:
        pane = self.tab_meaning
        split = tk.PanedWindow(pane, orient="horizontal", bg=UI["bg"], sashwidth=6, bd=0)
        split.pack(fill="both", expand=True, padx=10, pady=10)
        builder = tk.Frame(split, bg=UI["bg"])
        advanced = tk.Frame(split, bg=UI["panel"])
        split.add(builder, minsize=690)
        split.add(advanced, minsize=420)

        tk.Label(builder, text="Who's On The Air?", font=FONT_H1, fg=UI["text"], bg=UI["bg"]).pack(anchor="w")
        tk.Label(
            builder,
            text="Cast the show, tune the station instincts, then try the voice until it sounds right.",
            font=FONT_SMALL,
            fg=UI["muted"],
            bg=UI["bg"],
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(2, 10))

        action_row = tk.Frame(builder, bg=UI["bg"])
        action_row.pack(fill="x", pady=(0, 10))
        tk.Button(action_row, text="Build From Source Profile", command=self.generate_spec, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(side="left", padx=(0, 6))
        tk.Button(action_row, text="Try This Voice", command=self.tune_sample_voice, bg=UI["accent"], fg="#000", relief="flat").pack(side="left", padx=(0, 6))
        tk.Button(action_row, text="Save Station Voice", command=self.save_station_voice, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left")

        format_box = tk.LabelFrame(builder, text="Show Format", fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw")
        format_box.pack(fill="x", pady=(0, 10))
        self.show_format_var = tk.StringVar(value="news_desk")
        for index, fmt in enumerate(SHOW_FORMATS):
            card = tk.Button(
                format_box,
                text=f"{fmt['label']}\n{fmt['description']}",
                command=lambda slug=fmt["slug"]: self.select_show_format(slug),
                bg=UI["panel"],
                fg=UI["text"],
                relief="flat",
                justify="left",
                anchor="w",
                width=24,
                height=3,
                wraplength=180,
            )
            card.grid(row=index // 4, column=index % 4, sticky="ew", padx=6, pady=6)
            self.format_buttons[fmt["slug"]] = card
            format_box.grid_columnconfigure(index % 4, weight=1)
        secondary = tk.LabelFrame(format_box, text="Secondary flavors (optional)", fg=UI["muted"], bg=UI["bg"], bd=0, labelanchor="nw")
        secondary.grid(row=(len(SHOW_FORMATS) + 3) // 4, column=0, columnspan=4, sticky="ew", padx=6, pady=(8, 6))
        for index, fmt in enumerate(SHOW_FORMATS):
            var = tk.BooleanVar(value=False)
            self.secondary_format_vars[fmt["slug"]] = var
            cb = tk.Checkbutton(
                secondary,
                text=fmt["label"],
                variable=var,
                command=self.refresh_voice_ui,
                bg=UI["bg"],
                fg=UI["text"],
                selectcolor=UI["panel"],
                activebackground=UI["bg"],
                activeforeground=UI["accent"],
                font=FONT_SMALL,
            )
            cb.grid(row=index // 4, column=index % 4, sticky="w", padx=6, pady=2)
            secondary.grid_columnconfigure(index % 4, weight=1)

        cast_box = tk.LabelFrame(builder, text="On-Air Talent / Cast", fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw")
        cast_box.pack(fill="x", pady=(0, 10))
        top = tk.Frame(cast_box, bg=UI["bg"])
        top.pack(fill="x", padx=8, pady=8)
        tk.Label(top, text="Cast format", font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left", padx=(0, 6))
        self.cast_format_var = tk.StringVar(value="solo_host")
        ttk.Combobox(top, textvariable=self.cast_format_var, values=CAST_FORMATS, width=28, state="readonly").pack(side="left")

        cards = tk.Frame(cast_box, bg=UI["bg"])
        cards.pack(fill="x", padx=8, pady=(0, 8))
        self.host_name_var = tk.StringVar(value="Host")
        self.host_role_var = tk.StringVar(value="Host")
        self.host_bio_var = tk.StringVar(value="The main voice of the station.")
        self.host_traits_var = tk.StringVar(value="clear, curious")
        self.host_weight_var = tk.StringVar(value="0.70")
        self.cohost_name_var = tk.StringVar(value="")
        self.cohost_role_var = tk.StringVar(value="Analyst")
        self.cohost_bio_var = tk.StringVar(value="")
        self.cohost_traits_var = tk.StringVar(value="storyteller, human drama")
        self.cohost_weight_var = tk.StringVar(value="0.30")
        self._cast_card(cards, "Host", self.host_name_var, self.host_role_var, self.host_bio_var, self.host_traits_var, self.host_weight_var).pack(side="left", fill="both", expand=True, padx=(0, 6))
        self._cast_card(cards, "Co-host / Analyst", self.cohost_name_var, self.cohost_role_var, self.cohost_bio_var, self.cohost_traits_var, self.cohost_weight_var).pack(side="left", fill="both", expand=True, padx=(6, 0))

        heat_box = tk.LabelFrame(builder, text="Signal Heat - how worlds share the air", fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw")
        heat_box.pack(fill="x", pady=(0, 10))
        tk.Label(
            heat_box,
            text="A world that just lit up earns airtime; a quiet one recedes. Defaults below; per-source overrides live in Advanced Details.",
            font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"], wraplength=760, justify="left",
        ).grid(row=0, column=0, columnspan=8, sticky="w", padx=8, pady=(6, 4))
        self.heat_gain_var = tk.StringVar(value="1.0")
        self.heat_interrupt_var = tk.StringVar(value="0.60")
        self.heat_quiet_var = tk.StringVar(value="0.05")
        self.heat_halflife_var = tk.StringVar(value="1800")
        heat_fields = [
            ("How loud?", self.heat_gain_var),
            ("When can it cut in?", self.heat_interrupt_var),
            ("Quiet before silent?", self.heat_quiet_var),
            ("Cools after (sec)?", self.heat_halflife_var),
        ]
        for idx, (label, var) in enumerate(heat_fields):
            tk.Label(heat_box, text=label, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).grid(row=1, column=idx * 2, sticky="e", padx=(8, 2), pady=(0, 8))
            tk.Entry(heat_box, textvariable=var, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", width=7).grid(row=1, column=idx * 2 + 1, sticky="w", padx=(0, 6), pady=(0, 8))
        tk.Button(heat_box, text="Apply To Station Voice", command=self.apply_signal_heat, bg=UI["panel_2"], fg=UI["text"], relief="flat").grid(row=2, column=0, columnspan=8, sticky="w", padx=8, pady=(0, 8))

        tags_box = tk.LabelFrame(builder, text="Station Instincts - What does this station care about?", fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw")
        tags_box.pack(fill="both", expand=True)
        for shelf, tags in TAG_SHELVES:
            row = tk.Frame(tags_box, bg=UI["bg"])
            row.pack(fill="x", padx=8, pady=(8, 0))
            tk.Label(row, text=shelf, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"], width=12, anchor="w").pack(side="left")
            for tag in tags:
                btn = tk.Button(row, text="", command=lambda slug=tag["slug"]: self.cycle_tag(slug), bg=UI["panel"], fg=UI["text"], relief="flat")
                btn.pack(side="left", padx=4, pady=2)
                self.tag_buttons[tag["slug"]] = btn

        custom = tk.Frame(tags_box, bg=UI["bg"])
        custom.pack(fill="x", padx=8, pady=10)
        tk.Label(custom, text="Add your own", font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"], width=12, anchor="w").pack(side="left")
        self.custom_tag_var = tk.StringVar()
        self.custom_tag_var.trace_add("write", lambda *_: self.update_custom_tag_suggestions())
        tk.Entry(custom, textvariable=self.custom_tag_var, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", width=36).pack(side="left", padx=4)
        tk.Button(custom, text="Create Tag", command=self.add_custom_tag, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        self.custom_suggestions_var = tk.StringVar(value="Try: shakespeare, noir detective, washed-up pirate broadcaster")
        tk.Label(tags_box, textvariable=self.custom_suggestions_var, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"], anchor="w").pack(fill="x", padx=22, pady=(0, 8))
        self.custom_tags_frame = tk.Frame(tags_box, bg=UI["bg"])
        self.custom_tags_frame.pack(fill="x", padx=20, pady=(0, 8))

        tk.Label(advanced, text="Advanced Details", font=FONT_H2, fg=UI["text"], bg=UI["panel"]).pack(anchor="w", padx=10, pady=(10, 2))
        tk.Label(
            advanced,
            text="Power-user view of the compiled station voice artifact. Normal tuning happens on the left.",
            font=FONT_SMALL,
            fg=UI["muted"],
            bg=UI["panel"],
            wraplength=420,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 8))
        adv_controls = tk.Frame(advanced, bg=UI["panel"])
        adv_controls.pack(fill="x", padx=10, pady=(0, 8))
        tk.Button(adv_controls, text="Format Details", command=self.format_spec, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(side="left", padx=(0, 6))
        tk.Button(adv_controls, text="Save Details", command=self.save_spec, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(side="left")
        self.spec_text = tk.Text(advanced, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO, wrap="none", height=20)
        self.spec_text.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        tk.Label(advanced, text="Character details compiled into the station voice", font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).pack(anchor="w", padx=10)
        self.cast_text = tk.Text(advanced, height=6, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO, wrap="none")
        self.cast_text.pack(fill="x", padx=10, pady=(4, 10))
        self.refresh_voice_ui()

    def _build_production_tab(self) -> None:
        pane = self.tab_production
        wrap = tk.Frame(pane, bg=UI["bg"])
        wrap.pack(fill="x", padx=12, pady=12)

        self.llm_provider_var = tk.StringVar(value="ollama")
        self.llm_endpoint_var = tk.StringVar(value="http://127.0.0.1:11434/api/generate")
        self.producer_model_var = tk.StringVar(value="")
        self.host_model_var = tk.StringVar(value="")
        self.navigator_model_var = tk.StringVar(value="")
        self.voice_provider_var = tk.StringVar(value="kokoro")
        self.piper_bin_var = tk.StringVar()
        self.host_voice_var = tk.StringVar()

        self._entry_row(wrap, 0, "LLM provider", self.llm_provider_var, 18)
        self._entry_row(wrap, 0, "Endpoint", self.llm_endpoint_var, 58, col=2)
        self._entry_row(wrap, 1, "Producer", self.producer_model_var, 24)
        self._entry_row(wrap, 1, "Host model", self.host_model_var, 24, col=2)
        self._entry_row(wrap, 1, "Navigator", self.navigator_model_var, 24, col=4)
        self._entry_row(wrap, 2, "Voice provider", self.voice_provider_var, 18)
        self._entry_row(wrap, 2, "Piper bin", self.piper_bin_var, 58, col=2)
        tk.Button(wrap, text="Browse", command=lambda: self._browse_into(self.piper_bin_var), bg=UI["panel"], fg=UI["text"], relief="flat").grid(
            row=2, column=4, padx=6, pady=7
        )
        self._entry_row(wrap, 3, "Host voice", self.host_voice_var, 70)
        tk.Button(wrap, text="Browse", command=lambda: self._browse_into(self.host_voice_var), bg=UI["panel"], fg=UI["text"], relief="flat").grid(
            row=3, column=2, padx=6, pady=7, sticky="w"
        )

        # --- LLM Tune-In: machine-level provisioning ("do it once, you're in the club") ---
        # Live LLM narration is the medium, not a fallback. This validates a provider and
        # saves it at the machine level (shared global config) so every future .oradio is
        # already tuned in. Engine: provisioning.py (stdlib-only, standalone-testable).
        tune = tk.LabelFrame(
            pane, text="LLM Tune-In  ·  machine-level — do it once, you're in the club",
            fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw",
        )
        tune.pack(fill="x", padx=12, pady=(4, 12))

        _gc = read_global_config()
        _dm = _gc.get("default_models", {}) if isinstance(_gc.get("default_models"), dict) else {}
        self.llm_api_key_var = tk.StringVar(
            value=_dm.get("openai_api_key") or _dm.get("anthropic_api_key") or _dm.get("google_api_key") or ""
        )
        self._entry_row(tune, 0, "API key (hosted providers)", self.llm_api_key_var, 58)

        self.llm_status_var = tk.StringVar(value="Tune-In status: not checked yet")
        tk.Label(tune, textvariable=self.llm_status_var, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"], anchor="w").grid(
            row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(2, 8)
        )

        btns = tk.Frame(tune, bg=UI["bg"])
        btns.grid(row=2, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))
        tk.Button(btns, text="Check", command=self._tunein_check, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        tk.Button(btns, text="Save to machine (join club)", command=self._tunein_save, bg=UI["accent"], fg="#000", relief="flat").pack(side="left", padx=4)
        tk.Button(btns, text="Pull model (Ollama)", command=self._tunein_pull, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        self.root.after(400, self._tunein_check)  # show membership status on open

        # --- Production layer: SFX + event rules + scripted interstitials (plan §3) ---
        prod = tk.LabelFrame(
            pane, text="Production  ·  sfx + event rules + scripted interstitials (JSON)",
            fg=UI["text"], bg=UI["bg"], bd=1, relief="solid", labelanchor="nw",
        )
        prod.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        tk.Label(
            prod,
            text="sfx: [{tag, ref, source}] · rules: [{event, stinger, bed, voice, priority, interrupt, cooldown_sec}] · "
                 "interstitials: [{kind, text, every_sec}]   (stingers between LLM = seasoning, never the narration)",
            font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"], anchor="w", justify="left", wraplength=900,
        ).pack(fill="x", padx=10, pady=(6, 2))
        self.production_text = tk.Text(prod, height=10, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO, wrap="none")
        self.production_text.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        prow = tk.Frame(prod, bg=UI["bg"])
        prow.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(prow, text="Format JSON", command=self._format_production, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        tk.Button(prow, text="Source remote SFX", command=self._source_sfx, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        self.production_status_var = tk.StringVar(value="")
        tk.Label(prow, textvariable=self.production_status_var, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left", padx=8)

    def production_block_from_form(self) -> Optional[Dict[str, Any]]:
        """Parse the Production JSON editor; None if empty (preserve existing), raise on invalid."""
        raw = self._text(self.production_text)
        if not raw:
            return None
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Production block must be a JSON object with sfx / rules / interstitials.")
        return parsed

    def _format_production(self) -> None:
        try:
            block = self.production_block_from_form()
        except Exception as exc:
            messagebox.showerror("Production JSON", str(exc))
            return
        self._set_text(self.production_text, json.dumps(block or {}, indent=2, ensure_ascii=False))
        self.production_status_var.set("formatted")

    def _source_sfx(self) -> None:
        try:
            manifest = self.build_manifest()
        except Exception as exc:
            messagebox.showerror("Source SFX", str(exc))
            return
        declared = declared_sfx(manifest)
        remote = [d for d in declared if d["source"] != "local"]
        if not remote:
            self.production_status_var.set("no remote sfx declared")
            return
        self.production_status_var.set(f"sourcing {len(remote)} remote sfx…")
        cache_dir = self.current_project_dir() / "assets_cache" / "sfx"

        def work() -> None:
            try:
                import sfx_sourcing
                out = sfx_sourcing.source_declared_sfx(declared, cache_dir)
                ok = sum(1 for r in out["report"] if r["ok"])
                msg = (f"sourced {ok}/{len(remote)} (key {'set' if out['key_present'] else 'missing'})"
                       if out["key_present"] else "no Freesound API key in settings (asset_sources.freesound.api_key)")
            except Exception as exc:
                msg = f"sourcing failed: {exc}"
            self.root.after(0, lambda: self.production_status_var.set(msg))

        threading.Thread(target=work, daemon=True).start()

    def _tunein_params(self) -> Tuple[str, str, str, str]:
        prov = (self.llm_provider_var.get().strip() or "ollama").lower()
        endpoint = self.llm_endpoint_var.get().strip()
        key = self.llm_api_key_var.get().strip()
        model = self.host_model_var.get().strip() or self.producer_model_var.get().strip()
        return prov, endpoint, key, model

    def _tunein_check(self) -> None:
        prov, endpoint, key, model = self._tunein_params()
        self.llm_status_var.set(f"Checking {prov}…")

        def work() -> None:
            try:
                import provisioning
                res = provisioning.validate_provider(prov, endpoint=endpoint, key=key, model=model)
            except Exception as exc:
                res = {"ok": False, "error": str(exc)}
            self.root.after(0, lambda: self._tunein_result(prov, res))

        threading.Thread(target=work, daemon=True).start()

    def _tunein_result(self, prov: str, res: Dict[str, Any]) -> None:
        if res.get("ok"):
            self.llm_status_var.set(f"In the club ✓   ({prov} ready — every future .oradio is tuned in)")
        else:
            extra = "   → click ‘Pull model (Ollama)’" if res.get("needs_pull") else ""
            self.llm_status_var.set(f"Not tuned in ✗   {prov}: {res.get('error') or 'not ready'}{extra}")

    def _tunein_save(self) -> None:
        prov, endpoint, key, model = self._tunein_params()
        self.llm_status_var.set("Saving to machine…")

        def work() -> None:
            try:
                import provisioning
                provisioning.save_llm_membership(
                    prov, endpoint=endpoint or None, key=key or None,
                    host_model=model or None, producer_model=model or None,
                )
                ok, err = True, None
            except Exception as exc:
                ok, err = False, str(exc)

            def done() -> None:
                if ok:
                    self.llm_status_var.set("Saved ✓ — re-checking…")
                    self._tunein_check()
                else:
                    self.llm_status_var.set(f"Save failed: {err}")

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _tunein_pull(self) -> None:
        prov, endpoint, _key, model = self._tunein_params()
        if prov != "ollama":
            messagebox.showinfo("Pull model", "Model pull applies to the Ollama provider.")
            return
        if not model:
            messagebox.showinfo("Pull model", "Set a Host or Producer model name first.")
            return
        self.llm_status_var.set(f"Pulling {model}…")

        def progress(line: str) -> None:
            self.root.after(0, lambda: self.llm_status_var.set(f"Pulling {model}: {line}"))

        def work() -> None:
            try:
                import provisioning
                r = provisioning.pull_ollama_model(endpoint, model, on_progress=progress)
            except Exception as exc:
                r = {"ok": False, "error": str(exc)}
            self.root.after(0, lambda: self.llm_status_var.set(
                f"Pull complete ✓ — {model} ready" if r.get("ok") else f"Pull failed: {r.get('error')}"
            ))

        threading.Thread(target=work, daemon=True).start()

    def _build_preview_tab(self) -> None:
        pane = self.tab_preview
        header = tk.Frame(pane, bg=UI["bg"])
        header.pack(fill="x", padx=10, pady=10)

        tk.Label(
            header,
            text="Builder Simulator",
            font=FONT_H2,
            fg=UI["text"],
            bg=UI["bg"],
        ).pack(anchor="w")
        tk.Label(
            header,
            text=(
                "Play-in-editor preview for station authors. This runs the current draft through "
                "bookmark.py, but normal listeners still open .oradio files or use the library/player."
            ),
            font=FONT_SMALL,
            fg=UI["muted"],
            bg=UI["bg"],
            wraplength=980,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        controls = tk.Frame(pane, bg=UI["bg"])
        controls.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(
            controls,
            text="Save Draft + Start Simulator",
            command=self.start_preview,
            bg=UI["accent"],
            fg="#000",
            relief="flat",
        ).pack(side="left", padx=(0, 6))
        tk.Button(controls, text="Stop", command=self.stop_preview, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left", padx=(0, 6))
        tk.Button(
            controls,
            text="Refresh Log",
            command=self.load_preview_log,
            bg=UI["panel"],
            fg=UI["text"],
            relief="flat",
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            controls,
            text="Show Transition Demo",
            command=self.show_transition_demo,
            bg=UI["panel"],
            fg=UI["accent"],
            relief="flat",
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            controls,
            text="Open Audio Pipe",
            command=self.open_preview_audio_pipe,
            bg=UI["panel"],
            fg=UI["text"],
            relief="flat",
        ).pack(side="left")

        self.preview_status_var = tk.StringVar(value="Simulator idle")
        tk.Label(
            pane,
            textvariable=self.preview_status_var,
            font=FONT_SMALL,
            fg=UI["muted"],
            bg=UI["bg"],
        ).pack(anchor="w", padx=10, pady=(0, 6))

        log_wrap = tk.Frame(pane, bg=UI["bg"])
        log_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.preview_log_text = tk.Text(
            log_wrap,
            bg=UI["surface"],
            fg=UI["text"],
            insertbackground=UI["text"],
            relief="flat",
            font=FONT_MONO,
            wrap="word",
        )
        scroll = tk.Scrollbar(log_wrap, command=self.preview_log_text.yview)
        self.preview_log_text.configure(yscrollcommand=scroll.set)
        self.preview_log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_export_tab(self) -> None:
        pane = self.tab_export
        controls = tk.Frame(pane, bg=UI["bg"])
        controls.pack(fill="x", padx=10, pady=10)
        tk.Button(controls, text="Refresh Package Preview", command=self.refresh_export_preview, bg=UI["panel"], fg=UI["text"], relief="flat").pack(
            side="left", padx=(0, 6)
        )
        tk.Button(
            controls,
            text="Check Readiness",
            command=self.check_export_readiness,
            bg=UI["panel_2"],
            fg=UI["text"],
            relief="flat",
        ).pack(side="left", padx=(0, 6))
        tk.Button(controls, text="Export .oradio", command=self.export_oradio, bg=UI["accent"], fg="#000", relief="flat").pack(side="left")
        self.export_status_var = tk.StringVar(value="Readiness: not checked")
        tk.Label(
            pane,
            textvariable=self.export_status_var,
            font=FONT_SMALL,
            fg=UI["muted"],
            bg=UI["bg"],
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 6))
        self.export_text = tk.Text(pane, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO, wrap="word")
        self.export_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _browse_into(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(parent=self.root)
        if path:
            var.set(path)

    def reload_projects(self, select_id: Optional[str] = None) -> None:
        if select_id is None and self.project:
            select_id = self.project.station_id
        self.projects = load_projects()
        self.project_list.delete(0, "end")
        selected_index = 0
        for index, project in enumerate(self.projects):
            antenna_key, _ = find_antenna_feed(project.manifest)
            spec_path = project.path / "meta_plugin_spec.json"
            marks = []
            if antenna_key:
                marks.append("source")
            if project.manifest.get("meta_plugin") == "generated":
                marks.append("meaning")
            if spec_path.exists():
                marks.append("voice")
            suffix = f"  [{' / '.join(marks)}]" if marks else ""
            self.project_list.insert("end", f"{project.station_id}{suffix}")
            if select_id and project.station_id == select_id:
                selected_index = index
        if self.projects:
            self.project_list.selection_clear(0, "end")
            self.project_list.selection_set(selected_index)
            self.project_list.activate(selected_index)
            self.load_project(self.projects[selected_index])
        else:
            self.new_project()

    def _select_project_from_list(self) -> None:
        sel = self.project_list.curselection()
        if not sel:
            return
        self.load_project(self.projects[sel[0]])

    def new_project(self) -> None:
        station_id = "UntitledStation"
        manifest = default_manifest(station_id, "Untitled Station", "Host")
        self.project = Project(station_id, STATIONS_DIR / station_id, manifest)
        self.signature = None
        self._load_manifest_into_form(manifest, station_id)
        self.load_meta_profile_into_form(default_meta_profile("Untitled Station", "Host"))
        self._set_text(self.signature_text, "No signature profile yet.")
        self._set_text(self.spec_text, "")
        self._set_project_detail("Draft .oradio station. Save Draft will create a project folder.")
        self.refresh_export_preview()
        self.status_var.set("Draft .oradio station")

    def load_project(self, project: Project) -> None:
        project.manifest = read_yaml(project.path / "manifest.yaml")
        self.project = project
        self._load_manifest_into_form(project.manifest, project.station_id)
        self.signature = read_json(project.path / "signature.json", None)
        self._set_text(self.signature_text, json.dumps(self.signature, indent=2, ensure_ascii=False) if self.signature else "No signature profile yet.")
        spec = read_json(project.path / "meta_plugin_spec.json", None)
        self._set_text(self.spec_text, json.dumps(spec, indent=2, ensure_ascii=False) if spec else "")
        self.load_meta_profile_into_form(meta_profile_from_manifest_and_spec(project.manifest, spec))
        self.status_var.set(f"Loaded {project.station_id}")
        self._render_project_detail(project)
        self.refresh_export_preview()

    def _load_manifest_into_form(self, manifest: Dict[str, Any], station_id: str) -> None:
        station = manifest.get("station") if isinstance(manifest.get("station"), dict) else {}
        self.station_id_var.set(station.get("id") or station_id)
        self.station_name_var.set(station.get("name") or station_id)
        self.host_var.set(station.get("host") or "Host")
        self.category_var.set(station.get("category") or "")

        feed_key, feed = find_antenna_feed(manifest)
        self.antenna_enabled_var.set(bool(feed_key and feed.get("enabled", True)))
        self.feed_key_var.set(feed_key or "source")
        self.base_url_var.set(str(feed.get("base_url", "")))
        self.poll_sec_var.set(str(feed.get("poll_sec", 45)))
        self.priority_var.set(str(feed.get("priority", 70)))
        self.max_items_var.set(str(feed.get("max_items_per_poll", 12)))
        self._set_text(self.endpoints_text, endpoints_to_text(feed.get("endpoints") or []))

        llm = manifest.get("llm") if isinstance(manifest.get("llm"), dict) else {}
        models = manifest.get("models") if isinstance(manifest.get("models"), dict) else {}
        audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        voices = manifest.get("voices") if isinstance(manifest.get("voices"), dict) else {}
        self.llm_provider_var.set(str(llm.get("provider", "ollama")))
        self.llm_endpoint_var.set(str(llm.get("endpoint", "http://127.0.0.1:11434/api/generate")))
        self.producer_model_var.set(str(models.get("producer", "")))
        self.host_model_var.set(str(models.get("host", "")))
        self.navigator_model_var.set(str(models.get("navigator", "")))
        self.voice_provider_var.set(str(audio.get("voices_provider", "kokoro")))
        self.piper_bin_var.set(str(audio.get("piper_bin", "")))
        self.host_voice_var.set(str(voices.get("host", "")))

        production = manifest.get("production") if isinstance(manifest.get("production"), dict) else {}
        self._set_text(self.production_text, json.dumps(production, indent=2, ensure_ascii=False) if production else "")

        # Feeds & Cast tab: the HTTP antenna lives on the Source tab, so the feeds editor shows the
        # OTHER feeds; characters + mix/scheduler round-trip through their editors.
        all_feeds = manifest.get("feeds") if isinstance(manifest.get("feeds"), dict) else {}
        http_key, _http_feed = find_antenna_feed(manifest)
        extra_feeds = {k: v for k, v in all_feeds.items() if k != http_key}
        self._set_text(self.feeds_text, json.dumps(extra_feeds, indent=2, ensure_ascii=False) if extra_feeds else "")
        characters = manifest.get("characters") if isinstance(manifest.get("characters"), dict) else {}
        self._set_text(self.cast_text, json.dumps(characters, indent=2, ensure_ascii=False) if characters else "")
        mix = manifest.get("mix") if isinstance(manifest.get("mix"), dict) else {}
        sched = manifest.get("scheduler") if isinstance(manifest.get("scheduler"), dict) else {}
        weights = mix.get("weights") if isinstance(mix.get("weights"), dict) else {}
        quotas = sched.get("source_quotas") if isinstance(sched.get("source_quotas"), dict) else {}
        mix_block = {"weights": weights, "quotas": quotas}
        self._set_text(self.mix_text, json.dumps(mix_block, indent=2, ensure_ascii=False) if (weights or quotas) else "")

    def _json_editor_block(self, widget: tk.Text) -> Optional[Dict[str, Any]]:
        """Parse a JSON-object editor; None if empty (preserve existing), raise on invalid."""
        raw = self._text(widget)
        if not raw:
            return None
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Editor must contain a JSON object.")
        return parsed

    def _render_project_detail(self, project: Project) -> None:
        feed_key, feed = find_antenna_feed(project.manifest)
        spec_exists = (project.path / "meta_plugin_spec.json").exists()
        sig_exists = (project.path / "signature.json").exists()
        lines = [
            f"Station: {project.name}",
            f"Draft folder: {project.path}",
            f"Source feed: {feed_key or 'none'}",
            f"Base URL: {feed.get('base_url', '') if feed else ''}",
            f"Signature: {'yes' if sig_exists else 'no'}",
            f"Station voice: {'yes' if spec_exists else 'no'}",
            f"Last export: {self.last_export_path or 'none'}",
        ]
        self._set_project_detail("\n".join(lines))

    def _set_project_detail(self, text: str) -> None:
        self.project_detail.configure(state="normal")
        self.project_detail.delete("1.0", "end")
        self.project_detail.insert("1.0", text)
        self.project_detail.configure(state="disabled")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _text(self, widget: tk.Text) -> str:
        return widget.get("1.0", "end").strip()

    def current_station_id(self) -> str:
        return station_id_from_text(self.station_id_var.get())

    def current_project_dir(self) -> Path:
        if self.project and self.project.station_id == self.current_station_id():
            return self.project.path
        return STATIONS_DIR / self.current_station_id()

    def build_manifest(self) -> Dict[str, Any]:
        station_id = self.current_station_id()
        base = deepcopy_json(self.project.manifest) if self.project and self.project.manifest else default_manifest(
            station_id, self.station_name_var.get(), self.host_var.get()
        )
        base.setdefault("station", {})
        base["station"]["id"] = station_id
        base["station"]["name"] = self.station_name_var.get().strip() or station_id
        base["station"]["host"] = self.host_var.get().strip() or "Host"
        base["station"]["category"] = self.category_var.get().strip() or "Live media organism"

        base["meta_plugin"] = "generated"
        base["meta_plugin_spec"] = "meta_plugin_spec.json"
        base.setdefault("llm", {})
        base["llm"]["provider"] = self.llm_provider_var.get().strip() or "ollama"
        base["llm"]["endpoint"] = self.llm_endpoint_var.get().strip()
        base.setdefault("models", {})
        base["models"]["producer"] = self.producer_model_var.get().strip()
        base["models"]["host"] = self.host_model_var.get().strip()
        base["models"]["navigator"] = self.navigator_model_var.get().strip()
        base["models"].setdefault("embedding", "")
        base.setdefault("audio", {})
        base["audio"]["voices_provider"] = self.voice_provider_var.get().strip() or "kokoro"
        base["audio"]["piper_bin"] = self.piper_bin_var.get().strip()
        base.setdefault("voices", {})
        base["voices"]["host"] = self.host_voice_var.get().strip()
        production_block = self.production_block_from_form()
        if production_block is not None:
            base["production"] = production_block
        base["characters"] = characters_from_meta_profile(self.current_meta_profile_from_form())

        feed_key = station_id_from_text(self.feed_key_var.get()).lower()
        endpoints = parse_endpoint_lines(self._text(self.endpoints_text))
        if self.antenna_enabled_var.get() and (not self.base_url_var.get().strip() or not endpoints):
            raise ValueError("HTTP/JSON antenna needs a base URL and at least one endpoint.")

        feeds = base.setdefault("feeds", {})
        if not isinstance(feeds, dict):
            feeds = {}
            base["feeds"] = feeds
        old_key, _old_feed = find_antenna_feed(base)
        if old_key and old_key != feed_key:
            feeds.pop(old_key, None)
        if self.antenna_enabled_var.get():
            feeds[feed_key] = {
                "enabled": True,
                "plugin": "antenna_http",
                "base_url": self.base_url_var.get().strip(),
                "endpoints": endpoints,
                "poll_sec": int_var(self.poll_sec_var, 45),
                "priority": float_var(self.priority_var, 70.0),
                "max_items_per_poll": int_var(self.max_items_var, 12),
                "write_signature": True,
            }

        base.setdefault("mix", {})
        weights = base["mix"].setdefault("weights", {})
        if isinstance(weights, dict):
            weights.setdefault(feed_key, 1.0)
        base.setdefault("scheduler", {})
        quotas = base["scheduler"].setdefault("source_quotas", {})
        if isinstance(quotas, dict):
            quotas.setdefault(feed_key, 6)
        # Feeds & Cast tab overlays: additional feeds + mix weights / scheduler quotas.
        extra_feeds = self._json_editor_block(self.feeds_text)
        if extra_feeds:
            feeds.update(extra_feeds)
        mix_block = self._json_editor_block(self.mix_text)
        if mix_block:
            if isinstance(mix_block.get("weights"), dict):
                base["mix"]["weights"] = mix_block["weights"]
            if isinstance(mix_block.get("quotas"), dict):
                base["scheduler"]["source_quotas"] = mix_block["quotas"]
        base.setdefault("paths", {"db": "station.sqlite", "memory": "station_memory.json"})
        return base

    def save_manifest(self, *, refresh: bool = True) -> None:
        manifest = self.build_manifest()
        station_id = manifest["station"]["id"]
        station_dir = STATIONS_DIR / station_id
        write_yaml(station_dir / "manifest.yaml", manifest)
        self.project = Project(station_id, station_dir, manifest)
        self.status_var.set(f"Saved draft for {station_id}")
        if refresh:
            self.reload_projects(select_id=station_id)
        elif self.project:
            self._render_project_detail(self.project)

    def save_spec(self) -> bool:
        station_dir = self.current_project_dir()
        raw = self._text(self.spec_text)
        if not raw:
            messagebox.showwarning("Station Voice", "There are no station voice details to save.")
            return False
        try:
            spec = json.loads(raw)
        except Exception as exc:
            messagebox.showerror("Station Voice Details", str(exc))
            return False
        write_json(station_dir / "meta_plugin_spec.json", spec)
        self.status_var.set(f"Saved station voice for {station_dir.name}")
        return True

    def save_all(self) -> bool:
        try:
            self.compile_station_voice(update_editor=True)
            spec_raw = self._text(self.spec_text)
            signature = self.signature
            self.save_manifest(refresh=False)
            station_dir = self.current_project_dir()
            if spec_raw:
                spec = json.loads(spec_raw)
                write_json(station_dir / "meta_plugin_spec.json", spec)
            if signature:
                write_json(station_dir / "signature.json", signature)
            self.reload_projects(select_id=self.current_station_id())
            self.refresh_export_preview()
            return True
        except Exception as exc:
            messagebox.showerror("Save Draft", str(exc))
            return False

    def profile_source(self) -> None:
        try:
            manifest = self.build_manifest()
            station_id = manifest["station"]["id"]
            station_dir = STATIONS_DIR / station_id
            write_yaml(station_dir / "manifest.yaml", manifest)
        except Exception as exc:
            messagebox.showerror("Profile Source", str(exc))
            return

        self.status_var.set("Profiling source...")
        self._set_text(self.signature_text, "Profiling source. This may take a few seconds...")
        endpoints = parse_endpoint_lines(self._text(self.endpoints_text))
        base_url = self.base_url_var.get().strip()

        def work() -> None:
            try:
                antenna = import_module_from(PLUGINS_DIR / "antenna_http.py", "studio_antenna_http")
                signature = antenna.build_signature(base_url, [(e["path"], e["source"]) for e in endpoints])
                write_json(station_dir / "signature.json", signature)
                self.root.after(0, lambda: self._profile_done(signature, station_id))
            except Exception as exc:
                self.root.after(0, lambda: self._profile_failed(exc))

        threading.Thread(target=work, daemon=True).start()

    def _profile_done(self, signature: Dict[str, Any], station_id: str) -> None:
        self.signature = signature
        self._set_text(self.signature_text, json.dumps(signature, indent=2, ensure_ascii=False))
        self.status_var.set(f"Profiled {station_id}")
        self.reload_projects(select_id=station_id)

    def _profile_failed(self, exc: Exception) -> None:
        self.status_var.set("Profile failed")
        self._set_text(self.signature_text, f"Profile failed:\n{exc}")

    def generate_spec(self) -> None:
        if not self.signature:
            existing = read_json(self.current_project_dir() / "signature.json", None)
            if isinstance(existing, dict):
                self.signature = existing
        if not self.signature:
            messagebox.showwarning("Station Voice", "Profile a source first, then build the station voice.")
            return
        try:
            generated = import_module_from(META_PLUGINS_DIR / "generated.py", "studio_generated_meta")
            voices = ["host"]
            spec = generated.generate_meta_plugin_spec(
                self.signature,
                station_name=self.station_name_var.get().strip() or self.current_station_id(),
                voices=voices,
            )
            spec = compile_meta_profile_to_spec(
                self.current_meta_profile_from_form(),
                base_spec=spec,
                station_name=self.station_name_var.get().strip() or self.current_station_id(),
            )
            self._set_text(self.spec_text, json.dumps(spec, indent=2, ensure_ascii=False))
            self._load_signal_heat_fields(spec)
            self.save_spec()
            self.status_var.set("Built and saved station voice from source profile")
        except Exception as exc:
            messagebox.showerror("Build Station Voice", str(exc))

    def format_spec(self) -> None:
        try:
            spec = json.loads(self._text(self.spec_text) or "{}")
            self._set_text(self.spec_text, json.dumps(spec, indent=2, ensure_ascii=False))
            self._load_signal_heat_fields(spec)
        except Exception as exc:
            messagebox.showerror("Station Voice Details", str(exc))

    def _load_signal_heat_fields(self, spec: Dict[str, Any]) -> None:
        """Populate the Signal Heat entry fields from a spec's global signal_heat block."""
        block = spec.get("signal_heat") if isinstance(spec.get("signal_heat"), dict) else {}
        if not block:
            return
        for var, key, default in (
            (self.heat_gain_var, "gain", "1.0"),
            (self.heat_interrupt_var, "interrupt_threshold", "0.60"),
            (self.heat_quiet_var, "quiet_floor", "0.05"),
            (self.heat_halflife_var, "half_life_sec", "1800"),
        ):
            if key in block and block[key] not in (None, ""):
                var.set(str(block[key]))

    def apply_signal_heat(self) -> None:
        """Write the four global Signal Heat defaults into the spec's signal_heat block. Per-source
        overrides and every other key are preserved; this only touches the four globals."""
        raw = self._text(self.spec_text)
        if not raw.strip():
            messagebox.showwarning("Signal Heat", "Build the station voice first, then apply Signal Heat.")
            return
        try:
            spec = json.loads(raw)
        except Exception as exc:
            messagebox.showerror("Signal Heat", f"Station voice details are not valid JSON yet:\n{exc}")
            return

        def _num(var: tk.StringVar, fallback: float) -> float:
            try:
                return float(var.get())
            except (TypeError, ValueError):
                return fallback

        block = spec.get("signal_heat") if isinstance(spec.get("signal_heat"), dict) else {}
        block["gain"] = _num(self.heat_gain_var, 1.0)
        block["interrupt_threshold"] = _num(self.heat_interrupt_var, 0.60)
        block["quiet_floor"] = _num(self.heat_quiet_var, 0.05)
        block["half_life_sec"] = _num(self.heat_halflife_var, 1800.0)
        spec["signal_heat"] = block
        self._set_text(self.spec_text, json.dumps(spec, indent=2, ensure_ascii=False))
        self.save_spec()
        self.status_var.set("Applied Signal Heat defaults to the station voice")

    def package_manifest(self) -> Dict[str, Any]:
        return package_descriptor_for_manifest(self.build_manifest())

    def _portable_warnings(self, manifest: Dict[str, Any]) -> List[str]:
        return portable_warnings_for_manifest(manifest)

    def refresh_export_preview(self) -> None:
        try:
            manifest = self.build_manifest()
            station_dir = self.current_project_dir()
            preview_data = build_oradio_preview_data(manifest, station_dir)
            preview = json.dumps(preview_data, indent=2, ensure_ascii=False)
        except Exception as exc:
            preview = f"Package preview unavailable:\n{exc}"
        self._set_text(self.export_text, preview)

    def check_export_readiness(self) -> None:
        if not self.save_all():
            return
        station_id = self.current_station_id()
        station_dir = self.current_project_dir()
        self.export_status_var.set(f"Readiness: checking {station_id}...")
        self._set_text(self.export_text, "Packaging a temporary .oradio and checking readiness...")

        try:
            manifest = self.build_manifest()
            signature = self.signature or read_json(station_dir / "signature.json", None)
            spec = read_json(station_dir / "meta_plugin_spec.json", None)
            spec_raw = self._text(self.spec_text)
            if spec_raw:
                spec = json.loads(spec_raw)
        except Exception as exc:
            self.export_status_var.set("Readiness: unavailable")
            messagebox.showerror("Check Readiness", str(exc))
            return

        def work() -> None:
            try:
                import oradio_resolver

                with tempfile.TemporaryDirectory(prefix="radio_os_studio_readiness_") as td:
                    target = Path(td) / f"{station_id}.oradio"
                    assets, lock = collect_station_assets(manifest, station_dir)
                    write_oradio_package(
                        target,
                        manifest,
                        signature=signature,
                        spec=spec,
                        assets=assets,
                        lock=lock,
                        station_dir=station_dir,
                    )
                    result = oradio_resolver.resolve_station(target, check_llm=True)
                    report = oradio_resolver.readiness_report(result)
                    payload = {
                        "readiness": result,
                        "report": report,
                    }
            except Exception as exc:
                payload = {"error": str(exc)}
            self.root.after(0, lambda: self._readiness_done(payload))

        threading.Thread(target=work, daemon=True).start()

    def _readiness_done(self, payload: Dict[str, Any]) -> None:
        if payload.get("error"):
            self.export_status_var.set("Readiness: check failed")
            self._set_text(self.export_text, f"Readiness check failed:\n{payload['error']}")
            return
        result = payload.get("readiness", {})
        report = payload.get("report", "")
        ready = bool(result.get("ready"))
        self.export_status_var.set("Readiness: ready to broadcast" if ready else "Readiness: needs Tune-In/cache work")
        detail = {
            "summary": report,
            "readiness": result,
        }
        self._set_text(self.export_text, json.dumps(detail, indent=2, ensure_ascii=False))

    def export_oradio(self) -> None:
        if not self.save_all():
            return
        station_id = self.current_station_id()
        default_path = EXPORTS_DIR / f"{station_id}.oradio"
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export .oradio",
            defaultextension=".oradio",
            initialdir=str(EXPORTS_DIR),
            initialfile=default_path.name,
            filetypes=[("Radio OS station", "*.oradio"), ("Zip archive", "*.zip"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            out = self.write_oradio(Path(target))
            self.last_export_path = out
            lock = getattr(self, "_last_export_lock", {}) or {}
            bundled = [v for v in lock.get("voices", []) if v.get("bundled")]
            unresolved = lock.get("unresolved", [])
            self.status_var.set(f"Exported {out.name} · {len(bundled)} voice(s) bundled")
            self.refresh_export_preview()
            msg = f"Exported:\n{out}\n\nBundled voices: {len(bundled)}\nLLM: required (live narration — provisioned per machine)"
            if unresolved:
                msg += "\n\nCould not bundle (will resolve on the target machine):\n - " + "\n - ".join(unresolved)
            messagebox.showinfo("Export .oradio", msg)
        except Exception as exc:
            messagebox.showerror("Export .oradio", str(exc))

    def write_oradio(self, target: Path) -> Path:
        station_dir = self.current_project_dir()
        manifest = self.build_manifest()
        signature = self.signature or read_json(station_dir / "signature.json", None)
        spec = read_json(station_dir / "meta_plugin_spec.json", None)
        spec_raw = self._text(self.spec_text)
        if spec_raw:
            spec = json.loads(spec_raw)
        # Fetch declared remote SFX so the shipped artifact stays self-contained (best-effort;
        # no key/network just leaves them declared-for-fetch — SFX is seasoning, never fatal).
        sourced: Dict[str, Path] = {}
        if any(d["source"] != "local" for d in declared_sfx(manifest)):
            try:
                import sfx_sourcing
                out = sfx_sourcing.source_declared_sfx(declared_sfx(manifest), station_dir / "assets_cache" / "sfx")
                sourced = out["sourced"]
            except Exception:
                sourced = {}
        assets, lock = collect_station_assets(manifest, station_dir, sourced=sourced)
        self._last_export_lock = lock
        return write_oradio_package(
            target, manifest, signature=signature, spec=spec, assets=assets, lock=lock, station_dir=station_dir
        )

    def _plugin_payloads(self, manifest: Dict[str, Any]) -> List[Tuple[Path, str]]:
        return plugin_payloads_for_manifest(manifest)

    def start_preview(self) -> None:
        if not self.save_all():
            return
        project = self.project
        if not project:
            messagebox.showerror("Builder Simulator", "No saved station project is selected.")
            return
        spec_path = project.path / "meta_plugin_spec.json"
        if not spec_path.exists():
            messagebox.showwarning("Builder Simulator", "Build or save a station voice before previewing the station.")
            return
        try:
            self.preview.launch(project)
            self.nb.select(self.tab_preview)
            ready = simulator_readiness()
            if ready["live_llm"]:
                self.preview_status_var.set(f"Simulator running (live LLM · {ready['provider']}): {project.station_id}")
            else:
                self.preview_status_var.set(
                    f"Simulator running ({project.station_id}) — deterministic authoring scaffold; "
                    f"Tune-In (Production tab) for live narration"
                )
            self.status_var.set(f"Simulating {project.station_id}")
            self.load_preview_log()
        except Exception as exc:
            self.preview_status_var.set("Simulator failed to start")
            messagebox.showerror("Builder Simulator", str(exc))

    def stop_preview(self) -> None:
        self.preview.stop()
        self.preview_status_var.set("Simulator idle")
        self.status_var.set("Simulator stopped")

    def load_preview_log(self) -> None:
        project = self.preview.project or self.project
        if not project:
            self._set_text(self.preview_log_text, "No station selected.")
            return
        log_path = project.path / "runtime.log"
        if not log_path.exists():
            self._set_text(self.preview_log_text, "No runtime log yet. Start the simulator to create one.")
            return
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            self._set_text(self.preview_log_text, "\n".join(lines[-900:]))
            self.preview_log_text.see("end")
        except Exception as exc:
            self._set_text(self.preview_log_text, f"Could not read runtime log:\n{exc}")

    def show_transition_demo(self) -> None:
        try:
            spec: Dict[str, Any] = {}
            raw = self._text(self.spec_text)
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    spec = parsed
            if not spec and self.project:
                loaded = read_json(self.project.path / "meta_plugin_spec.json", None)
                if isinstance(loaded, dict):
                    spec = loaded
            if not spec:
                messagebox.showwarning("Broadcast Grammar", "Build or paste station voice details first.")
                return
            lines = broadcast_grammar_demo(spec)
            self.nb.select(self.tab_preview)
            self._set_text(self.preview_log_text, "\n".join(lines))
            self.preview_status_var.set("Broadcast Grammar transition demo rendered")
        except Exception as exc:
            messagebox.showerror("Broadcast Grammar", str(exc))

    def open_preview_audio_pipe(self) -> None:
        project = self.preview.project or self.project
        if not project:
            messagebox.showwarning("Audio Pipe", "No station selected.")
            return
        audio_dir = project.path / ".audio_pipe"
        audio_dir.mkdir(parents=True, exist_ok=True)
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(audio_dir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(audio_dir)])
            else:
                subprocess.Popen(["xdg-open", str(audio_dir)])
        except Exception as exc:
            messagebox.showerror("Audio Pipe", str(exc))

    def _append_preview_log(self, text: str) -> None:
        if not text:
            return
        self.preview_log_text.insert("end", text.rstrip() + "\n")
        self.preview_log_text.see("end")

    def _tick_preview(self) -> None:
        try:
            while True:
                self._append_preview_log(self.preview_queue.get_nowait())
        except queue.Empty:
            pass

        project = self.preview.project
        if project and self.preview.is_alive():
            audio_dir = project.path / ".audio_pipe"
            wav_count = len(list(audio_dir.glob("*.wav"))) if audio_dir.exists() else 0
            self.preview_status_var.set(f"Simulator running: {project.station_id} | {wav_count} audio segment(s)")
        elif self.preview_status_var.get().startswith("Simulator running"):
            self.preview_status_var.set("Simulator idle")

        self.root.after(500, self._tick_preview)

    def _on_close(self) -> None:
        self.preview.stop(quiet=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    StudioApp().run()


if __name__ == "__main__":
    main()
