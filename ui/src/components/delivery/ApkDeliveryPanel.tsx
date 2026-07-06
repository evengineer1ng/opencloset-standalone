import { useEffect, useMemo, useState } from "react";
import { buildAndQueueProjectDelivery, listProjectDeliveries, publishProjectDelivery, updateProjectDelivery } from "../../api/client";
import type { ProjectDeliveryRecord } from "../../api/types";
import CustomSelect from "../forms/CustomSelect";
import "./ApkDeliveryPanel.css";

const WORKSPACE_ONLY_VALUE = "__workspace_only__";

export interface ApkDeliveryProjectOption {
  id: string;
  label: string;
}

export interface ApkDeliverySessionOption {
  id: string;
  label: string;
  buildProjectId: string | null;
}

interface ApkDeliveryPanelProps {
  workspaceId: string | null;
  projectOptions: ApkDeliveryProjectOption[];
  sessionOptions?: ApkDeliverySessionOption[];
  preferredProjectId?: string | null;
  preferredSessionId?: string | null;
  originTag: string;
  eyebrow?: string;
  title: string;
  subtitle: string;
  emptyProjectMessage: string;
  className?: string;
}

export default function ApkDeliveryPanel({
  workspaceId,
  projectOptions,
  sessionOptions = [],
  preferredProjectId = null,
  preferredSessionId = null,
  originTag,
  eyebrow = "APK Delivery",
  title,
  subtitle,
  emptyProjectMessage,
  className = "",
}: ApkDeliveryPanelProps) {
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState(WORKSPACE_ONLY_VALUE);
  const [targetDeviceId, setTargetDeviceId] = useState("fold5");
  const [buildVariant, setBuildVariant] = useState("debug");
  const [deliveryFile, setDeliveryFile] = useState<File | null>(null);
  const [deliveries, setDeliveries] = useState<ProjectDeliveryRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actingDeliveryId, setActingDeliveryId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);

  useEffect(() => {
    setSelectedProjectId((current) => {
      if (preferredProjectId && projectOptions.some((option) => option.id === preferredProjectId)) {
        return preferredProjectId;
      }
      if (current && projectOptions.some((option) => option.id === current)) {
        return current;
      }
      return projectOptions[0]?.id ?? "";
    });
  }, [preferredProjectId, projectOptions]);

  const filteredSessions = useMemo(() => {
    if (!selectedProjectId) {
      return [] as ApkDeliverySessionOption[];
    }
    return sessionOptions.filter((session) => session.buildProjectId === selectedProjectId);
  }, [selectedProjectId, sessionOptions]);

  useEffect(() => {
    setSelectedSessionId((current) => {
      if (preferredSessionId && filteredSessions.some((session) => session.id === preferredSessionId)) {
        return preferredSessionId;
      }
      if (current !== WORKSPACE_ONLY_VALUE && filteredSessions.some((session) => session.id === current)) {
        return current;
      }
      return WORKSPACE_ONLY_VALUE;
    });
  }, [filteredSessions, preferredSessionId]);

  useEffect(() => {
    if (!workspaceId || !selectedProjectId) {
      setDeliveries([]);
      return;
    }

    let cancelled = false;

    async function loadDeliveries() {
      setLoading(true);
      try {
        const response = await listProjectDeliveries(workspaceId, selectedProjectId, { limit: 8 });
        if (!cancelled) {
          setDeliveries(response.deliveries);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load project deliveries");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadDeliveries();
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, workspaceId]);

  const sessionSelectOptions = useMemo(
    () => [
      { value: WORKSPACE_ONLY_VALUE, label: "Workspace only" },
      ...filteredSessions.map((session) => ({ value: session.id, label: session.label })),
    ],
    [filteredSessions],
  );

  const selectedSessionLabel =
    selectedSessionId === WORKSPACE_ONLY_VALUE
      ? "workspace only"
      : filteredSessions.find((session) => session.id === selectedSessionId)?.label ?? "session-linked";

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspaceId || !selectedProjectId || !deliveryFile) {
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const created = await publishProjectDelivery(workspaceId, selectedProjectId, {
        file: deliveryFile,
        device_id: targetDeviceId.trim() || undefined,
        session_id: selectedSessionId !== WORKSPACE_ONLY_VALUE ? selectedSessionId : undefined,
        metadata: {
          build_variant: buildVariant,
          release_channel: buildVariant,
          uploaded_from: originTag,
        },
      });
      setDeliveries((current) => [created, ...current.filter((delivery) => delivery.id !== created.id)]);
      setDeliveryFile(null);
      setFileInputKey((current) => current + 1);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Failed to queue APK delivery");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleBuildAndQueue() {
    if (!workspaceId || !selectedProjectId) {
      return;
    }

    setBuilding(true);
    setError(null);
    try {
      const created = await buildAndQueueProjectDelivery(workspaceId, selectedProjectId, {
        variant: buildVariant as "debug" | "release",
        device_id: targetDeviceId.trim() || undefined,
        session_id: selectedSessionId !== WORKSPACE_ONLY_VALUE ? selectedSessionId : undefined,
        metadata: {
          release_channel: buildVariant,
          requested_from: originTag,
        },
      });
      setDeliveries((current) => [created, ...current.filter((delivery) => delivery.id !== created.id)]);
    } catch (buildError) {
      setError(buildError instanceof Error ? buildError.message : "Failed to build and queue APK delivery");
    } finally {
      setBuilding(false);
    }
  }

  async function handleDeliveryStatusUpdate(delivery: ProjectDeliveryRecord, status: string, note: string) {
    if (!workspaceId) {
      return;
    }

    setActingDeliveryId(delivery.id);
    setError(null);
    try {
      const updated = await updateProjectDelivery(workspaceId, delivery.id, {
        status,
        device_id: delivery.target_device_id || targetDeviceId.trim() || undefined,
        note,
        metadata: {
          updated_from: originTag,
          updated_at: new Date().toISOString(),
        },
      });
      setDeliveries((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Failed to update delivery status");
    } finally {
      setActingDeliveryId(null);
    }
  }

  return (
    <section className={`apk-delivery-panel ${className}`.trim()}>
      <div className="apk-delivery-panel__header">
        <div>
          <div className="apk-delivery-panel__eyebrow">{eyebrow}</div>
          <div className="apk-delivery-panel__title">{title}</div>
          <div className="apk-delivery-panel__subtitle">{subtitle}</div>
        </div>
        {loading && <span className="apk-delivery-panel__loading">loading</span>}
      </div>

      {projectOptions.length ? (
        <>
          <form className="apk-delivery-panel__form" onSubmit={handleSubmit}>
            <div className="apk-delivery-panel__grid">
              <label className="apk-delivery-panel__field">
                <span className="apk-delivery-panel__label">Build project</span>
                <CustomSelect
                  triggerClassName="apk-delivery-panel__input apk-delivery-panel__input--select"
                  value={selectedProjectId}
                  onChange={setSelectedProjectId}
                  options={projectOptions.map((option) => ({ value: option.id, label: option.label }))}
                  ariaLabel="Build project"
                />
              </label>
              <label className="apk-delivery-panel__field">
                <span className="apk-delivery-panel__label">Build variant</span>
                <CustomSelect
                  triggerClassName="apk-delivery-panel__input apk-delivery-panel__input--select"
                  value={buildVariant}
                  onChange={setBuildVariant}
                  options={[
                    { value: "debug", label: "Debug" },
                    { value: "release", label: "Release" },
                  ]}
                  ariaLabel="Build variant"
                />
              </label>
              <label className="apk-delivery-panel__field">
                <span className="apk-delivery-panel__label">Target device</span>
                <input
                  className="apk-delivery-panel__input"
                  value={targetDeviceId}
                  onChange={(event) => setTargetDeviceId(event.target.value)}
                  placeholder="fold5"
                />
              </label>
              <label className="apk-delivery-panel__field">
                <span className="apk-delivery-panel__label">Link to session</span>
                <CustomSelect
                  triggerClassName="apk-delivery-panel__input apk-delivery-panel__input--select"
                  value={selectedSessionId}
                  onChange={setSelectedSessionId}
                  options={sessionSelectOptions}
                  ariaLabel="Session link"
                />
              </label>
            </div>

            <label className="apk-delivery-panel__field apk-delivery-panel__field--wide">
              <span className="apk-delivery-panel__label">APK file</span>
              <input
                key={fileInputKey}
                className="apk-delivery-panel__input apk-delivery-panel__file"
                type="file"
                accept=".apk,application/vnd.android.package-archive"
                onChange={(event) => setDeliveryFile(event.target.files?.[0] ?? null)}
              />
            </label>

            <div className="apk-delivery-panel__actions">
              <div className="apk-delivery-panel__meta">
                {selectedProjectId ? `${buildVariant} build queued to ${targetDeviceId || "broadcast"} · ${selectedSessionLabel}` : emptyProjectMessage}
              </div>
              <div className="apk-delivery-panel__action-group">
                <button className="apk-delivery-panel__secondary" type="button" disabled={building || submitting || !selectedProjectId} onClick={() => void handleBuildAndQueue()}>
                  {building ? "Building..." : "Build + queue"}
                </button>
                <button className="apk-delivery-panel__submit" type="submit" disabled={submitting || building || !selectedProjectId || !deliveryFile}>
                  {submitting ? "Uploading..." : "Queue existing APK"}
                </button>
              </div>
            </div>
          </form>

          {error && <div className="apk-delivery-panel__error">{error}</div>}

          <div className="apk-delivery-panel__list">
            {deliveries.map((delivery) => {
              const variantLabel =
                typeof delivery.metadata.build_variant === "string"
                  ? delivery.metadata.build_variant
                  : typeof delivery.metadata.release_channel === "string"
                    ? delivery.metadata.release_channel
                    : "queued";

              return (
                <div key={delivery.id} className="apk-delivery-panel__card">
                  <div className="apk-delivery-panel__card-header">
                    <div>
                      <div className="apk-delivery-panel__card-title">{delivery.file_name}</div>
                      <div className="apk-delivery-panel__card-meta">
                        {variantLabel} · {delivery.target_device_id || "broadcast"} · {formatDeliveryTime(delivery.created_at)}
                      </div>
                    </div>
                    <span className={`badge ${delivery.status === "installed" ? "badge-success" : "badge-pending"}`}>{delivery.status}</span>
                  </div>
                  <div className="apk-delivery-panel__card-summary">
                    {delivery.session_id ? "session-linked" : "workspace only"} · {Math.max(1, Math.round(delivery.size_bytes / 1024))} KB
                  </div>
                  <div className="apk-delivery-panel__card-actions">
                    {delivery.status === "ready" && (
                      <button
                        type="button"
                        className="apk-delivery-panel__mini-action"
                        disabled={actingDeliveryId === delivery.id}
                        onClick={() => void handleDeliveryStatusUpdate(delivery, "downloading", "browser acknowledged download start")}
                      >
                        {actingDeliveryId === delivery.id ? "Updating..." : "Downloading"}
                      </button>
                    )}
                    {!(["downloaded", "installed", "cancelled", "failed"] as string[]).includes(delivery.status) && (
                      <button
                        type="button"
                        className="apk-delivery-panel__mini-action"
                        disabled={actingDeliveryId === delivery.id}
                        onClick={() => void handleDeliveryStatusUpdate(delivery, "downloaded", "browser acknowledged APK download")}
                      >
                        {actingDeliveryId === delivery.id ? "Updating..." : "Downloaded"}
                      </button>
                    )}
                    {!(["installed", "cancelled"] as string[]).includes(delivery.status) && (
                      <button
                        type="button"
                        className="apk-delivery-panel__mini-action apk-delivery-panel__mini-action--success"
                        disabled={actingDeliveryId === delivery.id}
                        onClick={() => void handleDeliveryStatusUpdate(delivery, "installed", "browser marked sideload complete")}
                      >
                        {actingDeliveryId === delivery.id ? "Updating..." : "Installed"}
                      </button>
                    )}
                    {!(["installed", "cancelled", "failed"] as string[]).includes(delivery.status) && (
                      <button
                        type="button"
                        className="apk-delivery-panel__mini-action apk-delivery-panel__mini-action--warn"
                        disabled={actingDeliveryId === delivery.id}
                        onClick={() => void handleDeliveryStatusUpdate(delivery, "failed", "browser marked delivery failed")}
                      >
                        {actingDeliveryId === delivery.id ? "Updating..." : "Fail"}
                      </button>
                    )}
                    {!(["installed", "cancelled"] as string[]).includes(delivery.status) && (
                      <button
                        type="button"
                        className="apk-delivery-panel__mini-action apk-delivery-panel__mini-action--ghost"
                        disabled={actingDeliveryId === delivery.id}
                        onClick={() => void handleDeliveryStatusUpdate(delivery, "cancelled", "browser cancelled delivery")}
                      >
                        {actingDeliveryId === delivery.id ? "Updating..." : "Cancel"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            {!deliveries.length && !loading && <div className="apk-delivery-panel__empty">No APK deliveries queued for this project yet.</div>}
          </div>
        </>
      ) : (
        <div className="apk-delivery-panel__empty">{emptyProjectMessage}</div>
      )}
    </section>
  );
}

function formatDeliveryTime(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}