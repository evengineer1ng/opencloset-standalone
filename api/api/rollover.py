from __future__ import annotations

from dataclasses import dataclass

from api.api.maintenance import HANDOFF_CANDIDATE_ARTIFACT
from api.db.schema import new_id


@dataclass
class RolloverResult:
    id: str
    label: str
    model: str
    provider: str
    context_window: int
    task_budget_remaining: int | None
    status: str
    source_session_id: str
    active_plan: dict | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "model": self.model,
            "provider": self.provider,
            "context_window": self.context_window,
            "task_budget_remaining": self.task_budget_remaining,
            "status": self.status,
            "source_session_id": self.source_session_id,
            "active_plan": self.active_plan,
        }


class RolloverError(Exception):
    pass


class SessionNotFoundError(RolloverError):
    pass


class RolloverConflictError(RolloverError):
    pass


def _apply_preferred_handoff_artifact(app, source_session_id: str, target_session_id: str) -> dict | None:
    successor_plan = app.planning.get_plan(target_session_id)
    artifact = app.maintenance.get_latest_valid_artifact(source_session_id, HANDOFF_CANDIDATE_ARTIFACT)
    if not successor_plan or not artifact:
        return successor_plan

    handoff = dict(successor_plan.get("handoff") or {})
    handoff["handoff_artifact_id"] = artifact["id"]
    handoff["handoff_artifact_type"] = artifact["artifact_type"]
    handoff["handoff_artifact_summary"] = artifact["content"]
    if artifact.get("start_position") is not None and artifact.get("end_position") is not None:
        handoff["handoff_artifact_message_range"] = f"{artifact['start_position']}-{artifact['end_position']}"

    app.planning.set_handoff(target_session_id, handoff)
    return app.planning.get_plan(target_session_id)


def create_rollover_successor(
    app,
    session_id: str,
    *,
    label: str | None = None,
    task_budget_remaining: int | None = None,
) -> RolloverResult:
    db = app.db
    session = db.execute(
        "SELECT id, label, model, provider, status, context_window, task_budget_remaining, workspace_id, build_project_id FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not session:
        raise SessionNotFoundError(f"Session not found: {session_id}")
    if session["status"] != "active":
        raise RolloverConflictError(f"Session is {session['status']}, not active")

    active_run = db.execute(
        "SELECT id FROM runs WHERE session_id = ? AND status IN ('queued', 'running') LIMIT 1",
        (session_id,),
    ).fetchone()
    if active_run:
        raise RolloverConflictError("Cannot roll over session with active run")

    new_session_id = new_id()
    new_label = label or ((session["label"] or "session") + " rollover")
    next_task_budget = session["task_budget_remaining"] if task_budget_remaining is None else task_budget_remaining

    db.execute(
        """INSERT INTO sessions (id, label, model, provider, context_window, task_budget_remaining, workspace_id, build_project_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_session_id,
            new_label,
            session["model"],
            session["provider"],
            session["context_window"],
            next_task_budget,
            session["workspace_id"],
            session["build_project_id"],
        ),
    )
    db.execute(
        """UPDATE sessions
           SET status = 'rolled-over', rolled_over_to = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE id = ?""",
        (new_session_id, session_id),
    )
    db.commit()

    app.event_logger.log_session_created(new_session_id, label=new_label, model=session["model"])
    app.event_logger.log_rollover_triggered(session_id, reason=f"successor:{new_session_id}")
    successor_plan = app.planning.rollover_plan(session_id, new_session_id)
    successor_plan = _apply_preferred_handoff_artifact(app, session_id, new_session_id)

    return RolloverResult(
        id=new_session_id,
        label=new_label,
        model=session["model"],
        provider=session["provider"],
        context_window=session["context_window"],
        task_budget_remaining=next_task_budget,
        status="active",
        source_session_id=session_id,
        active_plan=successor_plan,
    )
