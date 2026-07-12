import { useState, type ChangeEvent } from "react";
import { api, type EntityScope, type ModuleDetail, type ModuleField, type Sheet } from "../api/client";
import { Field } from "./Field";

/** Module sheet-type kind for a file kind (pcs share characters types) — mirrors backend sheets.sheet_kind. */
export const typeKind = (k: string) => (k === "pcs" ? "characters" : k);

type ResourceValue = { current: number; max: number };

function isResource(v: unknown): v is ResourceValue {
  return !!v && typeof v === "object" && "current" in (v as object) && "max" in (v as object);
}

function fieldLabel(f: ModuleField): string {
  return f.label ?? f.key;
}

function displayValue(f: ModuleField, v: unknown): string {
  switch (f.type) {
    case "resource": {
      const rv = isResource(v) ? v : { current: 0, max: f.max ?? 0 };
      return `${rv.current} / ${rv.max}`;
    }
    case "list":
      return Array.isArray(v) && v.length > 0 ? (v as string[]).join(", ") : "—";
    case "text":
      return typeof v === "string" && v ? v : "—";
    default:
      return typeof v === "number" ? String(v) : "0";
  }
}

function widget(f: ModuleField, value: unknown, onChange: (v: unknown) => void) {
  const label = fieldLabel(f);
  switch (f.type) {
    case "resource": {
      const rv = isResource(value) ? value : { current: 0, max: f.max ?? 0 };
      return (
        <div className="field" key={f.key}>
          <label>{label}</label>
          <div className="resource-inputs">
            <input type="number" aria-label={`${label} current`} min={0} value={rv.current}
                   onChange={(e) => onChange({ ...rv, current: Number(e.target.value) })} />
            <span>/</span>
            <input type="number" aria-label={`${label} max`} min={0} value={rv.max}
                   onChange={(e) => onChange({ ...rv, max: Number(e.target.value) })} />
          </div>
        </div>
      );
    }
    case "text":
      return (
        <Field key={f.key} label={label}>
          <input type="text" value={typeof value === "string" ? value : ""}
                 onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
    case "list": {
      const arr = Array.isArray(value) ? (value as string[]) : [];
      return (
        <Field key={f.key} label={label} hint="one per line">
          <textarea rows={3} value={arr.join("\n")}
                    onChange={(e) => onChange(e.target.value.split("\n"))} />
        </Field>
      );
    }
    default: { // number | dots | track
      const n = typeof value === "number" ? value : 0;
      return (
        <Field key={f.key} label={label}>
          <input type="number" min={0} max={f.max} value={n}
                 onChange={(e) => onChange(Number(e.target.value))} />
        </Field>
      );
    }
  }
}

/** Full field-key set (group fields + own fields) for a sheet type — used to diff a type change. */
function keysOf(module: ModuleDetail, t: string): string[] {
  const st = module.sheets.sheet_types[t];
  if (!st) return [];
  return st.groups
    .flatMap((g) => module.sheets.groups[g]?.fields ?? [])
    .concat(st.fields)
    .map((f) => f.key);
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
  const groupIds = typeDef?.groups ?? [];
  const ownFields = typeDef?.fields ?? [];

  const otherTypes = Object.entries(module.sheets.sheet_types)
    .filter(([tid, st]) => st.kind === typeKind(kind) && tid !== sheetType);

  function startEdit() {
    setDraft(fields);
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
      await api.putSheet(scope, module.id, kind, eid, { sheet_type: sheetType, fields: draft });
      setFields(draft);
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
    const survivors: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(draft)) {
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
      <div className="sheet-takeover" role="dialog" aria-label={typeDef?.label ?? "Sheet"}>
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

        <h3>{typeDef?.label ?? "No sheet type"}</h3>

        {!typeDef ? (
          <div className="field-hint">This entity has no sheet type yet — pick one above to begin.</div>
        ) : mode === "view" ? (
          <div className="sheet-view">
            {groupIds.map((gid) => {
              const g = module.sheets.groups[gid];
              if (!g) return null;
              return (
                <div className="side-section" key={gid}>
                  <h4>{g.label ?? gid}</h4>
                  {g.fields.map((f) => (
                    <div className="sheet-row" key={f.key}>{fieldLabel(f)}: {displayValue(f, fields[f.key])}</div>
                  ))}
                </div>
              );
            })}
            {ownFields.length > 0 && (
              <div className="side-section">
                <h4>Details</h4>
                {ownFields.map((f) => (
                  <div className="sheet-row" key={f.key}>{fieldLabel(f)}: {displayValue(f, fields[f.key])}</div>
                ))}
              </div>
            )}
            {Object.entries(initial.derived).length > 0 && (
              <div className="side-section">
                <h4>Derived</h4>
                {Object.entries(initial.derived).map(([k, v]) => (
                  <div className="field-hint" key={k}>{k}: {String(v)}</div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="form">
            {groupIds.map((gid) => {
              const g = module.sheets.groups[gid];
              if (!g) return null;
              return (
                <div key={gid}>
                  <h4>{g.label ?? gid}</h4>
                  {g.fields.map((f) => widget(f, draft[f.key], (v) => setField(f.key, v)))}
                </div>
              );
            })}
            {ownFields.map((f) => widget(f, draft[f.key], (v) => setField(f.key, v)))}
          </div>
        )}
      </div>
    </>
  );
}
