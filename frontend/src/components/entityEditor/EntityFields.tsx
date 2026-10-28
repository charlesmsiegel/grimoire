import type { EntityFieldSpec, GroupRefBinding } from "../../api/client";
import type { RecordRef } from "../../api/loreOwners";
import { Field } from "../Field";
import { activationKeys, choiceOptions, displayLabels, KIND_NOUNS, numberInputCanShow, parseRefs,
  worldRefLabel, WORLD_BINDING_HINT, SEVERAL_REFS_HINT, DANGLING_HINT, UNLOADED_HINT, type ChoiceList } from "./fields";

/** A `choice` as a picker. A stored value none of the options answers to -- a
 *  climate since deleted, a hand-edited file -- is kept as a row of its own so
 *  it is VISIBLE and clearable rather than silently blanked on the next save,
 *  the same reason `RefField` keeps a dangling ref. It is called "not an
 *  option" only once the list has actually arrived; until then (and after a
 *  failed load) the value is simply shown, because a request outage is not a
 *  fact about the value -- and a failed load says so under the control, since
 *  a picker that offers nothing and says nothing reads as a broken one. */
function ChoiceField({ spec, options, value, onChange }: {
  spec: EntityFieldSpec; options: ChoiceList | undefined; value: string;
  onChange: (v: string) => void;
}) {
  const list = Array.isArray(options) ? options : null;
  const listed = list !== null && list.some((o) => o.value === value);
  return (
    <Field label={spec.label}
           hint={options === "failed" ? "Could not load the choices for this field." : undefined}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">(none)</option>
        {value !== "" && !listed && (
          <option value={value}>{list === null ? value : `${value} (not an option)`}</option>
        )}
        {(list ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </Field>
  );
}

/** The picker for one `ref` field (#222).
 *
 *  Real radios and real checkboxes, for the reason the secrecy picker gives:
 *  a native group gets arrow-key navigation and roving focus for free, which a
 *  row of buttons wearing `role="radio"` does not. Which of the two a field
 *  gets is `multi` — a group has one leader and a creature ranges over several
 *  places, and a control that let you tick two leaders would be offering a save
 *  the backend refuses.
 *
 *  Not wrapped in `Field`: `Field` labels ONE control by id, and this is a set
 *  of them. The heading is carried by the group's `aria-label` instead, which
 *  is what makes each option's own label unambiguous — "Mara" under Leader and
 *  "Mara" under Held by are two different controls, and only the group says so.
 */
function RefField({ spec, options, value, onChange, unresolvedHint, optionsComplete, foreignRefs = [] }: {
  spec: EntityFieldSpec;
  options: RecordRef[];
  value: string;
  onChange: (v: string) => void;
  /** What a ref no candidate answers to means right now — "the record is gone"
   *  only when the candidate lists actually loaded. See `unresolvedHint` in the
   *  editor below. */
  unresolvedHint: string;
  /** Whether `options` is the whole truth. False while the listings are in
   *  flight or after one failed, and it is what stops an empty picker from
   *  claiming the store is empty. */
  optionsComplete: boolean;
  /** Server identity diagnostics override even a matching campaign option.
   *  Candidate names are presentation, not evidence that a World ref binds. */
  foreignRefs?: string[];
}) {
  const selected = parseRefs(value);
  const candidates = options.filter((o) => !selected.includes(o.ref) || !foreignRefs.includes(o.ref));
  const shown = displayLabels(options);
  const labelOf = (o: RecordRef) => shown.get(o.ref) ?? o.label;
  // A stored ref that no candidate answers to — the record was deleted, or it
  // lives outside this scope. It gets a row of its own, checked, so the value
  // is VISIBLE and clearable. Leaving it out was the tempting shortcut and the
  // wrong one: the field would look unset while still saving the old ref, and
  // the one thing the user cannot then do is remove it.
  const dangling = selected.filter((r) => !candidates.some((o) => o.ref === r));
  // Only what this field is allowed to name, in the words the field uses. A
  // picker that offered nothing and said nothing reads as a broken control.
  //
  // ...but "there are none" is a claim about the store, and only a listing
  // that arrived can make it. A single-kind field whose one listing failed
  // would otherwise sit there reading "No locations yet." — a request outage
  // presented as an empty library, with no error and nothing to retry.
  const empty = optionsComplete
    ? `No ${spec.kinds?.map((k) => KIND_NOUNS[k].many).join(" or ")} yet.`
    : "Could not load the list of records to choose from.";
  const danglingRow = (ref: string, type: "radio" | "checkbox", onClear: () => void) => (
    <label key={ref} className="owner-option dangling"
           title={foreignRefs.includes(ref) ? WORLD_BINDING_HINT : unresolvedHint}>
      <input type={type} name={spec.key}
             aria-label={foreignRefs.includes(ref) ? worldRefLabel(ref) : ref} checked onChange={onClear} />
      {foreignRefs.includes(ref) ? worldRefLabel(ref) : ref}
    </label>
  );
  if (spec.multi) {
    return (
      <div className="field">
        <label>{spec.label}</label>
        <div className="chips owner-picker" role="group" aria-label={spec.label}>
          {candidates.map((o) => (
            <label key={o.ref} className="owner-option">
              <input type="checkbox" aria-label={labelOf(o)} checked={selected.includes(o.ref)}
                     onChange={(e) => onChange(
                       (e.target.checked
                         ? [...selected, o.ref]
                         : selected.filter((r) => r !== o.ref)).join(", "))} />
              {labelOf(o)}
            </label>
          ))}
          {dangling.map((ref) => danglingRow(ref, "checkbox",
            () => onChange(selected.filter((r) => r !== ref).join(", "))))}
          {options.length === 0 && dangling.length === 0
            && <span className="field-hint">{empty}</span>}
        </div>
      </div>
    );
  }
  return (
    <div className="field">
      <label>{spec.label}</label>
      <div className="chips owner-picker" role="radiogroup" aria-label={spec.label}>
        {/* An explicit None, because a radio group has no way back to unset:
            without it, picking a leader by accident could never be undone. */}
        <label className="owner-option">
          <input type="radio" name={spec.key} aria-label="None" checked={selected.length === 0}
                 onChange={() => onChange("")} />
          None
        </label>
        {candidates.map((o) => (
          <label key={o.ref} className="owner-option">
            <input type="radio" name={spec.key} aria-label={labelOf(o)}
                   checked={selected[0] === o.ref} onChange={() => onChange(o.ref)} />
            {labelOf(o)}
          </label>
        ))}
        {/* Clearing it means picking None or another candidate; the row itself
            is checked and stays checked until one of those happens, which is
            how a radio group says "this is the current value". */}
        {dangling.map((ref) => danglingRow(ref, "radio", () => undefined))}
        {options.length === 0 && dangling.length === 0
          && <span className="field-hint">{empty}</span>}
      </div>
    </div>
  );
}

export function EntityFieldControl({ spec: f, value, options, optionsComplete, choiceOptionsByKey,
  foreignRefs, bindings, onChange, onBindingChange }: {
  spec: EntityFieldSpec; value: string; options: RecordRef[]; optionsComplete: boolean;
  choiceOptionsByKey: Record<string, ChoiceList>; foreignRefs: string[]; bindings: readonly GroupRefBinding[];
  onChange: (value: string) => void;
  onBindingChange: (binding: GroupRefBinding, checked: boolean) => void;
}) {
  switch (f.widget) {
    case "ref":
      return (
        <div key={f.key}>
          <RefField spec={f} options={options} value={value}
                    foreignRefs={foreignRefs}
                    unresolvedHint={optionsComplete ? DANGLING_HINT : UNLOADED_HINT}
                    optionsComplete={optionsComplete} onChange={onChange} />
          {foreignRefs.filter((actor) => parseRefs(value).includes(actor)).map((actor) => (
            // A single-valued field still holding a legacy second ref cannot
            // be resent as it stands (the save boundary refuses two Leaders),
            // so no opt-in could reach the store: say how to resolve it.
            !f.multi && parseRefs(value).length > 1 ? (
              <div className="field-hint" key={actor}>{worldRefLabel(actor)}. {SEVERAL_REFS_HINT}</div>
            ) : (
            <div className="field" key={actor}>
              <label>
                <input type="checkbox" aria-label={`Bind ${f.label} reference ${actor}`}
                       checked={bindings.some((b) => b.field === f.key && b.actor === actor)}
                       onChange={(event) => {
                         const binding: GroupRefBinding = { field: f.key === "leader" ? "leader" : "members", actor };
                         const checked = event.target.checked;
                         onBindingChange(binding, checked);
                       }} />
                Bind {f.label} reference {actor}
              </label>
              <div className="field-hint">{WORLD_BINDING_HINT}</div>
            </div>
            )
          ))}
        </div>
      );
    case "choice":
      return (
        <ChoiceField key={f.key} spec={f} options={choiceOptions(f, choiceOptionsByKey)}
                     value={value} onChange={onChange} />
      );
    case "number":
      if (!numberInputCanShow(value)) break;   // the text box below
      // `step="any"`: the bound is on the value, not its precision --
      // a persistence of 0.35 is as valid as 0.5. The activation counts are
      // whole numbers, and the backend refuses a fraction, so they step by 1.
      return (
        <Field key={f.key} label={f.label}>
          <input type="number" value={value} min={f.min} max={f.max}
                 step={activationKeys().includes(f.key) ? 1 : "any"}
                 onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
    default:
      break;
  }
  return (
    <Field key={f.key} label={f.label}>
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );

}
