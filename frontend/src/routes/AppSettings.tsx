/**
 * App-level settings. Surfaces from spec 14:
 *   - Library location (path)
 *   - LLM / embedding / ImageGen provider configs (shared across campaigns)
 *   - Mechanics + plugin scan paths and error log
 *   - Backup policy
 *   - Appearance (theme, font, density)
 *
 * The backend exposes the bulk of these endpoints incrementally; this view
 * renders the structure so users (and downstream tasks) see one canonical
 * place for app-wide configuration. Appearance interacts with the theme
 * provider directly. Plugin/mechanics inventories and rescan are wired today.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import {
  type PluginConfig,
  type PluginManifest,
  type TemplateSummary,
  pluginsApi,
  templatesApi,
} from "../api/library";
import {
  type MechanicsModuleSummary,
  type PluginSummary,
  fetchInstalledMechanics,
  fetchInstalledPlugins,
} from "../api/wizard";
import { PluginModelPicker } from "../components/PluginModelPicker";
import { useTheme } from "../state/useTheme";

type Tab =
  | "library"
  | "providers"
  | "templates"
  | "mechanics"
  | "plugins"
  | "backup"
  | "appearance";

const TABS: { id: Tab; label: string }[] = [
  { id: "library", label: "Library" },
  { id: "providers", label: "Providers" },
  { id: "templates", label: "Prompts" },
  { id: "mechanics", label: "Mechanics" },
  { id: "plugins", label: "Plugins" },
  { id: "backup", label: "Backup" },
  { id: "appearance", label: "Appearance" },
];

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

interface FieldSchema {
  title?: string;
  description?: string;
  default?: unknown;
  [key: string]: unknown;
}

/** Find the schema property annotated with ``x-source: "models"``, if any. */
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

export function AppSettings() {
  const [tab, setTab] = useState<Tab>("library");
  return (
    <section className="route app-settings" aria-labelledby="app-settings-heading">
      <header>
        <h2 id="app-settings-heading">Settings</h2>
      </header>
      <nav className="tab-bar" aria-label="App settings tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "tab active" : "tab"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="tab-panel">
        {tab === "library" && <LibraryPathTab />}
        {tab === "providers" && <ProvidersTab />}
        {tab === "templates" && <TemplatesTab />}
        {tab === "mechanics" && <MechanicsInventoryTab />}
        {tab === "plugins" && <PluginsInventoryTab />}
        {tab === "backup" && <BackupTab />}
        {tab === "appearance" && <AppearanceTab />}
      </div>
    </section>
  );
}

interface AppConfig {
  library_path: string;
  backup: { schedule: string; retention_days: number; location: string };
}

/** Debounced PATCH to /api/config/app. */
function useAppConfig(): {
  data: AppConfig | null;
  patch: (next: Partial<AppConfig>) => void;
  status: "idle" | "loading" | "saving" | "saved" | "error";
  error: string | null;
} {
  const [data, setData] = useState<AppConfig | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "saving" | "saved" | "error">(
    "loading",
  );
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<Partial<AppConfig> | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await api.get<AppConfig>("/api/config/app");
        if (!cancelled) {
          setData(result);
          setStatus("idle");
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setStatus("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const patch = useCallback((next: Partial<AppConfig>) => {
    setData((prev) => (prev ? { ...prev, ...next } : prev));
    pending.current = { ...(pending.current ?? {}), ...next };
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const body = pending.current;
      pending.current = null;
      timer.current = null;
      if (!body) return;
      setStatus("saving");
      setError(null);
      void (async () => {
        try {
          const result = await api.patch<AppConfig>("/api/config/app", body);
          setData(result);
          setStatus("saved");
        } catch (err) {
          setError(errorMessage(err));
          setStatus("error");
        }
      })();
    }, 500);
  }, []);

  return { data, patch, status, error };
}

function ConfigSaveIndicator({
  status,
  error,
}: {
  status: "idle" | "loading" | "saving" | "saved" | "error";
  error: string | null;
}) {
  if (status === "loading") return <small className="wizard-meta">Loading…</small>;
  if (status === "saving") return <small className="wizard-meta">Saving…</small>;
  if (status === "error") {
    return (
      <small className="wizard-error" role="alert">
        {error ?? "Save failed"}
      </small>
    );
  }
  if (status === "saved") return <small className="library-ok">Saved.</small>;
  return null;
}

function LibraryPathTab() {
  const { data, patch, status, error } = useAppConfig();
  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Library path</span>
        <input
          type="text"
          value={data?.library_path ?? ""}
          onChange={(e) => patch({ library_path: e.target.value })}
          disabled={!data}
        />
        <small>Filesystem directory scanned for settings, style guides, presets.</small>
      </label>
      <p className="wizard-meta">
        Persisted to <code>data/config/app.yaml</code>. Changes save automatically.
      </p>
      <ConfigSaveIndicator status={status} error={error} />
    </div>
  );
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

function ProvidersTab() {
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

  // Fetch saved config when the selected LLM changes so the status badge
  // reflects "Connected" (configured) vs "Not configured" — including which
  // secret fields are set.
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

      {/* Primary LLM connection card — most-used setting, given top billing */}
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

      {/* Secondary provider kinds */}
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

function MechanicsInventoryTab() {
  const [modules, setModules] = useState<MechanicsModuleSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rescanning, setRescanning] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchInstalledMechanics();
      setModules(data);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const rescan = async () => {
    setRescanning(true);
    try {
      await api.post("/api/mechanics/rescan");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRescanning(false);
    }
  };

  return (
    <div className="settings-form">
      <p className="wizard-step-help">Discovery paths configured in app.yaml.</p>
      <button type="button" disabled={rescanning} onClick={() => void rescan()}>
        {rescanning ? "Rescanning…" : "Rescan"}
      </button>
      {loading && <p className="wizard-meta">Loading…</p>}
      {error && <p className="wizard-error">{error}</p>}
      {!loading && modules.length === 0 && (
        <p className="wizard-meta">No mechanics modules installed.</p>
      )}
      <ul className="provider-list">
        {modules.map((m) => (
          <li key={m.id} className={m.load_error ? "has-error" : undefined}>
            <strong>{m.name ?? m.id}</strong>
            {m.version && <small> v{m.version}</small>}
            {m.api_version && <small> · api {m.api_version}</small>}
            {m.load_error && <p className="wizard-error">Load error: {m.load_error}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PluginsInventoryTab() {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rescanning, setRescanning] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchInstalledPlugins();
      setPlugins(data);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const rescan = async () => {
    setRescanning(true);
    try {
      await api.post("/api/plugins/rescan");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRescanning(false);
    }
  };

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        All plugin kinds. Drop new plugins into <code>~/.grimoire/plugins</code> and rescan. Click a
        plugin to open its configuration form.
      </p>
      <button type="button" disabled={rescanning} onClick={() => void rescan()}>
        {rescanning ? "Rescanning…" : "Rescan"}
      </button>
      {loading && <p className="wizard-meta">Loading…</p>}
      {error && <p className="wizard-error">{error}</p>}
      {!loading && plugins.length === 0 && <p className="wizard-meta">No plugins installed.</p>}
      <ul className="provider-list">
        {plugins.map((p) => (
          <li key={`${p.kind}:${p.id}`} className={p.load_error ? "has-error" : undefined}>
            <Link to={`/library/plugins/${encodeURIComponent(p.id)}`}>
              <strong>{p.name ?? p.id}</strong>
              <small> · {p.kind}</small>
              {p.version && <small> v{p.version}</small>}
            </Link>
            {p.load_error && <p className="wizard-error">Load error: {p.load_error}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TemplatesTab() {
  const [data, setData] = useState<{
    templates: TemplateSummary[];
    user_dir: string;
    default_variant: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [variant, setVariant] = useState<string>("default");
  const [body, setBody] = useState<string>("");
  const [editable, setEditable] = useState<boolean>(false);
  const [bodyLoading, setBodyLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [newVariantName, setNewVariantName] = useState("");

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await templatesApi.list();
      setData(result);
      if (!selected && result.templates.length > 0) {
        const first = result.templates[0]!;
        setSelected(first.name);
        setVariant(first.active || "default");
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const current = data?.templates.find((t) => t.name === selected) ?? null;

  // Load body when (selected, variant) changes.
  useEffect(() => {
    if (!selected || !variant) {
      setBody("");
      setEditable(false);
      return;
    }
    let cancelled = false;
    setBodyLoading(true);
    setStatus(null);
    void (async () => {
      try {
        const text = await templatesApi.read(selected, variant);
        if (!cancelled) {
          setBody(text.body);
          setEditable(text.editable);
        }
      } catch (err) {
        if (!cancelled) setError(errorMessage(err));
      } finally {
        if (!cancelled) setBodyLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected, variant]);

  const onSelectTemplate = (name: string) => {
    setSelected(name);
    const t = data?.templates.find((x) => x.name === name);
    setVariant(t?.active ?? "default");
  };

  const onSave = async () => {
    if (!selected || !variant) return;
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      await templatesApi.write(selected, variant, body);
      setStatus("Saved.");
      await refresh();
      setEditable(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const onSetActive = async () => {
    if (!selected || !variant) return;
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      await templatesApi.setActive(selected, variant);
      setStatus(`Active variant set to ${variant}.`);
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (!selected || !variant || !editable) return;
    if (!window.confirm(`Delete user variant "${variant}" of ${selected}?`)) return;
    setSaving(true);
    setError(null);
    try {
      await templatesApi.remove(selected, variant);
      setStatus(`Deleted ${variant}.`);
      setVariant("default");
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const onCreateVariant = async () => {
    if (!selected) return;
    const name = newVariantName.trim();
    if (!name) return;
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(name)) {
      setError("Variant name must be letters/digits/_/- and start with a letter or digit.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Seed the new variant with the body the user is currently viewing.
      await templatesApi.write(selected, name, body);
      setStatus(`Created variant ${name}.`);
      setNewVariantName("");
      setVariant(name);
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-form templates-form">
      <p className="wizard-step-help">
        Pick a prompt template, choose a variant, and edit. Bundled defaults are read-only; saving
        creates a new variant under your data directory. The active variant is used for new renders.
      </p>

      {loading && <p className="wizard-meta">Loading templates…</p>}
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}

      {data && (
        <div className="templates-layout">
          <aside className="templates-list">
            <h3>Templates</h3>
            <ul>
              {data.templates.map((t) => (
                <li key={t.name}>
                  <button
                    type="button"
                    className={t.name === selected ? "templates-item active" : "templates-item"}
                    onClick={() => onSelectTemplate(t.name)}
                  >
                    <span className="templates-item-name">{t.name}</span>
                    <span className="templates-item-meta">
                      {t.variants.length} {t.variants.length === 1 ? "variant" : "variants"}
                      {t.active !== data.default_variant && (
                        <span className="badge badge-ok"> {t.active}</span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <small className="muted">User dir: {data.user_dir}</small>
          </aside>

          <section className="templates-editor">
            {!current ? (
              <p className="wizard-meta">Select a template to edit.</p>
            ) : (
              <>
                <header className="templates-editor-head">
                  <div>
                    <h3>{current.name}</h3>
                    <p className="provider-card-sub">
                      Active variant: <strong>{current.active}</strong>
                    </p>
                  </div>
                  <div className="templates-editor-actions">
                    <label className="provider-combobox templates-variant-picker">
                      <span className="provider-combobox-label">Variant</span>
                      <select value={variant} onChange={(e) => setVariant(e.target.value)}>
                        {current.variants.map((v) => (
                          <option key={v} value={v}>
                            {v}
                            {current.editable.includes(v) ? "  ·  user" : "  ·  bundled"}
                            {v === current.active ? "  ·  active" : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </header>

                {bodyLoading ? (
                  <p className="wizard-meta">Loading…</p>
                ) : (
                  <textarea
                    className="templates-body"
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    spellCheck={false}
                    rows={18}
                    aria-label={`${current.name} / ${variant} template body`}
                  />
                )}

                {!editable && (
                  <p className="wizard-meta">
                    Bundled variant — read-only. Save below to create an editable user copy.
                  </p>
                )}

                <div className="templates-action-row">
                  {editable ? (
                    <button
                      type="button"
                      className="primary"
                      onClick={() => void onSave()}
                      disabled={saving}
                    >
                      {saving ? "Saving…" : "Save"}
                    </button>
                  ) : (
                    <div className="templates-create-variant">
                      <input
                        type="text"
                        placeholder="new-variant-name"
                        value={newVariantName}
                        onChange={(e) => setNewVariantName(e.target.value)}
                      />
                      <button
                        type="button"
                        className="primary"
                        onClick={() => void onCreateVariant()}
                        disabled={saving || !newVariantName.trim()}
                      >
                        Save as new variant
                      </button>
                    </div>
                  )}
                  {variant !== current.active && (
                    <button type="button" onClick={() => void onSetActive()} disabled={saving}>
                      Make active
                    </button>
                  )}
                  {editable && (
                    <button
                      type="button"
                      onClick={() => void onDelete()}
                      disabled={saving}
                      className="templates-delete"
                    >
                      Delete variant
                    </button>
                  )}
                </div>

                {status && <p className="library-ok">{status}</p>}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function BackupTab() {
  const { data, patch, status, error } = useAppConfig();
  const backup = data?.backup ?? { schedule: "off", retention_days: 30, location: "data/backups" };

  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Default schedule</span>
        <select
          value={backup.schedule}
          onChange={(e) => patch({ backup: { ...backup, schedule: e.target.value } })}
          disabled={!data}
        >
          <option value="off">Off</option>
          <option value="hourly">Hourly</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </select>
      </label>
      <label className="wizard-field">
        <span>Retention (days)</span>
        <input
          type="number"
          min={0}
          value={backup.retention_days}
          onChange={(e) =>
            patch({
              backup: {
                ...backup,
                retention_days: Number.isFinite(Number(e.target.value))
                  ? Number(e.target.value)
                  : backup.retention_days,
              },
            })
          }
          disabled={!data}
        />
      </label>
      <label className="wizard-field">
        <span>Destination</span>
        <input
          type="text"
          value={backup.location}
          onChange={(e) => patch({ backup: { ...backup, location: e.target.value } })}
          disabled={!data}
        />
      </label>
      <ConfigSaveIndicator status={status} error={error} />
    </div>
  );
}

function AppearanceTab() {
  const { mode, setMode, fontFamily, setFontFamily, density, setDensity } = useTheme();

  return (
    <div className="settings-form">
      <fieldset className="wizard-style-mode" aria-label="Theme">
        <legend>Theme</legend>
        {(["light", "dark", "system"] as const).map((t) => (
          <label key={t}>
            <input type="radio" name="theme" checked={mode === t} onChange={() => setMode(t)} />
            <span>{t}</span>
          </label>
        ))}
      </fieldset>
      <label className="wizard-field">
        <span>Font family</span>
        <select
          value={fontFamily}
          onChange={(e) =>
            setFontFamily(e.target.value as "system" | "serif" | "dyslexia")
          }
        >
          <option value="system">System</option>
          <option value="serif">Serif</option>
          <option value="dyslexia">Dyslexia-friendly</option>
        </select>
      </label>
      <label className="wizard-field">
        <span>Density</span>
        <select
          value={density}
          onChange={(e) => setDensity(e.target.value as "comfortable" | "compact")}
        >
          <option value="comfortable">Comfortable</option>
          <option value="compact">Compact</option>
        </select>
      </label>
      <p className="wizard-meta">
        Font family and density apply across the app and persist locally.
      </p>
    </div>
  );
}
