#!/usr/bin/env python3
"""
Descriptor-style `.oradio` player for The Loom.

This player opens the Loom-native descriptor artifacts directly, runs the
`oradio_engine` federation, renders a simple study surface, speaks selected
beats through the configured TTS provider, and spawns transient surfaces when
rules match.
"""
from __future__ import annotations

import argparse
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:  # tkinter is an optional system package (python3-tk); absent in headless/CI
    tk = messagebox = None

import yaml
try:
    from PIL import ImageTk
except ImportError:  # PIL.ImageTk imports tkinter (optional system pkg); GUI-only
    ImageTk = None

import app_paths
import provisioning
from oradio_engine import (
    Club,
    NormalizedCandidate,
    VisualTapeLog,
    candidate_to_visual_events,
    descriptor_visual_families,
    open_oradio,
    truth_to_visual_events,
)
# PIL-dependent rasterization lives in visual_thumbnail (an ENDPOINT), NOT the pure decoder
# core — see oradio_engine/__init__.py. The player is an endpoint, so it imports them here.
from oradio_engine.visual_thumbnail import (
    VideoLoop,
    render_visual_frame,
    resolve_media_path,
    thumbnail_sidecar_path,
    visual_config,
    write_visual_thumbnail,
)
from loom_narration import Narrator
from voice_provider import get_voice_provider


UI = {
    "bg": "#0e0f12",
    "panel": "#13161d",
    "surface": "#090b10",
    "card": "#171b23",
    "text": "#ecf1f7",
    "muted": "#a7b0bc",
    "accent": "#66d2e7",
    "line": "#27303a",
    "good": "#52d273",
    "warn": "#f4c95d",
}

FONT_H1 = ("Segoe UI", 20, "bold")
FONT_H2 = ("Segoe UI", 14, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)


def is_descriptor_oradio(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) != b"PK\x03\x04"
    except OSError:
        return False


def read_descriptor(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Descriptor must decode to an object.")
    return data


def format_candidate_line(cand: NormalizedCandidate) -> str:
    body = (cand.body or cand.title or "").strip()
    return body if body else cand.title


def render_transient_body(template: str, cand: NormalizedCandidate) -> str:
    return (template or "{title}\n\n{body}").format(
        title=cand.title,
        body=cand.body,
        source=cand.source,
        type=cand.type,
        priority=f"{cand.priority:.2f}",
    )


@dataclass
class TransientRule:
    name: str
    title: str
    min_priority: float
    body_template: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransientRule":
        return cls(
            name=str(data.get("name") or "transient"),
            title=str(data.get("title") or "Transient surface"),
            min_priority=float(data.get("min_priority") or 0.6),
            body_template=str(data.get("body_template") or "{title}\n\n{body}"),
        )

    def matches(self, cand: NormalizedCandidate) -> bool:
        return cand.priority >= self.min_priority


class VoiceSpeaker:
    def __init__(self, descriptor: Dict[str, Any]) -> None:
        voice_cfg = descriptor.get("voice") if isinstance(descriptor.get("voice"), dict) else {}
        self.provider_name = str(voice_cfg.get("provider") or "none")
        self.assignments = voice_cfg.get("assignments") if isinstance(voice_cfg.get("assignments"), dict) else {}
        self.order = list(self.assignments) or ["host"]
        self.index = 0
        self.provider = None
        self.last_error = ""
        self._queue = None
        self._worker_started = False

        if self.provider_name == "none":
            return

        cfg = provisioning.read_global_config()
        assets = cfg.get("assets", {}) if isinstance(cfg.get("assets"), dict) else {}
        voice_dirs = assets.get("voices_dirs", []) if isinstance(assets.get("voices_dirs"), list) else []
        if voice_dirs:
            import os

            os.environ.setdefault("RADIO_OS_VOICES", str(voice_dirs[0]))

        audio_cfg: Dict[str, Any] = {"voices_provider": self.provider_name}
        piper_bin = provisioning.get_piper_bin()
        if piper_bin:
            audio_cfg["piper_bin"] = piper_bin

        try:
            self.provider = get_voice_provider(cfg, audio_cfg)
        except Exception as exc:
            self.last_error = str(exc)

    @property
    def ready(self) -> bool:
        return self.provider is not None

    @property
    def required(self) -> bool:
        return self.provider_name != "none"

    def next_voice(self) -> str:
        key = self.order[self.index % len(self.order)]
        self.index += 1
        return key

    def _ensure_worker(self) -> None:
        if self._worker_started:
            return
        import queue
        self._queue = queue.Queue()
        self._worker_started = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        # One worker drains the queue and plays BLOCKING, so a play-by-play call and the
        # interior monologue that answers it are heard in order, never on top of each other.
        import sounddevice as sd
        while True:
            voice_key, text = self._queue.get()
            try:
                audio, sample_rate = self.provider.synthesize(voice_key, text, self.assignments)
                sd.play(audio, sample_rate, blocking=True)
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                self._queue.task_done()

    def speak(self, voice_key: str, text: str) -> None:
        """Enqueue a line in a specific voice (voice_key resolved via assignments)."""
        if not self.provider or not text.strip():
            return
        self._ensure_worker()
        self._queue.put((voice_key, text))

    def speak_async(self, text: str) -> None:
        # Fallback for descriptors without named voice effectors: rotate through assigned voices.
        self.speak(self.next_voice(), text)


class LoomPlayerApp:
    def __init__(self, descriptor_path: Path) -> None:
        self.descriptor_path = descriptor_path
        self.descriptor = read_descriptor(descriptor_path)
        self.club = Club()
        self.allow_sensitive = False
        self.open_result = open_oradio(str(descriptor_path), club=self.club, gate=False, allow_sensitive=self.allow_sensitive)
        if self.open_result.engine is None:
            raise RuntimeError("Could not build a Loom engine from this descriptor.")
        self.engine = self.open_result.engine
        self.transient_rules = [
            TransientRule.from_dict(item)
            for item in self.descriptor.get("transient_surfaces", [])
            if isinstance(item, dict)
        ]
        self.triggered_transients: set[str] = set()
        self.narrator = Narrator.from_descriptor(self.descriptor)
        self.voice = VoiceSpeaker(self.descriptor)
        if self.voice.required and not self.voice.ready:
            detail = self.voice.last_error or "Voice playback is not ready for this descriptor."
            raise RuntimeError(detail)
        self.visual_tape = VisualTapeLog()
        self.visual_families = descriptor_visual_families(self.descriptor)
        self.visual_snapshot = None
        self.stage_image = None
        self.media_loop = self._build_media_loop()
        self.previous_truth = self.engine.truth()
        self.playing = False
        self.tick_ms = 1400
        self.loop_after_id: Optional[str] = None
        self.media_after_id: Optional[str] = None
        self.media_refresh_ms = 80
        self.thumbnail_refresh_ms = 3500
        self.last_thumbnail_refresh_at = 0.0
        self.last_spoken_post_id = ""
        self.beats: List[NormalizedCandidate] = []
        self.events: "queue.Queue[str]" = queue.Queue()
        self.ribbon_phase = 0.0
        self.playback_elapsed_before_pause = 0.0
        self.play_started_at: Optional[float] = None

        self.root = tk.Tk()
        self.root.title(f"The Loom Player - {self.descriptor.get('oradio', descriptor_path.stem)}")
        self.root.geometry("1240x820")
        self.root.configure(bg=UI["bg"])

        self.status_var = tk.StringVar(value="Ready to loom.")
        self.subtitle_var = tk.StringVar(value="Press Play to observe the world.")
        self.club_var = tk.StringVar(value=self._club_summary())
        self.voice_var = tk.StringVar(value=self._voice_summary())
        self.visual_var = tk.StringVar(value=self._visual_summary())
        self.now_var = tk.StringVar(value="")

        self._build()
        self._refresh_thumbnail()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_media_loop(self) -> Optional[VideoLoop]:
        config = visual_config(self.descriptor)
        if config.get("mode") != "media":
            return None
        path = resolve_media_path(self.descriptor_path, str(config.get("path") or ""))
        if path is None:
            return None
        if path.suffix.lower() not in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".ogv", ".ogg"}:
            return None
        loop = VideoLoop(path)
        return loop if loop.ok else None

    def _media_time(self) -> float:
        elapsed = self.playback_elapsed_before_pause
        if self.playing and self.play_started_at is not None:
            elapsed += max(0.0, time.perf_counter() - self.play_started_at)
        return elapsed

    def _build(self) -> None:
        top = tk.Frame(self.root, bg=UI["bg"])
        top.pack(fill="x", padx=16, pady=(16, 8))
        tk.Label(top, text=self.descriptor.get("oradio", self.descriptor_path.stem), font=FONT_H1, fg=UI["text"], bg=UI["bg"]).pack(side="left")
        tk.Label(top, textvariable=self.status_var, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left", padx=12)
        tk.Button(top, text="Club", command=self.show_club_dialog, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="right", padx=4)

        transport = tk.Frame(self.root, bg=UI["panel"])
        transport.pack(fill="x", padx=16)
        for label, command in (
            ("Rewind", self.rewind),
            ("Play", self.play),
            ("Pause", self.pause),
            ("Stop", self.stop),
            ("Restart", self.restart),
        ):
            tk.Button(
                transport,
                text=label,
                command=command,
                bg=UI["accent"] if label == "Play" else UI["card"],
                fg="#000" if label == "Play" else UI["text"],
                relief="flat",
                padx=14,
                pady=6,
            ).pack(side="left", padx=6, pady=10)
        tk.Label(transport, textvariable=self.now_var, fg=UI["muted"], bg=UI["panel"], font=FONT_SMALL).pack(side="right", padx=12)

        center = tk.Frame(self.root, bg=UI["bg"])
        center.pack(fill="both", expand=True, padx=16, pady=16)

        stage_card = tk.Frame(center, bg=UI["surface"], highlightbackground=UI["line"], highlightthickness=1)
        stage_card.pack(side="left", fill="both", expand=True)
        self.stage = tk.Canvas(stage_card, bg=UI["surface"], highlightthickness=0)
        self.stage.pack(fill="both", expand=True)
        self.stage.bind("<Configure>", lambda _e: self.draw_stage())

        side = tk.Frame(center, bg=UI["bg"], width=320)
        side.pack(side="left", fill="y", padx=(16, 0))
        side.pack_propagate(False)

        self._side_card(side, "Club", self.club_var)
        self._side_card(side, "Voices", self.voice_var)
        self._side_card(side, "Tape", self.visual_var)
        self._side_card(side, "Latest Beats", None)
        self.beats_list = tk.Listbox(side, bg=UI["panel"], fg=UI["text"], relief="flat", borderwidth=0, highlightthickness=0, font=FONT_SMALL)
        self.beats_list.pack(fill="both", expand=True)

        bottom = tk.Frame(self.root, bg="#05070c", height=92)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        tk.Label(bottom, text="LISTEN IN", fg=UI["accent"], bg="#05070c", font=FONT_SMALL).pack(anchor="w", padx=18, pady=(10, 0))
        tk.Label(bottom, textvariable=self.subtitle_var, fg=UI["text"], bg="#05070c", font=("Segoe UI", 18, "bold"), wraplength=1160, justify="left").pack(anchor="w", padx=18)
        self.draw_stage()

    def _side_card(self, parent: tk.Widget, title: str, var: Optional[tk.StringVar]) -> None:
        card = tk.Frame(parent, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1)
        card.pack(fill="x", pady=(0, 12))
        tk.Label(card, text=title, font=FONT_H2, fg=UI["text"], bg=UI["card"]).pack(anchor="w", padx=12, pady=(10, 4))
        if var is not None:
            tk.Label(card, textvariable=var, font=FONT_SMALL, fg=UI["muted"], bg=UI["card"], justify="left", wraplength=280).pack(anchor="w", padx=12, pady=(0, 10))

    def _club_summary(self) -> str:
        asks = ", ".join(a.capability for a in self.open_result.report.asks) or "none"
        withheld = ", ".join(req.name for req in (self.open_result.withheld or [])) or "none"
        return f"asks: {asks}\nwithheld: {withheld}\nconfig: {provisioning.membership_summary()}"

    def _voice_summary(self) -> str:
        if self.descriptor.get("voice", {}).get("provider") == "none":
            return "voice disabled"
        if self.voice.ready:
            return f"{self.voice.provider_name} ready\nvoices: {', '.join(self.voice.order)}"
        if self.voice.last_error:
            return f"{self.voice.provider_name} unavailable\n{self.voice.last_error}"
        return f"{self.voice.provider_name} pending"

    def _visual_summary(self) -> str:
        snapshot = self.visual_snapshot
        if snapshot is None:
            sidecar = thumbnail_sidecar_path(self.descriptor_path)
            return f"families: {', '.join(self.visual_families)}\nentries: 0\nthumbnail: {sidecar.name}"
        lineage = " | ".join(" > ".join(item[-2:]) for item in snapshot.lineage[-2:]) or "seed only"
        return (
            f"families: {', '.join(self.visual_families)}\n"
            f"entries: {snapshot.entries}\n"
            f"energy: {snapshot.total_energy:.2f}\n"
            f"lineage: {lineage}"
        )

    def _refresh_thumbnail(self) -> None:
        try:
            write_visual_thumbnail(
                self.descriptor,
                self.descriptor_path,
                self.visual_tape,
                tick=self.engine.clock.tick,
                media_time=self._media_time(),
            )
            self.last_thumbnail_refresh_at = time.perf_counter()
        except Exception:
            return

    def draw_stage(self) -> None:
        width = max(self.stage.winfo_width(), 640)
        height = max(self.stage.winfo_height(), 360)
        self.stage.delete("all")
        image, snapshot, meta = render_visual_frame(
            self.descriptor,
            self.descriptor_path,
            self.visual_tape,
            tick=self.engine.clock.tick,
            size=(width, height),
            phase=self.ribbon_phase,
            media_time=self._media_time(),
            video_loop=self.media_loop,
        )
        self.visual_snapshot = snapshot
        self.visual_var.set(self._visual_summary())
        self.stage_image = ImageTk.PhotoImage(image)
        self.stage.create_image(0, 0, anchor="nw", image=self.stage_image)
        self.stage.create_text(
            20,
            18,
            anchor="nw",
            text=f"{meta['base']}  |  tick {self.engine.clock.tick}",
            fill=UI["muted"],
            font=FONT_SMALL,
        )

    def show_club_dialog(self) -> None:
        top = tk.Toplevel(self.root)
        top.title("The Club")
        top.geometry("520x340")
        top.configure(bg=UI["bg"])
        tk.Label(top, text="Club status", font=FONT_H1, fg=UI["text"], bg=UI["bg"]).pack(anchor="w", padx=18, pady=(16, 8))
        tk.Label(top, text=self._club_summary(), font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"], justify="left", wraplength=480).pack(anchor="w", padx=18, pady=(0, 12))
        tk.Button(top, text="Allow sensitive telemetry for this open", command=lambda: self._grant_sensitive(top), bg=UI["accent"], fg="#000", relief="flat").pack(anchor="w", padx=18, pady=4)
        tk.Button(top, text="Close", command=top.destroy, bg=UI["panel"], fg=UI["text"], relief="flat").pack(anchor="e", padx=18, pady=12)

    def _grant_sensitive(self, dialog: tk.Toplevel) -> None:
        self.allow_sensitive = True
        self.open_result = open_oradio(str(self.descriptor_path), club=self.club, gate=False, allow_sensitive=True)
        if self.open_result.engine is None:
            messagebox.showerror("The Club", "Could not reopen the .oradio with consent.", parent=dialog)
            return
        self.engine = self.open_result.engine
        self.club_var.set(self._club_summary())
        dialog.destroy()
        self.status_var.set("Sensitive telemetry allowed for this open.")

    def play(self) -> None:
        if self.playing:
            return
        self.playing = True
        self.play_started_at = time.perf_counter()
        self.status_var.set("Playing")
        self.now_var.set("Broadcasting")
        self._media_loop()
        self._loop()

    def pause(self) -> None:
        if self.playing and self.play_started_at is not None:
            self.playback_elapsed_before_pause += max(0.0, time.perf_counter() - self.play_started_at)
        self.playing = False
        self.play_started_at = None
        if self.loop_after_id:
            self.root.after_cancel(self.loop_after_id)
            self.loop_after_id = None
        if self.media_after_id:
            self.root.after_cancel(self.media_after_id)
            self.media_after_id = None
        self.status_var.set("Paused")
        self.now_var.set("Paused")

    def stop(self) -> None:
        self.pause()
        self.status_var.set("Stopped")
        self.now_var.set("Stopped")

    def rewind(self) -> None:
        self.pause()
        self.open_result = open_oradio(str(self.descriptor_path), club=self.club, gate=False, allow_sensitive=self.allow_sensitive)
        if self.open_result.engine is None:
            raise RuntimeError("Could not rewind; engine failed to rebuild.")
        self.engine = self.open_result.engine
        self.playback_elapsed_before_pause = 0.0
        self.play_started_at = None
        self.visual_tape.clear()
        self.visual_snapshot = None
        self.previous_truth = self.engine.truth()
        self.beats.clear()
        self.triggered_transients.clear()
        self.beats_list.delete(0, "end")
        self.subtitle_var.set("Rewound to the beginning.")
        self._refresh_thumbnail()
        self.draw_stage()

    def restart(self) -> None:
        self.rewind()
        self.play()

    def _media_loop(self) -> None:
        if not self.playing:
            return
        self.ribbon_phase += 0.045
        self.draw_stage()
        now = time.perf_counter()
        if (now - self.last_thumbnail_refresh_at) * 1000.0 >= self.thumbnail_refresh_ms:
            self._refresh_thumbnail()
        self.media_after_id = self.root.after(self.media_refresh_ms, self._media_loop)

    def _loop(self) -> None:
        if not self.playing:
            return
        produced = self.engine.tick(1)
        current_truth = self.engine.truth()
        self.visual_tape.extend(
            truth_to_visual_events(
                current_truth,
                self.previous_truth,
                self.engine.clock.tick,
                families=self.visual_families,
            )
        )
        self.previous_truth = current_truth
        if produced:
            self._ingest_candidates(produced)
        self.loop_after_id = self.root.after(self.tick_ms, self._loop)

    def _ingest_candidates(self, produced: List[NormalizedCandidate]) -> None:
        for cand in produced:
            self.beats.append(cand)
            self.visual_tape.extend(
                candidate_to_visual_events(
                    cand,
                    self.engine.clock.tick,
                    families=self.visual_families,
                )
            )
            self.beats_list.insert("end", f"[{cand.priority:.2f}] {cand.source}: {cand.title}")
            if self.beats_list.size() > 40:
                self.beats_list.delete(0)

        top = max(produced, key=lambda c: c.priority)
        # Tier 1 narration: render the abstract beat into a world-grounded sentence,
        # so the subtitles lane and the TTS both speak lines that sound like THIS world.
        line = self.narrator.line(top)
        self.subtitle_var.set(line)
        self.status_var.set(f"{top.source} / {top.type}")
        self.now_var.set(f"tick {self.engine.clock.tick}")

        # Voice-effector beats (e.g. pa, inner) each speak in their OWN voice, in bus order
        # (the call before the interior). Descriptors with no such effectors fall back to
        # narrating the top beat through the rotating voice.
        spoken = [c for c in produced if c.type == "spoken"]
        if self.voice.ready and spoken:
            for c in spoken:
                self.voice.speak(c.source, c.body)
                self.last_spoken_post_id = c.post_id
            self.voice_var.set(self._voice_summary())
        elif self.voice.ready and top.post_id != self.last_spoken_post_id:
            self.last_spoken_post_id = top.post_id
            self.voice.speak_async(line)
            self.voice_var.set(self._voice_summary())

        self._refresh_thumbnail()

        for rule in self.transient_rules:
            key = f"{rule.name}:{top.post_id}"
            if key in self.triggered_transients:
                continue
            if rule.matches(top):
                self.triggered_transients.add(key)
                self.spawn_transient(rule, top)

    def spawn_transient(self, rule: TransientRule, cand: NormalizedCandidate) -> None:
        top = tk.Toplevel(self.root)
        top.title(rule.title)
        top.geometry("480x320")
        top.configure(bg=UI["bg"])
        tk.Label(top, text=rule.title, font=FONT_H2, fg=UI["text"], bg=UI["bg"]).pack(anchor="w", padx=18, pady=(16, 8))
        body = render_transient_body(rule.body_template, cand)
        tk.Label(top, text=body, font=FONT_BODY, fg=UI["muted"], bg=UI["bg"], justify="left", wraplength=430).pack(anchor="w", padx=18, pady=(0, 16))
        tk.Button(top, text="Close", command=top.destroy, bg=UI["panel"], fg=UI["text"], relief="flat").pack(anchor="e", padx=18, pady=(0, 12))

    def _close(self) -> None:
        self.pause()
        self._refresh_thumbnail()
        if self.media_loop is not None:
            self.media_loop.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Open a Loom descriptor-style .oradio")
    parser.add_argument("descriptor", type=Path)
    args = parser.parse_args(argv)
    try:
        app = LoomPlayerApp(args.descriptor)
    except Exception as exc:
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("The Loom Player", str(exc), parent=root)
            root.destroy()
        except Exception:
            print(f"The Loom Player could not open: {exc}", flush=True)
        return 2
    try:
        app.run()
    except Exception as exc:
        if app_paths.is_frozen():
            app_paths.append_packaged_error(traceback.format_exc())
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("The Loom Player", str(exc), parent=root)
            root.destroy()
        except Exception:
            print(f"The Loom Player crashed: {exc}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        if app_paths.is_frozen():
            app_paths.append_packaged_error(traceback.format_exc())
        raise
