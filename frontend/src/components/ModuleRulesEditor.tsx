import { useMemo, useState } from "react";
import { api, type ModuleDetail, type ModuleEditResult } from "../api/client";
import { ErrorList, ImpactConfirm, useModuleDryRun, type SaveFn } from "./ModuleEditor";
import { RenamePrompt } from "./ModuleSchemaEditor";

type OutcomeRow = { label: string; when: string };

function OutcomeRows({ outcomes, setOutcomes }: {
  outcomes: OutcomeRow[]; setOutcomes: (o: OutcomeRow[]) => void;
}) {
  const upd = (i: number, patch: Partial<OutcomeRow>) =>
    setOutcomes(outcomes.map((o, j) => (j === i ? { ...o, ...patch } : o)));
  return (
    <div className="side-section">
      <h4>Outcomes</h4>
      {outcomes.map((o, i) => (
        <div className="chips" key={i}>
          <label>Outcome label
            <input value={o.label} onChange={(e) => upd(i, { label: e.target.value })} />
          </label>
          <label>Outcome when
            <input value={o.when} onChange={(e) => upd(i, { when: e.target.value })} />
          </label>
          <button onClick={() => setOutcomes(outcomes.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button onClick={() => setOutcomes([...outcomes, { label: "", when: "" }])}>
        + Add outcome
      </button>
    </div>
  );
}

type CheckForm = {
  cid: string; isNew: boolean; isDefaults: boolean;
  label: string; roll: string; requires: string[]; rules: string[];
  difficulty: string; outcomes: OutcomeRow[];
};

const emptyResult = async (): Promise<ModuleEditResult> =>
  ({ ok: true, errors: [], display_errors: [] });

export function ChecksSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const checks = pack.checks;
  const groups = pack.sheets.groups;
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [form, setForm] = useState<CheckForm | null>(null);

  const seed = (cid: string): CheckForm => {
    if (cid === "_defaults") {
      const d = checks._defaults ?? {};
      return { cid, isNew: false, isDefaults: true, label: "", roll: "",
               requires: [], rules: [],
               difficulty: d.difficulty !== undefined ? String(d.difficulty) : "",
               outcomes: (d.outcomes ?? []).map((o) => ({ ...o })) };
    }
    const c = checks[cid];
    return { cid, isNew: false, isDefaults: false,
             label: c?.label ?? cid, roll: c?.roll ?? "",
             requires: [...(c?.requires ?? [])], rules: [...(c?.rules ?? [])],
             difficulty: c?.difficulty !== undefined ? String(c.difficulty) : "",
             outcomes: (c?.outcomes ?? []).map((o) => ({ ...o })) };
  };
  const baseline = useMemo(
    () => (form && !form.isNew ? JSON.stringify(seed(form.cid)) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form?.cid, pack]);
  const dirty = form != null && baseline != null && JSON.stringify(form) !== baseline;

  const save: SaveFn = (dryRun) => {
    const f = form!;
    if (f.isDefaults) {
      const body: Record<string, unknown> = {};
      if (f.difficulty !== "") body.difficulty = parseInt(f.difficulty, 10);
      if (f.outcomes.length) body.outcomes = f.outcomes;
      return api.putModuleCheckDefaults(pack.id, body, dryRun);
    }
    const def: Record<string, unknown> = { label: f.label, roll: f.roll };
    if (f.requires.length) def.requires = f.requires;
    if (f.rules.length) def.rules = f.rules;
    if (f.difficulty !== "") def.difficulty = parseInt(f.difficulty, 10);
    if (f.outcomes.length) def.outcomes = f.outcomes;
    return api.putModuleCheck(pack.id, f.cid, def, dryRun);
  };
  const dr = useModuleDryRun(form ? save : emptyResult, [form]);
  const done = () => { setMode("view"); setForm(null); void reload(); };

  // impact gate shared by renames and deletes: dry-run first; confirm when
  // any impact count is nonzero; Cancel sends nothing.
  const [gate, setGate] = useState<{ impact: NonNullable<ModuleEditResult["impact"]>;
                                     run: () => Promise<unknown> } | null>(null);
  const [gateError, setGateError] = useState<string[]>([]);
  const confirmGate = (dryCall: () => Promise<ModuleEditResult>,
                       realCall: () => Promise<ModuleEditResult>,
                       onSuccess: () => void) =>
    dryCall().then((r) => {
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
  const renameCheck = (to: string) =>
    void confirmGate(
      () => api.renameModulePart(pack.id, "check", { from: form!.cid }, to, true),
      () => api.renameModulePart(pack.id, "check", { from: form!.cid }, to, false),
      () => { setForm((f) => (f ? { ...f, cid: to } : f)); setSelected(to); void reload(); });

  const toggleRequires = (gid: string) => {
    if (!form) return;
    const has = form.requires.includes(gid);
    setForm({ ...form, requires: has ? form.requires.filter((g) => g !== gid) : [...form.requires, gid] });
  };
  const toggleRuleDoc = (rid: string) => {
    if (!form) return;
    const has = form.rules.includes(rid);
    setForm({ ...form, rules: has ? form.rules.filter((r) => r !== rid) : [...form.rules, rid] });
  };

  const otherIds = Object.keys(checks).filter((c) => c !== "_defaults");

  return (
    <div className="editor">
      <div className="editor-list">
        <button className={"row" + (selected === "_defaults" ? " active" : "")}
                onClick={() => { setSelected("_defaults"); setMode("view"); setForm(null); }}>
          Defaults
        </button>
        <button className="row" onClick={() => {
          setSelected(null);
          setForm({ cid: "", isNew: true, isDefaults: false, label: "", roll: "",
                     requires: [], rules: [], difficulty: "", outcomes: [] });
          setMode("edit");
        }}>+ New check</button>
        {otherIds.map((cid) => (
          <button key={cid} className={"row" + (selected === cid ? " active" : "")}
                  onClick={() => { setSelected(cid); setMode("view"); setForm(null); }}>
            {checks[cid]?.label ?? cid}
          </button>
        ))}
      </div>
      <div className="editor-body">
        {mode === "view" && selected === "_defaults" && (
          <div className="detail-view">
            <div className="detail-main">
              <h3>Defaults</h3>
              <div className="chips">
                {checks._defaults?.difficulty !== undefined && (
                  <span className="chip on">difficulty {checks._defaults.difficulty}</span>
                )}
                {(checks._defaults?.outcomes ?? []).map((o, i) => (
                  <span key={i} className="chip">{o.label}</span>
                ))}
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button onClick={() => { setForm(seed("_defaults")); setMode("edit"); }}>Edit</button>
              </div>
            </aside>
          </div>
        )}
        {mode === "view" && selected && selected !== "_defaults" && (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{checks[selected]?.label ?? selected}</h3>
              <p className="field-hint">{checks[selected]?.roll}</p>
              <div className="chips">
                {(checks[selected]?.requires ?? []).map((r) => (
                  <span key={r} className="chip">{groups[r]?.label ?? r}</span>
                ))}
                {(checks[selected]?.rules ?? []).map((r) => (
                  <span key={r} className="chip on">{r}</span>
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
                <button onClick={() => { setForm(seed(selected)); setMode("edit"); }}>Edit</button>
                <button onClick={() => void confirmGate(
                  () => api.deleteModuleCheck(pack.id, selected, true),
                  () => api.deleteModuleCheck(pack.id, selected, false),
                  done)}>Delete</button>
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
            {!form.isDefaults && form.isNew && (
              <label>Check id
                <input value={form.cid}
                       onChange={(e) => setForm({ ...form, cid: e.target.value })} />
              </label>
            )}
            {!form.isDefaults && !form.isNew && (
              <div className="chips">
                <span className="chip on">{form.cid}</span>
                <RenamePrompt disabled={dirty} onRename={renameCheck} />
              </div>
            )}
            {!form.isDefaults && (
              <>
                <label>Label
                  <input value={form.label}
                         onChange={(e) => setForm({ ...form, label: e.target.value })} />
                </label>
                <label>Roll
                  <input value={form.roll}
                         onChange={(e) => setForm({ ...form, roll: e.target.value })} />
                </label>
                <div className="chips">
                  {Object.keys(groups).map((gid) => (
                    <label key={gid}>{groups[gid]?.label ?? gid}
                      <input type="checkbox" checked={form.requires.includes(gid)}
                             onChange={() => toggleRequires(gid)} />
                    </label>
                  ))}
                </div>
                <div className="chips">
                  {pack.rules.map((r) => (
                    <label key={r.id}>{r.id}
                      <input type="checkbox" checked={form.rules.includes(r.id)}
                             onChange={() => toggleRuleDoc(r.id)} />
                    </label>
                  ))}
                </div>
              </>
            )}
            <label>Difficulty
              <input type="number" value={form.difficulty}
                     onChange={(e) => setForm({ ...form, difficulty: e.target.value })} />
            </label>
            <OutcomeRows outcomes={form.outcomes}
                         setOutcomes={(outcomes) => setForm({ ...form, outcomes })} />
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

type RuleForm = { slug: string; isNew: boolean; keys: string; always: boolean; onRoll: boolean;
                  sheetTypes: string[]; body: string };

export function RulesSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const rules = pack.rules;
  const types = pack.sheets.sheet_types;
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [form, setForm] = useState<RuleForm | null>(null);
  const [bodyBaseline, setBodyBaseline] = useState("");

  const ruleShell = (slug: string): RuleForm => {
    const r = rules.find((x) => x.id === slug);
    return { slug, isNew: false, keys: (r?.keys ?? []).join(", "),
             always: r?.always ?? false, onRoll: r?.on_roll ?? false,
             sheetTypes: [...(r?.sheet_types ?? [])], body: "" };
  };
  const baseline = useMemo(
    () => (form && !form.isNew ? JSON.stringify({ ...ruleShell(form.slug), body: bodyBaseline }) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form?.slug, pack, bodyBaseline]);
  const dirty = form != null && baseline != null && JSON.stringify(form) !== baseline;

  const save: SaveFn = (dryRun) =>
    api.putModuleRule(pack.id, form!.slug, {
      always: form!.always, on_roll: form!.onRoll,
      keys: form!.keys.split(",").map((k) => k.trim()).filter(Boolean),
      sheet_types: form!.sheetTypes,
    }, form!.body, dryRun);
  const dr = useModuleDryRun(form ? save : emptyResult, [form]);
  const done = () => { setMode("view"); setForm(null); void reload(); };

  const [gate, setGate] = useState<{ impact: NonNullable<ModuleEditResult["impact"]>;
                                     run: () => Promise<unknown> } | null>(null);
  const [gateError, setGateError] = useState<string[]>([]);
  const confirmGate = (dryCall: () => Promise<ModuleEditResult>,
                       realCall: () => Promise<ModuleEditResult>,
                       onSuccess: () => void) =>
    dryCall().then((r) => {
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
  const renameRule = (to: string) =>
    void confirmGate(
      () => api.renameModulePart(pack.id, "rule", { from: form!.slug }, to, true),
      () => api.renameModulePart(pack.id, "rule", { from: form!.slug }, to, false),
      () => { setForm((f) => (f ? { ...f, slug: to } : f)); setSelected(to); void reload(); });

  const toggleSheetType = (tid: string) => {
    if (!form) return;
    const has = form.sheetTypes.includes(tid);
    setForm({ ...form, sheetTypes: has ? form.sheetTypes.filter((t) => t !== tid) : [...form.sheetTypes, tid] });
  };

  // Rule bodies aren't in `pack.rules` (frontmatter only) — fetch on Edit and
  // merge into the form once resolved, guarding against a stale response
  // landing after the user has switched to a different row.
  const openEdit = (slug: string) => {
    setForm(ruleShell(slug));
    setBodyBaseline("");
    setMode("edit");
    void api.readModuleRule(pack.id, slug).then((r) => {
      setBodyBaseline(r.body);
      setForm((f) => (f && f.slug === slug ? { ...f, body: r.body } : f));
    });
  };

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="row" onClick={() => {
          setSelected(null);
          setForm({ slug: "", isNew: true, keys: "", always: false, onRoll: false,
                     sheetTypes: [], body: "" });
          setBodyBaseline("");
          setMode("edit");
        }}>+ New rule</button>
        {rules.map((r) => (
          <button key={r.id} className={"row" + (selected === r.id ? " active" : "")}
                  onClick={() => { setSelected(r.id); setMode("view"); setForm(null); }}>
            {r.id}
          </button>
        ))}
      </div>
      <div className="editor-body">
        {mode === "view" && selected && (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{selected}</h3>
              <div className="chips">
                {rules.find((r) => r.id === selected)?.always && <span className="chip on">always</span>}
                {rules.find((r) => r.id === selected)?.on_roll && <span className="chip on">on_roll</span>}
                {(rules.find((r) => r.id === selected)?.keys ?? []).map((k) => (
                  <span key={k} className="chip">{k}</span>
                ))}
                {(rules.find((r) => r.id === selected)?.sheet_types ?? []).map((t) => (
                  <span key={t} className="chip">{types[t]?.label ?? t}</span>
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
                <button onClick={() => openEdit(selected)}>Edit</button>
                <button onClick={() => void confirmGate(
                  () => api.deleteModuleRule(pack.id, selected, true),
                  () => api.deleteModuleRule(pack.id, selected, false),
                  done)}>Delete</button>
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
              <label>Rule slug
                <input value={form.slug}
                       onChange={(e) => setForm({ ...form, slug: e.target.value })} />
              </label>
            ) : (
              <div className="chips">
                <span className="chip on">{form.slug}</span>
                <RenamePrompt disabled={dirty} onRename={renameRule} />
              </div>
            )}
            <label>Body
              <textarea value={form.body}
                        onChange={(e) => setForm({ ...form, body: e.target.value })} />
            </label>
            <div className="chips">
              <label>Always
                <input type="checkbox" checked={form.always}
                       onChange={() => setForm({ ...form, always: !form.always })} />
              </label>
              <label>On roll
                <input type="checkbox" checked={form.onRoll}
                       onChange={() => setForm({ ...form, onRoll: !form.onRoll })} />
              </label>
            </div>
            <label>Keys
              <input value={form.keys}
                     onChange={(e) => setForm({ ...form, keys: e.target.value })} />
            </label>
            <div className="chips">
              {Object.keys(types).map((tid) => (
                <label key={tid}>{types[tid]?.label ?? tid}
                  <input type="checkbox" checked={form.sheetTypes.includes(tid)}
                         onChange={() => toggleSheetType(tid)} />
                </label>
              ))}
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
