import type { SamplerParamSpec } from "../../api/client";
import { Field } from "../Field";
import type { Draft } from "./presetDraft";

/** The preset form, drawn from the server's table: a control the server adds
 *  is a row here with no change on this side. Save and Cancel are the caller's. */
export function PresetForm(
  { draft, onDraft, table, busy, newer, onSave, onCancel }: {
    draft: Draft;
    onDraft: (d: Draft) => void;
    table: SamplerParamSpec[];
    busy: boolean;
    newer: boolean;
    onSave: () => void;
    onCancel: () => void;
  },
) {
  return (
          <div className="sampler-form">
            <Field label="Name">
              <input type="text" value={draft.name}
                     onChange={(e) => onDraft({ ...draft, name: e.target.value })} />
            </Field>
            <p className="field-hint">
              Leave a box blank to leave that parameter at the provider's default.
              A parameter a model cannot take is kept here and not sent, and
              Preview on… says which.
            </p>
            {table.map((spec) => spec.kind === "choice" ? (
              <Field key={spec.name} label={spec.label}>
                <select value={draft.values[spec.name] ?? ""}
                        onChange={(e) => onDraft({ ...draft,
                          values: { ...draft.values, [spec.name]: e.target.value } })}>
                  <option value="">Provider's default</option>
                  {(spec.choices ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
                  {/* A stored value the table no longer offers (a hand edit, a
                      newer build's level) is shown as itself, not as the
                      default it is not; saving it asks the server to refuse. */}
                  {draft.values[spec.name] && !(spec.choices ?? []).includes(draft.values[spec.name])
                    && <option value={draft.values[spec.name]} disabled>
                         {draft.values[spec.name]} (not offered)
                       </option>}
                </select>
              </Field>
            ) : spec.kind === "stop" ? (
              <Field key={spec.name} label={spec.label}
                     hint={`One per line, at most ${spec.max_entries}. Write a line break as \\n.`}>
                <textarea rows={3} value={draft.values[spec.name] ?? ""}
                          onChange={(e) => onDraft({ ...draft,
                            values: { ...draft.values, [spec.name]: e.target.value } })} />
              </Field>
            ) : (
              <Field key={spec.name} label={spec.label}
                     hint={spec.name === "max_tokens"
                       ? "Caps every call this preset reaches — absorb and dossiers included — and a reply cut off by it is kept as if complete. On a reasoning model, many providers count the model's thinking against this cap too, so a cap sized for the prose can be spent before the reply starts. Response targets (words and paragraphs) are the tool for prose length."
                       : `${spec.min} to ${spec.max}`}>
                <input type="number" inputMode="decimal"
                       step={spec.kind === "int" ? 1 : "any"}
                       min={spec.min} max={spec.max}
                       value={draft.values[spec.name] ?? ""}
                       onChange={(e) => onDraft({ ...draft,
                         values: { ...draft.values, [spec.name]: e.target.value } })} />
              </Field>
            ))}
            <Field label="Notes">
              <textarea rows={3} value={draft.notes}
                        onChange={(e) => onDraft({ ...draft, notes: e.target.value })} />
            </Field>
            <div className="form-actions">
              <button className="primary" onClick={onSave}
                      disabled={busy || newer || !draft.name.trim()}>Save</button>
              <button className="subtle" onClick={onCancel}>Cancel</button>
            </div>
          </div>
  );
}
