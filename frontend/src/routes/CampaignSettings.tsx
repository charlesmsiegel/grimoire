/**
 * Per-campaign settings. Tabs follow spec 14 §Per-campaign settings:
 * General, Model routing, ImageGen, Mechanics, Storage, Advanced.
 *
 * Concrete editors for routing tables and plugin configs are out of scope for
 * task 35 — each tab renders the form scaffolding and a stable structure
 * downstream tabs can extend. The General and Mechanics tabs persist via
 * `PATCH /campaigns/{id}`; other tabs surface their inputs as local-only state
 * marked as "not yet persisted" so users can see the surface area without
 * silently losing data.
 */

import { useEffect, useState } from "react";
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
          {tab === "routing" && <RoutingTab />}
          {tab === "imagegen" && <ImageGenTab />}
          {tab === "mechanics" && <MechanicsTab campaign={campaign} onUpdate={setCampaign} />}
          {tab === "storage" && <StorageTab />}
          {tab === "advanced" && <AdvancedTab />}
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

function RoutingTab() {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});

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

  const llmPlugins = plugins.filter((p) => p.kind === "llm_provider");
  const embedPlugins = plugins.filter((p) => p.kind === "embedding_provider");

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Route each task to an installed provider. Empty falls through to the app-wide default for
        that task.
      </p>
      {loading && <p className="wizard-meta">Loading providers…</p>}
      {error && <p className="wizard-error">{error}</p>}
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
                  value={overrides[`llm:${task}`] ?? ""}
                  onChange={(e) => setOverrides((o) => ({ ...o, [`llm:${task}`]: e.target.value }))}
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
                  value={overrides[`embed:${task}`] ?? ""}
                  onChange={(e) =>
                    setOverrides((o) => ({ ...o, [`embed:${task}`]: e.target.value }))
                  }
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
      <p className="wizard-meta">
        Persistence ships with the routing-table API endpoint; the form surfaces the structure now
        so a future task only has to wire the PUT call.
      </p>
    </div>
  );
}

function ImageGenTab() {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [backend, setBackend] = useState<string>("");
  const [preset, setPreset] = useState<string>("");
  const [sampler, setSampler] = useState<string>("");

  useEffect(() => {
    void fetchInstalledPlugins().then((data) => setPlugins(data));
  }, []);

  const backends = plugins.filter((p) => p.kind === "imagegen_backend");

  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Backend</span>
        <select value={backend} onChange={(e) => setBackend(e.target.value)}>
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
          value={preset}
          onChange={(e) => setPreset(e.target.value)}
          placeholder="oil-painting"
        />
      </label>
      <label className="wizard-field">
        <span>Sampler defaults</span>
        <input
          type="text"
          value={sampler}
          onChange={(e) => setSampler(e.target.value)}
          placeholder="DPM++ 2M Karras, 25 steps"
        />
      </label>
      <p className="wizard-meta">Backend-specific config UIs follow in task 34's Images view.</p>
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

function StorageTab() {
  const [schedule, setSchedule] = useState("daily");
  const [retention, setRetention] = useState("30");
  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Backup schedule</span>
        <select value={schedule} onChange={(e) => setSchedule(e.target.value)}>
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
          value={retention}
          onChange={(e) => setRetention(e.target.value)}
        />
      </label>
      <p className="wizard-meta">Backup runner ships in the operational tooling pass.</p>
    </div>
  );
}

function AdvancedTab() {
  const [debug, setDebug] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState("");
  return (
    <div className="settings-form">
      <label className="wizard-toggle">
        <input type="checkbox" checked={debug} onChange={(e) => setDebug(e.target.checked)} />
        <span>Verbose debug log for this campaign</span>
      </label>
      <label className="wizard-field">
        <span>Per-task system prompt override (main)</span>
        <textarea
          rows={6}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="Override the main-task system prompt for this campaign."
        />
      </label>
      <p className="wizard-meta">
        Per-task prompt overrides land alongside Observability's debug log surface.
      </p>
    </div>
  );
}
