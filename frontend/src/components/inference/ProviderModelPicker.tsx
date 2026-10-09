import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import {
  api, type CapabilityModel, type CapabilityNeed, type ModelCapabilities,
  type TestableCapability,
} from "../../api/client";
import { errorText } from "../../api/errors";
import { TestCallDialog, useModelTests } from "./TestCallDialog";

export type ProviderModel = { provider: string; model: string };

/** Adapters whose provider can be asked for a catalog (`llm.LISTABLE_KINDS`).
 *  The Claude subscription's models are SDK aliases with nothing to list, so
 *  an empty list there is not a stale one, and no Refresh would help. */
const LISTABLE = new Set(["openrouter", "openai_compatible", "anthropic"]);

/** Where one model lands for a set of needs. */
type Placement =
  | { group: "fits" | "unverified"; row: CapabilityModel }
  | { group: "hidden"; id: string; reason: string };

/** Several needs' answers as one list (spec 6.3: "the capabilities API takes
 *  one need per call, so a route needing two combines two calls").
 *
 *  A model **fits** when every need fits, is **hidden** when any need hides it
 *  (with that need's reason), and is otherwise **unverified** -- carrying the
 *  reason from a need that left it unverified, which is the one worth reading.
 *  A provider that rules a need out for every model answers with one `reason`
 *  and no rows, and that reason is the answer for the whole list. */
export function combine(answers: ModelCapabilities[]): {
  reason: string | null; placements: Map<string, Placement>;
} {
  const reason = answers.find((a) => a.reason)?.reason ?? null;
  const placements = new Map<string, Placement>();
  if (reason !== null) return { reason, placements };
  const ids = new Set<string>();
  for (const a of answers) {
    for (const r of [...a.groups.fits, ...a.groups.unverified]) ids.add(r.id);
    for (const h of a.hidden) ids.add(h.id);
  }
  for (const id of [...ids].sort()) {
    const hidden = answers.map((a) => a.hidden.find((h) => h.id === id)).find(Boolean);
    if (hidden) {
      placements.set(id, { group: "hidden", id, reason: hidden.reason });
      continue;
    }
    const fits = answers.map((a) => a.groups.fits.find((r) => r.id === id));
    const unverified = answers.map((a) => a.groups.unverified.find((r) => r.id === id))
      .find(Boolean);
    const everyFits = fits.every(Boolean);
    const row = everyFits ? fits[0] : unverified ?? fits.find(Boolean);
    if (row) placements.set(id, { group: everyFits ? "fits" : "unverified", row });
  }
  return { reason, placements };
}

/** The probes a Test… sends for `needs` on `row`: each need it does not
 *  already answer yes to. A need names its own probe, except `decide`: a
 *  decision is answered by structured generation wherever a model generates
 *  (spec 7.4), so it tests `generate`. It tests `decide_native` as well only
 *  for a row that is KNOWN unable to generate and not known unable to decide
 *  natively: a model that generates stays structured whatever that probe
 *  finds, so the probe would spend and change nothing. The server refuses
 *  anything it has no probe for, with a reason the dialog shows. */
function probesFor(needs: CapabilityNeed[], row: CapabilityModel | null): TestableCapability[] {
  const all = [...new Set(needs.map((n) => (n === "decide" ? "generate" : n)))] as
    TestableCapability[];
  if (needs.includes("decide") && row?.capabilities.generate?.value === "no"
      && row.capabilities.decide_native?.value !== "no") {
    all.push("decide_native");
  }
  const open = all.filter((c) => row?.capabilities[c]?.value !== "yes");
  return open.length ? open : all;
}

/** Ask each need of one provider, optionally about one model. */
function ask(provider: string, needs: CapabilityNeed[], model?: string) {
  return Promise.all(needs.map((need) => (model === undefined
    ? api.readConnectionCapabilities(provider, need)
    : api.readConnectionCapabilities(provider, need, model))));
}

/** Provider, then a model that can do what the caller needs (spec 6.3).
 *
 *  The model list comes from the server, one capabilities call per need,
 *  combined by `combine`: **Fits**, **Unverified** (each with why, and a
 *  **Test…** that opens `TestCallDialog`), and nothing at all for a model
 *  known not to. A provider that cannot serve the need for any model shows
 *  that one reason in place of the list.
 *
 *  A typed model id is always accepted -- a catalog can be stale, partial or
 *  absent -- and is judged on its own: the same calls, narrowed to that id,
 *  say which group it lands in, so a typed id is never silently trusted nor
 *  silently refused. The chosen model, when no row lists it, is shown with
 *  that verdict under "Typed id".
 *
 *  A test outlives its dialog: the picker holds each run by provider and
 *  model (`useModelTests`), so closing the dialog mid-run and pressing Test…
 *  again rejoins that run rather than offering a second, paid one.
 *
 *  No needs, nothing to ask: the provider list alone, and no model list.
 *
 *  A provider that cannot send says so beside its name -- with `problem`, the
 *  server's own sentence, when the caller has one, and neutrally otherwise,
 *  since a missing key is only one of the reasons.
 *
 *  The chosen model never goes out of sight: a list that could not be read
 *  still shows it (and still takes a typed id), and a provider that rules the
 *  need out for every model shows it beside that reason. */
export function ProviderModelPicker({ needs, value, onChange, providers, disabled, onDraft }:
  { needs: CapabilityNeed[]; value: ProviderModel;
    onChange: (value: ProviderModel) => void;
    providers: { id: string; name: string; kind?: string; usable?: boolean;
                 problem?: string | null }[];
    disabled?: boolean;
    /** Told the typed-id box's text (trimmed) whenever it changes, and ""
     *  when the picker goes: a typed id is not the value until "Use this id"
     *  takes it, so a holder whose own button sends the value -- a reroll --
     *  can hold that button until it is. */
    onDraft?: (draft: string) => void }) {
  const name = useId();
  const [listed, setListed] = useState<ReturnType<typeof combine> | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const [typedVerdict, setTypedVerdict] = useState<Placement | null>(null);
  const [draft, setDraft] = useState("");
  const draftSink = useRef(onDraft);
  draftSink.current = onDraft;
  useEffect(() => { draftSink.current?.(draft.trim()); }, [draft]);
  useEffect(() => () => { draftSink.current?.(""); }, []);
  const [testing, setTesting] = useState<{ model: string; probes: TestableCapability[] } | null>(
    null);
  // Bumped when a test lands: the answers it changed are stale.
  const [asked, setAsked] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  // Where focus goes back to once the rows are drawn again. A landed test
  // redraws every row, so the control focus was on -- or the Test… a dialog
  // will hand focus back to when it closes -- is gone, and focus fell to the
  // body, out of reach of every key in the picker and anything around it.
  const refocus = useRef<{ key: string; model: string } | null>(null);
  const tests = useModelTests(() => {
    const at = document.activeElement;
    if (at instanceof HTMLElement && root.current?.contains(at) && at.dataset.refocus) {
      refocus.current = { key: at.dataset.refocus, model: at.dataset.model ?? "" };
    }
    // Cleared here rather than by the re-ask below, which runs a commit later:
    // until then the old rows -- and the focus on one -- would still be up.
    setListed(null);
    setAsked((n) => n + 1);
  });

  // Held as a key, so a caller passing a fresh array each render asks once.
  const needKey = needs.join(",");
  const { provider, model } = value;

  useEffect(() => {
    setListed(null);
    setFailed(null);
    if (!provider || !needKey) return;
    let current = true;
    ask(provider, needKey.split(",") as CapabilityNeed[])
      .then((answers) => { if (current) setListed(combine(answers)); })
      .catch((err: unknown) => { if (current) setFailed(err); });
    return () => { current = false; };
  }, [provider, needKey, asked]);

  // The chosen model's own verdict, when no row lists it.
  const known = listed?.placements.get(model);
  const needsVerdict = !!listed && !!model && listed.reason === null && !known;
  useEffect(() => {
    setTypedVerdict(null);
    if (!needsVerdict) return;
    let current = true;
    ask(provider, needKey.split(",") as CapabilityNeed[], model)
      .then((answers) => {
        if (!current) return;
        const { reason, placements } = combine(answers);
        setTypedVerdict(placements.get(model)
          ?? { group: "hidden", id: model, reason: reason ?? "Nothing is known of this id." });
      })
      .catch(() => { if (current) setTypedVerdict(null); });
    return () => { current = false; };
  }, [needsVerdict, provider, needKey, model, asked]);

  // Focus back, once the dialog is shut and the rows are drawn again: to the
  // same control, else that model's row, else the provider. Only focus that
  // was LOST -- somewhere the reader moved it meanwhile is theirs.
  useEffect(() => {
    const want = refocus.current;
    if (!want || testing) return;
    if (listed === null && failed === null && provider && needKey) return;   // still asking
    refocus.current = null;
    const at = document.activeElement;
    if (!root.current || (at && at !== document.body)) return;
    const all = [...root.current.querySelectorAll<HTMLElement>("[data-refocus]")];
    const find = (key: string) => all.find((el) => el.dataset.refocus === key
      && !(el as HTMLButtonElement | HTMLInputElement).disabled);
    (find(want.key) ?? find(`radio:${want.model}`)
      ?? root.current.querySelector<HTMLElement>("select"))?.focus();
  }, [testing, listed, failed, provider, needKey]);

  const rows = listed ? [...listed.placements.values()] : [];
  const fits = rows.flatMap((p) => (p.group === "fits" ? [p.row] : []));
  const unverified = rows.flatMap((p) => (p.group === "unverified" ? [p.row] : []));
  const chosen = known?.group === "hidden" ? known : needsVerdict ? typedVerdict : null;
  const typedShown = !!model && listed?.reason === null && (known?.group === "hidden" || needsVerdict);

  /** The typed id, chosen -- from its button or the box's Enter. */
  function chooseDraft() {
    const id = draft.trim();
    if (disabled || !id) return;
    onChange({ provider, model: id });
    setDraft("");
  }

  function test(id: string, row: CapabilityModel | null) {
    setTesting({ model: id, probes: probesFor(needs, row) });
  }

  function radio(id: string, label: string) {
    return (
      <label className="model-pick">
        <input type="radio" name={name} value={id} checked={model === id} disabled={disabled}
               data-refocus={`radio:${id}`} data-model={id}
               onChange={() => onChange({ provider, model: id })} />
        {label}
      </label>
    );
  }

  function testButton(id: string, label: string, row: CapabilityModel | null) {
    return (
      <button type="button" className="subtle" aria-label={`Test ${label}`}
              data-refocus={`test:${id}`} data-model={id}
              disabled={disabled} onClick={() => test(id, row)}>
        Test…
      </button>
    );
  }

  /** The arrows on a model row are the radio group's own travel. A radio is
   *  not a typing target, so a bare ← or → bubbling to the shortcut registry
   *  answered whatever bare-arrow binding the page underneath holds -- the
   *  play view's variant swipe, which persists a different reply behind the
   *  form -- and that binding's `preventDefault` took the travel too. Kept
   *  here, so every holder of a picker gets it: a reroll popover, Response
   *  actions and the Inspector's Models section alike. Stopped, never
   *  prevented: the browser still moves the selection. */
  function keepArrows(e: KeyboardEvent<HTMLDivElement>) {
    const t = e.target;
    if (t instanceof HTMLInputElement && t.type === "radio" && e.key.startsWith("Arrow")) {
      e.stopPropagation();
    }
  }

  const typedBox = (
    <div className="model-typed">
      <label>
        <span className="field-label">Or type a model id</span>
        <input aria-label="Model id" value={draft} disabled={disabled} data-refocus="typed"
               placeholder="vendor/model-name"
               onChange={(e) => setDraft(e.target.value)}
               onKeyDown={(e) => {
                 // Enter here is "Use this id", and goes no further: a
                 // form around the picker would submit, and the reroll
                 // popover commits on Enter. Not while an IME is still
                 // composing, where Enter accepts the character.
                 if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
                 e.preventDefault();
                 e.stopPropagation();
                 chooseDraft();
               }} />
      </label>
      <button type="button" className="subtle" disabled={disabled || !draft.trim()}
              onClick={chooseDraft}>
        Use this id
      </button>
    </div>
  );

  /** The chosen model alone, with what is (not) known of it, where no list
   *  says anything about it. */
  const chosenAlone = (note: string) => (
    <fieldset className="model-group">
      <legend>Typed id</legend>
      {radio(model, model)}
      <span className="field-hint"> {note}</span>
    </fieldset>
  );

  const kind = providers.find((p) => p.id === provider)?.kind;
  const emptyHint = kind && !LISTABLE.has(kind)
    ? "This provider lists no models: type an id."
    : "No listed model can do this. Refresh this provider's models on the Providers page, "
      + "or type an id.";

  return (
    // Keydown is only filtered here, never acted on; the controls inside are
    // the interactive elements.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div className="provider-model-picker" ref={root} onKeyDown={keepArrows}>
      <label>
        <span className="field-label">Provider</span>
        <select aria-label="Provider" value={provider} disabled={disabled}
                onChange={(e) => onChange({ provider: e.target.value, model: "" })}>
          <option value="">Choose a provider…</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}{p.usable === false
                ? ` (cannot send${p.problem ? `: ${p.problem}` : ""})` : ""}
            </option>
          ))}
          {provider && !providers.some((p) => p.id === provider) && (
            <option value={provider}>{provider} (missing provider)</option>
          )}
        </select>
      </label>

      {provider && failed !== null && (
        <>
          <p className="field-hint">Couldn't list this provider's models: {errorText(failed)}</p>
          {model && chosenAlone("Not checked: the list could not be read.")}
          {typedBox}
        </>
      )}
      {provider && listed?.reason && (
        <>
          <p className="field-hint">{listed.reason}</p>
          {model && chosenAlone(`Known not to fit: ${listed.reason}`)}
        </>
      )}

      {provider && listed && listed.reason === null && (
        <>
          {fits.length > 0 && (
            <fieldset className="model-group">
              <legend>Fits</legend>
              <ul>
                {fits.map((r) => <li key={r.id}>{radio(r.id, r.name || r.id)}</li>)}
              </ul>
            </fieldset>
          )}
          {unverified.length > 0 && (
            <fieldset className="model-group">
              <legend>Unverified</legend>
              <ul>
                {unverified.map((r) => (
                  <li key={r.id}>
                    {radio(r.id, r.name || r.id)}
                    {testButton(r.id, r.name || r.id, r)}
                    <span className="field-hint">{r.reason}</span>
                  </li>
                ))}
              </ul>
            </fieldset>
          )}
          {fits.length === 0 && unverified.length === 0 && (
            <p className="field-hint">{emptyHint}</p>
          )}
          {typedShown && (
            <fieldset className="model-group">
              <legend>Typed id</legend>
              {radio(model, model)}
              {chosen === null ? (
                <span className="field-hint"> Checking what is known of it…</span>
              ) : chosen.group === "hidden" ? (
                <span className="field-hint"> Known not to fit: {chosen.reason}</span>
              ) : chosen.group === "fits" ? (
                <span className="field-hint"> Fits</span>
              ) : (
                <>
                  <span className="field-hint"> Unverified: {chosen.row.reason}</span>
                  {testButton(model, model, chosen.row)}
                </>
              )}
            </fieldset>
          )}
          {typedBox}
        </>
      )}

      {testing && (
        <TestCallDialog key={`${provider}\u0000${testing.model}`}
                        provider={provider} model={testing.model} capabilities={testing.probes}
                        tests={tests} onClose={() => {
                          // The dialog hands focus back to its Test… -- unless
                          // a landed test redrew it away, and then this does.
                          refocus.current = { key: `test:${testing.model}`, model: testing.model };
                          setTesting(null);
                        }} />
      )}
    </div>
  );
}
