import type { BuildProjectRecord, ProviderRecord, SessionSummary, WorkspaceSummary } from "../../api/types";
import CustomSelect from "../forms/CustomSelect";
import "./ChatNativeSidebar.css";

interface ChatNativeSidebarProps {
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string | null;
  buildProjects: BuildProjectRecord[];
  activeBuildProjectId: string | null;
  sessions: SessionSummary[];
  activeSessionId: string | null;
  providers: ProviderRecord[];
  newSessionProvider: string;
  newSessionModel: string;
  collapsed: boolean;
  onWorkspaceSelect: (workspaceId: string) => void;
  onBuildProjectSelect: (projectId: string | null) => void;
  onSessionSelect: (sessionId: string) => void;
  onNewSessionProviderChange: (provider: string) => void;
  onNewSessionModelChange: (model: string) => void;
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

export default function ChatNativeSidebar({
  workspaces,
  activeWorkspaceId,
  buildProjects,
  activeBuildProjectId,
  sessions,
  activeSessionId,
  providers,
  newSessionProvider,
  newSessionModel,
  collapsed,
  onWorkspaceSelect,
  onBuildProjectSelect,
  onSessionSelect,
  onNewSessionProviderChange,
  onNewSessionModelChange,
  onCreateWorkspace,
  creatingWorkspace,
  onDeleteWorkspace,
  onCreateBuildProject,
  creatingBuildProject,
  onDeleteBuildProject,
  onCreateSession,
  creatingSession,
  onDeleteSession,
}: ChatNativeSidebarProps) {
  const filteredSessions = sessions.filter((session) => {
    if (activeWorkspaceId && session.workspace_id !== activeWorkspaceId) {
      return false;
    }
    if (activeBuildProjectId) {
      return session.build_project_id === activeBuildProjectId;
    }
    return true;
  });

  return (
    <aside className={`chat-native-sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="chat-native-sidebar-brand">
        <div className="chat-native-sidebar-title">
          <span>Open</span>Closet
        </div>
        <div className="chat-native-sidebar-copy">Chat-native desktop runtime</div>
      </div>

      <section className="chat-native-sidebar-section">
        <div className="chat-native-sidebar-row">
          <label className="chat-native-sidebar-label">Workspaces</label>
          <button className="chat-native-sidebar-create" type="button" onClick={onCreateWorkspace} disabled={creatingWorkspace}>
            {creatingWorkspace ? "..." : "New"}
          </button>
        </div>
        <div className="chat-native-sidebar-stack">
          {workspaces.map((workspace) => (
            <div key={workspace.id} className="chat-native-sidebar-entity-row">
              <button
                className={`chat-native-sidebar-entity ${activeWorkspaceId === workspace.id ? "active" : ""}`}
                type="button"
                onClick={() => onWorkspaceSelect(workspace.id)}
              >
                <span className="chat-native-sidebar-entity-title">{workspace.name}</span>
                <span className="chat-native-sidebar-entity-meta">{workspace.status}</span>
              </button>
              <button
                className="chat-native-sidebar-delete"
                type="button"
                aria-label={`Delete workspace ${workspace.name}`}
                title={`Delete workspace ${workspace.name}`}
                onClick={() => onDeleteWorkspace(workspace.id)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="chat-native-sidebar-section">
        <div className="chat-native-sidebar-row">
          <label className="chat-native-sidebar-label">Projects</label>
          <button
            className="chat-native-sidebar-create"
            type="button"
            onClick={onCreateBuildProject}
            disabled={!activeWorkspaceId || creatingBuildProject}
          >
            {creatingBuildProject ? "..." : "New"}
          </button>
        </div>
        <div className="chat-native-sidebar-stack">
          <button
            className={`chat-native-sidebar-entity slim ${activeBuildProjectId === null ? "active" : ""}`}
            type="button"
            onClick={() => onBuildProjectSelect(null)}
          >
            <span className="chat-native-sidebar-entity-title">Workspace-wide</span>
            <span className="chat-native-sidebar-entity-meta">no build project filter</span>
          </button>
          {buildProjects.map((project) => (
            <div key={project.id} className="chat-native-sidebar-entity-row">
              <button
                className={`chat-native-sidebar-entity slim ${activeBuildProjectId === project.id ? "active" : ""}`}
                type="button"
                onClick={() => onBuildProjectSelect(project.id)}
              >
                <span className="chat-native-sidebar-entity-title">{project.name}</span>
                <span className="chat-native-sidebar-entity-meta">{project.status}</span>
              </button>
              <button
                className="chat-native-sidebar-delete"
                type="button"
                aria-label={`Delete build project ${project.name}`}
                title={`Delete build project ${project.name}`}
                onClick={() => onDeleteBuildProject(project.id)}
              >
                ×
              </button>
            </div>
          ))}
          {!buildProjects.length && activeWorkspaceId && (
            <div className="chat-native-sidebar-empty">No build projects in this workspace yet.</div>
          )}
        </div>
      </section>

      <section className="chat-native-sidebar-section grow">
        <div className="chat-native-sidebar-row">
          <label className="chat-native-sidebar-label">Sessions</label>
          <button className="chat-native-sidebar-create" type="button" onClick={onCreateSession} disabled={creatingSession}>
            {creatingSession ? "..." : "New"}
          </button>
        </div>
        <div className="chat-native-sidebar-session-compose">
          <label className="chat-native-sidebar-subfield">
            <span className="chat-native-sidebar-subfield-label">Run on</span>
            <CustomSelect
              triggerClassName="chat-native-sidebar-input"
              value={newSessionProvider}
              onChange={onNewSessionProviderChange}
              options={providers.map((provider) => ({
                value: provider.id,
                label: `${provider.id}${!provider.enabled ? " (disabled)" : ""}`,
              }))}
              ariaLabel="Run on provider"
            />
          </label>
          <label className="chat-native-sidebar-subfield">
            <span className="chat-native-sidebar-subfield-label">Model</span>
            <input
              className="chat-native-sidebar-input"
              type="text"
              value={newSessionModel}
              onChange={(event) => onNewSessionModelChange(event.target.value)}
              placeholder="Model name"
            />
          </label>
        </div>
        <div className="chat-native-sidebar-session-list">
          {filteredSessions.map((session) => (
            <div key={session.id} className="chat-native-sidebar-session-row">
              <button
                type="button"
                className={`chat-native-sidebar-session ${session.id === activeSessionId ? "active" : ""}`}
                onClick={() => onSessionSelect(session.id)}
              >
                <div className="chat-native-sidebar-session-title">{session.label}</div>
                <div className="chat-native-sidebar-session-meta">
                  {session.provider} · {session.model}
                </div>
              </button>
              <button
                className="chat-native-sidebar-delete session"
                type="button"
                aria-label={`Delete session ${session.label}`}
                title={`Delete session ${session.label}`}
                onClick={() => onDeleteSession(session.id)}
              >
                ×
              </button>
            </div>
          ))}
          {!filteredSessions.length && <div className="chat-native-sidebar-empty">No sessions in this scope yet.</div>}
        </div>
      </section>
    </aside>
  );
}
