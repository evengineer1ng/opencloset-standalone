from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.api.app import create_app
from api.api.streaming import EventQueueStore
from api.api.pokemon_bridge import POKEMON_SCHEMA_VERSION, PokemonXYStateAdapter


class TestPokemonXYStateAdapter:
    def test_adapter_emits_first_concrete_xy_state_events(self):
        adapter = PokemonXYStateAdapter()

        result = adapter.adapt_snapshot(
            {
                "emulator": {"connected": True, "window_title": "Citra | Pokemon X", "fps": 60},
                "frame": {"path": "artifacts/images/pokemon_bridge/frame.png", "width": 400, "height": 240, "capture_source": "window"},
                "trainer": {"name": "Serena", "badges": 1, "pokedex_seen": 24, "pokedex_caught": 11, "money": 4200},
                "team": {
                    "active_slot": 1,
                    "party": [
                        {"species": "Froakie", "level": 13, "hp": 31, "max_hp": 31, "moves": ["Water Pulse", "Quick Attack"]},
                        {"species": "Fletchling", "level": 11, "hp": 25, "max_hp": 29, "status": "burn"},
                    ],
                },
                "route": {"map_name": "Kalos Route 3", "route": "Route 3", "objective": "Beat Viola", "next_action": "Heal and enter the gym."},
                "objective": {"title": "Beat Viola", "why": "Unlock Lumiose City", "watch": "Potion count"},
                "battle": {
                    "phase": "turn_decision",
                    "turn_index": 3,
                    "decision_required": True,
                    "active_pokemon": {"species": "Froakie", "hp": "31/31"},
                    "opponent": {"species": "Surskit", "level": 10},
                    "available_actions": ["Water Pulse", "Quick Attack", "Potion"],
                },
                "encounter": {"species": "Surskit", "level": 10, "catch_opportunity": False},
            },
            control_state={"mode": "assist"},
        )

        event_types = [emission.event_type for emission in result.emissions]

        assert result.bridge_status["schema_version"] == POKEMON_SCHEMA_VERSION
        assert result.bridge_status["frame"]["path"].endswith("frame.png")
        assert result.state_summary["trainer_name"] == "Serena"
        assert result.working_memory["pokemon"]["route"]["route"] == "Route 3"
        assert result.working_memory["pokemon"]["frame"]["capture_source"] == "window"
        assert "pokemon.frame.captured" in event_types
        assert "pokemon.team.snapshot" in event_types
        assert "pokemon.location.snapshot" in event_types
        assert "pokemon.progress.snapshot" in event_types
        assert "pokemon.objective.updated" in event_types
        assert "pokemon.battle.state" in event_types
        assert "pokemon.encounter" in event_types


class TestPokemonBridgeApi:
    def setup_method(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            self.db_path = handle.name
        self.app = create_app(db_path=self.db_path, start_background_workers=False)
        self.workspace_root = Path(self.app.config["WORKSPACE_ROOT"])
        self.frame_dir = self.workspace_root / "artifacts" / "images" / "pokemon_bridge" / "tests"
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        self.frame_path = self.frame_dir / "live-frame.png"
        self.frame_path.write_bytes(b"png-test")
        self.client = self.app.test_client()
        create_response = self.client.post(
            "/api/runtime/agents",
            json={
                "name": "kalos-runtime",
                "domain": "pokemon",
                "mode": "ambient",
                "active_objective": "Beat Viola and move toward Lumiose City",
            },
        )
        assert create_response.status_code == 201

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if self.frame_path.exists():
            self.frame_path.unlink()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_exposes_pokemon_schema_and_session_filter(self):
        channel = self.client.get("/api/runtime/agents/kalos-runtime").get_json()

        schema_response = self.client.get("/api/runtime/pokemon/schema")
        filtered_response = self.client.get(f"/api/runtime/agents?session_id={channel['session_id']}&domain=pokemon")

        assert schema_response.status_code == 200
        assert schema_response.get_json()["version"] == POKEMON_SCHEMA_VERSION
        filtered_channels = filtered_response.get_json()["channels"]
        assert len(filtered_channels) == 1
        assert filtered_channels[0]["name"] == "kalos-runtime"

    def test_control_route_updates_mode_and_step_budget(self):
        response = self.client.patch(
            "/api/runtime/agents/kalos-runtime/pokemon/control",
            json={"mode": "step", "advance_steps": 1, "operator_note": "Advance one battle decision at a time."},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["control"]["mode"] == "step"
        assert payload["control"]["step_budget"] == 1
        assert payload["control"]["operator_note"] == "Advance one battle decision at a time."

        events = self.client.get("/api/runtime/agents/kalos-runtime/events?limit=10").get_json()["events"]
        assert any(event["event_type"] == "pokemon.control.updated" for event in events)

        reset_response = self.client.patch(
            "/api/runtime/agents/kalos-runtime/pokemon/control",
            json={"step_budget": 0, "mode": "assist"},
        )

        assert reset_response.status_code == 200
        assert reset_response.get_json()["control"]["step_budget"] == 0
        assert reset_response.get_json()["control"]["mode"] == "assist"

    def test_snapshot_route_updates_bridge_status_and_working_memory(self):
        response = self.client.post(
            "/api/runtime/agents/kalos-runtime/pokemon/bridge/snapshot",
            json={
                "emulator": {"name": "Citra", "connected": True, "window_title": "Citra | Pokemon X", "fps": 60},
                "frame": {"path": str(self.frame_path.relative_to(self.workspace_root)).replace("\\", "/"), "width": 400, "height": 240, "capture_source": "window"},
                "trainer": {"name": "Serena", "badges": 1, "pokedex_seen": 24, "pokedex_caught": 11},
                "team": {
                    "active_slot": 1,
                    "party": [
                        {"species": "Froakie", "level": 13, "hp": 31, "max_hp": 31},
                    ],
                },
                "route": {"map_name": "Kalos Route 3", "route": "Route 3", "objective": "Beat Viola", "next_action": "Heal and enter the gym."},
                "objective": {"title": "Beat Viola", "why": "Unlock Lumiose City", "next_action": "Heal and enter the gym."},
                "battle": {
                    "phase": "enemy_animation",
                    "turn_index": 3,
                    "decision_required": False,
                    "low_confidence": False,
                    "active_pokemon": {"species": "Froakie"},
                    "opponent": {"species": "Surskit"},
                },
            },
        )

        assert response.status_code == 202
        payload = response.get_json()
        assert payload["bridge"]["connected"] is True
        assert payload["state_summary"]["route"] == "Route 3"
        assert any(event["event_type"] == "pokemon.team.snapshot" for event in payload["events"])

        bridge_status = self.client.get("/api/runtime/agents/kalos-runtime/pokemon/bridge")
        assert bridge_status.status_code == 200
        bridge_payload = bridge_status.get_json()
        assert bridge_payload["state_summary"]["trainer_name"] == "Serena"
        assert bridge_payload["bridge"]["emulator"]["name"] == "Citra"
        assert bridge_payload["bridge"]["frame"]["path"].endswith("live-frame.png")

        frame_response = self.client.get("/api/runtime/agents/kalos-runtime/frame")
        assert frame_response.status_code == 200
        assert frame_response.mimetype == "image/png"

        channel = self.client.get("/api/runtime/agents/kalos-runtime").get_json()
        assert channel["working_memory"]["pokemon"]["battle"]["phase"] == "enemy_animation"
        assert channel["working_memory"]["pokemon"]["objective"]["title"] == "Beat Viola"
        assert channel["working_memory"]["pokemon"]["frame"]["path"].endswith("live-frame.png")

    def test_input_result_event_and_snapshot_survive_saturated_runtime_queue(self):
        channel = self.client.get("/api/runtime/agents/kalos-runtime").get_json()
        saturated_store = EventQueueStore(maxsize=1)
        saturated_store.enqueue(channel["id"], {"type": "stale", "data": {"value": 1}})
        self.app.runtime_event_store = saturated_store
        self.app.agent_channels.event_store = saturated_store

        event_response = self.client.post(
            "/api/runtime/agents/kalos-runtime/events",
            json={
                "type": "pokemon.input.result",
                "payload": {
                    "buttons": ["LEFT"],
                    "delivered": True,
                    "delivered_buttons": [{"button": "LEFT", "mapped_to": "LEFT", "vk": 37}],
                    "window_title": "Citra | Pokemon X",
                    "captured_at": "2026-05-24T02:20:00.000000Z",
                },
                "text": "Injected inputs: LEFT.",
            },
        )

        assert event_response.status_code == 201

        snapshot_response = self.client.post(
            "/api/runtime/agents/kalos-runtime/pokemon/bridge/snapshot",
            json={
                "emulator": {"name": "Citra", "connected": True, "window_title": "Citra | Pokemon X", "fps": 60},
                "frame": {"path": str(self.frame_path.relative_to(self.workspace_root)).replace("\\", "/"), "width": 400, "height": 240, "capture_source": "window"},
                "trainer": {"name": "Serena", "badges": 1, "pokedex_seen": 24, "pokedex_caught": 11},
            },
        )

        assert snapshot_response.status_code == 202