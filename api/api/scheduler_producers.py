from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.api.maintenance_artifacts import MICRO_SUMMARY_ARTIFACT
from api.api.workspace_runtime import WorkCandidate


def _elapsed_seconds(ts: str) -> float:
    try:
        last = datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds()
    except (ValueError, AttributeError):
        return 0.0


class MaintenanceCandidateProducer:
    """
    Produces `maintenance_needed` WorkCandidates for sessions that are idle
    and whose maintenance artifacts are stale.

    Plugs into the SchedulerArbiter as a CandidateProducer.
    Does not replace the existing SessionMaintenanceWorker poll loop — it
    feeds candidate visibility into the unified arbiter so maintenance work
    competes fairly with other background work.
    """

    def __init__(self, app, *, idle_threshold_seconds: float = 15.0) -> None:
        self.app = app
        self.idle_threshold_seconds = idle_threshold_seconds

    def collect_candidates(self, workspace_id: str) -> list[WorkCandidate]:
        rows = self.app.db.execute(
            """SELECT id, label FROM sessions
               WHERE workspace_id = ? AND status = 'active' AND rolled_over_to IS NULL
               ORDER BY updated_at DESC""",
            (workspace_id,),
        ).fetchall()
        candidates: list[WorkCandidate] = []
        for row in rows:
            session_id = row["id"]
            if self.app.run_manager.get_active_run(session_id):
                continue
            last_msg = self.app.db.execute(
                "SELECT created_at, position FROM messages WHERE session_id = ? ORDER BY position DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if not last_msg:
                continue
            elapsed = _elapsed_seconds(last_msg["created_at"])
            if elapsed < self.idle_threshold_seconds:
                continue
            latest_summary = self.app.maintenance.get_latest_valid_artifact(session_id, MICRO_SUMMARY_ARTIFACT)
            if latest_summary and (latest_summary.get("end_position") or 0) >= last_msg["position"]:
                continue
            label = row["label"] or "Untitled Session"
            urgency = min(90, 40 + int(elapsed / 60))
            candidates.append(
                WorkCandidate(
                    id=f"maintenance-needed:{session_id}",
                    type="maintenance_needed",
                    source="maintenance",
                    workspace_id=workspace_id,
                    session_id=session_id,
                    priority=45,
                    urgency=urgency,
                    compute_cost=20,
                    interruptibility="high",
                    cooldown=120,
                    expires_at=None,
                    prerequisites=["idle_session", "no_active_run"],
                    mutability_level="read_only",
                    foreground_blocking=False,
                    title=f"Maintenance needed: {label}",
                    summary=f"Session idle {int(elapsed)}s; maintenance artifacts stale.",
                    metadata={"session_label": label, "idle_seconds": int(elapsed)},
                )
            )
        return candidates


class WatchdogCandidateProducer:
    """
    Produces `rollover_needed` WorkCandidates for sessions that have exceeded
    the rollover pressure threshold and are not currently running.

    Plugs into the SchedulerArbiter as a CandidateProducer alongside the
    existing SessionWatchdog poll loop.
    """

    def __init__(self, app) -> None:
        self.app = app

    def collect_candidates(self, workspace_id: str) -> list[WorkCandidate]:
        rows = self.app.db.execute(
            """SELECT id, label FROM sessions
               WHERE workspace_id = ? AND status = 'active' AND rolled_over_to IS NULL""",
            (workspace_id,),
        ).fetchall()
        candidates: list[WorkCandidate] = []
        for row in rows:
            session_id = row["id"]
            if self.app.run_manager.get_active_run(session_id):
                continue
            if not self.app.planning.should_rollover(session_id):
                continue
            label = row["label"] or "Untitled Session"
            candidates.append(
                WorkCandidate(
                    id=f"rollover-needed:{session_id}",
                    type="rollover_needed",
                    source="maintenance",
                    workspace_id=workspace_id,
                    session_id=session_id,
                    priority=95,
                    urgency=95,
                    compute_cost=30,
                    interruptibility="low",
                    cooldown=0,
                    expires_at=None,
                    prerequisites=["no_active_run", "rollover_threshold_exceeded"],
                    mutability_level="workspace_write",
                    foreground_blocking=True,
                    title=f"Rollover needed: {label}",
                    summary="Session has exceeded rollover threshold and should be handed off.",
                    metadata={"session_label": label},
                )
            )
        return candidates
