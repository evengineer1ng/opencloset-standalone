from __future__ import annotations

import os
import tempfile

from api.api.app import create_app
from api.tools.memory_tools import make_memory_search_tool, register_memory_tools
from api.tools.registry import ToolRegistry


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


class TestMemoryToolMetadata:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.memory_dir = tempfile.mkdtemp()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()
        self.app.memory = self.app.memory.__class__(self.memory_dir)
        resp = self.client.post("/api/sessions", json={"model": "test-model", "label": "memory tools"})
        self.session_id = resp.get_json()["id"]

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_make_memory_search_tool_metadata(self):
        tool = make_memory_search_tool(memory_manager=self.app.memory, session_id=self.session_id)
        assert tool.name == "memory_search"
        assert tool.read_only is True
        assert tool.input_schema["required"] == ["query"]

    def test_register_in_registry(self):
        reg = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["memory_search"],
            provider_capabilities={"supports_tool_use": True},
        )
        tools = register_memory_tools(reg, memory_manager=self.app.memory, session_id=self.session_id)
        assert len(tools) == 1
        assert tools[0].name == "memory_search"


class TestMemoryToolExecution:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.memory_dir = tempfile.mkdtemp()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()
        self.app.memory = self.app.memory.__class__(self.memory_dir)
        resp = self.client.post("/api/sessions", json={"model": "test-model", "label": "memory tools"})
        self.session_id = resp.get_json()["id"]
        self.app.memory.append_session_entry(self.session_id, "Searchable deterministic memory note")

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_memory_search_returns_hits(self):
        tool = make_memory_search_tool(memory_manager=self.app.memory, session_id=self.session_id)
        result = tool.execute({"query": "deterministic", "include_seen": True})
        assert result["session_id"] == self.session_id
        assert result["strategy"] == "ranked-keyword"
        assert len(result["results"]) == 1
        assert "deterministic" in result["results"][0]["snippet"].lower()
        assert result["results"][0]["score"] > 0

    def test_memory_search_marks_hits_as_seen(self):
        tool = make_memory_search_tool(memory_manager=self.app.memory, session_id=self.session_id)
        first = tool.execute({"query": "deterministic", "include_seen": True})
        assert len(first["results"]) == 1

        second = tool.execute({"query": "deterministic"})
        assert second["results"] == []

    def test_memory_search_reports_hybrid_strategy_for_semantic_hits(self):
        self.app.memory = self.app.memory.__class__(self.memory_dir, embedder=FakeEmbedder())
        self.app.memory.append_session_entry(self.session_id, "Keep the blocker visible in memory")

        tool = make_memory_search_tool(memory_manager=self.app.memory, session_id=self.session_id)
        result = tool.execute({"query": "roadblock", "include_seen": True, "include_daily": False})

        assert result["strategy"] == "hybrid-semantic"
        assert len(result["results"]) == 1
        assert result["results"][0]["sources"] == ["semantic"]
        assert result["results"][0]["semantic_score"] > 0.9