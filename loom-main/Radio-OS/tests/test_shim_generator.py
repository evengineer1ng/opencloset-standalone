import ast
import importlib.util
from pathlib import Path

import pytest

import shim_generator as sg

# antenna_bridge (the OTHER end of the pipe) loaded in isolation for the contract round-trip.
_spec = importlib.util.spec_from_file_location(
    "antenna_bridge", Path(__file__).resolve().parent.parent / "plugins" / "antenna_bridge.py"
)
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


def _scope_for(transport: str) -> str:
    return {"udp_listen": "8125", "command_output": "echo hi"}.get(transport, "C:/some/path")


def test_all_transports_generate_valid_lightweight_python():
    for transport in sg.TRANSPORTS:
        text = sg.generate_shim(transport, "mysrc", _scope_for(transport))
        ast.parse(text)  # must be valid Python
        assert "RadioOSBridge" in text       # writes to the bridge
        assert "events.jsonl" in text         # stream contract
        assert "'mysrc'" in text              # scoped to this source
        assert "def emit(" in text            # one-file, self-contained
        assert "import json, os, time" in text  # stdlib only (no heavy deps)


def test_windows_path_scope_generates_valid_python():
    # The common real case: a backslash Windows path as the scope must not break the shim.
    text = sg.generate_shim("file_watch", "proj", r"C:\Users\me\MyProject")
    ast.parse(text)
    assert r"C:\Users\me\MyProject".replace("\\", "/") in text  # docstring sanitized to forward slashes


def test_generated_shim_writes_what_antenna_bridge_reads(tmp_path):
    # End-to-end contract symmetry: a generated shim's emit() output is consumed by antenna_bridge.
    text = sg.generate_shim(
        "file_watch", "codeproj", str(tmp_path / "watch"), bridge_dir=str(tmp_path / "bridge")
    )
    ns = {"__name__": "scout_under_test"}  # so the `if __name__ == "__main__"` guard stays False
    exec(compile(text, "scout.py", "exec"), ns)

    ns["emit"]({"type": "file_changed", "title": "main.py", "priority": 55})

    events = tmp_path / "bridge" / "events.jsonl"
    assert events.is_file()
    line = events.read_text(encoding="utf-8").strip()

    cand = ab.parse_observation_line(line, "codeproj", 60)
    assert cand["type"] == "file_changed"
    assert cand["title"] == "main.py"
    assert cand["priority"] == 55  # heat hint survives the round trip end-to-end


def test_command_output_shim_embeds_command_and_stays_observational(tmp_path):
    text = sg.generate_shim("command_output", "agent", 'codex exec "run tests"')
    assert "'codex exec \"run tests\"'" in text or 'codex exec' in text
    # observe, never impersonate: it reports harness_output, it does not claim to be the harness
    assert "harness_output" in text
    assert "harness_started" in text and "harness_finished" in text


def test_udp_shim_passes_through_device_observations(tmp_path):
    text = sg.generate_shim("udp_listen", "fridge", "8125")
    assert "socket" in text
    assert "8125" in text
    assert "emit(obj)" in text  # an ESP32 already speaking the schema is passed straight through


def test_unknown_transport_raises():
    with pytest.raises(ValueError):
        sg.generate_shim("telepathy", "s", "x")


def test_list_transports_is_the_safe_set():
    names = {t["name"] for t in sg.list_transports()}
    assert names == {"file_watch", "log_tail", "command_output", "udp_listen"}
