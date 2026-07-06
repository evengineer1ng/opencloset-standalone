"""Tests for antenna_resolver.py — the antenna club (auto-find a target, else remember once).

Runnable two ways:
    python -m pytest tests/test_antenna_resolver.py -q
    python tests/test_antenna_resolver.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import provisioning  # noqa: E402
import antenna_resolver as ar  # noqa: E402


class _Swap:
    """Temporarily swap an attribute on a module (steam path / config path), restored on exit."""

    def __init__(self, mod, attr, value):
        self.mod, self.attr, self.value = mod, attr, value

    def __enter__(self):
        self._orig = getattr(self.mod, self.attr)
        setattr(self.mod, self.attr, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.mod, self.attr, self._orig)


def test_infer_target_shapes():
    assert ar.infer_target("w", {"plugin": "antenna_http", "url": "http://x"})["kind"] == "http"
    assert ar.infer_target("r", {"plugin": "rss", "url": "http://x/feed.xml"})["kind"] == "rss"
    assert ar.infer_target("g", {"plugin": "antenna_bridge", "bridge_dir": "C:/Games/Fallout4"})["kind"] == "folder"
    assert ar.infer_target("l", {"plugin": "antenna_bridge", "path": "C:/logs/app.log"})["kind"] == "file"
    assert ar.infer_target("p", {"udp_port": 5005})["kind"] == "port"
    assert ar.infer_target("c", {"command": "myharness --run"})["kind"] == "command"
    assert ar.infer_target("e", {"target": {"kind": "folder", "names": ["X"]}})["kind"] == "folder"


def test_find_game_folder_in_fake_steam():
    with tempfile.TemporaryDirectory() as td:
        steam = Path(td)
        (steam / "steamapps" / "common" / "Fallout4").mkdir(parents=True)
        with _Swap(ar, "_steam_path", lambda: steam):
            hit = ar.find_game_folder(["Fallout4", "Fallout 4"])
            assert hit is not None and hit.name == "Fallout4"


def test_resolve_folder_uses_steam_appmanifest():
    with tempfile.TemporaryDirectory() as td:
        steam = Path(td)
        apps = steam / "steamapps"
        (apps / "common" / "Fallout 4").mkdir(parents=True)
        (apps / "appmanifest_377160.acf").write_text('"AppState"\n{\n"installdir"\t"Fallout 4"\n}\n', encoding="utf-8")
        with _Swap(ar, "_steam_path", lambda: steam):
            res = ar.resolve_target({"kind": "folder", "names": [], "steam_appid": "377160", "key": "game:fo4"}, "game:fo4")
            assert res["status"] == "found"
            assert res["source"] == "auto"
            assert Path(res["path"]).name == "Fallout 4"


def test_remembered_target_reused():
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.json"
        target = Path(td) / "MyFalloutInstall"
        target.mkdir()
        with _Swap(provisioning, "global_config_path", lambda: cfg):
            provisioning.save_antenna_target("game:fo4", target)
            res = ar.resolve_target({"kind": "folder", "names": ["Nope"], "key": "game:fo4"}, "game:fo4")
            assert res["status"] == "found"
            assert res["source"] == "remembered"
            assert Path(res["path"]) == target


def test_resolve_folder_needs_target_when_nothing_found():
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.json"  # empty club
        with _Swap(provisioning, "global_config_path", lambda: cfg), _Swap(ar, "_steam_path", lambda: None):
            res = ar.resolve_target({"kind": "folder", "names": ["NoSuchGameXYZ"], "key": "game:none"}, "game:none")
            assert res["status"] == "needs_target"


def test_resolve_command_found():
    res = ar.resolve_target({"kind": "command", "command": f"{Path(sys.executable).name} --version"}, "c")
    # python is on PATH in the test runner
    assert res["status"] in ("found", "missing_command")  # found on normal setups


def test_resolve_port_free():
    res = ar.resolve_target({"kind": "port", "port": 59607}, "p")  # almost certainly nothing here
    assert res["status"] in ("port_free", "found")


def test_http_unreachable_is_not_a_path_problem():
    res = ar.resolve_target({"kind": "http", "url": "http://127.0.0.1:9/definitely-nothing"}, "h")
    assert res["status"] == "unreachable"
    assert "running" in res["message"].lower()  # earnest: it's a service problem, not a missing file


def test_apply_resolved_targets_rewrites_to_remembered_path():
    """resolution → use: a folder antenna's config key is rewritten to the remembered install path."""
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "config.json"
        real = Path(td) / "RealFalloutInstall"
        real.mkdir()
        with _Swap(provisioning, "global_config_path", lambda: cfg_path), _Swap(ar, "_steam_path", lambda: None):
            provisioning.save_antenna_target("antenna_bridge:game", real)
            manifest = {"feeds": {"game": {"plugin": "antenna_bridge", "bridge_dir": r"C:\nope\Fallout4"}}}
            patched, applied = ar.apply_resolved_targets(manifest)
            assert applied, "should have pointed the antenna at the remembered path"
            assert patched["feeds"]["game"]["bridge_dir"] == str(real)
            assert manifest["feeds"]["game"]["bridge_dir"] == r"C:\nope\Fallout4"  # input not mutated


def test_apply_resolved_targets_leaves_non_path_antennas_alone():
    manifest = {"feeds": {
        "w": {"plugin": "antenna_http", "url": "http://x"},
        "social": {"plugin": "bluesky", "password": "secret"},
    }}
    patched, applied = ar.apply_resolved_targets(manifest)
    assert applied == []
    assert patched["feeds"]["w"]["url"] == "http://x"


def _run_standalone():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERR   {fn.__name__}: {exc!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
