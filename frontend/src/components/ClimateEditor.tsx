import { useCallback, useEffect, useState } from "react";
import { api, type Climate, type ClimateEntry, type ClimateSeason, type ClimateSummary } from "../api/client";
import { Field } from "./Field";

const BLANK_SEASON: ClimateSeason = {
  name: "all year", from: 0, to: 0,
  temperature: [{ name: "mild", weight: 1 }],
  conditions: [{ name: "clear", weight: 1 }],
  wind: [{ name: "calm", weight: 1 }],
};
const BLANK: Climate = { id: "", name: "", persistence: 0.5, seasons: [BLANK_SEASON] };

const AXES = [
  { key: "temperature" as const, label: "Temperature" },
  { key: "conditions" as const, label: "Conditions" },
  { key: "wind" as const, label: "Wind" },
];

/** Percent of the year, for display. Seasons are stored as fractions so a
 *  preset drops into a calendar of any length; showing raw 0.92 helps nobody. */
function pct(fraction: number) {
  return `${Math.round(fraction * 1000) / 10}%`;
}

export function ClimateEditor() {
  const [climates, setClimates] = useState<ClimateSummary[]>([]);
  const [id, setId] = useState<string | null>(null); // null = new
  const [form, setForm] = useState<Climate>(BLANK);
  const [flags, setFlags] = useState({ builtin: false, custom: false });
  const [mode, setMode] = useState<"view" | "edit">("edit");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(
    () => api.listClimates().then((r) => setClimates(r.climates)), []);
  useEffect(() => { reload(); }, [reload]);

  function resetForm() {
    setId(null);
    setForm(BLANK);
    setFlags({ builtin: false, custom: false });
    setError(null);
    setMode("edit"); // a brand-new climate goes straight to the form
  }

  async function select(next: string) {
    setError(null);
    const got = await api.readClimate(next);
    setId(next);
    setForm(got.climate);
    setFlags({ builtin: got.builtin, custom: got.custom });
    setMode("view"); // existing climates are read-only until Edit
  }

  async function save() {
    const target = (id ?? form.id).trim();
    if (!target || !form.name.trim()) return;
    setError(null);
    try {
      await api.saveClimate(target, { ...form, id: target });
      await reload();
      await select(target);
    } catch (err: any) {
      // The resolver is lenient so bad data cannot take a turn down, which
      // makes this the only place a mistake is ever reported.
      setError(err.detail ?? String(err));
    }
  }

  async function revertOrDelete() {
    if (!id || !flags.custom) return;
    const reverting = flags.builtin;
    const ok = window.confirm(reverting
      ? `Discard your changes to '${form.name}' and go back to the shipped preset?`
      : `Delete climate '${form.name}'? Locations using it fall back to the campaign default.`);
    if (!ok) return;
    const got = await api.deleteClimate(id);
    await reload();
    if (got.reverted_to_preset) await select(id);
    else resetForm();
  }

  function patchSeason(n: number, patch: Partial<ClimateSeason>) {
    setForm({ ...form, seasons: form.seasons.map((s, i) => (i === n ? { ...s, ...patch } : s)) });
  }

  function patchEntry(n: number, axis: typeof AXES[number]["key"], e: number, patch: Partial<ClimateEntry>) {
    patchSeason(n, {
      [axis]: form.seasons[n][axis].map((x, i) => (i === e ? { ...x, ...patch } : x)),
    } as Partial<ClimateSeason>);
  }

  function addEntry(n: number, axis: typeof AXES[number]["key"]) {
    patchSeason(n, { [axis]: [...form.seasons[n][axis], { name: "", weight: 1 }] } as Partial<ClimateSeason>);
  }

  function removeEntry(n: number, axis: typeof AXES[number]["key"], e: number) {
    patchSeason(n, { [axis]: form.seasons[n][axis].filter((_, i) => i !== e) } as Partial<ClimateSeason>);
  }

  function addSeason() {
    setForm({ ...form, seasons: [...form.seasons, { ...BLANK_SEASON, name: "new season" }] });
  }

  function removeSeason(n: number) {
    setForm({ ...form, seasons: form.seasons.filter((_, i) => i !== n) });
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New climate</button>
        {climates.map((c) => (
          <button key={c.id} className={"row" + (c.id === id ? " on" : "")}
                  onClick={() => select(c.id)}>
            {c.name}
            {c.custom && <span className="chip on">custom</span>}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" && id ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{form.name}</h3>
              <div className="detail-rendered">
                {form.seasons.map((s, n) => (
                  <div key={n} className="side-section">
                    <h4>{s.name} — {s.from === s.to ? "the whole year" : `${pct(s.from)} to ${pct(s.to)}`}</h4>
                    {AXES.map((axis) => (
                      <div key={axis.key} className="chips">
                        <span className="field-hint">{axis.label}:</span>
                        {s[axis.key].map((e, i) => (
                          <span key={i} className={"chip" + (e.weight > 0 ? " on" : "")}>
                            {e.name} ×{e.weight}
                            {e.requires_temp?.length ? ` (${e.requires_temp.join(", ")} only)` : ""}
                          </span>
                        ))}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              <div className="side-section">
                <h4>Persistence</h4>
                <span className="chip on">{form.persistence}</span>
                <div className="field-hint">
                  How alike two neighbouring times of day are. 0 makes every block
                  independent; 0.9 gives long settled runs.
                </div>
              </div>
              <div className="side-section">
                <h4>Source</h4>
                <div className="chips">
                  {flags.builtin && <span className="chip on">shipped preset</span>}
                  {flags.custom && <span className="chip on">your copy</span>}
                </div>
                {flags.builtin && !flags.custom && (
                  <div className="field-hint">
                    Editing this makes your own copy — the original is never changed.
                  </div>
                )}
                {flags.custom && (
                  <div className="form-actions">
                    <button className="subtle" onClick={revertOrDelete}>
                      {/* Both flags means a custom copy shadows a preset, so the
                          undo is a revert; custom alone means the id disappears. */}
                      {flags.builtin ? "Revert to preset" : "Delete"}
                    </button>
                  </div>
                )}
              </div>
            </aside>
          </div>
        ) : (
          <div className="form">
            {!id && (
              <Field label="Id" hint="Letters, digits, dots, dashes and underscores.">
                <input value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} />
              </Field>
            )}
            <Field label="Name">
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Persistence"
                   hint="0 to 1. How alike two neighbouring times of day are — the lag-1 correlation between blocks.">
              <input type="number" min={0} max={1} step={0.05} value={form.persistence}
                     onChange={(e) => setForm({ ...form, persistence: Number(e.target.value) })} />
            </Field>

            {form.seasons.map((s, n) => (
              <div key={n} className="side-section">
                <h4>Season {n + 1}</h4>
                <Field label="Season name">
                  <input value={s.name} onChange={(e) => patchSeason(n, { name: e.target.value })} />
                </Field>
                <Field label="From"
                       hint="Fraction of the year, 0 to 1 — not a date, so the climate fits any calendar. Equal from and to means the whole year.">
                  <input type="number" min={0} max={0.999} step={0.01} value={s.from}
                         onChange={(e) => patchSeason(n, { from: Number(e.target.value) })} />
                </Field>
                <Field label="To" hint="Wraps the year end when it is less than From.">
                  <input type="number" min={0} max={0.999} step={0.01} value={s.to}
                         onChange={(e) => patchSeason(n, { to: Number(e.target.value) })} />
                </Field>

                {AXES.map((axis) => (
                  <div key={axis.key} className="side-section">
                    <h4>{axis.label}</h4>
                    {s[axis.key].map((e, i) => (
                      <div key={i} className="chips">
                        <input aria-label={`${axis.label} name ${i + 1}`} value={e.name}
                               onChange={(ev) => patchEntry(n, axis.key, i, { name: ev.target.value })} />
                        <input aria-label={`${axis.label} weight ${i + 1}`} type="number" min={0} step={1}
                               value={e.weight}
                               onChange={(ev) => patchEntry(n, axis.key, i, { weight: Number(ev.target.value) })} />
                        {axis.key === "conditions" && (
                          <input aria-label={`${axis.label} requires ${i + 1}`}
                                 placeholder="only when (comma-separated)"
                                 value={(e.requires_temp ?? []).join(", ")}
                                 onChange={(ev) => patchEntry(n, axis.key, i, {
                                   requires_temp: ev.target.value.split(",").map((t) => t.trim()).filter(Boolean),
                                 })} />
                        )}
                        <button className="chip" onClick={() => removeEntry(n, axis.key, i)}>✕</button>
                      </div>
                    ))}
                    <button className="chip" onClick={() => addEntry(n, axis.key)}>+ add</button>
                  </div>
                ))}
                {form.seasons.length > 1 && (
                  <button className="chip" onClick={() => removeSeason(n)}>Remove season</button>
                )}
              </div>
            ))}
            <button className="chip" onClick={addSeason}>+ add season</button>

            <div className="form-actions">
              <button className="primary" onClick={save}>Save</button>
              {id && <button className="subtle" onClick={() => select(id)}>Cancel</button>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
