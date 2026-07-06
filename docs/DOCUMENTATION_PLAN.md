# OpenCloset Documentation Plan

> **Purpose**: Document the OpenCloset harness for general reference and Evan's understanding.  
> **Format**: Simple website with tabs/links, one section per topic.  
> **Workflow**: Generate docs one item at a time, check off as completed, context reset between items.

---

## Progress Tracker

| # | Section | Status |
|---|---------|--------|
| 1 | System Overview & Architecture | ☑ |
| 2 | Session Management | ☑ |
| 3 | Message System & Persistence | ☑ |
| 4 | Agent Turn Loop | ☑ |
| 5 | Token Pressure Guard | ☑ |
| 6 | Conversation Runtime | ☑ |
| 7 | Tool Registry & Permissions | ☑ |
| 8 | Filesystem Tools (read/write/edit) | ☑ |
| 9 | Memory Tools | ☑ |
| 10 | Provider Abstraction Layer | ☐ |
| 11 | Session Rollover & Handoff | ☐ |
| 12 | Watchdog Process | ☐ |
| 13 | Storage Layer (SQLite + JSONL) | ☐ |
| 14 | REST API Reference | ☐ |
| 15 | Workspace & Planning System | ☐ |
| 16 | Event Logging | ☐ |
| 17 | Configuration & Runtime Settings | ☐ |
| 18 | Capture System | ☐ |
| 19 | Maintenance & Compaction System | ☐ |
| 20 | Deployment & Local Setup | ☐ |
| 21 | Migration Notes (OpenClaw → OpenCloset) | ☐ |

---

## Documentation Rules

- **Authoritative codebase**: `opencloset/api/` (Flask, V2, stateful). All docs target V2 unless explicitly marked "Legacy V1".
- **Do not mix** V1 (`opencloset/app.py`) and V2 (`opencloset/api/api/app.py`).
- Session states: `active`, `paused`, `rolled-over`, `deleted`.
- Token pressure thresholds: percentage-based — warning 75%, critical 87%.
- CJK token estimate: **2 chars/token**.
- Real subsystems to document as current: `ConversationRuntime`, `AgentLoop`, streaming/tool dispatch, `PersistenceQueue`, `MessageKind`, `TokenPressureMonitor`, `SessionWatchdog`, workspace/build_project subsystem.
- Remove invented fields (e.g., `trigger_message_id`).

## 1. System Overview & Architecture

**Scope**: High-level architecture, design principles, component map.

**Key Points**:
- OpenCloset as a desktop-first local harness for Clo
- Designed by Evan + Clo + ChatGPT + Claude
- Core differentiators vs OpenClaw: explicit token management, local-first deployment, visible architecture
- Component map: Flask API → agent loop → provider → tools → storage
- Runtime: llama.cpp / Ollama backends, SQLite persistence, JSONL transcripts
- Explicit session/run/message/tool_invocation lifecycle tables
- Character-based token estimation vs OpenClaw's opaque counting
- Two implementations exist: V1 (`app.py`, FastAPI, legacy) and V2 (`api/api/app.py`, Flask, authoritative)

**Files to Reference**:
- `api/api/app.py` (Flask app factory, V2)
- `api/db/schema.py`
- Directory tree overview
- `app.py` (legacy V1, FastAPI — reference only)

---

## 2. Session Management

**Scope**: Session CRUD, lifecycle states, configuration, tool policy.

**Key Points**:
- Session creation: model, provider, context_window, workspace, build_project
- Status states: `active`, `paused`, `rolled-over`, `deleted`
- Tool policy per session: `enabled_tools`, `allow_destructive_tools`, `allowed_paths`
- Task budget: `task_budget_remaining` carried across rollover
- Rollover linkage: `rolled_over_to` FK to successor session
- Session endpoints: `POST /api/sessions`, `GET`, `DELETE`
- Tool policy endpoints: `GET/PATCH /api/sessions/<id>/tool-policy`

**Files to Reference**:
- `api/api/routes.py` (session routes, tool policy endpoints)
- `api/db/schema.py` (sessions table)
- `api/agent/engine.py` (ConversationRuntime)
- `api/agent/runner.py` (session tool policy composition)

---

## 3. Message System & Persistence

**Scope**: Normalized messages, persistence queue, blocking vs fire-and-forget writes.

**Key Points**:
- Message kinds: `TEXT`, `TOOL_USE`, `TOOL_RESULT`, `SYSTEM_EVENT`, `THINKING`
- Normalized `Message` dataclass with role, kind, content, token_estimate, persistent flag
- PersistenceQueue: order-preserving write queue
- Blocking writes: user/system messages (crash-safe, written before model call)
- Fire-and-forget writes: assistant messages (queued, flushed at end of run)
- Position tracking within session transcript
- Token accumulation per message

**Files to Reference**:
- `api/api/transcript_manager.py` (message persistence)
- `api/agent/engine.py` (Message, PersistenceQueue, MessageKind)
- `api/db/schema.py` (messages table)
- `api/agent/loop.py` (message chain assembly)

---

## 4. Agent Turn Loop

**Scope**: Explicit turn execution, streaming collection, tool dispatch, interruption handling.

**Key Points**:
- `execute_turn()` function drives the agent loop
- Max-turn budget (`max_turns: int = 20`) prevents infinite loops
- Token-aware continuation: checks pressure guard before each turn
- Streaming collection: `collect_streamed_response()` accumulates TEXT_DELTA, TOOL_USE, THINKING_DELTA, USAGE events
- Mid-stream tool detection: tools discovered from streaming, not post-hoc parse
- Tool dispatch loop: collect all tool calls → execute → inject results → continue
- Synthetic tool-result on interruption: preserves message-chain validity
- Turn state machine: prompt assembly → stream → collect tools → execute → loop until no tools
- Run lifecycle: `queued` → `running` → `succeeded`/`failed`/`interrupted`

**Files to Reference**:
- `api/agent/loop.py` (full agent loop, AgentLoop class)
- `api/agent/runner.py` (SessionAgentRunner, queued-run execution, compaction)
- `api/provider/base.py` (streaming interface)
- `api/agent/engine.py` (run lifecycle, ConversationRuntime)
- `api/api/run_lifecycle.py` (RunManager, run finalization)

---

## 5. Token Pressure Guard

**Scope**: Character-based token estimation, warning/critical thresholds, pluggable callbacks.

**Key Points**:
- `TokenPressureMonitor` class tracks estimated usage vs context window
- Character-based estimation: ~4 chars/token (EN), ~3 (code), ~2 (CJK)
- Warning threshold: 75% of context window
- Critical threshold: 87% of context window
- Pluggable callbacks: `on_warning`, `on_critical` for custom actions (e.g., trigger rollover)
- Token accounting: add per-message estimates, subtract on rollover
- Integration with agent loop: checked before each turn continuation
- Pressure percentage calculation: `usage_pct = (token_count / context_window) * 100`
- Tokens remaining: `context_window - token_count`

**Files to Reference**:
- `api/agent/guard.py` (TokenPressureMonitor)
- `api/agent/loop.py` (guard integration, threshold pause)
- `api/agent/runner.py` (compaction-aware token accounting)
- `api/agent/engine.py` (token_count tracking)

---

## 6. Conversation Runtime

**Scope**: Per-session engine, run lifecycle, message chain, interrupt/pause mechanisms.

**Key Points**:
- `ConversationRuntime`: one instance per active session
- Owns: in-memory message chain, run lifecycle state, token estimates, interrupt/pause flags
- Run lifecycle: `begin_run()` → execute → `end_run(status)` → `interrupt_run()`
- Synthetic interrupt results for in-progress tools
- Pause/resume signals from watchdog
- Message management: `add_message()`, `get_message_chain()`, `update_token_count()`
- Tool state tracking: `active_tools`, `register_tool_call()`, `complete_tool_call()`
- Rollover helpers: `mark_rolled_over(successor_id)`
- Snapshot capability for debugging/handoff generation

**Files to Reference**:
- `api/agent/engine.py` (full ConversationRuntime)
- `api/agent/loop.py` (runtime integration)
- `api/api/run_lifecycle.py` (RunManager, lifecycle authority)

---

## 7. Tool Registry & Permissions

**Scope**: Two-layer permission model, ToolContract, validation pipeline.

**Key Points**:
- `ToolRegistry`: manages tool registration, lookup, and assembly-time filtering
- `ToolContract`: name, description, input_schema, execute, validate_input, permission_check
- Two-layer permissions:
  1. **Assembly-time filtering**: `build_active_tools()` filters by `tool_allow` + `destructive_allow`
  2. **Invocation-time validation**: `check_permission()` runs tool-specific permission hook
- `PermissionDecision`: `ALLOW`, `ASK`, `DENY`
- `ValidationResult`: valid/invalid with error list
- Tool metadata: `read_only`, `destructive`, `concurrency_safe`, `categories`
- Dynamic tool injection: `register_many()`, `set_tool_allow()`, `set_destructive_allow()`

**Files to Reference**:
- `api/tools/registry.py` (ToolRegistry, ToolContract, PermissionDecision)
- `api/tools/normalizer.py` (ToolCallNormalizer — JSON repair + registry validation)
- `api/agent/loop.py` (tool assembly in loop)
- `api/api/routes.py` (tool policy management)

---

## 8. Filesystem Tools (read/write/edit)

**Scope**: Core file operations with path normalization, binary refusal, token-aware truncation.

**Key Points**:
- **read**: Text files only (binary refused), offset/limit support, 5MB size cap, 12500 char truncation (~3125 tokens)
- **write**: Creates parent dirs, 1MB content cap, UTF-8 encoding
- **edit**: Exact text replacement, non-overlapping regions, 50KB combined old+new cap
- Path normalization: resolve ~, expand variables, workspace-relative paths
- Permission check: `make_filesystem_permission_check()` constrains to `allowed_paths`
- Semantic validation: file exists, not directory, not binary, size within limits
- Tool flags: read (read_only, concurrency_safe), write (destructive), edit (destructive)
- Categories: `core`

**Files to Reference**:
- `api/tools/filesystem.py` (full implementation)
- `api/tools/registry.py` (ToolContract integration)

---

## 9. Memory Tools

**Scope**: memory_search, keyword retrieval, semantic reranking.

**Key Points**:
- `memory_search`: ranked keyword retrieval against session diary + daily logs
- Parameters: query, limit, include_daily, include_seen
- Search strategy: keyword + optional semantic reranking
- Mark seen: prevents redundant surfacing of same entries
- Integration with MemoryManager
- Tool flags: read_only, concurrency_safe
- Categories: `core`

**Files to Reference**:
- `api/tools/memory_tools.py` (memory_search implementation)
- `api/agent/loop.py` (memory tool registration)

---

## 10. Provider Abstraction Layer

**Scope**: Structured event streaming, mid-stream tool detection, ProviderResult accumulation.

**Key Points**:
- `ProviderEvent` enum: `TEXT_DELTA`, `TOOL_USE`, `THINKING_START`, `THINKING_DELTA`, `THINKING_END`, `USAGE`, `DONE`, `ERROR`
- `ProviderResult`: accumulated text, thinking, tool_calls, input/output tokens, finish_reason
- Streaming interface: `chat_stream(session_id, messages, max_tokens)` → event stream
- Event types: `EventType.TEXT`, `EventType.TOOL`, `EventType.THINKING`
- `StreamedToolCall` dataclass: id, name, input, raw
- Mid-stream tool detection: tools discovered during streaming, not post-hoc
- Provider backends: llama.cpp, Ollama (OpenAI-compatible)
- Error handling: structured error events with reason codes

**Files to Reference**:
- `api/provider/base.py` (ProviderEvent, ProviderResult, Provider interface)
- `api/agent/loop.py` (streaming collection)

---

## 11. Session Rollover & Handoff

**Scope**: Session handoff logic, successor creation, task budget inheritance, plan migration.

**Key Points**:
- `RolloverResult`: successor_id, summary, migrated_plans, remaining_budget
- Pre-checks: session must be active, not already rolled over, no active run
- Handoff plan generation: `app.planning.generate_handoff_plan(session_id)`
- Successor session creation: inherits model, provider, context_window, workspace, build_project, tool_policy
- Task budget inheritance: `task_budget_remaining` carried to successor
- Artifact linking: `HANDOFF_CANDIDATE_ARTIFACT` events bridge to successor
- FK linkage: `rolled_over_to` in sessions table
- Status transition: old session → `rolled-over`, new session → `active`
- `RolloverConflictError`: raised when pre-checks fail

**Files to Reference**:
- `api/api/rollover.py` (create_rollover_successor, shared rollover service)
- `api/api/routes.py` (rollover endpoint)
- `api/agent/runner.py` (rollover handoff injection on first successor run)
- `api/db/schema.py` (sessions table with rolled_over_to FK)
- `api/agent/engine.py` (mark_rolled_over)

---

## 12. Watchdog Process

**Scope**: Independent poller, threshold monitoring, rollover triggering.

**Key Points**:
- `SessionWatchdog`: polls active sessions every 30s (configurable)
- Checks planning state: `app.planning.should_rollover(session_id)`
- Skips if active run in progress
- Triggers rollover via `create_rollover_successor()`
- Standalone CLI: `python -m api.api.watchdog`
- Modes: `--once` (single poll) or `run_forever` (continuous)
- `WatchdogPollResult`: checked_sessions, triggered_rollovers
- Runs independently of Flask app (can be background process)

**Files to Reference**:
- `api/api/watchdog.py` (SessionWatchdog, main)
- `api/api/rollover.py` (rollover integration)

---

## 13. Storage Layer (SQLite + JSONL)

**Scope**: Database schema, indexes, WAL mode, transcript storage.

**Key Points**:
- SQLite database with WAL mode (`PRAGMA journal_mode=WAL`)
- Foreign keys enforced (`PRAGMA foreign_keys=ON`)
- Tables: sessions, runs, messages, tool_invocations, captures, workspaces, build_projects, workspace_evidence, workspace_pastimes, scheduler_jobs, plans, session_plan_state, plan_items, plan_revisions, maintenance_artifacts, agent_events, transcript_ranges
- Indexes for hot paths: messages(session_id, position), runs(session_id), tool_invocations(run_id), captures(status), captures(workspace_id), workspace_evidence(workspace_id, evidence_type), workspace_pastimes(workspace_id, status, priority)
- JSONL transcripts: separate from DB, for full-text search and replay
- Message position tracking: order-preserving within session
- Run states: queued → running → succeeded/failed/interrupted/rolled-over
- Tool invocation states: pending → running → completed/failed/interrupted
- Capture states: pending → routed → processed/failed
- Session states: active, paused, rolled-over, deleted
- Migration support: ALTER TABLE for schema evolution (e.g., workspace columns added via Phase 0D migration)

**Files to Reference**:
- `api/db/schema.py` (full schema, init_db, SCHEMA_SQL, migrations)
- `api/api/transcript_manager.py` (transcript authority)
- `api/api/event_logger.py` (lifecycle audit sink)

---

## 14. REST API Reference

**Scope**: All API endpoints, request/response schemas, error handling.

**Key Points**:
- **Sessions**:
  - `POST /api/sessions` — Create session (model, provider, context_window, workspace, tool_policy)
  - `GET /api/sessions` — List sessions (optional ?status= filter)
  - `GET /api/sessions/<id>` — Get session details + current run
  - `DELETE /api/sessions/<id>` — Delete session
- **Messages**:
  - `POST /api/sessions/<id>/messages` — Submit user message, queue run
- **Tool Policy**:
  - `GET /api/sessions/<id>/tool-policy` — Get session tool policy
  - `PATCH /api/sessions/<id>/tool-policy` — Update tool policy
- **Runs**:
  - `GET /api/sessions/<id>/runs` — List runs
  - `POST /api/sessions/<id>/runs/<run_id>/execute` — Execute queued run
  - `POST /api/sessions/<id>/runs/<run_id>/interrupt` — Interrupt run
- **Rollover**:
  - `POST /api/sessions/<id>/rollover` — Trigger rollover
- **Events**:
  - `GET /api/sessions/<id>/events` — Get session event log
- **Transcript**:
  - `GET /api/sessions/<id>/transcript-ranges` — Get transcript compaction ranges
  - `GET /api/sessions/<id>/transcript-message-states` — Get message compaction states
- **Artifacts**:
  - `GET /api/sessions/<id>/artifacts` — Get maintenance artifacts
- **Workspaces**:
  - `GET /api/workspaces` — List workspaces
  - `POST /api/workspaces` — Create workspace
  - `GET /api/workspaces/<id>` — Get workspace details
  - `PATCH /api/workspaces/<id>` — Update workspace
  - `DELETE /api/workspaces/<id>` — Delete workspace
  - `GET /api/workspaces/<id>/projects` — List build projects
  - `POST /api/workspaces/<id>/projects` — Create build project
- **Plans**:
  - `GET /api/sessions/<id>/plans` — List session plans
  - `POST /api/sessions/<id>/plans` — Create plan
  - `GET /api/sessions/<id>/plans/<plan_id>` — Get plan
  - `PATCH /api/sessions/<id>/plans/<plan_id>` — Update plan
  - `POST /api/sessions/<id>/plans/<plan_id>/activate` — Activate plan
  - `POST /api/sessions/<id>/plans/<plan_id>/items` — Create plan item
  - `GET /api/sessions/<id>/plans/<plan_id>/items` — List plan items
  - `PATCH /api/sessions/<id>/plans/<plan_id>/items/<item_id>` — Update plan item
- **Health**:
  - `GET /api/health` — Health check (status, uptime, provider, db)

**Files to Reference**:
- `api/api/routes.py` (all route handlers)
- `api/api/app.py` (Flask app factory)

---

## 15. Workspace & Planning System

**Scope**: Workspace grouping, plan hierarchy, goal tracking, session bootstrap.

**Key Points**:
- Workspaces: grouping container for related sessions/plans/projects (status: active, maintenance, dormant, archived)
- Build projects: projects within a workspace (status: planned, active, blocked, paused, completed, archived)
- Plan rolodex: `plans` + `session_plan_state` — one active plan per session
- Plan items: ordered `plan_items` with status, reorder, archive
- Plan revisions: snapshot-style `plan_revisions`
- Sessions bootstrap with an active plan automatically
- Plan creation/list/activation APIs
- `plan_activated` is a real durable event
- `next_item`: first non-done non-archived item, exposed in active plan view
- Prompt injection: includes `next_item` (bounded, not whole list)
- Rollover: successor inherits active plan state, items, and next action via `active_plan_id`
- Workspace pastimes: `workspace_pastimes` table — idle-work registry with priority, cooldown, compute_cost
- Workspace evidence: `workspace_evidence` — durable notes/links/proof points
- Scheduler jobs: `scheduler_jobs` — cron-like and one-shot scheduled work
- Session-level event listing at `/api/sessions/<id>/events`

**Files to Reference**:
- `api/workspaces/` (workspace manager)
- `api/planning/` (plan management)
- `api/db/schema.py` (workspaces, build_projects tables)

---

## 16. Event Logging

**Scope**: Structured event capture, session events, audit trail.

**Key Points**:
- `EventLogger`: captures structured events per session
- Shared typed stream event model (`api/api/events.py`) unified by V2
- Event types: session lifecycle, run lifecycle, stream events, tool events, planning events, rollover events
- Events stored per session: `get_session_events(session_id, limit)`
- `RunManager.emit_stream_event()`: shared queue + persistence seam
- `EventLogger.get_run_events()`: canonical replay of full run-scoped event sequence
- Endpoints: `GET /api/sessions/<id>/events`, `GET /api/sessions/<id>/runs/<run_id>/events`, `GET /api/sessions/<id>/runs/<run_id>/stream?replay=1`

**Files to Reference**:
- `api/api/events.py` (shared stream event model)
- `api/api/event_logger.py` (EventLogger)
- `api/api/run_lifecycle.py` (RunManager, emit_stream_event)
- `api/agent/loop.py` (loop event emissions)

---

## 17. Configuration & Runtime Settings

**Scope**: Config schema, runtime settings, provider configuration.

**Key Points**:
- Config schema: `config_schema.json` defines all settings
- Key settings:
  - `model.default`: default model ID
  - `model.context_window`: default context window
  - `provider.default`: default provider backend
  - `provider.llamacpp.endpoint`: llama.cpp server URL
  - `workspace.root`: workspace root path
  - `workspace.default_tool_allowlist`: default enabled tools
- Environment variables: `OPENCLOSET_DB_PATH`, `OPENCLOSET_CONFIG`
- Runtime settings: poll_interval, max_turns, token thresholds
- Tool policy defaults: read, write, edit, exec, process

**Files to Reference**:
- `api/api/app.py` (`create_app`, config loading)
- `config_schema.json` (if exists)
- Environment variables

---

## 18. Capture System

**Scope**: External event ingestion, PhoneCloset bridge, routing, processing.

**Key Points**:
- `Capture` table: stores external events before routing
- Sources: phonecloset, webhook, cli, manual
- Event types: text, image, audio, location, app_event
- Status flow: pending → routed → processed/failed
- Routing: unassigned captures (session_id=NULL) → assign to session → inject into run
- Processing: capture → normalized message → add to session transcript
- `PhoneCloset`: external bridge for mobile/smartphone events
- `CaptureProcessor`: background processor for pending captures

**Files to Reference**:
- `api/captures/` (capture manager, processor)
- `api/db/schema.py` (captures table)

---

## 19. Maintenance & Compaction System

**Scope**: SessionMaintenanceWorker, artifact types, compaction markers, prompt-time compaction, token-aware context guard.

**Key Points**:
- `SessionMaintenanceWorker`: poll-based background worker for idle sessions
- Artifact types: `micro-summary`, `segment-summary`, `handoff-candidate`, `decision-tool-digest`, `compaction-marker`
- Artifact lifecycle: valid → stale (older valid artifacts marked stale when new valid one created)
- `compaction-marker`: explicit durable boundary with `start_position`/`end_position`
- Prompt-time compaction: transcript messages covered by compaction marker are removed from prompt, replaced by maintenance artifacts
- Token-aware context guard bookkeeping: `tokens_used` = effective prompt pressure after compaction; `compaction_savings_tokens` tracks savings
- Maintenance injection order: `segment-summary` → `decision-tool-digest` → `micro-summary`
- Rollover prefers valid `handoff-candidate` artifact from source session
- Multi-range compaction: independent ranges tracked via `transcript_ranges` table and message states
- `SessionMaintenanceWorker.poll_once()` creates artifacts based on transcript position, tool activity, and compaction state

**Files to Reference**:
- `api/api/maintenance.py` (MaintenanceManager, SessionMaintenanceWorker)
- `api/agent/runner.py` (compaction integration in runner)
- `api/agent/prompt.py` (maintenance artifact injection in prompt)
- `api/db/schema.py` (maintenance_artifacts, transcript_ranges tables)

## 20. Deployment & Local Setup

**Scope**: Running OpenCloset locally, environment setup, provider configuration.

**Key Points**:
- Local-first deployment on home PC
- Prerequisites: Python 3.10+, llama.cpp/Ollama server, SQLite
- Startup: `python -m api.api.app` (V2 Flask entrypoint)
- Legacy: `python app.py` (V1 FastAPI, not authoritative)
- Provider setup:
  - llama.cpp: HTTP server on local port (e.g., :8080)
  - Ollama: local Ollama server
- Database: auto-created at `opencloset.db` in project root
- Watchdog: optional background process (`python -m api.api.watchdog`)
- Environment variables: `OPENCLOSET_DB_PATH`, `OPENCLOSET_CONFIG`
- Workspace: `D:\openclaw` (default)
- Security: local-only, no external exposure, path-scoped tool permissions

**Files to Reference**:
- `api/api/app.py` (Flask app factory, V2)
- `api/api/watchdog.py` (watchdog CLI)
- `README.md` (setup instructions)

---

## 21. Migration Notes (OpenClaw → OpenCloset)

**Scope**: Why migrate, what changed, lessons learned.

**Key Points**:
- **Motivation**: Token inefficiency in OpenClaw (65k context burned quickly), llama.cpp desync issues
- **OpenClaw issues**:
  - Opaque token counting (model reported vs actual)
  - Context guard relied on external watcher + built-in system
  - Rapid token consumption (65k exhausted in short conversations)
  - Desync between llama.cpp layer and OpenClaw tracking
- **OpenCloset improvements**:
  - Explicit character-based token estimation (visible, predictable)
  - Built-in rollover mechanism (no external watcher needed)
  - Structured run lifecycle states
  - Early persistence of user turns (crash-safe)
  - Fire-and-forget assistant writes (performance)
  - Two-layer tool permissions (assembly-time + invocation-time)
  - Provider abstraction layer (backend-agnostic)
  - Session planning system (handoff plans, budget inheritance)
- **Architecture philosophy**:
  - Explicit > implicit
  - Visible token tracking > opaque counting
  - Local-first > cloud-dependent
  - Structured state > buried state
- **Design influences**: Claude Code research, Clo + Evan collaboration, ChatGPT + Claude contributions

**Files to Reference**:
- `README.md` (migration context)
- OpenClaw docs (for comparison)
- `api/agent/guard.py` (token estimation improvements)
- `api/api/rollover.py` (built-in rollover vs external watcher)

---

## Notes

- Document one section at a time, check off when complete
- Context reset between sections to preserve token budget
- Target format: simple website with tabs/links for navigation
- Focus on clarity for both general understanding and Evan's deep reference
