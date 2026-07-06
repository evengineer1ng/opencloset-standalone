# Tests for persistent planning schema + API

from __future__ import annotations

import os
import sqlite3
import tempfile

from api.api.app import create_app
from api.api.planning import PlanningManager
from api.agent.prompt import PromptBuilder, default_env_facts


class TestPlanningManager:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()
        self.pm = self.app.planning

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_session(self) -> str:
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "plan test"},
        )
        return resp.get_json()["id"]

    def _create_workspace(self, name: str = "Workspace A") -> str:
        resp = self.client.post("/api/workspaces", json={"name": name})
        return resp.get_json()["id"]

    def _create_scoped_session(self, workspace_id: str, *, build_project_id: str | None = None) -> str:
        payload = {
            "model": "test-model",
            "label": "scoped session",
            "workspace_id": workspace_id,
        }
        if build_project_id:
            payload["build_project_id"] = build_project_id
        resp = self.client.post("/api/sessions", json=payload)
        return resp.get_json()["id"]

    def test_ensure_session_state_tolerates_duplicate_bootstrap_race(self):
        class _Row:
            pass

        class _Cursor:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        class _RaceDB:
            def __init__(self):
                self.state_exists = False

            def execute(self, query, params=()):
                if "SELECT id FROM sessions" in query:
                    return _Cursor(_Row())
                if "SELECT session_id FROM session_plan_state" in query:
                    return _Cursor(_Row() if self.state_exists else None)
                if "INSERT INTO session_plan_state" in query:
                    self.state_exists = True
                    raise sqlite3.IntegrityError("UNIQUE constraint failed: session_plan_state.session_id")
                raise AssertionError(f"Unexpected query: {query}")

            def commit(self):
                raise AssertionError("commit should not run when duplicate bootstrap race is recovered")

        pm = PlanningManager(_RaceDB())

        assert pm._ensure_session_state("session-1") is True

    # -- CRUD --

    def test_bootstrap_session_has_active_plan(self):
        session_id = self._create_session()
        plan = self.pm.get_plan(session_id)
        assert plan is not None
        assert plan["title"] == "Session Plan"
        assert plan["status"] == "active"

    def test_get_plan_not_found(self):
        assert self.pm.get_plan_by_id("nonexistent", "missing") is None

    def test_create_plan_activates_new_plan(self):
        session_id = self._create_session()
        plan_id = self.pm.create_plan(
            session_id,
            title="Build API",
            active_goal="build something",
            want_to_know=["api docs"],
        )
        plan = self.pm.get_plan(session_id)
        assert plan["id"] == plan_id
        assert plan["active_goal"] == "build something"
        assert plan["want_to_know"] == ["api docs"]

    def test_create_plan_inherits_session_workspace_scope(self):
        workspace_id = self._create_workspace()
        project_id = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Project A"},
        ).get_json()["id"]
        session_id = self._create_scoped_session(workspace_id, build_project_id=project_id)

        plan_id = self.pm.create_plan(session_id, title="Shared", active_goal="scope me", activate=False)

        plan = self.pm.get_plan_by_id(session_id, plan_id)
        assert plan["workspace_id"] == workspace_id
        assert plan["build_project_id"] == project_id

    def test_list_plans_shows_active_flag(self):
        session_id = self._create_session()
        first_active = self.pm.get_plan(session_id)["id"]
        second_plan = self.pm.create_plan(session_id, title="Second", active_goal="two")
        plans = self.pm.list_plans(session_id)

        assert len(plans) == 2
        assert any(plan["id"] == first_active for plan in plans)
        assert any(plan["id"] == second_plan and plan["is_active"] for plan in plans)

    def test_activate_plan_switches_active_plan(self):
        session_id = self._create_session()
        first_plan = self.pm.get_plan(session_id)["id"]
        second_plan = self.pm.create_plan(session_id, title="Alternative", active_goal="alt", activate=False)

        self.pm.activate_plan(session_id, second_plan)

        active = self.pm.get_plan(session_id)
        assert active["id"] == second_plan
        assert self.pm.get_plan_by_id(session_id, first_plan)["is_active"] is False

    def test_list_plans_includes_workspace_accessible_plans(self):
        workspace_id = self._create_workspace()
        first_session_id = self._create_scoped_session(workspace_id)
        second_session_id = self._create_scoped_session(workspace_id)

        shared_plan_id = self.pm.create_plan(
            first_session_id,
            title="Workspace Shared",
            active_goal="reuse across sessions",
            activate=False,
        )

        plans = self.pm.list_plans(second_session_id)

        assert any(plan["id"] == shared_plan_id for plan in plans)

    def test_activate_workspace_plan_from_peer_session(self):
        workspace_id = self._create_workspace()
        first_session_id = self._create_scoped_session(workspace_id)
        second_session_id = self._create_scoped_session(workspace_id)

        shared_plan_id = self.pm.create_plan(
            first_session_id,
            title="Workspace Shared",
            active_goal="shared goal",
            activate=False,
        )
        self.pm.add_plan_item(first_session_id, shared_plan_id, "Shared next action", status="doing")

        self.pm.activate_plan(second_session_id, shared_plan_id)

        active = self.pm.get_plan(second_session_id)
        assert active["id"] == shared_plan_id
        assert active["active_goal"] == "shared goal"
        assert active["next_item"]["content"] == "Shared next action"

    def test_project_scoped_plan_stays_with_matching_project(self):
        workspace_id = self._create_workspace()
        project_a = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Project A"},
        ).get_json()["id"]
        project_b = self.client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Project B"},
        ).get_json()["id"]

        first_session_id = self._create_scoped_session(workspace_id, build_project_id=project_a)
        second_session_id = self._create_scoped_session(workspace_id, build_project_id=project_b)

        shared_plan_id = self.pm.create_plan(
            first_session_id,
            title="Project A Plan",
            active_goal="only project a",
            activate=False,
        )

        assert all(plan["id"] != shared_plan_id for plan in self.pm.list_plans(second_session_id))
        assert self.pm.get_plan_by_id(second_session_id, shared_plan_id) is None

    def test_workspace_scoped_plan_proposals_emit_captures(self):
        workspace_id = self._create_workspace()
        session_id = self._create_scoped_session(workspace_id)
        plan_id = self.pm.get_plan(session_id)["id"]

        pending = self.pm.submit_plan_proposal(
            session_id,
            "add_item",
            {"content": "Route this through review"},
            plan_id=plan_id,
            summary="Add reviewed item",
            proposed_by="buddy",
        )

        pending_captures = self.app.workspaces.list_workspace_captures(workspace_id, session_id=session_id)
        pending_capture = next(item for item in pending_captures if item["metadata"].get("proposal_id") == pending["id"])
        assert pending_capture["event_type"] == "plan_proposal"
        assert pending_capture["metadata"]["proposal_status"] == "pending"
        assert pending_capture["status"] == "pending"

        accepted = self.pm.accept_plan_proposal(session_id, pending["id"], accepted_by="clo")

        accepted_captures = self.app.workspaces.list_workspace_captures(workspace_id, session_id=session_id)
        accepted_capture = next(
            item
            for item in accepted_captures
            if item["metadata"].get("proposal_id") == accepted["proposal"]["id"]
            and item["metadata"].get("proposal_status") == "accepted"
        )
        assert accepted_capture["status"] == "processed"

        rejected = self.pm.submit_plan_proposal(
            session_id,
            "add_item",
            {"content": "Reject this route"},
            plan_id=plan_id,
            summary="Reject item",
            proposed_by="buddy",
        )
        self.pm.reject_plan_proposal(session_id, rejected["id"], rejected_by="clo", resolution_note="not now")

        final_captures = self.app.workspaces.list_workspace_captures(workspace_id, session_id=session_id)
        rejected_capture = next(
            item
            for item in final_captures
            if item["metadata"].get("proposal_id") == rejected["id"]
            and item["metadata"].get("proposal_status") == "rejected"
        )
        assert rejected_capture["metadata"]["resolution_note"] == "not now"
        assert rejected_capture["status"] == "processed"

    def test_add_plan_item_sets_next_item(self):
        session_id = self._create_session()
        plan_id = self.pm.get_plan(session_id)["id"]

        first_item = self.pm.add_plan_item(session_id, plan_id, "First action")
        self.pm.add_plan_item(session_id, plan_id, "Second action")

        plan = self.pm.get_plan(session_id)
        assert [item["content"] for item in plan["items"]] == ["First action", "Second action"]
        assert plan["next_item"]["id"] == first_item["id"]

    def test_update_plan_item_reorders_and_archives(self):
        session_id = self._create_session()
        plan_id = self.pm.get_plan(session_id)["id"]

        first = self.pm.add_plan_item(session_id, plan_id, "First")
        second = self.pm.add_plan_item(session_id, plan_id, "Second")

        self.pm.update_plan_item(session_id, plan_id, second["id"], position=1, status="doing")
        self.pm.update_plan_item(session_id, plan_id, first["id"], archived=True)

        items = self.pm.list_plan_items(session_id, plan_id)
        archived_items = self.pm.list_plan_items(session_id, plan_id, include_archived=True)
        assert items[0]["id"] == second["id"]
        assert items[0]["status"] == "doing"
        assert len(items) == 1
        assert any(item["id"] == first["id"] and item["archived"] for item in archived_items)

    def test_plan_revisions_capture_item_changes(self):
        session_id = self._create_session()
        plan_id = self.pm.get_plan(session_id)["id"]

        self.pm.add_plan_item(session_id, plan_id, "First")
        revisions = self.pm.list_plan_revisions(session_id, plan_id)

        assert revisions
        assert revisions[0]["change_type"] == "item_added"
        assert revisions[0]["snapshot"]["items"][0]["content"] == "First"
        assert len(revisions[0]["diff"]["item_ids_added"]) == 1
        assert revisions[0]["diff"]["item_ids_updated"] == []

    def test_rollover_plan_clones_active_plan_state(self):
        source_session_id = self._create_session()
        source_plan = self.pm.get_plan(source_session_id)
        self.pm.update_active_goal(source_session_id, "Carry me forward")
        self.pm.update_want_to_know(source_session_id, ["Keep continuity"])
        item = self.pm.add_plan_item(source_session_id, source_plan["id"], "Do the next thing", status="doing")

        target_session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "successor"},
        )
        target_session_id = target_session_resp.get_json()["id"]

        successor_plan = self.pm.rollover_plan(source_session_id, target_session_id)

        assert successor_plan["active_goal"] == "Carry me forward"
        assert successor_plan["next_item"]["content"] == "Do the next thing"
        assert successor_plan["handoff"]["source_session_id"] == source_session_id
        assert successor_plan["handoff"]["active_plan_id"] == source_plan["id"]
        assert successor_plan["handoff"]["next_item_status"] == item["status"]

    def test_update_active_goal(self):
        session_id = self._create_session()
        self.pm.update_active_goal(session_id, "new goal")
        plan = self.pm.get_plan(session_id)
        assert plan["active_goal"] == "new goal"

    # -- Want to know --

    def test_update_want_to_know(self):
        session_id = self._create_session()
        self.pm.update_want_to_know(session_id, ["a", "b", "c"])
        assert self.pm.get_plan(session_id)["want_to_know"] == ["a", "b", "c"]

    def test_add_want_to_know(self):
        session_id = self._create_session()
        self.pm.update_want_to_know(session_id, ["api docs"])
        self.pm.add_want_to_know(session_id, "new item")
        wtk = self.pm.get_plan(session_id)["want_to_know"]
        assert "new item" in wtk
        assert "api docs" in wtk

    def test_add_want_to_know_no_dupes(self):
        session_id = self._create_session()
        self.pm.update_want_to_know(session_id, ["api docs"])
        self.pm.add_want_to_know(session_id, "api docs")
        wtk = self.pm.get_plan(session_id)["want_to_know"]
        assert wtk.count("api docs") == 1

    def test_remove_want_to_know(self):
        session_id = self._create_session()
        self.pm.update_want_to_know(session_id, ["api docs"])
        self.pm.remove_want_to_know(session_id, "api docs")
        assert "api docs" not in self.pm.get_plan(session_id)["want_to_know"]

    # -- Context guard --

    def test_update_context_guard(self):
        session_id = self._create_session()
        self.pm.update_context_guard(session_id, tokens_used=40000)
        guard = self.pm.get_plan(session_id)["context_guard"]
        assert guard["tokens_used"] == 40000

    def test_should_rollover_false(self):
        session_id = self._create_session()
        assert self.pm.should_rollover(session_id) is False

    def test_should_rollover_true(self):
        session_id = self._create_session()
        self.pm.rollover_guard_enabled = True
        self.pm.update_context_guard(session_id, tokens_used=60000)
        assert self.pm.should_rollover(session_id) is True

    def test_should_rollover_false_when_guard_disabled(self):
        session_id = self._create_session()
        self.pm.rollover_guard_enabled = False
        self.pm.update_context_guard(session_id, tokens_used=60000)
        assert self.pm.should_rollover(session_id) is False

    # -- Handoff --

    def test_set_handoff(self):
        session_id = self._create_session()
        self.pm.set_handoff(session_id, {"summary": "work done", "next_step": "continue"})
        plan = self.pm.get_plan(session_id)
        assert plan["handoff"]["summary"] == "work done"

    def test_clear_handoff(self):
        session_id = self._create_session()
        self.pm.set_handoff(session_id, {"x": 1})
        self.pm.clear_handoff(session_id)
        assert self.pm.get_plan(session_id)["handoff"] is None

    # -- Status --

    def test_set_status(self):
        session_id = self._create_session()
        self.pm.set_status(session_id, "paused")
        assert self.pm.get_plan(session_id)["status"] == "paused"

    def test_set_invalid_status(self):
        session_id = self._create_session()
        try:
            self.pm.set_status(session_id, "invalid")
            assert False, "Should raise ValueError"
        except ValueError:
            pass

    # -- Delete --

    def test_delete_plan(self):
        session_id = self._create_session()
        assert self.pm.delete_plan(session_id) is True
        assert self.pm.get_plan(session_id) is None

    def test_delete_plan_not_found(self):
        assert self.pm.delete_plan("nonexistent") is False

    def test_active_plan_slice_still_drives_prompt_builder(self):
        session_id = self._create_session()
        self.pm.update_active_goal(session_id, "Ship planning spine")
        self.pm.update_want_to_know(session_id, ["Validate replay", "Keep prompt lean"])
        plan_id = self.pm.get_plan(session_id)["id"]
        self.pm.add_plan_item(session_id, plan_id, "Land next actionable item", status="doing")

        plan_slice = PromptBuilder.extract_plan_slice(self.pm.get_plan(session_id))
        builder = PromptBuilder(base_identity="Base identity", env_facts=default_env_facts(workspace="D:/openclaw"))
        assembled = builder.assemble(transcript=[], plan_data=plan_slice)
        system_content = assembled.messages[0]["content"]

        assert "## Active Plan" in system_content
        assert "Ship planning spine" in system_content
        assert "Validate replay" in system_content
        assert "Land next actionable item" in system_content


class TestPlanningRoutes:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()
        self.pm = self.app.planning

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_session(self) -> str:
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "plan route test"},
        )
        return resp.get_json()["id"]

    def _create_workspace(self, name: str = "Workspace A") -> str:
        return self.client.post("/api/workspaces", json={"name": name}).get_json()["id"]

    def test_get_plan(self):
        session_id = self._create_session()
        self.pm.update_active_goal(session_id, "initial goal")
        resp = self.client.get(f"/api/sessions/{session_id}/plan")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["active_goal"] == "initial goal"

    def test_get_plan_not_found(self):
        resp = self.client.get("/api/sessions/fake-id/plan")
        assert resp.status_code == 404

    def test_patch_active_goal(self):
        session_id = self._create_session()
        resp = self.client.patch(
            f"/api/sessions/{session_id}/plan",
            json={"active_goal": "updated goal"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["active_goal"] == "updated goal"

    def test_patch_want_to_know(self):
        session_id = self._create_session()
        resp = self.client.patch(
            f"/api/sessions/{session_id}/plan",
            json={"want_to_know": ["item1", "item2"]},
        )
        assert resp.status_code == 200
        assert resp.get_json()["want_to_know"] == ["item1", "item2"]

    def test_add_want_to_know_route(self):
        session_id = self._create_session()
        resp = self.client.post(
            f"/api/sessions/{session_id}/plan/want_to_know",
            json={"item": "new thing"},
        )
        assert resp.status_code == 200
        assert "new thing" in resp.get_json()["want_to_know"]

    def test_add_wtk_no_item(self):
        session_id = self._create_session()
        resp = self.client.post(
            f"/api/sessions/{session_id}/plan/want_to_know",
            json={},
        )
        assert resp.status_code == 400

    def test_rollover_check(self):
        session_id = self._create_session()
        resp = self.client.get(f"/api/sessions/{session_id}/plan/rollover-check")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["should_rollover"] is False
        assert "tokens_used" in data
        assert "threshold" in data

    def test_list_plans_route(self):
        session_id = self._create_session()
        resp = self.client.get(f"/api/sessions/{session_id}/plans")

        assert resp.status_code == 200
        assert len(resp.get_json()["plans"]) == 1

    def test_create_plan_route(self):
        session_id = self._create_session()
        resp = self.client.post(
            f"/api/sessions/{session_id}/plans",
            json={
                "title": "Execution Plan",
                "active_goal": "implement it",
                "want_to_know": ["tests", "events"],
            },
        )

        assert resp.status_code == 201
        data = resp.get_json()
        assert data["title"] == "Execution Plan"
        assert data["is_active"] is True
        assert self.pm.get_plan(session_id)["id"] == data["id"]

    def test_activate_plan_route(self):
        session_id = self._create_session()
        initial_plan = self.pm.get_plan(session_id)["id"]
        next_plan = self.pm.create_plan(session_id, title="Next", active_goal="next", activate=False)

        resp = self.client.post(f"/api/sessions/{session_id}/plans/{next_plan}/activate")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == next_plan
        assert self.pm.get_plan(session_id)["id"] != initial_plan

    def test_delete_plan_route_switches_active_plan(self):
        session_id = self._create_session()
        initial_plan_id = self.pm.get_plan(session_id)["id"]
        next_plan_id = self.pm.create_plan(session_id, title="Next", active_goal="next", activate=True)

        resp = self.client.delete(f"/api/sessions/{session_id}/plans/{next_plan_id}")

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["deleted"] == next_plan_id
        assert payload["active_plan_id"] == initial_plan_id
        assert self.pm.get_plan(session_id)["id"] == initial_plan_id
        assert all(plan["id"] != next_plan_id for plan in self.pm.list_plans(session_id))

    def test_patch_plan_route_updates_status_and_list_filters(self):
        session_id = self._create_session()
        self.pm.create_plan(session_id, title="Dormant Plan", active_goal="leave parked", activate=False)
        target_plan_id = self.pm.create_plan(session_id, title="Keep Shipping", active_goal="ship filtering", activate=False)

        patch_resp = self.client.patch(
            f"/api/sessions/{session_id}/plans/{target_plan_id}",
            json={"status": "paused"},
        )

        assert patch_resp.status_code == 200
        assert patch_resp.get_json()["plan_status"] == "paused"

        filtered_resp = self.client.get(f"/api/sessions/{session_id}/plans?status=paused")
        assert filtered_resp.status_code == 200
        assert [plan["id"] for plan in filtered_resp.get_json()["plans"]] == [target_plan_id]

        search_resp = self.client.get(f"/api/sessions/{session_id}/plans?q=shipping")
        assert search_resp.status_code == 200
        assert [plan["id"] for plan in search_resp.get_json()["plans"]] == [target_plan_id]

    def test_plan_activation_history_route_lists_activation_events(self):
        session_id = self._create_session()
        initial_plan_id = self.pm.get_plan(session_id)["id"]
        target_plan_id = self.pm.create_plan(session_id, title="History Plan", active_goal="audit switches", activate=False)

        activate_resp = self.client.post(f"/api/sessions/{session_id}/plans/{target_plan_id}/activate")
        assert activate_resp.status_code == 200

        history_resp = self.client.get(f"/api/sessions/{session_id}/plans/{target_plan_id}/activation-history")

        assert history_resp.status_code == 200
        history = history_resp.get_json()["history"]
        assert len(history) == 1
        assert history[0]["plan_id"] == target_plan_id
        assert history[0]["previous_plan_id"] == initial_plan_id
        assert history[0]["reason"] == "user"

    def test_plan_proposal_routes_submit_and_accept_separately(self):
        session_id = self._create_session()

        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/plan-proposals",
            json={
                "proposal_type": "add_item",
                "proposed_by": "buddy",
                "payload": {"content": "Queued suggestion", "status": "todo"},
            },
        )

        assert submit_resp.status_code == 201
        proposal = submit_resp.get_json()
        assert proposal["status"] == "pending"
        assert not any(item["content"] == "Queued suggestion" for item in self.pm.get_plan(session_id)["items"])

        accept_resp = self.client.post(f"/api/sessions/{session_id}/plan-proposals/{proposal['id']}/accept")

        assert accept_resp.status_code == 200
        accepted = accept_resp.get_json()
        assert accepted["proposal"]["status"] == "accepted"
        assert any(item["content"] == "Queued suggestion" for item in self.pm.get_plan(session_id)["items"])

    def test_plan_proposal_route_can_reject_without_applying(self):
        session_id = self._create_session()
        submit_resp = self.client.post(
            f"/api/sessions/{session_id}/plan-proposals",
            json={
                "proposal_type": "add_item",
                "proposed_by": "buddy",
                "payload": {"content": "Skip this one", "status": "todo"},
            },
        )
        proposal_id = submit_resp.get_json()["id"]

        reject_resp = self.client.post(
            f"/api/sessions/{session_id}/plan-proposals/{proposal_id}/reject",
            json={"resolution_note": "not now"},
        )

        assert reject_resp.status_code == 200
        rejected = reject_resp.get_json()
        assert rejected["proposal"]["status"] == "rejected"
        assert rejected["proposal"]["resolution_note"] == "not now"
        assert not any(item["content"] == "Skip this one" for item in self.pm.get_plan(session_id)["items"])

    def test_list_plans_route_includes_workspace_shared_plans(self):
        workspace_id = self._create_workspace()
        first_session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "one", "workspace_id": workspace_id},
        ).get_json()["id"]
        second_session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "two", "workspace_id": workspace_id},
        ).get_json()["id"]

        shared_plan_id = self.pm.create_plan(first_session_id, title="Workspace Shared", active_goal="share me", activate=False)

        resp = self.client.get(f"/api/sessions/{second_session_id}/plans")

        assert resp.status_code == 200
        assert any(plan["id"] == shared_plan_id for plan in resp.get_json()["plans"])

    def test_activate_plan_route_allows_workspace_shared_plan(self):
        workspace_id = self._create_workspace()
        first_session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "one", "workspace_id": workspace_id},
        ).get_json()["id"]
        second_session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "two", "workspace_id": workspace_id},
        ).get_json()["id"]

        shared_plan_id = self.pm.create_plan(first_session_id, title="Workspace Shared", active_goal="share me", activate=False)

        resp = self.client.post(f"/api/sessions/{second_session_id}/plans/{shared_plan_id}/activate")

        assert resp.status_code == 200
        assert resp.get_json()["id"] == shared_plan_id

    def test_activate_plan_route_not_found(self):
        session_id = self._create_session()
        resp = self.client.post(f"/api/sessions/{session_id}/plans/missing/activate")
        assert resp.status_code == 404

    def test_plan_items_routes(self):
        session_id = self._create_session()
        plan_id = self.pm.get_plan(session_id)["id"]

        create_resp = self.client.post(
            f"/api/sessions/{session_id}/plans/{plan_id}/items",
            json={"content": "Wire item route", "status": "todo"},
        )
        assert create_resp.status_code == 201
        item_id = create_resp.get_json()["id"]

        patch_resp = self.client.patch(
            f"/api/sessions/{session_id}/plans/{plan_id}/items/{item_id}",
            json={"status": "doing", "position": 1},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.get_json()["status"] == "doing"

        list_resp = self.client.get(f"/api/sessions/{session_id}/plans/{plan_id}/items")
        assert list_resp.status_code == 200
        assert list_resp.get_json()["items"][0]["id"] == item_id

    def test_plan_revisions_route(self):
        session_id = self._create_session()
        plan_id = self.pm.get_plan(session_id)["id"]
        self.pm.add_plan_item(session_id, plan_id, "Track revisions")

        resp = self.client.get(f"/api/sessions/{session_id}/plans/{plan_id}/revisions")
        assert resp.status_code == 200
        revisions = resp.get_json()["revisions"]
        assert revisions
        assert revisions[0]["change_type"] == "item_added"

    def test_plan_items_route_rejects_bad_status(self):
        session_id = self._create_session()
        plan_id = self.pm.get_plan(session_id)["id"]

        resp = self.client.post(
            f"/api/sessions/{session_id}/plans/{plan_id}/items",
            json={"content": "Bad", "status": "wrong"},
        )
        assert resp.status_code == 400

    def test_session_events_route_includes_plan_activation(self):
        session_id = self._create_session()
        plan_id = self.pm.create_plan(session_id, title="Review", active_goal="review", activate=False)

        resp = self.client.post(f"/api/sessions/{session_id}/plans/{plan_id}/activate")
        assert resp.status_code == 200

        events_resp = self.client.get(f"/api/sessions/{session_id}/events")
        assert events_resp.status_code == 200
        events = events_resp.get_json()["events"]
        event_types = [event["type"] for event in events]
        assert "session_created" in event_types
        assert "plan_activated" in event_types

    def test_session_events_route_not_found(self):
        resp = self.client.get("/api/sessions/fake-id/events")
        assert resp.status_code == 404
