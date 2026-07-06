from __future__ import annotations

import os
import tempfile

from api.api.app import create_app
from api.tools.planning_tools import (
    make_plan_add_item_tool,
    make_plan_activate_tool,
    make_plan_archive_tool,
    make_plan_create_tool,
    make_plan_get_active_tool,
    make_plan_list_stored_tool,
    make_plan_propose_change_tool,
    make_plan_accept_proposal_tool,
    make_plan_reject_proposal_tool,
    make_plan_reorder_tool,
    make_plan_set_status_tool,
    register_planning_tools,
)
from api.tools.registry import ToolRegistry


class TestPlanningToolMetadata:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()
        resp = self.client.post("/api/sessions", json={"model": "test-model", "label": "planning tools"})
        self.session_id = resp.get_json()["id"]

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_make_plan_get_active_tool_metadata(self):
        tool = make_plan_get_active_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_get_active"
        assert tool.read_only is True
        assert "extended" in tool.categories

    def test_make_plan_add_item_tool_metadata(self):
        tool = make_plan_add_item_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_add_item"
        assert tool.input_schema["required"] == ["content"]
        assert "extended" in tool.categories

    def test_make_plan_list_stored_tool_metadata(self):
        tool = make_plan_list_stored_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_list_stored"
        assert tool.read_only is True
        assert "extended" in tool.categories

    def test_make_plan_set_status_tool_metadata(self):
        tool = make_plan_set_status_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_set_status"
        assert tool.input_schema["required"] == ["status"]
        assert "runtime status only" in tool.description.lower()
        assert "extended" in tool.categories

    def test_make_plan_create_tool_metadata(self):
        tool = make_plan_create_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_create"
        assert tool.input_schema["required"] == ["title"]

    def test_make_plan_activate_tool_metadata(self):
        tool = make_plan_activate_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_activate"
        assert tool.input_schema["required"] == ["plan_id"]

    def test_make_plan_reorder_tool_metadata(self):
        tool = make_plan_reorder_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_reorder"
        assert tool.input_schema["required"] == ["item_id", "position"]

    def test_make_plan_archive_tool_metadata(self):
        tool = make_plan_archive_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_archive"
        assert tool.input_schema["required"] == ["plan_id"]

    def test_make_plan_propose_change_tool_metadata(self):
        tool = make_plan_propose_change_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_propose_change"
        assert tool.input_schema["required"] == ["proposal_type", "payload"]

    def test_make_plan_accept_proposal_tool_metadata(self):
        tool = make_plan_accept_proposal_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_accept_proposal"
        assert tool.input_schema["required"] == ["proposal_id"]

    def test_make_plan_reject_proposal_tool_metadata(self):
        tool = make_plan_reject_proposal_tool(planning=self.app.planning, session_id=self.session_id)
        assert tool.name == "plan_reject_proposal"
        assert tool.input_schema["required"] == ["proposal_id"]

    def test_register_in_registry(self):
        reg = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=[],
            provider_capabilities={"supports_tool_use": True},
        )
        tools = register_planning_tools(reg, planning=self.app.planning, session_id=self.session_id)
        assert {
            "plan_get_active",
            "plan_list_stored",
            "plan_add_item",
            "plan_set_status",
            "plan_create",
            "plan_activate",
            "plan_reorder",
            "plan_archive",
            "plan_list_proposals",
            "plan_accept_proposal",
            "plan_reject_proposal",
        }.issubset({tool.name for tool in tools})

    def test_register_for_buddy_uses_proposal_tools_instead_of_acceptance(self):
        reg = ToolRegistry(
            agent_type="buddy",
            trust_mode="allowlist",
            allowlist=[],
            provider_capabilities={"supports_tool_use": True},
        )
        tools = register_planning_tools(reg, planning=self.app.planning, session_id=self.session_id)
        names = {tool.name for tool in tools}
        assert "plan_propose_change" in names
        assert "plan_accept_proposal" not in names
        assert "plan_reject_proposal" not in names


class TestPlanningToolExecution:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()
        resp = self.client.post("/api/sessions", json={"model": "test-model", "label": "planning tools"})
        self.session_id = resp.get_json()["id"]

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_plan_get_active_returns_plan(self):
        tool = make_plan_get_active_tool(planning=self.app.planning, session_id=self.session_id)
        result = tool.execute({})
        assert result["session_id"] == self.session_id
        assert "items" in result

    def test_plan_add_item_updates_active_plan(self):
        tool = make_plan_add_item_tool(planning=self.app.planning, session_id=self.session_id)
        result = tool.execute({"content": "Add browser plan controls", "status": "doing"})
        assert result["item"]["content"] == "Add browser plan controls"
        plan = self.app.planning.get_plan(self.session_id)
        assert any(item["content"] == "Add browser plan controls" for item in plan["items"])

    def test_plan_list_stored_returns_accessible_workspace_plans(self):
        workspace_id = self.client.post(
            "/api/workspaces",
            json={"name": "Algotrading Lab", "description": "shared workspace"},
        ).get_json()["id"]
        session_id = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "planning tools scoped", "workspace_id": workspace_id},
        ).get_json()["id"]
        other_session = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "other", "workspace_id": workspace_id},
        ).get_json()["id"]
        other_plan_id = self.app.planning.create_plan(
            other_session,
            title="Trading Bot Artifact Analysis & Report",
            active_goal="Inventory copied artifacts",
            activate=False,
        )

        tool = make_plan_list_stored_tool(planning=self.app.planning, session_id=session_id)
        result = tool.execute({"query": "Trading Bot", "limit": 10})

        assert result["count"] >= 1
        assert any(plan["id"] == other_plan_id for plan in result["plans"])

    def test_plan_set_status_updates_runtime_status(self):
        tool = make_plan_set_status_tool(planning=self.app.planning, session_id=self.session_id)
        result = tool.execute({"status": "paused"})
        assert result["status"] == "paused"
        assert result["runtime_status"] == "paused"
        assert result["scope"] == "runtime_only"
        assert "does not change plan items" in result["note"].lower()
        assert self.app.planning.get_plan(self.session_id)["status"] == "paused"

    def test_plan_create_and_activate_tools_switch_active_plan(self):
        create_tool = make_plan_create_tool(planning=self.app.planning, session_id=self.session_id)
        active_before = self.app.planning.get_plan(self.session_id)["id"]

        created = create_tool.execute({"title": "Stored Plan", "active_goal": "switch later", "activate": False})
        assert created["id"] != active_before
        assert created["is_active"] is False

        activate_tool = make_plan_activate_tool(planning=self.app.planning, session_id=self.session_id)
        activated = activate_tool.execute({"plan_id": created["id"]})
        assert activated["id"] == created["id"]
        assert self.app.planning.get_plan(self.session_id)["id"] == created["id"]

    def test_plan_reorder_updates_item_position(self):
        active_plan = self.app.planning.get_plan(self.session_id)
        first = self.app.planning.add_plan_item(self.session_id, active_plan["id"], "First")
        second = self.app.planning.add_plan_item(self.session_id, active_plan["id"], "Second")

        tool = make_plan_reorder_tool(planning=self.app.planning, session_id=self.session_id)
        result = tool.execute({"item_id": second["id"], "position": 1})

        assert result["item"]["id"] == second["id"]
        ordered = self.app.planning.list_plan_items(self.session_id, active_plan["id"])
        assert [item["id"] for item in ordered] == [second["id"], first["id"]]

    def test_plan_archive_marks_non_active_plan_archived(self):
        active_plan_id = self.app.planning.get_plan(self.session_id)["id"]
        archived_plan_id = self.app.planning.create_plan(
            self.session_id,
            title="Archive Me",
            active_goal="done",
            activate=False,
        )
        assert archived_plan_id != active_plan_id

        tool = make_plan_archive_tool(planning=self.app.planning, session_id=self.session_id)
        result = tool.execute({"plan_id": archived_plan_id})

        assert result["plan_status"] == "archived"
        assert self.app.planning.get_plan_by_id(self.session_id, archived_plan_id)["plan_status"] == "archived"

    def test_buddy_proposal_requires_separate_acceptance(self):
        buddy_tool = make_plan_propose_change_tool(planning=self.app.planning, session_id=self.session_id, proposed_by="buddy")
        proposal = buddy_tool.execute(
            {
                "proposal_type": "add_item",
                "payload": {"content": "Buddy suggests this", "status": "todo"},
            }
        )

        active_plan = self.app.planning.get_plan(self.session_id)
        assert proposal["status"] == "pending"
        assert not any(item["content"] == "Buddy suggests this" for item in active_plan["items"])

        accept_tool = make_plan_accept_proposal_tool(planning=self.app.planning, session_id=self.session_id)
        accepted = accept_tool.execute({"proposal_id": proposal["id"]})

        assert accepted["proposal"]["status"] == "accepted"
        refreshed = self.app.planning.get_plan(self.session_id)
        assert any(item["content"] == "Buddy suggests this" for item in refreshed["items"])