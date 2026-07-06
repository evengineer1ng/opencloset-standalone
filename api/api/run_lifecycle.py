from __future__ import annotations

from datetime import datetime, timezone

from api.api.events import StreamEvent, stream_event_to_dict


class RunLifecycleError(Exception):
    """Raised when a run lifecycle transition is invalid."""


class RunManager:
    """Manage run lifecycle transitions, SSE emission, transcript writes, and audit logging."""

    def __init__(self, db, event_store, transcript, event_logger) -> None:
        self.db = db
        self.event_store = event_store
        self.transcript = transcript
        self.event_logger = event_logger
        self._stream_listeners = []

    def register_stream_listener(self, listener) -> None:
        self._stream_listeners.append(listener)

    def start_run(self, run_id: str) -> None:
        self._transition(run_id, "running")
        run = self.get_run(run_id)
        if run:
            self.event_logger.log_run_started(run["session_id"], run_id, run["turn_number"])

    def succeed_run(self, run_id: str) -> None:
        self._transition(run_id, "succeeded")
        now = self._now()
        self.db.execute(
            "UPDATE runs SET completed_at = ? WHERE id = ?",
            (now, run_id),
        )
        self.db.commit()
        run = self.get_run(run_id)
        if run:
            self.event_logger.log_run_completed(run["session_id"], run_id)
        self.event_store.enqueue(run_id, StreamEvent.done("succeeded"))
        self.event_store.complete(run_id)

    def fail_run(self, run_id: str, error: str, error_code: str = "agent_error") -> None:
        self._transition(run_id, "failed")
        now = self._now()
        self.db.execute(
            "UPDATE runs SET error = ?, completed_at = ? WHERE id = ?",
            (error, now, run_id),
        )
        self.db.commit()
        run = self.get_run(run_id)
        if run:
            self.event_logger.log_run_failed(run["session_id"], run_id, error)
        self.emit_stream_event(run_id, StreamEvent.error(error, error_code))
        self.event_store.complete(run_id)

    def block_run(self, run_id: str, error: str, error_code: str = "action_progress_blocked") -> None:
        self._transition(run_id, "blocked")
        now = self._now()
        self.db.execute(
            "UPDATE runs SET error = ?, completed_at = ? WHERE id = ?",
            (error, now, run_id),
        )
        self.db.commit()
        run = self.get_run(run_id)
        if run:
            self.event_logger.log_run_failed(run["session_id"], run_id, error)
        self.event_store.complete(run_id)

    def interrupt_run(self, run_id: str) -> None:
        self._transition(run_id, "interrupted")
        now = self._now()
        self.db.execute(
            "UPDATE runs SET completed_at = ? WHERE id = ?",
            (now, run_id),
        )
        self.db.commit()
        self.emit_stream_event(run_id, StreamEvent.interrupted())
        self.event_store.complete(run_id)

    def rollover_run(self, run_id: str, new_session_id: str) -> None:
        self._transition(run_id, "rolled-over")
        now = self._now()
        self.db.execute(
            "UPDATE runs SET completed_at = ? WHERE id = ?",
            (now, run_id),
        )
        self.db.commit()
        run = self.get_run(run_id)
        if run:
            self.event_logger.log_rollover_triggered(run["session_id"], reason=f"successor:{new_session_id}")
        self.event_store.complete(run_id)

    def stream_text_delta(self, run_id: str, text: str) -> None:
        self.emit_stream_event(run_id, StreamEvent.text_delta(text))

    def stream_tool_use(self, run_id: str, tool_name: str, input_data: dict) -> None:
        run = self.get_run(run_id)
        if run:
            self.event_logger.log_tool_called(run["session_id"], run_id, tool_name, input_data)
        self.emit_stream_event(run_id, StreamEvent.tool_use(tool_name, input_data))

    def stream_tool_result(
        self,
        run_id: str,
        tool_id: str,
        tool_name: str,
        status: str,
        content: str = "",
        error: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.emit_stream_event(
            run_id,
            StreamEvent.tool_result(tool_id, tool_name, status, content, error, error_code),
        )

    def stream_thinking_delta(self, run_id: str, text: str) -> None:
        self.emit_stream_event(run_id, StreamEvent.thinking_delta(text))

    def stream_usage(self, run_id: str, input_tokens: int, output_tokens: int) -> None:
        self.emit_stream_event(run_id, StreamEvent.usage(input_tokens, output_tokens))

    def emit_stream_event(self, run_id: str, event: StreamEvent | dict) -> None:
        event_dict = stream_event_to_dict(event)
        self.event_store.enqueue(run_id, event_dict)
        run = self.get_run(run_id)
        if run:
            self.event_logger.log_stream_event(run["session_id"], run_id, event_dict)
        for listener in list(self._stream_listeners):
            try:
                listener(run_id, event_dict, run)
            except Exception:
                continue

    def persist_assistant_message(
        self,
        session_id: str,
        run_id: str,
        content: str,
        token_estimate: int = 0,
        persistent: bool = False,
    ) -> str:
        return self.transcript.commit_assistant_message(
            session_id=session_id,
            run_id=run_id,
            content=content,
            token_estimate=token_estimate,
            persistent=persistent,
        )

    def persist_tool_result(
        self,
        session_id: str,
        run_id: str,
        tool_name: str,
        input_data: dict,
        output_data: dict | None,
        status: str = "completed",
        error: str | None = None,
    ) -> str:
        inv_id = self.transcript.log_tool_invocation(
            session_id=session_id,
            run_id=run_id,
            tool_name=tool_name,
            input_data=input_data,
            output_data=output_data,
            status=status,
            error=error,
            transcript_persistent=True,
        )
        if error:
            self.event_logger.log_tool_failed(session_id, run_id, tool_name, error)
        else:
            self.event_logger.log_tool_completed(session_id, run_id, tool_name)
        return inv_id

    def get_run(self, run_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT id, session_id, status, turn_number, max_turns, error, created_at, completed_at "
            "FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_active_run(self, session_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT id, session_id, status, turn_number FROM runs "
            "WHERE session_id = ? AND status IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_session_runs(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, status, turn_number, max_turns, error, created_at, completed_at "
            "FROM runs WHERE session_id = ? ORDER BY turn_number DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def reconcile_stale_running_runs(self, *, reason: str = "Recovered stale running run during API startup.") -> int:
        rows = self.db.execute(
            "SELECT id, session_id FROM runs WHERE status = 'running'"
        ).fetchall()
        stale_runs = [dict(row) for row in rows]
        if not stale_runs:
            return 0

        now = self._now()
        for run in stale_runs:
            self.db.execute(
                "UPDATE runs SET status = 'interrupted', error = ?, completed_at = ? WHERE id = ?",
                (reason, now, run["id"]),
            )
        self.db.commit()
        for run in stale_runs:
            self.event_logger.log_run_failed(run["session_id"], run["id"], reason)
        return len(stale_runs)

    def _transition(self, run_id: str, new_status: str) -> None:
        row = self.db.execute(
            "SELECT status FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            raise RunLifecycleError(f"Run {run_id} not found")

        current = row["status"]
        allowed = {
            "queued": {"running", "interrupted"},
            "running": {"succeeded", "failed", "blocked", "interrupted", "rolled-over"},
        }
        if new_status not in allowed.get(current, set()):
            raise RunLifecycleError(
                f"Cannot transition run from '{current}' to '{new_status}'"
            )

        self.db.execute(
            "UPDATE runs SET status = ? WHERE id = ?",
            (new_status, run_id),
        )
        self.db.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
