#!/usr/bin/env python3
"""
Forked .oradio listener shell.

This is the first ambient runtime surface: a charcoal/cyan player chrome that
wraps the preserved bookmark.py kernel in headless mode. The kernel still does
the narration work; this file owns the listener-facing shell.
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except ImportError:  # tkinter is an optional system package (python3-tk); absent in headless/CI
    tk = filedialog = messagebox = None

import oradio_player
import oradio_resolver


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_PATH = BASE_DIR / "bookmark.py"

UI = {
    "bg": "#0e0e0e",
    "panel": "#121212",
    "card": "#181818",
    "card_hover": "#222222",
    "surface": "#0a0a0a",
    "text": "#e8e8e8",
    "muted": "#9a9a9a",
    "accent": "#4cc9f0",
    "danger": "#ff4d6d",
    "good": "#2ee59d",
    "line": "#2a2f33",
}

DEFAULT_UI = dict(UI)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ppm", ".pgm"}
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm"}

FONT_H1 = ("Segoe UI", 20, "bold")
FONT_H2 = ("Segoe UI", 14, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 10)


@dataclass
class SurfaceSpec:
    kind: str
    title: str
    body: str
    accent: str = UI["accent"]


def station_label(readiness: Dict[str, Any], fallback: str = "Radio OS Station") -> str:
    return str(readiness.get("station") or fallback)


def manifest_from_oradio(package: Path) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(package) as zf:
            if "manifest.yaml" not in zf.namelist():
                return {}
            import yaml
            data = yaml.safe_load(zf.read("manifest.yaml")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def manifest_from_dir(extract_dir: Path) -> Dict[str, Any]:
    path = Path(extract_dir) / "manifest.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def art_background_spec(manifest: Dict[str, Any]) -> Dict[str, Any]:
    art = manifest.get("art") if isinstance(manifest.get("art"), dict) else {}
    bg = art.get("global_bg") if isinstance(art.get("global_bg"), dict) else {}
    kind = str(bg.get("type") or "color").strip().lower()
    if kind not in {"color", "gradient", "image", "video"}:
        kind = "color"
    gradient = bg.get("gradient") if isinstance(bg.get("gradient"), dict) else {}
    return {
        "type": kind,
        "value": str(bg.get("value") or "").strip(),
        "path": str(bg.get("path") or "").strip(),
        "gradient": dict(gradient),
    }


def panel_theme_color(block: Dict[str, Any], fallback: str) -> str:
    if not isinstance(block, dict):
        return fallback
    kind = str(block.get("type") or "color").strip().lower()
    if kind == "gradient" and isinstance(block.get("gradient"), dict):
        return str(block["gradient"].get("color1") or block.get("value") or fallback)
    return str(block.get("value") or fallback)


def palette_from_manifest(manifest: Dict[str, Any]) -> Dict[str, str]:
    palette = dict(DEFAULT_UI)
    art = manifest.get("art") if isinstance(manifest.get("art"), dict) else {}
    global_bg = art_background_spec(manifest)
    panels = art.get("panels") if isinstance(art.get("panels"), dict) else {}
    toolbar = panels.get("toolbar") if isinstance(panels.get("toolbar"), dict) else {}
    subtitle = panels.get("subtitle") if isinstance(panels.get("subtitle"), dict) else {}

    if global_bg["type"] == "gradient" and global_bg["gradient"]:
        c1 = str(global_bg["gradient"].get("color1") or global_bg["value"] or palette["bg"])
        c2 = str(global_bg["gradient"].get("color2") or global_bg["value"] or palette["surface"])
        palette["bg"] = c1
        palette["surface"] = c2
    elif global_bg["value"]:
        palette["bg"] = global_bg["value"]
        palette["surface"] = global_bg["value"]
    if toolbar:
        palette["panel"] = panel_theme_color(toolbar, palette["panel"])
    if subtitle:
        palette["subtitle"] = panel_theme_color(subtitle, palette.get("subtitle", "#000000"))
    if art.get("accent"):
        palette["accent"] = str(art["accent"])
    return palette


def apply_palette(palette: Dict[str, str]) -> None:
    UI.update(dict(DEFAULT_UI))
    UI.update({k: v for k, v in palette.items() if isinstance(v, str) and v})


def background_media_kind(path_or_ref: str, declared_type: str = "") -> str:
    suffix = Path(str(path_or_ref or "")).suffix.lower()
    if suffix == ".gif":
        return "gif"
    if suffix in VIDEO_SUFFIXES or declared_type == "video":
        return "video"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return "media"


def resolve_art_path_from_manifest(manifest: Dict[str, Any], extract_dir: Optional[Path] = None) -> Optional[Path]:
    spec = art_background_spec(manifest)
    ref = spec.get("path", "")
    if not ref or spec.get("type") not in {"image", "video"}:
        return None
    candidates: List[Path] = []
    ref_path = Path(ref)
    if ref_path.is_absolute():
        candidates.append(ref_path)
    if extract_dir:
        candidates.append(Path(extract_dir) / ref)
    candidates.append(BASE_DIR / ref)
    candidates.append(Path.cwd() / ref)
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def background_status(manifest: Dict[str, Any], extract_dir: Optional[Path] = None) -> Dict[str, Any]:
    spec = art_background_spec(manifest)
    if spec["type"] == "color":
        return {"mode": "color", "ok": True, "message": f"Color background {spec.get('value') or UI['bg']}"}
    if spec["type"] == "gradient":
        return {"mode": "gradient", "ok": True, "message": "Gradient wallpaper inherited from manifest"}
    resolved = resolve_art_path_from_manifest(manifest, extract_dir)
    media_kind = background_media_kind(spec.get("path", ""), spec["type"])
    if resolved:
        return {"mode": media_kind, "ok": True, "path": str(resolved), "message": f"{media_kind.title()} wallpaper inherited"}
    return {
        "mode": media_kind,
        "ok": False,
        "path": spec.get("path", ""),
        "message": f"{media_kind.title()} wallpaper declared but not found on this machine",
    }


def blocking_text(readiness: Dict[str, Any]) -> str:
    blocking = readiness.get("blocking", [])
    if not blocking:
        return "Ready to broadcast."
    return "\n".join(f"- {item}" for item in blocking)


def surface_specs_from_readiness(readiness: Dict[str, Any]) -> List[SurfaceSpec]:
    station = station_label(readiness)
    specs = [
        SurfaceSpec("station", station, "Persistent narrated world. The station keeps moving while you are away."),
        SurfaceSpec("readiness", "Readiness", "READY TO BROADCAST" if readiness.get("ready") else blocking_text(readiness),
                    UI["good"] if readiness.get("ready") else UI["danger"]),
    ]
    voices = readiness.get("voices", [])
    if voices:
        body = "\n".join(
            f"{v.get('role', 'voice')}: {v.get('source') or 'unresolved'}"
            for v in voices[:8]
        )
        specs.append(SurfaceSpec("voices", "Voices", body))
    piper = readiness.get("piper", {})
    if piper.get("needed"):
        specs.append(SurfaceSpec("piper", "Piper", piper.get("bin") or "Piper will resolve from machine cache.", UI["muted"]))
    return specs


def clean_runtime_line(line: str) -> str:
    text = (line or "").strip()
    if not text:
        return ""
    # bookmark.py logs can be verbose; preserve content while trimming noisy prefixes.
    for prefix in ("[bookmark]", "[UI]", "[init]", "[audio]", "[host]"):
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix):].strip(" -:")
    return text


def is_subtitle_candidate(line: str) -> bool:
    text = line.lower()
    return "host:" in text or "subtitle" in text or "now playing" in text


def append_recent(items: List[str], item: str, limit: int = 12) -> List[str]:
    text = (item or "").strip()
    if not text:
        return items[-limit:]
    items.append(text)
    del items[:-limit]
    return items


def event_kind(line: str) -> str:
    text = (line or "").lower()
    if text.startswith("audio segment:"):
        return "audio"
    if "candidate" in text or "feed" in text or "antenna" in text or "observation" in text:
        return "signal"
    if is_subtitle_candidate(line):
        return "subtitle"
    return "log"


def surface_body_for_kind(
    kind: str,
    *,
    readiness: Dict[str, Any],
    extract_dir: Optional[Path],
    log_lines: List[str],
    audio_lines: List[str],
    signal_lines: List[str],
) -> str:
    if kind == "log":
        return "\n".join(log_lines[-14:]) or "Runtime log lines will wash through this surface as the station speaks."
    if kind == "audio":
        audio_dir = extract_dir / ".audio_pipe" if extract_dir else Path(".audio_pipe")
        recent = "\n".join(audio_lines[-10:])
        return f"Audio pipe: {audio_dir}\n\n{recent or 'No emitted audio segments yet.'}"
    if kind == "signal":
        return "\n".join(signal_lines[-10:]) or "No hot signal yet. Silence is a valid state."
    if kind == "readiness":
        return blocking_text(readiness)
    return "Surface waiting for compatible station events."


def transport_capabilities() -> Dict[str, bool]:
    return {
        "play": True,
        "stop": True,
        "restart": True,
        "open_audio_pipe": True,
        "spawn_surface": True,
        "pause": False,
        "rewind": False,
        "forward": False,
    }


def unsupported_transport_message(action: str) -> str:
    return f"{action.title()} is not exposed by the preserved bookmark.py kernel yet."


class AudioPipeWatcher:
    def __init__(self, events: "queue.Queue[str]"):
        self.events = events
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: set[Path] = set()

    def start(self, audio_dir: Path) -> None:
        self.stop()
        self._stop.clear()
        audio_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, args=(audio_dir,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._thread = None

    def _run(self, audio_dir: Path) -> None:
        while not self._stop.is_set():
            for wav in sorted(audio_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime):
                if wav in self._seen:
                    continue
                self._seen.add(wav)
                self.events.put(f"Audio segment: {wav.name}")
                self._play_wav(wav)
            time.sleep(0.5)

    def _play_wav(self, wav: Path) -> None:
        try:
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(str(wav), winsound.SND_FILENAME)
            elif sys.platform == "darwin":
                subprocess.run(["afplay", str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                player = "paplay" if _which("paplay") else "aplay"
                subprocess.run([player, str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception as exc:
            self.events.put(f"Audio playback skipped: {exc}")


def _which(name: str) -> str:
    from shutil import which
    return which(name) or ""


class KernelProcess:
    def __init__(self, events: "queue.Queue[str]"):
        self.events = events
        self.proc: Optional[subprocess.Popen[str]] = None
        self._thread: Optional[threading.Thread] = None

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def launch(self, extract_dir: Path, readiness: Dict[str, Any]) -> None:
        self.stop()
        env = oradio_player.build_launch_env(extract_dir, readiness, headless=True, local_audio=False)
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
        self.events.put(f"Kernel started (PID {self.proc.pid})")
        self._thread = threading.Thread(target=self._capture, args=(self.proc,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
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
        self.proc = None

    def _capture(self, proc: subprocess.Popen[str]) -> None:
        if not proc.stdout:
            return
        for line in proc.stdout:
            cleaned = clean_runtime_line(line)
            if cleaned:
                self.events.put(cleaned)
        self.events.put(f"Kernel exited with code {proc.poll()}")


class OradioRuntimeShell:
    def __init__(self, package: Optional[Path] = None):
        self.package = package
        self.current_manifest: Dict[str, Any] = manifest_from_oradio(self.package) if self.package else {}
        if self.current_manifest:
            apply_palette(palette_from_manifest(self.current_manifest))
        self.extract_dir: Optional[Path] = None
        self.readiness: Dict[str, Any] = {}
        self.events: "queue.Queue[str]" = queue.Queue()
        self.kernel = KernelProcess(self.events)
        self.audio = AudioPipeWatcher(self.events)
        self.surface_count = 0
        self.log_lines: List[str] = []
        self.audio_lines: List[str] = []
        self.signal_lines: List[str] = []
        self.live_surface_vars: Dict[str, List[tk.StringVar]] = {}
        self.transport_buttons: Dict[str, tk.Button] = {}
        self.bg_label: Optional[tk.Label] = None
        self.bg_photo: Optional[tk.PhotoImage] = None
        self.bg_frames: List[Any] = []
        self.bg_durations: List[int] = []
        self.bg_frame_idx = 0
        self.bg_after_id: Optional[str] = None
        self.bg_resize_after_id: Optional[str] = None
        self.bg_last_size: tuple[int, int] = (0, 0)

        self.root = tk.Tk()
        self.root.title("Radio OS Player")
        self.root.geometry("1280x820")
        self.root.minsize(1040, 680)
        self.root.configure(bg=UI["bg"])

        self.status_var = tk.StringVar(value="Open a .oradio station")
        self.subtitle_var = tk.StringVar(value="No narration yet.")
        self.now_var = tk.StringVar(value="")
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(250, self._tick)

        if self.package:
            self.open_package(self.package)

    def _build_layout(self) -> None:
        self.top = tk.Frame(self.root, bg=UI["bg"], height=64)
        self.top.pack(fill="x", side="top")
        tk.Label(self.top, text="Radio OS Player", font=FONT_H1, fg=UI["text"], bg=UI["bg"]).pack(side="left", padx=18)
        tk.Label(self.top, textvariable=self.status_var, font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"]).pack(side="left", padx=8)
        tk.Button(self.top, text="Open .oradio", command=self.choose_package, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="right", padx=(4, 18), pady=16)
        tk.Button(self.top, text="Tune-In", command=self.show_tune_in_gate, bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="right", padx=4, pady=16)

        self.transport = tk.Frame(self.root, bg=UI["panel"], height=54)
        self.transport.pack(fill="x", side="top")
        self._build_transport_controls(self.transport)
        tk.Label(self.transport, textvariable=self.now_var, fg=UI["muted"], bg=UI["panel"], font=FONT_SMALL).pack(side="right", padx=18)

        self.body = tk.Frame(self.root, bg=UI["bg"])
        self.body.pack(fill="both", expand=True)
        self.palette = tk.Frame(self.body, bg=UI["panel"], width=250)
        self.palette.pack(side="left", fill="y")
        self.palette.pack_propagate(False)
        tk.Label(self.palette, text="Surface Palette", font=FONT_H2, fg=UI["text"], bg=UI["panel"]).pack(anchor="w", padx=14, pady=(14, 8))
        for kind, title in (("signal", "Signal Monitor"), ("log", "Runtime Log"), ("audio", "Audio Pipe"), ("readiness", "Readiness")):
            self._palette_card(kind, title)

        self.stage_wrap = tk.Frame(self.body, bg=UI["surface"])
        self.stage_wrap.pack(side="left", fill="both", expand=True)
        self.stage = tk.Frame(self.stage_wrap, bg=UI["surface"])
        self.stage.pack(fill="both", expand=True, padx=16, pady=16)
        self.stage.bind("<Configure>", self._schedule_background_repaint)

        subtitle_bg = UI.get("subtitle", "#000000")
        self.bottom = tk.Frame(self.root, bg=subtitle_bg, height=92)
        self.bottom.pack(fill="x", side="bottom")
        self.bottom.pack_propagate(False)
        tk.Label(self.bottom, text="HOST", fg=UI["accent"], bg=subtitle_bg, font=FONT_SMALL).pack(anchor="w", padx=18, pady=(10, 0))
        tk.Label(self.bottom, textvariable=self.subtitle_var, fg=UI["text"], bg=subtitle_bg, font=("Segoe UI", 18, "bold"), wraplength=1180, justify="left").pack(anchor="w", padx=18)
        self.apply_manifest_background()

    def _build_transport_controls(self, parent: tk.Widget) -> None:
        controls = [
            ("rewind", "Rewind", lambda: self.transport_unsupported("rewind")),
            ("play", "Play", self.start_kernel),
            ("pause", "Pause", lambda: self.transport_unsupported("pause")),
            ("stop", "Stop", self.stop_kernel),
            ("restart", "Restart", self.restart_kernel),
            ("forward", "Forward", lambda: self.transport_unsupported("forward")),
            ("open_audio_pipe", "Audio Pipe", self.open_audio_pipe),
            ("spawn_surface", "Spawn Surface", self.spawn_palette_surface),
        ]
        caps = transport_capabilities()
        for key, label, cmd in controls:
            bg = UI["accent"] if key == "play" else UI["card"]
            fg = "#000" if key == "play" else UI["text"]
            btn = tk.Button(parent, text=label, command=cmd, bg=bg, fg=fg, relief="flat", padx=14, pady=6)
            btn.pack(side="left", padx=6, pady=10)
            self.transport_buttons[key] = btn
            if not caps.get(key, False):
                btn.configure(state="disabled", fg=UI["muted"])
        self.update_transport_state()

    def _palette_card(self, kind: str, title: str) -> None:
        card = tk.Frame(self.palette, bg=UI["card"], bd=0)
        card.pack(fill="x", padx=12, pady=6)
        tk.Label(card, text=title, font=FONT_BODY, fg=UI["text"], bg=UI["card"]).pack(anchor="w", padx=10, pady=(8, 2))
        tk.Label(card, text="Spawn transient surface", font=FONT_SMALL, fg=UI["muted"], bg=UI["card"]).pack(anchor="w", padx=10)
        tk.Button(card, text="Spawn", command=lambda k=kind: self.spawn_surface_kind(k), bg=UI["panel"], fg=UI["accent"], relief="flat").pack(anchor="e", padx=8, pady=8)

    def _schedule_background_repaint(self, event: tk.Event) -> None:
        if (int(event.width), int(event.height)) == self.bg_last_size:
            return
        if self.bg_resize_after_id:
            try:
                self.root.after_cancel(self.bg_resize_after_id)
            except tk.TclError:
                pass
        self.bg_resize_after_id = self.root.after(220, self.apply_manifest_background)

    def _ensure_bg_label(self) -> tk.Label:
        if self.bg_label is None:
            self.bg_label = tk.Label(self.stage, bd=0, bg=UI["surface"])
        self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self.bg_label.lower()
        return self.bg_label

    def _cancel_background_animation(self) -> None:
        if self.bg_after_id:
            try:
                self.root.after_cancel(self.bg_after_id)
            except tk.TclError:
                pass
        self.bg_after_id = None
        self.bg_frames = []
        self.bg_durations = []
        self.bg_frame_idx = 0

    @staticmethod
    def _hex_rgb(value: str, fallback: str = "#0e0e0e") -> tuple[int, int, int]:
        raw = str(value or fallback).strip().lstrip("#")
        if len(raw) != 6:
            raw = fallback.lstrip("#")
        try:
            return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            return tuple(int(fallback.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]

    @staticmethod
    def _rgb_hex(rgb: tuple[int, int, int]) -> str:
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    def _gradient_photo(self, width: int, height: int, gradient: Dict[str, Any], fallback: str) -> tk.PhotoImage:
        width = max(int(width or 1), 1)
        height = max(int(height or 1), 1)
        c1 = self._hex_rgb(str(gradient.get("color1") or fallback), fallback)
        c2 = self._hex_rgb(str(gradient.get("color2") or fallback), fallback)
        photo = tk.PhotoImage(width=width, height=height)
        for x in range(width):
            ratio = x / max(width - 1, 1)
            rgb = (
                int(c1[0] * (1 - ratio) + c2[0] * ratio),
                int(c1[1] * (1 - ratio) + c2[1] * ratio),
                int(c1[2] * (1 - ratio) + c2[2] * ratio),
            )
            photo.put(self._rgb_hex(rgb), to=(x, 0, x + 1, height))
        return photo

    def _fit_image_photo(self, path: Path, width: int, height: int) -> Optional[Any]:
        try:
            from PIL import Image as PILImage, ImageOps, ImageTk as PILImageTk
            img = PILImage.open(path)
            if getattr(img, "is_animated", False):
                img.seek(0)
            resampling = getattr(PILImage, "Resampling", PILImage).LANCZOS
            fitted = ImageOps.fit(img.convert("RGBA"), (max(width, 1), max(height, 1)), method=resampling)
            return PILImageTk.PhotoImage(fitted)
        except Exception:
            try:
                return tk.PhotoImage(file=str(path))
            except tk.TclError:
                return None

    def _load_gif_frames(self, path: Path, width: int, height: int) -> tuple[List[Any], List[int]]:
        try:
            from PIL import Image as PILImage, ImageOps, ImageSequence, ImageTk as PILImageTk
            img = PILImage.open(path)
            resampling = getattr(PILImage, "Resampling", PILImage).LANCZOS
            frames: List[Any] = []
            durations: List[int] = []
            for frame in ImageSequence.Iterator(img):
                duration = int(frame.info.get("duration") or 100)
                fitted = ImageOps.fit(frame.convert("RGBA"), (max(width, 1), max(height, 1)), method=resampling)
                frames.append(PILImageTk.PhotoImage(fitted))
                durations.append(max(duration, 40))
                if len(frames) >= 120:
                    break
            return frames, durations
        except Exception:
            photo = self._fit_image_photo(path, width, height)
            return ([photo], [250]) if photo is not None else ([], [])

    def _load_video_poster(self, path: Path, width: int, height: int) -> Optional[Any]:
        try:
            import imageio.v3 as iio
            from PIL import Image as PILImage, ImageOps, ImageTk as PILImageTk
            frame = iio.imread(path, index=0)
            img = PILImage.fromarray(frame)
            resampling = getattr(PILImage, "Resampling", PILImage).LANCZOS
            fitted = ImageOps.fit(img.convert("RGBA"), (max(width, 1), max(height, 1)), method=resampling)
            return PILImageTk.PhotoImage(fitted)
        except Exception:
            return None

    def _animate_background(self) -> None:
        if not self.bg_label or not self.bg_frames:
            return
        frame = self.bg_frames[self.bg_frame_idx % len(self.bg_frames)]
        self.bg_label.configure(image=frame, text="")
        self.bg_label.lower()
        delay = self.bg_durations[self.bg_frame_idx % len(self.bg_durations)] if self.bg_durations else 120
        self.bg_frame_idx = (self.bg_frame_idx + 1) % len(self.bg_frames)
        self.bg_after_id = self.root.after(max(int(delay), 40), self._animate_background)

    def refresh_shell_palette(self) -> None:
        self.root.configure(bg=UI["bg"])
        for widget, bg in (
            (getattr(self, "top", None), UI["bg"]),
            (getattr(self, "body", None), UI["bg"]),
            (getattr(self, "transport", None), UI["panel"]),
            (getattr(self, "palette", None), UI["panel"]),
            (getattr(self, "stage_wrap", None), UI["surface"]),
            (getattr(self, "stage", None), UI["surface"]),
            (getattr(self, "bottom", None), UI.get("subtitle", "#000000")),
        ):
            if widget is not None:
                try:
                    widget.configure(bg=bg)
                except tk.TclError:
                    pass

    def apply_manifest_background(self) -> str:
        if not hasattr(self, "stage"):
            return "Background pending."
        self._cancel_background_animation()
        self.stage.update_idletasks()
        width = max(self.stage.winfo_width(), 640)
        height = max(self.stage.winfo_height(), 360)
        self.bg_last_size = (width, height)
        label = self._ensure_bg_label()
        spec = art_background_spec(self.current_manifest)
        fallback = spec.get("value") or UI["surface"]

        if spec["type"] == "gradient" and spec.get("gradient"):
            self.bg_photo = self._gradient_photo(width, height, spec["gradient"], fallback)
            label.configure(image=self.bg_photo, text="", bg=fallback)
            label.lower()
            return "Gradient wallpaper inherited."

        if spec["type"] in {"image", "video"} and spec.get("path"):
            resolved = resolve_art_path_from_manifest(self.current_manifest, self.extract_dir)
            media_kind = background_media_kind(spec["path"], spec["type"])
            if not resolved:
                label.configure(image="", text=f"{media_kind.title()} wallpaper missing\n{spec['path']}", fg=UI["muted"], bg=fallback, font=FONT_SMALL)
                label.lower()
                return f"{media_kind.title()} wallpaper declared but not found."
            if media_kind == "gif":
                self.bg_frames, self.bg_durations = self._load_gif_frames(resolved, width, height)
                if self.bg_frames:
                    self._animate_background()
                    return "GIF wallpaper inherited."
            elif media_kind == "video":
                self.bg_photo = self._load_video_poster(resolved, width, height)
                if self.bg_photo:
                    label.configure(image=self.bg_photo, text="", bg=fallback)
                    label.lower()
                    return "Video wallpaper poster inherited."
                label.configure(
                    image="",
                    text=f"Video wallpaper inherited\n{resolved.name}\nPoster renderer unavailable",
                    fg=UI["muted"],
                    bg=fallback,
                    font=FONT_SMALL,
                )
                label.lower()
                return "Video wallpaper inherited; poster renderer unavailable."
            else:
                self.bg_photo = self._fit_image_photo(resolved, width, height)
                if self.bg_photo:
                    label.configure(image=self.bg_photo, text="", bg=fallback)
                    label.lower()
                    return "Image wallpaper inherited."
            label.configure(image="", text=f"Could not render wallpaper\n{resolved.name}", fg=UI["muted"], bg=fallback, font=FONT_SMALL)
            label.lower()
            return "Wallpaper found but could not be rendered."

        label.configure(image="", text="", bg=fallback)
        label.lower()
        self.stage.configure(bg=fallback)
        return "Color wallpaper inherited."

    def choose_package(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Open .oradio", filetypes=[("Radio OS station", "*.oradio"), ("All files", "*.*")])
        if path:
            self.open_package(Path(path))

    def open_package(self, package: Path) -> None:
        self.package = package
        self.stop_kernel()
        self.extract_dir = Path(tempfile.mkdtemp(prefix="oradio_shell_"))
        self.log_lines = []
        self.audio_lines = []
        self.signal_lines = []
        try:
            oradio_resolver.extract_oradio(package, self.extract_dir)
            self.readiness = oradio_resolver.resolve_station(package, extract_dir=self.extract_dir, check_llm=True)
        except Exception as exc:
            messagebox.showerror("Open .oradio", str(exc))
            return
        self.current_manifest = manifest_from_dir(self.extract_dir) or manifest_from_oradio(package)
        apply_palette(palette_from_manifest(self.current_manifest))
        self.refresh_shell_palette()
        bg_note = self.apply_manifest_background()
        self.status_var.set(station_label(self.readiness, package.stem))
        self.now_var.set(("Ready" if self.readiness.get("ready") else "Tune-In required") + f" / {bg_note}")
        self.clear_surfaces()
        for spec in surface_specs_from_readiness(self.readiness):
            self.add_surface(spec)
        self.update_transport_state()
        if self.readiness.get("ready"):
            self.start_kernel()
        else:
            self.show_tune_in_gate()

    def start_kernel(self) -> None:
        if not self.extract_dir or not self.readiness:
            self.status_var.set("Open a .oradio first")
            return
        if not self.readiness.get("ready"):
            self.show_tune_in_gate()
            return
        self.kernel.launch(self.extract_dir, self.readiness)
        self.audio.start(self.extract_dir / ".audio_pipe")
        self.now_var.set("Broadcasting")
        self.update_transport_state()

    def stop_kernel(self) -> None:
        self.audio.stop()
        self.kernel.stop()
        self.now_var.set("Stopped")
        self.update_transport_state()

    def restart_kernel(self) -> None:
        if not self.extract_dir or not self.readiness.get("ready"):
            self.status_var.set("Open a ready .oradio first")
            self.update_transport_state()
            return
        self.stop_kernel()
        self.start_kernel()
        self.status_var.set("Broadcast restarted")

    def open_audio_pipe(self) -> None:
        if not self.extract_dir:
            self.status_var.set("Open a .oradio first")
            self.update_transport_state()
            return
        audio_dir = self.extract_dir / ".audio_pipe"
        audio_dir.mkdir(parents=True, exist_ok=True)
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(audio_dir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(audio_dir)])
            else:
                subprocess.Popen(["xdg-open", str(audio_dir)])
            self.status_var.set(f"Opened audio pipe: {audio_dir}")
        except Exception as exc:
            self.status_var.set(f"Could not open audio pipe: {exc}")

    def transport_unsupported(self, action: str) -> None:
        self.status_var.set(unsupported_transport_message(action))

    def update_transport_state(self) -> None:
        if not self.transport_buttons:
            return
        caps = transport_capabilities()
        has_station = self.extract_dir is not None and bool(self.readiness)
        ready = bool(self.readiness.get("ready"))
        running = self.kernel.is_alive()
        states = {
            "play": ready and not running,
            "stop": running,
            "restart": ready,
            "open_audio_pipe": has_station,
            "spawn_surface": True,
            "pause": False,
            "rewind": False,
            "forward": False,
        }
        for key, btn in self.transport_buttons.items():
            enabled = caps.get(key, False) and states.get(key, False)
            try:
                btn.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass

    def spawn_palette_surface(self) -> None:
        self.spawn_surface_kind("signal")

    def spawn_surface_kind(self, kind: str) -> None:
        body = surface_body_for_kind(
            kind,
            readiness=self.readiness,
            extract_dir=self.extract_dir,
            log_lines=self.log_lines,
            audio_lines=self.audio_lines,
            signal_lines=self.signal_lines,
        )
        live_kind = kind if kind in {"log", "audio", "signal", "readiness"} else None
        self.add_surface(SurfaceSpec(kind, kind.replace("_", " ").title(), body), live_kind=live_kind)

    def add_surface(self, spec: SurfaceSpec, *, live_kind: Optional[str] = None) -> None:
        self.surface_count += 1
        card = tk.Frame(self.stage, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1)
        card.grid(row=(self.surface_count - 1) // 2, column=(self.surface_count - 1) % 2, sticky="nsew", padx=8, pady=8)
        self.stage.grid_columnconfigure(0, weight=1)
        self.stage.grid_columnconfigure(1, weight=1)
        bar = tk.Frame(card, bg=UI["card"])
        bar.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(bar, text=spec.title, font=FONT_H2, fg=spec.accent, bg=UI["card"]).pack(side="left")
        tk.Button(bar, text="Close", command=card.destroy, bg=UI["surface"], fg=UI["muted"], relief="flat").pack(side="right")
        body_var = tk.StringVar(value=spec.body)
        tk.Label(card, textvariable=body_var, font=FONT_BODY, fg=UI["text"], bg=UI["card"], justify="left", wraplength=420).pack(fill="both", expand=True, padx=12, pady=(4, 14))
        if live_kind:
            self.live_surface_vars.setdefault(live_kind, []).append(body_var)

    def clear_surfaces(self) -> None:
        for child in self.stage.winfo_children():
            child.destroy()
        self.surface_count = 0
        self.live_surface_vars = {}

    def show_tune_in_gate(self) -> None:
        gate = tk.Toplevel(self.root)
        gate.title("Radio OS Tune-In")
        gate.geometry("560x360")
        gate.configure(bg=UI["bg"])
        llm = self.readiness.get("llm", {}) if isinstance(self.readiness.get("llm"), dict) else {}
        provider = tk.StringVar(value=str(llm.get("provider") or "ollama"))
        endpoint = tk.StringVar(value=str(llm.get("endpoint") or "http://127.0.0.1:11434/api/generate"))
        model = tk.StringVar(value=str(llm.get("model") or ""))
        api_key = tk.StringVar(value="")
        tk.Label(gate, text="Tune-In", font=FONT_H1, fg=UI["text"], bg=UI["bg"]).pack(anchor="w", padx=18, pady=(16, 6))
        tk.Label(gate, text=blocking_text(self.readiness), font=FONT_SMALL, fg=UI["muted"], bg=UI["bg"], justify="left", wraplength=500).pack(anchor="w", padx=18)
        self._gate_entry(gate, "Provider", provider)
        self._gate_entry(gate, "Endpoint", endpoint)
        self._gate_entry(gate, "Model", model)
        self._gate_entry(gate, "API key", api_key, show="*")

        def save(validate: bool = True, pull: bool = False) -> None:
            result = oradio_player.tune_in_membership(
                provider=provider.get(), endpoint=endpoint.get(), api_key=api_key.get(),
                model=model.get(), pull_model=pull, validate=validate,
            )
            if not result.get("ok"):
                messagebox.showerror("Tune-In", result.get("error", "Tune-In failed"), parent=gate)
                return
            messagebox.showinfo("Tune-In", "Saved. Re-opening station readiness.", parent=gate)
            gate.destroy()
            if self.package:
                self.open_package(self.package)

        buttons = tk.Frame(gate, bg=UI["bg"])
        buttons.pack(fill="x", padx=18, pady=14)
        tk.Button(buttons, text="Save + Validate", command=lambda: save(True, False), bg=UI["accent"], fg="#000", relief="flat").pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="Pull Ollama + Save", command=lambda: save(True, True), bg=UI["panel"], fg=UI["text"], relief="flat").pack(side="left", padx=8)
        tk.Button(buttons, text="Save Without Check", command=lambda: save(False, False), bg=UI["card"], fg=UI["muted"], relief="flat").pack(side="right")

    def _gate_entry(self, parent: tk.Widget, label: str, var: tk.StringVar, show: str = "") -> None:
        row = tk.Frame(parent, bg=UI["bg"])
        row.pack(fill="x", padx=18, pady=4)
        tk.Label(row, text=label, fg=UI["muted"], bg=UI["bg"], width=10, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, show=show, bg=UI["surface"], fg=UI["text"], insertbackground=UI["text"], relief="flat").pack(side="left", fill="x", expand=True)

    def _tick(self) -> None:
        try:
            while True:
                line = self.events.get_nowait()
                self._record_event(line)
                if is_subtitle_candidate(line):
                    self.subtitle_var.set(line)
                self.status_var.set(line[:120])
        except queue.Empty:
            pass
        self._refresh_live_surfaces()
        if self.kernel.is_alive():
            self.now_var.set("Broadcasting")
        self.update_transport_state()
        self.root.after(250, self._tick)

    def _record_event(self, line: str) -> None:
        append_recent(self.log_lines, line, 80)
        kind = event_kind(line)
        if kind == "audio":
            append_recent(self.audio_lines, line, 30)
        elif kind in {"signal", "subtitle"}:
            append_recent(self.signal_lines, line, 40)

    def _refresh_live_surfaces(self) -> None:
        for kind, vars_for_kind in list(self.live_surface_vars.items()):
            body = surface_body_for_kind(
                kind,
                readiness=self.readiness,
                extract_dir=self.extract_dir,
                log_lines=self.log_lines,
                audio_lines=self.audio_lines,
                signal_lines=self.signal_lines,
            )
            alive: List[tk.StringVar] = []
            for var in vars_for_kind:
                try:
                    var.set(body)
                    alive.append(var)
                except tk.TclError:
                    pass
            self.live_surface_vars[kind] = alive

    def _close(self) -> None:
        self.stop_kernel()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Open the Radio OS ambient .oradio shell.")
    parser.add_argument("package", nargs="?", type=Path)
    args = parser.parse_args(argv)
    package = args.package
    app = OradioRuntimeShell(package)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
