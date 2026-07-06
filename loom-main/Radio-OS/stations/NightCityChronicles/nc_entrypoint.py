#!/usr/bin/env python3
"""
Night City Chronicles — Station Entrypoint

A narrative station that weaves V's personal journey through Night City
into an ongoing story bible. Each session builds on the last chapter.

Usage:
    # Via launcher (recommended):
    python launcher.py NightCityChronicles

    # Direct:
    python stations/NightCityChronicles/nc_entrypoint.py
"""

import os
import sys

_HERE        = os.path.dirname(os.path.abspath(__file__))
_STATION_DIR = _HERE
_ROOT        = os.path.abspath(os.path.join(_HERE, "..", ".."))

sys.path.insert(0, _ROOT)

os.environ.setdefault("STATION_DIR",         _STATION_DIR)
os.environ.setdefault("STATION_DB_PATH",     os.path.join(_STATION_DIR, "station.sqlite"))
os.environ.setdefault("STATION_MEMORY_PATH", os.path.join(_STATION_DIR, "station_memory.json"))
os.environ.setdefault("RADIO_OS_ROOT",       _ROOT)
os.environ.setdefault("RADIO_OS_PLUGINS",    os.path.join(_ROOT, "plugins"))
os.environ.setdefault("RADIO_OS_VOICES",     os.path.join(_ROOT, "voices"))

# Bible lives alongside the station
os.environ.setdefault("NC_BIBLE_PATH", os.path.join(_STATION_DIR, "player_bible.json"))

import bookmark

if __name__ == "__main__":
    bookmark.main()
