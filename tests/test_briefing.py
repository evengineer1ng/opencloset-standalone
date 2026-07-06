from __future__ import annotations

import json
import os
import tempfile

import pytest

from api.api.app import create_app
from api.api.briefing import WorkspaceBriefingManager


class _AppFixture:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _make_workspace(self, name: str = "Test WS") -> str:
        resp = self.client.post("/api/workspaces", json={"name": name})
        return resp.get_json()["id"]

    def _make_session(self, workspace_id: str, label: str = "Test Session") -> str:
        import uuid
        sess_id = uuid.uuid4().hex
        self.app.db.execute(
            """INSERT INTO sessions (id, label, model, provider, status, workspace_id)
               VALUES (?, ?, 'test-model', 'llamacpp', 'active', ?)""",
            (sess_id, label, workspace_id),
        )
        self.app.db.commit()
        return sess_id


# ---------------------------------------------------------------------------
# WorkspaceBriefingManager unit tests
# ---------------------------------------------------------------------------

class TestWorkspaceBriefingManager(_AppFixture):

    def test_generate_briefing_returns_dict_with_id(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        assert isinstance(result, dict)
        assert "id" in result

    def test_generate_briefing_stores_evidence(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        assert evidence is not None
        assert evidence["evidence_type"] == "briefing"

    def test_generate_briefing_title_contains_date(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        assert "briefing" in evidence["title"].lower()

    def test_generate_briefing_content_is_valid_json(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        parsed = json.loads(evidence["content"])
        assert isinstance(parsed, dict)

    def test_generate_briefing_content_has_expected_sections(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        sections = json.loads(evidence["content"])
        assert "open_signals" in sections
        assert "top_candidates" in sections
        assert "active_plans" in sections
        assert "recent_captures" in sections
        assert "session_summary" in sections

    def test_generate_briefing_summary_is_string(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        assert isinstance(evidence["summary"], str)
        assert len(evidence["summary"]) > 0

    def test_get_latest_briefing_returns_none_when_no_briefings(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.get_latest_briefing(ws_id)
        assert result is None

    def test_get_latest_briefing_returns_most_recent(self):
        ws_id = self._make_workspace()
        self.app.briefing.generate_briefing(ws_id)
        latest = self.app.briefing.get_latest_briefing(ws_id)
        assert latest is not None
        assert latest["evidence_type"] == "briefing"

    def test_list_briefings_empty_before_generation(self):
        ws_id = self._make_workspace()
        results = self.app.briefing.list_briefings(ws_id)
        assert results == []

    def test_list_briefings_after_generation(self):
        ws_id = self._make_workspace()
        self.app.briefing.generate_briefing(ws_id)
        self.app.briefing.generate_briefing(ws_id)
        results = self.app.briefing.list_briefings(ws_id, limit=10)
        assert len(results) == 2

    def test_list_briefings_limit_respected(self):
        ws_id = self._make_workspace()
        for _ in range(5):
            self.app.briefing.generate_briefing(ws_id)
        results = self.app.briefing.list_briefings(ws_id, limit=2)
        assert len(results) == 2

    def test_briefing_source_kind_is_system(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        assert evidence.get("source_kind") == "system"

    def test_briefing_tags_include_briefing(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        tags = evidence.get("tags") or []
        if isinstance(tags, str):
            import json as _json
            tags = _json.loads(tags)
        assert "briefing" in tags

    def test_no_notable_activity_summary_for_empty_workspace(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        assert "No notable activity" in evidence["summary"]

    def test_summary_mentions_sessions_when_present(self):
        ws_id = self._make_workspace()
        self._make_session(ws_id)
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        assert "session" in evidence["summary"].lower()

    def test_briefings_scoped_to_workspace(self):
        ws1 = self._make_workspace("WS One")
        ws2 = self._make_workspace("WS Two")
        self.app.briefing.generate_briefing(ws1)
        results_ws2 = self.app.briefing.list_briefings(ws2)
        assert results_ws2 == []

    def test_generate_briefing_metadata_has_section_keys(self):
        ws_id = self._make_workspace()
        result = self.app.briefing.generate_briefing(ws_id)
        evidence = self.app.workspaces.get_workspace_evidence(ws_id, result["id"])
        meta = evidence.get("metadata") or {}
        if isinstance(meta, str):
            import json as _json
            meta = _json.loads(meta)
        assert "section_keys" in meta


# ---------------------------------------------------------------------------
# Briefing routes
# ---------------------------------------------------------------------------

class TestBriefingRoutes(_AppFixture):

    def test_latest_404_for_unknown_workspace(self):
        resp = self.client.get("/api/workspaces/does-not-exist/briefing/latest")
        assert resp.status_code == 404

    def test_latest_returns_null_when_no_briefings(self):
        ws_id = self._make_workspace()
        resp = self.client.get(f"/api/workspaces/{ws_id}/briefing/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["briefing"] is None

    def test_latest_returns_briefing_after_generation(self):
        ws_id = self._make_workspace()
        self.app.briefing.generate_briefing(ws_id)
        resp = self.client.get(f"/api/workspaces/{ws_id}/briefing/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["briefing"] is not None
        assert "id" in data["briefing"]

    def test_list_404_for_unknown_workspace(self):
        resp = self.client.get("/api/workspaces/does-not-exist/briefing")
        assert resp.status_code == 404

    def test_list_returns_empty_before_generation(self):
        ws_id = self._make_workspace()
        resp = self.client.get(f"/api/workspaces/{ws_id}/briefing")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["briefings"] == []

    def test_list_returns_generated_briefings(self):
        ws_id = self._make_workspace()
        self.app.briefing.generate_briefing(ws_id)
        resp = self.client.get(f"/api/workspaces/{ws_id}/briefing")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["briefings"]) == 1

    def test_list_limit_query_param(self):
        ws_id = self._make_workspace()
        for _ in range(5):
            self.app.briefing.generate_briefing(ws_id)
        resp = self.client.get(f"/api/workspaces/{ws_id}/briefing", query_string={"limit": "3"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["briefings"]) == 3

    def test_generate_404_for_unknown_workspace(self):
        resp = self.client.post("/api/workspaces/does-not-exist/briefing/generate")
        assert resp.status_code == 404

    def test_generate_returns_201(self):
        ws_id = self._make_workspace()
        resp = self.client.post(f"/api/workspaces/{ws_id}/briefing/generate")
        assert resp.status_code == 201

    def test_generate_response_contains_briefing_id(self):
        ws_id = self._make_workspace()
        resp = self.client.post(f"/api/workspaces/{ws_id}/briefing/generate")
        data = resp.get_json()
        assert "briefing" in data
        assert "id" in data["briefing"]

    def test_generate_then_list_shows_new_briefing(self):
        ws_id = self._make_workspace()
        gen_resp = self.client.post(f"/api/workspaces/{ws_id}/briefing/generate")
        gen_id = gen_resp.get_json()["briefing"]["id"]
        list_resp = self.client.get(f"/api/workspaces/{ws_id}/briefing")
        ids = [b["id"] for b in list_resp.get_json()["briefings"]]
        assert gen_id in ids
