import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { EditableRow } from "./EditableRow";

export function TagEditor({ wid }: { wid: string }) {
  const [tags, setTags] = useState<Record<string, string>>({});
  const [name, setName] = useState("");

  const reload = useCallback(() => api.listTags(wid).then(setTags), [wid]);
  useEffect(() => {
    reload();
  }, [reload]);

  async function add() {
    const trimmed = name.trim();
    if (!trimmed) return;
    await api.addTag(wid, trimmed);
    setName("");
    await reload();
  }

  async function rename(tid: string, next: string) {
    await api.renameTag(wid, tid, next);
    await reload();
  }

  async function remove(tid: string, display: string) {
    if (!window.confirm(`Delete tag '${display}'? PCs already carrying it keep it as a dangling id.`)) return;
    await api.deleteTag(wid, tid);
    await reload();
  }

  const ids = Object.keys(tags).sort();

  return (
    <div>
      <div className="picker">
        <input placeholder="Tag name…" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="primary" onClick={add} disabled={!name.trim()}>Add tag</button>
      </div>
      <div className="list">
        {ids.map((tid) => (
          <EditableRow
            key={tid}
            label={tags[tid]}
            subtitle={tid}
            onRename={(next) => rename(tid, next)}
            onDelete={() => remove(tid, tags[tid])}
          />
        ))}
        {ids.length === 0 && <div className="editor-empty">No tags yet.</div>}
      </div>
    </div>
  );
}
