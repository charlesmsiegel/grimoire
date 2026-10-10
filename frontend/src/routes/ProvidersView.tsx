import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Link, useLocation, useMatch, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ApiError, api, type CapabilityName, type CapabilityValue, type HealthCheckResult,
  type InferenceSettings, type LLMConnection, type LLMConnectionDetail,
  type LLMConnectionDraft, type LLMConnectionKind, type ModelCapabilities, type ModelFacts,
  type ModelFactsUpdate, type ProviderBilling, type ProviderHealth, type ProviderPresetOption,
  type LimitSource, type ModelLimit, type ProviderUse, type TestableCapability,
  type VisionOverride,
} from "../api/client";
import { ErrorNote } from "../components/ErrorNote";
import { Field } from "../components/Field";
import { CapabilityBadges } from "../components/inference/CapabilityBadges";
import { InferenceNav } from "../components/inference/InferenceNav";
import { InferenceBanner } from "../components/inference/InferenceBanner";
import { migrationBanner, migrationLine } from "../components/inference/migration";
import { TestCallDialog, useModelTests } from "../components/inference/TestCallDialog";
import { useInferenceSettings } from "../components/inference/useInferenceSettings";
import { EmbeddingOptionsFields } from "../components/models/EmbeddingOptionsFields";
import {
  blockOf, dimensionsTyped, embeddingForm, mismatchLine, optionsSummary, sameBlock,
  type EmbeddingForm,
} from "../components/models/embeddingOptions";
import { taskHash } from "../components/models/taskHash";
import { ColumnSection, PageShell } from "../components/PageShell";
import { perThousand } from "../components/cost";
import { RATE_FIELDS, RateFields, entryOf, filled, formOf, hasBase,
         type RateForm } from "../components/RateFields";
import { RegexRulesEditor } from "../components/RegexRulesEditor";
import { errorText } from "../api/errors";
import { EDIT_LIMITS, EDIT_RATES, modelPath, providerPath } from "../providerPaths";

/** Adapters whose provider can be asked for a catalog. Mirrors
 *  `llm.LISTABLE_KINDS`: the Claude subscription's models are SDK aliases with
 *  nothing to enumerate, so it is offered no Refresh that could only 400. */
const LISTABLE: LLMConnectionKind[] = ["openrouter", "openai_compatible", "anthropic"];

/** The capabilities a test call has a probe for, in the order it runs them. */
const TESTABLE: TestableCapability[] = ["generate", "vision", "embed", "decide_native", "tools"];

/** The capabilities a user may assert over what discovery says (spec 4.2). */
const OVERRIDABLE: { name: CapabilityName; label: string }[] = [
  { name: "generate", label: "Generate" },
  { name: "vision", label: "Vision" },
  { name: "embed", label: "Embed" },
  { name: "decide_native", label: "Decide natively" },
  { name: "structured_output", label: "Structured output" },
  { name: "prefill", label: "Prefill" },
  { name: "tools", label: "Tool calling" },
];

const ROLE_LABEL: Record<string, string> = {
  primary: "Primary", fast: "Fast", decision: "Decision", embedding: "Embedding",
};

/** What the provider last did, in words (#146). */
function healthLabel(health: ProviderHealth): string {
  if (health.state === "unknown") return "Not checked yet.";
  const when = health.at ? ` · ${new Date(health.at).toLocaleString()}` : "";
  if (health.state === "ok") return `Working${when}`;
  return `${health.detail || health.kind}${when}`;
}

function checkedHealth(r: HealthCheckResult): ProviderHealth {
  return { state: r.ok ? "ok" : "error", kind: r.kind, detail: r.detail, at: r.checked_at };
}

/** The provider preset a record was made from: its own, else the one the
 *  settings view inferred for it. */
function presetOf(p: LLMConnection, presets: ProviderPresetOption[],
                  settings: InferenceSettings | null): ProviderPresetOption | undefined {
  const pid = p.preset || settings?.providers.find((x) => x.id === p.id)?.preset || "";
  return presets.find((x) => x.id === pid);
}

/** Whether this provider's health check SENDS a message. Errs towards asking:
 *  with no preset to say, any preset on the same adapter that generates is
 *  reason enough not to check unasked. */
function generatingCheck(p: LLMConnection, preset: ProviderPresetOption | undefined,
                         presets: ProviderPresetOption[]): boolean {
  if (preset) return preset.generating_check;
  return presets.some((x) => x.kind === p.kind && x.generating_check);
}

/** One `used_by` entry as a chip: its words, where it leads and, for a
 *  campaign's own use, where in that campaign it is changed. A campaign's
 *  choice is not the library's record of the same role or route -- that one
 *  may not name this provider at all -- so its chip opens the campaign. */
function chipFor(use: ProviderUse, settings: InferenceSettings | null):
  { label: string; to: string; title?: string } {
  const campaign = use.scope === "campaign" && use.cid ? use.cid : null;
  const role = ROLE_LABEL[use.key] ?? use.key;
  const label = use.kind === "route"
    ? settings?.routes.find((r) => r.key === use.key)?.label ?? use.key
    : use.kind === "fallback" ? `${role} fallback` : role;
  if (campaign) {
    return { label: `${label} · ${campaign}`, to: `/campaigns/${encodeURIComponent(campaign)}`,
             title: "This campaign's own choice: change it under Models in a scene's Inspector." };
  }
  if (use.kind === "route") return { label, to: `/models/edit${taskHash(use.key)}` };
  return { label, to: "/models" };
}

/** Where Cancel on `/providers/new` goes: the router state an in-app link left
 *  (`{ returnTo }`), if it is a path inside the app and not the form itself;
 *  null otherwise, which lands on `/providers`. A refresh keeps the state the
 *  browser kept, so it still goes back where the form was opened from. */
export function validReturn(state: unknown): string | null {
  const to = (state as { returnTo?: unknown } | null)?.returnTo;
  if (typeof to !== "string" || !to.startsWith("/") || to.startsWith("//")) return null;
  const path = to.split(/[?#]/)[0];
  return path === "/providers/new" ? null : to;
}

/** One row of the catalog: a model and every capability as it resolves, or —
 *  for a model hidden from every need asked about — why. */
type CatalogRow = {
  id: string; name: string;
  capabilities: Partial<Record<CapabilityName, CapabilityValue>> | null;
  why: string;
};

/** The catalog, out of the capability answers for `generate` and `embed`: the
 *  two that between them place every row (an embedding model is hidden from
 *  the first and listed by the second). */
function catalogRows(answers: ModelCapabilities[]): CatalogRow[] {
  const rows = new Map<string, CatalogRow>();
  for (const a of answers) {
    for (const m of [...a.groups.fits, ...a.groups.unverified]) {
      if (!rows.get(m.id)?.capabilities) {
        rows.set(m.id, { id: m.id, name: m.name, capabilities: m.capabilities, why: "" });
      }
    }
  }
  for (const a of answers) {
    for (const h of a.hidden) {
      if (!rows.has(h.id)) rows.set(h.id, { id: h.id, name: h.id, capabilities: null, why: h.reason });
    }
  }
  return [...rows.values()].sort((x, y) => x.id.localeCompare(y.id));
}

type ProviderForm = {
  /** "" is the preset's default: the record states none. */
  name: string; base_url: string; billing: "" | ProviderBilling; sampler_support: "" | "extended";
};

/** `/providers`: the providers a model is served from, each one's catalog, and
 *  what is known of every model on it (spec 10).
 *
 *  A page that owns the screen, so its records are the context column's
 *  (`ColumnSection`s: the providers, then the selected one's models) and the
 *  detail is main's. Selection is the URL — `/providers/:id` and
 *  `/providers/:id/models/<model id, slashes and all>` — so a Used by chip or a
 *  bookmark lands on exactly one record. Everything is read-only until Edit.
 *
 *  Nothing here spends unasked. The free health checks (a key lookup, a model
 *  listing) run when a provider is opened; one that generates (the Claude
 *  subscription) waits behind a button and a confirmation, and the server
 *  refuses it without `confirm: true` anyway. A test call shows its preview
 *  before it can be run. */
export default function ProvidersView() {
  const params = useParams();
  const id = params.id ?? "";
  const model = params["*"] ?? "";
  const navigate = useNavigate();

  const [providers, setProviders] = useState<LLMConnection[]>([]);
  const [presets, setPresets] = useState<ProviderPresetOption[] | null>(null);
  // A failed presets read is NOT "no preset generates": whether a check costs
  // money is then unknown, and an unknown check is asked for, never sent.
  const [presetsError, setPresetsError] = useState<unknown>(null);
  const presetsFailed = presetsError !== null;
  // Read for the banner, the route labels, the inferred presets and which
  // provider the Embedding role embeds through, and read again while an
  // upgrade is on its way, so the page unlocks when it lands. A failed read
  // leaves it null, which blocks nothing: the server refuses what it must on
  // its own.
  const { settings, settled } = useInferenceSettings();
  // Each loaded record is held WITH the provider it describes: this component
  // stays mounted across `:id` changes, and a slow read must not paint one
  // provider's detail under another's name.
  const [detail, setDetail] = useState<LLMConnectionDetail | null>(null);
  const [catalog, setCatalog] =
    useState<{ id: string; rows: CatalogRow[]; reason: string | null } | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  // The provider list's own failure, apart from `error`: that one is the open
  // record's, cleared whenever the record changes, and a list that could not be
  // re-read after a create must still say so on the provider it opens.
  const [listError, setListError] = useState<unknown>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const location = useLocation();
  const isNew = useMatch("/providers/new") !== null;
  /** The preset the new-provider form has been given, once one is picked. */
  const [newPreset, setNewPreset] = useState<ProviderPresetOption | null>(null);
  useEffect(() => { if (isNew) setNewPreset(null); }, [isNew]);
  const [checking, setChecking] = useState(false);
  const [askCheck, setAskCheck] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // What the body is showing NOW, readable from a request that was started for
  // something else.
  const openId = useRef(id);
  openId.current = id;

  const reloadList = useCallback(() => api.listConnections().then((list) => {
    setProviders(list);
    setListError(null);
  }), []);
  useEffect(() => { reloadList().catch(setListError); }, [reloadList]);
  const loadPresets = useCallback(() => {
    setPresets(null);
    setPresetsError(null);
    api.listProviderPresets().then(setPresets).catch((err: unknown) => {
      setPresets([]);
      setPresetsError(err);
    });
  }, []);
  useEffect(() => { loadPresets(); }, [loadPresets]);

  // Gated as the server gates them. The provider record itself -- create,
  // edit (name, key, address, billing, samplers), delete, refresh, its rules
  // and the test call -- is refused only on a store a newer build switched:
  // at format 1 (an upgrade pending, or one that keeps failing) the server
  // takes these writes, and this is the one place a revoked key can be
  // replaced. A model's FACTS (its rates included) are the new layout's own
  // and wait on the store's FORMAT, not on the migration being done: the
  // status stays pending after the global switch while any campaign is left
  // unmarked (busy, synced from an older build, deferred by maintenance), and
  // the server takes them once the format is "2". What is still left then is
  // the quiet line, information rather than a lock -- worded as every other
  // model-settings surface words it. Until the first read has settled the
  // format is not known, and not known is held like locked for the facts: a
  // form opened by a link (`?edit=rates`) must not offer a live Save first.
  // A read that FAILED leaves it to the server, as it always has.
  const blocked = !!settings && settings.newer;
  const factsBlocked = settings ? settings.newer || settings.format !== "2" : !settled;
  const banner = migrationBanner(settings);
  const upgradeNote = migrationLine(settings);

  const loadDetail = useCallback(async (pid: string) => {
    const d = await api.readConnection(pid);
    if (openId.current === pid) setDetail(d);
  }, []);

  const loadCatalog = useCallback(async (pid: string) => {
    setCatalogError(null);
    const answers = await Promise.allSettled(
      (["generate", "embed"] as const).map((need) => api.readConnectionCapabilities(pid, need)));
    if (openId.current !== pid) return;
    const got = answers.flatMap((a) => (a.status === "fulfilled" ? [a.value] : []));
    if (got.length === 0) {
      setCatalog(null);
      setCatalogError("The catalog could not be read.");
      return;
    }
    const rows = catalogRows(got);
    setCatalog({ id: pid, rows, reason: rows.length ? null : got[0].reason });
  }, []);

  // A different provider: start from its read-only view, with nothing of the
  // last one's on screen.
  useEffect(() => {
    setMode("view");
    setAskCheck(false);
    setError(null);
    setDetail(null);
    setCatalog(null);
    if (!id) return;
    loadDetail(id).catch((err: unknown) => { if (openId.current === id) setError(err); });
    void loadCatalog(id);
  }, [id, loadDetail, loadCatalog]);

  // Opening a model (or leaving one) is a different record too: an unsaved
  // provider form does not wait behind it.
  useEffect(() => { setMode("view"); }, [model]);

  const shown = detail && detail.id === id ? detail : null;
  const shownPreset = shown && presets ? presetOf(shown, presets, settings) : undefined;
  const generating = shown && presets && !presetsFailed
    ? generatingCheck(shown, shownPreset, presets) : true;

  const check = useCallback(async (pid: string, confirm = false) => {
    setChecking(true);
    setAskCheck(false);
    try {
      const r = confirm
        ? await api.checkConnection(pid, { confirm: true })
        : await api.checkConnection(pid);
      if (openId.current !== pid) return;
      setDetail((d) => (d && d.id === pid ? { ...d, health: checkedHealth(r) } : d));
      // The column badges a failing provider from the list.
      void reloadList().catch(() => {});
    } catch (err: unknown) {
      if (openId.current === pid) setError(err);
    } finally {
      setChecking(false);
    }
  }, [reloadList]);

  // The free checks run on open — once per opening, not per render. A save
  // clears the mark, because a new key or address is exactly what a reader
  // wants checked.
  const autoChecked = useRef<string | null>(null);
  useEffect(() => {
    if (!shown || presets === null) return;
    if (autoChecked.current === shown.id) return;
    autoChecked.current = shown.id;
    if (!generating) void check(shown.id);
  }, [shown, presets, generating, check]);
  useEffect(() => { if (!id) autoChecked.current = null; }, [id]);

  async function refresh(pid: string) {
    setRefreshing(true);
    try {
      await api.refreshConnectionModels(pid);
      await Promise.all([loadDetail(pid), loadCatalog(pid)]);
    } catch (err: unknown) {
      if (openId.current === pid) setError(err);
    } finally {
      setRefreshing(false);
    }
  }

  function startNew() {
    navigate("/providers/new",
             { state: { returnTo: location.pathname + location.search + location.hash } });
  }

  function cancelNew() {
    navigate(validReturn(location.state) ?? "/providers");
  }

  // The provider exists once the create answers, so it is opened by the id
  // that answer named whatever the list re-read does: a failed re-read left on
  // the form would offer Create again, and a second press makes a duplicate.
  async function created(newId: string) {
    navigate(providerPath(newId));
    await reloadList().catch(setListError);
  }

  async function saved(pid: string) {
    await reloadList().catch(setListError);
    // Cleared BEFORE the re-read: the effect that checks runs when the new
    // record lands, and must find the mark already gone.
    autoChecked.current = null;
    await loadDetail(pid);
    // A new key or address can be a different catalog, and the column's
    // models and badges would otherwise describe the old endpoint.
    void loadCatalog(pid);
    setMode("view");
  }

  async function remove(p: LLMConnectionDetail) {
    if (!window.confirm(`Delete provider '${p.name}'?`)) return;
    try {
      await api.deleteConnection(p.id);
    } catch (err: unknown) {
      setError(err);
      return;
    }
    navigate("/providers");
    await reloadList().catch(setListError);
  }

  const column = (
    <>
      <InferenceNav current="providers" />
      <ColumnSection label="Providers" count={providers.length}>
        <div className="column-actions">
          <button type="button" className="column-primary" onClick={startNew}
                  disabled={blocked || isNew} aria-current={isNew ? "page" : undefined}>
            + New provider
          </button>
        </div>
        {providers.length === 0 && <p className="column-empty">None yet.</p>}
        {providers.map((p) => (
          <Link key={p.id} to={providerPath(p.id)}
                className={"column-row" + (p.id === id && !isNew ? " active" : "")
                           + (p.health.state === "error" ? " alert" : "")}>
            <span className="column-row-label">{p.name}</span>
            {/* Only the failure: a badge on every state would spend most of
                the column saying nothing happened. */}
            {p.health.state === "error" &&
              <span className="column-row-count alert" title={p.health.detail}>failing</span>}
          </Link>
        ))}
      </ColumnSection>
      {shown && (
        <ColumnSection label="Models" count={catalog?.id === id ? catalog.rows.length : undefined}>
          {catalog?.id === id && catalog.rows.length === 0 &&
            <p className="column-empty">No catalog.</p>}
          {catalog?.id === id && catalog.rows.map((m) => (
            <Link key={m.id} to={modelPath(id, m.id)}
                  className={"column-row" + (m.id === model ? " active" : "")}>
              <span className="column-row-label">{m.id}</span>
            </Link>
          ))}
          <OpenModelById onOpen={(m) => navigate(modelPath(id, m))} />
        </ColumnSection>
      )}
    </>
  );

  let body;
  if (isNew) {
    body = (
      <NewProvider presets={presets} presetsError={presetsError} onRetry={loadPresets}
                   choice={newPreset} onChoose={setNewPreset}
                   onCancel={cancelNew} onCreated={created} />
    );
  } else if (!id) {
    body = (
      <p className="empty-state">
        <span className="empty-what">Pick a provider</span> to see its models and what each can
        do, or add one with + New provider.
      </p>
    );
  } else if (!shown) {
    body = error ? null : <p className="field-hint">Reading the provider…</p>;
  } else if (model) {
    body = (
      <ModelFactsPanel key={`${id}\n${model}`} provider={shown} model={model} blocked={blocked}
                       factsBlocked={factsBlocked}
                       onChanged={() => { void loadCatalog(id); }} />
    );
  } else if (mode === "edit") {
    body = (
      <EditProvider provider={shown} preset={shownPreset} blocked={blocked}
                    embeds={settings?.roles.embedding?.resolves?.provider === shown.id}
                    onCancel={() => setMode("view")} onSaved={() => saved(shown.id)}
                    onDelete={() => { void remove(shown); }} />
    );
  } else {
    const listable = LISTABLE.includes(shown.kind);
    body = (
      <>
        <div className="detail-view">
          <div className="detail-main">
            <h3>{shown.name}</h3>
            <div className="detail-rendered">
              <p>{shownPreset ? shownPreset.label : shown.kind}</p>
              {shown.base_url && <p>Address: <code>{shown.base_url}</code></p>}
            </div>
            <h4>Models on this provider</h4>
            {catalogError && <p className="field-hint">{catalogError}</p>}
            {catalog?.id === id && catalog.reason && <p className="field-hint">{catalog.reason}</p>}
            {catalog?.id === id && catalog.rows.length === 0 && !catalog.reason && (
              <p className="field-hint">
                {listable ? "No catalog yet. Refresh asks the provider for one."
                          : "This provider lists no models. Open one by its id from the column."}
              </p>
            )}
            {catalog?.id === id && catalog.rows.length > 0 && (
              <ul className="provider-catalog">
                {catalog.rows.map((m) => (
                  <li key={m.id}>
                    <Link to={modelPath(id, m.id)}>{m.id}</Link>
                    {m.name !== m.id && <span className="field-hint"> {m.name}</span>}{" "}
                    {m.capabilities
                      ? <CapabilityBadges capabilities={m.capabilities} />
                      : <span className="field-hint">{m.why}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <ProviderSidebar provider={shown} preset={shownPreset} settings={settings}
                           blocked={blocked} generating={generating} presetsKnown={presets !== null}
                           checking={checking} askCheck={askCheck} refreshing={refreshing}
                           onEdit={() => setMode("edit")} onAsk={setAskCheck}
                           onCheck={(confirm) => { void check(shown.id, confirm); }}
                           onRefresh={() => { void refresh(shown.id); }}
                           onUse={(to) => navigate(to)} />
        </div>
        {/* This provider's own rules, run before the global ones: they correct
            one endpoint's habits. They save as they are edited. */}
        <div className="connection-output">
          <h3>Output processing</h3>
          <RegexRulesEditor key={shown.id} scope={{ kind: "connection", id: shown.id }}
                            readOnly={blocked} />
        </div>
      </>
    );
  }

  return (
    <PageShell column={column} columnLabel="Providers">
      <div className="page view-anim">
        <div className="page-head">
          <h1 className="page-h1">Providers</h1>
        </div>
        <InferenceBanner status={banner} />
        {upgradeNote && <p className="field-hint">{upgradeNote}</p>}
        {listError != null && <div className="banner"><ErrorNote err={listError} /></div>}
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
        {body}
      </div>
    </PageShell>
  );
}

/** A model the catalog does not list — a Claude alias, a model a local server
 *  loads on demand — opened by typing its id. Its own small form: it is in the
 *  column, never inside another one. */
function OpenModelById({ onOpen }: { onOpen: (model: string) => void }) {
  const [typed, setTyped] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    const m = typed.trim();
    if (m) onOpen(m);
  }
  return (
    <form className="column-actions" onSubmit={submit}>
      <input type="text" aria-label="Model id" placeholder="Open a model by id"
             value={typed} onChange={(e) => setTyped(e.target.value)} />
      <button type="submit" className="subtle" disabled={!typed.trim()}>Open</button>
    </form>
  );
}

function ProviderSidebar({
  provider, preset, settings, blocked, generating, presetsKnown, checking, askCheck, refreshing,
  onEdit, onAsk, onCheck, onRefresh, onUse,
}: {
  provider: LLMConnectionDetail; preset: ProviderPresetOption | undefined;
  settings: InferenceSettings | null; blocked: boolean; generating: boolean;
  presetsKnown: boolean; checking: boolean; askCheck: boolean; refreshing: boolean;
  onEdit: () => void; onAsk: (ask: boolean) => void; onCheck: (confirm: boolean) => void;
  onRefresh: () => void; onUse: (to: string) => void;
}) {
  const billing = provider.billing || preset?.billing || "";
  const uses = provider.used_by ?? [];
  return (
    <aside className="detail-sidebar" aria-label={provider.name}>
      <div className="form-actions">
        <button className="subtle" onClick={onEdit} disabled={blocked}>Edit</button>
      </div>
      <div className="side-section">
        <h4>Billing</h4>
        {billing ? <span className="chip on">{billing}</span>
                 : <span className="field-hint">Not stated.</span>}
      </div>
      {provider.kind === "openai_compatible" && (
        <div className="side-section">
          <h4>Extended samplers</h4>
          <span className="field-hint">
            {provider.sampler_support === "extended"
              ? "On: top-k, min-p and repetition penalty are sent."
              : "Off: only the standard samplers are sent."}
          </span>
        </div>
      )}
      <div className="side-section">
        <h4>Credentials</h4>
        {provider.kind === "claude"
          ? <span className="field-hint">Uses the local Claude Code login — no key needed.</span>
          : <span className={"chip" + (provider.key_set ? " on" : "")}>
              {provider.key_set ? "Key set" : "No key set"}
            </span>}
      </div>
      <div className="side-section">
        <h4>Health</h4>
        <div className={"field-hint health-" + provider.health.state}>
          {healthLabel(provider.health)}
        </div>
        {generating ? (
          askCheck ? (
            <div className="field-hint" role="group" aria-label="Confirm the check">
              This sends one short message through {provider.name}, which counts against
              its plan.{" "}
              <button className="subtle" onClick={() => onCheck(true)} disabled={checking}>
                Send it
              </button>{" "}
              <button className="subtle" onClick={() => onAsk(false)}>Cancel</button>
            </div>
          ) : (
            <button className="subtle" onClick={() => onAsk(true)}
                    disabled={checking || !presetsKnown}>
              {checking ? "Checking…" : "Check (sends one short message)"}
            </button>
          )
        ) : (
          // Live even when blocked: a free check writes nothing to the store.
          <button className="subtle" onClick={() => onCheck(false)} disabled={checking}>
            {checking ? "Checking…" : "Check again"}
          </button>
        )}
      </div>
      {LISTABLE.includes(provider.kind) && (
        <div className="side-section">
          <h4>Catalog</h4>
          <div className="field-hint">
            {provider.fetched_at
              ? `Last fetched ${new Date(provider.fetched_at).toLocaleString()}`
              : "Never fetched"}
          </div>
          {/* Held when blocked: a refresh writes the catalog cache, into a
              store a newer build owns or one not yet switched. */}
          <button className="subtle" onClick={onRefresh} disabled={blocked || refreshing}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      )}
      <div className="side-section">
        <h4>Used by</h4>
        {uses.length === 0 ? (
          <span className="field-hint">
            No role or route names this provider. It can still be picked for one reroll at
            a time.
          </span>
        ) : (
          <div className="chips" role="group" aria-label="Used by">
            {uses.map((use) => {
              const chip = chipFor(use, settings);
              return (
                <button key={`${use.scope}/${use.cid ?? ""}/${use.kind}/${use.key}`}
                        type="button" className="chip" title={chip.title}
                        onClick={() => onUse(chip.to)}>
                  {chip.label}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );
}

/** The fields a provider has once its preset is chosen. Shared by New and
 *  Edit; nothing about a model is here, because nothing about a model is a
 *  provider's to say (the server refuses one: "set this on the model"). */
function ProviderFields({ kind, preset, value, onChange, apiKey, onApiKey, keySet }: {
  kind: LLMConnectionKind; preset: ProviderPresetOption | undefined;
  value: ProviderForm; onChange: (next: ProviderForm) => void;
  apiKey: string; onApiKey: (key: string) => void; keySet: boolean;
}) {
  const set = (patch: Partial<ProviderForm>) => onChange({ ...value, ...patch });
  const locked = !!preset?.url_locked;
  return (
    <>
      <Field label="Name">
        <input type="text" value={value.name} onChange={(e) => set({ name: e.target.value })} />
      </Field>
      {kind !== "claude" && (
        <>
          <Field label="Address"
                 hint={locked ? `Fixed by the ${preset?.label ?? "provider's"} preset.`
                              : "The address that ends in /v1."}>
            <input type="text" value={value.base_url} disabled={locked}
                   onChange={(e) => set({ base_url: e.target.value })} />
          </Field>
          <Field label="API key"
                 hint={kind === "openai_compatible"
                   ? "Optional for a server that does not ask for one." : undefined}>
            <input type="password" value={apiKey} onChange={(e) => onApiKey(e.target.value)}
                   placeholder={keySet ? "A key is set — type to replace" : ""} />
          </Field>
        </>
      )}
      <Field label="Billing" hint="Subscription: calls are covered by a plan rather than charged per token.">
        <select value={value.billing}
                onChange={(e) => set({ billing: e.target.value as "" | ProviderBilling })}>
          <option value="">
            The preset&apos;s default{preset ? ` (${preset.billing})` : ""}
          </option>
          <option value="metered">Metered</option>
          <option value="subscription">Subscription</option>
        </select>
      </Field>
      {kind === "openai_compatible" && (
        <label className="checkbox-row">
          <input type="checkbox" checked={value.sampler_support === "extended"}
                 onChange={(e) => set({ sampler_support: e.target.checked ? "extended" : "" })} />
          {" "}Extended samplers: this endpoint takes top-k, min-p and repetition penalty
          (llama.cpp, vLLM, LM Studio, koboldcpp, TabbyAPI)
        </label>
      )}
    </>
  );
}

function NewProvider({ presets, presetsError, onRetry, choice, onChoose, onCancel, onCreated }: {
  presets: ProviderPresetOption[] | null; presetsError: unknown; onRetry: () => void;
  choice: ProviderPresetOption | null;
  onChoose: (preset: ProviderPresetOption | null) => void; onCancel: () => void;
  onCreated: (id: string) => Promise<void>;
}) {
  const [form, setForm] = useState<ProviderForm | null>(null);
  const [key, setKey] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  // A new choice of preset starts its form from that preset's own values.
  useEffect(() => {
    setKey("");
    setError(null);
    setForm(choice
      ? { name: choice.label, base_url: choice.base_url, billing: choice.billing,
          sampler_support: "" }
      : null);
  }, [choice]);

  if (!choice || !form) {
    return (
      <div className="form">
        <h3>New provider</h3>
        <p className="field-hint">Which kind of provider is it?</p>
        {presetsError !== null ? (
          // Not an empty chooser: with no presets read there is nothing to
          // choose from, and the reader is owed the reason and a way on.
          <div className="banner">
            Couldn&apos;t list the kinds of provider: {errorText(presetsError)}{" "}
            <button className="subtle" onClick={onRetry}>Try again</button>
          </div>
        ) : presets === null ? <p className="field-hint">Reading the presets…</p> : (
          <div className="chips" role="group" aria-label="Provider presets">
            {presets.map((p) => (
              <button key={p.id} type="button" className="chip" onClick={() => onChoose(p)}>
                {p.label}
              </button>
            ))}
          </div>
        )}
        <div className="form-actions">
          <button className="subtle" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    );
  }

  async function create() {
    if (!choice || !form || !form.name.trim()) return;
    setSaving(true);
    setError(null);
    const draft: LLMConnectionDraft = {
      kind: choice.kind, preset: choice.id, name: form.name.trim(),
    };
    if (choice.kind !== "claude" && !choice.url_locked) draft.base_url = form.base_url.trim();
    if (key) draft.api_key = key;
    draft.billing = form.billing;
    if (choice.kind === "openai_compatible") draft.sampler_support = form.sampler_support;
    try {
      const { id } = await api.createConnection(draft);
      await onCreated(id);
    } catch (err: unknown) {
      setError(err);
      setSaving(false);
    }
  }

  return (
    <div className="form">
      <h3>New provider: {choice.label}</h3>
      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      <ProviderFields kind={choice.kind} preset={choice} value={form} onChange={setForm}
                      apiKey={key} onApiKey={setKey} keySet={false} />
      <div className="form-actions">
        <button className="subtle" onClick={() => onChoose(null)}>Another preset</button>
        <button className="subtle" onClick={onCancel}>Cancel</button>
        <button className="primary" onClick={() => { void create(); }}
                disabled={saving || !form.name.trim()}>
          Create provider
        </button>
      </div>
    </div>
  );
}

/** Edit a provider. A new key or address restamps the provider's `rev`, and
 *  the Embedding role's vectors are keyed on it -- so on the provider that
 *  role embeds through (`embeds`, read off the settings view), such an edit
 *  re-embeds the library and is asked about first, in the Models page's
 *  words, and only then sent with `confirm_embedding`. The server holds the
 *  same line against what is on disk now (400 `confirm_embedding`), which
 *  another tab may have moved: its refusal is the question, in its own words. */
function EditProvider({ provider, preset, blocked, embeds, onCancel, onSaved, onDelete }: {
  provider: LLMConnectionDetail; preset: ProviderPresetOption | undefined; blocked: boolean;
  embeds: boolean;
  onCancel: () => void; onSaved: () => Promise<void>; onDelete: () => void;
}) {
  // Billing starts at what the RECORD states, "" for nothing -- never the
  // preset's value, and never a guess when the preset is unknown. Billing
  // decides which money column a provider's calls land in (spend or
  // estimated), so an edit that only renames must not turn it into a claim.
  const loadedBilling = provider.billing || "";
  const [form, setForm] = useState<ProviderForm>(() => ({
    name: provider.name, base_url: provider.base_url,
    billing: loadedBilling,
    sampler_support: provider.sampler_support === "extended" ? "extended" : "",
  }));
  const [key, setKey] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  // The re-embedding question, in the words it is asked in; null when not asking.
  const [asking, setAsking] = useState<string | null>(null);

  function patchOf(): Partial<LLMConnectionDraft> {
    const patch: Partial<LLMConnectionDraft> = { name: form.name.trim() };
    // Only a billing the user chose; "" back from a stated one is a choice too.
    if (form.billing !== loadedBilling) patch.billing = form.billing;
    if (provider.kind === "openai_compatible") patch.sampler_support = form.sampler_support;
    if (provider.kind !== "claude" && !preset?.url_locked && form.base_url !== provider.base_url) {
      patch.base_url = form.base_url.trim();
    }
    if (key) patch.api_key = key;
    return patch;
  }

  async function send(confirm: boolean) {
    setSaving(true);
    setError(null);
    const patch = patchOf();
    if (confirm) patch.confirm_embedding = true;
    try {
      await api.updateConnection(provider.id, patch);
      await onSaved();
    } catch (err: unknown) {
      if (!confirm && err instanceof ApiError && err.kind === "confirm_embedding") {
        setAsking(errorText(err));
      } else {
        setError(err);
      }
      setSaving(false);
    }
  }

  function save() {
    if (!form.name.trim()) return;
    const patch = patchOf();
    if (embeds && (patch.api_key !== undefined || patch.base_url !== undefined)) {
      setAsking(`Changing this provider's key or address re-embeds your library through `
                + `${provider.name}, which may cost money.`);
      return;
    }
    void send(false);
  }

  return (
    <div className="form">
      <h3>Edit provider</h3>
      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      <ProviderFields kind={provider.kind} preset={preset} value={form} onChange={setForm}
                      apiKey={key} onApiKey={setKey} keySet={provider.key_set} />
      {asking !== null && (
        <div className="banner" role="group" aria-label="Confirm the re-embedding">
          {asking}{" "}
          <button className="primary" disabled={saving}
                  onClick={() => { setAsking(null); void send(true); }}>
            Re-embed and save
          </button>{" "}
          <button className="subtle" onClick={() => setAsking(null)}>Not now</button>
        </div>
      )}
      <div className="form-actions">
        <button className="subtle" onClick={onDelete} disabled={blocked}>Delete</button>
        <button className="subtle" onClick={onCancel}>Cancel</button>
        <button className="primary" onClick={save}
                disabled={blocked || saving || !form.name.trim() || asking !== null}>
          Save provider
        </button>
      </div>
    </div>
  );
}

const VISION_WORDS: Record<VisionOverride, string> = {
  "": "Auto — follows the catalog.",
  on: "Yes — post images are sent.",
  off: "No — post images are not sent.",
};

type FactsForm = {
  vision: VisionOverride; prefill: boolean; post_process: "none" | "strict";
  overrides: Partial<Record<CapabilityName, "" | "yes" | "no">>;
  rates: RateForm;
  /** The model's stated size (01i), as typed: "" is not stated. */
  context_window: string; max_output: string;
  /** The model's embedding options (01h), as typed. */
  embedding: EmbeddingForm;
};

/** The two size facts the form states, in the order it shows them. */
const SIZE_FIELDS = [
  { key: "context_window", label: "Context window", side: "Window" },
  { key: "max_output", label: "Max output", side: "Max output" },
] as const;

/** Why the size fields exist, and what they are not: a second budget. */
const SIZE_HINT = "The model's own limits on this provider, when its listing does not say or "
  + "says wrong. To pack prompts smaller than the model allows, set the context budget instead.";

const SOURCE_WORDS: Record<LimitSource, string> = {
  user: "you", catalog: "catalog", unknown: "unknown",
};

const tokens = (n: number) => n.toLocaleString("en-US");

function factsForm(f: ModelFacts): FactsForm {
  return {
    vision: f.vision, prefill: !!f.prefill, post_process: f.post_process || "none",
    overrides: Object.fromEntries(OVERRIDABLE.map(({ name }) => [name, f.overrides[name] ?? ""])),
    rates: formOf(f.rates),
    context_window: f.context_window ? String(f.context_window) : "",
    max_output: f.max_output ? String(f.max_output) : "",
    embedding: embeddingForm(f.embedding),
  };
}

/** One size fact in the view: its figure and where it came from, and -- when
 *  the user's word is what counts and the listing says otherwise -- the
 *  listing's figure beside it, so a statement gone stale shows. */
function SizeLine({ label, limit }: { label: string; limit: ModelLimit | undefined }) {
  if (!limit || limit.value == null) {
    return <div><span className="field-hint">{label} unknown</span></div>;
  }
  const disagrees = limit.source === "user" && limit.catalog != null && limit.catalog !== limit.value;
  return (
    <div>
      <span className="chip on">
        {`${label} ${tokens(limit.value)} tokens (${SOURCE_WORDS[limit.source]})`}
      </span>
      {disagrees && (
        <span className="field-hint">{` The catalog says ${tokens(limit.catalog!)}.`}</span>
      )}
    </div>
  );
}

/** What is known of one model on one provider: the user's statements, what a
 *  test found, and every capability as it resolves (spec 4.2). Read-only until
 *  Edit; a save sends only what changed, so a field nobody touched is not
 *  turned into an assertion. A save that turns the Embedding role on (say,
 *  `embed: yes` over the catalog's no) embeds the library, so the server
 *  refuses it with 400 `confirm_embedding`: that refusal is the question, in
 *  its own words, and the yes resends with `confirm_embedding`. */
function ModelFactsPanel({ provider, model, blocked, factsBlocked, onChanged }: {
  provider: LLMConnectionDetail; model: string;
  /** The test call (a newer store only); `factsBlocked` the facts themselves. */
  blocked: boolean; factsBlocked: boolean; onChanged: () => void;
}) {
  const [facts, setFacts] = useState<ModelFacts | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [form, setForm] = useState<FactsForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  // The embedding question, in the server's words; null when not asking.
  const [asking, setAsking] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const ratesId = useId();
  /** `?edit=rates` (or `?edit=limits`) opens the form once the facts are read
   *  and the store is known to take writes, and puts the caret in the Input
   *  box (or the Context window box). A store that is locked (or not yet
   *  known) keeps the view, as its disabled Edit does. Once per arrival: a
   *  later reload (a landed test) must not throw a reader back into a form,
   *  and leaving the form clears the param, which re-arms it for the next
   *  link that carries one. */
  const askedEdit = params.get("edit");
  const asked = askedEdit === EDIT_RATES || askedEdit === EDIT_LIMITS ? askedEdit : null;
  const openedFromUrl = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const windowRef = useRef<HTMLInputElement>(null);
  const [focusOn, setFocusOn] = useState<string | null>(null);

  const load = useCallback(() => api.readModelFacts(provider.id, model).then(setFacts), [provider.id, model]);
  useEffect(() => { load().catch(setError); }, [load]);
  useEffect(() => {
    if (!asked) { openedFromUrl.current = false; return; }
    if (!facts || factsBlocked || openedFromUrl.current) return;
    openedFromUrl.current = true;
    setForm(factsForm(facts));
    setMode("edit");
    setFocusOn(asked);
  }, [facts, asked, factsBlocked]);
  useEffect(() => {
    if (mode !== "edit" || !focusOn) return;
    (focusOn === EDIT_LIMITS ? windowRef : inputRef).current?.focus();
    setFocusOn(null);
  }, [mode, focusOn]);
  // A landed test moves this model's facts and its badges -- whichever page
  // started it (the runs are shared, so a test begun from the /models picker
  // is the one Test… rejoins here).
  const tests = useModelTests((landed) => {
    if (landed.provider !== provider.id || landed.model !== model) return;
    load().catch(setError);
    onChanged();
  });

  if (!facts) {
    return error != null
      ? <div className="banner"><ErrorNote err={error} /></div>
      : <p className="field-hint">Reading what is known of {model}…</p>;
  }

  // Only the adapter's `no` is past testing (the server refuses the preset's
  // `never`); a catalog's, the name rule's or the user's `no` is one a passing
  // test outranks, so a test may challenge it.
  const testable = TESTABLE.filter((c) => {
    const found = facts.capabilities[c];
    return !(found?.value === "no" && found.source === "adapter");
  });
  // What the server could not read is not "nothing stated": saving over it
  // would replace the user's word, so the panel says so and offers no save.
  const unreadable = facts.unreadable === true;
  const unreadableNote = unreadable && (
    <div className="banner" role="status">
      {facts.unreadable_reason === "mangled"
        ? "This provider's model facts file is not valid JSON (it may have been edited by "
          + "hand), so nothing here can be edited until the file is fixed or removed. "
          + "Saving now would replace every model's facts in it."
        : "This model's facts file could not be read, so it cannot be edited until it "
          + "has synced."}
    </div>
  );
  const stated = OVERRIDABLE.filter(({ name }) => facts.overrides[name]);
  // Embedding options for a model that may embed: a known `no` has nothing
  // to send them to (01h §4.4).
  const embeds = facts.capabilities.embed?.value !== "no";
  const optionsLine = optionsSummary(
    facts.embedding && !facts.embedding_invalid ? facts.embedding : null);
  // A rate the entry carries, zero included: a stated $0 is a price.
  const statedRates = RATE_FIELDS.flatMap(({ key, label }) => {
    const value = facts.rates?.[key];
    return typeof value === "number" ? [{ key, label, value }] : [];
  });

  /** Back to the view, and `?edit` out of the URL with it (replacing the entry,
   *  not pushing one): left in, a refresh would open the form again over a
   *  save that already landed or an edit the reader abandoned. */
  function leave() {
    setMode("view");
    if (params.has("edit")) {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        next.delete("edit");
        return next;
      }, { replace: true });
    }
  }

  async function save(confirm = false) {
    if (!facts || !form) return;
    const was = factsForm(facts);
    const body: ModelFactsUpdate = { model };
    if (form.vision !== was.vision) body.vision = form.vision;
    if (form.prefill !== was.prefill) body.prefill = form.prefill;
    if (form.post_process !== was.post_process) body.post_process = form.post_process;
    const overrides = Object.fromEntries(OVERRIDABLE.map(({ name }) => name)
      .filter((name) => (form.overrides[name] ?? "") !== (was.overrides[name] ?? ""))
      .map((name) => [name, form.overrides[name] ?? ""]));
    if (Object.keys(overrides).length) body.overrides = overrides;
    // Rates only when a box changed, like every other field. Every box empty
    // clears them (`{}`); otherwise the filled boxes, an empty one LEFT OUT —
    // the store refuses a present null — and a zero kept, being a price.
    if (RATE_FIELDS.some(({ key }) => form.rates[key].trim() !== was.rates[key].trim())) {
      body.rates = filled(form.rates) ? entryOf(form.rates) : {};
    }
    // A size only when its box changed: emptied, a stated one is removed
    // (`0`); a box that was empty and still is sends nothing.
    for (const { key } of SIZE_FIELDS) {
      const now = form[key].trim();
      if (now !== was[key].trim()) body[key] = now === "" ? 0 : Number(now);
    }
    // The options only when they send something else: a block the store
    // replaces whole (`{}` removes it). A stored block the server refused is
    // not what its form reads back as (an unknown input type reads as none),
    // so the form is always sent over one: saving is how it is repaired.
    const options = blockOf(form.embedding);
    const repairing = embeds && facts.embedding_invalid === true;
    if (repairing || !sameBlock(options, blockOf(was.embedding))) body.embedding = options;
    if (confirm) body.confirm_embedding = true;
    setSaving(true);
    setError(null);
    try {
      setFacts(await api.putModelFacts(provider.id, body));
      leave();
      onChanged();
    } catch (err: unknown) {
      if (!confirm && err instanceof ApiError && err.kind === "confirm_embedding") {
        setAsking(errorText(err));
      } else {
        setError(err);
      }
    } finally {
      setSaving(false);
    }
  }

  const back = <Link className="column-back" to={providerPath(provider.id)}>‹ {provider.name}</Link>;

  if (mode === "edit" && form) {
    const set = (patch: Partial<FactsForm>) => setForm({ ...form, ...patch });
    // A rate typed with no pair to price against: one base rate alone, or a
    // cache rate alone -- a base box left EMPTY, which only the form can say.
    // A filled box whose value the store refuses (a negative) is not this: it
    // is sent, and the store's own reason is shown.
    const halfRated = filled(form.rates) && !hasBase(form.rates);
    // A size is a whole number of tokens, or nothing; the store refuses the
    // rest (a max output above the window included) in its own words.
    const badSize = SIZE_FIELDS.some(({ key }) => {
      const typed = form[key].trim();
      return typed !== "" && !/^[1-9][0-9]*$/.test(typed);
    });
    return (
      <div className="form">
        {back}
        <h3>Edit {model}</h3>
        {unreadableNote}
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
        <Field label="Reads images"
               hint="Auto follows the catalog. No stops post images and asserts nothing about the model.">
          <select value={form.vision}
                  onChange={(e) => set({ vision: e.target.value as VisionOverride })}>
            <option value="">Auto</option>
            <option value="on">Yes</option>
            <option value="off">No</option>
          </select>
        </Field>
        <label className="checkbox-row">
          <input type="checkbox" checked={form.prefill}
                 onChange={(e) => set({ prefill: e.target.checked })} />
          {" "}Continue replies by prefill
        </label>
        <div className="field-hint">
          Send a cut-short reply back as the start of the model&apos;s own turn. Only for models
          that continue a trailing assistant message; the rest get an instruction instead.
        </div>
        <Field label="Prompt post-processing"
               hint="Strict folds system messages into user turns and starts the sequence with a user turn.">
          <select value={form.post_process}
                  onChange={(e) => set({ post_process: e.target.value as "none" | "strict" })}>
            <option value="none">None</option>
            <option value="strict">Strict</option>
          </select>
        </Field>
        <h4>Capability overrides</h4>
        <div className="field-hint">
          Your word on what this model can do, where discovery says nothing. It cannot claim
          past what the provider can do at all.
        </div>
        {OVERRIDABLE.map(({ name, label }) => (
          <Field key={name} label={label}>
            <select aria-label={`${label}: override`} value={form.overrides[name] ?? ""}
                    onChange={(e) => set({ overrides: { ...form.overrides,
                      [name]: e.target.value as "" | "yes" | "no" } })}>
              <option value="">As found</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </Field>
        ))}
        <h4>Rates</h4>
        <div className="field-hint">
          This model&apos;s price on this provider, in dollars per 1,000 tokens. Used before your
          pricing table, for calls whose provider reports no price. Empty every box to state none.
        </div>
        <RateFields value={form.rates} onChange={(rates) => set({ rates })}
                    idPrefix={ratesId} subject={model} disabled={saving}
                    inputRef={inputRef} />
        {halfRated && <div className="field-hint error">Input and output are both needed.</div>}
        <h4>Size</h4>
        <div className="field-hint">{SIZE_HINT}</div>
        {SIZE_FIELDS.map(({ key, label }) => (
          <Field key={key} label={label}>
            <input type="number" min={1} step={1} inputMode="numeric" placeholder="Not stated"
                   value={form[key]} disabled={saving}
                   ref={key === "context_window" ? windowRef : undefined}
                   onChange={(e) => set({ [key]: e.target.value })} />
          </Field>
        ))}
        {badSize && <div className="field-hint error">Sizes are whole numbers of tokens.</div>}
        {embeds && (
          <EmbeddingOptionsFields model={model} value={form.embedding} disabled={saving}
                                  onChange={(embedding) => set({ embedding })} />
        )}
        {asking !== null && (
          <div className="banner" role="group" aria-label="Confirm the embedding">
            {asking}{" "}
            <button className="primary" disabled={saving}
                    onClick={() => { setAsking(null); void save(true); }}>
              Embed and save
            </button>{" "}
            <button className="subtle" onClick={() => setAsking(null)}>Not now</button>
          </div>
        )}
        <div className="form-actions">
          <button className="subtle" onClick={() => { setAsking(null); setError(null); leave(); }}>
            Cancel
          </button>
          <button className="primary" onClick={() => { void save(); }}
                  disabled={factsBlocked || saving || unreadable || halfRated || badSize
                            || !dimensionsTyped(form.embedding) || asking !== null}>
            Save facts
          </button>
        </div>
      </div>
    );
  }

  const verified = Object.entries(facts.verified);
  return (
    <div className="detail-view">
      <div className="detail-main">
        {back}
        <h3>{model}</h3>
        {unreadableNote}
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
        <div className="detail-rendered">
          <CapabilityBadges capabilities={facts.capabilities} />
          <h4>Tested</h4>
          {verified.length === 0 ? (
            <p className="field-hint">Not tested on this provider as it is now.</p>
          ) : (
            <ul>
              {verified.map(([cap, r]) => (
                <li key={cap}>
                  {cap}: {r.ok ? "works"
                    : `failed — ${mismatchLine(r) ?? r.error ?? "no reason given"}`}
                  {r.at ? ` · ${new Date(r.at).toLocaleString()}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <aside className="detail-sidebar" aria-label={model}>
        <div className="form-actions">
          <button className="subtle" disabled={factsBlocked}
                  onClick={() => { setForm(factsForm(facts)); setMode("edit"); }}>
            Edit
          </button>
          <button className="subtle" disabled={blocked || testable.length === 0}
                  onClick={() => setTesting(true)}>
            Test…
          </button>
        </div>
        <div className="side-section">
          <h4>Reads images</h4>
          <span className="field-hint">{VISION_WORDS[facts.vision]}</span>
        </div>
        <div className="side-section">
          <h4>Keep writing</h4>
          <span className="chip on">{facts.prefill ? "prefill" : "instruction"}</span>
        </div>
        <div className="side-section">
          <h4>Prompt post-processing</h4>
          <span className="chip on">{facts.post_process || "none"}</span>
        </div>
        <div className="side-section">
          <h4>Rates</h4>
          {statedRates.length === 0 ? (
            <span className="field-hint">
              None stated — your pricing table is used if it covers this model.
            </span>
          ) : (
            <div className="chips">
              {statedRates.map(({ key, label, value }) => (
                <span key={key} className="chip on">{`${label} ${perThousand(value)}`}</span>
              ))}
            </div>
          )}
        </div>
        <div className="side-section">
          <h4>Size</h4>
          {SIZE_FIELDS.map(({ key, side }) => (
            <SizeLine key={key} label={side}
                      limit={key === "context_window" ? facts.limits?.window : facts.limits?.max_output} />
          ))}
        </div>
        {embeds && (
          <div className="side-section">
            <h4>Embedding options</h4>
            {facts.embedding_invalid ? (
              <span className="field-hint error">
                {`Invalid — embedding is off with this model: ${facts.embedding_invalid_reason ?? ""}`}
              </span>
            ) : optionsLine ? (
              <span className="chip on">{optionsLine}</span>
            ) : (
              <span className="field-hint">None — texts are sent as they are.</span>
            )}
          </div>
        )}
        <div className="side-section">
          <h4>Capability overrides</h4>
          {stated.length === 0 ? <span className="field-hint">None — discovery decides.</span> : (
            <div className="chips">
              {stated.map(({ name, label }) => (
                <span key={name} className="chip on">{label}: {facts.overrides[name]}</span>
              ))}
            </div>
          )}
        </div>
      </aside>
      {testing && (
        <TestCallDialog provider={provider.id} model={model} capabilities={testable}
                        tests={tests} onClose={() => setTesting(false)} />
      )}
    </div>
  );
}
