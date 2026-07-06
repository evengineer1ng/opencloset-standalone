#!/usr/bin/env python3
"""The Loom Booth — a live DJ rig for tapes. The first Radio-OS "DAW".

Load a baked tape, hit play, and RIDE THE FADERS while it speaks: depth, flavour (chase the
cause / run the other way), curiosity, salience, continuity, and a voice pedal that swaps the
narrator mid-flow. You hear the changes live. A "mix" is a tape too — Keep the takes you like
and Save the mixtape.

    python loom_booth.py --tape data/f1_barcelona_2026.json

Onboarding a new fader/pedal is ONE line: add an entry to FADERS (and the matching attr on
Mixer in oradio_engine/mix.py). Scales for continuous knobs, toggles/choices for pedals.
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:  # tkinter is an optional system package (python3-tk); absent in headless/CI
    tk = filedialog = None

from oradio_engine.antenna import Antenna, Source
from oradio_engine.mix import LiveNarrator, Mixer
from oradio_engine.speech import Grammar

ROOT = Path(__file__).resolve().parent
GRAMMAR_DIR = ROOT / "data" / "grammars"
VERBS = str(ROOT / "data" / "english" / "irregular_verbs.json")
VOICE_CHOICES = ["intern", "town_crier", "prime_minister"]
COLOR_MODELS = ["phi3:3.8b", "qwen3:8b", "tinyllama:1.1b", "smollm2:135m"]
SAPI_VOICE = {"intern": "Microsoft David Desktop",
              "town_crier": "Microsoft Hazel Desktop",
              "prime_minister": "Microsoft Zira Desktop"}

# THE RACK — declarative. (attr on Mixer, widget kind, options). Add a line to add a fader.
FADERS = [
    ("depth", "scale", {"from": 0, "to": 4, "res": 1, "label": "DEPTH (how far you pull)"}),
    ("flavour", "choice", {"options": ["back", "both", "forward"], "label": "FLAVOUR (cause ← / consequence →)"}),
    ("salience", "scale", {"from": 0.0, "to": 1.0, "res": 0.05, "label": "SALIENCE (what's worth saying)"}),
    ("curiosity", "scale", {"from": 0, "to": 3, "res": 1, "label": "CURIOSITY (questions born)"}),
    ("continuity", "toggle", {"label": "CONTINUITY (carry state)"}),
    ("voice", "choice", {"options": VOICE_CHOICES, "label": "VOICE (the pedal)"}),
    ("color", "toggle", {"label": "COLOR (guarded flair)"}),
    ("color_model", "choice", {"options": COLOR_MODELS, "label": "COLOR MODEL"}),
]

UI = {"bg": "#0c0d10", "panel": "#16181d", "line": "#2b2f37", "text": "#eef1f4",
      "muted": "#9aa3af", "accent": "#7ad7f0", "good": "#5ad27a"}


class SpeechRig:
    """SAPI TTS on a single blocking worker so lines don't overlap. Voice swaps per pedal."""

    def __init__(self) -> None:
        self.provider = None
        try:
            from voice_provider import SapiProvider
            self.provider = SapiProvider()
        except Exception as exc:
            self.error = str(exc)
        self.q: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self._started = False

    def _worker(self) -> None:
        import sounddevice as sd
        while True:
            voice, text = self.q.get()
            try:
                audio, sr = self.provider.synthesize(voice, text, SAPI_VOICE)
                sd.play(audio, sr, blocking=True)
            except Exception:
                pass
            finally:
                self.q.task_done()

    def say(self, voice: str, text: str) -> None:
        if not self.provider or not text.strip():
            return
        if not self._started:
            self._started = True
            threading.Thread(target=self._worker, daemon=True).start()
        self.q.put((voice, text))

    def silence(self) -> None:
        try:
            while True:
                self.q.get_nowait(); self.q.task_done()
        except queue.Empty:
            pass
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass


class BoothApp:
    def __init__(self, tapes: List[Tuple[str, str]], rules: Optional[str], inquiry: Optional[str]) -> None:
        self.antenna = Antenna()
        for name, path in tapes:
            try:
                self.antenna.add(Source.from_tape(name, path))
            except Exception as exc:
                print(f"[antenna] skipped {name}: {exc}")
        self.rules = json.load(open(rules, encoding="utf-8")) if rules and Path(rules).exists() else None
        self.narrator = LiveNarrator(self.antenna.stream(), rules=self.rules)
        self.mixer = Mixer()
        self.rig = SpeechRig()
        self._grammars: Dict[str, Grammar] = {}
        self._colorists: Dict[str, Any] = {}
        self.entities = set()                       # driver vocabulary, for the colorist's guard
        for s in self.antenna.sources:
            for e in s.events:
                if e.get("actor"):
                    self.entities.add(e["actor"])
                o = e.get("object", "")
                if o and o[:1].isupper():
                    self.entities.add(o)
        self.tempo_ms = 2500
        self.playing = False
        self.after_id = None
        self.last_line = ""
        self.mixtape: List[str] = []

        self.root = tk.Tk()
        self.root.title("The Loom Booth")
        self.root.geometry("980x620")
        self.root.configure(bg=UI["bg"])
        self._build()

    def grammar(self) -> Grammar:
        v = self.mixer.voice
        if v not in self._grammars:
            self._grammars[v] = Grammar.from_file(str(GRAMMAR_DIR / f"{v}.json"), verbs=VERBS)
        return self._grammars[v]

    def _colorist(self, model: str):
        if model not in self._colorists:
            from colorist import Colorist
            self._colorists[model] = Colorist(model)
        return self._colorists[model]

    def _rebuild_narrator(self) -> None:
        self.narrator = LiveNarrator(self.antenna.stream(), rules=self.rules)
        self.tape_list.delete(0, "end")

    def _toggle_source(self, name: str, on: bool) -> None:
        self.antenna.toggle(name, on)
        self._rebuild_narrator()
        self.now_var.set("antenna: " + ", ".join(s.name for s in self.antenna.sources if s.enabled))

    # ---- UI ---------------------------------------------------------------- #
    def _build(self) -> None:
        tk.Label(self.root, text="THE LOOM BOOTH", font=("Consolas", 18, "bold"),
                 fg=UI["accent"], bg=UI["bg"]).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(self.root, text="ride the faders · hear the tape · keep what you want",
                 font=("Consolas", 9), fg=UI["muted"], bg=UI["bg"]).pack(anchor="w", padx=16)

        transport = tk.Frame(self.root, bg=UI["bg"])
        transport.pack(fill="x", padx=12, pady=8)
        self.play_btn = tk.Button(transport, text="▶ PLAY", width=10, command=self.toggle,
                                  bg=UI["good"], fg="#000", relief="flat")
        self.play_btn.pack(side="left", padx=4)
        tk.Button(transport, text="KEEP", width=8, command=self.keep, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        tk.Button(transport, text="SAVE MIXTAPE", command=self.save_mixtape, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        tk.Button(transport, text="REWIND", command=self.rewind, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left", padx=4)
        tk.Scale(transport, from_=400, to=5000, resolution=100, orient="horizontal", label="TEMPO ms/beat",
                 command=lambda v: setattr(self, "tempo_ms", int(float(v))), bg=UI["panel"], fg=UI["muted"],
                 troughcolor=UI["line"], highlightthickness=0, length=220).pack(side="right", padx=8)

        rack = tk.Frame(self.root, bg=UI["panel"], highlightbackground=UI["line"], highlightthickness=1)
        rack.pack(fill="x", padx=12, pady=6)
        for attr, kind, opts in FADERS:
            self._fader(rack, attr, kind, opts)

        ant = tk.Frame(self.root, bg=UI["bg"])
        ant.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(ant, text="ANTENNA (toggle tapes):", font=("Consolas", 9, "bold"),
                 fg=UI["muted"], bg=UI["bg"]).pack(side="left", padx=(0, 8))
        for s in self.antenna.sources:
            var = tk.BooleanVar(value=s.enabled)
            tk.Checkbutton(ant, text=f"{s.name} ({len(s.events)})", variable=var, bg=UI["bg"], fg=UI["text"],
                           selectcolor=UI["line"], activebackground=UI["bg"], activeforeground=UI["text"],
                           command=lambda n=s.name, v=var: self._toggle_source(n, v.get())).pack(side="left", padx=4)

        self.now_var = tk.StringVar(value="press PLAY")
        tk.Label(self.root, textvariable=self.now_var, font=("Consolas", 13), fg=UI["text"], bg=UI["bg"],
                 wraplength=940, justify="left").pack(anchor="w", padx=16, pady=(8, 4))

        self.tape_list = tk.Listbox(self.root, bg="#08090b", fg=UI["text"], font=("Consolas", 10),
                                    relief="flat", highlightthickness=0)
        self.tape_list.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _fader(self, parent: tk.Widget, attr: str, kind: str, opts: Dict[str, Any]) -> None:
        col = tk.Frame(parent, bg=UI["panel"])
        col.pack(side="left", padx=10, pady=8, anchor="n")
        label = opts.get("label", attr)
        cur = getattr(self.mixer, attr)
        if kind == "scale":
            tk.Scale(col, from_=opts["from"], to=opts["to"], resolution=opts["res"], orient="horizontal",
                     label=label, length=170, bg=UI["panel"], fg=UI["text"], troughcolor=UI["line"],
                     highlightthickness=0,
                     command=lambda v, a=attr: setattr(self.mixer, a,
                                                       int(float(v)) if isinstance(getattr(self.mixer, a), int) else float(v))
                     ).pack()
        elif kind == "toggle":
            var = tk.BooleanVar(value=bool(cur))
            tk.Checkbutton(col, text=label, variable=var, bg=UI["panel"], fg=UI["text"], selectcolor=UI["line"],
                           activebackground=UI["panel"], activeforeground=UI["text"],
                           command=lambda a=attr, vv=var: setattr(self.mixer, a, bool(vv.get()))).pack(anchor="w")
        elif kind == "choice":
            tk.Label(col, text=label, bg=UI["panel"], fg=UI["muted"], font=("Consolas", 8)).pack(anchor="w")
            var = tk.StringVar(value=str(cur))
            tk.OptionMenu(col, var, *opts["options"],
                          command=lambda v, a=attr: setattr(self.mixer, a, v)).pack(fill="x")

    # ---- transport --------------------------------------------------------- #
    def toggle(self) -> None:
        self.playing = not self.playing
        self.play_btn.configure(text="❚❚ PAUSE" if self.playing else "▶ PLAY")
        if self.playing:
            self._tick()
        else:
            self.rig.silence()
            if self.after_id:
                self.root.after_cancel(self.after_id)
                self.after_id = None

    def rewind(self) -> None:
        self.playing = False
        self.play_btn.configure(text="▶ PLAY")
        self.rig.silence()
        self._rebuild_narrator()
        self.now_var.set("rewound — press PLAY")

    def _tick(self) -> None:
        if not self.playing:
            return
        result = self.narrator.step(self.grammar(), self.mixer)
        if result is None:
            if self.narrator.done:
                self.now_var.set("— end of tape —")
                self.playing = False
                self.play_btn.configure(text="▶ PLAY")
                return
        else:
            lap, line = result
            if self.mixer.color:                    # guarded LLM flair (falls back to the mirror)
                line = self._colorist(self.mixer.color_model).colorize(line, self.entities)
            self.last_line = (f"[lap {lap}] " if lap else "") + line
            self.now_var.set(self.last_line)
            self.tape_list.insert("end", self.last_line)
            self.tape_list.see("end")
            self.rig.say(self.mixer.voice, line)
        self.after_id = self.root.after(self.tempo_ms, self._tick)

    def keep(self) -> None:
        if self.last_line and (not self.mixtape or self.mixtape[-1] != self.last_line):
            self.mixtape.append(self.last_line)
            self.now_var.set("★ kept · " + self.last_line)

    def save_mixtape(self) -> None:
        if not self.mixtape:
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            initialdir=str(ROOT / "transcripts"),
                                            initialfile="mixtape.txt")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# loom mixtape · mix={self.mixer.as_dict()}\n\n")
            f.write("\n".join(self.mixtape) + "\n")
        self.now_var.set(f"saved {len(self.mixtape)} kept lines → {Path(path).name}")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", action="append", default=[], help="name=path or path; repeatable")
    ap.add_argument("--rules", default="data/f1_causal_rules.json")
    ap.add_argument("--inquiry", default="data/inquiry/f1.json")
    args = ap.parse_args()
    specs = args.tape or ["f1=data/f1_barcelona_2026.json"]
    tapes = []
    for spec in specs:
        name, path = spec.split("=", 1) if "=" in spec else (Path(spec).stem, spec)
        tapes.append((name, path))
    BoothApp(tapes, args.rules, args.inquiry).run()


if __name__ == "__main__":
    main()
