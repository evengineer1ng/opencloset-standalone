from __future__ import annotations

import os
import tempfile
import time

from api.api.app import create_app
from api.api.maintenance import HANDOFF_CANDIDATE_ARTIFACT


class TestWorkspaceRoutes:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.workspace_runtime_worker.stop_background(timeout=0.1)
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_workspace_crud_and_project_listing(self):
        create_resp = self.client.post(
            "/api/workspaces",
            json={"name": "OpenCloset", "description": "Main workspace", "kind": "software"},
        )
        assert create_resp.status_code == 201
        workspace = create_resp.get_json()
        workspace_id = workspace["id"]
        assert workspace["name"] == "OpenCloset"
        assert workspace["kind"] == "software"
        assert workspace["attention_profile"]["workspace_id"] == workspace_id
        assert workspace["attention_profile"]["mode"] == "warm"

        list_resp = self.client.get("/api/workspaces")
        assert list_resp.status_code == 200
        listed_workspace = next(item for item in list_resp.get_json()["workspaces"] if item["id"] == workspace_id)
        assert listed_workspace["attention_profile"]["mode"] == "warm"

        patch_resp = self.client.patch(
            f"/api/workspaces/{workspace_id}",
            json={"status": "maintenance", "description": "Refining workspace"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.get_json()["status"] == "maintenance"

        project_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "UI Wiring", "description": "Wire the real browser flow"},
        )
        assert project_resp.status_code == 201
        project = project_resp.get_json()
        project_id = project["id"]
        assert project["workspace_id"] == workspace_id

        projects_resp = self.client.get(f"/api/workspaces/{workspace_id}/projects")
        assert projects_resp.status_code == 200
        assert any(item["id"] == project_id for item in projects_resp.get_json()["build_projects"])

        get_project_resp = self.client.get(f"/api/workspaces/{workspace_id}/projects/{project_id}")
        assert get_project_resp.status_code == 200
        assert get_project_resp.get_json()["name"] == "UI Wiring"

    def test_workspace_attention_profile_defaults_patch_and_runtime_snapshot(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Hockey Lab", "description": "Signal workspace"},
        ).get_json()["id"]

        attention_resp = self.client.get(f"/api/workspaces/{workspace_id}/attention")

        assert attention_resp.status_code == 200
        attention = attention_resp.get_json()
        assert attention["workspace_id"] == workspace_id
        assert attention["mode"] == "warm"
        assert set(attention["allowed_pastime_types"]) >= {
            "maintenance",
            "operational",
            "reflective",
        }

        patch_resp = self.client.patch(
            f"/api/workspaces/{workspace_id}/attention",
            json={
                "mode": "active",
                "baseline_priority": 70,
                "current_attention_level": 85,
                "max_idle_budget": 15,
                "allowed_pastime_types": ["operational", "reflective"],
                "notification_threshold": "immediate",
                "freshness_target": "daily",
                "review_at": "2026-05-16T00:00:00Z",
                "user_rationale": "Prioritize buy-low signal finding for two weeks.",
            },
        )

        assert patch_resp.status_code == 200
        updated = patch_resp.get_json()
        assert updated["mode"] == "active"
        assert updated["baseline_priority"] == 70
        assert updated["current_attention_level"] == 85
        assert updated["max_idle_budget"] == 15
        assert updated["allowed_pastime_types"] == ["operational", "reflective"]
        assert updated["notification_threshold"] == "immediate"
        assert updated["freshness_target"] == "daily"
        assert updated["review_at"] == "2026-05-16T00:00:00Z"
        assert updated["user_rationale"] == "Prioritize buy-low signal finding for two weeks."

        runtime_resp = self.client.get(f"/api/workspaces/{workspace_id}/runtime")

        assert runtime_resp.status_code == 200
        runtime_snapshot = runtime_resp.get_json()
        assert runtime_snapshot["attention_profile"]["workspace_id"] == workspace_id
        assert runtime_snapshot["attention_profile"]["mode"] == "active"
        assert runtime_snapshot["attention_profile"]["notification_threshold"] == "immediate"

    def test_workspace_attention_profile_rejects_invalid_mode(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Basketball Lab"},
        ).get_json()["id"]

        patch_resp = self.client.patch(
            f"/api/workspaces/{workspace_id}/attention",
            json={"mode": "loud"},
        )

        assert patch_resp.status_code == 400
        assert "invalid workspace attention mode" in patch_resp.get_json()["error"]

    def test_workspace_attention_compile_updates_profile_and_returns_diff(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "OpenCloset"},
        ).get_json()["id"]

        compile_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/attention/compile",
            json={
                "instruction": "For the next two weeks, prioritize OpenCloset 70% and notify me immediately if something important changes.",
            },
        )

        assert compile_resp.status_code == 200
        payload = compile_resp.get_json()
        assert payload["applied"] is True
        assert payload["profile"]["mode"] == "active"
        assert payload["profile"]["baseline_priority"] == 70
        assert payload["profile"]["current_attention_level"] == 70
        assert payload["profile"]["notification_threshold"] == "immediate"
        assert payload["profile"]["review_at"] is not None
        assert payload["profile"]["expires_at"] is not None
        assert payload["profile"]["user_rationale"].startswith("For the next two weeks")
        assert any(change["field"] == "mode" for change in payload["diff"])
        assert payload["scheduler_effects"]["max_idle_budget"] == payload["profile"]["max_idle_budget"]

        get_resp = self.client.get(f"/api/workspaces/{workspace_id}/attention")
        assert get_resp.status_code == 200
        assert get_resp.get_json()["mode"] == "active"

    def test_delete_build_project_route_removes_project_and_scoped_sessions(self):
        workspace_id = self.client.post("/api/workspaces", json={"name": "Workspace A"}).get_json()["id"]
        project_id = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Project A"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "project session",
                "workspace_id": workspace_id,
                "build_project_id": project_id,
            },
        ).get_json()["id"]

        delete_resp = self.client.delete(f"/api/workspaces/{workspace_id}/projects/{project_id}")

        assert delete_resp.status_code == 200
        assert delete_resp.get_json()["deleted"] == project_id
        assert self.client.get(f"/api/workspaces/{workspace_id}/projects/{project_id}").status_code == 404
        assert self.client.get(f"/api/sessions/{session_id}").status_code == 404

    def test_delete_workspace_route_removes_workspace_projects_and_sessions(self):
        workspace_id = self.client.post("/api/workspaces", json={"name": "Workspace A"}).get_json()["id"]
        project_id = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Project A"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "workspace session",
                "workspace_id": workspace_id,
                "build_project_id": project_id,
            },
        ).get_json()["id"]

        delete_resp = self.client.delete(f"/api/workspaces/{workspace_id}")

        assert delete_resp.status_code == 200
        assert delete_resp.get_json()["deleted"] == workspace_id
        listed_workspaces = self.client.get("/api/workspaces").get_json()["workspaces"]
        assert all(workspace["id"] != workspace_id for workspace in listed_workspaces)
        assert self.client.get(f"/api/sessions/{session_id}").status_code == 404
        assert self.client.get(f"/api/workspaces/{workspace_id}/projects").status_code == 404

    def test_workspace_plan_and_session_integration(self):
        workspace_resp = self.client.post("/api/workspaces", json={"name": "Workspace A"})
        workspace_id = workspace_resp.get_json()["id"]

        project_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Project A"},
        )
        project_id = project_resp.get_json()["id"]

        session_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "scoped session",
                "workspace_id": workspace_id,
                "build_project_id": project_id,
            },
        )
        assert session_resp.status_code == 201
        session = session_resp.get_json()
        session_id = session["id"]
        assert session["workspace_id"] == workspace_id
        assert session["build_project_id"] == project_id

        get_session_resp = self.client.get(f"/api/sessions/{session_id}")
        assert get_session_resp.status_code == 200
        session_detail = get_session_resp.get_json()
        assert session_detail["workspace_id"] == workspace_id
        assert session_detail["build_project_id"] == project_id

        plan_resp = self.client.get(f"/api/sessions/{session_id}/plan")
        assert plan_resp.status_code == 200
        plan = plan_resp.get_json()
        assert plan["workspace_id"] == workspace_id
        assert plan["build_project_id"] == project_id

        workspace_plans_resp = self.client.get(f"/api/workspaces/{workspace_id}/plans")
        assert workspace_plans_resp.status_code == 200
        workspace_plans = workspace_plans_resp.get_json()["plans"]
        assert any(item["id"] == plan["id"] for item in workspace_plans)

    def test_workspace_pastime_registry_defaults_and_patch(self):
        workspace_id = self.client.post("/api/workspaces", json={"name": "Workspace Ops"}).get_json()["id"]

        pastimes_resp = self.client.get(f"/api/workspaces/{workspace_id}/pastimes")

        assert pastimes_resp.status_code == 200
        pastimes = pastimes_resp.get_json()["pastimes"]
        assert {item["key"] for item in pastimes} >= {"handoff-review", "backlog-review", "context-review", "fresh-eyes-thread-pulling"}

        backlog_pastime = next(item for item in pastimes if item["key"] == "backlog-review")
        patch_resp = self.client.patch(
            f"/api/workspaces/{workspace_id}/pastimes/{backlog_pastime['id']}",
            json={"status": "paused", "cooldown_seconds": 42},
        )

        assert patch_resp.status_code == 200
        updated = patch_resp.get_json()
        assert updated["status"] == "paused"
        assert updated["cooldown_seconds"] == 42

    def test_create_session_rejects_cross_workspace_project(self):
        workspace_a = self.client.post("/api/workspaces", json={"name": "Workspace A"}).get_json()["id"]
        workspace_b = self.client.post("/api/workspaces", json={"name": "Workspace B"}).get_json()["id"]
        project_b = self.client.post(
            f"/api/workspaces/{workspace_b}/projects",
            json={"name": "Project B"},
        ).get_json()["id"]

        resp = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "bad scope",
                "workspace_id": workspace_a,
                "build_project_id": project_b,
            },
        )
        assert resp.status_code == 400
        assert "project does not belong" in resp.get_json()["error"]

    def test_rollover_preserves_workspace_and_project_context(self):
        workspace_id = self.client.post("/api/workspaces", json={"name": "Workspace A"}).get_json()["id"]
        project_id = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Project A"},
        ).get_json()["id"]

        session_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "root session",
                "workspace_id": workspace_id,
                "build_project_id": project_id,
            },
        )
        session_id = session_resp.get_json()["id"]

        rollover_resp = self.client.post(f"/api/sessions/{session_id}/rollover")
        assert rollover_resp.status_code == 201
        successor = rollover_resp.get_json()
        successor_id = successor["id"]

        successor_session = self.client.get(f"/api/sessions/{successor_id}").get_json()
        assert successor_session["workspace_id"] == workspace_id
        assert successor_session["build_project_id"] == project_id

        successor_plan = self.client.get(f"/api/sessions/{successor_id}/plan").get_json()
        assert successor_plan["workspace_id"] == workspace_id
        assert successor_plan["build_project_id"] == project_id

    def test_workspace_runtime_surfaces_handoff_review_candidates_and_signals(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Ops", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_resp = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Paused review session",
                "workspace_id": workspace_id,
            },
        )
        session_id = session_resp.get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Review the pending handoff", status="doing")
        self.app.planning.set_status(session_id, "paused")
        self.app.maintenance.create_artifact(
            session_id,
            HANDOFF_CANDIDATE_ARTIFACT,
            "Prepared handoff candidate: Resume the pending handoff review.",
            metadata={"reason": "idle_review"},
        )

        runtime_resp = self.client.get(f"/api/workspaces/{workspace_id}/runtime")

        assert runtime_resp.status_code == 200
        runtime_payload = runtime_resp.get_json()
        assert runtime_payload["top_candidate"] is not None
        assert runtime_payload["top_candidate"]["type"] == "handoff_review"
        assert runtime_payload["top_candidate"]["session_id"] == session_id
        assert runtime_payload["signals"] == []

        poll_resp = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll")

        assert poll_resp.status_code == 200
        poll_payload = poll_resp.get_json()
        assert poll_payload["selected_candidate"] is not None
        assert poll_payload["selected_candidate"]["session_id"] == session_id
        assert poll_payload["emitted_signal"] is not None
        assert poll_payload["emitted_signal"]["signal_type"] == "handoff_ready"
        assert len(poll_payload["signals"]) == 1

        second_poll = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll")
        assert second_poll.status_code == 200
        assert len(second_poll.get_json()["signals"]) == 1

    def test_workspace_runtime_worker_emits_signal_in_background(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Background", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Background review session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Review in background", status="doing")
        self.app.planning.set_status(session_id, "paused")
        self.app.maintenance.create_artifact(
            session_id,
            HANDOFF_CANDIDATE_ARTIFACT,
            "Prepared handoff candidate: Resume background review.",
            metadata={"reason": "background_runtime"},
        )

        self.app.workspace_runtime_worker.poll_interval_seconds = 0.01
        self.app.workspace_runtime_worker.start_background()

        deadline = time.time() + 1.0
        signals = []
        while time.time() < deadline:
            signals = self.app.workspace_runtime.list_open_signals(workspace_id)
            if signals:
                break
            time.sleep(0.02)

        assert len(signals) == 1
        assert signals[0]["signal_type"] == "handoff_ready"
        assert signals[0]["session_id"] == session_id

    def test_workspace_runtime_surfaces_backlog_review_candidates_and_signals(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Queue", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Backlog session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Resolve blocked dependency", status="blocked")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Triage follow-up", status="todo")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Queue next review", status="todo")

        runtime_resp = self.client.get(f"/api/workspaces/{workspace_id}/runtime")

        assert runtime_resp.status_code == 200
        runtime_payload = runtime_resp.get_json()
        assert runtime_payload["top_candidate"] is not None
        assert runtime_payload["top_candidate"]["type"] == "backlog_review"
        assert runtime_payload["top_candidate"]["session_id"] == session_id
        assert runtime_payload["top_candidate"]["metadata"]["blocked_count"] == 1
        assert runtime_payload["signals"] == []

        poll_resp = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll")

        assert poll_resp.status_code == 200
        poll_payload = poll_resp.get_json()
        assert poll_payload["selected_candidate"] is not None
        assert poll_payload["selected_candidate"]["type"] == "backlog_review"
        assert poll_payload["selected_pastime"] is not None
        assert poll_payload["selected_pastime"]["key"] == "backlog-review"
        assert poll_payload["emitted_signal"] is not None
        assert poll_payload["emitted_signal"]["signal_type"] == "backlog_review_needed"
        assert poll_payload["emitted_signal"]["worker_name"] == "backlog-review-clerk"

        captures = self.client.get(f"/api/workspaces/{workspace_id}/captures").get_json()["captures"]
        signal_capture = next(item for item in captures if item["event_type"] == "workspace_signal")
        assert signal_capture["metadata"]["signal_type"] == "backlog_review_needed"
        assert signal_capture["metadata"]["pastime_key"] == "backlog-review"

    def test_workspace_runtime_pastime_selector_respects_disabled_registry_entry(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Queue", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        pastimes = self.client.get(f"/api/workspaces/{workspace_id}/pastimes").get_json()["pastimes"]
        backlog_pastime = next(item for item in pastimes if item["key"] == "backlog-review")
        self.client.patch(
            f"/api/workspaces/{workspace_id}/pastimes/{backlog_pastime['id']}",
            json={"status": "paused"},
        )
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Backlog session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Resolve blocked dependency", status="blocked")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Queue follow-up", status="todo")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Queue next review", status="todo")

        poll_resp = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll")

        assert poll_resp.status_code == 200
        poll_payload = poll_resp.get_json()
        assert poll_payload["top_candidate"]["type"] == "backlog_review"
        assert poll_payload["selected_candidate"] is None
        assert poll_payload["selected_pastime"] is None
        assert poll_payload["emitted_signal"] is None

    def test_workspace_runtime_fresh_eyes_thread_pulling_emits_reflective_outputs(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Reflective Workspace", "description": "Reflective test bed", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "Reflective session", "workspace_id": workspace_id},
        ).get_json()["id"]

        self.app.planning.update_active_goal(session_id, "Deepen workspace synthesis")
        self.client.post(
            f"/api/workspaces/{workspace_id}/captures",
            json={"source": "manual", "event_type": "text", "content": "Raw field note waiting for synthesis.", "session_id": session_id},
        )
        self.app.db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 minutes') WHERE id = ?",
            (session_id,),
        )
        self.app.db.commit()

        poll_resp = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll")

        assert poll_resp.status_code == 200
        poll_payload = poll_resp.get_json()
        assert poll_payload["selected_candidate"] is not None
        assert poll_payload["selected_candidate"]["type"] == "fresh_eyes_thread_pull"
        assert poll_payload["selected_pastime"] is not None
        assert poll_payload["selected_pastime"]["key"] == "fresh-eyes-thread-pulling"
        assert poll_payload["emitted_signal"] is not None
        assert poll_payload["emitted_signal"]["signal_type"] == "thread_pulling_ready"
        assert poll_payload["emitted_signal"]["metadata"]["thread_candidate_count"] >= 1

        captures = self.client.get(f"/api/workspaces/{workspace_id}/captures").get_json()["captures"]
        assert any(item["event_type"] == "reflection_note" for item in captures)
        assert any(item["event_type"] == "thread_candidate" for item in captures)

        events = self.client.get(f"/api/sessions/{session_id}/events?limit=50").get_json()["events"]
        event_types = {event["type"] for event in events}
        assert "pastime_started" in event_types
        assert "pastime_completed" in event_types
        assert "reflection_note" in event_types
        assert "thread_candidate" in event_types

    def test_workspace_runtime_clo_review_loop_creates_proposal_from_signal_without_duplication(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "CLO Signal Workspace", "description": "Signal review", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "Signal session", "workspace_id": workspace_id},
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        blocked_item = self.app.planning.add_plan_item(session_id, active_plan["id"], "Resolve blocked dependency", status="blocked")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Follow-up review", status="todo")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Queue next step", status="todo")

        poll_payload = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll").get_json()
        signal_id = poll_payload["emitted_signal"]["id"]

        proposals = self.app.planning.list_plan_proposals(session_id, status="pending")
        assert len(proposals) == 1
        assert proposals[0]["proposed_by"] == "clo"
        assert proposals[0]["proposal_type"] == "add_item"
        assert "Review backlog signal" in proposals[0]["payload"]["content"]

        signal = self.app.workspace_runtime.get_signal(workspace_id, signal_id)
        assert signal is not None
        assert signal["metadata"]["clo_proposal_id"] == proposals[0]["id"]
        assert signal["metadata"]["lead_item_id"] == blocked_item["id"]

        clo_review_captures = [
            item
            for item in self.client.get(f"/api/workspaces/{workspace_id}/captures").get_json()["captures"]
            if item["event_type"] == "clo_review"
        ]
        assert len(clo_review_captures) == 1
        assert clo_review_captures[0]["metadata"]["signal_id"] == signal_id

        second_poll = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll").get_json()
        assert second_poll["signals"][0]["metadata"]["clo_proposal_id"] == proposals[0]["id"]
        assert len(self.app.planning.list_plan_proposals(session_id, status="pending")) == 1

    def test_workspace_runtime_clo_review_loop_creates_proposal_from_thread_candidate_capture(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "CLO Thread Workspace", "description": "Thread review", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "Thread session", "workspace_id": workspace_id},
        ).get_json()["id"]

        self.app.planning.update_active_goal(session_id, "Deepen workspace synthesis")
        self.client.post(
            f"/api/workspaces/{workspace_id}/captures",
            json={"source": "manual", "event_type": "text", "content": "Raw field note waiting for synthesis.", "session_id": session_id},
        )
        self.app.db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 minutes') WHERE id = ?",
            (session_id,),
        )
        self.app.db.commit()

        self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll")

        proposals = self.app.planning.list_plan_proposals(session_id, status="pending")
        assert any(proposal["proposed_by"] == "clo" for proposal in proposals)
        thread_proposal = next(proposal for proposal in proposals if proposal["summary"].startswith("CLO: thread pull ->"))
        assert thread_proposal["proposal_type"] == "add_item"
        assert thread_proposal["payload"]["content"].startswith("Investigate thread:")

        captures = self.client.get(f"/api/workspaces/{workspace_id}/captures").get_json()["captures"]
        reviewed_thread_capture = next(item for item in captures if item["event_type"] == "thread_candidate")
        assert reviewed_thread_capture["status"] == "processed"
        assert reviewed_thread_capture["metadata"]["clo_proposal_id"] == thread_proposal["id"]

        clo_review_capture = next(item for item in captures if item["event_type"] == "clo_review")
        assert clo_review_capture["metadata"]["capture_id"] == reviewed_thread_capture["id"]

    def test_workspace_runtime_arbiter_prefers_higher_priority_operational_candidate_over_fresh_eyes(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Competition Workspace", "description": "Competition test", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "Competition session", "workspace_id": workspace_id},
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Blocked dependency", status="blocked")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Follow-up", status="todo")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Queue next step", status="todo")
        self.client.post(
            f"/api/workspaces/{workspace_id}/captures",
            json={"source": "manual", "event_type": "text", "content": "Reflection material is available too.", "session_id": session_id},
        )
        self.app.db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 minutes') WHERE id = ?",
            (session_id,),
        )
        self.app.db.commit()

        runtime_resp = self.client.get(f"/api/workspaces/{workspace_id}/runtime")
        runtime_payload = runtime_resp.get_json()
        assert any(candidate["type"] == "fresh_eyes_thread_pull" for candidate in runtime_payload["candidates"])
        assert any(candidate["type"] == "backlog_review" for candidate in runtime_payload["candidates"])

        poll_payload = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll").get_json()
        assert poll_payload["selected_candidate"]["type"] == "backlog_review"
        assert poll_payload["selected_pastime"]["key"] == "backlog-review"
        assert poll_payload["emitted_signal"]["signal_type"] == "backlog_review_needed"

    def test_workspace_runtime_surfaces_context_review_candidates_and_workers(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Pressure", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Pressure session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Prepare rollover review", status="doing")
        self.app.planning.update_context_guard(session_id, 45000, rollover_threshold=50000)

        runtime_resp = self.client.get(f"/api/workspaces/{workspace_id}/runtime")

        assert runtime_resp.status_code == 200
        runtime_payload = runtime_resp.get_json()
        assert runtime_payload["top_candidate"] is not None
        assert runtime_payload["top_candidate"]["type"] == "context_review"
        assert runtime_payload["top_candidate"]["metadata"]["pressure_pct"] == 90
        worker = next(item for item in runtime_payload["workers"] if item["name"] == "context-guard-clerk")
        assert worker["queue_count"] == 1
        assert worker["open_signal_count"] == 0
        assert worker["status"] == "queued"

        self.app.db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-3 minutes') WHERE id = ?",
            (session_id,),
        )
        self.app.db.commit()

        poll_resp = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll")

        assert poll_resp.status_code == 200
        poll_payload = poll_resp.get_json()
        assert poll_payload["selected_candidate"] is not None
        assert poll_payload["selected_candidate"]["type"] == "context_review"
        assert poll_payload["emitted_signal"] is not None
        assert poll_payload["emitted_signal"]["signal_type"] == "context_review_needed"
        assert poll_payload["emitted_signal"]["worker_name"] == "context-guard-clerk"
        worker_after_poll = next(item for item in poll_payload["workers"] if item["name"] == "context-guard-clerk")
        assert worker_after_poll["open_signal_count"] == 1
        assert worker_after_poll["status"] == "attention"

    def test_workspace_signal_action_starts_backlog_lead_item(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Grooming", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Grooming session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        blocked_item = self.app.planning.add_plan_item(session_id, active_plan["id"], "Resolve blocked dependency", status="blocked")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Follow-up review", status="todo")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Queue next step", status="todo")

        poll_payload = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll").get_json()
        signal_id = poll_payload["emitted_signal"]["id"]

        action_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/signals/{signal_id}/actions",
            json={"action": "start_lead_item"},
        )

        assert action_resp.status_code == 200
        action_payload = action_resp.get_json()
        assert action_payload["signal"]["status"] == "resolved"
        assert action_payload["updated_item"]["id"] == blocked_item["id"]
        assert action_payload["updated_item"]["status"] == "doing"
        assert action_payload["snapshot"]["signals"] == []

        refreshed_plan = self.app.planning.get_plan(session_id)
        refreshed_item = next(item for item in refreshed_plan["items"] if item["id"] == blocked_item["id"])
        assert refreshed_item["status"] == "doing"

        captures = self.client.get(f"/api/workspaces/{workspace_id}/captures").get_json()["captures"]
        review_capture = next(item for item in captures if item["event_type"] == "signal_review")
        assert review_capture["metadata"]["action"] == "start_lead_item"
        assert review_capture["status"] == "processed"

    def test_workspace_signal_action_defers_backlog_lead_item(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Defer", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Defer session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        blocked_item = self.app.planning.add_plan_item(session_id, active_plan["id"], "Blocked item", status="blocked")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Follow-up review", status="todo")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Queue next step", status="todo")

        poll_payload = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll").get_json()
        signal_id = poll_payload["emitted_signal"]["id"]

        action_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/signals/{signal_id}/actions",
            json={"action": "defer_lead_item"},
        )

        assert action_resp.status_code == 200
        action_payload = action_resp.get_json()
        assert action_payload["signal"]["status"] == "resolved"
        assert action_payload["updated_item"]["id"] == blocked_item["id"]
        assert action_payload["updated_item"]["status"] == "deferred"

        refreshed_plan = self.app.planning.get_plan(session_id)
        refreshed_item = next(item for item in refreshed_plan["items"] if item["id"] == blocked_item["id"])
        assert refreshed_item["status"] == "deferred"

    def test_workspace_signal_action_pauses_context_review_session(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Pause", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Pause session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Prepare pause", status="doing")
        self.app.planning.update_context_guard(session_id, 45000, rollover_threshold=50000)

        poll_payload = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll").get_json()
        signal_id = poll_payload["emitted_signal"]["id"]

        action_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/signals/{signal_id}/actions",
            json={"action": "pause_plan"},
        )

        assert action_resp.status_code == 200
        action_payload = action_resp.get_json()
        assert action_payload["signal"]["status"] == "resolved"
        assert action_payload["updated_item"] is None
        assert action_payload["snapshot"]["signals"] == []

        refreshed_plan = self.app.planning.get_plan(session_id)
        assert refreshed_plan["status"] == "paused"

    def test_workspace_signal_action_escalates_and_preserves_status_across_poll(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Escalation", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Escalation session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Blocked item", status="blocked")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Follow-up review", status="todo")
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Queue next step", status="todo")

        poll_payload = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll").get_json()
        signal_id = poll_payload["emitted_signal"]["id"]

        action_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/signals/{signal_id}/actions",
            json={"action": "escalate_to_buddy"},
        )

        assert action_resp.status_code == 200
        action_payload = action_resp.get_json()
        assert action_payload["signal"]["status"] == "escalated"
        assert action_payload["signal"]["metadata"]["escalation_target"] == "buddy"
        assert action_payload["snapshot"]["signals"][0]["status"] == "escalated"

        second_poll = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll")
        assert second_poll.status_code == 200
        second_payload = second_poll.get_json()
        assert second_payload["signals"][0]["status"] == "escalated"
        assert second_payload["signals"][0]["metadata"]["escalation_target"] == "buddy"

    def test_workspace_signal_action_resumes_handoff_review_session(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Workspace Handoff", "description": "Operational workspace", "kind": "operations"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={
                "model": "test-model",
                "label": "Handoff session",
                "workspace_id": workspace_id,
            },
        ).get_json()["id"]

        active_plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, active_plan["id"], "Resume handoff", status="doing")
        self.app.planning.set_status(session_id, "paused")
        self.app.maintenance.create_artifact(
            session_id,
            HANDOFF_CANDIDATE_ARTIFACT,
            "Prepared handoff candidate: Resume the paused handoff review.",
            metadata={"reason": "handoff_action"},
        )

        poll_payload = self.client.post(f"/api/workspaces/{workspace_id}/runtime/poll").get_json()
        signal_id = poll_payload["emitted_signal"]["id"]

        action_resp = self.client.post(
            f"/api/workspaces/{workspace_id}/signals/{signal_id}/actions",
            json={"action": "resume_plan"},
        )

        assert action_resp.status_code == 200
        action_payload = action_resp.get_json()
        assert action_payload["signal"]["status"] == "resolved"
        assert action_payload["updated_item"] is None
        assert action_payload["snapshot"]["signals"] == []

        refreshed_plan = self.app.planning.get_plan(session_id)
        assert refreshed_plan["status"] == "active"
