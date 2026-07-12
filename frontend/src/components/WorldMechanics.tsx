import { useEffect, useState } from "react";
import { api, type ModuleSummary } from "../api/client";

export default function WorldMechanics({ wid }: { wid: string }) {
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);

  const load = () =>
    api.getWorld(wid).then((w) => setValue(w.meta.module ?? ""));

  useEffect(() => {
    api.listModules().then(setMods).catch(() => setMods([]));
    load().catch(() => setValue(""));
  }, [wid]);

  const save = async () => {
    await api.setWorldModule(wid, value);
    setSaved(true);
    await load();
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
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
}
