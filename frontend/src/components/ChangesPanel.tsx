import { useEffect, useState } from "react";
import { api, type RecordChange } from "../api/client";

const GROUPS: { kind: string; label: string }[] = [
  { kind: "characters", label: "Characters" },
  { kind: "lore", label: "Lore" },
  { kind: "locations", label: "Locations" },
];

export function ChangesPanel({ cid }: { cid: string }) {
  const [rows, setRows] = useState<RecordChange[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => {
    api.campaignChanges(cid).then(setRows).catch(() => setRows([]));
  }, [cid]);

  if (rows === null) return <div className="changes-panel">Loading…</div>;
  if (rows.length === 0)
    return <div className="changes-panel"><p className="field-hint">No record changes yet.</p></div>;

  const active = rows.find((r) => `${r.ref.kind}/${r.ref.id}` === sel) ?? null;

  return (
    <div className="changes-panel editor">
      <div className="editor-list">
        {GROUPS.map((g) => {
          const group = rows.filter((r) => r.ref.kind === g.kind);
          if (!group.length) return null;
          return (
            <div key={g.kind} className="side-section">
              <h4>{g.label}</h4>
              {group.map((r) => {
                const key = `${r.ref.kind}/${r.ref.id}`;
                return (
                  <button key={key} className={"row" + (key === sel ? " active" : "")}
                          onClick={() => setSel(key)}>
                    {r.name}
                    <span className="field-hint"> · changed in {r.scene.title}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
      <div className="editor-body">
        {active ? (
          <div className="detail-view">
            <h3>{active.name}</h3>
            {active.fields.map((f) => (
              <div key={f.field} className="side-section">
                <h4>{f.label}</h4>
                <pre className="record-diff">
                  {f.diff.map((d, i) => (
                    <div key={i} className={"diff-line diff-" + d.op}>{d.text}</div>
                  ))}
                </pre>
              </div>
            ))}
          </div>
        ) : (
          <p className="field-hint">Select a record to see what changed.</p>
        )}
      </div>
    </div>
  );
}
