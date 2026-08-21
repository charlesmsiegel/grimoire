import { useMemo, useState } from "react";
import { api, type ModuleDetail, type ModuleEditResult, type ModuleField } from "../api/client";
// From `types`, not through `client` — see the note on ENTITY_KINDS there.
import { ENTITY_KINDS } from "../api/types";
import { ErrorList, ImpactConfirm, useModuleDryRun, type SaveFn } from "./moduleEditShared";

const FIELD_TYPES = ["number", "dots", "track", "resource", "text", "list", "ref"];
const SHEET_KINDS = ["characters", "items", "locations", "creatures", "groups", "lore"];

type Derived = Record<string, string>;

export function RenamePrompt({ disabled, onRename }: {
  disabled: boolean; onRename: (to: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState("");
  if (!open) {
    return (
      <button disabled={disabled}
              title={disabled ? "save or cancel your edits first" : "rename"}
              onClick={() => setOpen(true)}>Rename…</button>
    );
  }
  return (
    <span className="chips">
      <label>New key<input value={to} onChange={(e) => setTo(e.target.value)} /></label>
      <button onClick={() => { setOpen(false); onRename(to); }}>Rename</button>
      <button onClick={() => setOpen(false)}>Cancel</button>
    </span>
  );
}

function num(v: string): number | undefined {
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? undefined : n;
}

export function FieldRows({ fields, setFields, existingKeys, dirty, onRename }: {
  fields: ModuleField[]; setFields: (f: ModuleField[]) => void;
  existingKeys: Set<string>; dirty: boolean;
  onRename: (from: string, to: string) => void;
}) {
  const upd = (i: number, patch: Partial<ModuleField>) =>
    setFields(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  return (
    <div>
      {fields.map((f, i) => (
        <div className="chips" key={i}>
          <input aria-label="Field key" value={f.key}
                 readOnly={existingKeys.has(f.key)}
                 onChange={(e) => upd(i, { key: e.target.value })} />
          {existingKeys.has(f.key) && (
            <RenamePrompt disabled={dirty} onRename={(to) => onRename(f.key, to)} />
          )}
          <input aria-label="Field label" value={f.label ?? ""}
                 onChange={(e) => upd(i, { label: e.target.value })} />
          <select aria-label="Field type" value={f.type}
                  onChange={(e) => upd(i, { type: e.target.value })}>
            {FIELD_TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
          {["dots", "track", "resource", "number"].includes(f.type) && (
            <input aria-label="Max" type="number" value={f.max ?? ""}
                   onChange={(e) => upd(i, { max: num(e.target.value) })} />
          )}
          {f.type === "number" && (
            <>
              <input aria-label="Min" type="number" value={f.min ?? ""}
                     onChange={(e) => upd(i, { min: num(e.target.value) })} />
              <input aria-label="Default" type="number" value={f.default ?? ""}
                     onChange={(e) => upd(i, { default: num(e.target.value) })} />
            </>
          )}
          {f.type === "ref" && (
            <select aria-label="Ref kind" value={f.ref_kind ?? "lore"}
                    onChange={(e) => upd(i, { ref_kind: e.target.value })}>
              {ENTITY_KINDS.map((k) => <option key={k}>{k}</option>)}
            </select>
          )}
          <button onClick={() => setFields(fields.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button onClick={() => setFields([...fields, { key: "", type: "number" } as ModuleField])}>
        + Add field
      </button>
    </div>
  );
}

export function DerivedRows({ derived, setDerived, existing, dirty, onRename, sample }: {
  derived: Derived; setDerived: (d: Derived) => void;
  existing: Set<string>; dirty: boolean;
  onRename: (from: string, to: string) => void;
  sample?: Record<string, number | boolean>;
}) {
  const entries = Object.entries(derived);
  return (
    <div>
      {entries.map(([name, expr]) => (
        <div className="chips" key={name}>
          <input aria-label="Derived name" value={name} readOnly={existing.has(name)}
                 onChange={(e) => {
                   const d = { ...derived };
                   delete d[name];
                   d[e.target.value] = expr;
                   setDerived(d);
                 }} />
          {existing.has(name) && (
            <RenamePrompt disabled={dirty} onRename={(to) => onRename(name, to)} />
          )}
          <input aria-label="Derived expression" value={expr}
                 onChange={(e) => setDerived({ ...derived, [name]: e.target.value })} />
          {sample && name in sample && (
            <span className="field-hint">= {String(sample[name])}</span>
          )}
          <button onClick={() => {
            const d = { ...derived };
            delete d[name];
            setDerived(d);
          }}>Remove</button>
        </div>
      ))}
      <button onClick={() => setDerived({ ...derived, "": "" })}>+ Add derived</button>
    </div>
  );
}

type GroupForm = { gid: string; isNew: boolean; label: string;
                   fields: ModuleField[]; derived: Derived };

export function GroupsSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const groups = pack.sheets.groups;
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [form, setForm] = useState<GroupForm | null>(null);

  const seed = (gid: string): GroupForm => ({
    gid, isNew: false, label: groups[gid]?.label ?? gid,
    fields: (groups[gid]?.fields ?? []).map((f) => ({ ...f })),
    derived: { ...(groups[gid]?.derived ?? {}) },
  });
  const baseline = useMemo(
    () => (form && !form.isNew ? JSON.stringify(seed(form.gid)) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form?.gid, pack]);
  const dirty = form != null && baseline != null && JSON.stringify(form) !== baseline;

  const save: SaveFn = (dryRun) => {
    // A blank id must reject, not silently no-op as ok -- requestSave's
    // `!fresh.ok` guard then keeps the form open and shows the error instead
    // of Save quietly discarding the draft (with no PUT ever fired).
    if (!form!.gid) {
      return Promise.resolve(
        { ok: false, errors: ["group id is required"], display_errors: [] } as ModuleEditResult);
    }
    return api.putModuleGroup(pack.id, form!.gid, {
      label: form!.label, fields: form!.fields,
      ...(Object.keys(form!.derived).length ? { derived: form!.derived } : {}),
    }, dryRun);
  };
  const dr = useModuleDryRun(form ? save : async () => ({ ok: true, errors: [], display_errors: [] } as ModuleEditResult), [form]);
  // Group derived samples aren't computed at the group level (the backend's
  // group-only dry-run has no sheet to instantiate) -- they live on every
  // sheet type that composes this group, keyed by tid. A derived name means
  // the same thing (same defaults-based value) in every composing type, so
  // merging is safe; collisions are harmless.
  const composingTids = Object.entries(pack.sheets.sheet_types)
    .filter(([, st]) => (st?.groups ?? []).includes(form?.gid ?? ""))
    .map(([tid]) => tid);
  const groupSample = (() => {
    if (!form || composingTids.length === 0) return undefined;
    const merged: Record<string, number | boolean> = {};
    for (const tid of composingTids) {
      Object.assign(merged, dr.result?.sample?.[tid]?.derived ?? {});
    }
    return Object.keys(merged).length ? merged : undefined;
  })();
  const done = () => { setMode("view"); setForm(null); void reload(); };
  // Delete removes the record the form/selection points at, so unlike `done`
  // (rename/save keep the same record selected) it must also clear `selected`
  // — otherwise the read-only view keeps rendering the now-deleted record.
  const doneDelete = () => { setSelected(null); setMode("view"); setForm(null); void reload(); };
  // impact gate shared by renames and deletes: dry-run first; confirm when
  // any impact count is nonzero; Cancel sends nothing.
  const [gate, setGate] = useState<{ impact: NonNullable<ModuleEditResult["impact"]>;
                                     run: () => Promise<unknown> } | null>(null);
  const [gateError, setGateError] = useState<string[]>([]);
  // `onSuccess` is distinct per caller: a delete returns to the read-only view
  // (`done`), while a rename just refreshes the pack in place — the form stays
  // open with its current edits so the user can keep working.
  const confirmGate = (dryCall: () => Promise<ModuleEditResult>,
                       realCall: () => Promise<ModuleEditResult>,
                       onSuccess: () => void) => {
    setGateError([]);
    return dryCall().then((r) => {
      if (!r.ok) { setGateError(r.errors); return; }
      const i = r.impact;
      const run = () => realCall().then((rr) =>
        rr.ok ? onSuccess() : setGateError(rr.errors));
      if (i && i.sheets_migrated + i.sheets_newly_invalid + i.dangling_refs > 0) {
        setGate({ impact: i, run });
      } else {
        void run();
      }
    });
  };
  // Rename keeps the form open (see above), so the freshly reloaded `pack`
  // and the still-mounted `form` must agree on the key/name that changed —
  // otherwise the old key lingers in the form (readOnly/Rename-button checks
  // key off `existingKeys`, which now reflects the NEW key) and `dirty`
  // spuriously flips true.
  const applyRename = (kind: "field" | "derived", from: string, to: string) =>
    setForm((f) => {
      if (!f) return f;
      if (kind === "field") {
        return { ...f, fields: f.fields.map((fl) => (fl.key === from ? { ...fl, key: to } : fl)) };
      }
      if (!(from in f.derived)) return f;
      const derived = { ...f.derived };
      derived[to] = derived[from];
      delete derived[from];
      return { ...f, derived };
    });
  const rename = (kind: "field" | "derived") => (from: string, to: string) =>
    void confirmGate(
      () => api.renameModulePart(pack.id, kind, { from, group: form!.gid }, to, true),
      () => api.renameModulePart(pack.id, kind, { from, group: form!.gid }, to, false),
      () => { applyRename(kind, from, to); dr.reset(); void reload(); });
  // Group ids are renameable too (backend's `group` rename op) — same
  // confirmGate as field/derived, resyncing both the still-open form's gid
  // and the sidebar selection so the rest of the form (existingKeys, the
  // group-sample lookup keyed by gid) keeps pointing at the right record.
  const renameGroup = (to: string) =>
    void confirmGate(
      () => api.renameModulePart(pack.id, "group", { from: form!.gid }, to, true),
      () => api.renameModulePart(pack.id, "group", { from: form!.gid }, to, false),
      () => {
        setForm((f) => (f ? { ...f, gid: to } : f));
        setSelected(to);
        dr.reset();
        void reload();
      });

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="row" onClick={() => {
          setSelected(null);
          setGateError([]);
          setForm({ gid: "", isNew: true, label: "", fields: [], derived: {} });
          setMode("edit");
        }}>+ New group</button>
        {Object.keys(groups).map((gid) => (
          <button key={gid} className={"row" + (selected === gid ? " active" : "")}
                  onClick={() => { setSelected(gid); setMode("view"); setForm(null); setGateError([]); }}>
            {groups[gid]?.label ?? gid}
          </button>
        ))}
      </div>
      <div className="editor-body">
        {mode === "view" && selected && (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{groups[selected]?.label ?? selected}</h3>
              <div className="chips">
                {(groups[selected]?.fields ?? []).map((f) => (
                  <span key={f.key} className="chip">{f.label ?? f.key}</span>
                ))}
                {Object.keys(groups[selected]?.derived ?? {}).map((d) => (
                  <span key={d} className="chip on">{d}</span>
                ))}
              </div>
            </div>
            <aside className="detail-sidebar">
              {gate && (
                <ImpactConfirm impact={gate.impact}
                               onConfirm={() => { const g = gate; setGate(null); void g.run(); }}
                               onCancel={() => setGate(null)} />
              )}
              {gateError.map((e, i) => <div key={i} className="banner">{e}</div>)}
              <div className="form-actions">
                <button onClick={() => { setGateError([]); setForm(seed(selected)); setMode("edit"); }}>Edit</button>
                <button onClick={() => void confirmGate(
                  () => api.deleteModuleGroup(pack.id, selected, true),
                  () => api.deleteModuleGroup(pack.id, selected, false),
                  doneDelete)}>Delete</button>
              </div>
            </aside>
          </div>
        )}
        {mode === "edit" && form && (
          <div className="detail-main">
            {dr.confirming && dr.result?.impact && (
              <ImpactConfirm impact={dr.result.impact}
                             onConfirm={() => { dr.setConfirming(false); void dr.commit(done); }}
                             onCancel={() => dr.setConfirming(false)} />
            )}
            {gate && (
              <ImpactConfirm impact={gate.impact}
                             onConfirm={() => { const g = gate; setGate(null); void g.run(); }}
                             onCancel={() => setGate(null)} />
            )}
            {gateError.map((e, i) => <div key={i} className="banner">{e}</div>)}
            <ErrorList result={dr.result} />
            {form.isNew ? (
              <label>Group id
                <input value={form.gid}
                       onChange={(e) => setForm({ ...form, gid: e.target.value })} />
              </label>
            ) : (
              <div className="chips">
                <span className="chip on">{form.gid}</span>
                <RenamePrompt disabled={dirty} onRename={renameGroup} />
              </div>
            )}
            <label>Label
              <input value={form.label}
                     onChange={(e) => setForm({ ...form, label: e.target.value })} />
            </label>
            <FieldRows fields={form.fields}
                       setFields={(fields) => setForm({ ...form, fields })}
                       existingKeys={new Set((groups[form.gid]?.fields ?? []).map((f) => f.key))}
                       dirty={dirty} onRename={rename("field")} />
            <DerivedRows derived={form.derived}
                         setDerived={(derived) => setForm({ ...form, derived })}
                         existing={new Set(Object.keys(groups[form.gid]?.derived ?? {}))}
                         dirty={dirty} onRename={rename("derived")}
                         sample={groupSample} />
            {/* hidden while a rename/delete or save confirmation banner is up —
                each banner owns its own Confirm/Cancel until resolved */}
            {!gate && !dr.confirming && (
              <div className="form-actions">
                <button className="primary" onClick={() => dr.requestSave(done)}>Save</button>
                <button onClick={() => { setMode("view"); setForm(null); }}>Cancel</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

type CreationForm = Record<string, { budget: string; costs: Record<string, string> }>;
type AdvancementForm = { pool: string; costs: Derived };
type TypeForm = { tid: string; isNew: boolean; label: string; kind: string; groups: string[];
                  fields: ModuleField[]; derived: Derived;
                  creation: CreationForm; advancement: AdvancementForm };

export function SheetTypesSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const groups = pack.sheets.groups;
  const types = pack.sheets.sheet_types;
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [form, setForm] = useState<TypeForm | null>(null);

  const seed = (tid: string): TypeForm => {
    const t = types[tid];
    const creation: CreationForm = {};
    for (const [gid, pool] of Object.entries(t?.creation?.pools ?? {})) {
      creation[gid] = {
        budget: String(pool.budget ?? ""),
        costs: Object.fromEntries(
          Object.entries(pool.costs ?? {}).map(([k, v]) => [k, String(v)])),
      };
    }
    return {
      tid, isNew: false, label: t?.label ?? tid, kind: t?.kind ?? SHEET_KINDS[0],
      groups: [...(t?.groups ?? [])],
      fields: (t?.fields ?? []).map((f) => ({ ...f })),
      derived: { ...(t?.derived ?? {}) },
      creation,
      advancement: { pool: t?.advancement?.pool ?? "", costs: { ...(t?.advancement?.costs ?? {}) } },
    };
  };
  const baseline = useMemo(
    () => (form && !form.isNew ? JSON.stringify(seed(form.tid)) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form?.tid, pack]);
  const dirty = form != null && baseline != null && JSON.stringify(form) !== baseline;

  const assembledFields = useMemo(() => {
    if (!form) return [] as ModuleField[];
    const gFields = form.groups.flatMap((gid) => groups[gid]?.fields ?? []);
    return [...gFields, ...form.fields];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form?.groups, form?.fields, groups]);
  const resourceFields = assembledFields.filter((f) => f.type === "resource");
  const poolFields = assembledFields.filter((f) => f.type === "number" || f.type === "dots");

  const buildDef = () => {
    const f = form!;
    // Budgets are submitted verbatim (the backend accepts an int or an
    // expression string, and a plain integer typed as text is a valid
    // expression) — never coerce to a number here. A blank budget omits the
    // `budget` key entirely (backend defaults it to 0) rather than
    // submitting an empty string, which would fail expression parsing.
    const creationPools: Record<string, { budget?: string; costs: Record<string, number> }> = {};
    for (const gid of f.groups) {
      const c = f.creation[gid] ?? { budget: "", costs: {} };
      const costs: Record<string, number> = {};
      for (const [k, v] of Object.entries(c.costs)) {
        if (v.trim() !== "") { const n = num(v); if (n !== undefined) costs[k] = n; }
      }
      const budgetTrim = c.budget.trim();
      if (budgetTrim === "" && Object.keys(costs).length === 0) continue;
      creationPools[gid] = { ...(budgetTrim !== "" ? { budget: c.budget } : {}), costs };
    }
    const advancement = f.advancement.pool
      ? { pool: f.advancement.pool, costs: f.advancement.costs }
      : undefined;
    return {
      label: f.label, kind: f.kind, groups: f.groups, fields: f.fields,
      ...(Object.keys(f.derived).length ? { derived: f.derived } : {}),
      ...(Object.keys(creationPools).length ? { creation: { pools: creationPools } } : {}),
      ...(advancement ? { advancement } : {}),
    };
  };

  const save: SaveFn = (dryRun) => {
    // A blank id must reject, not silently no-op as ok -- requestSave's
    // `!fresh.ok` guard then keeps the form open and shows the error instead
    // of Save quietly discarding the draft (with no PUT ever fired).
    if (!form!.tid) {
      return Promise.resolve(
        { ok: false, errors: ["sheet type id is required"], display_errors: [] } as ModuleEditResult);
    }
    return api.putModuleSheetType(pack.id, form!.tid, buildDef(), dryRun);
  };
  const dr = useModuleDryRun(form ? save : async () => ({ ok: true, errors: [], display_errors: [] } as ModuleEditResult), [form]);
  const done = () => { setMode("view"); setForm(null); void reload(); };
  // Delete removes the record the form/selection points at, so unlike `done`
  // (rename/save keep the same record selected) it must also clear `selected`
  // — otherwise the read-only view keeps rendering the now-deleted record.
  const doneDelete = () => { setSelected(null); setMode("view"); setForm(null); void reload(); };

  const [gate, setGate] = useState<{ impact: NonNullable<ModuleEditResult["impact"]>;
                                     run: () => Promise<unknown> } | null>(null);
  const [gateError, setGateError] = useState<string[]>([]);
  // `onSuccess` is distinct per caller: a delete returns to the read-only view
  // (`done`), while a rename just refreshes the pack in place — the form stays
  // open with its current edits so the user can keep working.
  const confirmGate = (dryCall: () => Promise<ModuleEditResult>,
                       realCall: () => Promise<ModuleEditResult>,
                       onSuccess: () => void) => {
    setGateError([]);
    return dryCall().then((r) => {
      if (!r.ok) { setGateError(r.errors); return; }
      const i = r.impact;
      const run = () => realCall().then((rr) =>
        rr.ok ? onSuccess() : setGateError(rr.errors));
      if (i && i.sheets_migrated + i.sheets_newly_invalid + i.dangling_refs > 0) {
        setGate({ impact: i, run });
      } else {
        void run();
      }
    });
  };
  // Rename keeps the form open (see above), so the freshly reloaded `pack`
  // and the still-mounted `form` must agree on the key/name that changed —
  // otherwise the old key lingers in the form (readOnly/Rename-button checks
  // key off `existingKeys`, which now reflects the NEW key) and `dirty`
  // spuriously flips true.
  const applyRename = (kind: "field" | "derived", from: string, to: string) =>
    setForm((f) => {
      if (!f) return f;
      if (kind === "field") {
        return { ...f, fields: f.fields.map((fl) => (fl.key === from ? { ...fl, key: to } : fl)) };
      }
      if (!(from in f.derived)) return f;
      const derived = { ...f.derived };
      derived[to] = derived[from];
      delete derived[from];
      return { ...f, derived };
    });
  const rename = (kind: "field" | "derived") => (from: string, to: string) =>
    void confirmGate(
      () => api.renameModulePart(pack.id, kind, { from, sheet_type: form!.tid }, to, true),
      () => api.renameModulePart(pack.id, kind, { from, sheet_type: form!.tid }, to, false),
      () => { applyRename(kind, from, to); dr.reset(); void reload(); });
  const renameType = (to: string) =>
    void confirmGate(
      () => api.renameModulePart(pack.id, "sheet_type", { from: form!.tid }, to, true),
      () => api.renameModulePart(pack.id, "sheet_type", { from: form!.tid }, to, false),
      () => { setForm((f) => (f ? { ...f, tid: to } : f)); dr.reset(); void reload(); });

  const toggleGroup = (gid: string) => {
    if (!form) return;
    const has = form.groups.includes(gid);
    setForm({ ...form, groups: has ? form.groups.filter((g) => g !== gid) : [...form.groups, gid] });
  };

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="row" onClick={() => {
          setSelected(null);
          setGateError([]);
          setForm({ tid: "", isNew: true, label: "", kind: SHEET_KINDS[0], groups: [],
                     fields: [], derived: {}, creation: {}, advancement: { pool: "", costs: {} } });
          setMode("edit");
        }}>+ New sheet type</button>
        {Object.keys(types).map((tid) => (
          <button key={tid} className={"row" + (selected === tid ? " active" : "")}
                  onClick={() => { setSelected(tid); setMode("view"); setForm(null); setGateError([]); }}>
            {types[tid]?.label ?? tid}
          </button>
        ))}
      </div>
      <div className="editor-body">
        {mode === "view" && selected && (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{types[selected]?.label ?? selected}</h3>
              <div className="chips">
                <span className="chip on">{types[selected]?.kind}</span>
                {(types[selected]?.groups ?? []).map((gid) => (
                  <span key={gid} className="chip">{groups[gid]?.label ?? gid}</span>
                ))}
                {(types[selected]?.fields ?? []).map((f) => (
                  <span key={f.key} className="chip">{f.label ?? f.key}</span>
                ))}
                {Object.keys(types[selected]?.derived ?? {}).map((d) => (
                  <span key={d} className="chip on">{d}</span>
                ))}
              </div>
            </div>
            <aside className="detail-sidebar">
              {gate && (
                <ImpactConfirm impact={gate.impact}
                               onConfirm={() => { const g = gate; setGate(null); void g.run(); }}
                               onCancel={() => setGate(null)} />
              )}
              {gateError.map((e, i) => <div key={i} className="banner">{e}</div>)}
              <div className="form-actions">
                <button onClick={() => { setGateError([]); setForm(seed(selected)); setMode("edit"); }}>Edit</button>
                <button onClick={() => void confirmGate(
                  () => api.deleteModuleSheetType(pack.id, selected, true),
                  () => api.deleteModuleSheetType(pack.id, selected, false),
                  doneDelete)}>Delete</button>
              </div>
            </aside>
          </div>
        )}
        {mode === "edit" && form && (
          <div className="detail-main">
            {dr.confirming && dr.result?.impact && (
              <ImpactConfirm impact={dr.result.impact}
                             onConfirm={() => { dr.setConfirming(false); void dr.commit(done); }}
                             onCancel={() => dr.setConfirming(false)} />
            )}
            {gate && (
              <ImpactConfirm impact={gate.impact}
                             onConfirm={() => { const g = gate; setGate(null); void g.run(); }}
                             onCancel={() => setGate(null)} />
            )}
            {gateError.map((e, i) => <div key={i} className="banner">{e}</div>)}
            <ErrorList result={dr.result} />
            {form.isNew ? (
              <label>Sheet type id
                <input value={form.tid}
                       onChange={(e) => setForm({ ...form, tid: e.target.value })} />
              </label>
            ) : (
              <div className="chips">
                <span className="chip on">{form.tid}</span>
                <RenamePrompt disabled={dirty} onRename={renameType} />
              </div>
            )}
            <label>Label
              <input value={form.label}
                     onChange={(e) => setForm({ ...form, label: e.target.value })} />
            </label>
            <label>Kind
              <select value={form.kind}
                      onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                {SHEET_KINDS.map((k) => <option key={k}>{k}</option>)}
              </select>
            </label>
            <div className="chips">
              {Object.keys(groups).map((gid) => (
                <label key={gid}>
                  {groups[gid]?.label ?? gid}
                  <input type="checkbox" checked={form.groups.includes(gid)}
                         onChange={() => toggleGroup(gid)} />
                </label>
              ))}
            </div>
            <FieldRows fields={form.fields}
                       setFields={(fields) => setForm({ ...form, fields })}
                       existingKeys={new Set((types[form.tid]?.fields ?? []).map((f) => f.key))}
                       dirty={dirty} onRename={rename("field")} />
            <DerivedRows derived={form.derived}
                         setDerived={(derived) => setForm({ ...form, derived })}
                         existing={new Set(Object.keys(types[form.tid]?.derived ?? {}))}
                         dirty={dirty} onRename={rename("derived")}
                         sample={dr.result?.sample?.[form.tid]?.derived} />

            <div className="side-section">
              <h4>Creation</h4>
              {form.groups.map((gid) => (
                <div className="chips" key={gid}>
                  <span>{groups[gid]?.label ?? gid}</span>
                  <label>Budget
                    <input aria-label={`Budget ${gid}`}
                           value={form.creation[gid]?.budget ?? ""}
                           onChange={(e) => setForm({ ...form, creation: {
                             ...form.creation,
                             [gid]: { budget: e.target.value, costs: form.creation[gid]?.costs ?? {} },
                           } })} />
                  </label>
                  {(groups[gid]?.fields ?? []).map((gf) => (
                    <label key={gf.key}>{gf.label ?? gf.key}
                      <input aria-label={`Cost ${gid} ${gf.key}`} type="number"
                             value={form.creation[gid]?.costs[gf.key] ?? ""}
                             onChange={(e) => setForm({ ...form, creation: {
                               ...form.creation,
                               [gid]: {
                                 budget: form.creation[gid]?.budget ?? "",
                                 costs: { ...(form.creation[gid]?.costs ?? {}), [gf.key]: e.target.value },
                               },
                             } })} />
                    </label>
                  ))}
                </div>
              ))}
            </div>

            <div className="side-section">
              <h4>Advancement</h4>
              <label>Pool
                <select aria-label="Advancement pool" value={form.advancement.pool}
                        onChange={(e) => setForm({ ...form,
                          advancement: { ...form.advancement, pool: e.target.value } })}>
                  <option value="">(none)</option>
                  {resourceFields.map((rf) => <option key={rf.key} value={rf.key}>{rf.label ?? rf.key}</option>)}
                </select>
              </label>
              {form.advancement.pool && (
                <div>
                  {Object.entries(form.advancement.costs).map(([field, expr]) => (
                    <div className="chips" key={field}>
                      <select aria-label="Cost field" value={field}
                              onChange={(e) => {
                                const costs = { ...form.advancement.costs };
                                delete costs[field];
                                costs[e.target.value] = expr;
                                setForm({ ...form, advancement: { ...form.advancement, costs } });
                              }}>
                        {poolFields.map((pf) => <option key={pf.key} value={pf.key}>{pf.label ?? pf.key}</option>)}
                      </select>
                      <input aria-label="Cost expression" value={expr}
                             onChange={(e) => setForm({ ...form, advancement: {
                               ...form.advancement,
                               costs: { ...form.advancement.costs, [field]: e.target.value },
                             } })} />
                      <button onClick={() => {
                        const costs = { ...form.advancement.costs };
                        delete costs[field];
                        setForm({ ...form, advancement: { ...form.advancement, costs } });
                      }}>Remove</button>
                    </div>
                  ))}
                  <button onClick={() => setForm({ ...form, advancement: {
                    ...form.advancement,
                    costs: { ...form.advancement.costs, [poolFields[0]?.key ?? ""]: "" },
                  } })}>+ Add cost</button>
                </div>
              )}
            </div>

            {!gate && !dr.confirming && (
              <div className="form-actions">
                <button className="primary" onClick={() => dr.requestSave(done)}>Save</button>
                <button onClick={() => { setMode("view"); setForm(null); }}>Cancel</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
