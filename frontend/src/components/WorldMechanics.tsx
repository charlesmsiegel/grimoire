import { useEffect, useState } from "react";
import { api, type ModuleSummary, type SheetCoverage } from "../api/client";

const KIND_LABELS: Record<string, string> = {
  characters: "Characters", pcs: "PCs", locations: "Locations", lore: "Lore",
  items: "Items", groups: "Groups", creatures: "Creatures",
};

export default function WorldMechanics({
  wid, worldMid = "", onPickMid = () => {},
}: { wid: string; worldMid?: string; onPickMid?: (mid: string) => void }) {
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<SheetCoverage | null>(null);

  const load = () =>
    api.getWorld(wid).then((w) => setValue(w.meta.module ?? ""));

  useEffect(() => {
    api.listModules().then(setMods).catch(() => setMods([]));
    load().catch(() => setValue(""));
  }, [wid]);

  useEffect(() => {
    if (!worldMid) { setCoverage(null); return; }
    let live = true;
    api.getWorldSheets(wid, worldMid)
      .then((r) => { if (live) setCoverage(r.coverage); })
      .catch(() => { if (live) setCoverage(null); });
    return () => { live = false; };
  }, [wid, worldMid]);

  const save = async () => {
    setError(null);
    try {
      await api.setWorldModule(wid, value);
      setSaved(true);
      await load();
    } catch (e) {
      setError(String(e));
    }
  };

  const name = (mid: string) => mods.find((m) => m.id === mid)?.name ?? mid;

  return (
    <div className="side-section">
      <h4>Mechanics</h4>
      <label>
        Mechanics
        <select aria-label="Mechanics" value={value}
                onChange={(e) => { setValue(e.target.value); setSaved(false); }}>
          <option value="">None</option>
          {mods.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </label>
      <div className="field-hint">
        {value
          ? `Campaigns on this world default to ${name(value)}.`
          : "No default — campaigns choose their own mechanics."}
      </div>
      <label>
        Starting sheets for:
        <select aria-label="Starting sheets module" value={worldMid}
                onChange={(e) => onPickMid(e.target.value)}>
          <option value="">None</option>
          {mods.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </label>
      {coverage && Object.keys(coverage).length > 0 && (
        <div className="side-section">
          <h4>Sheets</h4>
          {Object.entries(coverage).map(([kind, c]) => (
            <div key={kind} className="field-hint">
              {KIND_LABELS[kind] ?? kind} {c.sheeted}/{c.total}
              {c.invalid > 0 ? ` · ${c.invalid} invalid` : ""}
            </div>
          ))}
        </div>
      )}
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
      {error && <div className="field-hint">{error}</div>}
    </div>
  );
}
