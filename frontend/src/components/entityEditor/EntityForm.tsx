import { useMemo, type Dispatch, type SetStateAction, type ReactNode } from "react";
import { ACTIVATION_FIELDS, SECRECY_LABELS, SECRECY_LEVELS, type EntityKind, type Secrecy } from "../../api/client";
import type { RecordRef } from "../../api/loreOwners";
import { Field } from "../Field";
import { activationSpec, isKeepOn } from "./fields";
import { KIND_LABELS, SECRECY_HINTS } from "./presentation";

export type EntityDraft = {
  readonly name: string; readonly body: string; readonly keys: string; readonly secrecy: Secrecy;
  readonly fields: Readonly<Record<string, string>>; readonly owners: readonly string[];
};
export function EntityForm({ kind, editing, draft, setName, setBody, setKeys, setSecrecy, setOwners,
  setField, activationOpen, setActivationOpen, ownerOpts, fieldControls, renderActivation, groupNeedsReload,
  onDelete, onCancel, onSave }: {
  kind: EntityKind; editing: string | null; draft: EntityDraft;
  setName: (value: string) => void; setBody: (value: string) => void; setKeys: (value: string) => void;
  setSecrecy: (value: Secrecy) => void; setOwners: Dispatch<SetStateAction<string[]>>;
  setField: (key: string, value: string) => void; activationOpen: boolean;
  setActivationOpen: (open: boolean) => void; ownerOpts: RecordRef[];
  fieldControls: ReactNode; renderActivation: (spec: ReturnType<typeof activationSpec>) => ReactNode;
  groupNeedsReload: boolean; onDelete: () => void; onCancel: () => void; onSave: () => void;
}) {
  const { name, body, keys, secrecy, fields, owners } = draft;
  const label = KIND_LABELS[kind];
  // The owner picker: a checkbox per character, PC and location the world
  // holds, so as long as the rail in a large world -- and it sits in the same
  // form as the body, so it was rebuilt for every keystroke there too. The
  // updater reads the owners as they are when the box is clicked, not as this
  // memo last saw them.
  const ownerPicker = useMemo(() => ownerOpts.map((o) => (
    <label key={o.ref} className="owner-option">
      <input
        type="checkbox"
        aria-label={o.label}
        checked={owners.includes(o.ref)}
        onChange={(e) => {
          const on = e.target.checked;
          setOwners((cur) => (on ? [...cur, o.ref] : cur.filter((r) => r !== o.ref)));
        }}
      />
      {o.label}
    </label>
  )), [ownerOpts, owners, setOwners]);

  return (
    <div className="form">
      <h3>{editing ? `Edit ${label}` : `New ${label}`}</h3>
      <Field label="Name">
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Body">
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={10} />
      </Field>
      <Field label="Keys" hint="comma-separated activation triggers; blank = always-on">
        <input type="text" value={keys} onChange={(e) => setKeys(e.target.value)} />
      </Field>
      {/* Real radio inputs, like the Owners checkboxes above: a native
          group gets arrow-key navigation and roving focus for free,
          which a row of buttons wearing role="radio" does not. */}
      <Field label="Secrecy" hint={SECRECY_HINTS[secrecy]}>
        <div className="secrecy-picker" role="radiogroup" aria-label="Secrecy">
          {SECRECY_LEVELS.map((level) => (
            <label key={level} className="secrecy-option">
              <input type="radio" name="secrecy" value={level}
                     checked={secrecy === level}
                     onChange={() => setSecrecy(level)} />
              {SECRECY_LABELS[level]}
            </label>
          ))}
        </div>
      </Field>
      {fieldControls}
      {/* One disclosure for every kind, closed unless the record already
          sets something in it: most entries use none of these, and nine
          controls under every body would bury the four that matter. The
          summary toggles state rather than the native `open`, so the
          contents can be left undrawn while it is shut. */}
      <details className="activation-settings" open={activationOpen}>
        <summary onClick={(e) => { e.preventDefault(); setActivationOpen(!activationOpen); }}>
          Activation
        </summary>
        {activationOpen && ACTIVATION_FIELDS.map((a) => (
          a.widget === "bool" ? (
            <Field key={a.key} label={a.label}
                   hint="keep this entry when the world-info budget has to drop others">
              <input type="checkbox" checked={isKeepOn(fields[a.key])}
                     onChange={(e) => setField(a.key, e.target.checked ? "true" : "")} />
            </Field>
          ) : renderActivation(activationSpec(a))
        ))}
      </details>
      {kind === "lore" && (
        <Field label="Owners" hint="lore activates only when an owner is in the scene; none = world-level">
          <div className="chips owner-picker">
            {ownerPicker}
            {ownerOpts.length === 0 && <span className="field-hint">Nothing to own it yet.</span>}
          </div>
        </Field>
      )}
      <div className="form-actions">
        {editing && <button className="subtle" onClick={onDelete}>Delete</button>}
        {editing && <button className="subtle" onClick={onCancel}>Cancel</button>}
        <button className="primary" onClick={onSave} disabled={!name.trim() || groupNeedsReload}>
          {editing ? "Save" : `Create ${label}`}
        </button>
      </div>
    </div>
  );
}
