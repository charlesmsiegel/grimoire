import { useEffect, useState } from "react";
import { api, type CampaignModule, type ModuleSummary } from "../api/client";

export default function MechanicsConfig({ cid }: { cid: string }) {
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [state, setState] = useState<CampaignModule | null>(null);
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api.getCampaignModule(cid).then((s) => {
      setState(s);
      setValue(s.setting);
    });

  useEffect(() => {
    api.listModules().then(setMods).catch(() => setMods([]));
    load().catch(() => setState(null));
  }, [cid]);

  const save = async () => {
    setError(null);
    try {
      await api.setCampaignModule(cid, value);
      setSaved(true);
      await load();
    } catch (e) {
      setError(String(e));
    }
  };

  const name = (mid: string | null) =>
    mods.find((m) => m.id === mid)?.name ?? mid ?? "";

  return (
    <div className="side-section">
      <h4>Mechanics</h4>
      <label>
        Mechanics
        <select aria-label="Mechanics" value={value}
                onChange={(e) => { setValue(e.target.value); setSaved(false); }}>
          <option value="">World default</option>
          <option value="none">None</option>
          {mods.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </label>
      {state && (
        <div className="field-hint">
          {state.resolved
            ? `Playing with ${name(state.resolved)}` +
              (state.source === "world" ? " (world default)" : "")
            : state.setting && state.setting !== "none"
            ? `Bound module "${state.setting}" is missing or invalid — resolving to no mechanics.`
            : "No mechanics — freeform play."}
        </div>
      )}
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
      {error && <div className="field-hint">{error}</div>}
    </div>
  );
}
