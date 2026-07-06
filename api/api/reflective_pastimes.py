from __future__ import annotations

from dataclasses import replace
from typing import Any

from api.api.scheduler import IdleTier
from api.api.workspace_runtime import WorkCandidate, _now, _trim_text
from api.db.schema import new_id


class ReflectivePastimeManager:
    """Reflective pastime producer + executor for fresh-eyes thread pulling."""

    def __init__(self, db, *, workspaces, planning, event_logger=None) -> None:
        self.db = db
        self.workspaces = workspaces
        self.planning = planning
        self.event_logger = event_logger

    def collect_candidates(self, workspace_id: str) -> list[WorkCandidate]:
        workspace = self.workspaces.get_workspace(workspace_id)
        if not workspace:
            return []
        anchor = self._get_anchor_session(workspace_id)
        if not anchor:
            return []
        if self._has_open_thread_signal(workspace_id):
            return []

        state = self._build_workspace_state(workspace_id, anchor)
        if not self._should_reflect(state):
            return []

        pastime = self._get_fresh_eyes_pastime(workspace_id)
        summary = self._build_candidate_summary(state)
        priority = 34 + min(state["open_signal_count"] * 6, 18) + min(state["pending_capture_count"] * 2, 8)
        urgency = 28 + min(state["pending_capture_count"] * 4, 20) + min(state["pending_proposal_count"] * 3, 12)

        return [
            WorkCandidate(
                id=f"fresh-eyes:{workspace_id}:{anchor['id']}",
                type="fresh_eyes_thread_pull",
                source="pastime",
                workspace_id=workspace_id,
                session_id=anchor["id"],
                priority=priority,
                urgency=urgency,
                compute_cost=int((pastime or {}).get("compute_cost") or 6),
                interruptibility="medium",
                cooldown=int((pastime or {}).get("cooldown_seconds") or 3600),
                expires_at=None,
                prerequisites=["workspace_context_available", "idle_long"],
                mutability_level="read_only",
                foreground_blocking=False,
                title=f"Run fresh-eyes pass for {workspace['name']}",
                summary=summary,
                metadata={
                    "pastime_key": "fresh-eyes-thread-pulling",
                    "workspace_name": workspace["name"],
                    "workspace_kind": workspace.get("kind"),
                    "session_label": anchor["label"],
                    "target_scale": "workspace",
                    "min_idle_tier": IdleTier.LONG,
                    "project_count": state["project_count"],
                    "plan_count": state["plan_count"],
                    "open_signal_count": state["open_signal_count"],
                    "pending_capture_count": state["pending_capture_count"],
                    "pending_proposal_count": state["pending_proposal_count"],
                },
            )
        ]

    def execute_candidate(
        self,
        candidate: WorkCandidate,
        *,
        selected_pastime: dict[str, Any] | None = None,
    ) -> WorkCandidate:
        anchor = self._get_anchor_session(candidate.workspace_id, preferred_session_id=candidate.session_id)
        state = self._build_workspace_state(candidate.workspace_id, anchor)
        familiar_summary = self._build_familiar_summary(state)
        thread_candidates = self._build_thread_candidates(familiar_summary, state)

        thread_count = len(thread_candidates)
        if thread_count:
            updated_title = f"Review fresh-eyes threads for {state['workspace']['name']}"
            updated_summary = f"Fresh-eyes pass surfaced {thread_count} thread candidates for {state['workspace']['name']}."
        else:
            updated_title = f"Fresh-eyes pass complete for {state['workspace']['name']}"
            updated_summary = f"Fresh-eyes pass found no strong missingness threads for {state['workspace']['name']}."

        metadata = dict(candidate.metadata)
        metadata.update(
            {
                "familiar_summary": familiar_summary,
                "thread_candidates": thread_candidates,
                "thread_candidate_count": thread_count,
                "pastime_key": "fresh-eyes-thread-pulling",
                "pastime_id": selected_pastime["id"] if selected_pastime else None,
            }
        )
        executed = replace(candidate, title=updated_title, summary=updated_summary, metadata=metadata)

        reflection_content = familiar_summary
        if thread_candidates:
            reflection_content = "\n\n".join(
                [
                    familiar_summary,
                    "Fresh-eyes questions:\n" + "\n".join(
                        f"- {thread['title']}: {thread['question']}" for thread in thread_candidates
                    ),
                ]
            )

        self.workspaces.create_workspace_activity_capture(
            workspace_id=executed.workspace_id,
            session_id=executed.session_id,
            source="pastime_reflection",
            event_type="reflection_note",
            content=reflection_content,
            metadata={
                "pastime_key": "fresh-eyes-thread-pulling",
                "thread_candidate_count": thread_count,
                "workspace_name": state["workspace"]["name"],
            },
            status="processed",
        )
        if self.event_logger and executed.session_id:
            self.event_logger.log(
                executed.session_id,
                "reflection_note",
                {
                    "workspace_id": executed.workspace_id,
                    "pastime_key": "fresh-eyes-thread-pulling",
                    "thread_candidate_count": thread_count,
                    "summary": _trim_text(familiar_summary, limit=280),
                },
            )

        for thread in thread_candidates:
            self.workspaces.create_workspace_activity_capture(
                workspace_id=executed.workspace_id,
                session_id=executed.session_id,
                source="pastime_reflection",
                event_type="thread_candidate",
                content=f"{thread['question']}\n\n{thread['rationale']}",
                metadata={
                    "pastime_key": "fresh-eyes-thread-pulling",
                    "thread_id": thread["id"],
                    "thread_type": thread["thread_type"],
                    "title": thread["title"],
                    "target_scale": thread["target_scale"],
                },
                status="pending",
            )
            if self.event_logger and executed.session_id:
                self.event_logger.log(
                    executed.session_id,
                    "thread_candidate",
                    {
                        "workspace_id": executed.workspace_id,
                        "thread_id": thread["id"],
                        "thread_type": thread["thread_type"],
                        "title": thread["title"],
                    },
                )

        return executed

    def _get_anchor_session(self, workspace_id: str, preferred_session_id: str | None = None) -> dict[str, Any] | None:
        if preferred_session_id:
            row = self.db.execute(
                "SELECT id, label, updated_at FROM sessions WHERE workspace_id = ? AND id = ?",
                (workspace_id, preferred_session_id),
            ).fetchone()
            if row:
                return dict(row)
        row = self.db.execute(
            """SELECT id, label, updated_at FROM sessions
               WHERE workspace_id = ? AND status = 'active'
               ORDER BY updated_at DESC, created_at DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        return dict(row) if row else None

    def _has_open_thread_signal(self, workspace_id: str) -> bool:
        row = self.db.execute(
            """SELECT id FROM workspace_signals
               WHERE workspace_id = ? AND signal_type = 'thread_pulling_ready'
                 AND status IN ('open', 'escalated')
               LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        return row is not None

    def _get_fresh_eyes_pastime(self, workspace_id: str) -> dict[str, Any] | None:
        pastimes = self.workspaces.list_workspace_pastimes(workspace_id, limit=100)
        return next((item for item in pastimes if item["key"] == "fresh-eyes-thread-pulling"), None)

    def _build_workspace_state(self, workspace_id: str, anchor: dict[str, Any] | None) -> dict[str, Any]:
        workspace = self.workspaces.get_workspace(workspace_id) or {"id": workspace_id, "name": workspace_id, "description": ""}
        projects = self.workspaces.list_build_projects(workspace_id)
        plans = self.planning.list_plans_by_workspace(workspace_id)
        evidence = self.workspaces.list_workspace_evidence(workspace_id, limit=5)
        captures = self.workspaces.list_workspace_captures(workspace_id, limit=10)
        signal_row = self.db.execute(
            "SELECT COUNT(*) AS count FROM workspace_signals WHERE workspace_id = ? AND status IN ('open', 'escalated')",
            (workspace_id,),
        ).fetchone()
        pending_proposal_row = self.db.execute(
            """SELECT COUNT(*) AS count
               FROM plan_proposals pp
               JOIN sessions s ON s.id = pp.session_id
               WHERE s.workspace_id = ? AND pp.status = 'pending'""",
            (workspace_id,),
        ).fetchone()
        active_plan = self.planning.get_plan(anchor["id"]) if anchor else None
        pending_captures = [capture for capture in captures if capture.get("status") == "pending"]
        return {
            "workspace": workspace,
            "anchor": anchor,
            "projects": projects,
            "plans": plans,
            "evidence": evidence,
            "captures": captures,
            "pending_captures": pending_captures,
            "open_signal_count": int(signal_row["count"] if signal_row else 0),
            "pending_proposal_count": int(pending_proposal_row["count"] if pending_proposal_row else 0),
            "project_count": len(projects),
            "plan_count": len(plans),
            "evidence_count": len(evidence),
            "pending_capture_count": len(pending_captures),
            "active_plan": active_plan,
        }

    def _should_reflect(self, state: dict[str, Any]) -> bool:
        return any(
            (
                state["project_count"],
                state["plan_count"],
                state["evidence_count"],
                state["pending_capture_count"],
                state["open_signal_count"],
                state["pending_proposal_count"],
            )
        )

    def _build_candidate_summary(self, state: dict[str, Any]) -> str:
        workspace_name = state["workspace"]["name"]
        focus = state["active_plan"].get("active_goal") if state["active_plan"] else None
        parts = [
            f"Take a reflective fresh-eyes pass across {workspace_name}.",
            f"{state['project_count']} projects, {state['plan_count']} plans, {state['open_signal_count']} open signals, and {state['pending_capture_count']} pending captures are in play.",
        ]
        if focus:
            parts.append(f"Current center of gravity: {focus}")
        return _trim_text(" ".join(parts))

    def _build_familiar_summary(self, state: dict[str, Any]) -> str:
        workspace = state["workspace"]
        anchor = state["anchor"]
        active_plan = state["active_plan"]
        project_titles = ", ".join(project["name"] for project in state["projects"][:3]) or "no named projects yet"
        evidence_titles = ", ".join(item["title"] for item in state["evidence"][:3]) or "no durable evidence has been logged yet"
        capture_note = (
            f"There are {state['pending_capture_count']} pending captures still waiting for synthesis."
            if state["pending_capture_count"]
            else "Incoming captures are currently under control."
        )
        focus = active_plan.get("next_item", {}).get("content") if active_plan and active_plan.get("next_item") else None
        if not focus and active_plan:
            focus = active_plan.get("active_goal")

        parts = [
            f"{workspace['name']} already feels like a living workspace rather than a blank shell: it is a {workspace.get('kind', 'general')} workspace with {state['project_count']} build projects and {state['plan_count']} stored plans.",
            f"The active working anchor is {anchor['label'] if anchor else 'an unlabelled session'}, and the most visible project surface right now is {project_titles}.",
            f"The settled operational loop is carrying {state['open_signal_count']} open signals and {state['pending_proposal_count']} pending plan proposals while the durable memory side already includes {evidence_titles}.",
            capture_note,
        ]
        if focus:
            parts.append(f"The current center of gravity appears to be: {focus}.")
        if workspace.get("description"):
            parts.append(f"The workspace itself is framed as: {workspace['description']}.")
        return " ".join(parts)

    def _build_thread_candidates(self, summary: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        threads: list[dict[str, Any]] = []
        workspace_name = state["workspace"]["name"]

        if state["pending_capture_count"] > 0 and state["evidence_count"] <= state["pending_capture_count"]:
            threads.append(
                self._thread_candidate(
                    thread_type="missing primitive",
                    title="Capture distillation is outrunning durable synthesis",
                    question=f"What durable review primitive turns {state['pending_capture_count']} pending captures in {workspace_name} into reviewed queue, proposal, or evidence state without relying on manual promotion?",
                    rationale="The familiar summary makes the intake side feel more mature than the review side, which suggests a missing synthesis primitive.",
                )
            )

        if state["open_signal_count"] > 0 and state["pending_proposal_count"] == 0:
            threads.append(
                self._thread_candidate(
                    thread_type="architectural inconsistency",
                    title="Signals have visibility but not enough executive interpretation",
                    question=f"What is the smallest CLO-side review step that converts open workspace signals in {workspace_name} into reviewed proposals, queue changes, or explicit dismissal?",
                    rationale="The summary shows live signals but a thin synthesis layer after those signals appear.",
                )
            )

        if state["project_count"] > max(1, state["plan_count"]):
            threads.append(
                self._thread_candidate(
                    thread_type="underdescribed mechanism",
                    title="Project surfaces may be outpacing shared planning structure",
                    question=f"How should {workspace_name} explain the handoff between project-specific work and workspace-level plans when there are more projects than durable plan frames?",
                    rationale="The visible structure suggests the project layer may be clearer than the shared planning layer.",
                )
            )

        if not state["workspace"].get("description"):
            threads.append(
                self._thread_candidate(
                    thread_type="clarification needed",
                    title="Workspace intent is still under-described",
                    question=f"What concise operating statement would make it obvious what {workspace_name} is meant to maintain, review, and improve over time?",
                    rationale="The summary has operational facts but not a compact statement of enduring purpose.",
                )
            )

        if not threads and state["open_signal_count"] >= 3:
            threads.append(
                self._thread_candidate(
                    thread_type="scope opportunity",
                    title="The next leverage point may be cross-signal synthesis",
                    question=f"Which recurring report or briefing in {workspace_name} would collapse several open signals into one reviewed narrative for later decision-making?",
                    rationale="The summary suggests enough recurring signal volume to justify a higher-level synthesis pass.",
                )
            )

        return threads[:3]

    def _thread_candidate(
        self,
        *,
        thread_type: str,
        title: str,
        question: str,
        rationale: str,
    ) -> dict[str, Any]:
        return {
            "id": new_id(),
            "thread_type": thread_type,
            "title": title,
            "question": question,
            "rationale": rationale,
            "target_scale": "workspace",
        }