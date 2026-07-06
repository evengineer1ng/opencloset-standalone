from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

from api.api.agent_harnesses import (
    AgentHarnessRegistry,
    BaseAgentDomainHarness,
    ChannelOutput,
    EventDirective,
    TRIAGE_PATCH_ONLY,
    TRIAGE_RECORD_ONLY,
)
from api.api.events import StreamEvent, stream_event_to_dict
from api.api.streaming import EventQueueStore, replay_sse_stream, sse_stream
from api.agent.runner import RunExecutionResult
from api.db.schema import new_id


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(current, value)
        else:
            merged[key] = value
    return merged


@dataclass(slots=True)
class EventTriage:
    action: str
    requires_llm: bool
    dashboard_patch: dict[str, Any] | None = None
    outputs: list[ChannelOutput] = field(default_factory=list)
    summary: str = ""


class ChannelStreamHub:
    def __init__(self, *, history_limit: int = 200) -> None:
        self.history_limit = history_limit
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, dict[str, queue.Queue]] = {}
        self._lock = threading.Lock()

    def publish(self, channel_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            history = self._history.setdefault(channel_id, [])
            history.append(event)
            if len(history) > self.history_limit:
                del history[:-self.history_limit]
            subscribers = list(self._subscribers.get(channel_id, {}).values())
        for subscriber_queue in subscribers:
            try:
                subscriber_queue.put_nowait(event)
            except queue.Full:
                continue

    def subscribe(self, channel_id: str) -> tuple[str, queue.Queue]:
        subscriber_id = new_id()
        subscriber_queue: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.setdefault(channel_id, {})[subscriber_id] = subscriber_queue
        return subscriber_id, subscriber_queue

    def unsubscribe(self, channel_id: str, subscriber_id: str) -> None:
        with self._lock:
            channel_subscribers = self._subscribers.get(channel_id)
            if not channel_subscribers:
                return
            channel_subscribers.pop(subscriber_id, None)
            if not channel_subscribers:
                self._subscribers.pop(channel_id, None)

    def replay(self, channel_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            history = list(self._history.get(channel_id, []))
        if limit <= 0:
            return history
        return history[-limit:]


class AgentChannelManager:
    def __init__(self, app: Flask, event_store: EventQueueStore, *, harness_registry: AgentHarnessRegistry | None = None) -> None:
        self.app = app
        self.db = app.db
        self.event_store = event_store
        self.harnesses = harness_registry or getattr(app, "agent_harnesses", AgentHarnessRegistry())
        self.stream_hub = ChannelStreamHub(
            history_limit=int(self.app.config.get("RUNTIME_STREAM_HISTORY_LIMIT", 200)),
        )
        self._lock = threading.Lock()
        self._run_channel_map: dict[str, str] = {}
        self.app.run_manager.register_stream_listener(self._handle_run_stream_event)

    def list_channels(
        self,
        *,
        status: str | None = None,
        session_id: str | None = None,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if domain:
            clauses.append("lower(domain) = ?")
            params.append(str(domain).strip().lower())

        query = "SELECT * FROM agent_channels"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC"
        rows = self.db.execute(query, tuple(params)).fetchall()
        return [self._channel_row_to_dict(row) for row in rows]

    def list_harnesses(self) -> list[dict[str, Any]]:
        return self.harnesses.list_harnesses()

    def get_channel(self, name: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM agent_channels WHERE name = ?", (name,)).fetchone()
        return self._channel_row_to_dict(row) if row else None

    def get_status(self, name: str) -> dict[str, Any] | None:
        channel = self.get_channel(name)
        if not channel:
            return None
        channel["recent_events"] = self.list_events(name, limit=10)
        channel["recent_outputs"] = self.list_outputs(name, limit=10)
        channel["jobs"] = self.list_channel_jobs(name)
        channel["active_run"] = self.app.run_manager.get_active_run(channel["session_id"])
        channel["harness"] = self.harnesses.describe(channel["domain"])
        channel["transports"] = {
            "sse": f"/api/runtime/agents/{channel['name']}/stream",
            "websocket": f"/api/runtime/agents/{channel['name']}/ws" if self.websocket_enabled() else None,
        }
        return channel

    def get_dashboard(self, name: str) -> dict[str, Any] | None:
        channel = self.get_channel(name)
        if not channel:
            return None
        events = self.list_events(name, limit=50)
        outputs = self.list_outputs(name, limit=100)
        dashboard = self._get_harness(channel).build_dashboard(channel, events=events, outputs=outputs)
        return {
            "channel": {
                "id": channel["id"],
                "name": channel["name"],
                "domain": channel["domain"],
                "mode": channel["mode"],
                "status": channel["status"],
                "active_objective": channel.get("active_objective") or "",
            },
            "dashboard": dashboard,
        }

    def websocket_enabled(self) -> bool:
        return bool(getattr(self.app, "runtime_sock", None))

    def start_channel(
        self,
        *,
        name: str,
        mode: str,
        domain: str,
        active_objective: str,
        metadata: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        provider: str = "auto",
        model: str | None = None,
        context_window: int = 65536,
        tool_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_channel(name)
        if existing and existing["status"] != "stopped":
            return existing

        metadata = dict(metadata or {})
        normalized_tool_policy = self._normalize_tool_policy(tool_policy)

        if existing and existing["status"] == "stopped":
            now = _now()
            self.db.execute(
                (
                    "UPDATE agent_channels SET status = 'active', mode = ?, domain = ?, active_objective = ?, tool_permissions = ?, "
                    "channel_metadata = ?, stopped_at = NULL, updated_at = ? WHERE id = ?"
                ),
                (
                    mode,
                    domain,
                    active_objective,
                    json.dumps(normalized_tool_policy),
                    json.dumps(metadata),
                    now,
                    existing["id"],
                ),
            )
            self.db.execute(
                "UPDATE sessions SET status = 'active', updated_at = ? WHERE id = ?",
                (now, existing["session_id"]),
            )
            self.db.commit()
            resumed = self.get_channel(name)
            if resumed:
                self._ensure_tick_job_for_channel(
                    resumed,
                    enabled=(mode == "ambient"),
                    payload=(metadata.get("tick_payload") if isinstance(metadata, dict) else None),
                )
                self._emit_channel_stream_event(existing["id"], StreamEvent(type="agent_channel_started", data={"channel": resumed}))
            return resumed or {}

        session_id = new_id()
        channel_id = new_id()
        session_label = f"Channel {name}"
        resolved_model = model or self._default_model(provider)

        self.db.execute(
            (
                "INSERT INTO sessions (id, label, model, provider, context_window, workspace_id, tool_policy) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                session_id,
                session_label,
                resolved_model,
                provider,
                context_window,
                workspace_id,
                json.dumps(normalized_tool_policy),
            ),
        )
        self.db.execute(
            (
                "INSERT INTO agent_channels (id, name, session_id, workspace_id, domain, mode, status, active_objective, tool_permissions, channel_metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)"
            ),
            (
                channel_id,
                name,
                session_id,
                workspace_id,
                domain,
                mode,
                active_objective,
                json.dumps(normalized_tool_policy),
                json.dumps(metadata),
            ),
        )
        self.db.commit()
        self.app.planning.bootstrap_session(session_id, workspace_id=workspace_id)

        channel = self.get_channel(name)
        if channel:
            self._ensure_tick_job_for_channel(
                channel,
                enabled=(mode == "ambient"),
                payload=(metadata.get("tick_payload") if isinstance(metadata, dict) else None),
            )
            self._emit_channel_stream_event(channel_id, StreamEvent(type="agent_channel_started", data={"channel": channel}))
        return channel or {}

    def stop_channel(self, name: str) -> dict[str, Any] | None:
        channel = self.get_channel(name)
        if not channel:
            return None
        now = _now()
        self.db.execute(
            "UPDATE agent_channels SET status = 'stopped', stopped_at = ?, updated_at = ? WHERE id = ?",
            (now, now, channel["id"]),
        )
        self.db.execute(
            "UPDATE sessions SET status = 'paused', updated_at = ? WHERE id = ?",
            (now, channel["session_id"]),
        )
        self.db.commit()
        self._ensure_tick_job_for_channel(channel, enabled=False)
        stopped = self.get_channel(name)
        if stopped:
            self._emit_channel_stream_event(channel["id"], StreamEvent(type="agent_channel_stopped", data={"channel": stopped}))
        return stopped

    def update_channel(
        self,
        name: str,
        *,
        mode: str | None = None,
        active_objective: str | None = None,
        metadata: dict[str, Any] | None = None,
        working_memory: dict[str, Any] | None = None,
        current_plan: dict[str, Any] | None = None,
        open_threads: list[dict[str, Any]] | None = None,
        dashboard_state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        channel = self.get_channel(name)
        if not channel:
            return None

        next_mode = str(mode or channel["mode"] or "ambient")
        next_objective = channel["active_objective"] if active_objective is None else active_objective
        next_metadata = dict(channel.get("metadata") or {})
        next_working_memory = dict(channel.get("working_memory") or {})
        next_current_plan = dict(channel.get("current_plan") or {})
        next_open_threads = list(channel.get("open_threads") or [])
        next_dashboard_state = dict(channel.get("dashboard_state") or {})

        if metadata is not None:
            next_metadata = _deep_merge_dict(next_metadata, metadata)
        if working_memory is not None:
            next_working_memory = _deep_merge_dict(next_working_memory, working_memory)
        if current_plan is not None:
            next_current_plan = _deep_merge_dict(next_current_plan, current_plan)
        if open_threads is not None:
            next_open_threads = list(open_threads)
        if dashboard_state is not None:
            next_dashboard_state = _deep_merge_dict(next_dashboard_state, dashboard_state)

        now = _now()
        self.db.execute(
            (
                "UPDATE agent_channels SET mode = ?, active_objective = ?, channel_metadata = ?, working_memory = ?, "
                "current_plan = ?, open_threads = ?, dashboard_state = ?, updated_at = ? WHERE id = ?"
            ),
            (
                next_mode,
                next_objective,
                json.dumps(next_metadata),
                json.dumps(next_working_memory),
                json.dumps(next_current_plan),
                json.dumps(next_open_threads),
                json.dumps(next_dashboard_state),
                now,
                channel["id"],
            ),
        )
        self.db.commit()

        updated = self.get_channel(name)
        if updated:
            self._emit_channel_stream_event(channel["id"], StreamEvent(type="agent_channel_updated", data={"channel": updated}))
        return updated

    def list_events(self, name: str, *, limit: int = 50) -> list[dict[str, Any]]:
        channel = self.get_channel(name)
        if not channel:
            return []
        rows = self.db.execute(
            "SELECT * FROM agent_channel_events WHERE channel_id = ? ORDER BY created_at DESC LIMIT ?",
            (channel["id"], limit),
        ).fetchall()
        return [self._event_row_to_dict(row) for row in rows]

    def list_outputs(self, name: str, *, limit: int = 50) -> list[dict[str, Any]]:
        channel = self.get_channel(name)
        if not channel:
            return []
        rows = self.db.execute(
            "SELECT * FROM agent_channel_outputs WHERE channel_id = ? ORDER BY created_at DESC LIMIT ?",
            (channel["id"], limit),
        ).fetchall()
        return [self._output_row_to_dict(row) for row in rows]

    def list_channel_jobs(self, name: str, *, limit: int = 20) -> list[dict[str, Any]]:
        channel = self.get_channel(name)
        if not channel or not hasattr(self.app, "scheduler_jobs"):
            return []
        return self.app.scheduler_jobs.list_jobs(
            session_id=channel["session_id"],
            job_type="agent_channel_tick",
            source="agent_channel",
            limit=limit,
        )

    def configure_schedule(
        self,
        name: str,
        *,
        enabled: bool = True,
        cooldown_seconds: int | None = None,
        next_run_at: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        channel = self.get_channel(name)
        if not channel:
            return None
        return self._ensure_tick_job_for_channel(
            channel,
            enabled=enabled,
            cooldown_seconds=cooldown_seconds,
            next_run_at=next_run_at,
            payload=payload,
        )

    def send_message(
        self,
        name: str,
        *,
        content: str,
        source: str = "user",
        priority: int = 50,
        sync: bool = False,
    ) -> dict[str, Any]:
        return self.ingest_event(
            name,
            event_type="user.message",
            source=source,
            payload={"content": content},
            text=content,
            priority=priority,
            sync=sync,
        )

    def ingest_event(
        self,
        name: str,
        *,
        event_type: str,
        source: str,
        payload: dict[str, Any] | None,
        text: str = "",
        priority: int = 0,
        sync: bool = False,
    ) -> dict[str, Any]:
        channel = self.get_channel(name)
        if not channel:
            raise KeyError(name)
        triage = self._triage_event(channel, event_type, payload or {}, text)
        event_id = new_id()
        self.db.execute(
            (
                "INSERT INTO agent_channel_events (id, channel_id, session_id, event_type, source, payload, text, priority, triage_action, requires_llm, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted')"
            ),
            (
                event_id,
                channel["id"],
                channel["session_id"],
                event_type,
                source,
                json.dumps(payload or {}),
                text,
                int(priority),
                triage.action,
                1 if triage.requires_llm else 0,
            ),
        )
        self.db.commit()

        stored_event = self.get_event(event_id)
        self._emit_channel_stream_event(channel["id"], StreamEvent(type="agent_channel_event", data={"event": stored_event}))

        if triage.dashboard_patch:
            self._store_output(
                channel_id=channel["id"],
                session_id=channel["session_id"],
                run_id=None,
                output_type="dashboard.patch",
                payload=triage.dashboard_patch,
            )
        for output in triage.outputs:
            self._store_output(
                channel_id=channel["id"],
                session_id=channel["session_id"],
                run_id=None,
                output_type=output.output_type,
                payload=output.payload,
            )

        if triage.requires_llm:
            self._queue_agent_turn(channel, stored_event, sync=sync)
            stored_event = self.get_event(event_id)
        elif triage.action in {TRIAGE_RECORD_ONLY, TRIAGE_PATCH_ONLY}:
            self.db.execute(
                "UPDATE agent_channel_events SET status = 'processed', processed_at = ? WHERE id = ?",
                (_now(), event_id),
            )
            self.db.commit()
            stored_event = self.get_event(event_id)

        return stored_event or {}

    def run_scheduled_tick(self, job: dict[str, Any]) -> dict[str, Any]:
        channel = self._get_channel_by_session(job.get("session_id"))
        if not channel:
            return {"skipped": "channel_not_found"}
        if channel.get("status") != "active":
            return {"skipped": "channel_inactive", "channel_name": channel.get("name")}
        if self.app.run_manager.get_active_run(channel["session_id"]):
            return {"skipped": "active_run", "channel_name": channel.get("name")}
        harness = self._get_harness(channel)
        tick_event = harness.build_tick_event(channel, job=job) or BaseAgentDomainHarness().build_tick_event(channel, job=job)
        if tick_event is None:
            return {"skipped": "tick_disabled", "channel_name": channel.get("name")}
        event = self.ingest_event(
            channel["name"],
            event_type=tick_event.event_type,
            source=tick_event.source,
            payload=tick_event.payload,
            text=tick_event.text,
            priority=tick_event.priority,
            sync=tick_event.sync,
        )
        return {
            "channel_name": channel["name"],
            "event_id": event.get("id"),
            "run_id": event.get("run_id"),
            "event_status": event.get("status"),
        }

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM agent_channel_events WHERE id = ?", (event_id,)).fetchone()
        return self._event_row_to_dict(row) if row else None

    def _queue_agent_turn(self, channel: dict[str, Any], event: dict[str, Any], *, sync: bool) -> str:
        run_id = new_id()
        turn_number = self.db.execute(
            "SELECT COALESCE(MAX(turn_number), 0) AS tn FROM runs WHERE session_id = ?",
            (channel["session_id"],),
        ).fetchone()["tn"] + 1
        max_turns = self.app.config.get("LOOP_MAX_TURNS")
        self.db.execute(
            "INSERT INTO runs (id, session_id, status, turn_number, max_turns) VALUES (?, ?, 'queued', ?, ?)",
            (run_id, channel["session_id"], turn_number, max_turns),
        )
        message_content = self._build_turn_prompt(channel, event)
        role = "user" if event["event_type"] == "user.message" else "system"
        self.app.transcript.submit_user_message(channel["session_id"], run_id, message_content, role=role)
        self.db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (channel["session_id"],),
        )
        self.db.execute(
            "UPDATE agent_channel_events SET run_id = ?, status = 'processing' WHERE id = ?",
            (run_id, event["id"]),
        )
        self.db.commit()

        with self._lock:
            self._run_channel_map[run_id] = channel["id"]

        if sync:
            self._execute_run(channel, event, run_id)
        else:
            thread = threading.Thread(
                target=self._execute_run,
                args=(channel, event, run_id),
                daemon=True,
                name=f"agent-channel-{channel['name']}-{run_id[:8]}",
            )
            thread.start()
        return run_id

    def _execute_run(self, channel: dict[str, Any], event: dict[str, Any], run_id: str) -> None:
        self._emit_channel_stream_event(
            channel["id"],
            StreamEvent(type="agent_channel_run_started", data={"run_id": run_id, "event_id": event["id"]}),
        )
        try:
            result = self.app.execution_runtime.execute_run(channel["session_id"], run_id)
        except Exception as exc:
            refreshed = self.app.run_manager.get_run(run_id)
            if refreshed and refreshed["status"] in {"queued", "running"}:
                self.app.run_manager.fail_run(run_id, str(exc) or type(exc).__name__)
            self.db.execute(
                "UPDATE agent_channel_events SET status = 'failed', processed_at = ? WHERE id = ?",
                (_now(), event["id"]),
            )
            self.db.commit()
            self._store_output(
                channel_id=channel["id"],
                session_id=channel["session_id"],
                run_id=run_id,
                output_type="alert",
                payload={"message": str(exc) or type(exc).__name__, "severity": "error"},
            )
            return

        self._finalize_agent_result(channel, event, run_id, result)

    def _finalize_agent_result(
        self,
        channel: dict[str, Any],
        event: dict[str, Any],
        run_id: str,
        result: RunExecutionResult,
    ) -> None:
        final_text = result.final_text or result.text or ""
        now = _now()
        self.db.execute(
            "UPDATE agent_channel_events SET status = 'processed', processed_at = ? WHERE id = ?",
            (now, event["id"]),
        )
        self.db.execute(
            "UPDATE agent_channels SET last_agent_decision = ?, updated_at = ? WHERE id = ?",
            (final_text, now, channel["id"]),
        )
        self.db.commit()

        if final_text:
            self._store_output(
                channel_id=channel["id"],
                session_id=channel["session_id"],
                run_id=run_id,
                output_type="message",
                payload={
                    "text": final_text,
                    "finish_reason": result.finish_reason,
                    "provider_route": result.provider_route,
                },
            )

        self._store_output(
            channel_id=channel["id"],
            session_id=channel["session_id"],
            run_id=run_id,
            output_type="decision",
            payload={
                "summary": final_text[:280],
                "event_type": event["event_type"],
                "run_id": run_id,
            },
        )
        for output in self._get_harness(channel).adapt_result_outputs(channel, event=event, result=result):
            self._store_output(
                channel_id=channel["id"],
                session_id=channel["session_id"],
                run_id=run_id,
                output_type=output.output_type,
                payload=output.payload,
            )
        self._emit_channel_stream_event(
            channel["id"],
            StreamEvent(type="agent_channel_run_completed", data={"run_id": run_id, "status": result.status}),
        )

    def _handle_run_stream_event(self, run_id: str, event_dict: dict[str, Any], run: dict[str, Any] | None) -> None:
        with self._lock:
            channel_id = self._run_channel_map.get(run_id)
        if not channel_id:
            return
        self._emit_channel_stream_event(channel_id, event_dict)
        event_type = str(event_dict.get("type") or "")
        if event_type in {"assistant_final", "tool_result", "error", "done"}:
            session_id = run["session_id"] if run else ""
            self._store_output(
                channel_id=channel_id,
                session_id=session_id,
                run_id=run_id,
                output_type=event_type,
                payload=event_dict.get("data", {}),
            )

    def _store_output(
        self,
        *,
        channel_id: str,
        session_id: str,
        run_id: str | None,
        output_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        output_id = new_id()
        self.db.execute(
            "INSERT INTO agent_channel_outputs (id, channel_id, session_id, run_id, output_type, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (output_id, channel_id, session_id, run_id, output_type, json.dumps(payload)),
        )
        if output_type == "dashboard.patch":
            self.db.execute(
                "UPDATE agent_channels SET dashboard_state = ?, updated_at = ? WHERE id = ?",
                (json.dumps(payload), _now(), channel_id),
            )
        self.db.commit()
        row = self.db.execute("SELECT * FROM agent_channel_outputs WHERE id = ?", (output_id,)).fetchone()
        output = self._output_row_to_dict(row)
        self._emit_channel_stream_event(channel_id, StreamEvent(type=output_type, data={"output": output}))
        return output

    def _emit_channel_stream_event(self, channel_id: str, event: StreamEvent | dict[str, Any]) -> None:
        event_dict = stream_event_to_dict(event)
        self.event_store.enqueue(channel_id, event_dict)
        self.stream_hub.publish(channel_id, event_dict)

    def _build_turn_prompt(self, channel: dict[str, Any], event: dict[str, Any]) -> str:
        payload_text = json.dumps(event["payload"], indent=2, sort_keys=True) if event["payload"] else "{}"
        if event["event_type"] == "user.message":
            return event["text"]
        base_prompt = (
            f"Headless runtime event for channel '{channel['name']}' in domain '{channel['domain']}'.\n"
            f"Objective: {channel['active_objective'] or 'none'}\n"
            f"Event type: {event['event_type']}\n"
            f"Source: {event['source']}\n"
            f"Priority: {event['priority']}\n"
            f"Text: {event['text'] or '(none)'}\n"
            f"Payload:\n{payload_text}\n\n"
            "Respond with the next best action, concise reasoning, and any structured dashboard patch needed."
        )
        custom_prompt = self._get_harness(channel).build_turn_prompt(channel, event=event, base_prompt=base_prompt)
        return custom_prompt or base_prompt

    def _default_model(self, provider: str) -> str:
        if provider == "openai":
            return str(self.app.config.get("OPENAI_MODEL") or "gpt-4.1-mini")
        row = self.db.execute(
            "SELECT model_name FROM providers WHERE enabled = 1 ORDER BY CASE WHEN id = 'llamacpp' THEN 0 WHEN id = 'ollama' THEN 1 WHEN id = 'openai' THEN 2 ELSE 3 END LIMIT 1"
        ).fetchone()
        if row and row["model_name"]:
            return row["model_name"]
        return "qwen3.6-27b"

    def _normalize_tool_policy(self, tool_policy: dict[str, Any] | None) -> dict[str, Any]:
        if tool_policy is None:
            return {
                "enabled_tools": list(self.app.config.get("DEFAULT_TOOL_ALLOWLIST", [])),
                "allow_destructive_tools": [],
                "allowed_paths": [],
            }
        return dict(tool_policy)

    def _triage_event(self, channel: dict[str, Any], event_type: str, payload: dict[str, Any], text: str) -> EventTriage:
        harness = self._get_harness(channel)
        directive = harness.classify_event(channel, event_type=event_type, payload=payload, text=text)
        if directive is None:
            directive = EventDirective(action=TRIAGE_RECORD_ONLY, requires_llm=False, summary=text)
        return EventTriage(
            action=directive.action or TRIAGE_RECORD_ONLY,
            requires_llm=bool(directive.requires_llm),
            dashboard_patch=directive.dashboard_patch,
            outputs=list(directive.outputs),
            summary=directive.summary or text,
        )

    def _get_harness(self, channel: dict[str, Any]):
        return self.harnesses.get(channel.get("domain"))

    def _get_channel_by_session(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        row = self.db.execute("SELECT * FROM agent_channels WHERE session_id = ?", (session_id,)).fetchone()
        return self._channel_row_to_dict(row) if row else None

    def _ensure_tick_job_for_channel(
        self,
        channel: dict[str, Any],
        *,
        enabled: bool,
        cooldown_seconds: int | None = None,
        next_run_at: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not hasattr(self.app, "scheduler_jobs"):
            return None
        jobs = self.app.scheduler_jobs.list_jobs(
            session_id=channel["session_id"],
            job_type="agent_channel_tick",
            source="agent_channel",
            limit=5,
        )
        if not enabled:
            for job in jobs:
                self.app.scheduler_jobs.disable_job(job["id"])
            return jobs[0] if jobs else None

        cooldown = int(
            cooldown_seconds
            if cooldown_seconds is not None
            else self.app.config.get("AGENT_CHANNEL_TICK_INTERVAL_SECONDS", 300)
        )
        metadata = {
            "channel_id": channel["id"],
            "channel_name": channel["name"],
            "domain": channel["domain"],
            "payload": payload or {},
        }
        resolved_next_run = next_run_at or (_now() if cooldown <= 0 else self._future_timestamp(cooldown))

        if jobs:
            job_id = jobs[0]["id"]
            now = _now()
            self.db.execute(
                """UPDATE scheduler_jobs
                   SET workspace_id = ?, session_id = ?, source = 'agent_channel', schedule = ?, cooldown_seconds = ?,
                       next_run_at = ?, status = 'scheduled', metadata = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    channel["workspace_id"],
                    channel["session_id"],
                    f"every:{cooldown}s",
                    cooldown,
                    resolved_next_run,
                    json.dumps(metadata),
                    now,
                    job_id,
                ),
            )
            self.db.commit()
            return self.app.scheduler_jobs.get_job(job_id)

        return self.app.scheduler_jobs.create_job(
            job_type="agent_channel_tick",
            workspace_id=channel["workspace_id"],
            session_id=channel["session_id"],
            source="agent_channel",
            schedule=f"every:{cooldown}s",
            cooldown_seconds=cooldown,
            next_run_at=resolved_next_run,
            metadata=metadata,
        )

    def _future_timestamp(self, seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _channel_row_to_dict(self, row) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            "id": row["id"],
            "name": row["name"],
            "session_id": row["session_id"],
            "workspace_id": row["workspace_id"],
            "domain": row["domain"],
            "mode": row["mode"],
            "status": row["status"],
            "active_objective": row["active_objective"],
            "tool_permissions": json.loads(row["tool_permissions"] or "{}"),
            "metadata": json.loads(row["channel_metadata"] or "{}"),
            "working_memory": json.loads(row["working_memory"] or "{}"),
            "current_plan": json.loads(row["current_plan"] or "{}"),
            "dashboard_state": json.loads(row["dashboard_state"] or "{}"),
            "last_agent_decision": row["last_agent_decision"],
            "open_threads": json.loads(row["open_threads"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "stopped_at": row["stopped_at"],
        }

    def _event_row_to_dict(self, row) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            "id": row["id"],
            "channel_id": row["channel_id"],
            "session_id": row["session_id"],
            "event_type": row["event_type"],
            "source": row["source"],
            "payload": json.loads(row["payload"] or "{}"),
            "text": row["text"],
            "priority": row["priority"],
            "triage_action": row["triage_action"],
            "requires_llm": bool(row["requires_llm"]),
            "status": row["status"],
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "processed_at": row["processed_at"],
        }

    def _output_row_to_dict(self, row) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            "id": row["id"],
            "channel_id": row["channel_id"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "output_type": row["output_type"],
            "payload": json.loads(row["payload"] or "{}"),
            "created_at": row["created_at"],
        }


def register_agent_channel_routes(app: Flask) -> None:
    manager = app.agent_channels

    @app.route("/api/runtime/harnesses", methods=["GET"])
    def list_runtime_harnesses():
        return jsonify({"harnesses": manager.list_harnesses()})

    @app.route("/api/runtime/agents", methods=["GET"])
    def list_agent_channels():
        status = request.args.get("status")
        session_id = request.args.get("session_id")
        domain = request.args.get("domain")
        return jsonify({"channels": manager.list_channels(status=status, session_id=session_id, domain=domain)})

    @app.route("/api/runtime/agents", methods=["POST"])
    def start_agent_channel():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        channel = manager.start_channel(
            name=name,
            mode=str(data.get("mode") or "ambient"),
            domain=str(data.get("domain") or "general"),
            active_objective=str(data.get("active_objective") or data.get("objective") or "").strip(),
            metadata=data.get("metadata") or {},
            workspace_id=data.get("workspace_id"),
            provider=str(data.get("provider") or "auto"),
            model=data.get("model"),
            context_window=int(data.get("context_window") or 65536),
            tool_policy=data.get("tool_policy"),
        )
        if data.get("schedule"):
            schedule = data.get("schedule") or {}
            manager.configure_schedule(
                name,
                enabled=bool(schedule.get("enabled", True)),
                cooldown_seconds=(int(schedule["cooldown_seconds"]) if "cooldown_seconds" in schedule else None),
                next_run_at=schedule.get("next_run_at"),
                payload=schedule.get("payload"),
            )
            channel = manager.get_status(name) or channel
        return jsonify(channel), 201

    @app.route("/api/runtime/agents/<name>", methods=["GET"])
    def get_agent_channel(name: str):
        status = manager.get_status(name)
        if not status:
            return jsonify({"error": "agent channel not found"}), 404
        return jsonify(status)

    @app.route("/api/runtime/agents/<name>/dashboard", methods=["GET"])
    def get_agent_channel_dashboard(name: str):
        dashboard = manager.get_dashboard(name)
        if not dashboard:
            return jsonify({"error": "agent channel not found"}), 404
        return jsonify(dashboard)

    @app.route("/api/runtime/agents/<name>/frame", methods=["GET"])
    def get_agent_channel_frame(name: str):
        dashboard = manager.get_dashboard(name)
        if not dashboard:
            return jsonify({"error": "agent channel not found"}), 404

        frame = (((dashboard.get("dashboard") or {}).get("panels") or {}).get("frame_capture") or {})
        frame_path = str(frame.get("path") or "").strip()
        if not frame_path:
            return jsonify({"error": "frame not available"}), 404

        workspace_root = Path(app.config.get("WORKSPACE_ROOT") or ".").resolve()
        artifacts_root = (workspace_root / "artifacts").resolve()
        candidate = Path(frame_path)
        resolved = (candidate if candidate.is_absolute() else workspace_root / candidate).resolve()

        try:
            resolved.relative_to(artifacts_root)
        except ValueError:
            return jsonify({"error": "frame path is outside artifacts"}), 400

        if not resolved.is_file():
            return jsonify({"error": "frame not found"}), 404

        return send_file(str(resolved), mimetype="image/png")

    @app.route("/runtime/agents/<name>/dashboard", methods=["GET"])
    def render_agent_channel_dashboard(name: str):
        channel = manager.get_channel(name)
        if not channel:
            return jsonify({"error": "agent channel not found"}), 404
        return render_template(
            "eve_dashboard.html",
            channel_name=name,
            channel_domain=channel.get("domain") or "general",
            dashboard_api_path=f"/api/runtime/agents/{name}/dashboard",
            events_api_path=f"/api/runtime/agents/{name}/events?limit=20",
            outputs_api_path=f"/api/runtime/agents/{name}/outputs?limit=20",
            stream_path=f"/api/runtime/agents/{name}/stream",
        )

    @app.route("/api/runtime/agents/<name>/send", methods=["POST"])
    def send_agent_channel_message(name: str):
        data = request.get_json(silent=True) or {}
        content = str(data.get("content") or "").strip()
        if not content:
            return jsonify({"error": "content is required"}), 400
        try:
            event = manager.send_message(
                name,
                content=content,
                source=str(data.get("source") or "user"),
                priority=int(data.get("priority") or 50),
                sync=bool(data.get("sync", False)),
            )
        except KeyError:
            return jsonify({"error": "agent channel not found"}), 404
        return jsonify(event), 201

    @app.route("/api/runtime/agents/<name>/events", methods=["POST"])
    def ingest_agent_channel_event(name: str):
        data = request.get_json(silent=True) or {}
        event_type = str(data.get("type") or data.get("event_type") or "").strip()
        if not event_type:
            return jsonify({"error": "type is required"}), 400
        try:
            event = manager.ingest_event(
                name,
                event_type=event_type,
                source=str(data.get("source") or "external"),
                payload=data.get("payload") or {},
                text=str(data.get("text") or ""),
                priority=int(data.get("priority") or 0),
                sync=bool(data.get("sync", False)),
            )
        except KeyError:
            return jsonify({"error": "agent channel not found"}), 404
        return jsonify(event), 201

    @app.route("/api/runtime/agents/<name>/events", methods=["GET"])
    def list_agent_channel_events(name: str):
        if not manager.get_channel(name):
            return jsonify({"error": "agent channel not found"}), 404
        limit = request.args.get("limit", 50, type=int)
        return jsonify({"events": manager.list_events(name, limit=limit)})

    @app.route("/api/runtime/agents/<name>/outputs", methods=["GET"])
    def list_agent_channel_outputs(name: str):
        if not manager.get_channel(name):
            return jsonify({"error": "agent channel not found"}), 404
        limit = request.args.get("limit", 50, type=int)
        return jsonify({"outputs": manager.list_outputs(name, limit=limit)})

    @app.route("/api/runtime/agents/<name>/schedule", methods=["GET"])
    def get_agent_channel_schedule(name: str):
        if not manager.get_channel(name):
            return jsonify({"error": "agent channel not found"}), 404
        return jsonify({"jobs": manager.list_channel_jobs(name)})

    @app.route("/api/runtime/agents/<name>/schedule", methods=["POST"])
    def configure_agent_channel_schedule(name: str):
        data = request.get_json(silent=True) or {}
        job = manager.configure_schedule(
            name,
            enabled=bool(data.get("enabled", True)),
            cooldown_seconds=(int(data["cooldown_seconds"]) if "cooldown_seconds" in data else None),
            next_run_at=data.get("next_run_at"),
            payload=data.get("payload"),
        )
        if job is None and not manager.get_channel(name):
            return jsonify({"error": "agent channel not found"}), 404
        return jsonify({"job": job, "jobs": manager.list_channel_jobs(name)})

    @app.route("/api/runtime/agents/<name>/stop", methods=["POST"])
    def stop_agent_channel(name: str):
        channel = manager.stop_channel(name)
        if not channel:
            return jsonify({"error": "agent channel not found"}), 404
        return jsonify(channel)

    @app.route("/api/runtime/agents/<name>/stream", methods=["GET"])
    def stream_agent_channel(name: str):
        channel = manager.get_channel(name)
        if not channel:
            return jsonify({"error": "agent channel not found"}), 404
        replay = request.args.get("replay", "0") in {"1", "true", "yes"}
        if replay:
            events = manager.stream_hub.replay(channel["id"], limit=request.args.get("limit", 200, type=int))
            return Response(
                stream_with_context(replay_sse_stream(events)),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        manager.event_store.get_queue(channel["id"])
        return Response(
            stream_with_context(sse_stream(channel["id"], manager.event_store)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    if getattr(app, "runtime_sock", None) is not None:

        @app.runtime_sock.route("/api/runtime/agents/<name>/ws")
        def stream_agent_channel_websocket(ws, name: str):
            channel = manager.get_channel(name)
            if not channel:
                ws.send(json.dumps({"type": "error", "data": {"message": "agent channel not found"}}))
                return
            replay = request.args.get("replay", "0") in {"1", "true", "yes"}
            if replay:
                for event in manager.stream_hub.replay(channel["id"], limit=request.args.get("limit", 200, type=int)):
                    ws.send(json.dumps(event))
            subscriber_id, subscriber_queue = manager.stream_hub.subscribe(channel["id"])
            try:
                while True:
                    try:
                        event = subscriber_queue.get(timeout=5)
                    except queue.Empty:
                        continue
                    ws.send(json.dumps(event))
            except Exception:
                return
            finally:
                manager.stream_hub.unsubscribe(channel["id"], subscriber_id)
