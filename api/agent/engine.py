# ConversationRuntime — per-session agent engine
#
# One instance per active session. Owns:
# - In-memory message chain (normalized events)
# - Run lifecycle state
# - Token estimates per message and running total
# - Interrupt / pause flags
# - Task budget tracking across rollover
#
# Design principles (from Claude Code research):
# - Explicit runtime per session, not buried in routes
# - Fire-and-forget persistence queue for assistant messages
# - Blocking persistence for user/system messages
# - Early persistence of user turns before model execution
# - Structured run states: queued → running → succeeded/failed/interrupted

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from api.db.schema import (
    init_db,
    new_id,
    MESSAGE_ROLES,
    RUN_STATUSES,
    SESSION_STATUSES,
)


# ---------------------------------------------------------------------------
# Normalized message types
# ---------------------------------------------------------------------------

class MessageKind(str, Enum):
    """Kind of normalized event, not just plain text."""
    TEXT = "text"
    TOOL_USE = "tool_use"          # model emitted a tool call
    TOOL_RESULT = "tool_result"    # tool execution result back to model
    SYSTEM_EVENT = "system_event"  # internal system message (handoff, cron, etc.)
    THINKING = "thinking"          # model thinking block (if provider supports)


@dataclass
class Message:
    """Normalized message in the transcript. Not plain text."""
    id: str = field(default_factory=new_id)
    session_id: str = ""
    run_id: str = ""
    role: str = "user"             # user | assistant | system | tool
    kind: str = MessageKind.TEXT
    content: str = ""              # text body or JSON for structured events
    token_estimate: int = 0
    persistent: bool | None = None  # None = default durable write, False = in-memory only

    @property
    def needs_blocking_write(self) -> bool:
        """User and system messages must be durably written before model call."""
        return self.role in ("user", "system")


@dataclass
class RunState:
    """Structured run lifecycle."""
    id: str = field(default_factory=new_id)
    session_id: str = ""
    status: str = "queued"         # queued | running | succeeded | failed | interrupted
    turn_number: int = 0
    error: str | None = None
    tool_calls: list[str] = field(default_factory=list)  # tool invocation ids this turn


# ---------------------------------------------------------------------------
# Persistence queue (fire-and-forget for assistant messages)
# ---------------------------------------------------------------------------

@dataclass
class PendingWrite:
    message: Message
    blocking: bool = False


class PersistenceQueue:
    """Order-preserving write queue for transcript persistence.

    - User/system messages: blocking (written immediately)
    - Assistant messages: fire-and-forget (queued, flushed in order)
    """

    def __init__(self, db_conn, transcript_manager=None):
        self._conn = db_conn
        self._transcript = transcript_manager
        self._queue: list[PendingWrite] = []

    def enqueue(self, msg: Message, *, blocking: bool = False) -> None:
        if blocking:
            self._persist(msg)
        else:
            self._queue.append(PendingWrite(msg, blocking=False))

    def flush(self) -> list[Message]:
        """Flush all queued writes in order. Returns flushed messages."""
        flushed = []
        while self._queue:
            pw = self._queue.pop(0)
            self._persist(pw.message)
            flushed.append(pw.message)
        return flushed

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    def _persist(self, msg: Message) -> None:
        """Write a message to the DB."""
        if self._transcript is not None:
            if msg.role in ("user", "system"):
                self._transcript.submit_user_message(
                    msg.session_id,
                    msg.run_id,
                    msg.content,
                    role=msg.role,
                    token_estimate=msg.token_estimate,
                )
            elif msg.role == "assistant":
                self._transcript.commit_assistant_message(
                    msg.session_id,
                    msg.run_id,
                    msg.content,
                    token_estimate=msg.token_estimate,
                    persistent=True,
                )
            elif msg.role == "tool":
                self._transcript.log_tool_invocation(
                    msg.session_id,
                    msg.run_id,
                    "runtime_tool",
                    output_data={"content": msg.content},
                    status="completed",
                    transcript_persistent=True,
                )
            return

        self._conn.execute(
            """INSERT INTO messages
               (id, session_id, run_id, role, content, position, token_estimate, persistent)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                msg.id,
                msg.session_id,
                msg.run_id,
                msg.role,
                msg.content,
                self._next_position(msg.session_id),
                msg.token_estimate,
            ),
        )

    def _next_position(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(position), 0) AS pos FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["pos"]) + 1


# ---------------------------------------------------------------------------
# ConversationRuntime
# ---------------------------------------------------------------------------

class ConversationRuntime:
    """One instance per active session. Owns message chain, token tracking,
    run state, and persistence queue.

    This is the core engine object — the agent loop (loop.py) drives turns
    by calling into this runtime.
    """

    def __init__(
        self,
        session_id: str,
        *,
        model: str,
        provider: str = "llamacpp",
        context_window: int = 65536,
        db_conn,
        transcript_manager=None,
        event_logger=None,
    ):
        self.session_id = session_id
        self.model = model
        self.provider = provider
        self.context_window = context_window
        self.db = db_conn
        self.transcript = transcript_manager
        self.event_logger = event_logger

        # -- State --
        self.status: str = "active"              # session status
        self.token_count: int = 0                # running token estimate
        self.task_budget_remaining: int | None = None  # turns budget (carried across rollover)
        self.rolled_over_to: str | None = None   # successor session id

        # -- Message chain (in-memory) --
        self.messages: list[Message] = []
        self._position_counter: int = 0

        # -- Run state --
        self.current_run: RunState | None = None
        self._run_number: int = 0

        # -- Persistence --
        self.persist = PersistenceQueue(db_conn, transcript_manager)

        # -- Interrupt / pause --
        self._paused: bool = False
        self._interrupted: bool = False

        # -- Tool state --
        self.active_tools: list[str] = []  # tool invocation ids in progress

    # -----------------------------------------------------------------------
    # Run lifecycle
    # -----------------------------------------------------------------------

    def begin_run(
        self,
        *,
        existing_run_id: str | None = None,
        turn_number: int | None = None,
    ) -> RunState:
        """Start a new run (agent turn) or adopt an existing queued run."""
        if existing_run_id:
            self._run_number = turn_number or max(self._run_number, 1)
            self.current_run = RunState(
                id=existing_run_id,
                session_id=self.session_id,
                status="running",
                turn_number=self._run_number,
            )
            self._interrupted = False

            self.db.execute(
                "UPDATE runs SET status = 'running' WHERE id = ? AND session_id = ?",
                (existing_run_id, self.session_id),
            )
            self.db.commit()
            if self.event_logger is not None:
                self.event_logger.log_run_started(
                    self.session_id,
                    self.current_run.id,
                    self.current_run.turn_number,
                )
            return self.current_run

        self._run_number += 1
        self.current_run = RunState(
            session_id=self.session_id,
            status="running",
            turn_number=self._run_number,
        )
        self._interrupted = False

        # Persist run to DB
        self.db.execute(
            """INSERT INTO runs (id, session_id, status, turn_number)
               VALUES (?, ?, 'running', ?)""",
            (self.current_run.id, self.session_id, self._run_number),
        )
        self.db.commit()
        if self.event_logger is not None:
            self.event_logger.log_run_started(self.session_id, self.current_run.id, self._run_number)
        return self.current_run

    def end_run(self, status: str = "succeeded", *, error: str | None = None) -> None:
        """Finalize the current run."""
        if not self.current_run:
            return

        self.current_run.status = status
        self.current_run.error = error

        # Flush pending assistant writes before closing run
        self.persist.flush()

        self.db.execute(
            """UPDATE runs SET status = ?, error = ?
               WHERE id = ? AND session_id = ?""",
            (status, error, self.current_run.id, self.session_id),
        )
        self.db.commit()
        if self.event_logger is not None:
            if status == "succeeded":
                self.event_logger.log_run_completed(self.session_id, self.current_run.id)
            elif status == "failed":
                self.event_logger.log_run_failed(self.session_id, self.current_run.id, error or "unknown")
        self.current_run = None

    def interrupt_run(self) -> None:
        """Interrupt current run (e.g., user sent new message or watchdog triggered)."""
        if self.current_run:
            interrupted_run = self.current_run
            self._interrupted = True
            self.end_run(status="interrupted")

            # Create synthetic tool-result messages for in-progress tools
            for tool_id in interrupted_run.tool_calls:
                self._synthetic_interrupt_result(tool_id)

    # -----------------------------------------------------------------------
    # Message management
    # -----------------------------------------------------------------------

    def add_message(self, msg: Message, *, run_id: str | None = None) -> Message:
        """Add a message to the in-memory chain and queue persistence.

        User/system messages are written blocking (crash-safe).
        Assistant/tool messages are queued fire-and-forget.
        """
        msg.session_id = self.session_id
        msg.run_id = run_id or (self.current_run.id if self.current_run else "")
        if msg.persistent is None:
            msg.persistent = True

        self._position_counter += 1
        # position is set in PersistenceQueue._persist based on DB max

        self.messages.append(msg)
        self.token_count += msg.token_estimate

        if msg.persistent:
            blocking = msg.needs_blocking_write
            self.persist.enqueue(msg, blocking=blocking)
        return msg

    def get_message_chain(self) -> list[Message]:
        """Return the in-memory message chain for prompt assembly."""
        return list(self.messages)

    # -----------------------------------------------------------------------
    # Token tracking
    # -----------------------------------------------------------------------

    @property
    def usage_pct(self) -> float:
        """Token usage as percentage of context window."""
        if self.context_window <= 0:
            return 0.0
        return (self.token_count / self.context_window) * 100

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.context_window - self.token_count)

    def update_token_count(self, delta: int) -> None:
        """Adjust running token estimate (e.g., after compaction)."""
        self.token_count = max(0, self.token_count + delta)
        if self.transcript is not None and delta != 0:
            current = self.transcript.get_token_count(self.session_id)
            desired = max(0, current + delta)
            self.transcript.update_token_count(self.session_id, desired - current)
            self.token_count = desired
        else:
            self.db.execute(
                "UPDATE sessions SET token_count = ? WHERE id = ?",
                (self.token_count, self.session_id),
            )
            self.db.commit()

    # -----------------------------------------------------------------------
    # Pause / control
    # -----------------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_interrupted(self) -> bool:
        return self._interrupted

    def request_interrupt(self) -> None:
        """Signal cooperative interruption without finalizing the run inline."""
        self._interrupted = True

    def pause(self) -> None:
        """Signal from watchdog: pause current turn for rollover."""
        self._paused = True
        if self.current_run:
            self.interrupt_run()

    def resume(self) -> None:
        self._paused = False

    # -----------------------------------------------------------------------
    # Tools
    # -----------------------------------------------------------------------

    def register_tool_call(self, tool_id: str) -> None:
        """Track a tool invocation in progress."""
        if self.current_run:
            self.current_run.tool_calls.append(tool_id)
        self.active_tools.append(tool_id)

    def complete_tool_call(self, tool_id: str) -> None:
        """Remove from active tools after completion."""
        self.active_tools = [t for t in self.active_tools if t != tool_id]
        if self.current_run:
            self.current_run.tool_calls = [t for t in self.current_run.tool_calls if t != tool_id]

    def _synthetic_interrupt_result(self, tool_id: str) -> None:
        """Create a synthetic tool-result message on interruption to preserve
        message-chain validity."""
        msg = Message(
            role="tool",
            kind=MessageKind.TOOL_RESULT,
            content=f'{{"tool_id": "{tool_id}", "status": "interrupted", "error": "Run interrupted before tool completed"}}',
            token_estimate=10,
        )
        self.add_message(msg)

    # -----------------------------------------------------------------------
    # Rollover helpers
    # -----------------------------------------------------------------------

    def mark_rolled_over(self, successor_id: str) -> None:
        """Mark session as rolled over, link to successor."""
        self.status = "rolled-over"
        self.rolled_over_to = successor_id
        self.db.execute(
            """UPDATE sessions
               SET status = 'rolled-over', rolled_over_to = ?
               WHERE id = ?""",
            (successor_id, self.session_id),
        )
        self.db.commit()
        if self.event_logger is not None:
            self.event_logger.log_rollover_triggered(self.session_id, reason=f"successor:{successor_id}")

    # -----------------------------------------------------------------------
    # Snapshot (for debugging / handoff generation)
    # -----------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot of runtime state."""
        return {
            "session_id": self.session_id,
            "model": self.model,
            "provider": self.provider,
            "status": self.status,
            "token_count": self.token_count,
            "context_window": self.context_window,
            "usage_pct": round(self.usage_pct, 1),
            "tokens_remaining": self.tokens_remaining,
            "message_count": len(self.messages),
            "turn_number": self._run_number,
            "pending_writes": self.persist.pending_count,
            "paused": self._paused,
            "interrupted": self._interrupted,
            "active_tools": len(self.active_tools),
            "task_budget_remaining": self.task_budget_remaining,
            "rolled_over_to": self.rolled_over_to,
        }
