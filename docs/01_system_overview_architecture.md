# 01 — System Overview & Architecture

## 1. Identity

OpenCloset V2 is a stateful conversational agent runtime. It accepts user messages, runs an LLM-driven agent loop with tool execution, persists a full message/tool-call transcript per session, and exposes the conversation via REST endpoints and Server-Sent Events (SSE) streaming.

**Authoritative implementation:** `opencloset/api/api/app.py` (Flask).  
**Legacy V1:** `opencloset/app.py` (FastAPI, stateless) — not documented unless explicitly labeled "Legacy V1".

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Flask App Layer                         │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │   Routes     │  │ Streaming (SSE)  │  │ Transcript Mgr  │  │
│  │  (app.py)    │  │   (sse.py)       │  │ (transcript_    │  │
│  │              │  │                  │  │  manager.py)    │  │
│  └──────┬───────┘  └────────┬─────────┘  └────────┬────────┘  │
│         │                   │                     │            │
│         ▼                   ▼                     ▼            │
│  ┌────────────────────────────────────────────────────────┐    │
│  │              ConversationRuntime                       │    │
│  │              (engine.py)                               │    │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────────┐  │    │
│  │  │ AgentLoop  │  │RunManager  │  │PersistenceQueue │  │    │
│  │  │(loop.py)   │  │(engine.py) │  │  (pqueue.py)    │  │    │
│  │  └──────┬─────┘  └─────┬──────┘  └────────┬────────┘  │    │
│  │         │              │                   │            │    │
│  │         ▼              ▼                   ▼            │    │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────────┐  │    │
│  │  │ProviderAd  │  │TokenPress. │  │SessionWatchdog  │  │    │
│  │  │(provider/) │  │Monitor     │  │(maint_worker.py)│  │    │
│  │  └────────────┘  └────────────┘  └─────────────────┘  │    │
│  └────────────────────────┬───────────────────────────────┘    │
│                           │                                    │
│                           ▼                                    │
│  ┌────────────────────────────────────────────────────────┐    │
│  │              SQLite Persistence                         │    │
│  │              (db/schema.py + db/session.py)             │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Subsystems

### 3.1 Flask Application (`api/api/app.py`)

- **Factory:** `create_app(config)` initializes DB, config, and registers route groups.
- **Route groups:**
  - `init_session_routes()` — session CRUD, resume, delete, config update.
  - `init_run_routes()` — run creation, status, abort, queued-run execute.
  - `init_streaming_routes()` — SSE event stream per session.
  - `init_health_routes()` — `/health` + `/health/config`.
  - `init_config_routes()` — runtime config introspection (`/api/config`).
- **Streaming:** `create_sse_stream()` wraps a generator into a `StreamingResponse` with `text/event-stream` content type. Uses `format_sse_event()` to emit typed events.
- **TranscriptManager:** Injected per-session via `create_transcript_manager(session_id)`. Provides context-window building, message retrieval, and compaction.

### 3.2 ConversationRuntime (`api/agent/engine.py`)

The central orchestrator per session. Responsibilities:
- Owns an `AgentLoop` instance (created lazily on first turn).
- Manages `SessionConfig` (model, provider key, max tokens/turns, temperature, tool-call limits).
- Runs user messages through the loop: `process_user_message(session_id, text)`.
- Handles tool requests: `_handle_tool_request()` dispatches to `ToolRegistry.execute()`, then feeds results back via `_process_message()`.
- Manages queued runs: `adopt_queued_run(run_id)` extends the loop to pick up API-scheduled runs.
- Maintains a `RunManager` for durable event replay.
- Owns a `SessionMaintenanceWorker` for background compaction.

### 3.3 AgentLoop (`api/agent/loop.py`)

The turn-by-turn LLM interaction engine.

- **`LoopConfig`** — max_tokens, max_turns, temperature, max_tool_calls_per_turn, stop_seqs, context_manager ref.
- **Turn loop:** Iterates up to `max_turns`. Each turn:
  1. Builds prompt via `_build_prompt()` (system prompt + transcript context + plan section).
  2. Calls provider via `_call_provider()` (supports streaming and non-streaming).
  3. Accumulates text deltas.
  4. Detects tool calls in the response.
  5. If tool calls present: yields `tool_request` events, loops back to step 1 with tool results appended.
  6. If no tool calls: yields `turn_end`, exits.
- **Event spine:** Every significant step emits a `LoopEvent` (kind, data, timestamp). Events stream to the SSE client and log to `RunManager`.
- **TokenPressureMonitor:** Tracks context-window usage. Triggers compaction at warning (75%) and critical (87%) thresholds based on `context_manager.get_token_pressure()`.
- **LoopEvents enum:** `run_started`, `run_completed`, `run_aborted`, `turn_started`, `turn_end`, `text_delta`, `tool_request`, `tool_result`, `context_injected`, `system_injected`, `compaction`, `error`, `unknown`.

### 3.4 Provider Layer (`api/provider/base.py` + implementations)

- **`ProviderAdapter`** (ABC): `chat()`, `chat_stream()`, `translate_tool_schemas()`, `normalize_response()`.
- **`ProviderEventType`** enum: `start_turn`, `end_turn`, `text_delta`, `tool_call`, `tool_result`, `session_info`, `compaction_info`, `error`, `unknown`.
- **`ProviderResponse`** dataclass: `text`, `tool_calls: list[ToolCall]`, `event_type`, `session_info`, `compaction_info`, `error`.
- **`ToolCall`** dataclass: `id`, `name`, `arguments: dict`.
- **Implementations:**
  - `openai_provider.py` — OpenAI-compatible API (Chat Completions).
  - `llamacpp_provider.py` — Local llama.cpp server (127.0.0.1:8080). Bounded `read` allowlist, tool schema translation.
  - `openrouter_provider.py` — OpenRouter API gateway.
- **`ProviderFactory.create_provider(key, model)`** — resolves provider by key string (e.g., `llamacpp/qwen3.6-27b`).

### 3.5 Tool System (`api/tools/`)

- **`ToolRegistry`** (`tools/registry.py`):
  - `register_tool(name, fn, schema, description, permissions)` — adds tool to catalog.
  - `execute(tool_name, arguments, permission_context)` — resolves permission, calls handler.
  - `resolve_permission(decision, context)` — evaluates `PermissionDecision` (always_ask / never_ask / always_allow / always_deny).
  - `list_tools()` / `get_tool_schema()` — introspection.
- **`PermissionDecision`** enum: `always_ask`, `never_ask`, `always_allow`, `always_deny`.
- **Tool categories:**
  - **Workspace** (`tools/workspace_tools.py`) — `read_file`, `list_directory`, `write_file`, `edit_file`, `run_command`. Bounded read allowlist, write confirmations, command safety.
  - **Evidence** (`tools/memory_tools.py`) — `capture_evidence`, `capture_screenshot`, `semantic_memory_search`, `add_semantic_memory`, `capture_code_context`.
  - **Planning** (`tools/planning_tools.py`) — `create_plan`, `create_slice`, `update_slice`, `checklist`, `build_project`, `workspace_status`, `list_workspace`.
  - **Pastime** (`tools/pastime_tools.py`) — pastime registry and event management.

### 3.6 Run Lifecycle (`api/api/run_lifecycle.py`)

- **`create_run(session_id, message)`** — inserts run row (status=pending), creates system message, persists to DB.
- **`finalize_run(run_id, status, token_usage)`** — updates run status, sets finished_at, logs final message.
- **`abort_run(run_id)`** — transitions run to aborted, records final state.

### 3.7 Run Executor (`api/agent/runner.py`)

- **`RunExecutor`**: threaded execution with `_running` lock.
- **`execute_run(session_id, run_id)`**: resolves session config, locates queued run, calls `runtime.adopt_queued_run(run_id)`, adopts into current loop.
- **Endpoint:** `POST /api/sessions/<session_id>/runs/<run_id>/execute` — triggers queued-run execution.
- **`abort_run(session_id, run_id)`** — signals runtime to cancel active run.

### 3.8 Streaming (`api/api/sse.py` + `api/agent/stream.py`)

- **SSE format:** `event: <kind>\ndata: <json>\nid: <seq>\n\n`
- **StreamEvent** dataclass: `event_type`, `session_id`, `run_id`, `data: dict`, `timestamp`.
- **Event kinds:** `message`, `tool_call`, `tool_result`, `status`, `error`, `done`, `compaction`, `context_injection`, `system_injection`.
- **`EventLogger`** (`api/agent/event_logger.py`) — durable event log per session; supports replay via `get_events(session_id, since_seq)`.
- **`RunManager`** (`api/agent/engine.py`) — maps `run_id → RunState`; tracks events, token usage, phase (initializing/running/completed/aborted).

### 3.9 Token Pressure & Compaction

- **`TokenPressureMonitor`** (`api/agent/loop.py`):
  - Queries `context_manager.get_token_pressure()` each turn.
  - `pressure_ratio` = used_tokens / max_context_tokens (0.0–1.0).
  - **Warning threshold:** 0.75 (75%) — logs warning, considers compaction.
  - **Critical threshold:** 0.87 (87%) — forces compaction or aborts run.
  - **CJK token estimate:** 2 chars/token.
  - **English token estimate:** 4 chars/token.
- **Compaction** (`api/api/transcript_manager.py`):
  - `compact(old_keep, new_keep)` — summarizes middle messages via provider, replaces with summary messages.
  - `compact_and_replace()` — atomically swaps transcript segment.
  - Compaction markers persisted as `CompactionMarker` (start_idx, end_idx, summary, token_savings, timestamp).

### 3.10 Session Maintenance (`api/agent/maintenance_worker.py`)

- **`SessionMaintenanceWorker`** — background thread per session.
- **`CompactionConfig`** — max_tokens, warning_ratio (0.75), critical_ratio (0.87), min_messages_to_compact (10), compaction_window_size (50).
- Checks token pressure on interval; triggers compaction when warning/critical thresholds breach.
- Lifecycle: `start()` / `stop()` / `check()`.

---

## 4. Session States

| State | Meaning |
|---|---|
| `active` | Session is live, accepting messages |
| `paused` | Session exists but not processing (e.g., mid-compaction) |
| `rolled-over` | Session context was compacted/handed off; ready for resume |
| `deleted` | Session marked for removal (soft delete) |

---

## 5. REST API Endpoints (V2)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/sessions` | Create session |
| `GET` | `/api/sessions` | List sessions |
| `GET` | `/api/sessions/<id>` | Get session |
| `DELETE` | `/api/sessions/<id>` | Delete session |
| `POST` | `/api/sessions/<id>/resume` | Resume session |
| `PUT` | `/api/sessions/<id>/config` | Update session config |
| `POST` | `/api/sessions/<id>/messages` | Send message (sync) |
| `POST` | `/api/sessions/<id>/runs` | Create queued run |
| `GET` | `/api/sessions/<id>/runs/<run_id>` | Get run status |
| `POST` | `/api/sessions/<id>/runs/<run_id>/execute` | Execute queued run |
| `POST` | `/api/sessions/<id>/runs/<run_id>/abort` | Abort run |
| `GET` | `/api/sessions/<id>/stream` | SSE event stream |
| `POST` | `/api/sessions/<id>/compact` | Trigger compaction |
| `GET` | `/api/sessions/<id>/transcript` | Get transcript context |
| `GET` | `/api/workspace` | List workspace projects |
| `GET` | `/api/workspace/<project>` | Get project status |
| `POST` | `/api/workspace/<project>/build` | Trigger project build |
| `GET` | `/health` | Health check |
| `GET` | `/health/config` | Health + config |
| `GET` | `/api/config` | Runtime config |

---

## 6. Database Schema (SQLite)

| Table | Purpose |
|---|---|
| `sessions` | Session metadata, config, state |
| `runs` | Run lifecycle (pending/running/completed/aborted) |
| `messages` | Conversation messages (kind: user, assistant, tool_result, system, compaction_summary) |
| `tool_calls` | Tool call records (name, input, output, status) |
| `tool_runs` | Tool execution results (stdout, stderr, returncode, exit_code) |
| `pastime_registry` | Registered pastimes (module, entry_point, permissions, schedule) |
| `pastime_events` | Pastime-triggered events |
| `captures` | Evidence captures (path, hash, metadata) |
| `evidence` | Structured evidence records |
| `memory_entries` | Semantic memory entries (text, embedding, tags, source) |
| `plan_slices` | Plan and slice tracking (status, progress, dependencies) |

---

## 7. Message Flow (User Message → Response)

```
Client POST /api/sessions/<id>/messages
  → Flask route handles request
    → ConversationRuntime.process_user_message()
      → create_run() (DB: run row + system message)
      → AgentLoop.run()
        → TokenPressureMonitor.check()
        → _build_prompt() → TranscriptManager.get_context_window()
        → _call_provider() → ProviderAdapter.chat_stream()
        → Text deltas accumulated
        → Tool calls detected?
          → Yes: ToolRegistry.execute() → results appended → next turn
          → No: Turn complete
      → finalize_run() (DB: run status + token usage)
    → Response returned (or streamed via SSE)
```

---

## 8. Key Design Decisions

- **Stateful over stateless:** V2 persists full transcript in SQLite; context is rebuilt from DB on resume.
- **Flask over FastAPI:** Flask factory pattern for simpler dependency injection and SSE streaming.
- **Event spine:** Every step emits a typed event for durable replay and SSE streaming.
- **Queued runs:** Runs can be created (POST /runs) and executed later (POST /runs/<id>/execute), enabling scheduled and background work.
- **Provider abstraction:** `ProviderAdapter` ABC allows swapping LLM backends (OpenAI, llama.cpp, OpenRouter) without changing loop logic.
- **Token pressure is percentage-based:** Warning at 75%, critical at 87% of max context window.
- **Compaction is prompt-time:** Context window is built by `TranscriptManager`; compaction summarizes middle messages to stay within limits.
