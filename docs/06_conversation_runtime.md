# 06 — Conversation Runtime

## 1. Overview

`ConversationRuntime` (`api/agent/engine.py`) is the per-session orchestrator. It owns the agent loop, run management, event logging, and maintenance worker. It is the central entry point for all session activity.

---

## 2. Creation & Lifecycle

### 2.1 Initialization

Created per-session via `create_app()` in `app.py`:

```python
runtime = ConversationRuntime(
    session_id=session_id,
    config=SessionConfig(...),
    transcript_manager=TranscriptManager(session_id),
    tool_registry=ToolRegistry(...),
    provider_factory=ProviderFactory(...)
)
```

### 2.2 Lifecycle

| Phase | Action |
|---|---|
| Created | Session initialized, runtime instantiated |
| Active | Processing messages, loop running |
| Paused | Mid-compaction or maintenance |
| Rolled over | Context compacted, handoff written |
| Deleted | Runtime cleaned up, resources released |

---

## 3. Core Components

### 3.1 SessionConfig

Held by runtime. Controls model, provider, loop parameters, system prompt.

### 3.2 AgentLoop

Created lazily on first user message. Drives LLM turns and tool dispatch.

### 3.3 RunManager

Tracks active runs: phase, events, token usage. Enables durable replay.

### 3.4 EventLogger

Durable event log per session. Supports SSE catch-up for late-connecting clients.

### 3.5 PersistenceQueue

Async message/event writer. Decouples loop speed from DB latency.

### 3.6 SessionMaintenanceWorker

Background thread for proactive compaction monitoring.

---

## 4. Message Processing

### 4.1 `process_user_message(session_id, text)`

Primary entry point for user input:

1. Validate session state is `active`.
2. Call `create_run(session_id, text)` → creates run row + system message.
3. Resolve provider and tool registry.
4. Call `agent_loop.run(session_id, text)`.
5. On completion: `finalize_run(run_id, status, token_usage)`.
6. Return response or stream via SSE.

### 4.2 `_process_message(session_id, message, kind)`

Internal handler for all message kinds:

| MessageKind | Handler |
|---|---|
| `user_message` | Route to agent loop |
| `tool_result` | Append to run context, continue loop |
| `system_directive` | Inject into context window |
| `pastime_event` | Route to agent loop as user message |
| `session_resumption` | Inject context, signal resume |

### 4.3 `_handle_tool_request(session_id, tool_call)`

Called by agent loop when model requests tool execution:

1. Look up tool in `ToolRegistry`.
2. Resolve permission (`always_ask` / `never_ask` / `always_allow` / `always_deny`).
3. If allowed: execute tool, capture result.
4. Feed result back via `_process_message(kind=tool_result)`.
5. Emit `LoopEvent(tool_result)`.

---

## 5. Queued Runs

### 5.1 `adopt_queued_run(run_id)`

Extends the agent loop to pick up a previously created run:

1. Look up run by `run_id` in DB.
2. Validate run status is `pending`.
3. Update run status to `running`.
4. Inject run message into agent loop context.
5. Call `agent_loop.run()` for the queued message.

### 5.2 Run Executor (`api/agent/runner.py`)

`RunExecutor` handles threaded execution of queued runs:

- `execute_run(session_id, run_id)` — resolves config, calls `runtime.adopt_queued_run()`.
- `_running` lock prevents concurrent runs per session.
- `abort_run(session_id, run_id)` — signals runtime to cancel.

Endpoint: `POST /api/sessions/<id>/runs/<run_id>/execute`

---

## 6. MessageKind Enum

```python
class MessageKind(Enum):
    user_message = "user_message"
    tool_result = "tool_result"
    system_directive = "system_directive"
    pastime_event = "pastime_event"
    session_resumption = "session_resumption"
```

| Kind | Source | Usage |
|---|---|---|
| `user_message` | Client POST | Primary input |
| `tool_result` | ToolRegistry | Tool output fed back to loop |
| `system_directive` | Runtime | Internal instructions (run start, compaction info) |
| `pastime_event` | Pastime system | Scheduled/bounded background events |
| `session_resumption` | Runtime | Context restoration on resume |

---

## 7. Streaming Integration

Runtime integrates with SSE streaming:

- Agent loop events → `EventLogger` → SSE event stream.
- `StreamingResponse` generator yields events in real-time.
- Late clients catch up via `EventLogger.get_events(session_id, since_seq)`.

---

## 8. Guard Integration

Content safety guard (`api/agent/guard.py`) is integrated into the runtime:

- `GuardConfig` — max_response_length, max_tool_calls, banned_patterns, allowed_tools.
- `check_response(text, tool_calls)` → `GuardResult(passed, violations, action)`.
- Applied after provider response, before tool dispatch.
- Actions: `allow` / `warn` / `block` / `truncate`.

---

## 9. Runtime Cleanup

On session deletion:

1. Stop `SessionMaintenanceWorker`.
2. Drain `PersistenceQueue` (`_flush()`).
3. Cancel active run if running.
4. Remove runtime from session map.
5. Update session state to `deleted`.
