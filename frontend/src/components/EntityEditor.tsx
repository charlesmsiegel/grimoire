import { useCallback, useEffect, useState } from "react";
import { api, type EntityKind, type EntitySummary } from "../api/client";
import { EditableRow } from "./EditableRow";
import { Field } from "./Field";

export function EntityEditor({ wid, kind }: { wid: string; kind: EntityKind }) {
  const scope = { kind: "world" as const, id: wid };
  const [items, setItems] = useState<EntitySummary[]>([]);
  const [editing, setEditing] = useState<string | null>(null); // entity id, or null = new
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [keys, setKeys] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => api.listEntities(scope, kind).then(setItems), [wid, kind]);
  useEffect(() => {
    reload();
    resetForm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wid, kind]);

  function resetForm() {
    setEditing(null);
    setName("");
    setBody("");
    setKeys("");
  }

  async function select(id: string) {
    setError(null);
    const e = await api.readEntity(scope, kind, id);
    setEditing(id);
    setName(e.meta.name);
    setBody(e.body);
    setKeys(e.meta.keys ?? "");
  }

  async function save() {
    if (!name.trim()) return;
    setError(null);
    try {
      if (editing) await api.updateEntity(scope, kind, editing, { name, body, keys });
      else await api.createEntity(scope, kind, { name, body, keys });
      await reload();
      resetForm();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function remove(e: EntitySummary) {
    if (!window.confirm(`Delete '${e.name}'?`)) return;
    await api.deleteEntity(scope, kind, e.id);
    if (editing === e.id) resetForm();
    await reload();
  }

  const label = kind === "lore" ? "lore entry" : "location";

  return (
    <div>
      {error && <div className="banner">{error}</div>}
      <div className="list">
        {items.map((e) => (
          <EditableRow
            key={e.id}
            label={e.name}
            subtitle={e.keys ? `keys: ${e.keys}` : "always-on"}
            active={editing === e.id}
            onSelect={() => select(e.id)}
            onRename={(next) => api.updateEntity(scope, kind, e.id, { name: next }).then(reload)}
            onDelete={() => remove(e)}
          />
        ))}
        {items.length === 0 && <div className="editor-empty">No {kind} yet.</div>}
      </div>

      <div className="form">
        <h3>{editing ? `Edit ${label}` : `New ${label}`}</h3>
        <Field label="Name">
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Body">
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={6} />
        </Field>
        <Field label="Keys" hint="comma-separated activation triggers; blank = always-on">
          <input type="text" value={keys} onChange={(e) => setKeys(e.target.value)} />
        </Field>
        <div className="form-actions">
          {editing && <button className="subtle" onClick={resetForm}>New</button>}
          <button className="primary" onClick={save} disabled={!name.trim()}>
            {editing ? "Save" : `Create ${label}`}
          </button>
        </div>
      </div>
    </div>
  );
}
