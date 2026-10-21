import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { errorText } from "../api/errors";
import { api, type Climate, type ClimateEntry, type ClimateSeason, type ClimateSummary } from "../api/client";
import { Field } from "./Field";
import { useCreationRequest } from "./useCreationRequest";
import {
  AXES, BLANK, addEntry, addSeason, patchEntry, patchSeason, pct, perBandShares, referrerNotice,
  removeEntry, removeSeason, renameTemperature, share, type ClimateAxis,
} from "./climateForm";

/** Where a record came from: a shipped preset, the reader's own copy, or both
 *  (a custom copy shadowing a preset). */
type ClimateFlags = { builtin: boolean; custom: boolean };

/** One form edit: a function from the current draft to the next. */
type FormEdit = (next: (form: Climate) => Climate) => void;

export function ClimateEditor() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const creating = params.get("new") === "1";
  const [climates, setClimates] = useState<ClimateSummary[]>([]);
  const [id, setId] = useState<string | null>(null); // null = new
  const [form, setForm] = useState<Climate>(BLANK);
  const [flags, setFlags] = useState<ClimateFlags>({ builtin: false, custom: false });
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [error, setError] = useState<string | null>(null);
  const { busy: saving, begin: beginSave, retire: retireSave } = useCreationRequest(
    creating || selected ? "climate" : null);

  const reload = useCallback(
    () => api.listClimates().then((r) => setClimates(r.climates)), []);
  useEffect(() => { void reload(); }, [reload]);

  function resetForm() {
    setId(null);
    setForm(BLANK);
    setFlags({ builtin: false, custom: false });
    setError(null);
    setMode("edit"); // a brand-new climate goes straight to the form
  }

  // URL selection and Cancel share one read sequence: a late reload cannot
  // overwrite the record the reader moved to, or banner it with an old error.
  const selectionRead = useRef(0);
  async function select(next: string, current = () => true) {
    const request = ++selectionRead.current;
    try {
      setError(null);
      const got = await api.readClimate(next);
      if (!current() || request !== selectionRead.current) return;
      setId(next);
      setForm(got.climate);
      setFlags({ builtin: got.builtin, custom: got.custom });
      setMode("view"); // existing climates are read-only until Edit
    } catch (err: unknown) {
      if (request === selectionRead.current && current()) throw err;
    }
  }

  useEffect(() => {
    let live = true;
    resetForm();
    if (selected) {
      setMode("view");
      void select(selected, () => live).catch((err: unknown) => {
        if (live) setError(errorText(err));
      });
    }
    return () => { live = false; selectionRead.current += 1; };
    // The URL is the record identity; form edits must never re-read its draft.
  }, [selected, creating]);

  async function save() {
    const target = (id ?? form.id).trim();
    if (!target || !form.name.trim() || saving) return;
    setError(null);
    if (!id && climates.some((c) => c.id === target)) {
      // Every registry entry, not just custom ones: entering a shipped
      // preset's id here would submit the blank form under that id and create
      // a custom document shadowing the preset. Copy-on-write belongs to the
      // explicit Edit flow, where the form is seeded from the preset.
      setError(`A climate with the id '${target}' already exists. Open it to edit, or choose another id.`);
      return;
    }
    const request = beginSave();
    if (!request) return;
    try {
      await api.saveClimate(target, { ...form, id: target });
      await reload();
      if (!request.current()) return;
      await select(target, request.current);
      if (request.current()) setParams({ id: target });
    } catch (err: unknown) {
      // The resolver is lenient so bad data cannot take a turn down, which
      // makes this the only place a mistake is ever reported.
      if (request.current()) setError(errorText(err));
    } finally { request.finish(); }
  }

  async function revertOrDelete() {
    if (!id || !flags.custom) return;
    const reverting = flags.builtin;
    const question = reverting
      ? `Discard your changes to '${form.name}' and go back to the shipped preset?`
      : `Delete climate '${form.name}'?`;
    // Both paths change the sky, so both disclose. Deleting a custom-only
    // climate used as a *campaign default* silently moves every untagged
    // location in that campaign to the fallback; reverting swaps the tables
    // under everything using the id — and the widest case of all is a
    // customized *fallback* preset, which every campaign with no default of
    // its own resolves to (#237).
    let refs;
    try {
      refs = await api.climateReferrers(id);
    } catch {
      // Fail closed. Treating a failed lookup as an empty one would tell the
      // user "Nothing is using it" and still go ahead — presenting an unknown
      // impact as no impact, which is the one thing this warning exists to
      // prevent.
      setError("Could not check what is using this climate. "
        + `Not ${reverting ? "reverting" : "deleting"} — try again.`);
      return;
    }
    const ok = window.confirm(question + referrerNotice(refs, reverting));
    if (!ok) return;
    const got = await api.deleteClimate(id);
    await reload();
    if (got.reverted_to_preset) await select(id);
    else { resetForm(); setParams({}); }
  }

  /** Back to the saved record. */
  function cancelEdit(saved: string) {
    // Reload only after the write settles, never the old bytes ahead of it.
    if (saving) return;
    retireSave(); void select(saved).catch((err: unknown) => setError(errorText(err)));
  }

  function editorBody() {
    if (!selected && !creating) {
      return <div className="editor-empty">Choose a climate to read, or create one.</div>;
    }
    if (selected && !id) return error ? null : <p className="field-hint">Loading climate...</p>;
    if (mode === "view" && id) {
      return <ClimateDetail form={form} flags={flags} onEdit={() => setMode("edit")}
                            onRevertOrDelete={() => void revertOrDelete()} />;
    }
    return <ClimateForm form={form} id={id} saving={saving}
                        edit={(next) => setForm(next(form))}
                        onSave={() => void save()}
                        onCancel={id ? () => cancelEdit(id) : null} />;
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <Link className="primary new" to="?new=1">+ New climate</Link>
        {climates.map((c) => (
          <Link key={c.id} className={"row" + (c.id === id ? " on" : "")}
                to={`?${new URLSearchParams({ id: c.id })}`}>
            {c.name}
            {c.custom && <span className="chip on">custom</span>}
          </Link>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {editorBody()}
      </div>
    </div>
  );
}

/** One season, read-only: its span of the year and each axis's table. */
function SeasonView({ s }: { s: ClimateSeason }) {
  return <>
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
        Per band: {perBandShares(s)}
      </div>
    )}
  </>;
}

/** The sidebar's Source section: where the record came from, and the undo
 *  that removes the reader's copy. */
function ClimateSource({ flags, onRevertOrDelete }: { flags: ClimateFlags; onRevertOrDelete: () => void }) {
  return (
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
          <button className="subtle" onClick={onRevertOrDelete}>
            {/* Both flags means a custom copy shadows a preset, so the
                undo is a revert; custom alone means the id disappears. */}
            {flags.builtin ? "Revert to preset" : "Delete"}
          </button>
        </div>
      )}
    </div>
  );
}

/** A saved climate, read-only, with Edit at the top of its sidebar. */
function ClimateDetail(
  { form, flags, onEdit, onRevertOrDelete }: {
    form: Climate;
    flags: ClimateFlags;
    onEdit: () => void;
    onRevertOrDelete: () => void;
  },
) {
  return (
    <div className="detail-view">
      <div className="detail-main">
        <h3>{form.name}</h3>
        <div className="detail-rendered">
          {form.seasons.map((s, n) => (
            <div key={n} className="side-section">
              <SeasonView s={s} />
            </div>
          ))}
        </div>
      </div>
      <aside className="detail-sidebar">
        <div className="form-actions">
          <button className="subtle" onClick={onEdit}>Edit</button>
        </div>
        <div className="side-section">
          <h4>Persistence</h4>
          <span className="chip on">{form.persistence}</span>
          <div className="field-hint">
            How alike two neighbouring times of day are. 0 makes every block
            independent; 0.9 gives long settled runs.
          </div>
        </div>
        <ClimateSource flags={flags} onRevertOrDelete={onRevertOrDelete} />
      </aside>
    </div>
  );
}

/** One row of an axis's table: name, weight, (for conditions) the
 *  temperatures it requires, and its remove button. */
function EntryFields(
  { n, axis, e, i, edit }: {
    n: number;
    axis: ClimateAxis;
    e: ClimateEntry;
    i: number;
    edit: FormEdit;
  },
) {
  return (
    <div className="climate-entry">
      <input aria-label={`${axis.label} name ${i + 1}`} value={e.name}
             onChange={(ev) => {
               if (axis.key === "temperature" && e.name) {
                 edit((f) => renameTemperature(f, n, i, e.name, ev.target.value));
               } else {
                 edit((f) => patchEntry(f, n, axis.key, i, { name: ev.target.value }));
               }
             }} />
      <input aria-label={`${axis.label} weight ${i + 1}`} type="number" min={0} step={1}
             value={e.weight}
             onChange={(ev) => edit((f) => patchEntry(f, n, axis.key, i, { weight: Number(ev.target.value) }))} />
      {axis.key === "conditions" && (
        <input aria-label={`${axis.label} requires ${i + 1}`}
               placeholder="only when (comma-separated)"
               value={(e.requires_temp ?? []).join(", ")}
               onChange={(ev) => edit((f) => patchEntry(f, n, axis.key, i, {
                 requires_temp: ev.target.value.split(",").map((t) => t.trim()).filter(Boolean),
               }))} />
      )}
      <button className="chip" aria-label={`Remove ${axis.label.toLowerCase()} ${e.name || i + 1} from season ${n + 1}`} onClick={() => edit((f) => removeEntry(f, n, axis.key, i))}>✕</button>
    </div>
  );
}

/** One season of the form: its name, its span of the year, and its tables. */
function SeasonFields({ form, s, n, edit }: { form: Climate; s: ClimateSeason; n: number; edit: FormEdit }) {
  return (
    <div className="side-section">
      <h4>Season {n + 1}</h4>
      <Field label="Season name">
        <input value={s.name} onChange={(e) => edit((f) => patchSeason(f, n, { name: e.target.value }))} />
      </Field>
      <Field label="From"
             hint="Fraction of the year, 0 to 1 — not a date, so the climate fits any calendar. Equal from and to means the whole year.">
        <input type="number" min={0} max={0.999} step={0.01} value={s.from}
               onChange={(e) => edit((f) => patchSeason(f, n, { from: Number(e.target.value) }))} />
      </Field>
      <Field label="To" hint="Wraps the year end when it is less than From.">
        <input type="number" min={0} max={0.999} step={0.01} value={s.to}
               onChange={(e) => edit((f) => patchSeason(f, n, { to: Number(e.target.value) }))} />
      </Field>

      {AXES.map((axis) => (
        <div key={axis.key} className="side-section">
          <h4>{axis.label}</h4>
          <div className="climate-entry-head" aria-hidden="true"><span>Name</span><span>Weight</span>
            {axis.key === "conditions" && <span>Only when</span>}
          </div>
          {s[axis.key].map((e, i) => (
            <EntryFields key={i} n={n} axis={axis} e={e} i={i} edit={edit} />
          ))}
          <button className="chip" onClick={() => edit((f) => addEntry(f, n, axis.key))}>+ add</button>
        </div>
      ))}
      {form.seasons.length > 1 && (
        <button className="chip" onClick={() => edit((f) => removeSeason(f, n))}>Remove season</button>
      )}
    </div>
  );
}

/** The form: a new climate (with its id) or an existing one being edited.
 *  `onCancel` is null for a new climate, whose Cancel is a link to the root. */
function ClimateForm(
  { form, id, saving, edit, onSave, onCancel }: {
    form: Climate;
    id: string | null;
    saving: boolean;
    edit: FormEdit;
    onSave: () => void;
    onCancel: (() => void) | null;
  },
) {
  return (
    <div className="form">
      {!id && (
        <Field label="Id" hint="Letters, digits, dots, dashes and underscores.">
          <input value={form.id} onChange={(e) => edit((f) => ({ ...f, id: e.target.value }))} />
        </Field>
      )}
      <Field label="Name">
        <input value={form.name} onChange={(e) => edit((f) => ({ ...f, name: e.target.value }))} />
      </Field>
      <Field label="Persistence"
             hint="0 to 1. How alike two neighbouring times of day are — the lag-1 correlation between blocks.">
        <input type="number" min={0} max={1} step={0.05} value={form.persistence}
               onChange={(e) => edit((f) => ({ ...f, persistence: Number(e.target.value) }))} />
      </Field>

      {form.seasons.map((s, n) => (
        <SeasonFields key={n} form={form} s={s} n={n} edit={edit} />
      ))}
      <button className="chip" onClick={() => edit(addSeason)}>+ add season</button>

      <div className="form-actions">
        <button className="primary" onClick={onSave} disabled={saving}>Save</button>
        {onCancel ? <button className="subtle" disabled={saving} onClick={onCancel}>Cancel</button>
            : <Link className="subtle" to="?">Cancel</Link>}
      </div>
    </div>
  );
}
