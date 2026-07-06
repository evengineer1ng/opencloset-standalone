# 05 — Token Pressure Guard

## 1. Overview

The `TokenPressureMonitor` (`api/agent/loop.py`) prevents the agent loop from exceeding the model's context window. It runs at the start of each turn and triggers compaction when thresholds are breached.

---

## 2. Thresholds

Thresholds are **percentage-based** (ratio of used tokens to max context tokens):

| Level | Ratio | Action |
|---|---|---|
| Normal | `< 0.75` | No action |
| Warning | `≥ 0.75` | Log warning, consider compaction |
| Critical | `≥ 0.87` | Force compaction or abort run |

---

## 3. Token Estimation

Token counts are estimated from character counts:

| Language | Chars/Token |
|---|---|
| CJK (Chinese, Japanese, Korean) | 2 |
| English (Latin script) | 4 |

Estimation is performed by `TranscriptManager.get_token_pressure()`:
1. Iterates messages in context window.
2. For each message, detects script type (CJK vs Latin).
3. Applies appropriate chars/token ratio.
4. Sums total estimated tokens.
5. Returns `pressure_ratio = estimated_tokens / max_context_tokens`.

---

## 4. Monitor Check Flow

Called at the start of each turn in `AgentLoop`:

```python
def check(self, context_manager):
    pressure = context_manager.get_token_pressure()
    ratio = pressure.pressure_ratio

    if ratio >= CRITICAL_RATIO (0.87):
        emit LoopEvent(compaction, level="critical")
        trigger_compaction(context_manager)
        if compaction_failed:
            abort_run("Context window exceeded")
    elif ratio >= WARNING_RATIO (0.75):
        log warning
        emit LoopEvent(compaction, level="warning")
        consider_compaction(context_manager)
```

---

## 5. Compaction Process

Handled by `TranscriptManager.compact()` (`api/api/transcript_manager.py`):

### 5.1 Parameters

- `old_keep`: Number of earliest messages to preserve (e.g., system prompt, initial context).
- `new_keep`: Number of most recent messages to preserve (e.g., current conversation).
- Messages between `old_keep` and `len - new_keep` are candidates for summarization.

### 5.2 Steps

1. Select message range for compaction.
2. Serialize candidate messages into a summary request.
3. Send to provider for summarization (`ProviderAdapter.chat()`).
4. Replace candidate messages with a single `compaction_summary` message.
5. Persist `CompactionMarker`:
   - `start_idx` — first message index compacted
   - `end_idx` — last message index compacted
   - `summary` — generated summary text
   - `token_savings` — tokens saved by compaction
   - `timestamp` — when compaction occurred
6. Update `TranscriptManager` internal state.
7. Emit `LoopEvent(compaction)` with savings info.

### 5.3 Atomic Swap

`compact_and_replace()` performs the transcript swap atomically:
- Old messages are marked as compacted.
- Summary message is inserted.
- Persistence queue flushes changes.

---

## 6. CompactionConfig (`api/agent/maintenance_worker.py`)

```python
@dataclass
class CompactionConfig:
    max_tokens: int               # Max context window size
    warning_ratio: float = 0.75   # Warning threshold
    critical_ratio: float = 0.87  # Critical threshold
    min_messages_to_compact: int = 10  # Minimum messages before compacting
    compaction_window_size: int = 50   # Max messages to summarize at once
```

---

## 7. SessionMaintenanceWorker

Background thread per session (`api/agent/maintenance_worker.py`):

- **Purpose:** Proactively monitor token pressure and trigger compaction outside the agent loop.
- **Operation:**
  - Runs on a configurable interval.
  - Calls `TokenPressureMonitor.check()`.
  - If warning/critical thresholds breached, triggers `TranscriptManager.compact()`.
- **Lifecycle:** `start()` → runs background loop → `stop()` → drains and exits.
- **Integration:** Created by `ConversationRuntime` per session. Started when session becomes active, stopped on delete.

---

## 8. Pressure Data

`TokenPressureData` returned by `get_token_pressure()`:

```python
@dataclass
class TokenPressureData:
    used_tokens: int        # Estimated tokens used
    max_tokens: int         # Context window limit
    pressure_ratio: float   # used / max (0.0 - 1.0)
    message_count: int      # Messages in context window
```

---

## 9. Compaction Markers

Persisted in the `messages` table as kind=`compaction_summary`. Metadata stored in `CompactionMarker` objects within the transcript manager.

Markers allow:
- Tracking compaction history.
- Reconstructing full transcript (summary + preserved messages).
- Auditing token savings over time.
