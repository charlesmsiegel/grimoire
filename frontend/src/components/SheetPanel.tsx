import { useEffect, useState } from "react";
import { api, type EntityScope, type ModuleDetail, type Sheet } from "../api/client";
import SheetEditor, { typeKind } from "./SheetEditor";
import { assembledDefs } from "./SheetLayout";
import { isResource } from "./SheetWidgets";

export default function SheetPanel({ scope, module, kind, eid, onOpenRef, onChanged }:
  { scope: EntityScope; module: ModuleDetail | null; kind: string; eid: string;
    onOpenRef?: (kind: string, id: string) => void;
    /** Fired once a create, save or delete has landed AND been re-read, for a
     *  host that renders something else about this sheet's existence.
     *
     *  The sheets room is why it exists: it shows the whole cast beside this
     *  panel with a has-sheet badge per member, so creating a sheet here left
     *  the rail one metre away still saying "Missing" until the page was
     *  navigated off and back. Optional -- every other caller predates it and
     *  displays nothing that a sheet write can falsify. */
    onChanged?: () => void }) {
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [choice, setChoice] = useState("");
  const [editing, setEditing] = useState(false);

  const types = module
    ? Object.entries(module.sheets.sheet_types).filter(([, st]) => st.kind === typeKind(kind))
    : [];
  const hasType = types.length > 0;
  const choiceValue = choice || (types.length === 1 ? types[0][0] : "");

  useEffect(() => {
    if (!module || !hasType) return;
    setLoaded(false);
    setError(null);
    setEditing(false);
    setChoice("");
    api.getSheet(scope, module.id, kind, eid)
      .then(({ sheet }) => { setSheet(sheet); setLoaded(true); })
      .catch((err: any) => { setError(err?.detail ?? String(err)); setLoaded(true); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.id, module?.id, kind, eid]);

  if (!module || !hasType) return null;

  async function refetch(): Promise<Sheet | null> {
    setError(null);
    try {
      const { sheet: fresh } = await api.getSheet(scope, module!.id, kind, eid);
      setSheet(fresh);
      // After the re-read, not before: a host that reacts by re-reading its own
      // view of this sheet would otherwise race the read we are already making.
      onChanged?.();
      return fresh;
    } catch (err: any) {
      setError(err?.detail ?? String(err));
      return null;
    }
  }

  async function createSheet() {
    if (!choiceValue) return;
    setError(null);
    try {
      await api.putSheet(scope, module!.id, kind, eid, { sheet_type: choiceValue, fields: null, expected: null });
      const fresh = await refetch();
      if (fresh) setEditing(true);
    } catch (err: any) {
      setError(err?.detail ?? String(err));
    }
  }

  if (error) {
    return (
      <div className="side-section">
        <h4>Sheet</h4>
        <div className="field-hint">{error}</div>
      </div>
    );
  }

  if (!loaded) return null;

  if (editing && sheet) {
    return (
      <SheetEditor scope={scope} module={module} kind={kind} eid={eid} initial={sheet}
                   onClose={() => setEditing(false)}
                   onSaved={() => { refetch(); }} onOpenRef={onOpenRef} />
    );
  }

  if (sheet === null) {
    return (
      <div className="side-section">
        <h4>Sheet</h4>
        <div className="field-hint">No sheet</div>
        <select aria-label="Sheet type" value={choiceValue} onChange={(e) => setChoice(e.target.value)}>
          <option value="" disabled>Select type…</option>
          {types.map(([tid, st]) => <option key={tid} value={tid}>{st.label}</option>)}
        </select>
        <button className="subtle" onClick={createSheet} disabled={!choiceValue}>Create</button>
      </div>
    );
  }

  const typeDef = sheet.sheet_type ? module.sheets.sheet_types[sheet.sheet_type] : undefined;
  const resourceFields = sheet.sheet_type
    ? assembledDefs(module, sheet.sheet_type).filter((f) => f.type === "resource")
    : [];
  const derivedEntries = Object.entries(sheet.derived);

  return (
    <div className="side-section">
      <h4>Sheet</h4>
      <div className="chips">
        <span className="chip on">{typeDef?.label ?? sheet.sheet_type}</span>
        {resourceFields.map((f) => {
          const v = sheet.fields[f.key];
          const rv = isResource(v) ? v : { current: 0, max: f.max ?? 0 };
          return <span className="chip on" key={f.key}>{f.key} {rv.current}/{rv.max}</span>;
        })}
        {derivedEntries.map(([k, v]) => <span className="chip" key={k}>{k} {String(v)}</span>)}
      </div>
      {sheet.errors.map((e, i) => <div className="field-hint" key={i}>{e}</div>)}
      <button className="subtle" onClick={() => setEditing(true)}>Open sheet</button>
    </div>
  );
}
