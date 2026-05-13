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

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import {
  type MechanicsModuleSummary,
  type PluginSummary,
  fetchInstalledMechanics,
  fetchInstalledPlugins,
} from "../api/wizard";
import { useTheme } from "../state/useTheme";

type Tab = "library" | "providers" | "mechanics" | "plugins" | "backup" | "appearance";

const TABS: { id: Tab; label: string }[] = [
  { id: "library", label: "Library" },
  { id: "providers", label: "Providers" },
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
        {tab === "mechanics" && <MechanicsInventoryTab />}
        {tab === "plugins" && <PluginsInventoryTab />}
        {tab === "backup" && <BackupTab />}
        {tab === "appearance" && <AppearanceTab />}
      </div>
    </section>
  );
}

function LibraryPathTab() {
  const [path, setPath] = useState("data/library");
  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Library path</span>
        <input type="text" value={path} onChange={(e) => setPath(e.target.value)} />
        <small>Filesystem directory scanned for settings, style guides, presets.</small>
      </label>
      <p className="wizard-meta">
        Path is read from <code>data/config/app.yaml</code>; in-app editing ships with the
        configuration editor.
      </p>
    </div>
  );
}

function ProvidersTab() {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchInstalledPlugins();
        if (!cancelled) {
          setPlugins(data);
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

  const sections: { kind: string; label: string }[] = [
    { kind: "llm_provider", label: "LLM providers" },
    { kind: "embedding_provider", label: "Embedding providers" },
    { kind: "imagegen_backend", label: "ImageGen backends" },
    { kind: "export_adapter", label: "Export adapters" },
  ];

  return (
    <div className="settings-form">
      {loading && <p className="wizard-meta">Loading…</p>}
      {error && <p className="wizard-error">{error}</p>}
      {sections.map(({ kind, label }) => {
        const list = plugins.filter((p) => p.kind === kind);
        return (
          <section key={kind} className="provider-section">
            <h3>{label}</h3>
            {list.length === 0 ? (
              <p className="wizard-meta">None installed.</p>
            ) : (
              <ul className="provider-list">
                {list.map((p) => (
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
      })}
      <p className="wizard-meta">
        Click a provider to enter its API key, default model, and other settings. The form is
        rendered from the plugin's <code>config_schema</code> and saved to the OS keyring where
        possible.
      </p>
    </div>
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

function BackupTab() {
  const [schedule, setSchedule] = useState("daily");
  const [destination, setDestination] = useState("data/backups");
  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Default schedule</span>
        <select value={schedule} onChange={(e) => setSchedule(e.target.value)}>
          <option value="off">Off</option>
          <option value="hourly">Hourly</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </select>
      </label>
      <label className="wizard-field">
        <span>Destination</span>
        <input type="text" value={destination} onChange={(e) => setDestination(e.target.value)} />
      </label>
    </div>
  );
}

function AppearanceTab() {
  const { mode, setMode } = useTheme();
  const [fontFamily, setFontFamily] = useState("system");
  const [density, setDensity] = useState("comfortable");

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
        <select value={fontFamily} onChange={(e) => setFontFamily(e.target.value)}>
          <option value="system">System</option>
          <option value="serif">Serif</option>
          <option value="dyslexia">Dyslexia-friendly</option>
        </select>
      </label>
      <label className="wizard-field">
        <span>Density</span>
        <select value={density} onChange={(e) => setDensity(e.target.value)}>
          <option value="comfortable">Comfortable</option>
          <option value="compact">Compact</option>
        </select>
      </label>
      <p className="wizard-meta">
        Font family + density wire into the layout in a follow-up theming pass.
      </p>
    </div>
  );
}
