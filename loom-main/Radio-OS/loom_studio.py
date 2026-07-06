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
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

import yaml

from oradio_engine.club import DEFAULT_THEME, DEFAULT_THEME_PACKS


BASE_DIR = Path(__file__).resolve().parent
EXPORTS_DIR = BASE_DIR / "exports"

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

SIGNAL_OPTIONS = [
    ("simulated_spatial_array", "Spatial array (simulated)"),
    ("pc_telemetry", "PC telemetry"),
    ("ring_telemetry", "Ring telemetry"),
    ("video_capture_sim", "Video capture (simulated)"),
]

VOICE_PROVIDERS = ["none", "kokoro", "piper", "elevenlabs", "google", "azure"]


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


def default_transient_body() -> str:
    return "{title}\n\n{body}"


def build_loom_descriptor(state: LoomFormState) -> Dict[str, Any]:
    descriptor: Dict[str, Any] = {
        "oradio": station_id_from_text(state.name),
        "lens": "identity",
        "surfaces": ["ribbon"],
        "club": [],
    }

    if state.world_kind != "none":
        world: Dict[str, Any] = {
            "organ": state.world_kind,
            "name": station_id_from_text(state.name),
            "seed": int(state.seed),
        }
        if state.world_kind == "forkuniverse":
            world["creation"] = {
                "universe_title": state.name.strip() or "Untitled Loom",
                "premise": state.premise.strip() or "A world expressed through a Loom artifact.",
                "setting_kind": "custom",
                "story_mode": "continuous",
                "world_scale": "district",
                "starting_population": 24,
                "seed_mode": "custom",
                "custom_seed": str(state.seed),
                "ontology_domains": ["identity", "pressure", "signal"],
            }
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
        else:
            telemetry.append({"source": signal, "name": signal.replace("_telemetry", "").replace("_sim", "")})
    if telemetry:
        descriptor["telemetry"] = telemetry

    if "world" not in descriptor and not telemetry:
        raise ValueError("The Loom needs at least one world or one signal to produce a runnable .oradio.")

    theme_value = state.builtin_theme if state.loop_mode == "builtin" else state.loop_path.strip()
    descriptor["theme"] = theme_value or DEFAULT_THEME

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
                "body_template": state.transient_body_template or default_transient_body(),
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

        self.name_var = tk.StringVar(value="my-loom")
        self.world_var = tk.StringVar(value=WORLD_OPTIONS[0][1])
        self.seed_var = tk.StringVar(value="42")
        self.premise_var = tk.StringVar(value="")
        self.loop_mode_var = tk.StringVar(value="builtin")
        self.theme_var = tk.StringVar(value=DEFAULT_THEME)
        self.loop_path_var = tk.StringVar(value="")
        self.voice_provider_var = tk.StringVar(value="kokoro")
        self.spatial_nodes_var = tk.StringVar(value="front_door, living_room, kitchen")
        self.transient_enabled_var = tk.BooleanVar(value=True)
        self.transient_title_var = tk.StringVar(value="Glimpse")
        self.transient_min_priority_var = tk.StringVar(value="0.6")
        self.status_var = tk.StringVar(value="Answer the four questions, then export.")
        self.last_export_path: Optional[Path] = None

        self.signal_vars = {key: tk.BooleanVar(value=(key == "simulated_spatial_array")) for key, _ in SIGNAL_OPTIONS}

        self.voice_text = tk.Text(self.root, height=6, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO)
        self.transient_body_text = tk.Text(self.root, height=6, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO)
        self.preview_text = tk.Text(self.root, height=18, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat", font=FONT_MONO)

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

    def _build_question_two(self, parent: tk.Widget) -> None:
        signals = tk.Frame(parent, bg=UI["panel"])
        signals.grid(row=0, column=0, columnspan=4, sticky="w")
        for idx, (key, label) in enumerate(SIGNAL_OPTIONS):
            tk.Checkbutton(
                signals,
                text=label,
                variable=self.signal_vars[key],
                bg=UI["panel"],
                fg=UI["text"],
                activebackground=UI["panel"],
                activeforeground=UI["text"],
                selectcolor=UI["surface"],
                command=self.refresh_preview,
            ).grid(row=0, column=idx, padx=(0, 12), sticky="w")
        self._entry_row(parent, 1, "Spatial nodes", self.spatial_nodes_var, 80)

    def _build_question_three(self, parent: tk.Widget) -> None:
        mode = tk.Frame(parent, bg=UI["panel"])
        mode.grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Radiobutton(mode, text="Builtin theme", variable=self.loop_mode_var, value="builtin", command=self.refresh_preview, bg=UI["panel"], fg=UI["text"], selectcolor=UI["surface"]).pack(side="left")
        tk.Radiobutton(mode, text="Custom loop/media path", variable=self.loop_mode_var, value="custom", command=self.refresh_preview, bg=UI["panel"], fg=UI["text"], selectcolor=UI["surface"]).pack(side="left", padx=(12, 0))
        self._combo_row(parent, 1, "Theme pack", self.theme_var, list(DEFAULT_THEME_PACKS.keys()))
        self._entry_row(parent, 2, "Loop path", self.loop_path_var, 58)
        tk.Button(parent, text="Browse", command=self._browse_loop, bg=UI["panel_2"], fg=UI["text"], relief="flat").grid(row=2, column=3, padx=6, sticky="w")
        self._combo_row(parent, 3, "Voice provider", self.voice_provider_var, VOICE_PROVIDERS)
        tk.Label(parent, text="Voice assignments (one per line, `role=value`)", font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 4))
        self.voice_text.grid(row=5, column=0, columnspan=4, sticky="ew")

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
        tk.Label(parent, text="Body template (`{title}` / `{body}` / `{source}` / `{type}`)", font=FONT_SMALL, fg=UI["muted"], bg=UI["panel"]).grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 4))
        self.transient_body_text.grid(row=3, column=0, columnspan=4, sticky="ew")

    def _seed_defaults(self) -> None:
        self.voice_text.delete("1.0", "end")
        self.voice_text.insert("1.0", "host=af_sarah\nanalyst=am_adam\nwitness=bf_emma")
        self.transient_body_text.delete("1.0", "end")
        self.transient_body_text.insert("1.0", default_transient_body())

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

    def _sync_combo_value(self) -> None:
        reverse = {label: key for key, label in WORLD_OPTIONS}
        if self.world_var.get() in reverse:
            self.world_var.set(reverse[self.world_var.get()])
        self.refresh_preview()

    def _browse_loop(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Choose organism loop/media",
            filetypes=[("Media", "*.gif *.png *.jpg *.jpeg *.mp4 *.mov *.webm"), ("All files", "*.*")],
        )
        if path:
            self.loop_path_var.set(path)
            self.loop_mode_var.set("custom")
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
        self.last_export_path = path
        self.status_var.set(f"Exported {path.name}")
        messagebox.showinfo("Export .oradio", f"Exported:\n{path}", parent=self.root)

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
        self.status_var.set(f"Opening {self.last_export_path.name} in the Loom player")
        subprocess.Popen([sys.executable, str(BASE_DIR / "loom_player_ui.py"), str(self.last_export_path)], cwd=str(BASE_DIR))

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LoomStudioApp().run()


if __name__ == "__main__":
    main()
