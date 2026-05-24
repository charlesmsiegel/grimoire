import { useCallback, useEffect, useState } from "react";

import { pluginsApi, type PluginModelInfo } from "../../../api/library";
import {
  type PluginSummary,
  fetchInstalledPlugins,
} from "../../../api/wizard";
import { cleanRoutes, type RoutingValue } from "../../campaignRouting";
import { SaveIndicator } from "./SaveIndicator";
import { errorMessage, useAutoSavedResource } from "./shared";

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

type RoutingKind = "llm" | "embedding" | "imagegen";

const PLUGIN_KIND_FOR_ROUTING: Record<RoutingKind, string> = {
  llm: "llm_provider",
  embedding: "embedding_provider",
  imagegen: "imagegen_backend",
};

function parseRoute(raw: string): { provider: string; model: string } {
  const idx = raw.indexOf(".");
  if (idx <= 0) return { provider: raw, model: "" };
  return { provider: raw.slice(0, idx), model: raw.slice(idx + 1) };
}

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

interface TiersValue {
  heavy: string | null;
  light: string | null;
  embedding: string | null;
}

export function RoutingTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<TiersValue>(
    campaignId,
    "/tiers",
    { heavy: null, light: null, embedding: null },
  );

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Heavy handles generation (narrator, summaries, rewrites). Light
        handles classification and short transforms (drift checks,
        scene-break, translate). Embedding handles vector embeddings.
        Leave a field blank to use the app-wide default.
      </p>
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-field">
        <span>Heavy model</span>
        <input
          type="text"
          placeholder="e.g. deepseek.deepseek-v4-pro"
          value={value.heavy ?? ""}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, heavy: e.target.value.trim() || null }))
          }
          disabled={!ready}
        />
      </label>
      <label className="wizard-field">
        <span>Light model</span>
        <input
          type="text"
          placeholder="e.g. deepseek.deepseek-v4-flash"
          value={value.light ?? ""}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, light: e.target.value.trim() || null }))
          }
          disabled={!ready}
        />
      </label>
      <label className="wizard-field">
        <span>Embedding model</span>
        <input
          type="text"
          placeholder="e.g. voyage.voyage-3"
          value={value.embedding ?? ""}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, embedding: e.target.value.trim() || null }))
          }
          disabled={!ready}
        />
      </label>
      <SaveIndicator status={status} error={error} />
      <details className="routing-advanced">
        <summary>Advanced: per-task overrides</summary>
        <RoutingTabAdvanced campaignId={campaignId} />
      </details>
    </div>
  );
}

function RoutingTabAdvanced({ campaignId }: { campaignId: string }) {
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
