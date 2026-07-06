import { useEffect, useState } from "react";
import {
  acceptPlanProposal,
  activatePlan,
  addPlanItem,
  addSessionMemory,
  compileWorkspaceAttentionProfile,
  createWorkspaceCapture,
  createPlan,
  deletePlan as deletePlanRecord,
  getWorkspaceAttentionProfile,
  getPlan as getActivePlanRecord,
  getSessionMemory,
  getSessionToolPolicy,
  listPlanProposals,
  listPlanRevisions,
  listPlans,
  listWorkspaceCaptures,
  patchWorkspaceAttentionProfile,
  patchSessionToolPolicy,
  promoteWorkspaceCapture,
  rejectPlanProposal,
  searchSessionMemory,
  updateActivePlan,
  updatePlanItem,
} from "../../api/client";
import type {
  PlanProposalRecord,
  PlanRecord,
  PlanRevisionRecord,
  PlanSummaryRecord,
  SessionMemoryRecord,
  SessionMemorySearchRecord,
  ToolPolicyRecord,
  WorkspaceAttentionCompileResponse,
  WorkspaceAttentionPolicyDiffRecord,
  WorkspaceAttentionProfileRecord,
  WorkspaceCaptureRecord,
  WorkspaceEvidenceRecord,
} from "../../api/types";
import ApkDeliveryPanel from "../delivery/ApkDeliveryPanel";
import CustomSelect from "../forms/CustomSelect";
import "./RightPanel.css";

export type PanelTab = "plan" | "attention" | "queue" | "memory" | "captures" | "settings";
const PLAN_ITEM_STATUSES = ["todo", "doing", "done", "blocked", "deferred"] as const;
const ATTENTION_MODE_OPTIONS = ["active", "warm", "background", "parked", "paused"] as const;
const ATTENTION_NOTIFICATION_OPTIONS = ["immediate", "significant", "quiet", "silent"] as const;
const ATTENTION_FRESHNESS_OPTIONS = ["daily", "weekly", "monthly", "manual"] as const;
const ATTENTION_PASTIME_OPTIONS = ["maintenance", "operational", "reflective", "preparatory", "autonomous_execution"] as const;

function buildSelectOptions(values: readonly string[]) {
  return values.map((value) => ({ value, label: value }));
}

const TOOL_POLICY_GROUPS = [
  {
    label: "Files + Process",
    tools: ["read", "write", "edit", "exec", "process"],
  },
  {
    label: "Planning + Memory",
    tools: [
      "memory_search",
      "plan_get_active",
      "plan_add_item",
      "plan_set_status",
      "plan_create",
      "plan_activate",
      "plan_reorder",
      "plan_archive",
      "plan_list_proposals",
      "plan_accept_proposal",
      "plan_reject_proposal",
    ],
  },
] as const;

interface RightPanelProps {
  sessionId: string | null;
  workspaceId: string | null;
  buildProjectId: string | null;
  plan: PlanRecord | null;
  onPlanRefresh: () => Promise<void>;
  onWorkspaceAttentionRefresh?: () => Promise<void>;
  onEvidenceCreated?: (evidence: WorkspaceEvidenceRecord) => void;
  requestedTab?: PanelTab | null;
  requestedTabToken?: number;
}

export default function RightPanel({
  sessionId,
  workspaceId,
  buildProjectId,
  plan,
  onPlanRefresh,
  onWorkspaceAttentionRefresh,
  onEvidenceCreated,
  requestedTab = null,
  requestedTabToken = 0,
}: RightPanelProps) {
  const defaultTab: PanelTab = "plan";
  const [activeTab, setActiveTab] = useState<PanelTab>(defaultTab);

  useEffect(() => {
    if (requestedTab) {
      setActiveTab(requestedTab);
    }
  }, [requestedTab, requestedTabToken]);

  return (
    <div className="right-panel">
      <div className="panel-tabs">
        <button className={`panel-tab ${activeTab === "plan" ? "active" : ""}`} onClick={() => setActiveTab("plan")}>
          Plan
        </button>
        <button className={`panel-tab ${activeTab === "attention" ? "active" : ""}`} onClick={() => setActiveTab("attention")}>
          Attention
        </button>
        <button className={`panel-tab ${activeTab === "queue" ? "active" : ""}`} onClick={() => setActiveTab("queue")}>
          Review
        </button>
        <button className={`panel-tab ${activeTab === "memory" ? "active" : ""}`} onClick={() => setActiveTab("memory")}>
          Memory
        </button>
        <button
          className={`panel-tab ${activeTab === "captures" ? "active" : ""}`}
          onClick={() => setActiveTab("captures")}
        >
          Captures
        </button>
        <button className={`panel-tab ${activeTab === "settings" ? "active" : ""}`} onClick={() => setActiveTab("settings")}>
          Settings
        </button>
      </div>

      <div className="panel-content">
        {activeTab === "plan" && <PlanTab sessionId={sessionId} plan={plan} onPlanRefresh={onPlanRefresh} />}
        {activeTab === "attention" && <AttentionTab workspaceId={workspaceId} onAttentionRefresh={onWorkspaceAttentionRefresh} />}
        {activeTab === "queue" && <QueueTab />}
        {activeTab === "memory" && <MemoryTab sessionId={sessionId} />}
        {activeTab === "captures" && (
          <CapturesTab
            workspaceId={workspaceId}
            buildProjectId={buildProjectId}
            sessionId={sessionId}
            onEvidenceCreated={onEvidenceCreated}
          />
        )}
        {activeTab === "settings" && <SettingsTab sessionId={sessionId} />}
      </div>
    </div>
  );
}

function PlanTab({
  sessionId,
  plan,
  onPlanRefresh,
}: {
  sessionId: string | null;
  plan: PlanRecord | null;
  onPlanRefresh: () => Promise<void>;
}) {
  const [newItemText, setNewItemText] = useState("");
  const [newPlanTitle, setNewPlanTitle] = useState("");
  const [newPlanGoal, setNewPlanGoal] = useState("");
  const [planSearch, setPlanSearch] = useState("");
  const [titleDraft, setTitleDraft] = useState("");
  const [goalDraft, setGoalDraft] = useState("");
  const [availablePlans, setAvailablePlans] = useState<PlanSummaryRecord[]>([]);
  const [proposals, setProposals] = useState<PlanProposalRecord[]>([]);
  const [revisions, setRevisions] = useState<PlanRevisionRecord[]>([]);
  const [loadingPlans, setLoadingPlans] = useState(false);
  const [loadingProposals, setLoadingProposals] = useState(false);
  const [loadingRevisions, setLoadingRevisions] = useState(false);
  const [selectedProposalId, setSelectedProposalId] = useState<string | null>(null);
  const [selectedRevisionId, setSelectedRevisionId] = useState<string | null>(null);
  const [isMutating, setIsMutating] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  useEffect(() => {
    setPlanError(null);
  }, [plan?.id]);

  useEffect(() => {
    setTitleDraft(plan?.title || "");
    setGoalDraft(plan?.active_goal || "");
  }, [plan?.id, plan?.title, plan?.active_goal]);

  useEffect(() => {
    if (!sessionId) {
      setAvailablePlans([]);
      return;
    }

    let cancelled = false;

    async function loadAvailablePlans() {
      setLoadingPlans(true);
      try {
        const response = await listPlans(sessionId);
        if (!cancelled) {
          setAvailablePlans(response.plans);
        }
      } catch (error) {
        if (!cancelled) {
          setPlanError(error instanceof Error ? error.message : "Failed to load plans");
        }
      } finally {
        if (!cancelled) {
          setLoadingPlans(false);
        }
      }
    }

    void loadAvailablePlans();

    return () => {
      cancelled = true;
    };
  }, [sessionId, plan?.id]);

  useEffect(() => {
    if (!sessionId) {
      setProposals([]);
      return;
    }

    let cancelled = false;

    async function loadProposals() {
      setLoadingProposals(true);
      try {
        const response = await listPlanProposals(sessionId, { limit: 20 });
        if (!cancelled) {
          setProposals(response.proposals);
        }
      } catch (error) {
        if (!cancelled) {
          setPlanError(error instanceof Error ? error.message : "Failed to load plan proposals");
        }
      } finally {
        if (!cancelled) {
          setLoadingProposals(false);
        }
      }
    }

    void loadProposals();
    return () => {
      cancelled = true;
    };
  }, [sessionId, plan?.id]);

  useEffect(() => {
    if (!sessionId || !plan?.id) {
      setRevisions([]);
      return;
    }

    let cancelled = false;

    async function loadRevisions() {
      setLoadingRevisions(true);
      try {
        const response = await listPlanRevisions(sessionId, plan.id, { limit: 12 });
        if (!cancelled) {
          setRevisions(response.revisions);
        }
      } catch (error) {
        if (!cancelled) {
          setPlanError(error instanceof Error ? error.message : "Failed to load plan revisions");
        }
      } finally {
        if (!cancelled) {
          setLoadingRevisions(false);
        }
      }
    }

    void loadRevisions();
    return () => {
      cancelled = true;
    };
  }, [sessionId, plan?.id]);

  useEffect(() => {
    if (!proposals.length) {
      setSelectedProposalId(null);
      return;
    }
    setSelectedProposalId((current) => {
      if (current && proposals.some((proposal) => proposal.id === current)) {
        return current;
      }
      return proposals[0]?.id || null;
    });
  }, [proposals]);

  useEffect(() => {
    if (!revisions.length) {
      setSelectedRevisionId(null);
      return;
    }
    setSelectedRevisionId((current) => {
      if (current && revisions.some((revision) => revision.id === current)) {
        return current;
      }
      return revisions[0]?.id || null;
    });
  }, [revisions]);

  const pendingProposalCount = proposals.filter((proposal) => proposal.status === "pending").length;

  if (!plan) {
    return (
      <div className="plan-panel">
        <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
          No active plan for this session
        </div>
        <ProposalSection
          proposals={proposals}
          loading={loadingProposals}
          sessionId={sessionId}
          isMutating={isMutating}
          onMutation={async (action) => {
            if (!sessionId) {
              return;
            }
            setIsMutating(true);
            setPlanError(null);
            try {
              await action();
              const response = await listPlanProposals(sessionId, { limit: 20 });
              setProposals(response.proposals);
              if (plan?.id) {
                const revisionResponse = await listPlanRevisions(sessionId, plan.id, { limit: 12 });
                setRevisions(revisionResponse.revisions);
              }
            } catch (error) {
              setPlanError(error instanceof Error ? error.message : "Proposal update failed");
            } finally {
              setIsMutating(false);
            }
          }}
        />
      </div>
    );
  }

  const items = [...(plan.items || [])].sort((a, b) => a.position - b.position).filter((item) => !item.archived);
  const doneCount = items.filter((item) => item.status === "done").length;
  const progress = items.length > 0 ? Math.round((doneCount / items.length) * 100) : 0;
  const activeItemId = plan.next_item?.id || items.find((item) => item.status === "doing")?.id || null;
  const searchTerm = planSearch.trim().toLowerCase();
  const matchingPlans = availablePlans.filter((candidate) => {
    if (!searchTerm) {
      return true;
    }
    return buildPlanLabel(candidate).toLowerCase().includes(searchTerm);
  });
  const titleDirty = titleDraft.trim() !== (plan.title || "");
  const goalDirty = goalDraft.trim() !== (plan.active_goal || "");

  async function runPlanMutation(action: () => Promise<void>) {
    if (!sessionId) {
      return;
    }
    setIsMutating(true);
    setPlanError(null);
    try {
      await action();
      await onPlanRefresh();
      const revisionPromise = plan?.id
        ? listPlanRevisions(sessionId, plan.id, { limit: 12 })
        : Promise.resolve({ plan_id: "", revisions: [] as PlanRevisionRecord[] });
      const [planResponse, proposalResponse, revisionResponse] = await Promise.all([
        listPlans(sessionId),
        listPlanProposals(sessionId, { limit: 20 }),
        revisionPromise,
      ]);
      setAvailablePlans(planResponse.plans);
      setProposals(proposalResponse.proposals);
      setRevisions(revisionResponse.revisions);
    } catch (error) {
      setPlanError(error instanceof Error ? error.message : "Plan update failed");
    } finally {
      setIsMutating(false);
    }
  }

  async function handleAddItem(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = newItemText.trim();
    if (!content || !sessionId) {
      return;
    }
    await runPlanMutation(async () => {
      await addPlanItem(sessionId, plan.id, { content });
      setNewItemText("");
    });
  }

  async function handleCreatePlan(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sessionId) {
      return;
    }
    const title = newPlanTitle.trim();
    const activeGoal = newPlanGoal.trim();
    if (!title && !activeGoal) {
      return;
    }
    await runPlanMutation(async () => {
      await createPlan(sessionId, {
        title,
        active_goal: activeGoal,
        activate: true,
      });
      setNewPlanTitle("");
      setNewPlanGoal("");
      setPlanSearch("");
    });
  }

  async function handleSaveMetadata(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sessionId || (!titleDirty && !goalDirty)) {
      return;
    }
    await runPlanMutation(async () => {
      await updateActivePlan(sessionId, {
        title: titleDraft.trim(),
        active_goal: goalDraft.trim(),
      });
    });
  }

  async function handleDeleteCurrentPlan() {
    if (!sessionId) {
      return;
    }
    const confirmed = window.confirm(`Delete plan "${buildPlanLabel(plan)}"? This removes its items and revision history.`);
    if (!confirmed) {
      return;
    }
    setIsMutating(true);
    setPlanError(null);
    try {
      await deletePlanRecord(sessionId, plan.id);
      await onPlanRefresh();
      const [planResponse, proposalResponse, nextPlan] = await Promise.all([
        listPlans(sessionId),
        listPlanProposals(sessionId, { limit: 20 }),
        getActivePlanRecord(sessionId),
      ]);
      setAvailablePlans(planResponse.plans);
      setProposals(proposalResponse.proposals);
      if (nextPlan?.id) {
        const revisionResponse = await listPlanRevisions(sessionId, nextPlan.id, { limit: 12 });
        setRevisions(revisionResponse.revisions);
      } else {
        setRevisions([]);
      }
    } catch (error) {
      setPlanError(error instanceof Error ? error.message : "Plan delete failed");
    } finally {
      setIsMutating(false);
    }
  }

  return (
    <div className="plan-panel">
      <div className="plan-header">
        <div className="plan-heading">
          <div className="plan-eyebrow">Active Plan</div>
          <div className="plan-title">{plan.title || plan.active_goal || "Active Plan"}</div>
        </div>
        <div className="plan-header-actions">
          <label className="plan-switcher">
            <span className="plan-switcher-label">Switch</span>
            <CustomSelect
              triggerClassName="plan-switcher-select"
              value={plan.id}
              onChange={(nextPlanId) => {
                if (!nextPlanId || nextPlanId === plan.id || !sessionId) {
                  return;
                }
                void runPlanMutation(async () => {
                  await activatePlan(sessionId, nextPlanId);
                });
              }}
              disabled={isMutating || loadingPlans || !sessionId || availablePlans.length <= 1}
              options={availablePlans.map((candidate) => ({ value: candidate.id, label: buildPlanLabel(candidate) }))}
              ariaLabel="Switch active plan"
            />
          </label>
          <button
            className="plan-danger-button"
            type="button"
            onClick={() => {
              void handleDeleteCurrentPlan();
            }}
            disabled={isMutating || !sessionId}
          >
            Delete Plan
          </button>
        </div>
      </div>

      <div className="plan-rolodex">
        <input
          className="plan-search-input"
          type="text"
          placeholder="Search plans"
          value={planSearch}
          onChange={(event) => setPlanSearch(event.target.value)}
          disabled={loadingPlans || isMutating}
        />
        <div className="plan-search-results">
          {matchingPlans.slice(0, 5).map((candidate) => (
            <button
              key={candidate.id}
              className={`plan-result-chip ${candidate.id === plan.id ? "active" : ""}`}
              type="button"
              disabled={isMutating || candidate.id === plan.id || !sessionId}
              onClick={() => {
                void runPlanMutation(async () => {
                  await activatePlan(sessionId, candidate.id);
                });
              }}
            >
              {buildPlanLabel(candidate)}
            </button>
          ))}
          {!matchingPlans.length && <div className="plan-search-empty">No matching plans</div>}
        </div>
      </div>

      <form className="plan-metadata-form" onSubmit={handleSaveMetadata}>
        <input
          className="plan-metadata-input"
          type="text"
          placeholder="Plan title"
          value={titleDraft}
          onChange={(event) => setTitleDraft(event.target.value)}
          disabled={isMutating || !sessionId}
        />
        <textarea
          className="plan-metadata-textarea"
          placeholder="Current goal"
          value={goalDraft}
          onChange={(event) => setGoalDraft(event.target.value)}
          disabled={isMutating || !sessionId}
          rows={3}
        />
        <button
          className="plan-secondary-button"
          type="submit"
          disabled={isMutating || !sessionId || (!titleDirty && !goalDirty)}
        >
          Save Details
        </button>
      </form>

      <form className="plan-create-form" onSubmit={handleCreatePlan}>
        <input
          className="plan-create-input"
          type="text"
          placeholder="New plan title"
          value={newPlanTitle}
          onChange={(event) => setNewPlanTitle(event.target.value)}
          disabled={isMutating || !sessionId}
        />
        <input
          className="plan-create-input"
          type="text"
          placeholder="New plan goal"
          value={newPlanGoal}
          onChange={(event) => setNewPlanGoal(event.target.value)}
          disabled={isMutating || !sessionId}
        />
        <button
          className="plan-secondary-button"
          type="submit"
          disabled={isMutating || !sessionId || (!newPlanTitle.trim() && !newPlanGoal.trim())}
        >
          Create Plan
        </button>
      </form>

      <div className="plan-progress-bar">
        <div className="plan-progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="plan-progress-label">{progress}% complete · {items.length} items</div>

      <ProposalSection
        proposals={proposals}
        loading={loadingProposals}
        sessionId={sessionId}
        isMutating={isMutating}
        onMutation={runPlanMutation}
        pendingCount={pendingProposalCount}
        selectedProposalId={selectedProposalId}
        onSelectProposal={setSelectedProposalId}
      />

      <RevisionSection
        revisions={revisions}
        loading={loadingRevisions}
        selectedRevisionId={selectedRevisionId}
        onSelectRevision={setSelectedRevisionId}
      />

      <form className="plan-composer" onSubmit={handleAddItem}>
        <input
          className="plan-composer-input"
          type="text"
          placeholder="Add plan item"
          value={newItemText}
          onChange={(event) => setNewItemText(event.target.value)}
          disabled={isMutating || !sessionId}
        />
        <button
          className="plan-composer-button"
          type="submit"
          disabled={isMutating || !sessionId || !newItemText.trim()}
        >
          Add
        </button>
      </form>

      {planError && <div className="plan-error">{planError}</div>}

      {items.map((item, index) => (
        <div key={item.id} className={`plan-item ${activeItemId === item.id ? "active" : ""} ${item.status === "done" ? "done" : ""}`}>
          <div
            className={`plan-item-status ${
              item.status === "done" ? "done" : item.status === "doing" ? "active" : item.status === "blocked" ? "blocked" : "pending"
            }`}
          >
            {item.status === "done" ? "✓" : item.status === "doing" ? "●" : item.status === "blocked" ? "✕" : `${index + 1}`}
          </div>
          <div className="plan-item-main">
            <span className="plan-item-title">{item.content}</span>
            <div className="plan-item-controls">
              <CustomSelect
                triggerClassName="plan-item-select"
                value={item.status}
                onChange={(nextStatus) => {
                  void runPlanMutation(async () => {
                    await updatePlanItem(sessionId, plan.id, item.id, { status: nextStatus });
                  });
                }}
                disabled={isMutating || !sessionId}
                options={buildSelectOptions(PLAN_ITEM_STATUSES)}
                ariaLabel="Plan item status"
              />
              <button
                className="plan-item-archive"
                type="button"
                onClick={() => {
                  void runPlanMutation(async () => {
                    await updatePlanItem(sessionId, plan.id, item.id, { archived: true });
                  });
                }}
                disabled={isMutating || !sessionId}
              >
                Archive
              </button>
            </div>
          </div>
          <span className={`plan-item-priority ${priorityClass(item.status)}`}>{item.status}</span>
        </div>
      ))}
    </div>
  );
}

function buildPlanLabel(plan: Pick<PlanSummaryRecord, "title" | "active_goal">): string {
  return plan.title || plan.active_goal || "Untitled Plan";
}

function ProposalSection({
  proposals,
  loading,
  sessionId,
  isMutating,
  onMutation,
  pendingCount,
  selectedProposalId,
  onSelectProposal,
}: {
  proposals: PlanProposalRecord[];
  loading: boolean;
  sessionId: string | null;
  isMutating: boolean;
  onMutation: (action: () => Promise<void>) => Promise<void>;
  pendingCount?: number;
  selectedProposalId: string | null;
  onSelectProposal: (proposalId: string) => void;
}) {
  const visibleProposals = proposals.slice(0, 6);
  const selectedProposal = proposals.find((proposal) => proposal.id === selectedProposalId) || visibleProposals[0] || null;

  return (
    <div className="proposal-panel">
      <div className="proposal-panel-header">
        <div>
          <div className="plan-eyebrow">Plan Proposals</div>
          <div className="proposal-panel-title">
            {pendingCount ?? proposals.filter((proposal) => proposal.status === "pending").length} pending review
          </div>
        </div>
        {loading && <span className="proposal-loading">syncing</span>}
      </div>
      {!visibleProposals.length && !loading && (
        <div className="proposal-empty">No plan proposals queued for this session.</div>
      )}
      {visibleProposals.map((proposal) => {
        const payloadSummary = summarizeProposalPayload(proposal);
        return (
          <button
            key={proposal.id}
            className={`proposal-card ${selectedProposal?.id === proposal.id ? "selected" : ""}`}
            type="button"
            onClick={() => onSelectProposal(proposal.id)}
          >
            <div className="proposal-card-header">
              <div className="proposal-card-title">{proposal.summary || humanizeProposalType(proposal.proposal_type)}</div>
              <span className={`proposal-status proposal-status-${proposal.status}`}>{proposal.status}</span>
            </div>
            <div className="proposal-card-meta">
              {proposal.proposed_by} · {humanizeProposalType(proposal.proposal_type)} · {formatDateTime(proposal.created_at)}
            </div>
            {payloadSummary && <div className="proposal-card-summary">{payloadSummary}</div>}
            {proposal.resolution_note && <div className="proposal-card-note">{proposal.resolution_note}</div>}
            {proposal.status === "pending" && sessionId && (
              <div className="proposal-card-actions">
                <button
                  className="plan-secondary-button proposal-accept-button"
                  type="button"
                  disabled={isMutating}
                  onClick={() => {
                    void onMutation(async () => {
                      await acceptPlanProposal(sessionId, proposal.id);
                    });
                  }}
                >
                  Accept
                </button>
                <button
                  className="plan-secondary-button proposal-reject-button"
                  type="button"
                  disabled={isMutating}
                  onClick={() => {
                    void onMutation(async () => {
                      await rejectPlanProposal(sessionId, proposal.id, { resolution_note: "Rejected from panel" });
                    });
                  }}
                >
                  Reject
                </button>
              </div>
            )}
          </button>
        );
      })}
      {selectedProposal && (
        <div className="proposal-detail-card">
          <div className="proposal-detail-header">
            <div className="proposal-detail-title">Proposal details</div>
            <span className={`proposal-status proposal-status-${selectedProposal.status}`}>{selectedProposal.status}</span>
          </div>
          <div className="proposal-detail-grid">
            <div>
              <span className="proposal-detail-label">Source</span>
              <span className="proposal-detail-value">{selectedProposal.proposed_by}</span>
            </div>
            <div>
              <span className="proposal-detail-label">Type</span>
              <span className="proposal-detail-value">{humanizeProposalType(selectedProposal.proposal_type)}</span>
            </div>
            <div>
              <span className="proposal-detail-label">Created</span>
              <span className="proposal-detail-value">{formatDateTime(selectedProposal.created_at)}</span>
            </div>
            {selectedProposal.resolved_at && (
              <div>
                <span className="proposal-detail-label">Resolved</span>
                <span className="proposal-detail-value">{formatDateTime(selectedProposal.resolved_at)}</span>
              </div>
            )}
          </div>
          <div className="proposal-detail-block">
            <div className="proposal-detail-label">Payload</div>
            <pre className="proposal-detail-payload">{JSON.stringify(selectedProposal.payload, null, 2)}</pre>
          </div>
          {selectedProposal.resolution_note && (
            <div className="proposal-detail-block">
              <div className="proposal-detail-label">Resolution note</div>
              <div className="proposal-detail-value">{selectedProposal.resolution_note}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RevisionSection({
  revisions,
  loading,
  selectedRevisionId,
  onSelectRevision,
}: {
  revisions: PlanRevisionRecord[];
  loading: boolean;
  selectedRevisionId: string | null;
  onSelectRevision: (revisionId: string) => void;
}) {
  const visibleRevisions = revisions.slice(0, 8);
  const selectedRevision = revisions.find((revision) => revision.id === selectedRevisionId) || visibleRevisions[0] || null;

  return (
    <div className="revision-panel">
      <div className="proposal-panel-header">
        <div>
          <div className="plan-eyebrow">Revision History</div>
          <div className="proposal-panel-title">{revisions.length} recent revisions</div>
        </div>
        {loading && <span className="proposal-loading">syncing</span>}
      </div>
      {!visibleRevisions.length && !loading && <div className="proposal-empty">No revisions recorded yet.</div>}
      {visibleRevisions.map((revision) => (
        <button
          key={revision.id}
          className={`revision-row ${selectedRevision?.id === revision.id ? "selected" : ""}`}
          type="button"
          onClick={() => onSelectRevision(revision.id)}
        >
          <div className="revision-row-header">
            <span className="revision-row-type">{humanizeProposalType(revision.change_type)}</span>
            <span className="proposal-card-meta">{formatDateTime(revision.created_at)}</span>
          </div>
          <div className="revision-row-summary">{revision.summary || "No summary"}</div>
        </button>
      ))}
      {selectedRevision && (
        <div className="revision-detail-card">
          <div className="proposal-detail-header">
            <div className="proposal-detail-title">Revision details</div>
            <span className="proposal-card-meta">{formatDateTime(selectedRevision.created_at)}</span>
          </div>
          <div className="proposal-detail-grid">
            <div>
              <span className="proposal-detail-label">Change</span>
              <span className="proposal-detail-value">{humanizeProposalType(selectedRevision.change_type)}</span>
            </div>
            <div>
              <span className="proposal-detail-label">Summary</span>
              <span className="proposal-detail-value">{selectedRevision.summary || "No summary"}</span>
            </div>
            <div>
              <span className="proposal-detail-label">Plan title</span>
              <span className="proposal-detail-value">{selectedRevision.snapshot.plan.title || "Untitled Plan"}</span>
            </div>
            <div>
              <span className="proposal-detail-label">Next item</span>
              <span className="proposal-detail-value">{selectedRevision.snapshot.next_item?.content || "None"}</span>
            </div>
          </div>
          <div className="revision-diff-grid">
            <RevisionDiffList label="Changed fields" values={selectedRevision.diff.changed_fields} emptyLabel="No plan field changes" />
            <RevisionDiffList label="Items added" values={selectedRevision.diff.item_ids_added} emptyLabel="No items added" />
            <RevisionDiffList label="Items updated" values={selectedRevision.diff.item_ids_updated} emptyLabel="No items updated" />
            <RevisionDiffList label="Items removed" values={selectedRevision.diff.item_ids_removed} emptyLabel="No items removed" />
          </div>
        </div>
      )}
    </div>
  );
}

function RevisionDiffList({ label, values, emptyLabel }: { label: string; values: string[]; emptyLabel: string }) {
  return (
    <div className="revision-diff-card">
      <div className="proposal-detail-label">{label}</div>
      {values.length ? (
        <div className="revision-diff-list">
          {values.map((value) => (
            <span key={value} className="revision-diff-pill">{value}</span>
          ))}
        </div>
      ) : (
        <div className="proposal-card-meta">{emptyLabel}</div>
      )}
    </div>
  );
}

function humanizeProposalType(proposalType: string): string {
  return proposalType.replace(/_/g, " ");
}

function summarizeProposalPayload(proposal: PlanProposalRecord): string {
  const payload = proposal.payload || {};
  if (proposal.proposal_type === "add_item" && typeof payload.content === "string") {
    return payload.content;
  }
  if (proposal.proposal_type === "create_plan") {
    const title = typeof payload.title === "string" ? payload.title : "Untitled plan";
    const activeGoal = typeof payload.active_goal === "string" && payload.active_goal ? ` · ${payload.active_goal}` : "";
    return `${title}${activeGoal}`;
  }
  if (proposal.proposal_type === "reorder_item") {
    const itemId = typeof payload.item_id === "string" ? payload.item_id : "item";
    const position = typeof payload.position === "number" ? payload.position : "?";
    return `Move ${itemId} to position ${position}`;
  }
  if (proposal.proposal_type === "activate_plan" || proposal.proposal_type === "archive_plan") {
    return typeof payload.plan_id === "string" ? payload.plan_id : "Stored plan change";
  }
  return "";
}

function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function humanizeAttentionMode(mode: string): string {
  return mode.charAt(0).toUpperCase() + mode.slice(1);
}

function formatAttentionDiffValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : "none";
  }
  if (value === null || value === undefined || value === "") {
    return "none";
  }
  return String(value);
}

function AttentionTab({
  workspaceId,
  onAttentionRefresh,
}: {
  workspaceId: string | null;
  onAttentionRefresh?: () => Promise<void>;
}) {
  const [profile, setProfile] = useState<WorkspaceAttentionProfileRecord | null>(null);
  const [draft, setDraft] = useState<WorkspaceAttentionProfileRecord | null>(null);
  const [instruction, setInstruction] = useState("");
  const [compileResult, setCompileResult] = useState<WorkspaceAttentionCompileResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceId) {
      setProfile(null);
      setDraft(null);
      setCompileResult(null);
      return;
    }

    let cancelled = false;

    async function loadAttentionProfile() {
      setLoading(true);
      setErrorMessage(null);
      try {
        const nextProfile = await getWorkspaceAttentionProfile(workspaceId);
        if (!cancelled) {
          setProfile(nextProfile);
          setDraft(nextProfile);
        }
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error instanceof Error ? error.message : "Failed to load workspace attention policy");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadAttentionProfile();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  if (!workspaceId) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
        Select a workspace to manage durable attention policy
      </div>
    );
  }

  if (!draft) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
        {loading ? "Loading workspace attention policy..." : "No attention profile available"}
      </div>
    );
  }

  const updateDraft = <K extends keyof WorkspaceAttentionProfileRecord>(field: K, value: WorkspaceAttentionProfileRecord[K]) => {
    setDraft((current) => (current ? { ...current, [field]: value } : current));
  };

  const togglePastimeType = (pastimeType: string) => {
    setDraft((current) => {
      if (!current) {
        return current;
      }
      const nextTypes = new Set(current.allowed_pastime_types);
      if (nextTypes.has(pastimeType)) {
        nextTypes.delete(pastimeType);
      } else {
        nextTypes.add(pastimeType);
      }
      return {
        ...current,
        allowed_pastime_types: ATTENTION_PASTIME_OPTIONS.filter((item) => nextTypes.has(item)),
      };
    });
  };

  async function handleSaveManualPolicy(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !draft) {
      return;
    }
    setSaving(true);
    setErrorMessage(null);
    try {
      const nextProfile = await patchWorkspaceAttentionProfile(workspaceId, {
        mode: draft.mode,
        baseline_priority: draft.baseline_priority,
        current_attention_level: draft.current_attention_level,
        max_idle_budget: draft.max_idle_budget,
        allowed_pastime_types: draft.allowed_pastime_types,
        notification_threshold: draft.notification_threshold,
        freshness_target: draft.freshness_target,
        review_at: draft.review_at,
        expires_at: draft.expires_at,
        user_rationale: draft.user_rationale,
      });
      setProfile(nextProfile);
      setDraft(nextProfile);
      setCompileResult(null);
      await onAttentionRefresh?.();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to update workspace attention policy");
    } finally {
      setSaving(false);
    }
  }

  async function handleCompileInstruction() {
    if (!workspaceId || !instruction.trim()) {
      return;
    }
    setSaving(true);
    setErrorMessage(null);
    try {
      const result = await compileWorkspaceAttentionProfile(workspaceId, {
        instruction: instruction.trim(),
        apply: true,
      });
      setCompileResult(result);
      setProfile(result.profile);
      setDraft(result.profile);
      await onAttentionRefresh?.();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to compile workspace attention policy");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="attention-panel">
      <div className="attention-card attention-summary-card">
        <div className="plan-eyebrow">Workspace Attention</div>
        <div className="attention-summary-grid">
          <AttentionSummaryMetric label="Mode" value={humanizeAttentionMode(draft.mode)} detail={`${draft.current_attention_level}% current`} />
          <AttentionSummaryMetric label="Alerts" value={draft.notification_threshold} detail={draft.freshness_target} />
          <AttentionSummaryMetric label="Idle Budget" value={String(draft.max_idle_budget)} detail={`${draft.allowed_pastime_types.length} pastime types`} />
        </div>
        <div className="attention-chip-list">
          {draft.allowed_pastime_types.length ? (
            draft.allowed_pastime_types.map((pastimeType) => (
              <span key={pastimeType} className="attention-chip">{pastimeType.replaceAll("_", " ")}</span>
            ))
          ) : (
            <span className="attention-empty">No pastime types currently allowed</span>
          )}
        </div>
      </div>

      <div className="attention-card">
        <div className="settings-group-title">Intent Compiler</div>
        <div className="settings-help">
          Write durable policy in natural language. Clo compiles it into scheduler-facing workspace attention settings and shows the policy diff.
        </div>
        <textarea
          className="settings-paths-input"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          placeholder="For the next two weeks, prioritize OpenCloset 70%, keep review noise low, and notify me immediately if something important changes."
          rows={5}
          disabled={saving}
        />
        <div className="settings-actions">
          <button className="plan-secondary-button" type="button" onClick={() => void handleCompileInstruction()} disabled={saving || !instruction.trim()}>
            {saving ? "Applying..." : "Compile + Apply"}
          </button>
        </div>
        {compileResult && (
          <div className="attention-diff-list">
            <div className="proposal-detail-label">Policy diff</div>
            {compileResult.diff.length ? (
              compileResult.diff.map((change) => (
                <AttentionDiffRow key={change.field} change={change} />
              ))
            ) : (
              <div className="attention-empty">No policy fields changed.</div>
            )}
            <div className="attention-effects-card">
              <div className="proposal-detail-label">Scheduler effects</div>
              <div className="proposal-detail-value">{compileResult.scheduler_effects.mode_summary}</div>
              <div className="proposal-card-meta">
                Budget {compileResult.scheduler_effects.max_idle_budget} · alerts {compileResult.scheduler_effects.notification_threshold} · freshness {compileResult.scheduler_effects.freshness_target}
              </div>
            </div>
          </div>
        )}
      </div>

      <form className="attention-card attention-form" onSubmit={handleSaveManualPolicy}>
        <div className="settings-group-title">Manual Policy</div>
        <div className="attention-form-grid">
          <label className="attention-field">
            <span className="proposal-detail-label">Mode</span>
            <CustomSelect
              triggerClassName="plan-switcher-select"
              value={draft.mode}
              onChange={(nextValue) => updateDraft("mode", nextValue)}
              disabled={saving}
              options={buildSelectOptions(ATTENTION_MODE_OPTIONS)}
              ariaLabel="Attention mode"
            />
          </label>
          <label className="attention-field">
            <span className="proposal-detail-label">Notification threshold</span>
            <CustomSelect
              triggerClassName="plan-switcher-select"
              value={draft.notification_threshold}
              onChange={(nextValue) => updateDraft("notification_threshold", nextValue)}
              disabled={saving}
              options={buildSelectOptions(ATTENTION_NOTIFICATION_OPTIONS)}
              ariaLabel="Notification threshold"
            />
          </label>
          <label className="attention-field">
            <span className="proposal-detail-label">Baseline priority</span>
            <input
              className="plan-metadata-input"
              type="number"
              min={0}
              max={100}
              value={draft.baseline_priority}
              onChange={(event) => updateDraft("baseline_priority", Number(event.target.value || 0))}
              disabled={saving}
            />
          </label>
          <label className="attention-field">
            <span className="proposal-detail-label">Current attention</span>
            <input
              className="plan-metadata-input"
              type="number"
              min={0}
              max={100}
              value={draft.current_attention_level}
              onChange={(event) => updateDraft("current_attention_level", Number(event.target.value || 0))}
              disabled={saving}
            />
          </label>
          <label className="attention-field">
            <span className="proposal-detail-label">Max idle budget</span>
            <input
              className="plan-metadata-input"
              type="number"
              min={0}
              max={20}
              value={draft.max_idle_budget}
              onChange={(event) => updateDraft("max_idle_budget", Number(event.target.value || 0))}
              disabled={saving}
            />
          </label>
          <label className="attention-field">
            <span className="proposal-detail-label">Freshness target</span>
            <CustomSelect
              triggerClassName="plan-switcher-select"
              value={draft.freshness_target}
              onChange={(nextValue) => updateDraft("freshness_target", nextValue)}
              disabled={saving}
              options={buildSelectOptions(ATTENTION_FRESHNESS_OPTIONS)}
              ariaLabel="Freshness target"
            />
          </label>
        </div>
        <div className="attention-field">
          <span className="proposal-detail-label">Allowed pastime types</span>
          <div className="attention-checkbox-grid">
            {ATTENTION_PASTIME_OPTIONS.map((pastimeType) => (
              <label key={pastimeType} className="settings-inline-checkbox">
                <input
                  type="checkbox"
                  checked={draft.allowed_pastime_types.includes(pastimeType)}
                  onChange={() => togglePastimeType(pastimeType)}
                  disabled={saving}
                />
                <span>{pastimeType.replaceAll("_", " ")}</span>
              </label>
            ))}
          </div>
        </div>
        <label className="attention-field">
          <span className="proposal-detail-label">Rationale</span>
          <textarea
            className="settings-paths-input"
            value={draft.user_rationale}
            onChange={(event) => updateDraft("user_rationale", event.target.value)}
            rows={4}
            disabled={saving}
          />
        </label>
        <div className="attention-meta-grid">
          <div className="proposal-card-meta">Review: {draft.review_at ? formatDateTime(draft.review_at) : "none"}</div>
          <div className="proposal-card-meta">Expires: {draft.expires_at ? formatDateTime(draft.expires_at) : "none"}</div>
        </div>
        <div className="settings-actions">
          <button className="plan-secondary-button" type="submit" disabled={saving}>
            {saving ? "Saving..." : "Save Policy"}
          </button>
        </div>
      </form>

      {errorMessage && <div className="plan-error">{errorMessage}</div>}
    </div>
  );
}

function AttentionSummaryMetric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="attention-summary-metric">
      <div className="proposal-detail-label">{label}</div>
      <div className="attention-summary-value">{value}</div>
      <div className="proposal-card-meta">{detail}</div>
    </div>
  );
}

function AttentionDiffRow({ change }: { change: WorkspaceAttentionPolicyDiffRecord }) {
  return (
    <div className="attention-diff-row">
      <div className="attention-diff-header">
        <span className="attention-diff-label">{change.label}</span>
        <span className="proposal-card-meta">{change.reason}</span>
      </div>
      <div className="attention-diff-values">
        <span>{formatAttentionDiffValue(change.before)}</span>
        <span>→</span>
        <span>{formatAttentionDiffValue(change.after)}</span>
      </div>
    </div>
  );
}

function QueueTab() {
  return (
    <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
      Queue candidates stay thin in this pass.
      <div style={{ marginTop: 4, fontSize: 11 }}>
        Runtime truth is now shown in the dock from live session and event data.
      </div>
    </div>
  );
}

function SettingsTab({ sessionId }: { sessionId: string | null }) {
  const [policy, setPolicy] = useState<ToolPolicyRecord | null>(null);
  const [allowedPathsText, setAllowedPathsText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setPolicy(null);
      setAllowedPathsText("");
      return;
    }

    let cancelled = false;

    async function loadToolPolicy() {
      setLoading(true);
      setErrorMessage(null);
      try {
        const response = await getSessionToolPolicy(sessionId);
        if (!cancelled) {
          setPolicy(response.tool_policy);
          setAllowedPathsText(response.tool_policy.allowed_paths.join("\n"));
        }
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error instanceof Error ? error.message : "Failed to load session settings");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadToolPolicy();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  if (!sessionId) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
        Select a session to configure tools and path scope
      </div>
    );
  }

  const enabledTools = new Set(policy?.enabled_tools || []);
  const destructiveTools = new Set(policy?.allow_destructive_tools || []);

  const toggleEnabledTool = (toolName: string) => {
    setPolicy((current) => {
      if (!current) {
        return current;
      }
      const nextEnabled = new Set(current.enabled_tools);
      const nextDestructive = new Set(current.allow_destructive_tools);
      if (nextEnabled.has(toolName)) {
        nextEnabled.delete(toolName);
        nextDestructive.delete(toolName);
      } else {
        nextEnabled.add(toolName);
      }
      return {
        ...current,
        enabled_tools: Array.from(nextEnabled),
        allow_destructive_tools: Array.from(nextDestructive),
      };
    });
  };

  const toggleDestructiveTool = (toolName: string) => {
    setPolicy((current) => {
      if (!current) {
        return current;
      }
      const nextEnabled = new Set(current.enabled_tools);
      const nextDestructive = new Set(current.allow_destructive_tools);
      nextEnabled.add(toolName);
      if (nextDestructive.has(toolName)) {
        nextDestructive.delete(toolName);
      } else {
        nextDestructive.add(toolName);
      }
      return {
        ...current,
        enabled_tools: Array.from(nextEnabled),
        allow_destructive_tools: Array.from(nextDestructive),
      };
    });
  };

  const handleSave = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!policy) {
      return;
    }
    const allowedPaths = allowedPathsText
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);

    setSaving(true);
    setErrorMessage(null);
    try {
      const response = await patchSessionToolPolicy(sessionId, {
        enabled_tools: policy.enabled_tools,
        allow_destructive_tools: policy.allow_destructive_tools,
        allowed_paths: allowedPaths,
      });
      setPolicy(response.tool_policy);
      setAllowedPathsText(response.tool_policy.allowed_paths.join("\n"));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to save session settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="settings-panel" onSubmit={handleSave}>
      <div className="settings-card">
        <div className="plan-eyebrow">Session Tool Policy</div>
        <div className="proposal-panel-title">What CLO can use in this session</div>
        <div className="settings-help">
          Enable the tools this session should expose to the model, and separately mark write/edit style tools as
          pre-approved destructive actions.
        </div>
      </div>

      {TOOL_POLICY_GROUPS.map((group) => (
        <div key={group.label} className="settings-card">
          <div className="settings-group-title">{group.label}</div>
          <div className="settings-tool-list">
            {group.tools.map((toolName) => (
              <label key={toolName} className="settings-tool-row">
                <input
                  type="checkbox"
                  checked={enabledTools.has(toolName)}
                  onChange={() => toggleEnabledTool(toolName)}
                  disabled={saving || loading || !policy}
                />
                <span className="settings-tool-name">{toolName}</span>
                {(toolName === "write" || toolName === "edit" || toolName === "exec" || toolName === "process") && (
                  <span className="settings-inline-checkbox">
                    <input
                      type="checkbox"
                      checked={destructiveTools.has(toolName)}
                      onChange={() => toggleDestructiveTool(toolName)}
                      disabled={saving || loading || !policy || !enabledTools.has(toolName)}
                    />
                    <span>pre-approved destructive</span>
                  </span>
                )}
              </label>
            ))}
          </div>
        </div>
      ))}

      <div className="settings-card">
        <div className="settings-group-title">Allowed Paths</div>
        <div className="settings-help">
          One path per line. CLO can only read, write, edit, or execute inside these roots.
        </div>
        <textarea
          className="settings-paths-input"
          value={allowedPathsText}
          onChange={(event) => setAllowedPathsText(event.target.value)}
          rows={5}
          disabled={saving || loading || !policy}
          placeholder="D:\\openclaw\\opencloset"
        />
      </div>

      {errorMessage && <div className="memory-error">{errorMessage}</div>}

      <div className="settings-actions">
        <button className="mode-btn active" type="submit" disabled={saving || loading || !policy}>
          {saving ? "Saving..." : "Save Settings"}
        </button>
      </div>
    </form>
  );
}

function MemoryTab({ sessionId }: { sessionId: string | null }) {
  const [memory, setMemory] = useState<SessionMemoryRecord | null>(null);
  const [searchResults, setSearchResults] = useState<SessionMemorySearchRecord | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [noteText, setNoteText] = useState("");
  const [alsoDaily, setAlsoDaily] = useState(true);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [searching, setSearching] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setMemory(null);
      setSearchResults(null);
      return;
    }

    let cancelled = false;

    async function loadMemory() {
      setLoading(true);
      setErrorMessage(null);
      try {
        const response = await getSessionMemory(sessionId);
        if (!cancelled) {
          setMemory(response);
        }
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error instanceof Error ? error.message : "Failed to load memory");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadMemory();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sessionId || !noteText.trim()) {
      return;
    }

    setSaving(true);
    setErrorMessage(null);
    try {
      const updated = await addSessionMemory(sessionId, {
        content: noteText.trim(),
        also_daily: alsoDaily,
      });
      setMemory(updated);
      setNoteText("");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to save memory note");
    } finally {
      setSaving(false);
    }
  }

  async function handleSearch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sessionId || !searchQuery.trim()) {
      return;
    }

    setSearching(true);
    setErrorMessage(null);
    try {
      const response = await searchSessionMemory(sessionId, {
        query: searchQuery.trim(),
        limit: 5,
        include_daily: true,
      });
      setSearchResults(response);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to search memory");
    } finally {
      setSearching(false);
    }
  }

  if (!sessionId) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
        Select a session to view memory
      </div>
    );
  }

  return (
    <div className="memory-panel">
      <form className="memory-composer" onSubmit={handleSubmit}>
        <textarea
          className="memory-composer-input"
          placeholder="Add a durable session note"
          value={noteText}
          onChange={(event) => setNoteText(event.target.value)}
          disabled={saving}
          rows={4}
        />
        <label className="memory-checkbox">
          <input
            type="checkbox"
            checked={alsoDaily}
            onChange={(event) => setAlsoDaily(event.target.checked)}
            disabled={saving}
          />
          Also append to daily log
        </label>
        <button className="memory-composer-button" type="submit" disabled={saving || !noteText.trim()}>
          {saving ? "Saving..." : "Save Note"}
        </button>
      </form>

      <form className="memory-search" onSubmit={handleSearch}>
        <input
          className="memory-search-input"
          type="text"
          placeholder="Search memory"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          disabled={searching}
        />
        <button className="memory-search-button" type="submit" disabled={searching || !searchQuery.trim()}>
          {searching ? "Searching..." : "Search"}
        </button>
      </form>

      {errorMessage && <div className="memory-error">{errorMessage}</div>}

      {searchResults && (
        <section className="memory-document-card">
          <div className="memory-document-title">Search Results · {searchResults.strategy}</div>
          {searchResults.results.length ? (
            <div className="memory-search-results">
              {searchResults.results.map((result) => (
                <div key={result.id} className="memory-search-result">
                  <div className="memory-search-meta">
                    <span>{result.kind === "daily" ? `Daily ${result.date || ""}`.trim() : "Session Diary"}</span>
                    <span>{result.created_at}</span>
                    <span>score {result.score.toFixed(1)}</span>
                  </div>
                  <div className="memory-search-signals">
                    {result.sources.map((source) => (
                      <span key={`${result.id}-${source}`} className={`memory-search-badge memory-search-badge-${source}`}>
                        {source}
                      </span>
                    ))}
                    {result.semantic_score > 0 ? (
                      <span className="memory-search-badge memory-search-badge-score">
                        semantic {result.semantic_score.toFixed(2)}
                      </span>
                    ) : null}
                  </div>
                  <div className="memory-search-snippet">{result.snippet}</div>
                  {result.matched_terms.length ? (
                    <div className="memory-search-meta">matched: {result.matched_terms.join(", ")}</div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="memory-empty">No matches found</div>
          )}
        </section>
      )}

      {loading ? (
        <div className="memory-empty">Loading memory...</div>
      ) : (
        <>
          <MemoryDocument title="Session Diary" content={memory?.session_diary.content || ""} />
          <MemoryDocument
            title={memory?.daily_log.date ? `Daily Log (${memory.daily_log.date})` : "Daily Log"}
            content={memory?.daily_log.content || ""}
          />
        </>
      )}
    </div>
  );
}

function MemoryDocument({ title, content }: { title: string; content: string }) {
  return (
    <section className="memory-document-card">
      <div className="memory-document-title">{title}</div>
      {content ? <pre className="memory-document-body">{content}</pre> : <div className="memory-empty">No entries yet</div>}
    </section>
  );
}

function CapturesTab({
  workspaceId,
  buildProjectId,
  sessionId,
  onEvidenceCreated,
}: {
  workspaceId: string | null;
  buildProjectId: string | null;
  sessionId: string | null;
  onEvidenceCreated?: (evidence: WorkspaceEvidenceRecord) => void;
}) {
  const [captures, setCaptures] = useState<WorkspaceCaptureRecord[]>([]);
  const [content, setContent] = useState("");
  const [source, setSource] = useState("manual");
  const [eventType, setEventType] = useState("text");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [captureError, setCaptureError] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceId) {
      setCaptures([]);
      return;
    }

    let cancelled = false;

    async function loadCaptureContext() {
      setLoading(true);
      try {
        const captureResponse = await listWorkspaceCaptures(workspaceId, { limit: 20 });
        if (!cancelled) {
          setCaptures(captureResponse.captures);
        }
      } catch (error) {
        if (!cancelled) {
          setCaptureError(error instanceof Error ? error.message : "Failed to load captures");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadCaptureContext();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const handleCreateCapture = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!workspaceId) {
      return;
    }
    setSubmitting(true);
    setCaptureError(null);
    try {
      const created = await createWorkspaceCapture(workspaceId, {
        source,
        event_type: eventType,
        content,
        session_id: sessionId || undefined,
        build_project_id: buildProjectId || undefined,
      });
      setCaptures((current) => [created, ...current]);
      setContent("");
    } catch (error) {
      setCaptureError(error instanceof Error ? error.message : "Failed to create capture");
    } finally {
      setSubmitting(false);
    }
  };

  const handlePromote = async (captureId: string) => {
    if (!workspaceId) {
      return;
    }
    setPromotingId(captureId);
    setCaptureError(null);
    try {
      const result = await promoteWorkspaceCapture(workspaceId, captureId);
      setCaptures((current) => current.map((capture) => (capture.id === captureId ? result.capture : capture)));
      onEvidenceCreated?.(result.evidence);
    } catch (error) {
      setCaptureError(error instanceof Error ? error.message : "Failed to promote capture");
    } finally {
      setPromotingId(null);
    }
  };

  if (!workspaceId) {
    return <div className="capture-empty">Attach this session to a workspace to use capture intake.</div>;
  }

  return (
    <div className="capture-panel">
      <div className="proposal-panel-header">
        <div>
          <div className="plan-eyebrow">Capture Intake</div>
          <div className="proposal-panel-title">Workspace captures</div>
        </div>
        {loading && <span className="proposal-loading">loading</span>}
      </div>

      <form className="capture-form" onSubmit={handleCreateCapture}>
        <div className="capture-form-grid">
          <CustomSelect
            triggerClassName="plan-create-input"
            value={source}
            onChange={setSource}
            options={buildSelectOptions(["manual", "phonecloset", "cli", "webhook"])}
            ariaLabel="Capture source"
          />
          <CustomSelect
            triggerClassName="plan-create-input"
            value={eventType}
            onChange={setEventType}
            options={buildSelectOptions(["text", "image", "audio", "location", "app_event"])}
            ariaLabel="Capture event type"
          />
        </div>
        <textarea
          className="plan-metadata-textarea"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          placeholder="Capture what came in, what changed, or what needs routing next"
          rows={4}
          required
        />
        <div className="capture-form-actions">
          <button className="mode-btn active" type="submit" disabled={submitting}>
            {submitting ? "Saving..." : "Add capture"}
          </button>
        </div>
      </form>

      <ApkDeliveryPanel
        workspaceId={workspaceId}
        projectOptions={buildProjectId ? [{ id: buildProjectId, label: "Active build project" }] : []}
        sessionOptions={sessionId ? [{ id: sessionId, label: "Active session", buildProjectId: buildProjectId ?? null }] : []}
        preferredProjectId={buildProjectId}
        preferredSessionId={sessionId}
        originTag="opencloset_browser_capture_panel"
        eyebrow="APK Delivery"
        title="Publish to project queue"
        subtitle="Expose release/debug routing and session linkage without leaving the capture tab."
        emptyProjectMessage="Choose a build project before publishing an APK."
      />

      {captureError && <div className="capture-error">{captureError}</div>}

      <div className="capture-list">
        {captures.map((capture) => (
          <div key={capture.id} className="capture-card">
            <div className="capture-card-header">
              <div>
                <div className="proposal-card-title">{capture.event_type} capture</div>
                <div className="proposal-card-meta">{capture.source} · {formatCaptureDate(capture.received_at)}</div>
              </div>
              <span className={`badge ${capture.status === "processed" ? "badge-success" : "badge-pending"}`}>{capture.status}</span>
            </div>
            <div className="proposal-card-summary">{capture.content}</div>
            <div className="capture-card-actions">
              <button
                className="inbox-action-btn approve"
                type="button"
                disabled={capture.status === "processed" || promotingId === capture.id}
                onClick={() => void handlePromote(capture.id)}
              >
                {promotingId === capture.id ? "Promoting..." : capture.status === "processed" ? "Promoted" : "Promote to evidence"}
              </button>
            </div>
          </div>
        ))}
        {!captures.length && !loading && <div className="capture-empty">No captures in this workspace yet.</div>}
      </div>
    </div>
  );
}

function formatCaptureDate(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function priorityClass(status: string): "high" | "medium" | "low" {
  if (status === "blocked") {
    return "high";
  }
  if (status === "doing") {
    return "medium";
  }
  return "low";
}
