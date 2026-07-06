"""Radio OS — canonical theme system (the good one).

The library (`shell_bookmark.py`) already has a clean named-preset theme system: `COLOR_THEMES`
(dark / light / nord / dracula / **monokai**). The runtime (`bookmark.py`) has a different, weaker
theme path built around a raw `self.art` dict. The product decision (owner): **keep the named-preset
system — green monokai is correct — and integrate it OVER the runtime's theme system for the palette
(theme + accent), while the runtime keeps its own per-station BACKGROUND customization.**

This module is that single source of truth so the runtime fork and the library can share one set of
presets instead of drifting copies. Stdlib only, standalone-testable, touches no preserved file.

    palette(name)            -> flat UI dict (bg/panel/card/text/accent/...), like the library's UI
    runtime_art(name, ...)   -> the runtime's `self.art` shape, derived from a named preset, with the
                                background left customizable (pass global_bg to preserve a wallpaper)
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, Optional


# The base palette (mirrors shell_bookmark.py's default UI).
DEFAULT_PALETTE: Dict[str, str] = {
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
}

# Named presets — the canonical set (kept in sync with shell_bookmark.py's COLOR_THEMES).
COLOR_THEMES: Dict[str, Dict[str, str]] = {
    "dark": dict(DEFAULT_PALETTE),
    "light": {
        "bg": "#ffffff", "panel": "#f5f5f5", "card": "#fafafa", "card_hover": "#e8e8e8",
        "surface": "#f0f0f0", "text": "#1a1a1a", "muted": "#666666",
        "accent": "#0891b2", "danger": "#dc2626", "good": "#16a34a",
    },
    "nord": {
        "bg": "#2e3440", "panel": "#3b4252", "card": "#434c5e", "card_hover": "#4c566a",
        "surface": "#2e3440", "text": "#eceff4", "muted": "#d8dee9",
        "accent": "#88c0d0", "danger": "#bf616a", "good": "#a3be8c",
    },
    "dracula": {
        "bg": "#282a36", "panel": "#343746", "card": "#44475a", "card_hover": "#6272a4",
        "surface": "#21222c", "text": "#f8f8f2", "muted": "#6272a4",
        "accent": "#bd93f9", "danger": "#ff5555", "good": "#50fa7b",
    },
    "monokai": {
        "bg": "#272822", "panel": "#2d2e27", "card": "#3e3d32", "card_hover": "#49483e",
        "surface": "#1e1f1c", "text": "#f8f8f2", "muted": "#75715e",
        "accent": "#66d9ef", "danger": "#f92672", "good": "#a6e22e",
    },
}

# Owner decision: green monokai is the correct runtime default.
DEFAULT_THEME = "monokai"


def palette(name: str = DEFAULT_THEME) -> Dict[str, str]:
    """A flat UI palette dict for a named theme (falls back to the default theme)."""
    base = dict(DEFAULT_PALETTE)
    base.update(COLOR_THEMES.get(name, COLOR_THEMES[DEFAULT_THEME]))
    return base


def runtime_art(name: str = DEFAULT_THEME, *, global_bg: Optional[Dict[str, Any]] = None,
                subtitle_wave: bool = True) -> Dict[str, Any]:
    """Build the runtime's `self.art` structure from a named preset.

    The palette (panels + accent) comes from the theme; the BACKGROUND stays customizable — pass an
    existing `global_bg` (e.g. a per-station wallpaper) to preserve it, otherwise it defaults to the
    theme's base color. This is the integration seam: named-preset palette over the runtime, runtime
    keeps its background.
    """
    pal = palette(name)
    bg = deepcopy(global_bg) if isinstance(global_bg, dict) and global_bg else {"type": "color", "value": pal["bg"], "path": ""}
    return {
        "theme": name,
        "global_bg": bg,
        "panels": {
            "left": {"type": "color", "value": pal["panel"]},
            "center": {"type": "color", "value": pal["panel"]},
            "right": {"type": "color", "value": pal["panel"]},
            "toolbar": {"type": "color", "value": pal["bg"]},
            "subtitle": {"type": "color", "value": pal["bg"]},
        },
        "accent": pal["accent"],
        "subtitle_wave": subtitle_wave,
    }


# ---------------------------------------------------------------------------
# Library theme inheritance
# "Themes" stay one concept in one place: the Radio OS Library. If the Library is installed, a
# standalone runtime can INHERIT whatever theme the user picked there (which may not be monokai),
# keeping a single source of expression. If the Library isn't present, the runtime defaults to
# monokai. This is a read-only peek at the Library's global config — it never creates or writes it.
# ---------------------------------------------------------------------------
def library_config_path() -> str:
    """The Radio OS Library's global config path (read-only; does not create the directory)."""
    if os.name == "nt":
        base = os.path.join(os.getenv("APPDATA", os.path.expanduser("~")), "RadioOS")
    else:
        base = os.path.expanduser("~/.radioOS")
    return os.path.join(base, "config.json")


def library_installed() -> bool:
    """True if the Radio OS Library has written its global config on this machine."""
    return os.path.isfile(library_config_path())


def library_theme() -> Optional[str]:
    """The theme the user selected in the Library (general.theme), if it's a known preset; else None."""
    path = library_config_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    name = (cfg.get("general", {}) or {}).get("theme") if isinstance(cfg, dict) else None
    return name if name in COLOR_THEMES else None


def resolve_runtime_theme(inherit_from_library: bool = True) -> str:
    """The theme name the runtime should use: the Library's choice when present + inheritance is on,
    otherwise the monokai default. (The inherit toggle is the operator's; default is to inherit.)"""
    if inherit_from_library:
        inherited = library_theme()
        if inherited:
            return inherited
    return DEFAULT_THEME


if __name__ == "__main__":
    art = runtime_art("monokai")
    print("monokai accent:", art["accent"], "| bg:", art["global_bg"]["value"], "| panel:", art["panels"]["left"]["value"])
    assert art["accent"] == "#66d9ef" and art["global_bg"]["value"] == "#272822"
    kept = runtime_art("monokai", global_bg={"type": "image", "value": "", "path": "wall.png"})
    assert kept["global_bg"]["path"] == "wall.png", "wallpaper must be preserved"
    assert kept["accent"] == "#66d9ef", "palette still comes from the theme"
    # Inheritance: with the toggle off we always get monokai; on, we get the library theme if any.
    assert resolve_runtime_theme(inherit_from_library=False) == "monokai"
    print("library installed:", library_installed(), "| library theme:", library_theme(),
          "| resolved:", resolve_runtime_theme())
    print("radio_os_theme self-test OK")
