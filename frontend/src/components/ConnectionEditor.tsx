import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, type HealthCheckResult, type LLMConnection, type LLMConnectionDetail,
  type LLMConnectionKind, type Model, type ProviderHealth,
} from "../api/client";
import { BLANK_CONNECTION, ConnectionForm } from "./ConnectionForm";
import { ErrorNote } from "./ErrorNote";

/** Connection kinds whose provider can be asked for a catalog (#149).
 *
 *  Mirrors `llm.LISTABLE_KINDS`, and is checked here for the same reason the
 *  route checks it there: `claude`'s models are SDK aliases with no endpoint to
 *  enumerate, so the form offers that kind a fixed list and this must not offer
 *  it a Fetch button that can only ever 400. */
const LISTABLE: LLMConnectionKind[] = ["openrouter", "openai_compatible"];

/** What the last thing this connection's provider did says, in words.
 *
 *  Words rather than only a colour, and beside the credential rather than only
 *  in the status bar: the dot in the header cannot be hovered on a phone, and
 *  the reader who needs the *reason* is the one already looking at the key
 *  (#146). */
function healthLabel(health: ProviderHealth): string {
  if (health.state === "unknown") return "Not checked yet.";
  const when = health.at ? ` · ${new Date(health.at).toLocaleString()}` : "";
  if (health.state === "ok") return `Working${when}`;
  return `${health.detail || health.kind}${when}`;
}

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
  const [models, setModels] = useState<Model[]>([]);
  const [modelsError, setModelsError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState<HealthCheckResult | null>(null);
  // Connections whose empty catalog has already been fetched once this mount.
  // Without it, a connection whose provider is unreachable re-fetches on every
  // re-render that reselects it — and the failure is exactly the case where
  // retrying in a loop is worst.
  const fetchedOnce = useRef(new Set<string>());
  // Which connection the body is showing (null = the New form). A ref rather
  // than the `id` state because the async fetches below have to compare
  // against what is open *when they land*, not against what was open when
  // they were started — a catalog fetch is slow enough to outlive the click
  // that started it, and a reader who moves to another connection meanwhile
  // would otherwise get the first one's models in the second one's picker.
  const openId = useRef<string | null>(null);

  const reload = useCallback(() => api.listConnections().then(setConnections), []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { api.getConfig().then((c) => setActiveId(c.active_connection_id)); }, []);

  // The catalog for a connection that does not exist yet (#149). OpenRouter's
  // is public and needs no key, so the New-connection form can fill its picker
  // the moment the kind is chosen — which is what it did before, except that it
  // did it for every kind. A custom endpoint has nothing to fetch until a base
  // URL is typed, so that one waits for the button.
  useEffect(() => {
    if (id !== null) return;              // a saved connection reads its cache
    setModelsError(false);
    if (form.kind !== "openrouter") { setModels([]); return; }
    let alive = true;
    // Both guards, and they are not the same guard. `alive` retires the effect
    // when its deps move; `openId` is what the *body* is showing, which changes
    // synchronously on a click and so can already have moved on while this
    // effect's cleanup is still one render away.
    api.previewModels({ kind: "openrouter" })
      .then((r) => alive && openId.current === null && setModels(r.models))
      .catch(() => alive && openId.current === null && setModelsError(true));
    return () => { alive = false; };
  }, [id, form.kind]);

  function resetForm() {
    openId.current = null;
    setId(null);
    setDetail(null);
    setForm(BLANK_CONNECTION);
    setKey("");
    setMode("edit");
    setError(null);
    setChecked(null);
  }

  async function select(cid: string) {
    setError(null);
    setChecked(null);
    setModelsError(false);
    openId.current = cid;
    const d = await api.readConnection(cid);
    if (openId.current !== cid) return;   // moved on while this read was out
    setId(cid);
    setDetail(d);
    setForm({ kind: d.kind, name: d.name, base_url: d.base_url, model: d.model, post_process: d.post_process });
    setKey("");
    setMode("view");
    setModels(d.models);
    // An OpenRouter connection that has never been fetched has an empty
    // picker, which is how all of them arrive the first time after #149 —
    // their catalog used to be fetched by the browser on mount and cached
    // nowhere. Filling it on open keeps that behaviour rather than making the
    // reader press a button nobody told them about.
    //
    // OpenRouter only, and not `LISTABLE`: its catalog is public, served by a
    // host that is up, and costs one cheap GET. A custom endpoint can be a
    // local server that is switched off, where the same courtesy is a stall on
    // merely *looking* at a connection — so that kind keeps its explicit
    // button, exactly as it had before.
    if (d.kind === "openrouter" && d.models.length === 0 && !fetchedOnce.current.has(cid)) {
      fetchedOnce.current.add(cid);
      await refreshModels(cid, { quiet: true });
    }
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
        // The saved settings are new ones, so whatever their predecessors
        // fetched is not this connection's catalog any more — let the reselect
        // below fetch it again rather than showing the old endpoint's models.
        fetchedOnce.current.delete(id);
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
    fetchedOnce.current.delete(c.id);
    if (id === c.id) resetForm();
    await reload();
  }

  async function setActive(cid: string) {
    const next = await api.putConfig({ active_connection_id: cid });
    setActiveId(next.active_connection_id);
  }

  /** Fetch this connection's model catalog from its own provider (#149).
   *
   *  A failure here is reported and nothing else: the connection is NOT marked
   *  unreachable, and the cached list it already has stays exactly where it is
   *  (#210). One refused catalog fetch is not a standing verdict on an
   *  endpoint — it can be a proxy, a cold local server, or the reader having
   *  clicked while their laptop's wifi was reassociating — and a rail badge
   *  saying "unreachable" would outlive the condition with nothing to clear
   *  it. "Is this endpoint up" is a question with its own button now (#146),
   *  and its verdict is cleared by editing the connection; this stays a
   *  per-call failure.
   *
   *  `quiet` is for the fetch nobody asked for (the one on open): it degrades
   *  the picker to free text and says so *there*, without raising a banner
   *  about a request the reader did not make. A click still reports.
   */
  async function refreshModels(forId: string, { quiet = false } = {}) {
    setRefreshing(true);
    setModelsError(false);
    try {
      const result = await api.refreshConnectionModels(forId);
      // Discard a response that arrived after the open form moved on to a
      // different connection or a newer revision of this one (e.g. the
      // user saved a base_url change while the fetch was in flight) — the
      // same stale-async-response guard used elsewhere in this codebase
      // (ModelCombobox/StyleGuideEditor's `alive` pattern), keyed here on
      // the connection's rev instead of a mount flag.
      if (openId.current === forId) setModels(result.models);
      setDetail((d) => (d && d.id === forId && d.rev === result.rev
        ? { ...d, models: result.models, fetched_at: result.fetched_at }
        : d));
    } catch (err: unknown) {
      if (openId.current !== forId) return;   // a failure about a closed form
      setModelsError(true);
      if (!quiet) setError(err);
    } finally {
      setRefreshing(false);
    }
  }

  /** The same fetch for a connection that has not been saved: the credentials
   *  come off the form rather than off disk, and nothing is cached. */
  async function previewModels() {
    setRefreshing(true);
    setModelsError(false);
    try {
      const r = await api.previewModels({
        kind: form.kind, base_url: form.base_url, api_key: key,
      });
      if (openId.current !== null) return;   // a saved connection is open now
      setModels(r.models);
    } catch (err: unknown) {
      if (openId.current !== null) return;
      setModelsError(true);
      setError(err);
    } finally {
      setRefreshing(false);
    }
  }

  /** Ask the provider whether this connection can serve, right now (#146).
   *
   *  Reports a failing connection through `checked` rather than the error
   *  banner: "your key is rejected" is the answer to the question that was
   *  asked, not a failure of the app to answer it. */
  async function check(forId: string) {
    setChecking(true);
    setChecked(null);
    try {
      const result = await api.checkConnection(forId);
      // A verdict about a connection nobody is looking at any more belongs in
      // the registry (where the server already put it), not on this panel.
      if (openId.current !== forId) return;
      setChecked(result);
      // The rail badges a failing connection, and it is drawn from the list —
      // so a check whose verdict only reached this panel would leave the two
      // halves of the same page disagreeing about the same connection.
      await reload();
      setDetail((d) => (d && d.id === forId
        ? { ...d, health: {
              state: result.ok ? "ok" : "error", kind: result.kind,
              detail: result.detail, at: result.checked_at } }
        : d));
    } catch (err: unknown) {
      setError(err);
    } finally {
      setChecking(false);
    }
  }

  const listable = LISTABLE.includes(form.kind);
  const fetchLabel = refreshing ? "Fetching…" : "Fetch models";

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New connection</button>
        {connections.map((c) => (
          <button key={c.id} className={"row" + (id === c.id ? " active" : "")} onClick={() => select(c.id)}>
            {c.name}
            <span className="mark-badge">{c.kind}</span>
            {activeId === c.id && <span className="mark-badge">active</span>}
            {/* Only the failure. A rail that badged all three states would
                spend two thirds of its ink saying nothing happened. */}
            {c.health.state === "error" &&
              <span className="mark-badge health-error" title={c.health.detail}>failing</span>}
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
              <div className="side-section">
                <h4>Status</h4>
                {/* The verdict, then the button that produces one. A key being
                    present has never meant it works, and until #146 that was
                    the only thing this page could say. */}
                <div className={"field-hint health-" + detail.health.state}>
                  {healthLabel(detail.health)}
                </div>
                <button className="subtle" onClick={() => { void check(id); }} disabled={checking}>
                  {checking ? "Testing…" : "Test connection"}
                </button>
                {checked && !checked.ok && (
                  <div className="field-hint">Reported as: {checked.kind}</div>
                )}
              </div>
              {LISTABLE.includes(detail.kind) && (
                <div className="side-section">
                  <h4>Cached models</h4>
                  <div className="field-hint">
                    {detail.fetched_at ? `Last fetched ${detail.fetched_at}` : "Never fetched"}
                  </div>
                  <button className="subtle" onClick={() => refreshModels(id)} disabled={refreshing}>
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
              models={models} modelsError={modelsError}
              modelsHint={listable ? (
                <p className="field-hint">
                  {id
                    ? (detail?.fetched_at ? `Cached models last fetched ${detail.fetched_at}. ` : "No cached models yet. ")
                    : "Models are listed from this provider. "}
                  <button className="link" disabled={refreshing}
                          onClick={() => (id ? refreshModels(id) : previewModels())}>
                    {fetchLabel}
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
