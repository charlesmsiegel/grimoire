import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ModuleDetail, type ModuleEditResult } from "../api/client";
import { Field } from "./Field";
import { GroupsSection, SheetTypesSection } from "./ModuleSchemaEditor";
import { ChecksSection, RulesSection } from "./ModuleRulesEditor";
import { ContentSection } from "./ModuleContentEditor";
import { LayoutSection, ThemeSection } from "./ModuleDisplayEditor";

const SECTIONS = ["Manifest", "Groups", "Sheet types", "Checks", "Rules",
                  "Content", "Layout", "Theme"] as const;
type Section = (typeof SECTIONS)[number];

export type SaveFn = (dryRun: boolean) => Promise<ModuleEditResult>;

/**
 * Shared dry-run/save harness for every module-editor section.
 *
 * `check` (the debounce effect below) re-validates 500ms after the caller's
 * `deps` change, purely to surface live errors/impact as the form is edited.
 * `requestSave` is a distinct path: it cancels that debounce and runs its
 * OWN fresh dry-run of the current form — the impact decision must never be
 * made from a stale debounced `result`. A revision counter discards any
 * debounced response that resolves after a newer check/save superseded it.
 */
export function useModuleDryRun(save: SaveFn, deps: unknown[]) {
  const [result, setResult] = useState<ModuleEditResult | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const revision = useRef(0);

  useEffect(() => {
    const rev = ++revision.current;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      save(true).then((r) => {
        if (revision.current === rev) setResult(r);
      }).catch(() => {});
    }, 500);
    return () => clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const impactful = (i?: ModuleEditResult["impact"]) =>
    !!i && i.sheets_migrated + i.sheets_newly_invalid + i.dangling_refs > 0;

  const commit = useCallback(async (onOk: (r: ModuleEditResult) => void) => {
    setSaving(true);
    try {
      const r = await save(false);
      setResult(r);
      if (r.ok) onOk(r);
      return r;
    } finally {
      setSaving(false);
    }
  }, [save]);

  const requestSave = useCallback(async (onOk: (r: ModuleEditResult) => void) => {
    clearTimeout(timer.current);           // never decide from a stale debounce
    const rev = ++revision.current;        // invalidate any in-flight debounced check
    setSaving(true);
    let fresh: ModuleEditResult;
    try {
      fresh = await save(true);            // fresh dry-run of the CURRENT form
    } catch {
      setSaving(false);
      return;
    }
    // Superseded meanwhile: the debounced effect re-armed on a form edit that
    // happened while this fresh dry-run was in flight. Silently dropping the
    // Save click here is intentional and safe — the click targeted a form
    // state that's no longer current, so committing it would save stale
    // data; the user can just press Save again for the edited form.
    if (revision.current !== rev) { setSaving(false); return; }
    setResult(fresh);
    if (!fresh.ok) { setSaving(false); return; } // invalid draft: show errors, never commit
    if (impactful(fresh.impact)) {
      setSaving(false);
      setConfirming(true);
    } else {
      await commit(onOk);
    }
  }, [save, commit]);

  // Called from a rename success handler: the last debounced dry-run result
  // was computed against the PRE-rename form text (e.g. a derived expression
  // still reading the old field name) and would otherwise keep rendering a
  // now-stale error until the next edit re-arms the debounce. Bumping the
  // revision counter also drops any debounced response still in flight from
  // before the rename.
  const reset = useCallback(() => {
    clearTimeout(timer.current);
    ++revision.current;
    setResult(null);
  }, []);

  return { result, confirming, setConfirming, saving, commit, requestSave, reset };
}

export function ErrorList({ result }: { result: ModuleEditResult | null }) {
  if (!result) return null;
  return (
    <>
      {result.errors.map((e, i) => <div key={i} className="banner">{e}</div>)}
      {result.display_errors.map((e, i) => (
        <div key={`d${i}`} className="field-hint">{e.message}</div>
      ))}
    </>
  );
}

export function ImpactConfirm({ impact, onConfirm, onCancel }: {
  impact: NonNullable<ModuleEditResult["impact"]>;
  onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <div className="banner">
      used by {impact.sheet_types.length} sheet types · migrates{" "}
      {impact.sheets_migrated} sheets · {impact.sheets_newly_invalid} sheets
      become invalid · {impact.dangling_refs} refs go dangling
      <div className="form-actions">
        <button className="primary" onClick={onConfirm}>Confirm</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function ModuleEditor({ detail, onDone }: {
  detail: ModuleDetail; onDone: () => void;
}) {
  const [pack, setPack] = useState(detail);
  const [section, setSection] = useState<Section>("Manifest");
  const reload = useCallback(
    () => api.readModule(pack.id).then(setPack), [pack.id]);
  return (
    <div className="module-editor">
      <div className="chips">
        {SECTIONS.map((s) => (
          <button key={s}
                  className={"chip" + (s === section ? " on" : "")}
                  onClick={() => setSection(s)}>{s}</button>
        ))}
        <button className="chip" onClick={onDone}>Done</button>
      </div>
      {section === "Manifest" && <ManifestSection pack={pack} reload={reload} />}
      {section === "Groups" && <GroupsSection pack={pack} reload={reload} />}
      {section === "Sheet types" && <SheetTypesSection pack={pack} reload={reload} />}
      {section === "Checks" && <ChecksSection pack={pack} reload={reload} />}
      {section === "Rules" && <RulesSection pack={pack} reload={reload} />}
      {section === "Content" && <ContentSection pack={pack} reload={reload} />}
      {section === "Layout" && <LayoutSection pack={pack} reload={reload} />}
      {section === "Theme" && <ThemeSection pack={pack} reload={reload} />}
    </div>
  );
}

function ManifestSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const m = pack.manifest;
  const [form, setForm] = useState({
    name: m.name ?? "", description: m.description ?? "",
    version: m.version ?? "", dice: m.dice ?? "", notes: m.notes ?? "",
  });
  const save: SaveFn = (dryRun) =>
    api.putModuleManifest(pack.id, { ...form, dry_run: dryRun });
  const dr = useModuleDryRun(save, [form]);
  return (
    <div className="detail-main">
      {dr.confirming && dr.result?.impact && (
        <ImpactConfirm impact={dr.result.impact}
                       onConfirm={() => { dr.setConfirming(false); void dr.commit(() => void reload()); }}
                       onCancel={() => dr.setConfirming(false)} />
      )}
      <ErrorList result={dr.result} />
      <Field label="Name">
        <input value={form.name}
               onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </Field>
      <Field label="Description">
        <input value={form.description}
               onChange={(e) => setForm({ ...form, description: e.target.value })} />
      </Field>
      <Field label="Version">
        <input value={form.version}
               onChange={(e) => setForm({ ...form, version: e.target.value })} />
      </Field>
      <Field label="Dice">
        <input value={form.dice}
               onChange={(e) => setForm({ ...form, dice: e.target.value })} />
      </Field>
      <Field label="Notes">
        <textarea value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={dr.saving}
                onClick={() => void dr.requestSave(() => void reload())}>Save</button>
      </div>
    </div>
  );
}
