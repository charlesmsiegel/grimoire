import { useEffect, useState } from "react";

import { type PluginSummary, fetchInstalledPlugins } from "../../../api/wizard";
import { SaveIndicator } from "./SaveIndicator";
import { useAutoSavedResource } from "./shared";

interface ImageGenValue {
  backend: string | null;
  preset: string | null;
  sampler_defaults: unknown;
}

export function ImageGenTab({ campaignId }: { campaignId: string }) {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const { value, setValue, status, error, ready } = useAutoSavedResource<ImageGenValue>(
    campaignId,
    "/imagegen",
    { backend: null, preset: null, sampler_defaults: null },
  );

  useEffect(() => {
    void fetchInstalledPlugins().then((data) => setPlugins(data));
  }, []);

  const backends = plugins.filter((p) => p.kind === "imagegen_backend");

  const samplerText =
    typeof value.sampler_defaults === "string"
      ? value.sampler_defaults
      : value.sampler_defaults === null || value.sampler_defaults === undefined
        ? ""
        : JSON.stringify(value.sampler_defaults);

  return (
    <div className="settings-form">
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-field">
        <span>Backend</span>
        <select
          value={value.backend ?? ""}
          onChange={(e) => setValue((prev) => ({ ...prev, backend: e.target.value || null }))}
          disabled={!ready}
        >
          <option value="">(integrated diffusers)</option>
          {backends.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name ?? b.id}
            </option>
          ))}
        </select>
      </label>
      <label className="wizard-field">
        <span>Active preset</span>
        <input
          type="text"
          value={value.preset ?? ""}
          onChange={(e) => setValue((prev) => ({ ...prev, preset: e.target.value || null }))}
          placeholder="oil-painting"
          disabled={!ready}
        />
      </label>
      <label className="wizard-field">
        <span>Sampler defaults</span>
        <input
          type="text"
          value={samplerText}
          onChange={(e) =>
            setValue((prev) => ({
              ...prev,
              sampler_defaults: e.target.value ? e.target.value : null,
            }))
          }
          placeholder="DPM++ 2M Karras, 25 steps"
          disabled={!ready}
        />
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
