import { useCallback, useEffect, useState } from "react";
import {
  api, type LLMConnection, type LLMConnectionDetail, type LLMConnectionKind,
} from "../api/client";
import { getModels, type Model } from "../api/models";
import ModelCombobox from "../routes/ModelCombobox";
import { Field } from "./Field";

// Aliases resolve to the newest model of each tier at request time (the Agent
// SDK passes them through to Claude Code); pinned ids freeze a version and
// need a refresh here when new models ship.
const CLAUDE_ALIASES = [
  { id: "fable", label: "Fable (latest)" },
  { id: "opus", label: "Opus (latest)" },
  { id: "sonnet", label: "Sonnet (latest)" },
  { id: "haiku", label: "Haiku (latest)" },
];
const CLAUDE_PINNED = [
  "claude-fable-5",
  "claude-opus-4-8",
  "claude-opus-4-7",
  "claude-opus-4-6",
  "claude-sonnet-5",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
];

const BLANK_FORM = {
  kind: "openrouter" as LLMConnectionKind, name: "", base_url: "",
  model: "", post_process: "none" as "none" | "strict",
};

export function ConnectionEditor() {
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [activeId, setActiveId] = useState("");
  const [id, setId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LLMConnectionDetail | null>(null);
  const [form, setForm] = useState(BLANK_FORM);
  const [mode, setMode] = useState<"view" | "edit">("edit");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [orModels, setOrModels] = useState<Model[]>([]);
  const [orError, setOrError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const reload = useCallback(() => api.listConnections().then(setConnections), []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { api.getConfig().then((c) => setActiveId(c.active_connection_id)); }, []);
  useEffect(() => {
    let alive = true;
    getModels().then((m) => alive && setOrModels(m)).catch(() => alive && setOrError(true));
    return () => { alive = false; };
  }, []);

  function resetForm() {
    setId(null);
    setDetail(null);
    setForm(BLANK_FORM);
    setKey("");
    setMode("edit");
    setError(null);
  }

  async function select(cid: string) {
    setError(null);
    const d = await api.readConnection(cid);
    setId(cid);
    setDetail(d);
    setForm({ kind: d.kind, name: d.name, base_url: d.base_url, model: d.model, post_process: d.post_process });
    setKey("");
    setMode("view");
  }

  async function save() {
    if (!form.name.trim()) return;
    setError(null);
    try {
      if (id) {
        const patch: Record<string, unknown> = {
          name: form.name, base_url: form.base_url, model: form.model, post_process: form.post_process,
        };
        if (key) patch.api_key = key;
        await api.updateConnection(id, patch);
        await reload();
        await select(id);
      } else {
        const { id: newId } = await api.createConnection({ ...form, api_key: key });
        await reload();
        await select(newId);
      }
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function remove(c: LLMConnection) {
    if (!window.confirm(`Delete connection '${c.name}'?`)) return;
    await api.deleteConnection(c.id);
    if (id === c.id) resetForm();
    await reload();
  }

  async function setActive(cid: string) {
    const next = await api.putConfig({ active_connection_id: cid });
    setActiveId(next.active_connection_id);
  }

  async function refreshModels() {
    if (!id) return;
    const forId = id;
    setRefreshing(true);
    try {
      const result = await api.refreshConnectionModels(forId);
      // Discard a response that arrived after the open form moved on to a
      // different connection or a newer revision of this one (e.g. the
      // user saved a base_url change while the fetch was in flight) — the
      // same stale-async-response guard used elsewhere in this codebase
      // (ModelCombobox/StyleGuideEditor's `alive` pattern), keyed here on
      // the connection's rev instead of a mount flag.
      setDetail((d) => (d && d.id === forId && d.rev === result.rev
        ? { ...d, models: result.models, fetched_at: result.fetched_at }
        : d));
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setRefreshing(false);
    }
  }

  const customModels = detail?.models ?? [];

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New connection</button>
        {connections.map((c) => (
          <button key={c.id} className={"row" + (id === c.id ? " active" : "")} onClick={() => select(c.id)}>
            {c.name}
            <span className="mark-badge">{c.kind}</span>
            {activeId === c.id && <span className="mark-badge">active</span>}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" && id && detail ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{detail.name}</h3>
              <div className="detail-rendered">
                <p>Kind: {detail.kind}</p>
                <p>Model: {detail.model || "(none set)"}</p>
                {detail.kind === "openai_compatible" && <p>Base URL: {detail.base_url || "(none set)"}</p>}
                {detail.kind === "openai_compatible" && <p>Prompt post-processing: {detail.post_process}</p>}
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                {activeId === id
                  ? <span className="chip on">Active</span>
                  : <button className="subtle" onClick={() => setActive(id)}>Set as active</button>}
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              <div className="side-section">
                <h4>Credentials</h4>
                {detail.kind === "claude"
                  ? <span className="field-hint">Uses the local Claude Code login — no key needed.</span>
                  : <span className={"chip" + (detail.key_set ? " on" : "")}>
                      {detail.key_set ? "Key set" : "No key set"}
                    </span>}
              </div>
              {detail.kind === "openai_compatible" && (
                <div className="side-section">
                  <h4>Cached models</h4>
                  <div className="field-hint">
                    {detail.fetched_at ? `Last fetched ${detail.fetched_at}` : "Never fetched"}
                  </div>
                  <button className="subtle" onClick={refreshModels} disabled={refreshing}>
                    {refreshing ? "Refreshing…" : "Refresh models"}
                  </button>
                </div>
              )}
            </aside>
          </div>
        ) : (
          <div className="form">
            <h3>{id ? "Edit connection" : "New connection"}</h3>
            <Field label="Kind">
              <select value={form.kind} disabled={!!id}
                      onChange={(e) => setForm({ ...form, kind: e.target.value as LLMConnectionKind })}>
                <option value="openrouter">OpenRouter</option>
                <option value="claude">Claude</option>
                <option value="openai_compatible">Custom (OpenAI-compatible)</option>
              </select>
            </Field>
            <Field label="Name">
              <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>

            {form.kind === "openrouter" && (
              <>
                <Field label="API key">
                  <input type="password" placeholder={detail?.key_set ? "A key is set — type to replace" : "sk-or-…"}
                         value={key} onChange={(e) => setKey(e.target.value)} />
                </Field>
                <Field label="Model">
                  <ModelCombobox value={form.model} onChange={(v) => setForm({ ...form, model: v })}
                                 models={orModels} error={orError} />
                </Field>
              </>
            )}

            {form.kind === "claude" && (
              <Field label="Claude model">
                <select aria-label="Claude model" value={form.model}
                        onChange={(e) => setForm({ ...form, model: e.target.value })}>
                  <optgroup label="Latest">
                    {CLAUDE_ALIASES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </optgroup>
                  <optgroup label="Pinned versions">
                    {CLAUDE_PINNED.map((mid) => <option key={mid} value={mid}>{mid}</option>)}
                  </optgroup>
                  {form.model &&
                    !CLAUDE_ALIASES.some((m) => m.id === form.model) &&
                    !CLAUDE_PINNED.includes(form.model) && (
                      <optgroup label="Custom">
                        <option value={form.model}>{form.model}</option>
                      </optgroup>
                    )}
                </select>
              </Field>
            )}

            {form.kind === "openai_compatible" && (
              <>
                <Field label="Base URL">
                  <input type="text" placeholder="https://api.example.com/v1" value={form.base_url}
                         onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
                </Field>
                <Field label="API key" hint="Optional — leave blank for servers that don't require auth.">
                  <input type="password" placeholder={detail?.key_set ? "A key is set — type to replace" : "(optional)"}
                         value={key} onChange={(e) => setKey(e.target.value)} />
                </Field>
                <Field label="Model">
                  <ModelCombobox value={form.model} onChange={(v) => setForm({ ...form, model: v })}
                                 models={customModels} />
                </Field>
                {id && (
                  <p className="field-hint">
                    {detail?.fetched_at ? `Cached models last fetched ${detail.fetched_at}. ` : "No cached models yet. "}
                    <button className="link" onClick={refreshModels} disabled={refreshing}>
                      {refreshing ? "Refreshing…" : "Fetch models"}
                    </button>
                  </p>
                )}
                <Field label="Prompt post-processing"
                       hint="Strict folds system messages into user turns and forces the sequence to start with a user turn — needed by some coding-style endpoints (e.g. z.ai's GLM) that reject a system message mid-conversation.">
                  <select value={form.post_process}
                          onChange={(e) => setForm({ ...form, post_process: e.target.value as "none" | "strict" })}>
                    <option value="none">None</option>
                    <option value="strict">Strict</option>
                  </select>
                </Field>
              </>
            )}

            <div className="form-actions">
              {id && <button className="subtle" onClick={() => remove(connections.find((c) => c.id === id)!)}>Delete</button>}
              {id && <button className="subtle" onClick={() => setMode("view")}>Cancel</button>}
              <button className="primary" onClick={save} disabled={!form.name.trim()}>
                {id ? "Save connection" : "Create connection"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
