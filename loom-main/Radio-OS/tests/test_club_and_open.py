"""The Club + the MK1 open lifecycle.

Proves: built-in theme packs (never ask), the configure-once/ask-only-when-new memory model,
re-ask on vanish/change, and the full `open an .oradio` power-on (parse → resolve → run).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Club, open_oradio  # noqa: E402
from oradio_engine.club import DEFAULT_THEME, DEFAULT_THEME_PACKS  # noqa: E402
from oradio_engine.descriptor import OradioDescriptor  # noqa: E402


def _club(tmp_path):
    return Club(store_path=str(tmp_path / "club.json"))


def test_default_theme_packs_never_ask(tmp_path):
    club = _club(tmp_path)
    # no theme declared -> default-default (ribbon), built-in, no ask
    res = club.resolve(OradioDescriptor.from_dict({"oradio": "x", "world": {"organ": "neikos"}}))
    assert res.ready and not res.asks
    assert res.resolved["theme"]["theme"] == DEFAULT_THEME
    assert "ribbon" in DEFAULT_THEME_PACKS and "smoke" in DEFAULT_THEME_PACKS
    # a named built-in pack also never asks
    res2 = club.resolve(OradioDescriptor.from_dict(
        {"oradio": "x", "world": {"organ": "neikos"}, "theme": "smoke"}))
    assert res2.resolved["theme"]["source"] == "builtin"


def test_configure_once_then_no_more_asks(tmp_path):
    club = _club(tmp_path)
    desc = OradioDescriptor.from_dict(
        {"oradio": "x", "world": {"organ": "neikos"}, "club": ["voices", "llm"]})

    first = club.resolve(desc)
    assert {a.capability for a in first.asks} == {"voices", "llm"}
    assert all(a.reason == "new" for a in first.asks), "first sight of a dep is 'new'"
    # not required -> still ready (asks are enhancements, never block the engine)
    assert first.ready

    # answer them once
    club.remember("voices", str(tmp_path))   # an existing dir
    club.remember("llm", "http://127.0.0.1:8080")

    second = club.resolve(desc)
    assert not second.asks, "configure once, reuse forever — no re-ask"
    assert second.resolved["voices"]["status"] == "remembered"


def test_reask_only_when_vanished(tmp_path):
    club = _club(tmp_path)
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir()
    club.remember("voices", str(voices_dir))
    desc = OradioDescriptor.from_dict(
        {"oradio": "x", "world": {"organ": "neikos"}, "club": ["voices"]})
    assert not club.resolve(desc).asks  # resolved + valid

    # the remembered location genuinely vanishes -> re-ask (only now)
    voices_dir.rmdir()
    asks = club.resolve(desc).asks
    assert len(asks) == 1 and asks[0].reason == "vanished"


def test_open_lifecycle_runs(tmp_path):
    result = open_oradio(
        {"oradio": "isle", "world": {"organ": "neikos", "name": "isle", "seed": 42},
         "club": ["voices"]},
        club=_club(tmp_path),
    )
    assert result.ok
    assert result.report.resolved["theme"]["theme"] == "ribbon"
    result.engine.run(steps=5)
    assert result.engine.truth()["isle"]["tick"] == 50
    assert any(c.source == "isle" for c in result.engine.bus)


def test_open_example_file_powers_on(tmp_path):
    import pytest
    pytest.importorskip("yaml")
    example = os.path.join(os.path.dirname(__file__), "..", "examples", "motorsport-ladder.oradio")
    result = open_oradio(example, club=_club(tmp_path))
    assert result.ok and result.name == "motorsport-ladder"
    result.engine.run(steps=2)  # lens (declared in the file) tames FTB's flood
    assert len(result.engine.bus) <= 40
