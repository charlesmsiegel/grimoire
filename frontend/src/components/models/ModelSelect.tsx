import { useEffect, useRef, useState } from "react";
import type { CapabilityModel, CapabilityNeed, InferenceProvider, ProviderHealth } from "../../api/client";
import { errorText } from "../../api/errors";
import { TestCallDialog, useModelTests } from "../inference/TestCallDialog";
import { probesFor, useModelList } from "../inference/useModelList";
import { healthWord } from "./health";

/** The model select's value for "Other model id…": a byte no model id holds. */
const OTHER = "\u0000other";

function providerLabel(p: InferenceProvider, health: ProviderHealth | undefined): string {
  const word = healthWord(health);
  return `${p.name}${word ? ` (${word})` : ""}`
    + (p.usable ? "" : ` — cannot send: ${p.problem ?? "it cannot send"}`);
}

/** Provider ▾ then model ▾ (spec 3.2): the Models page's one model control.
 *
 *  - A provider that cannot send stays choosable -- a stored choice must be
 *    adjustable while its key is fixed on Providers -- and says why.
 *  - A stored provider or model the lists no longer hold is kept as the
 *    selected option, so opening the form never changes a value.
 *  - Models come from `useModelList` (the picker's own logic): Fits this
 *    role, then Unverified; a model known not to fit is not offered.
 *  - "Other model id…" takes any id; it is then judged on its own, and gets
 *    Test… when that verdict is unverified, as a listed unverified one does.
 *  - Test… opens `TestCallDialog`, which previews and waits for a yes; a run
 *    outlives its dialog and is rejoined (`useModelTests`). */
export function ModelSelect({ label, needs, value, onChange, providers, health, emptyLabel,
                              disabled = false }:
  { label: string; needs: CapabilityNeed[]; value: { provider: string; model: string };
    onChange: (v: { provider: string; model: string }) => void;
    providers: InferenceProvider[]; health: ReadonlyMap<string, ProviderHealth>;
    emptyLabel: string; disabled?: boolean }) {
  const { provider, model } = value;
  const list = useModelList(provider, needs, model);
  const [other, setOther] = useState(false);
  const [draft, setDraft] = useState("");
  const [testing, setTesting] = useState<{ model: string; row: CapabilityModel | null } | null>(null);
  const modelRef = useRef<HTMLSelectElement>(null);
  // A landed test can move the model out of Unverified, taking the Test…
  // that had focus with it: focus goes back to the model select.
  const refocus = useRef(false);
  const tests = useModelTests(() => {
    refocus.current = document.activeElement instanceof HTMLElement
      && !!document.activeElement.closest(".model-select");
    list.reask(false);
  });
  useEffect(() => {
    if (!refocus.current || testing) return;
    if (document.activeElement && document.activeElement !== document.body) return;
    refocus.current = false;
    modelRef.current?.focus();
  }, [list.listed, testing]);
  useEffect(() => { setOther(false); setDraft(""); }, [provider]);

  const chosen = providers.find((p) => p.id === provider);
  const rows = list.listed ? [...list.listed.placements.values()] : [];
  const fits = rows.flatMap((p) => (p.group === "fits" ? [p.row] : []));
  const unverified = rows.flatMap((p) => (p.group === "unverified" ? [p.row] : []));
  const offered = new Set([...fits, ...unverified].map((r) => r.id));
  const chosenRow = list.known?.group === "unverified" ? list.known.row
    : list.verdict?.group === "unverified" ? list.verdict.row : null;

  function takeDraft() {
    const id = draft.trim();
    if (!id || disabled) return;
    onChange({ provider, model: id });
    setOther(false);
    setDraft("");
  }

  return (
    <div className="model-select" role="group" aria-label={label}>
      <select aria-label={`${label} provider`} value={provider} disabled={disabled}
              onChange={(e) => onChange({ provider: e.target.value, model: "" })}>
        <option value="">{emptyLabel}</option>
        {providers.map((p) => (
          <option key={p.id} value={p.id} title={p.problem ?? undefined}>
            {providerLabel(p, health.get(p.id))}
          </option>
        ))}
        {provider && !chosen && <option value={provider}>{provider} (missing provider)</option>}
      </select>
      {chosen && !chosen.usable && (
        <p className="field-hint field-warning" role="note">
          {chosen.name} cannot send: {chosen.problem ?? "it cannot send"}
        </p>
      )}
      {provider && (
        <>
          <select ref={modelRef} aria-label={`${label} model`} value={other ? OTHER : model}
                  disabled={disabled}
                  onChange={(e) => {
                    if (e.target.value === OTHER) { setOther(true); return; }
                    setOther(false);
                    onChange({ provider, model: e.target.value });
                  }}>
            <option value="">Choose a model…</option>
            {fits.length > 0 && (
              <optgroup label="Fits this role">
                {fits.map((r) => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
              </optgroup>
            )}
            {unverified.length > 0 && (
              <optgroup label="Unverified">
                {unverified.map((r) => <option key={r.id} value={r.id}>{r.name || r.id}</option>)}
              </optgroup>
            )}
            {model && !offered.has(model) && (
              <option value={model}>
                {model}{list.listed ? " (not in this provider's list)" : ""}
              </option>
            )}
            <option value={OTHER}>Other model id…</option>
          </select>
          {other && (
            <span className="model-typed">
              <input aria-label={`${label} model id`} value={draft} disabled={disabled}
                     placeholder="vendor/model-name"
                     onChange={(e) => setDraft(e.target.value)} />
              <button type="button" className="subtle" disabled={disabled || !draft.trim()}
                      onClick={takeDraft}>
                Use this id
              </button>
            </span>
          )}
          {list.failed !== null && (
            <p className="field-hint">Couldn't list this provider's models: {errorText(list.failed)}</p>
          )}
          {list.listed?.reason && <p className="field-hint">{list.listed.reason}</p>}
          {list.needsVerdict && (
            <p className="field-hint">
              {list.verdict === null ? "Checking what is known of this id…"
                : list.verdict.group === "hidden" ? `Known not to fit: ${list.verdict.reason}`
                : list.verdict.group === "fits" ? "Fits"
                : `Unverified: ${list.verdict.row.reason}`}
            </p>
          )}
          {model && chosenRow && (
            <button type="button" className="subtle" aria-label={`Test ${model}`}
                    disabled={disabled} onClick={() => setTesting({ model, row: chosenRow })}>
              Test…
            </button>
          )}
        </>
      )}
      {testing && (
        <TestCallDialog key={`${provider}\u0000${testing.model}`}
                        provider={provider} model={testing.model}
                        capabilities={probesFor(needs, testing.row)}
                        tests={tests} onClose={() => {
                          // The dialog hands focus back to its Test… -- unless
                          // a landed test redrew it away, and then this does.
                          refocus.current = true;
                          setTesting(null);
                        }} />
      )}
    </div>
  );
}
