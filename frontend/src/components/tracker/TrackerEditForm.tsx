import { useState } from "react";
import type { TrackerActor, TrackerAware, TrackerEdits, TrackerField, TrackerRecord } from "../../api/client";
import { ErrorNote } from "../ErrorNote";
import { valueText } from "./TrackerValues";

/** One value as the form holds it. A list field is edited item by item in
 *  `items`, so an item carrying a comma ("cut, left arm") stays one item;
 *  `text` is what every other field edits, and how a list reads when shown. */
type Draft = { text: string; items: Item[]; aware: TrackerAware };

/** A list item and the id React keys its row by: an item has no identity of
 *  its own, and keyed by position, removing one would hand the next item's
 *  row (and its caret) to the one after it. */
type Item = { id: number; text: string };
let lastItemId = 0;
const item = (text: string): Item => ({ id: ++lastItemId, text });

/** What the server gives a value nobody has set yet (`merge._default_aware`):
 *  everyone for a "present" field, only its owner for a "self" one. */
const defaultAware = (f: TrackerField): TrackerAware => f.aware === "present" ? "present" : [];

/** The items a list draft saves: trimmed, the blank ones dropped. */
const cleanItems = (items: Item[]) => items.map((s) => s.text.trim()).filter(Boolean);

const sameItems = (a: string[], b: string[]) =>
  a.length === b.length && a.every((s, i) => s === b[i]);

/** Whether the draft holds a value at all -- who knows nothing is not a thing
 *  the server will record. */
const filled = (f: TrackerField, d: Draft) =>
  f.type === "list" ? cleanItems(d.items).length > 0 : d.text !== "";

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
export function TrackerEditForm({ record, only, onSave, onCancel }: {
  record: TrackerRecord;
  /** Limit the form to this one actor's ref. The other actors still count as
   *  "present" for the awareness choices, but none of their values is offered,
   *  so none can be sent. */
  only?: string;
  onSave: (edits: TrackerEdits) => Promise<void>;
  onCancel: () => void;
}) {
  const snapshot = record.snapshot ?? {};
  const everyone = orderedActors(snapshot);
  const actors = only === undefined ? everyone : everyone.filter(([ref]) => ref === only);
  const initial = (ref: string, f: TrackerField): Draft => {
    const v = snapshot[ref].fields[f.key];
    return { text: v ? valueText(v.value) : "",
             items: v && Array.isArray(v.value) ? v.value.map(item) : [],
             aware: v ? v.aware : defaultAware(f) };
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
        const moved = f.type === "list"
          ? !sameItems(cleanItems(now.items), cleanItems(was.items))
          : now.text !== was.text;
        if (moved) edit.value = f.type === "list" ? cleanItems(now.items) : now.text;
        if (filled(f, now) && !sameAware(now.aware, was.aware)) edit.aware = now.aware;
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
        const others = everyone.filter(([r, a]) => r !== ref && a.present).map(([r]) => r);
        return (
          <fieldset key={ref} className="tracker-actor">
            <legend>{nameOf(ref)}{actor.present ? "" : " (not present)"}</legend>
            {editableFields(record.fields, actor).map((f) => {
              if (f.off) {
                // Switched off: the server refuses an edit, so it is shown, not offered.
                return (
                  <div key={f.key} className="tracker-field">
                    <span className="tracker-label">{f.label}</span>
                    {": "}{drafts[`${ref}\u0000${f.key}`].text || "—"}
                    <span className="field-hint"> (switched off)</span>
                  </div>
                );
              }
              const id = `${ref}\u0000${f.key}`;
              const d = drafts[id];
              const label = `${nameOf(ref)} ${f.label}`;
              const aware = d.aware;
              const addable = others.filter((r) => aware === "present" || !aware.includes(r));
              const current = aware === "present" ? "present" : aware.length === 0 ? "private" : "list";
              const setItem = (at: number, text: string) =>
                set(id, { items: d.items.map((s) => (s.id === at ? { ...s, text } : s)) });
              return (
                <div key={f.key} className="tracker-field">
                  {f.type === "list" ? (
                    <div role="group" aria-label={label} className="tracker-list">
                      <span className="tracker-label">{f.label}</span>
                      {d.items.map((it, i) => (
                        <span key={it.id} className="tracker-list-item">
                          <input aria-label={`${label} item ${i + 1}`} value={it.text}
                                 onChange={(e) => setItem(it.id, e.target.value)} />
                          <button type="button" className="subtle"
                                  aria-label={`Remove ${label} item ${i + 1}`}
                                  onClick={() => set(id, { items: d.items.filter((s) => s.id !== it.id) })}>
                            ×
                          </button>
                        </span>
                      ))}
                      <button type="button" className="subtle" aria-label={`Add ${label} item`}
                              onClick={() => set(id, { items: [...d.items, item("")] })}>
                        + Add item
                      </button>
                    </div>
                  ) : (
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
                               onChange={(e) => set(id, { text: e.target.value })} />
                      )}
                    </label>
                  )}
                  <select aria-label={`${label} awareness`} value={current} disabled={!filled(f, d)}
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
