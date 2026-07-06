from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from api.api.maintenance_artifacts import (
    COMPACTION_MARKER_ARTIFACT,
    DECISION_TOOL_DIGEST_ARTIFACT,
    HANDOFF_CANDIDATE_ARTIFACT,
    MICRO_SUMMARY_ARTIFACT,
    SEGMENT_SUMMARY_ARTIFACT,
    STALE_ARTIFACT_STATUS,
    TRANSCRIPT_ARCHIVE_CANDIDATE_ARTIFACT,
    VALID_ARTIFACT_STATUS,
    DRAFT_ARTIFACT_STATUS,
    MaintenanceArtifact,
    MaintenanceManager,
    MaintenancePollResult,
    _parse_timestamp,
    init_maintenance_table,
    normalize_compaction_ranges,
)
from api.api.maintenance_ranges import (
    build_archive_candidate_content,
    build_archive_safe_ranges,
    build_compaction_ranges,
    compaction_marker_matches,
    next_segment_range,
)

logger = logging.getLogger(__name__)


class SessionMaintenanceWorker:
    """Poll idle sessions and create bounded maintenance artifacts."""

    def __init__(
        self,
        app,
        *,
        poll_interval_seconds: float = 30.0,
        idle_threshold_seconds: float = 15.0,
        summary_message_limit: int = 6,
    ) -> None:
        self.app = app
        self.poll_interval_seconds = poll_interval_seconds
        self.idle_threshold_seconds = idle_threshold_seconds
        self.summary_message_limit = summary_message_limit
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> MaintenancePollResult:
        rows = self.app.db.execute(
            "SELECT id FROM sessions WHERE status = 'active' AND rolled_over_to IS NULL ORDER BY created_at ASC"
        ).fetchall()
        created: list[MaintenanceArtifact] = []
        for row in rows:
            session_id = row["id"]
            if self._should_abort_globally():
                break
            if self.app.run_manager.get_active_run(session_id):
                continue

            last_message = self._get_last_message(session_id)
            if not last_message or not self._is_idle(last_message["created_at"]):
                continue

            latest_summary = self.app.maintenance.get_latest_valid_artifact(session_id, MICRO_SUMMARY_ARTIFACT)
            if latest_summary and (latest_summary.get("end_position") or 0) >= last_message["position"]:
                summary_artifact = latest_summary
            else:
                summary_artifact = self._create_micro_summary(session_id, last_message["position"])
                if summary_artifact is not None:
                    created.append(MaintenanceArtifact(**summary_artifact))

            if self._should_abort_session_pass(session_id, last_message):
                continue

            valid_segments = self.app.maintenance.get_valid_artifacts(session_id, SEGMENT_SUMMARY_ARTIFACT)
            target_segment_end = (summary_artifact.get("start_position") or 1) - 1 if summary_artifact else 0
            pending_segment_range = next_segment_range(
                valid_segments,
                target_segment_end,
                summary_message_limit=self.summary_message_limit,
            )
            if pending_segment_range is not None:
                segment_artifact = self._create_segment_summary(
                    session_id,
                    pending_segment_range["start_position"],
                    pending_segment_range["end_position"],
                )
                if segment_artifact is not None:
                    created.append(MaintenanceArtifact(**segment_artifact))
                    valid_segments.append(segment_artifact)

            if self._should_abort_session_pass(session_id, last_message):
                continue

            latest_marker = self.app.maintenance.get_latest_valid_artifact(session_id, COMPACTION_MARKER_ARTIFACT)
            desired_ranges = build_compaction_ranges(summary_artifact, valid_segments)
            self.app.transcript.sync_summary_covered_ranges(
                session_id,
                desired_ranges,
                source_artifact_id=latest_marker.get("id") if latest_marker else None,
            )
            if desired_ranges and not compaction_marker_matches(latest_marker, desired_ranges):
                marker_artifact = self._create_compaction_marker(
                    session_id,
                    valid_segments,
                    summary_artifact,
                    desired_ranges,
                )
                if marker_artifact is not None:
                    created.append(MaintenanceArtifact(**marker_artifact))
                    self.app.transcript.sync_summary_covered_ranges(
                        session_id,
                        desired_ranges,
                        source_artifact_id=marker_artifact["id"],
                    )

            if self._should_abort_session_pass(session_id, last_message):
                continue

            archive_candidate = self._create_or_refresh_archive_candidate(session_id, desired_ranges)
            if archive_candidate is not None:
                created.append(MaintenanceArtifact(**archive_candidate))

            if self._should_abort_session_pass(session_id, last_message):
                continue

            latest_digest = self.app.maintenance.get_latest_valid_artifact(session_id, DECISION_TOOL_DIGEST_ARTIFACT)
            if not latest_digest or (latest_digest.get("end_position") or 0) < last_message["position"]:
                digest_artifact = self._create_decision_tool_digest(session_id, last_message["position"])
                if digest_artifact is not None:
                    created.append(MaintenanceArtifact(**digest_artifact))

            if self._should_abort_session_pass(session_id, last_message):
                continue

            latest_handoff = self.app.maintenance.get_latest_valid_artifact(session_id, HANDOFF_CANDIDATE_ARTIFACT)
            if latest_handoff and (latest_handoff.get("end_position") or 0) >= last_message["position"]:
                continue

            handoff_artifact = self._create_handoff_candidate(session_id, summary_artifact)
            if handoff_artifact is not None:
                created.append(MaintenanceArtifact(**handoff_artifact))

        return MaintenancePollResult(checked_sessions=len(rows), created_artifacts=created)

    def run_forever(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.poll_interval_seconds)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_background_loop, name="opencloset-maintenance", daemon=True)
        self._thread.start()

    def stop_background(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run_background_loop(self) -> None:
        from api.api.app import create_app

        isolated_app = create_app(db_path=self.app.config["DB_PATH"], start_background_workers=False)
        isolated_worker = isolated_app.maintenance_worker
        isolated_worker.poll_interval_seconds = self.poll_interval_seconds
        isolated_worker.idle_threshold_seconds = self.idle_threshold_seconds
        isolated_worker.summary_message_limit = self.summary_message_limit
        try:
            while not self._stop_event.is_set():
                isolated_worker.poll_once()
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            isolated_app.close()

    def _get_last_message(self, session_id: str):
        return self.app.db.execute(
            "SELECT position, created_at FROM messages WHERE session_id = ? ORDER BY position DESC LIMIT 1",
            (session_id,),
        ).fetchone()

    def _is_idle(self, created_at: str | None) -> bool:
        last_activity = _parse_timestamp(created_at)
        if last_activity is None:
            return False
        return last_activity <= datetime.now(timezone.utc) - timedelta(seconds=self.idle_threshold_seconds)

    def _should_abort_globally(self) -> bool:
        if self._stop_event.is_set():
            return True
        return self._is_provider_saturated()

    def _should_abort_session_pass(self, session_id: str, baseline_last_message) -> bool:
        if self._should_abort_globally():
            return True
        if self.app.run_manager.get_active_run(session_id):
            return True
        latest_message = self._get_last_message(session_id)
        if latest_message is None:
            return True
        if latest_message["position"] != baseline_last_message["position"]:
            return True
        if not self._is_idle(latest_message["created_at"]):
            return True
        return False

    def _is_provider_saturated(self) -> bool:
        checker = getattr(self.app, "provider_saturation_checker", None)
        if callable(checker):
            return bool(checker())
        return bool(self.app.config.get("MAINTENANCE_PROVIDER_SATURATED", False))

    def _create_micro_summary(self, session_id: str, end_position: int) -> dict[str, Any] | None:
        rows = self.app.db.execute(
            """SELECT role, content, position FROM messages
               WHERE session_id = ?
               ORDER BY position DESC LIMIT ?""",
            (session_id, self.summary_message_limit),
        ).fetchall()
        if not rows:
            return None
        ordered_rows = list(reversed(rows))
        start_position = ordered_rows[0]["position"]
        artifact = self._create_draft_then_finalize(
            session_id,
            MICRO_SUMMARY_ARTIFACT,
            metadata={"message_count": len(ordered_rows)},
            start_position=start_position,
            end_position=end_position,
            content_builder=lambda: "\n".join(
                ["Idle micro-summary:"]
                + [f"- {row['role']}: {self._summarize_content(row['content'])}" for row in ordered_rows]
            ),
        )
        if artifact is None:
            return None
        logger.info("Created maintenance artifact for %s covering messages %s-%s", session_id, start_position, end_position)
        return artifact

    def _create_segment_summary(self, session_id: str, start_position: int, end_position: int) -> dict[str, Any] | None:
        if start_position < 1 or end_position < start_position:
            return None

        total_row = self.app.db.execute(
            "SELECT COUNT(*) AS total_count FROM messages WHERE session_id = ? AND position BETWEEN ? AND ?",
            (session_id, start_position, end_position),
        ).fetchone()
        total_count = int(total_row["total_count"]) if total_row else 0
        if total_count == 0:
            return None

        head_limit = min(2, self.summary_message_limit)
        tail_limit = max(0, self.summary_message_limit - head_limit)
        head_rows = self.app.db.execute(
            """SELECT role, content, position FROM messages
                    WHERE session_id = ? AND position BETWEEN ? AND ?
               ORDER BY position ASC LIMIT ?""",
                (session_id, start_position, end_position, head_limit),
        ).fetchall()
        tail_rows = []
        if tail_limit > 0:
            tail_rows = self.app.db.execute(
                """SELECT role, content, position FROM messages
                         WHERE session_id = ? AND position BETWEEN ? AND ?
                   ORDER BY position DESC LIMIT ?""",
                     (session_id, start_position, end_position, tail_limit),
            ).fetchall()

        sampled_by_position: dict[int, Any] = {}
        for row in head_rows:
            sampled_by_position[row["position"]] = row
        for row in reversed(tail_rows):
            sampled_by_position[row["position"]] = row

        sampled_rows = [sampled_by_position[position] for position in sorted(sampled_by_position)]
        omitted_count = max(0, total_count - len(sampled_rows))
        artifact = self._create_draft_then_finalize(
            session_id,
            SEGMENT_SUMMARY_ARTIFACT,
            metadata={
                "total_message_count": total_count,
                "sampled_message_count": len(sampled_rows),
            },
            start_position=start_position,
            end_position=end_position,
            content_builder=lambda: "\n".join(
                [f"Segment summary (messages {start_position}-{end_position}):"]
                + [f"- {row['role']}[{row['position']}]: {self._summarize_content(row['content'])}" for row in sampled_rows]
                + ([f"- ... omitted {omitted_count} message(s) from this segment"] if omitted_count else [])
            ),
        )
        if artifact is None:
            return None
        logger.info("Created segment summary for %s covering messages %s-%s", session_id, start_position, end_position)
        return artifact

    def _create_compaction_marker(
        self,
        session_id: str,
        segment_artifacts: list[dict[str, Any]] | None,
        summary_artifact: dict[str, Any] | None,
        covered_ranges: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not covered_ranges:
            return None

        start_position = min(item["start_position"] for item in covered_ranges)
        end_position = max(item["end_position"] for item in covered_ranges)
        range_count = len(covered_ranges)
        if range_count == 1:
            content = f"Compaction marker for messages {start_position}-{end_position}"
        else:
            content = f"Compaction marker for {range_count} covered ranges"

        artifact = self._create_draft_then_finalize(
            session_id,
            COMPACTION_MARKER_ARTIFACT,
            metadata={
                "source_artifact_id": segment_artifacts[-1].get("id") if segment_artifacts else None,
                "source_artifact_type": SEGMENT_SUMMARY_ARTIFACT if segment_artifacts else None,
                "segment_artifact_ids": [artifact.get("id") for artifact in segment_artifacts or []],
                "summary_artifact_id": summary_artifact.get("id") if summary_artifact else None,
                "covered_ranges": covered_ranges,
                "covered_range_count": range_count,
            },
            start_position=start_position,
            end_position=end_position,
            content_builder=lambda: content,
        )
        if artifact is None:
            return None
        logger.info("Created compaction marker for %s covering ranges %s", session_id, covered_ranges)
        return artifact

    def _create_or_refresh_archive_candidate(
        self,
        session_id: str,
        covered_ranges: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        archive_ranges = build_archive_safe_ranges(covered_ranges)
        latest_candidate = self.app.maintenance.get_latest_valid_artifact(
            session_id,
            TRANSCRIPT_ARCHIVE_CANDIDATE_ARTIFACT,
        )
        if not archive_ranges:
            self.app.transcript.sync_archive_ready_ranges(session_id, [])
            return None
        if latest_candidate and (latest_candidate.get("metadata") or {}).get("archive_ranges") == archive_ranges:
            return None

        start_position = min(item["start_position"] for item in archive_ranges)
        end_position = max(item["end_position"] for item in archive_ranges)
        archive_message_count = sum((item["end_position"] - item["start_position"] + 1) for item in archive_ranges)
        source_artifact_ids: list[str] = []
        for item in archive_ranges:
            for source_range in item.get("source_ranges") or []:
                artifact_id = source_range.get("artifact_id")
                if artifact_id and artifact_id not in source_artifact_ids:
                    source_artifact_ids.append(artifact_id)

        artifact = self._create_draft_then_finalize(
            session_id,
            TRANSCRIPT_ARCHIVE_CANDIDATE_ARTIFACT,
            metadata={
                "archive_ranges": archive_ranges,
                "archive_range_count": len(archive_ranges),
                "archive_message_count": archive_message_count,
                "source_artifact_ids": source_artifact_ids,
            },
            start_position=start_position,
            end_position=end_position,
            content_builder=lambda: build_archive_candidate_content(archive_ranges),
        )
        if artifact is not None:
            self.app.transcript.sync_archive_ready_ranges(
                session_id,
                archive_ranges,
                source_artifact_id=artifact["id"],
            )
        return artifact

    def _create_decision_tool_digest(self, session_id: str, end_position: int) -> dict[str, Any] | None:
        rows = self.app.db.execute(
            """SELECT id, tool_name, input, output, status, error
               FROM tool_invocations
               WHERE session_id = ?
               ORDER BY completed_at DESC, started_at DESC, rowid DESC
               LIMIT ?""",
            (session_id, self.summary_message_limit),
        ).fetchall()
        if not rows:
            return None

        ordered_rows = list(reversed(rows))
        plan = self.app.planning.get_plan(session_id) or {}
        artifact = self._create_draft_then_finalize(
            session_id,
            DECISION_TOOL_DIGEST_ARTIFACT,
            metadata={
                "tool_invocation_count": len(ordered_rows),
                "tool_invocation_ids": [row["id"] for row in ordered_rows],
            },
            end_position=end_position,
            content_builder=lambda: self._build_decision_tool_digest_content(plan, ordered_rows),
        )
        if artifact is None:
            return None
        logger.info("Created decision/tool digest for %s covering transcript through %s", session_id, end_position)
        return artifact

    def _create_handoff_candidate(
        self,
        session_id: str,
        summary_artifact: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        handoff = self.app.planning.build_rollover_handoff(session_id)
        if not handoff:
            return None

        lines = ["Prepared handoff candidate:"]
        if handoff.get("active_goal"):
            lines.append(f"- Active goal: {handoff['active_goal']}")
        want_to_know = handoff.get("want_to_know") or []
        if want_to_know:
            lines.append(f"- Need to know: {', '.join(str(item) for item in want_to_know)}")
        if handoff.get("next_action"):
            lines.append(f"- Next action: {handoff['next_action']}")
        if handoff.get("next_item_status"):
            lines.append(f"- Next action status: {handoff['next_item_status']}")
        if summary_artifact and summary_artifact.get("content"):
            lines.append("- Recent maintenance summary:")
            for line in str(summary_artifact["content"]).splitlines():
                lines.append(f"  {line}")

        metadata = {
            "source_plan_id": handoff.get("source_plan_id"),
            "summary_artifact_id": summary_artifact.get("id") if summary_artifact else None,
        }
        artifact = self._create_draft_then_finalize(
            session_id,
            HANDOFF_CANDIDATE_ARTIFACT,
            metadata=metadata,
            start_position=summary_artifact.get("start_position") if summary_artifact else None,
            end_position=summary_artifact.get("end_position") if summary_artifact else None,
            content_builder=lambda: "\n".join(lines),
        )
        if artifact is None:
            return None
        logger.info("Created handoff candidate for %s", session_id)
        return artifact

    def _create_draft_then_finalize(
        self,
        session_id: str,
        artifact_type: str,
        *,
        metadata: dict[str, Any] | None = None,
        start_position: int | None = None,
        end_position: int | None = None,
        content_builder,
    ) -> dict[str, Any] | None:
        draft = self.app.maintenance.create_draft_artifact(
            session_id,
            artifact_type,
            metadata=metadata,
            start_position=start_position,
            end_position=end_position,
        )
        try:
            content = content_builder()
        except Exception:
            logger.exception("Failed to finalize maintenance draft %s for session %s", artifact_type, session_id)
            return None
        return self.app.maintenance.finalize_draft_artifact(
            draft["id"],
            content=content,
            metadata=metadata,
            start_position=start_position,
            end_position=end_position,
        )

    def _build_decision_tool_digest_content(self, plan: dict[str, Any], ordered_rows: list[Any]) -> str:
        lines = ["Decision/tool digest:"]
        if plan.get("active_goal"):
            lines.append(f"- Active goal: {plan['active_goal']}")
        next_item = plan.get("next_item")
        if next_item:
            lines.append(f"- Next action: {next_item.get('content', '')}")
            if next_item.get("status"):
                lines.append(f"- Next action status: {next_item['status']}")

        for row in ordered_rows:
            detail = self._summarize_tool_invocation(row)
            lines.append(f"- Tool {row['tool_name']} [{row['status']}]: {detail}")
        return "\n".join(lines)

    @staticmethod
    def _summarize_content(content: str, limit: int = 140) -> str:
        cleaned = " ".join((content or "").split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3].rstrip() + "..."

    def _summarize_tool_invocation(self, row, limit: int = 140) -> str:
        details: list[str] = []
        try:
            input_data = json.loads(row["input"]) if row["input"] else {}
        except json.JSONDecodeError:
            input_data = {}
        try:
            output_data = json.loads(row["output"]) if row["output"] else None
        except json.JSONDecodeError:
            output_data = None

        if input_data:
            details.append(f"input {self._summarize_content(json.dumps(input_data, sort_keys=True), limit=60)}")
        if output_data is not None:
            details.append(f"output {self._summarize_content(json.dumps(output_data, sort_keys=True), limit=60)}")
        if row["error"]:
            details.append(f"error {self._summarize_content(str(row['error']), limit=60)}")
        if not details:
            details.append("no details recorded")

        joined = "; ".join(details)
        return self._summarize_content(joined, limit=limit)


def register_maintenance_routes(app) -> None:
    from flask import jsonify, request
    from api.api.session_validation import validate_session_route_scope

    @app.route("/api/sessions/<session_id>/artifacts", methods=["GET"])
    def list_artifacts(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="list_artifacts")
        if error_response:
            return error_response
        return jsonify({
            "session_id": session_id,
            "artifacts": app.maintenance.list_artifacts(session_id),
        })

    @app.route("/api/sessions/<session_id>/transcript-ranges", methods=["GET"])
    def list_transcript_ranges(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="list_transcript_ranges")
        if error_response:
            return error_response
        range_type = request.args.get("range_type") or None
        status = request.args.get("status") or None
        return jsonify({
            "session_id": session_id,
            "ranges": app.transcript.list_ranges(session_id, range_type=range_type, status=status),
        })

    @app.route("/api/sessions/<session_id>/transcript-message-states", methods=["GET"])
    def list_transcript_message_states(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="list_transcript_message_states")
        if error_response:
            return error_response
        state_type = request.args.get("state_type") or None
        status = request.args.get("status") or None
        return jsonify({
            "session_id": session_id,
            "message_states": app.transcript.list_message_states(session_id, state_type=state_type, status=status),
        })


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll OpenCloset maintenance artifacts for idle sessions.")
    parser.add_argument("--db", dest="db_path", default=None, help="Path to the OpenCloset SQLite database.")
    parser.add_argument("--once", action="store_true", help="Run a single maintenance poll and exit.")
    parser.add_argument("--poll-interval", dest="poll_interval_seconds", type=float, default=30.0)
    parser.add_argument("--idle-threshold", dest="idle_threshold_seconds", type=float, default=15.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from api.api.app import create_app

    app = create_app(db_path=args.db_path, start_background_workers=False)
    app.maintenance_worker.poll_interval_seconds = args.poll_interval_seconds
    app.maintenance_worker.idle_threshold_seconds = args.idle_threshold_seconds
    try:
        if args.once:
            result = app.maintenance_worker.poll_once()
            logger.info(
                "Maintenance checked %d session(s) and created %d artifact(s)",
                result.checked_sessions,
                len(result.created_artifacts),
            )
            return 0

        app.maintenance_worker.run_forever()
        return 0
    finally:
        app.maintenance_worker.stop_background(timeout=0.1)
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
