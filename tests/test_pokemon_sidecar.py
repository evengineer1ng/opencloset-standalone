from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pokemon_bridge


class _Args:
    def __init__(self, **kwargs):
        self.snapshot_source = kwargs.get("snapshot_source")
        self.snapshot_file = kwargs.get("snapshot_file")
        self.hook_command = kwargs.get("hook_command")
        self.window_title = kwargs.get("window_title")
        self.window_class = kwargs.get("window_class")


class _FakeBridge:
    def capture_window_snapshot(self, *, channel: str, output_dir: Path, focus_window: bool = False) -> dict:
        return {
            "captured_at": "2026-05-23T12:00:00.000000Z",
            "emulator": {"name": "Citra", "connected": True, "window_title": "Citra | Pokemon X"},
            "frame": {
                "path": str(output_dir / f"{channel}.png"),
                "width": 400,
                "height": 240,
                "capture_source": "window",
                "captured_at": "2026-05-23T12:00:00.000000Z",
            },
            "objective": {"title": "Observe Pokemon X/Y state"},
        }


def test_resolve_snapshot_source_prefers_hybrid_when_window_and_hook_present():
    args = _Args(hook_command="python hook.py", window_title="Citra")

    assert pokemon_bridge._resolve_snapshot_source(args) == "hybrid"


def test_collect_snapshot_merges_window_and_hook_payloads(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pokemon_bridge,
        "_run_hook_command",
        lambda command, cwd=None: {
            "trainer": {"name": "Serena", "badges": 1},
            "route": {"route": "Route 3", "map_name": "Kalos Route 3"},
            "battle": {"phase": "turn_decision", "decision_required": True},
        },
    )

    payload = pokemon_bridge._collect_snapshot(
        source="hybrid",
        snapshot_path=None,
        hook_command="python hook.py",
        hook_cwd=None,
        live_bridge=_FakeBridge(),
        channel="kalos-runtime",
        output_dir=tmp_path,
        focus_window=False,
    )

    assert payload["emulator"]["name"] == "Citra"
    assert payload["frame"]["capture_source"] == "window"
    assert payload["trainer"]["name"] == "Serena"
    assert payload["battle"]["decision_required"] is True


def test_load_keymap_merges_with_default_bindings(tmp_path):
    keymap_path = tmp_path / "keymap.json"
    keymap_path.write_text(json.dumps({"A": "K", "START": "ENTER"}), encoding="utf-8")

    payload = pokemon_bridge._load_keymap(str(keymap_path))

    assert payload["A"] == "K"
    assert payload["START"] == "ENTER"
    assert payload["UP"] == "UP"


def test_virtual_key_resolution_supports_named_and_ascii_tokens():
    assert pokemon_bridge._resolve_virtual_key("LEFT") == 0x25
    assert pokemon_bridge._resolve_virtual_key("Z") == ord("Z")


def test_infer_pokemon_identity_from_window_title_supports_oras_and_xy():
    assert pokemon_bridge._infer_pokemon_identity("Citra Nightly | Pokemon Omega Ruby") == ("pokemon_oras", "Pokemon Omega Ruby")
    assert pokemon_bridge._infer_pokemon_identity("Citra Nightly | Pokémon X") == ("pokemon_xy", "Pokemon X")
    assert pokemon_bridge._infer_pokemon_identity("Citra Nightly") == ("pokemon_citra", "Pokemon")


def test_select_action_command_ignores_old_outputs_and_returns_button_plan():
    outputs = [
        {"id": "new", "output_type": "pokemon.action_command", "payload": {"buttons": ["LEFT", "A"]}},
        {"id": "old", "output_type": "pokemon.action_command", "payload": {"buttons": ["A"]}},
    ]

    payload = pokemon_bridge._select_action_command(outputs, last_output_id="old")

    assert payload is not None
    assert payload["output_id"] == "new"
    assert payload["buttons"] == ["LEFT", "A"]


def test_select_action_command_stops_when_last_seen_output_is_reached():
    outputs = [
        {"id": "dashboard", "output_type": "dashboard.patch", "payload": {}},
        {"id": "new", "output_type": "pokemon.action_command", "payload": {"buttons": ["A"]}},
        {"id": "seen", "output_type": "pokemon.action_command", "payload": {"buttons": ["B"]}},
        {"id": "older", "output_type": "pokemon.action_command", "payload": {"buttons": ["LEFT"]}},
    ]

    payload = pokemon_bridge._select_action_command(outputs, last_output_id="seen")

    assert payload is not None
    assert payload["output_id"] == "new"

    payload = pokemon_bridge._select_action_command(outputs[2:], last_output_id="seen")

    assert payload is None


def test_is_transient_live_window_error_only_matches_window_lookup_failures():
    assert pokemon_bridge._is_transient_live_window_error(
        RuntimeError("Could not find a visible Citra window matching 'Citra'"),
        source="window",
    ) is True
    assert pokemon_bridge._is_transient_live_window_error(
        RuntimeError("Could not find a visible Citra window matching 'Citra'"),
        source="hybrid",
    ) is True
    assert pokemon_bridge._is_transient_live_window_error(
        RuntimeError("Could not find a visible Citra window matching 'Citra'"),
        source="file",
    ) is False
    assert pokemon_bridge._is_transient_live_window_error(RuntimeError("boom"), source="window") is False


def test_main_retries_transient_window_lookup_errors(monkeypatch, capsys):
    class _DummyBridge:
        def __init__(self, *args, **kwargs):
            pass

    def _raise_missing_window(**kwargs):
        raise RuntimeError("Could not find a visible Citra window matching 'Citra'")

    def _stop_after_retry(seconds):
        raise KeyboardInterrupt()

    monkeypatch.setattr(pokemon_bridge, "WindowsCitraBridge", _DummyBridge)
    monkeypatch.setattr(pokemon_bridge, "_collect_snapshot", _raise_missing_window)
    monkeypatch.setattr(pokemon_bridge.time, "sleep", _stop_after_retry)

    exit_code = pokemon_bridge.main([
        "--channel",
        "pokemon-x-live",
        "--snapshot-source",
        "window",
        "--window-title",
        "Citra",
        "--poll-seconds",
        "0.1",
    ])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"retrying": true' in captured.out.lower()
    assert "Could not find a visible Citra window matching 'Citra'" in captured.out