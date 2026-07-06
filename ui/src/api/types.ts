export interface SessionSummary {
  id: string;
  label: string;
  model: string;
  provider: string;
  status: string;
  token_count: number;
  context_window: number;
  workspace_id: string | null;
  build_project_id: string | null;
  created_at: string;
}

export interface ProviderRecord {
  id: string;
  kind: string;
  base_url: string;
  model_name: string;
  timeout_sec: number;
  enabled: boolean;
  capabilities: Record<string, unknown>;
  has_api_key: boolean;
  last_health_status: string;
  last_health_at: string | null;
}

export interface ProviderRouteRecord {
  delegation_substrate_id?: string;
  requested_provider: string;
  resolved_provider: string;
  requested_model: string;
  resolved_model: string;
  route_reason: string;
  used_auto_routing: boolean;
  provider_capabilities: Record<string, unknown>;
  task_profile: Record<string, unknown>;
}

export interface ProviderModelCatalogRecord {
  provider_id: string;
  models: string[];
  discovered: boolean;
  error: string | null;
}

export interface SessionRunRef {
  id: string;
  status: string;
  turn_number: number;
}

export interface SessionDetail extends SessionSummary {
  task_budget_remaining: number | null;
  rolled_over_to: string | null;
  message_count: number;
  current_run: SessionRunRef | null;
  tool_policy?: ToolPolicyRecord;
  delegation_policy?: DelegationPolicyRecord;
  updated_at: string;
}

export interface RuntimeAgentRecord {
  id: string;
  name: string;
  session_id: string;
  workspace_id: string | null;
  domain: string;
  mode: string;
  status: string;
  active_objective: string;
  tool_permissions: Record<string, unknown>;
  metadata: Record<string, unknown>;
  working_memory: Record<string, unknown>;
  current_plan: Record<string, unknown>;
  dashboard_state: Record<string, unknown>;
  last_agent_decision: string | null;
  open_threads: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
  stopped_at: string | null;
}

export type PokemonControlMode = "auto" | "assist" | "pause" | "step";

export interface PokemonBridgeStatusRecord {
  channel_name: string;
  session_id: string;
  domain: string;
  schema_version: string;
  control: {
    mode: PokemonControlMode;
    step_budget: number;
    operator_note: string;
    updated_at: string;
  };
  bridge: {
    schema_version: string;
    emulator: Record<string, unknown>;
    connected: boolean;
    last_snapshot_at: string;
    last_event_count: number;
    low_confidence: boolean;
    recent_emissions: string[];
  };
  state_summary: {
    trainer_name: string;
    route: string;
    team_size: number;
    has_battle: boolean;
    has_encounter: boolean;
  };
  event_id?: string;
}

export interface CloQueueItemRecord {
  id: string;
  session_id: string;
  session_label: string;
  workspace_id: string | null;
  workspace_name: string | null;
  build_project_id: string | null;
  build_project_name: string | null;
  message_content: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  position: number | null;
  run_id: string | null;
  error: string | null;
  result_summary: string | null;
  stop_after_error: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface CloQueueStateRecord {
  paused: boolean;
  pause_on_error: boolean;
  running_item: CloQueueItemRecord | null;
  queued_items: CloQueueItemRecord[];
  recent_items: CloQueueItemRecord[];
}

export interface ToolPolicyRecord {
  enabled_tools: string[];
  allow_destructive_tools: string[];
  allowed_paths: string[];
}

export interface WorkspaceAttentionProfileRecord {
  id: string;
  workspace_id: string;
  baseline_priority: number;
  current_attention_level: number;
  mode: string;
  max_idle_budget: number;
  allowed_pastime_types: string[];
  notification_threshold: string;
  freshness_target: string;
  review_at: string | null;
  expires_at: string | null;
  user_rationale: string;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceAttentionPolicyDiffRecord {
  field: string;
  label: string;
  before: unknown;
  after: unknown;
  reason: string;
}

export interface WorkspaceAttentionCompileResponse {
  workspace_id: string;
  workspace_name: string;
  instruction: string;
  applied: boolean;
  profile: WorkspaceAttentionProfileRecord;
  diff: WorkspaceAttentionPolicyDiffRecord[];
  scheduler_effects: {
    mode_summary: string;
    allowed_pastime_types: string[];
    max_idle_budget: number;
    baseline_priority: number;
    current_attention_level: number;
    notification_threshold: string;
    freshness_target: string;
  };
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  description: string;
  status: string;
  kind: string;
  attention_profile: WorkspaceAttentionProfileRecord | null;
  created_at: string;
  updated_at: string;
}

export interface BuildProjectRecord {
  id: string;
  workspace_id: string;
  name: string;
  description: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectDeliveryRecord {
  id: string;
  workspace_id: string;
  build_project_id: string;
  session_id: string | null;
  capture_id: string | null;
  target_device_id: string | null;
  artifact_kind: string;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  downloaded_at: string | null;
  installed_at: string | null;
  download_url: string;
  ack_url: string;
}

export interface TranscriptMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  position: number;
  token_estimate: number;
  created_at: string;
  archive_ready: boolean;
  archive_state: Record<string, unknown> | null;
}

export interface BehaviorFeedbackRecord {
  id: string;
  session_id: string;
  workspace_id: string | null;
  build_project_id: string | null;
  message_id: string;
  signal: "up" | "down" | "promote";
  message_preview: string;
  traits: string[];
  created_at: string;
  updated_at: string;
}

export interface BehaviorPatchRecord {
  id: string;
  session_id: string;
  workspace_id: string | null;
  build_project_id: string | null;
  scope: "chat" | "build_project" | "workspace" | "global";
  scope_id: string;
  rule_key: string;
  title: string;
  patch: string;
  status: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface BehaviorProposalDismissalRecord {
  session_id: string;
  rule_key: string;
  created_at: string;
  updated_at: string;
}

export interface BehaviorStateRecord {
  session_id: string;
  feedback: BehaviorFeedbackRecord[];
  patches: BehaviorPatchRecord[];
  dismissals: BehaviorProposalDismissalRecord[];
}

export interface PlanItemRecord {
  id: string;
  plan_id: string;
  content: string;
  status: string;
  position: number;
  archived: boolean;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanSummaryRecord {
  id: string;
  session_id: string;
  title: string;
  active_goal: string;
  want_to_know: string[];
  handoff: Record<string, unknown> | null;
  plan_status: string;
  workspace_id: string | null;
  build_project_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PlanRecord {
  id: string;
  session_id: string;
  title: string;
  active_goal: string;
  want_to_know: string[];
  handoff: Record<string, unknown> | null;
  status: string;
  is_active?: boolean;
  plan_status?: string;
  items: PlanItemRecord[];
  next_item: PlanItemRecord | null;
  context_guard: Record<string, unknown>;
  workspace_id?: string | null;
  build_project_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface PlanProposalRecord {
  id: string;
  session_id: string;
  plan_id: string | null;
  proposal_type: string;
  summary: string;
  payload: Record<string, unknown>;
  status: "pending" | "accepted" | "rejected";
  proposed_by: string;
  accepted_by: string | null;
  rejected_by: string | null;
  resolution_note: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface PlanRevisionDiffRecord {
  changed_fields: string[];
  item_ids_added: string[];
  item_ids_removed: string[];
  item_ids_updated: string[];
  next_item_changed: boolean;
}

export interface PlanRevisionSnapshotRecord {
  plan: PlanSummaryRecord;
  items: PlanItemRecord[];
  next_item: PlanItemRecord | null;
}

export interface PlanRevisionRecord {
  id: string;
  session_id: string;
  plan_id: string;
  change_type: string;
  summary: string;
  snapshot: PlanRevisionSnapshotRecord;
  diff: PlanRevisionDiffRecord;
  created_at: string;
}

export interface MemoryDocumentRecord {
  path: string;
  content: string;
  date?: string;
}

export interface SessionMemoryRecord {
  session_id: string;
  session_diary: MemoryDocumentRecord;
  daily_log: MemoryDocumentRecord;
}

export interface MemorySearchResultRecord {
  id: number;
  session_id: string | null;
  kind: string;
  date: string | null;
  path: string;
  created_at: string;
  snippet: string;
  score: number;
  matched_terms: string[];
  semantic_score: number;
  sources: string[];
}

export interface SessionMemorySearchRecord {
  session_id: string;
  query: string;
  strategy: string;
  results: MemorySearchResultRecord[];
}

export interface SessionEventRecord {
  id: string;
  session_id: string;
  run_id: string | null;
  type: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface WorkspaceRuntimeCandidateRecord {
  id: string;
  type: string;
  source: string;
  workspace_id: string;
  session_id: string;
  priority: number;
  urgency: number;
  compute_cost: number;
  interruptibility: string;
  cooldown: number;
  expires_at: string | null;
  prerequisites: string[];
  mutability_level: string;
  foreground_blocking: boolean;
  title: string;
  summary: string;
  metadata: Record<string, unknown>;
}

export interface WorkspaceSignalRecord {
  id: string;
  workspace_id: string;
  session_id: string | null;
  worker_name: string;
  signal_type: string;
  source_key: string;
  title: string;
  summary: string;
  status: string;
  priority: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceWorkerRecord {
  name: string;
  label: string;
  description: string;
  candidate_types: string[];
  signal_types: string[];
  queue_count: number;
  open_signal_count: number;
  status: string;
  top_candidate: WorkspaceRuntimeCandidateRecord | null;
  top_signal: WorkspaceSignalRecord | null;
}

export interface WorkspacePastimeRecord {
  id: string;
  workspace_id: string;
  key: string;
  title: string;
  description: string;
  pastime_type: string;
  source_kind: string;
  candidate_type: string | null;
  status: string;
  priority: number;
  cooldown_seconds: number;
  compute_cost: number;
  metadata: Record<string, unknown>;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  last_selected_at: string | null;
  last_completed_at: string | null;
}

export interface WorkspacePastimeSelectionRecord extends WorkspacePastimeRecord {
  matched_candidate: WorkspaceRuntimeCandidateRecord | null;
  selected_at: string;
  selection_reason: string;
}

export interface WorkspaceRuntimeRecord {
  workspace_id: string;
  attention_profile: WorkspaceAttentionProfileRecord | null;
  candidates: WorkspaceRuntimeCandidateRecord[];
  top_candidate: WorkspaceRuntimeCandidateRecord | null;
  pastimes: WorkspacePastimeRecord[];
  selected_pastime: WorkspacePastimeSelectionRecord | null;
  signals: WorkspaceSignalRecord[];
  workers: WorkspaceWorkerRecord[];
}

export interface WorkspaceEvidenceRecord {
  id: string;
  workspace_id: string;
  session_id: string | null;
  build_project_id: string | null;
  evidence_type: string;
  title: string;
  summary: string;
  content: string;
  source_kind: string;
  status: string;
  tags: string[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceCaptureRecord {
  id: string;
  workspace_id: string;
  build_project_id: string | null;
  session_id: string | null;
  run_id: string | null;
  source: string;
  event_type: string;
  content: string;
  media_url: string | null;
  metadata: Record<string, unknown>;
  status: string;
  received_at: string;
  processed_at: string | null;
}

export interface WorkspaceCapturePromotionResponse {
  capture: WorkspaceCaptureRecord;
  evidence: WorkspaceEvidenceRecord;
}

export interface DelegationTaskRecord {
  id: string;
  session_id: string;
  workspace_id: string | null;
  task_type: string;
  substrate_id: string | null;
  authority_mode: string | null;
  title: string;
  instruction: string;
  status: "queued" | "running" | "completed" | "failed" | "blocked" | "cancelled";
  requested_provider: string | null;
  requested_model: string | null;
  budget: DelegationBudgetRecord;
  provider_route: ProviderRouteRecord | null;
  worker_name: string | null;
  result_text: string | null;
  result_summary: string | null;
  result_payload: Record<string, unknown>;
  input_tokens: number | null;
  output_tokens: number | null;
  duration_ms: number | null;
  error: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export interface DelegationBudgetRecord {
  max_input_tokens?: number;
  max_output_tokens?: number;
  max_cost_usd?: number;
  max_duration_seconds?: number;
}

export interface DelegationPolicyTaskRouteRecord {
  preferred_substrate_id: string;
  fallback_substrate_ids: string[];
  auto_delegate: boolean;
  budget: DelegationBudgetRecord;
}

export interface DelegationPolicyRecord {
  mode: "manual" | "suggest" | "auto";
  max_live_tasks: number;
  default_budget: DelegationBudgetRecord;
  task_routes: Record<string, DelegationPolicyTaskRouteRecord>;
}

export interface DelegationSubstrateRecord {
  id: string;
  label: string;
  description: string;
  family: string;
  execution_mode: string;
  worker_name: string;
  supports_tool_use: boolean;
  supports_mutation: boolean;
  frontier: boolean;
  available: boolean;
  dispatchable: boolean;
  health_status: string;
}

export interface SessionAttachmentUploadRecord {
  attachment_id: string;
  capture_id: string | null;
  type: string;
  description: string;
  content: string;
  media_path: string;
  metadata: Record<string, unknown>;
}

export interface WorkspaceSignalActionResponse {
  signal: WorkspaceSignalRecord;
  updated_item: PlanItemRecord | null;
  snapshot: WorkspaceRuntimeRecord;
}

export interface CreateSessionResponse {
  id: string;
  label: string;
  model: string;
  provider: string;
  context_window: number;
  status: string;
  workspace_id: string | null;
  build_project_id: string | null;
  tool_policy?: ToolPolicyRecord;
  delegation_policy?: DelegationPolicyRecord;
}

export interface SubmitMessageResponse {
  message_id: string;
  run_id: string;
  turn_number: number;
  session_id: string;
  role: string;
  content: string;
  position: number;
  status: string;
  attachments: Array<Record<string, unknown>>;
  metadata: Record<string, unknown>;
  capture_ids: string[];
}

export interface ExecuteRunResponse {
  run_id: string;
  session_id: string;
  status: string;
  finish_reason: string;
  text: string | null;
  final_text: string | null;
  transient_text: string | null;
  provider_route: ProviderRouteRecord | null;
  tool_results: Array<Record<string, unknown>>;
  input_tokens: number;
  output_tokens: number;
  interrupted: boolean;
  error: string | null;
}

export interface InteractiveProcessRecord {
  session_id: string;
  command: string;
  workdir: string | null;
  pid: number | null;
  interactive: boolean;
  status: "running" | "completed";
  return_code: number | null;
  elapsed_seconds: number;
  output: string;
}

export interface InteractiveProcessActionResponse {
  session_id: string;
  results?: string[];
  result?: string;
  process: InteractiveProcessRecord | null;
}

export interface StreamEventRecord {
  type: string;
  data: Record<string, unknown>;
}

export interface TransientWindowRecord {
  id: string;
  session_id: string;
  title: string;
  source_type: "generated" | "native";
  native_type: string | null;
  html: string | null;
  payload: Record<string, unknown> | null;
  capabilities: { network: boolean; toolBridge: boolean; storage: boolean; media: boolean };
  state_flags: { pinned: boolean; minimized: boolean; stale: boolean; saved: boolean };
  mutable: boolean;
  version: number;
  summary: string;
  created_at: string;
  updated_at: string;
}
