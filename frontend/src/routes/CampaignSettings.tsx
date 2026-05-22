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
import { campaignApi, type MissingSheet } from "../api/campaign";
import { mechanicsApi, pluginsApi, type PluginModelInfo } from "../api/library";
import {
  type MechanicsModuleSummary,
  type PluginSummary,
  fetchInstalledMechanics,
  fetchInstalledPlugins,
  patchCampaign,
} from "../api/wizard";
import { BulkSheetCreation } from "./campaign/BulkSheetCreation";
import { cleanRoutes, type RoutingValue } from "./campaignRouting";

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
          {/* key={campaign.id} on the campaign-scoped tabs so switching
              campaigns remounts the panel — otherwise the local draft /
              dirty / editing state from the previous campaign bleeds in
              and the user sees the wrong unsaved edits. */}
          {tab === "general" && (
            <GeneralTab key={campaign.id} campaign={campaign} onUpdate={setCampaign} />
          )}
          {tab === "routing" && <RoutingTab key={campaignId} campaignId={campaignId} />}
          {tab === "imagegen" && <ImageGenTab key={campaignId} campaignId={campaignId} />}
          {tab === "mechanics" && (
            <MechanicsTab key={campaign.id} campaign={campaign} onUpdate={setCampaign} />
          )}
          {tab === "storage" && <StorageTab key={campaignId} campaignId={campaignId} />}
          {tab === "advanced" && <AdvancedTab key={campaignId} campaignId={campaignId} />}
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

const LLM_TASKS = [
  "main",
  "drift_check",
  "extractor",
  "npc_tick",
  "scene_summary",
  "running_summary",
  "validation",
] as const;

const EMBEDDING_TASKS = [
  "embed:context",
  "library.embed",
] as const;

const IMAGEGEN_TASKS = [
  "scene_open",
  "portrait",
  "location",
  "combat",
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
  transformForSave?: (value: T) => T,
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
          const seed = transformForSave ? transformForSave(data) : data;
          lastSent.current = JSON.stringify(seed);
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
  }, [campaignId, path, transformForSave]);

  // Debounced save
  useEffect(() => {
    if (!campaignId || !ready) return;
    if (!dirty.current) return;
    const payload = transformForSave ? transformForSave(value) : value;
    const serialized = JSON.stringify(payload);
    if (serialized === lastSent.current) return;
    const handle = window.setTimeout(() => {
      void (async () => {
        setStatus("saving");
        setError(null);
        try {
          await api.put(`/api/campaigns/${encodeURIComponent(campaignId)}${path}`, payload);
          lastSent.current = serialized;
          setStatus("saved");
        } catch (err) {
          setError(errorMessage(err));
          setStatus("error");
        }
      })();
    }, 400);
    return () => window.clearTimeout(handle);
  }, [campaignId, path, value, ready, transformForSave]);

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

type RoutingKind = "llm" | "embedding" | "imagegen";

const PLUGIN_KIND_FOR_ROUTING: Record<RoutingKind, string> = {
  llm: "llm_provider",
  embedding: "embedding_provider",
  imagegen: "imagegen_backend",
};

/** Parse a "provider.model" string into its two halves. */
function parseRoute(raw: string): { provider: string; model: string } {
  const idx = raw.indexOf(".");
  if (idx <= 0) return { provider: raw, model: "" };
  return { provider: raw.slice(0, idx), model: raw.slice(idx + 1) };
}

/**
 * Hook that fetches the model list for a plugin and caches it. The route
 * picker calls it once per visible provider; results are dropped when the
 * provider id changes.
 */
function usePluginModels(pluginId: string | null): {
  models: PluginModelInfo[];
  loading: boolean;
  error: string | null;
} {
  const [models, setModels] = useState<PluginModelInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pluginId) {
      setModels([]);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const rows = await pluginsApi.listModels(pluginId);
        if (!cancelled) {
          setModels(rows);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          // Plugins that can't list models yet (e.g. unconfigured) are not
          // a fatal error; the user can still type a model id manually.
          setError(errorMessage(err));
          setModels([]);
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pluginId]);

  return { models, loading, error };
}

interface RouteRowProps {
  task: string;
  value: string;
  providers: PluginSummary[];
  ready: boolean;
  onChange: (next: string) => void;
}

function RouteRow({ task, value, providers, ready, onChange }: RouteRowProps) {
  const parsed = value ? parseRoute(value) : { provider: "", model: "" };
  const { models, loading: modelsLoading } = usePluginModels(parsed.provider || null);

  const updateProvider = (provider: string) => {
    if (!provider) {
      onChange("");
      return;
    }
    // Reset model when the provider changes — the previous model is unlikely
    // to be valid for the new provider.
    onChange(`${provider}.`);
  };
  const updateModel = (model: string) => {
    if (!parsed.provider) return;
    onChange(model ? `${parsed.provider}.${model}` : "");
  };

  return (
    <tr>
      <th scope="row">{task}</th>
      <td>
        <select
          value={parsed.provider}
          onChange={(e) => updateProvider(e.target.value)}
          disabled={!ready}
          aria-label={`${task} provider`}
        >
          <option value="">(app default)</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name ?? p.id}
            </option>
          ))}
        </select>
      </td>
      <td>
        {parsed.provider ? (
          modelsLoading ? (
            <small className="wizard-meta">Loading…</small>
          ) : models.length > 0 ? (
            <select
              value={parsed.model}
              onChange={(e) => updateModel(e.target.value)}
              disabled={!ready}
              aria-label={`${task} model`}
            >
              <option value="">(pick a model)</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name || m.id}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={parsed.model}
              onChange={(e) => updateModel(e.target.value)}
              placeholder="model id"
              disabled={!ready}
              aria-label={`${task} model`}
            />
          )
        ) : (
          <span className="wizard-meta">—</span>
        )}
      </td>
    </tr>
  );
}

interface RoutingSectionProps {
  title: string;
  help: string;
  kind: RoutingKind;
  tasks: readonly string[];
  routes: Record<string, string>;
  providers: PluginSummary[];
  ready: boolean;
  onChange: (task: string, next: string) => void;
}

function RoutingSection({
  title,
  help,
  kind,
  tasks,
  routes,
  providers,
  ready,
  onChange,
}: RoutingSectionProps) {
  return (
    <fieldset className="routing-section">
      <legend>{title}</legend>
      <p className="wizard-step-help">{help}</p>
      <table className="routing-table">
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">Provider</th>
            <th scope="col">Model</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <RouteRow
              key={`${kind}-${task}`}
              task={task}
              value={routes[task] ?? ""}
              providers={providers}
              ready={ready}
              onChange={(next) => onChange(task, next)}
            />
          ))}
        </tbody>
      </table>
    </fieldset>
  );
}

function RoutingTab({ campaignId }: { campaignId: string }) {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [pluginsLoading, setPluginsLoading] = useState(true);
  const [pluginsError, setPluginsError] = useState<string | null>(null);
  const { value, setValue, status, error, ready } = useAutoSavedResource<RoutingValue>(
    campaignId,
    "/routing",
    { llm: {}, embedding: {}, imagegen: {} },
    cleanRoutes,
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

  const providersFor = useCallback(
    (kind: RoutingKind): PluginSummary[] =>
      plugins.filter((p) => p.kind === PLUGIN_KIND_FOR_ROUTING[kind]),
    [plugins],
  );

  const updateRoute = (kind: RoutingKind, task: string, next: string) =>
    setValue((prev) => {
      const block = { ...(prev[kind] ?? {}) };
      // A trailing "provider." (no model picked yet) is incomplete — keep
      // it locally so the provider dropdown stays selected, but the PUT
      // is run through `cleanRoutes` which drops trailing-dot entries
      // (the backend's ``Route.parse`` rejects them with 422).
      if (next === "") delete block[task];
      else block[task] = next;
      return { ...prev, [kind]: block };
    });

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Route each task to an installed provider and one of its advertised models. Empty falls
        through to the app-wide default. Changes save automatically.
      </p>
      {pluginsLoading && <p className="wizard-meta">Loading providers…</p>}
      {pluginsError && <p className="wizard-error">{pluginsError}</p>}
      {!ready && <p className="wizard-meta">Loading saved routing…</p>}
      <RoutingSection
        title="LLM tasks"
        help="Completion / streaming tasks routed to an LLM provider plugin."
        kind="llm"
        tasks={LLM_TASKS}
        routes={value.llm ?? {}}
        providers={providersFor("llm")}
        ready={ready}
        onChange={(task, next) => updateRoute("llm", task, next)}
      />
      <RoutingSection
        title="Embedding tasks"
        help="Vector embeddings (context retrieval, library search) routed to an embedding plugin."
        kind="embedding"
        tasks={EMBEDDING_TASKS}
        routes={value.embedding ?? {}}
        providers={providersFor("embedding")}
        ready={ready}
        onChange={(task, next) => updateRoute("embedding", task, next)}
      />
      <RoutingSection
        title="Image generation tasks"
        help="Per-task image backends. The ImageGen service consults these when the orchestrator names a task; otherwise it falls back to the active backend on the ImageGen tab."
        kind="imagegen"
        tasks={IMAGEGEN_TASKS}
        routes={value.imagegen ?? {}}
        providers={providersFor("imagegen")}
        ready={ready}
        onChange={(task, next) => updateRoute("imagegen", task, next)}
      />
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

type PreRollPolicy = "never" | "always" | "high_stakes";

const PRE_ROLL_POLICIES: { value: PreRollPolicy; label: string }[] = [
  { value: "never", label: "Never confirm" },
  { value: "always", label: "Always confirm" },
  { value: "high_stakes", label: "High-stakes rolls only" },
];

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
  const [bulk, setBulk] = useState<{
    moduleId: string;
    themeCss: string | null;
    missing: MissingSheet[];
  } | null>(null);
  const [confirmPolicy, setConfirmPolicy] = useState<PreRollPolicy>("never");
  const [policySaving, setPolicySaving] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);

  useEffect(() => {
    void fetchInstalledMechanics().then(setModules);
  }, []);

  // Best-effort: read existing orchestrator config so the dropdown reflects
  // the persisted value. The endpoint is best-effort here — failures fall
  // back to "never" silently so the rest of the tab stays usable.
  useEffect(() => {
    void api
      .get<{ pre_roll?: { confirm_before_executing?: PreRollPolicy } }>(
        `/api/campaigns/${encodeURIComponent(campaign.id)}/orchestrator-config`,
      )
      .then((cfg) => {
        const v = cfg?.pre_roll?.confirm_before_executing;
        if (v === "never" || v === "always" || v === "high_stakes") {
          setConfirmPolicy(v);
        }
      })
      .catch(() => undefined);
  }, [campaign.id]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await campaignApi.switchMechanics(campaign.id, selected);
      onUpdate({ ...campaign, mechanics_module: result.current });
      if (result.current && result.missing_sheets.length > 0) {
        // Best-effort look-up of the inline theme.css; fall back to null on
        // any failure (the wizard tolerates the absence).
        let themeCss: string | null = null;
        try {
          const installed = await mechanicsApi.listInstalled();
          themeCss = installed.find((m) => m.manifest.id === result.current)?.theme_css ?? null;
        } catch {
          themeCss = null;
        }
        setBulk({
          moduleId: result.current,
          themeCss,
          missing: result.missing_sheets,
        });
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  async function savePolicy() {
    setPolicySaving(true);
    setPolicyError(null);
    try {
      await api.patch<unknown>(
        `/api/campaigns/${encodeURIComponent(campaign.id)}/orchestrator-config`,
        { pre_roll: { confirm_before_executing: confirmPolicy } },
      );
    } catch (err) {
      setPolicyError(errorMessage(err));
    } finally {
      setPolicySaving(false);
    }
  }

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Active mechanics module. Switching modules preserves existing sheets under their old module
        id and opens a wizard to create new sheets where needed.
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
        {saving ? "Switching…" : "Save"}
      </button>

      <hr className="wizard-divider" />

      <p className="wizard-step-help">
        Pre-roll confirmation: when to interrupt a turn and ask the player to accept, modify, or
        decline the proposed dice rolls before resolving them.
      </p>
      <label className="wizard-field">
        <span>Confirm before executing</span>
        <select
          value={confirmPolicy}
          onChange={(e) => setConfirmPolicy(e.target.value as PreRollPolicy)}
        >
          {PRE_ROLL_POLICIES.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      {policyError && <p className="wizard-error">{policyError}</p>}
      <button
        type="button"
        className="primary"
        disabled={policySaving}
        onClick={() => void savePolicy()}
      >
        {policySaving ? "Saving…" : "Save policy"}
      </button>

      {bulk && (
        <BulkSheetCreation
          campaignId={campaign.id}
          moduleId={bulk.moduleId}
          themeCss={bulk.themeCss}
          missing={bulk.missing}
          onClose={() => setBulk(null)}
        />
      )}
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
