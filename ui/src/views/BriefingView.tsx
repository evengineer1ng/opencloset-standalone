import type {
  PlanProposalRecord,
  SessionEventRecord,
  WorkspaceRuntimeCandidateRecord,
  WorkspaceSignalRecord,
} from "../api/types";
import "./BriefingView.css";

interface BriefingViewProps {
  sessionLabel: string | null;
  proposals: PlanProposalRecord[];
  events: SessionEventRecord[];
  signals: WorkspaceSignalRecord[];
  candidates: WorkspaceRuntimeCandidateRecord[];
  onInspectPlan: () => void;
  onOpenInbox: () => void;
}

type BriefingItem = {
  id: string;
  title: string;
  summary: string;
  significance: "high" | "medium" | "low";
  type: "completed" | "changed" | "queued" | "signal" | "proposal";
  timestamp: string;
  action: "inspect-plan" | "open-inbox";
};

export default function BriefingView({ sessionLabel, proposals, events, signals, candidates, onInspectPlan, onOpenInbox }: BriefingViewProps) {
  const items: BriefingItem[] = [
    ...signals.slice(0, 4).map((signal) => ({
      id: signal.id,
      title: signal.title,
      summary: signal.summary,
      significance: signal.priority >= 75 ? "high" : signal.priority >= 40 ? "medium" : "low",
      type: "signal" as const,
      timestamp: signal.updated_at,
      action: "open-inbox" as const,
    })),
    ...proposals.slice(0, 4).map((proposal) => ({
      id: proposal.id,
      title: proposal.summary || humanizeLabel(proposal.proposal_type),
      summary: `${proposal.proposed_by} proposed ${humanizeLabel(proposal.proposal_type)} (${proposal.status})`,
      significance: proposal.status === "pending" ? "high" : "low",
      type: "proposal" as const,
      timestamp: proposal.updated_at,
      action: "inspect-plan" as const,
    })),
    ...candidates.slice(0, 4).map((candidate) => ({
      id: candidate.id,
      title: candidate.title,
      summary: candidate.summary,
      significance: candidate.urgency >= 75 ? "high" : candidate.urgency >= 40 ? "medium" : "low",
      type: "queued" as const,
      timestamp: candidate.expires_at || new Date().toISOString(),
      action: "inspect-plan" as const,
    })),
    ...events.slice(-6).map((event) => ({
      id: event.id,
      title: humanizeLabel(event.type),
      summary: summarizeEvent(event),
      significance: event.type.includes("error") ? "high" : event.type.includes("plan") ? "medium" : "low",
      type: event.type.includes("complete") ? "completed" as const : "changed" as const,
      timestamp: event.created_at,
      action: event.type.includes("signal") ? "open-inbox" as const : "inspect-plan" as const,
    })),
  ]
    .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp))
    .slice(0, 10);

  return (
    <div className="briefing-view">
      <div className="briefing-header">
        <div className="briefing-title">While You Were Away</div>
        <div className="briefing-subtitle">
          {sessionLabel || "Active session"} · {items.filter((item) => item.significance === "high").length} important · {items.filter((item) => item.significance === "medium").length} notable · {items.filter((item) => item.significance === "low").length} routine
        </div>
        <div className="briefing-time-range">
          Live workspace briefing · {new Date().toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
        </div>
      </div>

      <div className="briefing-list">
        {items.map((item) => (
          <BriefingCard key={item.id} item={item} onInspectPlan={onInspectPlan} onOpenInbox={onOpenInbox} />
        ))}
        {!items.length && <div className="briefing-empty">No recent workspace changes to summarize.</div>}
      </div>
    </div>
  );
}

function BriefingCard({
  item,
  onInspectPlan,
  onOpenInbox,
}: {
  item: BriefingItem;
  onInspectPlan: () => void;
  onOpenInbox: () => void;
}) {
  return (
    <div className={`briefing-card ${item.significance}`}>
      <div className="briefing-icon">{typeIcon(item.type)}</div>
      <div className="briefing-content">
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span className="briefing-card-title">{item.title}</span>
          <span className={`briefing-type-badge ${item.type}`}>{item.type}</span>
        </div>
        <div className="briefing-card-summary">{item.summary}</div>
        <div className="briefing-card-time">{formatTime(item.timestamp)}</div>
        <div className="briefing-card-actions">
          <button className="inbox-action-btn" type="button" onClick={item.action === "open-inbox" ? onOpenInbox : onInspectPlan}>
            {item.action === "open-inbox" ? "Open inbox" : "Inspect plan"}
          </button>
        </div>
      </div>
    </div>
  );
}

function summarizeEvent(event: SessionEventRecord): string {
  const summary = typeof event.data.summary === "string" ? event.data.summary : null;
  if (summary) {
    return summary;
  }
  const keys = Object.keys(event.data || {});
  return keys.length ? `Updated fields: ${keys.slice(0, 4).join(", ")}` : "Session activity recorded.";
}

function humanizeLabel(value: string): string {
  return value.replace(/[_\.\-]/g, " ");
}

function typeIcon(type: string): string {
  switch (type) {
    case "completed":
      return "✅";
    case "changed":
      return "🔄";
    case "queued":
      return "📋";
    case "signal":
      return "📡";
    case "proposal":
      return "💡";
    default:
      return "📌";
  }
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}
