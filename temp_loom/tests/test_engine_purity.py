"""Guard: the .oradio DECODER stays importable on stdlib + PyYAML alone.

This is a FORMAT-INTEGRITY test, not a style test. A heavy dependency in the engine
core forks the file format: you can version Loom-the-software forever, but you can
never reissue everyone's already-shipped .oradio files. If `import oradio_engine`
starts requiring an image/GUI/audio/ML library, a file that opens on one machine
fails on another, and the format dies. This test fails loudly the moment that
creeps back in (it caught PIL leaking in via visual_thumbnail once already).

Rendering, audio, ML, GUI are ENDPOINT jobs. Import those submodules explicitly
(`from oradio_engine.visual_thumbnail import render_visual_frame`) when you mean to
draw pixels — never from the decoder's import path.
"""
import sys

import pytest

# Anything heavier than stdlib + PyYAML must NOT load when you `import oradio_engine`.
FORBIDDEN_IN_CORE = (
    "PIL", "numpy", "cv2", "tkinter", "sounddevice", "soundfile", "requests", "pydantic",
)


def test_engine_imports_with_nothing_but_stdlib_and_yaml(monkeypatch):
    # Poison the heavy deps: a None entry in sys.modules makes `import X` raise.
    for dep in FORBIDDEN_IN_CORE:
        monkeypatch.setitem(sys.modules, dep, None)
    # Drop cached engine modules so __init__ re-runs under the poison.
    for name in [m for m in sys.modules if m == "oradio_engine" or m.startswith("oradio_engine.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)

    import oradio_engine  # must succeed on stdlib + PyYAML alone

    # the decoder surface is intact
    assert oradio_engine.load_oradio_file is not None
    assert oradio_engine.FederationEngine is not None
    assert oradio_engine.DipoleMeter is not None
