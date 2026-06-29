import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type EntityKind, type EntitySummary } from "../api/client";
import { Field } from "./Field";

export function EntityEditor({ wid, kind }: { wid: string; kind: EntityKind }) {
  const scope = { kind: "world" as const, id: wid };
  const [items, setItems] = useState<EntitySummary[]>([]);
  const [editing, setEditing] = useState<string | null>(null); // entity id, or null = new
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [keys, setKeys] = useState("");
  const [mode, setMode] = useState<"view" | "edit">("edit"); // existing entries open read-only
  const [error, setError] = useState<string | null>(null);
  const label = kind === "lore" ? "lore entry" : "location";

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
    setMode("edit"); // a brand-new entry goes straight to the form
  }

  async function select(id: string) {
    setError(null);
    const e = await api.readEntity(scope, kind, id);
    setEditing(id);
    setName(e.meta.name);
    setBody(e.body);
    setKeys(e.meta.keys ?? "");
    setMode("view");
  }

  async function save() {
    if (!name.trim()) return;
    setError(null);
    try {
      if (editing) {
        await api.updateEntity(scope, kind, editing, { name, body, keys });
        await reload();
        await select(editing); // back to the read-only view
      } else {
        await api.createEntity(scope, kind, { name, body, keys });
        await reload();
        resetForm();
      }
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

  const keyList = keys.split(",").map((k) => k.trim()).filter(Boolean);

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New {label}</button>
        {items.map((e) => (
          <button key={e.id} className={"row" + (editing === e.id ? " active" : "")} onClick={() => select(e.id)}>
            {e.name}
          </button>
        ))}
        {items.length === 0 && <div className="editor-empty">No {kind} yet.</div>}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" && editing ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{name}</h3>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              <div className="side-section">
                <h4>Keys</h4>
                {keyList.length > 0
                  ? <div className="chips">{keyList.map((k) => <span key={k} className="chip on">{k}</span>)}</div>
                  : <div className="field-hint">always-on</div>}
              </div>
            </aside>
          </div>
        ) : (
          <div className="form">
            <h3>{editing ? `Edit ${label}` : `New ${label}`}</h3>
            <Field label="Name">
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Body">
              <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={10} />
            </Field>
            <Field label="Keys" hint="comma-separated activation triggers; blank = always-on">
              <input type="text" value={keys} onChange={(e) => setKeys(e.target.value)} />
            </Field>
            <div className="form-actions">
              {editing && <button className="subtle" onClick={() => remove(items.find((x) => x.id === editing)!)}>Delete</button>}
              {editing && <button className="subtle" onClick={() => select(editing)}>Cancel</button>}
              <button className="primary" onClick={save} disabled={!name.trim()}>
                {editing ? "Save" : `Create ${label}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
