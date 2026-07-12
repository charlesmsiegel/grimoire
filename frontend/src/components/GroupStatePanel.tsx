import { useEffect, useState } from "react";
import { api, type GroupState } from "../api/client";
import { Field } from "./Field";

const SECTIONS = [
  { key: "goals", label: "Goals" },
  { key: "resources", label: "Resources" },
  { key: "focus", label: "Focus" },
  { key: "public_perception", label: "Public perception" },
  { key: "secrets", label: "Secrets" },
] as const;

const EMPTY: Omit<GroupState, "updated"> = {
  goals: "", resources: "", focus: "", public_perception: "", secrets: "",
};

export function GroupStatePanel({ cid, gid }: { cid: string; gid: string }) {
  const [state, setState] = useState<Omit<GroupState, "updated">>(EMPTY);
  const [draft, setDraft] = useState<Omit<GroupState, "updated">>(EMPTY);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    api.getGroupState(cid, gid).then(({ updated: _u, ...s }) => { setState(s); setDraft(s); });
    setEditing(false);
  }, [cid, gid]);

  async function save() {
    await api.putGroupState(cid, gid, draft);
    setState(draft);
    setEditing(false);
  }

  const hasAny = SECTIONS.some((s) => state[s.key]);
  return (
    <div className="side-section">
      <h4>Campaign state</h4>
      {editing ? (
        <>
          {SECTIONS.map((s) => (
            <Field key={s.key} label={s.label}>
              <textarea rows={2} value={draft[s.key]}
                        onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })} />
            </Field>
          ))}
          <div className="form-actions">
            <button className="subtle" onClick={() => { setDraft(state); setEditing(false); }}>Cancel</button>
            <button className="primary" onClick={save}>Save state</button>
          </div>
        </>
      ) : (
        <>
          {hasAny ? SECTIONS.filter((s) => state[s.key]).map((s) => (
            <div key={s.key}>
              <div className="section-label">{s.label}</div>
              <div className="field-hint">{state[s.key]}</div>
            </div>
          )) : <div className="field-hint">No campaign state yet.</div>}
          <div className="form-actions">
            <button className="subtle" onClick={() => setEditing(true)}>Edit state</button>
          </div>
        </>
      )}
    </div>
  );
}
