import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  type PluginConfig,
  type PluginManifest,
  pluginsApi,
} from "../../api/library";
import { type PluginSummary } from "../../api/wizard";
import { PluginModelPicker } from "../../components/PluginModelPicker";

export interface ModelSlot {
  key: string;
  label: string;
  sublabel?: string;
}

interface Props {
  title: string;
  icon: string;
  plugins: PluginSummary[];
  manifests: PluginManifest[];
  modelSlots: ModelSlot[];
  defaults: Record<string, string | null>;
  onDefaultChange: (slotKey: string, route: string | null) => void;
  loading?: boolean;
}

function StatusBadge({
  plugin,
  configured,
  loading,
}: {
  plugin: PluginSummary | undefined;
  configured: boolean;
  loading?: boolean;
}) {
  if (!plugin) {
    return <span className="provider-status provider-status-idle">No provider</span>;
  }
  if (plugin.load_error) {
    return <span className="provider-status provider-status-error">Error</span>;
  }
  if (loading) {
    return <span className="provider-status provider-status-idle">Checking…</span>;
  }
  if (configured) {
    return <span className="provider-status provider-status-ok">Connected</span>;
  }
  return <span className="provider-status provider-status-idle">Not configured</span>;
}

function findModelPathValue(
  config: PluginConfig | null,
  manifest: PluginManifest | undefined,
): string | null {
  if (!config || !manifest) return null;
  const props = (
    manifest.config_schema as
      | { properties?: Record<string, Record<string, unknown>> }
      | undefined
  )?.properties;
  if (!props?.["model_path"]) return null;
  const val = config.values["model_path"];
  return typeof val === "string" ? val : null;
}

export function ProviderCard({
  title,
  icon,
  plugins,
  manifests,
  modelSlots,
  defaults,
  onDefaultChange,
  loading: parentLoading,
}: Props) {
  const [selectedId, setSelectedId] = useState<string>("");
  const [config, setConfig] = useState<PluginConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(false);

  // Auto-select the first plugin if only one is installed
  useEffect(() => {
    if (!selectedId && plugins.length === 1) {
      setSelectedId(plugins[0]!.id);
    }
  }, [plugins, selectedId]);

  // Auto-select the plugin that matches the first default route's provider prefix
  useEffect(() => {
    if (selectedId || plugins.length === 0) return;
    const firstDefault = Object.values(defaults).find((v) => v != null);
    if (!firstDefault) return;
    const providerPart = firstDefault.split(".")[0];
    const match = plugins.find((p) => p.id === providerPart);
    if (match) setSelectedId(match.id);
  }, [plugins, defaults, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setConfig(null);
      return;
    }
    let cancelled = false;
    setConfigLoading(true);
    void (async () => {
      try {
        const cfg = await pluginsApi.getConfig(selectedId);
        if (!cancelled) setConfig(cfg);
      } catch {
        if (!cancelled) setConfig(null);
      } finally {
        if (!cancelled) setConfigLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const activePlugin = plugins.find((p) => p.id === selectedId);
  const activeManifest = manifests.find((m) => m.id === selectedId);
  const modelPath = findModelPathValue(config, activeManifest);

  return (
    <section className="provider-card provider-card-primary">
      <header className="provider-card-head">
        <div className="provider-card-title">
          <span className="provider-card-icon" aria-hidden="true">
            {icon}
          </span>
          <h3>{title}</h3>
        </div>
        <StatusBadge
          plugin={activePlugin}
          configured={Boolean(config?.configured)}
          loading={configLoading}
        />
      </header>

      <label className="provider-combobox">
        <span className="provider-combobox-label">Provider</span>
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          disabled={parentLoading || plugins.length === 0}
        >
          <option value="">
            {plugins.length === 0 ? "None installed" : "— Select a provider —"}
          </option>
          {plugins.map((p) => (
            <option key={p.id} value={p.id} disabled={Boolean(p.load_error)}>
              {(p.name ?? p.id) + (p.version ? `  ·  v${p.version}` : "")}
              {p.load_error ? "  ·  load error" : ""}
            </option>
          ))}
        </select>
      </label>

      {activePlugin?.load_error && (
        <p className="wizard-error" role="alert">
          Load error: {activePlugin.load_error}
        </p>
      )}

      {activePlugin &&
        config?.configured &&
        modelSlots.map((slot) => (
          <section key={slot.key} className="provider-model-picker">
            <PluginModelPicker
              pluginId={activePlugin.id}
              label={slot.label}
              description={slot.sublabel}
              value={defaults[slot.key] ?? ""}
              onChange={(next) => onDefaultChange(slot.key, next || null)}
            />
          </section>
        ))}

      {activePlugin && config && !config.configured && (
        <p className="wizard-meta">
          Configure your API key first.{" "}
          <Link to={`/library/plugins/${encodeURIComponent(activePlugin.id)}`}>
            Open settings
          </Link>
        </p>
      )}

      {modelPath && (
        <div className="provider-model-path">
          <span className="provider-model-path-label">Model file</span>
          <code>{modelPath}</code>
        </div>
      )}

      <div className="provider-card-actions">
        {activePlugin ? (
          <Link
            to={`/library/plugins/${encodeURIComponent(activePlugin.id)}`}
            className="button-link primary"
          >
            Configure {activePlugin.name ?? activePlugin.id}
          </Link>
        ) : (
          <span className="provider-card-hint">Select a provider to get started.</span>
        )}
      </div>
    </section>
  );
}
