import { useEffect, useState } from "react";
import {
  addSessionMemory,
  createWorkspaceCapture,
  getSessionMemory,
  listWorkspaceCaptures,
  promoteWorkspaceCapture,
  searchSessionMemory,
} from "../api/client";
import type {
  SessionMemoryRecord,
  SessionMemorySearchRecord,
  WorkspaceCaptureRecord,
  WorkspaceEvidenceRecord,
} from "../api/types";
import CustomSelect from "../components/forms/CustomSelect";
import "./EvidenceView.css";

interface EvidenceViewProps {
  workspaceId: string | null;
  buildProjectId: string | null;
  sessionId: string | null;
  evidence: WorkspaceEvidenceRecord[];
  workspaceName: string;
  sessionLabel: string | null;
  onCreateEvidence: (payload: {
    title: string;
    summary: string;
    content?: string;
    evidence_type?: string;
    source_kind?: string;
    tags?: string[];
  }) => Promise<void>;
}

export default function EvidenceView({
  workspaceId,
  buildProjectId,
  sessionId,
  evidence,
  workspaceName,
  sessionLabel,
  onCreateEvidence,
}: EvidenceViewProps) {
  const [memory, setMemory] = useState<SessionMemoryRecord | null>(null);
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memorySearchResults, setMemorySearchResults] = useState<SessionMemorySearchRecord | null>(null);
  const [memoryNote, setMemoryNote] = useState("");
  const [alsoDaily, setAlsoDaily] = useState(true);
  const [captures, setCaptures] = useState<WorkspaceCaptureRecord[]>([]);
  const [captureContent, setCaptureContent] = useState("");
  const [captureSource, setCaptureSource] = useState("manual");
  const [captureEventType, setCaptureEventType] = useState("text");
  const [loadingMemory, setLoadingMemory] = useState(false);
  const [loadingCaptures, setLoadingCaptures] = useState(false);
  const [submittingMemory, setSubmittingMemory] = useState(false);
  const [searchingMemory, setSearchingMemory] = useState(false);
  const [submittingCapture, setSubmittingCapture] = useState(false);
  const [promotingCaptureId, setPromotingCaptureId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [content, setContent] = useState("");
  const [evidenceType, setEvidenceType] = useState("note");
  const [sourceKind, setSourceKind] = useState("note");
  const [tags, setTags] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setMemory(null);
      setMemorySearchResults(null);
      return;
    }

    let cancelled = false;

    async function loadMemory() {
      setLoadingMemory(true);
      try {
        const response = await getSessionMemory(sessionId);
        if (!cancelled) {
          setMemory(response);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load memory");
        }
      } finally {
        if (!cancelled) {
          setLoadingMemory(false);
        }
      }
    }

    void loadMemory();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    if (!workspaceId) {
      setCaptures([]);
      return;
    }

    let cancelled = false;

    async function loadCaptures() {
      setLoadingCaptures(true);
      try {
        const response = await listWorkspaceCaptures(workspaceId, { limit: 30 });
        if (!cancelled) {
          setCaptures(response.captures);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load captures");
        }
      } finally {
        if (!cancelled) {
          setLoadingCaptures(false);
        }
      }
    }

    void loadCaptures();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await onCreateEvidence({
        title,
        summary,
        content,
        evidence_type: evidenceType,
        source_kind: sourceKind,
        tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
      });
      setTitle("");
      setSummary("");
      setContent("");
      setTags("");
    } catch (submissionError) {
      setError(submissionError instanceof Error ? submissionError.message : "Failed to create evidence");
    } finally {
      setSubmitting(false);
    }
  };

  const handleAddMemory = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!sessionId || !memoryNote.trim()) {
      return;
    }
    setSubmittingMemory(true);
    setError(null);
    try {
      const updated = await addSessionMemory(sessionId, {
        content: memoryNote.trim(),
        also_daily: alsoDaily,
      });
      setMemory(updated);
      setMemoryNote("");
    } catch (submissionError) {
      setError(submissionError instanceof Error ? submissionError.message : "Failed to add memory note");
    } finally {
      setSubmittingMemory(false);
    }
  };

  const handleSearchMemory = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!sessionId || !memoryQuery.trim()) {
      setMemorySearchResults(null);
      return;
    }
    setSearchingMemory(true);
    setError(null);
    try {
      const results = await searchSessionMemory(sessionId, {
        query: memoryQuery.trim(),
        limit: 8,
        include_daily: true,
      });
      setMemorySearchResults(results);
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "Failed to search memory");
    } finally {
      setSearchingMemory(false);
    }
  };

  const handleCreateCapture = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!workspaceId || !captureContent.trim()) {
      return;
    }
    setSubmittingCapture(true);
    setError(null);
    try {
      const created = await createWorkspaceCapture(workspaceId, {
        source: captureSource,
        event_type: captureEventType,
        content: captureContent.trim(),
        session_id: sessionId || undefined,
        build_project_id: buildProjectId || undefined,
      });
      setCaptures((current) => [created, ...current]);
      setCaptureContent("");
    } catch (captureError) {
      setError(captureError instanceof Error ? captureError.message : "Failed to create capture");
    } finally {
      setSubmittingCapture(false);
    }
  };

  const handlePromoteCapture = async (captureId: string) => {
    if (!workspaceId) {
      return;
    }
    setPromotingCaptureId(captureId);
    setError(null);
    try {
      const result = await promoteWorkspaceCapture(workspaceId, captureId);
      setCaptures((current) => current.map((capture) => (capture.id === captureId ? result.capture : capture)));
    } catch (captureError) {
      setError(captureError instanceof Error ? captureError.message : "Failed to promote capture");
    } finally {
      setPromotingCaptureId(null);
    }
  };

  return (
    <div className="evidence-view">
      <div className="evidence-header">
        <div>
          <div className="evidence-title">Memory + captures</div>
          <div className="evidence-subtitle">{workspaceName} · {sessionLabel || "Active session"} · working memory, intake, and durable proof</div>
        </div>
        <div className="evidence-count">{captures.length} captures · {evidence.length} evidence entries</div>
      </div>

      <div className="evidence-grid">
        <section className="evidence-column">
          <div className="evidence-section-header">
            <div>
              <div className="evidence-section-title">Session memory</div>
              <div className="evidence-section-subtitle">Append to the diary and search recent memory without leaving the active OpenCloset session.</div>
            </div>
            {loadingMemory && <span className="evidence-inline-meta">loading</span>}
          </div>

          <form className="evidence-form" onSubmit={handleAddMemory}>
            <textarea
              value={memoryNote}
              onChange={(event) => setMemoryNote(event.target.value)}
              placeholder="Add a concise memory note"
              rows={3}
            />
            <div className="evidence-form-footer compact">
              <label className="evidence-checkbox">
                <input type="checkbox" checked={alsoDaily} onChange={(event) => setAlsoDaily(event.target.checked)} />
                also add to daily log
              </label>
              <button className="mode-btn active" type="submit" disabled={submittingMemory || !sessionId}>
                {submittingMemory ? "Saving..." : "Add note"}
              </button>
            </div>
          </form>

          <form className="evidence-form" onSubmit={handleSearchMemory}>
            <div className="evidence-form-grid single-row">
              <input
                value={memoryQuery}
                onChange={(event) => setMemoryQuery(event.target.value)}
                placeholder="Search session and daily memory"
              />
            </div>
            <div className="evidence-form-footer compact">
              <div className="evidence-inline-meta">Hybrid memory search</div>
              <button className="mode-btn" type="submit" disabled={searchingMemory || !sessionId}>
                {searchingMemory ? "Searching..." : "Search"}
              </button>
            </div>
          </form>

          {memorySearchResults ? (
            <div className="evidence-list">
              {memorySearchResults.results.length ? (
                memorySearchResults.results.map((result) => (
                  <div key={`${result.path}-${result.id}`} className="evidence-card compact">
                    <div className="evidence-card-header">
                      <div>
                        <div className="evidence-card-title">{result.kind}</div>
                        <div className="evidence-card-meta">{result.sources.join(" + ")} · score {result.score.toFixed(2)}</div>
                      </div>
                    </div>
                    <div className="evidence-card-summary">{result.snippet}</div>
                  </div>
                ))
              ) : (
                <div className="evidence-empty">No memory results for this query.</div>
              )}
            </div>
          ) : null}

          <div className="evidence-list">
            <MemoryDocumentCard title="Session diary" content={memory?.session_diary.content || ""} />
            <MemoryDocumentCard
              title={memory?.daily_log.date ? `Daily log (${memory.daily_log.date})` : "Daily log"}
              content={memory?.daily_log.content || ""}
            />
          </div>
        </section>

        <section className="evidence-column">
          <div className="evidence-section-header">
            <div>
              <div className="evidence-section-title">Capture intake</div>
              <div className="evidence-section-subtitle">Route new material into the workspace and promote it when it becomes durable.</div>
            </div>
            {loadingCaptures && <span className="evidence-inline-meta">loading</span>}
          </div>

          <form className="evidence-form" onSubmit={handleCreateCapture}>
            <div className="evidence-form-grid">
              <CustomSelect
                value={captureSource}
                onChange={setCaptureSource}
                options={[
                  { value: "manual", label: "manual" },
                  { value: "phonecloset", label: "phonecloset" },
                  { value: "cli", label: "cli" },
                  { value: "webhook", label: "webhook" },
                ]}
                ariaLabel="Capture source"
              />
              <CustomSelect
                value={captureEventType}
                onChange={setCaptureEventType}
                options={[
                  { value: "text", label: "text" },
                  { value: "app_event", label: "app_event" },
                  { value: "thread_candidate", label: "thread_candidate" },
                  { value: "workspace_signal", label: "workspace_signal" },
                ]}
                ariaLabel="Capture event type"
              />
            </div>
            <textarea
              value={captureContent}
              onChange={(event) => setCaptureContent(event.target.value)}
              placeholder="Capture what changed, what arrived, or what needs routing next"
              rows={4}
            />
            <div className="evidence-form-footer compact">
              <div className="evidence-inline-meta">Workspace-scoped intake</div>
              <button className="mode-btn active" type="submit" disabled={submittingCapture || !workspaceId}>
                {submittingCapture ? "Saving..." : "Add capture"}
              </button>
            </div>
          </form>

          <div className="evidence-list">
            {captures.length ? (
              captures.map((capture) => (
                <div key={capture.id} className="evidence-card compact">
                  <div className="evidence-card-header">
                    <div>
                      <div className="evidence-card-title">{capture.event_type}</div>
                      <div className="evidence-card-meta">{capture.source} · {formatDate(capture.received_at)}</div>
                    </div>
                    <span className={`badge ${capture.status === "processed" ? "badge-success" : "badge-pending"}`}>{capture.status}</span>
                  </div>
                  <div className="evidence-card-summary">{capture.content}</div>
                  <div className="evidence-form-footer compact">
                    <div className="evidence-inline-meta">{capture.session_id ? "session-linked" : "workspace only"}</div>
                    <button
                      className="mode-btn"
                      type="button"
                      disabled={capture.status === "processed" || promotingCaptureId === capture.id}
                      onClick={() => void handlePromoteCapture(capture.id)}
                    >
                      {promotingCaptureId === capture.id ? "Promoting..." : capture.status === "processed" ? "Promoted" : "Promote"}
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <div className="evidence-empty">No workspace captures yet.</div>
            )}
          </div>
        </section>

        <section className="evidence-column">
          <div className="evidence-section-header">
            <div>
              <div className="evidence-section-title">Evidence registry</div>
              <div className="evidence-section-subtitle">Curated proof that should outlive transient captures.</div>
            </div>
            <span className="evidence-inline-meta">{evidence.length} entries</span>
          </div>

          <form className="evidence-form" onSubmit={handleSubmit}>
            <div className="evidence-form-grid">
              <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Evidence title" required />
              <CustomSelect
                value={evidenceType}
                onChange={setEvidenceType}
                options={[
                  { value: "note", label: "note" },
                  { value: "verification", label: "verification" },
                  { value: "decision", label: "decision" },
                  { value: "artifact", label: "artifact" },
                ]}
                ariaLabel="Evidence type"
              />
              <input value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="Short summary" required />
              <CustomSelect
                value={sourceKind}
                onChange={setSourceKind}
                options={[
                  { value: "note", label: "note" },
                  { value: "build", label: "build" },
                  { value: "review", label: "review" },
                  { value: "signal", label: "signal" },
                  { value: "capture", label: "capture" },
                ]}
                ariaLabel="Evidence source kind"
              />
            </div>
            <textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="Durable details, command output, or proof notes" rows={4} />
            <div className="evidence-form-footer">
              <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="tags, comma, separated" />
              <button className="mode-btn active" type="submit" disabled={submitting || !workspaceId}>
                {submitting ? "Saving..." : "Add evidence"}
              </button>
            </div>
          </form>

          <div className="evidence-list">
            {evidence.map((entry) => (
              <div key={entry.id} className="evidence-card">
                <div className="evidence-card-header">
                  <div>
                    <div className="evidence-card-title">{entry.title}</div>
                    <div className="evidence-card-meta">{entry.evidence_type} · {entry.source_kind} · {formatDate(entry.updated_at)}</div>
                  </div>
                  <span className={`badge ${entry.evidence_type === "verification" ? "badge-success" : "badge-pending"}`}>{entry.status}</span>
                </div>
                <div className="evidence-card-summary">{entry.summary}</div>
                {entry.content && <pre className="evidence-card-content">{entry.content}</pre>}
                {entry.tags.length > 0 && (
                  <div className="evidence-tags">
                    {entry.tags.map((tag) => (
                      <span key={tag} className="evidence-tag">{tag}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {!evidence.length && <div className="evidence-empty">No workspace evidence yet.</div>}
          </div>
        </section>
      </div>

      {error && <div className="evidence-error">{error}</div>}
    </div>
  );
}

function MemoryDocumentCard({ title, content }: { title: string; content: string }) {
  return (
    <div className="evidence-card">
      <div className="evidence-card-header">
        <div className="evidence-card-title">{title}</div>
      </div>
      {content ? <pre className="evidence-card-content">{content}</pre> : <div className="evidence-empty">No entries yet.</div>}
    </div>
  );
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}
