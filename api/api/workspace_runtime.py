from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request

from api.api.maintenance import HANDOFF_CANDIDATE_ARTIFACT, VALID_ARTIFACT_STATUS
from api.db.schema import new_id


WORKER_REGISTRY: dict[str, dict[str, Any]] = {
    "handoff-review-clerk": {
        "label": "Handoff Review",
        "description": "Surfaces prepared handoff packets for paused sessions.",
        "candidate_types": ["handoff_review"],
        "signal_types": ["handoff_ready"],
    },
    "backlog-review-clerk": {
        "label": "Backlog Review",
        "description": "Flags blocked, deferred, or oversized plan backlogs for review.",
        "candidate_types": ["backlog_review"],
        "signal_types": ["backlog_review_needed"],
    },
    "context-guard-clerk": {
        "label": "Context Guard",
        "description": "Monitors sessions approaching rollover pressure before they hard-stop.",
        "candidate_types": ["context_review"],
        "signal_types": ["context_review_needed"],
    },
    "fresh-eyes-clerk": {
        "label": "Fresh Eyes",
        "description": "Runs a reflective thread-pulling pass and surfaces missingness for later review.",
        "candidate_types": ["fresh_eyes_thread_pull"],
        "signal_types": ["thread_pulling_ready"],
    },
}


CANDIDATE_SIGNAL_CONTRACT: dict[str, dict[str, str]] = {
    "handoff_review": {
        "worker_name": "handoff-review-clerk",
        "signal_type": "handoff_ready",
        "source_key_prefix": "handoff-review",
    },
    "backlog_review": {
        "worker_name": "backlog-review-clerk",
        "signal_type": "backlog_review_needed",
        "source_key_prefix": "backlog-review",
    },
    "context_review": {
        "worker_name": "context-guard-clerk",
        "signal_type": "context_review_needed",
        "source_key_prefix": "context-review",
    },
    "fresh_eyes_thread_pull": {
        "worker_name": "fresh-eyes-clerk",
        "signal_type": "thread_pulling_ready",
        "source_key_prefix": "fresh-eyes",
    },
}


CANDIDATE_PASTIME_KEYS: dict[str, str] = {
    "handoff_review": "handoff-review",
    "backlog_review": "backlog-review",
    "context_review": "context-review",
    "fresh_eyes_thread_pull": "fresh-eyes-thread-pulling",
}


WORKSPACE_SIGNAL_SQL = """
CREATE TABLE IF NOT EXISTS workspace_signals (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT,
    worker_name TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    source_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_signals_workspace_status
ON workspace_signals(workspace_id, status, updated_at DESC);
"""


def init_workspace_runtime_table(db) -> None:
    db.executescript(WORKSPACE_SIGNAL_SQL)
    db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _trim_text(value: str, limit: int = 220) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


@dataclass
class WorkCandidate:
    id: str
    type: str
    source: str
    workspace_id: str
    session_id: str
    priority: int
    urgency: int
    compute_cost: int
    interruptibility: str
    cooldown: int
    expires_at: str | None
    prerequisites: list[str]
    mutability_level: str
    foreground_blocking: bool
    title: str
    summary: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "priority": self.priority,
            "urgency": self.urgency,
            "compute_cost": self.compute_cost,
            "interruptibility": self.interruptibility,
            "cooldown": self.cooldown,
            "expires_at": self.expires_at,
            "prerequisites": list(self.prerequisites),
            "mutability_level": self.mutability_level,
            "foreground_blocking": self.foreground_blocking,
            "title": self.title,
            "summary": self.summary,
            "metadata": dict(self.metadata),
        }


@dataclass
class WorkspacePollResult:
    workspace_id: str
    candidates: list[WorkCandidate]
    selected_candidate: WorkCandidate | None
    selected_pastime: dict[str, Any] | None
    emitted_signal: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_candidate": self.selected_candidate.to_dict() if self.selected_candidate else None,
            "selected_pastime": dict(self.selected_pastime) if self.selected_pastime else None,
            "emitted_signal": self.emitted_signal,
        }


@dataclass
class WorkspaceWorkerRecord:
    name: str
    label: str
    description: str
    candidate_types: list[str]
    signal_types: list[str]
    queue_count: int
    open_signal_count: int
    status: str
    top_candidate: dict[str, Any] | None
    top_signal: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "candidate_types": list(self.candidate_types),
            "signal_types": list(self.signal_types),
            "queue_count": self.queue_count,
            "open_signal_count": self.open_signal_count,
            "status": self.status,
            "top_candidate": dict(self.top_candidate) if self.top_candidate else None,
            "top_signal": dict(self.top_signal) if self.top_signal else None,
        }


@dataclass
class WorkspacePastimeSelection:
    pastime: dict[str, Any]
    candidate: dict[str, Any]
    selected_at: str
    selection_reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.pastime)
        payload.update(
            {
                "matched_candidate": dict(self.candidate),
                "selected_at": self.selected_at,
                "selection_reason": self.selection_reason,
            }
        )
        return payload


class WorkspaceRuntimeManager:
    """Minimal operational workspace runtime for handoff review and signal emission."""

    def __init__(self, db, *, workspaces, planning, maintenance, run_manager, event_logger=None) -> None:
        self.db = db
        self.workspaces = workspaces
        self.planning = planning
        self.maintenance = maintenance
        self.run_manager = run_manager
        self.event_logger = event_logger
        self.arbiter = None
        self.reflective_pastimes = None

    def list_open_signals(self, workspace_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT id, workspace_id, session_id, worker_name, signal_type, source_key,
                      title, summary, status, priority, metadata, created_at, updated_at
               FROM workspace_signals
               WHERE workspace_id = ? AND status IN ('open', 'escalated')
               ORDER BY CASE status WHEN 'escalated' THEN 0 ELSE 1 END,
                        priority DESC, updated_at DESC, rowid DESC LIMIT ?""",
            (workspace_id, limit),
        ).fetchall()
        return [self._signal_row_to_dict(row) for row in rows]

    def collect_candidates(self, workspace_id: str) -> list[WorkCandidate]:
        rows = self.db.execute(
            """SELECT id, label, status, created_at, updated_at
               FROM sessions WHERE workspace_id = ? AND status = 'active'
               ORDER BY updated_at DESC, created_at DESC""",
            (workspace_id,),
        ).fetchall()
        candidates: list[WorkCandidate] = []
        for row in rows:
            session_id = row["id"]
            if self.run_manager.get_active_run(session_id):
                continue
            plan = self.planning.get_plan(session_id)
            session_label = row["label"] or "Untitled Session"
            if plan:
                candidates.extend(self._build_plan_candidates(workspace_id, session_id, session_label, plan))

        return sorted(
            candidates,
            key=lambda candidate: (
                -candidate.priority,
                -candidate.urgency,
                candidate.compute_cost,
                candidate.session_id,
            ),
        )

    def _build_plan_candidates(
        self,
        workspace_id: str,
        session_id: str,
        session_label: str,
        plan: dict[str, Any],
    ) -> list[WorkCandidate]:
        candidates: list[WorkCandidate] = []
        plan_status = str(plan.get("status") or "active")
        next_item = plan.get("next_item")

        artifact = self.maintenance.get_latest_valid_artifact(session_id, HANDOFF_CANDIDATE_ARTIFACT)
        if plan_status == "paused" and artifact and artifact.get("status") == VALID_ARTIFACT_STATUS:
            summary = _trim_text(artifact.get("content") or "Prepared handoff candidate")
            if next_item and next_item.get("content"):
                summary = f"{summary} Next: {next_item['content']}"
            candidates.append(
                WorkCandidate(
                    id=f"handoff-review:{session_id}",
                    type="handoff_review",
                    source="worker",
                    workspace_id=workspace_id,
                    session_id=session_id,
                    priority=90,
                    urgency=90,
                    compute_cost=1,
                    interruptibility="high",
                    cooldown=300,
                    expires_at=None,
                    prerequisites=["valid_handoff_candidate", "no_active_run"],
                    mutability_level="read_only",
                    foreground_blocking=False,
                    title=f"Review handoff for {session_label}",
                    summary=summary,
                    metadata={
                        "pastime_key": CANDIDATE_PASTIME_KEYS["handoff_review"],
                        "artifact_id": artifact["id"],
                        "plan_status": plan_status,
                        "plan_id": plan.get("id"),
                        "session_label": session_label,
                        "next_item": next_item["content"] if next_item else None,
                    },
                )
            )

        open_items = [
            item for item in plan.get("items") or []
            if not item.get("archived") and item.get("status") in {"todo", "blocked", "deferred"}
        ]
        blocked_items = [item for item in open_items if item.get("status") == "blocked"]
        deferred_items = [item for item in open_items if item.get("status") == "deferred"]
        todo_items = [item for item in open_items if item.get("status") == "todo"]
        guard = plan.get("context_guard") or {}
        tokens_used = int(guard.get("tokens_used") or 0)
        rollover_threshold = int(guard.get("rollover_threshold") or 0)
        pressure_pct = int(round((tokens_used / rollover_threshold) * 100)) if rollover_threshold > 0 else 0

        if blocked_items or deferred_items or len(todo_items) >= 3:
            summary_parts = []
            if blocked_items:
                summary_parts.append(f"{len(blocked_items)} blocked")
            if deferred_items:
                summary_parts.append(f"{len(deferred_items)} deferred")
            if todo_items:
                summary_parts.append(f"{len(todo_items)} todo")
            lead_item = blocked_items[0] if blocked_items else deferred_items[0] if deferred_items else todo_items[0]
            summary = f"Backlog needs review: {', '.join(summary_parts)}."
            if lead_item and lead_item.get("content"):
                summary = f"{summary} Lead item: {lead_item['content']}"

            priority = 80 if blocked_items else 68 if deferred_items else 62
            urgency = 85 if blocked_items else 65 if deferred_items else 55
            candidates.append(
                WorkCandidate(
                    id=f"backlog-review:{session_id}",
                    type="backlog_review",
                    source="worker",
                    workspace_id=workspace_id,
                    session_id=session_id,
                    priority=priority,
                    urgency=urgency,
                    compute_cost=1,
                    interruptibility="high",
                    cooldown=600,
                    expires_at=None,
                    prerequisites=["active_plan", "no_active_run"],
                    mutability_level="read_only",
                    foreground_blocking=False,
                    title=f"Review backlog for {session_label}",
                    summary=_trim_text(summary),
                    metadata={
                        "pastime_key": CANDIDATE_PASTIME_KEYS["backlog_review"],
                        "plan_id": plan.get("id"),
                        "plan_status": plan_status,
                        "session_label": session_label,
                        "blocked_count": len(blocked_items),
                        "deferred_count": len(deferred_items),
                        "todo_count": len(todo_items),
                        "lead_item_id": lead_item.get("id") if lead_item else None,
                        "lead_item": lead_item.get("content") if lead_item else None,
                    },
                )
            )

        if plan_status == "active" and rollover_threshold > 0 and pressure_pct >= 75:
            high_pressure = pressure_pct >= 90
            priority = 88 if high_pressure else 66
            urgency = 88 if high_pressure else 72
            summary = (
                f"Context pressure is {pressure_pct}% of rollover threshold "
                f"({tokens_used}/{rollover_threshold} effective tokens)."
            )
            if next_item and next_item.get("content"):
                summary = f"{summary} Next: {next_item['content']}"

            candidates.append(
                WorkCandidate(
                    id=f"context-review:{session_id}",
                    type="context_review",
                    source="worker",
                    workspace_id=workspace_id,
                    session_id=session_id,
                    priority=priority,
                    urgency=urgency,
                    compute_cost=1,
                    interruptibility="high",
                    cooldown=300,
                    expires_at=None,
                    prerequisites=["active_plan", "no_active_run", "context_guard_high"],
                    mutability_level="read_only",
                    foreground_blocking=False,
                    title=f"Review context pressure for {session_label}",
                    summary=_trim_text(summary),
                    metadata={
                        "pastime_key": CANDIDATE_PASTIME_KEYS["context_review"],
                        "plan_id": plan.get("id"),
                        "plan_status": plan_status,
                        "session_label": session_label,
                        "tokens_used": tokens_used,
                        "rollover_threshold": rollover_threshold,
                        "pressure_pct": pressure_pct,
                        "next_item": next_item["content"] if next_item else None,
                    },
                )
            )

        return candidates

    def get_runtime_snapshot(self, workspace_id: str) -> dict[str, Any]:
        candidates = self._collect_candidate_pool(workspace_id)
        signals = self.list_open_signals(workspace_id)
        pastimes = self.workspaces.list_workspace_pastimes(workspace_id, limit=100)
        attention_profile = self.workspaces.get_workspace_attention_profile(workspace_id)
        selected_candidate = self._select_candidate(workspace_id)
        selected_pastime = self._resolve_selected_pastime(workspace_id, selected_candidate, touch=False) if selected_candidate else None
        workers = self._build_worker_records(candidates, signals)
        return {
            "workspace_id": workspace_id,
            "attention_profile": attention_profile,
            "candidates": [candidate.to_dict() for candidate in candidates],
            "top_candidate": selected_candidate.to_dict() if selected_candidate else candidates[0].to_dict() if candidates else None,
            "pastimes": pastimes,
            "selected_pastime": selected_pastime.to_dict() if selected_pastime else None,
            "signals": signals,
            "workers": [worker.to_dict() for worker in workers],
        }

    def _build_worker_records(
        self,
        candidates: list[WorkCandidate],
        signals: list[dict[str, Any]],
    ) -> list[WorkspaceWorkerRecord]:
        worker_candidates: dict[str, list[WorkCandidate]] = {name: [] for name in WORKER_REGISTRY}
        worker_signals: dict[str, list[dict[str, Any]]] = {name: [] for name in WORKER_REGISTRY}

        for candidate in candidates:
            contract = CANDIDATE_SIGNAL_CONTRACT.get(candidate.type)
            if not contract:
                continue
            worker_candidates.setdefault(contract["worker_name"], []).append(candidate)

        for signal in signals:
            worker_signals.setdefault(signal["worker_name"], []).append(signal)

        records: list[WorkspaceWorkerRecord] = []
        for worker_name, definition in WORKER_REGISTRY.items():
            queue = worker_candidates.get(worker_name, [])
            open_items = worker_signals.get(worker_name, [])
            status = "attention" if open_items else "queued" if queue else "idle"
            records.append(
                WorkspaceWorkerRecord(
                    name=worker_name,
                    label=definition["label"],
                    description=definition["description"],
                    candidate_types=list(definition["candidate_types"]),
                    signal_types=list(definition["signal_types"]),
                    queue_count=len(queue),
                    open_signal_count=len(open_items),
                    status=status,
                    top_candidate=queue[0].to_dict() if queue else None,
                    top_signal=dict(open_items[0]) if open_items else None,
                )
            )

        return sorted(
            records,
            key=lambda record: (
                0 if record.status == "attention" else 1 if record.status == "queued" else 2,
                -record.open_signal_count,
                -record.queue_count,
                record.name,
            ),
        )

    def poll_once(self) -> list[WorkspacePollResult]:
        workspaces = self.workspaces.list_workspaces(status="active")
        results: list[WorkspacePollResult] = []
        for workspace in workspaces:
            results.append(self.poll_workspace(workspace["id"]))
        return results

    def poll_workspace(self, workspace_id: str) -> WorkspacePollResult:
        candidates = self._collect_candidate_pool(workspace_id)
        # Expire stale open signals before emitting new ones.
        active_source_keys = {self._candidate_source_key(c) for c in candidates}
        self._cleanup_stale_signals(workspace_id, active_source_keys)
        selected = self._select_candidate(workspace_id)
        selected_pastime = self._resolve_selected_pastime(workspace_id, selected, touch=True) if selected else None
        emitted_signal = self._dispatch_candidate(selected, selected_pastime=selected_pastime) if selected else None
        self._run_clo_review_loop(workspace_id)
        return WorkspacePollResult(
            workspace_id=workspace_id,
            candidates=candidates,
            selected_candidate=selected,
            selected_pastime=selected_pastime.to_dict() if selected_pastime else None,
            emitted_signal=emitted_signal,
        )

    def _collect_candidate_pool(self, workspace_id: str) -> list[WorkCandidate]:
        if self.arbiter is not None:
            candidates = self.arbiter.collect_all_candidates(workspace_id)
            return sorted(
                candidates,
                key=lambda candidate: (
                    -candidate.priority,
                    -candidate.urgency,
                    candidate.compute_cost,
                    candidate.session_id,
                    candidate.type,
                ),
            )
        return self.collect_candidates(workspace_id)

    def _select_candidate(self, workspace_id: str) -> WorkCandidate | None:
        if self.arbiter is None:
            candidates = self.collect_candidates(workspace_id)
            selected_pastime = self._resolve_selected_pastime(workspace_id, candidates[0] if candidates else None, touch=False)
            if not selected_pastime:
                return None
            return candidates[0] if candidates else None
        last_activity_at = self._get_workspace_last_activity_at(workspace_id)
        selected, _ = self.arbiter.select_work(workspace_id, last_activity_at=last_activity_at)
        return selected

    def _get_workspace_last_activity_at(self, workspace_id: str) -> str | None:
        row = self.db.execute(
            "SELECT MAX(updated_at) AS updated_at FROM sessions WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if not row:
            return None
        return row["updated_at"]

    def _candidate_source_key(self, candidate: WorkCandidate) -> str | None:
        contract = CANDIDATE_SIGNAL_CONTRACT.get(candidate.type)
        if not contract:
            return None
        return f"{contract['source_key_prefix']}:{candidate.session_id}"

    def _cleanup_stale_signals(self, workspace_id: str, active_source_keys: set[str | None]) -> int:
        """Auto-resolve open signals whose source candidate no longer exists."""
        rows = self.db.execute(
            """SELECT id, source_key FROM workspace_signals
               WHERE workspace_id = ? AND status = 'open'""",
            (workspace_id,),
        ).fetchall()
        now = _now()
        resolved_count = 0
        for row in rows:
            if row["source_key"] not in active_source_keys:
                self.db.execute(
                    "UPDATE workspace_signals SET status = 'resolved', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                resolved_count += 1
        if resolved_count:
            self.db.commit()
        return resolved_count

    def _resolve_selected_pastime(
        self,
        workspace_id: str,
        candidate: WorkCandidate | None,
        *,
        touch: bool,
    ) -> WorkspacePastimeSelection | None:
        if not candidate:
            return None
        metadata = dict(candidate.metadata or {})
        pastime_key = metadata.get("pastime_key") or CANDIDATE_PASTIME_KEYS.get(candidate.type)
        if not pastime_key:
            return None
        pastimes = self.workspaces.list_workspace_pastimes(workspace_id, limit=100)
        pastime = next((item for item in pastimes if item.get("key") == pastime_key and item.get("status") == "enabled"), None)
        if not pastime:
            return None
        selected_at = _now()
        updated_pastime = pastime
        if touch:
            updated_pastime = self.workspaces.mark_workspace_pastime_selected(
                workspace_id,
                pastime["id"],
                selected_at=selected_at,
            ) or pastime
        return WorkspacePastimeSelection(
            pastime=updated_pastime,
            candidate=candidate.to_dict(),
            selected_at=selected_at,
            selection_reason=f"Matched {candidate.type} candidate: {candidate.title}",
        )

    def _pastime_on_cooldown(self, pastime: dict[str, Any]) -> bool:
        last_selected_at = pastime.get("last_selected_at")
        cooldown_seconds = int(pastime.get("cooldown_seconds") or 0)
        if not last_selected_at or cooldown_seconds <= 0:
            return False
        try:
            selected_at = datetime.fromisoformat(str(last_selected_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        elapsed_seconds = (datetime.now(timezone.utc) - selected_at).total_seconds()
        return elapsed_seconds < cooldown_seconds

    def _dispatch_candidate(
        self,
        candidate: WorkCandidate,
        *,
        selected_pastime: WorkspacePastimeSelection | None,
    ) -> dict[str, Any] | None:
        executed_candidate = candidate
        session_id = candidate.session_id
        if self.event_logger and session_id:
            self.event_logger.log(
                session_id,
                "pastime_started",
                {
                    "workspace_id": candidate.workspace_id,
                    "candidate_id": candidate.id,
                    "candidate_type": candidate.type,
                    "pastime_key": selected_pastime.pastime["key"] if selected_pastime else None,
                },
            )

        if candidate.type == "fresh_eyes_thread_pull" and self.reflective_pastimes is not None:
            executed_candidate = self.reflective_pastimes.execute_candidate(
                candidate,
                selected_pastime=selected_pastime.to_dict() if selected_pastime else None,
            )

        emitted_signal = None
        if CANDIDATE_SIGNAL_CONTRACT.get(executed_candidate.type):
            thread_count = int(executed_candidate.metadata.get("thread_candidate_count") or 0)
            if executed_candidate.type != "fresh_eyes_thread_pull" or thread_count > 0:
                emitted_signal = self._emit_signal(executed_candidate, selected_pastime=selected_pastime)

        if self.arbiter is not None:
            self.arbiter.job_manager.record_candidate_execution(
                job_type=executed_candidate.type,
                workspace_id=executed_candidate.workspace_id,
                session_id=executed_candidate.session_id,
                source=executed_candidate.source,
                cooldown_seconds=int(executed_candidate.cooldown or 0),
                metadata={
                    "candidate_id": executed_candidate.id,
                    "pastime_key": selected_pastime.pastime["key"] if selected_pastime else None,
                    "signal_id": emitted_signal["id"] if emitted_signal else None,
                },
            )

        if self.event_logger and session_id:
            self.event_logger.log(
                session_id,
                "pastime_completed",
                {
                    "workspace_id": candidate.workspace_id,
                    "candidate_id": executed_candidate.id,
                    "candidate_type": executed_candidate.type,
                    "pastime_key": selected_pastime.pastime["key"] if selected_pastime else None,
                    "thread_candidate_count": int(executed_candidate.metadata.get("thread_candidate_count") or 0),
                    "signal_id": emitted_signal["id"] if emitted_signal else None,
                },
            )

        return emitted_signal

    def _run_clo_review_loop(self, workspace_id: str) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        for signal in self.list_open_signals(workspace_id, limit=50):
            review = self._review_signal_with_clo(workspace_id, signal)
            if review:
                reviews.append(review)

        pending_thread_captures = [
            capture
            for capture in self.workspaces.list_workspace_captures(workspace_id, status="pending", limit=100)
            if capture.get("event_type") == "thread_candidate"
        ]
        for capture in pending_thread_captures:
            review = self._review_thread_capture_with_clo(workspace_id, capture)
            if review:
                reviews.append(review)
        return reviews

    def _review_signal_with_clo(self, workspace_id: str, signal: dict[str, Any]) -> dict[str, Any] | None:
        metadata = dict(signal.get("metadata") or {})
        if metadata.get("clo_reviewed_at"):
            return None
        if signal.get("signal_type") == "thread_pulling_ready":
            return None
        session_id = signal.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        plan = self.planning.get_plan(session_id)
        if not plan:
            return None

        proposal_type, payload, summary = self._build_clo_signal_proposal(signal, plan)
        if not proposal_type:
            return None

        proposal = self.planning.submit_plan_proposal(
            session_id,
            proposal_type,
            payload,
            plan_id=plan.get("id"),
            summary=summary,
            proposed_by="clo",
        )
        reviewed_signal = self.update_signal_status(
            workspace_id,
            signal["id"],
            signal["status"],
            metadata={
                "clo_reviewed_at": _now(),
                "clo_review_summary": summary,
                "clo_proposal_id": proposal["id"],
                "clo_proposal_type": proposal["proposal_type"],
            },
        )
        self.workspaces.create_workspace_activity_capture(
            workspace_id=workspace_id,
            session_id=session_id,
            source="clo_review",
            event_type="clo_review",
            content=f"CLO queued proposal from signal: {summary}",
            metadata={
                "signal_id": signal["id"],
                "signal_type": signal["signal_type"],
                "proposal_id": proposal["id"],
                "proposal_type": proposal["proposal_type"],
                "summary": summary,
            },
            status="processed",
        )
        if self.event_logger:
            self.event_logger.log(
                session_id,
                "clo_review_completed",
                {
                    "workspace_id": workspace_id,
                    "source_kind": "signal",
                    "source_id": signal["id"],
                    "signal_type": signal["signal_type"],
                    "proposal_id": proposal["id"],
                    "proposal_type": proposal["proposal_type"],
                },
            )
        return {"signal": reviewed_signal, "proposal": proposal}

    def _review_thread_capture_with_clo(self, workspace_id: str, capture: dict[str, Any]) -> dict[str, Any] | None:
        metadata = dict(capture.get("metadata") or {})
        if metadata.get("clo_reviewed_at"):
            return None
        session_id = capture.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        plan = self.planning.get_plan(session_id)
        if not plan:
            return None

        title = str(metadata.get("title") or "Investigate fresh-eyes thread").strip()
        question = capture.get("content", "").split("\n", 1)[0].strip()
        proposal_content = f"Investigate thread: {title}"
        if question:
            proposal_content = _trim_text(f"{proposal_content} - {question}", limit=180)
        summary = f"CLO: thread pull -> {title}"
        proposal = self.planning.submit_plan_proposal(
            session_id,
            "add_item",
            {"content": proposal_content, "status": "todo"},
            plan_id=plan.get("id"),
            summary=summary,
            proposed_by="clo",
        )
        updated_capture = self.workspaces.update_workspace_capture(
            workspace_id,
            capture["id"],
            status="processed",
            processed_at=_now(),
            metadata={
                **metadata,
                "clo_reviewed_at": _now(),
                "clo_review_summary": summary,
                "clo_proposal_id": proposal["id"],
                "clo_proposal_type": proposal["proposal_type"],
            },
        )
        self.workspaces.create_workspace_activity_capture(
            workspace_id=workspace_id,
            session_id=session_id,
            source="clo_review",
            event_type="clo_review",
            content=f"CLO queued proposal from thread candidate: {title}",
            metadata={
                "capture_id": capture["id"],
                "thread_id": metadata.get("thread_id"),
                "thread_type": metadata.get("thread_type"),
                "proposal_id": proposal["id"],
                "proposal_type": proposal["proposal_type"],
                "summary": summary,
            },
            status="processed",
        )
        if self.event_logger:
            self.event_logger.log(
                session_id,
                "clo_review_completed",
                {
                    "workspace_id": workspace_id,
                    "source_kind": "capture",
                    "source_id": capture["id"],
                    "event_type": capture["event_type"],
                    "proposal_id": proposal["id"],
                    "proposal_type": proposal["proposal_type"],
                },
            )
        return {"capture": updated_capture, "proposal": proposal}

    def _build_clo_signal_proposal(
        self,
        signal: dict[str, Any],
        plan: dict[str, Any],
    ) -> tuple[str | None, dict[str, Any], str]:
        metadata = dict(signal.get("metadata") or {})
        session_label = str(metadata.get("session_label") or signal.get("title") or "session")
        signal_type = str(signal.get("signal_type") or "")
        if signal_type == "handoff_ready":
            return (
                "activate_plan",
                {"plan_id": plan["id"]},
                f"CLO: resume paused handoff for {session_label}",
            )
        if signal_type == "context_review_needed":
            focus = str(metadata.get("next_item") or signal.get("summary") or "context pressure review").strip()
            return (
                "add_item",
                {"content": _trim_text(f"Address context pressure: {focus}", limit=180), "status": "todo"},
                f"CLO: address context pressure for {session_label}",
            )
        if signal_type == "backlog_review_needed":
            lead_item = str(metadata.get("lead_item") or signal.get("summary") or "backlog review").strip()
            return (
                "add_item",
                {"content": _trim_text(f"Review backlog signal: {lead_item}", limit=180), "status": "todo"},
                f"CLO: review backlog signal for {session_label}",
            )
        return (None, {}, "")

    def get_signal(self, workspace_id: str, signal_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            """SELECT id, workspace_id, session_id, worker_name, signal_type, source_key,
                      title, summary, status, priority, metadata, created_at, updated_at
               FROM workspace_signals
               WHERE workspace_id = ? AND id = ?""",
            (workspace_id, signal_id),
        ).fetchone()
        return self._signal_row_to_dict(row) if row else None

    def update_signal_status(self, workspace_id: str, signal_id: str, status: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        signal = self.get_signal(workspace_id, signal_id)
        if not signal:
            raise ValueError("workspace signal not found")
        if status not in {"open", "escalated", "resolved"}:
            raise ValueError("invalid workspace signal status")
        payload = dict(signal.get("metadata") or {})
        if metadata:
            payload.update(metadata)
        now = _now()
        self.db.execute(
            "UPDATE workspace_signals SET status = ?, metadata = ?, updated_at = ? WHERE id = ?",
            (status, json.dumps(payload), now, signal_id),
        )
        self.db.commit()
        updated = self.get_signal(workspace_id, signal_id)
        if not updated:
            raise ValueError("workspace signal not found after update")
        return updated

    def apply_signal_action(self, workspace_id: str, signal_id: str, action: str) -> dict[str, Any]:
        signal = self.get_signal(workspace_id, signal_id)
        if not signal:
            raise ValueError("workspace signal not found")
        if signal["status"] != "open":
            raise ValueError("workspace signal is not open")

        metadata = signal.get("metadata") or {}
        session_id = signal.get("session_id")
        updated_item = None
        if action == "start_lead_item" and signal["signal_type"] == "backlog_review_needed":
            plan_id = metadata.get("plan_id")
            lead_item_id = metadata.get("lead_item_id")
            if not session_id or not isinstance(plan_id, str) or not isinstance(lead_item_id, str):
                raise ValueError("backlog review signal is missing actionable plan metadata")
            updated_item = self.planning.update_plan_item(session_id, plan_id, lead_item_id, status="doing")
        elif action == "defer_lead_item" and signal["signal_type"] == "backlog_review_needed":
            plan_id = metadata.get("plan_id")
            lead_item_id = metadata.get("lead_item_id")
            if not session_id or not isinstance(plan_id, str) or not isinstance(lead_item_id, str):
                raise ValueError("backlog review signal is missing actionable plan metadata")
            updated_item = self.planning.update_plan_item(session_id, plan_id, lead_item_id, status="deferred")
        elif action == "pause_plan" and signal["signal_type"] == "context_review_needed":
            if not session_id:
                raise ValueError("context review signal is missing session metadata")
            self.planning.set_status(session_id, "paused")
        elif action == "resume_plan" and signal["signal_type"] == "handoff_ready":
            if not session_id:
                raise ValueError("handoff review signal is missing session metadata")
            self.planning.set_status(session_id, "active")
        elif action in {"escalate_to_buddy", "escalate_to_clo"}:
            escalation_target = "buddy" if action == "escalate_to_buddy" else "clo"
            escalated_signal = self.update_signal_status(
                workspace_id,
                signal_id,
                "escalated",
                metadata={
                    "escalation_target": escalation_target,
                    "escalation_action": action,
                    "escalated_at": _now(),
                },
            )
            if self.event_logger and session_id:
                self.event_logger.log(
                    session_id,
                    "workspace_signal_escalated",
                    {
                        "workspace_id": workspace_id,
                        "signal_id": signal_id,
                        "signal_type": signal["signal_type"],
                        "target": escalation_target,
                    },
                )
            self.workspaces.create_workspace_activity_capture(
                workspace_id=workspace_id,
                session_id=session_id,
                source="workspace_review",
                event_type="signal_review",
                content=f"Escalated workspace signal to {escalation_target}: {signal['title']}",
                metadata={
                    "signal_id": signal_id,
                    "signal_type": signal["signal_type"],
                    "worker_name": signal["worker_name"],
                    "action": action,
                    "resolution_state": "escalated",
                    "target": escalation_target,
                },
                status="pending",
            )
            return {
                "signal": escalated_signal,
                "updated_item": None,
                "snapshot": self.get_runtime_snapshot(workspace_id),
            }
        else:
            raise ValueError("unsupported workspace signal action")

        updated_signal = self.update_signal_status(
            workspace_id,
            signal_id,
            "resolved",
            metadata={
                "resolution_action": action,
                "resolved_item_id": updated_item["id"] if updated_item else None,
            },
        )
        if self.event_logger:
            self.event_logger.log(
                session_id,
                "workspace_signal_resolved",
                {
                    "workspace_id": workspace_id,
                    "signal_id": signal_id,
                    "signal_type": signal["signal_type"],
                    "action": action,
                    "item_id": updated_item["id"] if updated_item else None,
                },
            )
        self.workspaces.create_workspace_activity_capture(
            workspace_id=workspace_id,
            session_id=session_id,
            source="workspace_review",
            event_type="signal_review",
            content=f"Resolved workspace signal via {action}: {signal['title']}",
            metadata={
                "signal_id": signal_id,
                "signal_type": signal["signal_type"],
                "worker_name": signal["worker_name"],
                "action": action,
                "resolution_state": "resolved",
                "resolved_item_id": updated_item["id"] if updated_item else None,
            },
            status="processed",
        )
        return {
            "signal": updated_signal,
            "updated_item": updated_item,
            "snapshot": self.get_runtime_snapshot(workspace_id),
        }

    def _emit_signal(
        self,
        candidate: WorkCandidate,
        *,
        selected_pastime: WorkspacePastimeSelection | None = None,
    ) -> dict[str, Any]:
        worker_name, signal_type, source_key = self._signal_contract(candidate)
        now = _now()
        payload = dict(candidate.metadata)
        payload.update({
            "candidate_id": candidate.id,
            "candidate_type": candidate.type,
        })
        if selected_pastime:
            payload.update(
                {
                    "pastime_id": selected_pastime.pastime["id"],
                    "pastime_key": selected_pastime.pastime["key"],
                    "pastime_type": selected_pastime.pastime["pastime_type"],
                    "pastime_selected_at": selected_pastime.selected_at,
                }
            )

        existing = self.db.execute(
            """SELECT id, status, metadata FROM workspace_signals
               WHERE workspace_id = ? AND source_key = ?""",
            (candidate.workspace_id, source_key),
        ).fetchone()
        created_new_signal = existing is None

        if existing:
            next_status = existing["status"] if existing["status"] == "escalated" else "open"
            existing_metadata = json.loads(existing["metadata"] or "{}")
            payload = {
                **payload,
                **{
                    k: v
                    for k, v in existing_metadata.items()
                    if k.startswith("escalation_") or k.startswith("clo_")
                },
            }
            self.db.execute(
                """UPDATE workspace_signals
                   SET session_id = ?, worker_name = ?, signal_type = ?, title = ?, summary = ?,
                       status = ?, priority = ?, metadata = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    candidate.session_id,
                    worker_name,
                    signal_type,
                    candidate.title,
                    candidate.summary,
                    next_status,
                    candidate.priority,
                    json.dumps(payload),
                    now,
                    existing["id"],
                ),
            )
            signal_id = existing["id"]
        else:
            signal_id = new_id()
            self.db.execute(
                """INSERT INTO workspace_signals
                   (id, workspace_id, session_id, worker_name, signal_type, source_key, title, summary,
                    status, priority, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
                (
                    signal_id,
                    candidate.workspace_id,
                    candidate.session_id,
                    worker_name,
                    signal_type,
                    source_key,
                    candidate.title,
                    candidate.summary,
                    candidate.priority,
                    json.dumps(payload),
                    now,
                    now,
                ),
            )

        self.db.commit()
        signal = self.db.execute(
            """SELECT id, workspace_id, session_id, worker_name, signal_type, source_key,
                      title, summary, status, priority, metadata, created_at, updated_at
               FROM workspace_signals WHERE id = ?""",
            (signal_id,),
        ).fetchone()
        signal_dict = self._signal_row_to_dict(signal)

        if self.event_logger and candidate.session_id:
            self.event_logger.log(
                candidate.session_id,
                "scheduler_event",
                {
                    "workspace_id": candidate.workspace_id,
                    "candidate_id": candidate.id,
                    "candidate_type": candidate.type,
                    "source": candidate.source,
                    "priority": candidate.priority,
                    "urgency": candidate.urgency,
                },
            )
            self.event_logger.log(
                candidate.session_id,
                "worker_signal",
                {
                    "workspace_id": candidate.workspace_id,
                    "signal_id": signal_dict["id"],
                    "signal_type": signal_dict["signal_type"],
                    "worker_name": signal_dict["worker_name"],
                },
            )

        if created_new_signal:
            self.workspaces.create_workspace_activity_capture(
                workspace_id=candidate.workspace_id,
                session_id=candidate.session_id,
                source="workspace_runtime",
                event_type="workspace_signal",
                content=f"{signal_dict['title']}: {signal_dict['summary']}",
                metadata={
                    "signal_id": signal_dict["id"],
                    "signal_type": signal_dict["signal_type"],
                    "worker_name": signal_dict["worker_name"],
                    "candidate_id": candidate.id,
                    "candidate_type": candidate.type,
                    "pastime_id": selected_pastime.pastime["id"] if selected_pastime else None,
                    "pastime_key": selected_pastime.pastime["key"] if selected_pastime else None,
                },
                status="pending",
            )

        return signal_dict

    def _signal_contract(self, candidate: WorkCandidate) -> tuple[str, str, str]:
        contract = CANDIDATE_SIGNAL_CONTRACT.get(candidate.type)
        if not contract:
            raise ValueError(f"Unsupported workspace candidate type: {candidate.type}")
        return (
            contract["worker_name"],
            contract["signal_type"],
            f"{contract['source_key_prefix']}:{candidate.session_id}",
        )

    def _signal_row_to_dict(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "session_id": row["session_id"],
            "worker_name": row["worker_name"],
            "signal_type": row["signal_type"],
            "source_key": row["source_key"],
            "title": row["title"],
            "summary": row["summary"],
            "status": row["status"],
            "priority": row["priority"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


class WorkspaceRuntimeWorker:
    """Background poller for operational workspace candidates and signals."""

    def __init__(self, app, *, poll_interval_seconds: float = 30.0) -> None:
        self.app = app
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> list[WorkspacePollResult]:
        return self.app.workspace_runtime.poll_once()

    def run_forever(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.poll_interval_seconds)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_background_loop, name="opencloset-workspace-runtime", daemon=True)
        self._thread.start()

    def stop_background(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run_background_loop(self) -> None:
        from api.api.app import create_app

        isolated_app = create_app(db_path=self.app.config["DB_PATH"], start_background_workers=False)
        isolated_worker = isolated_app.workspace_runtime_worker
        isolated_worker.poll_interval_seconds = self.poll_interval_seconds
        try:
            while not self._stop_event.is_set():
                isolated_worker.poll_once()
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            isolated_app.close()


def register_workspace_runtime_routes(app: Flask) -> None:
    runtime = app.workspace_runtime

    @app.route("/api/workspaces/<workspace_id>/runtime", methods=["GET"])
    def get_workspace_runtime(workspace_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        return jsonify(runtime.get_runtime_snapshot(workspace_id))

    @app.route("/api/workspaces/<workspace_id>/runtime/poll", methods=["POST"])
    def poll_workspace_runtime(workspace_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        result = runtime.poll_workspace(workspace_id)
        snapshot = runtime.get_runtime_snapshot(workspace_id)
        return jsonify({
            "workspace_id": workspace_id,
            "selected_candidate": result.selected_candidate.to_dict() if result.selected_candidate else None,
            "selected_pastime": result.selected_pastime,
            "emitted_signal": result.emitted_signal,
            "candidates": snapshot["candidates"],
            "top_candidate": snapshot["top_candidate"],
            "pastimes": snapshot["pastimes"],
            "signals": snapshot["signals"],
            "workers": snapshot["workers"],
        })

    @app.route("/api/workspaces/<workspace_id>/signals/<signal_id>/actions", methods=["POST"])
    def apply_workspace_signal_action(workspace_id: str, signal_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        data = request.get_json(silent=True) or {}
        action = str(data.get("action") or "").strip()
        if not action:
            return jsonify({"error": "action is required"}), 400
        try:
            result = runtime.apply_signal_action(workspace_id, signal_id, action)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)