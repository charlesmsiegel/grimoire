import { useEffect, useState } from "react";
import { api, type CalendarConfig as Cfg, type CalendarScope } from "../api/client";

const REGIONS = ["US", "GB", "CA", "AU", "IL", ""];

/** The calendar editor for either scope (#223).
 *
 *  One store file under two roots: a campaign's own calendar.json, and the
 *  world default `create_campaign` copies into every campaign started from that
 *  world. The form is the same because the file is the same; the scope only
 *  decides the URL, the wording of the hints, and whether `confirmed` gets a
 *  control of its own — campaign-side the scene inspector already owns that
 *  flag, world-side nothing else can set it.
 */
export function CalendarConfig({ scope, onConfig }: {
  scope: CalendarScope;
  /** Every config this panel comes to hold — the one it loads, and each one it
   *  saves. The world Overview's checklist row is derived from it rather than
   *  from a read of its own: two components fetching one endpoint on one mount
   *  is a request nobody needs and a second `confirmed` that can disagree with
   *  this one. Not called when the load fails, which is what leaves that row
   *  off the list entirely — unknown is not unconfirmed. */
  onConfig?: (cfg: Cfg) => void;
}) {
  const isWorld = scope.kind === "world";
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [providers, setProviders] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // A load that failed is not a load still running. Without the distinction
  // "Loading calendar…" is what an unreachable store shows forever, and on the
  // world Overview that is the first thing a new library puts on screen.
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    // Blanked first, so the form never shows one record's calendar under
    // another record's Save button: `save` posts to the CURRENT scope, so
    // pressing it in that window would write the record you left into the one
    // you are looking at. "Loading calendar…" has no Save to press.
    setCfg(null);
    setSaved(false);
    setError(null);
    setFailed(false);
    api.getCalendarConfig(scope)
      .then((c) => { if (live) { setCfg(c); onConfig?.(c); } })
      .catch(() => { if (live) { setCfg(null); setFailed(true); } });
    return () => { live = false; };
    // `onConfig` is deliberately not a dependency: callers pass an inline
    // lambda, and re-running this on every render of the parent would refetch
    // forever. The scope is the only thing that decides what to load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.id]);

  // Once per mount, not once per scope: the provider list is a global — the
  // built-in calendars plus whatever plugins the store holds — and nothing
  // about which record you are editing changes it.
  useEffect(() => {
    api.getCalendarProviders().then((r) => setProviders(r.providers)).catch(() => setProviders([]));
  }, []);

  if (!cfg) {
    return <div className="field-hint">
      {failed ? "Could not load this calendar." : "Loading calendar…"}
    </div>;
  }

  function setPrimary(patch: Partial<Cfg["primary"]>) {
    setSaved(false);
    setCfg({ ...cfg!, primary: { ...cfg!.primary, ...patch } });
  }

  async function save() {
    setError(null);
    try {
      await api.setCalendarConfig(scope, cfg!);
      setSaved(true);
      // The saved config, not a re-read: the server normalizes but does not
      // decide any field the form shows, and a second GET would race this
      // component's own scope effect if the reader had already moved on.
      onConfig?.(cfg!);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="calendar-config">
      {error && <div className="banner">{error}</div>}
      <label>
        Calendar
        <select aria-label="Calendar" value={cfg.primary.provider}
                onChange={(e) => setPrimary({ provider: e.target.value, region: e.target.value === "gregorian" ? "US" : "" })}>
          {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      {cfg.primary.provider === "gregorian" && (
        <label>
          Holidays region
          <select aria-label="Holidays region" value={cfg.primary.region}
                  onChange={(e) => setPrimary({ region: e.target.value })}>
            {REGIONS.map((r) => <option key={r || "none"} value={r}>{r || "None"}</option>)}
          </select>
        </label>
      )}
      {cfg.primary.provider === "hebrew" && (
        <label>
          Observance
          <select aria-label="Observance" value={cfg.primary.region}
                  onChange={(e) => setPrimary({ region: e.target.value })}>
            <option value="">Diaspora</option>
            <option value="IL">Israel</option>
          </select>
        </label>
      )}
      {/* World scope only, and above the aging knob because it is the decision
          the world Overview's checklist points at. `confirmed` means "a person
          chose this calendar"; campaign-side that answer is given to the scene
          inspector's prompt, and a second control there would be two ways to
          say the same thing. World-side there is no prompt to answer, and
          `create_campaign` copies the whole file — this flag included — into
          every campaign started from this world. */}
      {isWorld && (
        <>
          <label>
            <input type="checkbox" aria-label="Confirmed" checked={cfg.confirmed}
                   onChange={(e) => {
                     setSaved(false);
                     setCfg({ ...cfg!, confirmed: e.target.checked });
                   }} />
            Confirmed
          </label>
          <div className="field-hint">
            Campaigns started from this world inherit this calendar and will not ask again.
          </div>
        </>
      )}
      {/* The aging knob (#103), here because it is a fact about how the record
          reckons time and this is where those live — calendar.json holds it
          beside the calendars themselves. A world's copy is the default its
          campaigns are created with, same as everything else in this file.
          Empty means "no opinion" and saves as 0, which the store answers with
          its own default rather than a threshold that would call every record
          stale on the day it was written. */}
      <label>
        Stale after
        <input type="number" aria-label="Stale after days" min={1}
               value={cfg.stale_after_days || ""}
               onChange={(e) => {
                 setSaved(false);
                 setCfg({ ...cfg!, stale_after_days: parseInt(e.target.value, 10) || 0 });
               }} />
      </label>
      <div className="field-hint">
        Days a thread or commitment may go untouched before the ledger calls it stale.
        {isWorld && " Campaigns started from this world begin with this threshold."}
      </div>
      {/* World-side this panel sits beside Mechanics, which has a Save of its
          own; "Save" twice in a row is ambiguous to anyone reading the page by
          its controls. The visible text stays "Save" and the name only extends
          it, so "click Save" still resolves (WCAG 2.5.3). */}
      <button className="primary" onClick={save}
              aria-label={isWorld ? "Save calendar" : undefined}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
}
