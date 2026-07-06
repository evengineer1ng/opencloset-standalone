"""Shared visual renderer for Loom playback and sidecar thumbnails."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import cv2  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    cv2 = None
try:
    import numpy as np  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    np = None

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from oradio_engine.club import DEFAULT_THEME
from oradio_engine.visual_index import VisualIndex, visual_seed
from oradio_engine.visual_tape import VisualTapeLog, VisualTapeSnapshot, build_visual_snapshot


PALETTES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "ribbon": {"bg": (11, 16, 32), "accent": (102, 210, 231), "secondary": (159, 134, 255)},
    "smoke": {"bg": (16, 16, 16), "accent": (212, 215, 221), "secondary": (108, 117, 125)},
    "aurora": {"bg": (8, 20, 31), "accent": (114, 241, 184), "secondary": (122, 162, 247)},
    "ember": {"bg": (24, 13, 8), "accent": (255, 140, 66), "secondary": (255, 61, 104)},
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".ogv", ".ogg"}


class VideoLoop:
    """Small reusable video loop reader for continuous preview playback."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._capture = None
        self._fps = 24.0
        self._frame_count = 0
        self._lock = threading.Lock()
        self._open()

    def _open(self) -> None:
        if cv2 is None:
            return
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            return
        self._capture = capture
        self._fps = float(capture.get(cv2.CAP_PROP_FPS)) or 24.0
        self._frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

    @property
    def ok(self) -> bool:
        return self._capture is not None

    def read(self, media_time: float) -> Optional[Image.Image]:
        if self._capture is None or cv2 is None:
            return None
        with self._lock:
            frame_count = max(1, self._frame_count)
            target = int(max(0.0, media_time) * self._fps) % frame_count
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, frame = self._capture.read()
            if not ok or frame is None:
                return None
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)

    def close(self) -> None:
        with self._lock:
            if self._capture is not None:
                self._capture.release()
                self._capture = None


def resolve_media_path(descriptor_path: Path, ref: str) -> Optional[Path]:
    if not ref:
        return None
    raw = Path(ref)
    if raw.is_absolute() and raw.exists():
        return raw
    candidate = descriptor_path.parent / ref
    if candidate.exists():
        return candidate
    cwd_candidate = Path.cwd() / ref
    if cwd_candidate.exists():
        return cwd_candidate
    return None


def visual_config(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    visual = descriptor.get("visual") if isinstance(descriptor.get("visual"), dict) else {}
    base = visual.get("base") if isinstance(visual.get("base"), dict) else {}
    if base:
        return {
            "mode": str(base.get("mode") or "builtin"),
            "theme": str(base.get("theme") or descriptor.get("theme") or DEFAULT_THEME),
            "path": str(base.get("path") or ""),
        }
    theme = str(descriptor.get("theme") or DEFAULT_THEME)
    mode = "builtin" if theme in PALETTES else "media"
    return {"mode": mode, "theme": theme if theme in PALETTES else DEFAULT_THEME, "path": theme if mode == "media" else ""}


def _fit_cover(image: Image.Image, size: Tuple[int, int]) -> Image.Image:
    width, height = size
    src_w, src_h = image.size
    scale = max(width / max(1, src_w), height / max(1, src_h))
    resized = image.resize((max(1, int(src_w * scale)), max(1, int(src_h * scale))), Image.Resampling.LANCZOS)
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _load_video_frame(path: Path, tick: int, media_time: float = 0.0, video_loop: Optional[VideoLoop] = None) -> Optional[Image.Image]:
    if video_loop is not None and video_loop.ok:
        frame = video_loop.read(media_time)
        if frame is not None:
            return frame
    if cv2 is None:
        return None
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return None
    try:
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        fps = float(capture.get(cv2.CAP_PROP_FPS)) or 24.0
        target = 0
        if frames > 0:
            target = int((max(0.0, media_time) * fps) % frames)
            if media_time <= 0:
                target = int((tick * max(1.0, fps / 4.0)) % frames)
        capture.set(cv2.CAP_PROP_POS_FRAMES, target)
        ok, frame = capture.read()
        if not ok or frame is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    finally:
        capture.release()


def _builtin_body(size: Tuple[int, int], theme_name: str, phase: float, caption: str) -> Image.Image:
    width, height = size
    palette = PALETTES.get(theme_name, PALETTES[DEFAULT_THEME])
    image = Image.new("RGB", size, palette["bg"])
    draw = ImageDraw.Draw(image, "RGBA")

    for y in range(height):
        mix = y / max(1, height - 1)
        color = tuple(
            int((palette["bg"][i] * (1.0 - mix)) + (palette["secondary"][i] * mix * 0.55))
            for i in range(3)
        )
        draw.line((0, y, width, y), fill=color + (255,))

    def ribbon(offset: float, amp: float, width_px: int, color: Tuple[int, int, int]) -> None:
        points: List[Tuple[float, float]] = []
        for x in range(0, width + 16, 16):
            wave = math.sin((x / 90.0) + phase + offset)
            y = (height * (0.58 + offset * 0.04)) + (amp * wave)
            points.append((x, y))
        draw.line(points, fill=color + (220,), width=width_px, joint="curve")

    ribbon(0.0, 42.0, 16, palette["accent"])
    ribbon(1.35, 28.0, 10, palette["secondary"])

    if caption:
        draw.text((30, height - 42), caption[:120], fill=(236, 241, 247, 210))
    return image


def _base_frame(
    descriptor: Dict[str, Any],
    descriptor_path: Path,
    size: Tuple[int, int],
    tick: int,
    phase: float,
    *,
    media_time: float = 0.0,
    video_loop: Optional[VideoLoop] = None,
) -> Tuple[Image.Image, str]:
    config = visual_config(descriptor)
    theme_name = config["theme"] if config["theme"] in PALETTES else DEFAULT_THEME
    caption = ""
    notes = descriptor.get("loom_notes") if isinstance(descriptor.get("loom_notes"), dict) else {}
    if notes:
        caption = str(notes.get("premise") or "")
    mode = config["mode"]
    path = resolve_media_path(descriptor_path, config["path"])

    if mode == "media" and path is not None:
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            image = Image.open(path).convert("RGB")
            return _fit_cover(image, size), f"media:{path.name}"
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            frame = _load_video_frame(path, tick, media_time=media_time, video_loop=video_loop)
            if frame is not None:
                return _fit_cover(frame, size), f"video:{path.name}"
            caption = f"{caption}\nVideo fallback: {path.name}".strip()
    return _builtin_body(size, theme_name, phase, caption), f"builtin:{theme_name}"


def _apply_color_shift(image: Image.Image, snapshot: VisualTapeSnapshot, index: VisualIndex, tick: int) -> Image.Image:
    shifted = image.convert("RGBA")
    r = index.color(tick, "r")
    g = index.color(tick, "g")
    b = index.color(tick, "b")
    overlay = Image.new(
        "RGBA",
        shifted.size,
        (
            int(60 + (120 * max(0.0, snapshot.hue_shift + 0.5) * r["u"])),
            int(80 + (120 * g["v"])),
            int(110 + (110 * b["w"])),
            int(26 + (70 * min(1.0, abs(snapshot.hue_shift) + snapshot.haze + snapshot.bloom * 0.5))),
        ),
    )
    return Image.alpha_composite(shifted, overlay)


def _apply_zoom(image: Image.Image, zoom: float) -> Image.Image:
    if zoom <= 1.001:
        return image
    width, height = image.size
    scaled = image.resize((int(width * zoom), int(height * zoom)), Image.Resampling.LANCZOS)
    left = max(0, (scaled.width - width) // 2)
    top = max(0, (scaled.height - height) // 2)
    return scaled.crop((left, top, left + width, top + height))


def _paint_overlay(image: Image.Image, snapshot: VisualTapeSnapshot, descriptor: Dict[str, Any]) -> Image.Image:
    canvas = image.convert("RGBA")
    width, height = canvas.size
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    theme_name = str(descriptor.get("theme") or DEFAULT_THEME)
    palette = PALETTES.get(theme_name if theme_name in PALETTES else DEFAULT_THEME, PALETTES[DEFAULT_THEME])

    for particle in snapshot.particles:
        x = particle["x"]
        y = particle["y"]
        r = particle["r"]
        alpha = int(255 * particle["alpha"])
        warm = particle.get("warm", 0.5)
        color = (
            int((palette["accent"][0] * (1.0 - warm)) + (255 * warm)),
            int((palette["accent"][1] * (1.0 - warm)) + (180 * warm)),
            int((palette["accent"][2] * (1.0 - warm)) + (90 * warm)),
        )
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color + (alpha,))

    for ripple in snapshot.ripples:
        radius = ripple["radius"]
        alpha = int(255 * ripple["alpha"])
        draw.ellipse(
            (
                ripple["cx"] - radius,
                ripple["cy"] - radius,
                ripple["cx"] + radius,
                ripple["cy"] + radius,
            ),
            outline=palette["secondary"] + (alpha,),
            width=max(1, int(2 + snapshot.breath * 2)),
        )

    for orbital in snapshot.orbitals:
        draw.ellipse(
            (
                orbital["cx"] - orbital["radius"],
                orbital["cy"] - orbital["radius"],
                orbital["cx"] + orbital["radius"],
                orbital["cy"] + orbital["radius"],
            ),
            outline=palette["accent"] + (int(255 * orbital["alpha"]),),
            width=max(1, int(orbital["thickness"])),
        )

    if snapshot.scanline_alpha > 0:
        line_alpha = int(255 * snapshot.scanline_alpha)
        for y in range(0, height, 4):
            draw.line((0, y, width, y), fill=(0, 0, 0, line_alpha), width=1)

    if snapshot.haze > 0:
        draw.rectangle((0, 0, width, height), fill=palette["secondary"] + (int(255 * snapshot.haze * 0.22),))
    if snapshot.veil > 0:
        draw.ellipse(
            (-width * 0.15, -height * 0.05, width * 0.8, height * 1.05),
            fill=palette["secondary"] + (int(255 * snapshot.veil * 0.12),),
        )
        draw.ellipse(
            (width * 0.3, -height * 0.1, width * 1.15, height * 0.95),
            fill=palette["accent"] + (int(255 * snapshot.veil * 0.10),),
        )

    composed = Image.alpha_composite(canvas, overlay)
    if snapshot.prism > 0:
        prism = Image.new("RGBA", composed.size, (0, 0, 0, 0))
        prism_draw = ImageDraw.Draw(prism, "RGBA")
        bands = [
            ((255, 90, 90), -10),
            ((90, 255, 170), 0),
            ((110, 150, 255), 10),
        ]
        for color, offset in bands:
            prism_draw.rectangle((max(0, offset), 0, width + min(0, offset), height), fill=color + (int(255 * snapshot.prism * 0.05),))
        composed = Image.alpha_composite(composed, prism)
    if snapshot.haze > 0.02:
        blur_radius = min(2.6, snapshot.haze * 6.0)
        composed = composed.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    if snapshot.bloom > 0.02:
        bloom = composed.filter(ImageFilter.GaussianBlur(radius=2.0 + (snapshot.bloom * 10.0)))
        composed = Image.blend(composed, bloom, min(0.35, snapshot.bloom * 0.45))
    enhancer = ImageEnhance.Contrast(composed)
    composed = enhancer.enhance(1.0 + (snapshot.breath * 0.06))
    if snapshot.glitch > 0.02:
        rgba = composed.convert("RGBA")
        r, g, b, a = rgba.split()
        shift = max(1, int(width * snapshot.glitch * 0.03))
        r = ImageChops.offset(r, shift, 0)
        b = ImageChops.offset(b, -shift, 0)
        composed = Image.merge("RGBA", (r, g, b, a))
    if snapshot.grain > 0.01 and np is not None:
        arr = np.array(composed.convert("RGB"), dtype=np.int16)
        noise = np.random.default_rng(int(snapshot.tick + snapshot.entries)).integers(
            low=-int(35 * snapshot.grain),
            high=int(35 * snapshot.grain) + 1,
            size=arr.shape,
            dtype=np.int16,
        )
        arr = np.clip(arr + noise, 0, 255).astype("uint8")
        composed = Image.fromarray(arr, mode="RGB").convert("RGBA")
    return composed


def render_visual_frame(
    descriptor: Dict[str, Any],
    descriptor_path: Path,
    tape_log: VisualTapeLog,
    *,
    tick: int,
    size: Tuple[int, int],
    phase: float = 0.0,
    media_time: float = 0.0,
    video_loop: Optional[VideoLoop] = None,
) -> Tuple[Image.Image, VisualTapeSnapshot, Dict[str, Any]]:
    index = VisualIndex(visual_seed(descriptor))
    snapshot = build_visual_snapshot(tape_log, index, tick, width=size[0], height=size[1])
    base, base_label = _base_frame(
        descriptor,
        descriptor_path,
        size,
        tick,
        phase,
        media_time=media_time,
        video_loop=video_loop,
    )
    frame = _apply_color_shift(base, snapshot, index, tick)
    frame = _apply_zoom(frame, snapshot.zoom)
    frame = _paint_overlay(frame, snapshot, descriptor)
    return frame.convert("RGBA"), snapshot, {"base": base_label}


def thumbnail_sidecar_path(descriptor_path: Path) -> Path:
    return descriptor_path.with_suffix(".thumbnail.png")


def write_visual_thumbnail(
    descriptor: Dict[str, Any],
    descriptor_path: Path,
    tape_log: VisualTapeLog,
    *,
    tick: int,
    size: Tuple[int, int] = (640, 360),
    media_time: float = 0.0,
) -> Path:
    image, _snapshot, _meta = render_visual_frame(
        descriptor,
        descriptor_path,
        tape_log,
        tick=tick,
        size=size,
        phase=tick * 0.35,
        media_time=media_time,
    )
    out = thumbnail_sidecar_path(descriptor_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out, format="PNG")
    return out
