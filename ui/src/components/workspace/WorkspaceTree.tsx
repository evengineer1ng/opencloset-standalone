import { useEffect, useMemo, useRef, useState } from "react";
import type {
  BuildProjectRecord,
  PlanRecord,
  SessionSummary,
  WorkspaceSummary,
} from "../../api/types";
import "./WorkspaceTree.css";

interface WorkspaceTreeProps {
  workspaces: WorkspaceSummary[];
  buildProjects: BuildProjectRecord[];
  sessions: SessionSummary[];
  activeWorkspaceId: string | null;
  activeBuildProjectId: string | null;
  activeSessionId: string | null;
  activePlan: PlanRecord | null;
  runningQueueSessionId?: string | null;
  queuedSessionMeta?: Record<string, { position: number; count: number }>;
  searchQuery: string;
  onWorkspaceSelect: (workspaceId: string) => void;
  onBuildProjectSelect: (projectId: string) => void;
  onSessionSelect: (sessionId: string) => void;
  onPlanSelect?: (planId: string) => void;
}

function getQueueBadge(
  sessionId: string,
  runningQueueSessionId: string | null | undefined,
  queuedSessionMeta: Record<string, { position: number; count: number }> | undefined,
): { label: string; tone: "running" | "queued" } | null {
  if (runningQueueSessionId && runningQueueSessionId === sessionId) {
    return { label: "Running", tone: "running" };
  }

  const meta = queuedSessionMeta?.[sessionId];
  if (!meta) {
    return null;
  }

  return {
    label: meta.count > 1 ? `Queued #${meta.position} +${meta.count - 1}` : `Queued #${meta.position}`,
    tone: "queued",
  };
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function formatBranchStatus(status: string | null | undefined): string {
  if (!status) {
    return "Unknown";
  }

  return status.replace(/[_-]+/g, " ");
}

export function WorkspaceTree({
  workspaces,
  buildProjects,
  sessions,
  activeWorkspaceId,
  activeBuildProjectId,
  activeSessionId,
  activePlan,
  runningQueueSessionId,
  queuedSessionMeta,
  searchQuery,
  onWorkspaceSelect,
  onBuildProjectSelect,
  onSessionSelect,
  onPlanSelect,
}: WorkspaceTreeProps) {
  const [index, setIndex] = useState(0);
  const [expandedBranches, setExpandedBranches] = useState<Record<string, boolean>>({});
  const touchStartX = useRef<number | null>(null);
  const touchDeltaX = useRef(0);

  const normalizedQuery = searchQuery.trim().toLowerCase();

  const filteredWorkspaces = useMemo(() => {
    if (!normalizedQuery) return workspaces;
    return workspaces.filter((workspace) => {
      const workspaceProjects = buildProjects.filter((p) => p.workspace_id === workspace.id);
      const workspaceSessions = sessions.filter((s) => s.workspace_id === workspace.id);
      const haystack = [
        workspace.name,
        workspace.description ?? "",
        ...workspaceProjects.map((p) => p.name),
        ...workspaceSessions.map((s) => s.label),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [buildProjects, normalizedQuery, sessions, workspaces]);

  useEffect(() => {
    if (!filteredWorkspaces.length) {
      setIndex(0);
      return;
    }
    const activeIndex = filteredWorkspaces.findIndex((ws) => ws.id === activeWorkspaceId);
    if (activeIndex >= 0) {
      setIndex(activeIndex);
      return;
    }
    setIndex((current) => Math.min(current, filteredWorkspaces.length - 1));
  }, [activeWorkspaceId, filteredWorkspaces]);

  useEffect(() => {
    setExpandedBranches((current) => {
      const next = { ...current };

      for (const workspace of filteredWorkspaces) {
        const workspaceProjects = buildProjects.filter((project) => project.workspace_id === workspace.id);
        for (const project of workspaceProjects) {
          const branchId = `${workspace.id}:project:${project.id}`;
          if (!(branchId in next)) {
            next[branchId] = activeBuildProjectId ? activeBuildProjectId === project.id : true;
          }
        }

        const hasUnassignedSessions = sessions.some(
          (session) => session.workspace_id === workspace.id && !session.build_project_id,
        );
        if (hasUnassignedSessions) {
          const branchId = `${workspace.id}:unassigned`;
          if (!(branchId in next)) {
            next[branchId] = true;
          }
        }

        if (activePlan?.workspace_id === workspace.id) {
          const branchId = `${workspace.id}:plan:${activePlan.id}`;
          if (!(branchId in next)) {
            next[branchId] = true;
          }
        }
      }

      return next;
    });
  }, [activeBuildProjectId, activePlan, buildProjects, filteredWorkspaces, sessions]);

  const currentWorkspace = filteredWorkspaces[index] ?? null;

  function move(direction: -1 | 1) {
    setIndex((current) => {
      const next = Math.max(0, Math.min(filteredWorkspaces.length - 1, current + direction));
      const workspace = filteredWorkspaces[next];
      if (workspace) onWorkspaceSelect(workspace.id);
      return next;
    });
  }

  function handleTouchStart(event: React.TouchEvent<HTMLDivElement>) {
    touchStartX.current = event.touches[0]?.clientX ?? null;
    touchDeltaX.current = 0;
  }

  function handleTouchMove(event: React.TouchEvent<HTMLDivElement>) {
    if (touchStartX.current == null) return;
    touchDeltaX.current = (event.touches[0]?.clientX ?? 0) - touchStartX.current;
  }

  function handleTouchEnd() {
    if (Math.abs(touchDeltaX.current) < 48) {
      touchStartX.current = null;
      touchDeltaX.current = 0;
      return;
    }
    if (touchDeltaX.current < 0) {
      move(1);
    } else {
      move(-1);
    }
    touchStartX.current = null;
    touchDeltaX.current = 0;
  }

  function toggleBranch(branchId: string, projectId?: string) {
    setExpandedBranches((current) => ({
      ...current,
      [branchId]: !(current[branchId] ?? true),
    }));

    if (projectId) {
      onBuildProjectSelect(projectId);
    }
  }

  if (!filteredWorkspaces.length) {
    return (
      <section className="workspace-tree">
        <div className="workspace-tree__empty">No matching workspaces</div>
      </section>
    );
  }

  return (
    <section className="workspace-tree">
      <header className="workspace-tree__header">
        <div className="workspace-tree__title-group">
          <p className="workspace-tree__eyebrow">Family tree</p>
          <h2 className="workspace-tree__title">
            {filteredWorkspaces.length} workspace{filteredWorkspaces.length === 1 ? "" : "s"}
          </h2>
          <p className="workspace-tree__subtitle">Swipe between roots or expand a branch to drill into sessions.</p>
        </div>
        <div className="workspace-tree__controls">
          <button
            type="button"
            className="workspace-tree__nav"
            disabled={index === 0}
            onClick={() => move(-1)}
          >
            ←
          </button>
          <button
            type="button"
            className="workspace-tree__nav"
            disabled={index === filteredWorkspaces.length - 1}
            onClick={() => move(1)}
          >
            →
          </button>
        </div>
      </header>

      <div
        className="workspace-tree__viewport"
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      >
        <div
          className="workspace-tree__track"
          style={{ transform: `translateX(-${index * 100}%)` }}
        >
          {filteredWorkspaces.map((workspace) => {
            const projects = buildProjects.filter((p) => p.workspace_id === workspace.id);
            const wsSessions = sessions.filter((s) => s.workspace_id === workspace.id);
            const planItems = activePlan?.workspace_id === workspace.id ? activePlan.items : [];
            const unassignedSessions = wsSessions.filter((s) => !s.build_project_id);
            const workspaceSessionCount = wsSessions.length;

            return (
              <div key={workspace.id} className="workspace-tree__slide">
                <div className="workspace-tree__card">
                  <div className="workspace-tree__root-wrap">
                    <button
                      type="button"
                      className={`workspace-tree__root-node${workspace.id === activeWorkspaceId ? " is-active" : ""}`}
                      onClick={() => onWorkspaceSelect(workspace.id)}
                    >
                      <span className="workspace-tree__root-label">Workspace root</span>
                      <span className="workspace-tree__workspace-name">{workspace.name}</span>
                      <span className="workspace-tree__root-meta">
                        {projects.length} projects · {workspaceSessionCount} sessions
                      </span>
                    </button>
                    <p className="workspace-tree__workspace-meta">
                      {workspace.description || "Swipe between workspaces, then open a session to jump into chat."}
                    </p>
                  </div>

                  <div className="workspace-tree__branches workspace-tree__branches--family">
                    {projects.map((project) => {
                      const pSessions = wsSessions.filter(
                        (s) => s.build_project_id === project.id,
                      );
                      const branchId = `${workspace.id}:project:${project.id}`;
                      const isExpanded = expandedBranches[branchId] ?? true;

                      return (
                        <section key={project.id} className="workspace-tree__branch workspace-tree__branch--project">
                          <button
                            type="button"
                            className={`workspace-tree__branch-node${isExpanded ? " is-expanded" : ""}${project.id === activeBuildProjectId ? " is-active" : ""}`}
                            onClick={() => toggleBranch(branchId, project.id)}
                          >
                            <span className="workspace-tree__branch-toggle">{isExpanded ? "▾" : "▸"}</span>
                            <span className="workspace-tree__branch-stem" />
                            <div className="workspace-tree__branch-copy">
                              <h3 className="workspace-tree__branch-title">{project.name}</h3>
                              <p className="workspace-tree__branch-subtitle">
                                {formatBranchStatus(project.status)}
                              </p>
                            </div>
                            <span className="workspace-tree__count">{pSessions.length}</span>
                          </button>

                          {isExpanded && (
                            <div className="workspace-tree__descendants">
                              <ul className="workspace-tree__items workspace-tree__items--leaves">
                                {pSessions.map((session) => (
                                  <li key={session.id} className="workspace-tree__leaf-item">
                                    {(() => {
                                      const queueBadge = getQueueBadge(session.id, runningQueueSessionId, queuedSessionMeta);
                                      return (
                                    <button
                                      type="button"
                                      className={`workspace-tree__item workspace-tree__item--button${session.id === activeSessionId ? " is-active" : ""}`}
                                      onClick={() => onSessionSelect(session.id)}
                                    >
                                      <span className="workspace-tree__item-line" />
                                      <div>
                                        <span className="workspace-tree__item-title">
                                          {session.label}
                                        </span>
                                        <span className="workspace-tree__item-meta">
                                          {session.model} · {formatDate(session.created_at)}
                                        </span>
                                      </div>
                                      <div className="workspace-tree__item-tail">
                                        {queueBadge && (
                                          <span className={`workspace-tree__item-queue workspace-tree__item-queue--${queueBadge.tone}`}>
                                            {queueBadge.label}
                                          </span>
                                        )}
                                        <span className="workspace-tree__item-status">
                                          {formatBranchStatus(session.status)}
                                        </span>
                                      </div>
                                    </button>
                                      );
                                    })()}
                                  </li>
                                ))}
                                {!pSessions.length && (
                                  <li>
                                    <p className="workspace-tree__empty workspace-tree__empty--inline">
                                      No sessions yet
                                    </p>
                                  </li>
                                )}
                              </ul>
                            </div>
                          )}
                        </section>
                      );
                    })}

                    {unassignedSessions.length > 0 && (
                      <section className="workspace-tree__branch workspace-tree__branch--orphan">
                        {(() => {
                          const branchId = `${workspace.id}:unassigned`;
                          const isExpanded = expandedBranches[branchId] ?? true;

                          return (
                            <>
                              <button
                                type="button"
                                className={`workspace-tree__branch-node${isExpanded ? " is-expanded" : ""}`}
                                onClick={() => toggleBranch(branchId)}
                              >
                                <span className="workspace-tree__branch-toggle">{isExpanded ? "▾" : "▸"}</span>
                                <span className="workspace-tree__branch-stem" />
                                <div className="workspace-tree__branch-copy">
                                  <h3 className="workspace-tree__branch-title">Workspace-only sessions</h3>
                                  <p className="workspace-tree__branch-subtitle">No project branch</p>
                                </div>
                                <span className="workspace-tree__count">{unassignedSessions.length}</span>
                              </button>

                              {isExpanded && (
                                <div className="workspace-tree__descendants">
                                  <ul className="workspace-tree__items workspace-tree__items--leaves">
                                    {unassignedSessions.map((session) => (
                                      <li key={session.id} className="workspace-tree__leaf-item">
                                        {(() => {
                                          const queueBadge = getQueueBadge(session.id, runningQueueSessionId, queuedSessionMeta);
                                          return (
                                        <button
                                          type="button"
                                          className={`workspace-tree__item workspace-tree__item--button${session.id === activeSessionId ? " is-active" : ""}`}
                                          onClick={() => onSessionSelect(session.id)}
                                        >
                                          <span className="workspace-tree__item-line" />
                                          <div>
                                            <span className="workspace-tree__item-title">
                                              {session.label}
                                            </span>
                                            <span className="workspace-tree__item-meta">
                                              {session.model} · {formatDate(session.created_at)}
                                            </span>
                                          </div>
                                          <div className="workspace-tree__item-tail">
                                            {queueBadge && (
                                              <span className={`workspace-tree__item-queue workspace-tree__item-queue--${queueBadge.tone}`}>
                                                {queueBadge.label}
                                              </span>
                                            )}
                                            <span className="workspace-tree__item-status">
                                              {formatBranchStatus(session.status)}
                                            </span>
                                          </div>
                                        </button>
                                          );
                                        })()}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </>
                          );
                        })()}
                      </section>
                    )}

                    {planItems.length > 0 && (
                      <section className="workspace-tree__branch workspace-tree__branch--plan">
                        {(() => {
                          const branchId = `${workspace.id}:plan:${activePlan!.id}`;
                          const isExpanded = expandedBranches[branchId] ?? true;

                          return (
                            <>
                              <button
                                type="button"
                                className={`workspace-tree__branch-node${isExpanded ? " is-expanded" : ""}`}
                                onClick={() => toggleBranch(branchId)}
                              >
                                <span className="workspace-tree__branch-toggle">{isExpanded ? "▾" : "▸"}</span>
                                <span className="workspace-tree__branch-stem" />
                                <div className="workspace-tree__branch-copy">
                                  <h3 className="workspace-tree__branch-title">{activePlan!.title}</h3>
                                  <p className="workspace-tree__branch-subtitle">Active plan branch</p>
                                </div>
                                <span className="workspace-tree__count">{planItems.length}</span>
                              </button>

                              {isExpanded && (
                                <div className="workspace-tree__descendants">
                                  <ul className="workspace-tree__items workspace-tree__items--leaves">
                                    {activePlan!.items.slice(0, 4).map((item) => (
                                      <li key={item.id} className="workspace-tree__leaf-item">
                                        <button
                                          type="button"
                                          className={`workspace-tree__item workspace-tree__item--button${item.status === "done" ? " is-done" : ""}`}
                                          onClick={() => onPlanSelect?.(activePlan!.id)}
                                        >
                                          <span className="workspace-tree__item-line" />
                                          <span className="workspace-tree__item-title">
                                            {item.content}
                                          </span>
                                          <span className="workspace-tree__item-status">
                                            {formatBranchStatus(item.status)}
                                          </span>
                                        </button>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </>
                          );
                        })()}
                      </section>
                    )}

                    {!projects.length && !unassignedSessions.length && (
                      <div className="workspace-tree__empty">No projects or sessions yet</div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="workspace-tree__dots">
        {filteredWorkspaces.map((ws, i) => (
          <button
            key={ws.id}
            type="button"
            className={`workspace-tree__dot${i === index ? " is-active" : ""}`}
            onClick={() => {
              setIndex(i);
              onWorkspaceSelect(ws.id);
            }}
          />
        ))}
      </div>
    </section>
  );
}

export default WorkspaceTree;
