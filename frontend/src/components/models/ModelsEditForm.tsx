import { type ReactNode, useState } from "react";
import {
  api, type GenerativeRole, type InferenceSelection, type InferenceSettings,
  type InferenceWrite, type ProviderHealth,
} from "../../api/client";
import { errorText } from "../../api/errors";
import { ErrorNote } from "../ErrorNote";
import { WhatItSends } from "../inference/ControlsReadout";
import { PresetSelect } from "../inference/PresetSelect";
import {
  CHOOSE_A_MODEL, droppedFallbackWords, ROLE_LABEL, ROLE_NEEDS, wantsModel,
} from "../inference/selection";
import { ModelSelect } from "./ModelSelect";
import { CHECKED_ON_SAVE, Problem, useWarning, Warning } from "./notes";
import { SAME_AS } from "./RoleRow";
import { EMPTY_SEL, GENERATIVE, sameSel } from "./selections";
import { routeBody, routesIncomplete, startRouteDrafts, TaskOverrides, type RouteDraft } from "./TaskOverrides";

export type RoleDraft = { sel: InferenceSelection; fallback: InferenceSelection; fallbackOpen: boolean };
export type Drafts = Record<GenerativeRole, RoleDraft>;

function startDrafts(settings: InferenceSettings): Drafts {
  const out = {} as Drafts;
  for (const role of GENERATIVE) {
    const card = settings.roles[role];
    out[role] = { sel: card.stored, fallback: card.fallback, fallbackOpen: !!card.fallback.provider };
  }
  return out;
}

/** The roles' part of the write (spec 3.4): a role's `selection` only when it
 *  moved, its `fallback` only when that moved -- a save must not put back what
 *  another tab changed in a part this form never touched. */
export function roleBody(drafts: Drafts, settings: InferenceSettings): InferenceWrite["roles"] {
  const roles: NonNullable<InferenceWrite["roles"]> = {};
  for (const role of GENERATIVE) {
    const card = settings.roles[role];
    const d = drafts[role];
    const entry: { selection?: InferenceSelection; fallback?: InferenceSelection } = {};
    if (!sameSel(d.sel, card.stored)) entry.selection = d.sel;
    if (!sameSel(d.fallback, card.fallback)) entry.fallback = d.fallback;
    if (entry.selection || entry.fallback) roles[role] = entry;
  }
  return roles;
}

/** One selection's controls: ModelSelect, then (generative) the preset, the
 *  model warning, what it sends. "Same as" / "Not set" (provider "") clears
 *  the WHOLE selection, preset included (spec 3.2), and hides model and preset. */
function SelectionControls({ label, role, value, onChange, settings, health, blocked,
                             emptyLabel, presetLabel, stale, children }:
  { label: string; role: GenerativeRole; value: InferenceSelection;
    onChange: (next: InferenceSelection) => void; settings: InferenceSettings;
    health: ReadonlyMap<string, ProviderHealth>; blocked: boolean; emptyLabel: string;
    presetLabel: string; stale: boolean; children?: ReactNode }) {
  const decision = role === "decision";
  const warning = useWarning(value.provider, value.model,
                             decision ? null : ROLE_NEEDS[role][0], ROLE_LABEL[role]);
  const dormant = !value.provider && !!value.preset;
  return (
    <>
      <ModelSelect label={label} needs={ROLE_NEEDS[role]} providers={settings.providers}
                   health={health} emptyLabel={emptyLabel} disabled={blocked}
                   value={{ provider: value.provider, model: value.model }}
                   onChange={(v) => onChange(v.provider ? { ...value, ...v } : EMPTY_SEL)} />
      <Warning text={warning} />
      {wantsModel(value) && <p className="field-hint">{CHOOSE_A_MODEL}</p>}
      {value.provider && (
        <label className="field">
          <span>{presetLabel}</span>
          <PresetSelect label={`${label} preset`} value={value.preset} presets={settings.presets}
                        emptyLabel="Provider defaults" disabled={blocked}
                        onChange={(preset) => onChange({ ...value, preset })} />
        </label>
      )}
      {dormant && (
        <p className="field-hint">
          A preset with no provider is not used.{" "}
          <button type="button" className="subtle" disabled={blocked}
                  onClick={() => onChange({ ...value, preset: "" })}>Clear it</button>
        </p>
      )}
      {!stale && children}
      {!stale && value.provider && value.model && (
        <WhatItSends presetId={value.preset} provider={value.provider} model={value.model}
                     operation={decision ? "decide" : undefined} />
      )}
    </>
  );
}

/** `/models/edit` (spec 3.2-3.4): every role on one form, one Save. */
export function ModelsEditForm({ settings, health, blocked, onSaved, onCancel }:
  { settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth>; blocked: boolean;
    onSaved: (next: InferenceSettings) => void; onCancel: () => void }) {
  const [drafts, setDrafts] = useState<Drafts>(() => startDrafts(settings));
  // Every "did this move" comparison is against what the form opened with, not
  // the live prop: a refresh while the form is open (a token-rate save, the
  // migration poll) must not make an untouched part look moved.
  const [baseline] = useState(settings);
  const [routes, setRoutes] = useState<Record<string, RouteDraft>>(
    () => startRouteDrafts(settings.routes));
  const storedEmbedding = baseline.roles.embedding?.stored ?? { provider: "", model: "" };
  const [embedding, setEmbedding] = useState(storedEmbedding);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState<string | null>(null);

  const setRole = (role: GenerativeRole, next: Partial<RoleDraft>) => {
    setDrafts((d) => ({ ...d, [role]: { ...d[role], ...next } }));
    setAsking(null);
  };
  const embeddingMoved = embedding.provider !== storedEmbedding.provider
    || embedding.model !== storedEmbedding.model;
  // Today's Embedding form warns of a model known not to embed (`warn=embed`).
  const embeddingWarning = useWarning(embedding.provider, embedding.model, "embed",
                                      ROLE_LABEL.embedding);

  function body(): InferenceWrite {
    const roles = roleBody(drafts, baseline) ?? {};
    const out: InferenceWrite = {};
    const withEmbedding = embeddingMoved
      ? { ...roles, embedding: { selection: { provider: embedding.provider, model: embedding.model } } }
      : roles;
    if (Object.keys(withEmbedding).length) out.roles = withEmbedding;
    const routeWrites = routeBody(routes, baseline.routes);
    if (Object.keys(routeWrites).length) out.routes = routeWrites;
    return out;
  }

  const incomplete = GENERATIVE.some((r) => wantsModel(drafts[r].sel)
      || (drafts[r].fallbackOpen && wantsModel(drafts[r].fallback)))
    || routesIncomplete(routes);

  async function send(confirm: boolean) {
    setSaving(true);
    setError(null);
    try {
      const next = confirm
        ? await api.putInferenceSettings(body(), { confirmEmbedding: true })
        : await api.putInferenceSettings(body());
      onSaved(next);
    } catch (err: unknown) {
      const kind = typeof err === "object" && err !== null
        ? (err as { kind?: unknown }).kind : undefined;
      // The server's question, in its words, is authoritative (spec 3.4).
      if (kind === "confirm_embedding" && !confirm) setAsking(errorText(err));
      else setError(err);
      setSaving(false);
    }
  }

  function save() {
    // Nothing moved: nothing is sent (an empty PUT is a write nobody asked for).
    if (Object.keys(body()).length === 0) { onCancel(); return; }
    // Asked up front only for a complete, changed selection -- today's rule;
    // turning embedding off asks nothing, and the server covers the rest.
    if (embeddingMoved && embedding.provider && embedding.model) {
      const name = settings.providers.find((p) => p.id === embedding.provider)?.name
        ?? embedding.provider;
      setAsking(`Changing the embedding model re-embeds your library through ${name}, `
                + "which may cost money.");
      return;
    }
    void send(false);
  }

  return (
    <form className="models-edit" aria-label="Edit models" onSubmit={(e) => e.preventDefault()}>
      <h2>Edit models</h2>
      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      {GENERATIVE.map((role) => {
        const label = ROLE_LABEL[role];
        const card = settings.roles[role];
        const d = drafts[role];
        const fbName = settings.providers.find((p) => p.id === card.fallback.provider)?.name
          ?? card.fallback.provider;
        // The role's problem, dropped-fallback words and readouts describe the
        // SAVED resolution; once its selection or fallback moved they would
        // answer the wrong question, so they wait for the save that checks it.
        const base = baseline.roles[role];
        const stale = !sameSel(d.sel, base.stored) || !sameSel(d.fallback, base.fallback);
        const dropped = droppedFallbackWords(card.fallback_missing, label,
          card.fallback.provider && card.fallback.model ? `${fbName} ▸ ${card.fallback.model}` : "",
          card.fallback_problem);
        return (
          <fieldset key={role} className="models-edit-role" aria-label={label}>
            <legend>{label}</legend>
            <SelectionControls label={label} role={role} value={d.sel} settings={settings}
                               health={health} blocked={blocked} presetLabel="Preset"
                               emptyLabel={SAME_AS[role] ?? "Not set"} stale={stale}
                               onChange={(sel) => setRole(role, { sel })}>
              <Problem text={card.problem} />
            </SelectionControls>
            {stale && <p className="field-hint">{CHECKED_ON_SAVE}</p>}
            {d.fallbackOpen ? (
              <fieldset className="models-edit-fallback" aria-label={`${label} fallback`}>
                <legend>Fallback, tried once when {label} cannot answer</legend>
                <SelectionControls label={`${label} fallback`} role={role} value={d.fallback}
                                   settings={settings} health={health} blocked={blocked}
                                   presetLabel="Fallback preset" emptyLabel="No fallback"
                                   stale={stale}
                                   onChange={(fallback) => setRole(role, { fallback })}>
                  <Problem text={dropped} />
                </SelectionControls>
                <button type="button" className="subtle" aria-label={`Remove ${label} fallback`}
                        disabled={blocked}
                        onClick={() => setRole(role, { fallback: EMPTY_SEL, fallbackOpen: false })}>
                  ✕
                </button>
              </fieldset>
            ) : (
              <button type="button" className="subtle" aria-label={`+ Fallback for ${label}`}
                      disabled={blocked} onClick={() => setRole(role, { fallbackOpen: true })}>
                + Fallback
              </button>
            )}
          </fieldset>
        );
      })}
      {settings.roles.embedding && (
        <fieldset className="models-edit-role" aria-label="Embedding">
          <legend>Embedding</legend>
          <ModelSelect label="Embedding" needs={ROLE_NEEDS.embedding} providers={settings.providers}
                       health={health} emptyLabel="Not set — nothing is embedded"
                       disabled={blocked} value={embedding}
                       onChange={(v) => { setEmbedding(v); setAsking(null); }} />
          <Warning text={embeddingWarning} />
          {!settings.roles.embedding.on && <Problem text={settings.roles.embedding.problem ?? null} />}
        </fieldset>
      )}
      <TaskOverrides settings={settings} baseline={baseline.routes} health={health} blocked={blocked} drafts={routes}
                     onChange={(key, d) => { setRoutes((r) => ({ ...r, [key]: d })); setAsking(null); }} />
      {asking !== null && (
        <div className="banner" role="group" aria-label="Confirm the re-embedding">
          {asking}{" "}
          <button type="button" className="primary" disabled={saving}
                  onClick={() => { setAsking(null); void send(true); }}>
            Re-embed and save
          </button>{" "}
          <button type="button" className="subtle" onClick={() => setAsking(null)}>Not now</button>
        </div>
      )}
      <div className="form-actions">
        <button type="button" className="subtle" onClick={onCancel}>Cancel</button>
        <button type="button" className="primary" onClick={save}
                disabled={blocked || saving || asking !== null || incomplete}>
          Save
        </button>
      </div>
    </form>
  );
}
