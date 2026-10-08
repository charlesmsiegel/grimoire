import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError, api, type CampaignInferenceWrite, type CapabilityNeed, type GenerativeRole,
  type InferenceSelection, type InferenceSettings, type MigrationStatus,
  type RoleCard, type RouteRow, type RouteUse,
} from "../../api/client";
import { onConfigChanged } from "../../appEvents";
import { ErrorNote } from "../ErrorNote";
import { ControlsReadout } from "./ControlsReadout";
import { InferenceBanner } from "./InferenceBanner";
import { migrationBanner, migrationLine, migrationUnfinished } from "./migration";
import { PresetSelect } from "./PresetSelect";
import { ProviderModelPicker } from "./ProviderModelPicker";
import { upgradePollDelay, upgrading } from "./useInferenceSettings";
import {
  CHOOSE_A_MODEL, describe, droppedFallbackWords, inheritedPreset, inheritWords, ROLE_LABEL,
  ROLE_NEEDS, routePinNeeds, routePresetWords, wantsModel,
} from "./selection";

/** The roles a campaign may override. Embedding is the library's alone: the
 *  campaign view carries no card for it and the server refuses one here. */
const ROLES: GenerativeRole[] = ["primary", "fast", "decision"];

const NOTHING: InferenceSelection = { provider: "", model: "", preset: "" };

type Picked = { kind: "role"; role: GenerativeRole } | { kind: "route"; key: string };
const pickKey = (p: Picked) => (p.kind === "role" ? `role:${p.role}` : `route:${p.key}`);

/** Whether this campaign chooses anything for the role itself. */
function overridden(card: RoleCard): boolean {
  return !!(card.stored.provider || card.stored.model || card.stored.preset);
}

function roleLine(card: RoleCard): string {
  return overridden(card) ? describe(card.resolves) : inheritWords(card.inherits);
}

/** A route's row: what it RUNS on, always. Its `inherits` silences every
 *  campaign key of the route, its preset included, so it is the answer for
 *  "inherit the whole route" and wrong as a headline for a route that
 *  inherits its role but overrides its preset. */
function routeLine(row: RouteRow): string {
  if (row.use === "") return `Runs on ${describe(row.resolves)}`;
  if (row.use === "model") return `Specific model: ${describe(row.resolves)}`;
  return `${ROLE_LABEL[row.use]}: ${describe(row.resolves)}`;
}

function Problem({ text }: { text: string | null }) {
  return text ? <p className="field-hint problem">{text}</p> : null;
}

/** The upgrade's state where the banner says nothing: the library's own
 *  settings are at the new layout, but something is still left (the /models
 *  page hides its banner then). A quiet line, because nothing here is blocked
 *  by it -- a save migrates an unmoved campaign first. Worded as every other
 *  model-settings surface words it (`migrationLine`), except when the campaign
 *  left behind is this one, which this section can say more plainly. */
function MigrationLine({ settings, cid }: { settings: InferenceSettings; cid: string }) {
  const line = migrationLine(settings);
  if (!line) return null;
  const mine = migrationUnfinished(settings)
    && settings.migration.skipped.find((s) => s.startsWith(`campaign ${cid}:`));
  if (mine) {
    return (
      <p className="field-hint">
        This campaign's model settings are still in the old layout
        ({mine.slice(`campaign ${cid}:`.length).trim()}). Saving an override here moves them
        first.
      </p>
    );
  }
  return <p className="field-hint">{line}</p>;
}

/** The Inspector's **Models** section (spec 10): this campaign's overrides of
 *  Primary, Fast and Decision, and of its campaign-scoped routes.
 *
 *  A list of rows, each read-only: clicking one opens what it resolves to,
 *  and only **Edit** turns that into a form. Every "resolves to", "inherit"
 *  and problem is the server's (`getCampaignInference`), never worked out
 *  here, and a Save sends only the one role or route it edited.
 *
 *  Writes are gated on the store's FORMAT, not on the migration finishing:
 *  a campaign the move has not reached is migrated by the server inside the
 *  write. Two refusals are this section's to show -- 409 `not_migrated`
 *  carries the migration status the banner renders until the view, read
 *  again at once, says whether a write can be made now, and 409
 *  `newer_format` for a campaign a newer build marked (which the global
 *  status cannot say) keeps the section read-only from then on. */
export function CampaignModels({ cid }: { cid: string }) {
  const [settings, setSettings] = useState<InferenceSettings | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [showRoutes, setShowRoutes] = useState(false);
  // What a refused write said about the store, kept past the form it came from.
  const [refusedStatus, setRefusedStatus] = useState<MigrationStatus | null>(null);
  const [newerHere, setNewerHere] = useState<string | null>(null);
  // Bumped to read the view again: on any model-settings change made anywhere
  // (its Inherit labels are the library's answer, which can move under it),
  // and after a `not_migrated` refusal, whose status is a moment's answer.
  const [rev, setRev] = useState(0);
  // Bumped each time a read settles, answered or not: what the upgrade poll
  // re-arms on, so a failed read does not end the asking.
  const [reads, setReads] = useState(0);
  const shown = useRef<string | null>(null);

  useEffect(() => onConfigChanged(() => setRev((n) => n + 1)), []);

  useEffect(() => {
    let live = true;
    // A different campaign starts over; a re-read of this one keeps what is
    // open, and the view it had until the new one lands.
    if (shown.current !== cid) {
      shown.current = cid;
      setSettings(null);
      setError(null);
      setPicked(null);
      setMode("view");
      setRefusedStatus(null);
      setNewerHere(null);
    }
    api.getCampaignInference(cid)
      .then((s) => {
        if (!live) return;
        setSettings(s);
        // The fresh view is what decides whether a write can be made now.
        setRefusedStatus(null);
      })
      .catch((err: unknown) => { if (live) setError(err); })
      .finally(() => { if (live) setReads((n) => n + 1); });
    return () => { live = false; };
  }, [cid, rev]);

  // A library still moving to the new layout is asked again until it has, so
  // Edit opens when the upgrade lands rather than when the section is left.
  const polling = upgrading(settings);
  useEffect(() => {
    if (!polling) return;
    const timer = setTimeout(() => setRev((n) => n + 1), upgradePollDelay(reads));
    return () => clearTimeout(timer);
  }, [polling, reads]);

  if (!settings) {
    return error != null
      ? <div className="banner"><ErrorNote err={error} /></div>
      : <p className="field-hint">Reading this campaign's models…</p>;
  }

  const blocked = settings.newer || settings.format !== "2" || newerHere !== null
    || refusedStatus !== null;
  // A refusal's status is fresher than the view's, except that a newer
  // library outranks it.
  const banner: MigrationStatus | null = settings.newer
    ? migrationBanner(settings) : refusedStatus ?? migrationBanner(settings);

  async function save(body: CampaignInferenceWrite) {
    try {
      const next = await api.putCampaignInference(cid, body);
      setSettings(next);
      setMode("view");
    } catch (err: unknown) {
      if (err instanceof ApiError && err.kind === "not_migrated") {
        // Shown until the view is read again, which says whether the move
        // has finished since -- a refusal is not a latch.
        const status = err.body?.status as MigrationStatus | undefined;
        if (status) setRefusedStatus(status);
        setRev((n) => n + 1);
      }
      if (err instanceof ApiError && err.kind === "newer_format") {
        setNewerHere(err.detail);
        setMode("view");
        return;
      }
      throw err;
    }
  }

  function open(next: Picked) {
    setPicked(next);
    setMode("view");
  }

  const isPicked = (p: Picked) => picked !== null && pickKey(picked) === pickKey(p);

  const card = picked?.kind === "role" ? settings.roles[picked.role] : null;
  const route = picked?.kind === "route"
    ? settings.routes.find((r) => r.key === picked.key) ?? null : null;

  let detail = null;
  if (picked?.kind === "role" && card) {
    detail = mode === "edit"
      ? <RoleForm key={picked.role} role={picked.role} card={card} settings={settings}
                  blocked={blocked} onCancel={() => setMode("view")} onSave={save} />
      : <RoleDetail role={picked.role} card={card} settings={settings} blocked={blocked}
                    onEdit={() => setMode("edit")} />;
  } else if (route) {
    detail = mode === "edit"
      ? <RouteForm key={route.key} row={route} settings={settings} blocked={blocked}
                   onCancel={() => setMode("view")} onSave={save} />
      : <RouteDetail row={route} settings={settings} blocked={blocked}
                     onEdit={() => setMode("edit")} />;
  }

  return (
    <div className="campaign-models">
      <InferenceBanner status={banner} />
      {newerHere !== null && <div className="banner" role="status">{newerHere}</div>}
      <MigrationLine settings={settings} cid={cid} />
      <p className="field-hint">
        This campaign's own choices. Anything left to inherit follows the library's{" "}
        <Link to="/models">Models</Link>.
      </p>
      <div className="campaign-models-list" role="group" aria-label="Roles">
        {ROLES.map((r) => (
          <button key={r} type="button"
                  className={"inspector-row campaign-models-row"
                             + (isPicked({ kind: "role", role: r }) ? " active" : "")}
                  aria-pressed={isPicked({ kind: "role", role: r })}
                  onClick={() => open({ kind: "role", role: r })}>
            <span className="inspector-name">{ROLE_LABEL[r]}</span>
            <span className="field-hint">{roleLine(settings.roles[r])}</span>
          </button>
        ))}
      </div>
      <button type="button" className="subtle" aria-expanded={showRoutes}
              onClick={() => setShowRoutes((v) => !v)}>
        Routes
      </button>
      {showRoutes && (
        <div className="campaign-models-list" role="group" aria-label="Routes">
          {settings.routes.map((r) => (
            <button key={r.key} type="button"
                    className={"inspector-row campaign-models-row"
                               + (isPicked({ kind: "route", key: r.key }) ? " active" : "")}
                    aria-pressed={isPicked({ kind: "route", key: r.key })}
                    onClick={() => open({ kind: "route", key: r.key })}>
              <span className="inspector-name">{r.label}</span>
              <span className="field-hint">{routeLine(r)}</span>
            </button>
          ))}
        </div>
      )}
      {detail && <div className="campaign-models-detail">{detail}</div>}
    </div>
  );
}

/** One stored selection as chips: the provider is another record, so it
 *  links to it. */
function SelectionChips({ sel, settings }: { sel: InferenceSelection; settings: InferenceSettings }) {
  const provider = settings.providers.find((p) => p.id === sel.provider);
  const preset = sel.preset ? settings.presets.find((p) => p.id === sel.preset) : undefined;
  return (
    <div className="chips">
      {sel.provider && (
        <Link className="chip" to={`/providers/${encodeURIComponent(sel.provider)}`}>
          {provider?.name ?? `${sel.provider} (missing provider)`}
        </Link>
      )}
      <span className="chip on">{sel.model || "default model"}</span>
      {sel.preset && <span className="chip on">{preset?.name ?? sel.preset}</span>}
    </div>
  );
}

function RoleDetail({ role, card, settings, blocked, onEdit }:
  { role: GenerativeRole; card: RoleCard; settings: InferenceSettings; blocked: boolean;
    onEdit: () => void }) {
  const label = ROLE_LABEL[role];
  // The fallback is named only when this campaign chooses it: an inherited
  // one is the library's, and the view does not say which it is.
  const { provider, model } = card.fallback;
  const named = provider && model
    ? `${settings.providers.find((p) => p.id === provider)?.name ?? provider} ▸ ${model}` : "";
  return (
    <section className="campaign-models-view" aria-label={label}>
      <h5>{label}</h5>
      <p>{overridden(card) ? `Runs on ${describe(card.resolves)}` : inheritWords(card.inherits)}</p>
      <Problem text={card.problem} />
      <Problem text={droppedFallbackWords(card.fallback_missing, label, named,
                                                card.fallback_problem)} />
      <div className="campaign-models-meta">
        <h6>Chosen for this campaign</h6>
        {overridden(card) ? <SelectionChips sel={card.stored} settings={settings} />
          : <span className="field-hint">Nothing; it inherits.</span>}
      </div>
      {(card.fallback.provider || card.fallback.model) && (
        <div className="campaign-models-meta">
          <h6>Fallback</h6>
          <SelectionChips sel={card.fallback} settings={settings} />
        </div>
      )}
      <div className="form-actions">
        <button className="subtle" onClick={onEdit} disabled={blocked}>Edit</button>
      </div>
    </section>
  );
}

/** Provider, then a model that can do what is needed, then a preset, with
 *  what that preset sends on the pick. Never inside a `<form>`: the picker's
 *  typed-id box has its own button, and Enter there must not submit anything. */
function SelectionFields({ label, needs, sel, onChange, settings, blocked, presetLabel }:
  { label: string; needs: CapabilityNeed[]; sel: InferenceSelection;
    onChange: (sel: InferenceSelection) => void; settings: InferenceSettings;
    blocked: boolean; presetLabel: string }) {
  // A selection with no provider is not read at all, so a preset chosen alone
  // would be stored and dormant: held until a provider is chosen, unless one
  // is already stored that way, which stays clearable.
  const presetHeld = !sel.provider && !sel.preset;
  return (
    <fieldset className="model-selection" aria-label={label}>
      <ProviderModelPicker needs={needs} providers={settings.providers} disabled={blocked}
                           value={{ provider: sel.provider, model: sel.model }}
                           onChange={(v) => onChange({ ...sel, ...v })} />
      {wantsModel(sel) && <p className="field-hint">{CHOOSE_A_MODEL}</p>}
      <label className="field">
        <span>{presetLabel}</span>
        <PresetSelect label={presetLabel} value={sel.preset} presets={settings.presets}
                      emptyLabel="Provider defaults" disabled={blocked || presetHeld}
                      onChange={(preset) => onChange({ ...sel, preset })} />
      </label>
      {/* What /models says of the same draft: nothing reads a preset whose
          selection names no provider, and a fallback saved that way is
          saved empty -- so the form says so rather than showing one. */}
      {!sel.provider && !!sel.preset && (
        <p className="field-hint">
          A preset with no provider is not used. Choose a provider, or clear it.
        </p>
      )}
      <ControlsReadout presetId={sel.preset} provider={sel.provider} model={sel.model} />
    </fieldset>
  );
}

function RoleForm({ role, card, settings, blocked, onCancel, onSave }:
  { role: GenerativeRole; card: RoleCard; settings: InferenceSettings; blocked: boolean;
    onCancel: () => void; onSave: (body: CampaignInferenceWrite) => Promise<void> }) {
  const label = ROLE_LABEL[role];
  const choiceLabel = `${label} for this campaign`;
  const [own, setOwn] = useState(overridden(card));
  const [sel, setSel] = useState<InferenceSelection>(card.stored);
  const [fallback, setFallback] = useState<InferenceSelection>(card.fallback);
  const [fallbackOpen, setFallbackOpen] = useState(
    !!(card.fallback.provider || card.fallback.model || card.fallback.preset));
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  const f = card.fallback;
  const fallbackChanged = fallback.provider !== f.provider || fallback.model !== f.model
    || fallback.preset !== f.preset;

  async function save() {
    setSaving(true);
    setError(null);
    // The selection, replaced whole: inheriting sends it empty, which clears
    // every part. The fallback only when it was touched, so a save of the
    // selection cannot put back one another tab changed meanwhile; sent
    // empty, it falls back to the library's again.
    const entry: { selection: InferenceSelection; fallback?: InferenceSelection } =
      { selection: own ? sel : NOTHING };
    if (fallbackChanged) entry.fallback = fallback.provider ? fallback : NOTHING;
    try {
      await onSave({ roles: { [role]: entry } });
    } catch (err: unknown) {
      setError(err);
      setSaving(false);
    }
  }

  return (
    <div className="form">
      <h5>Edit {label}</h5>
      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      <label className="field">
        <span>{choiceLabel}</span>
        <select aria-label={choiceLabel} value={own ? "own" : ""} disabled={blocked}
                onChange={(e) => setOwn(e.target.value === "own")}>
          <option value="">{inheritWords(card.inherits)}</option>
          <option value="own">Choose a model for this campaign…</option>
        </select>
      </label>
      {own && (
        <SelectionFields label={label} needs={ROLE_NEEDS[role]} sel={sel} onChange={setSel}
                         settings={settings} blocked={blocked} presetLabel="Preset" />
      )}
      <button type="button" className="subtle" aria-expanded={fallbackOpen}
              onClick={() => setFallbackOpen((v) => !v)}>
        Fallback
      </button>
      {fallbackOpen && (
        <>
          <p className="field-hint">
            Tried once when {label} cannot answer. With no provider chosen, the library&apos;s
            fallback applies.
          </p>
          <SelectionFields label={`${label} fallback`} needs={ROLE_NEEDS[role]} sel={fallback}
                           onChange={setFallback} settings={settings} blocked={blocked}
                           presetLabel="Fallback preset" />
        </>
      )}
      <div className="form-actions">
        <button className="subtle" onClick={onCancel}>Cancel</button>
        <button className="primary" onClick={() => { void save(); }}
                disabled={blocked || saving || (own && (!sel.provider || wantsModel(sel)))
                          || (fallbackChanged && wantsModel(fallback))}>
          Save
        </button>
      </div>
    </div>
  );
}

function RouteDetail({ row, settings, blocked, onEdit }:
  { row: RouteRow; settings: InferenceSettings; blocked: boolean; onEdit: () => void }) {
  return (
    <section className="campaign-models-view" aria-label={row.label}>
      <h5>{row.label}</h5>
      {row.hint && <p className="field-hint">{row.hint}</p>}
      <p>Runs on {describe(row.resolves)}</p>
      <Problem text={row.problem} />
      <Problem text={droppedFallbackWords(row.fallback_missing, row.label, "",
                                                row.fallback_problem)} />
      <div className="campaign-models-meta">
        <h6>Uses</h6>
        {/* The role choice alone, so the inherited model without a preset:
            the preset is the next block's, and may be this campaign's own. */}
        {row.use === "" ? <span className="field-hint">{inheritWords(row.inherits, false)}</span>
          : <span className="chip on">
              {row.use === "model" ? "Specific model" : ROLE_LABEL[row.use]}
            </span>}
        {row.use === "model" && <SelectionChips sel={row.pin} settings={settings} />}
      </div>
      <div className="campaign-models-meta">
        <h6>Preset override</h6>
        <span className="field-hint">{routePresetWords(row, settings)}</span>
      </div>
      <div className="form-actions">
        <button className="subtle" onClick={onEdit} disabled={blocked}>Edit</button>
      </div>
    </section>
  );
}

function RouteForm({ row, settings, blocked, onCancel, onSave }:
  { row: RouteRow; settings: InferenceSettings; blocked: boolean;
    onCancel: () => void; onSave: (body: CampaignInferenceWrite) => Promise<void> }) {
  const [use, setUse] = useState<RouteUse>(row.use);
  const [pin, setPin] = useState<InferenceSelection>(row.pin);
  const [preset, setPreset] = useState(row.preset);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  const needs = routePinNeeds(row);
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
      <h5>Edit {row.label}</h5>
      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      <label className="field">
        <span>{roleLabel}</span>
        <select aria-label={roleLabel} value={use} disabled={blocked}
                onChange={(e) => setUse(e.target.value as RouteUse)}>
          {/* No preset in it: that is the override field's below. */}
          <option value="">{inheritWords(row.inherits, false)}</option>
          {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          <option value="model">Specific model…</option>
        </select>
      </label>
      {use === "model" && (
        <SelectionFields label="Specific model" needs={needs} sel={pin} onChange={setPin}
                         settings={settings} blocked={blocked} presetLabel="Pin preset" />
      )}
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
