"""Build a one-folder Windows Loom app with PyInstaller."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"


def main() -> int:
    try:
        import PyInstaller.__main__
    except Exception as exc:
        print(f"PyInstaller is not available: {exc}", file=sys.stderr)
        print("Install it with: python -m pip install pyinstaller", file=sys.stderr)
        return 2

    sep = ";" if os.name == "nt" else ":"
    add_data = [
        f"{ROOT / 'plugins'}{sep}plugins",
        f"{ROOT / 'spec'}{sep}spec",
    ]
    for path in (DIST_DIR / "loom", BUILD_DIR / "loom"):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    args = [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "loom",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--hidden-import",
        "oradio_runtime",
        "--hidden-import",
        "loom_player_ui",
        "--hidden-import",
        "descriptor_club_gate",
        "--hidden-import",
        "pydantic",
        "--hidden-import",
        "cv2",
        "--hidden-import",
        "PIL._tkinter_finder",
    ]
    for item in add_data:
        args.extend(["--add-data", item])
    args.append(str(ROOT / "oradio_player.py"))

    PyInstaller.__main__.run(args)
    exe = DIST_DIR / "loom" / ("loom.exe" if os.name == "nt" else "loom")
    if exe.exists():
        print(exe)
        return 0
    print("Build completed but loom executable was not found.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
