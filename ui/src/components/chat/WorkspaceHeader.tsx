import type { SessionDetail } from "../../api/types";
import "./WorkspaceHeader.css";

interface WorkspaceHeaderProps {
  workspaceName: string;
  buildProjectName?: string | null;
  workspaceStatus?: string | null;
  buildProjectStatus?: string | null;
  session: SessionDetail;
  onRefresh: () => void;
  onResumeRun: () => void;
  onInterruptRun: () => void;
  onRerunLastTurn: () => void;
  canResumeRun: boolean;
  canInterruptRun: boolean;
  canRerunLastTurn: boolean;
  isExecuting?: boolean;
  isRefreshing?: boolean;
}

export default function WorkspaceHeader({
  workspaceName,
  buildProjectName,
  workspaceStatus,
  buildProjectStatus,
  session,
  onRefresh,
  onResumeRun,
  onInterruptRun,
  onRerunLastTurn,
  canResumeRun,
  canInterruptRun,
  canRerunLastTurn,
  isExecuting = false,
  isRefreshing = false,
}: WorkspaceHeaderProps) {
  const runStatus = session.current_run?.status || "idle";
  const runLabel = session.current_run ? `turn ${session.current_run.turn_number}` : "ready";
  const tokenLabel = `${session.token_count}/${session.context_window}`;
  const interruptLabel = runStatus === "running" ? "Interrupt Run" : "Interrupt Queue";

  return (
    <div className="workspace-header">
      <div className="workspace-header-left">
        <span className="ws-icon">🏗</span>
        <div className="ws-context-stack">
          <div className="ws-breadcrumbs">
            <span className="ws-name">{workspaceName}</span>
            <span className="ws-sep">→</span>
            <span className="ws-project">{buildProjectName || "No Build Project"}</span>
            <span className="ws-sep">→</span>
            <span className="ws-session">{session.label || "Untitled Session"}</span>
          </div>
          <div className="ws-subline">
            <span className="ws-status-chip">workspace {workspaceStatus || "active"}</span>
            <span className="ws-status-chip">project {buildProjectStatus || "none"}</span>
            <span className="ws-dot">•</span>
            <span>{session.provider}</span>
            <span className="ws-dot">•</span>
            <span>{session.model}</span>
            <span className="ws-dot">•</span>
            <span>{session.message_count} msgs</span>
          </div>
        </div>
      </div>
      <div className="workspace-header-right">
        <span className={`badge ${runStatus === "running" ? "badge-running" : runStatus === "queued" ? "badge-pending" : "badge-idle"}`}>
          {runStatus}
        </span>
        <span className="ws-stat">{runLabel}</span>
        <span className="ws-stat">context {tokenLabel}</span>
        <div className="ws-action-group">
          <button className="ws-refresh-btn" type="button" onClick={onResumeRun} disabled={!canResumeRun || isExecuting}>
            Resume
          </button>
          <button className="ws-refresh-btn" type="button" onClick={onInterruptRun} disabled={!canInterruptRun || isExecuting}>
            {interruptLabel}
          </button>
          <button className="ws-refresh-btn" type="button" onClick={onRerunLastTurn} disabled={!canRerunLastTurn || isExecuting}>
            Rerun Last Turn
          </button>
        </div>
        <button className="ws-refresh-btn" type="button" onClick={onRefresh} disabled={isRefreshing}>
          {isRefreshing ? "Refreshing..." : "Refresh"}
        </button>
      </div>
    </div>
  );
}
