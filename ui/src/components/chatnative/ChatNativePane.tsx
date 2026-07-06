import { useEffect, useMemo, useRef, useState } from "react";
import type {
  BuildProjectRecord,
  PlanProposalRecord,
  PlanRecord,
  SessionDetail,
  SessionEventRecord,
  WorkspaceEvidenceRecord,
  WorkspaceRuntimeRecord,
} from "../../api/types";
import type { ChatMessageView, ToolStepData } from "../chat/ChatPane";
import { ARTIFACT_REGISTRY } from "./artifacts/registry";
import { renderArtifactBody } from "./artifacts/ArtifactRenderers";
import type {
  ArtifactAction,
  ArtifactDescriptor,
  ArtifactKind,
  ArtifactRegistryEntry,
} from "./artifacts/types";
import "./ChatNativePane.css";

export type { ArtifactAction, ArtifactDescriptor, ArtifactKind } from "./artifacts/types";

type ConversationItem =
  | { kind: "message"; key: string; createdAt: string; message: ChatMessageView }
  | { kind: "tool_group"; key: string; createdAt: string; steps: ChatMessageView[] }
  | { kind: "artifact"; key: string; createdAt: string; artifact: ArtifactDescriptor };

interface ChatNativePaneProps {
  sessionTitle: string;
  session: SessionDetail | null;
  workspaceName: string;
  buildProject: BuildProjectRecord | null;
  messages: ChatMessageView[];
  artifacts: ArtifactDescriptor[];
  plan: PlanRecord | null;
  proposals: PlanProposalRecord[];
  events: SessionEventRecord[];
  workspaceRuntime: WorkspaceRuntimeRecord | null;
  evidence: WorkspaceEvidenceRecord[];
  isSending: boolean;
  errorMessage: string | null;
  onSend: (content: string) => void;
  onArtifactAction: (action: ArtifactAction) => void;
  onSummonArtifact: (kind: ArtifactKind, source?: "chip" | "intent") => void;
  onResumeRun: () => void;
  onInterruptRun: () => void;
  onRerunLastTurn: () => void;
  onInspectPlan: () => void;
  onApplySignalAction: (signalId: string, action: string, sessionId?: string | null) => Promise<void>;
}

const SUGGESTION_CHIPS: Array<{ label: string; kind: ArtifactKind; prompt: string }> = [
  { label: "Overview", kind: "workspace_overview", prompt: "Show overview" },
  { label: "Inspector", kind: "chat_inspector", prompt: "Open inspector" },
  { label: "Plan", kind: "plan", prompt: "Show plan" },
  { label: "Review", kind: "review", prompt: "Open review" },
  { label: "Runtime", kind: "runtime_status", prompt: "Show runtime" },
  { label: "Visual board", kind: "generated_html", prompt: "Show this differently as a visual board" },
];

export default function ChatNativePane({
  sessionTitle,
  session,
  workspaceName,
  buildProject,
  messages,
  artifacts,
  plan,
  proposals,
  events,
  workspaceRuntime,
  evidence,
  isSending,
  errorMessage,
  onSend,
  onArtifactAction,
  onSummonArtifact,
  onResumeRun,
  onInterruptRun,
  onRerunLastTurn,
  onInspectPlan,
  onApplySignalAction,
}: ChatNativePaneProps) {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  const visibleArtifacts = useMemo(() => artifacts.filter((artifact) => !artifact.dismissed), [artifacts]);
  const conversationItems = useMemo(
    () => buildConversationItems(messages, visibleArtifacts),
    [messages, visibleArtifacts],
  );

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    // Only auto-scroll if the user is near the bottom (within 150px)
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
    if (nearBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [conversationItems]);

  const handleSend = () => {
    const nextInput = input.trim();
    if (!nextInput || isSending) {
      return;
    }
    onSend(nextInput);
    setInput("");
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  const hasConversation = conversationItems.length > 0;
  const currentProvider = session?.provider || "idle";
  const currentModel = session?.model || "no-model";
  const currentRunState = session?.current_run?.status || (isSending ? "running" : "idle");
  const nextItem = plan?.next_item?.content || plan?.active_goal || "No active plan item";
  const contextGuard = buildContextGuardSummary(plan);
  const latestStep = [...messages].reverse().find((item) => item.role === "tool_step" && item.toolStep)?.toolStep || null;

  return (
    <div className="chat-native-pane">
      <div className="chat-native-context-strip">
        <ContextPill label="Workspace" value={workspaceName} detail={buildProject?.name || "workspace-wide"} />
        <ContextPill label="Run" value={currentRunState} detail={session?.current_run ? `turn ${session.current_run.turn_number}` : "ready"} />
        <ContextPill label="Plan" value={truncateValue(nextItem, 36)} detail={plan?.title || "Active goal"} />
        <ContextPill label="Model" value={currentModel} detail={currentProvider} />
        <ContextPill label="Context" value={contextGuard.value} detail={contextGuard.detail} />
        <ContextPill
          label="Last step"
          value={latestStep ? latestStep.toolName : "No tool work yet"}
          detail={latestStep ? truncateValue(latestStep.summary, 28) : "chat-only"}
        />
      </div>

      <div ref={messagesContainerRef} className="chat-native-thread">
        {!hasConversation && (
          <div className="chat-native-empty">
            <div className="chat-native-empty-title">Chat with Clo to operate OpenCloset.</div>
            <div className="chat-native-empty-copy">
              Ask for implementation, an overview, the current plan, runtime state, review items, or a private generated
              view. Clo can surface the working view inline when you ask.
            </div>
            <div className="chat-native-chip-row">
              {SUGGESTION_CHIPS.map((chip) => (
                <button
                  key={chip.kind}
                  className="chat-native-chip"
                  type="button"
                  onClick={() => {
                    setInput(chip.prompt);
                    onSend(chip.prompt);
                  }}
                  disabled={isSending}
                >
                  {chip.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {conversationItems.map((item) => {
          if (item.kind === "tool_group") {
            return (
              <div key={item.key} className="chat-native-tool-group">
                <div className="chat-native-step-label">Operational steps</div>
                {item.steps.map((step) => (
                  <ToolStepRow key={step.id} step={step.toolStep!} />
                ))}
              </div>
            );
          }

          if (item.kind === "artifact") {
            const registryEntry = ARTIFACT_REGISTRY[item.artifact.kind];
            return (
              <ArtifactFrame
                key={item.key}
                artifact={item.artifact}
                registryEntry={registryEntry}
                session={session}
                workspaceName={workspaceName}
                buildProject={buildProject}
                plan={plan}
                proposals={proposals}
                events={events}
                workspaceRuntime={workspaceRuntime}
                evidence={evidence}
                onArtifactAction={onArtifactAction}
                onSummonArtifact={onSummonArtifact}
                onInspectPlan={onInspectPlan}
                onApplySignalAction={onApplySignalAction}
              />
            );
          }

          return (
            <ConversationBubble
              key={item.key}
              message={item.message}
              provider={currentProvider}
              model={currentModel}
              onResumeRun={onResumeRun}
              onInterruptRun={onInterruptRun}
              onRerunLastTurn={onRerunLastTurn}
            />
          );
        })}

        <div ref={messagesEndRef} />
      </div>

      <div className="chat-native-composer-shell">
        <div className="chat-native-chip-row composer">
          {SUGGESTION_CHIPS.map((chip) => (
            <button
              key={chip.kind}
              className="chat-native-chip subtle"
              type="button"
              onClick={() => {
                setInput(chip.prompt);
                onSend(chip.prompt);
              }}
              disabled={isSending}
            >
              {chip.label}
            </button>
          ))}
        </div>

        <div className="chat-native-composer">
          <textarea
            className="chat-native-input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Message Clo in "${sessionTitle}"...`}
            rows={1}
            disabled={isSending}
          />
          <button className="chat-native-send" type="button" onClick={handleSend} disabled={isSending}>
            {isSending ? "..." : "Send"}
          </button>
        </div>
        {errorMessage && <div className="chat-native-error">{errorMessage}</div>}
      </div>
    </div>
  );
}

function buildConversationItems(messages: ChatMessageView[], artifacts: ArtifactDescriptor[]): ConversationItem[] {
  const items: ConversationItem[] = [];
  let index = 0;

  while (index < messages.length) {
    if (messages[index].role === "tool_step") {
      const steps: ChatMessageView[] = [];
      const key = messages[index].id;
      const createdAt = messages[index].timestamp;
      while (index < messages.length && messages[index].role === "tool_step") {
        steps.push(messages[index]);
        index += 1;
      }
      items.push({ kind: "tool_group", key, createdAt, steps });
      continue;
    }

    items.push({
      kind: "message",
      key: messages[index].id,
      createdAt: messages[index].timestamp,
      message: messages[index],
    });
    index += 1;
  }

  const artifactItems = artifacts.map<ConversationItem>((artifact) => ({
    kind: "artifact",
    key: artifact.id,
    createdAt: artifact.created_at,
    artifact,
  }));

  return [...items, ...artifactItems].sort((left, right) => {
    const timeDelta = Date.parse(left.createdAt) - Date.parse(right.createdAt);
    if (timeDelta !== 0) {
      return timeDelta;
    }
    return left.key.localeCompare(right.key);
  });
}

function ArtifactFrame({
  artifact,
  registryEntry,
  session,
  workspaceName,
  buildProject,
  plan,
  proposals,
  events,
  workspaceRuntime,
  evidence,
  onArtifactAction,
  onSummonArtifact,
  onInspectPlan,
  onApplySignalAction,
}: {
  artifact: ArtifactDescriptor;
  registryEntry: ArtifactRegistryEntry;
  session: SessionDetail | null;
  workspaceName: string;
  buildProject: BuildProjectRecord | null;
  plan: PlanRecord | null;
  proposals: PlanProposalRecord[];
  events: SessionEventRecord[];
  workspaceRuntime: WorkspaceRuntimeRecord | null;
  evidence: WorkspaceEvidenceRecord[];
  onArtifactAction: (action: ArtifactAction) => void;
  onSummonArtifact: (kind: ArtifactKind, source?: "chip" | "intent") => void;
  onInspectPlan: () => void;
  onApplySignalAction: (signalId: string, action: string, sessionId?: string | null) => Promise<void>;
}) {
  const isCollapsed = Boolean(artifact.frame_state.collapsed);

  return (
    <section className={`artifact-frame ${isCollapsed ? "collapsed" : ""}`}>
      <div className="artifact-frame-header">
        <div>
          <div className="artifact-frame-title">{artifact.title}</div>
          <div className="artifact-frame-meta">
            {workspaceName}
            {buildProject ? ` · ${buildProject.name}` : ""}
            {session ? ` · ${session.label}` : ""}
          </div>
        </div>
        <div className="artifact-frame-actions">
          <button
            className="artifact-frame-action"
            type="button"
            onClick={() => onArtifactAction({ type: artifact.frame_state.pinned ? "pin" : "pin", artifact_id: artifact.id, pinned: !artifact.frame_state.pinned })}
          >
            {artifact.frame_state.pinned ? "Unpin" : "Pin"}
          </button>
          <button
            className="artifact-frame-action"
            type="button"
            onClick={() => onArtifactAction({ type: isCollapsed ? "reopen" : "collapse", artifact_id: artifact.id })}
          >
            {isCollapsed ? "Reopen" : "Collapse"}
          </button>
          {registryEntry.supports_persist && (
            <button className="artifact-frame-action" type="button" onClick={() => onArtifactAction({ type: "mark_persist", artifact_id: artifact.id })}>
              Save later
            </button>
          )}
          {registryEntry.supports_export && (
            <button className="artifact-frame-action" type="button" onClick={() => onArtifactAction({ type: "mark_export", artifact_id: artifact.id })}>
              Export later
            </button>
          )}
          <button className="artifact-frame-dismiss" type="button" onClick={() => onArtifactAction({ type: "dismiss", artifact_id: artifact.id })}>
            Close
          </button>
        </div>
      </div>
      {!isCollapsed && (
        <div className="artifact-frame-body">
          {renderArtifactBody({
            artifact,
            session,
            workspaceName,
            buildProject,
            plan,
            proposals,
            events,
            workspaceRuntime,
            evidence,
            onSummonArtifact,
            onInspectPlan,
            onApplySignalAction,
          })}
        </div>
      )}
    </section>
  );
}

function ConversationBubble({
  message,
  provider,
  model,
  onResumeRun,
  onInterruptRun,
  onRerunLastTurn,
}: {
  message: ChatMessageView;
  provider: string;
  model: string;
  onResumeRun: () => void;
  onInterruptRun: () => void;
  onRerunLastTurn: () => void;
}) {
  if (message.role === "system") {
    return (
      <div className="chat-native-outcome">
        <div className="chat-native-outcome-header">
          <span className="chat-native-outcome-badge">Run status</span>
          <span className="chat-native-outcome-time">{formatTime(message.timestamp)}</span>
        </div>
        <div className="chat-native-outcome-copy">{message.content}</div>
        <div className="chat-native-outcome-actions">
          <button className="chat-native-outcome-btn" type="button" onClick={onRerunLastTurn}>
            Retry turn
          </button>
          <button className="chat-native-outcome-btn subtle" type="button" onClick={onResumeRun}>
            Resume queued run
          </button>
          <button className="chat-native-outcome-btn subtle" type="button" onClick={onInterruptRun}>
            Interrupt
          </button>
        </div>
      </div>
    );
  }

  const roleClass = message.role === "user" ? "user" : "assistant";
  const label = message.role === "user" ? "You" : "Clo";

  return (
    <div className={`chat-native-message ${roleClass}`}>
      <div className="chat-native-message-label">{label}</div>
      {message.role === "assistant" && (
        <div className="chat-native-message-meta">
          {provider} · {model}
        </div>
      )}
      {message.isStreaming && (
        <div className="chat-native-streaming">
          <span className="chat-native-stream-dot" />
          Clo is responding
        </div>
      )}
      <div className={`chat-native-bubble ${roleClass}`}>
        {message.content}
        {message.isStreaming && <span className="chat-native-cursor">▌</span>}
      </div>
      <div className="chat-native-message-time">{formatTime(message.timestamp)}</div>
    </div>
  );
}

function ToolStepRow({ step }: { step: ToolStepData }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = Boolean(step.detail);
  const statusIcon =
    step.status === "running"
      ? "○"
      : step.status === "success"
        ? "✓"
        : step.status === "interrupted"
          ? "⊘"
          : "✕";

  return (
    <div className={`chat-native-step ${step.status}${hasDetail ? " has-detail" : ""}`}>
      <div
        className="chat-native-step-main"
        onClick={() => hasDetail && setExpanded((current) => !current)}
        title={hasDetail ? (expanded ? "Collapse details" : "Expand details") : undefined}
      >
        <span className={`chat-native-step-icon ${step.status}`}>{statusIcon}</span>
        <span className="chat-native-step-name">{step.toolName}</span>
        <span className="chat-native-step-summary">{step.summary}</span>
        <span className={`chat-native-step-badge ${step.status}`}>{step.status}</span>
      </div>
      {expanded && step.detail && <pre className="chat-native-step-detail">{step.detail}</pre>}
    </div>
  );
}

function ContextPill({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="chat-native-context-pill">
      <span className="chat-native-context-label">{label}</span>
      <span className="chat-native-context-value" title={value}>
        {value}
      </span>
      <span className="chat-native-context-detail" title={detail}>
        {detail}
      </span>
    </div>
  );
}

function buildContextGuardSummary(plan: PlanRecord | null): { value: string; detail: string } {
  const tokensUsed = Number(plan?.context_guard?.tokens_used || 0);
  const threshold = Number(plan?.context_guard?.rollover_threshold || 0);
  if (threshold <= 0) {
    return { value: "n/a", detail: "No guard configured" };
  }
  const tokenPercent = Math.max(0, Math.min(100, Math.round((tokensUsed / threshold) * 100)));
  return {
    value: `${tokenPercent}%`,
    detail: `${tokensUsed}/${threshold}`,
  };
}

function truncateValue(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength - 3)}...` : value;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}
