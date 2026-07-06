from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request

from api.api.events import EVENT_WORKER_REPORT
from api.db.schema import new_id


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class WorkspaceBriefingManager:
    """
    Generates and stores "when user returns" briefings for workspaces.

    A briefing is a programmatic digest synthesized from:
      - open / escalated workspace signals
      - top scheduler candidates
      - active plan state across sessions
      - recent workspace captures
      - maintenance artifact state

    Stored as workspace_evidence with evidence_type='briefing' so they
    accumulate over time and can be reviewed via the evidence registry.

    The summary text is deterministic today; a model-generated narrative
    sits above this layer and arrives with the idle CLO executive loop.
    """

    BRIEFING_EVIDENCE_TYPE = "briefing"

    def __init__(self, db, *, workspace_runtime, planning, workspaces, event_logger=None) -> None:
        self.db = db
        self.workspace_runtime = workspace_runtime
        self.planning = planning
        self.workspaces = workspaces
        self.event_logger = event_logger

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_briefing(self, workspace_id: str) -> dict[str, Any]:
        """Build and persist a briefing for the workspace. Returns the evidence row."""
        sections = self._collect_sections(workspace_id)
        summary = self._build_summary(sections)
        content = json.dumps(sections, ensure_ascii=False)

        evidence_id = self.workspaces.create_workspace_evidence(
            workspace_id=workspace_id,
            evidence_type=self.BRIEFING_EVIDENCE_TYPE,
            title=f"Workspace briefing – {_now()[:10]}",
            summary=summary,
            content=content,
            source_kind="system",
            tags=["briefing", "auto-generated"],
            metadata={"generated_at": _now(), "section_keys": list(sections.keys())},
        )
        evidence = self.workspaces.get_workspace_evidence(workspace_id, evidence_id) or {"id": evidence_id}

        if self.event_logger:
            sessions = self.db.execute(
                "SELECT id FROM sessions WHERE workspace_id = ? AND status = 'active' LIMIT 1",
                (workspace_id,),
            ).fetchone()
            if sessions:
                self.event_logger.log(
                    sessions["id"],
                    EVENT_WORKER_REPORT,
                    {
                        "worker_name": "briefing-generator",
                        "workspace_id": workspace_id,
                        "summary": summary,
                        "evidence_id": evidence["id"],
                    },
                )

        return evidence

    def get_latest_briefing(self, workspace_id: str) -> dict[str, Any] | None:
        """Return the most recent briefing evidence item, or None."""
        results = self.workspaces.list_workspace_evidence(
            workspace_id,
            evidence_type=self.BRIEFING_EVIDENCE_TYPE,
            status="active",
            limit=1,
        )
        return results[0] if results else None

    def list_briefings(self, workspace_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent briefings for a workspace, newest first."""
        return self.workspaces.list_workspace_evidence(
            workspace_id,
            evidence_type=self.BRIEFING_EVIDENCE_TYPE,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Section collectors
    # ------------------------------------------------------------------

    def _collect_sections(self, workspace_id: str) -> dict[str, Any]:
        return {
            "open_signals": self._collect_signals(workspace_id),
            "top_candidates": self._collect_candidates(workspace_id),
            "active_plans": self._collect_plans(workspace_id),
            "recent_captures": self._collect_captures(workspace_id),
            "session_summary": self._collect_session_summary(workspace_id),
        }

    def _collect_signals(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT id, worker_name, signal_type, title, summary, status, priority, created_at
               FROM workspace_signals
               WHERE workspace_id = ? AND status IN ('open', 'escalated')
               ORDER BY CASE status WHEN 'escalated' THEN 0 ELSE 1 END, priority DESC
               LIMIT 20""",
            (workspace_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "worker_name": row["worker_name"],
                "signal_type": row["signal_type"],
                "title": row["title"],
                "summary": row["summary"],
                "status": row["status"],
                "priority": row["priority"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _collect_candidates(self, workspace_id: str) -> list[dict[str, Any]]:
        try:
            candidates = self.workspace_runtime.collect_candidates(workspace_id)
            return [c.to_dict() for c in candidates[:5]]
        except Exception:
            return []

    def _collect_plans(self, workspace_id: str) -> list[dict[str, Any]]:
        sessions = self.db.execute(
            """SELECT id, label, status FROM sessions
               WHERE workspace_id = ? AND status IN ('active', 'paused')
               ORDER BY updated_at DESC LIMIT 10""",
            (workspace_id,),
        ).fetchall()
        plans = []
        for session in sessions:
            try:
                plan = self.planning.get_plan(session["id"])
                if not plan:
                    continue
                plans.append({
                    "session_id": session["id"],
                    "session_label": session["label"] or "Untitled",
                    "session_status": session["status"],
                    "plan_id": plan.get("id"),
                    "plan_title": plan.get("title"),
                    "plan_status": plan.get("status"),
                    "next_item": (plan.get("next_item") or {}).get("content"),
                    "open_item_count": len([
                        i for i in (plan.get("items") or [])
                        if not i.get("archived") and i.get("status") in {"todo", "doing", "blocked"}
                    ]),
                })
            except Exception:
                pass
        return plans

    def _collect_captures(self, workspace_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT id, source, event_type, content, status, received_at
               FROM captures
               WHERE workspace_id = ? AND status = 'pending'
               ORDER BY received_at DESC LIMIT 10""",
            (workspace_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "source": row["source"],
                "event_type": row["event_type"],
                "content_preview": str(row["content"] or "")[:120],
                "status": row["status"],
                "received_at": row["received_at"],
            }
            for row in rows
        ]

    def _collect_session_summary(self, workspace_id: str) -> dict[str, Any]:
        counts = self.db.execute(
            """SELECT status, COUNT(*) AS n FROM sessions
               WHERE workspace_id = ?
               GROUP BY status""",
            (workspace_id,),
        ).fetchall()
        return {row["status"]: row["n"] for row in counts}

    # ------------------------------------------------------------------
    # Summary text builder
    # ------------------------------------------------------------------

    def _build_summary(self, sections: dict[str, Any]) -> str:
        parts: list[str] = []

        signals = sections.get("open_signals", [])
        escalated = [s for s in signals if s.get("status") == "escalated"]
        if escalated:
            parts.append(f"{len(escalated)} signal(s) escalated to CLO.")
        elif signals:
            parts.append(f"{len(signals)} open signal(s) awaiting review.")

        candidates = sections.get("top_candidates", [])
        if candidates:
            top = candidates[0]
            parts.append(f"Top candidate: {top.get('title', 'unknown')} (priority {top.get('priority', 0)}).")

        plans = sections.get("active_plans", [])
        active_plans = [p for p in plans if p.get("session_status") == "active"]
        paused_plans = [p for p in plans if p.get("session_status") == "paused"]
        if active_plans:
            parts.append(f"{len(active_plans)} active plan(s).")
        if paused_plans:
            parts.append(f"{len(paused_plans)} paused session(s) awaiting handoff.")

        captures = sections.get("recent_captures", [])
        if captures:
            parts.append(f"{len(captures)} pending capture(s).")

        session_counts = sections.get("session_summary", {})
        total_sessions = sum(session_counts.values())
        if total_sessions:
            parts.append(f"{total_sessions} total session(s).")

        return " ".join(parts) if parts else "No notable activity to report."



# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_briefing_routes(app: Flask) -> None:
    briefing: WorkspaceBriefingManager = app.briefing

    @app.route("/api/workspaces/<workspace_id>/briefing/latest", methods=["GET"])
    def get_latest_briefing(workspace_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        result = briefing.get_latest_briefing(workspace_id)
        if result is None:
            return jsonify({"briefing": None}), 200
        return jsonify({"briefing": result})

    @app.route("/api/workspaces/<workspace_id>/briefing", methods=["GET"])
    def list_briefings(workspace_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        limit = int(request.args.get("limit", 10))
        results = briefing.list_briefings(workspace_id, limit=limit)
        return jsonify({"briefings": results})

    @app.route("/api/workspaces/<workspace_id>/briefing/generate", methods=["POST"])
    def generate_briefing(workspace_id: str):
        if not app.workspaces.verify_workspace(workspace_id):
            return jsonify({"error": "workspace not found"}), 404
        result = briefing.generate_briefing(workspace_id)
        return jsonify({"briefing": result}), 201
