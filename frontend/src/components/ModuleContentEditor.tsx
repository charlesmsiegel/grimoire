import { useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type ModuleContentEntry, type ModuleDetail, type ModuleEditResult, type ModuleField } from "../api/client";
import { ErrorList, ImpactConfirm, useModuleDryRun, type SaveFn } from "./ModuleEditor";
import { RenamePrompt } from "./ModuleSchemaEditor";

const CONTENT_KINDS = ["locations", "lore", "items", "groups", "creatures"];

// Keys `readModuleContent` always returns; anything else on the response is
// custom frontmatter (e.g. `rarity: legendary`) that must round-trip through
// the "Metadata" rows rather than being silently dropped on the next save.
const KNOWN_ENTRY_KEYS = new Set(["kind", "id", "name", "body", "keys", "sheet_type", "fields"]);

type ContentEntry = ModuleContentEntry & Record<string, unknown>;

function metaFromEntry(entry: ContentEntry): Record<string, string> {
  const meta: Record<string, string> = {};
  for (const [k, v] of Object.entries(entry)) {
    if (!KNOWN_ENTRY_KEYS.has(k)) meta[k] = typeof v === "string" ? v : String(v);
  }
  return meta;
}

function MetaRows({ meta, setMeta }: {
  meta: Record<string, string>; setMeta: (m: Record<string, string>) => void;
}) {
  const entries = Object.entries(meta);
  return (
    <div className="side-section">
      <h4>Metadata</h4>
      {entries.map(([k, v], i) => (
        <div className="chips" key={i}>
          <input aria-label="Metadata key" value={k}
                 onChange={(e) => {
                   const m: Record<string, string> = {};
                   for (const [ok, ov] of entries) m[ok === k ? e.target.value : ok] = ov;
                   setMeta(m);
                 }} />
          <input aria-label="Metadata value" value={v}
                 onChange={(e) => setMeta({ ...meta, [k]: e.target.value })} />
          <button onClick={() => {
            const m = { ...meta };
            delete m[k];
            setMeta(m);
          }}>Remove</button>
        </div>
      ))}
      <button onClick={() => setMeta({ ...meta, "": "" })}>+ Add</button>
    </div>
  );
}

function StatBlock({ pack, sheetType, setSheetType, fields, setFields, kind }: {
  pack: ModuleDetail; sheetType: string | null;
  setSheetType: (t: string | null) => void;
  fields: Record<string, unknown>; setFields: (f: Record<string, unknown>) => void;
  kind: string;
}) {
  const types = Object.entries(pack.sheets.sheet_types)
    .filter(([, st]) => st.kind === kind);
  const assembled: ModuleField[] = sheetType
    ? [...(pack.sheets.sheet_types[sheetType]?.groups ?? [])
         .flatMap((g) => pack.sheets.groups[g]?.fields ?? []),
       ...(pack.sheets.sheet_types[sheetType]?.fields ?? [])]
    : [];
  return (
    <div className="side-section">
      <h4>Stat block</h4>
      <label>Sheet type
        <select aria-label="Sheet type" value={sheetType ?? ""}
                onChange={(e) => setSheetType(e.target.value || null)}>
          <option value="">No stat block</option>
          {types.map(([tid, st]) => <option key={tid} value={tid}>{st.label}</option>)}
        </select>
      </label>
      {assembled.map((f) => {
        const v = fields[f.key];
        if (["number", "dots", "track"].includes(f.type)) {
          return (
            <label key={f.key}>{f.key}
              <input type="number" aria-label={f.key}
                     value={typeof v === "number" ? v : 0}
                     onChange={(e) => setFields({ ...fields, [f.key]: parseInt(e.target.value || "0", 10) })} />
            </label>
          );
        }
        if (f.type === "resource") {
          const r = (v ?? { current: f.max ?? 0, max: f.max ?? 0 }) as { current: number; max: number };
          return (
            <label key={f.key}>{f.key}
              <input type="number" aria-label={`${f.key} current`} value={r.current}
                     onChange={(e) => setFields({ ...fields, [f.key]: { ...r, current: parseInt(e.target.value || "0", 10) } })} />
              <input type="number" aria-label={`${f.key} max`} value={r.max}
                     onChange={(e) => setFields({ ...fields, [f.key]: { ...r, max: parseInt(e.target.value || "0", 10) } })} />
            </label>
          );
        }
        if (f.type === "list" || f.type === "ref") {
          return (
            <label key={f.key}>{f.key}
              <input aria-label={f.key}
                     value={Array.isArray(v) ? (v as string[]).join(", ") : ""}
                     onChange={(e) => setFields({ ...fields, [f.key]:
                       e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </label>
          );
        }
        return (
          <label key={f.key}>{f.key}
            <input aria-label={f.key} value={typeof v === "string" ? v : ""}
                   onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })} />
          </label>
        );
      })}
    </div>
  );
}

type ContentForm = {
  contentId: string; kind: string; isNew: boolean;
  name: string; keys: string; body: string;
  meta: Record<string, string>;
  sheetType: string | null; statFields: Record<string, unknown>;
};

const emptyResult = async (): Promise<ModuleEditResult> =>
  ({ ok: true, errors: [], display_errors: [] });

export function ContentSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const [selected, setSelected] = useState<{ kind: string; id: string } | null>(null);
  const [viewEntry, setViewEntry] = useState<ContentEntry | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [form, setForm] = useState<ContentForm | null>(null);
  const selectionRef = useRef(0);

  const seedForm = (entry: ContentEntry, kind: string, id: string): ContentForm => ({
    contentId: id, kind, isNew: false,
    name: entry.name, keys: entry.keys, body: entry.body,
    meta: metaFromEntry(entry),
    sheetType: entry.sheet_type, statFields: { ...(entry.fields ?? {}) },
  });

  // Loading a row (or a fresh Save/rename round-trip) fetches the full body +
  // stat block + custom frontmatter -- guarded by a revision counter so a
  // stale response from an abandoned selection can't clobber a newer one.
  const selectContent = (kind: string, id: string) => {
    const rev = ++selectionRef.current;
    setSelected({ kind, id });
    setMode("view");
    setForm(null);
    setGateError([]);
    setViewEntry(null);
    void api.readModuleContent(pack.id, kind, id).then((entry) => {
      if (selectionRef.current === rev) setViewEntry(entry as ContentEntry);
    });
  };

  const baseline = useMemo(
    () => (form && !form.isNew && viewEntry ? JSON.stringify(seedForm(viewEntry, form.kind, form.contentId)) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form?.contentId, form?.kind, viewEntry]);
  const dirty = form != null && baseline != null && JSON.stringify(form) !== baseline;

  const save: SaveFn = (dryRun) => {
    const f = form!;
    const sheet = f.sheetType ? { sheet_type: f.sheetType, fields: f.statFields } : null;
    return api.putModuleContent(pack.id, f.kind, f.contentId,
      { name: f.name, body: f.body, keys: f.keys, fields: f.meta, sheet }, dryRun);
  };
  const dr = useModuleDryRun(form ? save : emptyResult, [form]);
  const done = () => { const f = form!; void reload(); selectContent(f.kind, f.contentId); };
  // Delete removes the record the form/selection points at -- unlike `done`
  // (rename/save keep the same record selected) it must also clear
  // `selected`/`viewEntry`, otherwise the read-only view keeps rendering the
  // now-deleted record.
  const doneDelete = () => { setSelected(null); setViewEntry(null); setMode("view"); setForm(null); void reload(); };

  // impact gate shared by renames and deletes: dry-run first; confirm when
  // any impact count is nonzero (for content, that includes dangling_refs);
  // Cancel sends nothing.
  const [gate, setGate] = useState<{ impact: NonNullable<ModuleEditResult["impact"]>;
                                     run: () => Promise<unknown> } | null>(null);
  const [gateError, setGateError] = useState<string[]>([]);
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
  // A content-id rename keeps the form open, so the still-mounted form must
  // resync to the new id (and `selected`, so the rail highlight + any later
  // Save/Delete address the new id) rather than keep pointing at the old one.
  const renameContent = (to: string) =>
    void confirmGate(
      () => api.renameModulePart(pack.id, "content", { from: form!.contentId, kind: form!.kind }, to, true),
      () => api.renameModulePart(pack.id, "content", { from: form!.contentId, kind: form!.kind }, to, false),
      () => {
        setForm((f) => (f ? { ...f, contentId: to } : f));
        setSelected({ kind: form!.kind, id: to });
        void reload();
      });

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="row" onClick={() => {
          setSelected(null);
          setViewEntry(null);
          setGateError([]);
          setForm({ contentId: "", kind: CONTENT_KINDS[0], isNew: true,
                     name: "", keys: "", body: "", meta: {}, sheetType: null, statFields: {} });
          setMode("edit");
        }}>+ New content</button>
        {CONTENT_KINDS.map((kind) => {
          const rows = pack.content.filter((c) => c.kind === kind);
          if (!rows.length) return null;
          return (
            <div key={kind} className="rail-group">
              <div className="rail-group-head">{kind}</div>
              {rows.map((c) => (
                <button key={`${kind}-${c.id}`}
                        className={"row" + (selected?.kind === kind && selected?.id === c.id ? " active" : "")}
                        onClick={() => selectContent(kind, c.id)}>
                  {c.name}
                </button>
              ))}
            </div>
          );
        })}
      </div>
      <div className="editor-body">
        {mode === "view" && selected && viewEntry && (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{viewEntry.name}</h3>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{viewEntry.body}</Markdown>
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
                <button onClick={() => {
                  setGateError([]);
                  setForm(seedForm(viewEntry, selected.kind, selected.id));
                  setMode("edit");
                }}>Edit</button>
                <button onClick={() => void confirmGate(
                  () => api.deleteModuleContent(pack.id, selected.kind, selected.id, true),
                  () => api.deleteModuleContent(pack.id, selected.kind, selected.id, false),
                  doneDelete)}>Delete</button>
              </div>
              <div className="side-section">
                <h4>Keys</h4>
                {viewEntry.keys
                  ? <div className="chips">{viewEntry.keys.split(",").map((k) => k.trim()).filter(Boolean)
                      .map((k) => <span key={k} className="chip on">{k}</span>)}</div>
                  : <div className="field-hint">always-on</div>}
              </div>
              {viewEntry.sheet_type && (
                <div className="side-section">
                  <h4>Stat block</h4>
                  <div className="chips">
                    <span className="chip on">
                      {pack.sheets.sheet_types[viewEntry.sheet_type]?.label ?? viewEntry.sheet_type}
                    </span>
                    {Object.entries(viewEntry.fields).map(([k, v]) => (
                      <span key={k} className="chip">{k}: {JSON.stringify(v)}</span>
                    ))}
                  </div>
                </div>
              )}
              {Object.keys(metaFromEntry(viewEntry)).length > 0 && (
                <div className="side-section">
                  <h4>Metadata</h4>
                  <div className="chips">
                    {Object.entries(metaFromEntry(viewEntry)).map(([k, v]) => (
                      <span key={k} className="chip">{k}: {v}</span>
                    ))}
                  </div>
                </div>
              )}
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
              <>
                <label>Content id
                  <input value={form.contentId}
                         onChange={(e) => setForm({ ...form, contentId: e.target.value })} />
                </label>
                <label>Kind
                  <select value={form.kind}
                          onChange={(e) => setForm({ ...form, kind: e.target.value, sheetType: null, statFields: {} })}>
                    {CONTENT_KINDS.map((k) => <option key={k}>{k}</option>)}
                  </select>
                </label>
              </>
            ) : (
              <div className="chips">
                <span className="chip on">{form.contentId}</span>
                <RenamePrompt disabled={dirty} onRename={renameContent} />
              </div>
            )}
            <label>Name
              <input value={form.name}
                     onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label>Keys
              <input value={form.keys}
                     onChange={(e) => setForm({ ...form, keys: e.target.value })} />
            </label>
            <label>Body
              <textarea value={form.body}
                        onChange={(e) => setForm({ ...form, body: e.target.value })} />
            </label>
            <MetaRows meta={form.meta} setMeta={(meta) => setForm({ ...form, meta })} />
            <StatBlock pack={pack} kind={form.kind}
                       sheetType={form.sheetType} setSheetType={(t) => setForm({ ...form, sheetType: t })}
                       fields={form.statFields} setFields={(statFields) => setForm({ ...form, statFields })} />
            {/* hidden while a rename/delete or save confirmation banner is up --
                each banner owns its own Confirm/Cancel until resolved */}
            {!gate && !dr.confirming && (
              <div className="form-actions">
                <button className="primary" disabled={dr.saving} onClick={() => dr.requestSave(done)}>Save</button>
                <button onClick={() => { setMode("view"); setForm(null); }}>Cancel</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
