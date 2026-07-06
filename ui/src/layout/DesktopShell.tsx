import { useEffect, useRef, useState } from "react";
import {
  activatePlan,
  cancelCloQueueItem,
  closeTransientWindow,
  createDelegation,
  createTransientWindow,
  createBuildProject,
  createCloQueueItem,
  createSession,
  createWorkspace,
  executeRun,
  getPokemonBridgeStatus,
  getSessionDelegationPolicy,
  getInteractiveProcess,
  interruptRun,
  getMessages,
  getPlan,
  getSession,
  getSessionEvents,
  listBuildProjects,
  listCloQueue,
  listDelegationSubstrates,
  listProviderModels,
  listProviders,
  listRuntimeAgents,
  listSessions,
  listTransientWindows,
  listDelegations,
  uploadSessionAttachments,
  listWorkspaceCaptures,
  listWorkspaceEvidence,
  listWorkspacePlans,
  listWorkspaces,
  moveCloQueueItem,
  patchCloQueueSettings,
  patchPokemonControl,
  patchSessionDelegationPolicy,
  patchTransientWindow,
  patchSession,
  sendInteractiveProcessInput,
  streamCloQueue,
  streamRun,
  submitMessage,
  terminateInteractiveProcess,
} from "../api/client";
import type {
  BuildProjectRecord,
  CloQueueStateRecord,
  DelegationPolicyRecord,
  DelegationSubstrateRecord,
  DelegationTaskRecord,
  InteractiveProcessRecord,
  PlanRecord,
  PlanSummaryRecord,
  PokemonBridgeStatusRecord,
  PokemonControlMode,
  ProviderRecord,
  RuntimeAgentRecord,
  SessionEventRecord,
  SessionDetail,
  SessionSummary,
  TransientWindowRecord,
  TranscriptMessage,
  WorkspaceCaptureRecord,
  WorkspaceEvidenceRecord,
  WorkspaceSummary,
} from "../api/types";
import { SimpleChat, type ToolStepView } from "../components/chat/SimpleChat";
import { TreeViewHome } from "../components/workspace/TreeViewHome";
import "./DesktopShell.css";

const TOOL_DISPLAY_NAMES: Record<string, string> = {
  run_in_terminal: "Shell",
  exec_process: "Process",
  exec: "Shell",
  plan_create: "Plan",
  plan_update: "Plan",
  plan_activate: "Plan",
  plan_set_status: "Plan Runtime",
  read_file: "Read",
  read: "Read",
  write: "Write",
  create_file: "Write",
  edit_file: "Edit",
  file_search: "Search",
  grep_search: "Search",
  semantic_search: "Search",
  list_dir: "List",
};

const REDACTED_PREVIEW_KEYS = new Set(["content_base64", "script_content_base64"]);
const LARGE_INLINE_PREVIEW_KEYS = new Set(["content", "script_content", "command"]);

type ToolOutcome = {
  status: ToolStepView["status"];
  statusLabel: string;
  summary: string;
  detail: string;
};

function toRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function maybeParseJson(value: unknown): unknown {
  if (typeof value !== "string") {
    return value;
  }

  const trimmed = value.trim();
  if (!(trimmed.startsWith("{") || trimmed.startsWith("["))) {
    return value;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function redactPreviewValue(value: unknown, keyName = ""): unknown {
  if (typeof value === "string") {
    if (REDACTED_PREVIEW_KEYS.has(keyName)) {
      return `[omitted ${keyName}: ${value.length.toLocaleString()} chars]`;
    }
    if (LARGE_INLINE_PREVIEW_KEYS.has(keyName) && value.length > 320) {
      return `[omitted ${keyName}: ${value.length.toLocaleString()} chars]`;
    }
    return value;
  }

  if (Array.isArray(value)) {
    return value.map((entry) => redactPreviewValue(entry));
  }

  const record = toRecord(value);
  if (!record) {
    return value;
  }

  const redacted: Record<string, unknown> = {};
  for (const [entryKey, entryValue] of Object.entries(record)) {
    redacted[entryKey] = redactPreviewValue(entryValue, entryKey);
  }
  return redacted;
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function extractExitCode(value: unknown): number | null {
  const parsed = maybeParseJson(value);
  const record = toRecord(parsed);

  if (record) {
    return toNumber(record.exit_code) ?? toNumber(record.returncode) ?? toNumber(record.return_code);
  }

  if (typeof parsed === "string") {
    const match = parsed.match(/(?:exit code|return_code|returncode)\s*:\s*(-?\d+)/i);
    if (match) {
      return Number(match[1]);
    }
  }

  return null;
}

function prettifyToolName(toolKey: string): string {
  return TOOL_DISPLAY_NAMES[toolKey] ?? toolKey.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function basenameFromPath(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) {
    return null;
  }
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? value;
}

function extractTargetLabel(toolKey: string, payload: unknown): string | null {
  const parsed = maybeParseJson(payload);
  const record = toRecord(parsed);

  if (!record) {
    return null;
  }

  if (toolKey.startsWith("plan_")) {
    return firstString(record.title, record.plan_title, record.label, record.name);
  }

  return basenameFromPath(firstString(record.path, record.filePath, record.file, record.uri))
    ?? firstString(record.title, record.label, record.name, record.url);
}

function stringifyPreview(value: unknown, limit = 220): string {
  if (value == null) {
    return "";
  }

  const redactedValue = redactPreviewValue(value);

  const text =
    typeof redactedValue === "string"
      ? redactedValue
      : (() => {
          try {
            return JSON.stringify(redactedValue, null, 2);
          } catch {
            return String(redactedValue);
          }
        })();

  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length > limit ? `${compact.slice(0, limit - 3)}...` : compact;
}

function summarizeToolInput(toolKey: string, input: unknown): string {
  const target = extractTargetLabel(toolKey, input);

  if (toolKey === "run_in_terminal" || toolKey === "exec_process" || toolKey === "exec") {
    return "Running shell command";
  }
  if (toolKey === "plan_set_status") {
    const record = toRecord(maybeParseJson(input));
    const status = firstString(record?.status, record?.runtime_status);
    return status ? `Setting plan runtime to ${status}` : "Setting plan runtime";
  }
  if (toolKey === "write") {
    return target ? `Writing ${target}` : "Writing file";
  }
  if (toolKey === "read") {
    return target ? `Reading ${target}` : "Reading file";
  }
  if (toolKey === "plan_create") {
    return target ? `Creating plan: ${target}` : "Creating plan";
  }
  if (toolKey === "plan_update") {
    return target ? `Updating plan: ${target}` : "Updating plan";
  }
  if (toolKey === "read_file") {
    return target ? `Reading ${target}` : "Reading file";
  }
  if (toolKey === "create_file") {
    return target ? `Creating ${target}` : "Creating file";
  }
  if (toolKey === "edit_file") {
    return target ? `Updating ${target}` : "Updating file";
  }
  if (toolKey === "file_search" || toolKey === "grep_search" || toolKey === "semantic_search") {
    return "Searching workspace";
  }
  if (toolKey === "list_dir") {
    return target ? `Listing ${target}` : "Listing directory";
  }
  return `Running ${prettifyToolName(toolKey).toLowerCase()}`;
}

function extractWindowTarget(value: unknown): string | null {
  const record = toRecord(value);
  if (!record) {
    return firstString(value);
  }

  return firstString(
    record.path,
    record.file_path,
    record.filePath,
    record.target,
    record.target_path,
    record.directory,
    record.dirPath,
  );
}

function summarizeToolSuccess(toolKey: string, content: unknown): string {
  const target = extractTargetLabel(toolKey, content);

  if (toolKey === "run_in_terminal" || toolKey === "exec_process" || toolKey === "exec") {
    return "Ran shell command";
  }
  if (toolKey === "plan_set_status") {
    const record = toRecord(maybeParseJson(content));
    const status = firstString(record?.runtime_status, record?.status);
    return status ? `Plan runtime set to ${status}` : "Plan runtime updated";
  }
  if (toolKey === "write") {
    return target ? `Wrote ${target}` : "Wrote file";
  }
  if (toolKey === "read") {
    return target ? `Read ${target}` : "Read file";
  }
  if (toolKey === "plan_create") {
    return target ? `Created plan: ${target}` : "Created plan";
  }
  if (toolKey === "plan_update") {
    return target ? `Updated plan: ${target}` : "Updated plan";
  }
  if (toolKey === "read_file") {
    return target ? `Read ${target}` : "Read file";
  }
  if (toolKey === "create_file") {
    return target ? `Created ${target}` : "Created file";
  }
  if (toolKey === "edit_file") {
    return target ? `Updated ${target}` : "Updated file";
  }
  if (toolKey === "file_search" || toolKey === "grep_search" || toolKey === "semantic_search") {
    return "Search completed";
  }
  return `Completed ${prettifyToolName(toolKey).toLowerCase()}`;
}

function summarizeToolFailure(toolKey: string, detail: string, exitCode: number | null): string {
  if (toolKey === "run_in_terminal" || toolKey === "exec_process" || toolKey === "exec") {
    return exitCode != null ? `Shell command failed (exit ${exitCode})` : "Shell command failed";
  }
  const brief = stringifyPreview(detail, 100);
  return brief ? `Failed: ${brief}` : `Failed ${prettifyToolName(toolKey).toLowerCase()}`;
}

function deriveToolOutcome(
  toolKey: string,
  status: unknown,
  content: unknown,
  error: unknown,
): ToolOutcome {
  const rawStatus = typeof status === "string" ? status : "success";
  const detail = stringifyPreview(error || content, 1200);
  const exitCode = extractExitCode(error) ?? extractExitCode(content);
  const nestedStatus = firstString(toRecord(maybeParseJson(error))?.status, toRecord(maybeParseJson(content))?.status);

  if (rawStatus === "interrupted" || nestedStatus === "interrupted") {
    return { status: "interrupted", statusLabel: "Interrupted", summary: "Interrupted", detail };
  }

  if (
    rawStatus === "error" ||
    rawStatus === "permission_denied" ||
    nestedStatus === "error" ||
    nestedStatus === "permission_denied" ||
    (exitCode != null && exitCode !== 0) ||
    /permission denied|traceback|exception|failed/i.test(detail)
  ) {
    return {
      status: "error",
      statusLabel: "Failed",
      summary: summarizeToolFailure(toolKey, detail, exitCode),
      detail,
    };
  }

  if (/\bwarning\b|\btruncated\b/i.test(detail)) {
    return {
      status: "warning",
      statusLabel: "Warning",
      summary: summarizeToolSuccess(toolKey, content),
      detail,
    };
  }

  return {
    status: "success",
    statusLabel: "Completed",
    summary: summarizeToolSuccess(toolKey, content),
    detail,
  };
}

function buildToolStepsFromEvents(events: SessionEventRecord[]): ToolStepView[] {
  const steps: ToolStepView[] = [];

  for (const event of events) {
    const normalizedType = event.type.startsWith("stream.") ? event.type.slice("stream.".length) : event.type;
    const runtimeStep = buildRuntimeStepFromEvent(normalizedType, event.data, event.created_at, event.id);
    if (runtimeStep) {
      steps.push(runtimeStep);
      continue;
    }

    if (normalizedType === "tool_call" || normalizedType === "tool_use") {
      const toolKey = stringifyPreview(event.data.tool_name || event.data.name, 60) || "tool";
      steps.push({
        id: event.id,
        toolKey,
        toolName: prettifyToolName(toolKey),
        summary: summarizeToolInput(toolKey, event.data.input || event.data.arguments || event.data.args),
        statusLabel: "Running",
        detail: stringifyPreview(event.data.input || event.data.arguments || event.data.args, 600),
        status: "running",
        createdAt: event.created_at,
      });
      continue;
    }

    if (normalizedType === "tool_result") {
      const toolKey = stringifyPreview(event.data.tool_name || event.data.name, 60) || "tool";
      const outcome = deriveToolOutcome(toolKey, event.data.status, event.data.content, event.data.error);
      const runningIndex = [...steps]
        .map((step, index) => ({ step, index }))
        .reverse()
        .find(({ step }) => step.toolKey === toolKey && step.status === "running")?.index;

      if (runningIndex != null) {
        steps[runningIndex] = {
          ...steps[runningIndex],
          status: outcome.status,
          statusLabel: outcome.statusLabel,
          summary: outcome.summary,
          detail: outcome.detail || steps[runningIndex].detail,
          createdAt: event.created_at,
        };
      } else {
        steps.push({
          id: event.id,
          toolKey,
          toolName: prettifyToolName(toolKey),
          summary: outcome.summary,
          statusLabel: outcome.statusLabel,
          detail: outcome.detail,
          status: outcome.status,
          createdAt: event.created_at,
        });
      }
    }
  }

  return steps;
}

function formatSeconds(value: number | null): string | null {
  if (value == null || !Number.isFinite(value)) {
    return null;
  }
  return value >= 10 ? value.toFixed(0) : value.toFixed(1);
}

function buildRuntimeStepFromEvent(
  eventType: string,
  data: Record<string, unknown>,
  createdAt: string,
  id: string,
): ToolStepView | null {
  if (eventType === "provider_routed") {
    const requestedProvider = firstString(data.requested_provider) ?? "unknown";
    const resolvedProvider = firstString(data.resolved_provider) ?? requestedProvider;
    const requestedModel = firstString(data.requested_model);
    const resolvedModel = firstString(data.resolved_model);
    const routeReason = firstString(data.route_reason) ?? "provider routing";
    const usedAutoRouting = Boolean(data.used_auto_routing);
    const taskProfile = toRecord(data.task_profile);
    const attachmentTypes = Array.isArray(taskProfile?.attachment_types)
      ? taskProfile.attachment_types.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const detailParts = [
      `Requested ${requestedProvider}${requestedModel ? ` / ${requestedModel}` : ""}.`,
      `Resolved ${resolvedProvider}${resolvedModel ? ` / ${resolvedModel}` : ""}.`,
      `Reason: ${routeReason}.`,
    ];
    if (attachmentTypes.length > 0) {
      detailParts.push(`Input signals: ${attachmentTypes.join(", ")}.`);
    }
    if (taskProfile?.requires_vision === true) {
      detailParts.push("Vision input detected.");
    }
    if (taskProfile?.requires_audio === true) {
      detailParts.push("Audio input detected.");
    }
    return {
      id,
      toolKey: "runtime.provider-route",
      toolName: "Routing",
      title: usedAutoRouting ? "Provider route selected" : "Pinned provider confirmed",
      summary: usedAutoRouting
        ? `Auto-routed to ${resolvedProvider}${resolvedModel ? ` / ${resolvedModel}` : ""}`
        : `Using ${resolvedProvider}${resolvedModel ? ` / ${resolvedModel}` : ""}`,
      status: "success",
      statusLabel: usedAutoRouting ? "Routed" : "Pinned",
      detail: detailParts.join(" "),
      presentation: "card",
      createdAt,
    };
  }

  if (eventType === "assistant_final") {
    const status = firstString(data.status) ?? "unknown";
    const finishReason = firstString(data.finish_reason) ?? "completed";
    const finalText = firstString(data.final_text) ?? "";
    const transientText = firstString(data.transient_text) ?? "";
    const transcriptPersisted = Boolean(data.transcript_persisted);
    const normalizedFinal = finalText.trim();
    const normalizedTransient = transientText.trim();
    if (!normalizedFinal && !normalizedTransient) {
      return null;
    }
    if (normalizedFinal && normalizedTransient && normalizedFinal === normalizedTransient) {
      return null;
    }

    const detailParts = [];
    if (normalizedFinal) {
      detailParts.push(`Durable final answer: ${normalizedFinal}`);
    } else {
      detailParts.push("Durable final answer: none committed.");
    }
    if (normalizedTransient && normalizedTransient !== normalizedFinal) {
      detailParts.push(`Transient run output: ${normalizedTransient}`);
    }
    detailParts.push(`Finish reason: ${finishReason}.`);

    return {
      id,
      toolKey: "runtime.assistant-final",
      toolName: "Runtime",
      title: transcriptPersisted ? "Run completion summary" : "Run output withheld",
      summary: transcriptPersisted
        ? "Durable answer committed separately from transient run output"
        : "No durable answer was committed for this run",
      status: status === "blocked" ? "error" : "success",
      statusLabel: status === "blocked" ? "Blocked" : "Completed",
      detail: detailParts.join(" "),
      presentation: "card",
      createdAt,
    };
  }

  if (eventType === "provider_stream_timeout") {
    const elapsed = toNumber(data.elapsed_s);
    const threshold = toNumber(data.threshold_s);
    const lastEventType = firstString(data.last_event_type);
    const elapsedLabel = formatSeconds(elapsed);
    const thresholdLabel = formatSeconds(threshold);
    const detailParts = [
      elapsedLabel && thresholdLabel
        ? `No provider events arrived for ${elapsedLabel}s (threshold ${thresholdLabel}s).`
        : "No provider events arrived before the idle timeout threshold.",
    ];
    if (lastEventType) {
      detailParts.push(`Last provider event: ${lastEventType}.`);
    }
    return {
      id,
      toolKey: "runtime.timeout",
      toolName: "Runtime",
      title: "Provider stream stalled",
      summary: elapsedLabel ? `Provider stream timed out after ${elapsedLabel}s` : "Provider stream timed out",
      status: "error",
      statusLabel: "Timeout",
      detail: detailParts.join(" "),
      presentation: "card",
      recoveryPrompt: "Retry the task from the current session state and keep the next attempt narrowly scoped.",
      createdAt,
    };
  }

  if (eventType === "subprocess_killed") {
    const pid = toNumber(data.pid);
    const reason = firstString(data.reason) ?? "kill";
    const command = firstString(data.command);
    const sessionId = firstString(data.session_id);
    const summary = pid != null
      ? `Managed subprocess ${pid} terminated`
      : "Managed subprocess terminated";
    const detailParts = [`Reason: ${reason}.`];
    if (command) {
      detailParts.push(`Command: ${command}.`);
    }
    if (sessionId) {
      detailParts.push(`Process session: ${sessionId}.`);
    }
    return {
      id,
      toolKey: "runtime.process",
      toolName: "Runtime",
      title: "Managed subprocess interrupted",
      summary,
      status: "interrupted",
      statusLabel: "Interrupted",
      detail: detailParts.join(" "),
      presentation: "card",
      recoveryPrompt: "Inspect the process output first, then decide whether the subprocess should be restarted or left terminated.",
      createdAt,
    };
  }

  if (eventType === "tool_failure_pivot") {
    const toolKey = stringifyPreview(data.tool_name, 60) || "tool";
    const attemptCount = toNumber(data.attempt_count);
    const repeatedPattern = firstString(data.repeated_pattern);
    const pivotHint = firstString(data.pivot_hint);
    const detailParts = [];
    if (repeatedPattern) {
      detailParts.push(`Pattern: ${repeatedPattern}.`);
    }
    if (pivotHint) {
      detailParts.push(`Pivot: ${pivotHint}`);
    }
    return {
      id,
      toolKey: `${toolKey}.pivot`,
      toolName: prettifyToolName(toolKey),
      title: "Recovery pivot available",
      summary: attemptCount != null
        ? `Pivoting after repeated ${prettifyToolName(toolKey).toLowerCase()} failures (${attemptCount})`
        : `Pivoting after repeated ${prettifyToolName(toolKey).toLowerCase()} failures`,
      status: "warning",
      statusLabel: "Pivot",
      detail: detailParts.join(" "),
      presentation: "card",
      recoveryPrompt: pivotHint ?? undefined,
      createdAt,
    };
  }

  if (eventType === "action_progress_blocked") {
    const message = firstString(data.message) ?? "Action run exceeded the discovery budget without a concrete result.";
    const discoverySteps = toNumber(data.discovery_steps);
    const discoveryBudget = toNumber(data.discovery_budget);
    const filesRead = toNumber(data.files_read);
    const symbolsFound = toNumber(data.symbols_found);
    const filesModified = toNumber(data.files_modified);
    const testsRun = toNumber(data.tests_run);
    const artifactsCreated = toNumber(data.artifacts_created);
    const nextRequiredAction = firstString(data.next_required_action);
    const evidence = Array.isArray(data.evidence)
      ? data.evidence.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const detailParts = [
      `Progress ledger: discovery ${discoverySteps ?? 0}/${discoveryBudget ?? 0}, files read ${filesRead ?? 0}, symbols found ${symbolsFound ?? 0}, files modified ${filesModified ?? 0}, tests run ${testsRun ?? 0}, artifacts created ${artifactsCreated ?? 0}.`,
    ];
    if (evidence.length > 0) {
      detailParts.push(`Evidence: ${evidence.join("; ")}`);
    }
    return {
      id,
      toolKey: "runtime.action-progress-blocked",
      toolName: "Runtime",
      title: "Action run blocked",
      summary: message,
      status: "error",
      statusLabel: "Blocked",
      detail: detailParts.join(" "),
      presentation: "card",
      recoveryPrompt: nextRequiredAction ?? undefined,
      createdAt,
    };
  }

  if (eventType === "prompt_unanswered") {
    const reason = firstString(data.reason);
    const filesModified = toNumber(data.files_modified);
    const testsRun = toNumber(data.tests_run);
    const artifactsCreated = toNumber(data.artifacts_created);
    const detailParts = [];
    if (reason) {
      detailParts.push(`Reason: ${reason}.`);
    }
    detailParts.push(
      `Verifiable progress: files modified ${filesModified ?? 0}, tests run ${testsRun ?? 0}, artifacts created ${artifactsCreated ?? 0}.`,
    );
    return {
      id,
      toolKey: "runtime.prompt-unanswered",
      toolName: "Runtime",
      title: "Prompt not answered",
      summary: "The run ended without committing a final answer.",
      status: "error",
      statusLabel: "Blocked",
      detail: detailParts.join(" "),
      presentation: "card",
      createdAt,
    };
  }

  if (eventType === "repeated_intent_blocked") {
    const message = firstString(data.message) ?? "The run kept restating the same planned action without acting on it.";
    const intentSignature = firstString(data.intent_signature);
    const repeatCount = toNumber(data.repeat_count);
    const lastText = firstString(data.last_text);
    const nextRequiredAction = firstString(data.next_required_action);
    const detailParts = [];
    if (intentSignature) {
      detailParts.push(`Intent signature: ${intentSignature}.`);
    }
    if (repeatCount != null) {
      detailParts.push(`Repeated ${repeatCount} times.`);
    }
    if (lastText) {
      detailParts.push(`Last repeated text: ${lastText}`);
    }
    return {
      id,
      toolKey: "runtime.repeated-intent-blocked",
      toolName: "Runtime",
      title: "Repeated intent blocked",
      summary: message,
      status: "error",
      statusLabel: "Blocked",
      detail: detailParts.join(" "),
      presentation: "card",
      recoveryPrompt: nextRequiredAction ?? undefined,
      createdAt,
    };
  }

  return null;
}

type InteractiveExecSeed = {
  session_id: string;
  command: string;
  workdir: string | null;
  pid: number | null;
};

type FailurePivotSignal = {
  toolName: string;
  attemptCount: number | null;
  pivotHint: string | null;
};

function parseInteractiveExecSeed(value: unknown): InteractiveExecSeed | null {
  if (typeof value !== "string" || !/interactive process started\./i.test(value)) {
    return null;
  }

  const sessionId = value.match(/session_id:\s*([^\r\n]+)/i)?.[1]?.trim();
  if (!sessionId) {
    return null;
  }

  return {
    session_id: sessionId,
    command: value.match(/command:\s*([^\r\n]+)/i)?.[1]?.trim() || "",
    workdir: value.match(/workdir:\s*([^\r\n]+)/i)?.[1]?.trim() || null,
    pid: toNumber(value.match(/pid:\s*(\d+)/i)?.[1]) ?? null,
  };
}

function extractInteractiveExecSeedFromToolResult(data: Record<string, unknown>): InteractiveExecSeed | null {
  const toolKey = stringifyPreview(data.tool_name || data.name, 60) || "";
  if (toolKey !== "exec") {
    return null;
  }
  return parseInteractiveExecSeed(data.content ?? data.error);
}

function findLatestInteractiveExecSeed(events: SessionEventRecord[]): InteractiveExecSeed | null {
  for (const event of [...events].reverse()) {
    if (event.type !== "tool_result") {
      continue;
    }
    const seed = extractInteractiveExecSeedFromToolResult(event.data);
    if (seed) {
      return seed;
    }
  }
  return null;
}

const ERROR_WINDOW_AUTO_INJECT_DELAY_MS = 60_000;

function parseTimestampMs(value: unknown): number | null {
  if (typeof value !== "string" || !value.trim()) {
    return null;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function getTransientWindowPayload(windowRecord: TransientWindowRecord): Record<string, unknown> {
  return toRecord(windowRecord.payload) ?? {};
}

function getTransientWindowCreatedAt(windowRecord: TransientWindowRecord): string {
  const payload = getTransientWindowPayload(windowRecord);
  return firstString(payload.created_at, windowRecord.created_at) ?? windowRecord.created_at;
}

function getTransientWindowRecoveryPrompt(windowRecord: TransientWindowRecord): string | null {
  const payload = getTransientWindowPayload(windowRecord);
  const suggestedDirection = toRecord(payload.suggested_direction);
  return firstString(suggestedDirection?.recovery_prompt, suggestedDirection?.pivot_hint);
}

function getTransientWindowAutoRecoveryStatus(windowRecord: TransientWindowRecord): string | null {
  const payload = getTransientWindowPayload(windowRecord);
  const autoRecovery = toRecord(payload.auto_recovery);
  return firstString(autoRecovery?.status);
}

export function DesktopShell() {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [buildProjects, setBuildProjects] = useState<BuildProjectRecord[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null);
  const [activeBuildProjectId, setActiveBuildProjectId] = useState<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<SessionDetail | null>(null);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [streamingThinking, setStreamingThinking] = useState<string | null>(null);
  const [plan, setPlan] = useState<PlanRecord | null>(null);
  const [workspacePlans, setWorkspacePlans] = useState<PlanSummaryRecord[]>([]);
  const [toolSteps, setToolSteps] = useState<ToolStepView[]>([]);
  const [transientWindows, setTransientWindows] = useState<TransientWindowRecord[]>([]);
  const [workspaceCaptures, setWorkspaceCaptures] = useState<WorkspaceCaptureRecord[]>([]);
  const [workspaceEvidence, setWorkspaceEvidence] = useState<WorkspaceEvidenceRecord[]>([]);
  const [delegationTasks, setDelegationTasks] = useState<DelegationTaskRecord[]>([]);
  const [delegationSubstrates, setDelegationSubstrates] = useState<DelegationSubstrateRecord[]>([]);
  const [delegationPolicy, setDelegationPolicy] = useState<DelegationPolicyRecord | null>(null);
  const [cloQueue, setCloQueue] = useState<CloQueueStateRecord | null>(null);
  const [loadingWorkspaces, setLoadingWorkspaces] = useState(true);
  const [loadingActive, setLoadingActive] = useState(false);
  const [busySessionId, setBusySessionId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [creatingBuildProject, setCreatingBuildProject] = useState(false);
  const [creatingSession, setCreatingSession] = useState(false);
  const [pendingSteerText, setPendingSteerText] = useState<string | null>(null);
  const [interactiveProcess, setInteractiveProcess] = useState<InteractiveProcessRecord | null>(null);
  const [interactivePending, setInteractivePending] = useState(false);
  const [interactiveError, setInteractiveError] = useState<string | null>(null);
  const [runtimeChannel, setRuntimeChannel] = useState<RuntimeAgentRecord | null>(null);
  const [pokemonBridgeStatus, setPokemonBridgeStatus] = useState<PokemonBridgeStatusRecord | null>(null);
  const [pokemonControlPending, setPokemonControlPending] = useState(false);
  const activeSessionIdRef = useRef<string | null>(null);
  const activeSessionRef = useRef<SessionDetail | null>(null);
  const messagesRef = useRef<TranscriptMessage[]>([]);
  const transientWindowsRef = useRef<TransientWindowRecord[]>([]);
  const busySessionIdRef = useRef<string | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const failurePivotSignalsRef = useRef<Map<string, FailurePivotSignal>>(new Map());
  const autoRecoveryTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const autoRecoveryInFlightRef = useRef<Set<string>>(new Set());

  void loadingWorkspaces;
  void loadingActive;

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    activeSessionRef.current = activeSession;
  }, [activeSession]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    transientWindowsRef.current = transientWindows;
  }, [transientWindows]);

  useEffect(() => {
    busySessionIdRef.current = busySessionId;
  }, [busySessionId]);

  useEffect(() => {
    return () => {
      for (const timeoutId of autoRecoveryTimersRef.current.values()) {
        clearTimeout(timeoutId);
      }
      autoRecoveryTimersRef.current.clear();
      autoRecoveryInFlightRef.current.clear();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void listDelegationSubstrates()
      .then((response) => {
        if (!cancelled) {
          setDelegationSubstrates(response.substrates);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  async function loadChatSideContext(sessionId: string, workspaceId: string | null) {
    const [delegationRes, captureRes, evidenceRes] = await Promise.all([
      listDelegations(sessionId, { limit: 12 }),
      workspaceId
        ? listWorkspaceCaptures(workspaceId, { session_id: sessionId, limit: 10 })
        : Promise.resolve({ workspace_id: "", captures: [] }),
      workspaceId
        ? listWorkspaceEvidence(workspaceId, { session_id: sessionId, limit: 8 })
        : Promise.resolve({ workspace_id: "", evidence: [] }),
    ]);

    let combinedEvidence = evidenceRes.evidence;
    if (workspaceId && evidenceRes.evidence.length < 4) {
      const workspaceWide = await listWorkspaceEvidence(workspaceId, { limit: 12 });
      const seenIds = new Set(combinedEvidence.map((item) => item.id));
      const fallbackEvidence = workspaceWide.evidence.filter((item) => !seenIds.has(item.id));
      combinedEvidence = [...combinedEvidence, ...fallbackEvidence].slice(0, 10);
    }

    return {
      delegationTasks: delegationRes.tasks,
      workspaceCaptures: captureRes.captures,
      workspaceEvidence: combinedEvidence,
    };
  }

  function sessionHasPostErrorActivity(windowRecord: TransientWindowRecord): boolean {
    const createdAtMs = parseTimestampMs(getTransientWindowCreatedAt(windowRecord));
    if (createdAtMs == null) {
      return false;
    }

    const latestMessageAt = messagesRef.current.reduce((latest, message) => {
      const messageTime = parseTimestampMs(message.created_at) ?? 0;
      return Math.max(latest, messageTime);
    }, 0);
    if (latestMessageAt > createdAtMs) {
      return true;
    }

    if (busySessionIdRef.current === windowRecord.session_id) {
      return true;
    }

    const runStatus = activeSessionRef.current?.current_run?.status?.toLowerCase();
    return runStatus === "queued" || runStatus === "running";
  }

  async function updateWindowAutoRecovery(
    windowRecord: TransientWindowRecord,
    autoRecoveryPatch: Record<string, unknown>,
  ): Promise<void> {
    const payload = getTransientWindowPayload(windowRecord);
    const nextPayload = {
      ...payload,
      auto_recovery: {
        ...(toRecord(payload.auto_recovery) ?? {}),
        ...autoRecoveryPatch,
      },
    };
    const optimistic = {
      ...windowRecord,
      payload: nextPayload,
      updated_at: new Date().toISOString(),
    };

    setTransientWindows((current) =>
      current.map((candidate) => (candidate.id === windowRecord.id ? optimistic : candidate)),
    );

    if (windowRecord.id.startsWith("local_")) {
      return;
    }

    try {
      const updated = await patchTransientWindow(windowRecord.id, { payload: nextPayload });
      setTransientWindows((current) =>
        current.map((candidate) => (candidate.id === updated.id ? updated : candidate)),
      );
    } catch {
      // Keep the optimistic local state; failing to persist should not re-arm the timer.
    }
  }

  async function handleAutoInjectSuggestedDirection(windowId: string): Promise<void> {
    autoRecoveryTimersRef.current.delete(windowId);

    if (autoRecoveryInFlightRef.current.has(windowId)) {
      return;
    }

    const windowRecord = transientWindowsRef.current.find((candidate) => candidate.id === windowId);
    if (!windowRecord) {
      return;
    }

    const prompt = getTransientWindowRecoveryPrompt(windowRecord);
    if (!prompt) {
      return;
    }

    const currentStatus = getTransientWindowAutoRecoveryStatus(windowRecord);
    if (currentStatus === "cancelled" || currentStatus === "injected") {
      return;
    }

    if (sessionHasPostErrorActivity(windowRecord)) {
      await updateWindowAutoRecovery(windowRecord, {
        status: "cancelled",
        cancelled_at: new Date().toISOString(),
        cancel_reason: "session_activity",
      });
      return;
    }

    autoRecoveryInFlightRef.current.add(windowId);
    try {
      const sent = await handleSendMessage(prompt);
      if (!sent) {
        return;
      }

      const latestWindow = transientWindowsRef.current.find((candidate) => candidate.id === windowId) ?? windowRecord;
      await updateWindowAutoRecovery(latestWindow, {
        status: "injected",
        injected_at: new Date().toISOString(),
        trigger: "idle_timeout",
      });
    } finally {
      autoRecoveryInFlightRef.current.delete(windowId);
    }
  }

  useEffect(() => {
    if (!activeSessionId) {
      for (const timeoutId of autoRecoveryTimersRef.current.values()) {
        clearTimeout(timeoutId);
      }
      autoRecoveryTimersRef.current.clear();
      return;
    }

    const retainedWindowIds = new Set<string>();
    const nowMs = Date.now();

    for (const windowRecord of transientWindows) {
      if (windowRecord.session_id !== activeSessionId || windowRecord.native_type !== "error_window") {
        continue;
      }

      const prompt = getTransientWindowRecoveryPrompt(windowRecord);
      if (!prompt) {
        continue;
      }

      const status = getTransientWindowAutoRecoveryStatus(windowRecord);
      if (status === "cancelled" || status === "injected") {
        continue;
      }

      if (sessionHasPostErrorActivity(windowRecord) && !autoRecoveryInFlightRef.current.has(windowRecord.id)) {
        const existingTimer = autoRecoveryTimersRef.current.get(windowRecord.id);
        if (existingTimer) {
          clearTimeout(existingTimer);
          autoRecoveryTimersRef.current.delete(windowRecord.id);
        }

        void updateWindowAutoRecovery(windowRecord, {
          status: "cancelled",
          cancelled_at: new Date().toISOString(),
          cancel_reason: "session_activity",
        });
        continue;
      }

      retainedWindowIds.add(windowRecord.id);
      if (autoRecoveryTimersRef.current.has(windowRecord.id) || autoRecoveryInFlightRef.current.has(windowRecord.id)) {
        continue;
      }

      const createdAtMs = parseTimestampMs(getTransientWindowCreatedAt(windowRecord)) ?? nowMs;
      const remainingMs = Math.max(0, createdAtMs + ERROR_WINDOW_AUTO_INJECT_DELAY_MS - nowMs);
      const timeoutId = setTimeout(() => {
        void handleAutoInjectSuggestedDirection(windowRecord.id);
      }, remainingMs);
      autoRecoveryTimersRef.current.set(windowRecord.id, timeoutId);
    }

    for (const [windowId, timeoutId] of autoRecoveryTimersRef.current.entries()) {
      if (retainedWindowIds.has(windowId)) {
        continue;
      }
      clearTimeout(timeoutId);
      autoRecoveryTimersRef.current.delete(windowId);
    }
  }, [activeSessionId, activeSession?.current_run?.status, busySessionId, messages, transientWindows]);

  async function refreshInteractiveProcess(processSessionId: string, owningSessionId: string | null) {
    try {
      const process = await getInteractiveProcess(processSessionId);
      if (activeSessionIdRef.current !== owningSessionId) {
        return;
      }
      setInteractiveProcess(process);
      setInteractiveError(null);
    } catch (err) {
      if (activeSessionIdRef.current !== owningSessionId) {
        return;
      }
      const message = err instanceof Error ? err.message : "Failed to load interactive process";
      if (/process not found|404/i.test(message)) {
        setInteractiveProcess((current) => (current?.session_id === processSessionId ? null : current));
        setInteractiveError(null);
        return;
      }
      setInteractiveError(message);
    }
  }

  async function refreshCloQueue() {
    const state = await listCloQueue();
    setCloQueue(state);
    return state;
  }

  function upsertTransientWindow(windowRecord: TransientWindowRecord) {
    setTransientWindows((current) => {
      const existingIndex = current.findIndex((candidate) => candidate.id === windowRecord.id);
      if (existingIndex === -1) {
        return [...current, windowRecord];
      }
      return current.map((candidate, index) => (index === existingIndex ? windowRecord : candidate));
    });
  }

  async function showTransientErrorWindow(
    sessionId: string,
    options: {
      title: string;
      summary: string;
      category: string;
      severity?: "warning" | "error";
      runId?: string | null;
      userGoal?: string;
      rawError?: string;
    },
  ) {
    const now = new Date().toISOString();
    const title = options.title;
    const summary = options.summary;
    const failurePivot = getFailurePivotSignal(options.runId);
    const payload = {
      id: `err_${options.runId ?? sessionId}_${options.category}`,
      origin: "watchdog",
      artifact_type: "transient_error_window",
      severity: options.severity ?? "error",
      category: options.category,
      title,
      summary,
      created_at: now,
      session_id: sessionId,
      run_id: options.runId ?? null,
      session: {
        id: sessionId,
        label: activeSession?.label ?? sessionId,
      },
      model: {
        provider: activeSession?.provider ?? "unknown",
        name: activeSession?.model ?? "unknown",
      },
      transcript_excerpt: {
        user_goal: options.userGoal ?? "",
        assistant_intent: "",
        recent_events: [
          {
            label: options.category,
            value: options.rawError ?? summary,
          },
        ],
      },
      last_known_activity: {
        goal: options.userGoal ?? "",
        assistant_intent: "",
        last_successful_action: null,
        failed_action: {
          summary,
          error: options.rawError ?? summary,
        },
        files_touched: [],
        modified_before_failure: false,
      },
      suggested_direction: {
        summary: "Retry the task after re-checking the active session and the latest runtime state.",
        recovery_prompt: failurePivot?.pivotHint
          ?? "Retry the task. First inspect the target session, confirm the exact failure, then make one small targeted attempt.",
        pivot_summary: failurePivot
          ? `The loop already pivoted ${failurePivot.toolName} after repeated failures${failurePivot.attemptCount != null ? ` (${failurePivot.attemptCount})` : ""}.`
          : undefined,
        pivot_hint: failurePivot?.pivotHint ?? undefined,
      },
      raw: {
        error: options.rawError ?? summary,
        traceback: "",
        event_tail: [],
      },
      actions: ["copy_recovery_prompt"],
    };

    try {
      const created = await createTransientWindow(sessionId, {
        title,
        summary,
        source_type: "native",
        native_type: "error_window",
        payload,
      });
      if (activeSessionIdRef.current === sessionId) {
        upsertTransientWindow(created);
      }
      return;
    } catch {
      if (activeSessionIdRef.current !== sessionId) {
        return;
      }

      upsertTransientWindow({
        id: `local_${payload.id}`,
        session_id: sessionId,
        title,
        source_type: "native",
        native_type: "error_window",
        html: null,
        payload,
        capabilities: { network: false, toolBridge: false, storage: false, media: false },
        state_flags: { pinned: false, minimized: false, stale: false, saved: false },
        mutable: false,
        version: 1,
        summary,
        created_at: now,
        updated_at: now,
      });
    }
  }

  function getPreferredProvider(providerId?: string | null): ProviderRecord | null {
    if (providerId) {
      const explicit = providers.find((provider) => provider.id === providerId);
      if (explicit) {
        return explicit;
      }
    }

    return providers.find((provider) => provider.enabled) ?? providers[0] ?? null;
  }

  function upsertLocalTransientWindow(windowRecord: TransientWindowRecord) {
    setTransientWindows((current) => {
      const existingIndex = current.findIndex((item) => item.id === windowRecord.id);
      if (existingIndex === -1) {
        return [...current, windowRecord];
      }
      return current.map((item, index) => (index === existingIndex ? windowRecord : item));
    });
  }

  function getFailurePivotSignal(runId?: string | null): FailurePivotSignal | null {
    if (!runId) {
      return null;
    }
    return failurePivotSignalsRef.current.get(runId) ?? null;
  }

  async function patchErrorWindowWithPivot(windowRecord: TransientWindowRecord, failurePivot: FailurePivotSignal) {
    const payload = getTransientWindowPayload(windowRecord);
    const suggestedDirection = toRecord(payload.suggested_direction) ?? {};
    const nextPayload = {
      ...payload,
      suggested_direction: {
        ...suggestedDirection,
        recovery_prompt: failurePivot.pivotHint ?? firstString(suggestedDirection.recovery_prompt),
        pivot_summary: `The loop already pivoted ${failurePivot.toolName} after repeated failures${failurePivot.attemptCount != null ? ` (${failurePivot.attemptCount})` : ""}.`,
        pivot_hint: failurePivot.pivotHint,
      },
    };
    const optimistic = {
      ...windowRecord,
      payload: nextPayload,
      updated_at: new Date().toISOString(),
    };

    setTransientWindows((current) => current.map((candidate) => (candidate.id === windowRecord.id ? optimistic : candidate)));

    if (windowRecord.id.startsWith("local_")) {
      return;
    }

    try {
      const updated = await patchTransientWindow(windowRecord.id, { payload: nextPayload });
      setTransientWindows((current) => current.map((candidate) => (candidate.id === updated.id ? updated : candidate)));
    } catch {
      // Keep the optimistic payload in local state if the patch request fails.
    }
  }

  async function showTransientErrorWindow(
    sessionId: string,
    options: {
      title: string;
      summary: string;
      category: string;
      severity?: "warning" | "error";
      runId?: string | null;
      userGoal?: string;
      rawError?: string;
    },
  ) {
    const now = new Date().toISOString();
    const title = options.title;
    const summary = options.summary;
    const payload = {
      id: `err_${options.runId ?? sessionId}_${options.category}`,
      origin: "watchdog",
      artifact_type: "transient_error_window",
      severity: options.severity ?? "warning",
      category: options.category,
      title,
      summary,
      created_at: now,
      session_id: sessionId,
      run_id: options.runId ?? null,
      session: {
        id: sessionId,
        label: activeSession?.label ?? sessionId,
      },
      model: {
        provider: activeSession?.provider ?? "unknown",
        name: activeSession?.model ?? "unknown",
      },
      transcript_excerpt: {
        user_goal: options.userGoal ?? "",
        assistant_intent: "",
        recent_events: [
          {
            label: options.category,
            value: options.rawError ?? summary,
          },
        ],
      },
      suggested_direction: {
        summary: "Retry the task after re-checking the active session and the latest runtime state.",
        recovery_prompt: "Retry the task. First inspect the target session, confirm the exact failure, then make one small targeted attempt.",
      },
      raw: {
        error: options.rawError ?? summary,
        traceback: "",
        event_tail: [],
      },
      actions: ["copy_recovery_prompt"],
    };

    try {
      const created = await createTransientWindow(sessionId, {
        title,
        summary,
        source_type: "native",
        native_type: "error_window",
        payload,
      });
      if (activeSessionIdRef.current === sessionId) {
        upsertLocalTransientWindow(created);
      }
      return;
    } catch {
      if (activeSessionIdRef.current !== sessionId) {
        return;
      }

      upsertLocalTransientWindow({
        id: `local_${payload.id}`,
        session_id: sessionId,
        title,
        source_type: "native",
        native_type: "error_window",
        html: null,
        payload,
        capabilities: { network: false, toolBridge: false, storage: false, media: false },
        state_flags: { pinned: false, minimized: false, stale: false, saved: false },
        mutable: false,
        version: 1,
        summary,
        created_at: now,
        updated_at: now,
      });
    }
  }

  async function refreshSessions(selectedSessionId?: string | null) {
    const sessionResponse = await listSessions();
    setSessions(sessionResponse.sessions);

    if (selectedSessionId) {
      setActiveSessionId(selectedSessionId);
    }
  }

  useEffect(() => {
    let cancelled = false;

    setLoadingWorkspaces(true);
    Promise.all([listWorkspaces(), listSessions(), listProviders(), listCloQueue()])
      .then(([wsRes, sessRes, provRes, queueRes]) => {
        if (cancelled) {
          return;
        }

        setWorkspaces(wsRes.workspaces);
        setSessions(sessRes.sessions);
        setProviders(provRes.providers);
        setCloQueue(queueRes);

        if (wsRes.workspaces.length > 0) {
          setActiveWorkspaceId(wsRes.workspaces[0].id);
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }

        setErrorMessage(err instanceof Error ? err.message : "Failed to load data");
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingWorkspaces(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const source = streamCloQueue({
      onState: (queueState) => {
        setCloQueue(queueState);
      },
      onTransportError: () => {
        void refreshCloQueue().catch(() => undefined);
      },
    });

    return () => {
      source.close();
    };
  }, []);

  useEffect(() => {
    if (!activeWorkspaceId) {
      setBuildProjects([]);
      setActiveBuildProjectId(null);
      return;
    }

    let cancelled = false;

    setActiveBuildProjectId(null);
    listBuildProjects(activeWorkspaceId)
      .then((res) => {
        if (!cancelled) {
          setBuildProjects(res.build_projects);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBuildProjects([]);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeWorkspaceId]);

  useEffect(() => {
    if (!activeWorkspaceId) {
      setWorkspacePlans([]);
      return;
    }

    let cancelled = false;

    listWorkspacePlans(activeWorkspaceId)
      .then((res) => {
        if (!cancelled) {
          setWorkspacePlans(res.plans);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setWorkspacePlans([]);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeWorkspaceId]);

  useEffect(() => {
    if (!activeWorkspaceId || !activeSessionId) {
      return;
    }

    const matchingSessions = sessions.filter((session) => {
      if (session.workspace_id !== activeWorkspaceId) {
        return false;
      }

      if (activeBuildProjectId && session.build_project_id !== activeBuildProjectId) {
        return false;
      }

      return true;
    });

    const currentMatch = matchingSessions.find((session) => session.id === activeSessionId);

    if (currentMatch) {
      if (currentMatch.build_project_id !== activeBuildProjectId) {
        setActiveBuildProjectId(currentMatch.build_project_id);
      }
      return;
    }

    const fallback = matchingSessions[0] ?? null;

    if (!fallback) {
      setActiveSessionId(null);
      setActiveBuildProjectId(null);
      return;
    }

    setActiveSessionId(fallback.id);
    setActiveBuildProjectId(fallback.build_project_id);
  }, [sessions, activeWorkspaceId, activeBuildProjectId, activeSessionId]);

  useEffect(() => {
    if (!activeSessionId) {
      setActiveSession(null);
      setMessages([]);
      setStreamingThinking(null);
      setPlan(null);
      setToolSteps([]);
      setTransientWindows([]);
      setWorkspaceCaptures([]);
      setWorkspaceEvidence([]);
      setDelegationTasks([]);
      setDelegationPolicy(null);
      setInteractiveProcess(null);
      setInteractiveError(null);
      setInteractivePending(false);
      setRuntimeChannel(null);
      setPokemonBridgeStatus(null);
      setLoadingActive(false);
      return;
    }

    let cancelled = false;

    setLoadingActive(true);
    setErrorMessage(null);
    setStreamingThinking(null);

    Promise.all([
      getSession(activeSessionId),
      getMessages(activeSessionId),
      getPlan(activeSessionId),
      getSessionEvents(activeSessionId),
      listTransientWindows(activeSessionId),
      getSessionDelegationPolicy(activeSessionId),
      listRuntimeAgents({ session_id: activeSessionId }),
    ])
      .then(async ([sess, msgsRes, planRes, eventRes, windowsRes, delegationPolicyRes, runtimeAgentsRes]) => {
        if (cancelled) {
          return;
        }

        const chatSideContext = await loadChatSideContext(activeSessionId, sess.workspace_id);
        const matchedRuntimeChannel = runtimeAgentsRes.channels[0] ?? null;
        let bridgeStatus: PokemonBridgeStatusRecord | null = null;
        if (matchedRuntimeChannel?.domain === "pokemon") {
          try {
            bridgeStatus = await getPokemonBridgeStatus(matchedRuntimeChannel.name);
          } catch {
            bridgeStatus = null;
          }
        }
        if (cancelled) {
          return;
        }

        const nextToolSteps = buildToolStepsFromEvents(eventRes.events);
        const interactiveSeed = findLatestInteractiveExecSeed(eventRes.events);

        setActiveSession(sess);
        setMessages(msgsRes.messages);
        setPlan(planRes);
        setToolSteps(nextToolSteps);
        setTransientWindows(windowsRes);
        setWorkspaceCaptures(chatSideContext.workspaceCaptures);
        setWorkspaceEvidence(chatSideContext.workspaceEvidence);
        setDelegationTasks(chatSideContext.delegationTasks);
        setDelegationPolicy(delegationPolicyRes.delegation_policy);
        setRuntimeChannel(matchedRuntimeChannel);
        setPokemonBridgeStatus(bridgeStatus);
        setActiveBuildProjectId(sess.build_project_id);
        setActiveWorkspaceId(sess.workspace_id);

        if (interactiveSeed) {
          void refreshInteractiveProcess(interactiveSeed.session_id, activeSessionId);
        } else {
          setInteractiveProcess(null);
          setInteractiveError(null);
        }
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }

        setErrorMessage(err instanceof Error ? err.message : "Failed to load session");
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingActive(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (!activeSessionId) {
      return;
    }

    const hasLiveDelegation = delegationTasks.some((task) => task.status === "queued" || task.status === "running");
    if (!hasLiveDelegation) {
      return;
    }

    let cancelled = false;
    const viewedSessionId = activeSessionId;
    const intervalId = window.setInterval(() => {
      void listDelegations(viewedSessionId, { limit: 12 })
        .then((response) => {
          if (!cancelled && activeSessionIdRef.current === viewedSessionId) {
            setDelegationTasks(response.tasks);
          }
        })
        .catch(() => undefined);
    }, 1800);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeSessionId, delegationTasks]);

  useEffect(() => {
    if (!activeSessionId || !interactiveProcess || interactiveProcess.status !== "running") {
      return;
    }

    let cancelled = false;
    const viewedSessionId = activeSessionId;
    const processSessionId = interactiveProcess.session_id;
    const intervalId = window.setInterval(() => {
      void getInteractiveProcess(processSessionId)
        .then((process) => {
          if (cancelled || activeSessionIdRef.current !== viewedSessionId) {
            return;
          }
          setInteractiveProcess(process);
          setInteractiveError(null);
        })
        .catch((err) => {
          if (cancelled || activeSessionIdRef.current !== viewedSessionId) {
            return;
          }
          const message = err instanceof Error ? err.message : "Failed to refresh interactive process";
          if (/process not found|404/i.test(message)) {
            setInteractiveProcess((current) => (current?.session_id === processSessionId ? null : current));
            setInteractiveError(null);
            return;
          }
          setInteractiveError(message);
        });
    }, 900);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeSessionId, interactiveProcess?.session_id, interactiveProcess?.status]);

  useEffect(() => {
    if (!activeSessionId || !busySessionId || activeSessionId !== busySessionId) {
      return;
    }

    let cancelled = false;
    const intervalId = window.setInterval(() => {
      void listTransientWindows(activeSessionId)
        .then((windows) => {
          if (!cancelled) {
            setTransientWindows(windows);
          }
        })
        .catch(() => undefined);
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeSessionId, busySessionId]);

  function handleSelectSession(sessionId: string) {
    const selectedSession = sessions.find((session) => session.id === sessionId);

    if (selectedSession?.workspace_id) {
      setActiveWorkspaceId(selectedSession.workspace_id);
    }

    setActiveBuildProjectId(selectedSession?.build_project_id ?? null);
    setActiveSessionId(sessionId);
  }

  async function handleOpenPlan(planSummary: PlanSummaryRecord) {
    const targetSessionId = planSummary.session_id;
    const selectedSession = sessions.find((session) => session.id === targetSessionId);

    setErrorMessage(null);

    try {
      const activatedPlan = await activatePlan(targetSessionId, planSummary.id);
      setPlan(activatedPlan);
      if (selectedSession?.workspace_id) {
        setActiveWorkspaceId(selectedSession.workspace_id);
      }
      setActiveBuildProjectId(selectedSession?.build_project_id ?? planSummary.build_project_id ?? null);
      setActiveSessionId(targetSessionId);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to open plan");
      throw err;
    }
  }

  async function handleCreateWorkspace(name: string, description: string) {
    setCreatingWorkspace(true);
    setErrorMessage(null);

    try {
      const created = await createWorkspace({ name, description });
      const workspaceResponse = await listWorkspaces();
      setWorkspaces(workspaceResponse.workspaces);
      setActiveWorkspaceId(created.id);
      setActiveBuildProjectId(null);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to create workspace");
      throw err;
    } finally {
      setCreatingWorkspace(false);
    }
  }

  async function handleCreateBuildProject(name: string, description: string) {
    if (!activeWorkspaceId) {
      setErrorMessage("Select a workspace before creating a project");
      return;
    }

    setCreatingBuildProject(true);
    setErrorMessage(null);

    try {
      const created = await createBuildProject(activeWorkspaceId, { name, description });
      const projectResponse = await listBuildProjects(activeWorkspaceId);
      setBuildProjects(projectResponse.build_projects);
      setActiveBuildProjectId(created.id);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to create project");
      throw err;
    } finally {
      setCreatingBuildProject(false);
    }
  }

  async function handleCreateSession(
    label: string,
    buildProjectId: string | null,
    providerId?: string,
    model?: string,
  ) {
    if (!activeWorkspaceId) {
      setErrorMessage("Select a workspace before creating a session");
      return;
    }

    const provider = getPreferredProvider(providerId);

    if (!provider) {
      setErrorMessage("No providers are configured");
      return;
    }

    const resolvedModel = firstString(model, provider.model_name);
    if (!resolvedModel) {
      setErrorMessage("Choose a model before creating a session");
      return;
    }

    setCreatingSession(true);
    setErrorMessage(null);

    try {
      const created = await createSession({
        label,
        model: resolvedModel,
        provider: provider.id,
        workspace_id: activeWorkspaceId,
        build_project_id: buildProjectId,
      });

      await refreshSessions(created.id);
      setActiveWorkspaceId(created.workspace_id);
      setActiveBuildProjectId(created.build_project_id);
      setActiveSessionId(created.id);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to create session");
      throw err;
    } finally {
      setCreatingSession(false);
    }
  }

  async function handleQueueMessage(sessionId: string, content: string, stopAfterError?: boolean) {
    try {
      await createCloQueueItem({ session_id: sessionId, content, stop_after_error: stopAfterError });
      await refreshCloQueue();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to queue message");
      throw err;
    }
  }

  async function handleMoveQueueItem(itemId: string, direction: "up" | "down") {
    try {
      await moveCloQueueItem(itemId, direction);
      await refreshCloQueue();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to reorder queue item");
    }
  }

  async function handleCancelQueueItem(itemId: string) {
    try {
      await cancelCloQueueItem(itemId);
      await refreshCloQueue();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to cancel queue item");
    }
  }

  async function handleUpdateQueueSettings(settings: { paused?: boolean; pause_on_error?: boolean }) {
    try {
      await patchCloQueueSettings(settings);
      await refreshCloQueue();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to update queue settings");
    }
  }

  async function handleToggleWindowPin(windowId: string, pinned: boolean) {
    if (windowId.startsWith("local_")) {
      setTransientWindows((current) =>
        current.map((windowRecord) =>
          windowRecord.id === windowId
            ? {
                ...windowRecord,
                state_flags: { ...windowRecord.state_flags, pinned },
                updated_at: new Date().toISOString(),
              }
            : windowRecord,
        ),
      );
      return;
    }

    try {
      const updated = await patchTransientWindow(windowId, {
        state_flags: { pinned },
      });
      setTransientWindows((current) =>
        current.map((windowRecord) => (windowRecord.id === updated.id ? updated : windowRecord)),
      );
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to update window");
    }
  }

  async function handleCloseWindow(windowId: string) {
    if (windowId.startsWith("local_")) {
      setTransientWindows((current) => current.filter((windowRecord) => windowRecord.id !== windowId));
      return;
    }

    try {
      await closeTransientWindow(windowId);
      setTransientWindows((current) => current.filter((windowRecord) => windowRecord.id !== windowId));
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to close window");
    }
  }

  async function handleSendMessage(
    text: string,
    runtime?: {
      providerId?: string;
      model?: string;
      files?: File[];
      attachments?: Array<Record<string, unknown>>;
      captureIds?: string[];
      metadata?: Record<string, unknown>;
    },
  ): Promise<boolean> {
    if (!activeSessionId) return false;

    const sessionId = activeSessionId;
    const provider = getPreferredProvider(runtime?.providerId || activeSession?.provider);

    if (!provider) {
      setErrorMessage("No providers are configured");
      return false;
    }

    const runtimeModel = firstString(runtime?.model, activeSession?.model, provider.model_name);
    if (!runtimeModel) {
      setErrorMessage("Choose a model before sending a message");
      return false;
    }

    setBusySessionId(sessionId);
    setErrorMessage(null);

    let submitRunId: string | null = null;
    let surfacedErrorWindow = false;

    const presentErrorWindow = async (options: {
      title: string;
      summary: string;
      category: string;
      severity?: "warning" | "error";
      runId?: string | null;
      userGoal?: string;
      rawError?: string;
    }) => {
      surfacedErrorWindow = true;
      await showTransientErrorWindow(sessionId, {
        ...options,
        runId: options.runId ?? submitRunId,
      });
    };

    try {
      if (activeSession && (activeSession.provider !== provider.id || activeSession.model !== runtimeModel)) {
        const patched = await patchSession(sessionId, {
          provider: provider.id,
          model: runtimeModel,
        });

        setActiveSession(patched);
        setSessions((current) =>
          current.map((session) =>
            session.id === patched.id
              ? {
                  ...session,
                  label: patched.label,
                  model: patched.model,
                  provider: patched.provider,
                  status: patched.status,
                  workspace_id: patched.workspace_id,
                  build_project_id: patched.build_project_id,
                }
              : session,
          ),
        );
      }

      let uploadedAttachments = runtime?.attachments ?? [];
      let uploadedCaptureIds = runtime?.captureIds ?? [];
      let uploadMetadata = runtime?.metadata ?? undefined;

      if (runtime?.files && runtime.files.length > 0) {
        const uploadRes = await uploadSessionAttachments(sessionId, runtime.files);
        uploadedAttachments = [...uploadedAttachments, ...uploadRes.attachments];
        uploadedCaptureIds = [...new Set([...uploadedCaptureIds, ...uploadRes.capture_ids])];
        uploadMetadata = {
          ...(uploadMetadata ?? {}),
          uploaded_attachment_count: uploadRes.attachments.length,
        };
      }

      const submitRes = await submitMessage(sessionId, text, "user", {
        attachments: uploadedAttachments,
        captureIds: uploadedCaptureIds,
        metadata: uploadMetadata,
      });
      const submittedRunId = firstString(submitRes.run_id);
      if (!submittedRunId) {
        throw new Error("Submit message response omitted run_id");
      }
      const submittedMessageId = firstString(submitRes.message_id) ?? `local-user-${Date.now()}`;
      const submittedContent = firstString(submitRes.content, text) ?? text;
      const submittedPosition = toNumber(submitRes.position) ?? messages.length;

      submitRunId = submittedRunId;
      activeRunIdRef.current = submittedRunId;
      const provisionalAssistantMessageId = `stream-${submittedRunId}`;
      setStreamingThinking(null);

      setMessages((current) => {
        if (current.some((message) => message.id === submittedMessageId)) {
          return current;
        }

        return [
          ...current,
          {
            id: submittedMessageId,
            role: "user",
            content: submittedContent,
            position: submittedPosition,
            token_estimate: Math.max(1, Math.ceil(submittedContent.length / 4)),
            created_at: new Date().toISOString(),
            archive_ready: false,
            archive_state: null,
          },
        ];
      });

      let streamProblemMessage: string | null = null;
      let lastToolUseData: Record<string, unknown> | null = null;
      let lastToolResultData: Record<string, unknown> | null = null;

      const streamingFinished = new Promise<void>((resolve) => {
        let settled = false;
        let terminalStreamEventReceived = false;
        const isViewingRunSession = () => activeSessionIdRef.current === sessionId;

        const finish = () => {
          if (!settled) {
            settled = true;
            resolve();
          }
        };


          const markInterrupted = () => {
            if (!isViewingRunSession()) {
              return;
            }

            setToolSteps((current) =>
              current.map((step) =>
                step.status === "running"
                  ? { ...step, status: "interrupted", statusLabel: "Interrupted", summary: "Interrupted" }
                  : step,
              ),
            );
          };
        const eventSource = streamRun(sessionId, submittedRunId, {
          onTextDelta: (deltaText) => {
            if (!deltaText || !isViewingRunSession()) {
              return;
            }

            setMessages((current) => {
              const existing = current.find((message) => message.id === provisionalAssistantMessageId);
              if (!existing) {
                return [
                  ...current,
                  {
                    id: provisionalAssistantMessageId,
                    role: "assistant",
                    content: deltaText,
                    position: submittedPosition + 1,
                    token_estimate: Math.max(1, Math.ceil(deltaText.length / 4)),
                    created_at: new Date().toISOString(),
                    archive_ready: false,
                    archive_state: null,
                  },
                ];
              }

              const nextContent = `${existing.content}${deltaText}`;
              return current.map((message) =>
                message.id === provisionalAssistantMessageId
                  ? {
                      ...message,
                      content: nextContent,
                      token_estimate: Math.max(1, Math.ceil(nextContent.length / 4)),
                    }
                  : message,
              );
            });
          },
          onThinkingDelta: (deltaText) => {
            if (!deltaText || !isViewingRunSession()) {
              return;
            }

            setStreamingThinking((current) => `${current ?? ""}${deltaText}`);
          },
          onToolUse: ({ data }) => {
            lastToolUseData = data;
            if (!isViewingRunSession()) {
              return;
            }
            const toolKey = stringifyPreview(data.tool_name || data.name, 60) || "tool";
            const detail = stringifyPreview(data.input || data.arguments || data.args, 800);
            setToolSteps((current) => [
              ...current,
              {
                id: `${submittedRunId}-${current.length}`,
                toolKey,
                toolName: prettifyToolName(toolKey),
                summary: summarizeToolInput(toolKey, data.input || data.arguments || data.args),
                statusLabel: "Running",
                detail,
                status: "running",
                createdAt: new Date().toISOString(),
              },
            ]);
          },
          onToolResult: ({ data }) => {
            lastToolResultData = data;
            if (!isViewingRunSession()) {
              return;
            }
            const toolKey = stringifyPreview(data.tool_name || data.name, 60) || "tool";
            const outcome = deriveToolOutcome(toolKey, data.status, data.content, data.error);
            const interactiveSeed = extractInteractiveExecSeedFromToolResult(data);

            setToolSteps((current) => {
              const index = [...current]
                .map((step, stepIndex) => ({ step, stepIndex }))
                .reverse()
                .find(({ step }) => step.toolKey === toolKey && step.status === "running")?.stepIndex;

              if (index == null) {
                return [
                  ...current,
                  {
                    id: `${submittedRunId}-result-${current.length}`,
                    toolKey,
                    toolName: prettifyToolName(toolKey),
                    summary: outcome.summary,
                    statusLabel: outcome.statusLabel,
                    detail: outcome.detail,
                    status: outcome.status,
                    createdAt: new Date().toISOString(),
                  },
                ];
              }

              return current.map((step, stepIndex) =>
                stepIndex === index
                  ? {
                      ...step,
                      status: outcome.status,
                      statusLabel: outcome.statusLabel,
                      summary: outcome.summary,
                      detail: outcome.detail || step.detail,
                    }
                  : step,
              );
            });

            void listTransientWindows(sessionId)
              .then((windows) => setTransientWindows(windows))
              .catch(() => undefined);

            if (interactiveSeed) {
              void refreshInteractiveProcess(interactiveSeed.session_id, sessionId);
            }
          },
          onRuntimeEvent: ({ type, data }) => {
            if (!isViewingRunSession()) {
              return;
            }

            if (type === "tool_failure_pivot") {
              const failurePivot: FailurePivotSignal = {
                toolName: stringifyPreview(data.tool_name, 60) || "tool",
                attemptCount: toNumber(data.attempt_count),
                pivotHint: firstString(data.pivot_hint),
              };
              failurePivotSignalsRef.current.set(submittedRunId, failurePivot);

              const matchingWindow = transientWindowsRef.current.find((candidate) => {
                if (candidate.session_id !== sessionId || candidate.native_type !== "error_window") {
                  return false;
                }
                const payload = getTransientWindowPayload(candidate);
                return firstString(payload.run_id) === submittedRunId;
              });
              if (matchingWindow) {
                void patchErrorWindowWithPivot(matchingWindow, failurePivot);
              }
            }

            setToolSteps((current) => {
              const step = buildRuntimeStepFromEvent(
                type,
                data,
                new Date().toISOString(),
                `${submittedRunId}-${type}-${current.length}`,
              );
              if (!step) {
                return current;
              }
              return [...current, step];
            });

            if (type === "subprocess_killed") {
              const processSessionId = firstString(data.session_id);
              if (processSessionId) {
                void refreshInteractiveProcess(processSessionId, sessionId);
              }
            }
          },
          onDone: () => {
            terminalStreamEventReceived = true;
            setStreamingThinking(null);
            eventSource.close();
            finish();
          },
          onInterrupted: () => {
            terminalStreamEventReceived = true;
            setStreamingThinking(null);
            markInterrupted();
            eventSource.close();
            finish();
          },
          onStreamError: (message) => {
            streamProblemMessage = message;
            setStreamingThinking(null);
            setErrorMessage(message);
            eventSource.close();
            finish();
          },
          onTransportError: () => {
            if (!terminalStreamEventReceived) {
              streamProblemMessage = "The live run stream disconnected before a terminal event arrived.";
            }
            setStreamingThinking(null);
            eventSource.close();
            finish();
          },
        });
      });

      const executeRes = await executeRun(sessionId, submittedRunId);
      await streamingFinished;

      const finalText = firstString(executeRes.final_text) ?? "";
      const transientText = firstString(executeRes.transient_text) ?? "";
      if (activeSessionIdRef.current === sessionId) {
        const summaryStep = buildRuntimeStepFromEvent(
          "assistant_final",
          {
            status: executeRes.status,
            finish_reason: executeRes.finish_reason,
            final_text: finalText,
            transient_text: transientText,
            transcript_persisted: Boolean(finalText),
          },
          new Date().toISOString(),
          `${submittedRunId}-assistant-final-local`,
        );
        if (summaryStep) {
          setToolSteps((current) => [
            ...current.filter((step) => step.id !== summaryStep.id),
            summaryStep,
          ]);
        }
      }

      if (executeRes.status === "failed") {
        const failureSummary = executeRes.error || executeRes.finish_reason || "Run failed before completion.";
        const windowsForRun = await listTransientWindows(sessionId);
        const backendWindowExists = windowsForRun.some((window) => {
          const payload = window.payload as { run_id?: string | null; origin?: string | null } | null;
          return window.native_type === "error_window"
            && payload?.run_id === submittedRunId
            && payload?.origin === "watchdog";
        });

        if (activeSessionIdRef.current === sessionId) {
          setTransientWindows(windowsForRun);
        }

        if (!backendWindowExists) {
          await presentErrorWindow({
            title: /session not found/i.test(failureSummary) ? "Session unavailable" : "Run failed",
            summary: failureSummary,
            category: /session not found/i.test(failureSummary) ? "session_error" : "run_error",
            severity: "error",
            runId: submittedRunId,
            userGoal: text,
            rawError: failureSummary,
          });
        }
      }

      if (streamProblemMessage && executeRes.status === "succeeded") {
        const failedToolName = firstString(lastToolResultData?.tool_name, lastToolUseData?.tool_name);
        const summary = failedToolName
          ? `The live stream disconnected while Clo was working with ${failedToolName}. The backend may have continued, but the active chat lost trustworthy live state.`
          : "The live stream disconnected while Clo was running, so the chat lost trustworthy live state before the run finished.";
        await presentErrorWindow({
          title: "Run stream disconnected",
          summary,
          category: "stream_error",
          severity: "warning",
          runId: submittedRunId,
          userGoal: text,
          rawError: streamProblemMessage,
        });
      }

      const [msgsAfter, planAfter, sessAfter, eventRes, windowsAfter] = await Promise.all([
        getMessages(sessionId),
        getPlan(sessionId),
        getSession(sessionId),
        getSessionEvents(sessionId),
        listTransientWindows(sessionId),
      ]);
      const chatSideContext = await loadChatSideContext(sessionId, sessAfter.workspace_id);
      if (activeSessionIdRef.current === sessionId) {
        const interactiveSeed = findLatestInteractiveExecSeed(eventRes.events);
        setMessages(msgsAfter.messages);
        setStreamingThinking(null);
        setPlan(planAfter);
        setActiveSession(sessAfter);
        setToolSteps(buildToolStepsFromEvents(eventRes.events));
        setTransientWindows(windowsAfter);
        setWorkspaceCaptures(chatSideContext.workspaceCaptures);
        setWorkspaceEvidence(chatSideContext.workspaceEvidence);
        setDelegationTasks(chatSideContext.delegationTasks);
        if (interactiveSeed) {
          void refreshInteractiveProcess(interactiveSeed.session_id, sessionId);
        } else {
          setInteractiveProcess(null);
          setInteractiveError(null);
        }
      }
      await refreshSessions(sessionId);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to send message";
      setErrorMessage(message);
      if (!surfacedErrorWindow) {
        await presentErrorWindow({
          title: /session not found/i.test(message) ? "Session unavailable" : "Run update failed",
          summary: message,
          category: /session not found/i.test(message) ? "session_error" : "run_error",
          severity: "error",
          userGoal: text,
          rawError: message,
        });
      }
      return false;
    } finally {
      activeRunIdRef.current = null;
      setBusySessionId(null);
    }
  }

  async function handleInterrupt() {
    if (!activeSessionId || !activeRunIdRef.current) return;
    try {
      await interruptRun(activeSessionId, activeRunIdRef.current);
    } catch {
      // ignore — stream will resolve via timeout or onInterrupted event
    }
  }

  function handleSteer(text: string) {
    setPendingSteerText(text);
    void handleInterrupt();
  }

  async function handlePokemonControlMode(mode: PokemonControlMode) {
    if (!runtimeChannel || runtimeChannel.domain !== "pokemon") {
      return;
    }
    setPokemonControlPending(true);
    try {
      const nextStatus = await patchPokemonControl(runtimeChannel.name, {
        mode,
        advance_steps: mode === "step" ? 1 : 0,
      });
      if (activeSessionIdRef.current === runtimeChannel.session_id) {
        setPokemonBridgeStatus(nextStatus);
      }
      if (mode === "pause") {
        await handleInterrupt();
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to update Pokemon runtime mode");
    } finally {
      if (activeSessionIdRef.current === runtimeChannel.session_id) {
        setPokemonControlPending(false);
      }
    }
  }

  async function handleSendInteractiveInput(text: string) {
    if (!interactiveProcess) {
      return;
    }

    const viewedSessionId = activeSessionIdRef.current;
    setInteractivePending(true);
    setInteractiveError(null);
    try {
      const response = await sendInteractiveProcessInput(interactiveProcess.session_id, {
        data: text,
        submit: true,
      });
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractiveProcess(response.process);
      }
    } catch (err) {
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractiveError(err instanceof Error ? err.message : "Failed to send interactive input");
      }
    } finally {
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractivePending(false);
      }
    }
  }

  async function handleSendInteractiveKey(key: string) {
    if (!interactiveProcess) {
      return;
    }

    const viewedSessionId = activeSessionIdRef.current;
    setInteractivePending(true);
    setInteractiveError(null);
    try {
      const response = await sendInteractiveProcessInput(interactiveProcess.session_id, { keys: key });
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractiveProcess(response.process);
      }
    } catch (err) {
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractiveError(err instanceof Error ? err.message : `Failed to send ${key}`);
      }
    } finally {
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractivePending(false);
      }
    }
  }

  async function handleTerminateInteractiveProcess() {
    if (!interactiveProcess) {
      return;
    }

    const viewedSessionId = activeSessionIdRef.current;
    setInteractivePending(true);
    setInteractiveError(null);
    try {
      const response = await terminateInteractiveProcess(interactiveProcess.session_id);
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractiveProcess(response.process);
      }
    } catch (err) {
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractiveError(err instanceof Error ? err.message : "Failed to terminate interactive process");
      }
    } finally {
      if (activeSessionIdRef.current === viewedSessionId) {
        setInteractivePending(false);
      }
    }
  }

  async function handleCreateDelegation(payload: {
    taskType: string;
    title?: string;
    instruction: string;
    substrateId?: string;
    budget?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  }) {
    if (!activeSessionId) {
      return;
    }

    setErrorMessage(null);
    const created = await createDelegation(activeSessionId, {
      task_type: payload.taskType,
      title: payload.title,
      instruction: payload.instruction,
      substrate_id: payload.substrateId,
      budget: payload.budget,
      metadata: payload.metadata,
    });

    if (activeSessionIdRef.current === activeSessionId) {
      setDelegationTasks((current) => [created, ...current.filter((task) => task.id !== created.id)]);
    }
  }

  async function handleUpdateDelegationPolicy(payload: {
    taskType: string;
    mode?: "manual" | "suggest" | "auto";
    maxLiveTasks?: number;
    preferredSubstrateId?: string;
    autoDelegate?: boolean;
  }) {
    if (!activeSessionId || !delegationPolicy) {
      return;
    }

    const routePatch: Record<string, unknown> = {};
    if (payload.preferredSubstrateId) {
      routePatch.preferred_substrate_id = payload.preferredSubstrateId;
    }
    if (typeof payload.autoDelegate === "boolean") {
      routePatch.auto_delegate = payload.autoDelegate;
    }

    const patchPayload: Record<string, unknown> = {};
    if (payload.mode) {
      patchPayload.mode = payload.mode;
    }
    if (typeof payload.maxLiveTasks === "number") {
      patchPayload.max_live_tasks = payload.maxLiveTasks;
    }
    if (Object.keys(routePatch).length > 0) {
      patchPayload.task_routes = {
        ...(patchPayload.task_routes as Record<string, unknown> | undefined),
        [payload.taskType]: routePatch,
      };
    }

    const response = await patchSessionDelegationPolicy(activeSessionId, patchPayload as Partial<DelegationPolicyRecord>);
    if (activeSessionIdRef.current === activeSessionId) {
      setDelegationPolicy(response.delegation_policy);
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (busySessionId !== null || pendingSteerText === null) return;
    const text = pendingSteerText;
    setPendingSteerText(null);
    void handleSendMessage(text);
  }, [busySessionId]);

  const queueRunningItem = cloQueue?.running_item ?? null;
  const busySessionSummary = busySessionId
    ? sessions.find((session) => session.id === busySessionId) ?? null
    : null;
  const effectiveBusySessionId = busySessionId ?? queueRunningItem?.session_id ?? null;
  const effectiveBusySessionLabel = busySessionId
    ? busySessionSummary?.label ?? activeSession?.label ?? null
    : queueRunningItem?.session_label ?? null;
  const isBusy = effectiveBusySessionId !== null;
  const isBusyHere = Boolean(activeSessionId && effectiveBusySessionId && activeSessionId === effectiveBusySessionId);
  const isTypingHere = Boolean(activeSessionId && busySessionId && activeSessionId === busySessionId);

  return (
    <div className={`desktop-shell${activeSessionId ? "" : " desktop-shell--home"}`}>
      {activeSessionId ? (
        <SimpleChat
          session={activeSession}
          messages={messages}
          streamingThinking={streamingThinking}
          toolSteps={toolSteps}
          transientWindows={transientWindows}
          workspaceCaptures={workspaceCaptures}
          workspaceEvidence={workspaceEvidence}
          delegationTasks={delegationTasks}
          delegationSubstrates={delegationSubstrates}
          delegationPolicy={delegationPolicy}
          providers={providers}
          isBusy={isBusy}
          isBusyHere={isBusyHere}
          isTypingHere={isTypingHere}
          busySessionLabel={effectiveBusySessionLabel}
          errorMessage={errorMessage}
          onToggleWindowPin={handleToggleWindowPin}
          onCloseWindow={handleCloseWindow}
          onBack={() => {
            setActiveSessionId(null);
            setActiveSession(null);
            setMessages([]);
            setStreamingThinking(null);
            setPlan(null);
            setToolSteps([]);
            setTransientWindows([]);
            setWorkspaceCaptures([]);
            setWorkspaceEvidence([]);
            setDelegationTasks([]);
            setDelegationPolicy(null);
            setInteractiveProcess(null);
            setInteractiveError(null);
            setInteractivePending(false);
            setRuntimeChannel(null);
            setPokemonBridgeStatus(null);
          }}
          onSend={handleSendMessage}
          onCreateDelegation={handleCreateDelegation}
          onUpdateDelegationPolicy={handleUpdateDelegationPolicy}
          onInterrupt={handleInterrupt}
          onSteer={handleSteer}
          runtimeChannel={runtimeChannel}
          pokemonBridgeStatus={pokemonBridgeStatus}
          pokemonControlPending={pokemonControlPending}
          onSelectPokemonControlMode={handlePokemonControlMode}
          interactiveProcess={interactiveProcess}
          interactivePending={interactivePending}
          interactiveError={interactiveError}
          onSendInteractiveInput={handleSendInteractiveInput}
          onSendInteractiveKey={handleSendInteractiveKey}
          onTerminateInteractiveProcess={handleTerminateInteractiveProcess}
        />
      ) : (
        <TreeViewHome
          workspaces={workspaces}
          buildProjects={buildProjects}
          sessions={sessions}
          providers={providers}
          activeWorkspaceId={activeWorkspaceId}
          activeBuildProjectId={activeBuildProjectId}
          activeSessionId={activeSessionId}
          activePlan={plan}
          allPlans={workspacePlans}
          cloQueue={cloQueue}
          isBusy={isBusy}
          busySessionLabel={effectiveBusySessionLabel}
          errorMessage={errorMessage}
          creatingWorkspace={creatingWorkspace}
          creatingBuildProject={creatingBuildProject}
          creatingSession={creatingSession}
          onSelectWorkspace={setActiveWorkspaceId}
          onSelectSession={(sessionId) => handleSelectSession(sessionId)}
          onCreateWorkspace={handleCreateWorkspace}
          onCreateBuildProject={handleCreateBuildProject}
          onCreateSession={handleCreateSession}
          onQueueMessage={handleQueueMessage}
          onMoveQueueItem={handleMoveQueueItem}
          onCancelQueueItem={handleCancelQueueItem}
          onUpdateQueueSettings={handleUpdateQueueSettings}
          onOpenPlan={handleOpenPlan}
        />
      )}
    </div>
  );
}

export default DesktopShell;
