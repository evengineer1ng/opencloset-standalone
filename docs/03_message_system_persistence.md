# 03 — Message System & Persistence

## 1. Message Model

Messages are the atomic unit of conversation. Stored in the `messages` table.

### 1.1 MessageKind Enum

```python
class MessageKind(Enum):
    user_message = "user_message"
    tool_result = "tool_result"
    system_directive = "system_directive"
    pastime_event = "pastime_event"
    session_resumption = "session_resumption"
```

| Kind | Source | Purpose |
|---|---|---|
| `user_message` | Client | User input |
| `tool_result` | ToolRegistry | Tool execution output |
| `system_directive` | Runtime | Instructions injected by the system |
| `pastime_event` | Pastime system | Scheduled/triggered pastime events |
| `session_resumption` | Runtime | Context restored on session resume |

### 1.2 Message Data

Each message stores:
- `session_id` — parent session
- `run_id` — originating run (FK to `runs`)
- `kind` — MessageKind
- `content` — text content
- `tool_call_id` — linked tool call (if tool_result)
- `created_at` — timestamp
- `token_count` — estimated tokens

---

## 2. TranscriptManager (`api/api/transcript_manager.py`)

Manages the conversation transcript per session. Core responsibilities:

### 2.1 Context Window Building

`get_context_window(max_tokens=None)` builds a prompt-ready message list:
1. Loads all messages for the session from DB.
2. Applies token budget constraints.
3. If over budget, triggers compaction.
4. Returns ordered list of `TranscriptMessage` objects.

### 2.2 Message Retrieval

- `get_messages(run_id=None, kind=None, limit=None)` — filtered query
- `get_last_n(n)` — recent messages
- `get_message(msg_id)` — single message lookup

### 2.3 Compaction

`compact(old_keep, new_keep)`:
1. Selects messages between `old_keep` and `new_keep` indices for summarization.
2. Sends middle segment to provider for summary.
3. Replaces summarized messages with a single `compaction_summary` message.
4. Persists `CompactionMarker` (start_idx, end_idx, summary, token_savings, timestamp).

`compact_and_replace()` — atomic swap of transcript segment.

### 2.4 Token Estimation

- **CJK text:** 2 chars/token
- **English text:** 4 chars/token
- `get_token_pressure()` → returns `pressure_ratio` (0.0–1.0) = used_tokens / max_context_tokens

---

## 3. PersistenceQueue (`api/agent/pqueue.py`)

Thread-safe queue for durable message/event persistence.

- **Purpose:** Decouple agent loop speed from DB write latency.
- **Operation:**
  - Agent loop yields events → enqueued in `PersistenceQueue`.
  - Background writer flushes batches to SQLite.
  - On shutdown/drain: `_flush()` ensures all pending items are written.
- **Failure handling:** If DB write fails, items are retried or logged to error queue.

---

## 4. EventLogger (`api/agent/event_logger.py`)

Durable event log per session. Supports replay for SSE clients connecting mid-run.

- `log_event(session_id, event)` — append event to log
- `get_events(session_id, since_seq=None)` — retrieve events, optionally from sequence number
- `get_last_sequence(session_id)` — latest event sequence number
- Events stored in-memory with periodic flush to disk

---

## 5. RunManager (`api/agent/engine.py`)

Tracks active runs per session.

- **`RunState` dataclass:** `run_id`, `phase` (`initializing` / `running` / `completed` / `aborted`), `events: list[StreamEvent]`, `token_usage`, `started_at`, `finished_at`.
- `get_run(run_id)` → RunState
- `start_run(run_id)` → creates RunState, phase=running
- `complete_run(run_id, token_usage)` → phase=completed
- `abort_run(run_id)` → phase=aborted
- Durable replay: events logged via `EventLogger` allow SSE clients to catch up.

---

## 6. Run Lifecycle Functions (`api/api/run_lifecycle.py`)

### 6.1 `create_run(session_id, message)`

1. Validates session exists and is active.
2. Inserts run row (`status=pending`) into `runs` table.
3. Creates system directive message ("Run started for: <message>").
4. Persists via `PersistenceQueue`.
5. Returns `run_id`.

### 6.2 `finalize_run(run_id, status, token_usage)`

1. Updates run row: `status=completed` (or `aborted`), `finished_at=now`.
2. Logs final assistant message.
3. Records token usage.
4. Notifies `RunManager`.

### 6.3 `abort_run(run_id)`

1. Signals `ConversationRuntime` to cancel active loop.
2. Updates run status to `aborted`.
3. Records abort reason.

---

## 7. Database Tables (Message-Related)

| Table | Columns | Purpose |
|---|---|---|
| `messages` | id, session_id, run_id, kind, content, tool_call_id, created_at, token_count | Conversation messages |
| `runs` | id, session_id, status, created_at, started_at, finished_at, token_usage | Run lifecycle |
| `tool_calls` | id, run_id, name, input, output, status, created_at | Tool call records |
| `tool_runs` | id, tool_call_id, stdout, stderr, returncode, exit_code, created_at | Command execution results |
