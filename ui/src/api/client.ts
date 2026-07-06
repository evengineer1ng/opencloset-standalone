import type {
  BehaviorStateRecord,
  BuildProjectRecord,
  CloQueueItemRecord,
  CloQueueStateRecord,
  CreateSessionResponse,
  DelegationBudgetRecord,
  DelegationPolicyRecord,
  DelegationSubstrateRecord,
  DelegationTaskRecord,
  ExecuteRunResponse,
  InteractiveProcessActionResponse,
  InteractiveProcessRecord,
  ProviderModelCatalogRecord,
  ProviderRecord,
  SessionMemorySearchRecord,
  SessionMemoryRecord,
  PlanItemRecord,
  PlanProposalRecord,
  PlanRecord,
  PlanRevisionRecord,
  PlanSummaryRecord,
  ProjectDeliveryRecord,
  PokemonBridgeStatusRecord,
  PokemonControlMode,
  SessionDetail,
  SessionAttachmentUploadRecord,
  SessionEventRecord,
  SessionSummary,
  RuntimeAgentRecord,
  StreamEventRecord,
  SubmitMessageResponse,
  ToolPolicyRecord,
  TranscriptMessage,
  WorkspaceAttentionCompileResponse,
  WorkspaceAttentionProfileRecord,
  WorkspaceSignalActionResponse,
  WorkspaceCapturePromotionResponse,
  WorkspaceCaptureRecord,
  WorkspaceEvidenceRecord,
  WorkspaceRuntimeRecord,
  WorkspaceSummary,
  TransientWindowRecord,
} from "./types";

const DEFAULT_API_PROXY_TARGET = "http://127.0.0.1:5000";

function summarizeResponseBody(text: unknown): string {
  if (typeof text !== "string") {
    return "";
  }
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length > 120 ? `${compact.slice(0, 117)}...` : compact;
}

function parseJsonPayload(raw: unknown, context: string, details?: { contentType?: string | null; status?: number }): unknown {
  if (raw == null) {
    return {};
  }

  if (typeof raw !== "string") {
    return raw;
  }

  if (!raw.trim()) {
    return {};
  }

  const contentType = details?.contentType?.toLowerCase() || "";
  const trimmed = raw.trimStart();
  const looksLikeHtml =
    contentType.includes("text/html") ||
    trimmed.startsWith("<!doctype html") ||
    trimmed.startsWith("<html");

  if (looksLikeHtml) {
    const statusSuffix = typeof details?.status === "number" ? ` (status ${details.status})` : "";
    const status = details?.status;
    const likelyCause =
      typeof status === "number" && status >= 500
        ? `The backend returned an HTML error page for ${context}${statusSuffix}. Check the API server logs for the underlying exception.`
        : `OpenCloset API returned HTML instead of JSON for ${context}${statusSuffix}. This usually means the Vite proxy is misconfigured or the backend is not running at ${DEFAULT_API_PROXY_TARGET}.`;
    throw new Error(
      likelyCause,
    );
  }

  try {
    return JSON.parse(raw) as unknown;
  } catch {
    const preview = summarizeResponseBody(raw);
    throw new Error(`OpenCloset API returned invalid JSON for ${context}: ${preview || "<empty response>"}`);
  }
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const text = await response.text();
  const data = parseJsonPayload(text, path, {
    contentType: response.headers.get("content-type"),
    status: response.status,
  }) as Record<string, unknown>;
  if (!response.ok) {
    throw new Error(data?.error || `Request failed: ${response.status}`);
  }
  return data as T;
}

export function listSessions(): Promise<{ sessions: SessionSummary[] }> {
  return apiRequest("/api/sessions");
}

export function listRuntimeAgents(payload?: {
  status?: string;
  session_id?: string;
  domain?: string;
}): Promise<{ channels: RuntimeAgentRecord[] }> {
  const params = new URLSearchParams();
  if (payload?.status) {
    params.set("status", payload.status);
  }
  if (payload?.session_id) {
    params.set("session_id", payload.session_id);
  }
  if (payload?.domain) {
    params.set("domain", payload.domain);
  }
  const query = params.toString();
  return apiRequest(`/api/runtime/agents${query ? `?${query}` : ""}`);
}

export function getPokemonBridgeStatus(channelName: string): Promise<PokemonBridgeStatusRecord> {
  return apiRequest(`/api/runtime/agents/${channelName}/pokemon/bridge`);
}

export function patchPokemonControl(
  channelName: string,
  payload: { mode?: PokemonControlMode; advance_steps?: number; operator_note?: string },
): Promise<PokemonBridgeStatusRecord> {
  return apiRequest(`/api/runtime/agents/${channelName}/pokemon/control`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function listProviders(): Promise<{ providers: ProviderRecord[] }> {
  return apiRequest("/api/providers");
}

export function listProviderModels(providerId: string): Promise<ProviderModelCatalogRecord> {
  return apiRequest(`/api/providers/${providerId}/models`);
}

export function createSession(payload: {
  model: string;
  label: string;
  provider?: string;
  context_window?: number;
  workspace_id?: string;
  build_project_id?: string;
}): Promise<CreateSessionResponse> {
  return apiRequest("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deleteSession(sessionId: string): Promise<{ deleted: string }> {
  return apiRequest(`/api/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export function listWorkspaces(): Promise<{ workspaces: WorkspaceSummary[] }> {
  return apiRequest("/api/workspaces");
}

export function listCloQueue(): Promise<CloQueueStateRecord> {
  return apiRequest("/api/clo-queue");
}

export function createCloQueueItem(payload: {
  session_id: string;
  content: string;
  stop_after_error?: boolean;
}): Promise<CloQueueItemRecord> {
  return apiRequest("/api/clo-queue/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function patchCloQueueSettings(payload: {
  paused?: boolean;
  pause_on_error?: boolean;
}): Promise<{ paused: boolean; pause_on_error: boolean; updated_at: string }> {
  return apiRequest("/api/clo-queue", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function moveCloQueueItem(itemId: string, direction: "up" | "down"): Promise<CloQueueItemRecord> {
  return apiRequest(`/api/clo-queue/items/${itemId}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ direction }),
  });
}

export function cancelCloQueueItem(itemId: string): Promise<CloQueueItemRecord> {
  return apiRequest(`/api/clo-queue/items/${itemId}/cancel`, {
    method: "POST",
  });
}

export function streamCloQueue(handlers: {
  onState?: (state: CloQueueStateRecord) => void;
  onStreamError?: (message: string) => void;
  onTransportError?: () => void;
}): EventSource {
  const streamPath = "/api/clo-queue/stream";
  const source = new EventSource(streamPath);

  const parseQueueState = (event: Event): CloQueueStateRecord | null => {
    const messageEvent = event as MessageEvent<string>;
    try {
      return parseJsonPayload(messageEvent.data, `${streamPath} [state]`) as CloQueueStateRecord;
    } catch (error) {
      handlers.onStreamError?.(error instanceof Error ? error.message : "Invalid queue stream payload");
      source.close();
      return null;
    }
  };

  source.addEventListener("state", (event) => {
    const state = parseQueueState(event);
    if (!state) {
      return;
    }
    handlers.onState?.(state);
  });

  source.addEventListener("error", (event) => {
    const messageEvent = event as MessageEvent<string>;
    if (messageEvent.data) {
      try {
        const data = parseJsonPayload(messageEvent.data, `${streamPath} [error]`) as Record<string, unknown>;
        handlers.onStreamError?.(String(data.message || "Queue stream error"));
      } catch {
        handlers.onStreamError?.("Queue stream error");
      }
    }
  });

  source.onerror = () => {
    handlers.onTransportError?.();
  };

  return source;
}

export function createWorkspace(payload: {
  name: string;
  description?: string;
  kind?: string;
}): Promise<WorkspaceSummary> {
  return apiRequest("/api/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deleteWorkspace(workspaceId: string): Promise<{ deleted: string }> {
  return apiRequest(`/api/workspaces/${workspaceId}`, {
    method: "DELETE",
  });
}

export function listBuildProjects(workspaceId: string): Promise<{ build_projects: BuildProjectRecord[] }> {
  return apiRequest(`/api/workspaces/${workspaceId}/projects`);
}

export function createBuildProject(
  workspaceId: string,
  payload: { name: string; description?: string },
): Promise<BuildProjectRecord> {
  return apiRequest(`/api/workspaces/${workspaceId}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deleteBuildProject(
  workspaceId: string,
  projectId: string,
): Promise<{ deleted: string; workspace_id: string }> {
  return apiRequest(`/api/workspaces/${workspaceId}/projects/${projectId}`, {
    method: "DELETE",
  });
}

export function listProjectDeliveries(
  workspaceId: string,
  projectId: string,
  payload?: { device_id?: string; status?: string; limit?: number },
): Promise<{ workspace_id: string; build_project_id: string; deliveries: ProjectDeliveryRecord[] }> {
  const params = new URLSearchParams();
  if (payload?.device_id) {
    params.set("device_id", payload.device_id);
  }
  if (payload?.status) {
    params.set("status", payload.status);
  }
  if (payload?.limit !== undefined) {
    params.set("limit", String(payload.limit));
  }
  const query = params.toString();
  return apiRequest(`/api/workspaces/${workspaceId}/projects/${projectId}/deliveries${query ? `?${query}` : ""}`);
}

export function publishProjectDelivery(
  workspaceId: string,
  projectId: string,
  payload: {
    file: File;
    device_id?: string;
    session_id?: string;
    metadata?: Record<string, unknown>;
  },
): Promise<ProjectDeliveryRecord> {
  const formData = new FormData();
  formData.append("file", payload.file);
  if (payload.device_id) {
    formData.append("device_id", payload.device_id);
  }
  if (payload.session_id) {
    formData.append("session_id", payload.session_id);
  }
  if (payload.metadata) {
    formData.append("metadata", JSON.stringify(payload.metadata));
  }
  return apiRequest(`/api/workspaces/${workspaceId}/projects/${projectId}/deliveries`, {
    method: "POST",
    body: formData,
  });
}

export function buildAndQueueProjectDelivery(
  workspaceId: string,
  projectId: string,
  payload: {
    variant: "debug" | "release";
    device_id?: string;
    session_id?: string;
    metadata?: Record<string, unknown>;
  },
): Promise<ProjectDeliveryRecord> {
  return apiRequest<{ delivery: ProjectDeliveryRecord }>(`/api/workspaces/${workspaceId}/projects/${projectId}/deliveries/build`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((response) => response.delivery);
}

export function updateProjectDelivery(
  workspaceId: string,
  deliveryId: string,
  payload: {
    status: string;
    device_id?: string;
    note?: string;
    metadata?: Record<string, unknown>;
  },
): Promise<ProjectDeliveryRecord> {
  return apiRequest(`/api/workspaces/${workspaceId}/deliveries/${deliveryId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getWorkspaceRuntime(workspaceId: string): Promise<WorkspaceRuntimeRecord> {
  return apiRequest(`/api/workspaces/${workspaceId}/runtime`);
}

export function getWorkspaceAttentionProfile(workspaceId: string): Promise<WorkspaceAttentionProfileRecord> {
  return apiRequest(`/api/workspaces/${workspaceId}/attention`);
}

export function patchWorkspaceAttentionProfile(
  workspaceId: string,
  payload: Partial<WorkspaceAttentionProfileRecord>,
): Promise<WorkspaceAttentionProfileRecord> {
  return apiRequest(`/api/workspaces/${workspaceId}/attention`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function compileWorkspaceAttentionProfile(
  workspaceId: string,
  payload: { instruction: string; apply?: boolean },
): Promise<WorkspaceAttentionCompileResponse> {
  return apiRequest(`/api/workspaces/${workspaceId}/attention/compile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function applyWorkspaceSignalAction(
  workspaceId: string,
  signalId: string,
  action: string,
): Promise<WorkspaceSignalActionResponse> {
  return apiRequest(`/api/workspaces/${workspaceId}/signals/${signalId}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
}

export function listWorkspaceEvidence(
  workspaceId: string,
  payload?: { evidence_type?: string; status?: string; session_id?: string; limit?: number },
): Promise<{ workspace_id: string; evidence: WorkspaceEvidenceRecord[] }> {
  const params = new URLSearchParams();
  if (payload?.evidence_type) {
    params.set("evidence_type", payload.evidence_type);
  }
  if (payload?.status) {
    params.set("status", payload.status);
  }
  if (payload?.session_id) {
    params.set("session_id", payload.session_id);
  }
  if (payload?.limit !== undefined) {
    params.set("limit", String(payload.limit));
  }
  const query = params.toString();
  return apiRequest(`/api/workspaces/${workspaceId}/evidence${query ? `?${query}` : ""}`);
}

export function createWorkspaceEvidence(
  workspaceId: string,
  payload: {
    title: string;
    summary: string;
    content?: string;
    evidence_type?: string;
    source_kind?: string;
    status?: string;
    session_id?: string;
    build_project_id?: string;
    tags?: string[];
    metadata?: Record<string, unknown>;
  },
): Promise<WorkspaceEvidenceRecord> {
  return apiRequest(`/api/workspaces/${workspaceId}/evidence`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function listDelegations(
  sessionId: string,
  payload?: { limit?: number },
): Promise<{ session_id: string; tasks: DelegationTaskRecord[] }> {
  const params = new URLSearchParams();
  if (payload?.limit !== undefined) {
    params.set("limit", String(payload.limit));
  }
  const query = params.toString();
  return apiRequest(`/api/sessions/${sessionId}/delegations${query ? `?${query}` : ""}`);
}

export function listDelegationSubstrates(): Promise<{ substrates: DelegationSubstrateRecord[] }> {
  return apiRequest("/api/delegation/substrates");
}

export function getSessionDelegationPolicy(
  sessionId: string,
): Promise<{ session_id: string; delegation_policy: DelegationPolicyRecord }> {
  return apiRequest(`/api/sessions/${sessionId}/delegation-policy`);
}

export function patchSessionDelegationPolicy(
  sessionId: string,
  payload: Partial<DelegationPolicyRecord>,
): Promise<{ session_id: string; delegation_policy: DelegationPolicyRecord }> {
  return apiRequest(`/api/sessions/${sessionId}/delegation-policy`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function createDelegation(
  sessionId: string,
  payload: {
    task_type: string;
    title?: string;
    instruction: string;
    substrate_id?: string;
    budget?: DelegationBudgetRecord;
    provider?: string;
    model?: string;
    metadata?: Record<string, unknown>;
  },
): Promise<DelegationTaskRecord> {
  return apiRequest(`/api/sessions/${sessionId}/delegations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function listWorkspaceCaptures(
  workspaceId: string,
  payload?: { status?: string; session_id?: string; limit?: number },
): Promise<{ workspace_id: string; captures: WorkspaceCaptureRecord[] }> {
  const params = new URLSearchParams();
  if (payload?.status) {
    params.set("status", payload.status);
  }
  if (payload?.session_id) {
    params.set("session_id", payload.session_id);
  }
  if (payload?.limit !== undefined) {
    params.set("limit", String(payload.limit));
  }
  const query = params.toString();
  return apiRequest(`/api/workspaces/${workspaceId}/captures${query ? `?${query}` : ""}`);
}

export function createWorkspaceCapture(
  workspaceId: string,
  payload: {
    source?: string;
    event_type?: string;
    content: string;
    media_url?: string;
    metadata?: Record<string, unknown>;
    session_id?: string;
    build_project_id?: string;
    status?: string;
  },
): Promise<WorkspaceCaptureRecord> {
  return apiRequest(`/api/workspaces/${workspaceId}/captures`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function promoteWorkspaceCapture(
  workspaceId: string,
  captureId: string,
  payload?: { title?: string; summary?: string; evidence_type?: string; source_kind?: string; tags?: string[] },
): Promise<WorkspaceCapturePromotionResponse> {
  return apiRequest(`/api/workspaces/${workspaceId}/captures/${captureId}/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
}

export function getSession(sessionId: string): Promise<SessionDetail> {
  return apiRequest(`/api/sessions/${sessionId}`);
}

export function patchSession(
  sessionId: string,
  payload: { label?: string; model?: string; provider?: string; build_project_id?: string | null },
): Promise<SessionDetail> {
  return apiRequest(`/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getSessionToolPolicy(sessionId: string): Promise<{ session_id: string; tool_policy: ToolPolicyRecord }> {
  return apiRequest(`/api/sessions/${sessionId}/tool-policy`);
}

export function patchSessionToolPolicy(
  sessionId: string,
  payload: Partial<ToolPolicyRecord>,
): Promise<{ session_id: string; tool_policy: ToolPolicyRecord }> {
  return apiRequest(`/api/sessions/${sessionId}/tool-policy`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getMessages(sessionId: string): Promise<{ messages: TranscriptMessage[]; session_id: string }> {
  return apiRequest(`/api/sessions/${sessionId}/messages?limit=5000`);
}

export function getSessionBehaviorState(sessionId: string): Promise<BehaviorStateRecord> {
  return apiRequest(`/api/sessions/${sessionId}/behavior-state`);
}

export function upsertBehaviorFeedback(
  sessionId: string,
  messageId: string,
  payload: { signal: "up" | "down" | "promote"; message_preview: string; traits: string[] },
): Promise<{ feedback: BehaviorStateRecord["feedback"][number] }> {
  return apiRequest(`/api/sessions/${sessionId}/behavior-feedback/${messageId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deleteBehaviorFeedback(
  sessionId: string,
  messageId: string,
): Promise<{ deleted: boolean; message_id: string }> {
  return apiRequest(`/api/sessions/${sessionId}/behavior-feedback/${messageId}`, {
    method: "DELETE",
  });
}

export function applyBehaviorPatch(
  sessionId: string,
  payload: {
    rule_key: string;
    title: string;
    patch: string;
    scope: "chat" | "build_project" | "workspace" | "global";
    scope_id?: string;
    created_by?: string;
  },
): Promise<{ patch: BehaviorStateRecord["patches"][number] }> {
  return apiRequest(`/api/sessions/${sessionId}/behavior-patches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function dismissBehaviorProposal(
  sessionId: string,
  ruleKey: string,
): Promise<{ dismissal: BehaviorStateRecord["dismissals"][number] }> {
  return apiRequest(`/api/sessions/${sessionId}/behavior-proposals/${ruleKey}/dismiss`, {
    method: "POST",
  });
}

export async function getPlan(sessionId: string): Promise<PlanRecord | null> {
  try {
    return await apiRequest<PlanRecord>(`/api/sessions/${sessionId}/plan`);
  } catch (error) {
    if (error instanceof Error && error.message.includes("no plan found")) {
      return null;
    }
    throw error;
  }
}

export function listPlans(sessionId: string): Promise<{ plans: PlanSummaryRecord[] }> {
  return apiRequest(`/api/sessions/${sessionId}/plans`);
}

export function listWorkspacePlans(workspaceId: string): Promise<{ workspace_id: string; plans: PlanSummaryRecord[] }> {
  return apiRequest(`/api/workspaces/${workspaceId}/plans`);
}

export function createPlan(
  sessionId: string,
  payload: { title?: string; active_goal?: string; want_to_know?: string[]; activate?: boolean },
): Promise<PlanSummaryRecord> {
  return apiRequest(`/api/sessions/${sessionId}/plans`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function activatePlan(sessionId: string, planId: string): Promise<PlanRecord> {
  return apiRequest(`/api/sessions/${sessionId}/plans/${planId}/activate`, {
    method: "POST",
  });
}

export function deletePlan(
  sessionId: string,
  planId: string,
): Promise<{ deleted: string; session_id: string; active_plan_id: string | null }> {
  return apiRequest(`/api/sessions/${sessionId}/plans/${planId}`, {
    method: "DELETE",
  });
}

export function updateActivePlan(
  sessionId: string,
  payload: { title?: string; active_goal?: string; want_to_know?: string[]; status?: string },
): Promise<PlanRecord> {
  return apiRequest(`/api/sessions/${sessionId}/plan`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getSessionMemory(sessionId: string): Promise<SessionMemoryRecord> {
  return apiRequest(`/api/sessions/${sessionId}/memory`);
}

export function addSessionMemory(
  sessionId: string,
  payload: { content: string; also_daily?: boolean; date?: string },
): Promise<SessionMemoryRecord> {
  return apiRequest(`/api/sessions/${sessionId}/memory`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function searchSessionMemory(
  sessionId: string,
  payload: { query: string; limit?: number; include_daily?: boolean },
): Promise<SessionMemorySearchRecord> {
  const params = new URLSearchParams({ q: payload.query });
  if (payload.limit !== undefined) {
    params.set("limit", String(payload.limit));
  }
  if (payload.include_daily !== undefined) {
    params.set("include_daily", payload.include_daily ? "1" : "0");
  }
  return apiRequest(`/api/sessions/${sessionId}/memory/search?${params.toString()}`);
}

export function addPlanItem(
  sessionId: string,
  planId: string,
  payload: { content: string; status?: string; position?: number },
): Promise<PlanItemRecord> {
  return apiRequest(`/api/sessions/${sessionId}/plans/${planId}/items`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function updatePlanItem(
  sessionId: string,
  planId: string,
  itemId: string,
  payload: { content?: string; status?: string; position?: number; archived?: boolean },
): Promise<PlanItemRecord> {
  return apiRequest(`/api/sessions/${sessionId}/plans/${planId}/items/${itemId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function listPlanProposals(
  sessionId: string,
  payload?: { status?: string; plan_id?: string; limit?: number },
): Promise<{ session_id: string; proposals: PlanProposalRecord[] }> {
  const params = new URLSearchParams();
  if (payload?.status) {
    params.set("status", payload.status);
  }
  if (payload?.plan_id) {
    params.set("plan_id", payload.plan_id);
  }
  if (payload?.limit !== undefined) {
    params.set("limit", String(payload.limit));
  }
  const query = params.toString();
  return apiRequest(`/api/sessions/${sessionId}/plan-proposals${query ? `?${query}` : ""}`);
}

export function listPlanRevisions(
  sessionId: string,
  planId: string,
  payload?: { limit?: number },
): Promise<{ plan_id: string; revisions: PlanRevisionRecord[] }> {
  const params = new URLSearchParams();
  if (payload?.limit !== undefined) {
    params.set("limit", String(payload.limit));
  }
  const query = params.toString();
  return apiRequest(`/api/sessions/${sessionId}/plans/${planId}/revisions${query ? `?${query}` : ""}`);
}

export function acceptPlanProposal(
  sessionId: string,
  proposalId: string,
): Promise<{ proposal: PlanProposalRecord; result: Record<string, unknown> }> {
  return apiRequest(`/api/sessions/${sessionId}/plan-proposals/${proposalId}/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accepted_by: "clo" }),
  });
}

export function rejectPlanProposal(
  sessionId: string,
  proposalId: string,
  payload?: { resolution_note?: string },
): Promise<{ proposal: PlanProposalRecord }> {
  return apiRequest(`/api/sessions/${sessionId}/plan-proposals/${proposalId}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rejected_by: "clo", resolution_note: payload?.resolution_note || "" }),
  });
}

export function getSessionEvents(sessionId: string): Promise<{ session_id: string; events: SessionEventRecord[] }> {
  return apiRequest(`/api/sessions/${sessionId}/events`);
}

export function submitMessage(
  sessionId: string,
  content: string,
  role: "user" | "system" = "user",
  options?: {
    attachments?: Array<Record<string, unknown>>;
    captureIds?: string[];
    metadata?: Record<string, unknown>;
  },
): Promise<SubmitMessageResponse> {
  return apiRequest(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      role,
      attachments: options?.attachments,
      capture_ids: options?.captureIds,
      metadata: options?.metadata,
    }),
  });
}

export function uploadSessionAttachments(
  sessionId: string,
  files: File[],
): Promise<{ session_id: string; attachments: SessionAttachmentUploadRecord[]; capture_ids: string[] }> {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file, file.name);
  }

  return apiRequest(`/api/sessions/${sessionId}/attachments`, {
    method: "POST",
    body: formData,
  });
}

export function executeRun(sessionId: string, runId: string): Promise<ExecuteRunResponse> {
  return apiRequest(`/api/sessions/${sessionId}/runs/${runId}/execute`, {
    method: "POST",
  });
}

export function interruptRun(sessionId: string, runId: string): Promise<{ run_id: string; session_id: string; status: string }> {
  return apiRequest(`/api/sessions/${sessionId}/runs/${runId}/interrupt`, {
    method: "POST",
  });
}

export function getInteractiveProcess(processSessionId: string): Promise<InteractiveProcessRecord> {
  return apiRequest(`/api/processes/${processSessionId}`);
}

export function sendInteractiveProcessInput(
  processSessionId: string,
  payload: { data?: string; keys?: string; submit?: boolean },
): Promise<InteractiveProcessActionResponse> {
  return apiRequest(`/api/processes/${processSessionId}/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function terminateInteractiveProcess(processSessionId: string): Promise<InteractiveProcessActionResponse> {
  return apiRequest(`/api/processes/${processSessionId}/terminate`, {
    method: "POST",
  });
}

export function streamRun(
  sessionId: string,
  runId: string,
  handlers: {
    onTextDelta?: (text: string) => void;
    onThinkingDelta?: (text: string) => void;
    onToolUse?: (event: StreamEventRecord) => void;
    onToolResult?: (event: StreamEventRecord) => void;
    onUsage?: (event: StreamEventRecord) => void;
    onDone?: (event: StreamEventRecord) => void;
    onRuntimeEvent?: (event: StreamEventRecord) => void;
    onInterrupted?: () => void;
    onStreamError?: (message: string) => void;
    onTransportError?: () => void;
  },
): EventSource {
  const streamPath = `/api/sessions/${sessionId}/runs/${runId}/stream`;
  const source = new EventSource(streamPath);

  const parseStreamEventData = (event: Event, eventType: string): Record<string, unknown> | null => {
    const messageEvent = event as MessageEvent<string>;
    try {
      return parseJsonPayload(messageEvent.data, `${streamPath} [${eventType}]`) as Record<string, unknown>;
    } catch (error) {
      handlers.onStreamError?.(error instanceof Error ? error.message : `Invalid stream payload for ${eventType}`);
      source.close();
      return null;
    }
  };

  const handleTextDelta = (event: Event) => {
    const data = parseStreamEventData(event, "text_delta");
    if (!data) {
      return;
    }
    handlers.onTextDelta?.(String(data.text || ""));
  };
  const handleThinkingDelta = (event: Event) => {
    const data = parseStreamEventData(event, "thinking_delta");
    if (!data) {
      return;
    }
    handlers.onThinkingDelta?.(String(data.text || ""));
  };
  const handleToolUse = (event: Event) => {
    const data = parseStreamEventData(event, "tool_use");
    if (!data) {
      return;
    }
    handlers.onToolUse?.({ type: "tool_use", data });
  };
  const handleInterrupted = () => {
    handlers.onInterrupted?.();
  };

  source.addEventListener("text_delta", handleTextDelta);
  source.addEventListener("assistant_delta", handleTextDelta);
  source.addEventListener("thinking_delta", handleThinkingDelta);
  source.addEventListener("tool_use", handleToolUse);
  source.addEventListener("tool_call", handleToolUse);
  source.addEventListener("tool_result", (event) => {
    const data = parseStreamEventData(event, "tool_result");
    if (!data) {
      return;
    }
    handlers.onToolResult?.({ type: "tool_result", data });
  });
  source.addEventListener("usage", (event) => {
    const data = parseStreamEventData(event, "usage");
    if (!data) {
      return;
    }
    handlers.onUsage?.({ type: "usage", data });
  });
  source.addEventListener("done", (event) => {
    const data = parseStreamEventData(event, "done");
    if (!data) {
      return;
    }
    handlers.onDone?.({ type: "done", data });
  });
  ["assistant_final", "provider_stream_timeout", "subprocess_killed", "tool_failure_pivot", "action_progress_blocked", "prompt_unanswered", "repeated_intent_blocked"].forEach((eventType) => {
    source.addEventListener(eventType, (event) => {
      const data = parseStreamEventData(event, eventType);
      if (!data) {
        return;
      }
      handlers.onRuntimeEvent?.({ type: eventType, data } as StreamEventRecord);
    });
  });
  source.addEventListener("interrupted", handleInterrupted);
  source.addEventListener("interrupt", handleInterrupted);
  source.addEventListener("error", (event) => {
    const messageEvent = event as MessageEvent<string>;
    if (messageEvent.data) {
      const data = parseStreamEventData(event, "error");
      if (!data) {
        return;
      }
      handlers.onStreamError?.(String(data.message || "Stream error"));
    }
  });
  source.onerror = () => {
    handlers.onTransportError?.();
  };

  return source;
}

// ---------------------------------------------------------------------------
// Transient Windows
// ---------------------------------------------------------------------------

export async function createTransientWindow(
  sessionId: string,
  payload: {
    title: string;
    html?: string;
    summary?: string;
    source_type?: "generated" | "native";
    native_type?: string | null;
    payload?: Record<string, unknown> | null;
  },
): Promise<TransientWindowRecord> {
  return apiRequest(`/api/sessions/${sessionId}/windows`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function patchTransientWindow(
  windowId: string,
  patch: {
    html?: string;
    title?: string;
    payload?: Record<string, unknown> | null;
    state_flags?: Partial<TransientWindowRecord["state_flags"]>;
  },
): Promise<TransientWindowRecord> {
  return apiRequest(`/api/windows/${windowId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export async function closeTransientWindow(windowId: string): Promise<void> {
  await apiRequest(`/api/windows/${windowId}`, { method: "DELETE" });
}

export async function listTransientWindows(sessionId: string): Promise<TransientWindowRecord[]> {
  return apiRequest(`/api/sessions/${sessionId}/windows`);
}
