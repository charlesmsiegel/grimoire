import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type Style } from "../api/client";
import { Field } from "./Field";

const BLANK = { name: "", description: "", tags: [] as string[], body: "" };

export function StyleGuideEditor() {
  const [styles, setStyles] = useState<Style[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const [form, setForm] = useState(BLANK);
  const [builtIn, setBuiltIn] = useState(false);
  const [mode, setMode] = useState<"view" | "edit">("edit");
  const [tagInput, setTagInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => api.listStyles().then(setStyles), []);
  useEffect(() => { reload(); }, [reload]);

  function resetForm() {
    setSid(null);
    setForm(BLANK);
    setBuiltIn(false);
    setMode("edit");
  }

  async function select(id: string) {
    setError(null);
    const s = await api.readStyle(id);
    setSid(id);
    setForm({ name: s.meta.name, description: s.meta.description, tags: s.meta.tags, body: s.body.trim() });
    setBuiltIn(s.meta.built_in);
    setMode("view");
  }

  async function save() {
    if (!form.name.trim()) return;
    setError(null);
    try {
      if (sid && !builtIn) {
        await api.updateStyle(sid, form);
        await reload();
        await select(sid);
      } else {
        const { id } = await api.createStyle(form);
        await reload();
        await select(id);
      }
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function remove(s: Style) {
    if (!window.confirm(`Delete style guide '${s.name}'?`)) return;
    await api.deleteStyle(s.id);
    if (sid === s.id) resetForm();
    await reload();
  }

  async function duplicate() {
    if (!sid) return;
    const { id } = await api.duplicateStyle(sid);
    await reload();
    await select(id);
  }

  function addTag() {
    const t = tagInput.trim();
    if (t && !form.tags.includes(t)) setForm({ ...form, tags: [...form.tags, t] });
    setTagInput("");
  }

  function removeTag(t: string) {
    setForm({ ...form, tags: form.tags.filter((x) => x !== t) });
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New style guide</button>
        {styles.map((s) => (
          <button key={s.id} className={"row" + (sid === s.id ? " active" : "")} onClick={() => select(s.id)}>
            {s.name}
            {s.built_in && <span className="mark-badge">built-in</span>}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" && sid ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{form.name}</h3>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{form.body}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                {builtIn
                  ? <button className="subtle" onClick={duplicate}>Duplicate to customize</button>
                  : <button className="subtle" onClick={() => setMode("edit")}>Edit</button>}
              </div>
              {form.description && (
                <div className="side-section">
                  <h4>Description</h4>
                  <div className="field-hint">{form.description}</div>
                </div>
              )}
              {form.tags.length > 0 && (
                <div className="side-section">
                  <h4>Tags</h4>
                  <div className="chips">
                    {form.tags.map((t) => <span key={t} className="chip on">{t}</span>)}
                  </div>
                </div>
              )}
            </aside>
          </div>
        ) : (
          <div className="form">
            <h3>{sid ? "Edit style guide" : "New style guide"}</h3>
            <Field label="Name">
              <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Description">
              <input type="text" value={form.description}
                     onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <Field label="Tags">
              <div className="chips">
                {form.tags.map((t) => (
                  <button key={t} className="chip on" onClick={() => removeTag(t)}>{t} ×</button>
                ))}
              </div>
              <div className="joined">
                <input type="text" value={tagInput} onChange={(e) => setTagInput(e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }} />
                <button className="subtle" onClick={addTag}>Add</button>
              </div>
            </Field>
            <Field label="Guide text">
              <textarea value={form.body} rows={12} onChange={(e) => setForm({ ...form, body: e.target.value })} />
            </Field>
            <div className="form-actions">
              {sid && !builtIn && (
                <button className="subtle" onClick={() => remove(styles.find((s) => s.id === sid)!)}>Delete</button>
              )}
              {sid && <button className="subtle" onClick={() => setMode("view")}>Cancel</button>}
              <button className="primary" onClick={save} disabled={!form.name.trim()}>
                {sid && !builtIn ? "Save style guide" : "Create style guide"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
