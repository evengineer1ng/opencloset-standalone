from __future__ import annotations

from typing import Any

from api.tools.registry import CATEGORY_CORE, ToolContract, ValidationResult, build_tool


def validate_memory_search_input(data: dict[str, Any]) -> ValidationResult:
    query = data.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return ValidationResult(valid=False, errors=["query is required"])

    limit = data.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        return ValidationResult(valid=False, errors=["limit must be a positive integer"])

    return ValidationResult(valid=True)


def make_memory_search_tool(*, memory_manager, session_id: str) -> ToolContract:
    def execute(args: dict[str, Any]) -> dict[str, Any]:
        results = memory_manager.search(
            session_id,
            args["query"],
            limit=args.get("limit", 5),
            include_daily=args.get("include_daily", True),
            exclude_seen=not args.get("include_seen", False),
            mark_seen=True,
        )
        return {
            "session_id": session_id,
            "query": args["query"],
            "strategy": memory_manager.search_strategy,
            "results": results,
        }

    return build_tool(
        name="memory_search",
        description="Search the current session diary and daily logs using ranked keyword retrieval with optional semantic reranking.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Keyword query to search for in stored memory"},
                "limit": {"type": "integer", "description": "Maximum results to return", "minimum": 1},
                "include_daily": {
                    "type": "boolean",
                    "description": "Whether to search daily logs in addition to the session diary",
                },
                "include_seen": {
                    "type": "boolean",
                    "description": "Include memory entries already surfaced to the model",
                },
            },
        },
        execute=execute,
        validate_input=validate_memory_search_input,
        read_only=True,
        concurrency_safe=True,
        categories=[CATEGORY_CORE],
    )


def register_memory_tools(registry, *, memory_manager, session_id: str) -> list[ToolContract]:
    tools = [make_memory_search_tool(memory_manager=memory_manager, session_id=session_id)]
    registry.register_many(tools)
    return tools