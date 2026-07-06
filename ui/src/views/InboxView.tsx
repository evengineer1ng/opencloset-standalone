import { useState } from "react";
import type { PlanProposalRecord, WorkspaceSignalRecord } from "../api/types";
import "./InboxView.css";

type FilterType = "all" | "signals" | "pending-proposals" | "resolved-proposals";

interface InboxViewProps {
  signals: WorkspaceSignalRecord[];
  proposals: PlanProposalRecord[];
  sessionLabel: string | null;
  onInspectPlan: () => void;
  onApplySignalAction: (signalId: string, action: string, sessionId?: string | null) => Promise<void>;
}

type InboxEntry = {
  id: string;
  kind: "signal" | "proposal";
  title: string;
  summary: string;
  status: string;
  source: string;
  createdAt: string;
  secondaryLabel: string;
};

export default function InboxView({ signals, proposals, sessionLabel, onInspectPlan, onApplySignalAction }: InboxViewProps) {
  const [filter, setFilter] = useState<FilterType>("all");
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const entries: InboxEntry[] = [
    ...signals.map((signal) => ({
      id: signal.id,
      kind: "signal" as const,
      title: signal.title,
      summary: signal.summary,
      status: signal.status,
      source: signal.worker_name,
      createdAt: signal.updated_at,
      secondaryLabel: signal.signal_type,
    })),
    ...proposals.map((proposal) => ({
      id: proposal.id,
      kind: "proposal" as const,
      title: proposal.summary || humanizeType(proposal.proposal_type),
      summary: summarizePayload(proposal),
      status: proposal.status,
      source: proposal.proposed_by,
      createdAt: proposal.updated_at,
      secondaryLabel: proposal.proposal_type,
    })),
  ].sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt));

  const filtered = entries.filter((entry) => {
    if (filter === "signals") {
      return entry.kind === "signal";
    }
    if (filter === "pending-proposals") {
      return entry.kind === "proposal" && entry.status === "pending";
    }
    if (filter === "resolved-proposals") {
      return entry.kind === "proposal" && entry.status !== "pending";
    }
    return true;
  });

  return (
    <div className="inbox-view">
      <div className="inbox-header">
        <div>
          <div className="inbox-title">Workspace inbox</div>
          <div className="inbox-subtitle">{sessionLabel || "Active session"} · live signals and plan review items</div>
        </div>
        <div className="inbox-count">
          {proposals.filter((proposal) => proposal.status === "pending").length} pending proposals · {signals.length} signals
        </div>
      </div>

      <div className="inbox-filters">
        {([
          ["all", "all"],
          ["signals", "signals"],
          ["pending-proposals", "pending proposals"],
          ["resolved-proposals", "resolved proposals"],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            className={`inbox-filter-btn ${filter === value ? "active" : ""}`}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="inbox-list">
        {filtered.map((entry) => (
          <InboxCard
            key={entry.id}
            entry={entry}
            signals={signals}
            onInspectPlan={onInspectPlan}
            pendingAction={pendingAction}
            onRunSignalAction={async (signalId, action, sessionId) => {
              setPendingAction(`${signalId}:${action}`);
              try {
                await onApplySignalAction(signalId, action, sessionId);
              } finally {
                setPendingAction(null);
              }
            }}
          />
        ))}
        {!filtered.length && <div className="inbox-empty">No items in this inbox slice.</div>}
      </div>
    </div>
  );
}

function InboxCard({
  entry,
  signals,
  onInspectPlan,
  pendingAction,
  onRunSignalAction,
}: {
  entry: InboxEntry;
  signals: WorkspaceSignalRecord[];
  onInspectPlan: () => void;
  pendingAction: string | null;
  onRunSignalAction: (signalId: string, action: string, sessionId?: string | null) => Promise<void>;
}) {
  const signal = entry.kind === "signal" ? signals.find((candidate) => candidate.id === entry.id) || null : null;
  const actions = signal ? getSignalActions(signal) : [];

  return (
    <div className="inbox-card">
      <div className="inbox-card-header">
        <div className="inbox-card-title">{entry.title}</div>
        <div className="inbox-card-meta">
          <span className={`badge badge-${badgeTone(entry)}`}>{entry.status}</span>
        </div>
      </div>

      <div className="inbox-card-summary">{entry.summary}</div>

      <div className="inbox-card-kicker">
        {entry.kind} · {entry.source} · {entry.secondaryLabel} · {formatDate(entry.createdAt)}
      </div>

      <div className="inbox-card-actions">
        {entry.kind === "proposal" && (
          <button className="inbox-action-btn" type="button" onClick={onInspectPlan}>
            Inspect plan
          </button>
        )}
        {actions.map((action) => (
          <button
            key={action.value}
            className={`inbox-action-btn ${action.tone || ""}`}
            type="button"
            disabled={pendingAction === `${entry.id}:${action.value}`}
            onClick={() => void onRunSignalAction(entry.id, action.value, signal?.session_id)}
          >
            {pendingAction === `${entry.id}:${action.value}` ? "Applying..." : action.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function getSignalActions(signal: WorkspaceSignalRecord): Array<{ value: string; label: string; tone?: string }> {
  if (signal.status !== "open") {
    return [];
  }
  switch (signal.signal_type) {
    case "backlog_review_needed":
      return [
        { value: "start_lead_item", label: "Start lead", tone: "approve" },
        { value: "defer_lead_item", label: "Defer lead" },
        { value: "escalate_to_buddy", label: "Escalate buddy" },
      ];
    case "context_review_needed":
      return [
        { value: "pause_plan", label: "Pause plan", tone: "reject" },
        { value: "escalate_to_clo", label: "Escalate Clo" },
      ];
    case "handoff_ready":
      return [
        { value: "resume_plan", label: "Resume plan", tone: "approve" },
        { value: "escalate_to_clo", label: "Escalate Clo" },
      ];
    default:
      return [];
  }
}

function badgeTone(entry: InboxEntry): string {
  if (entry.kind === "signal") {
    return entry.status === "open" ? "pending" : entry.status === "resolved" ? "success" : "running";
  }
  return entry.status === "pending" ? "pending" : entry.status === "accepted" ? "success" : "error";
}

function summarizePayload(proposal: PlanProposalRecord): string {
  const payloadKeys = Object.keys(proposal.payload || {});
  if (!payloadKeys.length) {
    return "No structured payload attached.";
  }
  return `Payload keys: ${payloadKeys.join(", ")}`;
}

function humanizeType(value: string): string {
  return value.replace(/[_-]/g, " ");
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
