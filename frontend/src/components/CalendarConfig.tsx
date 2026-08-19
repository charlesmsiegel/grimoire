import { useEffect, useState } from "react";
import { api, type CalendarConfig as Cfg } from "../api/client";

const REGIONS = ["US", "GB", "CA", "AU", "IL", ""];

export function CalendarConfig({ cid }: { cid: string }) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [providers, setProviders] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getCalendarConfig(cid).then(setCfg).catch(() => setCfg(null));
    api.getCalendarProviders().then((r) => setProviders(r.providers)).catch(() => setProviders([]));
  }, [cid]);

  if (!cfg) return <div className="field-hint">Loading calendar…</div>;

  function setPrimary(patch: Partial<Cfg["primary"]>) {
    setSaved(false);
    setCfg({ ...cfg!, primary: { ...cfg!.primary, ...patch } });
  }

  async function save() {
    setError(null);
    try {
      await api.setCalendarConfig(cid, cfg!);
      setSaved(true);
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
      {/* The campaign's one aging knob (#103), here because it is a fact about
          how this campaign reckons time and this is where those live —
          calendar.json holds it beside the calendars themselves. Empty means
          "no opinion" and saves as 0, which the store answers with its own
          default rather than a threshold that would call every record stale on
          the day it was written. */}
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
      </div>
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
}
