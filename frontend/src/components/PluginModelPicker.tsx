/**
 * Searchable plugin-model picker.
 *
 * Renders an `<input list>` + `<datalist>` so users can type-to-filter
 * the (potentially hundreds of) models advertised by an LLM-provider
 * plugin. Surfaces context-window and per-1k pricing when the selected
 * model resolves in the catalog.
 *
 * The component only handles UI; persistence is the parent's job.
 */

import { useEffect, useMemo, useState } from "react";

import { ApiError, type PluginModelInfo, pluginsApi } from "../api/library";

interface Props {
  pluginId: string;
  label: string;
  description?: string;
  required?: boolean;
  value: string;
  onChange: (next: string) => void;
}

export function PluginModelPicker({
  pluginId,
  label,
  description,
  required,
  value,
  onChange,
}: Props) {
  const [models, setModels] = useState<PluginModelInfo[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useMemo(
    () => async () => {
      setLoading(true);
      setLoadErr(null);
      try {
        const rows = await pluginsApi.listModels(pluginId);
        setModels(rows);
      } catch (err) {
        setLoadErr(err instanceof ApiError ? err.message : String(err));
        setModels([]);
      } finally {
        setLoading(false);
      }
    },
    [pluginId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const match = models?.find((m) => m.id === value);
  const groups = useMemo(() => groupModels(models ?? []), [models]);
  // Surface the saved value as a selectable option even if the catalog
  // hasn't loaded yet (or no longer lists it) — otherwise the select
  // would silently snap to whatever first option the browser picks.
  const includesValue = !value || (models?.some((m) => m.id === value) ?? false);

  return (
    <label>
      <span>
        {label} {required && <em>*</em>}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading && !models}
      >
        {!value && <option value="">— Select a model —</option>}
        {!includesValue && (
          <option value={value}>
            {value} {loading ? "(loading catalog…)" : "(not in catalog)"}
          </option>
        )}
        {groups.map(({ provider, items }) => (
          <optgroup key={provider} label={provider}>
            {items.map((m) => (
              <option key={m.id} value={m.id}>
                {optionLabel(m)}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {description && <small>{description}</small>}
      {loadErr && (
        <small className="library-error">
          Couldn't load models: {loadErr}.{" "}
          <button type="button" onClick={() => void load()}>
            Retry
          </button>
        </small>
      )}
      {match && match.context_window > 0 && (
        <small>
          {match.name} · context {match.context_window.toLocaleString()}
          {match.input_cost_per_1k !== null && match.output_cost_per_1k !== null
            ? ` · $${match.input_cost_per_1k.toFixed(4)}/$${match.output_cost_per_1k.toFixed(4)} per 1K in/out`
            : ""}
        </small>
      )}
    </label>
  );
}

function optionLabel(m: PluginModelInfo): string {
  const tail = m.id.includes("/") ? m.id.split("/").slice(1).join("/") : m.id;
  const base = m.name && m.name !== m.id ? `${m.name} (${tail})` : tail;
  return base + formatPriceTag(m);
}

/** Compact " · $3.00/$15.00 per 1M" suffix; "" when no pricing, " · free" when zero. */
function formatPriceTag(m: PluginModelInfo): string {
  const inP = m.input_cost_per_1k;
  const outP = m.output_cost_per_1k;
  if (inP == null && outP == null) return "";
  if ((inP ?? 0) === 0 && (outP ?? 0) === 0) return " · free";
  const fmt = (v: number | null) => (v == null ? "?" : `$${(v * 1000).toFixed(2)}`);
  return ` · ${fmt(inP)}/${fmt(outP)} per 1M`;
}

function groupModels(
  models: PluginModelInfo[],
): { provider: string; items: PluginModelInfo[] }[] {
  const buckets = new Map<string, PluginModelInfo[]>();
  for (const m of models) {
    const provider = m.id.includes("/") ? m.id.split("/", 1)[0]! : "other";
    const bucket = buckets.get(provider);
    if (bucket) bucket.push(m);
    else buckets.set(provider, [m]);
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([provider, items]) => ({
      provider,
      items: items.slice().sort((a, b) => a.id.localeCompare(b.id)),
    }));
}
