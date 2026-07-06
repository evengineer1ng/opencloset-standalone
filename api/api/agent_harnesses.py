from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from api.agent.runner import RunExecutionResult


TRIAGE_RECORD_ONLY = "record_only"
TRIAGE_PATCH_ONLY = "patch_only"
TRIAGE_AGENT_TURN = "agent_turn"


@dataclass(slots=True)
class ChannelOutput:
    output_type: str
    payload: dict[str, Any]


@dataclass(slots=True)
class EventDirective:
    action: str | None = None
    requires_llm: bool | None = None
    dashboard_patch: dict[str, Any] | None = None
    outputs: list[ChannelOutput] = field(default_factory=list)
    summary: str = ""


@dataclass(slots=True)
class TickEvent:
    event_type: str
    source: str
    payload: dict[str, Any]
    text: str = ""
    priority: int = 0
    sync: bool = True


class AgentDomainHarness(Protocol):
    domain: str
    name: str

    def classify_event(
        self,
        channel: dict[str, Any],
        *,
        event_type: str,
        payload: dict[str, Any],
        text: str,
    ) -> EventDirective | None:
        ...

    def build_turn_prompt(
        self,
        channel: dict[str, Any],
        *,
        event: dict[str, Any],
        base_prompt: str,
    ) -> str | None:
        ...

    def adapt_result_outputs(
        self,
        channel: dict[str, Any],
        *,
        event: dict[str, Any],
        result: RunExecutionResult,
    ) -> list[ChannelOutput]:
        ...

    def build_tick_event(self, channel: dict[str, Any], *, job: dict[str, Any]) -> TickEvent | None:
        ...

    def build_dashboard(
        self,
        channel: dict[str, Any],
        *,
        events: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


def _latest_output(outputs: list[dict[str, Any]], output_type: str) -> dict[str, Any] | None:
    for output in outputs:
        if output.get("output_type") == output_type:
            return dict(output.get("payload") or {})
    return None


def _outputs_with_prefix(outputs: list[dict[str, Any]], prefix: str, *, limit: int = 5) -> list[dict[str, Any]]:
    items = [dict(output.get("payload") or {}) for output in outputs if str(output.get("output_type") or "").startswith(prefix)]
    return items[:limit]


def _pokemon_game_name(channel: dict[str, Any]) -> str:
    pokemon_memory = dict(channel.get("working_memory") or {}).get("pokemon") or {}
    runtime_metadata = dict(channel.get("metadata") or {}).get("pokemon_runtime") or {}
    bridge_state = dict(runtime_metadata.get("bridge") or {})
    emulator = dict(bridge_state.get("emulator") or pokemon_memory.get("emulator") or {})
    return str(emulator.get("game") or "Pokemon X").strip() or "Pokemon X"


def _pokemon_control_guide() -> str:
    return (
        "3DS control model: D-pad moves the avatar or menu cursor; A confirms, interacts, and talks; "
        "B cancels, backs out, or advances safely through low-risk dialogue; X opens the main menu or bag context; "
        "Y triggers the secondary context action or registered item; L and R change tabs or cycle menu pages; "
        "START opens the pause/menu layer; SELECT is the secondary utility button. "
        "When proposing actions, use the button names UP, DOWN, LEFT, RIGHT, A, B, X, Y, L, R, START, and SELECT."
    )


POKEMON_BUTTON_TOKENS = ("UP", "DOWN", "LEFT", "RIGHT", "A", "B", "X", "Y", "L", "R", "START", "SELECT")


def _extract_pokemon_action_buttons(text: str) -> list[str]:
    if not text:
        return []

    candidate = ""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    for prefix in ("Action buttons:", "Buttons:", "Next buttons:", "Action:"):
        for line in reversed(lines):
            if line.lower().startswith(prefix.lower()):
                candidate = line.split(":", 1)[1].strip()
                break
        if candidate:
            break

    if not candidate:
        return []

    normalized = (
        candidate.upper()
        .replace("->", " ")
        .replace(",", " ")
        .replace(";", " ")
        .replace("|", " ")
        .replace("/", " ")
        .replace("+", " ")
    )
    if normalized in {"NONE", "WAIT", "PAUSE", "HOLD"}:
        return []

    buttons: list[str] = []
    for token in normalized.split():
        cleaned = token.strip("[](){}<>")
        if cleaned in POKEMON_BUTTON_TOKENS:
            buttons.append(cleaned)
    return buttons[:4]


class BaseAgentDomainHarness:
    domain = "general"
    name = "General Runtime Harness"

    def classify_event(
        self,
        channel: dict[str, Any],
        *,
        event_type: str,
        payload: dict[str, Any],
        text: str,
    ) -> EventDirective | None:
        if event_type in {"user.message", "user.voice", "channel.tick"}:
            return EventDirective(action=TRIAGE_AGENT_TURN, requires_llm=True, summary=text)

        if event_type == "danger.alert":
            return EventDirective(
                action=TRIAGE_AGENT_TURN,
                requires_llm=True,
                summary=text,
                dashboard_patch={
                    "target": "risk_banner",
                    "op": "replace",
                    "content": {
                        "severity": payload.get("severity", "warning"),
                        "title": payload.get("title", "Danger alert"),
                        "body": payload.get("body") or text or "Operational danger threshold crossed.",
                    },
                },
            )
        return None

    def build_turn_prompt(
        self,
        channel: dict[str, Any],
        *,
        event: dict[str, Any],
        base_prompt: str,
    ) -> str | None:
        return None

    def adapt_result_outputs(
        self,
        channel: dict[str, Any],
        *,
        event: dict[str, Any],
        result: RunExecutionResult,
    ) -> list[ChannelOutput]:
        return []

    def build_tick_event(self, channel: dict[str, Any], *, job: dict[str, Any]) -> TickEvent | None:
        payload = dict(job.get("metadata") or {})
        payload.setdefault("channel_name", channel.get("name"))
        payload.setdefault("domain", channel.get("domain"))
        payload.setdefault("objective", channel.get("active_objective"))
        payload.setdefault("tick_reason", "scheduled_review")
        return TickEvent(
            event_type="channel.tick",
            source="scheduler",
            payload=payload,
            text=f"Scheduled ambient review for channel {channel.get('name', 'unknown')}.",
            priority=int(payload.get("priority") or 25),
            sync=True,
        )

    def build_dashboard(
        self,
        channel: dict[str, Any],
        *,
        events: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest_message = _latest_output(outputs, "message") or {}
        latest_decision = _latest_output(outputs, "decision") or {}
        return {
            "mode": channel.get("mode"),
            "domain": channel.get("domain"),
            "current_state": {
                "objective": channel.get("active_objective") or "",
                "last_agent_decision": channel.get("last_agent_decision") or latest_message.get("text") or "",
                "next_action": latest_decision.get("summary") or latest_message.get("text") or "",
            },
            "alerts": [dict(output.get("payload") or {}) for output in outputs if output.get("output_type") == "alert"][:5],
            "timeline": events[:10],
        }


class EveOpsHarness(BaseAgentDomainHarness):
    domain = "eve"
    name = "EVEOps Harness"

    def classify_event(
        self,
        channel: dict[str, Any],
        *,
        event_type: str,
        payload: dict[str, Any],
        text: str,
    ) -> EventDirective | None:
        directive = super().classify_event(channel, event_type=event_type, payload=payload, text=text)
        if directive is not None:
            return directive

        if event_type == "current_run.updated":
            patch = {
                "target": "current_run_panel",
                "op": "replace",
                "content": payload,
            }
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                dashboard_patch=patch,
                outputs=[ChannelOutput("eveops.current_run", payload)],
                summary=text,
            )

        if event_type == "contract.scored":
            patch = {
                "target": "contract_panel",
                "op": "replace",
                "content": payload,
            }
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                dashboard_patch=patch,
                outputs=[ChannelOutput("eveops.contract_eval", payload)],
                summary=text,
            )

        if event_type == "asset.snapshot":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                dashboard_patch={
                    "target": "asset_panel",
                    "op": "replace",
                    "content": payload,
                },
                outputs=[ChannelOutput("eveops.asset_snapshot", payload)],
                summary=text,
            )

        if event_type == "character.profile":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                dashboard_patch={
                    "target": "pilot_identity_panel",
                    "op": "replace",
                    "content": payload,
                },
                outputs=[ChannelOutput("eveops.character_profile", payload)],
                summary=text,
            )

        if event_type == "character.location":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                dashboard_patch={
                    "target": "current_location_panel",
                    "op": "replace",
                    "content": payload,
                },
                outputs=[ChannelOutput("eveops.character_location", payload)],
                summary=text,
            )

        if event_type == "wallet.snapshot":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("eveops.wallet_snapshot", payload)],
                summary=text,
            )

        if event_type == "session.plan":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("eveops.session_plan", payload)],
                summary=text,
            )

        if event_type == "decision.record":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("eveops.decision_record", payload)],
                summary=text,
            )

        if event_type == "knowledge.gap":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("eveops.knowledge_gap", payload)],
                summary=text,
            )

        if event_type == "doctrine.updated":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("eveops.doctrine", payload)],
                summary=text,
            )

        if event_type == "training.goal":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("eveops.training_goal", payload)],
                summary=text,
            )

        if event_type == "market.opportunity":
            profit = float(payload.get("estimated_profit") or 0)
            patch = {
                "target": "opportunity_card",
                "op": "replace",
                "content": payload,
            }
            outputs = [ChannelOutput("eveops.market_snapshot", payload)]
            if profit >= 10_000_000:
                return EventDirective(
                    action=TRIAGE_AGENT_TURN,
                    requires_llm=True,
                    dashboard_patch=patch,
                    outputs=outputs,
                    summary=text,
                )
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                dashboard_patch=patch,
                outputs=outputs,
                summary=text,
            )

        if event_type == "route.changed":
            danger_score = int(payload.get("danger_score") or 0)
            patch = {
                "target": "route_panel",
                "op": "replace",
                "content": payload,
            }
            outputs = [ChannelOutput("eveops.route_snapshot", payload)]
            if danger_score >= 50:
                return EventDirective(
                    action=TRIAGE_AGENT_TURN,
                    requires_llm=True,
                    dashboard_patch=patch,
                    outputs=outputs,
                    summary=text,
                )
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                dashboard_patch=patch,
                outputs=outputs,
                summary=text,
            )

        return None

    def build_turn_prompt(
        self,
        channel: dict[str, Any],
        *,
        event: dict[str, Any],
        base_prompt: str,
    ) -> str | None:
        return (
            f"{base_prompt}\n\n"
            "You are a calm dispatcher, logistics analyst, quartermaster, risk officer, and session companion for an EVE hauler. "
            "Always answer four questions: what are we doing, why are we doing it, what should we watch out for, and what should we do next. "
            "Prefer boring profitable hauling over spicy greed, avoid autopilot for valuable cargo, and optimize for continuity, confidence, and operational clarity."
        )

    def adapt_result_outputs(
        self,
        channel: dict[str, Any],
        *,
        event: dict[str, Any],
        result: RunExecutionResult,
    ) -> list[ChannelOutput]:
        final_text = result.final_text or result.text or ""
        if not final_text:
            return []
        return [
            ChannelOutput(
                "eveops.recommendation",
                {
                    "summary": final_text[:280],
                    "event_type": event.get("event_type"),
                    "run_id": result.run_id,
                    "what": event.get("text") or event.get("event_type") or "",
                    "why": "Operational recommendation generated from live hauling context.",
                    "watch": "Route risk, collateral exposure, and chokepoint changes.",
                    "next": final_text[:280],
                },
            )
        ]

    def build_tick_event(self, channel: dict[str, Any], *, job: dict[str, Any]) -> TickEvent | None:
        payload = dict(job.get("metadata") or {})
        payload.setdefault("kind", "ambient_review")
        payload.setdefault("focus", ["market", "routes", "notifications"])
        return TickEvent(
            event_type="channel.tick",
            source="scheduler",
            payload=payload,
            text=f"Scheduled EVEOps hauling review for {channel.get('name', 'unknown')}.",
            priority=int(payload.get("priority") or 40),
            sync=True,
        )

    def build_dashboard(
        self,
        channel: dict[str, Any],
        *,
        events: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        profile = _latest_output(outputs, "eveops.character_profile") or {}
        location = _latest_output(outputs, "eveops.character_location") or {}
        current_run = _latest_output(outputs, "eveops.current_run") or {}
        route_risk = _latest_output(outputs, "eveops.route_snapshot") or {}
        contract_eval = _latest_output(outputs, "eveops.contract_eval") or {}
        market_opportunity = _latest_output(outputs, "eveops.market_snapshot") or {}
        assets = _latest_output(outputs, "eveops.asset_snapshot") or {}
        wallet = _latest_output(outputs, "eveops.wallet_snapshot") or {}
        session_plan = _latest_output(outputs, "eveops.session_plan") or {}
        doctrine = _latest_output(outputs, "eveops.doctrine") or {}
        recommendation = _latest_output(outputs, "eveops.recommendation") or {}
        training_goal = _latest_output(outputs, "eveops.training_goal") or {}
        decision_log = _outputs_with_prefix(outputs, "eveops.decision_record", limit=10)
        knowledge_gaps = _outputs_with_prefix(outputs, "eveops.knowledge_gap", limit=10)

        pilot_identity = {
            "character_name": profile.get("character_name") or profile.get("pilot_name") or "",
            "corporation_name": profile.get("corporation_name") or profile.get("corporation") or "",
            "alliance_name": profile.get("alliance_name") or "",
            "hauling_role": profile.get("hauling_role") or profile.get("role") or "owner-operator",
            "home_hub": profile.get("home_hub") or "",
            "preferred_hull": profile.get("preferred_hull") or profile.get("ship_type") or "",
            "risk_posture": profile.get("risk_posture") or doctrine.get("playstyle") or "",
        }

        current_location = {
            "current_system": location.get("current_system") or location.get("system") or "",
            "current_station": location.get("current_station") or location.get("station") or location.get("structure") or "",
            "region_name": location.get("region_name") or location.get("region") or "",
            "ship_name": location.get("ship_name") or "",
            "ship_type": location.get("ship_type") or pilot_identity.get("preferred_hull") or "",
            "flight_state": location.get("flight_state") or ("docked" if location.get("current_station") or location.get("station") else ""),
            "destination": location.get("destination") or "",
            "security_band": location.get("security_band") or "",
            "situation": location.get("situation") or "",
            "last_seen_at": location.get("last_seen_at") or "",
        }

        pilot_name = pilot_identity.get("character_name") or channel.get("name") or "Unbound pilot"
        ship_label = current_location.get("ship_name") or current_location.get("ship_type") or pilot_identity.get("preferred_hull") or "No ship recorded"
        location_bits = [bit for bit in [current_location.get("current_system"), current_location.get("current_station")] if bit]
        location_label = " - ".join(location_bits) if location_bits else "Location not pinned"

        questions = {
            "what": current_run.get("objective") or recommendation.get("what") or channel.get("active_objective") or "No active hauling objective recorded.",
            "why": session_plan.get("why") or recommendation.get("why") or "Maintain operational clarity and risk discipline.",
            "watch": session_plan.get("watch") or route_risk.get("watch") or route_risk.get("warning") or recommendation.get("watch") or "Monitor chokepoints, collateral, and kill spikes.",
            "next": current_run.get("next_action") or session_plan.get("next_actions", [""])[0] or recommendation.get("next") or channel.get("last_agent_decision") or "Re-evaluate and choose the next low-risk action.",
        }

        companion = {
            "current_plan": session_plan.get("current_plan") or current_run.get("objective") or channel.get("active_objective") or "",
            "why_we_chose_it": session_plan.get("why") or doctrine.get("playstyle") or "",
            "what_were_watching": session_plan.get("watch") or route_risk.get("watch") or questions["watch"],
            "abort_conditions": session_plan.get("abort_conditions") or [],
            "next_actions": session_plan.get("next_actions") or ([current_run.get("next_action")] if current_run.get("next_action") else []),
            "session_mood": session_plan.get("session_mood") or doctrine.get("session_mood") or "calm",
        }

        return {
            "domain": channel.get("domain"),
            "mode": channel.get("mode"),
            "fantasy": "dispatcher-analyst-quartermaster-risk-officer-wingman",
            "pilot": {
                "name": pilot_name,
                "ship_label": ship_label,
                "location_label": location_label,
                "corp_label": pilot_identity.get("corporation_name") or pilot_identity.get("alliance_name") or "Independent",
            },
            "four_questions": questions,
            "panels": {
                "pilot_identity": pilot_identity,
                "current_location": current_location,
                "current_run": current_run,
                "route_risk": route_risk,
                "contract_job": contract_eval,
                "market_opportunity": market_opportunity,
                "asset_inventory": assets,
                "wallet_profit": wallet,
                "session_companion": companion,
                "decision_timeline": decision_log,
                "knowledge_gaps": knowledge_gaps,
                "training_plan": training_goal,
                "personal_doctrine": doctrine,
            },
            "alerts": [dict(output.get("payload") or {}) for output in outputs if output.get("output_type") == "alert"][:5],
            "recent_events": events[:15],
        }


class PokemonHarness(BaseAgentDomainHarness):
    domain = "pokemon"
    name = "Pokemon Adventure Harness"

    def classify_event(
        self,
        channel: dict[str, Any],
        *,
        event_type: str,
        payload: dict[str, Any],
        text: str,
    ) -> EventDirective | None:
        directive = super().classify_event(channel, event_type=event_type, payload=payload, text=text)
        if directive is not None:
            return directive

        snapshot_events = {
            "pokemon.team.snapshot": ("team_panel", "pokemon.team_state"),
            "pokemon.location.snapshot": ("location_panel", "pokemon.location_state"),
            "pokemon.frame.captured": ("frame_panel", "pokemon.frame_state"),
            "pokemon.inventory.snapshot": ("inventory_panel", "pokemon.inventory_state"),
            "pokemon.progress.snapshot": ("progress_panel", "pokemon.progress_state"),
            "pokemon.objective.updated": ("objective_panel", "pokemon.objective"),
            "pokemon.catch.result": ("catch_panel", "pokemon.catch_result"),
        }
        if event_type in snapshot_events:
            target, output_type = snapshot_events[event_type]
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                dashboard_patch={
                    "target": target,
                    "op": "replace",
                    "content": payload,
                },
                outputs=[ChannelOutput(output_type, payload)],
                summary=text,
            )

        if event_type == "pokemon.decision.record":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("pokemon.decision_record", payload)],
                summary=text,
            )

        if event_type == "pokemon.knowledge.gap":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("pokemon.knowledge_gap", payload)],
                summary=text,
            )

        if event_type == "pokemon.input.result":
            return EventDirective(
                action=TRIAGE_PATCH_ONLY,
                requires_llm=False,
                outputs=[ChannelOutput("pokemon.input_result", payload)],
                summary=text,
            )

        if event_type == "pokemon.battle.state":
            needs_turn = bool(
                payload.get("decision_required")
                or payload.get("action_required")
                or payload.get("low_confidence")
                or str(payload.get("phase") or "").strip().lower() in {"turn_decision", "move_replace", "faint_switch"}
            )
            return EventDirective(
                action=TRIAGE_AGENT_TURN if needs_turn else TRIAGE_PATCH_ONLY,
                requires_llm=needs_turn,
                dashboard_patch={
                    "target": "battle_panel",
                    "op": "replace",
                    "content": payload,
                },
                outputs=[ChannelOutput("pokemon.battle_state", payload)],
                summary=text,
            )

        if event_type == "pokemon.encounter":
            requires_llm = bool(
                payload.get("decision_required")
                or payload.get("shiny")
                or payload.get("catch_opportunity")
                or payload.get("rare")
            )
            return EventDirective(
                action=TRIAGE_AGENT_TURN if requires_llm else TRIAGE_PATCH_ONLY,
                requires_llm=requires_llm,
                dashboard_patch={
                    "target": "encounter_panel",
                    "op": "replace",
                    "content": payload,
                },
                outputs=[ChannelOutput("pokemon.encounter_state", payload)],
                summary=text,
            )

        if event_type == "pokemon.pause.required":
            return EventDirective(
                action=TRIAGE_AGENT_TURN,
                requires_llm=True,
                dashboard_patch={
                    "target": "risk_banner",
                    "op": "replace",
                    "content": payload,
                },
                outputs=[ChannelOutput("pokemon.pause_required", payload)],
                summary=text,
            )

        return None

    def build_turn_prompt(
        self,
        channel: dict[str, Any],
        *,
        event: dict[str, Any],
        base_prompt: str,
    ) -> str | None:
        game_name = _pokemon_game_name(channel)
        return (
            f"{base_prompt}\n\n"
            f"You are a calm Pokemon field captain, tactician, shiny spotter, and long-horizon run companion for a live {game_name} playthrough. "
            "You are operating from vision targeted at the emulator window plus recent structured runtime events, not omniscient hidden state. "
            "Reason from what is visibly on screen, confirm each state transition visually before sending the next input, and avoid spamming buttons through animations or uncertain menus. "
            f"{_pokemon_control_guide()} "
            "Optimize for safe progress, battle legality, catch value, and steady advancement toward beating the Elite Four, finding shinies, and completing the Pokedex. "
            "Prefer deterministic, low-risk actions when game state is clear. If state is uncertain or a rare opportunity appears, slow down, explain the risk, and choose the safest reversible action. "
            "Treat direct operator guidance from the chat textbox as a high-priority tactical instruction unless it conflicts with safety or game legality. "
            "Respond in exactly three short lines: `Musing: ...`, `Why: ...`, and `Action buttons: ...`. "
            "For `Action buttons`, emit 1 to 4 space-separated button tokens from UP DOWN LEFT RIGHT A B X Y L R START SELECT, or `none` if no safe input should be sent yet."
        )

    def adapt_result_outputs(
        self,
        channel: dict[str, Any],
        *,
        event: dict[str, Any],
        result: RunExecutionResult,
    ) -> list[ChannelOutput]:
        final_text = result.final_text or result.text or ""
        if not final_text:
            return []
        outputs = [
            ChannelOutput(
                "pokemon.recommendation",
                {
                    "summary": final_text[:280],
                    "event_type": event.get("event_type"),
                    "run_id": result.run_id,
                    "situation": event.get("text") or event.get("event_type") or "",
                    "goal": channel.get("active_objective") or "Advance the Pokemon run safely.",
                    "watch": "State uncertainty, shiny/rare encounters, battle risk, and irreversible choices.",
                    "next": final_text[:280],
                },
            )
        ]

        buttons = _extract_pokemon_action_buttons(final_text)
        if buttons:
            outputs.append(
                ChannelOutput(
                    "pokemon.action_command",
                    {
                        "buttons": buttons,
                        "summary": final_text[:280],
                        "event_type": event.get("event_type"),
                        "run_id": result.run_id,
                        "situation": event.get("text") or event.get("event_type") or "",
                        "goal": channel.get("active_objective") or "Advance the Pokemon run safely.",
                    },
                )
            )
        return outputs

    def build_tick_event(self, channel: dict[str, Any], *, job: dict[str, Any]) -> TickEvent | None:
        payload = dict(job.get("metadata") or {})
        payload.setdefault("kind", "ambient_review")
        payload.setdefault("focus", ["objective", "team", "battle_risk", "catch_targets"])
        payload.setdefault("mode", channel.get("mode") or "ambient")
        return TickEvent(
            event_type="channel.tick",
            source="scheduler",
            payload=payload,
            text=f"Scheduled Pokemon progress review for {channel.get('name', 'unknown')}.",
            priority=int(payload.get("priority") or 35),
            sync=True,
        )

    def build_dashboard(
        self,
        channel: dict[str, Any],
        *,
        events: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        runtime_metadata = dict(channel.get("metadata") or {}).get("pokemon_runtime") or {}
        bridge_state = dict(runtime_metadata.get("bridge") or {})
        control_state = dict(runtime_metadata.get("control") or {})
        pokemon_memory = dict(channel.get("working_memory") or {}).get("pokemon") or {}
        team = _latest_output(outputs, "pokemon.team_state") or {}
        location = _latest_output(outputs, "pokemon.location_state") or {}
        frame = _latest_output(outputs, "pokemon.frame_state") or pokemon_memory.get("frame") or bridge_state.get("frame") or {}
        battle = _latest_output(outputs, "pokemon.battle_state") or {}
        encounter = _latest_output(outputs, "pokemon.encounter_state") or {}
        inventory = _latest_output(outputs, "pokemon.inventory_state") or {}
        objective = _latest_output(outputs, "pokemon.objective") or {}
        progress = _latest_output(outputs, "pokemon.progress_state") or {}
        catch_result = _latest_output(outputs, "pokemon.catch_result") or {}
        recommendation = _latest_output(outputs, "pokemon.recommendation") or {}
        input_result = _latest_output(outputs, "pokemon.input_result") or {}
        decision_log = _outputs_with_prefix(outputs, "pokemon.decision_record", limit=10)
        knowledge_gaps = _outputs_with_prefix(outputs, "pokemon.knowledge_gap", limit=10)

        current_route = location.get("route") or location.get("area") or location.get("map_name") or ""
        current_goal = objective.get("title") or objective.get("summary") or channel.get("active_objective") or "No active objective recorded."
        next_action = (
            battle.get("recommended_action")
            or encounter.get("recommended_action")
            or objective.get("next_action")
            or recommendation.get("next")
            or channel.get("last_agent_decision")
            or "Hold state and request the next deterministic action."
        )
        watch = (
            encounter.get("watch")
            or battle.get("watch")
            or objective.get("watch")
            or recommendation.get("watch")
            or "Watch for uncertain state, rare encounters, and party risk."
        )
        trainer_name = progress.get("trainer_name") or team.get("trainer_name") or channel.get("name") or "Pokemon channel"
        bridge_panel = {
            "capture_source": frame.get("capture_source") or bridge_state.get("frame", {}).get("capture_source") or "window",
            "window_title": bridge_state.get("emulator", {}).get("window_title") or pokemon_memory.get("emulator", {}).get("window_title") or "",
            "bridge_connected": bridge_state.get("connected"),
            "last_snapshot_at": bridge_state.get("last_snapshot_at") or pokemon_memory.get("last_snapshot_at") or "",
            "frame_path": frame.get("path") or "",
            "resolution": f"{frame.get('width')}x{frame.get('height')}" if frame.get("width") and frame.get("height") else "",
            "recent_emissions": bridge_state.get("recent_emissions") or [],
        }
        encounter_watch = {
            "danger_score": 90 if encounter.get("shiny") else (70 if battle.get("low_confidence") else (55 if encounter.get("rare") else 20)),
            "warning": encounter.get("species") or battle.get("summary") or "No live encounter warning.",
            "watch": watch,
            "alternate_route": {"label": objective.get("next_action") or next_action, "extra_jumps": 0} if objective.get("next_action") or next_action else {},
        }
        battle_panel = {
            "pickup": battle.get("active_pokemon", {}).get("species") or "",
            "dropoff": battle.get("opponent", {}).get("species") or "",
            "reward": battle.get("turn_index"),
            "collateral": len(battle.get("available_actions") or []),
            "volume_m3": "",
            "agent_comment": battle.get("summary") or next_action,
        }
        inventory_panel = {
            "total_sellable_value": inventory.get("money") or progress.get("money"),
            "stations": len(inventory.get("key_items") or inventory.get("healing_items") or []),
            "consolidation_route": inventory.get("key_items") or [],
            "dead_weight": inventory.get("discardable_items") or [],
        }
        progress_panel = {
            "wallet_balance": progress.get("money"),
            "session_profit_estimate": progress.get("pokedex_caught"),
            "target_profit": progress.get("pokedex_seen"),
        }
        control_panel = {
            "current_plan": current_goal,
            "why_we_chose_it": objective.get("why") or progress.get("why") or "Advance the run safely and steadily.",
            "what_were_watching": watch,
            "abort_conditions": ["Pause on low-confidence reads", "Pause on shiny or rare encounters"] + ([control_state.get("operator_note")] if control_state.get("operator_note") else []),
            "next_actions": [next_action] if next_action else [],
            "session_mood": control_state.get("mode") or channel.get("mode") or "assist",
            "step_budget": control_state.get("step_budget") or 0,
            "last_input": input_result.get("buttons") or [],
        }

        return {
            "domain": channel.get("domain"),
            "mode": channel.get("mode"),
            "fantasy": "field-captain-tactician-companion",
            "run": {
                "trainer_name": trainer_name,
                "current_route": current_route,
                "current_objective": current_goal,
                "next_action": next_action,
            },
            "pilot": {
                "name": trainer_name,
                "ship_label": f"Party {len(team.get('party') or [])}",
                "location_label": current_route or location.get("map_name") or "Location not pinned",
                "corp_label": (control_state.get("mode") or "assist").upper(),
            },
            "four_questions": {
                "what": battle.get("summary") or encounter.get("summary") or current_goal,
                "why": objective.get("why") or progress.get("why") or "Advance the run safely while improving long-horizon progression.",
                "watch": watch,
                "next": next_action,
            },
            "panels": {
                "pilot_identity": {
                    "character_name": trainer_name,
                    "corporation_name": location.get("area") or location.get("map_name") or "Kalos",
                    "alliance_name": progress.get("goal") or "Pokemon X/Y",
                    "hauling_role": "field captain",
                    "home_hub": location.get("checkpoint") or location.get("story_checkpoint") or "",
                    "preferred_hull": team.get("party", [{}])[0].get("species") if team.get("party") else "",
                    "risk_posture": watch,
                },
                "current_location": {
                    "current_system": location.get("route") or location.get("map_name") or "",
                    "current_station": location.get("checkpoint") or "",
                    "region_name": location.get("area") or "Kalos",
                    "ship_name": battle.get("active_pokemon", {}).get("species") or "",
                    "ship_type": encounter.get("species") or "",
                    "flight_state": control_state.get("mode") or channel.get("mode") or "assist",
                    "destination": objective.get("next_action") or "",
                    "security_band": "story",
                    "situation": battle.get("summary") or encounter.get("summary") or current_goal,
                },
                "current_run": bridge_panel,
                "route_risk": encounter_watch,
                "contract_job": battle_panel,
                "asset_inventory": inventory_panel,
                "wallet_profit": progress_panel,
                "session_companion": control_panel,
                "party": team,
                "location": location,
                "frame_capture": frame,
                "bridge_status": bridge_panel,
                "runtime_control": control_panel,
                "battle": battle,
                "encounter": encounter,
                "inventory": inventory,
                "progress": progress,
                "objective": objective,
                "recent_catch": catch_result,
                "decision_timeline": decision_log,
                "knowledge_gaps": knowledge_gaps,
            },
            "alerts": [dict(output.get("payload") or {}) for output in outputs if output.get("output_type") == "alert"][:5],
            "recent_events": events[:15],
        }


class AgentHarnessRegistry:
    def __init__(self) -> None:
        self._harnesses: dict[str, AgentDomainHarness] = {}
        self._default = BaseAgentDomainHarness()
        self.register(self._default.domain, self._default)

    def register(self, domain: str, harness: AgentDomainHarness) -> None:
        self._harnesses[str(domain).strip().lower() or self._default.domain] = harness

    def get(self, domain: str | None) -> AgentDomainHarness:
        key = str(domain or "").strip().lower()
        return self._harnesses.get(key, self._default)

    def describe(self, domain: str | None) -> dict[str, Any]:
        harness = self.get(domain)
        return {
            "domain": str(domain or harness.domain or self._default.domain),
            "name": getattr(harness, "name", harness.__class__.__name__),
            "class_name": harness.__class__.__name__,
        }

    def list_harnesses(self) -> list[dict[str, Any]]:
        return [
            {
                "domain": domain,
                "name": getattr(harness, "name", harness.__class__.__name__),
                "class_name": harness.__class__.__name__,
            }
            for domain, harness in sorted(self._harnesses.items())
        ]