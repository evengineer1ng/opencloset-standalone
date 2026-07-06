import { useEffect, useMemo, useRef, useState } from "react";
import {
  applyBehaviorPatch,
  deleteBehaviorFeedback,
  dismissBehaviorProposal,
  getSessionBehaviorState,
  upsertBehaviorFeedback,
} from "../../api/client";
import type {
  BehaviorFeedbackRecord,
  BehaviorPatchRecord,
  BehaviorProposalDismissalRecord,
  DelegationPolicyRecord,
  DelegationSubstrateRecord,
  DelegationTaskRecord,
  InteractiveProcessRecord,
  PokemonBridgeStatusRecord,
  PokemonControlMode,
  ProviderRecord,
  RuntimeAgentRecord,
  SessionDetail,
  TranscriptMessage,
  TransientWindowRecord,
  WorkspaceCaptureRecord,
  WorkspaceEvidenceRecord,
} from "../../api/types";
import TransientWindowRenderer from "../chatnative/artifacts/TransientWindowRenderer";
import type { ArtifactDescriptor } from "../chatnative/artifacts/types";
import CustomSelect from "../forms/CustomSelect";
import { ProviderModelPicker } from "../providers/ProviderModelPicker";
import "./SimpleChat.css";

type FeedbackSignal = "up" | "down" | "promote";
type FeedbackTrait = "actionable" | "abstract" | "preserve_existing" | "rewrite_risk" | "compact";
type BehaviorScope = "chat" | "build_project" | "workspace" | "global";

interface FeedbackEntry {
  id: string;
  messageId: string;
  sessionId: string;
  workspaceId: string | null;
  buildProjectId: string | null;
  signal: FeedbackSignal;
  createdAt: string;
  messagePreview: string;
  traits: FeedbackTrait[];
}

interface ScopeOption {
  scope: BehaviorScope;
  label: string;
  scopeId: string;
}

interface AppliedBehaviorPatch {
  id: string;
  ruleKey: string;
  title: string;
  patch: string;
  scope: BehaviorScope;
  scopeId: string;
  createdAt: string;
}

interface BehaviorProposal {
  ruleKey: string;
  title: string;
  observedPattern: string;
  hypothesis: string;
  patch: string;
  recommendedScope: BehaviorScope;
  sampleCount: number;
}

interface BehaviorDismissal {
  sessionId: string;
  ruleKey: string;
}

const INTERNAL_THINK_RE = /<think>[\s\S]*?<\/think>/gi;
const INTERNAL_PAIRED_TOOL_RE = /<(exec|tool_call|invoke)\b[\s\S]*?<\/\1>/gi;
const INTERNAL_OPEN_TOOL_RE = /<(exec|write|read|read_file|create_file|edit_file|tool_call|invoke)\b[^<>]*>/gi;
const INTERNAL_CLOSE_TOOL_RE = /<\/(write|read|read_file|create_file|edit_file|tool_call|invoke)>/gi;
const MOJIBAKE_REPLACEMENTS: Array<[string, string]> = [
  ["â", " - "],
  ["â", "-"],
  ["â", '"'],
  ["â", '"'],
  ["â", "'"],
  ["â", "'"],
  ["â¦", "..."],
  ["Â", ""],
];

export interface ToolStepView {
  id: string;
  toolKey: string;
  toolName: string;
  summary: string;
  status: "running" | "success" | "warning" | "error" | "interrupted";
  statusLabel: string;
  detail?: string;
  presentation?: "row" | "card";
  title?: string;
  recoveryPrompt?: string;
  createdAt: string;
}

interface SimpleChatProps {
  session: SessionDetail | null;
  messages: TranscriptMessage[];
  streamingThinking?: string | null;
  toolSteps: ToolStepView[];
  runtimeChannel?: RuntimeAgentRecord | null;
  pokemonBridgeStatus?: PokemonBridgeStatusRecord | null;
  pokemonControlPending?: boolean;
  transientWindows: TransientWindowRecord[];
  workspaceCaptures: WorkspaceCaptureRecord[];
  workspaceEvidence: WorkspaceEvidenceRecord[];
  delegationTasks: DelegationTaskRecord[];
  delegationSubstrates: DelegationSubstrateRecord[];
  delegationPolicy: DelegationPolicyRecord | null;
  interactiveProcess?: InteractiveProcessRecord | null;
  interactivePending?: boolean;
  interactiveError?: string | null;
  providers: ProviderRecord[];
  isBusy?: boolean;
  isBusyHere?: boolean;
  isTypingHere?: boolean;
  busySessionLabel?: string | null;
  errorMessage?: string | null;
  onToggleWindowPin?: (windowId: string, pinned: boolean) => Promise<void> | void;
  onCloseWindow?: (windowId: string) => Promise<void> | void;
  onBack: () => void;
  onSend: (text: string, runtime?: {
    providerId?: string;
    model?: string;
    files?: File[];
    attachments?: Array<Record<string, unknown>>;
    captureIds?: string[];
    metadata?: Record<string, unknown>;
  }) => Promise<void> | void;
  onCreateDelegation?: (payload: {
    taskType: string;
    title?: string;
    instruction: string;
    substrateId?: string;
    budget?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  }) => Promise<void> | void;
  onUpdateDelegationPolicy?: (payload: {
    taskType: string;
    mode?: "manual" | "suggest" | "auto";
    maxLiveTasks?: number;
    preferredSubstrateId?: string;
    autoDelegate?: boolean;
  }) => Promise<void> | void;
  onInterrupt?: () => void;
  onSteer?: (text: string) => void;
  onSelectPokemonControlMode?: (mode: PokemonControlMode) => Promise<void> | void;
  onSendInteractiveInput?: (text: string) => Promise<void> | void;
  onSendInteractiveKey?: (key: string) => Promise<void> | void;
  onTerminateInteractiveProcess?: () => Promise<void> | void;
}

type DelegationPolicyMode = "manual" | "suggest" | "auto";

type ComposerFileDraft = {
  id: string;
  file: File;
};

type DrawerKind = "context" | "delegation";

type RenderItem =
  | { kind: "message"; key: string; createdAt: string; message: TranscriptMessage }
  | { kind: "tool"; key: string; createdAt: string; step: ToolStepView }
  | { kind: "window"; key: string; createdAt: string; window: TransientWindowRecord };

type ConversationItem =
  | { kind: "message"; key: string; createdAt: string; message: TranscriptMessage }
  | { kind: "tool_group"; key: string; createdAt: string; steps: ToolStepView[] }
  | { kind: "window"; key: string; createdAt: string; window: TransientWindowRecord };

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function titleCaseToken(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function normalizeDisplayText(value: string): string {
  let normalized = value;
  for (const [from, to] of MOJIBAKE_REPLACEMENTS) {
    normalized = normalized.split(from).join(to);
  }
  return normalized;
}

function fileDraftId(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function inferAttachmentType(file: File): "image" | "audio" | "file" {
  if (file.type.startsWith("image/")) {
    return "image";
  }
  if (file.type.startsWith("audio/")) {
    return "audio";
  }
  return "file";
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTaskLabel(taskType: string): string {
  return taskType.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatDurationMs(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return "";
  }
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}s` : `${value}ms`;
}

function summarizeDelegationBudget(task: DelegationTaskRecord): string {
  const parts: string[] = [];
  if (typeof task.budget?.max_output_tokens === "number") {
    parts.push(`${task.budget.max_output_tokens} out tok`);
  }
  if (typeof task.budget?.max_duration_seconds === "number") {
    parts.push(`${task.budget.max_duration_seconds}s max`);
  }
  return parts.join(" · ");
}

function stripInternalArtifacts(value: string): string {
  return normalizeDisplayText(value)
    .replace(INTERNAL_THINK_RE, "")
    .replace(INTERNAL_PAIRED_TOOL_RE, "")
    .replace(INTERNAL_OPEN_TOOL_RE, "")
    .replace(INTERNAL_CLOSE_TOOL_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function truncateText(value: string, limit = 120): string {
  const normalized = normalizeDisplayText(value).replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit - 3)}...`;
}

function classifyAssistantTraits(value: string): FeedbackTrait[] {
  const normalized = normalizeDisplayText(value);
  const lower = normalized.toLowerCase();
  const traits = new Set<FeedbackTrait>();
  const lineCount = normalized.split(/\r?\n/).filter((line) => line.trim().length > 0).length;
  const hasCode = /```|`[^`]+`/.test(normalized);
  const hasFileSignal =
    /\b(?:patch|diff|test|build|validate|verification|run|grep|search|read_file|apply_patch|error|failure mode|next patch target|line\s+\d+|\.tsx?\b|\.py\b|\.md\b|\.json\b)\b/.test(lower);
  const preservesExisting = /\b(?:existing|already exists|preserve|extend|inspect|patch it|edit it|current state)\b/.test(lower);
  const rewriteRisk = /\b(?:from scratch|rewrite|recreate|greenfield|start over)\b/.test(lower);
  const abstractSignal = /\b(?:architecture|conceptual|conceptually|overall|directionally|framework|worldview|product direction)\b/.test(lower);

  if (hasCode || hasFileSignal) {
    traits.add("actionable");
  }
  if (preservesExisting) {
    traits.add("preserve_existing");
  }
  if (rewriteRisk || (normalized.length > 420 && !hasCode && !hasFileSignal && !preservesExisting)) {
    traits.add("rewrite_risk");
  }
  if (normalized.length <= 220 && lineCount <= 6) {
    traits.add("compact");
  }
  if (abstractSignal || ((normalized.length > 360 || lineCount >= 7) && !hasCode && !hasFileSignal)) {
    traits.add("abstract");
  }

  if (traits.size === 0) {
    traits.add("compact");
  }

  return Array.from(traits);
}

function scopeOptionsForSession(session: SessionDetail | null): ScopeOption[] {
  if (!session) {
    return [{ scope: "global", label: "Global", scopeId: "global" }];
  }

  const options: ScopeOption[] = [{ scope: "chat", label: "This chat", scopeId: session.id }];
  if (session.build_project_id) {
    options.push({ scope: "build_project", label: "Build project", scopeId: session.build_project_id });
  }
  if (session.workspace_id) {
    options.push({ scope: "workspace", label: "Workspace", scopeId: session.workspace_id });
  }
  options.push({ scope: "global", label: "Global", scopeId: "global" });
  return options;
}

function applicableBehaviorPatches(
  patches: AppliedBehaviorPatch[],
  session: SessionDetail | null,
): AppliedBehaviorPatch[] {
  if (!session) {
    return patches.filter((patch) => patch.scope === "global" && patch.scopeId === "global");
  }

  return patches.filter((patch) => {
    if (patch.scope === "global") {
      return patch.scopeId === "global";
    }
    if (patch.scope === "chat") {
      return patch.scopeId === session.id;
    }
    if (patch.scope === "build_project") {
      return Boolean(session.build_project_id) && patch.scopeId === session.build_project_id;
    }
    if (patch.scope === "workspace") {
      return Boolean(session.workspace_id) && patch.scopeId === session.workspace_id;
    }
    return false;
  });
}

function topTrait(entries: FeedbackEntry[], signal: FeedbackSignal): { trait: FeedbackTrait; count: number } | null {
  const counts = new Map<FeedbackTrait, number>();
  for (const entry of entries) {
    if (entry.signal !== signal) {
      continue;
    }
    for (const trait of entry.traits) {
      counts.set(trait, (counts.get(trait) ?? 0) + 1);
    }
  }

  let best: { trait: FeedbackTrait; count: number } | null = null;
  for (const [trait, count] of counts.entries()) {
    if (!best || count > best.count) {
      best = { trait, count };
    }
  }
  return best;
}

function describeTrait(trait: FeedbackTrait | null): string {
  switch (trait) {
    case "actionable":
      return "concrete, file-aware execution";
    case "abstract":
      return "abstract framing before action";
    case "preserve_existing":
      return "inspect-and-patch behavior";
    case "rewrite_risk":
      return "rewrite-from-scratch drift";
    case "compact":
      return "tight, low-friction delivery";
    default:
      return "recent behavior";
  }
}

function deriveBehaviorProposal(
  entries: FeedbackEntry[],
  activePatches: AppliedBehaviorPatch[],
  dismissed: BehaviorDismissal[],
  session: SessionDetail | null,
): BehaviorProposal | null {
  if (!session) {
    return null;
  }

  const candidateEntries = [
    session.build_project_id
      ? entries.filter((entry) => entry.buildProjectId === session.build_project_id)
      : [],
    session.workspace_id
      ? entries.filter((entry) => entry.workspaceId === session.workspace_id)
      : [],
    entries.filter((entry) => entry.sessionId === session.id),
  ];

  const sessionEntries = (candidateEntries.find((group) => group.length >= 4) ?? candidateEntries[2])
    .sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt))
    .slice(0, 12);

  if (sessionEntries.length < 4) {
    return null;
  }

  const positiveTop = topTrait(sessionEntries, "up") ?? topTrait(sessionEntries, "promote");
  const negativeTop = topTrait(sessionEntries, "down");
  if (!positiveTop && !negativeTop) {
    return null;
  }

  const appliedRuleKeys = new Set(activePatches.map((patch) => patch.ruleKey));
  const dismissedRuleKeys = new Set(
    dismissed.filter((entry) => entry.sessionId === session.id).map((entry) => entry.ruleKey),
  );

  const sampleCount = sessionEntries.length;

  const maybeReturn = (proposal: BehaviorProposal | null): BehaviorProposal | null => {
    if (!proposal) {
      return null;
    }
    if (appliedRuleKeys.has(proposal.ruleKey) || dismissedRuleKeys.has(proposal.ruleKey)) {
      return null;
    }
    return proposal;
  };

  if ((positiveTop?.trait === "preserve_existing" && (negativeTop?.trait === "rewrite_risk" || negativeTop?.trait === "abstract")) || negativeTop?.trait === "rewrite_risk") {
    return maybeReturn({
      ruleKey: "preserve-existing-artifacts",
      title: "I think I learned something from your recent feedback.",
      observedPattern: `You reinforce ${describeTrait(positiveTop?.trait ?? "preserve_existing")} and push back on ${describeTrait(negativeTop?.trait ?? "rewrite_risk")}.`,
      hypothesis: `Across the last ${sampleCount} rated assistant turns, preserving existing artifacts reads as higher trust than broad rewrites.`,
      patch: "When a file or artifact already exists, inspect it first, summarize whether it is being edited, extended, or replaced, and avoid recreating it from scratch unless explicitly requested.",
      recommendedScope: session.build_project_id ? "build_project" : session.workspace_id ? "workspace" : "chat",
      sampleCount,
    });
  }

  if (negativeTop?.trait === "abstract" && (positiveTop?.trait === "actionable" || positiveTop?.trait === "compact")) {
    return maybeReturn({
      ruleKey: "lead-with-failure-and-target",
      title: "I think I learned something from your recent feedback.",
      observedPattern: `You reinforce ${describeTrait(positiveTop?.trait ?? "actionable")} and push back on ${describeTrait("abstract")}.`,
      hypothesis: `Across the last ${sampleCount} rated assistant turns, direct execution framing lands better than conceptual setup.`,
      patch: "For runtime bugs and build work, lead with the exact failure mode and the next patch target before broader framing. After stating intent, move directly to a tool action, diff, or validation result.",
      recommendedScope: session.workspace_id ? "workspace" : "chat",
      sampleCount,
    });
  }

  if (positiveTop?.trait === "compact" && negativeTop?.trait) {
    return maybeReturn({
      ruleKey: "compress-pre-action-narration",
      title: "I think I learned something from your recent feedback.",
      observedPattern: `You reinforce ${describeTrait("compact")} and push back on ${describeTrait(negativeTop.trait)}.`,
      hypothesis: `Across the last ${sampleCount} rated assistant turns, shorter lead-ins are earning more trust than extended narration.`,
      patch: "Keep pre-action narration brief. Once the next step is stated, the next visible event should be a concrete action, a code change, a command result, or an explicit blocker.",
      recommendedScope: "chat",
      sampleCount,
    });
  }

  return null;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderAssistantHtml(value: string): string {
  const sanitized = stripInternalArtifacts(value);
  if (!sanitized) {
    return "";
  }

  const codeBlocks: string[] = [];
  let html = escapeHtml(sanitized).replace(/```([a-zA-Z0-9_-]+)?\n?([\s\S]*?)```/g, (_match, language, code) => {
    const langAttr = language ? ` data-lang="${language}"` : "";
    const block = `<pre class="sc__code-block"><code${langAttr}>${String(code).replace(/^\n+|\n+$/g, "")}</code></pre>`;
    const token = `@@CODEBLOCK_${codeBlocks.length}@@`;
    codeBlocks.push(block);
    return token;
  });

  html = html.replace(/`([^`\n]+)`/g, "<code class=\"sc__inline-code\">$1</code>");
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*(?=([^*]|$))/g, "$1<em>$2</em>");
  html = html.replace(/\n/g, "<br />");

  for (const [index, block] of codeBlocks.entries()) {
    html = html.replace(`@@CODEBLOCK_${index}@@`, block);
  }

  return html;
}

function toFeedbackEntry(record: BehaviorFeedbackRecord): FeedbackEntry {
  return {
    id: record.id,
    messageId: record.message_id,
    sessionId: record.session_id,
    workspaceId: record.workspace_id,
    buildProjectId: record.build_project_id,
    signal: record.signal,
    createdAt: record.created_at,
    messagePreview: record.message_preview,
    traits: record.traits.filter(
      (trait): trait is FeedbackTrait => ["actionable", "abstract", "preserve_existing", "rewrite_risk", "compact"].includes(trait),
    ),
  };
}

function toAppliedBehaviorPatch(record: BehaviorPatchRecord): AppliedBehaviorPatch {
  return {
    id: record.id,
    ruleKey: record.rule_key,
    title: record.title,
    patch: record.patch,
    scope: record.scope,
    scopeId: record.scope_id,
    createdAt: record.created_at,
  };
}

function toBehaviorDismissal(record: BehaviorProposalDismissalRecord): BehaviorDismissal {
  return {
    sessionId: record.session_id,
    ruleKey: record.rule_key,
  };
}

function isNearBottom(element: HTMLDivElement, threshold = 140): boolean {
  return element.scrollHeight - element.scrollTop - element.clientHeight < threshold;
}

function buildTransientWindowArtifact(
  session: SessionDetail | null,
  windowRecord: TransientWindowRecord,
): ArtifactDescriptor {
  return {
    id: windowRecord.id,
    kind: "transient_window",
    title: windowRecord.title,
    scope: {
      workspace_id: session?.workspace_id ?? null,
      build_project_id: session?.build_project_id ?? null,
      session_id: session?.id ?? null,
    },
    source: "intent",
    created_at: windowRecord.created_at,
    dismissed: false,
    frame_state: {
      expanded: true,
      pinned: windowRecord.state_flags.pinned,
      collapsed: false,
    },
    payload: {
      html: windowRecord.html ?? "",
      summary: windowRecord.summary,
      window_id: windowRecord.id,
      render_state: windowRecord.html ? "rendering" : "generating",
      source_type: windowRecord.source_type,
      native_type: windowRecord.native_type,
      window_payload: windowRecord.payload,
    },
  };
}

function groupConversationItems(items: RenderItem[]): ConversationItem[] {
  const sorted = [...items].sort((left, right) => {
    const delta = Date.parse(left.createdAt) - Date.parse(right.createdAt);
    if (delta !== 0) {
      return delta;
    }
    return left.key.localeCompare(right.key);
  });

  const grouped: ConversationItem[] = [];

  for (const item of sorted) {
    if (item.kind === "tool") {
      const last = grouped[grouped.length - 1];
      if (last?.kind === "tool_group") {
        last.steps.push(item.step);
      } else {
        grouped.push({
          kind: "tool_group",
          key: item.key,
          createdAt: item.createdAt,
          steps: [item.step],
        });
      }
      continue;
    }

    if (item.kind === "window") {
      grouped.push(item);
      continue;
    }

    grouped.push(item);
  }

  return grouped;
}

export function SimpleChat({
  session,
  messages,
  streamingThinking = null,
  toolSteps,
  runtimeChannel = null,
  pokemonBridgeStatus = null,
  pokemonControlPending = false,
  transientWindows,
  workspaceCaptures,
  workspaceEvidence,
  delegationTasks,
  delegationSubstrates,
  delegationPolicy,
  interactiveProcess = null,
  interactivePending = false,
  interactiveError = null,
  providers,
  isBusy = false,
  isBusyHere = false,
  isTypingHere = false,
  busySessionLabel,
  errorMessage,
  onToggleWindowPin,
  onCloseWindow,
  onBack,
  onSend,
  onCreateDelegation,
  onUpdateDelegationPolicy,
  onInterrupt,
  onSteer,
  onSelectPokemonControlMode,
  onSendInteractiveInput,
  onSendInteractiveKey,
  onTerminateInteractiveProcess,
}: SimpleChatProps) {
  const [draft, setDraft] = useState("");
  const [steerDraft, setSteerDraft] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<ComposerFileDraft[]>([]);
  const [selectedCaptureIds, setSelectedCaptureIds] = useState<string[]>([]);
  const [selectedEvidenceIds, setSelectedEvidenceIds] = useState<string[]>([]);
  const [activeDrawer, setActiveDrawer] = useState<DrawerKind | null>(null);
  const [visibleDrawer, setVisibleDrawer] = useState<DrawerKind | null>(null);
  const [drawerClosing, setDrawerClosing] = useState(false);
  const [delegationOpen, setDelegationOpen] = useState(false);
  const [delegationType, setDelegationType] = useState("review");
  const [delegationSubstrateId, setDelegationSubstrateId] = useState<string>("");
  const [delegationMaxOutputTokens, setDelegationMaxOutputTokens] = useState("");
  const [delegationMaxDurationSeconds, setDelegationMaxDurationSeconds] = useState("");
  const [delegationDraft, setDelegationDraft] = useState("");
  const [delegationPending, setDelegationPending] = useState(false);
  const [delegationPolicyModeDraft, setDelegationPolicyModeDraft] = useState<"manual" | "suggest" | "auto">("manual");
  const [delegationPolicyMaxLiveDraft, setDelegationPolicyMaxLiveDraft] = useState("2");
  const [delegationPolicySaving, setDelegationPolicySaving] = useState(false);
  const [expandedDelegationId, setExpandedDelegationId] = useState<string | null>(null);
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(
    session?.provider ?? providers[0]?.id ?? null,
  );
  const [selectedModel, setSelectedModel] = useState(session?.model ?? providers[0]?.model_name ?? "");
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [feedbackEntries, setFeedbackEntries] = useState<FeedbackEntry[]>([]);
  const [appliedBehaviorPatches, setAppliedBehaviorPatches] = useState<AppliedBehaviorPatch[]>([]);
  const [dismissedBehaviorProposals, setDismissedBehaviorProposals] = useState<BehaviorDismissal[]>([]);
  const [behaviorPending, setBehaviorPending] = useState(false);
  const [behaviorError, setBehaviorError] = useState<string | null>(null);
  const [selectedProposalScope, setSelectedProposalScope] = useState<BehaviorScope | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const attachmentInputRef = useRef<HTMLInputElement | null>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    setSelectedProviderId(session?.provider ?? providers[0]?.id ?? null);
  }, [providers, session?.id, session?.provider]);

  useEffect(() => {
    setSelectedModel(session?.model ?? providers.find((provider) => provider.id === session?.provider)?.model_name ?? providers[0]?.model_name ?? "");
    setRuntimeOpen(false);
    stickToBottomRef.current = true;
  }, [providers, session?.id, session?.model, session?.provider]);

  useEffect(() => {
    setSteerDraft("");
    setSelectedFiles([]);
    setSelectedCaptureIds([]);
    setSelectedEvidenceIds([]);
    setActiveDrawer(null);
    setVisibleDrawer(null);
    setDrawerClosing(false);
    setDelegationType("review");
    setDelegationSubstrateId("");
    setDelegationMaxOutputTokens("");
    setDelegationMaxDurationSeconds("");
    setDelegationDraft("");
    setDelegationOpen(false);
    setExpandedDelegationId(null);
    setFeedbackEntries([]);
    setAppliedBehaviorPatches([]);
    setDismissedBehaviorProposals([]);
    setBehaviorError(null);
  }, [session?.id]);

  useEffect(() => {
    const route = delegationPolicy?.task_routes?.[delegationType];
    const preferredSubstrateId = route?.preferred_substrate_id ?? "";
    setDelegationSubstrateId(preferredSubstrateId);
    const outputBudget = route?.budget?.max_output_tokens ?? delegationPolicy?.default_budget?.max_output_tokens;
    const durationBudget = route?.budget?.max_duration_seconds ?? delegationPolicy?.default_budget?.max_duration_seconds;
    setDelegationMaxOutputTokens(typeof outputBudget === "number" ? String(outputBudget) : "");
    setDelegationMaxDurationSeconds(typeof durationBudget === "number" ? String(durationBudget) : "");
  }, [delegationPolicy, delegationType]);

  useEffect(() => {
    setDelegationPolicyModeDraft(delegationPolicy?.mode ?? "manual");
    setDelegationPolicyMaxLiveDraft(String(delegationPolicy?.max_live_tasks ?? 2));
  }, [delegationPolicy?.mode, delegationPolicy?.max_live_tasks, session?.id]);

  useEffect(() => {
    let cancelled = false;

    async function loadBehaviorState(currentSession: SessionDetail) {
      try {
        const state = await getSessionBehaviorState(currentSession.id);
        if (cancelled) {
          return;
        }
        setFeedbackEntries(state.feedback.map(toFeedbackEntry));
        setAppliedBehaviorPatches(state.patches.map(toAppliedBehaviorPatch));
        setDismissedBehaviorProposals(state.dismissals.map(toBehaviorDismissal));
        setBehaviorError(null);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setBehaviorError(error instanceof Error ? error.message : "Failed to load behavior state.");
      }
    }

    if (!session) {
      return () => {
        cancelled = true;
      };
    }

    void loadBehaviorState(session);
    return () => {
      cancelled = true;
    };
  }, [session]);

  useEffect(() => {
    if (activeDrawer) {
      setVisibleDrawer(activeDrawer);
      setDrawerClosing(false);
      return;
    }

    if (!visibleDrawer) {
      return;
    }

    setDrawerClosing(true);
    const timeoutId = window.setTimeout(() => {
      setVisibleDrawer(null);
      setDrawerClosing(false);
    }, 190);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [activeDrawer, visibleDrawer]);

  function autoResize() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = draft.trim();

    if (!value || !session) return;

    const activeBehaviorPatches = applicableBehaviorPatches(appliedBehaviorPatches, session);

    await onSend(value, {
      providerId: selectedProviderId ?? undefined,
      model: selectedModel.trim() || undefined,
      files: selectedFiles.map((entry) => entry.file),
      captureIds: selectedCaptureIds,
      metadata:
        selectedFiles.length > 0 || selectedCaptureIds.length > 0 || activeBehaviorPatches.length > 0
          ? {
              composer_attachment_count: selectedFiles.length,
              composer_capture_count: selectedCaptureIds.length,
              behavior_patches: activeBehaviorPatches.map((patch) => ({
                rule_key: patch.ruleKey,
                title: patch.title,
                patch: patch.patch,
                scope: patch.scope,
                scope_id: patch.scopeId,
                created_at: patch.createdAt,
              })),
            }
          : undefined,
    });

    setDraft("");
    setRuntimeOpen(false);
    setSelectedFiles([]);
    setSelectedCaptureIds([]);
    stickToBottomRef.current = true;
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }

  async function handleDelegationSubmit() {
    const instruction = delegationDraft.trim();
    if (!instruction || !session || !onCreateDelegation) {
      return;
    }

    const budget: Record<string, number> = {};
    const maxOutputTokens = Number.parseInt(delegationMaxOutputTokens, 10);
    const maxDurationSeconds = Number.parseInt(delegationMaxDurationSeconds, 10);
    if (Number.isFinite(maxOutputTokens) && maxOutputTokens > 0) {
      budget.max_output_tokens = maxOutputTokens;
    }
    if (Number.isFinite(maxDurationSeconds) && maxDurationSeconds > 0) {
      budget.max_duration_seconds = maxDurationSeconds;
    }

    setDelegationPending(true);
    try {
      await onCreateDelegation({
        taskType: delegationType,
        title: instruction.slice(0, 80),
        instruction,
        substrateId: delegationSubstrateId || undefined,
        budget,
        metadata: {
          capture_ids: selectedCaptureIds,
          evidence_ids: selectedEvidenceIds,
          request_origin: "chat_native_ui",
        },
      });
      setDelegationDraft("");
      setDelegationOpen(false);
    } finally {
      setDelegationPending(false);
    }
  }

  async function handleDelegationPolicySave() {
    if (!session || !onUpdateDelegationPolicy) {
      return;
    }
    const maxLiveTasks = Number.parseInt(delegationPolicyMaxLiveDraft, 10);
    if (!Number.isFinite(maxLiveTasks) || maxLiveTasks <= 0) {
      return;
    }
    setDelegationPolicySaving(true);
    try {
      await onUpdateDelegationPolicy({
        taskType: delegationType,
        mode: delegationPolicyModeDraft,
        maxLiveTasks,
        preferredSubstrateId: delegationSubstrateId || undefined,
        autoDelegate: currentDelegationRoute?.auto_delegate,
      });
    } finally {
      setDelegationPolicySaving(false);
    }
  }

  function toggleCaptureSelection(captureId: string) {
    setSelectedCaptureIds((current) =>
      current.includes(captureId) ? current.filter((id) => id !== captureId) : [...current, captureId],
    );
  }

  function toggleEvidenceSelection(evidenceId: string) {
    setSelectedEvidenceIds((current) =>
      current.includes(evidenceId) ? current.filter((id) => id !== evidenceId) : [...current, evidenceId],
    );
  }

  function handleAttachmentSelection(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) {
      return;
    }
    setSelectedFiles((current) => {
      const next = [...current];
      for (const file of files) {
        const id = fileDraftId(file);
        if (next.some((entry) => entry.id === id)) {
          continue;
        }
        next.push({ id, file });
      }
      return next;
    });
    event.target.value = "";
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const form = event.currentTarget.closest("form");
      if (form) {
        form.requestSubmit();
      }
    }
  }

  const displayMessages = messages.filter((message) => message.role !== "tool");
  const conversationItems = useMemo(() => {
    const mixedItems: RenderItem[] = [
      ...displayMessages.map((message) => ({
        kind: "message" as const,
        key: message.id,
        createdAt: message.created_at,
        message,
      })),
      ...toolSteps.map((step) => ({
        kind: "tool" as const,
        key: step.id,
        createdAt: step.createdAt,
        step,
      })),
      ...transientWindows.map((windowRecord) => ({
        kind: "window" as const,
        key: windowRecord.id,
        createdAt: windowRecord.created_at,
        window: windowRecord,
      })),
    ];

    return groupConversationItems(mixedItems);
  }, [displayMessages, toolSteps, transientWindows]);

  useEffect(() => {
    const container = bodyRef.current;
    if (!container || !stickToBottomRef.current) {
      return;
    }

    bottomRef.current?.scrollIntoView({ behavior: isTypingHere ? "auto" : "smooth" });
  }, [conversationItems, isTypingHere]);

  function handleBodyScroll() {
    const container = bodyRef.current;
    if (!container) {
      return;
    }

    stickToBottomRef.current = isNearBottom(container);
  }

  const selectedProvider = providers.find((provider) => provider.id === selectedProviderId) ?? null;
  const scopeOptions = useMemo(() => scopeOptionsForSession(session), [session]);
  const activeBehaviorPatches = useMemo(
    () => applicableBehaviorPatches(appliedBehaviorPatches, session),
    [appliedBehaviorPatches, session],
  );
  const feedbackByMessageId = useMemo(() => {
    const map = new Map<string, FeedbackSignal>();
    if (!session) {
      return map;
    }
    for (const entry of feedbackEntries) {
      if (entry.sessionId === session.id) {
        map.set(entry.messageId, entry.signal);
      }
    }
    return map;
  }, [feedbackEntries, session]);
  const behaviorProposal = useMemo(
    () => deriveBehaviorProposal(feedbackEntries, activeBehaviorPatches, dismissedBehaviorProposals, session),
    [feedbackEntries, activeBehaviorPatches, dismissedBehaviorProposals, session],
  );

  useEffect(() => {
    if (!behaviorProposal) {
      setSelectedProposalScope(null);
      return;
    }
    setSelectedProposalScope((current) => current ?? behaviorProposal.recommendedScope);
  }, [behaviorProposal]);

  const busyStatusLabel = isBusyHere ? "Working here" : "Busy elsewhere";
  const busyStatusDetail = isBusyHere ? session?.label ?? null : busySessionLabel ?? null;
  const busyTitle = isBusyHere
    ? `Clo is actively working in ${session?.label ?? "this session"}.`
    : busySessionLabel
      ? `Clo is currently working in ${busySessionLabel}.`
      : "Clo is currently busy in another session.";

  function handleProviderChange(nextProviderId: string) {
    const provider = providers.find((candidate) => candidate.id === nextProviderId);
    setSelectedProviderId(nextProviderId);
    setSelectedModel(provider?.model_name ?? "");
  }

  const liveDelegationCount = delegationTasks.filter((task) => task.status === "queued" || task.status === "running").length;
  const recentDelegations = delegationTasks.slice(0, 6);
  const substrateById = new Map(delegationSubstrates.map((substrate) => [substrate.id, substrate]));
  const selectedDelegationSubstrate = delegationSubstrateId ? substrateById.get(delegationSubstrateId) ?? null : null;
  const currentDelegationRoute = delegationPolicy?.task_routes?.[delegationType] ?? null;
  const selectedContextCount = selectedFiles.length + selectedCaptureIds.length;
  const openDrawer = activeDrawer ?? visibleDrawer;

  async function refreshBehaviorState(currentSession: SessionDetail) {
    const state = await getSessionBehaviorState(currentSession.id);
    setFeedbackEntries(state.feedback.map(toFeedbackEntry));
    setAppliedBehaviorPatches(state.patches.map(toAppliedBehaviorPatch));
    setDismissedBehaviorProposals(state.dismissals.map(toBehaviorDismissal));
    setBehaviorError(null);
  }

  async function handleFeedback(message: TranscriptMessage, signal: FeedbackSignal) {
    if (!session || message.role !== "assistant") {
      return;
    }

    const nextTraits = classifyAssistantTraits(message.content);
    const nextPreview = truncateText(message.content);
    const nextEntry: FeedbackEntry = {
      id: `${session.id}:${message.id}`,
      messageId: message.id,
      sessionId: session.id,
      workspaceId: session.workspace_id,
      buildProjectId: session.build_project_id,
      signal,
      createdAt: new Date().toISOString(),
      messagePreview: nextPreview,
      traits: nextTraits,
    };

    const existingEntry = feedbackEntries.find(
      (entry) => entry.sessionId === session.id && entry.messageId === message.id,
    );

    setBehaviorPending(true);
    setBehaviorError(null);

    try {
      if (existingEntry?.signal === signal) {
        setFeedbackEntries((current) =>
          current.filter((entry) => !(entry.sessionId === session.id && entry.messageId === message.id)),
        );
        await deleteBehaviorFeedback(session.id, message.id);
      } else {
        setFeedbackEntries((current) => {
          const existingIndex = current.findIndex(
            (entry) => entry.sessionId === session.id && entry.messageId === message.id,
          );
          if (existingIndex === -1) {
            return [...current, nextEntry];
          }
          return current.map((entry, index) => (index === existingIndex ? nextEntry : entry));
        });
        const response = await upsertBehaviorFeedback(session.id, message.id, {
          signal,
          message_preview: nextPreview,
          traits: nextTraits,
        });
        setFeedbackEntries((current) => {
          const normalized = toFeedbackEntry(response.feedback);
          const existingIndex = current.findIndex(
            (entry) => entry.sessionId === session.id && entry.messageId === message.id,
          );
          if (existingIndex === -1) {
            return [...current, normalized];
          }
          return current.map((entry, index) => (index === existingIndex ? normalized : entry));
        });
      }

      setBehaviorError(null);
    } catch (error) {
      await refreshBehaviorState(session);
      setBehaviorError(error instanceof Error ? error.message : "Failed to save behavior feedback.");
    } finally {
      setBehaviorPending(false);
    }
  }

  async function handleApplyBehaviorProposal() {
    if (!session || !behaviorProposal || !selectedProposalScope) {
      return;
    }

    const selectedScope = scopeOptions.find((option) => option.scope === selectedProposalScope) ?? scopeOptions[0];
    if (!selectedScope) {
      return;
    }

    setBehaviorPending(true);
    setBehaviorError(null);
    try {
      await applyBehaviorPatch(session.id, {
        rule_key: behaviorProposal.ruleKey,
        title: behaviorProposal.title,
        patch: behaviorProposal.patch,
        scope: selectedScope.scope,
        scope_id: selectedScope.scopeId,
        created_by: "user",
      });
      await refreshBehaviorState(session);
    } catch (error) {
      setBehaviorError(error instanceof Error ? error.message : "Failed to apply behavior patch.");
    } finally {
      setBehaviorPending(false);
    }
  }

  async function handleRejectBehaviorProposal() {
    if (!session || !behaviorProposal) {
      return;
    }

    setBehaviorPending(true);
    setBehaviorError(null);
    try {
      await dismissBehaviorProposal(session.id, behaviorProposal.ruleKey);
      await refreshBehaviorState(session);
    } catch (error) {
      setBehaviorError(error instanceof Error ? error.message : "Failed to reject behavior proposal.");
    } finally {
      setBehaviorPending(false);
    }
  }

  function handleReviseBehaviorProposal() {
    if (!behaviorProposal || scopeOptions.length <= 1) {
      return;
    }
    setSelectedProposalScope((current) => {
      const currentIndex = scopeOptions.findIndex((option) => option.scope === current);
      const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % scopeOptions.length;
      return scopeOptions[nextIndex]?.scope ?? behaviorProposal.recommendedScope;
    });
  }

  const renderedMessages =
    conversationItems.length > 0 ? (
      conversationItems.map((item) => {
        if (item.kind === "tool_group") {
          return (
            <div key={item.key} className="sc__tool-group">
              {item.steps.map((step) => (
                <ToolStepRow key={step.id} step={step} />
              ))}
            </div>
          );
        }

        if (item.kind === "window") {
          const artifact = buildTransientWindowArtifact(session, item.window);
          return (
            <div key={item.key} className="sc__window-wrap">
              <TransientWindowRenderer
                artifact={artifact}
                onPin={() => onToggleWindowPin?.(item.window.id, !item.window.state_flags.pinned)}
                onClose={() => onCloseWindow?.(item.window.id)}
              />
            </div>
          );
        }

        const msg = item.message;
        const assistantHtml = msg.role === "assistant" ? renderAssistantHtml(msg.content) : "";
        const feedbackSignal = feedbackByMessageId.get(msg.id) ?? null;
        const feedbackLocked = behaviorPending || (isTypingHere && msg.id.startsWith("stream-"));
        return (
          <div key={msg.id} className={`sc__msg sc__msg--${msg.role}`}>
            {msg.role === "assistant" && <div className="sc__msg-avatar">C</div>}
            <div className="sc__msg-bubble">
              {msg.role === "assistant" ? (
                <div className="sc__msg-content sc__msg-content--assistant" dangerouslySetInnerHTML={{ __html: assistantHtml }} />
              ) : (
                <div className="sc__msg-content">{normalizeDisplayText(msg.content)}</div>
              )}
              {msg.role === "assistant" && (
                <div className="sc__feedback-row" aria-label="Assistant message feedback">
                  <button
                    type="button"
                    className={`sc__feedback-btn${feedbackSignal === "up" ? " is-active is-positive" : ""}`}
                    onClick={() => handleFeedback(msg, "up")}
                    disabled={feedbackLocked}
                    aria-pressed={feedbackSignal === "up"}
                    title="Swipe right: reinforce this behavior"
                  >
                    ↗<span>Right</span>
                  </button>
                  <button
                    type="button"
                    className={`sc__feedback-btn${feedbackSignal === "down" ? " is-active is-negative" : ""}`}
                    onClick={() => handleFeedback(msg, "down")}
                    disabled={feedbackLocked}
                    aria-pressed={feedbackSignal === "down"}
                    title="Swipe left: mark dissatisfaction"
                  >
                    ↙<span>Left</span>
                  </button>
                  <button
                    type="button"
                    className={`sc__feedback-btn${feedbackSignal === "promote" ? " is-active is-promote" : ""}`}
                    onClick={() => handleFeedback(msg, "promote")}
                    disabled={feedbackLocked}
                    aria-pressed={feedbackSignal === "promote"}
                    title="Promote this behavior as canon"
                  >
                    ★<span>Promote</span>
                  </button>
                </div>
              )}
              <div className="sc__msg-time">{formatTime(msg.created_at)}</div>
            </div>
          </div>
        );
      })
    ) : session ? (
      <div className="sc__placeholder">Start the conversation</div>
    ) : (
      <div className="sc__placeholder">Select a session from the tree</div>
    );

  return (
    <section className="sc">
      <header className="sc__header">
        <button type="button" className="sc__back" onClick={onBack}>
          ←
        </button>
        <div className="sc__session">
          <span className="sc__session-label">{session?.label ?? "No session"}</span>
          {session && (
            <span className="sc__session-meta">
              {session.model} · {session.provider}
            </span>
          )}
        </div>
        <div className="sc__header-actions">
          {isBusy && (
            <div
              className={`sc__busy-indicator${isBusyHere ? " is-here" : " is-elsewhere"}`}
              title={busyTitle}
              aria-label={busyTitle}
            >
              <span className="sc__busy-glyph" aria-hidden="true">
                <span className="sc__busy-core" />
                <span className="sc__busy-orbit">
                  <span className="sc__busy-satellite" />
                </span>
              </span>
              <span className="sc__busy-copy">
                <span className="sc__busy-label">{busyStatusLabel}</span>
                {busyStatusDetail && <span className="sc__busy-detail">{busyStatusDetail}</span>}
              </span>
            </div>
          )}
          <button
            type="button"
            className={`sc__runtime-toggle${runtimeOpen ? " is-open" : ""}`}
            onClick={() => setRuntimeOpen((current) => !current)}
            disabled={!providers.length || isBusy}
          >
            <span className="sc__runtime-toggle-label">Runtime</span>
            <span className="sc__runtime-toggle-value">
              {selectedModel || selectedProvider?.model_name || session?.model || "No model"}
            </span>
          </button>
        </div>
      </header>

      {runtimeOpen && (
        <div className="sc__runtime-panel">
          <div className="sc__runtime-panel-label">Next message uses</div>
          <ProviderModelPicker
            providers={providers}
            providerId={selectedProviderId ?? ""}
            model={selectedModel}
            onProviderChange={handleProviderChange}
            onModelChange={setSelectedModel}
            disabled={isBusy}
            compact
            providerLabel="Provider"
            knownModelsLabel="Known models"
            modelInputLabel="Model id"
          />
          {session && <div className="sc__runtime-panel-meta">Session default: {session.model} · {session.provider}</div>}
        </div>
      )}

      {runtimeChannel?.domain === "pokemon" && pokemonBridgeStatus && (
        <section className="sc__pokemon-strip" aria-label="Pokemon runtime controls">
          <div className="sc__pokemon-strip-head">
            <div>
              <div className="sc__pokemon-kicker">Pokemon runtime</div>
              <div className="sc__pokemon-title">{runtimeChannel.name}</div>
              <div className="sc__pokemon-copy">
                {pokemonBridgeStatus.bridge.connected ? "Bridge connected" : "Bridge disconnected"}
                {pokemonBridgeStatus.state_summary.route ? ` · ${pokemonBridgeStatus.state_summary.route}` : ""}
                {pokemonBridgeStatus.state_summary.trainer_name ? ` · ${pokemonBridgeStatus.state_summary.trainer_name}` : ""}
              </div>
            </div>
            <div className={`sc__pokemon-bridge-badge${pokemonBridgeStatus.bridge.connected ? " is-live" : ""}`}>
              {pokemonBridgeStatus.bridge.connected ? "Live" : "Offline"}
            </div>
          </div>
          <div className="sc__pokemon-mode-list">
            {(["auto", "assist", "pause", "step"] as PokemonControlMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                className={`sc__pokemon-mode${pokemonBridgeStatus.control.mode === mode ? " is-active" : ""}`}
                onClick={() => void onSelectPokemonControlMode?.(mode)}
                disabled={pokemonControlPending}
              >
                {titleCaseToken(mode)}
              </button>
            ))}
          </div>
          <div className="sc__pokemon-meta-row">
            <div className="sc__pokemon-pill">team {pokemonBridgeStatus.state_summary.team_size}</div>
            <div className="sc__pokemon-pill">battle {pokemonBridgeStatus.state_summary.has_battle ? "yes" : "no"}</div>
            <div className="sc__pokemon-pill">encounter {pokemonBridgeStatus.state_summary.has_encounter ? "yes" : "no"}</div>
            <div className="sc__pokemon-pill">step budget {pokemonBridgeStatus.control.step_budget}</div>
            <div className="sc__pokemon-pill">events {pokemonBridgeStatus.bridge.last_event_count}</div>
            {pokemonBridgeStatus.bridge.low_confidence && <div className="sc__pokemon-pill warning">low confidence</div>}
          </div>
          <div className="sc__pokemon-copy">
            Last snapshot {pokemonBridgeStatus.bridge.last_snapshot_at ? formatTime(pokemonBridgeStatus.bridge.last_snapshot_at) : "not yet"}
            {pokemonBridgeStatus.control.operator_note ? ` · note: ${pokemonBridgeStatus.control.operator_note}` : ""}
          </div>
        </section>
      )}

      <div className="sc__body" ref={bodyRef} onScroll={handleBodyScroll}>
        {activeBehaviorPatches.length > 0 && (
          <div className="sc__policy-strip">
            <div className="sc__policy-strip-label">Active behavior patches</div>
            <div className="sc__policy-strip-list">
              {activeBehaviorPatches.map((patch) => {
                const scopeLabel = scopeOptions.find(
                  (option) => option.scope === patch.scope && option.scopeId === patch.scopeId,
                )?.label ?? (patch.scope === "global" ? "Global" : patch.scope.replace(/_/g, " "));
                return (
                  <div key={patch.id} className="sc__policy-pill">
                    <span className="sc__policy-pill-scope">{scopeLabel}</span>
                    <span className="sc__policy-pill-text">{patch.patch}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {renderedMessages}
        {behaviorProposal && (
          <section className="sc__proposal-card" aria-label="Behavior improvement proposal">
            <div className="sc__proposal-kicker">Behavior learning</div>
            <div className="sc__proposal-title">{behaviorProposal.title}</div>
            <div className="sc__proposal-copy">
              <strong>Observed pattern:</strong> {behaviorProposal.observedPattern}
            </div>
            <div className="sc__proposal-copy">
              <strong>Hypothesis:</strong> {behaviorProposal.hypothesis}
            </div>
            <div className="sc__proposal-copy">
              <strong>Proposed patch:</strong> {behaviorProposal.patch}
            </div>
            <div className="sc__proposal-scope-row">
              <span className="sc__proposal-scope-label">Scope</span>
              <div className="sc__proposal-scope-list">
                {scopeOptions.map((option) => (
                  <button
                    key={`${option.scope}:${option.scopeId}`}
                    type="button"
                    className={`sc__proposal-scope-btn${selectedProposalScope === option.scope ? " is-active" : ""}`}
                    onClick={() => setSelectedProposalScope(option.scope)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="sc__proposal-meta">Evidence sample: {behaviorProposal.sampleCount} rated assistant turns</div>
            <div className="sc__proposal-actions">
              <button type="button" className="sc__proposal-btn apply" onClick={() => void handleApplyBehaviorProposal()} disabled={behaviorPending}>
                Apply
              </button>
              <button type="button" className="sc__proposal-btn reject" onClick={() => void handleRejectBehaviorProposal()} disabled={behaviorPending}>
                Reject
              </button>
              <button type="button" className="sc__proposal-btn revise" onClick={handleReviseBehaviorProposal} disabled={behaviorPending}>
                Revise
              </button>
            </div>
          </section>
        )}
        {streamingThinking && (
          <div className="sc__thinking-card">
            <div className="sc__thinking-label">Thinking</div>
            <div className="sc__thinking-content">{normalizeDisplayText(streamingThinking).trim()}</div>
          </div>
        )}
        {isTypingHere && (
          <div className="sc__typing-indicator">
            <span />
            <span />
            <span />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {interactiveProcess && (
        <InteractiveExecPanel
          process={interactiveProcess}
          pending={interactivePending}
          errorMessage={interactiveError}
          onSendInput={onSendInteractiveInput}
          onSendKey={onSendInteractiveKey}
          onTerminate={onTerminateInteractiveProcess}
        />
      )}

      <div className="sc__footer">
        {errorMessage && <div className="sc__error">{errorMessage}</div>}
        {behaviorError && <div className="sc__error">{behaviorError}</div>}
        {session && (
          <>
            <div className="sc__drawer-handle-row">
              <button
                type="button"
                className={`sc__drawer-handle${openDrawer ? " is-open" : ""}`}
                onClick={() => setActiveDrawer((current) => current ? null : "context")}
                aria-label={openDrawer ? "Close utilities drawer" : "Open utilities drawer"}
                title={openDrawer ? "Close utilities" : "Open utilities"}
              >
                <span className="sc__drawer-handle-icon" aria-hidden="true">⌃</span>
              </button>
            </div>

            {visibleDrawer && (
              <section className={`sc__drawer${drawerClosing ? " is-closing" : ""}`} aria-label="Captures and attachments drawer">
                <div className="sc__drawer-head">
                  <div>
                    <div className="sc__drawer-kicker">Utilities drawer</div>
                    <div className="sc__drawer-tabs" role="tablist" aria-label="Utility drawer sections">
                      <button
                        type="button"
                        role="tab"
                        aria-selected={openDrawer === "context"}
                        className={`sc__drawer-tab${openDrawer === "context" ? " is-active" : ""}`}
                        onClick={() => setActiveDrawer("context")}
                      >
                        Captures
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={openDrawer === "delegation"}
                        className={`sc__drawer-tab${openDrawer === "delegation" ? " is-active" : ""}`}
                        onClick={() => setActiveDrawer("delegation")}
                      >
                        Delegation
                      </button>
                    </div>
                    {openDrawer === "context" ? (
                      <>
                        <div className="sc__drawer-title">Reusable context for the next turn</div>
                        <div className="sc__drawer-copy">
                          Captures are saved workspace signals like uploads, screenshots, or extracted references. Select any of them here to send more context without pasting everything back into chat.
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="sc__drawer-title">Let Clo upgrade this into frontier build work</div>
                        <div className="sc__drawer-copy">
                          Clo stays the conversational layer here. When a request needs real coding, repo inspection, or tool use, it can route that work to Codex first, then Claude Code or Copilot, and bring the report back into chat.
                        </div>
                      </>
                    )}
                  </div>
                  <button type="button" className="sc__drawer-close" onClick={() => setActiveDrawer(null)}>
                    Close
                  </button>
                </div>

                {openDrawer === "context" ? (
                  <>
                    <div className="sc__context-row">
                      <div className="sc__context-label">Recent captures</div>
                      {workspaceCaptures.length > 0 ? (
                        <div className="sc__context-chips">
                          {workspaceCaptures.slice(0, 6).map((capture) => {
                            const selected = selectedCaptureIds.includes(capture.id);
                            return (
                              <button
                                key={capture.id}
                                type="button"
                                className={`sc__context-chip${selected ? " is-selected" : ""}`}
                                onClick={() => toggleCaptureSelection(capture.id)}
                              >
                                <span>{capture.event_type}</span>
                                <span>{capture.content.slice(0, 36) || capture.id}</span>
                              </button>
                            );
                          })}
                        </div>
                      ) : (
                        <div className="sc__context-empty">No saved captures for this session yet.</div>
                      )}
                    </div>

                    {(selectedFiles.length > 0 || selectedCaptureIds.length > 0) && (
                      <div className="sc__context-row">
                        <div className="sc__context-label">Queued for next message</div>
                        <div className="sc__context-chips">
                          {selectedCaptureIds.map((captureId) => {
                            const capture = workspaceCaptures.find((item) => item.id === captureId);
                            return (
                              <button
                                key={captureId}
                                type="button"
                                className="sc__context-chip is-selected"
                                onClick={() => toggleCaptureSelection(captureId)}
                              >
                                <span>capture</span>
                                <span>{capture?.content.slice(0, 36) || captureId}</span>
                              </button>
                            );
                          })}
                          {selectedFiles.map((fileDraft) => (
                            <button
                              key={fileDraft.id}
                              type="button"
                              className="sc__context-chip is-selected"
                              onClick={() => setSelectedFiles((current) => current.filter((entry) => entry.id !== fileDraft.id))}
                            >
                              <span>{inferAttachmentType(fileDraft.file)}</span>
                              <span>{fileDraft.file.name} · {formatBytes(fileDraft.file.size)}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <div className="sc__delegation-head">
                      <div>
                        <div className="sc__delegation-kicker">Worker queue</div>
                        <div className="sc__delegation-title">
                          {liveDelegationCount > 0 ? `${liveDelegationCount} active task${liveDelegationCount === 1 ? "" : "s"}` : "Recent worker tasks"}
                        </div>
                      </div>
                      <button
                        type="button"
                        className={`sc__delegation-toggle${delegationOpen ? " is-open" : ""}`}
                        onClick={() => setDelegationOpen((current) => !current)}
                      >
                        {delegationOpen ? "Hide form" : "New task"}
                      </button>
                    </div>

                    {delegationPolicy && (
                      <div className="sc__delegation-meta">
                        Mode {delegationPolicy.mode} · max live {delegationPolicy.max_live_tasks}
                        {currentDelegationRoute?.preferred_substrate_id ? ` · default ${currentDelegationRoute.preferred_substrate_id}` : ""}
                      </div>
                    )}

                    {workspaceEvidence.length > 0 && (
                      <div className="sc__context-row sc__context-row--delegation">
                        <div className="sc__context-label">Evidence context</div>
                        <div className="sc__context-chips">
                          {workspaceEvidence.slice(0, 5).map((evidence) => {
                            const selected = selectedEvidenceIds.includes(evidence.id);
                            const scopeLabel = evidence.session_id === session.id ? evidence.evidence_type : `workspace · ${evidence.evidence_type}`;
                            return (
                              <button
                                key={evidence.id}
                                type="button"
                                className={`sc__context-chip${selected ? " is-selected" : ""}`}
                                onClick={() => toggleEvidenceSelection(evidence.id)}
                              >
                                <span>{scopeLabel}</span>
                                <span>{evidence.title}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {delegationOpen && (
                      <div className="sc__delegation-form">
                        <div className="sc__delegation-controls">
                          <CustomSelect
                            triggerClassName="sc__delegation-select"
                            value={delegationType}
                            onChange={setDelegationType}
                            disabled={delegationPending}
                            options={[
                              { value: "review", label: "Review" },
                              { value: "summarize", label: "Summarize" },
                              { value: "audit", label: "Audit" },
                              { value: "proposal", label: "Proposal" },
                              { value: "plan", label: "Plan" },
                              { value: "inspect", label: "Inspect" },
                              { value: "implement", label: "Implement" },
                              { value: "verify", label: "Verify" },
                            ]}
                            ariaLabel="Delegation task type"
                          />
                          <CustomSelect
                            triggerClassName="sc__delegation-select"
                            value={delegationSubstrateId}
                            onChange={setDelegationSubstrateId}
                            disabled={delegationPending}
                            options={delegationSubstrates.map((substrate) => ({
                              value: substrate.id,
                              label: `${substrate.label}${substrate.dispatchable ? "" : " (unavailable)"}`,
                            }))}
                            ariaLabel="Delegation substrate"
                          />
                          <CustomSelect
                            triggerClassName="sc__delegation-select"
                            value={delegationPolicyModeDraft}
                            onChange={(value) => setDelegationPolicyModeDraft(value as "manual" | "suggest" | "auto")}
                            disabled={delegationPending || delegationPolicySaving}
                            options={[
                              { value: "manual", label: "Manual mode" },
                              { value: "suggest", label: "Suggest mode" },
                              { value: "auto", label: "Auto mode" },
                            ]}
                            ariaLabel="Delegation mode"
                          />
                          <button
                            type="button"
                            className="sc__delegation-send"
                            onClick={() => void handleDelegationSubmit()}
                            disabled={!delegationDraft.trim() || delegationPending || (selectedDelegationSubstrate ? !selectedDelegationSubstrate.dispatchable : false)}
                          >
                            Queue
                          </button>
                        </div>
                        <div className="sc__delegation-controls">
                          <input
                            className="sc__delegation-budget"
                            value={delegationPolicyMaxLiveDraft}
                            onChange={(event) => setDelegationPolicyMaxLiveDraft(event.target.value.replace(/[^\d]/g, ""))}
                            placeholder="Max live tasks"
                            disabled={delegationPending || delegationPolicySaving}
                          />
                          <input
                            className="sc__delegation-budget"
                            value={delegationMaxOutputTokens}
                            onChange={(event) => setDelegationMaxOutputTokens(event.target.value.replace(/[^\d]/g, ""))}
                            placeholder="Max output tokens"
                            disabled={delegationPending}
                          />
                          <input
                            className="sc__delegation-budget"
                            value={delegationMaxDurationSeconds}
                            onChange={(event) => setDelegationMaxDurationSeconds(event.target.value.replace(/[^\d]/g, ""))}
                            placeholder="Max seconds"
                            disabled={delegationPending}
                          />
                          <button
                            type="button"
                            className="sc__delegation-toggle"
                            onClick={() => void handleDelegationPolicySave()}
                            disabled={delegationPending || delegationPolicySaving || !delegationPolicy}
                          >
                            {delegationPolicySaving ? "Saving..." : "Save policy"}
                          </button>
                        </div>
                        <textarea
                          className="sc__delegation-input"
                          value={delegationDraft}
                          onChange={(event) => setDelegationDraft(event.target.value)}
                          placeholder="Queue a delegated worker task..."
                          rows={2}
                          disabled={delegationPending}
                        />
                        <div className="sc__delegation-meta">
                          {selectedDelegationSubstrate ? `${selectedDelegationSubstrate.label} · ${selectedDelegationSubstrate.health_status}` : "Choose a worker substrate."}
                        </div>
                        {(selectedCaptureIds.length > 0 || selectedEvidenceIds.length > 0) && (
                          <div className="sc__delegation-meta">
                            Delegation will include {selectedCaptureIds.length} capture reference{selectedCaptureIds.length === 1 ? "" : "s"} and {selectedEvidenceIds.length} evidence reference{selectedEvidenceIds.length === 1 ? "" : "s"}.
                          </div>
                        )}
                      </div>
                    )}

                    <div className="sc__delegation-list">
                      {recentDelegations.length > 0 ? recentDelegations.map((task) => (
                        <div key={task.id} className={`sc__delegation-item ${task.status}`}>
                          <button
                            type="button"
                            className="sc__delegation-item-head"
                            onClick={() => setExpandedDelegationId((current) => current === task.id ? null : task.id)}
                          >
                            <div className="sc__delegation-item-title">{task.title || formatTaskLabel(task.task_type)}</div>
                            <div className={`sc__delegation-status ${task.status}`}>{task.status}</div>
                          </button>
                          <div className="sc__delegation-item-summary">{task.result_summary || task.instruction}</div>
                          <div className="sc__delegation-item-route">
                            {substrateById.get(task.substrate_id ?? "")?.label ?? task.substrate_id ?? "worker"}
                            {task.authority_mode ? ` · ${task.authority_mode}` : ""}
                            {summarizeDelegationBudget(task) ? ` · ${summarizeDelegationBudget(task)}` : ""}
                            {formatDurationMs(task.duration_ms) ? ` · ${formatDurationMs(task.duration_ms)}` : ""}
                          </div>
                          {task.provider_route && (
                            <div className="sc__delegation-item-route">
                              {task.provider_route.resolved_provider}
                              {task.provider_route.resolved_model ? ` / ${task.provider_route.resolved_model}` : ""}
                            </div>
                          )}
                          {expandedDelegationId === task.id && (
                            <div className="sc__delegation-item-detail">
                              <div><strong>Instruction:</strong> {task.instruction}</div>
                              {task.result_text && <div><strong>Result:</strong> {task.result_text}</div>}
                              {Array.isArray(task.result_payload.files_touched) && task.result_payload.files_touched.length > 0 && (
                                <div><strong>Files:</strong> {(task.result_payload.files_touched as string[]).join(", ")}</div>
                              )}
                              {Array.isArray(task.result_payload.tests_run) && task.result_payload.tests_run.length > 0 && (
                                <div><strong>Tests:</strong> {(task.result_payload.tests_run as string[]).join(", ")}</div>
                              )}
                              {(task.input_tokens || task.output_tokens) && (
                                <div><strong>Usage:</strong> in {task.input_tokens ?? 0} · out {task.output_tokens ?? 0}</div>
                              )}
                              {task.error && <div><strong>Error:</strong> {task.error}</div>}
                              {task.provider_route && <div><strong>Route reason:</strong> {task.provider_route.route_reason}</div>}
                              <div className="sc__delegation-item-meta">
                                {Array.isArray(task.metadata.capture_ids) ? `${task.metadata.capture_ids.length} capture ref(s)` : "0 capture ref(s)"}
                                {" · "}
                                {Array.isArray(task.metadata.evidence_ids) ? `${task.metadata.evidence_ids.length} evidence ref(s)` : "0 evidence ref(s)"}
                                {" · "}
                                {formatTime(task.created_at)}
                              </div>
                            </div>
                          )}
                        </div>
                      )) : (
                        <div className="sc__delegation-empty">No delegation tasks for this session yet.</div>
                      )}
                    </div>
                  </>
                )}
              </section>
            )}
          </>
        )}
        {isBusyHere ? (
          <div className="sc__steer-strip">
            <button
              type="button"
              className="sc__steer-interrupt"
              onClick={onInterrupt}
              title="Stop the current run"
            >
              ⊘
            </button>
            <textarea
              className="sc__steer-input"
              value={steerDraft}
              onChange={(e) => setSteerDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  const value = steerDraft.trim();
                  if (value) {
                    onSteer?.(value);
                    setSteerDraft("");
                  }
                }
              }}
              placeholder="Steer Clo…  (interrupt + send)"
              rows={1}
            />
            <button
              type="button"
              className="sc__steer-send"
              disabled={!steerDraft.trim()}
              onClick={() => {
                const value = steerDraft.trim();
                if (value) {
                  onSteer?.(value);
                  setSteerDraft("");
                }
              }}
              title="Interrupt and send"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </button>
          </div>
        ) : (
          <form className="sc__composer" onSubmit={handleSubmit}>
            <input
              ref={attachmentInputRef}
              className="sc__attach-input"
              type="file"
              multiple
              onChange={handleAttachmentSelection}
            />
            <button
              type="button"
              className="sc__attach"
              title="Attach file"
              onClick={() => attachmentInputRef.current?.click()}
              disabled={!session || isBusy}
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            <textarea
              ref={textareaRef}
              className="sc__input"
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                autoResize();
              }}
              onKeyDown={handleKeyDown}
              placeholder={session ? "Message…" : "Select a session first"}
              disabled={!session || isBusy}
              rows={1}
            />
            <button
              type="submit"
              className="sc__send"
              disabled={!session || !draft.trim() || isBusy}
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </button>
          </form>
        )}
      </div>
    </section>
  );
}

interface InteractiveExecPanelProps {
  process: InteractiveProcessRecord;
  pending: boolean;
  errorMessage: string | null;
  onSendInput?: (text: string) => Promise<void> | void;
  onSendKey?: (key: string) => Promise<void> | void;
  onTerminate?: () => Promise<void> | void;
}

function InteractiveExecPanel({
  process,
  pending,
  errorMessage,
  onSendInput,
  onSendKey,
  onTerminate,
}: InteractiveExecPanelProps) {
  const [draft, setDraft] = useState("");
  const outputRef = useRef<HTMLPreElement | null>(null);
  const isRunning = process.status === "running";
  const statusLabel = isRunning ? "Live" : process.return_code === 0 ? "Completed" : "Exited";

  useEffect(() => {
    setDraft("");
  }, [process.session_id]);

  useEffect(() => {
    const el = outputRef.current;
    if (!el) {
      return;
    }
    el.scrollTop = el.scrollHeight;
  }, [process.output]);

  function submitDraft() {
    if (!draft) {
      return;
    }
    void onSendInput?.(draft);
    setDraft("");
  }

  return (
    <section className={`sc__terminal${isRunning ? "" : " is-complete"}`}>
      <div className="sc__terminal-head">
        <div>
          <div className="sc__terminal-kicker">Interactive Exec</div>
          <div className="sc__terminal-command">{process.command || "Managed process"}</div>
        </div>
        <div className={`sc__terminal-status ${process.status}`}>{statusLabel}</div>
      </div>

      <div className="sc__terminal-meta">
        <span>pid {process.pid ?? "-"}</span>
        <span>{process.workdir || "workspace default"}</span>
        <span>{Math.round(process.elapsed_seconds)}s</span>
      </div>

      <pre ref={outputRef} className="sc__terminal-output">{process.output || "Waiting for process output..."}</pre>

      {errorMessage && <div className="sc__terminal-error">{errorMessage}</div>}

      <div className="sc__terminal-controls">
        <textarea
          className="sc__terminal-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submitDraft();
            }
          }}
          placeholder={isRunning ? "Type into the live process and press Enter" : "Process finished"}
          disabled={!isRunning || pending}
          rows={1}
        />
        <button type="button" className="sc__terminal-btn primary" onClick={submitDraft} disabled={!isRunning || pending || !draft}>
          Send
        </button>
        <button type="button" className="sc__terminal-btn" onClick={() => void onSendKey?.("Enter")} disabled={!isRunning || pending}>
          Enter
        </button>
        <button type="button" className="sc__terminal-btn" onClick={() => void onSendKey?.("Tab")} disabled={!isRunning || pending}>
          Tab
        </button>
        <button type="button" className="sc__terminal-btn danger" onClick={() => void onSendKey?.("Ctrl+C")} disabled={!isRunning || pending}>
          Ctrl+C
        </button>
        <button type="button" className="sc__terminal-btn" onClick={() => void onTerminate?.()} disabled={!isRunning || pending}>
          Kill
        </button>
      </div>
    </section>
  );
}

function ToolStepRow({ step }: { step: ToolStepView }) {
  if (step.presentation === "card") {
    return <RuntimeSignalCard step={step} />;
  }

  const [expanded, setExpanded] = useState(false);
  const hasDetail = Boolean(step.detail);
  const statusIcon =
    step.status === "running"
      ? "○"
      : step.status === "warning"
        ? "!"
      : step.status === "success"
        ? "✓"
        : step.status === "interrupted"
          ? "⊘"
          : "✗";

  return (
    <div className={`sc__tool-step ${step.status}${hasDetail ? " has-detail" : ""}`}>
      <div
        className="sc__tool-step-main"
        onClick={() => hasDetail && setExpanded((current) => !current)}
      >
        <span className={`sc__tool-step-icon ${step.status}`}>{statusIcon}</span>
        <span className="sc__tool-step-name">{step.toolName}</span>
        <span className="sc__tool-step-summary">{step.summary}</span>
        <span className={`sc__tool-step-badge ${step.status}`}>{step.statusLabel}</span>
        {hasDetail && <span className="sc__tool-step-expand">{expanded ? "▲" : "▼"}</span>}
      </div>
      {expanded && step.detail && <pre className="sc__tool-step-detail">{step.detail}</pre>}
    </div>
  );
}

function RuntimeSignalCard({ step }: { step: ToolStepView }) {
  return (
    <div className={`sc__runtime-card ${step.status}`}>
      <div className="sc__runtime-card-head">
        <div>
          <div className="sc__runtime-card-kicker">{step.toolName}</div>
          <div className="sc__runtime-card-title">{step.title || step.summary}</div>
        </div>
        <div className={`sc__runtime-card-badge ${step.status}`}>{step.statusLabel}</div>
      </div>
      {step.title && <div className="sc__runtime-card-summary">{step.summary}</div>}
      {step.detail && <div className="sc__runtime-card-detail">{step.detail}</div>}
      {step.recoveryPrompt && <div className="sc__runtime-card-recovery">{step.recoveryPrompt}</div>}
      <div className="sc__runtime-card-time">{formatTime(step.createdAt)}</div>
    </div>
  );
}

export default SimpleChat;
