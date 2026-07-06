#!/usr/bin/env python3
"""Verify helper — open a station in the new themed runtime fork (oradio_runtime.py) with its REAL GUI.

This does NOT touch the .oradio launch path. It is a standalone way to eyeball oradio_runtime.py's
monokai theme + frontend (palette from radio_os_theme.py; per-station background preserved) before we
rewire the player. It sets the minimal station env and launches the fork NON-headless.

    python run_runtime.py                          # opens stations/BasketballFM
    python run_runtime.py stations/HockeyFM         # a specific station
    python run_runtime.py stations/HockeyFM dracula # force a theme (dark|light|nord|dracula|monokai)

Notes:
  * With no theme arg, the runtime uses the Library's theme if installed, else monokai.
  * Pass a theme name to compare presets. RADIO_OS_INHERIT_LIBRARY_THEME=0 disables Library inherit.
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def main(argv):
    station = Path(argv[0]) if argv else BASE / "stations" / "BasketballFM"
    if not station.is_absolute():
        station = BASE / station
    if not (station / "manifest.yaml").is_file():
        print(f"No manifest.yaml in {station}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["STATION_DIR"] = str(station)
    env["RADIO_OS_ROOT"] = str(BASE)
    env.setdefault("RADIO_OS_PLUGINS", str(BASE / "plugins"))
    env.setdefault("RADIO_OS_VOICES", str(BASE / "voices"))
    env.pop("RADIO_OS_HEADLESS", None)  # we WANT the real GUI window
    if len(argv) > 1:
        env["RADIO_OS_THEME"] = argv[1]  # force a named theme for visual comparison

    theme_label = env.get("RADIO_OS_THEME", "auto (Library theme if installed, else monokai)")
    print(f"Opening {station.name} in oradio_runtime.py  ·  theme={theme_label}")
    return subprocess.call([sys.executable, str(BASE / "oradio_runtime.py")], env=env, cwd=str(BASE))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
