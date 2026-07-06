from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request


POKEMON_CONTROL_MODES = ("auto", "assist", "pause", "step")
POKEMON_SCHEMA_VERSION = "pokemon_xy_bridge.v1"

POKEMON_EVENT_SCHEMA: dict[str, Any] = {
    "version": POKEMON_SCHEMA_VERSION,
    "profile": "pokemon_xy",
    "snapshot_sections": {
        "emulator": ["name", "connected", "window_title", "fps", "profile", "game"],
        "frame": ["path", "width", "height", "capture_source", "captured_at"],
        "trainer": ["name", "badges", "pokedex_seen", "pokedex_caught", "money"],
        "team": ["party", "active_slot", "party_status"],
        "route": ["map_name", "route", "area", "checkpoint", "story_checkpoint", "objective", "next_action"],
        "battle": ["phase", "turn_index", "decision_required", "low_confidence", "active_pokemon", "opponent", "available_actions"],
        "encounter": ["species", "level", "shiny", "rare", "catch_opportunity", "decision_required", "status_condition"],
    },
    "events": {
        "pokemon.team.snapshot": {
            "required": ["trainer_name", "party"],
            "purpose": "Durable view of current party composition and health.",
        },
        "pokemon.location.snapshot": {
            "required": ["map_name"],
            "purpose": "Current route, map, checkpoint, and overworld navigation state.",
        },
        "pokemon.frame.captured": {
            "required": ["path"],
            "purpose": "Latest captured emulator frame and window capture metadata.",
        },
        "pokemon.progress.snapshot": {
            "required": ["trainer_name", "badges"],
            "purpose": "Long-horizon progression toward gyms, Elite Four, shinies, and Pokedex goals.",
        },
        "pokemon.objective.updated": {
            "required": ["title"],
            "purpose": "Current objective and next deterministic action for the run.",
        },
        "pokemon.battle.state": {
            "required": ["phase", "decision_required"],
            "purpose": "Battle-turn decision boundary, including active Pokemon, opponent, and legal actions.",
        },
        "pokemon.encounter": {
            "required": ["species"],
            "purpose": "Wild encounter or catch opportunity, including shiny/rare flags and catch risk.",
        },
        "pokemon.pause.required": {
            "required": ["reason"],
            "purpose": "Signal that the run should stop for operator review because state is risky or uncertain.",
        },
        "pokemon.input.result": {
            "required": ["buttons", "delivered"],
            "purpose": "Latest input injection result delivered to the Citra window.",
        },
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y"}:
            return True
        if normalized in {"0", "false", "no", "off", "n"}:
            return False
    return default


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _ratio_text(hp: Any, max_hp: Any) -> str:
    hp_value = _int(hp)
    max_value = _int(max_hp)
    if hp_value is None or max_value is None or max_value <= 0:
        return ""
    return f"{hp_value}/{max_value}"


@dataclass(slots=True)
class PokemonBridgeEmission:
    event_type: str
    payload: dict[str, Any]
    text: str
    priority: int = 0
    sync: bool = False


@dataclass(slots=True)
class PokemonBridgeAdapterResult:
    emissions: list[PokemonBridgeEmission]
    bridge_status: dict[str, Any]
    state_summary: dict[str, Any]
    working_memory: dict[str, Any]
    current_plan: dict[str, Any]


class PokemonXYStateAdapter:
    schema_version = POKEMON_SCHEMA_VERSION

    def adapt_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        control_state: dict[str, Any] | None = None,
    ) -> PokemonBridgeAdapterResult:
        raw = dict(snapshot or {})
        emulator = self._adapt_emulator(raw.get("emulator") or {})
        frame = self._adapt_frame(raw)
        team = self._adapt_team(raw)
        route = self._adapt_route(raw)
        progress = self._adapt_progress(raw, team=team, route=route)
        objective = self._adapt_objective(raw, route=route, progress=progress)
        battle = self._adapt_battle(raw)
        encounter = self._adapt_encounter(raw, battle=battle)
        control = dict(control_state or {})
        emissions: list[PokemonBridgeEmission] = []

        if frame:
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.frame.captured",
                    payload=frame,
                    text="Captured a fresh emulator frame from the Pokemon bridge.",
                    priority=5,
                )
            )

        if team:
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.team.snapshot",
                    payload=team,
                    text=f"Team snapshot updated for {_text(team.get('trainer_name')) or 'trainer'}.",
                    priority=10,
                )
            )
        if route:
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.location.snapshot",
                    payload=route,
                    text=f"Location updated: {_text(route.get('route') or route.get('map_name') or route.get('area')) or 'unknown area'}.",
                    priority=10,
                )
            )
        if progress:
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.progress.snapshot",
                    payload=progress,
                    text=f"Progress snapshot updated for {_text(progress.get('trainer_name')) or 'trainer'}.",
                    priority=10,
                )
            )
        if objective:
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.objective.updated",
                    payload=objective,
                    text=f"Objective updated: {_text(objective.get('title')) or 'no title'}.",
                    priority=20,
                )
            )
        if encounter:
            encounter_priority = 70 if _bool(encounter.get("shiny")) else 40
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.encounter",
                    payload=encounter,
                    text=f"Encounter: {_text(encounter.get('species')) or 'unknown Pokemon'}.",
                    priority=encounter_priority,
                )
            )
        if battle:
            battle_priority = 75 if _bool(battle.get("decision_required")) else 30
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.battle.state",
                    payload=battle,
                    text=f"Battle update: {_text(battle.get('phase')) or 'battle'}.",
                    priority=battle_priority,
                )
            )

        if _text(control.get("mode")) == "pause":
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.pause.required",
                    payload={
                        "reason": "operator_pause",
                        "requested_mode": "pause",
                        "operator_note": _text(control.get("operator_note")),
                    },
                    text="Operator requested pause mode for the Pokemon run.",
                    priority=80,
                )
            )
        elif _bool(battle.get("low_confidence")) or _bool(encounter.get("shiny")):
            emissions.append(
                PokemonBridgeEmission(
                    event_type="pokemon.pause.required",
                    payload={
                        "reason": "risk_gate",
                        "battle_low_confidence": _bool(battle.get("low_confidence")),
                        "shiny": _bool(encounter.get("shiny")),
                        "operator_note": _text(control.get("operator_note")),
                    },
                    text="Pokemon run hit a risk gate that may require operator attention.",
                    priority=85,
                )
            )

        state_summary = {
            "trainer_name": _text(progress.get("trainer_name") or team.get("trainer_name")),
            "route": _text(route.get("route") or route.get("map_name") or route.get("area")),
            "team_size": len(team.get("party") or []),
            "has_battle": bool(battle),
            "has_encounter": bool(encounter),
            "decision_required": _bool(battle.get("decision_required")) or _bool(encounter.get("decision_required")),
        }
        bridge_status = {
            "schema_version": self.schema_version,
            "emulator": emulator,
            "frame": frame,
            "last_snapshot_at": _text(raw.get("captured_at")) or _now(),
            "connected": _bool(emulator.get("connected"), default=True),
            "last_event_count": len(emissions),
            "low_confidence": _bool(battle.get("low_confidence")),
            "recent_emissions": [emission.event_type for emission in emissions],
        }
        working_memory = {
            "pokemon": {
                "schema_version": self.schema_version,
                "emulator": emulator,
                "frame": frame,
                "team": team,
                "route": route,
                "progress": progress,
                "objective": objective,
                "battle": battle,
                "encounter": encounter,
                "control": control,
                "last_snapshot_at": bridge_status["last_snapshot_at"],
            }
        }
        current_plan = {
            "pokemon": {
                "objective": objective,
                "route": route,
                "progress": progress,
            }
        }
        return PokemonBridgeAdapterResult(
            emissions=emissions,
            bridge_status=bridge_status,
            state_summary=state_summary,
            working_memory=working_memory,
            current_plan=current_plan,
        )

    def _adapt_emulator(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": _text(payload.get("name") or "Citra"),
            "connected": _bool(payload.get("connected"), default=True),
            "window_title": _text(payload.get("window_title")),
            "fps": _int(payload.get("fps")),
            "profile": _text(payload.get("profile") or "pokemon_xy"),
            "game": _text(payload.get("game") or "Pokemon X/Y"),
        }

    def _adapt_frame(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = dict(snapshot.get("frame") or {})
        if not payload:
            return {}
        window_rect = dict(payload.get("window_rect") or {})
        return {
            "path": _text(payload.get("path")),
            "width": _int(payload.get("width")),
            "height": _int(payload.get("height")),
            "capture_source": _text(payload.get("capture_source") or "window"),
            "captured_at": _text(payload.get("captured_at") or snapshot.get("captured_at") or _now()),
            "window_rect": {
                "left": _int(window_rect.get("left")),
                "top": _int(window_rect.get("top")),
                "right": _int(window_rect.get("right")),
                "bottom": _int(window_rect.get("bottom")),
            },
        }

    def _adapt_team(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        trainer = dict(snapshot.get("trainer") or {})
        payload = dict(snapshot.get("team") or {})
        party: list[dict[str, Any]] = []
        for index, member in enumerate(payload.get("party") or [], start=1):
            row = dict(member or {})
            party.append(
                {
                    "slot": _int(row.get("slot")) or index,
                    "species": _text(row.get("species")),
                    "nickname": _text(row.get("nickname")),
                    "level": _int(row.get("level")),
                    "hp": _ratio_text(row.get("hp"), row.get("max_hp")) or _text(row.get("hp_text")),
                    "status": _text(row.get("status") or row.get("status_condition")),
                    "types": list(row.get("types") or []),
                    "ability": _text(row.get("ability")),
                    "item": _text(row.get("item")),
                    "moves": list(row.get("moves") or []),
                    "can_battle": not _bool(row.get("fainted")),
                }
            )
        return {
            "trainer_name": _text(trainer.get("name") or payload.get("trainer_name")),
            "active_slot": _int(payload.get("active_slot")) or 1,
            "party_status": _text(payload.get("party_status") or payload.get("summary")),
            "party": party,
        }

    def _adapt_route(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = dict(snapshot.get("route") or {})
        return {
            "map_name": _text(payload.get("map_name") or payload.get("area")),
            "route": _text(payload.get("route")),
            "area": _text(payload.get("area")),
            "checkpoint": _text(payload.get("checkpoint")),
            "story_checkpoint": _text(payload.get("story_checkpoint")),
            "objective": _text(payload.get("objective")),
            "next_action": _text(payload.get("next_action")),
            "movement_mode": _text(payload.get("movement_mode")),
            "x": _int(payload.get("x")),
            "y": _int(payload.get("y")),
        }

    def _adapt_progress(self, snapshot: dict[str, Any], *, team: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
        trainer = dict(snapshot.get("trainer") or {})
        objective = dict(snapshot.get("objective") or {})
        return {
            "trainer_name": _text(trainer.get("name") or team.get("trainer_name")),
            "badges": _int(trainer.get("badges")) or 0,
            "pokedex_seen": _int(trainer.get("pokedex_seen")) or 0,
            "pokedex_caught": _int(trainer.get("pokedex_caught")) or 0,
            "money": _int(trainer.get("money")),
            "goal": _text(objective.get("goal") or route.get("objective")),
            "next_milestone": _text(objective.get("next_milestone") or route.get("next_action")),
            "why": _text(objective.get("why")),
        }

    def _adapt_objective(self, snapshot: dict[str, Any], *, route: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        payload = dict(snapshot.get("objective") or {})
        title = _text(payload.get("title") or route.get("objective") or progress.get("goal"))
        if not title:
            return {}
        return {
            "title": title,
            "summary": _text(payload.get("summary") or title),
            "why": _text(payload.get("why") or progress.get("why")),
            "next_action": _text(payload.get("next_action") or route.get("next_action") or progress.get("next_milestone")),
            "watch": _text(payload.get("watch")),
            "priority": _text(payload.get("priority") or "medium"),
        }

    def _adapt_battle(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = dict(snapshot.get("battle") or {})
        if not payload:
            return {}
        return {
            "battle_id": _text(payload.get("battle_id")),
            "phase": _text(payload.get("phase") or "turn_decision"),
            "turn_index": _int(payload.get("turn_index")) or 0,
            "decision_required": _bool(payload.get("decision_required")),
            "low_confidence": _bool(payload.get("low_confidence")),
            "active_pokemon": dict(payload.get("active_pokemon") or {}),
            "opponent": dict(payload.get("opponent") or {}),
            "available_actions": list(payload.get("available_actions") or []),
            "summary": _text(payload.get("summary")),
            "watch": _text(payload.get("watch")),
        }

    def _adapt_encounter(self, snapshot: dict[str, Any], *, battle: dict[str, Any]) -> dict[str, Any]:
        payload = dict(snapshot.get("encounter") or {})
        if not payload:
            return {}
        battle_present = bool(battle)
        return {
            "species": _text(payload.get("species")),
            "level": _int(payload.get("level")),
            "shiny": _bool(payload.get("shiny")),
            "rare": _bool(payload.get("rare")),
            "catch_opportunity": _bool(payload.get("catch_opportunity")),
            "decision_required": False if battle_present else _bool(payload.get("decision_required") or payload.get("catch_opportunity") or payload.get("shiny")),
            "status_condition": _text(payload.get("status_condition")),
            "hp_ratio": _ratio_text(payload.get("hp"), payload.get("max_hp")) or _text(payload.get("hp_ratio")),
            "watch": _text(payload.get("watch")),
            "recommended_action": _text(payload.get("recommended_action")),
        }


class PokemonBridgeManager:
    def __init__(self, app: Flask) -> None:
        self.app = app
        self.adapter = PokemonXYStateAdapter()

    def get_schema(self) -> dict[str, Any]:
        return POKEMON_EVENT_SCHEMA

    def get_bridge_status(self, name: str) -> dict[str, Any]:
        channel = self._get_pokemon_channel(name)
        metadata = dict(channel.get("metadata") or {})
        runtime = dict(metadata.get("pokemon_runtime") or {})
        working_memory = dict(channel.get("working_memory") or {})
        pokemon_memory = dict(working_memory.get("pokemon") or {})
        bridge = dict(runtime.get("bridge") or {})
        control = self._normalize_control(dict(runtime.get("control") or {}))
        return {
            "channel_name": channel["name"],
            "session_id": channel["session_id"],
            "domain": channel["domain"],
            "schema_version": self.adapter.schema_version,
            "control": control,
            "bridge": {
                "schema_version": bridge.get("schema_version") or self.adapter.schema_version,
                "emulator": dict(bridge.get("emulator") or pokemon_memory.get("emulator") or {}),
                "frame": dict(bridge.get("frame") or pokemon_memory.get("frame") or {}),
                "connected": _bool(bridge.get("connected"), default=False),
                "last_snapshot_at": _text(bridge.get("last_snapshot_at") or pokemon_memory.get("last_snapshot_at")),
                "last_event_count": _int(bridge.get("last_event_count")) or 0,
                "low_confidence": _bool(bridge.get("low_confidence")),
                "recent_emissions": list(bridge.get("recent_emissions") or []),
            },
            "state_summary": {
                "trainer_name": _text(pokemon_memory.get("progress", {}).get("trainer_name") or pokemon_memory.get("team", {}).get("trainer_name")),
                "route": _text(pokemon_memory.get("route", {}).get("route") or pokemon_memory.get("route", {}).get("map_name") or pokemon_memory.get("route", {}).get("area")),
                "team_size": len(pokemon_memory.get("team", {}).get("party") or []),
                "has_battle": bool(pokemon_memory.get("battle")),
                "has_encounter": bool(pokemon_memory.get("encounter")),
            },
        }

    def patch_control(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        channel = self._get_pokemon_channel(name)
        status = self.get_bridge_status(name)
        control = dict(status["control"])
        mode = payload.get("mode")
        if mode is not None:
            control["mode"] = self._validate_mode(mode)
        if "step_budget" in payload:
            control["step_budget"] = max(0, _int(payload.get("step_budget")) or 0)
        advance_steps = _int(payload.get("advance_steps")) or 0
        if advance_steps > 0:
            control["step_budget"] = int(control.get("step_budget") or 0) + advance_steps
            if payload.get("mode") is None:
                control["mode"] = "step"
        if "operator_note" in payload:
            control["operator_note"] = _text(payload.get("operator_note"))
        control["updated_at"] = _now()

        self.app.agent_channels.update_channel(
            name,
            metadata={
                "pokemon_runtime": {
                    "control": control,
                }
            },
        )
        event = self.app.agent_channels.ingest_event(
            channel["name"],
            event_type="pokemon.control.updated",
            source="operator",
            payload=control,
            text=f"Pokemon control updated to {control['mode']} mode.",
            priority=60,
            sync=False,
        )
        refreshed = self.get_bridge_status(name)
        refreshed["event_id"] = event.get("id")
        return refreshed

    def ingest_snapshot(self, name: str, snapshot: dict[str, Any], *, source: str = "pokemon_bridge") -> dict[str, Any]:
        channel = self._get_pokemon_channel(name)
        status = self.get_bridge_status(name)
        adapted = self.adapter.adapt_snapshot(snapshot, control_state=status["control"])
        self.app.agent_channels.update_channel(
            name,
            metadata={
                "pokemon_runtime": {
                    "bridge": adapted.bridge_status,
                }
            },
            working_memory=adapted.working_memory,
            current_plan=adapted.current_plan,
        )

        stored_events: list[dict[str, Any]] = []
        for emission in adapted.emissions:
            stored_events.append(
                self.app.agent_channels.ingest_event(
                    channel["name"],
                    event_type=emission.event_type,
                    source=source,
                    payload=emission.payload,
                    text=emission.text,
                    priority=emission.priority,
                    sync=emission.sync,
                )
            )

        return {
            "channel_name": channel["name"],
            "schema_version": self.adapter.schema_version,
            "events": stored_events,
            "bridge": adapted.bridge_status,
            "state_summary": adapted.state_summary,
            "control": self.get_bridge_status(name)["control"],
        }

    def _get_pokemon_channel(self, name: str) -> dict[str, Any]:
        channel = self.app.agent_channels.get_channel(name)
        if not channel:
            raise KeyError(name)
        if channel.get("domain") != "pokemon":
            raise ValueError(f"channel {name} is not a pokemon runtime")
        return channel

    def _normalize_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = payload.get("mode") or "assist"
        return {
            "mode": self._validate_mode(mode),
            "step_budget": max(0, _int(payload.get("step_budget")) or 0),
            "operator_note": _text(payload.get("operator_note")),
            "updated_at": _text(payload.get("updated_at")) or "",
        }

    def _validate_mode(self, value: Any) -> str:
        normalized = _text(value).lower() or "assist"
        if normalized not in POKEMON_CONTROL_MODES:
            raise ValueError(f"invalid control mode: {value}")
        return normalized


def register_pokemon_bridge_routes(app: Flask) -> None:
    manager: PokemonBridgeManager = app.pokemon_bridge

    @app.route("/api/runtime/pokemon/schema", methods=["GET"])
    def get_pokemon_runtime_schema():
        return jsonify(manager.get_schema())

    @app.route("/api/runtime/agents/<name>/pokemon/bridge", methods=["GET"])
    def get_pokemon_bridge_status(name: str):
        try:
            return jsonify(manager.get_bridge_status(name))
        except KeyError:
            return jsonify({"error": "agent channel not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/runtime/agents/<name>/pokemon/control", methods=["PATCH"])
    def patch_pokemon_control(name: str):
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(manager.patch_control(name, data))
        except KeyError:
            return jsonify({"error": "agent channel not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/runtime/agents/<name>/pokemon/bridge/snapshot", methods=["POST"])
    def ingest_pokemon_bridge_snapshot(name: str):
        data = request.get_json(silent=True) or {}
        snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else data
        try:
            return jsonify(manager.ingest_snapshot(name, snapshot, source=str(data.get("source") or "pokemon_bridge"))), 202
        except KeyError:
            return jsonify({"error": "agent channel not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400