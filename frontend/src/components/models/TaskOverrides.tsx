import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import type {
  CapabilityNeed, InferenceSelection, InferenceSettings, InferenceWrite, ProviderHealth,
  RouteRow, RouteUse, RouteWrite,
} from "../../api/client";
import { WhatItSends } from "../inference/ControlsReadout";
import { PresetSelect } from "../inference/PresetSelect";
import {
  CHOOSE_A_MODEL, droppedFallbackWords, inheritedPreset, ROLE_LABEL, routePinNeeds, wantsModel,
} from "../inference/selection";
import { ModelSelect } from "./ModelSelect";
import { CHECKED_ON_SAVE, DecideNote, Problem, useWarning, Warning } from "./notes";
import { EMPTY_SEL, GENERATIVE, sameSel } from "./selections";
import { ADVANCED_HASH, taskFromHash } from "./taskHash";

export type RouteDraft = { use: RouteUse; pin: InferenceSelection; preset: string };

export function startRouteDrafts(routes: RouteRow[]): Record<string, RouteDraft> {
  return Object.fromEntries(routes.map((r) => [r.key, { use: r.use, pin: r.pin, preset: r.preset }]));
}

/** The tasks' part of the write (spec 3.4): only the fields that moved. Leaving
 *  "Specific model…" changes `use` alone -- the stored pin is neither sent nor
 *  cleared, so coming back restores it (today's rule) -- and `pin` is sent only
 *  when its own fields moved while the task is pinned. `""` and the
 *  stop-inheriting sentinel are different presets, compared as such. */
export function routeBody(drafts: Record<string, RouteDraft>, routes: RouteRow[]):
  NonNullable<InferenceWrite["routes"]> {
  const out: NonNullable<InferenceWrite["routes"]> = {};
  for (const row of routes) {
    const d = drafts[row.key];
    if (!d) continue;
    const entry: RouteWrite = {};
    if (d.use !== row.use) entry.use = d.use;
    if (d.preset !== row.preset) entry.preset = d.preset;
    if (d.use === "model" && !sameSel(d.pin, row.pin)) entry.pin = d.pin;
    if (Object.keys(entry).length) out[row.key] = entry;
  }
  return out;
}

/** A pinned task with no provider, or a provider and no model, cannot be saved. */
export function routesIncomplete(drafts: Record<string, RouteDraft>): boolean {
  return Object.values(drafts).some((d) => d.use === "model" && (!d.pin.provider || wantsModel(d.pin)));
}

// Held as names so the label rule sees text it cannot judge, as in ModelsEditForm.
const PIN_PRESET = "Pin preset";
const OVERRIDE_PRESET = "Preset override";
const overrides = (d: RouteDraft) => d.use !== "" || d.preset !== "";

/** Whether a task's draft differs from the row the form opened with. */
function moved(d: RouteDraft, row: RouteRow): boolean {
  return d.use !== row.use || d.preset !== row.preset
    || (d.use === "model" && !sameSel(d.pin, row.pin));
}

function TaskRow({ row, base, draft, onChange, settings, health, blocked }:
  { row: RouteRow; base: RouteRow | undefined; draft: RouteDraft; onChange: (d: RouteDraft) => void;
    settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth>; blocked: boolean }) {
  const needs = routePinNeeds(row);
  const vision: CapabilityNeed | null = row.requires.includes("vision") ? "vision" : null;
  // The model a role choice would send this task's images to; a pin warns
  // through its own select.
  const target = draft.use === "model" ? null
    : draft.use ? settings.roles[draft.use].resolves : row.inherits;
  const warning = useWarning(target?.provider ?? "", target?.model ?? "", vision, row.label);
  // A pinned model is warned of from its own fields, as `RouteForm` did
  // (`SelectionFields warn={vision}`).
  const pinWarning = useWarning(draft.use === "model" ? draft.pin.provider : "",
                                draft.use === "model" ? draft.pin.model : "", vision, row.label);
  const decides = row.operation === "decide";
  // The row's problem, dropped fallback, decide note and readout describe the
  // SAVED resolution; beside a changed draft they would answer the wrong
  // question, so they wait for the save that checks it.
  const stale = moved(draft, base ?? row);
  return (
    <fieldset className="task-row" aria-label={row.label} data-task={row.key}>
      <legend>
        {row.label}
        {overrides(draft) && <> <span className="chip on">overrides</span></>}
      </legend>
      {row.hint && <p className="field-hint">{row.hint}</p>}
      <label className="field">
        <span>Use</span>
        <select aria-label={`${row.label} use`} value={draft.use} disabled={blocked}
                onChange={(e) => onChange({ ...draft, use: e.target.value as RouteUse })}>
          <option value="">Role default ({ROLE_LABEL[row.default_role]})</option>
          {GENERATIVE.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          <option value="model">Specific model…</option>
        </select>
      </label>
      {draft.use === "model" ? (
        <>
          <ModelSelect label={`${row.label} pinned`} needs={needs} providers={settings.providers}
                       health={health} emptyLabel="Choose a provider…" disabled={blocked}
                       value={{ provider: draft.pin.provider, model: draft.pin.model }}
                       onChange={(v) => onChange({ ...draft,
                         pin: v.provider ? { ...draft.pin, ...v } : EMPTY_SEL })} />
          <Warning text={pinWarning} />
          {wantsModel(draft.pin) && <p className="field-hint">{CHOOSE_A_MODEL}</p>}
          {draft.pin.provider && (
            <label className="field">
              <span>{PIN_PRESET}</span>
              <PresetSelect label={`${row.label} pin preset`} value={draft.pin.preset}
                            presets={settings.presets} emptyLabel="Provider defaults"
                            disabled={blocked}
                            onChange={(preset) => onChange({ ...draft, pin: { ...draft.pin, preset } })} />
            </label>
          )}
        </>
      ) : <Warning text={warning} />}
      <label className="field">
        <span>{OVERRIDE_PRESET}</span>
        <PresetSelect label={`${row.label} preset override`} value={draft.preset}
                      presets={settings.presets} allowClear disabled={blocked}
                      emptyLabel={`Inherit (resolves to ${inheritedPreset(row.inherits)})`}
                      onChange={(preset) => onChange({ ...draft, preset })} />
      </label>
      {row.requires.length > 0 && (
        <p className="field-hint">Also needs: {row.requires.join(", ")}</p>
      )}
      {stale ? <p className="field-hint">{CHECKED_ON_SAVE}</p> : (
        <>
          <Problem text={row.problem} />
          {decides && <DecideNote mode={row.decision_mode} decidesNatively={row.decides_natively} />}
          <Problem text={droppedFallbackWords(row.fallback_missing, row.label, "", row.fallback_problem)} />
          {row.resolves && (
            <WhatItSends presetId={row.resolves.preset} provider={row.resolves.provider}
                         model={row.resolves.model} operation={decides ? "decide" : undefined} />
          )}
        </>
      )}
    </fieldset>
  );
}

/** Advanced (spec 3.3): one row per task, folded unless the address asks for
 *  the section (`#advanced`) or for one task (`#task-<encoded key>`). A task
 *  set here overrides its role's model, its preset, or both -- the resolver's
 *  precedence, unchanged; the form only labels it. */
export function TaskOverrides({ settings, baseline, health, blocked, drafts, onChange }:
  { settings: InferenceSettings; baseline: RouteRow[]; health: ReadonlyMap<string, ProviderHealth>;
    blocked: boolean; drafts: Record<string, RouteDraft>;
    onChange: (key: string, d: RouteDraft) => void }) {
  const { hash } = useLocation();
  const asked = taskFromHash(hash);
  const target = asked !== null && settings.routes.some((r) => r.key === asked) ? asked : null;
  const [open, setOpen] = useState(hash === ADVANCED_HASH || target !== null);
  const root = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (target === null) return;
    const el = [...(root.current?.querySelectorAll<HTMLElement>("[data-task]") ?? [])]
      .find((n) => n.dataset.task === target);
    el?.scrollIntoView?.({ block: "start" });
  }, [target]);
  const n = Object.values(drafts).filter(overrides).length;
  return (
    <details id="advanced" ref={root} className="task-overrides" open={open}
             onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Advanced: per-task overrides ({n} active)</summary>
      <p className="field-hint">
        Every task runs on its role unless it is set here. A task's own choice of
        model, its preset override, or both, win over the role for that task.
      </p>
      {settings.routes.map((row) => (
        <TaskRow key={row.key} row={row} base={baseline.find((b) => b.key === row.key)}
                 draft={drafts[row.key]} settings={settings}
                 health={health} blocked={blocked} onChange={(d) => onChange(row.key, d)} />
      ))}
    </details>
  );
}
