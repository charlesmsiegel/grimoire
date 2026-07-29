import { useCallback, useEffect, useRef, useState } from "react";
import { type ModuleEditResult } from "../api/client";

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
