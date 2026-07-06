"""Tests for the asset club — voices/piper remembered machine-level (ask once, reuse forever).

Runnable two ways:
    python -m pytest tests/test_club_assets.py -q
    python tests/test_club_assets.py            (standalone; the venv may lack pytest)

All tests redirect provisioning's global config to a temp file so the real %APPDATA% config is
never touched.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import provisioning  # noqa: E402
import oradio_resolver  # noqa: E402


class _TempConfig:
    """Point provisioning at a throwaway config.json for the duration of a block."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory(prefix="club_cfg_")
        self._orig = provisioning.global_config_path

    def __enter__(self):
        cfg = Path(self._dir.name) / "config.json"
        provisioning.global_config_path = lambda: cfg  # type: ignore[assignment]
        return Path(self._dir.name)

    def __exit__(self, *exc):
        provisioning.global_config_path = self._orig  # type: ignore[assignment]
        self._dir.cleanup()


def test_save_and_get_voices_dir():
    with _TempConfig() as root:
        voices = root / "myvoices"
        voices.mkdir()
        (voices / "host.onnx").write_bytes(b"x")
        res = provisioning.save_voices_dir(voices)
        assert res["ok"] is True
        assert res["voice_files"] == 1
        assert str(voices) in provisioning.get_voices_dirs()


def test_save_voices_dir_rejects_nonfolder():
    with _TempConfig() as root:
        res = provisioning.save_voices_dir(root / "nope")
        assert res["ok"] is False


def test_get_voices_dirs_skips_missing():
    with _TempConfig() as root:
        gone = root / "gone"
        gone.mkdir()
        provisioning.save_voices_dir(gone)
        gone.rmdir()  # the remembered location genuinely disappears
        assert provisioning.get_voices_dirs() == []  # we don't return dead paths (we'd re-ask)


def test_save_voices_dir_dedups_and_prepends():
    with _TempConfig() as root:
        a = root / "a"; a.mkdir()
        b = root / "b"; b.mkdir()
        provisioning.save_voices_dir(a)
        provisioning.save_voices_dir(b)
        provisioning.save_voices_dir(a)  # re-show a → moves to front, no dupe
        dirs = provisioning.get_voices_dirs()
        assert dirs[0] == str(a)
        assert dirs.count(str(a)) == 1


def test_save_and_get_piper_bin():
    with _TempConfig() as root:
        binp = root / "piper.exe"
        binp.write_bytes(b"MZ")
        assert provisioning.save_piper_bin(binp)["ok"] is True
        assert provisioning.get_piper_bin() == str(binp)


def test_resolver_finds_voice_in_remembered_dir():
    """The club payoff: a voices dir shown once resolves a station that didn't bundle the voice."""
    with _TempConfig() as root:
        voices = root / "remembered"
        voices.mkdir()
        (voices / "host.onnx").write_bytes(b"x")
        provisioning.save_voices_dir(voices)

        # A lock that needs host.onnx but did NOT bundle it → must resolve via machine cache.
        lock = {"voices": [{"role": "host", "ref": "host.onnx"}]}
        results = oradio_resolver.resolve_voices(lock, extract_dir=root / "empty_pkg")
        assert results and results[0]["ok"] is True
        assert results[0]["source"] == "machine-cache"


def test_save_and_list_transient_template():
    with _TempConfig() as root:
        src = root / "bulletin.txt"
        src.write_text("CASE: {title}", encoding="utf-8")
        res = provisioning.save_transient_template(src, alias="case_file")
        assert res["ok"] is True
        items = provisioning.list_transient_templates()
        assert any(item["name"] == "case_file" for item in items)
        assert provisioning.read_transient_template("case_file").startswith("CASE:")


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
