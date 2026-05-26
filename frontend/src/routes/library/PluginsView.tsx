import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Route, Routes, useParams } from "react-router-dom";

import { ApiError, type PluginKind, type PluginManifest, pluginsApi } from "../../api/library";
import { useResource } from "../../api/useResource";
import type { GGUFInfo } from "../../components/FilePathPicker";
import { SchemaField } from "../../components/SchemaField";
import { type JsonSchema, initialDraftFromSchema } from "../../components/schemaForm";
import { AsyncBoundary } from "./AsyncBoundary";

const KINDS: { kind: PluginKind | "all"; label: string }[] = [
  { kind: "all", label: "All" },
  { kind: "llm_provider", label: "LLM providers" },
  { kind: "embedding_provider", label: "Embedding providers" },
  { kind: "imagegen_backend", label: "ImageGen backends" },
  { kind: "export_adapter", label: "Export adapters" },
];

export function PluginsView() {
  return (
    <Routes>
      <Route index element={<PluginsList />} />
      <Route path=":pluginId" element={<PluginDetail />} />
    </Routes>
  );
}

function PluginsList() {
  const { data, loading, error, reload } = useResource(
    useCallback(() => pluginsApi.listInstalled(), []),
  );
  const [activeKind, setActiveKind] = useState<PluginKind | "all">("all");
  const [rescanning, setRescanning] = useState(false);
  const [rescanErr, setRescanErr] = useState<string | null>(null);

  async function rescan() {
    setRescanning(true);
    setRescanErr(null);
    try {
      await pluginsApi.rescan();
      reload();
    } catch (err) {
      setRescanErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setRescanning(false);
    }
  }

  const filtered = useMemo(() => {
    if (!data) return [];
    if (activeKind === "all") return data;
    return data.filter((m) => m.implements.includes(activeKind));
  }, [data, activeKind]);

  return (
    <section className="library-section plugins-list">
      <header className="library-section-header">
        <h3>Installed plugins</h3>
        <button onClick={rescan} disabled={rescanning}>
          {rescanning ? "Rescanning…" : "Rescan"}
        </button>
      </header>

      <nav className="library-subnav" aria-label="Plugin kind filter">
        {KINDS.map((k) => (
          <button
            key={k.kind}
            className={k.kind === activeKind ? "library-subnav-item active" : "library-subnav-item"}
            onClick={() => setActiveKind(k.kind)}
          >
            {k.label}
          </button>
        ))}
      </nav>

      {rescanErr && (
        <p className="library-error" role="alert">
          {rescanErr}
        </p>
      )}

      <AsyncBoundary
        loading={loading}
        error={error}
        empty={filtered.length === 0}
        emptyMessage="No plugins match this filter."
        onRetry={reload}
      >
        <ul className="library-card-grid">
          {filtered.map((p) => (
            <li key={p.id} className="library-card">
              <Link to={`/library/plugins/${encodeURIComponent(p.id)}`}>
                <h4>{p.name}</h4>
                <small>
                  {p.id} · v{p.version} · API v{p.api_version}
                </small>
                {p.description && <p className="library-card-desc">{p.description}</p>}
                <p className="library-card-meta">{p.implements.join(", ") || "no kinds"}</p>
              </Link>
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </section>
  );
}

function PluginDetail() {
  const { pluginId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => pluginsApi.listInstalled(), []),
  );
  const plugin = (data ?? []).find((p) => p.id === pluginId);
  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/plugins">Installed plugins</Link> / {pluginId}
      </p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {!plugin ? (
          <p className="library-status">Plugin {pluginId} is not installed.</p>
        ) : (
          <PluginCard plugin={plugin} />
        )}
      </AsyncBoundary>
    </section>
  );
}

function PluginCard({ plugin }: { plugin: PluginManifest }) {
  return (
    <div className="plugin-detail">
      <h3>{plugin.name}</h3>
      <p className="library-card-meta">
        <code>{plugin.id}</code> · v{plugin.version} · API v{plugin.api_version}
      </p>
      <p>
        <strong>Kinds:</strong> {plugin.implements.join(", ") || "none"}
      </p>
      {plugin.description && <p>{plugin.description}</p>}
      {plugin.author && (
        <p>
          <strong>Author:</strong> {plugin.author}
        </p>
      )}
      {plugin.homepage && (
        <p>
          <strong>Homepage:</strong>{" "}
          <a href={plugin.homepage} target="_blank" rel="noreferrer">
            {plugin.homepage}
          </a>
        </p>
      )}

      <PluginConfigForm plugin={plugin} />

      <PluginHealth pluginId={plugin.id} />
    </div>
  );
}

function PluginHealth({ pluginId }: { pluginId: string }) {
  const [health, setHealth] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function check() {
    setBusy(true);
    setErr(null);
    try {
      setHealth(await pluginsApi.health(pluginId));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="plugin-health">
      <h4>Health</h4>
      <button onClick={check} disabled={busy}>
        {busy ? "Checking…" : "Check now"}
      </button>
      {err && (
        <p className="library-error" role="alert">
          {err}
        </p>
      )}
      {health !== null && <pre className="preset-text">{JSON.stringify(health, null, 2)}</pre>}
    </section>
  );
}

function PluginConfigForm({ plugin }: { plugin: PluginManifest }) {
  const properties = useMemo(() => {
    const schema = plugin.config_schema as JsonSchema;
    return (schema?.properties ?? {}) as Record<string, JsonSchema>;
  }, [plugin]);
  const required = useMemo(() => {
    const schema = plugin.config_schema as JsonSchema;
    return new Set((schema?.required ?? []) as string[]);
  }, [plugin]);
  const propertyKeys = useMemo(() => Object.keys(properties), [properties]);
  const [draft, setDraft] = useState<Record<string, unknown>>(() =>
    initialDraftFromSchema(properties),
  );
  const [savingState, setSavingState] = useState<"idle" | "saving" | "ok" | "error">("idle");
  const [saveErr, setSaveErr] = useState<string | null>(null);

  useEffect(() => {
    setDraft(initialDraftFromSchema(properties));
    setSavingState("idle");
    setSaveErr(null);
  }, [properties]);

  const handleGGUFIntrospect = useCallback(
    (info: GGUFInfo) => {
      setDraft((d) => {
        const next = { ...d };
        if (info.context_length != null && !d["n_ctx"]) next["n_ctx"] = info.context_length;
        if (info.name && !d["model_id"]) next["model_id"] = info.name;
        return next;
      });
    },
    [],
  );

  if (propertyKeys.length === 0) {
    return (
      <section>
        <h4>Configuration</h4>
        <p className="library-status">This plugin declares no configuration schema.</p>
      </section>
    );
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSavingState("saving");
    setSaveErr(null);
    try {
      await pluginsApi.configure(plugin.id, draft);
      setSavingState("ok");
    } catch (err) {
      setSavingState("error");
      setSaveErr(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <section className="plugin-config">
      <h4>Configuration</h4>
      <form onSubmit={save} className="library-form">
        {propertyKeys.map((key) => (
          <SchemaField
            key={key}
            pluginId={plugin.id}
            name={key}
            schema={properties[key] ?? {}}
            required={required.has(key)}
            value={draft[key]}
            onChange={(v) => setDraft((d) => ({ ...d, [key]: v }))}
            onFileIntrospect={handleGGUFIntrospect}
          />
        ))}
        <button type="submit" disabled={savingState === "saving"}>
          {savingState === "saving" ? "Saving…" : "Save configuration"}
        </button>
        {savingState === "ok" && <p className="library-ok">Configuration saved.</p>}
        {saveErr && (
          <p className="library-error" role="alert">
            {saveErr}
          </p>
        )}
      </form>
    </section>
  );
}

