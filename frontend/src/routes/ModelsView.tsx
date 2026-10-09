import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api, type CapabilityNeed, type DecisionMode, type EmbeddingCard, type GenerativeRole,
  type InferenceRole, type InferenceSelection, type InferenceSettings, type InferenceWrite,
  type ModelCapabilities, type ResolvedSelection, type RoleCard, type RouteRow, type RouteUse,
} from "../api/client";
import { errorText } from "../api/errors";
import { onConfigChanged } from "../appEvents";
import { ErrorNote } from "../components/ErrorNote";
import { ControlsReadout } from "../components/inference/ControlsReadout";
import { InferenceBanner } from "../components/inference/InferenceBanner";
import { migrationBanner } from "../components/inference/migration";
import { PresetSelect } from "../components/inference/PresetSelect";
import { ProviderModelPicker } from "../components/inference/ProviderModelPicker";
import { useInferenceSettings } from "../components/inference/useInferenceSettings";
import {
  CHOOSE_A_MODEL, describe, droppedFallbackWords, inheritedPreset, inheritWords, ROLE_LABEL, ROLE_NEEDS, routePinNeeds,
  routePresetWords, wantsModel,
} from "../components/inference/selection";
import { ColumnSection, PageShell } from "../components/PageShell";
import { providerPath } from "../providerPaths";

const GENERATIVE: GenerativeRole[] = ["primary", "fast", "decision"];

/** How an unset role reads (spec 4.4). Wording only: what it resolves to is
 *  the view's `inherits`, never worked out here. */
const SAME_AS: Partial<Record<InferenceRole, string>> = {
  fast: "Same as Primary", decision: "Same as Fast",
};
/** The role an unset role reads through, for prose ("the same as Primary"). */
const SAME_AS_ROLE: Partial<Record<InferenceRole, string>> = {
  fast: "Primary", decision: "Fast",
};

/** The four capability warnings of spec 10, verbatim. The first names the
 *  role it is on, so a Fast model that cannot generate says Fast. */
export const WARNINGS = {
  generate: (role: string) => `This model can't generate text, so it can't be ${role}.`,
  vision: "This route sends images; the chosen model is unverified for vision.",
  decide: "No native decision API; structured generation will be used.",
  embed: "This model can't create embeddings.",
};

/** How a decision is answered, by the resolution's `decision_mode` (spec 10,
 *  I9) -- what it says, not a warning: `native` is the provider's decisions
 *  endpoint, and `structured` on a model that could also decide natively is
 *  no missing API. Structured on one with none is `WARNINGS.decide`. */
export const DECIDE_WORDS = {
  native: "Answered by the provider's decisions endpoint.",
  structured: "Answered by structured generation.",
};

const rolePath = (role: InferenceRole) => `/models/role/${role}`;
const routePath = (key: string) => `/models/route/${encodeURIComponent(key)}`;

function isRole(value: string): value is InferenceRole {
  return value in ROLE_LABEL;
}

/** The warning one capabilities answer gives for a model, or null. Reads
 *  only what the server grouped: a hidden model is one it knows cannot. How a
 *  decision is answered is the resolution's to say (`DecideNote`), so a
 *  decide answer warns only of a model that can do neither. */
function warningOf(answer: ModelCapabilities, model: string, role: string): string | null {
  const hidden = answer.reason !== null || answer.hidden.some((h) => h.id === model);
  switch (answer.need) {
    case "generate":
      return hidden ? WARNINGS.generate(role) : null;
    case "embed":
      return hidden ? WARNINGS.embed : null;
    case "vision":
      return answer.groups.unverified.some((r) => r.id === model) ? WARNINGS.vision : null;
    case "decide":
      return hidden ? WARNINGS.generate(role) : null;
  }
}

/** The capabilities API's answer for `model` on `provider` against `need`,
 *  narrowed to that model; null until it lands, and when it fails. Nothing to
 *  ask without all three. */
function useCapabilities(provider: string, model: string,
                         need: CapabilityNeed | null): ModelCapabilities | null {
  const [answer, setAnswer] = useState<ModelCapabilities | null>(null);
  // Asked again on any model-settings change -- a landed test, a facts edit
  // (both announce) -- or a warning the test just disproved outlives it.
  const [asked, setAsked] = useState(0);
  useEffect(() => onConfigChanged(() => setAsked((n) => n + 1)), []);
  useEffect(() => {
    setAnswer(null);
    if (!provider || !model || !need) return;
    let current = true;
    api.readConnectionCapabilities(provider, need, model)
      .then((a) => { if (current) setAnswer(a); })
      // A failed read warns of nothing: the role's `problem` is the seam's word.
      .catch(() => {});
    return () => { current = false; };
  }, [provider, model, need, asked]);
  return answer;
}

/** The capability warning for `model` on `provider` against `need`. */
function useWarning(provider: string, model: string, need: CapabilityNeed | null,
                    role: string): string | null {
  const answer = useCapabilities(provider, model, need);
  return answer ? warningOf(answer, model, role) : null;
}

function Warning({ text }: { text: string | null }) {
  return text ? <p className="field-hint field-warning" role="note">{text}</p> : null;
}

function Problem({ text }: { text: string | null }) {
  return text ? <p className="field-hint problem">{text}</p> : null;
}

/** How the resolved model `sel` answers a decision, keyed on the server's
 *  `decision_mode` (I9). A refused one (`""`) says nothing here: its
 *  `problem` is the refusal's own sentence. Which structured sentence applies
 *  is read off the model's `decide_native`, the one thing the mode does not
 *  carry -- wording, never a second rule about what runs. */
function DecideNote({ mode, sel }: { mode: DecisionMode; sel: ResolvedSelection | null }) {
  const answer = useCapabilities(sel?.provider ?? "", sel?.model ?? "",
                                 mode === "structured" ? "decide" : null);
  if (mode === "native") return <p className="field-hint">{DECIDE_WORDS.native}</p>;
  if (mode !== "structured" || !answer || !sel) return null;
  const row = [...answer.groups.fits, ...answer.groups.unverified].find((r) => r.id === sel.model);
  if (!row) return null;
  return row.capabilities.decide_native?.value === "yes"
    ? <p className="field-hint">{DECIDE_WORDS.structured}</p>
    : <Warning text={WARNINGS.decide} />;
}

/** The routes that use Decision (the view's `uses`), and for each one that
 *  Decision does not itself supply, the role it inherits -- with Decision
 *  unset the decide routes still use it, and setting it moves them. */
function DecisionRoutes({ routes }: { routes: RouteRow[] }) {
  const using = routes.filter((r) => r.uses === "decision");
  if (using.length === 0) return <p className="field-hint">No route uses Decision yet.</p>;
  return (
    <ul aria-label="Routes using Decision">
      {using.map((r) => (
        <li key={r.key}>
          <Link to={routePath(r.key)}>{r.label}</Link>
          {r.role && r.role !== "decision" && (
            <span className="field-hint"> — inherits {ROLE_LABEL[r.role]}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** What one role runs on, read-only: its line, its problem, its warning, what
 *  its preset sends, and -- on Decision -- the routes using it. Both the
 *  overview's cards and a selected role's detail are this. */
function RoleSummary({ role, settings }: { role: InferenceRole; settings: InferenceSettings }) {
  if (role === "embedding") {
    const card = settings.roles.embedding;
    return card ? <EmbeddingSummary card={card} /> : null;
  }
  const fallbackName = settings.providers.find((p) => p.id === settings.roles[role].fallback.provider)
    ?.name ?? settings.roles[role].fallback.provider;
  return <GenerativeSummary role={role} card={settings.roles[role]} routes={settings.routes}
                            fallbackName={fallbackName} />;
}

function GenerativeSummary({ role, card, routes, fallbackName }:
  { role: GenerativeRole; card: RoleCard; routes: RouteRow[]; fallbackName: string }) {
  const sel = card.resolves;
  const decision = role === "decision";
  // Decision's own line is `DecideNote`, keyed on how it resolved.
  const warning = useWarning(sel?.provider ?? "", sel?.model ?? "",
                             decision ? null : ROLE_NEEDS[role][0], ROLE_LABEL[role]);
  // The resolver's own verdict (`fallback_missing`), never a picker's: a
  // model the capabilities API hides on a guess (the name rule) is still sent.
  const { provider: fbProvider, model: fbModel } = card.fallback;
  const dropped = droppedFallbackWords(card.fallback_missing, ROLE_LABEL[role],
                                       fbProvider && fbModel ? `${fallbackName} ▸ ${fbModel}` : "",
                                       card.fallback_problem);
  const unset = !card.stored.provider && !card.stored.model;
  return (
    <>
      {unset && SAME_AS[role] ? (
        <p>{SAME_AS[role]} — {describe(card.inherits)}</p>
      ) : (
        <p>{sel ? describe(sel) : "Not set."}</p>
      )}
      <Problem text={card.problem} />
      <Warning text={warning} />
      {decision && <DecideNote mode={card.decision_mode} sel={sel} />}
      <Problem text={dropped} />
      {sel && <ControlsReadout presetId={sel.preset} provider={sel.provider} model={sel.model}
                               operation={decision ? "decide" : undefined} />}
      {decision && <DecisionRoutes routes={routes} />}
    </>
  );
}

function EmbeddingSummary({ card }: { card: EmbeddingCard }) {
  const { provider, model } = card.stored;
  const warning = useWarning(provider, model, "embed", ROLE_LABEL.embedding);
  return (
    <>
      <p>
        {card.on && card.resolves
          ? describe(card.resolves, false)
          : "Off — nothing is embedded, and semantic recall is not used."}
      </p>
      {/* Why it is off, in the server's words: the choice above may name a
          provider with no key, or one that cannot embed. */}
      {!card.on && <Problem text={card.problem ?? null} />}
      <Warning text={warning} />
    </>
  );
}

/** The Embedding role's problem as the column flags it: only a choice that
 *  embeds nothing. A library that chose no provider has Embedding off by
 *  choice, which its card says, and is not a problem. */
function embeddingProblem(card: EmbeddingCard | undefined): string | null {
  if (!card || card.on || (!card.stored.provider && !card.stored.model)) return null;
  return card.problem ?? null;
}

/** `/models`: which provider, model and preset each role runs on, and -- under
 *  Advanced -- which role or model each route uses (spec 10).
 *
 *  A page that owns the screen: the roles and the routes are the context
 *  column's records (`ColumnSection`s), selected by URL (`/models/role/<r>`,
 *  `/models/route/<k>`, where a provider's Used by chips land), and the detail
 *  is main's. The set is fixed, so there is no + New. Everything is read-only
 *  until Edit, and a Save sends only the one role or route it edited.
 *
 *  Library-wide only. A campaign's overrides are edited from that campaign's
 *  Inspector, so a Used by chip for a campaign's use opens that campaign,
 *  never this page.
 *
 *  Every "resolves to", "inherit" and problem is the server's (rule 2), and
 *  each warning is the capabilities API's answer for the model concerned. An
 *  Embedding change that re-embeds the library asks first, and only then is
 *  sent with `confirmEmbedding` -- the server refuses it without (rule 1). */
export default function ModelsView() {
  const params = useParams();
  const roleParam = params.role ?? "";
  const routeParam = params.key ?? "";
  const navigate = useNavigate();

  // Read again while an upgrade is on its way, so the page unlocks when it
  // lands rather than when it is left.
  const { settings, error, install } = useInferenceSettings();
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [advanced, setAdvanced] = useState(!!routeParam);
  // Another record: start from its read-only view. A route opened by address
  // brings the Advanced rows out with it.
  useEffect(() => {
    setMode("view");
    if (routeParam) setAdvanced(true);
  }, [roleParam, routeParam]);

  // Gated on the layout, not on the migration finishing: a campaign the
  // migration has not reached keeps it pending, and the library's own
  // settings are writable at format 2 all the same.
  const blocked = !!settings && (settings.newer || settings.format !== "2");
  const banner = migrationBanner(settings);

  const roles: InferenceRole[] = settings
    ? [...GENERATIVE, ...(settings.roles.embedding ? ["embedding" as const] : [])] : [];
  const role = isRole(roleParam) && roles.includes(roleParam) ? roleParam : null;
  const route = settings?.routes.find((r) => r.key === routeParam) ?? null;

  async function save(body: InferenceWrite, confirmEmbedding = false) {
    const next = confirmEmbedding
      ? await api.putInferenceSettings(body, { confirmEmbedding: true })
      : await api.putInferenceSettings(body);
    install(next);
    setMode("view");
  }

  const column = (
    <>
      <ColumnSection label="Roles" count={settings ? roles.length : undefined}>
        {roles.map((r) => {
          const problem = r === "embedding" ? embeddingProblem(settings?.roles.embedding)
            : settings?.roles[r].problem;
          return (
            <Link key={r} to={rolePath(r)}
                  className={"column-row" + (r === roleParam ? " active" : "")
                             + (problem ? " alert" : "")}>
              <span className="column-row-label">{ROLE_LABEL[r]}</span>
              {problem && <span className="column-row-count alert" title={problem}>problem</span>}
            </Link>
          );
        })}
      </ColumnSection>
      <ColumnSection label="Routing" count={settings?.routes.length}>
        <div className="column-actions">
          <button type="button" className="subtle" aria-expanded={advanced}
                  onClick={() => setAdvanced((v) => !v)}>
            Advanced
          </button>
        </div>
        {!advanced && (
          <p className="column-empty">Which role or model each kind of call runs on.</p>
        )}
        {advanced && settings?.routes.map((r) => (
          <Link key={r.key} to={routePath(r.key)}
                className={"column-row" + (r.key === routeParam ? " active" : "")
                           + (r.problem ? " alert" : "")}>
            <span className="column-row-label">{r.label}</span>
            {r.problem && <span className="column-row-count alert" title={r.problem}>problem</span>}
          </Link>
        ))}
      </ColumnSection>
    </>
  );

  let body;
  if (!settings) {
    body = error != null ? null : <p className="field-hint">Reading the model settings…</p>;
  } else if (roleParam && !role) {
    body = <p className="empty-state">No role is called {roleParam}.</p>;
  } else if (routeParam && !route) {
    body = <p className="empty-state">No route is called {routeParam}.</p>;
  } else if (role) {
    body = mode === "edit"
      ? <RoleForm key={role} role={role} settings={settings} blocked={blocked}
                  onCancel={() => setMode("view")} onSave={save} />
      : <RoleDetail role={role} settings={settings} blocked={blocked}
                    onEdit={() => setMode("edit")} onOpen={(to) => navigate(to)} />;
  } else if (route) {
    body = mode === "edit"
      ? <RouteForm key={route.key} row={route} settings={settings} blocked={blocked}
                   onCancel={() => setMode("view")} onSave={save} />
      : <RouteDetail row={route} settings={settings} blocked={blocked}
                     onEdit={() => setMode("edit")} onOpen={(to) => navigate(to)} />;
  } else {
    body = (
      <>
        <p className="config-copy">
          Four roles say which model does which kind of work. Every call runs on one of them
          unless a route under <em>Advanced</em> sends it somewhere else.
        </p>
        <div className="hub-grid">
          {roles.map((r) => (
            <section key={r} className="hub-card" aria-label={ROLE_LABEL[r]}>
              <div className="hub-card-head">
                <h2><Link to={rolePath(r)}>{ROLE_LABEL[r]}</Link></h2>
              </div>
              <div className="hub-card-body">
                <RoleSummary role={r} settings={settings} />
              </div>
            </section>
          ))}
        </div>
      </>
    );
  }

  return (
    <PageShell column={column} columnLabel="Models">
      <div className="page view-anim">
        <div className="page-head">
          <h1 className="page-h1">Models</h1>
        </div>
        <InferenceBanner status={banner} />
        {error != null && <div className="banner"><ErrorNote err={error} /></div>}
        {body}
      </div>
    </PageShell>
  );
}

/** One selection's parts in the sidebar: the provider is another record, so
 *  it is a chip that opens it. */
function SelectionSide({ label, sel, settings, onOpen, withPreset = true }:
  { label: string; sel: { provider: string; model: string; preset?: string };
    settings: InferenceSettings; onOpen: (to: string) => void; withPreset?: boolean }) {
  const provider = settings.providers.find((p) => p.id === sel.provider);
  const preset = sel.preset ? settings.presets.find((p) => p.id === sel.preset) : undefined;
  return (
    <div className="side-section">
      <h4>{label}</h4>
      {!sel.provider && !sel.model ? <span className="field-hint">Not set.</span> : (
        <div className="chips">
          {sel.provider && (
            <button type="button" className="chip" onClick={() => onOpen(providerPath(sel.provider))}>
              {provider?.name ?? `${sel.provider} (missing provider)`}
            </button>
          )}
          <span className="chip on">{sel.model || "default model"}</span>
          {withPreset && sel.preset && <span className="chip on">{preset?.name ?? sel.preset}</span>}
        </div>
      )}
    </div>
  );
}

function RoleDetail({ role, settings, blocked, onEdit, onOpen }:
  { role: InferenceRole; settings: InferenceSettings; blocked: boolean;
    onEdit: () => void; onOpen: (to: string) => void }) {
  const label = ROLE_LABEL[role];
  const generative = role === "embedding" ? null : settings.roles[role];
  const stored = role === "embedding" ? settings.roles.embedding?.stored : generative?.stored;
  return (
    <div className="detail-view">
      <div className="detail-main">
        <h3>{label}</h3>
        <div className="detail-rendered">
          <RoleSummary role={role} settings={settings} />
        </div>
      </div>
      <aside className="detail-sidebar" aria-label={label}>
        <div className="form-actions">
          <button className="subtle" onClick={onEdit} disabled={blocked}>Edit</button>
        </div>
        {stored && (
          <SelectionSide label="Chosen here" sel={stored} settings={settings} onOpen={onOpen}
                         withPreset={role !== "embedding"} />
        )}
        {generative && (
          <SelectionSide label="Fallback" sel={generative.fallback} settings={settings}
                         onOpen={onOpen} />
        )}
      </aside>
    </div>
  );
}

/** A selection being edited: provider, then a model that can do what the
 *  role needs, then -- unless it is Embedding -- a preset; with the warning
 *  and the controls readout for what is picked. */
function SelectionFields({ label, needs, sel, onChange, settings, blocked, withPreset,
                           presetLabel, warn, role }:
  { label: string; needs: CapabilityNeed[]; sel: InferenceSelection;
    onChange: (sel: InferenceSelection) => void; settings: InferenceSettings;
    blocked: boolean; withPreset: boolean; presetLabel: string;
    /** The need whose warning applies to what is picked, if any. */
    warn: CapabilityNeed | null; role: string }) {
  const warning = useWarning(sel.provider, sel.model, warn, role);
  // A selection with no provider is not used at all (the cascade reads a slot
  // only when it names one), so a preset chosen alone would be stored and
  // silently dormant. Held until a provider is chosen -- unless one is already
  // stored that way, which stays clearable and says what it is.
  const dormant = !sel.provider && !!sel.preset;
  return (
    <fieldset className="model-selection" aria-label={label}>
      <ProviderModelPicker needs={needs} providers={settings.providers} disabled={blocked}
                           value={{ provider: sel.provider, model: sel.model }}
                           onChange={(v) => onChange({ ...sel, ...v })} />
      <Warning text={warning} />
      {/* `withPreset` is every generative selection: Embedding's provider
          alone is embedding off, never a missing model. */}
      {withPreset && wantsModel(sel) && <p className="field-hint">{CHOOSE_A_MODEL}</p>}
      {withPreset && (
        <>
          <label className="field">
            <span>{presetLabel}</span>
            <PresetSelect label={presetLabel} value={sel.preset} presets={settings.presets}
                          emptyLabel="Provider defaults"
                          disabled={blocked || (!sel.provider && !sel.preset)}
                          onChange={(preset) => onChange({ ...sel, preset })} />
          </label>
          {dormant && (
            <p className="field-hint">
              A preset with no provider is not used. Choose a provider, or clear it.
            </p>
          )}
          <ControlsReadout presetId={sel.preset} provider={sel.provider} model={sel.model}
                           operation={needs.includes("decide") ? "decide" : undefined} />
        </>
      )}
    </fieldset>
  );
}

const same = (a: InferenceSelection, b: InferenceSelection) =>
  a.provider === b.provider && a.model === b.model && a.preset === b.preset;

function RoleForm({ role, settings, blocked, onCancel, onSave }:
  { role: InferenceRole; settings: InferenceSettings; blocked: boolean;
    onCancel: () => void; onSave: (body: InferenceWrite, confirm?: boolean) => Promise<void> }) {
  const label = ROLE_LABEL[role];
  const card = role === "embedding" ? null : settings.roles[role];
  const embedding = role === "embedding" ? settings.roles.embedding : undefined;
  const storedSel: InferenceSelection = card?.stored
    ?? { ...(embedding?.stored ?? { provider: "", model: "" }), preset: "" };
  const storedFallback: InferenceSelection = card?.fallback ?? { provider: "", model: "", preset: "" };

  const [sel, setSel] = useState<InferenceSelection>(storedSel);
  const [fallback, setFallback] = useState<InferenceSelection>(storedFallback);
  const [fallbackOpen, setFallbackOpen] = useState(!!storedFallback.provider);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  // The re-embedding question, in the words it is asked in; null when not asking.
  const [asking, setAsking] = useState<string | null>(null);

  function body(): InferenceWrite {
    if (role === "embedding") {
      return { roles: { embedding: { selection: { provider: sel.provider, model: sel.model } } } };
    }
    // The fallback only when it was touched: a save of the selection must not
    // put back a fallback another tab changed meanwhile.
    const entry: { selection: InferenceSelection; fallback?: InferenceSelection } =
      { selection: sel };
    if (!same(fallback, storedFallback)) entry.fallback = fallback;
    return { roles: { [role]: entry } };
  }

  async function send(confirm: boolean) {
    setSaving(true);
    setError(null);
    try {
      await onSave(body(), confirm);
    } catch (err: unknown) {
      const kind = typeof err === "object" && err !== null
        ? (err as { kind?: unknown }).kind : undefined;
      // The server compares against what is on disk now, which another tab
      // may have moved: its refusal is the question, in its own words.
      if (kind === "confirm_embedding" && !confirm) setAsking(errorText(err));
      else setError(err);
      setSaving(false);
    }
  }

  // A provider with no model is never saved (the server refuses it): the
  // selection, and the fallback when this save sends it.
  const incomplete = role !== "embedding"
    && (wantsModel(sel) || (!same(fallback, storedFallback) && wantsModel(fallback)));

  function save() {
    const changes = sel.provider !== storedSel.provider || sel.model !== storedSel.model;
    if (role === "embedding" && sel.provider && sel.model && changes) {
      const name = settings.providers.find((p) => p.id === sel.provider)?.name ?? sel.provider;
      setAsking(`Changing the embedding model re-embeds your library through ${name}, `
                + "which may cost money.");
      return;
    }
    void send(false);
  }

  return (
    <div className="form">
      <h3>Edit {label}</h3>
      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      {SAME_AS[role] && card && (
        <p className="field-hint">
          With no provider chosen, {label} is the same as {SAME_AS_ROLE[role]}:{" "}
          {describe(card.inherits)}.
        </p>
      )}
      {role === "embedding" && (
        <p className="field-hint">
          With no provider chosen, nothing is embedded and semantic recall is off.
        </p>
      )}
      {/* A change clears the re-embedding question (as the setup wizard
          does): "Re-embed and save" sends what is picked when it is pressed,
          so it may only be answering a question about that. */}
      <SelectionFields label={label} needs={ROLE_NEEDS[role]} sel={sel}
                       onChange={(next) => { setSel(next); setAsking(null); }}
                       settings={settings} blocked={blocked} withPreset={role !== "embedding"}
                       presetLabel="Preset" warn={ROLE_NEEDS[role][0]} role={label} />
      {role !== "embedding" && (
        <>
          <button type="button" className="subtle" aria-expanded={fallbackOpen}
                  onClick={() => setFallbackOpen((v) => !v)}>
            Fallback
          </button>
          {fallbackOpen && (
            <>
              <p className="field-hint">Tried once when {label} cannot answer.</p>
              <SelectionFields label={`${label} fallback`} needs={ROLE_NEEDS[role]} sel={fallback}
                               onChange={setFallback} settings={settings} blocked={blocked}
                               withPreset presetLabel="Fallback preset"
                               warn={ROLE_NEEDS[role][0]} role={label} />
            </>
          )}
        </>
      )}
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
        <button className="subtle" onClick={onCancel}>Cancel</button>
        <button className="primary" onClick={save}
                disabled={blocked || saving || asking !== null || incomplete}>
          Save
        </button>
      </div>
    </div>
  );
}

/** What a route's stored choice says, in words. */
function choiceWords(row: RouteRow): string {
  if (row.use === "") return inheritWords(row.inherits);
  if (row.use === "model") return "Specific model";
  return ROLE_LABEL[row.use];
}

function RouteDetail({ row, settings, blocked, onEdit, onOpen }:
  { row: RouteRow; settings: InferenceSettings; blocked: boolean;
    onEdit: () => void; onOpen: (to: string) => void }) {
  const sel = row.resolves;
  const warning = useWarning(sel?.provider ?? "", sel?.model ?? "",
                             row.requires.includes("vision") ? "vision" : null, row.label);
  const preset = routePresetWords(row, settings);
  const decides = row.operation === "decide";
  return (
    <div className="detail-view">
      <div className="detail-main">
        <h3>{row.label}</h3>
        <div className="detail-rendered">
          {row.hint && <p>{row.hint}</p>}
          <p>Runs on {describe(sel)}</p>
          <Problem text={row.problem} />
          <Warning text={warning} />
          {decides && <DecideNote mode={row.decision_mode} sel={sel} />}
          <Problem text={droppedFallbackWords(row.fallback_missing, row.label, "",
                                                   row.fallback_problem)} />
          {sel && <ControlsReadout presetId={sel.preset} provider={sel.provider} model={sel.model}
                                   operation={decides ? "decide" : undefined} />}
        </div>
      </div>
      <aside className="detail-sidebar" aria-label={row.label}>
        <div className="form-actions">
          <button className="subtle" onClick={onEdit} disabled={blocked}>Edit</button>
        </div>
        <div className="side-section">
          <h4>Uses</h4>
          {row.use && row.use !== "model" ? (
            <button type="button" className="chip" onClick={() => onOpen(rolePath(row.use as GenerativeRole))}>
              {ROLE_LABEL[row.use]}
            </button>
          ) : <span className="field-hint">{choiceWords(row)}</span>}
        </div>
        {row.use === "model" && (
          <SelectionSide label="Pinned model" sel={row.pin} settings={settings} onOpen={onOpen} />
        )}
        <div className="side-section">
          <h4>Default role</h4>
          <button type="button" className="chip" onClick={() => onOpen(rolePath(row.default_role))}>
            {ROLE_LABEL[row.default_role]}
          </button>
        </div>
        <div className="side-section">
          <h4>Preset override</h4>
          <span className="field-hint">{preset}</span>
        </div>
        {row.requires.length > 0 && (
          <div className="side-section">
            <h4>Also needs</h4>
            <div className="chips">
              {row.requires.map((r) => <span key={r} className="chip on">{r}</span>)}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function RouteForm({ row, settings, blocked, onCancel, onSave }:
  { row: RouteRow; settings: InferenceSettings; blocked: boolean;
    onCancel: () => void; onSave: (body: InferenceWrite) => Promise<void> }) {
  const [use, setUse] = useState<RouteUse>(row.use);
  const [pin, setPin] = useState<InferenceSelection>(row.pin);
  const [preset, setPreset] = useState(row.preset);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  const needs = routePinNeeds(row);
  const vision: CapabilityNeed | null = row.requires.includes("vision") ? "vision" : null;
  // The model a role choice would send this route's images to (a pin warns
  // from its own fields).
  const target = use === "model" ? null : use ? settings.roles[use].resolves : row.inherits;
  const warning = useWarning(target?.provider ?? "", target?.model ?? "", vision, row.label);
  const roleLabel = `Role (default: ${ROLE_LABEL[row.default_role]})`;
  const presetLabel = "Preset override";

  async function save() {
    setSaving(true);
    setError(null);
    // This row alone; the pin only when it is the choice, so switching to a
    // role keeps a pin to come back to.
    const entry = use === "model" ? { use, pin, preset } : { use, preset };
    try {
      await onSave({ routes: { [row.key]: entry } });
    } catch (err: unknown) {
      setError(err);
      setSaving(false);
    }
  }

  return (
    <div className="form">
      <h3>Edit {row.label}</h3>
      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      {row.hint && <p className="field-hint">{row.hint}</p>}
      <label className="field">
        <span>{roleLabel}</span>
        <select aria-label={roleLabel} value={use} disabled={blocked}
                onChange={(e) => setUse(e.target.value as RouteUse)}>
          <option value="">{inheritWords(row.inherits)}</option>
          {GENERATIVE.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          <option value="model">Specific model…</option>
        </select>
      </label>
      {use === "model" ? (
        <SelectionFields label="Specific model" needs={needs} sel={pin} onChange={setPin}
                         settings={settings} blocked={blocked} withPreset
                         presetLabel="Pin preset" warn={vision} role={row.label} />
      ) : <Warning text={warning} />}
      <label className="field">
        <span>{presetLabel}</span>
        <PresetSelect label={presetLabel} value={preset} presets={settings.presets} allowClear
                      disabled={blocked} onChange={setPreset}
                      emptyLabel={`Inherit (resolves to ${inheritedPreset(row.inherits)})`} />
      </label>
      <div className="form-actions">
        <button className="subtle" onClick={onCancel}>Cancel</button>
        <button className="primary" onClick={() => { void save(); }}
                disabled={blocked || saving || (use === "model" && (!pin.provider || wantsModel(pin)))}>
          Save
        </button>
      </div>
    </div>
  );
}
