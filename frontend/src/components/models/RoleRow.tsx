import { Link } from "react-router-dom";
import type {
  CardLimits, EmbeddingCard, GenerativeRole, InferenceSettings, ProviderHealth, RateInfo, RouteRow,
} from "../../api/client";
import { modelLimitsPath, providerPath } from "../../providerPaths";
import { WhatItSends } from "../inference/ControlsReadout";
import { describe, droppedFallbackWords, ROLE_LABEL, ROLE_NEEDS } from "../inference/selection";
import { HealthDot } from "./health";
import { DecideNote, Problem, useWarning, Warning } from "./notes";
import { windowWords } from "./limits";
import { rateWords, setRateHref } from "./rates";
import { taskHash } from "./taskHash";

/** How an unset role reads (spec 4.4): the role it inherits. */
export const SAME_AS: Partial<Record<GenerativeRole, string>> = {
  fast: "Same as Primary", decision: "Same as Fast",
};

/** provider · model · preset, as one summary line: the provider is a record
 *  of its own (a link, with its health), and so is the preset. A provider the
 *  list no longer holds is named as missing, never dropped. */
export function SelectionLine({ provider, providerName, model, preset = "", presetName = "",
                                health, withPreset = true }:
  { provider: string; providerName: string | null; model: string; preset?: string;
    presetName?: string; health: ReadonlyMap<string, ProviderHealth>; withPreset?: boolean }) {
  return (
    <>
      {providerName === null
        ? <span>{provider} (missing provider)</span>
        : <Link to={providerPath(provider)}>{providerName}</Link>}
      <HealthDot health={health.get(provider)} />
      {" · "}{model || "its default model"}
      {withPreset && (
        <>
          {" · "}
          {preset
            ? <Link to={`/presets/${encodeURIComponent(preset)}`}>{presetName || preset}</Link>
            : "no preset"}
        </>
      )}
    </>
  );
}

const providerName = (settings: InferenceSettings, id: string) =>
  settings.providers.find((p) => p.id === id)?.name ?? null;
const presetName = (settings: InferenceSettings, id: string) =>
  settings.presets.find((p) => p.id === id)?.name ?? id;

/** The rate line, and Set rate where nothing prices the model (spec 3.5). */
function RateLine({ rate, model, promptOnly = false }:
  { rate: RateInfo | null; model: string; promptOnly?: boolean }) {
  const words = rateWords(rate, { promptOnly });
  if (!words) return null;
  return (
    <p className="field-hint">
      {words}
      {rate?.source === "none" && model && (
        <> · <Link to={setRateHref(model)}>Set rate</Link></>
      )}
    </p>
  );
}

/** The window readout after the rate (spec 01i 6.3): the resolved model's
 *  window, or `window unknown` with **Set**, which opens that model's facts on
 *  its limits; and the ceiling's `reason` when the reply reserve leaves no
 *  room. Display only -- the facts panel owns the write. A card whose
 *  `limits` is null (nothing resolves, a native decision) shows nothing. */
function WindowLine({ limits, provider, fallbackShown = false }:
  { limits: CardLimits | null | undefined; provider: string; fallbackShown?: boolean }) {
  if (!limits) return null;
  const known = limits.window.value != null;
  // A riding fallback the row draws no line for (one the role inherits) still
  // shares the prompt, so its window is said here rather than nowhere.
  const fallback = !fallbackShown && limits.fallback_window?.value != null
    ? limits.fallback_window : null;
  return (
    <>
      <p className="field-hint">
        {windowWords(limits.window)}
        {!known && provider && limits.model && (
          <> · <Link to={modelLimitsPath(provider, limits.model)}>Set</Link></>
        )}
        {fallback && <>{" · fallback "}{windowWords(fallback)}</>}
      </p>
      <Warning text={limits.ceiling.reason || null} />
    </>
  );
}

/** Every task Decision answers -- its own `uses`, inherited or not -- each a
 *  link to that task's row on the edit form. */
function DecisionRoutes({ routes }: { routes: RouteRow[] }) {
  const using = routes.filter((r) => r.uses === "decision");
  if (using.length === 0) return <p className="field-hint">No task uses Decision yet.</p>;
  return (
    <details className="decision-tasks">
      <summary>Tasks answered by Decision ({using.length})</summary>
      <ul aria-label="Tasks answered by Decision">
        {using.map((r) => (
          <li key={r.key}>
            <Link to={`/models/edit${taskHash(r.key)}`}>{r.label}</Link>
            {r.role && r.role !== "decision" && (
              <span className="field-hint"> — inherits {ROLE_LABEL[r.role]}</span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** One generative role on the summary (spec 3.1): what it runs on, its
 *  fallback, its rate, every warning the old card carried, what its preset
 *  sends (folded), and on Decision the tasks it answers. */
export function RoleRow({ role, settings, health }:
  { role: GenerativeRole; settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth> }) {
  const card = settings.roles[role];
  const sel = card.resolves;
  const decision = role === "decision";
  const label = ROLE_LABEL[role];
  const warning = useWarning(sel?.provider ?? "", sel?.model ?? "",
                             decision ? null : ROLE_NEEDS[role][0], label);
  const unset = !card.stored.provider && !card.stored.model;
  const fb = card.fallback;
  const fbName = providerName(settings, fb.provider);
  const dropped = droppedFallbackWords(card.fallback_missing, label,
                                       fb.provider && fb.model ? `${fbName ?? fb.provider} ▸ ${fb.model}` : "",
                                       card.fallback_problem);
  return (
    <div className="role-row" role="group" aria-label={label}>
      <div className="role-row-name">{label}</div>
      <div className="role-row-body">
        {unset && SAME_AS[role] ? (
          <p>{SAME_AS[role]} — {describe(card.inherits)}</p>
        ) : sel ? (
          <p><SelectionLine provider={sel.provider} providerName={sel.provider_name || sel.provider}
                            model={sel.model} preset={sel.preset} presetName={sel.preset_name}
                            health={health} /></p>
        ) : card.stored.provider ? (
          <p><SelectionLine provider={card.stored.provider}
                            providerName={providerName(settings, card.stored.provider)}
                            model={card.stored.model} preset={card.stored.preset}
                            presetName={presetName(settings, card.stored.preset)} health={health} /></p>
        ) : (
          <p>Not set.</p>
        )}
        {fb.provider && (
          <p className="role-row-fallback">
            Fallback:{" "}
            <SelectionLine provider={fb.provider} providerName={fbName} model={fb.model}
                           preset={fb.preset} presetName={presetName(settings, fb.preset)}
                           health={health} />
            {card.limits?.fallback_window?.value != null && (
              <>{" · "}{windowWords(card.limits.fallback_window)}</>
            )}
          </p>
        )}
        <RateLine rate={card.rate} model={sel?.model ?? ""} />
        <WindowLine limits={card.limits} provider={sel?.provider ?? ""}
                    fallbackShown={!!fb.provider} />
        <Problem text={card.problem} />
        <Warning text={warning} />
        {decision && <DecideNote mode={card.decision_mode} decidesNatively={card.decides_natively} />}
        <Problem text={dropped} />
        {sel && (
          <WhatItSends presetId={sel.preset} provider={sel.provider} model={sel.model}
                       operation={decision ? "decide" : undefined} />
        )}
        {decision && <DecisionRoutes routes={settings.routes} />}
      </div>
    </div>
  );
}

/** The Embedding role (spec 3.1): what embeds, or today's off sentence with
 *  why; its rate is the input figure alone, and none when it is off. */
export function EmbeddingRow({ card, settings, health }:
  { card: EmbeddingCard; settings: InferenceSettings; health: ReadonlyMap<string, ProviderHealth> }) {
  const { provider, model } = card.stored;
  const warning = useWarning(provider, model, "embed", ROLE_LABEL.embedding);
  const on = card.on && card.resolves;
  return (
    <div className="role-row" role="group" aria-label="Embedding">
      <div className="role-row-name">Embedding</div>
      <div className="role-row-body">
        {on && card.resolves ? (
          <>
            <p><SelectionLine provider={card.resolves.provider}
                              providerName={providerName(settings, card.resolves.provider)
                                ?? card.resolves.provider_name}
                              model={card.resolves.model} health={health} withPreset={false} /></p>
            <RateLine rate={card.rate} model={card.resolves.model} promptOnly />
            <WindowLine limits={card.limits} provider={card.resolves.provider} />
          </>
        ) : (
          <>
            <p>Off — nothing is embedded, and semantic recall is not used.</p>
            <Problem text={card.problem ?? null} />
          </>
        )}
        <Warning text={warning} />
      </div>
    </div>
  );
}
