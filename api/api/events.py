from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Canonical event type constants
#
# All non-foreground and lifecycle events should use these constants so every
# part of the system emits consistent, searchable event types.
# ---------------------------------------------------------------------------

# Run / provider lifecycle
EVENT_USER_MESSAGE = "user_message"
EVENT_ASSISTANT_DELTA = "assistant_delta"
EVENT_ASSISTANT_FINAL = "assistant_final"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_PROVIDER_NOTICE = "provider_notice"
EVENT_PROVIDER_STREAM_TIMEOUT = "provider_stream_timeout"
EVENT_SUBPROCESS_KILLED = "subprocess_killed"
EVENT_TOOL_FAILURE_PIVOT = "tool_failure_pivot"
EVENT_PENDING_ACTION_GIVEUP = "pending_action_giveup"
EVENT_ACTION_PROGRESS_BLOCKED = "action_progress_blocked"
EVENT_PROMPT_UNANSWERED = "prompt_unanswered"
EVENT_REPEATED_INTENT_BLOCKED = "repeated_intent_blocked"
EVENT_INTERRUPT = "interrupt"

# Memory and context
EVENT_MEMORY_INJECTION = "memory_injection"
EVENT_ROLLOVER_HANDOFF = "rollover_handoff"

# Planning and workspaces
EVENT_PLAN_ACTIVATED = "plan_activated"
EVENT_WORKSPACE_ACTIVATED = "workspace_activated"
EVENT_PROPOSAL_CREATED = "proposal_created"

# Ambient workers and scheduler
EVENT_SCHEDULER_EVENT = "scheduler_event"
EVENT_WORKER_SIGNAL = "worker_signal"
EVENT_WORKER_REPORT = "worker_report"

# Pastimes and reflection
EVENT_PASTIME_STARTED = "pastime_started"
EVENT_PASTIME_COMPLETED = "pastime_completed"
EVENT_REFLECTION_NOTE = "reflection_note"
EVENT_THREAD_CANDIDATE = "thread_candidate"

# Capture and bridge
EVENT_BRIDGE_CAPTURE = "bridge_capture"


# ---------------------------------------------------------------------------
# StreamEvent — typed payload for run streaming and SSE replay
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StreamEvent:
    """Typed event payload for run streaming and replay."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}

    # -- Run / provider streaming --

    @classmethod
    def text_delta(cls, text: str) -> "StreamEvent":
        return cls(type=EVENT_ASSISTANT_DELTA, data={"text": text})

    @classmethod
    def assistant_final(
        cls,
        *,
        status: str,
        finish_reason: str,
        final_text: str,
        transient_text: str,
        transcript_persisted: bool,
    ) -> "StreamEvent":
        return cls(
            type=EVENT_ASSISTANT_FINAL,
            data={
                "status": status,
                "finish_reason": finish_reason,
                "final_text": final_text,
                "transient_text": transient_text,
                "transcript_persisted": transcript_persisted,
            },
        )

    @classmethod
    def tool_use(cls, tool_name: str, input_data: dict[str, Any]) -> "StreamEvent":
        return cls(type=EVENT_TOOL_CALL, data={"tool_name": tool_name, "input": input_data})

    @classmethod
    def tool_result(
        cls,
        tool_id: str,
        tool_name: str,
        status: str,
        content: str = "",
        error: str | None = None,
        error_code: str | None = None,
    ) -> "StreamEvent":
        data: dict[str, Any] = {
            "tool_id": tool_id,
            "tool_name": tool_name,
            "status": status,
            "content": content,
        }
        if error:
            data["error"] = error
        if error_code:
            data["error_code"] = error_code
        return cls(type=EVENT_TOOL_RESULT, data=data)

    @classmethod
    def thinking_delta(cls, text: str) -> "StreamEvent":
        return cls(type="thinking_delta", data={"text": text})

    @classmethod
    def usage(cls, input_tokens: int, output_tokens: int) -> "StreamEvent":
        return cls(
            type="usage",
            data={"input_tokens": input_tokens, "output_tokens": output_tokens},
        )

    @classmethod
    def done(cls, status: str = "succeeded") -> "StreamEvent":
        return cls(type="done", data={"status": status})

    @classmethod
    def error(cls, message: str, code: str | None = None) -> "StreamEvent":
        data: dict[str, Any] = {"message": message}
        if code:
            data["code"] = code
        return cls(type="error", data=data)

    @classmethod
    def interrupted(cls) -> "StreamEvent":
        return cls(type=EVENT_INTERRUPT, data={})

    @classmethod
    def provider_stream_timeout(
        cls,
        elapsed_s: float,
        threshold_s: float,
        last_event_type: str = "",
    ) -> "StreamEvent":
        return cls(
            type=EVENT_PROVIDER_STREAM_TIMEOUT,
            data={
                "elapsed_s": elapsed_s,
                "threshold_s": threshold_s,
                "last_event_type": last_event_type,
            },
        )

    @classmethod
    def subprocess_killed(
        cls,
        session_id: str,
        pid: int,
        command: str,
        reason: str,
    ) -> "StreamEvent":
        return cls(
            type=EVENT_SUBPROCESS_KILLED,
            data={
                "session_id": session_id,
                "pid": pid,
                "command": command,
                "reason": reason,
            },
        )

    @classmethod
    def tool_failure_pivot(
        cls,
        tool_name: str,
        repeated_pattern: str,
        attempt_count: int,
        pivot_hint: str,
    ) -> "StreamEvent":
        return cls(
            type=EVENT_TOOL_FAILURE_PIVOT,
            data={
                "tool_name": tool_name,
                "repeated_pattern": repeated_pattern,
                "attempt_count": attempt_count,
                "pivot_hint": pivot_hint,
            },
        )

    @classmethod
    def pending_action_giveup(cls, retries: int, last_text: str) -> "StreamEvent":
        return cls(
            type=EVENT_PENDING_ACTION_GIVEUP,
            data={
                "retries": retries,
                "last_text": last_text,
            },
        )

    @classmethod
    def repeated_intent_blocked(
        cls,
        *,
        message: str,
        intent_signature: str,
        repeat_count: int,
        last_text: str,
        next_required_action: str,
    ) -> "StreamEvent":
        return cls(
            type=EVENT_REPEATED_INTENT_BLOCKED,
            data={
                "message": message,
                "intent_signature": intent_signature,
                "repeat_count": repeat_count,
                "last_text": last_text,
                "next_required_action": next_required_action,
            },
        )

    @classmethod
    def action_progress_blocked(
        cls,
        *,
        message: str,
        discovery_steps: int,
        discovery_budget: int,
        files_read: int,
        symbols_found: int,
        files_modified: int,
        tests_run: int,
        artifacts_created: int,
        evidence: list[str],
        next_required_action: str,
    ) -> "StreamEvent":
        return cls(
            type=EVENT_ACTION_PROGRESS_BLOCKED,
            data={
                "message": message,
                "discovery_steps": discovery_steps,
                "discovery_budget": discovery_budget,
                "files_read": files_read,
                "symbols_found": symbols_found,
                "files_modified": files_modified,
                "tests_run": tests_run,
                "artifacts_created": artifacts_created,
                "evidence": evidence,
                "next_required_action": next_required_action,
            },
        )

    @classmethod
    def prompt_unanswered(
        cls,
        *,
        message: str,
        reason: str,
        last_user_prompt: str,
        assistant_text: str,
        action_mode: bool,
        files_modified: int,
        tests_run: int,
        artifacts_created: int,
        next_required_action: str,
    ) -> "StreamEvent":
        return cls(
            type=EVENT_PROMPT_UNANSWERED,
            data={
                "message": message,
                "reason": reason,
                "last_user_prompt": last_user_prompt,
                "assistant_text": assistant_text,
                "action_mode": action_mode,
                "files_modified": files_modified,
                "tests_run": tests_run,
                "artifacts_created": artifacts_created,
                "next_required_action": next_required_action,
            },
        )

    @classmethod
    def provider_notice(cls, message: str, detail: str = "") -> "StreamEvent":
        return cls(type=EVENT_PROVIDER_NOTICE, data={"message": message, "detail": detail})

    # -- Memory and context --

    @classmethod
    def memory_injection(cls, entry_ids: list[str], source: str = "retrieval") -> "StreamEvent":
        return cls(type=EVENT_MEMORY_INJECTION, data={"entry_ids": entry_ids, "source": source})

    @classmethod
    def rollover_handoff(cls, source_session_id: str, successor_session_id: str) -> "StreamEvent":
        return cls(
            type=EVENT_ROLLOVER_HANDOFF,
            data={"source_session_id": source_session_id, "successor_session_id": successor_session_id},
        )

    # -- Planning and workspaces --

    @classmethod
    def plan_activated(cls, plan_id: str, plan_title: str = "") -> "StreamEvent":
        return cls(type=EVENT_PLAN_ACTIVATED, data={"plan_id": plan_id, "plan_title": plan_title})

    @classmethod
    def workspace_activated(cls, workspace_id: str, workspace_name: str = "") -> "StreamEvent":
        return cls(type=EVENT_WORKSPACE_ACTIVATED, data={"workspace_id": workspace_id, "workspace_name": workspace_name})

    @classmethod
    def proposal_created(cls, proposal_id: str, proposal_type: str = "") -> "StreamEvent":
        return cls(type=EVENT_PROPOSAL_CREATED, data={"proposal_id": proposal_id, "proposal_type": proposal_type})

    # -- Ambient workers and scheduler --

    @classmethod
    def scheduler_event(cls, candidate_id: str, candidate_type: str, workspace_id: str) -> "StreamEvent":
        return cls(
            type=EVENT_SCHEDULER_EVENT,
            data={"candidate_id": candidate_id, "candidate_type": candidate_type, "workspace_id": workspace_id},
        )

    @classmethod
    def worker_signal(cls, signal_id: str, signal_type: str, worker_name: str, workspace_id: str) -> "StreamEvent":
        return cls(
            type=EVENT_WORKER_SIGNAL,
            data={"signal_id": signal_id, "signal_type": signal_type, "worker_name": worker_name, "workspace_id": workspace_id},
        )

    @classmethod
    def worker_report(cls, worker_name: str, workspace_id: str, summary: str, payload: dict[str, Any] | None = None) -> "StreamEvent":
        return cls(
            type=EVENT_WORKER_REPORT,
            data={"worker_name": worker_name, "workspace_id": workspace_id, "summary": summary, **(payload or {})},
        )

    # -- Pastimes --

    @classmethod
    def pastime_started(cls, pastime_id: str, pastime_key: str, workspace_id: str) -> "StreamEvent":
        return cls(
            type=EVENT_PASTIME_STARTED,
            data={"pastime_id": pastime_id, "pastime_key": pastime_key, "workspace_id": workspace_id},
        )

    @classmethod
    def pastime_completed(cls, pastime_id: str, pastime_key: str, workspace_id: str, produced_output: bool = False) -> "StreamEvent":
        return cls(
            type=EVENT_PASTIME_COMPLETED,
            data={
                "pastime_id": pastime_id,
                "pastime_key": pastime_key,
                "workspace_id": workspace_id,
                "produced_output": produced_output,
            },
        )

    @classmethod
    def reflection_note(cls, note: str, scope: str = "session", workspace_id: str = "") -> "StreamEvent":
        return cls(type=EVENT_REFLECTION_NOTE, data={"note": note, "scope": scope, "workspace_id": workspace_id})

    @classmethod
    def thread_candidate(cls, title: str, thread_type: str, scope: str, workspace_id: str) -> "StreamEvent":
        return cls(
            type=EVENT_THREAD_CANDIDATE,
            data={"title": title, "thread_type": thread_type, "scope": scope, "workspace_id": workspace_id},
        )

    # -- Bridge / capture --

    @classmethod
    def bridge_capture(cls, capture_id: str, source: str, event_type: str) -> "StreamEvent":
        return cls(
            type=EVENT_BRIDGE_CAPTURE,
            data={"capture_id": capture_id, "source": source, "event_type": event_type},
        )


def stream_event_to_dict(event: StreamEvent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, StreamEvent):
        return event.to_dict()
    return {
        "type": event.get("type", "message"),
        "data": event.get("data", {}),
    }
