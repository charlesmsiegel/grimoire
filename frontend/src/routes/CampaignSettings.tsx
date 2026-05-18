/**
 * Per-campaign settings. Tabs follow spec 14 §Per-campaign settings:
 * General, Model routing, ImageGen, Mechanics, Storage, Advanced.
 *
 * Routing / ImageGen / Storage / Advanced persist via dedicated PUT endpoints
 * (`/api/campaigns/{id}/routing` etc.). Each tab fetches its current value on
 * mount and auto-saves after a short debounce, with a "Saved" indicator next
 * to the form. General + Mechanics still use `PATCH /campaigns/{id}` (the
 * latter has its own explicit Save button because it changes a column the
 * orchestrator reads on every turn).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import {
  type MechanicsModuleSummary,
  type PluginSummary,
  fetchInstalledMechanics,
  fetchInstalledPlugins,
  patchCampaign,
} from "../api/wizard";

type Tab = "general" | "routing" | "imagegen" | "mechanics" | "storage" | "advanced";

const TABS: { id: Tab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "routing", label: "Model routing" },
  { id: "imagegen", label: "ImageGen" },
  { id: "mechanics", label: "Mechanics" },
  { id: "storage", label: "Storage" },
  { id: "advanced", label: "Advanced" },
];

interface CampaignRecord {
  id: string;
  name?: string;
  description?: string | null;
  mechanics_module?: string | null;
  style_guide_id?: string | null;
  image_preset_id?: string | null;
  inline_style_guide?: string | null;
  content_boundaries?: string | null;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function CampaignSettings() {
  const { campaignId } = useParams();
  const [tab, setTab] = useState<Tab>("general");
  const [campaign, setCampaign] = useState<CampaignRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const data = await api.get<CampaignRecord>(
          `/api/campaigns/${encodeURIComponent(campaignId)}`,
        );
        if (!cancelled) {
          setCampaign(data);
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
  }, [campaignId]);

  if (!campaignId) return null;

  return (
    <section className="route campaign-settings" aria-labelledby="campaign-settings-heading">
      <header>
        <h2 id="campaign-settings-heading">Campaign settings: {campaignId}</h2>
      </header>

      <nav className="tab-bar" aria-label="Campaign settings tabs">
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

      {loading && <p className="wizard-meta">Loading…</p>}
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}

      {campaign && (
        <div className="tab-panel">
          {tab === "general" && <GeneralTab campaign={campaign} onUpdate={setCampaign} />}
          {tab === "routing" && <RoutingTab campaignId={campaignId} />}
          {tab === "imagegen" && <ImageGenTab campaignId={campaignId} />}
          {tab === "mechanics" && <MechanicsTab campaign={campaign} onUpdate={setCampaign} />}
          {tab === "storage" && <StorageTab campaignId={campaignId} />}
          {tab === "advanced" && <AdvancedTab campaignId={campaignId} />}
        </div>
      )}
    </section>
  );
}

function GeneralTab({
  campaign,
  onUpdate,
}: {
  campaign: CampaignRecord;
  onUpdate: (next: CampaignRecord) => void;
}) {
  const [name, setName] = useState(campaign.name ?? "");
  const [description, setDescription] = useState(campaign.description ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await patchCampaign(campaign.id, { name, description });
      onUpdate({ ...campaign, name, description });
      setSavedAt(Date.now());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      className="settings-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label className="wizard-field">
        <span>Name</span>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="wizard-field">
        <span>Description</span>
        <textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}
      {savedAt && <p className="wizard-meta">Saved.</p>}
      <button type="submit" className="primary" disabled={saving}>
        {saving ? "Saving…" : "Save"}
      </button>
    </form>
  );
}

const TASKS = [
  "main",
  "drift_check",
  "extractor",
  "npc_tick",
  "scene_summary",
  "running_summary",
  "validation",
] as const;

type SaveStatus = "idle" | "saving" | "saved" | "error";

/**
 * Auto-save hook: PUT the latest value after a short debounce. Exposes a
 * "Saved" / "Saving…" / error indicator alongside the form so users get
 * feedback without an explicit submit button. Saves on the next tick after
 * the first GET completes (we wait for `ready` to flip true before we'll
 * trigger any PUT).
 */
function useAutoSavedResource<T>(
  campaignId: string | undefined,
  path: string,
  initial: T,
): {
  value: T;
  setValue: (next: T | ((prev: T) => T)) => void;
  status: SaveStatus;
  error: string | null;
  ready: boolean;
} {
  const [value, setValueState] = useState<T>(initial);
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const dirty = useRef(false);
  const lastSent = useRef<string>("");

  // Initial load
  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    setReady(false);
    setError(null);
    void (async () => {
      try {
        const data = await api.get<T>(`/api/campaigns/${encodeURIComponent(campaignId)}${path}`);
        if (!cancelled) {
          setValueState(data);
          lastSent.current = JSON.stringify(data);
          setReady(true);
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setReady(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId, path]);

  // Debounced save
  useEffect(() => {
    if (!campaignId || !ready) return;
    if (!dirty.current) return;
    const serialized = JSON.stringify(value);
    if (serialized === lastSent.current) return;
    const handle = window.setTimeout(() => {
      void (async () => {
        setStatus("saving");
        setError(null);
        try {
          await api.put(`/api/campaigns/${encodeURIComponent(campaignId)}${path}`, value);
          lastSent.current = serialized;
          setStatus("saved");
        } catch (err) {
          setError(errorMessage(err));
          setStatus("error");
        }
      })();
    }, 400);
    return () => window.clearTimeout(handle);
  }, [campaignId, path, value, ready]);

  const setValue = useCallback((next: T | ((prev: T) => T)) => {
    dirty.current = true;
    setValueState(next);
  }, []);

  return { value, setValue, status, error, ready };
}

function SaveIndicator({ status, error }: { status: SaveStatus; error: string | null }) {
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

interface RoutingValue {
  llm: Record<string, string>;
  embedding: Record<string, string>;
}

function RoutingTab({ campaignId }: { campaignId: string }) {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [pluginsLoading, setPluginsLoading] = useState(true);
  const [pluginsError, setPluginsError] = useState<string | null>(null);
  const { value, setValue, status, error, ready } = useAutoSavedResource<RoutingValue>(
    campaignId,
    "/routing",
    { llm: {}, embedding: {} },
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchInstalledPlugins();
        if (!cancelled) {
          setPlugins(data);
          setPluginsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setPluginsError(errorMessage(err));
          setPluginsLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const llmPlugins = plugins.filter((p) => p.kind === "llm_provider");
  const embedPlugins = plugins.filter((p) => p.kind === "embedding_provider");

  const updateLlm = (task: string, plugin: string) =>
    setValue((prev) => {
      const next = { ...prev.llm };
      if (plugin) next[task] = plugin;
      else delete next[task];
      return { ...prev, llm: next };
    });
  const updateEmbedding = (task: string, plugin: string) =>
    setValue((prev) => {
      const next = { ...prev.embedding };
      if (plugin) next[task] = plugin;
      else delete next[task];
      return { ...prev, embedding: next };
    });

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Route each task to an installed provider. Empty falls through to the app-wide default for
        that task. Changes save automatically.
      </p>
      {pluginsLoading && <p className="wizard-meta">Loading providers…</p>}
      {pluginsError && <p className="wizard-error">{pluginsError}</p>}
      {!ready && <p className="wizard-meta">Loading saved routing…</p>}
      <table className="routing-table">
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">LLM provider</th>
            <th scope="col">Embedding provider</th>
          </tr>
        </thead>
        <tbody>
          {TASKS.map((task) => (
            <tr key={task}>
              <th scope="row">{task}</th>
              <td>
                <select
                  value={value.llm[task] ?? ""}
                  onChange={(e) => updateLlm(task, e.target.value)}
                  disabled={!ready}
                >
                  <option value="">(app default)</option>
                  {llmPlugins.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name ?? p.id}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <select
                  value={value.embedding[task] ?? ""}
                  onChange={(e) => updateEmbedding(task, e.target.value)}
                  disabled={!ready}
                >
                  <option value="">(app default)</option>
                  {embedPlugins.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name ?? p.id}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}

interface ImageGenValue {
  backend: string | null;
  preset: string | null;
  sampler_defaults: unknown;
}

function ImageGenTab({ campaignId }: { campaignId: string }) {
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

  // Sampler defaults is stored as arbitrary JSON; the input edits a string and
  // we round-trip it through JSON.parse on save when possible so structured
  // configs can be expressed when needed.
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
          onChange={(e) =>
            setValue((prev) => ({ ...prev, backend: e.target.value || null }))
          }
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

function MechanicsTab({
  campaign,
  onUpdate,
}: {
  campaign: CampaignRecord;
  onUpdate: (next: CampaignRecord) => void;
}) {
  const [modules, setModules] = useState<MechanicsModuleSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(campaign.mechanics_module ?? null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetchInstalledMechanics().then(setModules);
  }, []);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await patchCampaign(campaign.id, { mechanics: selected });
      onUpdate({ ...campaign, mechanics_module: selected });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Active mechanics module. Switching modules does not migrate existing sheets.
      </p>
      <label className="wizard-field">
        <span>Module</span>
        <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value || null)}>
          <option value="">No mechanics (narrative only)</option>
          {modules.map((m) => (
            <option key={m.id} value={m.id} disabled={Boolean(m.load_error)}>
              {m.name ?? m.id}
              {m.load_error ? " — load error" : ""}
            </option>
          ))}
        </select>
      </label>
      {error && <p className="wizard-error">{error}</p>}
      <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

interface StorageValue {
  schedule: string;
  retention_days: number;
}

function StorageTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<StorageValue>(
    campaignId,
    "/storage",
    { schedule: "off", retention_days: 30 },
  );

  return (
    <div className="settings-form">
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-field">
        <span>Backup schedule</span>
        <select
          value={value.schedule}
          onChange={(e) => setValue((prev) => ({ ...prev, schedule: e.target.value }))}
          disabled={!ready}
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
          min={1}
          value={value.retention_days}
          onChange={(e) =>
            setValue((prev) => ({
              ...prev,
              retention_days: Number.isFinite(Number(e.target.value))
                ? Number(e.target.value)
                : prev.retention_days,
            }))
          }
          disabled={!ready}
        />
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}

interface AdvancedValue {
  debug_log: boolean;
  per_task_prompts: Record<string, string>;
}

function AdvancedTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<AdvancedValue>(
    campaignId,
    "/advanced",
    { debug_log: false, per_task_prompts: {} },
  );

  const setPromptFor = (task: string, text: string) =>
    setValue((prev) => {
      const next = { ...prev.per_task_prompts };
      if (text) next[task] = text;
      else delete next[task];
      return { ...prev, per_task_prompts: next };
    });

  return (
    <div className="settings-form">
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-toggle">
        <input
          type="checkbox"
          checked={value.debug_log}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, debug_log: e.target.checked }))
          }
          disabled={!ready}
        />
        <span>Verbose debug log for this campaign</span>
      </label>
      <label className="wizard-field">
        <span>Per-task system prompt override (main)</span>
        <textarea
          rows={6}
          value={value.per_task_prompts.main ?? ""}
          onChange={(e) => setPromptFor("main", e.target.value)}
          placeholder="Override the main-task system prompt for this campaign."
          disabled={!ready}
        />
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
