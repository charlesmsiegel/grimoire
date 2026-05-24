import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  type PluginConfig,
  type PluginManifest,
  pluginsApi,
} from "../../api/library";
import {
  type PluginSummary,
  fetchInstalledPlugins,
} from "../../api/wizard";
import { PluginModelPicker } from "../../components/PluginModelPicker";
import { errorMessage } from "./shared";

interface FieldSchema {
  title?: string;
  description?: string;
  default?: unknown;
  [key: string]: unknown;
}

function findModelField(
  manifest: PluginManifest | undefined,
): { name: string; schema: FieldSchema } | null {
  const props = (manifest?.config_schema as { properties?: Record<string, FieldSchema> } | undefined)
    ?.properties;
  if (!props) return null;
  for (const [name, schema] of Object.entries(props)) {
    if (schema && schema["x-source"] === "models") return { name, schema };
  }
  return null;
}

const LLM_DEFAULT_KEY = "grimoire.llm.default";

function readDefaultLlm(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(LLM_DEFAULT_KEY) ?? "";
  } catch {
    return "";
  }
}

function ProviderStatusBadge({
  plugin,
  configured,
  loading,
}: {
  plugin: PluginSummary | undefined;
  configured: boolean;
  loading?: boolean;
}) {
  if (!plugin) {
    return <span className="provider-status provider-status-idle">Not selected</span>;
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

function ProviderConfigSummary({ config, loading }: { config: PluginConfig; loading: boolean }) {
  if (loading) return null;
  const valueEntries = Object.entries(config.values).filter(
    ([, v]) => v !== null && v !== "" && v !== undefined,
  );
  const secretEntries = Object.entries(config.secrets_set);
  if (valueEntries.length === 0 && secretEntries.length === 0) return null;
  return (
    <dl className="provider-config-summary" aria-label="Saved configuration">
      {valueEntries.map(([k, v]) => (
        <div key={k} className="provider-config-row">
          <dt>{k}</dt>
          <dd title={formatConfigValue(v)}>{formatConfigValue(v)}</dd>
        </div>
      ))}
      {secretEntries.map(([k, isSet]) => (
        <div key={`secret-${k}`} className="provider-config-row">
          <dt>{k}</dt>
          <dd>
            {isSet ? (
              <span className="provider-config-secret-set">•••••• (set)</span>
            ) : (
              <span className="provider-config-secret-unset">(not set)</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function formatConfigValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function SecondaryProviderCard({
  title,
  description,
  plugins,
}: {
  title: string;
  description: string;
  plugins: PluginSummary[];
}) {
  return (
    <section className="provider-card provider-card-secondary">
      <header>
        <h4>{title}</h4>
        <p className="provider-card-sub">{description}</p>
      </header>
      {plugins.length === 0 ? (
        <p className="wizard-meta">None installed.</p>
      ) : (
        <ul className="provider-list">
          {plugins.map((p) => (
            <li key={p.id} className={p.load_error ? "has-error" : undefined}>
              <Link to={`/library/plugins/${encodeURIComponent(p.id)}`}>
                <strong>{p.name ?? p.id}</strong>
                {p.version && <small> v{p.version}</small>}
              </Link>
              {p.load_error && <p className="wizard-error">Load error: {p.load_error}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ProvidersTab() {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [manifests, setManifests] = useState<PluginManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedLlm, setSelectedLlm] = useState<string>(readDefaultLlm);
  const [activeConfig, setActiveConfig] = useState<PluginConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const [modelStatus, setModelStatus] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchInstalledPlugins();
        const full = await pluginsApi.listInstalled();
        if (!cancelled) {
          setPlugins(data);
          setManifests(full);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedLlm) {
      setActiveConfig(null);
      return;
    }
    let cancelled = false;
    setConfigLoading(true);
    void (async () => {
      try {
        const cfg = await pluginsApi.getConfig(selectedLlm);
        if (!cancelled) setActiveConfig(cfg);
      } catch {
        if (!cancelled) setActiveConfig(null);
      } finally {
        if (!cancelled) setConfigLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedLlm]);

  const llmPlugins = plugins.filter((p) => p.kind === "llm_provider");
  const embedPlugins = plugins.filter((p) => p.kind === "embedding_provider");
  const imageBackends = plugins.filter((p) => p.kind === "imagegen_backend");
  const exportAdapters = plugins.filter((p) => p.kind === "export_adapter");

  const onPickLlm = (id: string) => {
    setSelectedLlm(id);
    try {
      window.localStorage.setItem(LLM_DEFAULT_KEY, id);
    } catch {
      // ignore
    }
  };

  const activeLlm = llmPlugins.find((p) => p.id === selectedLlm);
  const activeManifest = manifests.find((m) => m.id === selectedLlm);
  const modelField = findModelField(activeManifest);
  const currentModel =
    typeof activeConfig?.values[modelField?.name ?? ""] === "string"
      ? (activeConfig.values[modelField!.name] as string)
      : (modelField?.schema.default as string | undefined) ?? "";

  async function saveModel(next: string) {
    if (!selectedLlm || !modelField) return;
    setModelSaving(true);
    setModelStatus(null);
    try {
      await pluginsApi.patchConfig(selectedLlm, { [modelField.name]: next });
      const cfg = await pluginsApi.getConfig(selectedLlm);
      setActiveConfig(cfg);
      setModelStatus("Saved.");
    } catch (err) {
      setModelStatus(`Error: ${errorMessage(err)}`);
    } finally {
      setModelSaving(false);
    }
  }

  return (
    <div className="settings-form providers-form">
      <div className="providers-wizard-launch">
        <div>
          <strong>Setup wizard</strong>
          <p className="provider-card-sub">
            Re-run the first-run wizard to walk through language model, embeddings, image
            generation, and a starter campaign.
          </p>
        </div>
        <button
          type="button"
          className="primary"
          onClick={() => window.dispatchEvent(new Event("grimoire:open-startup-wizard"))}
        >
          Run setup wizard
        </button>
      </div>

      {loading && <p className="wizard-meta">Loading providers…</p>}
      {error && <p className="wizard-error">{error}</p>}

      <section className="provider-card provider-card-primary" aria-labelledby="llm-heading">
        <header className="provider-card-head">
          <div className="provider-card-title">
            <span className="provider-card-icon" aria-hidden="true">
              ✦
            </span>
            <div>
              <h3 id="llm-heading">Language model</h3>
              <p className="provider-card-sub">
                Choose how Grimoire connects to a language model for narration, NPCs, and summaries.
              </p>
            </div>
          </div>
          <ProviderStatusBadge
            plugin={activeLlm}
            configured={Boolean(activeConfig?.configured)}
            loading={configLoading}
          />
        </header>

        <label className="provider-combobox">
          <span className="provider-combobox-label">Connection method</span>
          <select
            value={selectedLlm}
            onChange={(e) => onPickLlm(e.target.value)}
            disabled={loading || llmPlugins.length === 0}
            aria-label="Select LLM connection"
          >
            <option value="">
              {llmPlugins.length === 0 ? "No LLM providers installed" : "— Select a provider —"}
            </option>
            {llmPlugins.map((p) => (
              <option key={p.id} value={p.id} disabled={Boolean(p.load_error)}>
                {(p.name ?? p.id) + (p.version ? `  ·  v${p.version}` : "")}
                {p.load_error ? "  ·  load error" : ""}
              </option>
            ))}
          </select>
        </label>

        {activeLlm?.load_error && (
          <p className="wizard-error" role="alert">
            Load error: {activeLlm.load_error}
          </p>
        )}

        {activeLlm && activeConfig && (
          <ProviderConfigSummary config={activeConfig} loading={configLoading} />
        )}

        {activeLlm && modelField && activeConfig && !activeConfig.configured && (
          <p className="wizard-meta">
            Configure your API key first to pick a model.{" "}
            <Link to={`/library/plugins/${encodeURIComponent(activeLlm.id)}`}>Open settings</Link>
          </p>
        )}

        {activeLlm && modelField && activeConfig?.configured && (
          <section className="provider-model-picker" aria-label="Active model">
            <PluginModelPicker
              pluginId={activeLlm.id}
              label={modelField.schema.title ?? "Active model"}
              description={modelField.schema.description}
              value={currentModel}
              onChange={(next) => void saveModel(next)}
            />
            {modelSaving && <small className="wizard-meta">Saving…</small>}
            {modelStatus && (
              <small
                className={modelStatus.startsWith("Error") ? "wizard-error" : "library-ok"}
                role="status"
              >
                {modelStatus}
              </small>
            )}
          </section>
        )}

        <div className="provider-card-actions">
          {activeLlm ? (
            <Link
              to={`/library/plugins/${encodeURIComponent(activeLlm.id)}`}
              className="button-link primary"
            >
              Configure {activeLlm.name ?? activeLlm.id}
            </Link>
          ) : (
            <span className="provider-card-hint">
              Pick a connection method to enter API keys and default models.
            </span>
          )}
          {llmPlugins.length > 0 && (
            <Link to="/library/plugins" className="button-link">
              Browse all providers
            </Link>
          )}
        </div>

        {llmPlugins.length > 1 && (
          <details className="provider-disclose">
            <summary>Compare installed providers ({llmPlugins.length})</summary>
            <ul className="provider-list provider-list-compact">
              {llmPlugins.map((p) => (
                <li
                  key={p.id}
                  className={p.load_error ? "has-error" : undefined}
                  data-active={p.id === selectedLlm || undefined}
                >
                  <button type="button" className="provider-row" onClick={() => onPickLlm(p.id)}>
                    <span className="provider-row-name">{p.name ?? p.id}</span>
                    {p.version && <span className="provider-row-version">v{p.version}</span>}
                    {p.id === selectedLlm && <span className="badge badge-ok">Selected</span>}
                    {p.load_error && <span className="badge badge-warn">Error</span>}
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <div className="provider-secondary-grid">
        <SecondaryProviderCard
          title="Embeddings"
          description="Vector index for memory and retrieval."
          plugins={embedPlugins}
        />
        <SecondaryProviderCard
          title="Image generation"
          description="Backends that render scene illustrations."
          plugins={imageBackends}
        />
        <SecondaryProviderCard
          title="Export adapters"
          description="Save plays as PDF, HTML, journal, etc."
          plugins={exportAdapters}
        />
      </div>

      <p className="wizard-meta">
        Provider configuration (API keys, default models) lives in each plugin's detail page; keys
        save to the OS keyring where possible.
      </p>
    </div>
  );
}
