import { useEffect, useRef, useState, type ReactNode } from "react";

/** One card field, read-only until you ask to change it.
 *
 *  The character page has no edit MODE. It used to: a separate screen of
 *  fifteen stacked fields at a fixed 640px, four of them three-row scroll boxes
 *  over multi-paragraph prose, and one Save at the bottom of all of it. What
 *  replaced it is this — the page you read is the page you edit, one block at a
 *  time, with the control that changes a field next to the field it changes.
 *
 *  Three properties the old form did not have:
 *
 *  - **The textarea grows with the text.** A fixed `rows` is a peephole onto
 *    anything longer than it, and a description is the longest thing on a card.
 *  - **Save is where you are looking**, not at the bottom of a screen you have
 *    to leave the field to reach.
 *  - **Cancel restores what was there**, because the draft lives here and the
 *    card only hears about it on save. An abandoned edit costs nothing.
 *
 *  The keys are a local `onKeyDown` on the textarea rather than a registry
 *  binding, which is what `EditableRow` does too: `src/shortcuts` owns keys the
 *  APP answers, and eslint's ban is on `addEventListener("keydown")`. A key
 *  that only means something while the caret is inside this one control is not
 *  an app binding, and registering it as one would make it a binding every
 *  other screen has to be held off from.
 */
export function EditableField(
  { label, value, onSave, multiline = true, hint, stamp, rendered, disabled = false,
    editing, onEditingChange, placeholder }: {
    label: string;
    value: string;
    /** Rejections surface through the page's banner — this only has to say
     *  whether the field can close. Resolving false keeps the draft up. */
    onSave: (next: string) => Promise<boolean> | boolean;
    multiline?: boolean;
    hint?: string;
    /** Rendered beside the label — the description's token stamp. */
    stamp?: ReactNode;
    /** How the value reads when it is not being edited: markdown, HTML, a chip
     *  row. Plain text when absent. */
    rendered?: ReactNode;
    disabled?: boolean;
    /** Open state, lifted so the page can hold ONE field open at a time. A
     *  second field open at once is how a save built from a stale card
     *  clobbers a sibling that was saved a moment earlier. */
    editing: boolean;
    onEditingChange: (open: boolean) => void;
    placeholder?: string;
  },
) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);

  // The draft is seeded from the value at the moment the field OPENS, not on
  // every value change: a re-read landing mid-edit (the page reloads the
  // character after any write) would otherwise wipe what is being typed.
  useEffect(() => {
    if (editing) setDraft(value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  // Grow to fit. Run on open as well as on input, so a long stored value opens
  // at its full height rather than at the one row the element defaults to.
  useEffect(() => {
    const el = area.current;
    if (!el || !editing) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft, editing]);

  // Focused on open, imperatively rather than with `autoFocus`. The attribute
  // is the accessibility problem the lint names — it steals focus on PAGE LOAD,
  // before a reader has asked for anything. This fires only when a reader has
  // just clicked Edit on this field, which is a request for the caret.
  useEffect(() => { if (editing) area.current?.focus(); }, [editing]);

  async function save() {
    if (saving) return;
    setSaving(true);
    try {
      if (await onSave(draft)) onEditingChange(false);
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    const empty = !value.trim();
    return (
      <div className="card-field">
        <div className="card-field-head">
          <span className="data-label">{label}</span>
          {stamp}
          <button className="field-edit" type="button" disabled={disabled}
                  onClick={() => onEditingChange(true)}>
            {empty ? "Add" : "Edit"}
          </button>
        </div>
        {empty
          ? <p className="field-hint field-empty">{placeholder ?? "Not set."}</p>
          : <div className="card-field-body">{rendered ?? <div className="detail-text">{value}</div>}</div>}
      </div>
    );
  }

  return (
    <div className="card-field editing">
      <div className="card-field-head">
        <span className="data-label">{label}</span>
        {stamp}
      </div>
      {hint && <p className="field-hint">{hint}</p>}
      <textarea
        ref={area}
        className={"field-draft" + (multiline ? "" : " one-line")}
        aria-label={label}
        value={draft}
        rows={1}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.stopPropagation(); onEditingChange(false); }
          // Enter is a newline in prose, so saving takes the modifier. A
          // single-line field has no newline to protect and takes it bare.
          else if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            void save();
          }
        }}
      />
      <div className="field-actions">
        <button className="primary" type="button" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button className="subtle" type="button" disabled={saving}
                onClick={() => onEditingChange(false)}>Cancel</button>
      </div>
    </div>
  );
}

export default EditableField;
