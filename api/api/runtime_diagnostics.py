from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


FAILED_TOOL_STATUSES = {
    "failed",
    "validation_failed",
    "permission_denied",
    "ask_pending",
    "execution_error",
    "interrupted",
    "tool_not_found",
}
SUCCESS_TOOL_STATUSES = {"success", "completed"}
PATH_KEYS = {
    "path",
    "paths",
    "file",
    "file_path",
    "filePath",
    "target",
    "target_path",
    "directory",
    "dirPath",
    "workspaceFolder",
}
WRITE_TOOL_NAMES = {
    "write",
    "edit",
    "create_file",
    "edit_file",
    "write_file",
    "apply_patch",
}
ERROR_WINDOW_NATIVE_TYPE = "error_window"


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        return value
    if trimmed[0] not in "[{":
        return value
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        return value


def _compact_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _tool_failure_detail(tool: dict[str, Any] | None, fallback: str = "") -> str:
    if not tool:
        return str(fallback or "").strip()
    output = str(tool.get("output") or "").strip()
    error = str(tool.get("error") or "").strip()
    fallback_text = str(fallback or "").strip()
    combined = "\n".join(part for part in (output, error, fallback_text) if part)
    return combined.strip()


class RuntimeDiagnosticsManager:
    def __init__(self, db, event_logger, windows, execution_support=None) -> None:
        self.db = db
        self.event_logger = event_logger
        self.windows = windows
        self.execution_support = execution_support

    def maybe_emit_run_error_window(self, session_id: str, run_id: str) -> dict[str, Any] | None:
        existing = self._find_existing_window(session_id, run_id)
        if existing:
            return existing

        run = self._get_run(session_id, run_id)
        if not run:
            return None

        session = self._get_session(session_id)
        if not session:
            return None

        messages = self._get_run_messages(session_id, run_id)
        tool_invocations = self._get_tool_invocations(session_id, run_id)
        run_events = self.event_logger.get_run_events(session_id, run_id, limit=200)
        payload = self._build_payload(session, run, messages, tool_invocations, run_events)
        if payload is None:
            return None

        return self.windows.create(
            session_id,
            payload["title"],
            "",
            source_type="native",
            native_type=ERROR_WINDOW_NATIVE_TYPE,
            payload=payload,
            summary=payload["summary"],
        )

    def _find_existing_window(self, session_id: str, run_id: str) -> dict[str, Any] | None:
        for window in self.windows.list_for_session(session_id):
            payload = window.get("payload") or {}
            if (
                window.get("native_type") == ERROR_WINDOW_NATIVE_TYPE
                and payload.get("origin") == "watchdog"
                and payload.get("run_id") == run_id
            ):
                return window
        return None

    def _get_run(self, session_id: str, run_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT id, session_id, status, error, turn_number, created_at, completed_at FROM runs WHERE id = ? AND session_id = ?",
            (run_id, session_id),
        ).fetchone()
        return dict(row) if row else None

    def _get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT id, label, model, provider FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def _get_run_messages(self, session_id: str, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, run_id, role, content, created_at, position FROM messages WHERE session_id = ? AND (run_id = ? OR role = 'user') ORDER BY position ASC",
            (session_id, run_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _get_tool_invocations(self, session_id: str, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, tool_name, input, output, status, error, started_at, completed_at FROM tool_invocations WHERE session_id = ? AND run_id = ? ORDER BY COALESCE(completed_at, started_at) ASC, rowid ASC",
            (session_id, run_id),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["input"] = _parse_json(item.get("input") or "{}")
            item["output"] = _parse_json(item.get("output"))
            results.append(item)
        return results

    def _build_payload(
        self,
        session: dict[str, Any],
        run: dict[str, Any],
        messages: list[dict[str, Any]],
        tool_invocations: list[dict[str, Any]],
        run_events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        event_tools = self._build_tool_records_from_events(run_events)
        diagnostic_tools = [*tool_invocations, *event_tools] if event_tools else tool_invocations
        assistant_messages = [
            message for message in messages if message.get("run_id") == run["id"] and message.get("role") == "assistant" and str(message.get("content") or "").strip()
        ]
        failed_tool = next(
            (
                tool
                for tool in reversed(diagnostic_tools)
                if str(tool.get("status") or "").lower() in FAILED_TOOL_STATUSES or tool.get("error")
            ),
            None,
        )
        last_successful_tool = next(
            (
                tool
                for tool in reversed(diagnostic_tools)
                if str(tool.get("status") or "").lower() in SUCCESS_TOOL_STATUSES
            ),
            None,
        )

        category = self._classify_category(run, failed_tool, assistant_messages)
        if category is None:
            return None

        user_goal = self._last_user_goal(messages)
        assistant_intent = self._assistant_intent(run_events, assistant_messages)
        files_touched = self._collect_files(diagnostic_tools)
        modified_before_failure = any(
            tool.get("tool_name") in WRITE_TOOL_NAMES and str(tool.get("status") or "").lower() in SUCCESS_TOOL_STATUSES
            for tool in diagnostic_tools
        )
        recent_events = [
            {
                "label": event.get("type") or "event",
                "value": self._describe_event(event),
            }
            for event in run_events[-8:]
        ]

        failed_event = self._build_tool_event(failed_tool, fallback_error=run.get("error"), event_type="tool.call.failed")
        last_successful_event = self._build_tool_event(last_successful_tool, event_type="tool.call.completed")
        title = self._title_for(category, failed_tool)
        summary = self._summary_for(category, session, run, failed_tool, assistant_messages, files_touched)
        suggestion_summary, recovery_prompt = self._suggest_direction(
            session,
            run,
            category,
            failed_tool,
            files_touched,
            user_goal=user_goal,
            assistant_intent=assistant_intent,
            recent_events=recent_events,
        )
        failure_pivot = self._latest_failure_pivot(run_events)
        raw_error = _compact_text((failed_tool or {}).get("error") or run.get("error") or "Unknown runtime error", 1200)

        suggested_direction: dict[str, Any] = {
            "summary": suggestion_summary,
            "recovery_prompt": failure_pivot.get("pivot_hint") or recovery_prompt,
        }
        if failure_pivot.get("pivot_summary"):
            suggested_direction["pivot_summary"] = failure_pivot["pivot_summary"]
        if failure_pivot.get("pivot_hint"):
            suggested_direction["pivot_hint"] = failure_pivot["pivot_hint"]

        return {
            "id": f"err_{run['id']}",
            "session_id": session["id"],
            "run_id": run["id"],
            "created_at": run.get("completed_at") or run.get("created_at") or _utcnow(),
            "origin": "watchdog",
            "artifact_type": "transient_error_window",
            "severity": "error",
            "category": category,
            "title": title,
            "summary": summary,
            "session": {
                "id": session["id"],
                "label": session.get("label") or session["id"],
            },
            "model": {
                "provider": session.get("provider") or "unknown",
                "name": session.get("model") or "unknown",
            },
            "failed_component": {
                "type": "tool" if failed_tool else "run",
                "name": failed_tool.get("tool_name") if failed_tool else "agent_run",
                "target": files_touched[-1] if files_touched else None,
            },
            "last_successful_event": last_successful_event,
            "failed_event": failed_event,
            "files_touched": files_touched,
            "transcript_excerpt": {
                "user_goal": user_goal,
                "assistant_intent": assistant_intent,
                "recent_events": recent_events,
            },
            "last_known_activity": {
                "goal": user_goal,
                "assistant_intent": assistant_intent,
                "last_successful_action": last_successful_event,
                "failed_action": failed_event,
                "files_touched": files_touched,
                "modified_before_failure": modified_before_failure,
            },
            "suggested_direction": suggested_direction,
            "raw": {
                "error": raw_error,
                "traceback": raw_error if "traceback" in raw_error.lower() else "",
                "tool_payload": failed_tool.get("input") if failed_tool else None,
                "event_tail": run_events[-8:],
            },
            "actions": ["copy_recovery_prompt"],
        }

    def _classify_category(
        self,
        run: dict[str, Any],
        failed_tool: dict[str, Any] | None,
        assistant_messages: list[dict[str, Any]],
    ) -> str | None:
        status = str(run.get("status") or "").lower()
        if status == "failed":
            return self._tool_failure_category(failed_tool)
        if failed_tool and not assistant_messages:
            return self._tool_failure_category(failed_tool)
        if status == "succeeded" and not assistant_messages:
            return "empty_completion"
        return None

    def _tool_failure_category(self, failed_tool: dict[str, Any] | None) -> str:
        if not failed_tool:
            return "unknown_runtime_error"
        status = str(failed_tool.get("status") or "").lower()
        if status == "validation_failed":
            return "tool_schema_error"
        if status == "permission_denied":
            return "tool_permission_error"
        if status == "interrupted":
            return "unexpected_idle"
        if status == "execution_error":
            error_text = _tool_failure_detail(failed_tool)
            if any(token in error_text.lower() for token in ("timeout", "timed out")):
                return "tool_timeout"
            return "tool_failure"
        return "tool_failure"

    def _last_user_goal(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                return _compact_text(message.get("content") or "", 320)
        return ""

    def _assistant_intent(self, run_events: list[dict[str, Any]], assistant_messages: list[dict[str, Any]]) -> str:
        deltas = [str(event.get("data", {}).get("text") or "") for event in run_events if event.get("type") == "assistant_delta"]
        joined = "".join(deltas).strip()
        if joined:
            return _compact_text(joined, 320)
        if assistant_messages:
            return _compact_text(assistant_messages[-1].get("content") or "", 320)
        return ""

    def _collect_files(self, tool_invocations: list[dict[str, Any]]) -> list[str]:
        files: list[str] = []
        for tool in tool_invocations:
            for value in self._find_paths(tool.get("input")):
                if value not in files:
                    files.append(value)
        return files

    def _build_tool_records_from_events(self, run_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pending_inputs: dict[str, list[dict[str, Any]]] = {}
        tools: list[dict[str, Any]] = []

        for event in run_events:
            event_type = str(event.get("type") or "")
            data = event.get("data") or {}
            if event_type == "tool_call":
                tool_name = str(data.get("tool_name") or "tool")
                pending_inputs.setdefault(tool_name, []).append(data.get("input") or {})
                continue

            if event_type != "tool_result":
                continue

            tool_name = str(data.get("tool_name") or "tool")
            queued_inputs = pending_inputs.get(tool_name) or []
            input_payload = queued_inputs.pop(0) if queued_inputs else {}
            tools.append(
                {
                    "tool_name": tool_name,
                    "input": input_payload,
                    "output": data.get("content"),
                    "status": data.get("status"),
                    "error": data.get("error"),
                    "started_at": event.get("created_at"),
                    "completed_at": event.get("created_at"),
                }
            )

        return tools

    def _find_paths(self, payload: Any) -> list[str]:
        results: list[str] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in PATH_KEYS:
                    if isinstance(value, str) and value.strip():
                        results.append(value.strip())
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str) and item.strip():
                                results.append(item.strip())
                else:
                    results.extend(self._find_paths(value))
        elif isinstance(payload, list):
            for item in payload:
                results.extend(self._find_paths(item))
        return results

    def _build_tool_event(
        self,
        tool: dict[str, Any] | None,
        *,
        fallback_error: str | None = None,
        event_type: str,
    ) -> dict[str, Any] | None:
        if not tool:
            if fallback_error:
                return {
                    "type": event_type,
                    "name": "agent_run",
                    "target": None,
                    "summary": _compact_text(fallback_error, 240),
                    "error": _compact_text(fallback_error, 600),
                }
            return None

        targets = self._find_paths(tool.get("input"))
        summary = self._describe_tool(tool)
        result = {
            "type": event_type,
            "name": tool.get("tool_name"),
            "target": targets[0] if targets else None,
            "summary": summary,
        }
        if tool.get("error"):
            result["error"] = _compact_text(_tool_failure_detail(tool), 600)
        return result

    def _describe_tool(self, tool: dict[str, Any]) -> str:
        tool_name = str(tool.get("tool_name") or "tool")
        targets = self._find_paths(tool.get("input"))
        target = targets[0] if targets else None
        status = str(tool.get("status") or "").replace("_", " ")
        if target:
            return f"{tool_name} on {target} ({status})"
        return f"{tool_name} ({status})"

    def _describe_event(self, event: dict[str, Any]) -> str:
        event_type = str(event.get("type") or "event")
        data = event.get("data") or {}
        if event_type == "tool_call":
            return self._compact_event_tool_call(data)
        if event_type == "tool_result":
            tool_name = str(data.get("tool_name") or "tool")
            status = str(data.get("status") or "unknown").replace("_", " ")
            error = _tool_failure_detail({"output": data.get("content"), "error": data.get("error")})
            if error:
                return f"{tool_name} returned {status}: {_compact_text(error, 180)}"
            return f"{tool_name} returned {status}"
        if event_type == "tool_failure_pivot":
            tool_name = str(data.get("tool_name") or "tool")
            attempt_count = data.get("attempt_count")
            pivot_hint = _compact_text(data.get("pivot_hint") or "", 180)
            if attempt_count:
                return f"{tool_name} pivoted after repeated failures ({attempt_count}): {pivot_hint}"
            return f"{tool_name} pivoted after repeated failures: {pivot_hint}"
        if event_type == "run_failed":
            return _compact_text(data.get("error") or "run failed", 180)
        if event_type == "error":
            return _compact_text(data.get("message") or "stream error", 180)
        return _compact_text(data or event_type, 180)

    def _latest_failure_pivot(self, run_events: list[dict[str, Any]]) -> dict[str, str]:
        for event in reversed(run_events):
            if str(event.get("type") or "") != "tool_failure_pivot":
                continue
            data = event.get("data") or {}
            tool_name = str(data.get("tool_name") or "tool")
            attempt_count = data.get("attempt_count")
            repeated_pattern = _compact_text(data.get("repeated_pattern") or "", 160)
            pivot_hint = _compact_text(data.get("pivot_hint") or "", 600)
            if not pivot_hint:
                continue
            if attempt_count:
                summary = f"The loop already pivoted {tool_name} after {attempt_count} repeated failures."
            else:
                summary = f"The loop already pivoted {tool_name} after repeated failures."
            if repeated_pattern:
                summary = f"{summary} Pattern: {repeated_pattern}."
            return {
                "pivot_summary": summary,
                "pivot_hint": pivot_hint,
            }
        return {}

    def _compact_event_tool_call(self, data: dict[str, Any]) -> str:
        tool_name = str(data.get("tool_name") or "tool")
        targets = self._find_paths(data.get("input"))
        if targets:
            return f"{tool_name} targeting {targets[0]}"
        return f"{tool_name} invoked"

    def _title_for(self, category: str, failed_tool: dict[str, Any] | None) -> str:
        if category == "empty_completion":
            return "Run ended without a visible result"
        if category == "tool_schema_error":
            return "Run failed during tool validation"
        if category == "tool_permission_error":
            return "Run failed on a blocked tool action"
        if category == "tool_timeout":
            return "Run failed during tool execution"
        if failed_tool:
            return "Run failed during tool execution"
        return "Run failed"

    def _summary_for(
        self,
        category: str,
        session: dict[str, Any],
        run: dict[str, Any],
        failed_tool: dict[str, Any] | None,
        assistant_messages: list[dict[str, Any]],
        files_touched: list[str],
    ) -> str:
        if category == "empty_completion":
            return (
                f"Clo finished run {run['id']} in {session.get('label') or session['id']}, but no assistant message or inline artifact was produced."
            )

        if failed_tool:
            target = files_touched[-1] if files_touched else None
            tool_name = failed_tool.get("tool_name") or "tool"
            error = _compact_text(_tool_failure_detail(failed_tool, run.get("error") or "unknown failure"), 240)
            if target:
                return f"Clo attempted to use {tool_name} on {target}, but the run stopped after the tool failed: {error}"
            return f"Clo attempted to use {tool_name}, but the run stopped after the tool failed: {error}"

        if assistant_messages:
            return f"Clo stopped before completing the run after: {_compact_text(assistant_messages[-1].get('content') or '', 180)}"
        return _compact_text(run.get("error") or "The run stopped before producing a visible completion.", 240)

    def _suggest_direction(
        self,
        session: dict[str, Any],
        run: dict[str, Any],
        category: str,
        failed_tool: dict[str, Any] | None,
        files_touched: list[str],
        *,
        user_goal: str,
        assistant_intent: str,
        recent_events: list[dict[str, str]],
    ) -> tuple[str, str]:
        model_suggestion = self._suggest_direction_via_model(
            session,
            run,
            category,
            failed_tool,
            files_touched,
            user_goal=user_goal,
            assistant_intent=assistant_intent,
            recent_events=recent_events,
        )
        if model_suggestion is not None:
            return model_suggestion

        return self._suggest_direction_fallback(category, failed_tool, files_touched)

    def _suggest_direction_via_model(
        self,
        session: dict[str, Any],
        run: dict[str, Any],
        category: str,
        failed_tool: dict[str, Any] | None,
        files_touched: list[str],
        *,
        user_goal: str,
        assistant_intent: str,
        recent_events: list[dict[str, str]],
    ) -> tuple[str, str] | None:
        if self.execution_support is None:
            return None

        try:
            result, _route = self.execution_support.run_read_only_messages(
                {
                    "id": session.get("id"),
                    "provider": session.get("provider") or "llamacpp",
                    "model": session.get("model") or "",
                },
                messages,
                run_id=str(run.get("id") or "diagnostic"),
                message_text=user_goal or assistant_intent,
                temperature=0.1,
                max_tokens=220,
            )
        except Exception:
            return None

        target = files_touched[-1] if files_touched else None
        failed_tool_name = str((failed_tool or {}).get("tool_name") or "agent_run")
        failed_tool_error = _compact_text(_tool_failure_detail(failed_tool, run.get("error") or ""), 600)
        tool_payload = _compact_text(json.dumps((failed_tool or {}).get("input") or {}, ensure_ascii=True), 600)
        messages = [
            {
                "role": "system",
                "content": (
                    "You generate recovery suggestions for failed coding-agent runs. "
                    "Return strict JSON with keys summary and recovery_prompt. "
                    "Both fields must be short, concrete, and specific to the failure. "
                    "Do not suggest generic retries when the failure is an external blocker or the model stopped without making a tool call. "
                    "Do not include markdown fences."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Session model: {session.get('provider') or 'unknown'} / {session.get('model') or 'unknown'}\n"
                    f"Run category: {category}\n"
                    f"User goal: {user_goal or '(missing)'}\n"
                    f"Assistant intent: {assistant_intent or '(missing)'}\n"
                    f"Failed component: {failed_tool_name}\n"
                    f"Failed target: {target or '(none)'}\n"
                    f"Failed tool payload: {tool_payload or '(none)'}\n"
                    f"Error: {failed_tool_error or '(none)'}\n"
                    f"Recent events: {json.dumps(recent_events[-5:], ensure_ascii=True)}\n"
                    "Produce the best next-step suggestion for the UI."
                ),
            },
        ]

        parsed = self._parse_model_suggestion(result.text or "")
        if parsed is None:
            return None
        return parsed

    def _parse_model_suggestion(self, text: str) -> tuple[str, str] | None:
        candidate = text.strip()
        if not candidate:
            return None

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None

        summary = _compact_text(payload.get("summary") or "", 240)
        recovery_prompt = _compact_text(payload.get("recovery_prompt") or "", 600)
        if not summary or not recovery_prompt:
            return None
        return summary, recovery_prompt

    def _suggest_direction_fallback(
        self,
        category: str,
        failed_tool: dict[str, Any] | None,
        files_touched: list[str],
    ) -> tuple[str, str]:
        target = files_touched[-1] if files_touched else "the target file"
        tool_name = str((failed_tool or {}).get("tool_name") or "the last tool")
        detail = _tool_failure_detail(failed_tool).lower()
        if category == "tool_schema_error":
            return (
                "Retry with a smaller, schema-valid tool payload after re-reading the exact target.",
                f"Retry the last task. First re-read {target}, identify the exact edit point, and make one small patch only.",
            )
        if category == "tool_permission_error":
            return (
                "Adjust the tool allowlist or requested path access before retrying the same step.",
                f"Retry the task, but first verify the session tool policy and request access for {target} if needed.",
            )
        if category == "tool_timeout":
            return (
                "Retry with a narrower scope or a smaller target surface so the tool call can finish cleanly.",
                f"Retry the task with a narrower scope focused only on {target} and avoid broad edits.",
            )
        if "host key verification failed" in detail:
            return (
                "SSH host verification failed before the push could reach GitHub.",
                "Fix the SSH host-key trust issue for github.com first, then retry the same git push without changing commits or forcing history.",
            )
        if "permission denied (publickey)" in detail or "could not read from remote repository" in detail:
            return (
                "GitHub SSH authentication failed before the push could complete.",
                "Verify the SSH key and GitHub access for this remote, then retry the same git push without changing local commits.",
            )
        if category == "empty_completion":
            return (
                "Ask Clo to resume from the last visible state and explicitly produce either an answer or an artifact.",
                "Resume the previous run from the last successful step and produce a visible assistant response before stopping.",
            )
        return (
            f"Retry from the failed {tool_name} step after re-checking the latest target and making the smallest possible change.",
            f"Retry the task. First inspect {target}, confirm the exact failure around {tool_name}, then make one small targeted attempt.",
        )
