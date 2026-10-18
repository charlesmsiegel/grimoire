import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useSearchParams } from "react-router-dom";
import { errorText } from "../api/errors";
import { api, type Style } from "../api/client";
import { Field } from "./Field";
import { useCreationRequest } from "./useCreationRequest";

const BLANK = { name: "", description: "", tags: [] as string[], body: "" };

export function StyleGuideEditor() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const creating = params.get("new") === "1";
  const [styles, setStyles] = useState<Style[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const [form, setForm] = useState(BLANK);
  const [builtIn, setBuiltIn] = useState(false);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [tagInput, setTagInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { busy: saving, begin: beginSave, retire: retireSave } = useCreationRequest(
    creating || selected ? "style" : null);

  const reload = useCallback(() => api.listStyles().then(setStyles), []);
  useEffect(() => { reload(); }, [reload]);

  function resetForm() {
    setSid(null);
    setForm(BLANK);
    setBuiltIn(false);
    setMode("edit");
  }

  // URL selection and Cancel share one read sequence: a late reload cannot
  // overwrite the record the reader moved to, or banner it with an old error.
  const selectionRead = useRef(0);
  async function select(id: string, current = () => true) {
    const request = ++selectionRead.current;
    try {
      setError(null);
      const s = await api.readStyle(id);
      if (!current() || request !== selectionRead.current) return;
      setSid(id);
      setForm({ name: s.meta.name, description: s.meta.description, tags: s.meta.tags, body: s.body.trim() });
      setBuiltIn(s.meta.built_in);
      setMode("view");
    } catch (err: unknown) {
      if (request === selectionRead.current && current()) throw err;
    }
  }

  useEffect(() => {
    let live = true;
    resetForm();
    if (selected) {
      setMode("view");
      void select(selected, () => live).catch((err: unknown) => {
        if (live) setError(errorText(err));
      });
    }
    return () => { live = false; selectionRead.current += 1; };
    // The URL is the record identity; form edits must never re-read its draft.
  }, [selected, creating]);

  async function save() {
    if (!form.name.trim() || saving) return;
    const request = beginSave();
    if (!request) return;
    setError(null);
    try {
      if (sid && !builtIn) {
        await api.updateStyle(sid, form);
        await reload();
        if (!request.current()) return;
        await select(sid, request.current);
        if (request.current()) setParams({ id: sid });
      } else {
        const { id } = await api.createStyle(form);
        await reload();
        if (!request.current()) return;
        await select(id, request.current);
        if (request.current()) setParams({ id });
      }
    } catch (err: unknown) {
      if (request.current()) setError(errorText(err));
    } finally { request.finish(); }
  }

  async function remove(s: Style) {
    if (!window.confirm(`Delete style guide '${s.name}'?`)) return;
    await api.deleteStyle(s.id);
    if (sid === s.id) { resetForm(); setParams({}); }
    await reload();
  }

  async function duplicate() {
    if (!sid) return;
    const { id } = await api.duplicateStyle(sid);
    await reload();
    await select(id);
    setParams({ id });
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
        <Link className="primary new" to="?new=1">+ New style guide</Link>
        {styles.map((s) => (
          <Link key={s.id} className={"row" + (sid === s.id ? " active" : "")} to={`?${new URLSearchParams({ id: s.id })}`}>
            {s.name}
            {s.built_in && <span className="mark-badge">built-in</span>}
          </Link>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {!selected && !creating ? <div className="editor-empty">Choose a style guide to read, or create one.</div>
          : selected && !sid ? error ? null : <p className="field-hint">Loading style guide...</p>
          : mode === "view" && sid ? (
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
              {sid ? <button className="subtle" disabled={saving} onClick={() => {
                // Reload only after the write settles, never the old bytes ahead of it.
                if (saving) return;
                retireSave(); void select(sid).catch((err: unknown) => setError(errorText(err)));
              }}>Cancel</button>
                : <Link className="subtle" to="?">Cancel</Link>}
              <button className="primary" onClick={save} disabled={saving || !form.name.trim()}>
                {sid && !builtIn ? "Save style guide" : "Create style guide"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
