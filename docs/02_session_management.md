# 02 — Session Management

## 1. Session Lifecycle

Sessions are the top-level entity in OpenCloset V2. Each session represents a conversation thread with its own configuration, transcript, and workspace.

**States:** `active` → `paused` → `rolled-over` → `deleted`

| State | Description |
|---|---|
| `active` | Session is live, accepting messages, loop can run |
| `paused` | Session exists but not processing (mid-compaction, maintenance) |
| `rolled-over` | Context was compacted/handed off; session ready for resume |
| `deleted` | Soft-deleted; data retained until cleanup |

### 1.1 Creation

`POST /api/sessions` creates a session with:
- Unique `session_id` (UUID)
- Default `SessionConfig` (model, provider key, loop params)
- State: `active`
- DB row in `sessions` table

### 1.2 Resume

`POST /api/sessions/<id>/resume` reactivates a session after rollover or pause:
- Re-initializes `ConversationRuntime` for the session
- Rebuilds transcript context from persisted messages
- Restores session to `active` state

### 1.3 Deletion

`DELETE /api/sessions/<id>` soft-deletes the session:
- State transitions to `deleted`
- Associated runs, messages, tool_calls remain until cleanup
- Session removed from active session map

---

## 2. SessionConfig

Held per-session inside `ConversationRuntime`. Controls agent behavior.

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | str | `"llamacpp/qwen3.6-27b"` | Model identifier |
| `provider_key` | str | `"llamacpp"` | Provider backend key |
| `max_tokens` | int | `512` | Max tokens per turn |
| `max_turns` | int | `6` | Max loop iterations per run |
| `temperature` | float | `0.2` | Sampling temperature |
| `max_tool_calls_per_turn` | int | `4` | Tool call limit per turn |
| `stop_seqs` | list[str] | `[]` | Stop sequences |
| `system_prompt` | str | _(default)_ | System-level instructions |

Config is updatable at runtime via `PUT /api/sessions/<id>/config`. Changes apply to subsequent turns.

---

## 3. Session Storage

Sessions persist to SQLite (`sessions` table):

| Column | Type | Description |
|---|---|---|
| `id` | TEXT (PK) | Session UUID |
| `state` | TEXT | `active` / `paused` / `rolled-over` / `deleted` |
| `config` | TEXT (JSON) | Serialized SessionConfig |
| `created_at` | TIMESTAMP | Creation time |
| `updated_at` | TIMESTAMP | Last update |
| `last_message_at` | TIMESTAMP | Last activity |
| `message_count` | INTEGER | Total message count |
| `total_tokens` | INTEGER | Cumulative token usage |

---

## 4. Session Routes

| Method | Path | Action |
|---|---|---|
| `POST` | `/api/sessions` | Create session |
| `GET` | `/api/sessions` | List all sessions |
| `GET` | `/api/sessions/<id>` | Get session details |
| `DELETE` | `/api/sessions/<id>` | Delete session |
| `POST` | `/api/sessions/<id>/resume` | Resume session |
| `PUT` | `/api/sessions/<id>/config` | Update config |

---

## 5. Runtime Session Map

`ConversationRuntime` maintains an in-memory session registry:

- `_sessions: dict[session_id, SessionState]` — active session runtimes
- `get_or_create_runtime(session_id)` — lazy initialization
- `remove_session(session_id)` — cleanup on delete

Session state in memory includes:
- `SessionConfig`
- `AgentLoop` instance (created on first turn)
- `TranscriptManager` instance
- `RunManager` instance
- `SessionMaintenanceWorker` (background thread)
