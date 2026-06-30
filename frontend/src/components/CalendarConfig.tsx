import { useEffect, useState } from "react";
import { api, type CalendarConfig as Cfg } from "../api/client";

const REGIONS = ["US", "GB", "CA", "AU", "IL", ""];

export function CalendarConfig({ cid }: { cid: string }) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getCalendarConfig(cid).then(setCfg).catch(() => setCfg(null));
  }, [cid]);

  if (!cfg) return <div className="field-hint">Loading calendar…</div>;

  function setRegion(region: string) {
    setSaved(false);
    setCfg({ ...cfg!, primary: { ...cfg!.primary, region } });
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
        Holidays region
        <select aria-label="Holidays region" value={cfg.primary.region}
                onChange={(e) => setRegion(e.target.value)}>
          {REGIONS.map((r) => <option key={r || "none"} value={r}>{r || "None"}</option>)}
        </select>
      </label>
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
}
