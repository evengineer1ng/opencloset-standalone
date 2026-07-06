from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

from flask import Flask, jsonify, request

from api.db.schema import new_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Idle tiers
# ---------------------------------------------------------------------------

class IdleTier:
    HOT = 0       # user active / hot session — bookkeeping only
    BRIEF = 1     # 10–30 s — micro summaries, digest updates, lightweight tagging
    MEDIUM = 2    # 2–10 min — compaction, queue grooming, worker review
    LONG = 3      # 10–60 min — reflective pastimes, deeper workspace review
    EXTENDED = 4  # 60+ min — autonomous plan execution, major maintenance


# Lower bound (seconds) for each tier.
TIER_THRESHOLDS_SECONDS: dict[int, float] = {
    IdleTier.BRIEF: 10.0,
    IdleTier.MEDIUM: 120.0,
    IdleTier.LONG: 600.0,
    IdleTier.EXTENDED: 3600.0,
}


def detect_idle_tier(last_activity_at: str | None) -> int:
    """Return the current idle tier (0–4) based on elapsed time since last activity."""
    if last_activity_at is None:
        return IdleTier.EXTENDED
    try:
        ts = last_activity_at.rstrip("Z")
        last = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return IdleTier.EXTENDED
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    if elapsed < TIER_THRESHOLDS_SECONDS[IdleTier.BRIEF]:
        return IdleTier.HOT
    if elapsed < TIER_THRESHOLDS_SECONDS[IdleTier.MEDIUM]:
        return IdleTier.BRIEF
    if elapsed < TIER_THRESHOLDS_SECONDS[IdleTier.LONG]:
        return IdleTier.MEDIUM
    if elapsed < TIER_THRESHOLDS_SECONDS[IdleTier.EXTENDED]:
        return IdleTier.LONG
    return IdleTier.EXTENDED


# ---------------------------------------------------------------------------
# CandidateProducer protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class CandidateProducer(Protocol):
    """
    Interface for objects that emit WorkCandidates into the scheduler.

    Producers detect conditions (stale data, blocked plans, idle sessions,
    context pressure) and surface them as candidates. They do not decide
    what runs — that is the arbiter's job.
    """

    def collect_candidates(self, workspace_id: str) -> list[Any]:
        ...


# ---------------------------------------------------------------------------
# Eligibility gate
# ---------------------------------------------------------------------------

# Foreground-blocking candidates require at least this idle tier.
TIER_REQUIRED_FOR_FOREGROUND_BLOCKING = IdleTier.MEDIUM


@dataclass
class EligibilityContext:
    idle_tier: int
    active_run_session_ids: set[str]
    now: datetime
    job_last_run: dict[str, datetime]  # job_type -> datetime of most recent run
    attention_profile: dict[str, Any] | None = None
    pastime_types_by_key: dict[str, str] = field(default_factory=dict)


def _resolve_candidate_pastime_type(candidate: Any, pastime_types_by_key: dict[str, str]) -> str | None:
    metadata = getattr(candidate, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return None
    pastime_key = metadata.get("pastime_key")
    if isinstance(pastime_key, str) and pastime_key:
        pastime_type = pastime_types_by_key.get(pastime_key)
        if pastime_type:
            return pastime_type
    pastime_type = metadata.get("pastime_type")
    if isinstance(pastime_type, str) and pastime_type:
        return pastime_type
    return None


class EligibilityGate:
    """
    Apply hard filter checks before ranking.

    Resource gates:   no conflicting active run for the session.
    Context-safety:   foreground_blocking requires Tier 2+.
    Cooldown:         candidate.cooldown seconds must have elapsed since
                      the last job of this type ran.
    """

    def is_eligible(self, candidate: Any, *, ctx: EligibilityContext) -> bool:
        metadata = getattr(candidate, "metadata", {}) or {}
        min_idle_tier = metadata.get("min_idle_tier") if isinstance(metadata, dict) else None
        max_idle_tier = metadata.get("max_idle_tier") if isinstance(metadata, dict) else None
        attention_profile = ctx.attention_profile or {}
        attention_mode = str(attention_profile.get("mode") or "warm").lower()
        pastime_type = _resolve_candidate_pastime_type(candidate, ctx.pastime_types_by_key)

        if attention_mode == "paused":
            return False
        if attention_mode == "parked" and pastime_type != "maintenance":
            return False

        allowed_pastime_types = attention_profile.get("allowed_pastime_types")
        if pastime_type and isinstance(allowed_pastime_types, list) and allowed_pastime_types and pastime_type not in allowed_pastime_types:
            return False

        max_idle_budget = attention_profile.get("max_idle_budget")
        if max_idle_budget is not None:
            try:
                if float(getattr(candidate, "compute_cost", 0)) > float(max_idle_budget):
                    return False
            except (TypeError, ValueError):
                pass

        if min_idle_tier is not None and ctx.idle_tier < int(min_idle_tier):
            return False
        if max_idle_tier is not None and ctx.idle_tier > int(max_idle_tier):
            return False

        # Context-safety: foreground-blocking work must wait for sufficient idle depth.
        if candidate.foreground_blocking and ctx.idle_tier < TIER_REQUIRED_FOR_FOREGROUND_BLOCKING:
            return False

        # Resource: session must not have an active run.
        if candidate.session_id and candidate.session_id in ctx.active_run_session_ids:
            return False

        # Cooldown: enough time must have passed since the last run of this type.
        cooldown = int(getattr(candidate, "cooldown", 0))
        if cooldown > 0:
            last_run = ctx.job_last_run.get(candidate.type)
            if last_run is not None:
                elapsed = (ctx.now - last_run).total_seconds()
                if elapsed < cooldown:
                    return False

        return True


# ---------------------------------------------------------------------------
# Deterministic scoring
# ---------------------------------------------------------------------------

_URGENCY_WEIGHT = 0.35
_PRIORITY_WEIGHT = 0.25
_IDLE_DEPTH_BONUS_WEIGHT = 0.15
_COMPUTE_COST_PENALTY_WEIGHT = 0.10
_INTERRUPTION_RISK_PENALTY_WEIGHT = 0.10
_FRESHNESS_BONUS_WEIGHT = 0.05

_INTERRUPTIBILITY_RISK: dict[str, float] = {
    "high": 0.0,
    "medium": 0.5,
    "low": 1.0,
}


def score_candidate(
    candidate: Any,
    idle_tier: int,
    *,
    attention_profile: dict[str, Any] | None = None,
    pastime_type: str | None = None,
) -> float:
    """
    Deterministic candidate score. Higher = preferred.

    Components:
      + urgency_weight × urgency (0–100)
      + priority_weight × priority (0–100)
      + idle_depth_bonus_weight × (idle_tier × 25)   [0–100]
      − compute_cost_penalty_weight × compute_cost (0–100)
      − interruption_risk_penalty_weight × risk (0–1) × 100
    """
    interruption_risk = _INTERRUPTIBILITY_RISK.get(
        str(getattr(candidate, "interruptibility", "medium")).lower(), 0.5
    )
    idle_bonus = idle_tier * 25.0  # maps 0–4 tier to 0–100
    profile = attention_profile or {}
    attention_level = float(profile.get("current_attention_level") or 50)
    baseline_priority = float(profile.get("baseline_priority") or 50)
    attention_mode = str(profile.get("mode") or "warm").lower()
    compute_multiplier = max(0.35, 1.3 - (attention_level / 100.0))
    mode_bias_map: dict[str, dict[str, float]] = {
        "active": {
            "maintenance": 6.0,
            "operational": 10.0,
            "reflective": 16.0,
            "preparatory": 10.0,
            "autonomous_execution": 12.0,
        },
        "warm": {
            "maintenance": 6.0,
            "operational": 8.0,
            "reflective": 3.0,
            "preparatory": 4.0,
            "autonomous_execution": 0.0,
        },
        "background": {
            "maintenance": 10.0,
            "operational": 3.0,
            "reflective": -12.0,
            "preparatory": -6.0,
            "autonomous_execution": -12.0,
        },
        "parked": {
            "maintenance": 12.0,
            "operational": -25.0,
            "reflective": -30.0,
            "preparatory": -30.0,
            "autonomous_execution": -35.0,
        },
        "paused": {
            "maintenance": -100.0,
            "operational": -100.0,
            "reflective": -100.0,
            "preparatory": -100.0,
            "autonomous_execution": -100.0,
        },
    }
    mode_bias = mode_bias_map.get(attention_mode, mode_bias_map["warm"]).get(pastime_type or "operational", 0.0)
    priority_bias = ((baseline_priority - 50.0) * 0.18) + ((attention_level - 50.0) * 0.22)

    return (
        _URGENCY_WEIGHT * float(getattr(candidate, "urgency", 0))
        + _PRIORITY_WEIGHT * float(getattr(candidate, "priority", 0))
        + _IDLE_DEPTH_BONUS_WEIGHT * idle_bonus
        - _COMPUTE_COST_PENALTY_WEIGHT * float(getattr(candidate, "compute_cost", 0)) * compute_multiplier
        - _INTERRUPTION_RISK_PENALTY_WEIGHT * interruption_risk * 100.0
        + priority_bias
        + mode_bias
    )


# ---------------------------------------------------------------------------
# Scheduler job persistence
# ---------------------------------------------------------------------------

SCHEDULER_JOBS_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_jobs (
    id               TEXT PRIMARY KEY,
    job_type         TEXT NOT NULL,
    workspace_id     TEXT,
    session_id       TEXT,
    source           TEXT NOT NULL DEFAULT 'cron',
    schedule         TEXT,
    last_run_at      TEXT,
    next_run_at      TEXT,
    cooldown_seconds INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'scheduled',
    metadata         TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_workspace
    ON scheduler_jobs(workspace_id, status);

CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_next_run
    ON scheduler_jobs(next_run_at, status);
"""

JOB_STATUSES = {"scheduled", "running", "completed", "failed", "disabled"}


def init_scheduler_table(db) -> None:
    db.executescript(SCHEDULER_JOBS_SQL)
    db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SchedulerJobManager:
    """Persist and query scheduler jobs (cron-like recurring tasks)."""

    def __init__(self, db) -> None:
        self.db = db

    def create_job(
        self,
        *,
        job_type: str,
        workspace_id: str | None = None,
        session_id: str | None = None,
        source: str = "cron",
        schedule: str | None = None,
        cooldown_seconds: int = 0,
        next_run_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        job_id = new_id()
        self.db.execute(
            """INSERT INTO scheduler_jobs
               (id, job_type, workspace_id, session_id, source, schedule,
                cooldown_seconds, next_run_at, status, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)""",
            (
                job_id,
                job_type,
                workspace_id,
                session_id,
                source,
                schedule,
                cooldown_seconds,
                next_run_at,
                json.dumps(metadata or {}),
                now,
                now,
            ),
        )
        self.db.commit()
        job = self.get_job(job_id)
        if not job:
            raise RuntimeError("job not found after insert")
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM scheduler_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_jobs(
        self,
        *,
        workspace_id: str | None = None,
        session_id: str | None = None,
        job_type: str | None = None,
        source: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if job_type is not None:
            clauses.append("job_type = ?")
            params.append(job_type)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self.db.execute(
            f"SELECT * FROM scheduler_jobs {where} ORDER BY next_run_at ASC, created_at ASC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_due_jobs(self, now: str | None = None) -> list[dict[str, Any]]:
        ts = now or _now()
        rows = self.db.execute(
            """SELECT * FROM scheduler_jobs
               WHERE status = 'scheduled'
                 AND (next_run_at IS NULL OR next_run_at <= ?)
               ORDER BY next_run_at ASC, created_at ASC""",
            (ts,),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def mark_running(self, job_id: str) -> dict[str, Any] | None:
        self.db.execute(
            "UPDATE scheduler_jobs SET status = 'running', updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        self.db.commit()
        return self.get_job(job_id)

    def record_run(
        self,
        job_id: str,
        *,
        next_run_at: str | None = None,
        status: str = "scheduled",
        result_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if status not in JOB_STATUSES:
            raise ValueError(f"invalid job status: {status!r}")
        now = _now()
        if result_metadata:
            existing = self.get_job(job_id)
            merged = {**(existing.get("metadata") or {} if existing else {}), **result_metadata}
            self.db.execute(
                """UPDATE scheduler_jobs
                   SET last_run_at = ?, next_run_at = ?, status = ?, metadata = ?, updated_at = ?
                   WHERE id = ?""",
                (now, next_run_at, status, json.dumps(merged), now, job_id),
            )
        else:
            self.db.execute(
                """UPDATE scheduler_jobs
                   SET last_run_at = ?, next_run_at = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (now, next_run_at, status, now, job_id),
            )
        self.db.commit()
        return self.get_job(job_id)

    def disable_job(self, job_id: str) -> dict[str, Any] | None:
        self.db.execute(
            "UPDATE scheduler_jobs SET status = 'disabled', updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        self.db.commit()
        return self.get_job(job_id)

    def get_last_run_times(self) -> dict[str, datetime]:
        """Return {job_type: last_run datetime} for cooldown enforcement."""
        rows = self.db.execute(
            """SELECT job_type, MAX(last_run_at) AS last_run_at
               FROM scheduler_jobs
               WHERE last_run_at IS NOT NULL
               GROUP BY job_type""",
        ).fetchall()
        result: dict[str, datetime] = {}
        for row in rows:
            try:
                ts = row["last_run_at"].rstrip("Z")
                result[row["job_type"]] = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                pass
        return result

    def record_candidate_execution(
        self,
        *,
        job_type: str,
        workspace_id: str | None,
        session_id: str | None,
        source: str,
        cooldown_seconds: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        job = self.create_job(
            job_type=job_type,
            workspace_id=workspace_id,
            session_id=session_id,
            source=source,
            cooldown_seconds=cooldown_seconds,
            next_run_at=None,
            metadata={"execution_record": True, **(metadata or {})},
        )
        next_run_at = None
        if cooldown_seconds > 0:
            next_run_at = (datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return self.record_run(
            job["id"],
            next_run_at=next_run_at,
            status="completed",
            result_metadata={"candidate_execution": True, **(metadata or {})},
        )

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "job_type": row["job_type"],
            "workspace_id": row["workspace_id"],
            "session_id": row["session_id"],
            "source": row["source"],
            "schedule": row["schedule"],
            "last_run_at": row["last_run_at"],
            "next_run_at": row["next_run_at"],
            "cooldown_seconds": row["cooldown_seconds"],
            "status": row["status"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


# ---------------------------------------------------------------------------
# Scheduler arbiter — single selection point for all non-foreground work
# ---------------------------------------------------------------------------

class SchedulerArbiter:
    """
    Collect candidates from all registered producers, apply eligibility gates,
    rank deterministically, and return the best work item for the current moment.

    This is the single arbitration point. Do not create parallel schedulers for
    maintenance, workers, autonomy, and pastimes — all flow through here.
    """

    def __init__(self, db, *, job_manager: SchedulerJobManager) -> None:
        self.db = db
        self.job_manager = job_manager
        self._producers: list[Any] = []

    def register_producer(self, producer: Any) -> None:
        """Register a CandidateProducer. Call this once per producer at app startup."""
        self._producers.append(producer)

    def _is_enabled_pastime_candidate(self, candidate: Any) -> bool:
        metadata = getattr(candidate, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            return True
        pastime_key = metadata.get("pastime_key")
        workspace_id = getattr(candidate, "workspace_id", None)
        if not pastime_key or not workspace_id:
            return True
        row = self.db.execute(
            "SELECT status FROM workspace_pastimes WHERE workspace_id = ? AND key = ?",
            (workspace_id, pastime_key),
        ).fetchone()
        if not row:
            return True
        return row["status"] == "enabled"

    def _get_active_run_session_ids(self) -> set[str]:
        rows = self.db.execute(
            "SELECT DISTINCT session_id FROM runs WHERE status IN ('queued', 'running')"
        ).fetchall()
        return {row["session_id"] for row in rows}

    def _get_workspace_attention_profile(self, workspace_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT workspace_id, baseline_priority, current_attention_level, mode,
                      max_idle_budget, allowed_pastime_types, notification_threshold,
                      freshness_target, review_at, expires_at, user_rationale
               FROM workspace_attention_profiles WHERE workspace_id = ?""",
            (workspace_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "workspace_id": row["workspace_id"],
            "baseline_priority": row["baseline_priority"],
            "current_attention_level": row["current_attention_level"],
            "mode": row["mode"],
            "max_idle_budget": row["max_idle_budget"],
            "allowed_pastime_types": json.loads(row["allowed_pastime_types"] or "[]"),
            "notification_threshold": row["notification_threshold"],
            "freshness_target": row["freshness_target"],
            "review_at": row["review_at"],
            "expires_at": row["expires_at"],
            "user_rationale": row["user_rationale"],
        }

    def _get_workspace_pastime_types(self, workspace_id: str) -> dict[str, str]:
        rows = self.db.execute(
            "SELECT key, pastime_type FROM workspace_pastimes WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
        return {row["key"]: row["pastime_type"] for row in rows}

    def collect_all_candidates(self, workspace_id: str) -> list[Any]:
        candidates: list[Any] = []
        for producer in self._producers:
            try:
                candidates.extend(producer.collect_candidates(workspace_id))
            except Exception:
                logger.exception("producer %r raised during collect_candidates", producer)
        return candidates

    def select_work(
        self,
        workspace_id: str,
        *,
        idle_tier: int | None = None,
        last_activity_at: str | None = None,
    ) -> tuple[Any | None, list[Any]]:
        """
        Return (selected_candidate, ranked_eligible_candidates).

        idle_tier overrides last_activity_at if provided directly.
        """
        if idle_tier is None:
            idle_tier = detect_idle_tier(last_activity_at)

        candidates = self.collect_all_candidates(workspace_id)
        active_runs = self._get_active_run_session_ids()
        last_runs = self.job_manager.get_last_run_times()
        now = datetime.now(timezone.utc)
        attention_profile = self._get_workspace_attention_profile(workspace_id)
        pastime_types_by_key = self._get_workspace_pastime_types(workspace_id)

        gate = EligibilityGate()
        ctx = EligibilityContext(
            idle_tier=idle_tier,
            active_run_session_ids=active_runs,
            now=now,
            job_last_run=last_runs,
            attention_profile=attention_profile,
            pastime_types_by_key=pastime_types_by_key,
        )

        eligible = [
            c
            for c in candidates
            if self._is_enabled_pastime_candidate(c) and gate.is_eligible(c, ctx=ctx)
        ]
        ranked = sorted(
            eligible,
            key=lambda c: score_candidate(
                c,
                idle_tier,
                attention_profile=attention_profile,
                pastime_type=_resolve_candidate_pastime_type(c, pastime_types_by_key),
            ),
            reverse=True,
        )
        selected = ranked[0] if ranked else None
        return selected, ranked

    def get_arbiter_snapshot(
        self,
        workspace_id: str,
        *,
        idle_tier: int | None = None,
        last_activity_at: str | None = None,
    ) -> dict[str, Any]:
        if idle_tier is None:
            idle_tier = detect_idle_tier(last_activity_at)
        selected, ranked = self.select_work(workspace_id, idle_tier=idle_tier)
        return {
            "workspace_id": workspace_id,
            "idle_tier": idle_tier,
            "attention_profile": self._get_workspace_attention_profile(workspace_id),
            "eligible_count": len(ranked),
            "selected": selected.to_dict() if selected is not None else None,
            "ranked": [c.to_dict() for c in ranked],
        }


# ---------------------------------------------------------------------------
# Scheduler runner — dispatches due cron-like jobs
# ---------------------------------------------------------------------------

class SchedulerRunner:
    """
    Minimal cron-like runner: check scheduler_jobs for due work and
    dispatch via registered handlers.

    Handlers are callables registered by job_type:
        handler(job: dict) -> dict | None

    The return value (if any) is stored as result_metadata on the job record.
    """

    def __init__(self, db, *, job_manager: SchedulerJobManager) -> None:
        self.db = db
        self.job_manager = job_manager
        self._handlers: dict[str, Any] = {}

    def register_handler(self, job_type: str, handler: Any) -> None:
        self._handlers[job_type] = handler

    def run_due(self, now: str | None = None) -> list[dict[str, Any]]:
        """Run all jobs due now. Returns list of run result summaries."""
        due = self.job_manager.list_due_jobs(now=now)
        results: list[dict[str, Any]] = []
        for job in due:
            job_type = job["job_type"]
            handler = self._handlers.get(job_type)
            if handler is None:
                logger.debug("no handler registered for job_type=%r, skipping", job_type)
                continue
            self.job_manager.mark_running(job["id"])
            try:
                result = handler(job)
                cooldown = int(job.get("cooldown_seconds") or 0)
                next_run = None
                if cooldown > 0:
                    from datetime import timedelta
                    next_run = (
                        datetime.now(timezone.utc) + timedelta(seconds=cooldown)
                    ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                self.job_manager.record_run(
                    job["id"],
                    next_run_at=next_run,
                    status="scheduled" if cooldown > 0 else "completed",
                    result_metadata=result or {},
                )
                results.append({"job_id": job["id"], "job_type": job_type, "status": "ok"})
            except Exception:
                logger.exception("job %s (%s) raised during execution", job["id"], job_type)
                self.job_manager.record_run(job["id"], status="failed")
                results.append({"job_id": job["id"], "job_type": job_type, "status": "error"})
        return results


class SchedulerWorker:
    """Background poller for cron-like scheduler jobs."""

    def __init__(self, app, *, poll_interval_seconds: float = 15.0) -> None:
        self.app = app
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> list[dict[str, Any]]:
        return self.app.scheduler_runner.run_due()

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_background_loop, name="opencloset-scheduler", daemon=True)
        self._thread.start()

    def stop_background(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run_background_loop(self) -> None:
        from api.api.app import create_app

        isolated_app = create_app(db_path=self.app.config["DB_PATH"], start_background_workers=False)
        isolated_worker = isolated_app.scheduler_worker
        isolated_worker.poll_interval_seconds = self.poll_interval_seconds
        try:
            while not self._stop_event.is_set():
                isolated_worker.poll_once()
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            isolated_app.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_scheduler_routes(app: Flask) -> None:
    arbiter: SchedulerArbiter = app.scheduler_arbiter
    job_manager: SchedulerJobManager = app.scheduler_jobs

    @app.route("/api/workspaces/<workspace_id>/scheduler/snapshot", methods=["GET"])
    def get_scheduler_snapshot(workspace_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        idle_tier_param = request.args.get("idle_tier")
        idle_tier = int(idle_tier_param) if idle_tier_param is not None else None
        last_activity_at = request.args.get("last_activity_at")
        snapshot = arbiter.get_arbiter_snapshot(
            workspace_id,
            idle_tier=idle_tier,
            last_activity_at=last_activity_at,
        )
        return jsonify(snapshot)

    @app.route("/api/workspaces/<workspace_id>/scheduler/jobs", methods=["GET"])
    def list_scheduler_jobs(workspace_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        status_filter = request.args.get("status")
        jobs = job_manager.list_jobs(workspace_id=workspace_id, status=status_filter)
        return jsonify({"jobs": jobs})

    @app.route("/api/workspaces/<workspace_id>/scheduler/jobs", methods=["POST"])
    def create_scheduler_job(workspace_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        data = request.get_json(silent=True) or {}
        job_type = str(data.get("job_type") or "").strip()
        if not job_type:
            return jsonify({"error": "job_type is required"}), 400
        job = job_manager.create_job(
            job_type=job_type,
            workspace_id=workspace_id,
            session_id=data.get("session_id"),
            source=str(data.get("source") or "cron"),
            schedule=data.get("schedule"),
            cooldown_seconds=int(data.get("cooldown_seconds") or 0),
            next_run_at=data.get("next_run_at"),
            metadata=data.get("metadata"),
        )
        return jsonify(job), 201

    @app.route("/api/workspaces/<workspace_id>/scheduler/jobs/<job_id>", methods=["GET"])
    def get_scheduler_job(workspace_id: str, job_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        job = job_manager.get_job(job_id)
        if not job or job.get("workspace_id") != workspace_id:
            return jsonify({"error": "job not found"}), 404
        return jsonify(job)

    @app.route("/api/workspaces/<workspace_id>/scheduler/jobs/<job_id>/disable", methods=["POST"])
    def disable_scheduler_job(workspace_id: str, job_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        job = job_manager.get_job(job_id)
        if not job or job.get("workspace_id") != workspace_id:
            return jsonify({"error": "job not found"}), 404
        updated = job_manager.disable_job(job_id)
        return jsonify(updated)
