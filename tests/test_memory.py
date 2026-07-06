from __future__ import annotations

import os
import sqlite3
import tempfile

from api.api.app import create_app
from api.api.memory import MemoryManager


class FakeEmbedder:
    model_name = "fake-embed"
    enabled = True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(text) for text in texts]

    @staticmethod
    def _vectorize(text: str) -> list[float]:
        lowered = text.lower()
        blocker_axis = 1.0 if any(term in lowered for term in ("blocker", "roadblock")) else 0.0
        retrieval_axis = 1.0 if any(term in lowered for term in ("retrieval", "search", "ranking")) else 0.0
        duplicate_axis = 1.0 if any(term in lowered for term in ("duplicate", "dedup")) else 0.0
        return [blocker_axis, retrieval_axis, duplicate_axis]


class TestMemoryManager:


    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.memory_dir = tempfile.mkdtemp()
        self.app = create_app(db_path=self.db_path)
        self.app.memory = MemoryManager(self.memory_dir)
        self.client = self.app.test_client()

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
            json={"model": "test-model", "label": "memory test"},
        )
        return resp.get_json()["id"]

    def _wrap_db_with_session_error(self):
        real_db = self.app.db

        class _DbProxy:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, params=()):
                if "SELECT id FROM sessions WHERE id = ?" in sql:
                    raise sqlite3.InterfaceError("bad parameter or other API misuse")
                return self._inner.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        self.app.db = _DbProxy(real_db)

    def test_memory_manager_builds_prompt_memory(self):
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "Remember the current blocker")
        self.app.memory.append_daily_entry("Today we stabilized the workspace plan scope", date_label="2026-05-01")

        result = self.app.memory.build_prompt_memory(session_id, date_label="2026-05-01")

        assert "## Session Diary" in result
        assert "Remember the current blocker" in result
        assert "## Daily Log (2026-05-01)" in result
        assert "Today we stabilized the workspace plan scope" in result

    def test_memory_routes_store_and_read_notes(self):
        session_id = self._create_session()

        post_resp = self.client.post(
            f"/api/sessions/{session_id}/memory",
            json={"content": "Capture this in the diary", "date": "2026-05-01"},
        )

        assert post_resp.status_code == 201
        data = post_resp.get_json()
        assert "Capture this in the diary" in data["session_diary"]["content"]
        assert "Capture this in the diary" in data["daily_log"]["content"]

        get_resp = self.client.get(f"/api/sessions/{session_id}/memory?date=2026-05-01")
        assert get_resp.status_code == 200
        read_back = get_resp.get_json()
        assert "Capture this in the diary" in read_back["session_diary"]["content"]
        assert "Capture this in the diary" in read_back["daily_log"]["content"]

    def test_memory_routes_require_content(self):
        session_id = self._create_session()

        resp = self.client.post(f"/api/sessions/{session_id}/memory", json={"content": "   "})

        assert resp.status_code == 400

    def test_memory_search_returns_session_and_daily_hits(self):
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "Investigate sqlite retrieval for durable memory")
        self.app.memory.append_daily_entry("Daily note about sqlite search rollout", date_label="2026-05-01")

        results = self.app.memory.search(session_id, "sqlite")

        assert len(results) == 2
        assert any(result["kind"] == "session" for result in results)
        assert any(result["kind"] == "daily" for result in results)

    def test_memory_search_route(self):
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "Track deterministic retrieval work")

        resp = self.client.get(f"/api/sessions/{session_id}/memory/search?q=deterministic")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["query"] == "deterministic"
        assert data["strategy"] == "ranked-keyword"
        assert len(data["results"]) == 1
        assert "deterministic" in data["results"][0]["snippet"].lower()
        assert data["results"][0]["score"] > 0

    def test_memory_search_ranks_phrase_and_token_overlap(self):
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "Memory duplicate filter for prompt attachments")
        self.app.memory.append_session_entry(session_id, "Memory filter only")
        self.app.memory.append_daily_entry("Duplicate notes are noisy", date_label="2026-05-01")

        results = self.app.memory.search(session_id, "duplicate memory filter")

        assert len(results) >= 2
        assert results[0]["snippet"].lower().find("memory") != -1
        assert results[0]["matched_terms"] == ["duplicate", "memory", "filter"]
        assert results[0]["score"] > results[1]["score"]

    def test_memory_search_route_requires_query(self):
        session_id = self._create_session()

        resp = self.client.get(f"/api/sessions/{session_id}/memory/search")

        assert resp.status_code == 400

    def test_memory_route_invalid_session_id_returns_structured_sqlite_error_details(self):
        session_id = self._create_session()
        self._wrap_db_with_session_error()

        resp = self.client.get(f"/api/sessions/{session_id}/memory")

        assert resp.status_code == 400
        payload = resp.get_json()
        assert payload["error"] == "invalid session_id parameter"
        assert payload["detail"]["route"] == "get_session_memory"
        assert payload["detail"]["exception_type"] == "InterfaceError"

    def test_prepare_prompt_memory_excludes_seen_entries_after_mark(self):
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "First unseen note")

        payload = self.app.memory.prepare_prompt_memory(session_id)
        assert "First unseen note" in payload["text"]
        assert payload["entry_ids"]

        self.app.memory.mark_entries_read(session_id, payload["entry_ids"], source="prompt")

        second_payload = self.app.memory.prepare_prompt_memory(session_id)
        assert second_payload["text"] == ""
        assert second_payload["entry_ids"] == []

    def test_memory_search_excludes_seen_entries_when_requested(self):
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "Do not resend this note")

        payload = self.app.memory.prepare_prompt_memory(session_id)
        self.app.memory.mark_entries_read(session_id, payload["entry_ids"], source="prompt")

        results = self.app.memory.search(session_id, "resend", exclude_seen=True)
        assert results == []

        include_seen = self.app.memory.search(session_id, "resend", exclude_seen=False)
        assert len(include_seen) == 1

    def test_prepare_prompt_memory_resurfaces_relevant_seen_entry_for_continuity(self):
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "Remember that the harness continuity issue is about rolling synopsis reuse")

        first_payload = self.app.memory.prepare_prompt_memory(session_id)
        assert "rolling synopsis reuse" in first_payload["text"]
        self.app.memory.mark_entries_read(session_id, first_payload["entry_ids"], source="prompt")

        second_payload = self.app.memory.prepare_prompt_memory(
            session_id,
            continuity_query="Need continuity for the harness rolling synopsis",
        )

        assert "## Continuity Recall" in second_payload["text"]
        assert "rolling" in second_payload["text"].lower()
        assert "synopsis" in second_payload["text"].lower()
        assert second_payload["entry_ids"]

    def test_memory_manager_persists_embeddings_when_enabled(self):
        self.app.memory = MemoryManager(self.memory_dir, embedder=FakeEmbedder())
        session_id = self._create_session()

        self.app.memory.append_session_entry(session_id, "Remember the blocker for semantic lookup")

        row = self.app.memory.index_db.execute(
            "SELECT model, dimensions, content_hash FROM memory_embeddings"
        ).fetchone()

        assert row is not None
        assert row["model"] == "fake-embed"
        assert row["dimensions"] == 3
        assert row["content_hash"]

    def test_memory_search_hybrid_semantic_finds_synonym_hits(self):
        self.app.memory = MemoryManager(self.memory_dir, embedder=FakeEmbedder())
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "Capture the current blocker before the next run")
        self.app.memory.append_session_entry(session_id, "Review retrieval ranking after the rollout")

        results = self.app.memory.search(session_id, "roadblock", include_daily=False)

        assert len(results) == 1
        assert "blocker" in results[0]["snippet"].lower()
        assert results[0]["matched_terms"] == []
        assert results[0]["semantic_score"] > 0.9
        assert results[0]["sources"] == ["semantic"]

    def test_memory_search_route_reports_hybrid_strategy_when_enabled(self):
        self.app.memory = MemoryManager(self.memory_dir, embedder=FakeEmbedder())
        session_id = self._create_session()
        self.app.memory.append_session_entry(session_id, "Track the blocker in memory")

        resp = self.client.get(f"/api/sessions/{session_id}/memory/search?q=roadblock")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["strategy"] == "hybrid-semantic"
        assert len(data["results"]) == 1
