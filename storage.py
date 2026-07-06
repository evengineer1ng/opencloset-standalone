from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_AGENT_ID = "clo"
DEFAULT_PROVIDER_ID = "local-llamacpp"
DEFAULT_MODEL = "qwen3.6-27b"
OPENAI_PROVIDER_ID = "openai"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-5.5-2026-04-23-"


@dataclass
class AgentRecord:
    id: str
    display_name: str
    role_prompt: str
    default_model: str
    enabled: bool


@dataclass
class ProviderRecord:
    id: str
    kind: str
    base_url: str
    model_name: str
    timeout_sec: int
    enabled: bool
    api_key: str | None
    last_health_status: str
    last_health_at: str | None


@dataclass
class SessionRecord:
    id: str
    agent_id: str
    title: str
    status: str
    token_estimate: int
    created_at: str
    updated_at: str
    parent_session_id: str | None


@dataclass
class MessageRecord:
    id: str
    session_id: str
    role: str
    content: str
    status: str
    created_at: str


@dataclass
class RunRecord:
    id: str
    session_id: str
    trigger_message_id: str
    provider_id: str
    model: str
    status: str
    started_at: str
    completed_at: str | None
    error_code: str | None
    error_text: str | None
    token_input_est: int
    token_output_est: int | None


@dataclass
class CaptureRecord:
    id: str
    created_at: str
    source_mode: str
    raw_content: str
    cleaned_summary: str
    project: str
    tags: list[str]
    status: str
    destination_agent: str
    packet_json: dict[str, Any]


@dataclass
class TransientWindowRecord:
    id: str
    title: str
    source_type: str  # "generated" or "native"
    native_type: str | None
    html: str | None
    css: str | None
    js: str | None
    pinned: bool
    minimized: bool
    stale: bool
    saved: bool
    network: bool
    tool_bridge: bool
    storage_cap: bool
    media: bool
    conversation_id: str
    mutable: bool
    version: int
    created_at: str
    updated_at: str
    summary: str


@dataclass
class TransientHistoryEntry:
    id: str
    window_id: str
    version: int
    action: str  # "created", "mutated", "pinned", "unpinned", "saved", "closed"
    summary: str
    created_at: str


class OpenClosetStore:
    def __init__(self, db_path: Path, transcript_dir: Path | None = None) -> None:
        self.db_path = db_path
        self.transcript_dir = transcript_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.transcript_dir:
            self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    role_prompt TEXT NOT NULL,
                    default_model TEXT NOT NULL,
                    enabled INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS providers (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    timeout_sec INTEGER NOT NULL,
                    enabled INTEGER NOT NULL,
                    api_key TEXT,
                    last_health_status TEXT NOT NULL,
                    last_health_at TEXT
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_estimate INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    parent_session_id TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    trigger_message_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_code TEXT,
                    error_text TEXT,
                    token_input_est INTEGER NOT NULL,
                    token_output_est INTEGER
                );

                CREATE TABLE IF NOT EXISTS captures (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source_mode TEXT NOT NULL,
                    raw_content TEXT NOT NULL,
                    cleaned_summary TEXT NOT NULL,
                    project TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    destination_agent TEXT NOT NULL,
                    packet_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transient_windows (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    native_type TEXT,
                    html TEXT,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    pinned INTEGER NOT NULL,
                    session_id TEXT,
                    message_id TEXT
                );

                CREATE TABLE IF NOT EXISTS transient_history (
                    id TEXT PRIMARY KEY,
                    window_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    html TEXT,
                    diff_json TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO agents (id, display_name, role_prompt, default_model, enabled)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    DEFAULT_AGENT_ID,
                    "Clo",
                    "You are Clo, the main desktop harness agent for OpenCloset.",
                    DEFAULT_MODEL,
                    1,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO providers (
                    id, kind, base_url, model_name, timeout_sec, enabled, api_key, last_health_status, last_health_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEFAULT_PROVIDER_ID,
                    "openai-compatible",
                    "http://127.0.0.1:8080/v1",
                    DEFAULT_MODEL,
                    45,
                    1,
                    None,
                    "unknown",
                    None,
                ),
            )
            # Seed default OpenAI provider
            connection.execute(
                """
                INSERT OR IGNORE INTO providers (
                    id, kind, base_url, model_name, timeout_sec, enabled, api_key, last_health_status, last_health_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    OPENAI_PROVIDER_ID,
                    "openai",
                    OPENAI_BASE_URL,
                    OPENAI_DEFAULT_MODEL,
                    45,
                    0,  # disabled by default until API key is configured
                    None,
                    "unknown",
                    None,
                ),
            )

    def list_agents(self) -> list[AgentRecord]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM agents ORDER BY id").fetchall()
        return [self._row_to_agent(row) for row in rows]

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
        return self._row_to_agent(row) if row else None

    def list_providers(self) -> list[ProviderRecord]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM providers ORDER BY id").fetchall()
        return [self._row_to_provider(row) for row in rows]

    def get_provider(self, provider_id: str = DEFAULT_PROVIDER_ID) -> ProviderRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM providers WHERE id = ?",
                (provider_id,),
            ).fetchone()
        return self._row_to_provider(row) if row else None

    def create_provider(
        self,
        provider_id: str,
        kind: str,
        base_url: str,
        model_name: str,
        timeout_sec: int,
        enabled: bool,
        api_key: str | None = None,
    ) -> ProviderRecord:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO providers (
                    id, kind, base_url, model_name, timeout_sec, enabled, last_health_status, last_health_at, api_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    kind,
                    base_url,
                    model_name,
                    timeout_sec,
                    1 if enabled else 0,
                    "unknown",
                    None,
                    api_key,
                ),
            )
        return self.get_provider(provider_id) or ProviderRecord(
            id=provider_id,
            kind=kind,
            base_url=base_url,
            model_name=model_name,
            timeout_sec=timeout_sec,
            enabled=enabled,
            last_health_status="unknown",
            last_health_at=None,
            api_key=api_key,
        )

    def update_provider(
        self,
        provider_id: str,
        *,
        kind: str | None = None,
        base_url: str | None = None,
        model_name: str | None = None,
        timeout_sec: int | None = None,
        enabled: bool | None = None,
        api_key: str | None = None,
    ) -> ProviderRecord | None:
        current = self.get_provider(provider_id)
        if current is None:
            return None
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE providers
                SET kind = ?, base_url = ?, model_name = ?, timeout_sec = ?, enabled = ?, api_key = ?
                WHERE id = ?
                """,
                (
                    kind if kind is not None else current.kind,
                    base_url if base_url is not None else current.base_url,
                    model_name if model_name is not None else current.model_name,
                    timeout_sec if timeout_sec is not None else current.timeout_sec,
                    1 if (enabled if enabled is not None else current.enabled) else 0,
                    api_key if api_key is not None else current.api_key,
                    provider_id,
                ),
            )
        return self.get_provider(provider_id)

    def update_provider_health(self, provider_id: str, status: str, checked_at: str) -> ProviderRecord | None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE providers
                SET last_health_status = ?, last_health_at = ?
                WHERE id = ?
                """,
                (status, checked_at, provider_id),
            )
        return self.get_provider(provider_id)

    def create_session(
        self,
        session_id: str,
        agent_id: str,
        title: str,
        status: str,
        token_estimate: int,
        created_at: str,
        updated_at: str,
        parent_session_id: str | None = None,
    ) -> SessionRecord:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    id, agent_id, title, status, token_estimate, created_at, updated_at, parent_session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    agent_id,
                    title,
                    status,
                    token_estimate,
                    created_at,
                    updated_at,
                    parent_session_id,
                ),
            )
        session = self.get_session(session_id)
        if session is None:
            raise RuntimeError("Session insert failed")
        self._append_transcript_event(
            session_id,
            {
                "type": "session_created",
                "session_id": session_id,
                "agent_id": agent_id,
                "created_at": created_at,
                "title": title,
                "parent_session_id": parent_session_id,
            },
        )
        return session

    def list_sessions(self) -> list[SessionRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        token_estimate: int | None = None,
        updated_at: str,
    ) -> SessionRecord | None:
        session = self.get_session(session_id)
        if session is None:
            return None
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET title = ?, status = ?, token_estimate = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title if title is not None else session.title,
                    status if status is not None else session.status,
                    token_estimate if token_estimate is not None else session.token_estimate,
                    updated_at,
                    session_id,
                ),
            )
        return self.get_session(session_id)

    def create_message(
        self,
        session_id: str,
        role: str,
        content: str,
        status: str,
        created_at: str,
        message_id: str | None = None,
    ) -> MessageRecord:
        message_id = message_id or uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (id, session_id, role, content, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, status, created_at),
            )
        message = self.get_message(message_id)
        if message is None:
            raise RuntimeError("Message insert failed")
        self._append_transcript_event(
            session_id,
            {
                "type": "message",
                "id": message.id,
                "role": role,
                "status": status,
                "created_at": created_at,
                "content": content,
            },
        )
        return message

    def get_message(self, message_id: str) -> MessageRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        return self._row_to_message(row) if row else None

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def create_run(
        self,
        run_id: str,
        session_id: str,
        trigger_message_id: str,
        provider_id: str,
        model: str,
        status: str,
        started_at: str,
        token_input_est: int,
    ) -> RunRecord:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    id, session_id, trigger_message_id, provider_id, model, status, started_at,
                    completed_at, error_code, error_text, token_input_est, token_output_est
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL)
                """,
                (
                    run_id,
                    session_id,
                    trigger_message_id,
                    provider_id,
                    model,
                    status,
                    started_at,
                    token_input_est,
                ),
            )
        run = self.get_run(run_id)
        if run is None:
            raise RuntimeError("Run insert failed")
        return run

    def complete_run(
        self,
        run_id: str,
        *,
        status: str,
        completed_at: str,
        error_code: str | None = None,
        error_text: str | None = None,
        token_output_est: int | None = None,
    ) -> RunRecord | None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, completed_at = ?, error_code = ?, error_text = ?, token_output_est = ?
                WHERE id = ?
                """,
                (status, completed_at, error_code, error_text, token_output_est, run_id),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, session_id: str | None = None, limit: int = 20) -> list[RunRecord]:
        query = "SELECT * FROM runs"
        params: tuple[Any, ...] = ()
        if session_id:
            query += " WHERE session_id = ?"
            params = (session_id,)
        query += " ORDER BY started_at DESC, id DESC LIMIT ?"
        params = (*params, limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_run(row) for row in rows]

    def create_capture(self, record: CaptureRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO captures (
                    id, created_at, source_mode, raw_content, cleaned_summary,
                    project, tags_json, status, destination_agent, packet_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.created_at,
                    record.source_mode,
                    record.raw_content,
                    record.cleaned_summary,
                    record.project,
                    json.dumps(record.tags),
                    record.status,
                    record.destination_agent,
                    json.dumps(record.packet_json),
                ),
            )

    def list_captures(self) -> list[CaptureRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM captures ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_capture(row) for row in rows]

    def _append_transcript_event(self, session_id: str, payload: dict[str, Any]) -> None:
        if not self.transcript_dir:
            return
        transcript_path = self.transcript_dir / f"{session_id}.jsonl"
        with transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def _row_to_agent(self, row: sqlite3.Row) -> AgentRecord:
        return AgentRecord(
            id=row["id"],
            display_name=row["display_name"],
            role_prompt=row["role_prompt"],
            default_model=row["default_model"],
            enabled=bool(row["enabled"]),
        )

    def _row_to_provider(self, row: sqlite3.Row) -> ProviderRecord:
        return ProviderRecord(
            id=row["id"],
            kind=row["kind"],
            base_url=row["base_url"],
            model_name=row["model_name"],
            timeout_sec=int(row["timeout_sec"]),
            enabled=bool(row["enabled"]),
            last_health_status=row["last_health_status"],
            last_health_at=row["last_health_at"],
            api_key=row.get("api_key"),
        )

    def _row_to_session(self, row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            agent_id=row["agent_id"],
            title=row["title"],
            status=row["status"],
            token_estimate=int(row["token_estimate"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            parent_session_id=row["parent_session_id"],
        )

    def _row_to_message(self, row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            status=row["status"],
            created_at=row["created_at"],
        )

    def _row_to_run(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            session_id=row["session_id"],
            trigger_message_id=row["trigger_message_id"],
            provider_id=row["provider_id"],
            model=row["model"],
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error_code=row["error_code"],
            error_text=row["error_text"],
            token_input_est=int(row["token_input_est"]),
            token_output_est=int(row["token_output_est"]) if row["token_output_est"] is not None else None,
        )

    def _row_to_capture(self, row: sqlite3.Row) -> CaptureRecord:
        return CaptureRecord(
            id=row["id"],
            created_at=row["created_at"],
            source_mode=row["source_mode"],
            raw_content=row["raw_content"],
            cleaned_summary=row["cleaned_summary"],
            project=row["project"],
            tags=json.loads(row["tags_json"]),
            status=row["status"],
            destination_agent=row["destination_agent"],
            packet_json=json.loads(row["packet_json"]),
        )

    def _row_to_transient_window(self, row: sqlite3.Row) -> TransientWindowRecord:
        return TransientWindowRecord(
            id=row["id"],
            title=row["title"],
            source_type=row["source_type"],
            native_type=row["native_type"],
            html=row["html"],
            status=row["status"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            pinned=bool(row["pinned"]),
            session_id=row["session_id"],
            message_id=row["message_id"],
        )

    def create_transient_window(self, record: TransientWindowRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO transient_windows (
                    id, title, source_type, native_type, html,
                    status, version, created_at, updated_at, pinned,
                    session_id, message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.title,
                    record.source_type,
                    record.native_type,
                    record.html,
                    record.status,
                    record.version,
                    record.created_at,
                    record.updated_at,
                    int(record.pinned),
                    record.session_id,
                    record.message_id,
                ),
            )

    def get_transient_window(self, window_id: str) -> TransientWindowRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM transient_windows WHERE id = ?", (window_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_transient_window(row)

    def update_transient_window(
        self, window_id: str, html: str | None = None, title: str | None = None, pinned: bool | None = None
    ) -> None:
        with self.connect() as connection:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if html is not None:
                connection.execute(
                    "UPDATE transient_windows SET html = ?, version = version + 1, updated_at = ? WHERE id = ?",
                    (html, now, window_id),
                )
            if title is not None:
                connection.execute(
                    "UPDATE transient_windows SET title = ?, updated_at = ? WHERE id = ?",
                    (title, now, window_id),
                )
            if pinned is not None:
                connection.execute(
                    "UPDATE transient_windows SET pinned = ?, updated_at = ? WHERE id = ?",
                    (int(pinned), now, window_id),
                )

    def close_transient_window(self, window_id: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                "UPDATE transient_windows SET status = 'closed', updated_at = ? WHERE id = ?",
                (now, window_id),
            )

    def list_transient_windows(
        self, session_id: str | None = None, status: str | None = None
    ) -> list[TransientWindowRecord]:
        with self.connect() as connection:
            if session_id and status:
                rows = connection.execute(
                    "SELECT * FROM transient_windows WHERE session_id = ? AND status = ? ORDER BY created_at DESC",
                    (session_id, status),
                ).fetchall()
            elif session_id:
                rows = connection.execute(
                    "SELECT * FROM transient_windows WHERE session_id = ? ORDER BY created_at DESC",
                    (session_id,),
                ).fetchall()
            elif status:
                rows = connection.execute(
                    "SELECT * FROM transient_windows WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM transient_windows ORDER BY created_at DESC"
                ).fetchall()
        return [self._row_to_transient_window(row) for row in rows]

    def delete_transient_window(self, window_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM transient_windows WHERE id = ?", (window_id,)
            )

    def create_transient_history(self, window_id: str, diff_json: dict[str, Any]) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO transient_history (
                    id, window_id, version, diff_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    window_id,
                    0,  # will be updated by caller
                    json.dumps(diff_json),
                    now,
                ),
            )

    def get_transient_history(
        self, window_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transient_history WHERE window_id = ? ORDER BY created_at DESC LIMIT ?",
                (window_id, limit),
            ).fetchall()
        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "window_id": row["window_id"],
                "version": row["version"],
                "diff_json": json.loads(row["diff_json"]),
                "created_at": row["created_at"],
            })
        return results

    def cleanup_closed_windows(self, older_than_hours: int = 24) -> int:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=older_than_hours)
        with self.connect() as connection:
            result = connection.execute(
                "DELETE FROM transient_windows WHERE status = 'closed' AND updated_at < ?",
                (cutoff.isoformat(),),
            )
        return result.rowcount

