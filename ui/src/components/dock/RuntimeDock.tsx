import type { PlanRecord, SessionDetail, SessionEventRecord, WorkspaceRuntimeRecord, WorkspaceSignalRecord } from "../../api/types";
import "./RuntimeDock.css";

interface RuntimeDockProps {
  expanded: boolean;
  onToggle: (expanded: boolean) => void;
  session: SessionDetail | null;
  plan: PlanRecord | null;
  events: SessionEventRecord[];
  workspaceRuntime: WorkspaceRuntimeRecord | null;
  isExecuting: boolean;
  onApplySignalAction?: (signal: WorkspaceSignalRecord, action: string) => Promise<void>;
}

export default function RuntimeDock({
  expanded,
  onToggle,
  session,
  plan,
  events,
  workspaceRuntime,
  isExecuting,
  onApplySignalAction,
}: RuntimeDockProps) {
  const tokenPercent = buildTokenPercent(plan);
  const tokenClass = tokenPercent >= 85 ? "high" : tokenPercent >= 50 ? "medium" : "low";
  const activeRun = session?.current_run;
  const currentRunLabel = activeRun ? `turn ${activeRun.turn_number}` : isExecuting ? "starting" : "idle";
  const queuedCount = activeRun?.status === "queued" ? 1 : 0;
  const recentEvents = [...events].slice(-4).reverse();
  const queueCandidates = workspaceRuntime?.candidates || [];
  const topCandidate = workspaceRuntime?.top_candidate || null;
  const selectedPastime = workspaceRuntime?.selected_pastime || null;
  const attentionProfile = workspaceRuntime?.attention_profile || null;
  const openSignals = workspaceRuntime?.signals || [];
  const workerRecords = workspaceRuntime?.workers || [];
  const inboxSignals = openSignals.slice(0, 3);
  const registeredPastimeCount = workspaceRuntime?.pastimes.length || 0;
  const activeWorkerCount = workerRecords.filter((worker) => worker.status !== "idle").length;
  const contextGuard = buildContextGuardSummary(plan);
  const selectedPastimeMeta = describeSelectedPastime(selectedPastime, registeredPastimeCount);

  return (
    <div className="runtime-dock">
      <div
        className="dock-bar"
        onClick={() => onToggle(!expanded)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle(!expanded);
          }
        }}
        role="button"
        tabIndex={0}
      >
        <div className="dock-strip">
          <DockSummaryPill
            label="Run"
            value={activeRun ? activeRun.status : isExecuting ? "running" : "idle"}
            detail={currentRunLabel}
          />
          <DockSummaryPill
            label="Workspace"
            value={openSignals.length ? `${openSignals.length} inbox` : queueCandidates.length ? `${queueCandidates.length} queued` : "idle"}
            detail={`${activeWorkerCount} active workers`}
          />
          <DockSummaryPill
            label="Attention"
            value={formatAttentionMode(attentionProfile?.mode || "warm")}
            detail={`${attentionProfile?.current_attention_level || 0}% · ${attentionProfile?.notification_threshold || "significant"}`}
          />
          <DockSummaryPill
            label="Pastime"
            value={selectedPastime?.title || "No selection"}
            detail={selectedPastime?.matched_candidate?.title || topCandidate?.title || `${registeredPastimeCount} registered`}
          />
          <DockSummaryPill label="Context" value={contextGuard.value} detail={contextGuard.detail} tone={tokenClass} />
          <DockSummaryPill
            label="Model"
            value={session?.model || "no-model"}
            detail={session?.provider || "idle"}
          />
        </div>

        <button
          className="dock-expand-btn"
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onToggle(!expanded);
          }}
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse runtime dock" : "Expand runtime dock"}
        >
          {expanded ? "▾" : "▴"}
        </button>
      </div>

      {expanded && (
        <div className="dock-expanded">
          <div className="dock-expanded-grid">
            <div className="dock-expanded-section dock-panel dock-panel-wide">
              <div className="dock-expanded-title">Session Snapshot</div>
              <div className="dock-expanded-list">
                <div className="dock-expanded-item">
                  <span>💬</span>
                  <span className="dock-expanded-item-name">{session?.label || "No active session"}</span>
                  <span className="dock-expanded-item-meta">{session?.provider || "idle"}</span>
                </div>
                <div className="dock-expanded-item">
                  <span>📋</span>
                  <span className="dock-expanded-item-name">{plan?.next_item?.content || plan?.active_goal || "No active plan item"}</span>
                  <span className="dock-expanded-item-meta">{plan?.next_item?.status || plan?.status || "n/a"}</span>
                </div>
                <div className="dock-expanded-item">
                  <span>▶</span>
                  <span className="dock-expanded-item-name">{activeRun ? activeRun.status : isExecuting ? "running" : "idle"}</span>
                  <span className="dock-expanded-item-meta">{queuedCount} queued runs</span>
                </div>
              </div>
            </div>

            <div className="dock-expanded-section dock-panel">
              <div className="dock-expanded-title">Workspace State</div>
              <div className="dock-expanded-list">
                <div className="dock-expanded-item">
                  <span>🛰</span>
                  <span className="dock-expanded-item-name">{topCandidate?.title || "No active workspace candidate"}</span>
                  <span className="dock-expanded-item-meta">{topCandidate ? `${topCandidate.priority}/${topCandidate.urgency}` : "idle"}</span>
                </div>
                <div className="dock-expanded-item">
                  <span>🧭</span>
                  <span className="dock-expanded-item-name">{activeWorkerCount} active workers</span>
                  <span className="dock-expanded-item-meta">{openSignals.length} inbox</span>
                </div>
                <div className="dock-expanded-item">
                  <span>🎚</span>
                  <span className="dock-expanded-item-name">{formatAttentionMode(attentionProfile?.mode || "warm")}</span>
                  <span className="dock-expanded-item-meta">
                    {attentionProfile?.current_attention_level || 0}% · {attentionProfile?.notification_threshold || "significant"}
                  </span>
                </div>
                <div className="dock-expanded-item">
                  <span>🎛</span>
                  <span className="dock-expanded-item-name">{selectedPastime?.title || "No selected pastime"}</span>
                  <span className="dock-expanded-item-meta">{selectedPastimeMeta}</span>
                </div>
              </div>
            </div>

            <div className="dock-expanded-section dock-panel">
              <div className="dock-expanded-title">Context Guard</div>
              <div className="dock-expanded-list">
                <div className="dock-expanded-item">
                  <span>🧠</span>
                  <span className="dock-expanded-item-name">Effective tokens</span>
                  <span className="dock-expanded-item-meta">{contextGuard.tokensUsed}</span>
                </div>
                <div className="dock-expanded-item">
                  <span>⚠</span>
                  <span className="dock-expanded-item-name">Rollover threshold</span>
                  <span className="dock-expanded-item-meta">{contextGuard.threshold}</span>
                </div>
                <div className="dock-expanded-item">
                  <span>💾</span>
                  <span className="dock-expanded-item-name">Raw transcript tokens</span>
                  <span className="dock-expanded-item-meta">{contextGuard.rawTokens}</span>
                </div>
              </div>
            </div>

            <div className="dock-expanded-section dock-panel">
              <div className="dock-expanded-title">Workspace Inbox</div>
              {inboxSignals.length > 0 ? (
                <div className="dock-expanded-list">
                  {inboxSignals.map((signal) => {
                    const worker = workerRecords.find((item) => item.name === signal.worker_name);
                    return (
                      <div key={signal.id} className="dock-expanded-item dock-expanded-item-rich">
                        <span>📨</span>
                        <div className="dock-expanded-item-body">
                          <span className="dock-expanded-item-name">{signal.title}</span>
                          <span className="dock-expanded-item-summary">{signal.summary}</span>
                        </div>
                        {onApplySignalAction && signal.status === "open" ? (
                          <div className="dock-inline-actions">
                            {getSignalActions(signal).map((action) => (
                              <button
                                key={action.value}
                                className="dock-inline-action"
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void onApplySignalAction(signal, action.value);
                                }}
                              >
                                {action.label}
                              </button>
                            ))}
                          </div>
                        ) : null}
                        <span className="dock-expanded-item-meta">{formatSignalMeta(worker?.label || signal.signal_type, signal)}</span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="dock-expanded-item">
                  <span>…</span>
                  <span className="dock-expanded-item-name">No open workspace inbox items</span>
                  <span className="dock-expanded-item-meta">idle</span>
                </div>
              )}
            </div>

            <div className="dock-expanded-section dock-panel">
              <div className="dock-expanded-title">Candidate Queue</div>
              {queueCandidates.length > 0 ? (
                <div className="dock-expanded-list">
                  {queueCandidates.slice(0, 3).map((candidate) => (
                    <div key={candidate.id} className="dock-expanded-item">
                      <span>🗂</span>
                      <span className="dock-expanded-item-name">{candidate.title}</span>
                      <span className="dock-expanded-item-meta">{candidate.type}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="dock-expanded-item">
                  <span>…</span>
                  <span className="dock-expanded-item-name">No workspace queue candidates</span>
                  <span className="dock-expanded-item-meta">idle</span>
                </div>
              )}
            </div>

            <div className="dock-expanded-section dock-panel">
              <div className="dock-expanded-title">Worker Registry</div>
              <div className="dock-expanded-list">
                {workerRecords.slice(0, 3).map((worker) => (
                  <div key={worker.name} className="dock-expanded-item dock-expanded-item-rich">
                    <span>{worker.status === "attention" ? "⚠" : worker.status === "queued" ? "🧩" : "○"}</span>
                    <div className="dock-expanded-item-body">
                      <span className="dock-expanded-item-name">{worker.label}</span>
                      <span className="dock-expanded-item-summary">{worker.description}</span>
                    </div>
                    <span className="dock-expanded-item-meta">{formatWorkerLoad(worker.open_signal_count, worker.queue_count)}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="dock-expanded-section dock-panel dock-panel-wide">
              <div className="dock-expanded-title">Recent Events</div>
              {recentEvents.length > 0 ? (
                recentEvents.map((event) => (
                  <div key={event.id} className="dock-event-item">
                    <span className="dock-event-time">{formatTime(event.created_at)}</span>
                    <span className="dock-event-text">{describeEvent(event)}</span>
                  </div>
                ))
              ) : (
                <div className="dock-expanded-item">
                  <span>…</span>
                  <span className="dock-expanded-item-name">No runtime events yet</span>
                  <span className="dock-expanded-item-meta">waiting</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DockSummaryPill({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "low" | "medium" | "high";
}) {
  return (
    <div className={`dock-summary-pill ${tone ? `tone-${tone}` : ""}`}>
      <span className="dock-section-label">{label}</span>
      <span className="dock-summary-value" title={value}>{value}</span>
      <span className="dock-summary-detail" title={detail}>{detail}</span>
    </div>
  );
}

function buildTokenPercent(plan: PlanRecord | null): number {
  const tokensUsed = Number(plan?.context_guard?.tokens_used || 0);
  const threshold = Number(plan?.context_guard?.rollover_threshold || 0);
  if (threshold <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round((tokensUsed / threshold) * 100)));
}

function buildContextGuardSummary(plan: PlanRecord | null): {
  value: string;
  detail: string;
  tokensUsed: number;
  threshold: number;
  rawTokens: number;
} {
  const tokensUsed = Number(plan?.context_guard?.tokens_used || 0);
  const threshold = Number(plan?.context_guard?.rollover_threshold || 0);
  const rawTokens = Number(plan?.context_guard?.raw_tokens_used || 0);
  if (threshold <= 0) {
    return {
      value: "n/a",
      detail: "No guard configured",
      tokensUsed,
      threshold,
      rawTokens,
    };
  }
  const tokenPercent = Math.max(0, Math.min(100, Math.round((tokensUsed / threshold) * 100)));
  return {
    value: `${tokenPercent}%`,
    detail: `${tokensUsed}/${threshold}`,
    tokensUsed,
    threshold,
    rawTokens,
  };
}

function describeSelectedPastime(selectedPastime: RuntimeDockProps["workspaceRuntime"]["selected_pastime"], registeredPastimeCount: number): string {
  if (!selectedPastime) {
    return `${registeredPastimeCount} registered`;
  }
  const matchedType = selectedPastime.matched_candidate?.type || selectedPastime.candidate_type || "pending match";
  return `${selectedPastime.pastime_type} · ${matchedType}`;
}

function formatAttentionMode(mode: string): string {
  return mode ? `${mode.charAt(0).toUpperCase()}${mode.slice(1)}` : "Warm";
}

function describeEvent(event: SessionEventRecord): string {
  if (event.type === "run_queued") {
    return `Run queued${event.data.turn_number ? ` for turn ${event.data.turn_number}` : ""}`;
  }
  if (event.type === "run_started") {
    return "Run started";
  }
  if (event.type === "run_completed") {
    return "Run completed";
  }
  if (event.type === "run_failed") {
    return `Run failed${event.data.error ? `: ${String(event.data.error)}` : ""}`;
  }
  if (event.type === "context_warning") {
    return "Context warning recorded";
  }
  if (event.type === "plan_activated") {
    return "Plan activated";
  }
  if (event.type === "scheduler_event") {
    return "Workspace scheduler selected a candidate";
  }
  if (event.type === "worker_signal") {
    return "Workspace worker emitted a signal";
  }
  return event.type.replaceAll("_", " ");
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function formatWorkerLoad(openSignalCount: number, queueCount: number): string {
  return `${openSignalCount} open / ${queueCount} queued`;
}

function getSignalActions(signal: WorkspaceSignalRecord): Array<{ label: string; value: string }> {
  const actions: Array<{ label: string; value: string }> = [];
  if (signal.signal_type === "backlog_review_needed") {
    actions.push({ label: "Start", value: "start_lead_item" });
    actions.push({ label: "Defer", value: "defer_lead_item" });
  } else if (signal.signal_type === "context_review_needed") {
    actions.push({ label: "Pause", value: "pause_plan" });
  } else if (signal.signal_type === "handoff_ready") {
    actions.push({ label: "Resume", value: "resume_plan" });
  }
  actions.push({ label: "Buddy", value: "escalate_to_buddy" });
  actions.push({ label: "CLO", value: "escalate_to_clo" });
  return actions;
}

function formatSignalMeta(workerLabel: string, signal: WorkspaceSignalRecord): string {
  if (signal.status === "escalated") {
    const target = typeof signal.metadata.escalation_target === "string" ? signal.metadata.escalation_target : "unknown";
    return `${workerLabel} -> ${target}`;
  }
  return workerLabel;
}
