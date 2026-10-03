import { Field } from "../Field";
import { optionItems, type FieldDraft } from "./fieldLayer";

/** The form over one field definition, shared by the world and campaign editor
 *  and by the scene panel's "+ Scene-only field". It holds no state of its own:
 *  the owner holds the draft, so a keystroke here never reaches the rail. */
export function FieldForm({ draft, onChange, keyFixed, problem, saving, onSave, onCancel, saveLabel = "Save" }: {
  draft: FieldDraft;
  /** `keyTouched` says whether the reader typed the key themselves, so an owner
   *  can go on deriving it from the label until they do. */
  onChange: (next: FieldDraft, keyTouched?: boolean) => void;
  /** An existing field's key is its identity and cannot be edited. */
  keyFixed: boolean;
  /** Why Save is refused, from the same rules the server applies. */
  problem: string | null;
  saving: boolean;
  onSave: () => void;
  onCancel: () => void;
  saveLabel?: string;
}) {
  const set = (patch: Partial<FieldDraft>) => onChange({ ...draft, ...patch });
  return (
    <div className="form">
      <Field label="Label">
        <input type="text" value={draft.label}
               onChange={(e) => set({ label: e.target.value })} />
      </Field>
      <Field label="Key"
             hint={keyFixed ? "A field's key cannot be changed." : "Its identity in stored values. Taken from the label until you type one; it cannot change later."}>
        <input type="text" value={draft.key} readOnly={keyFixed}
               onChange={(e) => onChange({ ...draft, key: e.target.value }, true)} />
      </Field>
      <Field label="Hint" hint="Tells the model what belongs in this field and when to change it.">
        <textarea rows={3} value={draft.hint}
                  onChange={(e) => set({ hint: e.target.value })} />
      </Field>
      <Field label="Type">
        <select value={draft.type}
                onChange={(e) => set({ type: e.target.value as FieldDraft["type"] })}>
          <option value="text">Text</option>
          <option value="list">List</option>
          <option value="enum">Enum (one of a fixed set)</option>
        </select>
      </Field>
      {draft.type === "enum" && (
        // One input per option, as a list value is edited (`TrackerEditForm`):
        // an option may carry a comma, so no comma list could hold it.
        <div className="field">
          <div role="group" aria-label="Options" className="tracker-list">
            <span className="tracker-label">Options</span>
            {draft.options.map((o, i) => (
              <span key={o.id} className="tracker-list-item">
                <input type="text" aria-label={`Option ${i + 1}`} value={o.text}
                       onChange={(e) => set({ options: draft.options.map((p) =>
                         (p.id === o.id ? { ...p, text: e.target.value } : p)) })} />
                <button type="button" className="subtle" aria-label={`Remove option ${i + 1}`}
                        onClick={() => set({ options: draft.options.filter((p) => p.id !== o.id) })}>
                  ×
                </button>
              </span>
            ))}
            <button type="button" className="subtle" aria-label="Add option"
                    onClick={() => set({ options: [...draft.options, ...optionItems([""])] })}>
              + Add option
            </button>
          </div>
          <div className="field-hint">One per box. Blank ones are dropped.</div>
        </div>
      )}
      <Field label="Default awareness"
             hint="Who knows a value unless told otherwise: everyone present, or only the character it belongs to.">
        <select value={draft.aware}
                onChange={(e) => set({ aware: e.target.value as FieldDraft["aware"] })}>
          <option value="present">Everyone present</option>
          <option value="self">The character only</option>
        </select>
      </Field>
      {/* Silent on a blank new form: Save is already off, and a rule recited
          at someone who has typed nothing is noise. */}
      {problem && (keyFixed || draft.label || draft.key) && (
        <div className="field-hint" role="status">{problem}</div>
      )}
      <div className="form-actions">
        <button className="subtle" onClick={onCancel} disabled={saving}>Cancel</button>
        <button className="primary" onClick={onSave} disabled={saving || problem !== null}>
          {saveLabel}
        </button>
      </div>
    </div>
  );
}
