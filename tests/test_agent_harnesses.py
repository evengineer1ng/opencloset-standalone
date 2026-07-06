from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.api.agent_harnesses import PokemonHarness, TRIAGE_AGENT_TURN, TRIAGE_PATCH_ONLY
from api.agent.runner import RunExecutionResult
from api.api.app import create_app


class TestPokemonHarness:
    def test_shiny_encounter_requests_agent_turn(self):
        harness = PokemonHarness()

        directive = harness.classify_event(
            {"name": "xy-run", "domain": "pokemon"},
            event_type="pokemon.encounter",
            payload={"species": "Bunnelby", "shiny": True},
            text="A shiny appeared.",
        )

        assert directive is not None
        assert directive.action == TRIAGE_AGENT_TURN
        assert directive.requires_llm is True
        assert directive.outputs[0].output_type == "pokemon.encounter_state"

    def test_battle_snapshot_without_decision_only_patches_dashboard(self):
        harness = PokemonHarness()

        directive = harness.classify_event(
            {"name": "xy-run", "domain": "pokemon"},
            event_type="pokemon.battle.state",
            payload={"phase": "enemy_animation", "decision_required": False},
            text="Opponent is animating.",
        )

        assert directive is not None
        assert directive.action == TRIAGE_PATCH_ONLY
        assert directive.requires_llm is False
        assert directive.dashboard_patch is not None

    def test_adapts_run_result_into_pokemon_recommendation(self):
        harness = PokemonHarness()

        outputs = harness.adapt_result_outputs(
            {"active_objective": "Reach Santalune Forest"},
            event={"event_type": "pokemon.battle.state", "text": "Choose a move."},
            result=RunExecutionResult(
                run_id="run-1",
                session_id="session-1",
                status="succeeded",
                final_text="Use Vine Whip here and keep Potion usage in reserve.",
            ),
        )

        assert len(outputs) == 1
        assert outputs[0].output_type == "pokemon.recommendation"
        assert outputs[0].payload["goal"] == "Reach Santalune Forest"
        assert outputs[0].payload["next"].startswith("Use Vine Whip")

    def test_build_turn_prompt_is_vision_first_and_includes_3ds_controls(self):
        harness = PokemonHarness()

        prompt = harness.build_turn_prompt(
            {
                "name": "x-run",
                "domain": "pokemon",
                "working_memory": {"pokemon": {"emulator": {"game": "Pokemon X"}}},
            },
            event={"event_type": "pokemon.battle.state", "text": "Choose a move."},
            base_prompt="Base prompt.",
        )

        assert prompt is not None
        assert "Pokemon X" in prompt
        assert "vision targeted at the emulator window" in prompt
        assert "D-pad moves the avatar or menu cursor" in prompt
        assert "button names UP, DOWN, LEFT, RIGHT, A, B, X, Y, L, R, START, and SELECT" in prompt

    def test_adapts_structured_button_plan_into_action_command(self):
        harness = PokemonHarness()

        outputs = harness.adapt_result_outputs(
            {"active_objective": "Reach Aquacorde Town"},
            event={"event_type": "channel.tick", "text": "Review the latest frame."},
            result=RunExecutionResult(
                run_id="run-2",
                session_id="session-2",
                status="succeeded",
                final_text=(
                    "Musing: We are still in a safe overworld transition.\n"
                    "Why: A single confirm should progress dialogue without committing to risk.\n"
                    "Action buttons: A"
                ),
            ),
        )

        assert len(outputs) == 2
        assert outputs[1].output_type == "pokemon.action_command"
        assert outputs[1].payload["buttons"] == ["A"]


class TestPokemonHarnessRuntimeRoutes:
    def setup_method(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            self.db_path = handle.name
        self.app = create_app(db_path=self.db_path, start_background_workers=False)
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_runtime_lists_pokemon_harness(self):
        response = self.client.get("/api/runtime/harnesses")

        assert response.status_code == 200
        harnesses = response.get_json()["harnesses"]
        domains = {item["domain"] for item in harnesses}
        assert "general" in domains
        assert "eve" in domains
        assert "pokemon" in domains

    def test_pokemon_channel_dashboard_aggregates_patch_only_state(self):
        create_response = self.client.post(
            "/api/runtime/agents",
            json={
                "name": "kalos-run",
                "domain": "pokemon",
                "mode": "ambient",
                "active_objective": "Beat Viola and move toward Lumiose City",
            },
        )

        assert create_response.status_code == 201
        assert create_response.get_json()["domain"] == "pokemon"

        events = [
            (
                "pokemon.frame.captured",
                {
                    "path": "artifacts/images/pokemon_bridge/frame.png",
                    "width": 400,
                    "height": 240,
                    "capture_source": "window",
                    "captured_at": "2026-05-23T12:00:00.000000Z",
                },
            ),
            (
                "pokemon.team.snapshot",
                {
                    "trainer_name": "Serena",
                    "party": [
                        {"species": "Froakie", "level": 12, "hp": "31/31"},
                    ],
                },
            ),
            (
                "pokemon.location.snapshot",
                {"route": "Route 3", "map_name": "Kalos Route 3"},
            ),
            (
                "pokemon.objective.updated",
                {
                    "title": "Beat Viola",
                    "next_action": "Heal in Santalune City and enter the gym.",
                    "why": "Unlock progression toward Lumiose City.",
                },
            ),
            (
                "pokemon.decision.record",
                {
                    "summary": "Trained against optional Route 3 trainers before the gym.",
                },
            ),
        ]

        for event_type, payload in events:
            response = self.client.post(
                "/api/runtime/agents/kalos-run/events",
                json={"type": event_type, "payload": payload, "text": event_type},
            )
            assert response.status_code == 201

        dashboard_response = self.client.get("/api/runtime/agents/kalos-run/dashboard")

        assert dashboard_response.status_code == 200
        body = dashboard_response.get_json()
        dashboard = body["dashboard"]

        assert dashboard["domain"] == "pokemon"
        assert dashboard["run"]["trainer_name"] == "Serena"
        assert dashboard["run"]["current_route"] == "Route 3"
        assert dashboard["four_questions"]["why"] == "Unlock progression toward Lumiose City."
        assert dashboard["panels"]["objective"]["title"] == "Beat Viola"
        assert dashboard["panels"]["decision_timeline"][0]["summary"].startswith("Trained against optional")
        assert dashboard["panels"]["frame_capture"]["path"].endswith("frame.png")
        assert dashboard["panels"]["current_run"]["capture_source"] == "window"
        assert dashboard["panels"]["session_companion"]["session_mood"] == "ambient"