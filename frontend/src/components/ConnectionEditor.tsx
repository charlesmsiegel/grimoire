import { useCallback, useEffect, useState } from "react";
import { api, type LLMConnection, type LLMConnectionDetail } from "../api/client";
import { getModels, type Model } from "../api/models";
import { BLANK_CONNECTION, ConnectionForm } from "./ConnectionForm";
import { ErrorNote } from "./ErrorNote";

export function ConnectionEditor() {
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [activeId, setActiveId] = useState("");
  const [id, setId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LLMConnectionDetail | null>(null);
  const [form, setForm] = useState(BLANK_CONNECTION);
  const [mode, setMode] = useState<"view" | "edit">("edit");
  const [key, setKey] = useState("");
  // Raw: fetching a model catalog goes out to the provider, so this banner
  // is one of the places being offline shows up (#210).
  const [error, setError] = useState<unknown>(null);
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
    setForm(BLANK_CONNECTION);
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
    } catch (err: unknown) {
      setError(err);
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

  /** Fetch this connection's model catalog from its provider.
   *
   *  A failure here is reported and nothing else: the connection is NOT marked
   *  unreachable, and the cached list it already has stays exactly where it is
   *  (#210). One refused catalog fetch is not a standing verdict on an
   *  endpoint — it can be a proxy, a cold local server, or the reader having
   *  clicked while their laptop's wifi was reassociating — and a rail badge
   *  saying "unreachable" would outlive the condition with nothing to clear
   *  it. "Is this endpoint up" is a poll with its own lifecycle, which is
   *  #146; this stays a per-call failure. */
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
    } catch (err: unknown) {
      setError(err);
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
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
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
            <ConnectionForm
              value={form} onChange={setForm}
              apiKey={key} onApiKey={setKey}
              keySet={!!detail?.key_set} lockKind={!!id}
              orModels={orModels} orError={orError} cachedModels={customModels}
              modelsHint={id ? (
                <p className="field-hint">
                  {detail?.fetched_at ? `Cached models last fetched ${detail.fetched_at}. ` : "No cached models yet. "}
                  <button className="link" onClick={refreshModels} disabled={refreshing}>
                    {refreshing ? "Refreshing…" : "Fetch models"}
                  </button>
                </p>
              ) : undefined}
            />

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
