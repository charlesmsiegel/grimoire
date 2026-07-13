import { useState, type ChangeEvent } from "react";
import { api, type EntityScope, type ModuleDetail, type ModuleField, type Sheet } from "../api/client";
import SheetLayout, { assembledDefs, themeStyle } from "./SheetLayout";

/** Module sheet-type kind for a file kind (pcs share characters types) — mirrors backend sheets.sheet_kind. */
export const typeKind = (k: string) => (k === "pcs" ? "characters" : k);

/** Full field-key set (group fields + own fields) for a sheet type — used to diff a type change. */
const keysOf = (module: ModuleDetail, t: string) => assembledDefs(module, t).map((f) => f.key);

/** Converts stored list-field arrays into raw newline-joined strings for the textarea draft. Call
 *  once on entering edit mode (or after a type change) — never on every keystroke, or the trailing
 *  newline from a fresh Enter press gets stripped before the user can type the next line. */
function toEditDraft(fields: Record<string, unknown>, defs: ModuleField[]): Record<string, unknown> {
  const draft = { ...fields };
  for (const f of defs) {
    if (f.type === "list" && Array.isArray(draft[f.key])) {
      draft[f.key] = (draft[f.key] as string[]).join("\n");
    }
  }
  return draft;
}

/** Normalizes a draft's raw list-field strings into arrays. Call once at each commit point (save,
 *  type change) — never inside the textarea's onChange. */
function normalizeForSave(draft: Record<string, unknown>, defs: ModuleField[]): Record<string, unknown> {
  const out = { ...draft };
  for (const f of defs) {
    if (f.type === "list" && typeof out[f.key] === "string") {
      out[f.key] = (out[f.key] as string).split("\n").map((s) => s.trim()).filter(Boolean);
    }
  }
  return out;
}

export default function SheetEditor({ scope, module, kind, eid, initial, onClose, onSaved }:
  { scope: EntityScope; module: ModuleDetail; kind: string; eid: string; initial: Sheet;
    onClose: () => void; onSaved: () => void }) {
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [sheetType, setSheetType] = useState<string | null>(initial.sheet_type);
  const [fields, setFields] = useState<Record<string, unknown>>(initial.fields);
  const [draft, setDraft] = useState<Record<string, unknown>>(initial.fields);
  const [error, setError] = useState<string | null>(null);

  const typeDef = sheetType ? module.sheets.sheet_types[sheetType] : undefined;

  const otherTypes = Object.entries(module.sheets.sheet_types)
    .filter(([tid, st]) => st.kind === typeKind(kind) && tid !== sheetType);

  const layoutTree = sheetType ? module.layout?.sheet_types?.[sheetType] : undefined;
  const layoutDropped = !!sheetType && !layoutTree && (module.display_errors ?? []).some(
    (e) => e.source === "layout" && (e.sheet_type === sheetType ||
      (e.sheet_type === null && Object.keys(module.layout?.sheet_types ?? {}).length === 0)));

  function startEdit() {
    setDraft(toEditDraft(fields, assembledDefs(module, sheetType)));
    setError(null);
    setMode("edit");
  }

  function cancel() {
    setDraft(fields);
    setMode("view");
  }

  function setField(key: string, value: unknown) {
    setDraft({ ...draft, [key]: value });
  }

  async function save() {
    if (!sheetType) return;
    setError(null);
    try {
      const payload = normalizeForSave(draft, assembledDefs(module, sheetType));
      await api.putSheet(scope, module.id, kind, eid, { sheet_type: sheetType, fields: payload });
      setFields(payload);
      setMode("view");
      onSaved();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function changeType(e: ChangeEvent<HTMLSelectElement>) {
    const newType = e.target.value;
    e.target.value = "";
    if (!newType || !sheetType) {
      if (newType) await createFromScratch(newType);
      return;
    }
    const newKeys = new Set(keysOf(module, newType));
    const oldKeys = keysOf(module, sheetType);
    const dropped = oldKeys.filter((k) => !newKeys.has(k));
    const label = module.sheets.sheet_types[newType]?.label ?? newType;
    const msg = dropped.length > 0
      ? `Change type to ${label}? This drops: ${dropped.join(", ")}`
      : `Change type to ${label}?`;
    if (!window.confirm(msg)) return;
    const normalized = normalizeForSave(draft, assembledDefs(module, sheetType));
    const survivors: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(normalized)) {
      if (newKeys.has(k)) survivors[k] = v;
    }
    await commitTypeChange(newType, survivors);
  }

  async function createFromScratch(newType: string) {
    const label = module.sheets.sheet_types[newType]?.label ?? newType;
    if (!window.confirm(`Set sheet type to ${label}?`)) return;
    await commitTypeChange(newType, {});
  }

  async function commitTypeChange(newType: string, survivors: Record<string, unknown>) {
    setError(null);
    try {
      await api.putSheet(scope, module.id, kind, eid, { sheet_type: newType, fields: survivors });
      setSheetType(newType);
      setFields(survivors);
      setDraft(survivors);
      setMode("view");
      onSaved();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function removeSheet() {
    if (!window.confirm("Delete this sheet? This cannot be undone.")) return;
    setError(null);
    try {
      await api.deleteSheet(scope, module.id, kind, eid);
      onSaved();
      onClose();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <>
      <div className="sheet-backdrop" onClick={onClose} />
      <div className="sheet-takeover" role="dialog" aria-label={typeDef?.label ?? "Sheet"}
           style={themeStyle(module.theme)}
           data-dots={module.theme?.dots}
           data-corners={module.theme?.corners}>
        <div className="form-actions">
          {mode === "view" && <button className="subtle" onClick={startEdit} disabled={!typeDef}>Edit</button>}
          {mode === "edit" && <button className="primary" onClick={save}>Save</button>}
          {mode === "edit" && <button className="subtle" onClick={cancel}>Cancel</button>}
          <select aria-label="Change type" defaultValue="" onChange={changeType}>
            <option value="">Change type…</option>
            {otherTypes.map(([tid, st]) => <option key={tid} value={tid}>{st.label}</option>)}
          </select>
          <button className="subtle" onClick={removeSheet}>Delete sheet</button>
          <button className="subtle" onClick={onClose}>Close</button>
        </div>

        {initial.errors.length > 0 && <div className="banner">{initial.errors.join("; ")}</div>}
        {error && <div className="banner">{error}</div>}
        {layoutDropped && (
          <div className="field-hint">
            This module's layout for this sheet type is invalid — using the default arrangement.
          </div>
        )}

        <h3>{typeDef?.label ?? "No sheet type"}</h3>

        {!typeDef ? (
          <div className="field-hint">This entity has no sheet type yet — pick one above to begin.</div>
        ) : mode === "view" ? (
          <SheetLayout module={module} sheetType={sheetType!} mode="view"
                       values={fields} derived={initial.derived} />
        ) : (
          <SheetLayout module={module} sheetType={sheetType!} mode="edit"
                       values={draft} derived={initial.derived} onChange={setField} />
        )}
      </div>
    </>
  );
}
