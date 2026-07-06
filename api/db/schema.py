# OpenCloset DB Schema — SQLite
#
# Design principles (from Claude Code research):
# - Separate records per lifecycle (sessions, runs, messages, tool_invocations, captures)
# - Early persistence of user turns before model execution (crash-safe)
# - Fire-and-forget transcript writes on assistant msgs; blocking on user/system
# - Synthetic tool-result records on cancellation
# - Order-preserving message position within each run
# - Structured run lifecycle states

import json
import sqlite3
import os
import threading
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Sessions: one per conversation. Rollover creates a new session linked to the old.
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,                  -- UUID
    label           TEXT,                              -- human-readable name
    model           TEXT NOT NULL,                     -- provider model id
    provider        TEXT NOT NULL DEFAULT 'auto',      -- provider backend
    status          TEXT NOT NULL DEFAULT 'active',    -- active | paused | rolled-over | deleted
    context_window  INTEGER NOT NULL DEFAULT 65536,    -- model context limit in tokens
    token_count     INTEGER NOT NULL DEFAULT 0,        -- running token estimate
    max_tokens      INTEGER,                           -- per-session token budget (optional cap)
    task_budget_remaining INTEGER,                     -- turns/budget carried across rollover
    rolled_over_to  TEXT,                              -- FK to successor session id
    tool_policy     TEXT,                              -- JSON: enabled tools + destructive overrides
    delegation_policy TEXT,                            -- JSON: substrate routing + delegation defaults
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (rolled_over_to) REFERENCES sessions(id)
);

-- Runs: one per agent turn within a session.
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,                  -- UUID
    session_id      TEXT NOT NULL,                     -- FK to sessions
    status          TEXT NOT NULL DEFAULT 'queued',    -- queued | running | succeeded | failed | blocked | interrupted | rolled-over
    turn_number     INTEGER NOT NULL DEFAULT 1,        -- incrementing turn index within session
    max_turns       INTEGER,                           -- safety cap (optional)
    error           TEXT,                              -- failure reason
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at    TEXT,                              -- NULL until run ends
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Providers: durable backend configuration for local and remote model substrates.
CREATE TABLE IF NOT EXISTS providers (
    id                TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,
    base_url          TEXT NOT NULL DEFAULT '',
    model_name        TEXT NOT NULL DEFAULT '',
    timeout_sec       INTEGER NOT NULL DEFAULT 1800,
    enabled           INTEGER NOT NULL DEFAULT 1,
    capabilities      TEXT NOT NULL DEFAULT '{}',
    api_key           TEXT,
    last_health_status TEXT NOT NULL DEFAULT 'unknown',
    last_health_at    TEXT
);

-- Messages: the transcript. Separate from tool invocations.
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,                  -- UUID
    session_id      TEXT NOT NULL,                     -- FK to sessions
    run_id          TEXT NOT NULL,                     -- FK to runs
    role            TEXT NOT NULL,                     -- user | assistant | system | tool
    content         TEXT NOT NULL DEFAULT '',          -- message body (JSON for structured)
    position        INTEGER NOT NULL,                  -- order within session transcript
    token_estimate  INTEGER NOT NULL DEFAULT 0,        -- rough token count for this message
    persistent      INTEGER NOT NULL DEFAULT 0,        -- 1 = durably written, 0 = queued (fire-and-forget)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

-- Run input envelopes: non-transcript modality and capture context bound to a run.
CREATE TABLE IF NOT EXISTS run_input_envelopes (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    run_id          TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL DEFAULT 'user',
    attachments     TEXT NOT NULL DEFAULT '[]',
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS session_attachments (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    workspace_id     TEXT,
    build_project_id TEXT,
    capture_id       TEXT,
    attachment_type  TEXT NOT NULL,
    file_name        TEXT NOT NULL,
    mime_type        TEXT NOT NULL DEFAULT '',
    size_bytes       INTEGER NOT NULL DEFAULT 0,
    storage_path     TEXT NOT NULL,
    metadata         TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
    FOREIGN KEY (build_project_id) REFERENCES build_projects(id),
    FOREIGN KEY (capture_id) REFERENCES captures(id)
);

-- Delegation tasks: bounded read-only worker requests executed outside the main run loop.
CREATE TABLE IF NOT EXISTS delegation_tasks (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,
    workspace_id      TEXT,
    task_type         TEXT NOT NULL,
    substrate_id      TEXT NOT NULL DEFAULT 'local_readonly',
    authority_mode    TEXT NOT NULL DEFAULT 'read_only',
    title             TEXT NOT NULL DEFAULT '',
    instruction       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'queued',
    requested_provider TEXT,
    requested_model   TEXT,
    budget            TEXT NOT NULL DEFAULT '{}',
    provider_route    TEXT,
    worker_name       TEXT,
    result_text       TEXT,
    result_summary    TEXT,
    result_payload    TEXT,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    duration_ms       INTEGER,
    error             TEXT,
    metadata          TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at        TEXT,
    completed_at      TEXT,
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

-- Tool invocations: separate from chat messages.
CREATE TABLE IF NOT EXISTS tool_invocations (
    id              TEXT PRIMARY KEY,                  -- UUID
    session_id      TEXT NOT NULL,                     -- FK to sessions
    run_id          TEXT NOT NULL,                     -- FK to runs
    tool_name       TEXT NOT NULL,                     -- tool identifier
    input           TEXT NOT NULL DEFAULT '',          -- JSON input arguments
    output          TEXT,                              -- JSON result (NULL if not completed)
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | running | completed | failed | interrupted
    error           TEXT,                              -- failure reason
    started_at      TEXT,                              -- NULL until execution begins
    completed_at    TEXT,                              -- NULL until execution ends
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

-- Captures: bridge events from PhoneCloset and external sources.
CREATE TABLE IF NOT EXISTS captures (
    id              TEXT PRIMARY KEY,                  -- UUID
    workspace_id     TEXT,                              -- owning workspace for intake/review
    build_project_id TEXT,                              -- optional project scope
    session_id      TEXT,                              -- NULL = unassigned; set when routed
    run_id          TEXT,                              -- set when injected into a run
    source          TEXT NOT NULL,                     -- phonecloset | webhook | cli | manual
    event_type      TEXT NOT NULL,                     -- text | image | audio | location | app_event
    content         TEXT NOT NULL DEFAULT '',          -- JSON packet body
    media_url       TEXT,                              -- optional media attachment path/URL
    metadata        TEXT,                              -- JSON: sender, timestamp, context hints
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | routed | processed | failed
    received_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    processed_at    TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
    FOREIGN KEY (build_project_id) REFERENCES build_projects(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

-- Workspaces: grouping container for related sessions/plans/projects.
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    kind        TEXT NOT NULL DEFAULT 'general',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Build projects: projects within a workspace.
CREATE TABLE IF NOT EXISTS build_projects (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT,
    status       TEXT NOT NULL DEFAULT 'planned',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS idx_build_projects_workspace ON build_projects(workspace_id);

-- Workspace evidence: durable notes, links, and proof points attached to a workspace.
CREATE TABLE IF NOT EXISTS workspace_evidence (
    id               TEXT PRIMARY KEY,
    workspace_id     TEXT NOT NULL,
    session_id       TEXT,
    build_project_id TEXT,
    evidence_type    TEXT NOT NULL,
    title            TEXT NOT NULL,
    summary          TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    source_kind      TEXT NOT NULL DEFAULT 'note',
    status           TEXT NOT NULL DEFAULT 'active',
    tags             TEXT,
    metadata         TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (build_project_id) REFERENCES build_projects(id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_evidence_workspace ON workspace_evidence(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workspace_evidence_type ON workspace_evidence(workspace_id, evidence_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_attachments_session ON session_attachments(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS project_deliveries (
    id               TEXT PRIMARY KEY,
    workspace_id     TEXT NOT NULL,
    build_project_id TEXT NOT NULL,
    session_id       TEXT,
    capture_id       TEXT,
    target_device_id TEXT,
    artifact_kind    TEXT NOT NULL DEFAULT 'binary',
    file_name        TEXT NOT NULL,
    mime_type        TEXT NOT NULL DEFAULT '',
    size_bytes       INTEGER NOT NULL DEFAULT 0,
    storage_path     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'ready',
    metadata         TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    downloaded_at    TEXT,
    installed_at     TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
    FOREIGN KEY (build_project_id) REFERENCES build_projects(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (capture_id) REFERENCES captures(id)
);

CREATE INDEX IF NOT EXISTS idx_project_deliveries_project ON project_deliveries(build_project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_deliveries_device ON project_deliveries(workspace_id, target_device_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS workspace_pastimes (
    id               TEXT PRIMARY KEY,
    workspace_id     TEXT NOT NULL,
    key              TEXT NOT NULL,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    pastime_type     TEXT NOT NULL DEFAULT 'operational',
    source_kind      TEXT NOT NULL DEFAULT 'worker',
    candidate_type   TEXT,
    status           TEXT NOT NULL DEFAULT 'enabled',
    priority         INTEGER NOT NULL DEFAULT 50,
    cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    compute_cost     INTEGER NOT NULL DEFAULT 1,
    metadata         TEXT,
    config           TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_selected_at TEXT,
    last_completed_at TEXT,
    UNIQUE(workspace_id, key),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_pastimes_workspace ON workspace_pastimes(workspace_id, status, priority DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS workspace_attention_profiles (
    id                     TEXT PRIMARY KEY,
    workspace_id           TEXT NOT NULL UNIQUE,
    baseline_priority      INTEGER NOT NULL DEFAULT 50,
    current_attention_level INTEGER NOT NULL DEFAULT 50,
    mode                   TEXT NOT NULL DEFAULT 'warm',
    max_idle_budget        INTEGER NOT NULL DEFAULT 60,
    allowed_pastime_types  TEXT,
    notification_threshold TEXT NOT NULL DEFAULT 'significant',
    freshness_target       TEXT NOT NULL DEFAULT 'weekly',
    review_at              TEXT,
    expires_at             TEXT,
    user_rationale         TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_attention_profiles_workspace
    ON workspace_attention_profiles(workspace_id, mode, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_channels (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    session_id          TEXT NOT NULL,
    workspace_id        TEXT,
    domain              TEXT NOT NULL DEFAULT 'general',
    mode                TEXT NOT NULL DEFAULT 'ambient',
    status              TEXT NOT NULL DEFAULT 'active',
    active_objective    TEXT NOT NULL DEFAULT '',
    tool_permissions    TEXT NOT NULL DEFAULT '{}',
    channel_metadata    TEXT NOT NULL DEFAULT '{}',
    working_memory      TEXT NOT NULL DEFAULT '{}',
    current_plan        TEXT NOT NULL DEFAULT '{}',
    dashboard_state     TEXT NOT NULL DEFAULT '{}',
    last_agent_decision TEXT,
    open_threads        TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    stopped_at          TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_channels_status
    ON agent_channels(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_channel_events (
    id               TEXT PRIMARY KEY,
    channel_id       TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    source           TEXT NOT NULL,
    payload          TEXT NOT NULL DEFAULT '{}',
    text             TEXT NOT NULL DEFAULT '',
    priority         INTEGER NOT NULL DEFAULT 0,
    triage_action    TEXT NOT NULL DEFAULT 'record_only',
    requires_llm     INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'accepted',
    run_id           TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    processed_at     TEXT,
    FOREIGN KEY (channel_id) REFERENCES agent_channels(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_channel_events_channel
    ON agent_channel_events(channel_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_channel_outputs (
    id               TEXT PRIMARY KEY,
    channel_id       TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    run_id           TEXT,
    output_type      TEXT NOT NULL,
    payload          TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (channel_id) REFERENCES agent_channels(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_channel_outputs_channel
    ON agent_channel_outputs(channel_id, created_at DESC);

-- Scheduler jobs: cron-like and one-shot scheduled work items.
CREATE TABLE IF NOT EXISTS scheduler_jobs (
    id               TEXT PRIMARY KEY,
    job_type         TEXT NOT NULL,
    workspace_id     TEXT,
    session_id       TEXT,
    source           TEXT NOT NULL DEFAULT 'cron',
    schedule         TEXT,
    last_run_at      TEXT,
    next_run_at      TEXT,
    cooldown_seconds INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'scheduled',
    metadata         TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_workspace
    ON scheduler_jobs(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_next_run
    ON scheduler_jobs(next_run_at, status);

CREATE TABLE IF NOT EXISTS clo_queue_settings (
    id              TEXT PRIMARY KEY,
    paused          INTEGER NOT NULL DEFAULT 0,
    pause_on_error  INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS clo_queue_items (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    message_content  TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued',
    position         INTEGER,
    run_id           TEXT,
    error            TEXT,
    result_summary   TEXT,
    stop_after_error INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at       TEXT,
    finished_at      TEXT,
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_clo_queue_items_status_position
    ON clo_queue_items(status, position, created_at);
CREATE INDEX IF NOT EXISTS idx_clo_queue_items_session
    ON clo_queue_items(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS smart_usher_state (
    state_key       TEXT PRIMARY KEY,
    state_json      TEXT NOT NULL DEFAULT '{}',
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS smart_usher_events (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    profile_id      TEXT NOT NULL DEFAULT 'default',
    device_id       TEXT NOT NULL DEFAULT '',
    occurred_at     TEXT NOT NULL,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_smart_usher_events_device
    ON smart_usher_events(device_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_smart_usher_events_profile
    ON smart_usher_events(profile_id, occurred_at DESC);

-- Transient windows: ephemeral inline micro-apps generated by the model.
CREATE TABLE IF NOT EXISTS transient_windows (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    source_type     TEXT NOT NULL DEFAULT 'generated',  -- generated | native
    native_type     TEXT,
    html            TEXT,                               -- full self-contained HTML bundle
    payload         TEXT,                               -- JSON payload for native deterministic windows
    capabilities    TEXT NOT NULL DEFAULT '{}',         -- JSON: {network,toolBridge,storage,media}
    state_flags     TEXT NOT NULL DEFAULT '{}',         -- JSON: {pinned,minimized,stale,saved}
    mutable         INTEGER NOT NULL DEFAULT 1,
    version         INTEGER NOT NULL DEFAULT 1,
    summary         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Version snapshots: keep last N bundles per window for mutation history.
CREATE TABLE IF NOT EXISTS transient_window_versions (
    id              TEXT PRIMARY KEY,
    window_id       TEXT NOT NULL,
    version         INTEGER NOT NULL,
    html            TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (window_id) REFERENCES transient_windows(id)
);

-- History ledger: lightweight entry written when a window is closed/pruned.
CREATE TABLE IF NOT EXISTS transient_window_history (
    id                  TEXT PRIMARY KEY,
    window_id           TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    title               TEXT NOT NULL,
    summary             TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    closed_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    saved_artifact_id   TEXT
);

CREATE INDEX IF NOT EXISTS idx_transient_windows_session
    ON transient_windows(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transient_window_versions_window
    ON transient_window_versions(window_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_transient_window_history_session
    ON transient_window_history(session_id, closed_at DESC);

-- Indexes for hot paths
CREATE INDEX IF NOT EXISTS idx_messages_session_position ON messages(session_id, position);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_run ON tool_invocations(run_id);
CREATE INDEX IF NOT EXISTS idx_captures_status ON captures(status);
CREATE INDEX IF NOT EXISTS idx_captures_session ON captures(session_id);
CREATE INDEX IF NOT EXISTS idx_captures_workspace ON captures(workspace_id, received_at DESC);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ThreadLocalDB:
    """Thread-safe SQLite wrapper: each OS thread gets its own connection.

    All callers use the same interface as a raw sqlite3.Connection (execute /
    commit), but concurrent Flask request threads can no longer collide on a
    single shared connection, eliminating the intermittent
    ``sqlite3.InterfaceError: bad parameter or other API misuse`` errors.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self._db_path,
                detect_types=sqlite3.PARSE_COLNAMES,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        return self._conn().execute(sql, parameters)

    def executescript(self, script: str) -> sqlite3.Cursor:
        return self._conn().executescript(script)

    def commit(self) -> None:
        self._conn().commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        try:
            conn.close()
        finally:
            self._local.conn = None


def init_db(db_path: str | None = None) -> ThreadLocalDB:
    """Initialize the SQLite database with the schema. Returns a thread-local connection pool."""
    if db_path is None:
        db_path = os.path.join(os.path.dirname(__file__), "..", "..", "opencloset.db")
        db_path = os.path.abspath(db_path)

    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    # Use a temporary single connection for one-time schema setup and migrations.
    # The returned ThreadLocalDB creates fresh per-thread connections to the same
    # file, so all threads see the committed schema immediately via WAL mode.
    conn = sqlite3.connect(
        db_path,
        detect_types=sqlite3.PARSE_COLNAMES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _detect_incompatible_legacy_schema(conn, db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    # Phase 0D migration: add workspace columns to existing tables
    _migrations = [
        "ALTER TABLE sessions ADD COLUMN workspace_id TEXT",
        "ALTER TABLE sessions ADD COLUMN build_project_id TEXT",
        "ALTER TABLE sessions ADD COLUMN tool_policy TEXT",
        "ALTER TABLE sessions ADD COLUMN delegation_policy TEXT",
        "ALTER TABLE plans ADD COLUMN workspace_id TEXT",
        "ALTER TABLE plans ADD COLUMN build_project_id TEXT",
        "ALTER TABLE captures ADD COLUMN workspace_id TEXT",
        "ALTER TABLE captures ADD COLUMN build_project_id TEXT",
        "ALTER TABLE providers ADD COLUMN api_key TEXT",
        "ALTER TABLE providers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE providers ADD COLUMN capabilities TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE providers ADD COLUMN last_health_status TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE providers ADD COLUMN last_health_at TEXT",
        "ALTER TABLE transient_windows ADD COLUMN payload TEXT",
        "ALTER TABLE delegation_tasks ADD COLUMN substrate_id TEXT NOT NULL DEFAULT 'local_readonly'",
        "ALTER TABLE delegation_tasks ADD COLUMN authority_mode TEXT NOT NULL DEFAULT 'read_only'",
        "ALTER TABLE delegation_tasks ADD COLUMN budget TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE delegation_tasks ADD COLUMN result_payload TEXT",
        "ALTER TABLE delegation_tasks ADD COLUMN input_tokens INTEGER",
        "ALTER TABLE delegation_tasks ADD COLUMN output_tokens INTEGER",
        "ALTER TABLE delegation_tasks ADD COLUMN duration_ms INTEGER",
    ]
    for stmt in _migrations:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    _seed_default_providers(conn)
    _validate_runtime_schema(conn, db_path)
    conn.commit()
    conn.close()

    return ThreadLocalDB(db_path)


def _seed_default_providers(conn: sqlite3.Connection) -> None:
    from api.agent.substrate_router import default_provider_capabilities

    timeout_sec = int(float(os.environ.get("OPENCLOSET_PROVIDER_TIMEOUT_SECONDS", "1800")))
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip() or None
    openai_base_url = os.environ.get("OPENCLOSET_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    openai_model = os.environ.get("OPENCLOSET_OPENAI_MODEL", "gpt-4.1-mini")

    conn.execute(
        """
        INSERT OR IGNORE INTO providers (
            id, kind, base_url, model_name, timeout_sec, enabled, capabilities, api_key, last_health_status, last_health_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "llamacpp",
            "llamacpp",
            os.environ.get("OPENCLOSET_LLAMACPP_URL", "http://127.0.0.1:8080").rstrip("/"),
            os.environ.get("OPENCLOSET_LLAMACPP_MODEL", "qwen3.6-27b"),
            timeout_sec,
            1,
            json.dumps(default_provider_capabilities("llamacpp")),
            None,
            "unknown",
            None,
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO providers (
            id, kind, base_url, model_name, timeout_sec, enabled, capabilities, api_key, last_health_status, last_health_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ollama",
            "ollama",
            os.environ.get("OPENCLOSET_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            os.environ.get("OPENCLOSET_OLLAMA_MODEL", ""),
            timeout_sec,
            1,
            json.dumps(default_provider_capabilities("ollama")),
            None,
            "unknown",
            None,
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO providers (
            id, kind, base_url, model_name, timeout_sec, enabled, capabilities, api_key, last_health_status, last_health_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "openai",
            "openai",
            openai_base_url,
            openai_model,
            timeout_sec,
            1 if openai_api_key else 0,
            json.dumps(default_provider_capabilities("openai")),
            openai_api_key,
            "unknown",
            None,
        ),
    )
    if openai_api_key:
        conn.execute(
            """
            UPDATE providers
            SET api_key = ?, enabled = 1, base_url = ?, model_name = ?, timeout_sec = ?, capabilities = ?
            WHERE id = 'openai'
            """,
            (openai_api_key, openai_base_url, openai_model, timeout_sec, json.dumps(default_provider_capabilities("openai"))),
        )

    conn.execute(
        """
        UPDATE providers
        SET capabilities = CASE id
            WHEN 'llamacpp' THEN ?
            WHEN 'ollama' THEN ?
            WHEN 'openai' THEN ?
            ELSE COALESCE(capabilities, '{}')
        END
        WHERE capabilities IS NULL OR TRIM(capabilities) = ''
        """,
        (
            json.dumps(default_provider_capabilities("llamacpp")),
            json.dumps(default_provider_capabilities("ollama")),
            json.dumps(default_provider_capabilities("openai")),
        ),
    )

    # Migrate older default 10-minute provider timeouts forward without overwriting
    # any custom timeout a user explicitly set.
    conn.execute(
        """
        UPDATE providers
        SET timeout_sec = ?
        WHERE id IN ('llamacpp', 'ollama', 'openai') AND timeout_sec = 600
        """,
        (timeout_sec,),
    )


def _detect_incompatible_legacy_schema(conn: sqlite3.Connection, db_path: str) -> None:
    expected_columns = {
        "sessions": {"id", "label", "model", "provider", "status", "context_window", "token_count"},
        "runs": {"id", "session_id", "status", "turn_number"},
        "messages": {"id", "session_id", "run_id", "role", "content", "position"},
    }

    for table_name, required in expected_columns.items():
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        if not row:
            continue
        present = {info["name"] for info in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        missing = required - present
        if missing:
            raise RuntimeError(
                f"Database at {db_path} is not compatible with the authoritative OpenCloset Flask runtime. "
                f"Table '{table_name}' is missing columns: {', '.join(sorted(missing))}. "
                "This usually means OPENCLOSET_DB_PATH points at the older prototype store. "
                "Use a clean runtime DB such as d:/openclaw/opencloset/opencloset.db."
            )


def _validate_runtime_schema(conn: sqlite3.Connection, db_path: str) -> None:
    expected_columns = {
        "sessions": {"id", "label", "model", "provider", "status", "context_window", "token_count"},
        "runs": {"id", "session_id", "status", "turn_number"},
        "messages": {"id", "session_id", "run_id", "role", "content", "position"},
        "providers": {"id", "kind", "base_url", "model_name", "timeout_sec", "enabled", "capabilities"},
        "run_input_envelopes": {"id", "session_id", "run_id", "role", "attachments", "metadata"},
        "session_attachments": {"id", "session_id", "attachment_type", "file_name", "storage_path", "metadata"},
        "project_deliveries": {"id", "workspace_id", "build_project_id", "file_name", "storage_path", "status"},
        "delegation_tasks": {"id", "session_id", "task_type", "instruction", "status", "metadata"},
    }

    for table_name, required in expected_columns.items():
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        present = {row["name"] for row in rows}
        missing = required - present
        if missing:
            raise RuntimeError(
                f"Database at {db_path} is not compatible with the authoritative OpenCloset Flask runtime. "
                f"Table '{table_name}' is missing columns: {', '.join(sorted(missing))}. "
                "This usually means OPENCLOSET_DB_PATH points at the older prototype store. "
                "Use a clean runtime DB such as d:/openclaw/opencloset/opencloset.db."
            )


def new_id() -> str:
    """Generate a short UUID-like id using datetime + random bytes."""
    import uuid
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Enum-like constants (documented, not enforced by DB)
# ---------------------------------------------------------------------------

SESSION_STATUSES = {"active", "paused", "rolled-over", "deleted"}
RUN_STATUSES = {"queued", "running", "succeeded", "failed", "interrupted", "rolled-over"}
MESSAGE_ROLES = {"user", "assistant", "system", "tool"}
TOOL_STATUSES = {"pending", "running", "completed", "failed", "interrupted"}
CAPTURE_STATUSES = {"pending", "routed", "processed", "failed"}
WORKSPACE_STATUSES = {"active", "maintenance", "dormant", "archived"}
BUILD_PROJECT_STATUSES = {"planned", "active", "blocked", "paused", "completed", "archived"}
PROJECT_DELIVERY_STATUSES = {"ready", "downloading", "downloaded", "installed", "failed", "cancelled", "expired"}
WORKSPACE_PASTIME_STATUSES = {"enabled", "paused", "archived"}
DELEGATION_TASK_STATUSES = {"queued", "running", "completed", "failed", "blocked", "cancelled"}
