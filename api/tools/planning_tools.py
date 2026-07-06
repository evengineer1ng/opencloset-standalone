from __future__ import annotations

from typing import Any

from api.tools.registry import (
    CATEGORY_EXTENDED,
    ToolContract,
    ValidationResult,
    build_tool,
)


PLAN_RUNTIME_STATUSES = {"active", "paused", "rolled-over"}
PLAN_ITEM_STATUSES = {"todo", "doing", "done", "blocked", "deferred"}
PLAN_PROPOSAL_TYPES = {"create_plan", "activate_plan", "archive_plan", "reorder_item", "add_item", "grant_path_access"}
PLAN_PROPOSAL_STATUSES = {"pending", "accepted", "rejected"}


def _require_non_empty_string(data: dict[str, Any], field: str) -> ValidationResult | None:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        return ValidationResult(valid=False, errors=[f"{field} is required"])
    return None


def validate_plan_add_item_input(data: dict[str, Any]) -> ValidationResult:
    content = data.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return ValidationResult(valid=False, errors=["content is required"])

    status = data.get("status")
    if status is not None and status not in PLAN_ITEM_STATUSES:
        return ValidationResult(
            valid=False,
            errors=[f"Invalid item status '{status}'"],
        )

    position = data.get("position")
    if position is not None and (not isinstance(position, int) or position < 1):
        return ValidationResult(valid=False, errors=["position must be a positive integer"])

    return ValidationResult(valid=True)


def validate_plan_set_status_input(data: dict[str, Any]) -> ValidationResult:
    status = data.get("status")
    if status not in PLAN_RUNTIME_STATUSES:
        return ValidationResult(
            valid=False,
            errors=[f"status must be one of {tuple(sorted(PLAN_RUNTIME_STATUSES))}"],
        )
    return ValidationResult(valid=True)


def validate_plan_create_input(data: dict[str, Any]) -> ValidationResult:
    missing = _require_non_empty_string(data, "title")
    if missing:
        return missing
    active_goal = data.get("active_goal")
    if active_goal is not None and not isinstance(active_goal, str):
        return ValidationResult(valid=False, errors=["active_goal must be a string"])
    want_to_know = data.get("want_to_know")
    if want_to_know is not None and (
        not isinstance(want_to_know, list) or not all(isinstance(item, str) for item in want_to_know)
    ):
        return ValidationResult(valid=False, errors=["want_to_know must be a list of strings"])
    activate = data.get("activate")
    if activate is not None and not isinstance(activate, bool):
        return ValidationResult(valid=False, errors=["activate must be a boolean"])
    return ValidationResult(valid=True)


def validate_plan_activate_input(data: dict[str, Any]) -> ValidationResult:
    return _require_non_empty_string(data, "plan_id") or ValidationResult(valid=True)


def validate_plan_list_stored_input(data: dict[str, Any]) -> ValidationResult:
    status = data.get("status")
    if status is not None and status not in PLAN_RUNTIME_STATUSES:
        return ValidationResult(
            valid=False,
            errors=[f"status must be one of {tuple(sorted(PLAN_RUNTIME_STATUSES))}"],
        )
    query = data.get("query")
    if query is not None and not isinstance(query, str):
        return ValidationResult(valid=False, errors=["query must be a string"])
    active_only = data.get("active_only")
    if active_only is not None and not isinstance(active_only, bool):
        return ValidationResult(valid=False, errors=["active_only must be a boolean"])
    limit = data.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        return ValidationResult(valid=False, errors=["limit must be a positive integer"])
    return ValidationResult(valid=True)


def validate_plan_reorder_input(data: dict[str, Any]) -> ValidationResult:
    missing = _require_non_empty_string(data, "item_id")
    if missing:
        return missing
    position = data.get("position")
    if not isinstance(position, int) or position < 1:
        return ValidationResult(valid=False, errors=["position must be a positive integer"])
    return ValidationResult(valid=True)


def validate_plan_archive_input(data: dict[str, Any]) -> ValidationResult:
    missing = _require_non_empty_string(data, "plan_id")
    if missing:
        return missing
    status = data.get("status")
    if status is not None and status not in {"archived", "superseded"}:
        return ValidationResult(valid=False, errors=["status must be archived or superseded"])
    return ValidationResult(valid=True)


def validate_plan_list_proposals_input(data: dict[str, Any]) -> ValidationResult:
    status = data.get("status")
    if status is not None and status not in PLAN_PROPOSAL_STATUSES:
        return ValidationResult(valid=False, errors=[f"status must be one of {tuple(sorted(PLAN_PROPOSAL_STATUSES))}"])
    plan_id = data.get("plan_id")
    if plan_id is not None and (not isinstance(plan_id, str) or not plan_id.strip()):
        return ValidationResult(valid=False, errors=["plan_id must be a non-empty string"])
    limit = data.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        return ValidationResult(valid=False, errors=["limit must be a positive integer"])
    return ValidationResult(valid=True)


def validate_plan_propose_change_input(data: dict[str, Any]) -> ValidationResult:
    proposal_type = data.get("proposal_type")
    if proposal_type not in PLAN_PROPOSAL_TYPES:
        return ValidationResult(
            valid=False,
            errors=[f"proposal_type must be one of {tuple(sorted(PLAN_PROPOSAL_TYPES))}"],
        )
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return ValidationResult(valid=False, errors=["payload must be an object"])
    plan_id = data.get("plan_id")
    if plan_id is not None and (not isinstance(plan_id, str) or not plan_id.strip()):
        return ValidationResult(valid=False, errors=["plan_id must be a non-empty string"])
    summary = data.get("summary")
    if summary is not None and not isinstance(summary, str):
        return ValidationResult(valid=False, errors=["summary must be a string"])
    return ValidationResult(valid=True)


def validate_plan_resolution_input(data: dict[str, Any]) -> ValidationResult:
    missing = _require_non_empty_string(data, "proposal_id")
    if missing:
        return missing
    resolution_note = data.get("resolution_note")
    if resolution_note is not None and not isinstance(resolution_note, str):
        return ValidationResult(valid=False, errors=["resolution_note must be a string"])
    return ValidationResult(valid=True)


def make_plan_get_active_tool(*, planning, session_id: str) -> ToolContract:
    return build_tool(
        name="plan_get_active",
        description="Get the active plan for the current session, including plan items and the next actionable item.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        execute=lambda args: planning.get_plan(session_id) or {"error": "no active plan"},
        read_only=True,
        concurrency_safe=True,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_list_stored_tool(*, planning, session_id: str) -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        plans = planning.list_plans(
            session_id,
            status=args.get("status"),
            query=args.get("query"),
            active_only=args.get("active_only"),
        )
        limit = args.get("limit", 50)
        return {
            "plans": plans[:limit],
            "count": len(plans[:limit]),
            "truncated": len(plans) > limit,
        }

    return build_tool(
        name="plan_list_stored",
        description="List stored plans accessible to the current session, including workspace-shared plans, with optional search and status filters.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional case-insensitive search over title, active goal, and want-to-know items"},
                "status": {
                    "type": "string",
                    "description": "Optional plan status filter",
                    "enum": sorted(PLAN_RUNTIME_STATUSES),
                },
                "active_only": {"type": "boolean", "description": "Optional filter for only active or only inactive plans"},
                "limit": {"type": "integer", "description": "Maximum number of plans to return", "minimum": 1},
            },
        },
        execute=execute,
        validate_input=validate_plan_list_stored_input,
        read_only=True,
        concurrency_safe=True,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_add_item_tool(*, planning, session_id: str) -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        plan = planning.get_plan(session_id)
        if not plan:
            return {"error": "no active plan"}
        item = planning.add_plan_item(
            session_id,
            plan["id"],
            args["content"],
            status=args.get("status", "todo"),
            position=args.get("position"),
        )
        return {
            "plan_id": plan["id"],
            "item": item,
            "next_item": planning.get_plan(session_id).get("next_item"),
        }

    return build_tool(
        name="plan_add_item",
        description="Add an item to the active plan for the current session.",
        input_schema={
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {"type": "string", "description": "Plan item content"},
                "status": {
                    "type": "string",
                    "description": "Initial plan item status",
                    "enum": sorted(PLAN_ITEM_STATUSES),
                },
                "position": {
                    "type": "integer",
                    "description": "Optional 1-indexed insertion position",
                    "minimum": 1,
                },
            },
        },
        execute=execute,
        validate_input=validate_plan_add_item_input,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_set_status_tool(*, planning, session_id: str) -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        planning.set_status(session_id, args["status"])
        plan = planning.get_plan(session_id)
        return {
            "session_id": session_id,
            "status": plan.get("status") if plan else args["status"],
            "runtime_status": plan.get("status") if plan else args["status"],
            "plan_id": plan.get("id") if plan else None,
            "scope": "runtime_only",
            "note": "Updated plan runtime status only. This does not change plan items, active goal, or plan content.",
        }

    return build_tool(
        name="plan_set_status",
        description="Set the active session plan runtime status only. Supported values are active, paused, and rolled-over. This does not edit plan items, active goal, or stored plan content.",
        input_schema={
            "type": "object",
            "required": ["status"],
            "properties": {
                "status": {
                    "type": "string",
                    "description": "New runtime status for the active session plan (runtime only, not a content edit)",
                    "enum": sorted(PLAN_RUNTIME_STATUSES),
                },
            },
        },
        execute=execute,
        validate_input=validate_plan_set_status_input,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_create_tool(*, planning, session_id: str) -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        plan_id = planning.create_plan(
            session_id,
            title=args["title"],
            active_goal=args.get("active_goal", ""),
            want_to_know=args.get("want_to_know", []),
            activate=args.get("activate", False),
            activation_reason="tool",
        )
        return planning.get_plan_by_id(session_id, plan_id)

    return build_tool(
        name="plan_create",
        description="Create a stored plan for the current session, optionally activating it immediately.",
        input_schema={
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "description": "Stored plan title"},
                "active_goal": {"type": "string", "description": "Initial active goal for the plan"},
                "want_to_know": {
                    "type": "array",
                    "description": "Optional want-to-know list for the new plan",
                    "items": {"type": "string"},
                },
                "activate": {"type": "boolean", "description": "Whether to activate the new plan immediately"},
            },
        },
        execute=execute,
        validate_input=validate_plan_create_input,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_activate_tool(*, planning, session_id: str) -> ToolContract:
    return build_tool(
        name="plan_activate",
        description="Activate a stored plan that is already accessible to the current session.",
        input_schema={
            "type": "object",
            "required": ["plan_id"],
            "properties": {
                "plan_id": {"type": "string", "description": "Stored plan id to activate"},
            },
        },
        execute=lambda args: planning.activate_plan(session_id, args["plan_id"], reason="tool"),
        validate_input=validate_plan_activate_input,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_reorder_tool(*, planning, session_id: str) -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        plan = planning.get_plan(session_id)
        if not plan:
            return {"error": "no active plan"}
        item = planning.update_plan_item(
            session_id,
            plan["id"],
            args["item_id"],
            position=args["position"],
        )
        return {
            "plan_id": plan["id"],
            "item": item,
            "next_item": planning.get_plan(session_id).get("next_item"),
        }

    return build_tool(
        name="plan_reorder",
        description="Reorder an item within the active plan for the current session.",
        input_schema={
            "type": "object",
            "required": ["item_id", "position"],
            "properties": {
                "item_id": {"type": "string", "description": "Plan item id to move"},
                "position": {"type": "integer", "description": "New 1-indexed item position", "minimum": 1},
            },
        },
        execute=execute,
        validate_input=validate_plan_reorder_input,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_archive_tool(*, planning, session_id: str) -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        return planning.update_plan(session_id, args["plan_id"], status=args.get("status", "archived"))

    return build_tool(
        name="plan_archive",
        description="Archive or supersede a stored plan that is not currently active.",
        input_schema={
            "type": "object",
            "required": ["plan_id"],
            "properties": {
                "plan_id": {"type": "string", "description": "Stored plan id to archive"},
                "status": {
                    "type": "string",
                    "description": "Archive status to apply",
                    "enum": ["archived", "superseded"],
                },
            },
        },
        execute=execute,
        validate_input=validate_plan_archive_input,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_list_proposals_tool(*, planning, session_id: str) -> ToolContract:
    return build_tool(
        name="plan_list_proposals",
        description="List durable plan proposals for the current session, optionally filtered by plan or proposal status.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional proposal status filter",
                    "enum": sorted(PLAN_PROPOSAL_STATUSES),
                },
                "plan_id": {"type": "string", "description": "Optional stored plan id filter"},
                "limit": {"type": "integer", "description": "Maximum number of proposals to return", "minimum": 1},
            },
        },
        execute=lambda args: {
            "proposals": planning.list_plan_proposals(
                session_id,
                status=args.get("status"),
                plan_id=args.get("plan_id"),
                limit=args.get("limit", 50),
            )
        },
        validate_input=validate_plan_list_proposals_input,
        read_only=True,
        concurrency_safe=True,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_propose_change_tool(*, planning, session_id: str, proposed_by: str = "buddy") -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        return planning.submit_plan_proposal(
            session_id,
            args["proposal_type"],
            args["payload"],
            plan_id=args.get("plan_id"),
            summary=args.get("summary", ""),
            proposed_by=proposed_by,
        )

    return build_tool(
        name="plan_propose_change",
        description="Submit a durable planning proposal without applying it. Intended for Buddy-style proposal flow.",
        input_schema={
            "type": "object",
            "required": ["proposal_type", "payload"],
            "properties": {
                "proposal_type": {
                    "type": "string",
                    "description": "Kind of planning change to propose",
                    "enum": sorted(PLAN_PROPOSAL_TYPES),
                },
                "plan_id": {"type": "string", "description": "Optional plan id when the proposal targets a specific stored plan"},
                "summary": {"type": "string", "description": "Short proposal summary"},
                "payload": {"type": "object", "description": "Proposal payload for the requested planning change"},
            },
        },
        execute=execute,
        validate_input=validate_plan_propose_change_input,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_accept_proposal_tool(*, planning, session_id: str, accepted_by: str = "clo") -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        return planning.accept_plan_proposal(session_id, args["proposal_id"], accepted_by=accepted_by)

    return build_tool(
        name="plan_accept_proposal",
        description="Accept and apply a pending planning proposal for the current session.",
        input_schema={
            "type": "object",
            "required": ["proposal_id"],
            "properties": {
                "proposal_id": {"type": "string", "description": "Pending plan proposal id to accept"},
            },
        },
        execute=execute,
        validate_input=validate_plan_resolution_input,
        categories=[CATEGORY_EXTENDED],
    )


def make_plan_reject_proposal_tool(*, planning, session_id: str, rejected_by: str = "clo") -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        return planning.reject_plan_proposal(
            session_id,
            args["proposal_id"],
            rejected_by=rejected_by,
            resolution_note=args.get("resolution_note", ""),
        )

    return build_tool(
        name="plan_reject_proposal",
        description="Reject a pending planning proposal without applying it.",
        input_schema={
            "type": "object",
            "required": ["proposal_id"],
            "properties": {
                "proposal_id": {"type": "string", "description": "Pending plan proposal id to reject"},
                "resolution_note": {"type": "string", "description": "Optional rejection note"},
            },
        },
        execute=execute,
        validate_input=validate_plan_resolution_input,
        categories=[CATEGORY_EXTENDED],
    )


def register_planning_tools(registry, *, planning, session_id: str) -> list[ToolContract]:
    if getattr(registry, "agent_type", "main") == "buddy":
        tools = [
            make_plan_get_active_tool(planning=planning, session_id=session_id),
            make_plan_list_stored_tool(planning=planning, session_id=session_id),
            make_plan_list_proposals_tool(planning=planning, session_id=session_id),
            make_plan_propose_change_tool(planning=planning, session_id=session_id, proposed_by="buddy"),
        ]
    else:
        tools = [
            make_plan_get_active_tool(planning=planning, session_id=session_id),
            make_plan_list_stored_tool(planning=planning, session_id=session_id),
            make_plan_add_item_tool(planning=planning, session_id=session_id),
            make_plan_set_status_tool(planning=planning, session_id=session_id),
            make_plan_create_tool(planning=planning, session_id=session_id),
            make_plan_activate_tool(planning=planning, session_id=session_id),
            make_plan_reorder_tool(planning=planning, session_id=session_id),
            make_plan_archive_tool(planning=planning, session_id=session_id),
            make_plan_list_proposals_tool(planning=planning, session_id=session_id),
            make_plan_accept_proposal_tool(planning=planning, session_id=session_id),
            make_plan_reject_proposal_tool(planning=planning, session_id=session_id),
        ]
    registry.register_many(tools)
    return tools