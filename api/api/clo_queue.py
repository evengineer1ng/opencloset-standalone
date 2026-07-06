from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

from api.db.schema import new_id


SETTINGS_ROW_ID = "default"
QUEUE_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


logger = logging.getLogger(__name__)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class CloQueuePollResult:
    paused: bool
    running_item_id: str | None = None
    dispatched_item_id: str | None = None
    queued_count: int = 0
    blocked_reason: str | None = None


class CloQueueManager:
    def __init__(self, app) -> None:
        self.app = app
        self.db = app.db
        self._ensure_settings()

    def _ensure_settings(self) -> None:
        self.db.execute(
            """
            INSERT OR IGNORE INTO clo_queue_settings (id, paused, pause_on_error, updated_at)
            VALUES (?, 0, 1, ?)
            """,
            (SETTINGS_ROW_ID, _now()),
        )
        self.db.commit()

    def get_settings(self) -> dict[str, object]:
        self._ensure_settings()
        row = self.db.execute(
            "SELECT id, paused, pause_on_error, updated_at FROM clo_queue_settings WHERE id = ?",
            (SETTINGS_ROW_ID,),
        ).fetchone()
        if row is None:
            return {"paused": False, "pause_on_error": True, "updated_at": None}
        return {
            "paused": bool(row["paused"]),
            "pause_on_error": bool(row["pause_on_error"]),
            "updated_at": row["updated_at"],
        }

    def update_settings(
        self,
        *,
        paused: bool | None = None,
        pause_on_error: bool | None = None,
    ) -> dict[str, object]:
        current = self.get_settings()
        next_paused = current["paused"] if paused is None else bool(paused)
        next_pause_on_error = current["pause_on_error"] if pause_on_error is None else bool(pause_on_error)
        self.db.execute(
            """
            UPDATE clo_queue_settings
            SET paused = ?, pause_on_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (1 if next_paused else 0, 1 if next_pause_on_error else 0, _now(), SETTINGS_ROW_ID),
        )
        self.db.commit()
        return self.get_settings()

    def list_recent_items(self, *, limit: int = 8) -> list[dict[str, object]]:
        rows = self.db.execute(
            f"{self._base_item_query()} WHERE q.status IN ('completed', 'failed') ORDER BY q.finished_at DESC, q.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_state(self, *, history_limit: int = 8) -> dict[str, object]:
        settings = self.get_settings()
        running_row = self.db.execute(
            f"{self._base_item_query()} WHERE q.status = 'running' ORDER BY q.started_at DESC, q.created_at DESC LIMIT 1"
        ).fetchone()
        queued_rows = self.db.execute(
            f"{self._base_item_query()} WHERE q.status = 'queued' ORDER BY q.position ASC, q.created_at ASC"
        ).fetchall()
        worker = getattr(self.app, "clo_queue_worker", None)
        health = worker.get_health() if worker and hasattr(worker, "get_health") else {
            "worker_alive": False,
            "last_poll_at": None,
            "last_error": None,
        }
        return {
            "paused": settings["paused"],
            "pause_on_error": settings["pause_on_error"],
            "running_item": self._row_to_dict(running_row) if running_row else None,
            "queued_items": [self._row_to_dict(row) for row in queued_rows],
            "recent_items": self.list_recent_items(limit=history_limit),
            **health,
        }

    def enqueue_item(
        self,
        *,
        session_id: str,
        content: str,
        stop_after_error: bool = False,
    ) -> dict[str, object]:
        content = str(content or "").strip()
        if not content:
            raise ValueError("content is required")

        session = self.db.execute(
            "SELECT id, label, status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not session:
            raise ValueError("session not found")
        if session["status"] != "active":
            raise ValueError(f"session is {session['status']}, not active")

        position = self.db.execute(
            "SELECT COALESCE(MAX(position), 0) AS max_position FROM clo_queue_items WHERE status = 'queued'"
        ).fetchone()["max_position"] + 1
        item_id = new_id()
        now = _now()

        self.db.execute(
            """
            INSERT INTO clo_queue_items (
                id, session_id, message_content, status, position, stop_after_error, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (item_id, session_id, content, position, 1 if stop_after_error else 0, now, now),
        )
        self.db.commit()
        self.app.event_logger.log(session_id, "clo_queue_item_enqueued", {"item_id": item_id, "position": position})
        return self.get_item(item_id)

    def get_item(self, item_id: str) -> dict[str, object]:
        row = self.db.execute(
            f"{self._base_item_query()} WHERE q.id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            raise ValueError("queue item not found")
        return self._row_to_dict(row)

    def move_item(self, item_id: str, direction: str) -> dict[str, object]:
        if direction not in {"up", "down"}:
            raise ValueError("direction must be 'up' or 'down'")

        rows = self.db.execute(
            "SELECT id, position FROM clo_queue_items WHERE status = 'queued' ORDER BY position ASC, created_at ASC"
        ).fetchall()
        ordered_ids = [row["id"] for row in rows]
        if item_id not in ordered_ids:
            raise ValueError("queue item is not reorderable")

        index = ordered_ids.index(item_id)
        if direction == "up" and index == 0:
            return self.get_item(item_id)
        if direction == "down" and index == len(ordered_ids) - 1:
            return self.get_item(item_id)

        swap_index = index - 1 if direction == "up" else index + 1
        first = rows[index]
        second = rows[swap_index]
        self.db.execute(
            "UPDATE clo_queue_items SET position = ?, updated_at = ? WHERE id = ?",
            (second["position"], _now(), first["id"]),
        )
        self.db.execute(
            "UPDATE clo_queue_items SET position = ?, updated_at = ? WHERE id = ?",
            (first["position"], _now(), second["id"]),
        )
        self.db.commit()
        return self.get_item(item_id)

    def cancel_item(self, item_id: str) -> dict[str, object]:
        item = self.get_item(item_id)
        if item["status"] != "queued":
            raise ValueError("only queued items can be cancelled")

        now = _now()
        self.db.execute(
            """
            UPDATE clo_queue_items
            SET status = 'cancelled', position = NULL, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, item_id),
        )
        self.db.commit()
        self._renumber_queued_items()
        self.app.event_logger.log(item["session_id"], "clo_queue_item_cancelled", {"item_id": item_id})
        return self.get_item(item_id)

    def get_running_item(self) -> dict[str, object] | None:
        row = self.db.execute(
            f"{self._base_item_query()} WHERE q.status = 'running' ORDER BY q.started_at DESC, q.created_at DESC LIMIT 1"
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_next_queued_item(self) -> dict[str, object] | None:
        row = self.db.execute(
            f"{self._base_item_query()} WHERE q.status = 'queued' ORDER BY q.position ASC, q.created_at ASC LIMIT 1"
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def mark_running(self, item_id: str, run_id: str) -> dict[str, object]:
        now = _now()
        self.db.execute(
            """
            UPDATE clo_queue_items
            SET status = 'running', run_id = ?, started_at = ?, position = NULL, updated_at = ?
            WHERE id = ?
            """,
            (run_id, now, now, item_id),
        )
        self.db.commit()
        self._renumber_queued_items()
        item = self.get_item(item_id)
        self.app.event_logger.log(item["session_id"], "clo_queue_item_started", {"item_id": item_id, "run_id": run_id})
        return item

    def complete_item(
        self,
        item_id: str,
        *,
        status: str,
        error: str | None = None,
        result_summary: str | None = None,
    ) -> dict[str, object]:
        if status not in QUEUE_TERMINAL_STATUSES:
            raise ValueError("invalid queue item terminal status")

        now = _now()
        self.db.execute(
            """
            UPDATE clo_queue_items
            SET status = ?, error = ?, result_summary = ?, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error, result_summary, now, now, item_id),
        )
        self.db.commit()
        item = self.get_item(item_id)
        event_type = {
            "completed": "clo_queue_item_completed",
            "failed": "clo_queue_item_failed",
            "cancelled": "clo_queue_item_cancelled",
        }[status]
        payload = {"item_id": item_id}
        if item.get("run_id"):
            payload["run_id"] = item["run_id"]
        if error:
            payload["error"] = error
        self.app.event_logger.log(item["session_id"], event_type, payload, item.get("run_id") or None)
        return item

    def _renumber_queued_items(self) -> None:
        rows = self.db.execute(
            "SELECT id FROM clo_queue_items WHERE status = 'queued' ORDER BY position ASC, created_at ASC"
        ).fetchall()
        now = _now()
        for index, row in enumerate(rows, start=1):
            self.db.execute(
                "UPDATE clo_queue_items SET position = ?, updated_at = ? WHERE id = ?",
                (index, now, row["id"]),
            )
        self.db.commit()

    def _base_item_query(self) -> str:
        return """
            SELECT
                q.id,
                q.session_id,
                q.message_content,
                q.status,
                q.position,
                q.run_id,
                q.error,
                q.result_summary,
                q.stop_after_error,
                q.created_at,
                q.started_at,
                q.finished_at,
                q.updated_at,
                s.label AS session_label,
                s.workspace_id,
                s.build_project_id,
                w.name AS workspace_name,
                bp.name AS build_project_name
            FROM clo_queue_items q
            JOIN sessions s ON s.id = q.session_id
            LEFT JOIN workspaces w ON w.id = s.workspace_id
            LEFT JOIN build_projects bp ON bp.id = s.build_project_id
        """

    def _row_to_dict(self, row) -> dict[str, object]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "session_label": row["session_label"],
            "workspace_id": row["workspace_id"],
            "workspace_name": row["workspace_name"],
            "build_project_id": row["build_project_id"],
            "build_project_name": row["build_project_name"],
            "message_content": row["message_content"],
            "status": row["status"],
            "position": row["position"],
            "run_id": row["run_id"],
            "error": row["error"],
            "result_summary": row["result_summary"],
            "stop_after_error": bool(row["stop_after_error"]),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
        }


class CloQueueWorker:
    def __init__(self, app, *, poll_interval_seconds: float = 2.0):
        self.app = app
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._health_lock = threading.Lock()
        self._last_poll_at: str | None = None
        self._last_error: str | None = None

    def poll_once(self) -> CloQueuePollResult:
        try:
            result = self._poll_once_impl()
        except Exception as exc:
            self._record_poll_error(exc)
            raise
        self._record_poll_success()
        return result

    def _poll_once_impl(self) -> CloQueuePollResult:
        settings = self.app.clo_queue.get_settings()
        running_item = self.app.clo_queue.get_running_item()
        if running_item:
            if self._reconcile_running_item(running_item):
                settings = self.app.clo_queue.get_settings()
            else:
                queued_count = len(self.app.clo_queue.list_state()["queued_items"])
                return CloQueuePollResult(
                    paused=bool(settings["paused"]),
                    running_item_id=str(running_item["id"]),
                    queued_count=queued_count,
                    blocked_reason="queue_item_running",
                )

        if settings["paused"]:
            queued_count = len(self.app.clo_queue.list_state()["queued_items"])
            return CloQueuePollResult(paused=True, queued_count=queued_count, blocked_reason="paused")

        next_item = self.app.clo_queue.get_next_queued_item()
        if not next_item:
            return CloQueuePollResult(paused=False, queued_count=0, blocked_reason="idle")

        active_run = self.app.run_manager.get_active_run(str(next_item["session_id"]))
        if active_run:
            queued_count = len(self.app.clo_queue.list_state()["queued_items"])
            return CloQueuePollResult(paused=False, queued_count=queued_count, blocked_reason="run_active")

        self._dispatch_item(next_item)
        queued_count = len(self.app.clo_queue.list_state()["queued_items"])
        return CloQueuePollResult(
            paused=False,
            dispatched_item_id=str(next_item["id"]),
            queued_count=queued_count,
        )

    def get_health(self) -> dict[str, object]:
        with self._health_lock:
            return {
                "worker_alive": bool(self._thread and self._thread.is_alive()),
                "last_poll_at": self._last_poll_at,
                "last_error": self._last_error,
            }

    def _record_poll_success(self) -> None:
        with self._health_lock:
            self._last_poll_at = _now()
            self._last_error = None

    def _record_poll_error(self, exc: Exception) -> None:
        with self._health_lock:
            self._last_poll_at = _now()
            self._last_error = str(exc) or type(exc).__name__

    def _dispatch_item(self, item: dict[str, object]) -> None:
        session_id = str(item["session_id"])
        content = str(item["message_content"])
        run_id = self._submit_queue_message(session_id, content)
        self.app.clo_queue.mark_running(str(item["id"]), run_id)

        failure_error: str | None = None
        result_summary: str | None = None
        terminal_status = "completed"

        try:
            result = self.app.execution_runtime.execute_run(session_id, run_id)
        except Exception as exc:
            failure_error = str(exc) or type(exc).__name__
            terminal_status = "failed"
            try:
                refreshed = self.app.run_manager.get_run(run_id)
                if refreshed and refreshed["status"] in ("queued", "running"):
                    self.app.run_manager.fail_run(run_id, failure_error)
                self.app.runtime_diagnostics.maybe_emit_run_error_window(session_id, run_id)
            except Exception:
                pass
        else:
            try:
                self.app.runtime_diagnostics.maybe_emit_run_error_window(session_id, run_id)
            except Exception:
                pass

            if result.status != "succeeded":
                terminal_status = "failed"
                failure_error = result.error or result.finish_reason or result.status
            else:
                result_summary = self._build_result_summary(session_id, run_id, fallback=result.text or "")

        completed_item = self.app.clo_queue.complete_item(
            str(item["id"]),
            status=terminal_status,
            error=failure_error,
            result_summary=result_summary,
        )

        settings = self.app.clo_queue.get_settings()
        if terminal_status == "failed" and (bool(settings["pause_on_error"]) or bool(completed_item["stop_after_error"])):
            self.app.clo_queue.update_settings(paused=True)

    def _reconcile_running_item(self, item: dict[str, object]) -> bool:
        run_id = item.get("run_id")
        if not run_id:
            self.app.clo_queue.complete_item(
                str(item["id"]),
                status="failed",
                error="Queue item lost its run reference before completion.",
                result_summary="Queue item lost its run reference before completion.",
            )
            self.app.clo_queue.update_settings(paused=True)
            return True

        run = self.app.run_manager.get_run(str(run_id))
        if not run or run["status"] in ("queued", "running"):
            return False

        terminal_status = "completed" if run["status"] == "succeeded" else "failed"
        error = run.get("error") if terminal_status == "failed" else None
        summary = None
        if terminal_status == "completed":
            summary = self._build_result_summary(str(item["session_id"]), str(run_id), fallback="")
        self.app.clo_queue.complete_item(
            str(item["id"]),
            status=terminal_status,
            error=error,
            result_summary=summary,
        )
        if terminal_status == "failed":
            settings = self.app.clo_queue.get_settings()
            if bool(settings["pause_on_error"]) or bool(item["stop_after_error"]):
                self.app.clo_queue.update_settings(paused=True)
        return True

    def _submit_queue_message(self, session_id: str, content: str) -> str:
        run_id = new_id()
        max_turns = self.app.config.get("CLO_QUEUE_MAX_TURNS")
        if max_turns is None:
            max_turns = self.app.config.get("LOOP_MAX_TURNS")
        turn_number = self.app.db.execute(
            "SELECT COALESCE(MAX(turn_number), 0) AS tn FROM runs WHERE session_id = ?",
            (session_id,),
        ).fetchone()["tn"] + 1
        self.app.db.execute(
            "INSERT INTO runs (id, session_id, status, turn_number, max_turns) VALUES (?, ?, 'queued', ?, ?)",
            (run_id, session_id, turn_number, max_turns),
        )
        self.app.transcript.submit_user_message(session_id, run_id, content, role="user")
        self.app.db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (session_id,),
        )
        self.app.event_logger.log(
            session_id,
            "run_queued",
            {"run_id": run_id, "turn_number": turn_number, "origin": "clo_queue"},
            run_id,
        )
        self.app.db.commit()
        return run_id

    def _build_result_summary(self, session_id: str, run_id: str, *, fallback: str) -> str:
        row = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? AND run_id = ? AND role = 'assistant' ORDER BY position DESC LIMIT 1",
            (session_id, run_id),
        ).fetchone()
        text = (row["content"] if row and row["content"] else fallback or "").strip()
        compact = " ".join(text.split())
        if len(compact) <= 220:
            return compact
        return f"{compact[:217]}..."

    def run_forever(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("clo queue worker poll failed")
            self._stop_event.wait(self.poll_interval_seconds)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_background_loop, name="opencloset-clo-queue", daemon=True)
        self._thread.start()

    def stop_background(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run_background_loop(self) -> None:
        from api.api.app import create_app

        isolated_app = create_app(db_path=self.app.config["DB_PATH"], start_background_workers=False)
        isolated_worker = isolated_app.clo_queue_worker
        isolated_worker.poll_interval_seconds = self.poll_interval_seconds
        try:
            while not self._stop_event.is_set():
                try:
                    isolated_worker.poll_once()
                except Exception as exc:
                    self._record_poll_error(exc)
                    logger.exception("clo queue background poll failed")
                else:
                    self._record_poll_success()
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            isolated_app.close()


def register_clo_queue_routes(app) -> None:
    from flask import Response, jsonify, request, stream_with_context

    def format_queue_state_sse(state: dict[str, object]) -> str:
        return f"event: state\ndata: {json.dumps(state)}\n\n"

    def queue_keepalive() -> str:
        return ": keep-alive\n\n"

    def queue_state_stream():
        last_fingerprint: str | None = None
        last_keepalive_at = time.monotonic()

        while True:
            state = app.clo_queue.list_state()
            fingerprint = json.dumps(state, sort_keys=True)
            if fingerprint != last_fingerprint:
                yield format_queue_state_sse(state)
                last_fingerprint = fingerprint
                last_keepalive_at = time.monotonic()
            elif time.monotonic() - last_keepalive_at >= 10.0:
                yield queue_keepalive()
                last_keepalive_at = time.monotonic()
            time.sleep(0.4)

    @app.route("/api/clo-queue", methods=["GET"])
    def get_clo_queue_state():
        return jsonify(app.clo_queue.list_state())

    @app.route("/api/clo-queue/stream", methods=["GET"])
    def stream_clo_queue_state():
        return Response(
            stream_with_context(queue_state_stream()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.route("/api/clo-queue", methods=["PATCH"])
    def patch_clo_queue_state():
        data = request.get_json(silent=True) or {}
        settings = app.clo_queue.update_settings(
            paused=data.get("paused") if "paused" in data else None,
            pause_on_error=data.get("pause_on_error") if "pause_on_error" in data else None,
        )
        return jsonify(settings)

    @app.route("/api/clo-queue/items", methods=["POST"])
    def create_clo_queue_item():
        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id", "")
        try:
            item = app.clo_queue.enqueue_item(
                session_id=session_id,
                content=data.get("content", ""),
                stop_after_error=bool(data.get("stop_after_error", False)),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400 if "required" in str(exc) or "not found" not in str(exc) else 404
        return jsonify(item), 201

    @app.route("/api/clo-queue/items/<item_id>/move", methods=["POST"])
    def move_clo_queue_item(item_id: str):
        data = request.get_json(silent=True) or {}
        try:
            item = app.clo_queue.move_item(item_id, str(data.get("direction") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(item)

    @app.route("/api/clo-queue/items/<item_id>/cancel", methods=["POST"])
    def cancel_clo_queue_item(item_id: str):
        try:
            item = app.clo_queue.cancel_item(item_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(item)