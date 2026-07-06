import { useEffect, useMemo, useState } from "react";
import { listProviderModels } from "../../api/client";
import type { ProviderRecord } from "../../api/types";
import CustomSelect from "../forms/CustomSelect";
import "./ProviderModelPicker.css";

interface ProviderModelPickerProps {
  providers: ProviderRecord[];
  providerId: string;
  model: string;
  providerLabel?: string;
  knownModelsLabel?: string;
  modelInputLabel?: string;
  disabled?: boolean;
  compact?: boolean;
  onProviderChange: (providerId: string) => void;
  onModelChange: (model: string) => void;
}

const CUSTOM_MODEL_VALUE = "__custom_model__";

export function ProviderModelPicker({
  providers,
  providerId,
  model,
  providerLabel = "Provider",
  knownModelsLabel = "Known models",
  modelInputLabel = "Model",
  disabled = false,
  compact = false,
  onProviderChange,
  onModelChange,
}: ProviderModelPickerProps) {
  const [knownModels, setKnownModels] = useState<string[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);

  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.id === providerId) ?? null,
    [providerId, providers],
  );

  useEffect(() => {
    let cancelled = false;

    if (!providerId) {
      setKnownModels([]);
      setModelError(null);
      setLoadingModels(false);
      return;
    }

    setLoadingModels(true);
    setModelError(null);

    listProviderModels(providerId)
      .then((response) => {
        if (cancelled) {
          return;
        }

        setKnownModels(response.models);
        setModelError(response.discovered ? null : response.error || "Model discovery unavailable");

        if (!model.trim()) {
          onModelChange(response.models[0] ?? selectedProvider?.model_name ?? "");
        }
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }

        const fallbackModels = selectedProvider?.model_name ? [selectedProvider.model_name] : [];
        setKnownModels(fallbackModels);
        setModelError(error instanceof Error ? error.message : "Could not load models");

        if (!model.trim() && fallbackModels[0]) {
          onModelChange(fallbackModels[0]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingModels(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [model, onModelChange, providerId, selectedProvider?.model_name]);

  const knownModelValue = knownModels.includes(model) ? model : CUSTOM_MODEL_VALUE;

  function handleKnownModelChange(nextValue: string) {
    if (nextValue === CUSTOM_MODEL_VALUE) {
      if (knownModels.includes(model)) {
        onModelChange("");
      }
      return;
    }

    onModelChange(nextValue);
  }

  return (
    <div className={`provider-model-picker${compact ? " provider-model-picker--compact" : ""}`}>
      <label className="provider-model-picker__field-group">
        <span className="provider-model-picker__label">{providerLabel}</span>
        <CustomSelect
          triggerClassName="provider-model-picker__select"
          value={providerId}
          onChange={onProviderChange}
          disabled={disabled || !providers.length}
          options={
            providers.length
              ? providers.map((provider) => ({ value: provider.id, label: provider.id }))
              : [{ value: "", label: "No providers configured", disabled: true }]
          }
          ariaLabel={providerLabel}
        />
      </label>

      <label className="provider-model-picker__field-group">
        <span className="provider-model-picker__label">{knownModelsLabel}</span>
        <CustomSelect
          triggerClassName="provider-model-picker__select"
          value={knownModelValue}
          onChange={handleKnownModelChange}
          disabled={disabled || loadingModels}
          options={[
            ...knownModels.map((knownModel) => ({ value: knownModel, label: knownModel })),
            { value: CUSTOM_MODEL_VALUE, label: "Custom model…" },
          ]}
          ariaLabel={knownModelsLabel}
        />
      </label>

      <label className="provider-model-picker__field-group provider-model-picker__field-group--wide">
        <span className="provider-model-picker__label">{modelInputLabel}</span>
        <input
          className="provider-model-picker__input"
          value={model}
          onChange={(event) => onModelChange(event.target.value)}
          placeholder={selectedProvider?.model_name || "Enter a model id"}
          disabled={disabled}
        />
      </label>

      <div className="provider-model-picker__meta" aria-live="polite">
        <span className="provider-model-picker__provider-kind">
          {selectedProvider ? `${selectedProvider.kind} at ${selectedProvider.base_url}` : "No provider selected"}
        </span>
        {loadingModels && <span className="provider-model-picker__hint">Loading models…</span>}
        {!loadingModels && modelError && <span className="provider-model-picker__hint">{modelError}</span>}
      </div>
    </div>
  );
}

export default ProviderModelPicker;