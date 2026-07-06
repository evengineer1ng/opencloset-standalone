#!/usr/bin/env python3
"""
The Loom - four-question `.oradio` authoring surface.

This is the focused Loom-native path:
  1. define a universe
  2. define signals
  3. define the skin
  4. define transient surfaces

It compiles those answers into a runnable descriptor-style `.oradio` file that
`loom_player_ui.py` can open directly.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # tkinter is an optional system package (python3-tk); absent in headless/CI
    tk = filedialog = messagebox = ttk = None
from typing import Any, Dict, List, Optional

import yaml

import app_paths
import provisioning
from oradio_engine.club import Club, DEFAULT_THEME, DEFAULT_THEME_PACKS
from oradio_engine.registry import SOURCE_KINDS, SOURCE_META
from oradio_engine.visual_thumbnail import write_visual_thumbnail


# loom_studio.py lives in loom/; the descriptor player (loom_player_ui.py) lives at
# the repo root, so resolve both the player path and exports relative to the root.
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
EXPORTS_DIR = ROOT_DIR / "exports"

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
    "bad": "#ff5c78",
}

FONT_H1 = ("Segoe UI", 20, "bold")
FONT_H2 = ("Segoe UI", 13, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 10)

WORLD_OPTIONS = [
    ("forkuniverse", "ForkUniverse"),
    ("neikos", "Neikos"),
    ("oracle", "Oracle Kingdom"),
    ("ftb", "From The Backmarker"),
    ("none", "No pre-built world"),
]

SIGNAL_LIBRARY = {
    "simulated_spatial_array": {
        "label": "Spatial array",
        "description": "Presence across named nodes. Works immediately with the simulated house path.",
        "target_key": "",
        "target_kind": "",
    },
    "pc_telemetry": {
        "label": "PC telemetry",
        "description": "OS activity and resource changes. Sensitive; asks for consent on open.",
        "target_key": "",
        "target_kind": "",
    },
    "ring_telemetry": {
        "label": "Ring telemetry",
        "description": "Heart rate, motion, and sleep-style signals from the ring path.",
        "target_key": "",
        "target_kind": "",
    },
    "video_capture_sim": {
        "label": "Video capture",
        "description": "A simulated capture-card feed for perception-driven looms.",
        "target_key": "",
        "target_kind": "",
    },
    "moco": {
        "label": "MoCo telemetry",
        "description": "Motion / pose telemetry from a remembered file path or a path you point once.",
        "target_key": "moco",
        "target_kind": "file",
    },
    "atl_league": {
        "label": "ATL league data",
        "description": "A local research / trading league sqlite the loom can listen to.",
        "target_key": "atl_league",
        "target_kind": "file",
    },
}

SIGNAL_OPTIONS = [(key, meta["label"]) for key, meta in SIGNAL_LIBRARY.items() if key in SOURCE_KINDS]

VOICE_PROVIDERS = ["none", "kokoro", "piper", "elevenlabs", "google", "azure"]
VISUAL_FAMILY_OPTIONS = [
    ("color_drift", "Color drift"),
    ("breath", "Breath"),
    ("particles", "Particles"),
    ("ripples", "Ripples"),
    ("bloom", "Bloom"),
    ("scanlines", "Scanlines"),
    ("veil", "Veil"),
    ("orbitals", "Orbitals"),
    ("glitch", "Glitch"),
    ("embers", "Embers"),
    ("grain", "Grain"),
    ("prisms", "Prisms"),
]

# Q1 idea knobs. time_period is a free string on CreationRequest; these are convenient
# presets. genres/tones are multiselect -> genre_mix / tone_mix weighted dicts.
TIME_PERIOD_OPTIONS = [
    "present_day",
    "near_future",
    "far_future",
    "1980s",
    "victorian",
    "medieval",
    "ancient",
    "timeless",
]

GENRE_OPTIONS = ["horror", "drama", "mystery", "thriller", "comedy", "romance", "sci-fi", "fantasy"]

TONE_OPTIONS = ["dread", "tense", "hopeful", "comedic", "melancholic", "wondrous"]

LOOM_PRESETS = {
    "Goosebumps Street": {
        "name": "Fear Street",
        "world_kind": "forkuniverse",
        "premise": "A goosebumps horror neighborhood where a cursed dummy stalks kids.",
        "time_period": "1980s",
        "genres": ["horror"],
        "tones": ["dread", "tense"],
        "location_flavor": "a fog-bound cul-de-sac",
        "starting_context": "The dummy was found on the porch at dawn.",
    },
    "Boardroom Pressure": {
        "name": "Boardroom",
        "world_kind": "forkuniverse",
        "premise": "A tense corporate boardroom tracking quarterly earnings and hostile takeovers.",
        "time_period": "present_day",
        "genres": ["drama", "thriller"],
        "tones": ["tense"],
        "location_flavor": "a glass-walled executive floor",
        "starting_context": "The market opens in twenty minutes.",
    },
    "Living House": {
        "name": "home-region",
        "world_kind": "neikos",
        "premise": "A living house that listens to movement and speaks back.",
        "time_period": "present_day",
        "genres": [],
        "tones": ["wondrous"],
        "location_flavor": "a home stitched together by ambient signals",
        "starting_context": "Presence drifts from room to room.",
        "signals": ["simulated_spatial_array"],
    },
}

TRANSIENT_FORMATS = {
    "Bulletin": "{title}\n\n{body}",
    "Case File": "CASE: {title}\nSOURCE: {source}\nTYPE: {type}\nPRIORITY: {priority}\n\n{body}",
    "Ticker": "{source} / {type} / {priority}\n{title}",
    "Witness Note": "Witness note:\n\n{body}\n\n— surfaced by {source}",
}


def signal_catalog() -> List[Dict[str, Any]]:
    club = Club()
    catalog = []
    for key, label in SIGNAL_OPTIONS:
        spec = SIGNAL_LIBRARY.get(key, {})
        meta = SOURCE_META.get(key, {})
        sensitive = bool(meta.get("sensitive"))
        reads = str(meta.get("reads") or "")
        remembered_target = ""
        if spec.get("target_key"):
            remembered_target = provisioning.get_antenna_target(spec["target_key"])
        if key == "simulated_spatial_array":
            capability = "spatial_array"
            status = "simulated now"
        elif key == "video_capture_sim":
            capability = "capture_card"
            status = "simulated now"
        elif remembered_target:
            capability = spec.get("target_key") or key
            status = f"remembered target: {Path(remembered_target).name}"
        else:
            capability = key
            status = "consent already granted" if club.has_consent(key) else "will ask on open"
        catalog.append({
            "key": key,
            "label": label,
            "description": spec.get("description", ""),
            "sensitive": sensitive,
            "reads": reads,
            "capability": capability,
            "status": status,
            "target_key": spec.get("target_key", ""),
            "target_kind": spec.get("target_kind", ""),
            "remembered_target": remembered_target,
        })
    return catalog


def transient_format_catalog() -> List[Dict[str, Any]]:
    catalog = [{"kind": "builtin", "name": name, "label": f"Builtin · {name}"} for name in TRANSIENT_FORMATS]
    for item in provisioning.list_transient_templates():
        catalog.append({
            "kind": "custom",
            "name": item["name"],
            "label": f"Library · {item['name']}",
            "path": item["path"],
        })
    return catalog


def transient_label_to_name(label: str) -> str:
    for item in transient_format_catalog():
        if item["label"] == label or item["name"] == label:
            return item["name"]
    return label


def transient_name_to_label(name: str) -> str:
    for item in transient_format_catalog():
        if item["name"] == name or item["label"] == name:
            return item["label"]
    return name


def transient_catalog_item(name_or_label: str) -> Optional[Dict[str, Any]]:
    for item in transient_format_catalog():
        if item["name"] == name_or_label or item["label"] == name_or_label:
            return item
    return None


@dataclass
class LoomFormState:
    name: str
    world_kind: str
    seed: int
    premise: str
    enabled_signals: List[str]
    spatial_nodes: List[str]
    loop_mode: str
    builtin_theme: str
    loop_path: str
    voice_provider: str
    voice_assignments: Dict[str, str]
    transient_enabled: bool
    transient_title: str
    transient_min_priority: float
    transient_body_template: str
    # Idea-carrying knobs for the forkuniverse creation request. `time_period` is a
    # REQUIRED field on CreationRequest (its absence crashed the headline path on load);
    # the rest are optional but are where the premise gains real specificity.
    time_period: str = "present_day"
    genres: List[str] = field(default_factory=list)
    tones: List[str] = field(default_factory=list)
    location_flavor: str = ""
    starting_context: str = ""
    custom_signal_kind: str = ""
    custom_signal_name: str = ""
    custom_signal_params_text: str = ""
    moco_telemetry_path: str = ""
    atl_db_path: str = ""
    transient_format: str = "Bulletin"
    transient_template_path: str = ""
    visual_families: List[str] = field(default_factory=lambda: [key for key, _label in VISUAL_FAMILY_OPTIONS])


def station_id_from_text(raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in raw.strip())
    cleaned = cleaned.strip("_-")
    return cleaned or "untitled-loom"


def parse_voice_assignments(raw: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text or "=" not in text:
            continue
        key, value = [part.strip() for part in text.split("=", 1)]
        if key and value:
            mapping[key] = value
    return mapping


def parse_csv_nodes(raw: str) -> List[str]:
    nodes = [part.strip() for part in (raw or "").split(",")]
    return [node for node in nodes if node]


def parse_json_object(raw: str, *, label: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must decode to an object.")
    return data


def default_transient_body() -> str:
    return "{title}\n\n{body}"


def transient_body_from_state(state: LoomFormState) -> str:
    if state.transient_template_path.strip():
        return Path(state.transient_template_path.strip()).read_text(encoding="utf-8")
    selected_name = transient_label_to_name(state.transient_format)
    library_names = {item["name"] for item in provisioning.list_transient_templates()}
    if selected_name in library_names:
        return provisioning.read_transient_template(selected_name)
    if state.transient_body_template.strip():
        return state.transient_body_template.strip()
    return TRANSIENT_FORMATS.get(selected_name, default_transient_body())


def signal_target_from_state(state: LoomFormState, kind: str) -> str:
    if kind == "moco":
        return state.moco_telemetry_path.strip() or provisioning.get_antenna_target("moco")
    if kind == "atl_league":
        return state.atl_db_path.strip() or provisioning.get_antenna_target("atl_league")
    return ""


def build_visual_descriptor(state: LoomFormState, station_id: str) -> Dict[str, Any]:
    if state.loop_mode == "custom" and state.loop_path.strip():
        base = {
            "mode": "media",
            "theme": state.builtin_theme or DEFAULT_THEME,
            "path": state.loop_path.strip(),
        }
    else:
        base = {
            "mode": "builtin",
            "theme": state.builtin_theme or DEFAULT_THEME,
            "path": "",
        }
    return {
        "base": base,
        "tape": {
            "seed": f"{station_id}:{int(state.seed)}",
            "accumulation": "causal",
            "families": state.visual_families or [key for key, _label in VISUAL_FAMILY_OPTIONS],
        },
        "thumbnail": {"mode": "sidecar_png"},
    }


def build_loom_descriptor(state: LoomFormState) -> Dict[str, Any]:
    station_id = station_id_from_text(state.name)
    descriptor: Dict[str, Any] = {
        "oradio": station_id,
        "lens": "identity",
        "surfaces": ["ribbon"],
        "club": [],
    }

    if state.world_kind != "none":
        world: Dict[str, Any] = {
            "organ": state.world_kind,
            "name": station_id,
            "seed": int(state.seed),
        }
        if state.world_kind == "forkuniverse":
            creation: Dict[str, Any] = {
                "universe_title": state.name.strip() or "Untitled Loom",
                "premise": state.premise.strip() or "A world expressed through a Loom artifact.",
                "setting_kind": "custom",
                # Required by CreationRequest — omitting it crashed the headline path on load.
                "time_period": state.time_period.strip() or "present_day",
                "story_mode": "continuous",
                "world_scale": "district",
                "starting_population": 24,
                "seed_mode": "custom",
                "custom_seed": str(state.seed),
                "ontology_domains": ["identity", "pressure", "signal"],
            }
            # Optional idea-carrying knobs: only send them when the author supplied them,
            # so an empty form still produces a clean, minimal creation request.
            if state.genres:
                creation["genre_mix"] = {genre: 1.0 for genre in state.genres}
            if state.tones:
                creation["tone_mix"] = {tone: 1.0 for tone in state.tones}
            if state.location_flavor.strip():
                creation["location_flavor"] = state.location_flavor.strip()
            if state.starting_context.strip():
                creation["starting_context"] = state.starting_context.strip()
            world["creation"] = creation
        descriptor["world"] = world

    telemetry: List[Dict[str, Any]] = []
    for signal in state.enabled_signals:
        if signal == "simulated_spatial_array":
            telemetry.append({
                "source": signal,
                "name": "array",
                "nodes": state.spatial_nodes or ["front_door", "living_room", "kitchen"],
            })
        elif signal == "video_capture_sim":
            telemetry.append({
                "source": signal,
                "name": "capture",
                "frames": [
                    {"title": "idle frame", "body": "A calm loop of observation.", "type": "frame", "priority": 0.3},
                    {"title": "motion frame", "body": "Something in the scene changed.", "type": "frame", "priority": 0.6},
                ],
            })
        elif signal == "moco":
            target = signal_target_from_state(state, "moco")
            if not target:
                raise ValueError("MoCo telemetry needs a remembered or chosen telemetry file.")
            telemetry.append({"source": signal, "name": "moco", "telemetry_path": target})
        elif signal == "atl_league":
            target = signal_target_from_state(state, "atl_league")
            if not target:
                raise ValueError("ATL league input needs a remembered or chosen sqlite file.")
            telemetry.append({"source": signal, "name": "league", "db_path": target})
        else:
            telemetry.append({"source": signal, "name": signal.replace("_telemetry", "").replace("_sim", "")})
    if state.custom_signal_kind.strip():
        custom = {
            "source": state.custom_signal_kind.strip(),
            "name": state.custom_signal_name.strip() or state.custom_signal_kind.strip(),
        }
        custom.update(parse_json_object(state.custom_signal_params_text, label="Custom signal parameters"))
        telemetry.append(custom)
    if telemetry:
        descriptor["telemetry"] = telemetry

    if "world" not in descriptor and not telemetry:
        raise ValueError("The Loom needs at least one world or one signal to produce a runnable .oradio.")

    theme_value = state.builtin_theme if state.loop_mode == "builtin" else state.loop_path.strip()
    descriptor["theme"] = theme_value or DEFAULT_THEME
    descriptor["visual"] = build_visual_descriptor(state, station_id)

    raw_voice = {
        "provider": state.voice_provider,
        "assignments": dict(state.voice_assignments),
    }
    if state.voice_provider != "none":
        descriptor["surfaces"].append("voice")
        descriptor["club"].append("voices")
        descriptor["voice"] = raw_voice
        descriptor.setdefault("effectors", []).append({"kind": "voice", "name": "loom_voice"})

    if "simulated_spatial_array" in state.enabled_signals and state.voice_provider != "none":
        descriptor.setdefault("bindings", []).append({
            "from": "array",
            "to": "loom_voice",
            "transform": "presence_to_speech",
            "name": "array_speaks",
        })

    if state.enabled_signals:
        # Include the LLM capability when the station listens or speaks as a default club expectation.
        if state.voice_provider != "none" and "llm" not in descriptor["club"]:
            descriptor["club"].append("llm")

    if state.transient_enabled:
        descriptor["surfaces"].append("transient")
        descriptor["transient_surfaces"] = [
            {
                "name": station_id_from_text(state.transient_title or "glimpse"),
                "title": state.transient_title or "Transient surface",
                "min_priority": float(state.transient_min_priority),
                "body_template": transient_body_from_state(state),
            }
        ]

    if state.premise.strip():
        descriptor["loom_notes"] = {"premise": state.premise.strip()}

    return descriptor


class LoomStudioApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("The Loom")
        self.root.geometry("1060x860")
        self.root.configure(bg=UI["bg"])

        self.preset_var = tk.StringVar(value="Choose a Loom preset…")
        self.name_var = tk.StringVar(value="my-loom")
        self.world_var = tk.StringVar(value=WORLD_OPTIONS[0][1])
        self.seed_var = tk.StringVar(value="42")
        self.premise_var = tk.StringVar(value="")
        self.time_period_var = tk.StringVar(value=TIME_PERIOD_OPTIONS[0])
        self.location_flavor_var = tk.StringVar(value="")
        self.starting_context_var = tk.StringVar(value="")
        self.genre_vars = {key: tk.BooleanVar(value=False) for key in GENRE_OPTIONS}
        self.tone_vars = {key: tk.BooleanVar(value=False) for key in TONE_OPTIONS}
        self.loop_mode_var = tk.StringVar(value="builtin")
        self.theme_var = tk.StringVar(value=DEFAULT_THEME)
        self.loop_path_var = tk.StringVar(value="")
        self.voice_provider_var = tk.StringVar(value="kokoro")
        self.visual_family_vars = {
            key: tk.BooleanVar(value=True) for key, _label in VISUAL_FAMILY_OPTIONS
        }
        self.spatial_nodes_var = tk.StringVar(value="front_door, living_room, kitchen")
        self.custom_signal_kind_var = tk.StringVar(value="")
        self.custom_signal_name_var = tk.StringVar(value="")
        self.moco_telemetry_path_var = tk.StringVar(value=provisioning.get_antenna_target("moco"))
        self.atl_db_path_var = tk.StringVar(value=provisioning.get_antenna_target("atl_league"))
        self.transient_enabled_var = tk.BooleanVar(value=True)
        self.transient_title_var = tk.StringVar(value="Glimpse")
        self.transient_min_priority_var = tk.StringVar(value="0.6")
        self.transient_format_var = tk.StringVar(value=transient_name_to_label("Bulletin"))
        self.transient_template_path_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Answer the four questions, then export.")
        self.last_export_path: Optional[Path] = None

        self.signal_vars = {key: tk.BooleanVar(value=(key == "simulated_spatial_array")) for key, _ in SIGNAL_OPTIONS}
        self.signal_catalog = signal_catalog()
        self.signal_status_vars = {
            item["key"]: tk.StringVar(value=f"{'Sensitive' if item['sensitive'] else 'Safe'} · {item['status']}")
            for item in self.signal_catalog
        }
        self.transient_catalog = transient_format_catalog()

        self.voice_text = tk.Text(self.root, height=6, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO)
        self.custom_signal_params_text = tk.Text(self.root, height=5, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO)
        self.transient_body_text = tk.Text(self.root, height=6, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO)
        self.preview_text = tk.Text(self.root, height=18, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO)
        self.transient_format_combo: Optional[ttk.Combobox] = None

        self._build()
        self._seed_defaults()
        self.refresh_preview()

    def _build(self) -> None:
        wrap = tk.Frame(self.root, bg=UI["bg"])
        wrap.pack(fill="both", expand=True, padx=18, pady=18)

        tk.Label(wrap, text="The Loom", font=FONT_H1, fg=UI["text"], bg=UI["bg"]).pack(anchor="w")
        tk.Label(
            wrap,
            text="Four questions: universe, signals, skin, surfaces. Export a runnable descriptor-style .oradio.",
            font=FONT_SMALL,
            fg=UI["muted"],
            bg=UI["bg"],
        ).pack(anchor="w", pady=(2, 12))
        preset_row = tk.Frame(wrap, bg=UI["bg"])
        preset_row.pack(fill="x", pady=(0, 8))
        tk.Label(preset_row, text="Try a curated loom", font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left")
        preset_combo = ttk.Combobox(
            preset_row,
            textvariable=self.preset_var,
            values=["Choose a Loom preset…", *LOOM_PRESETS.keys()],
            state="readonly",
            width=28,
        )
        preset_combo.pack(side="left", padx=(10, 8))
        preset_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_selected_preset())
        tk.Label(
            preset_row,
            text="Use a working example to seed Q1–Q4, then mutate it into your own loom.",
            font=FONT_SMALL,
            fg=UI["muted"],
            bg=UI["bg"],
        ).pack(side="left")

        self._question_card(wrap, "1. Define the universe", self._build_question_one)
        self._question_card(wrap, "2. Define the signals", self._build_question_two)
        self._question_card(wrap, "3. Define the skin", self._build_question_three)
        self._question_card(wrap, "4. Define transient surfaces", self._build_question_four)

        preview_card = tk.Frame(wrap, bg=UI["panel"], highlightbackground=UI["line"], highlightthickness=1)
        preview_card.pack(fill="both", expand=True, pady=(12, 0))
        tk.Label(preview_card, text="Compiled .oradio preview", font=FONT_H2, fg=UI["text"], bg=UI["panel"]).pack(anchor="w", padx=14, pady=(12, 8))
        self.preview_text.pack(in_=preview_card, fill="both", expand=True, padx=14, pady=(0, 12))

        footer = tk.Frame(wrap, bg=UI["bg"])
        footer.pack(fill="x", pady=(12, 0))
        tk.Label(footer, textvariable=self.status_var, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left")
        tk.Button(footer, text="Refresh Preview", command=self.refresh_preview, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="right", padx=4)
        tk.Button(footer, text="Open In Player", command=self.open_in_player, bg=UI["panel_2"], fg=UI["text"], relief="flat").pack(side="right", padx=4)
        tk.Button(footer, text="Export .oradio", command=self.export_oradio, bg=UI["accent"], fg="#000", relief="flat").pack(side="right", padx=4)

    def _question_card(self, parent: tk.Widget, title: str, builder) -> None:
        card = tk.Frame(parent, bg=UI["panel"], highlightbackground=UI["line"], highlightthickness=1)
        card.pack(fill="x", pady=6)
        tk.Label(card, text=title, font=FONT_H2, fg=UI["text"], bg=UI["panel"]).pack(anchor="w", padx=14, pady=(12, 8))
        body = tk.Frame(card, bg=UI["panel"])
        body.pack(fill="x", padx=14, pady=(0, 12))
        builder(body)

    def _build_question_one(self, parent: tk.Widget) -> None:
        self._entry_row(parent, 0, "Loom name", self.name_var, 36)
        self._combo_row(parent, 1, "World organ", self.world_var, [label for _key, label in WORLD_OPTIONS])
        self._entry_row(parent, 2, "Seed", self.seed_var, 12)
        self._entry_row(parent, 3, "Premise", self.premise_var, 80)
        self._combo_row(parent, 4, "Time period", self.time_period_var, TIME_PERIOD_OPTIONS)
        self._checkbox_row(parent, 5, "Genre", self.genre_vars)
        self._checkbox_row(parent, 6, "Tone", self.tone_vars)
        self._entry_row(parent, 7, "Location flavor", self.location_flavor_var, 80)
        self._entry_row(parent, 8, "Starting context", self.starting_context_var, 80)

    def _checkbox_row(self, parent: tk.Widget, row: int, label: str, var_map: Dict[str, tk.BooleanVar]) -> None:
        tk.Label(parent, text=label, font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        group = tk.Frame(parent, bg=UI["panel"])
        group.grid(row=row, column=1, columnspan=3, sticky="w", pady=4)
        for idx, (key, var) in enumerate(var_map.items()):
            tk.Checkbutton(
                group,
                text=key,
                variable=var,
                bg=UI["panel"],
                fg=UI["text"],
                activebackground=UI["panel"],
                activeforeground=UI["text"],
                selectcolor=UI["surface"],
                command=self.refresh_preview,
            ).grid(row=idx // 4, column=idx % 4, padx=(0, 10), sticky="w")

    def _build_question_two(self, parent: tk.Widget) -> None:
        signals = tk.Frame(parent, bg=UI["panel"])
        signals.grid(row=0, column=0, columnspan=4, sticky="ew")
        for idx, item in enumerate(self.signal_catalog):
            card = tk.Frame(signals, bg=UI["panel_2"], highlightbackground=UI["line"], highlightthickness=1)
            card.grid(row=idx // 2, column=idx % 2, padx=(0, 10), pady=(0, 10), sticky="nsew")
            tk.Checkbutton(
                card,
                text=item["label"],
                variable=self.signal_vars[item["key"]],
                bg=UI["panel_2"],
                fg=UI["text"],
                activebackground=UI["panel_2"],
                activeforeground=UI["text"],
                selectcolor=UI["surface"],
                command=self.refresh_preview,
            ).pack(anchor="w", padx=10, pady=(8, 4))
            meta = item["description"] or item["reads"] or "Simulated input; no extra machine hookup required."
            tk.Label(card, text=meta, font=FONT_SMALL, fg=UI["muted"], bg=UI["panel_2"], wraplength=310, justify="left").pack(anchor="w", padx=10)
            reads = item["reads"] or "No extra endpoint details."
            tk.Label(card, text=reads, font=FONT_SMALL, fg=UI["muted"], bg=UI["panel_2"], wraplength=310, justify="left").pack(anchor="w", padx=10, pady=(2, 0))
            tk.Label(
                card,
                textvariable=self.signal_status_vars[item["key"]],
                font=FONT_SMALL,
                fg=UI["accent"],
                bg=UI["panel_2"],
            ).pack(anchor="w", padx=10, pady=(4, 8))
        self._entry_row(parent, 1, "Spatial nodes", self.spatial_nodes_var, 80)
        self._entry_row(parent, 2, "MoCo file", self.moco_telemetry_path_var, 52)
        tk.Button(parent, text="Browse + Remember", command=lambda: self._browse_signal_target("moco"), bg=UI["panel_2"], fg=UI["text"], relief="flat").grid(row=2, column=3, padx=6, sticky="w")
        self._entry_row(parent, 3, "ATL sqlite", self.atl_db_path_var, 52)
        tk.Button(parent, text="Browse + Remember", command=lambda: self._browse_signal_target("atl_league"), bg=UI["panel_2"], fg=UI["text"], relief="flat").grid(row=3, column=3, padx=6, sticky="w")
        tk.Label(parent, text="Bring your own source (advanced)", font=FONT_H2, fg=UI["text"], bg=UI["panel"]).grid(row=2, column=0, columnspan=4, sticky="w", pady=(14, 6))
        tk.Label(parent, text="Bring your own source (advanced)", font=FONT_H2, fg=UI["text"], bg=UI["panel"]).grid(row=4, column=0, columnspan=4, sticky="w", pady=(14, 6))
        self._entry_row(parent, 5, "Custom source kind", self.custom_signal_kind_var, 24)
        self._entry_row(parent, 5, "Custom name", self.custom_signal_name_var, 24, col=2)
        tk.Label(parent, text="Custom source parameters (JSON object)", font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 4))
        self.custom_signal_params_text.grid(row=7, column=0, columnspan=4, sticky="ew")

    def _build_question_three(self, parent: tk.Widget) -> None:
        mode = tk.Frame(parent, bg=UI["panel"])
        mode.grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Radiobutton(mode, text="Builtin theme", variable=self.loop_mode_var, value="builtin", command=self.refresh_preview, bg=UI["panel"], fg=UI["text"], selectcolor=UI["surface"]).pack(side="left")
        tk.Radiobutton(mode, text="Custom loop/media path", variable=self.loop_mode_var, value="custom", command=self.refresh_preview, bg=UI["panel"], fg=UI["text"], selectcolor=UI["surface"]).pack(side="left", padx=(12, 0))
        self._combo_row(parent, 1, "Theme pack", self.theme_var, list(DEFAULT_THEME_PACKS.keys()))
        self._entry_row(parent, 2, "Loop path", self.loop_path_var, 58)
        tk.Button(parent, text="Browse", command=self._browse_loop, bg=UI["panel_2"], fg=UI["text"], relief="flat").grid(row=2, column=3, padx=6, sticky="w")
        self._checkbox_row(parent, 3, "Tape layers", self.visual_family_vars)
        self._combo_row(parent, 4, "Voice provider", self.voice_provider_var, VOICE_PROVIDERS)
        tk.Label(parent, text="Voice assignments (one per line, `role=value`)", font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 4))
        self.voice_text.grid(row=6, column=0, columnspan=4, sticky="ew")

    def _build_question_four(self, parent: tk.Widget) -> None:
        tk.Checkbutton(
            parent,
            text="Enable transient surfaces",
            variable=self.transient_enabled_var,
            bg=UI["panel"],
            fg=UI["text"],
            activebackground=UI["panel"],
            activeforeground=UI["text"],
            selectcolor=UI["surface"],
            command=self.refresh_preview,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self._entry_row(parent, 1, "Surface title", self.transient_title_var, 28)
        self._entry_row(parent, 1, "Min priority", self.transient_min_priority_var, 10, col=2)
        self._combo_row(parent, 2, "Output format", self.transient_format_var, [item["label"] for item in self.transient_catalog])
        self._entry_row(parent, 3, "Template file", self.transient_template_path_var, 52)
        tk.Button(parent, text="Import Into Library", command=self._browse_transient_template, bg=UI["panel_2"], fg=UI["text"], relief="flat").grid(row=3, column=3, padx=6, sticky="w")
        tk.Label(parent, text="Body template (`{title}` / `{body}` / `{source}` / `{type}`)", font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 4))
        self.transient_body_text.grid(row=5, column=0, columnspan=4, sticky="ew")

    def _seed_defaults(self) -> None:
        self.voice_text.delete("1.0", "end")
        self.voice_text.insert("1.0", "host=af_sarah\nanalyst=am_adam\nwitness=bf_emma")
        self.custom_signal_params_text.delete("1.0", "end")
        self.custom_signal_params_text.insert("1.0", "{\n  \n}")
        self.transient_body_text.delete("1.0", "end")
        self.transient_body_text.insert("1.0", TRANSIENT_FORMATS[transient_label_to_name(self.transient_format_var.get())])

    def _entry_row(self, parent: tk.Widget, row: int, label: str, var: tk.StringVar, width: int, *, col: int = 0) -> None:
        tk.Label(parent, text=label, font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=4)
        entry = tk.Entry(parent, textvariable=var, width=width, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat")
        entry.grid(row=row, column=col + 1, sticky="ew", pady=4)
        entry.bind("<KeyRelease>", lambda _e: self.refresh_preview())

    def _combo_row(self, parent: tk.Widget, row: int, label: str, var: tk.StringVar, options: List[str]) -> None:
        tk.Label(parent, text=label, font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        combo = ttk.Combobox(parent, textvariable=var, values=options, state="readonly", width=34)
        combo.grid(row=row, column=1, sticky="w", pady=4)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_combo_value())
        if var is self.transient_format_var:
            self.transient_format_combo = combo

    def _sync_combo_value(self) -> None:
        reverse = {label: key for key, label in WORLD_OPTIONS}
        if self.world_var.get() in reverse:
            self.world_var.set(reverse[self.world_var.get()])
        selected_name = transient_label_to_name(self.transient_format_var.get())
        item = transient_catalog_item(selected_name)
        if item and item.get("kind") == "custom":
            self.transient_template_path_var.set(item.get("path", ""))
            self.transient_body_text.delete("1.0", "end")
            self.transient_body_text.insert("1.0", provisioning.read_transient_template(selected_name))
        elif selected_name in TRANSIENT_FORMATS:
            self.transient_template_path_var.set("")
            self.transient_body_text.delete("1.0", "end")
            self.transient_body_text.insert("1.0", TRANSIENT_FORMATS[selected_name])
        self.refresh_preview()

    def _apply_selected_preset(self) -> None:
        preset = LOOM_PRESETS.get(self.preset_var.get())
        if not preset:
            return
        self.name_var.set(preset.get("name", self.name_var.get()))
        world_key = preset.get("world_kind", "forkuniverse")
        label_lookup = {key: label for key, label in WORLD_OPTIONS}
        self.world_var.set(label_lookup.get(world_key, world_key))
        self.premise_var.set(preset.get("premise", ""))
        self.time_period_var.set(preset.get("time_period", TIME_PERIOD_OPTIONS[0]))
        self.location_flavor_var.set(preset.get("location_flavor", ""))
        self.starting_context_var.set(preset.get("starting_context", ""))
        for key, var in self.genre_vars.items():
            var.set(key in preset.get("genres", []))
        for key, var in self.tone_vars.items():
            var.set(key in preset.get("tones", []))
        wanted_signals = preset.get("signals", ["simulated_spatial_array"])
        for key, var in self.signal_vars.items():
            var.set(key in wanted_signals)
        self.refresh_preview()

    def _browse_loop(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Choose organism loop/media",
            filetypes=[("Media", "*.gif *.png *.jpg *.jpeg *.bmp *.webp *.mp4 *.mov *.avi *.mkv *.webm *.ogv"), ("All files", "*.*")],
        )
        if path:
            self.loop_path_var.set(path)
            self.loop_mode_var.set("custom")
            self.refresh_preview()

    def _write_thumbnail_for_descriptor(self, descriptor: Dict[str, Any], path: Path) -> Path:
        from oradio_engine.visual_tape import VisualTapeLog

        return write_visual_thumbnail(descriptor, path, VisualTapeLog(), tick=0)

    def _refresh_signal_catalog_state(self) -> None:
        self.signal_catalog = signal_catalog()
        for item in self.signal_catalog:
            value = f"{'Sensitive' if item['sensitive'] else 'Safe'} · {item['status']}"
            if item["key"] in self.signal_status_vars:
                self.signal_status_vars[item["key"]].set(value)

    def _browse_signal_target(self, key: str) -> None:
        title = "Choose a remembered signal target"
        path = filedialog.askopenfilename(parent=self.root, title=title)
        if not path:
            return
        res = provisioning.save_antenna_target(key, path)
        if not res.get("ok"):
            messagebox.showerror("Signal target", str(res.get("error")), parent=self.root)
            return
        if key == "moco":
            self.moco_telemetry_path_var.set(path)
        elif key == "atl_league":
            self.atl_db_path_var.set(path)
        self._refresh_signal_catalog_state()
        self.status_var.set(f"Remembered {key} target for future looms.")
        self.refresh_preview()

    def _browse_transient_template(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a transient template",
            filetypes=[("Template text", "*.txt *.md *.html"), ("All files", "*.*")],
        )
        if path:
            res = provisioning.save_transient_template(path)
            if not res.get("ok"):
                messagebox.showerror("Transient template", str(res.get("error")), parent=self.root)
                return
            self.transient_catalog = transient_format_catalog()
            if self.transient_format_combo is not None:
                self.transient_format_combo.configure(values=[item["label"] for item in self.transient_catalog])
            self.transient_format_var.set(transient_name_to_label(str(res["name"])))
            self.transient_template_path_var.set(str(res["path"]))
            text = Path(str(res["path"])).read_text(encoding="utf-8")
            self.transient_body_text.delete("1.0", "end")
            self.transient_body_text.insert("1.0", text)
            self.status_var.set(f"Imported transient template '{res['name']}' into the library.")
            self.refresh_preview()

    def gather_state(self) -> LoomFormState:
        try:
            seed = int(self.seed_var.get().strip() or "42")
        except ValueError as exc:
            raise ValueError("Seed must be an integer.") from exc
        try:
            min_priority = float(self.transient_min_priority_var.get().strip() or "0.6")
        except ValueError as exc:
            raise ValueError("Transient minimum priority must be numeric.") from exc
        reverse = {label: key for key, label in WORLD_OPTIONS}
        return LoomFormState(
            name=self.name_var.get().strip(),
            world_kind=reverse.get(self.world_var.get().strip(), self.world_var.get().strip() or "forkuniverse"),
            seed=seed,
            premise=self.premise_var.get().strip(),
            enabled_signals=[key for key, var in self.signal_vars.items() if var.get()],
            spatial_nodes=parse_csv_nodes(self.spatial_nodes_var.get()),
            loop_mode=self.loop_mode_var.get().strip() or "builtin",
            builtin_theme=self.theme_var.get().strip() or DEFAULT_THEME,
            loop_path=self.loop_path_var.get().strip(),
            voice_provider=self.voice_provider_var.get().strip() or "none",
            voice_assignments=parse_voice_assignments(self.voice_text.get("1.0", "end")),
            transient_enabled=bool(self.transient_enabled_var.get()),
            transient_title=self.transient_title_var.get().strip(),
            transient_min_priority=min_priority,
            transient_body_template=self.transient_body_text.get("1.0", "end").strip(),
            time_period=self.time_period_var.get().strip() or "present_day",
            genres=[key for key, var in self.genre_vars.items() if var.get()],
            tones=[key for key, var in self.tone_vars.items() if var.get()],
            location_flavor=self.location_flavor_var.get().strip(),
            starting_context=self.starting_context_var.get().strip(),
            custom_signal_kind=self.custom_signal_kind_var.get().strip(),
            custom_signal_name=self.custom_signal_name_var.get().strip(),
            custom_signal_params_text=self.custom_signal_params_text.get("1.0", "end").strip(),
            transient_format=self.transient_format_var.get().strip() or "Bulletin",
            transient_template_path=self.transient_template_path_var.get().strip(),
            visual_families=[key for key, var in self.visual_family_vars.items() if var.get()],
        )

    def refresh_preview(self, *_args: Any) -> None:
        try:
            descriptor = build_loom_descriptor(self.gather_state())
            text = yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True)
            self.status_var.set("Descriptor preview updated.")
        except Exception as exc:
            text = f"Descriptor error:\n{exc}"
            self.status_var.set(str(exc))
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)

    def export_oradio(self) -> None:
        try:
            descriptor = build_loom_descriptor(self.gather_state())
        except Exception as exc:
            messagebox.showerror("Export .oradio", str(exc), parent=self.root)
            return
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        default_name = f"{station_id_from_text(self.name_var.get())}.oradio"
        target = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Loom .oradio",
            defaultextension=".oradio",
            initialdir=str(EXPORTS_DIR),
            initialfile=default_name,
            filetypes=[("Loom descriptor", "*.oradio"), ("YAML", "*.yaml"), ("All files", "*.*")],
        )
        if not target:
            return
        path = Path(target)
        path.write_text(yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True), encoding="utf-8")
        thumb = self._write_thumbnail_for_descriptor(descriptor, path)
        self.last_export_path = path
        self.status_var.set(f"Exported {path.name} and {thumb.name}")
        messagebox.showinfo("Export .oradio", f"Exported:\n{path}\n\nThumbnail:\n{thumb}", parent=self.root)

    def open_in_player(self) -> None:
        if self.last_export_path is None or not self.last_export_path.exists():
            try:
                descriptor = build_loom_descriptor(self.gather_state())
            except Exception as exc:
                messagebox.showerror("Open In Player", str(exc), parent=self.root)
                return
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            self.last_export_path = EXPORTS_DIR / f"{station_id_from_text(self.name_var.get())}.oradio"
            self.last_export_path.write_text(yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True), encoding="utf-8")
            self._write_thumbnail_for_descriptor(descriptor, self.last_export_path)
        self.status_var.set(f"Opening {self.last_export_path.name} in the Loom player")
        subprocess.Popen(app_paths.opener_command(self.last_export_path), cwd=str(ROOT_DIR))

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LoomStudioApp().run()


if __name__ == "__main__":
    main()
