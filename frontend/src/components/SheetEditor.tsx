import { useState, type ChangeEvent } from "react";
import { ApiError, api, type EntityScope, type ModuleDetail, type ModuleField, type Sheet } from "../api/client";
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

export default function SheetEditor({ scope, module, kind, eid, initial, onClose, onSaved, onOpenRef }:
  { scope: EntityScope; module: ModuleDetail; kind: string; eid: string; initial: Sheet;
    onClose: () => void; onSaved: () => void; onOpenRef?: (kind: string, id: string) => void }) {
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [sheetType, setSheetType] = useState<string | null>(initial.sheet_type);
  const [fields, setFields] = useState<Record<string, unknown>>(initial.fields);
  const [draft, setDraft] = useState<Record<string, unknown>>(initial.fields);
  // The last-read whole-sheet snapshot -- sent back as `expected` (CAS) on every
  // write and as `gen` on delete. A sheet already exists by the time this
  // component mounts (SheetPanel only renders it once `sheet` is non-null), so
  // this is always an object snapshot here, never the JS `null` that asserts
  // "no sheet exists" (that's SheetPanel.createSheet's/CreationWizard's job).
  const [gen, setGen] = useState<string | null>(initial.gen);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const typeDef = sheetType ? module.sheets.sheet_types[sheetType] : undefined;

  const otherTypes = Object.entries(module.sheets.sheet_types)
    .filter(([tid, st]) => st.kind === typeKind(kind) && tid !== sheetType);

  const layoutTree = sheetType ? module.layout?.sheet_types?.[sheetType] : undefined;
  const layoutDropped = !!sheetType && !layoutTree && (module.display_errors ?? []).some(
    (e) => e.source === "layout" && (e.sheet_type === sheetType || e.sheet_type === "*"));

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

  // A 409 means someone else (another tab, the audit apply path, …) wrote this
  // sheet since we last read it -- re-fetch the live sheet, adopt it as the new
  // form state/snapshot, and tell the user rather than silently discarding
  // their edit or leaving the form pointed at a stale gen forever.
  async function reloadAfterConflict() {
    try {
      const { sheet: fresh } = await api.getSheet(scope, module.id, kind, eid);
      if (fresh) {
        setSheetType(fresh.sheet_type);
        setFields(fresh.fields);
        setDraft(fresh.fields);
        setGen(fresh.gen);
      }
      setMode("view");
      setNotice("This sheet changed elsewhere — reloaded.");
      onSaved();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  // After a successful campaign-scope write, refetch and adopt the live
  // sheet snapshot. A write can mint a new `gen` server-side (a type change
  // always does; a plain save may under a module's own rules) without
  // returning the new sheet in its response -- if we kept the pre-write
  // `gen` in local state, the *next* save's CAS check would 409 against it
  // and reloadAfterConflict would discard the user's live draft. Campaign
  // scope only: world-scope writes carry no CAS/gen at all. Best-effort --
  // on failure, keep the local snapshot; a subsequent CAS mismatch still
  // self-heals via reloadAfterConflict.
  async function refreshSnapshot() {
    try {
      const { sheet: fresh } = await api.getSheet(scope, module.id, kind, eid);
      if (fresh) {
        setSheetType(fresh.sheet_type);
        setFields(fresh.fields);
        setGen(fresh.gen);
      }
    } catch {
      // best effort -- see comment above
    }
  }

  async function save() {
    if (!sheetType) return;
    setError(null);
    setNotice(null);
    try {
      const payload = normalizeForSave(draft, assembledDefs(module, sheetType));
      await api.putSheet(scope, module.id, kind, eid,
        { sheet_type: sheetType, fields: payload, expected: { sheet_type: sheetType, fields, gen } });
      setFields(payload);
      if (scope.kind === "campaign") await refreshSnapshot();
      setMode("view");
      onSaved();
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 409) {
        await reloadAfterConflict();
        return;
      }
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
    setNotice(null);
    try {
      await api.putSheet(scope, module.id, kind, eid,
        { sheet_type: newType, fields: survivors, expected: { sheet_type: sheetType, fields, gen } });
      setSheetType(newType);
      setFields(survivors);
      setDraft(survivors);
      // A type change mints a new gen server-side, but the PUT response carries
      // no sheet -- refreshSnapshot() re-fetches so the next write's CAS check
      // uses the live gen instead of 409ing and discarding the user's next edit.
      if (scope.kind === "campaign") await refreshSnapshot();
      setMode("view");
      onSaved();
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 409) {
        await reloadAfterConflict();
        return;
      }
      setError(err.detail ?? String(err));
    }
  }

  async function advanceField(key: string) {
    setError(null);
    try {
      const { sheet: fresh } = await api.advanceSheet(scope.id, kind, eid, key);
      setFields(fresh.fields);
      setDraft(fresh.fields);
      setGen(fresh.gen);
      onSaved();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function removeSheet() {
    if (!window.confirm("Delete this sheet? This cannot be undone.")) return;
    setError(null);
    setNotice(null);
    try {
      await api.deleteSheet(scope, module.id, kind, eid, gen);
      onSaved();
      onClose();
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 409) {
        await reloadAfterConflict();
        return;
      }
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
        {notice && <div className="field-hint">{notice}</div>}
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
                       values={fields} derived={initial.derived}
                       scope={scope} onOpenRef={onOpenRef} onAdvance={advanceField} />
        ) : (
          <SheetLayout module={module} sheetType={sheetType!} mode="edit"
                       values={draft} derived={initial.derived} onChange={setField}
                       scope={scope} onOpenRef={onOpenRef} />
        )}
      </div>
    </>
  );
}
