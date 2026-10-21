import type { CalendarConfig as Cfg } from "../api/client";

const REGIONS = ["US", "GB", "CA", "AU", "IL", ""];

/** The widest warn window the store will keep (`calendars.MAX_WARN_DAYS`, #106).
 *  Mirrored rather than fetched: it is a constant, and a form that lets a reader
 *  type past it shows a number the server did not store. Anything past it is
 *  clamped here so the control cannot disagree with what was saved. */
const MAX_WARN_DAYS = 365;

/** What a typed warn window saves as.
 *
 *  `Number`, not `parseInt`: a number input accepts exponent notation, and
 *  `parseInt("1e2", 10)` stops at the `e` and answers 1 -- so typing a hundred
 *  days would silently save a one-day window. Truncated rather than rounded,
 *  because the store keeps whole days and that is what the old parse did with
 *  "1.5". A blank stays `null` ("no opinion"); anything unparseable becomes 0,
 *  which is a real setting here. */
function parseWarnDays(value: string): number | null {
  const raw = value.trim();
  const n = Number(raw);
  const days = Number.isFinite(n) ? Math.trunc(n) : 0;
  return raw === "" ? null : Math.min(MAX_WARN_DAYS, Math.max(0, days));
}

type FieldProps = { cfg: Cfg; uid: string; edit: (patch: Partial<Cfg>) => void };

/** Which calendar, and -- for the two that have one -- its holiday region. */
export function ProviderFields(
  { cfg, uid, edit, providers }: FieldProps & { providers: { id: string; name: string }[] },
) {
  const setPrimary = (patch: Partial<Cfg["primary"]>) => edit({ primary: { ...cfg.primary, ...patch } });
  return (
    <div className="config-fields">
      <div className="config-field">
        <label htmlFor={`${uid}-calendar`}>Calendar</label>
        <select id={`${uid}-calendar`} aria-label="Calendar" value={cfg.primary.provider}
                onChange={(e) => setPrimary({ provider: e.target.value, region: e.target.value === "gregorian" ? "US" : "" })}>
          {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </div>
      {cfg.primary.provider === "gregorian" && (
        <div className="config-field">
          <label htmlFor={`${uid}-region`}>Holidays region</label>
          <select id={`${uid}-region`} aria-label="Holidays region" value={cfg.primary.region}
                  onChange={(e) => setPrimary({ region: e.target.value })}>
            {REGIONS.map((r) => <option key={r || "none"} value={r}>{r || "None"}</option>)}
          </select>
        </div>
      )}
      {cfg.primary.provider === "hebrew" && (
        <div className="config-field">
          <label htmlFor={`${uid}-region`}>Observance</label>
          <select id={`${uid}-region`} aria-label="Observance" value={cfg.primary.region}
                  onChange={(e) => setPrimary({ region: e.target.value })}>
            <option value="">Diaspora</option>
            <option value="IL">Israel</option>
          </select>
        </div>
      )}
    </div>
  );
}

/** World scope's `confirmed` checkbox -- see `CalendarConfig` for why only there. */
export function ConfirmedField({ cfg, edit }: Omit<FieldProps, "uid">) {
  return (
    <div className="calendar-confirmed">
      <label className="checkbox-row">
        <input type="checkbox" aria-label="Confirmed" checked={cfg.confirmed}
               onChange={(e) => edit({ confirmed: e.target.checked })} />
        Confirmed
      </label>
      <p className="config-caption">
        Campaigns started from this world inherit this calendar and will not ask again.
      </p>
    </div>
  );
}

/** How the record reckons time: when a thread goes stale, and how far ahead
 *  an upcoming holiday or event is warned of. */
export function ReckoningFields({ cfg, uid, edit, isWorld }: FieldProps & { isWorld: boolean }) {
  return (
    <div className="config-fields">
      {/* The aging knob (#103), here because it is a fact about how the
          record reckons time and this is where those live — calendar.json
          holds it beside the calendars themselves. A world's copy is the
          default its campaigns are created with, same as everything else in
          this file. Empty means "no opinion" and saves as 0, which the store
          answers with its own default rather than a threshold that would
          call every record stale on the day it was written. */}
      <div className="config-field">
        <label htmlFor={`${uid}-stale`}>Stale after</label>
        <div className="config-input">
          <input id={`${uid}-stale`} type="number" aria-label="Stale after days" min={1}
                 value={cfg.stale_after_days || ""}
                 onChange={(e) => edit({ stale_after_days: parseInt(e.target.value, 10) || 0 })} />
          <span className="config-unit" aria-hidden>days</span>
        </div>
        <p className="config-caption">
          Days a thread or commitment may go untouched before the ledger calls it stale.
          {isWorld && " Campaigns started from this world begin with this threshold."}
        </p>
      </div>
      {/* The warn window (#106), beside the aging knob because it is the
          same kind of fact: how far ahead this record reckons. Empty is "no
          opinion" and saves as null, which the store answers with its own
          default; a typed 0 is a real setting — no warnings in this
          campaign — which is why the two cannot share `stale_after_days`'
          0-means-unset convention.

          Clamped to the same ceiling the store enforces (`MAX_WARN_DAYS`).
          Without it, typing 1000 leaves the form showing 1000 and reporting
          it onward while the server has stored 365 — a control that lies
          about what was saved, which is worse than one that refuses the
          input. */}
      <div className="config-field">
        <label htmlFor={`${uid}-warn`}>Warn ahead</label>
        <div className="config-input">
          <input id={`${uid}-warn`} type="number" aria-label="Warn ahead days" min={0}
                 max={MAX_WARN_DAYS} value={cfg.warn_days ?? ""}
                 onChange={(e) => edit({ warn_days: parseWarnDays(e.target.value) })} />
          <span className="config-unit" aria-hidden>days</span>
        </div>
        <p className="config-caption">
          Days ahead of an upcoming holiday or scheduled event to warn you, once. 0 turns
          the warnings off.
          {isWorld && " Campaigns started from this world begin with this window."}
        </p>
      </div>
    </div>
  );
}
