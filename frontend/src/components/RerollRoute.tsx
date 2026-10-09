import { useEffect, useState, type KeyboardEvent } from "react";

import { api, type InferenceSettings } from "../api/client";
import { errorText } from "../api/errors";
import { PresetSelect } from "./inference/PresetSelect";
import { ProviderModelPicker } from "./inference/ProviderModelPicker";

/** One reroll's override (#77, spec 5.6): the provider to send it to, the
 *  model to run there and the sampler preset to run it under. Each part `""`
 *  leaves that part to the campaign's scene route, and all three empty is
 *  Default -- what every reroll did before this existed. */
export type RerollRoute = { provider: string; model: string; preset: string };

export const NO_REROLL_ROUTE: RerollRoute = { provider: "", model: "", preset: "" };

/** What a route adds to a reroll's body: only the parts that were chosen, so
 *  Default sends no override at all rather than three empty ones. */
export function rerollOverrides(route: RerollRoute): Partial<RerollRoute> {
  return Object.fromEntries(Object.entries(route).filter(([, v]) => v));
}

/** What the campaign's scene route resolves to, if the view says. */
function standingOf(view: InferenceSettings | null) {
  return view?.routes.find((r) => r.tasks.includes("regenerate"))?.resolves ?? null;
}

/** Default, spelled out: what the campaign's scene route resolves to. */
function describe(view: InferenceSettings | null): string {
  const standing = standingOf(view);
  if (!standing) return "the campaign's scene model";
  return [standing.provider_name || standing.provider, standing.model, standing.preset_name]
    .filter(Boolean).join(" · ");
}

/** What a reroll naming a provider and no model runs, in words: the scene
 *  route's standing model, on any store -- the server reads a library it has
 *  not upgraded yet as the new layout too, where a provider has no model of
 *  its own (spec 5.6). */
function providerAloneWords(standingModel: string): string {
  return standingModel ? `Keeps ${standingModel}`
    : "The scene route names no model to keep: choose one.";
}

/** Pick the provider, model and preset ONE reroll runs on.
 *
 *  Provider ▸ model comes from `ProviderModelPicker`, asked for what a scene
 *  turn needs (`generate`), so every kind of provider offers its models the
 *  same way and one that lists none -- the Claude subscription -- takes a
 *  typed id. The preset is `PresetSelect`, its "" being the route's own.
 *
 *  The providers, presets and what Default runs are the campaign's inference
 *  view, read when this mounts -- which is when the reader opens the popover,
 *  so the answer is the current one rather than whatever the play view read
 *  when the campaign opened (`getCampaignInference` is uncached).
 */
export default function RerollRoutePicker({ cid, value, onChange, onPending }: {
  cid: string;
  value: RerollRoute;
  onChange: (route: RerollRoute) => void;
  /** Whether a model id is typed and not yet taken ("Use this id"). The
   *  holder's Reroll sends `value`, which does not name it -- so it waits
   *  until it does, rather than silently running the standing model in its
   *  place. False again once this goes. */
  onPending?: (pending: boolean) => void;
}) {
  const [view, setView] = useState<InferenceSettings | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    let live = true;
    api.getCampaignInference(cid)
      .then((v) => { if (live) { setView(v); setFailed(null); } })
      .catch((err: unknown) => { if (live) { setView(null); setFailed(err); } });
    return () => { live = false; };
  }, [cid]);

  const isDefault = !value.provider && !value.model && !value.preset;
  const standingModel = standingOf(view)?.model ?? "";

  // Enter on these controls is theirs, never the popover's commit: a select's
  // menu, a button's press and the typed-id box (whose id lands only on "Use
  // this id") all answer it, and letting it bubble sent the reroll through the
  // route the reader was still choosing. A model row has no Enter of its own,
  // so there it commits like the guidance box. Escape is left to bubble:
  // backing out of the popover from any control is deliberate. (A model row's
  // arrows are the picker's own to keep, for every holder of one.)
  function keepOwnKeys(e: KeyboardEvent) {
    const t = e.target;
    const onRadio = t instanceof HTMLInputElement && t.type === "radio";
    if (e.key === "Enter" && !onRadio) e.stopPropagation();
  }

  return (
    // Keydown is only filtered here, never acted on; the controls inside are
    // the interactive elements.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div className="reroll-route" onKeyDown={keepOwnKeys}>
      <div className="reroll-default">
        <button type="button" className="subtle" aria-pressed={isDefault}
                onClick={() => onChange(NO_REROLL_ROUTE)}>
          Default
        </button>
        <span className="field-hint">{describe(view)}</span>
      </div>
      {failed !== null && (
        <p className="field-hint">Couldn't read the providers: {errorText(failed)}</p>
      )}
      {view && (
        <>
          <ProviderModelPicker
            needs={["generate"]} providers={view.providers}
            value={{ provider: value.provider, model: value.model }}
            onChange={(pm) => onChange({ ...value, ...pm })}
            onDraft={(draft) => { setTyped(draft); onPending?.(!!draft); }} />
          {typed && (
            <p className="field-hint" role="status">
              Use this id to reroll on {typed}, or clear the box.
            </p>
          )}
          {/* A provider with no model sends `{provider}` alone. Said here, so
              the reroll says what it will run before it is sent -- and what
              that is depends on the layout (`resolve._overridden`): at format
              2 the scene route's standing model, THERE (spec 5.6), an id
              another provider may not serve; at format 1 (an upgrade pending
              or failed) the provider's OWN model, which may be another, and
              pricier, one. */}
          {value.provider && !value.model && (
            <p className="field-hint">
              {providerAloneWords(standingModel)}
            </p>
          )}
          {/* Offered in either settings layout: `override_inference` applies
              a reroll's preset at format 1 as well as 2. The select names
              itself (`label`); the caption is for the eye. */}
          <div className="reroll-preset">
            <span className="field-label" aria-hidden="true">Preset</span>
            <PresetSelect value={value.preset} presets={view.presets} allowClear
                          label="Reroll preset" emptyLabel="The route's preset"
                          onChange={(preset) => onChange({ ...value, preset })} />
          </div>
        </>
      )}
    </div>
  );
}
