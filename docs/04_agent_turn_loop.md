# 04 — Agent Turn Loop

## 1. Overview

The `AgentLoop` (`api/agent/loop.py`) is the core interaction engine. It drives LLM turns, handles tool calls, streams events, and monitors token pressure.

---

## 2. LoopConfig

```python
@dataclass
class LoopConfig:
    max_tokens: int = 512
    max_turns: int = 6
    temperature: float = 0.2
    max_tool_calls_per_turn: int = 4
    stop_seqs: list[str] = None
    context_manager: TranscriptManager = None
```

Lean defaults optimized for local LLM execution (llama.cpp). Configurable per-session via `SessionConfig`.

---

## 3. Turn Loop Flow

```
run(session_id, message)
  ├─ emit LoopEvent(run_started)
  ├─ create RunContext
  │
  FOR turn IN 1..max_turns:
  │  ├─ TokenPressureMonitor.check()
  │  │   ├─ pressure >= 0.87 → force compaction or abort
  │  │   └─ pressure >= 0.75 → log warning
  │  │
  │  ├─ emit LoopEvent(turn_started, turn_number)
  │  │
  │  ├─ _build_prompt(run_context)
  │  │   ├─ system prompt
  │  │   ├─ TranscriptManager.get_context_window()
  │  │   └─ plan section (build_plan_section)
  │  │
  │  ├─ _call_provider(prompt, tool_schemas)
  │  │   ├─ ProviderAdapter.chat_stream()
  │  │   ├─ yield text_delta events
  │  │   └─ collect full response text
  │  │
  │  ├─ parse tool calls from response
  │  │
  │  ├─ IF tool calls detected:
  │  │   ├─ FOR each tool_call:
  │  │   │   ├─ emit LoopEvent(tool_request, tool_call)
  │  │   │   ├─ ToolRegistry.execute(name, args)
  │  │   │   ├─ append tool_result to run_context
  │  │   │   └─ emit LoopEvent(tool_result, result)
  │  │   └─ CONTINUE to next turn
  │  │
  │  └─ ELSE (no tool calls):
  │      ├─ emit LoopEvent(turn_end)
  │      └─ BREAK loop
  │
  └─ emit LoopEvent(run_completed)
```

---

## 4. LoopEvents Enum

| Event | Meaning |
|---|---|
| `run_started` | Run began |
| `run_completed` | Run finished normally |
| `run_aborted` | Run cancelled |
| `turn_started` | New LLM turn |
| `turn_end` | Turn complete (no more tool calls) |
| `text_delta` | Streaming text chunk |
| `tool_request` | Tool call requested by model |
| `tool_result` | Tool execution result |
| `context_injected` | Context messages added |
| `system_injected` | System directive injected |
| `compaction` | Transcript compaction performed |
| `error` | Error during loop |
| `unknown` | Unrecognized event |

---

## 5. Prompt Assembly

`_build_prompt(run_context)` assembles the prompt sent to the provider:

1. **System prompt** — from `SessionConfig.system_prompt`
2. **Context window** — from `TranscriptManager.get_context_window()`
3. **Plan section** — from `build_plan_section()` (if plan exists for session)
4. **Tool schemas** — translated via `ProviderAdapter.translate_tool_schemas()`

Prompt assembly is handled by `api/agent/prompt.py`:
- `build_plan_section()` — formats active plan and slices into prompt text.
- `extract_plan_slice()` — extracts a specific slice for focused context.

---

## 6. Tool Dispatch

When the LLM response contains tool calls:

1. Loop parses `tool_calls` from provider response.
2. For each tool call:
   - Yields `LoopEvent(tool_request)` with tool name and arguments.
   - Calls `ToolRegistry.execute(tool_name, arguments, permission_context)`.
   - Tool result is appended to `run_context.messages` as a `tool_result` kind message.
   - Yields `LoopEvent(tool_result)` with output.
3. Loop continues to next turn with tool results in context.
4. Maximum tool calls per turn: `max_tool_calls_per_turn` (default 4).

---

## 7. RunContext

Holds mutable state for the duration of a single run:

```python
@dataclass
class RunContext:
    run_id: str
    session_id: str
    messages: list[dict]       # accumulated messages
    tool_calls: list[ToolCall] # tool calls this turn
    full_text: str             # accumulated response text
    turn_number: int           # current turn index
    started_at: datetime
```

---

## 8. Streaming Integration

Loop events are emitted in real-time and routed to:
- **SSE client** — via event generator in `StreamingResponse`
- **EventLogger** — durable log for replay
- **PersistenceQueue** — async DB write
- **RunManager** — run state tracking

---

## 9. Error Handling

- **Provider errors:** Caught in `_call_provider()`, yielded as `LoopEvent(error)`, run continues or aborts depending on severity.
- **Tool errors:** Caught in tool dispatch, result marked as error, loop continues to next turn.
- **Token pressure critical:** Loop forces compaction; if compaction fails, run aborts with `run_aborted` event.
