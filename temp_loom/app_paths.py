"""Shared path helpers for source and frozen Loom runtime paths."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        return Path(str(meipass)).resolve()
    return Path(__file__).resolve().parent


def install_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return bundle_root()


def player_script() -> Path:
    return bundle_root() / "oradio_player.py"


def launcher_executable() -> Path:
    return Path(sys.executable).resolve()


def opener_command(
    target: Optional[Path] = None,
    *,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    if is_frozen():
        cmd: List[str] = [str(launcher_executable())]
    else:
        cmd = [sys.executable, str(player_script())]
    if extra_args:
        cmd.extend(str(part) for part in extra_args)
    if target is not None:
        cmd.append(str(target))
    return cmd


def packaged_error_log() -> Path:
    return install_root() / "loom-error.log"


def append_packaged_error(text: str) -> None:
    path = packaged_error_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(text.rstrip() + "\n")
