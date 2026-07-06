from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class ThemePalette:
    bg: str
    ribbon_a: str
    ribbon_b: str
    ribbon_c: str
    overlay: str
    card: str
    line: str
    text: str
    muted: str
    accent: str


PALETTES = {
    "midnight": ThemePalette("#09111b", "#7ef0ff", "#7f8cff", "#f6f4ff", "#111821", "#16202b", "#284257", "#eef7ff", "#a6bdd1", "#6ce3ff"),
    "sunset": ThemePalette("#160d12", "#ff7b6b", "#ffc266", "#ffd8f0", "#22161c", "#2b1d24", "#5c3c4a", "#fff2ef", "#e0b7ab", "#ffb067"),
    "silver": ThemePalette("#101214", "#d7e1ef", "#87d0ff", "#ffffff", "#1a1f24", "#20262d", "#36404b", "#f4f7fb", "#b1bfce", "#7fd1ff"),
}


class RibbonStateMachine:
    def __init__(self) -> None:
        self.phase = "BOOT"
        self.palette_name = "midnight"
        self.boot_started = time.time()
        self.last_activity = time.time()
        self.launch_flash_until = 0.0

    def note_activity(self) -> None:
        self.last_activity = time.time()
        if self.phase != "BOOT":
            self.phase = "ACTIVE"

    def note_launch(self) -> None:
        self.launch_flash_until = time.time() + 1.3
        self.phase = "LAUNCH"

    def set_palette(self, name: str) -> None:
        if name in PALETTES:
            self.palette_name = name

    def tick(self) -> None:
        now = time.time()
        if self.phase == "BOOT" and now - self.boot_started > 1.2:
            self.phase = "ACTIVE"
            return
        if self.phase == "LAUNCH" and now >= self.launch_flash_until:
            self.phase = "ACTIVE"
            return
        if self.phase != "BOOT" and now - self.last_activity > 7.0:
            self.phase = "DIM"

    @property
    def palette(self) -> ThemePalette:
        return PALETTES[self.palette_name]

    def overlay_visible(self) -> bool:
        return self.phase in {"BOOT", "ACTIVE", "LAUNCH"}

    def overlay_alpha_hint(self) -> float:
        if self.phase == "BOOT":
            return 0.85
        if self.phase == "LAUNCH":
            return 0.95
        if self.phase == "DIM":
            return 0.18
        return 0.82

    def ribbon_geometry(self, width: int, height: int, steps: int = 44) -> List[List[Tuple[float, float]]]:
        now = time.time() - self.boot_started
        intensity = 1.18 if self.phase == "LAUNCH" else (0.72 if self.phase == "DIM" else 1.0)
        curves: List[List[Tuple[float, float]]] = []
        for band, base_y in enumerate((0.44, 0.52, 0.60), start=1):
            points: List[Tuple[float, float]] = []
            for idx in range(steps + 1):
                x = (idx / steps) * width
                drift = math.sin((idx * 0.31) + now * (0.85 + band * 0.08))
                pulse = math.cos((idx * 0.19) - now * (0.51 + band * 0.05))
                y = height * base_y + (height * 0.11 * drift + height * 0.04 * pulse) * intensity
                points.append((x, y))
            curves.append(points)
        return curves
