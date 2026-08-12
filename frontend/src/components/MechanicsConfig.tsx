import { useEffect, useState } from "react";
import { api, type CampaignModule, type ModuleSummary, type SheetCoverage } from "../api/client";

const KIND_LABELS: Record<string, string> = {
  characters: "Characters", pcs: "PCs", locations: "Locations", lore: "Lore",
  items: "Items", groups: "Groups", creatures: "Creatures",
};

export default function MechanicsConfig({ cid, onChanged }: {
  cid: string;
  /** Fired once a save has landed AND been re-read, so a caller that gates UI
   *  on "does this campaign have mechanics" (the input bar's dice button) can
   *  refresh. Optional: every existing caller predates it.
   *
   *  Carries no campaign id on purpose. A save CAN settle after the reader has
   *  navigated away -- this component holds the `cid` and the callback from the
   *  render that started it -- so the receiver must not assume the event is
   *  about the campaign it is showing. Naming the saved campaign here would
   *  invite exactly that assumption; the one receiver instead re-reads whatever
   *  is currently on screen, which is right either way. */
  onChanged?: () => void;
}) {
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [state, setState] = useState<CampaignModule | null>(null);
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<SheetCoverage | null>(null);

  const load = () =>
    api.getCampaignModule(cid).then((s) => {
      setState(s);
      setValue(s.setting);
      if (s.resolved) {
        api.getCampaignSheets(cid).then((r) => setCoverage(r.coverage)).catch(() => setCoverage(null));
      } else {
        setCoverage(null);
      }
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
      // After `load`, not before: the caller reads the same endpoint, and
      // firing on the bare PUT would race its own re-read.
      onChanged?.();
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
