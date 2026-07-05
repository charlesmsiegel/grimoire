import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type EntityScope, type PCDetail, type PCSummary, type Persona, type VersionRef } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { Field } from "./Field";
import { OwnedLorePanel } from "./OwnedLorePanel";

const BLANK: Persona = { name: "", pronouns: "", summary: "", birthdate: "", description: "" };

export function PCEditor({ scope, wid, onOpenLore }:
  { scope: EntityScope; wid: string;
    onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void }) {
  const worldScope = scope.kind === "world";
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [tags, setTags] = useState<Record<string, string>>({});
  const [detail, setDetail] = useState<PCDetail | null>(null);
  const [vid, setVid] = useState("");
  const [persona, setPersona] = useState<Persona>(BLANK);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [error, setError] = useState<string | null>(null);
  const lockReq = useRef(0);
  const [locked, setLocked] = useState<string | null>(null);       // campaign: locked version id
  const [worldVersions, setWorldVersions] = useState<VersionRef[]>([]);
  const [importVid, setImportVid] = useState("");
  const [newTag, setNewTag] = useState("");

  const reload = useCallback(() => api.listPCs(scope).then(setPCs), [scope.kind, scope.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    reload();
    if (worldScope) api.listTags(wid).then(setTags);
  }, [wid, worldScope, reload]);

  async function select(pid: string, version?: string) {
    setError(null);
    const d = await api.readPC(scope, pid);
    setDetail(d);
    const v = d.versions.find((x) => x.id === (version ?? d.meta.default_version)) ?? d.versions[0];
    setVid(v?.id ?? "");
    setPersona(v?.persona ?? BLANK);
    setMode("view");
    if (!worldScope) {
      // token drops a slow earlier response so selecting A then B can't show A's lock on B
      const req = ++lockReq.current;
      const roster = await api.listAppearances(scope.id).catch(() => []);
      if (lockReq.current !== req) return;
      setLocked(roster.find((r) => r.kind === "pcs" && r.id === pid)?.version ?? null);
      setImportVid("");
      // the source world's versions feed the import picker; a deleted world PC just offers none
      api.readPC({ kind: "world", id: wid }, pid)
        .then((w) => { if (lockReq.current === req) setWorldVersions(w.versions.map((x) => ({ id: x.id, name: x.name }))); })
        .catch(() => { if (lockReq.current === req) setWorldVersions([]); });
    }
  }

  function switchVersion(id: string) {
    setVid(id);
    const v = detail?.versions.find((x) => x.id === id);
    if (v) setPersona(v.persona);
  }

  async function newPC() {
    const name = window.prompt("New PC name?")?.trim();
    if (!name) return;
    const { pc } = worldScope
      ? await api.createPC(wid, { name })
      : await api.createCampaignPC(scope.id, { name });
    await reload();
    await select(pc);
    setMode("edit"); // a brand-new PC goes straight to the form
  }

  async function savePersona() {
    if (!detail) return;
    setError(null);
    try {
      await api.updatePCVersion(scope, detail.meta.id, vid, persona);
      await select(detail.meta.id, vid); // back to the read-only view
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function addVersion() {
    if (!detail) return;
    const name = window.prompt("New version name?")?.trim();
    if (!name) return;
    const { version } = await api.createPCVersion(scope, detail.meta.id, { name, persona });
    await select(detail.meta.id, version);
    setMode("edit");
  }

  async function setDefault() {
    if (!detail) return;
    await api.updatePC(scope, detail.meta.id, { default_version: vid });
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

  async function saveTags(next: string[]) {
    if (!detail) return;
    await api.updatePC(scope, detail.meta.id, { tags: next });
    const d = await api.readPC(scope, detail.meta.id);
    setDetail(d); // keep the form open; only the tag chips changed
  }

  async function toggleTag(tid: string) {
    if (!detail) return;
    const current = detail.meta.tags;
    await saveTags(current.includes(tid) ? current.filter((t) => t !== tid) : [...current, tid]);
  }

  const versionName = (id: string | null) =>
    detail?.versions.find((v) => v.id === id)?.name ?? id ?? "";

  async function runPick() {
    if (!detail) return;
    if (!window.confirm(`Lock '${detail.meta.name}' to this version? Other versions are removed from the campaign.`)) return;
    try {
      await api.pickVersion(scope.id, "pcs", detail.meta.id, vid);
      await select(detail.meta.id, vid);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function runImport() {
    if (!detail || !importVid) return;
    if (!window.confirm("Replace the locked version with the world's copy?")) return;
    try {
      await api.importVersion(scope.id, "pcs", detail.meta.id, importVid);
      await select(detail.meta.id, importVid);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
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
              {!worldScope && (
                <div className="side-section">
                  <h4>Version</h4>
                  {locked ? (
                    <>
                      <div className="field-hint">Locked to <b>{versionName(locked)}</b> for this campaign.</div>
                      <select aria-label="Import version" value={importVid}
                              onChange={(e) => setImportVid(e.target.value)}>
                        <option value="">— world version —</option>
                        {worldVersions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                      </select>
                      <button className="subtle" disabled={!importVid} onClick={runImport}>
                        Import from world
                      </button>
                    </>
                  ) : detail.versions.length > 1 ? (
                    <>
                      <div className="field-hint">
                        Viewing {versionName(vid)}. Picking locks it and removes the others from this campaign.
                      </div>
                      <button className="subtle" onClick={runPick}>Pick this version</button>
                    </>
                  ) : (
                    <div className="field-hint">Single version; it locks when first used in a scene.</div>
                  )}
                </div>
              )}
              {detail.versions.length > 1 && (
                <div className="side-section">
                  <h4>{worldScope ? "Version" : "Viewing"}</h4>
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
                  ? <div className="chips">{detail.meta.tags.map((t) => <span key={t} className="chip on">{worldScope ? (tags[t] ?? t) : t}</span>)}</div>
                  : <div className="field-hint">no tags</div>}
              </div>
              {onOpenLore && (
                <OwnedLorePanel
                  scope={scope}
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
              {(worldScope || !locked) && <button className="subtle" onClick={addVersion}>+ Version</button>}
              <button className="subtle" onClick={setDefault}>Set default</button>
              {worldScope && <button className="subtle" onClick={deletePC}>Delete PC</button>}
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
              <CalendarDatePicker scope={scope} value={persona.birthdate ?? ""}
                                  onChange={(v) => setPersona({ ...persona, birthdate: v })}
                                  ariaLabel="Birthdate" />
            </Field>
            <Field label="Description">
              <textarea value={persona.description} rows={6} onChange={(e) => setPersona({ ...persona, description: e.target.value })} />
            </Field>

            {worldScope ? (
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
            ) : (
              <Field label="Tags" hint="campaign tags are free strings; click one to remove it">
                <div className="chips">
                  {detail.meta.tags.map((t) => (
                    <button key={t} className="chip on" onClick={() => toggleTag(t)}>{t}</button>
                  ))}
                  <input type="text" aria-label="New tag" value={newTag} placeholder="add tag…"
                         onChange={(e) => setNewTag(e.target.value)} />
                  <button className="subtle" disabled={!newTag.trim()}
                          onClick={() => { saveTags([...detail.meta.tags, newTag.trim()]); setNewTag(""); }}>
                    Add
                  </button>
                </div>
              </Field>
            )}

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
