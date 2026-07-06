import type { PlanRecord, SessionDetail, SessionEventRecord, WorkspaceRuntimeRecord } from "../../api/types";
import "./WorkspaceDashboard.css";

interface WorkspaceDashboardProps {
  session: SessionDetail | null;
  plan: PlanRecord | null;
  events: SessionEventRecord[];
  workspaceRuntime: WorkspaceRuntimeRecord | null;
  pendingProposalCount: number;
  onOpenInbox: () => void;
  onOpenBriefing: () => void;
  onOpenEvidence: () => void;
}

export default function WorkspaceDashboard({
  session,
  plan,
  events,
  workspaceRuntime,
  pendingProposalCount,
  onOpenInbox,
  onOpenBriefing,
  onOpenEvidence,
}: WorkspaceDashboardProps) {
  const openSignals = workspaceRuntime?.signals || [];
  const queueCandidates = workspaceRuntime?.candidates || [];
  const enabledPastimes = (workspaceRuntime?.pastimes || []).filter((pastime) => pastime.status === "enabled");
  const selectedPastime = workspaceRuntime?.selected_pastime || null;
  const recentEvents = [...events].slice(-5).reverse();
  const contextGuard = buildContextGuardSummary(plan);

  return (
    <div className="workspace-dashboard">
      <div className="workspace-dashboard-header">
        <div>
          <div className="workspace-dashboard-eyebrow">OpenCloset Overview</div>
          <div className="workspace-dashboard-title">Workspace operating picture</div>
        </div>
        <div className="workspace-dashboard-header-actions">
          <div className="workspace-dashboard-meta">
            {session?.label || "No active session"} · {session?.status || "idle"}
          </div>
          <div className="workspace-dashboard-actions">
            <button className="workspace-dashboard-action" type="button" onClick={onOpenInbox}>
              Open inbox
            </button>
            <button className="workspace-dashboard-action" type="button" onClick={onOpenBriefing}>
              Open briefing
            </button>
            <button className="workspace-dashboard-action" type="button" onClick={onOpenEvidence}>
              Open evidence
            </button>
          </div>
        </div>
      </div>

      <div className="workspace-dashboard-grid">
        <DashboardStatCard label="Pending plan proposals" value={String(pendingProposalCount)} accent="proposal" onClick={onOpenInbox} />
        <DashboardStatCard label="Open workspace signals" value={String(openSignals.length)} accent="signal" onClick={onOpenInbox} />
        <DashboardStatCard label="Queued candidates" value={String(queueCandidates.length)} accent="queue" onClick={onOpenInbox} />
        <DashboardStatCard label="Context guard" value={contextGuard.value} accent="plan" onClick={onOpenBriefing} />
      </div>

      <div className="workspace-dashboard-columns">
        <section className="workspace-dashboard-panel workspace-dashboard-panel-wide">
          <div className="workspace-dashboard-panel-title">Current focus</div>
          <div className="workspace-dashboard-focus-title">{plan?.next_item?.content || plan?.active_goal || "No active plan focus"}</div>
          <div className="workspace-dashboard-focus-meta">
            {plan?.title || "No active stored plan"} · {plan?.plan_status || plan?.status || "idle"}
          </div>
          <div className="workspace-dashboard-item">
            <div className="workspace-dashboard-item-title">{selectedPastime?.title || "No pastime selected"}</div>
            <div className="workspace-dashboard-item-summary">
              {selectedPastime?.selection_reason || "Enabled pastimes match against current workspace candidates before ambient work is emitted."}
            </div>
            <div className="workspace-dashboard-item-meta">
              {selectedPastime
                ? `${selectedPastime.pastime_type} · ${selectedPastime.source_kind}`
                : `${enabledPastimes.length} enabled in registry`}
            </div>
          </div>
          <div className="workspace-dashboard-item compact">
            <div className="workspace-dashboard-item-title">Context guard</div>
            <div className="workspace-dashboard-item-summary">{contextGuard.detail}</div>
            <div className="workspace-dashboard-item-meta">raw {contextGuard.rawTokens}</div>
          </div>
        </section>

        <section className="workspace-dashboard-panel">
          <div className="workspace-dashboard-panel-title">Workspace inbox</div>
          <div className="workspace-dashboard-list">
            {openSignals.length ? (
              openSignals.slice(0, 4).map((signal) => (
                <div key={signal.id} className="workspace-dashboard-item">
                  <div className="workspace-dashboard-item-title">{signal.title}</div>
                  <div className="workspace-dashboard-item-summary">{signal.summary}</div>
                  <div className="workspace-dashboard-item-meta">{signal.worker_name} · {signal.status}</div>
                </div>
              ))
            ) : (
              <div className="workspace-dashboard-empty">No open workspace signals.</div>
            )}
          </div>
        </section>

        <section className="workspace-dashboard-panel">
          <div className="workspace-dashboard-panel-title">Workspace queue</div>
          <div className="workspace-dashboard-list">
            {queueCandidates.length ? (
              queueCandidates.slice(0, 4).map((candidate) => (
                <div key={candidate.id} className="workspace-dashboard-item compact">
                  <div className="workspace-dashboard-item-title">{candidate.title}</div>
                  <div className="workspace-dashboard-item-summary">{candidate.summary}</div>
                  <div className="workspace-dashboard-item-meta">{candidate.type} · {candidate.priority}/{candidate.urgency}</div>
                </div>
              ))
            ) : (
              <div className="workspace-dashboard-empty">No queued workspace candidates.</div>
            )}
          </div>
        </section>

        <section className="workspace-dashboard-panel">
          <div className="workspace-dashboard-panel-title">Recent session events</div>
          <div className="workspace-dashboard-list">
            {recentEvents.length ? (
              recentEvents.map((event) => (
                <div key={event.id} className="workspace-dashboard-item compact">
                  <div className="workspace-dashboard-item-title">{humanizeEventType(event.type)}</div>
                  <div className="workspace-dashboard-item-meta">{formatTime(event.created_at)}</div>
                </div>
              ))
            ) : (
              <div className="workspace-dashboard-empty">No recent session events.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function DashboardStatCard({
  label,
  value,
  accent,
  onClick,
}: {
  label: string;
  value: string;
  accent: string;
  onClick?: () => void;
}) {
  const Element = onClick ? "button" : "div";

  return (
    <Element className={`workspace-dashboard-stat ${accent} ${onClick ? "clickable" : ""}`} {...(onClick ? { type: "button", onClick } : {})}>
      <div className="workspace-dashboard-stat-label">{label}</div>
      <div className="workspace-dashboard-stat-value">{value}</div>
    </Element>
  );
}

function humanizeEventType(eventType: string): string {
  return eventType.replace(/[_\.]/g, " ");
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function buildContextGuardSummary(plan: PlanRecord | null): { value: string; detail: string; rawTokens: number } {
  const tokensUsed = Number(plan?.context_guard?.tokens_used || 0);
  const threshold = Number(plan?.context_guard?.rollover_threshold || 0);
  const rawTokens = Number(plan?.context_guard?.raw_tokens_used || 0);
  if (threshold <= 0) {
    return {
      value: "n/a",
      detail: "No threshold configured",
      rawTokens,
    };
  }
  const tokenPercent = Math.max(0, Math.min(100, Math.round((tokensUsed / threshold) * 100)));
  return {
    value: `${tokenPercent}%`,
    detail: `${tokensUsed}/${threshold} effective tokens`,
    rawTokens,
  };
}
