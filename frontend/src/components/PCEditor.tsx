import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type PCDetail, type PCSummary, type Persona } from "../api/client";
import { Field } from "./Field";
import { OwnedLorePanel } from "./OwnedLorePanel";

const BLANK: Persona = { name: "", pronouns: "", summary: "", birthdate: "", description: "" };

export function PCEditor({ wid, onOpenLore }:
  { wid: string; onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void }) {
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [tags, setTags] = useState<Record<string, string>>({});
  const [detail, setDetail] = useState<PCDetail | null>(null);
  const [vid, setVid] = useState("");
  const [persona, setPersona] = useState<Persona>(BLANK);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => api.listPCs(wid).then(setPCs), [wid]);
  useEffect(() => {
    reload();
    api.listTags(wid).then(setTags);
  }, [wid, reload]);

  async function select(pid: string, version?: string) {
    setError(null);
    const d = await api.readPC(wid, pid);
    setDetail(d);
    const v = d.versions.find((x) => x.id === (version ?? d.meta.default_version)) ?? d.versions[0];
    setVid(v?.id ?? "");
    setPersona(v?.persona ?? BLANK);
    setMode("view");
  }

  function switchVersion(id: string) {
    setVid(id);
    const v = detail?.versions.find((x) => x.id === id);
    if (v) setPersona(v.persona);
  }

  async function newPC() {
    const name = window.prompt("New PC name?")?.trim();
    if (!name) return;
    const { pc } = await api.createPC(wid, { name });
    await reload();
    await select(pc);
    setMode("edit"); // a brand-new PC goes straight to the form
  }

  async function savePersona() {
    if (!detail) return;
    setError(null);
    try {
      await api.updatePCVersion(wid, detail.meta.id, vid, persona);
      await select(detail.meta.id, vid); // back to the read-only view
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function addVersion() {
    if (!detail) return;
    const name = window.prompt("New version name?")?.trim();
    if (!name) return;
    const { version } = await api.createPCVersion(wid, detail.meta.id, { name, persona });
    await select(detail.meta.id, version);
    setMode("edit");
  }

  async function setDefault() {
    if (!detail) return;
    await api.updatePC(wid, detail.meta.id, { default_version: vid });
    await select(detail.meta.id, vid);
    setMode("edit");
  }

  async function deletePC() {
    if (!detail) return;
    if (!window.confirm(`Delete PC '${detail.meta.name}'?`)) return;
    await api.deletePC(wid, detail.meta.id);
    setDetail(null);
    await reload();
  }

  async function toggleTag(tid: string) {
    if (!detail) return;
    const current = detail.meta.tags;
    const next = current.includes(tid) ? current.filter((t) => t !== tid) : [...current, tid];
    await api.updatePC(wid, detail.meta.id, { tags: next });
    const d = await api.readPC(wid, detail.meta.id);
    setDetail(d); // keep the form open; only the tag chips changed
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={newPC}>+ New PC</button>
        {pcs.map((p) => (
          <button
            key={p.id}
            className={"row" + (detail?.meta.id === p.id ? " active" : "")}
            onClick={() => select(p.id)}
          >
            {p.name}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {!detail ? (
          <div className="editor-empty">Select or create a PC.</div>
        ) : mode === "view" ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{persona.name || detail.meta.name}</h3>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{persona.description}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              {detail.versions.length > 1 && (
                <div className="side-section">
                  <h4>Version</h4>
                  <select value={vid} onChange={(e) => switchVersion(e.target.value)} aria-label="Version">
                    {detail.versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}{v.id === detail.meta.default_version ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {persona.pronouns && (
                <div className="side-section">
                  <h4>Pronouns</h4>
                  <div className="field-hint">{persona.pronouns}</div>
                </div>
              )}
              {persona.summary && (
                <div className="side-section">
                  <h4>Summary</h4>
                  <div className="field-hint">{persona.summary}</div>
                </div>
              )}
              {persona.birthdate && (
                <div className="side-section">
                  <h4>Birthdate</h4>
                  <div className="field-hint">{persona.birthdate}</div>
                </div>
              )}
              <div className="side-section">
                <h4>Tags</h4>
                {detail.meta.tags.length > 0
                  ? <div className="chips">{detail.meta.tags.map((t) => <span key={t} className="chip on">{tags[t] ?? t}</span>)}</div>
                  : <div className="field-hint">no tags</div>}
              </div>
              {onOpenLore && (
                <OwnedLorePanel
                  wid={wid}
                  ownerRef={`pcs:${detail.meta.id}`}
                  onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                  onNewEntry={() => onOpenLore({ newOwner: `pcs:${detail.meta.id}` })}
                />
              )}
            </aside>
          </div>
        ) : (
          <div className="form">
            <div className="picker">
              <select value={vid} onChange={(e) => switchVersion(e.target.value)} aria-label="Version">
                {detail.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}{v.id === detail.meta.default_version ? " (default)" : ""}
                  </option>
                ))}
              </select>
              <button className="subtle" onClick={addVersion}>+ Version</button>
              <button className="subtle" onClick={setDefault}>Set default</button>
              <button className="subtle" onClick={deletePC}>Delete PC</button>
            </div>

            <Field label="Name">
              <input type="text" value={persona.name} onChange={(e) => setPersona({ ...persona, name: e.target.value })} />
            </Field>
            <Field label="Pronouns">
              <input type="text" value={persona.pronouns} onChange={(e) => setPersona({ ...persona, pronouns: e.target.value })} />
            </Field>
            <Field label="Summary">
              <input type="text" value={persona.summary} onChange={(e) => setPersona({ ...persona, summary: e.target.value })} />
            </Field>
            <Field label="Birthdate">
              <input type="date" aria-label="Birthdate" value={persona.birthdate ?? ""}
                     onChange={(e) => setPersona({ ...persona, birthdate: e.target.value })} />
            </Field>
            <Field label="Description">
              <textarea value={persona.description} rows={6} onChange={(e) => setPersona({ ...persona, description: e.target.value })} />
            </Field>

            <Field label="Tags">
              <div className="chips">
                {Object.keys(tags).sort().map((tid) => (
                  <button
                    key={tid}
                    className={"chip" + (detail.meta.tags.includes(tid) ? " on" : "")}
                    onClick={() => toggleTag(tid)}
                  >
                    {tags[tid]}
                  </button>
                ))}
                {Object.keys(tags).length === 0 && <span className="field-hint">No tags in this world yet.</span>}
              </div>
            </Field>

            <div className="form-actions">
              <button className="subtle" onClick={() => select(detail.meta.id, vid)}>Cancel</button>
              <button className="primary" onClick={savePersona}>Save persona</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
