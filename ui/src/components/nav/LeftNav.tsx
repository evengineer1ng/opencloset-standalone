import type { BuildProjectRecord, SessionSummary, WorkspaceSummary } from "../../api/types";
import type { CenterView } from "../../layout/DesktopShell";
import "./LeftNav.css";

interface LeftNavProps {
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string | null;
  buildProjects: BuildProjectRecord[];
  activeBuildProjectId: string | null;
  activeSessionId: string | null;
  centerView: CenterView;
  pendingProposalCount: number;
  sessions: SessionSummary[];
  loadingWorkspaces: boolean;
  loadingProjects: boolean;
  onWorkspaceSelect: (workspaceId: string) => void;
  onBuildProjectSelect: (projectId: string | null) => void;
  onSessionSelect: (sessionId: string) => void;
  onCenterViewSelect: (view: CenterView) => void;
  onCreateWorkspace: () => void;
  creatingWorkspace: boolean;
  onDeleteWorkspace: (workspaceId: string) => void;
  onCreateBuildProject: () => void;
  creatingBuildProject: boolean;
  onDeleteBuildProject: (projectId: string) => void;
  onCreateSession: () => void;
  creatingSession: boolean;
  onDeleteSession: (sessionId: string) => void;
}

export default function LeftNav({
  workspaces,
  activeWorkspaceId,
  buildProjects,
  activeBuildProjectId,
  activeSessionId,
  centerView,
  pendingProposalCount,
  sessions,
  loadingWorkspaces,
  loadingProjects,
  onWorkspaceSelect,
  onBuildProjectSelect,
  onSessionSelect,
  onCenterViewSelect,
  onCreateWorkspace,
  creatingWorkspace,
  onDeleteWorkspace,
  onCreateBuildProject,
  creatingBuildProject,
  onDeleteBuildProject,
  onCreateSession,
  creatingSession,
  onDeleteSession,
}: LeftNavProps) {
  const visibleSessions = sessions.filter((session) => {
    if (activeWorkspaceId && session.workspace_id !== activeWorkspaceId) {
      return false;
    }
    if (activeBuildProjectId) {
      return session.build_project_id === activeBuildProjectId;
    }
    return true;
  });

  return (
    <div className="left-nav">
      <section className="nav-section">
        <div className="nav-section-title nav-section-title-row">
          <span>Workspaces</span>
          <button className="nav-section-action" type="button" onClick={onCreateWorkspace} disabled={creatingWorkspace}>
            {creatingWorkspace ? "creating" : "new"}
          </button>
        </div>
        {workspaces.map((workspace) => (
          <div key={workspace.id} className="nav-item-row">
            <button
              className={`nav-item workspace-item ${activeWorkspaceId === workspace.id ? "active" : ""}`}
              onClick={() => onWorkspaceSelect(workspace.id)}
            >
              <span className="nav-item-icon">🏗</span>
              <span className="nav-item-label">{workspace.name}</span>
              <span className={`nav-item-status ${workspace.status === "archived" ? "archived" : "active"}`} />
            </button>
            <button
              className="nav-item-delete"
              type="button"
              aria-label={`Delete workspace ${workspace.name}`}
              title={`Delete workspace ${workspace.name}`}
              onClick={() => onDeleteWorkspace(workspace.id)}
            >
              ×
            </button>
          </div>
        ))}
        {loadingWorkspaces && (
          <div className="nav-item" style={{ cursor: "default", opacity: 0.8 }}>
            <span className="nav-item-icon">⋯</span>
            <span className="nav-item-label">Loading workspaces</span>
          </div>
        )}
        {!loadingWorkspaces && workspaces.length === 0 && (
          <div className="nav-item" style={{ cursor: "default", opacity: 0.8 }}>
            <span className="nav-item-icon">…</span>
            <span className="nav-item-label">No workspaces yet</span>
          </div>
        )}
      </section>

      <section className="nav-section">
        <div className="nav-section-title nav-section-title-row">
          <span>Build Projects</span>
          <button
            className="nav-section-action"
            type="button"
            onClick={onCreateBuildProject}
            disabled={!activeWorkspaceId || creatingBuildProject}
          >
            {creatingBuildProject ? "creating" : "new"}
          </button>
        </div>
        <button
          className={`nav-item build-project-item ${activeBuildProjectId === null ? "active" : ""}`}
          onClick={() => onBuildProjectSelect(null)}
        >
          <span className="nav-item-icon">◦</span>
          <span className="nav-item-label">Workspace only</span>
        </button>
        {buildProjects.map((project) => (
          <div key={project.id} className="nav-item-row">
            <button
              className={`nav-item build-project-item ${activeBuildProjectId === project.id ? "active" : ""}`}
              onClick={() => onBuildProjectSelect(project.id)}
            >
              <span className="nav-item-icon">▸</span>
              <span className="nav-item-label">{project.name}</span>
              <span className={`nav-item-status ${project.status === "archived" ? "archived" : "active"}`} />
            </button>
            <button
              className="nav-item-delete"
              type="button"
              aria-label={`Delete build project ${project.name}`}
              title={`Delete build project ${project.name}`}
              onClick={() => onDeleteBuildProject(project.id)}
            >
              ×
            </button>
          </div>
        ))}
        {loadingProjects && (
          <div className="nav-item build-project-item" style={{ cursor: "default", opacity: 0.8 }}>
            <span className="nav-item-icon">⋯</span>
            <span className="nav-item-label">Loading projects</span>
          </div>
        )}
        {!loadingProjects && buildProjects.length === 0 && activeWorkspaceId && (
          <div className="nav-item build-project-item" style={{ cursor: "default", opacity: 0.8 }}>
            <span className="nav-item-icon">…</span>
            <span className="nav-item-label">No build projects yet</span>
          </div>
        )}
      </section>

      <section className="nav-section">
        <div className="nav-section-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>Sessions</span>
          <button
            className="badge badge-running"
            onClick={onCreateSession}
            disabled={creatingSession}
            style={{ border: "none", cursor: creatingSession ? "default" : "pointer" }}
          >
            {creatingSession ? "creating" : "new"}
          </button>
        </div>
        {visibleSessions.map((sess) => (
          <div key={sess.id} className="nav-item-row">
            <button
              className={`nav-item session-item ${activeSessionId === sess.id ? "active" : ""}`}
              onClick={() => onSessionSelect(sess.id)}
            >
              <span className="nav-item-icon">💬</span>
              <span className="nav-item-label">{sess.label || "Untitled Session"}</span>
              <span className="session-time">{formatTime(sess.created_at)}</span>
            </button>
            <button
              className="nav-item-delete"
              type="button"
              aria-label={`Delete session ${sess.label || "Untitled Session"}`}
              title={`Delete session ${sess.label || "Untitled Session"}`}
              onClick={() => onDeleteSession(sess.id)}
            >
              ×
            </button>
          </div>
        ))}
        {visibleSessions.length === 0 && (
          <div className="nav-item" style={{ cursor: "default", opacity: 0.8 }}>
            <span className="nav-item-icon">…</span>
            <span className="nav-item-label">
              {activeBuildProjectId
                ? "No sessions in this build project"
                : activeWorkspaceId
                  ? "No sessions in this workspace"
                  : "No sessions yet"}
            </span>
          </div>
        )}
      </section>

      <section className="nav-section">
        <div className="nav-section-title">Views</div>
        <button className={`nav-item ${centerView === "build" ? "active" : ""}`} onClick={() => onCenterViewSelect("build")}>
          <span className="nav-item-icon">💬</span>
          <span className="nav-item-label">Build with Clo</span>
        </button>
        <button className={`nav-item ${centerView === "overview" ? "active" : ""}`} onClick={() => onCenterViewSelect("overview")}>
          <span className="nav-item-icon">◫</span>
          <span className="nav-item-label">Overview</span>
        </button>
        <button className={`nav-item ${centerView === "inbox" ? "active" : ""}`} onClick={() => onCenterViewSelect("inbox")}>
          <span className="nav-item-icon">📥</span>
          <span className="nav-item-label">Inbox</span>
          <span className="nav-item-badge">{pendingProposalCount}</span>
        </button>
        <button className={`nav-item ${centerView === "briefing" ? "active" : ""}`} onClick={() => onCenterViewSelect("briefing")}>
          <span className="nav-item-icon">🕘</span>
          <span className="nav-item-label">Briefing</span>
        </button>
        <button className={`nav-item ${centerView === "evidence" ? "active" : ""}`} onClick={() => onCenterViewSelect("evidence")}>
          <span className="nav-item-icon">🗂</span>
          <span className="nav-item-label">Evidence + Memory</span>
        </button>
      </section>
    </div>
  );
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}
