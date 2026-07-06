from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ribbon_shell_models import LaunchItem


class ShellLauncher:
    def __init__(self, workspace_root: Path, radio_root: Path) -> None:
        self.workspace_root = workspace_root
        self.radio_root = radio_root
        self.python_exe = Path(sys.executable)

    def launch(self, item: LaunchItem) -> None:
        if item.kind == "station":
            self._launch_station(item)
            return
        if item.kind == "oradio":
            self._launch_oradio(item)
            return
        if item.kind == "tool":
            self._launch_tool(item)
            return
        raise RuntimeError(f"Unsupported launch item kind: {item.kind}")

    def reveal(self, item: LaunchItem) -> None:
        path = Path(item.target_path)
        target = path if path.is_dir() else path.parent
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
            return
        subprocess.Popen(["xdg-open", str(target)])

    def _launch_station(self, item: LaunchItem) -> None:
        env = os.environ.copy()
        env["STATION_DIR"] = item.target_path
        env["RADIO_OS_ROOT"] = str(self.radio_root)
        env.setdefault("RADIO_OS_PLUGINS", str(self.radio_root / "plugins"))
        env.setdefault("RADIO_OS_VOICES", str(self.radio_root / "voices"))
        subprocess.Popen(
            [str(self.python_exe), "-u", str(self.radio_root / "bookmark.py")],
            cwd=str(self.radio_root),
            env=env,
        )

    def _launch_oradio(self, item: LaunchItem) -> None:
        subprocess.Popen(
            [str(self.python_exe), str(self.radio_root / "oradio_player.py"), item.target_path],
            cwd=str(self.radio_root),
        )

    def _launch_tool(self, item: LaunchItem) -> None:
        target = Path(item.target_path)
        args = [str(a) for a in item.meta.get("args", [])]
        if target.suffix.lower() == ".py":
            subprocess.Popen([str(self.python_exe), str(target), *args], cwd=str(target.parent))
            return
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
            return
        subprocess.Popen(["xdg-open", str(target)])
