import os
import subprocess
import sys
from pathlib import Path

import descriptor_club_gate
import oradio_player
import provisioning


def _descriptor(voice_provider: str = "none"):
    spec = {
        "oradio": "loomed",
        "world": {"organ": "neikos", "seed": 42},
        "theme": "ribbon",
        "surfaces": ["ribbon"],
    }
    if voice_provider != "none":
        spec["surfaces"].append("voice")
        spec["club"] = ["voices", "llm"]
        spec["voice"] = {"provider": voice_provider, "assignments": {"host": "af_sarah"}}
    return spec


class _TempConfig:
    def __init__(self, tmp_path):
        self._path = tmp_path / "config.json"
        self._orig = provisioning.global_config_path

    def __enter__(self):
        provisioning.global_config_path = lambda: self._path  # type: ignore[assignment]
        return self._path

    def __exit__(self, *exc):
        provisioning.global_config_path = self._orig  # type: ignore[assignment]


def test_descriptor_gate_requires_voice_assets(tmp_path):
    with _TempConfig(tmp_path):
        state = descriptor_club_gate.descriptor_gate_needs(_descriptor("kokoro"))
        assert state["voice_requested"] is True
        assert state["voices_ready"] is False
        assert state["ready"] is False


def test_descriptor_gate_allows_non_voice_descriptors(tmp_path):
    with _TempConfig(tmp_path):
        state = descriptor_club_gate.descriptor_gate_needs(_descriptor("none"))
        assert state["voice_requested"] is False
        assert state["ready"] is True


def test_descriptor_gate_stops_asking_once_voice_assets_are_remembered(tmp_path):
    with _TempConfig(tmp_path):
        voices = tmp_path / "voices"
        voices.mkdir()
        (voices / "host.onnx").write_bytes(b"x")
        provisioning.save_voices_dir(voices)

        state = descriptor_club_gate.descriptor_gate_needs(_descriptor("kokoro"))
        assert state["voices_ready"] is True
        assert state["ready"] is True


def test_oradio_player_routes_descriptor_to_descriptor_helper(tmp_path, monkeypatch):
    descriptor = tmp_path / "demo.oradio"
    descriptor.write_text("oradio: demo\nworld:\n  organ: neikos\n", encoding="utf-8")

    called = {}

    def fake_launch_descriptor(path, *, gui_gate=True, descriptor=None):
        called["path"] = Path(path)
        called["gui_gate"] = gui_gate
        return 0

    monkeypatch.setattr(oradio_player, "launch_descriptor_oradio", fake_launch_descriptor)
    rc = oradio_player.launch_oradio(descriptor, gui_gate=True)
    assert rc == 0
    assert called["path"] == descriptor
    assert called["gui_gate"] is True


def test_oradio_player_main_opens_descriptor_safely(tmp_path, monkeypatch):
    descriptor = tmp_path / "demo.oradio"
    descriptor.write_text("oradio: demo\nworld:\n  organ: neikos\n", encoding="utf-8")

    called = {}

    def fake_launch_oradio(path, **kwargs):
        called["path"] = Path(path)
        called["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(oradio_player, "launch_oradio", fake_launch_oradio)
    rc = oradio_player.main([str(descriptor)])
    assert rc == 0
    assert called["path"] == descriptor


def test_runtime_bootstrap_cli_opens_real_example():
    repo_root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-m", "oradio_engine", "open", str(repo_root / "spec" / "examples" / "home-region.oradio"), "--steps", "1"]
    proc = subprocess.run(cmd, cwd=repo_root, capture_output=True)
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, stderr + "\n" + stdout
    assert "ran 1 ticks" in stdout
