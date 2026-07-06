import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type {
  BuildProjectRecord,
  CloQueueStateRecord,
  PlanRecord,
  PlanSummaryRecord,
  ProviderRecord,
  SessionSummary,
  WorkspaceSummary,
} from "../../api/types";
import ApkDeliveryPanel from "../delivery/ApkDeliveryPanel";
import CustomSelect from "../forms/CustomSelect";
import { ProviderModelPicker } from "../providers/ProviderModelPicker";
import { WorkspaceTree } from "./WorkspaceTree";
import "./TreeViewHome.css";

interface TreeViewHomeProps {
  workspaces: WorkspaceSummary[];
  buildProjects: BuildProjectRecord[];
  sessions: SessionSummary[];
  providers: ProviderRecord[];
  activeWorkspaceId: string | null;
  activeBuildProjectId: string | null;
  activeSessionId: string | null;
  activePlan: PlanRecord | null;
  allPlans?: PlanSummaryRecord[];
  cloQueue: CloQueueStateRecord | null;
  isBusy?: boolean;
  busySessionLabel?: string | null;
  errorMessage?: string | null;
  creatingWorkspace?: boolean;
  creatingBuildProject?: boolean;
  creatingSession?: boolean;
  onSelectWorkspace: (workspaceId: string) => void;
  onSelectSession: (sessionId: string, workspaceId: string, buildProjectId: string | null) => void;
  onCreateWorkspace: (name: string, description: string) => Promise<void> | void;
  onCreateBuildProject: (name: string, description: string) => Promise<void> | void;
  onCreateSession: (
    label: string,
    buildProjectId: string | null,
    providerId?: string,
    model?: string,
  ) => Promise<void> | void;
  onQueueMessage: (sessionId: string, content: string, stopAfterError?: boolean) => Promise<void> | void;
  onMoveQueueItem: (itemId: string, direction: "up" | "down") => Promise<void> | void;
  onCancelQueueItem: (itemId: string) => Promise<void> | void;
  onUpdateQueueSettings: (settings: { paused?: boolean; pause_on_error?: boolean }) => Promise<void> | void;
  onOpenPlan: (plan: PlanSummaryRecord) => void;
}

type SortMode = "recent" | "name" | "sessions";

interface PlanTooltipState {
  plan: PlanSummaryRecord;
  x: number;
  y: number;
}

function PlansPanel({
  plans,
  sessions,
  onOpenPlan,
  collapsed,
  onToggleCollapsed,
}: {
  plans: PlanSummaryRecord[];
  sessions: SessionSummary[];
  onOpenPlan: (plan: PlanSummaryRecord) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}) {
  const [tooltip, setTooltip] = useState<PlanTooltipState | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionLabelById = useMemo(
    () => new Map(sessions.map((s) => [s.id, s.label])),
    [sessions],
  );
  const filledPlans = useMemo(
    () => plans.filter((p) => p.active_goal?.trim() || p.want_to_know?.length > 0),
    [plans],
  );

  function showTooltip(plan: PlanSummaryRecord, event: { clientX: number; clientY: number }) {
    if (hideTimer.current) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    setTooltip({ plan, x: event.clientX, y: event.clientY });
  }

  function trackMouse(event: { clientX: number; clientY: number }) {
    if (tooltip) {
      setTooltip((t) => t ? { ...t, x: event.clientX, y: event.clientY } : null);
    }
  }

  function scheduleHide() {
    hideTimer.current = setTimeout(() => setTooltip(null), 120);
  }

  return (
    <aside className={`tree-view-home__plans-panel${collapsed ? " is-collapsed" : ""}`}>
      <div className="tree-view-home__plans-header">
        <div className="tree-view-home__plans-header-copy">
          <span className="tree-view-home__plans-eyebrow">All Plans</span>
          <span className="tree-view-home__plans-count">{filledPlans.length}</span>
        </div>
        <button
          type="button"
          className="tree-view-home__panel-toggle"
          onClick={onToggleCollapsed}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand plans panel" : "Collapse plans panel"}
        >
          {collapsed ? "Show" : "Hide"}
        </button>
      </div>
      {!collapsed && (
        <div className="tree-view-home__plans-body">
          {filledPlans.length === 0 ? (
            <div className="tree-view-home__plans-empty">No plans yet</div>
          ) : (
            <ul className="tree-view-home__plans-list">
              {filledPlans.map((plan) => (
                <li key={plan.id}>
                  <button
                    type="button"
                    className={`tree-view-home__plan-row${plan.is_active ? " is-active" : ""}`}
                    onClick={() => onOpenPlan(plan)}
                    onMouseEnter={(e) => showTooltip(plan, e)}
                    onMouseMove={trackMouse}
                    onMouseLeave={scheduleHide}
                  >
                    <span className="tree-view-home__plan-title">{plan.title || "Untitled"}</span>
                    <span className="tree-view-home__plan-meta">
                      {sessionLabelById.get(plan.session_id) ?? plan.session_id.slice(0, 8)}
                    </span>
                    <span className={`tree-view-home__plan-status tree-view-home__plan-status--${plan.plan_status ?? "unknown"}`}>
                      {(plan.plan_status ?? "unknown").replace(/_/g, " ")}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {tooltip && (
        <div
          className="tree-view-home__plan-tooltip"
          style={{ left: tooltip.x + 14, top: tooltip.y + 14 }}
          onMouseEnter={() => {
            if (hideTimer.current) {
              clearTimeout(hideTimer.current);
              hideTimer.current = null;
            }
          }}
          onMouseLeave={scheduleHide}
        >
          <div className="tree-view-home__plan-tooltip-title">{tooltip.plan.title || "Untitled"}</div>
          {tooltip.plan.active_goal && (
            <div className="tree-view-home__plan-tooltip-section">
              <span className="tree-view-home__plan-tooltip-label">Active goal</span>
              <p className="tree-view-home__plan-tooltip-text">{tooltip.plan.active_goal}</p>
            </div>
          )}
          {tooltip.plan.want_to_know?.length > 0 && (
            <div className="tree-view-home__plan-tooltip-section">
              <span className="tree-view-home__plan-tooltip-label">Want to know</span>
              <ul className="tree-view-home__plan-tooltip-list">
                {tooltip.plan.want_to_know.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="tree-view-home__plan-tooltip-footer">
            <span>{sessionLabelById.get(tooltip.plan.session_id) ?? tooltip.plan.session_id.slice(0, 8)}</span>
            <span className={`tree-view-home__plan-status tree-view-home__plan-status--${tooltip.plan.plan_status ?? "unknown"}`}>
              {(tooltip.plan.plan_status ?? "unknown").replace(/_/g, " ")}
            </span>
          </div>
        </div>
      )}
    </aside>
  );
}

export function TreeViewHome({
  workspaces,
  buildProjects,
  sessions,
  providers,
  activeWorkspaceId,
  activeBuildProjectId,
  activeSessionId,
  activePlan,
  allPlans = [],
  cloQueue,
  isBusy = false,
  busySessionLabel,
  errorMessage,
  creatingWorkspace = false,
  creatingBuildProject = false,
  creatingSession = false,
  onSelectWorkspace,
  onSelectSession,
  onCreateWorkspace,
  onCreateBuildProject,
  onCreateSession,
  onQueueMessage,
  onMoveQueueItem,
  onCancelQueueItem,
  onUpdateQueueSettings,
  onOpenPlan,
}: TreeViewHomeProps) {
  const [query, setQuery] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("recent");
  const [formMode, setFormMode] = useState<"workspace" | "project" | "session" | null>(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceDescription, setWorkspaceDescription] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [sessionLabel, setSessionLabel] = useState("");
  const [sessionBuildProjectId, setSessionBuildProjectId] = useState<string>("");
  const [sessionProviderId, setSessionProviderId] = useState<string>(providers[0]?.id ?? "");
  const [sessionModel, setSessionModel] = useState<string>(providers[0]?.model_name ?? "");
  const [queueSessionId, setQueueSessionId] = useState<string>(activeSessionId ?? sessions[0]?.id ?? "");
  const [queueMessage, setQueueMessage] = useState("");
  const [queueStopAfterError, setQueueStopAfterError] = useState(false);
  const [plansCollapsed, setPlansCollapsed] = useState(false);
  const [queueCollapsed, setQueueCollapsed] = useState(false);

  const normalizedQuery = query.trim().toLowerCase();
  const activeWorkspaceProjects = buildProjects.filter((project) => project.workspace_id === activeWorkspaceId);
  const activeWorkspaceSessions = sessions.filter((session) => session.workspace_id === activeWorkspaceId);
  const handleTreePlanSelect = (planId: string) => {
    const plan = allPlans.find((candidate) => candidate.id === planId);
    if (plan) {
      onOpenPlan(plan);
    }
  };

  const sessionOptions = useMemo(() => {
    const workspaceNameById = new Map(workspaces.map((workspace) => [workspace.id, workspace.name]));
    return [...sessions]
      .sort((left, right) => left.label.localeCompare(right.label))
      .map((session) => ({
        ...session,
        workspaceName: session.workspace_id ? workspaceNameById.get(session.workspace_id) ?? "Workspace" : "Workspace",
      }));
  }, [sessions, workspaces]);

  useEffect(() => {
    if (activeSessionId) {
      setQueueSessionId(activeSessionId);
      return;
    }
    if (!queueSessionId && sessions[0]?.id) {
      setQueueSessionId(sessions[0].id);
    }
  }, [activeSessionId, queueSessionId, sessions]);

  async function handleCreateSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    try {
      if (formMode === "workspace") {
        await onCreateWorkspace(workspaceName.trim(), workspaceDescription.trim());
        setWorkspaceName("");
        setWorkspaceDescription("");
        setFormMode(null);
        return;
      }

      if (formMode === "project") {
        await onCreateBuildProject(projectName.trim(), projectDescription.trim());
        setProjectName("");
        setProjectDescription("");
        setFormMode(null);
        return;
      }

      if (formMode === "session") {
        await onCreateSession(
          sessionLabel.trim(),
          sessionBuildProjectId || null,
          sessionProviderId || undefined,
          sessionModel.trim() || undefined,
        );
        setSessionLabel("");
        setSessionBuildProjectId("");
        setSessionModel(providers.find((provider) => provider.id === sessionProviderId)?.model_name ?? "");
        setFormMode(null);
      }
    } catch {
      // The shell surfaces the error. Keep the current form state intact for correction.
    }
  }

  function openForm(mode: "workspace" | "project" | "session") {
    setFormMode((current) => (current === mode ? null : mode));

    if (mode === "session") {
      setSessionProviderId((current) => current || providers[0]?.id || "");
      setSessionModel((current) => current || providers[0]?.model_name || "");
    }
  }

  function handleSessionProviderChange(nextProviderId: string) {
    const provider = providers.find((candidate) => candidate.id === nextProviderId);
    setSessionProviderId(nextProviderId);
    setSessionModel(provider?.model_name ?? "");
  }

  async function handleQueueSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!queueSessionId || !queueMessage.trim()) {
      return;
    }
    await onQueueMessage(queueSessionId, queueMessage.trim(), queueStopAfterError);
    setQueueMessage("");
    setQueueStopAfterError(false);
  }

  const queueRunningItem = cloQueue?.running_item ?? null;
  const queueItems = cloQueue?.queued_items ?? [];
  const recentQueueItems = cloQueue?.recent_items ?? [];
  const queuedSessionMeta = useMemo(() => {
    const meta: Record<string, { position: number; count: number }> = {};
    queueItems.forEach((item) => {
      const existing = meta[item.session_id];
      if (!existing) {
        meta[item.session_id] = { position: item.position ?? 0, count: 1 };
        return;
      }
      meta[item.session_id] = {
        position: Math.min(existing.position, item.position ?? existing.position),
        count: existing.count + 1,
      };
    });
    return meta;
  }, [queueItems]);

  const filteredWorkspaces = useMemo(() => {
    const workspaceSessionCount = new Map<string, number>();
    sessions.forEach((session) => {
      if (session.workspace_id) {
        workspaceSessionCount.set(
          session.workspace_id,
          (workspaceSessionCount.get(session.workspace_id) ?? 0) + 1,
        );
      }
    });

    const matchesWorkspace = (workspace: WorkspaceSummary) => {
      if (!normalizedQuery) return true;

      const projectMatches = buildProjects.some(
        (project) =>
          project.workspace_id === workspace.id &&
          `${project.name} ${project.description ?? ""}`.toLowerCase().includes(normalizedQuery),
      );

      const sessionMatches = sessions.some(
        (session) =>
          session.workspace_id === workspace.id &&
          `${session.label} ${session.model} ${session.provider}`.toLowerCase().includes(normalizedQuery),
      );

      return (
        `${workspace.name} ${workspace.description ?? ""}`.toLowerCase().includes(normalizedQuery) ||
        projectMatches ||
        sessionMatches
      );
    };

    return [...workspaces]
      .filter(matchesWorkspace)
      .sort((left, right) => {
        if (sortMode === "name") return left.name.localeCompare(right.name);
        if (sortMode === "sessions") {
          return (
            (workspaceSessionCount.get(right.id) ?? 0) - (workspaceSessionCount.get(left.id) ?? 0)
          );
        }
        return (
          new Date(right.updated_at ?? right.created_at).getTime() -
          new Date(left.updated_at ?? left.created_at).getTime()
        );
      });
  }, [buildProjects, normalizedQuery, sessions, sortMode, workspaces]);

  return (
    <section className="tree-view-home">
      <div className="tree-view-home__topbar">
        <span className="tree-view-home__title" aria-label="OpenCloset">
          <span className="tree-view-home__title-open">Open</span>
          <span className="tree-view-home__title-closet">Closet</span>
        </span>
        <input
          className="tree-view-home__search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search workspaces, projects, sessions…"
        />
        <CustomSelect
          triggerClassName="tree-view-home__sort"
          value={sortMode}
          onChange={(nextValue) => setSortMode(nextValue as SortMode)}
          options={[
            { value: "recent", label: "Recent" },
            { value: "name", label: "Name" },
            { value: "sessions", label: "Most sessions" },
          ]}
          ariaLabel="Sort workspaces"
        />
        {isBusy && (
          <div className="tree-view-home__busy" title={busySessionLabel ? `Clo is currently working in ${busySessionLabel}.` : "Clo is currently busy."}>
            <span className="tree-view-home__busy-glyph" aria-hidden="true">
              <span className="tree-view-home__busy-core" />
              <span className="tree-view-home__busy-orbit">
                <span className="tree-view-home__busy-satellite" />
              </span>
            </span>
            <span className="tree-view-home__busy-copy">
              <span className="tree-view-home__busy-label">Busy</span>
              {busySessionLabel && <span className="tree-view-home__busy-detail">{busySessionLabel}</span>}
            </span>
          </div>
        )}
        <div className="tree-view-home__actions">
          <button type="button" className="tree-view-home__action" onClick={() => openForm("workspace")}>
            New workspace
          </button>
          <button
            type="button"
            className="tree-view-home__action"
            onClick={() => openForm("project")}
            disabled={!activeWorkspaceId}
          >
            New project
          </button>
          <button
            type="button"
            className="tree-view-home__action tree-view-home__action--primary"
            onClick={() => openForm("session")}
            disabled={!activeWorkspaceId}
          >
            New session
          </button>
        </div>
      </div>
      {formMode && (
        <form className="tree-view-home__composer" onSubmit={handleCreateSubmit}>
          {formMode === "workspace" && (
            <>
              <div className="tree-view-home__composer-title">Create workspace</div>
              <label className="tree-view-home__field-group tree-view-home__field-group--wide">
                <span className="tree-view-home__field-label">Workspace name</span>
                <input
                  className="tree-view-home__field"
                  value={workspaceName}
                  onChange={(event) => setWorkspaceName(event.target.value)}
                  placeholder="Workspace name"
                  required
                />
              </label>
              <label className="tree-view-home__field-group tree-view-home__field-group--wide">
                <span className="tree-view-home__field-label">Description</span>
                <input
                  className="tree-view-home__field"
                  value={workspaceDescription}
                  onChange={(event) => setWorkspaceDescription(event.target.value)}
                  placeholder="Short description"
                />
              </label>
            </>
          )}
          {formMode === "project" && (
            <>
              <div className="tree-view-home__composer-title">Create project</div>
              <label className="tree-view-home__field-group tree-view-home__field-group--wide">
                <span className="tree-view-home__field-label">Project name</span>
                <input
                  className="tree-view-home__field"
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  placeholder="Project name"
                  required
                />
              </label>
              <label className="tree-view-home__field-group tree-view-home__field-group--wide">
                <span className="tree-view-home__field-label">Description</span>
                <input
                  className="tree-view-home__field"
                  value={projectDescription}
                  onChange={(event) => setProjectDescription(event.target.value)}
                  placeholder="What this project is for"
                />
              </label>
            </>
          )}
          {formMode === "session" && (
            <>
              <div className="tree-view-home__composer-title">Create session</div>
              <label className="tree-view-home__field-group tree-view-home__field-group--wide">
                <span className="tree-view-home__field-label">Session label</span>
                <input
                  className="tree-view-home__field"
                  value={sessionLabel}
                  onChange={(event) => setSessionLabel(event.target.value)}
                  placeholder="Session label"
                  required
                />
              </label>
              <label className="tree-view-home__field-group">
                <span className="tree-view-home__field-label">Project</span>
                <CustomSelect
                  triggerClassName="tree-view-home__field tree-view-home__field--select"
                  value={sessionBuildProjectId}
                  onChange={setSessionBuildProjectId}
                  options={[
                    { value: "", label: "Workspace only" },
                    ...activeWorkspaceProjects.map((project) => ({ value: project.id, label: project.name })),
                  ]}
                  ariaLabel="Select project"
                />
              </label>
              <div className="tree-view-home__field-group tree-view-home__field-group--wide tree-view-home__field-group--provider-picker">
                <ProviderModelPicker
                  providers={providers}
                  providerId={sessionProviderId}
                  model={sessionModel}
                  onProviderChange={handleSessionProviderChange}
                  onModelChange={setSessionModel}
                  providerLabel="Provider"
                  knownModelsLabel="Known models"
                  modelInputLabel="Model id"
                />
              </div>
            </>
          )}
          <div className="tree-view-home__composer-actions">
            <button type="button" className="tree-view-home__secondary" onClick={() => setFormMode(null)}>
              Cancel
            </button>
            <button
              type="submit"
              className="tree-view-home__primary"
              disabled={
                (formMode === "workspace" && (creatingWorkspace || !workspaceName.trim())) ||
                (formMode === "project" && (creatingBuildProject || !projectName.trim())) ||
                (formMode === "session" && (creatingSession || !sessionLabel.trim() || !sessionModel.trim()))
              }
            >
              {formMode === "workspace" && (creatingWorkspace ? "Creating..." : "Create workspace")}
              {formMode === "project" && (creatingBuildProject ? "Creating..." : "Create project")}
              {formMode === "session" && (creatingSession ? "Creating..." : "Create session")}
            </button>
          </div>
        </form>
      )}
      {errorMessage && <div className="tree-view-home__error">{errorMessage}</div>}
      {activeWorkspaceId && (
        <div className="tree-view-home__delivery-band">
          <ApkDeliveryPanel
            workspaceId={activeWorkspaceId}
            projectOptions={activeWorkspaceProjects.map((project) => ({ id: project.id, label: project.name }))}
            sessionOptions={activeWorkspaceSessions.map((session) => ({
              id: session.id,
              label: session.label,
              buildProjectId: session.build_project_id,
            }))}
            preferredProjectId={activeBuildProjectId ?? activeWorkspaceProjects[0]?.id ?? null}
            preferredSessionId={activeSessionId ?? null}
            originTag="opencloset_browser_dashboard"
            eyebrow="Deliver APK"
            title="Mobile sideload bridge"
            subtitle="Queue a debug or release APK to the phone harness without opening the capture inspector first."
            emptyProjectMessage="Create or select a build project in this workspace before queueing an APK."
          />
        </div>
      )}
      <div
        className={[
          "tree-view-home__content",
          plansCollapsed ? "tree-view-home__content--plans-collapsed" : "",
          queueCollapsed ? "tree-view-home__content--queue-collapsed" : "",
        ].filter(Boolean).join(" ")}
      >
        <div className="tree-view-home__tree-panel">
          <WorkspaceTree
            workspaces={filteredWorkspaces}
            buildProjects={buildProjects}
            sessions={sessions}
            activeWorkspaceId={activeWorkspaceId}
            activeBuildProjectId={activeBuildProjectId}
            activeSessionId={activeSessionId}
            activePlan={activePlan}
            runningQueueSessionId={queueRunningItem?.session_id ?? null}
            queuedSessionMeta={queuedSessionMeta}
            searchQuery=""
            onWorkspaceSelect={onSelectWorkspace}
            onBuildProjectSelect={() => undefined}
            onSessionSelect={(sessionId) => onSelectSession(sessionId, activeWorkspaceId ?? "", null)}
            onPlanSelect={handleTreePlanSelect}
          />
        </div>

        <PlansPanel
          plans={allPlans}
          sessions={sessions}
          onOpenPlan={onOpenPlan}
          collapsed={plansCollapsed}
          onToggleCollapsed={() => setPlansCollapsed((current) => !current)}
        />

        <aside className={`tree-view-home__queue-panel${queueCollapsed ? " is-collapsed" : ""}`}>
          <div className="tree-view-home__queue-header">
            <div>
              <div className="tree-view-home__queue-eyebrow">Dispatch Queue</div>
              <h3 className="tree-view-home__queue-title">Clo Queue</h3>
              <p className="tree-view-home__queue-subtitle">One serious run at a time, many prepared intentions.</p>
            </div>
            <div className="tree-view-home__queue-header-actions">
              <button
                type="button"
                className="tree-view-home__panel-toggle"
                onClick={() => setQueueCollapsed((current) => !current)}
                aria-expanded={!queueCollapsed}
                aria-label={queueCollapsed ? "Expand queue panel" : "Collapse queue panel"}
              >
                {queueCollapsed ? "Show" : "Hide"}
              </button>
              <button
                type="button"
                className={`tree-view-home__queue-toggle${cloQueue?.paused ? " is-paused" : ""}`}
                onClick={() => onUpdateQueueSettings({ paused: !cloQueue?.paused })}
              >
                {cloQueue?.paused ? "Resume" : "Pause"}
              </button>
            </div>
          </div>

          {!queueCollapsed && (
            <div className="tree-view-home__queue-body">
              <label className="tree-view-home__queue-setting">
                <input
                  type="checkbox"
                  checked={cloQueue?.pause_on_error ?? true}
                  onChange={(event) => onUpdateQueueSettings({ pause_on_error: event.target.checked })}
                />
                <span>Pause queue on error</span>
              </label>

              <form className="tree-view-home__queue-composer" onSubmit={handleQueueSubmit}>
                <div className="tree-view-home__queue-composer-title">Queue a message</div>
                <CustomSelect
                  triggerClassName="tree-view-home__field tree-view-home__field--select"
                  value={queueSessionId}
                  onChange={setQueueSessionId}
                  options={sessionOptions.map((session) => ({
                    value: session.id,
                    label: `${session.workspaceName} / ${session.label}`,
                  }))}
                  ariaLabel="Queue session"
                />
                <textarea
                  className="tree-view-home__queue-textarea"
                  value={queueMessage}
                  onChange={(event) => setQueueMessage(event.target.value)}
                  placeholder="Queue the next message for a session..."
                  rows={4}
                />
                <label className="tree-view-home__queue-setting tree-view-home__queue-setting--compact">
                  <input
                    type="checkbox"
                    checked={queueStopAfterError}
                    onChange={(event) => setQueueStopAfterError(event.target.checked)}
                  />
                  <span>Pause after error for this item</span>
                </label>
                <button type="submit" className="tree-view-home__primary" disabled={!queueSessionId || !queueMessage.trim()}>
                  Add to queue
                </button>
              </form>

          <div className="tree-view-home__queue-section">
            <div className="tree-view-home__queue-section-title">Now running</div>
            {queueRunningItem ? (
              <div className="tree-view-home__queue-card tree-view-home__queue-card--running">
                <div>
                  <div className="tree-view-home__queue-card-label">{queueRunningItem.workspace_name} / {queueRunningItem.session_label}</div>
                  <div className="tree-view-home__queue-card-message">{queueRunningItem.message_content}</div>
                </div>
              </div>
            ) : (
              <div className="tree-view-home__queue-empty">No queue item is running right now.</div>
            )}
          </div>

          <div className="tree-view-home__queue-section">
            <div className="tree-view-home__queue-section-title">Up next</div>
            {queueItems.length ? (
              <div className="tree-view-home__queue-list">
                {queueItems.map((item, index) => (
                  <div key={item.id} className="tree-view-home__queue-item">
                    <div className="tree-view-home__queue-item-order">#{item.position}</div>
                    <div className="tree-view-home__queue-item-copy">
                      <div className="tree-view-home__queue-item-session">{item.workspace_name} / {item.session_label}</div>
                      <div className="tree-view-home__queue-item-message">{item.message_content}</div>
                    </div>
                    <div className="tree-view-home__queue-item-actions">
                      <button type="button" className="tree-view-home__queue-mini" onClick={() => onMoveQueueItem(item.id, "up")} disabled={index === 0}>
                        ↑
                      </button>
                      <button type="button" className="tree-view-home__queue-mini" onClick={() => onMoveQueueItem(item.id, "down")} disabled={index === queueItems.length - 1}>
                        ↓
                      </button>
                      <button type="button" className="tree-view-home__queue-mini tree-view-home__queue-mini--danger" onClick={() => onCancelQueueItem(item.id)}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="tree-view-home__queue-empty">No queued follow-ups yet.</div>
            )}
          </div>

          <div className="tree-view-home__queue-section">
            <div className="tree-view-home__queue-section-title">Recent outcomes</div>
            {recentQueueItems.length ? (
              <div className="tree-view-home__queue-list">
                {recentQueueItems.map((item) => (
                  <div key={item.id} className="tree-view-home__queue-item tree-view-home__queue-item--history">
                    <div className={`tree-view-home__queue-result tree-view-home__queue-result--${item.status}`}>
                      {item.status}
                    </div>
                    <div className="tree-view-home__queue-item-copy">
                      <div className="tree-view-home__queue-item-session">{item.workspace_name} / {item.session_label}</div>
                      <div className="tree-view-home__queue-item-message">{item.message_content}</div>
                      {(item.status === "failed" ? item.error : item.result_summary) ? (
                        <div
                          className={[
                            "tree-view-home__queue-item-detail",
                            item.status === "failed" ? "tree-view-home__queue-item-detail--failed" : "",
                          ].filter(Boolean).join(" ")}
                        >
                          {item.status === "failed" ? item.error : item.result_summary}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="tree-view-home__queue-empty">No completed or failed queue items yet.</div>
            )}
          </div>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}

export default TreeViewHome;
