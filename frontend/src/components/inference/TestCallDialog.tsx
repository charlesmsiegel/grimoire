import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ApiError, api, type ModelTestPreview, type ModelTestResult, type TestableCapability,
} from "../../api/client";
import { errorText } from "../../api/errors";
import { isAbortError, newAttemptId } from "../../api/stream";
import { isTopModal } from "../../shortcuts/registry";
import { useHotkeys } from "../../shortcuts/useHotkeys";
import { about } from "../cost";

/** What the estimate reads when the catalog states no price. A price nobody
 *  reported is never rendered as zero (CLAUDE.md, Costs). */
export const COST_UNKNOWN = "cost unknown — one tiny request";

/** The capabilities back out of the key they are held as. */
function listed(key: string): TestableCapability[] {
  return (key ? key.split(",") : []) as TestableCapability[];
}

/** One probe's outcome as a line. */
function outcome(cap: string, r: { ok: boolean; error?: string; dims?: number }): string {
  if (r.ok) return `${cap}: works${r.dims ? ` (${r.dims} dimensions)` : ""}`;
  return `${cap}: failed — ${r.error ?? "no reason given"}`;
}

/** The test runs in flight, by provider and model. */
export type ModelTests = {
  /** The run in flight for this model, if there is one. */
  live: (provider: string, model: string) => Promise<ModelTestResult> | undefined;
  /** Start a run for this model -- or hand back the one already in flight. */
  start: (provider: string, model: string,
          capabilities: TestableCapability[]) => Promise<ModelTestResult>;
};

function keyOf(provider: string, model: string): string {
  return `${provider}\u0000${model}`;
}

/** Every test run this page load has started, by provider and model: the run
 *  in flight, or -- after a wait lost to the network -- the attempt id the
 *  next Run must ask under. Module scope, not any one holder's: the picker on
 *  `/models` and the model page on `/providers` are two holders of the same
 *  model's test, and either can unmount while the run goes on. A holder of
 *  its own forgot the run with itself, so a Test… pressed after navigating
 *  (or on the other page) offered a second, paid run instead of the live one. */
const RUNS = new Map<string, { attempt: string; landed?: Promise<ModelTestResult> }>();
/** The mounted holders' `onLanded`, each heard once per landed run. */
const LISTENERS = new Set<{ current?: (result: ModelTestResult) => void }>();

/** Forget every run. For tests, which share this module. */
export function forgetModelTests(): void {
  RUNS.clear();
}

function startTest(provider: string, model: string,
                   capabilities: TestableCapability[]): Promise<ModelTestResult> {
  const key = keyOf(provider, model);
  const held = RUNS.get(key);
  if (held?.landed) return held.landed;
  const attempt = held?.attempt ?? newAttemptId();
  // No abort signal: the wait is nobody's to cancel. It ends when the run
  // lands or fails, whichever holder is mounted by then -- a test is a few
  // short probes, and stopping the wait early is what let one be paid twice.
  const landed = api.runModelTest(provider, { model, capabilities, confirm: true }, { attempt });
  const run = { attempt, landed };
  RUNS.set(key, run);
  // Each outcome answers for THIS run only: once the slot holds another (after
  // `forgetModelTests`), a settling stale promise must neither empty it nor
  // tell the holders a result that is not the live run's.
  const mine = () => RUNS.get(key) === run;
  landed.then(
    (result) => {
      if (!mine()) return;
      RUNS.delete(key);
      for (const heard of LISTENERS) heard.current?.(result);
    },
    (err: unknown) => {
      if (!mine()) return;
      // The server answered (a refusal, a failed run): the next Run is a new
      // test. Anything else lost the wait, not the run, so keep its id.
      if (err instanceof ApiError) RUNS.delete(key);
      else RUNS.set(key, { attempt });
    });
  return landed;
}

const TESTS: ModelTests = {
  live: (provider, model) => RUNS.get(keyOf(provider, model))?.landed,
  start: startTest,
};

/** The test runs, for a holder that shows a test dialog.
 *
 *  A test is a detached run on the server: closing the dialog does not stop
 *  it, and a dialog that held the run itself forgot it on close -- so the
 *  same model's Test… opened a fresh dialog offering a fresh, paid Run. The
 *  runs are kept here instead (module scope, above), keyed by provider and
 *  model, and any dialog for that model -- re-opened, on another page, after
 *  a remount -- rejoins the live one.
 *
 *  `start` is the one guard against paying twice: it is a synchronous lookup,
 *  so a second press that lands before React has re-rendered the button
 *  disabled is handed the first press's run rather than a second one.
 *
 *  Each run is sent under an attempt id minted here. A wait that is lost
 *  without the server answering (the network, not a refusal) keeps that id,
 *  so the next Run for the model asks under it and is handed the run it
 *  already started, or that run's outcome, rather than paying again. A run
 *  that landed, or that the server refused or failed, lets it go: the next
 *  Run is a new test. `onLanded` hears each landed result once while this
 *  holder is mounted, whether or not a dialog is open and whoever started
 *  the run -- that is when the holder's capability answers are stale. */
export function useModelTests(onLanded?: (result: ModelTestResult) => void): ModelTests {
  const heard = useRef(onLanded);
  useEffect(() => { heard.current = onLanded; });
  useEffect(() => {
    const listener = heard;
    LISTENERS.add(listener);
    return () => { LISTENERS.delete(listener); };
  }, []);
  return TESTS;
}

/** Ask a provider what one model can do, after saying what that will send
 *  and cost (spec 6.4, rule 1: nothing spends without asking).
 *
 *  On open it PREVIEWS -- which sends nothing, meters nothing and starts no
 *  run -- and shows the provider, the model, each probe's request and the
 *  estimate. Only **Run test** spends, with `confirm: true`, through `tests`
 *  (`useModelTests`), which hands back the run already in flight for this
 *  model rather than starting another. So the button is disabled while one
 *  is, a double click cannot pay twice, and a dialog closed mid-run and
 *  opened again shows that run still going instead of offering a new one.
 *  A preview the server refuses (no key, a capability the provider rules
 *  out) says why and offers nothing to run.
 *
 *  It is modal: it takes focus on open, keeps it (Tab past either end wraps
 *  round), gives it back to whatever had it on close, and Escape closes it. A caller showing several models keys it by
 *  provider and model, so nothing carries from one model's dialog to the
 *  next. */
/** What Tab can land on inside the dialog. */
const FOCUSABLE = "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])";

export function TestCallDialog({ provider, model, capabilities, tests, onClose }:
  { provider: string; model: string; capabilities: TestableCapability[];
    tests: ModelTests; onClose: () => void }) {
  const [preview, setPreview] = useState<ModelTestPreview | null>(null);
  const [refused, setRefused] = useState<unknown>(null);
  // The run this dialog is showing: one already in flight when it opened, or
  // the one Run test started.
  const [watching, setWatching] = useState<Promise<ModelTestResult> | null>(
    () => tests.live(provider, model) ?? null);
  const [result, setResult] = useState<ModelTestResult | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const self = useRef<HTMLDivElement>(null);

  const scope = useHotkeys(
    [{ keys: "escape", run: onClose, whileTyping: true, label: "Close", group: "Dialog" }],
    { modal: true },
  );

  // Focus stays in while it is up. `aria-modal` says the page behind is
  // inert, but nothing made it so: Tab walked out to it, and inside the
  // reroll popover Tab then Enter on the guidance box sent the reroll with
  // the dialog still open. The keyboard is the registry's, so this watches
  // where focus LANDS rather than which key moved it: focus arriving outside
  // goes back in, to the first control when it left from the last and to the
  // last when it left from the first. Only while this is the overlay on top
  // -- the shortcuts sheet drawn over it keeps its own focus. Declared before
  // the effect that hands focus back on close, so it is gone first.
  useEffect(() => {
    function hold(e: FocusEvent) {
      const box = self.current;
      const to = e.target;
      if (!box || !(to instanceof Node) || box.contains(to) || !isTopModal(scope)) return;
      const inside = [...box.querySelectorAll<HTMLElement>(FOCUSABLE)]
        .filter((el) => !(el as HTMLButtonElement).disabled);
      const from = e.relatedTarget;
      const first = inside[0];
      const last = inside[inside.length - 1];
      (from === first && last ? last : first ?? box).focus();
    }
    document.addEventListener("focusin", hold);
    return () => document.removeEventListener("focusin", hold);
  }, [scope]);

  // Focus in on open, and back to the opener on close -- if it is still on
  // the page (a landed test re-asks the picker, which may redraw its rows).
  useEffect(() => {
    const cameFrom = document.activeElement as HTMLElement | null;
    self.current?.focus();
    return () => { if (cameFrom?.isConnected) cameFrom.focus(); };
  }, []);

  // The capabilities as a key, so a caller passing a fresh array each render
  // does not re-ask the preview.
  const caps = capabilities.join(",");
  useEffect(() => {
    let current = true;
    setPreview(null);
    setRefused(null);
    api.previewModelTest(provider, { model, capabilities: listed(caps) })
      .then((p) => { if (current) setPreview(p); })
      .catch((err: unknown) => { if (current) setRefused(err); });
    return () => { current = false; };
  }, [provider, model, caps]);

  useEffect(() => {
    if (!watching) return;
    let current = true;
    watching.then(
      (landed) => { if (current) { setResult(landed); setWatching(null); } },
      (err: unknown) => {
        if (!current) return;
        if (!isAbortError(err)) setFailed(err);
        setWatching(null);
      });
    return () => { current = false; };
  }, [watching]);

  function run() {
    if (!preview) return;
    setFailed(null);
    setResult(null);
    setWatching(tests.start(provider, model, listed(caps)));
  }

  const running = watching !== null;
  // Portalled to the body. The picker that opens it can sit inside a
  // positioned, z-indexed overlay (the reroll popover), which is a stacking
  // context of its own: rendered there, the backdrop's `z-index` counted
  // only inside it, and the page's sticky chrome and the phone composer
  // painted over a dialog that claims to be modal.
  return createPortal(
    <div className="tagline-modal-backdrop" role="dialog" aria-modal="true"
         aria-label="Test a model" ref={self} tabIndex={-1}>
      <div className="tagline-modal">
        <h3>Test a model</h3>
        {refused ? (
          <p className="field-hint">{errorText(refused)}</p>
        ) : !preview ? (
          <p className="field-hint">Working out what the test would send…</p>
        ) : (
          <>
            <p>
              <strong>{preview.provider}</strong> · <code>{preview.model}</code>
            </p>
            <ul className="test-call-sends" aria-label="What is sent">
              {preview.sends.map((s) => <li key={s.capability}>{s.description}</li>)}
            </ul>
            <p className="field-hint">
              Estimated cost: {preview.estimated_cost_usd === null
                ? COST_UNKNOWN : about(preview.estimated_cost_usd)}
            </p>
          </>
        )}
        {result && (
          <ul className="test-call-results" aria-label="Results">
            {Object.entries(result.results).map(([cap, r]) => (
              <li key={cap}>{outcome(cap, r)}</li>
            ))}
            {!result.recorded && (
              <li className="field-hint">
                Nothing was recorded: the provider changed while the test ran, or no
                answer said anything about the model.
              </li>
            )}
          </ul>
        )}
        {failed !== null && <p className="field-hint">The test could not run: {errorText(failed)}</p>}
        <div className="form-actions">
          <button className="primary" type="button" onClick={run}
                  disabled={running || !preview}>
            {running ? "Testing…" : "Run test"}
          </button>
          <button className="subtle" type="button" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
