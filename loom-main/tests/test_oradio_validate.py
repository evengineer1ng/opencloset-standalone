"""Tests for oradio_validate.py — the `.oradio` format v1 validator.

Runnable:
    python -m pytest tests/test_oradio_validate.py -q
    python tests/test_oradio_validate.py
"""
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import oradio_validate as ov  # noqa: E402
import yaml  # noqa: E402


def _valid_members():
    return {
        "oradio.json": json.dumps({
            "format": "oradio", "format_version": "1.0",
            "station_id": "DemoFM", "station_name": "Demo FM",
            "entry": {"manifest": "manifest.yaml"},
        }),
        "manifest.yaml": yaml.safe_dump({"station": {"id": "DemoFM", "name": "Demo FM"}}),
        "requirements.json": json.dumps({"narration": "live_llm", "llm": {}, "voices": {}, "piper": {}}),
        "requirements.lock.json": json.dumps({"lock_version": "0.1", "voices": [], "sfx": [], "art": [], "unresolved": []}),
    }


def _write(tmp, members, *, polyglot=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data if isinstance(data, (bytes, bytearray)) else str(data))
    payload = buf.getvalue()
    if polyglot:
        payload = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"fake" + b"\xff\xd9" + payload
    p = Path(tmp) / "x.oradio"
    p.write_bytes(payload)
    return p


def test_valid_package_has_no_errors_or_warnings():
    with tempfile.TemporaryDirectory() as td:
        rep = ov.validate_oradio(_write(td, _valid_members()))
        assert rep["ok"] is True
        assert rep["errors"] == []
        assert rep["warnings"] == []
        assert rep["info"]["station"] == "Demo FM"


def test_missing_required_member_is_error():
    with tempfile.TemporaryDirectory() as td:
        m = _valid_members()
        del m["oradio.json"]
        rep = ov.validate_oradio(_write(td, m))
        assert rep["ok"] is False
        assert any("missing required file: oradio.json" in e for e in rep["errors"])


def test_bad_format_field_is_error():
    with tempfile.TemporaryDirectory() as td:
        m = _valid_members()
        m["oradio.json"] = json.dumps({"format": "zip", "station_id": "x", "entry": {"manifest": "manifest.yaml"}})
        rep = ov.validate_oradio(_write(td, m))
        assert rep["ok"] is False
        assert any("format must be 'oradio'" in e for e in rep["errors"])


def test_invalid_json_is_error():
    with tempfile.TemporaryDirectory() as td:
        m = _valid_members()
        m["oradio.json"] = "{ not valid json "
        rep = ov.validate_oradio(_write(td, m))
        assert rep["ok"] is False
        assert any("not valid JSON" in e for e in rep["errors"])


def test_entry_manifest_pointing_to_missing_member_is_error():
    with tempfile.TemporaryDirectory() as td:
        m = _valid_members()
        m["oradio.json"] = json.dumps({
            "format": "oradio", "format_version": "1.0", "station_id": "x", "station_name": "x",
            "entry": {"manifest": "not_here.yaml"},
        })
        rep = ov.validate_oradio(_write(td, m))
        assert rep["ok"] is False
        assert any("entry.manifest points to missing member" in e for e in rep["errors"])


def test_bundled_asset_without_member_is_error():
    """The heart of validity: the lock says 'bundled' but the file isn't in the package."""
    with tempfile.TemporaryDirectory() as td:
        m = _valid_members()
        m["requirements.lock.json"] = json.dumps({
            "lock_version": "0.1",
            "voices": [{"role": "host", "bundled": True, "arcname": "assets/voices/host.onnx", "sha256": "abc"}],
            "sfx": [], "art": [], "unresolved": [],
        })
        rep = ov.validate_oradio(_write(td, m))
        assert rep["ok"] is False
        assert any("claims bundled but member" in e for e in rep["errors"])


def test_bundled_asset_with_member_is_valid():
    with tempfile.TemporaryDirectory() as td:
        m = _valid_members()
        m["assets/voices/host.onnx"] = b"\x00\x01voice"
        m["requirements.lock.json"] = json.dumps({
            "lock_version": "0.1",
            "voices": [{"role": "host", "bundled": True, "arcname": "assets/voices/host.onnx", "sha256": "abc", "bytes": 7}],
            "sfx": [], "art": [], "unresolved": [],
        })
        rep = ov.validate_oradio(_write(td, m))
        assert rep["ok"] is True
        assert rep["info"]["bundled_voices"] == 1


def test_unbundled_referenced_plugin_is_warning_not_error():
    with tempfile.TemporaryDirectory() as td:
        m = _valid_members()
        m["manifest.yaml"] = yaml.safe_dump({
            "station": {"id": "DemoFM", "name": "Demo FM"},
            "meta_plugin": "generated",
            "feeds": {"rss": {"enabled": True, "plugin": "rss"}},
        })
        rep = ov.validate_oradio(_write(td, m))
        assert rep["ok"] is True  # portability risk, not malformed
        assert any("generated" in w for w in rep["warnings"])
        assert any("rss" in w for w in rep["warnings"])


def test_polyglot_still_validates():
    with tempfile.TemporaryDirectory() as td:
        rep = ov.validate_oradio(_write(td, _valid_members(), polyglot=True))
        assert rep["ok"] is True
        assert rep["info"]["is_polyglot"] is True


def test_non_oradio_file_is_error():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "not.oradio"
        p.write_bytes(b"this is not a zip at all")
        rep = ov.validate_oradio(p)
        assert rep["ok"] is False
        assert any("not a readable .oradio" in e for e in rep["errors"])


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
