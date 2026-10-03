import { useState } from "react";
import type { TrackerActor, TrackerAware, TrackerEdits, TrackerField, TrackerRecord } from "../../api/client";
import { ErrorNote } from "../ErrorNote";
import { valueText } from "./TrackerValues";

type Draft = { text: string; aware: TrackerAware };

const sameAware = (a: TrackerAware, b: TrackerAware) =>
  a === b || (Array.isArray(a) && Array.isArray(b)
    && a.length === b.length && a.every((r, i) => r === b[i]));

/** Present actors first, then absent ones, each in snapshot order. */
function orderedActors(snapshot: Record<string, TrackerActor>): [string, TrackerActor][] {
  const all = Object.entries(snapshot);
  return [...all.filter(([, a]) => a.present), ...all.filter(([, a]) => !a.present)];
}

/** A switched-off field is offered only where a value is already stored. */
function editableFields(fields: TrackerField[], actor: TrackerActor): TrackerField[] {
  return fields.filter((f) => !f.off || actor.fields[f.key] !== undefined);
}

/** The form over one record's values. It submits only what moved: a value
 *  that reads as it did, with the awareness it had, is not an edit, and sending
 *  it would stamp the user's hand on a value the user never touched. */
export function TrackerEditForm({ record, onSave, onCancel }: {
  record: TrackerRecord;
  onSave: (edits: TrackerEdits) => Promise<void>;
  onCancel: () => void;
}) {
  const snapshot = record.snapshot ?? {};
  const actors = orderedActors(snapshot);
  const initial = (ref: string, f: TrackerField): Draft => {
    const v = snapshot[ref].fields[f.key];
    return { text: v ? valueText(v.value) : "", aware: v ? v.aware : "present" };
  };
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() => {
    const out: Record<string, Draft> = {};
    for (const [ref, actor] of actors)
      for (const f of editableFields(record.fields, actor)) out[`${ref}\u0000${f.key}`] = initial(ref, f);
    return out;
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const set = (id: string, patch: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  const nameOf = (ref: string) => record.names[ref] ?? ref;

  async function save() {
    const edits: TrackerEdits = {};
    for (const [ref, actor] of actors) {
      for (const f of editableFields(record.fields, actor)) {
        const was = initial(ref, f);
        const now = drafts[`${ref}\u0000${f.key}`];
        const edit: { value?: string | string[]; aware?: TrackerAware } = {};
        if (now.text !== was.text)
          edit.value = f.type === "list"
            ? now.text.split(",").map((s) => s.trim()).filter(Boolean)
            : now.text;
        if (!sameAware(now.aware, was.aware)) edit.aware = now.aware;
        if (edit.value !== undefined || edit.aware !== undefined)
          (edits[ref] ??= {})[f.key] = edit;
      }
    }
    if (Object.keys(edits).length === 0) { onCancel(); return; }
    setSaving(true); setError(null);
    try { await onSave(edits); }
    catch (err) { setError(err); setSaving(false); }
  }

  return (
    <form className="tracker-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {actors.map(([ref, actor]) => {
        const others = actors.filter(([r, a]) => r !== ref && a.present).map(([r]) => r);
        return (
          <fieldset key={ref} className="tracker-actor">
            <legend>{nameOf(ref)}{actor.present ? "" : " (not present)"}</legend>
            {editableFields(record.fields, actor).map((f) => {
              const id = `${ref}\u0000${f.key}`;
              const d = drafts[id];
              const label = `${nameOf(ref)} ${f.label}`;
              const aware = d.aware;
              const addable = others.filter((r) => aware === "present" || !aware.includes(r));
              const current = aware === "present" ? "present" : aware.length === 0 ? "private" : "list";
              return (
                <div key={f.key} className="tracker-field">
                  <label>
                    <span className="tracker-label">{f.label}</span>
                    {f.type === "enum" ? (
                      <select aria-label={label} value={d.text}
                              onChange={(e) => set(id, { text: e.target.value })}>
                        <option value=""></option>
                        {[...new Set([...(f.options ?? []), ...(d.text ? [d.text] : [])])].map((o) =>
                          <option key={o} value={o}>{o}</option>)}
                      </select>
                    ) : (
                      <input aria-label={label} value={d.text}
                             placeholder={f.type === "list" ? "comma, separated" : undefined}
                             onChange={(e) => set(id, { text: e.target.value })} />
                    )}
                  </label>
                  <select aria-label={`${label} awareness`} value={current}
                          onChange={(e) => {
                            const v = e.target.value;
                            if (v === "present") set(id, { aware: "present" });
                            else if (v === "private") set(id, { aware: [] });
                            else if (v.startsWith("add:"))
                              set(id, { aware: [...(aware === "present" ? [] : aware), v.slice(4)] });
                          }}>
                    <option value="present">Everyone present</option>
                    <option value="private">Private</option>
                    {current === "list" && Array.isArray(aware) && (
                      <option value="list">Known to: {aware.map(nameOf).join(", ")}</option>
                    )}
                    {addable.map((r) => <option key={r} value={`add:${r}`}>Add {nameOf(r)}</option>)}
                  </select>
                </div>
              );
            })}
          </fieldset>
        );
      })}
      {error !== null && <p role="alert" className="field-hint"><ErrorNote err={error} /></p>}
      <div className="form-actions">
        <button type="button" className="subtle" onClick={onCancel} disabled={saving}>Cancel</button>
        <button type="submit" className="primary" disabled={saving}>Save</button>
      </div>
    </form>
  );
}
