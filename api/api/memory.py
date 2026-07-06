from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any

from flask import jsonify, request
import requests


class OllamaEmbeddingClient:
    """Minimal Ollama embedding client for persisted semantic memory retrieval."""

    def __init__(self, *, base_url: str, model_name: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name.strip()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.model_name)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.enabled:
            raise RuntimeError("embedding model is not configured")

        try:
            response = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model_name, "input": texts},
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return [self._embed_single_legacy(text) for text in texts]
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"embedding request failed: {exc}") from exc

        data = response.json()
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            return [self._coerce_vector(vector) for vector in embeddings]

        single_embedding = data.get("embedding")
        if single_embedding is not None:
            return [self._coerce_vector(single_embedding)]

        raise RuntimeError("embedding response did not include embeddings")

    def _embed_single_legacy(self, text: str) -> list[float]:
        try:
            response = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model_name, "prompt": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"legacy embedding request failed: {exc}") from exc

        data = response.json()
        embedding = data.get("embedding")
        if embedding is None:
            raise RuntimeError("legacy embedding response did not include an embedding")
        return self._coerce_vector(embedding)

    @staticmethod
    def _coerce_vector(value: Any) -> list[float]:
        if not isinstance(value, list):
            raise RuntimeError("embedding vector must be a list")
        return [float(component) for component in value]


class MemoryManager:
    """File-backed baseline memory store for durable session and daily notes."""

    def __init__(self, root_dir: str, *, embedder: OllamaEmbeddingClient | None = None) -> None:
        self.root_dir = Path(root_dir)
        self.session_dir = self.root_dir / "sessions"
        self.daily_dir = self.root_dir / "daily"
        self.index_path = self.root_dir / "memory_index.db"
        self.embedder = embedder if embedder and embedder.enabled else None
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.index_db = sqlite3.connect(self.index_path, check_same_thread=False)
        self.index_db.row_factory = sqlite3.Row
        self.fts_enabled = self._init_index()

    def close(self) -> None:
        try:
            self.index_db.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    @property
    def search_strategy(self) -> str:
        return "hybrid-semantic" if self.embedder is not None else "ranked-keyword"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _timestamp_label(cls, timestamp: datetime | None = None) -> str:
        target = timestamp or cls._now()
        return target.strftime("%Y-%m-%d %H:%M:%SZ")

    @classmethod
    def _date_label(cls, timestamp: datetime | None = None) -> str:
        target = timestamp or cls._now()
        return target.strftime("%Y-%m-%d")

    def session_diary_path(self, session_id: str) -> Path:
        return self.session_dir / f"{session_id}.md"

    def daily_log_path(self, date_label: str | None = None) -> Path:
        target = date_label or self._date_label()
        return self.daily_dir / f"{target}.md"

    def append_session_entry(self, session_id: str, content: str, *, timestamp: datetime | None = None) -> str:
        return self._append_entry(
            self.session_diary_path(session_id),
            content,
            timestamp=timestamp,
            kind="session",
            session_id=session_id,
        )

    def append_daily_entry(
        self,
        content: str,
        *,
        date_label: str | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        resolved_date = date_label or self._date_label(timestamp)
        return self._append_entry(
            self.daily_log_path(resolved_date),
            content,
            timestamp=timestamp,
            kind="daily",
            date_label=resolved_date,
        )

    def read_session_diary(self, session_id: str, *, max_chars: int = 2000) -> str:
        return self._read_tail(self.session_diary_path(session_id), max_chars=max_chars)

    def read_daily_log(self, *, date_label: str | None = None, max_chars: int = 2000) -> str:
        return self._read_tail(self.daily_log_path(date_label), max_chars=max_chars)

    def get_session_memory_bundle(
        self,
        session_id: str,
        *,
        date_label: str | None = None,
        max_chars: int = 2000,
    ) -> dict[str, object]:
        resolved_date = date_label or self._date_label()
        return {
            "session_id": session_id,
            "session_diary": {
                "path": str(self.session_diary_path(session_id)),
                "content": self.read_session_diary(session_id, max_chars=max_chars),
            },
            "daily_log": {
                "date": resolved_date,
                "path": str(self.daily_log_path(resolved_date)),
                "content": self.read_daily_log(date_label=resolved_date, max_chars=max_chars),
            },
        }

    def build_prompt_memory(
        self,
        session_id: str,
        *,
        date_label: str | None = None,
        max_chars: int = 2000,
    ) -> str:
        bundle = self.get_session_memory_bundle(session_id, date_label=date_label, max_chars=max_chars)
        session_diary = bundle["session_diary"]["content"]
        daily_log = bundle["daily_log"]["content"]

        lines: list[str] = []
        if session_diary:
            lines.extend(["## Session Diary", "", session_diary])
        if daily_log:
            if lines:
                lines.append("")
            lines.extend([f"## Daily Log ({bundle['daily_log']['date']})", "", daily_log])
        return "\n".join(lines).strip()

    def prepare_prompt_memory(
        self,
        session_id: str,
        *,
        date_label: str | None = None,
        continuity_query: str | None = None,
        max_chars: int = 2000,
    ) -> dict[str, Any]:
        resolved_date = date_label or self._date_label()
        rows = self._list_memory_rows(session_id, date_label=resolved_date, exclude_seen=True)

        session_rows = [row for row in rows if row["kind"] == "session"]
        daily_rows = [row for row in rows if row["kind"] == "daily"]

        session_text, session_ids = self._format_rows_for_prompt("Session Diary", session_rows, max_chars=max_chars)
        remaining_chars = max_chars - len(session_text) if session_text else max_chars
        daily_text, daily_ids = self._format_rows_for_prompt(
            f"Daily Log ({resolved_date})",
            daily_rows,
            max_chars=max(0, remaining_chars),
        )

        parts = [part for part in (session_text, daily_text) if part]
        continuity_ids: list[int] = []

        remaining_chars = max_chars - sum(len(part) for part in parts) - (2 * max(0, len(parts) - 1))
        continuity_query = (continuity_query or "").strip()
        if continuity_query and remaining_chars > 120:
            continuity_text, continuity_ids = self._build_continuity_memory_section(
                session_id,
                continuity_query,
                excluded_ids=set(session_ids + daily_ids),
                max_chars=remaining_chars,
            )
            if continuity_text:
                parts.append(continuity_text)
        return {
            "text": "\n\n".join(parts).strip(),
            "entry_ids": session_ids + daily_ids + continuity_ids,
        }

    def mark_entries_read(self, session_id: str, entry_ids: list[int], *, source: str) -> None:
        if not entry_ids:
            return

        timestamp = self._timestamp_label()
        for entry_id in entry_ids:
            self.index_db.execute(
                """INSERT INTO memory_read_state (session_id, entry_id, source, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(session_id, entry_id)
                   DO UPDATE SET last_seen_at = excluded.last_seen_at, source = excluded.source""",
                (session_id, entry_id, source, timestamp, timestamp),
            )
        self.index_db.commit()

    def search(
        self,
        session_id: str,
        query: str,
        *,
        limit: int = 5,
        include_daily: bool = True,
        exclude_seen: bool = False,
        mark_seen: bool = False,
    ) -> list[dict[str, Any]]:
        search_query = query.strip()
        if not search_query:
            raise ValueError("query is required")
        query_terms = self._tokenize_query(search_query) or [search_query.lower()]

        safe_limit = max(1, min(limit, 20))
        candidate_limit = max(safe_limit * 5, 10)
        semantic_candidates: list[dict[str, Any]] = []
        if self.fts_enabled:
            keyword_candidates = self._search_fts(
                session_id,
                search_query,
                query_terms=query_terms,
                limit=candidate_limit,
                include_daily=include_daily,
                exclude_seen=exclude_seen,
            )
        else:
            keyword_candidates = self._search_like(
                session_id,
                search_query,
                query_terms=query_terms,
                limit=candidate_limit,
                include_daily=include_daily,
                exclude_seen=exclude_seen,
            )

        if self.embedder is not None:
            semantic_candidates = self._search_semantic(
                session_id,
                search_query,
                query_terms=query_terms,
                limit=candidate_limit,
                include_daily=include_daily,
                exclude_seen=exclude_seen,
            )

        candidates = self._merge_search_candidates(keyword_candidates, semantic_candidates)

        results = self._rank_search_results(search_query, query_terms, candidates, limit=safe_limit)

        if mark_seen:
            self.mark_entries_read(session_id, [result["id"] for result in results], source="memory_search")
        return results

    def _list_memory_rows(self, session_id: str, *, date_label: str, exclude_seen: bool):
        params: list[Any] = [session_id, date_label]
        seen_clause = ""
        if exclude_seen:
            seen_clause = (
                " AND NOT EXISTS (SELECT 1 FROM memory_read_state rs "
                "WHERE rs.session_id = ? AND rs.entry_id = e.id)"
            )
            params.append(session_id)

        return self.index_db.execute(
            f"""SELECT e.id, e.session_id, e.kind, e.date_label, e.path, e.content, e.created_at
                 FROM memory_entries e
                 WHERE (e.session_id = ? OR (e.kind = 'daily' AND e.date_label = ?)){seen_clause}
                 ORDER BY e.created_at ASC""",
            params,
        ).fetchall()

    def _list_semantic_rows(self, session_id: str, *, include_daily: bool, exclude_seen: bool):
        scope_clause = "e.session_id = ?"
        params: list[Any] = [session_id]
        if include_daily:
            scope_clause = f"({scope_clause} OR e.kind = 'daily')"

        seen_clause = ""
        if exclude_seen:
            seen_clause = (
                " AND NOT EXISTS (SELECT 1 FROM memory_read_state rs "
                "WHERE rs.session_id = ? AND rs.entry_id = e.id)"
            )
            params.append(session_id)

        return self.index_db.execute(
            f"""SELECT e.id, e.session_id, e.kind, e.date_label, e.path, e.content, e.created_at,
                        me.vector_json, me.content_hash
                 FROM memory_entries e
                 LEFT JOIN memory_embeddings me ON me.entry_id = e.id
                 WHERE {scope_clause}{seen_clause}
                 ORDER BY e.created_at DESC""",
            params,
        ).fetchall()

    def _format_rows_for_prompt(self, title: str, rows, *, max_chars: int) -> tuple[str, list[int]]:
        if not rows or max_chars <= 0:
            return "", []

        selected_rows: list[tuple[Any, str]] = []
        used_chars = 0
        for row in reversed(rows):
            entry_text = f"## {row['created_at']}\n{row['content']}"
            additional = len(entry_text) + (2 if selected_rows else 0)
            if selected_rows and used_chars + additional > max_chars:
                break
            if not selected_rows and len(entry_text) > max_chars:
                entry_text = entry_text[-max_chars:]
                selected_rows.append((row, entry_text))
                break
            selected_rows.append((row, entry_text))
            used_chars += additional

        if not selected_rows:
            return "", []

        selected_rows.reverse()
        body = "\n\n".join(text for _, text in selected_rows)
        return f"## {title}\n\n{body}".strip(), [row["id"] for row, _ in selected_rows]

    def _build_continuity_memory_section(
        self,
        session_id: str,
        query: str,
        *,
        excluded_ids: set[int],
        max_chars: int,
    ) -> tuple[str, list[int]]:
        results = self.search(
            session_id,
            query,
            limit=4,
            include_daily=True,
            exclude_seen=False,
            mark_seen=False,
        )

        selected: list[dict[str, Any]] = []
        used_chars = 0
        for result in results:
            if result["id"] in excluded_ids:
                continue
            label = "Daily" if result["kind"] == "daily" else "Session"
            entry_text = f"## {label} Recall\n{result['snippet']}"
            additional = len(entry_text) + (2 if selected else 0)
            if used_chars + additional > max_chars:
                break
            selected.append(result)
            used_chars += additional

        if not selected:
            return "", []

        body = "\n\n".join(f"## {'Daily' if row['kind'] == 'daily' else 'Session'} Recall\n{row['snippet']}" for row in selected)
        return f"## Continuity Recall\n\n{body}".strip(), [row["id"] for row in selected]

    def _init_index(self) -> bool:
        self.index_db.execute(
            """CREATE TABLE IF NOT EXISTS memory_entries (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   session_id TEXT,
                   kind TEXT NOT NULL,
                   date_label TEXT,
                   path TEXT NOT NULL,
                   content TEXT NOT NULL,
                   created_at TEXT NOT NULL
               )"""
        )
        self.index_db.execute(
            """CREATE TABLE IF NOT EXISTS memory_read_state (
                   session_id TEXT NOT NULL,
                   entry_id INTEGER NOT NULL,
                   source TEXT NOT NULL,
                   first_seen_at TEXT NOT NULL,
                   last_seen_at TEXT NOT NULL,
                   PRIMARY KEY (session_id, entry_id)
               )"""
        )
        self.index_db.execute(
            """CREATE TABLE IF NOT EXISTS memory_embeddings (
                   entry_id INTEGER PRIMARY KEY,
                   model TEXT NOT NULL,
                   dimensions INTEGER NOT NULL,
                   vector_json TEXT NOT NULL,
                   content_hash TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )
        try:
            self.index_db.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS memory_entries_fts USING fts5(
                       entry_id UNINDEXED,
                       session_id,
                       kind,
                       date_label,
                       path,
                       content
                   )"""
            )
            self.index_db.commit()
            return True
        except sqlite3.OperationalError:
            self.index_db.commit()
            return False

    def _append_entry(
        self,
        path: Path,
        content: str,
        *,
        timestamp: datetime | None = None,
        kind: str,
        session_id: str | None = None,
        date_label: str | None = None,
    ) -> str:
        body = content.strip()
        if not body:
            raise ValueError("content is required")

        created_at = self._timestamp_label(timestamp)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = f"## {created_at}\n{body}\n"
        with path.open("a", encoding="utf-8") as handle:
            if path.exists() and path.stat().st_size > 0:
                handle.write("\n")
            handle.write(entry)

        self._index_entry(
            session_id=session_id,
            kind=kind,
            date_label=date_label,
            path=path,
            content=body,
            created_at=created_at,
        )
        return str(path)

    def _index_entry(
        self,
        *,
        session_id: str | None,
        kind: str,
        date_label: str | None,
        path: Path,
        content: str,
        created_at: str,
    ) -> None:
        cursor = self.index_db.execute(
            """INSERT INTO memory_entries (session_id, kind, date_label, path, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, kind, date_label, str(path), content, created_at),
        )
        entry_id = cursor.lastrowid
        if self.fts_enabled:
            self.index_db.execute(
                """INSERT INTO memory_entries_fts (entry_id, session_id, kind, date_label, path, content)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(entry_id), session_id or "", kind, date_label or "", str(path), content),
            )
        self._persist_entry_embedding(entry_id, content)
        self.index_db.commit()

    def _persist_entry_embedding(self, entry_id: int, content: str) -> None:
        if self.embedder is None:
            return

        try:
            vectors = self.embedder.embed_texts([content])
        except RuntimeError:
            return

        if not vectors:
            return

        vector = vectors[0]
        self.index_db.execute(
            """INSERT INTO memory_embeddings (entry_id, model, dimensions, vector_json, content_hash, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(entry_id) DO UPDATE SET
                   model = excluded.model,
                   dimensions = excluded.dimensions,
                   vector_json = excluded.vector_json,
                   content_hash = excluded.content_hash,
                   updated_at = excluded.updated_at""",
            (
                entry_id,
                self.embedder.model_name,
                len(vector),
                json.dumps(vector),
                self._content_hash(content),
                self._timestamp_label(),
            ),
        )

    def _search_fts(
        self,
        session_id: str,
        query: str,
        *,
        query_terms: list[str],
        limit: int,
        include_daily: bool,
        exclude_seen: bool,
    ) -> list[dict[str, Any]]:
        fts_query = self._build_fts_query(query, query_terms)
        scope_clause = "e.session_id = ?"
        params: list[Any] = [fts_query, session_id]
        if include_daily:
            scope_clause = f"({scope_clause} OR e.kind = 'daily')"
        seen_clause = ""
        if exclude_seen:
            seen_clause = (
                " AND NOT EXISTS (SELECT 1 FROM memory_read_state rs "
                "WHERE rs.session_id = ? AND rs.entry_id = e.id)"
            )
            params.append(session_id)

        rows = self.index_db.execute(
            f"""SELECT e.id, e.session_id, e.kind, e.date_label, e.path, e.content, e.created_at,
                        snippet(memory_entries_fts, 5, '[', ']', '...', 10) AS snippet,
                        bm25(memory_entries_fts) AS provider_score
                 FROM memory_entries_fts
                 JOIN memory_entries e ON e.id = CAST(memory_entries_fts.entry_id AS INTEGER)
                 WHERE memory_entries_fts MATCH ? AND {scope_clause}{seen_clause}
                 ORDER BY provider_score, e.created_at DESC
                 LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [self._build_search_candidate(row, snippet=row["snippet"], provider_score=row["provider_score"]) for row in rows]

    def _search_like(
        self,
        session_id: str,
        query: str,
        *,
        query_terms: list[str],
        limit: int,
        include_daily: bool,
        exclude_seen: bool,
    ) -> list[dict[str, Any]]:
        scope_clause = "session_id = ?"
        patterns = self._build_like_patterns(query, query_terms)
        match_clause = " OR ".join("content LIKE ? COLLATE NOCASE" for _ in patterns)
        params: list[Any] = [session_id, *patterns, limit]
        if include_daily:
            scope_clause = f"({scope_clause} OR kind = 'daily')"
        seen_clause = ""
        if exclude_seen:
            seen_clause = (
                " AND NOT EXISTS (SELECT 1 FROM memory_read_state rs "
                "WHERE rs.session_id = ? AND rs.entry_id = memory_entries.id)"
            )
            params = [session_id, session_id, *patterns, limit]

        rows = self.index_db.execute(
            f"""SELECT id, session_id, kind, date_label, path, content, created_at
                 FROM memory_entries
                 WHERE {scope_clause}{seen_clause} AND ({match_clause})
                 ORDER BY created_at DESC
                 LIMIT ?""",
            params,
        ).fetchall()
        return [self._build_search_candidate(row, snippet=self._build_excerpt(row["content"], query), provider_score=None) for row in rows]

    def _search_semantic(
        self,
        session_id: str,
        query: str,
        *,
        query_terms: list[str],
        limit: int,
        include_daily: bool,
        exclude_seen: bool,
    ) -> list[dict[str, Any]]:
        if self.embedder is None:
            return []

        rows = self._list_semantic_rows(session_id, include_daily=include_daily, exclude_seen=exclude_seen)
        if not rows:
            return []

        embeddings = self._ensure_row_embeddings(rows)
        if not embeddings:
            return []

        try:
            query_vector = self.embedder.embed_texts([query])[0]
        except RuntimeError:
            return []

        candidates: list[dict[str, Any]] = []
        for row in rows:
            vector = embeddings.get(row["id"])
            if not vector:
                continue
            similarity = self._cosine_similarity(query_vector, vector)
            if similarity <= 0:
                continue
            candidates.append(
                self._build_search_candidate(
                    row,
                    snippet=self._build_semantic_excerpt(row["content"], query_terms),
                    provider_score=None,
                    semantic_score=similarity,
                )
            )

        candidates.sort(key=lambda item: (item.get("semantic_score", 0.0), item["created_at"], item["id"]), reverse=True)
        return candidates[:limit]

    def _build_search_candidate(
        self,
        row,
        *,
        snippet: str,
        provider_score: float | None,
        semantic_score: float | None = None,
    ) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "kind": row["kind"],
            "date": row["date_label"],
            "path": row["path"],
            "created_at": row["created_at"],
            "snippet": snippet,
            "content": row["content"],
            "provider_score": provider_score,
            "semantic_score": semantic_score,
        }

    def _search_row_to_dict(
        self,
        row,
        *,
        snippet: str,
        score: float,
        matched_terms: list[str],
        semantic_score: float,
        sources: list[str],
    ) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "kind": row["kind"],
            "date": row["date"],
            "path": row["path"],
            "created_at": row["created_at"],
            "snippet": snippet,
            "score": score,
            "matched_terms": matched_terms,
            "semantic_score": round(semantic_score, 4),
            "sources": sources,
        }

    def _ensure_row_embeddings(self, rows) -> dict[int, list[float]]:
        if self.embedder is None:
            return {}

        embeddings: dict[int, list[float]] = {}
        stale_rows = []
        for row in rows:
            stored_vector = row["vector_json"]
            content_hash = self._content_hash(row["content"])
            if stored_vector and row["content_hash"] == content_hash:
                embeddings[row["id"]] = self._parse_vector_json(stored_vector)
            else:
                stale_rows.append(row)

        if not stale_rows:
            return embeddings

        try:
            vectors = self.embedder.embed_texts([row["content"] for row in stale_rows])
        except RuntimeError:
            return embeddings

        timestamp = self._timestamp_label()
        for row, vector in zip(stale_rows, vectors):
            embeddings[row["id"]] = vector
            self.index_db.execute(
                """INSERT INTO memory_embeddings (entry_id, model, dimensions, vector_json, content_hash, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(entry_id) DO UPDATE SET
                       model = excluded.model,
                       dimensions = excluded.dimensions,
                       vector_json = excluded.vector_json,
                       content_hash = excluded.content_hash,
                       updated_at = excluded.updated_at""",
                (
                    row["id"],
                    self.embedder.model_name,
                    len(vector),
                    json.dumps(vector),
                    self._content_hash(row["content"]),
                    timestamp,
                ),
            )
        self.index_db.commit()
        return embeddings

    @staticmethod
    def _parse_vector_json(value: str) -> list[float]:
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            return []
        return [float(component) for component in parsed]

    def _rank_search_results(
        self,
        query: str,
        query_terms: list[str],
        candidates: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized_query = query.lower()
        ranked: list[dict[str, Any]] = []

        for candidate in candidates:
            content_lower = candidate["content"].lower()
            matched_terms = [term for term in query_terms if term in content_lower]
            semantic_score = candidate.get("semantic_score") or 0.0
            if not matched_terms and normalized_query not in content_lower and semantic_score <= 0.0:
                continue

            phrase_match = normalized_query in content_lower
            all_terms_match = len(matched_terms) == len(query_terms)
            in_order = self._terms_in_order(content_lower, query_terms)
            has_keyword_match = phrase_match or bool(matched_terms)
            keyword_score = 0.0
            if phrase_match:
                keyword_score += 120.0
            if all_terms_match:
                keyword_score += 80.0
            keyword_score += (len(matched_terms) / max(len(query_terms), 1)) * 50.0
            if in_order:
                keyword_score += 15.0
            if has_keyword_match and candidate["kind"] == "session":
                keyword_score += 5.0

            score = keyword_score + (semantic_score * 100.0)
            sources: list[str] = []
            if keyword_score > 0:
                sources.append("keyword")
            if semantic_score > 0:
                sources.append("semantic")
            if len(sources) == 2:
                score += 15.0

            ranked.append(
                {
                    **self._search_row_to_dict(
                        candidate,
                        snippet=candidate["snippet"],
                        score=round(score, 2),
                        matched_terms=matched_terms,
                        semantic_score=semantic_score,
                        sources=sources,
                    ),
                    "_provider_score": candidate["provider_score"],
                }
            )

        ranked.sort(
            key=lambda item: (
                -item["score"],
                item["_provider_score"] if item["_provider_score"] is not None else float("inf"),
                item["created_at"],
                item["id"],
            ),
            reverse=False,
        )

        results: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for item in ranked:
            if item["id"] in seen_ids:
                continue
            seen_ids.add(item["id"])
            item.pop("_provider_score", None)
            results.append(item)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _merge_search_candidates(
        keyword_candidates: list[dict[str, Any]],
        semantic_candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[int, dict[str, Any]] = {}
        for candidate in keyword_candidates:
            merged[candidate["id"]] = dict(candidate)

        for candidate in semantic_candidates:
            existing = merged.get(candidate["id"])
            if existing is None:
                merged[candidate["id"]] = dict(candidate)
                continue

            existing["semantic_score"] = max(existing.get("semantic_score") or 0.0, candidate.get("semantic_score") or 0.0)
            if not existing.get("snippet"):
                existing["snippet"] = candidate.get("snippet", "")

        return list(merged.values())

    @staticmethod
    def _tokenize_query(query: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"[A-Za-z0-9]+", query.lower())))

    @staticmethod
    def _build_fts_query(query: str, query_terms: list[str]) -> str:
        parts = [query.strip().lower(), *query_terms]
        unique_parts = list(dict.fromkeys(part for part in parts if part))
        escaped_parts = [part.replace('"', '""') for part in unique_parts]
        return " OR ".join(f'"{part}"' for part in escaped_parts)

    @staticmethod
    def _build_like_patterns(query: str, query_terms: list[str]) -> list[str]:
        parts = [query.strip(), *query_terms]
        return [f"%{part}%" for part in dict.fromkeys(part for part in parts if part)]

    @staticmethod
    def _terms_in_order(content: str, query_terms: list[str]) -> bool:
        position = -1
        for term in query_terms:
            next_position = content.find(term, position + 1)
            if next_position == -1:
                return False
            position = next_position
        return True

    @staticmethod
    def _build_semantic_excerpt(content: str, query_terms: list[str], *, radius: int = 70) -> str:
        lowered = content.lower()
        positions = [lowered.find(term) for term in query_terms if lowered.find(term) != -1]
        if positions:
            start = max(0, min(positions) - radius)
            end = min(len(content), max(positions) + radius)
            excerpt = content[start:end].strip()
            if start > 0:
                excerpt = f"...{excerpt}"
            if end < len(content):
                excerpt = f"{excerpt}..."
            return excerpt
        return content[: radius * 2].strip()

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(left_value * right_value for left_value, right_value in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _build_excerpt(content: str, query: str, *, radius: int = 70) -> str:
        lowered = content.lower()
        needle = query.lower()
        index = lowered.find(needle)
        if index == -1:
            return content[: radius * 2].strip()
        start = max(0, index - radius)
        end = min(len(content), index + len(query) + radius)
        excerpt = content[start:end].strip()
        if start > 0:
            excerpt = f"...{excerpt}"
        if end < len(content):
            excerpt = f"{excerpt}..."
        return excerpt

    def _read_tail(self, path: Path, *, max_chars: int) -> str:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        if max_chars <= 0 or len(text) <= max_chars:
            return text.strip()
        return text[-max_chars:].strip()


def register_memory_routes(app) -> None:
    from api.api.session_validation import validate_session_route_scope

    @app.route("/api/sessions/<session_id>/memory", methods=["GET"])
    def get_session_memory(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="get_session_memory")
        if error_response:
            return error_response

        bundle = app.memory.get_session_memory_bundle(  # type: ignore[attr-defined]
            session_id,
            date_label=request.args.get("date"),
        )
        return jsonify(bundle)

    @app.route("/api/sessions/<session_id>/memory", methods=["POST"])
    def add_session_memory(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="add_session_memory")
        if error_response:
            return error_response

        data = request.get_json(silent=True) or {}
        content = data.get("content", "")
        also_daily = data.get("also_daily", True)
        date_label = data.get("date")

        try:
            app.memory.append_session_entry(session_id, content)  # type: ignore[attr-defined]
            if also_daily:
                app.memory.append_daily_entry(content, date_label=date_label)  # type: ignore[attr-defined]
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        bundle = app.memory.get_session_memory_bundle(session_id, date_label=date_label)  # type: ignore[attr-defined]
        return jsonify(bundle), 201

    @app.route("/api/sessions/<session_id>/memory/search", methods=["GET"])
    def search_session_memory(session_id: str):
        session_id, error_response = validate_session_route_scope(app, session_id, route_name="search_session_memory")
        if error_response:
            return error_response

        query = request.args.get("q", "")
        limit = request.args.get("limit", default=5, type=int)
        include_daily = request.args.get("include_daily", "1") in {"1", "true", "yes"}
        exclude_seen = request.args.get("exclude_seen", "0") in {"1", "true", "yes"}
        try:
            results = app.memory.search(  # type: ignore[attr-defined]
                session_id,
                query,
                limit=limit,
                include_daily=include_daily,
                exclude_seen=exclude_seen,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify({
            "session_id": session_id,
            "query": query,
            "strategy": app.memory.search_strategy,
            "results": results,
        })
