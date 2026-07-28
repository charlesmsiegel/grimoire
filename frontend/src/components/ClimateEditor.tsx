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

/** An entry's share of its table, as a percentage.
 *
 *  Weights are relative, so `×1` and `×4` alone do not tell an author they
 *  configured 20% and 80% — the number they actually care about. Zero-weight
 *  rows are excluded from the total, matching how the draw skips them. */
function share(entries: ClimateEntry[], entry: ClimateEntry) {
  const total = entries.reduce((sum, e) => sum + (e.weight > 0 ? e.weight : 0), 0);
  if (!total || entry.weight <= 0) return null;
  return `${Math.round((entry.weight / total) * 1000) / 10}%`;
}

/** The conditions a given temperature band can actually draw. */
function eligible(conditions: ClimateEntry[], temp: string) {
  return conditions.filter((c) => !c.requires_temp || c.requires_temp.includes(temp));
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
    if (!id && climates.some((c) => c.id === target)) {
      // Every registry entry, not just custom ones: entering a shipped
      // preset's id here would submit the blank form under that id and create
      // a custom document shadowing the preset. Copy-on-write belongs to the
      // explicit Edit flow, where the form is seeded from the preset.
      setError(`A climate with the id '${target}' already exists. Open it to edit, or choose another id.`);
      return;
    }
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
    let message = reverting
      ? `Discard your changes to '${form.name}' and go back to the shipped preset?`
      : `Delete climate '${form.name}'?`;
    if (!reverting) {
      // Deleting a custom-only climate used as a *campaign default* silently
      // moves every untagged location in that campaign to the fallback — the
      // widest effect, and the one a locations-only warning never mentions.
      let refs;
      try {
        refs = await api.climateReferrers(id);
      } catch {
        // Fail closed. Treating a failed lookup as an empty one would tell the
        // user "Nothing is using it" and still delete — presenting an unknown
        // impact as no impact, which is the one thing this warning exists to
        // prevent.
        setError("Could not check what is using this climate. Not deleting — try again.");
        return;
      }
      const campaigns = refs.campaigns ?? [];
      const locations = refs.locations ?? [];
      if (campaigns.length) {
        message += `\n\nIt is the default climate for: ${campaigns.map((c) => c.name).join(", ")}.`
          + " Every location there that doesn't name its own climate falls back.";
      }
      if (locations.length) {
        message += `\n\n${locations.length} location(s) name it directly: `
          + `${locations.slice(0, 5).map((l) => l.name).join(", ")}`
          + `${locations.length > 5 ? ", …" : ""}.`;
      }
      if (!campaigns.length && !locations.length) message += " Nothing is using it.";
    }
    const ok = window.confirm(message);
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
      [axis]: form.seasons[n][axis].map((x, i) => {
        if (i !== e) return x;
        const next = { ...x, ...patch } as ClimateEntry;
        // The validator rejects an empty requires_temp and wants the key
        // omitted for an unconstrained condition, so clearing the field has to
        // delete the property — otherwise Save fails and the constraint can
        // never be removed.
        if (Array.isArray(next.requires_temp) && next.requires_temp.length === 0) {
          delete next.requires_temp;
        }
        return next;
      }),
    } as Partial<ClimateSeason>);
  }

  /** Rename a temperature and rewrite every condition that requires it.
   *
   *  One state update, not two. Renaming and patching separately would each
   *  read the same captured `form`, so the second would overwrite the first
   *  and the rename would appear to do nothing.
   *
   *  Without the rewrite a plain rename leaves `requires_temp` pointing at a
   *  name that no longer exists, the next save is rejected as a dangling
   *  requirement, and the author has to hunt down each dependent condition. */
  function renameTemperature(n: number, e: number, from: string, to: string) {
    setForm({
      ...form,
      seasons: form.seasons.map((s, i) => (i !== n ? s : {
        ...s,
        temperature: s.temperature.map((x, j) => (j === e ? { ...x, name: to } : x)),
        conditions: s.conditions.map((c) => (
          c.requires_temp?.includes(from)
            ? { ...c, requires_temp: c.requires_temp.map((r) => (r === from ? to : r)) }
            : c)),
      })),
    });
  }

  function addEntry(n: number, axis: typeof AXES[number]["key"]) {
    patchSeason(n, { [axis]: [...form.seasons[n][axis], { name: "", weight: 1 }] } as Partial<ClimateSeason>);
  }

  function removeEntry(n: number, axis: typeof AXES[number]["key"], e: number) {
    const season = form.seasons[n];
    if (axis !== "temperature") {
      patchSeason(n, { [axis]: season[axis].filter((_, i) => i !== e) } as Partial<ClimateSeason>);
      return;
    }
    // Removing a temperature drops it from every condition that required it,
    // in the same update. Left behind, the reference is a dangling requirement
    // the backend rejects, so the remove button could not complete a normal
    // deletion without the author hunting down each dependent condition. A
    // condition left requiring nothing becomes unconstrained — the validator
    // wants the key absent rather than an empty array.
    const gone = season.temperature[e]?.name;
    setForm({
      ...form,
      seasons: form.seasons.map((s, i) => (i !== n ? s : {
        ...s,
        temperature: s.temperature.filter((_, j) => j !== e),
        conditions: s.conditions.map((c) => {
          if (!c.requires_temp?.includes(gone)) return c;
          const kept = c.requires_temp.filter((r) => r !== gone);
          const next = { ...c } as ClimateEntry;
          if (kept.length) next.requires_temp = kept;
          else delete next.requires_temp;
          return next;
        }),
      })),
    });
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
                            {share(s[axis.key], e) ? ` · ${share(s[axis.key], e)}` : ""}
                            {e.requires_temp?.length ? ` (${e.requires_temp.join(", ")} only)` : ""}
                          </span>
                        ))}
                      </div>
                    ))}
                    {s.conditions.some((c) => c.requires_temp?.length) && (
                      <div className="field-hint">
                        {/* A constraint changes the eligible total, so a
                            condition's headline share is not what it draws at
                            in any particular band. */}
                        Per band: {s.temperature.filter((tp) => tp.weight > 0).map((tp) => (
                          `${tp.name} — ` + eligible(s.conditions, tp.name)
                            .filter((c) => c.weight > 0)
                            .map((c) => `${c.name} ${share(eligible(s.conditions, tp.name), c)}`)
                            .join(", ")
                        )).join("; ")}
                      </div>
                    )}
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
                               onChange={(ev) => {
                                 if (axis.key === "temperature" && e.name) {
                                   renameTemperature(n, i, e.name, ev.target.value);
                                 } else {
                                   patchEntry(n, axis.key, i, { name: ev.target.value });
                                 }
                               }} />
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
