from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from opencloset.provider import (
    ChatProvider,
    OpenAICompatibleProvider,
    ProviderError,
    estimate_tokens,
)
from opencloset.storage import (
    DEFAULT_AGENT_ID,
    DEFAULT_PROVIDER_ID,
    AgentRecord,
    CaptureRecord,
    MessageRecord,
    OpenClosetStore,
    ProviderRecord,
    SessionRecord,
)


DEFAULT_DB_PATH = Path(
    os.getenv("OPENCLOSET_DB_PATH", Path(__file__).parent / "data" / "opencloset.db")
)
DEFAULT_TRANSCRIPTS_PATH = Path(
    os.getenv("OPENCLOSET_TRANSCRIPTS_PATH", Path(__file__).parent / "data" / "transcripts")
)

SYSTEM_WARNING_THRESHOLD = 20_000
SYSTEM_DANGER_THRESHOLD = 29_000


class SessionCreate(BaseModel):
    title: str | None = Field(default=None)
    agent_id: str = Field(default=DEFAULT_AGENT_ID)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)


class CaptureCreate(BaseModel):
    raw_content: str = Field(min_length=1)
    source_mode: str = Field(default="typed")
    project: str = Field(default="OpenCloset")
    destination_agent: str = Field(default="Clo")
    requested_action: str = Field(default="Review and route")


class ProviderCreate(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: str = Field(default="openai-compatible")
    base_url: str = Field(default="http://127.0.0.1:8080/v1")
    model_name: str = Field(default="")
    timeout_sec: int = Field(default=45)
    enabled: bool = Field(default=True)
    api_key: str | None = Field(default=None)


class ProviderUpdate(BaseModel):
    kind: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    timeout_sec: int | None = None
    enabled: bool | None = None
    api_key: str | None = None


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def summarize_text(text: str, limit: int = 140) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    head = cleaned[: limit - 3].rsplit(" ", 1)[0]
    return f"{head}..."


MAX_ASSISTANT_MESSAGE_CHARS = 4000


def truncate_content(text: str, limit: int = MAX_ASSISTANT_MESSAGE_CHARS) -> str:
    """Truncate long tool outputs / assistant replies to prevent context bloat."""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    # Try to break at a line boundary near the limit
    last_newline = truncated.rfind("\n")
    if last_newline > limit * 0.8:
        truncated = truncated[:last_newline]
    return f"{truncated}\n\n... [truncated: {len(text) - limit} chars omitted]"


def infer_tags(project: str, raw_content: str) -> list[str]:
    project_slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-") or "general"
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", raw_content.lower())
    stop_words = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "into",
        "should",
        "could",
        "would",
        "about",
        "because",
        "after",
        "before",
        "where",
        "there",
        "their",
        "being",
        "opencloset",
    }
    ranked: list[str] = []
    seen = {project_slug}
    for word in words:
        if word in stop_words or word in seen:
            continue
        seen.add(word)
        ranked.append(word)
        if len(ranked) >= 4:
            break
    return [project_slug, *ranked]


def session_to_dict(session: SessionRecord) -> dict[str, Any]:
    pressure = "normal"
    if session.token_estimate >= SYSTEM_DANGER_THRESHOLD:
        pressure = "danger"
    elif session.token_estimate >= SYSTEM_WARNING_THRESHOLD:
        pressure = "warning"
    return {
        "id": session.id,
        "agent_id": session.agent_id,
        "title": session.title,
        "status": session.status,
        "token_estimate": session.token_estimate,
        "pressure": pressure,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "parent_session_id": session.parent_session_id,
    }


def message_to_dict(message: MessageRecord) -> dict[str, Any]:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "created_at": message.created_at,
    }


def provider_to_dict(provider: ProviderRecord) -> dict[str, Any]:
    return {
        "id": provider.id,
        "kind": provider.kind,
        "base_url": provider.base_url,
        "model_name": provider.model_name,
        "timeout_sec": provider.timeout_sec,
        "enabled": provider.enabled,
        "last_health_status": provider.last_health_status,
        "last_health_at": provider.last_health_at,
    }


def capture_to_dict(record: CaptureRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "source_mode": record.source_mode,
        "raw_content": record.raw_content,
        "cleaned_summary": record.cleaned_summary,
        "project": record.project,
        "tags": record.tags,
        "status": record.status,
        "destination_agent": record.destination_agent,
        "packet": record.packet_json,
    }


def build_capture_packet(payload: CaptureCreate, capture_id: str, summary: str, tags: list[str]) -> dict[str, Any]:
    return {
        "id": capture_id,
        "created_at": utc_now(),
        "source_device": "OpenCloset Desktop",
        "input_mode": payload.source_mode,
        "project": payload.project,
        "task_type": "capture",
        "urgency": "normal",
        "local_summary": summary,
        "raw_input_ref": f"local://captures/{capture_id}",
        "tags": tags,
        "requested_action": payload.requested_action,
        "preferred_agent": payload.destination_agent,
        "requires_confirmation": False,
        "raw_content": payload.raw_content,
    }


def generate_session_title(first_message: str | None) -> str:
    """Legacy title generator: first-N-words heuristic."""
    if not first_message:
        return "Fresh session"
    return summarize_text(first_message, limit=48)


def generate_session_title_llm(
    messages: list[str],
    provider: ChatProvider | None = None,
    *,
    model: str | None = None,
) -> str:
    """Generate a concise session title using the LLM.

    Falls back to the legacy heuristic when no provider is available
    or the call fails.
    """
    if provider is None:
        # Fallback: use the first message
        first = messages[0] if messages else None
        return generate_session_title(first)

    # Build context from recent messages
    context = "\n".join(messages[-6:])
    prompt = (
        "You are a session titler. Given the following conversation excerpt, "
        "produce a concise, descriptive title (at most 60 characters) that "
        "captures the main topic or goal of this conversation.\n\n"
        f"Conversation:\n{context}\n\n"
        "Title:"
    )
    try:
        resp = provider.chat(prompt, model=model)
        title = resp.strip().strip("\"'").strip()
        if title and 3 <= len(title) <= 60:
            return title
    except Exception:
        pass
    # Fallback
    first = messages[0] if messages else None
    return generate_session_title(first)


def build_rollover_summary(messages: list[MessageRecord]) -> str:
    if not messages:
        return "Fresh session. No prior messages."
    recent = messages[-6:]
    lines = [f"{message.role}: {summarize_text(message.content, 90)}" for message in recent]
    return "Carry forward the important context from this prior session:\n" + "\n".join(lines)


def create_app(
    *,
    db_path: Path | None = None,
    transcript_dir: Path | None = None,
    provider_client: ChatProvider | None = None,
) -> FastAPI:
    resolved_db_path = db_path or DEFAULT_DB_PATH
    resolved_transcript_dir = transcript_dir or DEFAULT_TRANSCRIPTS_PATH
    store = OpenClosetStore(resolved_db_path, resolved_transcript_dir)
    provider_client = provider_client or OpenAICompatibleProvider()

    app = FastAPI(title="OpenCloset", version="0.1.0")
    app.state.store = store
    app.state.provider_client = provider_client

    def get_store() -> OpenClosetStore:
        return app.state.store

    def get_provider() -> ChatProvider:
        return app.state.provider_client

    async def probe_provider(provider_id: str = DEFAULT_PROVIDER_ID) -> dict[str, Any]:
        current_store = get_store()
        current_provider = current_store.get_provider(provider_id)
        if current_provider is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        checked_at = utc_now()
        try:
            health = await get_provider().health_check(current_provider)
            current_store.update_provider_health(provider_id, health.status, checked_at)
            return {
                "provider": provider_to_dict(current_store.get_provider(provider_id) or current_provider),
                "health": {
                    "status": health.status,
                    "detail": health.detail,
                    "model_count": health.model_count,
                },
            }
        except ProviderError as exc:
            current_store.update_provider_health(provider_id, exc.code, checked_at)
            return {
                "provider": provider_to_dict(current_store.get_provider(provider_id) or current_provider),
                "health": {
                    "status": exc.code,
                    "detail": exc.message,
                    "model_count": 0,
                },
            }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return """
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>OpenCloset</title>
            <style>
              :root {
                --bg: #f2ede4;
                --panel: rgba(255, 251, 245, 0.94);
                --ink: #1c2428;
                --muted: #66757f;
                --accent: #0d9488;
                --accent-dark: #115e59;
                --line: #d8d0c2;
                --danger: #b91c1c;
                --warn: #c2410c;
              }
              * { box-sizing: border-box; }
              body {
                margin: 0;
                font-family: "Segoe UI", sans-serif;
                color: var(--ink);
                background:
                  radial-gradient(circle at top left, rgba(13, 148, 136, 0.12), transparent 28%),
                  radial-gradient(circle at top right, rgba(194, 65, 12, 0.10), transparent 24%),
                  linear-gradient(180deg, #f7f3eb, #ece4d7);
              }
              main {
                max-width: 1280px;
                margin: 0 auto;
                padding: 24px 18px 40px;
              }
              .hero, .panel {
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 22px;
                box-shadow: 0 12px 36px rgba(15, 23, 42, 0.08);
              }
              .hero { padding: 24px; }
              .hero h1 { margin: 0; font-size: 2rem; }
              .hero p { margin: 8px 0 0; color: var(--muted); max-width: 70ch; }
              .layout {
                display: grid;
                grid-template-columns: 290px minmax(0, 1fr) 320px;
                gap: 16px;
                margin-top: 18px;
                align-items: start;
              }
              .panel { padding: 16px; }
              .stack { display: grid; gap: 14px; }
              .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
              button {
                border: 0;
                border-radius: 999px;
                padding: 10px 14px;
                background: var(--accent);
                color: white;
                cursor: pointer;
              }
              button.secondary { background: #e5e7eb; color: var(--ink); }
              button.warning { background: var(--warn); }
              input, textarea, select {
                width: 100%;
                border-radius: 14px;
                border: 1px solid #cfc6b7;
                padding: 10px 12px;
                background: #fffdfa;
              }
              textarea { min-height: 96px; resize: vertical; }
              .session-list, .capture-list, .message-list {
                display: grid;
                gap: 10px;
              }
              .session-card, .capture-card, .message-card, .status-card {
                border: 1px solid var(--line);
                border-radius: 16px;
                padding: 12px;
                background: rgba(255,255,255,0.75);
              }
              .session-card.active { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
              .meta, .chips {
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
                align-items: center;
              }
              .chip {
                border: 1px solid #d7ddd9;
                border-radius: 999px;
                padding: 3px 8px;
                font-size: 12px;
                color: var(--muted);
              }
              .pressure-warning { color: var(--warn); }
              .pressure-danger { color: var(--danger); }
              .message-card.user { border-left: 4px solid var(--accent); }
              .message-card.assistant { border-left: 4px solid var(--accent-dark); }
              .message-card.system { border-left: 4px solid var(--warn); }
              .muted { color: var(--muted); }
              .titleline {
                display: flex;
                justify-content: space-between;
                gap: 12px;
                align-items: baseline;
              }
              @media (max-width: 1100px) {
                .layout { grid-template-columns: 1fr; }
              }
            </style>
          </head>
          <body>
            <main>
              <section class="hero">
                <h1>OpenCloset</h1>
                <p>Desktop-first harness for Clo. Stable sessions, visible provider health, manual context control, and no phantom resets.</p>
              </section>
              <div class="layout">
                <section class="panel stack">
                  <div class="titleline">
                    <h2>Sessions</h2>
                    <button onclick="createSession()">Start Fresh Session</button>
                  </div>
                  <div class="row">
                    <button class="secondary" onclick="refreshAll()">Refresh</button>
                  </div>
                  <div id="session-list" class="session-list"></div>
                </section>
                <section class="panel stack">
                  <div class="titleline">
                    <h2 id="chat-title">Chat</h2>
                    <div class="row">
                      <button class="secondary" onclick="forkSession()">Fork Session</button>
                      <button class="warning" onclick="rolloverSession()">Summarize Into New Session</button>
                    </div>
                  </div>
                  <div id="chat-meta" class="meta muted"></div>
                  <div id="message-list" class="message-list"></div>
                  <form onsubmit="sendMessage(event)" class="stack">
                    <textarea id="message-input" placeholder="Talk to Clo." required></textarea>
                    <div class="row">
                      <button type="submit">Send</button>
                      <span id="composer-status" class="muted"></span>
                    </div>
                  </form>
                </section>
                <section class="panel stack">
                  <div>
                    <h2>System</h2>
                    <div id="system-status" class="stack"></div>
                  </div>
                  <div>
                    <h2>Captures</h2>
                    <form onsubmit="createCapture(event)" class="stack">
                      <textarea id="capture-input" placeholder="Optional structured capture for later." required></textarea>
                      <input id="capture-project" value="OpenCloset" />
                      <button type="submit">Save Capture</button>
                    </form>
                    <div id="capture-list" class="capture-list"></div>
                  </div>
                </section>
              </div>
            </main>
            <script>
              let activeSessionId = null;

              async function api(path, options = {}) {
                const response = await fetch(path, options);
                const data = await response.json();
                if (!response.ok) {
                  throw new Error(data.detail || 'Request failed');
                }
                return data;
              }

              function pressureClass(pressure) {
                if (pressure === 'danger') return 'pressure-danger';
                if (pressure === 'warning') return 'pressure-warning';
                return '';
              }

              async function loadSessions(preferredSessionId = null) {
                const sessions = await api('/api/sessions');
                const list = document.getElementById('session-list');
                list.innerHTML = '';
                if (!sessions.length) {
                  const created = await api('/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
                  activeSessionId = created.id;
                  return loadSessions(created.id);
                }
                activeSessionId = preferredSessionId || activeSessionId || sessions[0].id;
                for (const session of sessions) {
                  const card = document.createElement('article');
                  card.className = 'session-card' + (session.id === activeSessionId ? ' active' : '');
                  card.innerHTML = `
                    <div class="titleline">
                      <strong>${session.title}</strong>
                      <span class="chip ${pressureClass(session.pressure)}">${session.pressure}</span>
                    </div>
                    <div class="chips">
                      <span class="chip">${session.status}</span>
                      <span class="chip">${session.token_estimate} tokens</span>
                    </div>
                    <div class="muted">${session.updated_at}</div>
                  `;
                  card.onclick = async () => {
                    activeSessionId = session.id;
                    await loadSessions(session.id);
                    await loadSessionDetail();
                  };
                  list.appendChild(card);
                }
              }

              async function loadSessionDetail() {
                if (!activeSessionId) return;
                const detail = await api(`/api/sessions/${activeSessionId}`);
                document.getElementById('chat-title').textContent = detail.session.title;
                document.getElementById('chat-meta').innerHTML = `
                  <span class="chip">${detail.session.status}</span>
                  <span class="chip ${pressureClass(detail.session.pressure)}">${detail.session.token_estimate} tokens</span>
                  <span class="chip">${detail.agent.display_name}</span>
                `;
                const messages = document.getElementById('message-list');
                messages.innerHTML = '';
                if (!detail.messages.length) {
                  messages.innerHTML = '<p class="muted">No messages yet.</p>';
                } else {
                  for (const message of detail.messages) {
                    const card = document.createElement('article');
                    card.className = `message-card ${message.role}`;
                    card.innerHTML = `
                      <div class="titleline">
                        <strong>${message.role}</strong>
                        <span class="chip">${message.status}</span>
                      </div>
                      <p>${message.content.replaceAll('<', '&lt;').replaceAll('>', '&gt;')}</p>
                      <div class="muted">${message.created_at}</div>
                    `;
                    messages.appendChild(card);
                  }
                }
                for (const run of detail.runs) {
                  if (run.status === 'failed') {
                    const card = document.createElement('article');
                    card.className = 'status-card';
                    card.innerHTML = `
                      <strong>Run failed</strong>
                      <p>${run.error_code}: ${run.error_text}</p>
                      <div class="muted">${run.completed_at || run.started_at}</div>
                    `;
                    messages.appendChild(card);
                  }
                }
              }

              async function loadSystem() {
                const status = await api('/api/system/status');
                const container = document.getElementById('system-status');
                container.innerHTML = `
                  <article class="status-card">
                    <strong>Provider</strong>
                    <p>${status.provider.model_name} at ${status.provider.base_url}</p>
                    <div class="chips">
                      <span class="chip">${status.provider.last_health_status}</span>
                      <span class="chip">${status.session_count} sessions</span>
                    </div>
                  </article>
                  <article class="status-card">
                    <strong>Recent runs</strong>
                    <p class="muted">${status.recent_runs.map(run => `${run.status} · ${run.model}`).join('<br/>') || 'No runs yet.'}</p>
                  </article>
                `;
              }

              async function loadCaptures() {
                const captures = await api('/api/captures');
                const container = document.getElementById('capture-list');
                container.innerHTML = '';
                if (!captures.length) {
                  container.innerHTML = '<p class="muted">No captures yet.</p>';
                  return;
                }
                for (const capture of captures.slice(0, 6)) {
                  const card = document.createElement('article');
                  card.className = 'capture-card';
                  card.innerHTML = `
                    <div class="titleline">
                      <strong>${capture.project}</strong>
                      <span class="chip">${capture.status}</span>
                    </div>
                    <p>${capture.cleaned_summary}</p>
                    <div class="chips">${capture.tags.map(tag => `<span class="chip">${tag}</span>`).join('')}</div>
                  `;
                  container.appendChild(card);
                }
              }

              async function sendMessage(event) {
                event.preventDefault();
                const input = document.getElementById('message-input');
                const composer = document.getElementById('composer-status');
                composer.textContent = 'Sending…';
                try {
                  await api(`/api/sessions/${activeSessionId}/messages`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: input.value })
                  });
                  input.value = '';
                  composer.textContent = 'Reply received.';
                  await refreshAll();
                } catch (error) {
                  composer.textContent = error.message;
                  await refreshAll();
                }
              }

              async function createSession() {
                const created = await api('/api/sessions', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({})
                });
                activeSessionId = created.id;
                await refreshAll();
              }

              async function forkSession() {
                if (!activeSessionId) return;
                const forked = await api(`/api/sessions/${activeSessionId}/fork`, { method: 'POST' });
                activeSessionId = forked.session.id;
                await refreshAll();
              }

              async function rolloverSession() {
                if (!activeSessionId) return;
                const rolled = await api(`/api/sessions/${activeSessionId}/summarize-rollover`, { method: 'POST' });
                activeSessionId = rolled.session.id;
                await refreshAll();
              }

              async function createCapture(event) {
                event.preventDefault();
                const input = document.getElementById('capture-input');
                await api('/api/captures', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ raw_content: input.value, project: document.getElementById('capture-project').value })
                });
                input.value = '';
                await loadCaptures();
              }

              async function refreshAll() {
                await loadSessions(activeSessionId);
                await loadSessionDetail();
                await loadSystem();
                await loadCaptures();
              }

              refreshAll();
            </script>
          </body>
        </html>
        """

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        status = await probe_provider()
        current_store = get_store()
        return {
            "status": "ok",
            "app": "OpenCloset",
            "db_path": str(resolved_db_path),
            "transcript_dir": str(resolved_transcript_dir),
            "session_count": len(current_store.list_sessions()),
            "provider_health": status["health"],
        }

    @app.get("/api/providers")
    async def list_providers() -> list[dict[str, Any]]:
        return [provider_to_dict(record) for record in get_store().list_providers()]

    @app.post("/api/providers/test")
    async def test_provider() -> dict[str, Any]:
        return await probe_provider()

    @app.get("/api/agents")
    async def list_agents() -> list[dict[str, Any]]:
        return [
            {
                "id": record.id,
                "display_name": record.display_name,
                "default_model": record.default_model,
                "enabled": record.enabled,
            }
            for record in get_store().list_agents()
        ]

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return [session_to_dict(record) for record in get_store().list_sessions()]

    @app.post("/api/sessions")
    async def create_session(payload: SessionCreate) -> dict[str, Any]:
        current_store = get_store()
        agent = current_store.get_agent(payload.agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        now = utc_now()
        session = current_store.create_session(
            session_id=uuid.uuid4().hex,
            agent_id=payload.agent_id,
            title=payload.title or "Fresh session",
            status="ready",
            token_estimate=0,
            created_at=now,
            updated_at=now,
            parent_session_id=None,
        )
        return session_to_dict(session)

    @app.get("/api/sessions/{session_id}")
    async def get_session_detail(session_id: str) -> dict[str, Any]:
        current_store = get_store()
        session = current_store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        agent = current_store.get_agent(session.agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        runs = current_store.list_runs(session_id)
        return {
            "session": session_to_dict(session),
            "agent": {
                "id": agent.id,
                "display_name": agent.display_name,
                "default_model": agent.default_model,
            },
            "messages": [message_to_dict(message) for message in current_store.list_messages(session_id)],
            "runs": [
                {
                    "id": run.id,
                    "status": run.status,
                    "provider_id": run.provider_id,
                    "model": run.model,
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                    "error_code": run.error_code,
                    "error_text": run.error_text,
                    "token_input_est": run.token_input_est,
                    "token_output_est": run.token_output_est,
                }
                for run in runs
            ],
        }

    @app.post("/api/sessions/{session_id}/messages")
    async def create_message(session_id: str, payload: MessageCreate) -> dict[str, Any]:
        current_store = get_store()
        session = current_store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        agent = current_store.get_agent(session.agent_id)
        provider = current_store.get_provider(DEFAULT_PROVIDER_ID)
        if agent is None or provider is None:
            raise HTTPException(status_code=500, detail="Runtime configuration incomplete")

        now = utc_now()
        user_message = current_store.create_message(
            session_id,
            role="user",
            content=payload.content,
            status="complete",
            created_at=now,
        )
        history_for_prompt = current_store.list_messages(session_id)
        title = session.title
        if title == "Fresh session":
            title = generate_session_title(payload.content)
        token_input_est = sum(estimate_tokens(message.content) for message in history_for_prompt) + estimate_tokens(agent.role_prompt)
        session = current_store.update_session(
            session_id,
            title=title,
            status="running",
            token_estimate=token_input_est,
            updated_at=now,
        ) or session
        run = current_store.create_run(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            trigger_message_id=user_message.id,
            provider_id=provider.id,
            model=provider.model_name,
            status="running",
            started_at=now,
            token_input_est=token_input_est,
        )

        # Trim message history to stay within context budget
        history_for_prompt = trim_messages_to_budget(
            history_for_prompt, agent.role_prompt
        )

        try:
            reply = await get_provider().create_reply(provider, agent.role_prompt, history_for_prompt)
        except ProviderError as exc:
            failed_at = utc_now()
            current_store.complete_run(
                run.id,
                status="failed",
                completed_at=failed_at,
                error_code=exc.code,
                error_text=exc.message,
                token_output_est=0,
            )
            current_store.create_message(
                session_id,
                role="system",
                content=f"Run failed: {exc.code} - {exc.message}",
                status="error",
                created_at=failed_at,
            )
            updated_token_est = token_input_est
            current_store.update_session(
                session_id,
                status="error",
                token_estimate=updated_token_est,
                updated_at=failed_at,
            )
            return await get_session_detail(session_id)

        completed_at = utc_now()
        assistant_message = current_store.create_message(
            session_id,
            role="assistant",
            content=reply.text,
            status="complete",
            created_at=completed_at,
        )
        current_store.complete_run(
            run.id,
            status="succeeded",
            completed_at=completed_at,
            token_output_est=reply.output_tokens_est,
        )
        updated_token_est = token_input_est + reply.output_tokens_est
        current_store.update_session(
            session_id,
            status="ready",
            token_estimate=updated_token_est,
            updated_at=assistant_message.created_at,
        )
        return await get_session_detail(session_id)

    @app.post("/api/sessions/{session_id}/fork")
    async def fork_session(session_id: str) -> dict[str, Any]:
        current_store = get_store()
        original = current_store.get_session(session_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Session not found")
        now = utc_now()
        forked = current_store.create_session(
            session_id=uuid.uuid4().hex,
            agent_id=original.agent_id,
            title=f"{original.title} (fork)",
            status="ready",
            token_estimate=original.token_estimate,
            created_at=now,
            updated_at=now,
            parent_session_id=original.id,
        )
        for message in current_store.list_messages(session_id):
            current_store.create_message(
                forked.id,
                role=message.role,
                content=message.content,
                status=message.status,
                created_at=message.created_at,
            )
        return {"session": session_to_dict(forked)}

    @app.post("/api/sessions/{session_id}/summarize-rollover")
    async def summarize_rollover(session_id: str) -> dict[str, Any]:
        current_store = get_store()
        original = current_store.get_session(session_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = current_store.list_messages(session_id)
        summary_text = build_rollover_summary(messages)
        now = utc_now()
        rolled = current_store.create_session(
            session_id=uuid.uuid4().hex,
            agent_id=original.agent_id,
            title=f"{original.title} (fresh)",
            status="ready",
            token_estimate=estimate_tokens(summary_text),
            created_at=now,
            updated_at=now,
            parent_session_id=original.id,
        )
        current_store.create_message(
            rolled.id,
            role="system",
            content=summary_text,
            status="complete",
            created_at=now,
        )
        return {"session": session_to_dict(rolled)}

    @app.get("/api/system/status")
    async def system_status() -> dict[str, Any]:
        current_store = get_store()
        provider_status = await probe_provider()
        provider = current_store.get_provider(DEFAULT_PROVIDER_ID)
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        recent_runs = current_store.list_runs(limit=5)
        return {
            "provider": provider_to_dict(provider),
            "provider_probe": provider_status["health"],
            "session_count": len(current_store.list_sessions()),
            "recent_runs": [
                {
                    "id": run.id,
                    "status": run.status,
                    "model": run.model,
                    "error_code": run.error_code,
                    "error_text": run.error_text,
                }
                for run in recent_runs
            ],
        }

    @app.get("/api/captures")
    async def list_captures() -> list[dict[str, Any]]:
        return [capture_to_dict(record) for record in get_store().list_captures()]

    @app.post("/api/captures")
    async def create_capture(payload: CaptureCreate) -> dict[str, Any]:
        capture_id = f"opencloset_{datetime.now().strftime('%Y_%m_%d')}_{uuid.uuid4().hex[:8]}"
        summary = summarize_text(payload.raw_content)
        tags = infer_tags(payload.project, payload.raw_content)
        packet = build_capture_packet(payload, capture_id, summary, tags)
        record = CaptureRecord(
            id=capture_id,
            created_at=utc_now(),
            source_mode=payload.source_mode,
            raw_content=payload.raw_content,
            cleaned_summary=summary,
            project=payload.project,
            tags=tags,
            status="Queued",
            destination_agent=payload.destination_agent,
            packet_json=packet,
        )
        get_store().create_capture(record)
        return capture_to_dict(record)

    return app


app = create_app()

