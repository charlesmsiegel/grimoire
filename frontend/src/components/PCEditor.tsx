import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type EntityScope, type ModuleDetail, type PCDetail, type PCSummary, type Persona, type VersionRef } from "../api/client";
import { AvatarFocusPicker } from "./AvatarFocusPicker";
import { CalendarDatePicker } from "./CalendarDatePicker";
import CreationWizard from "./CreationWizard";
import { Field } from "./Field";
import { LibraryPanel } from "./LibraryPanel";
import { ImageDescriptionField } from "./ImageDescriptionField";
import { OwnedLorePanel } from "./OwnedLorePanel";
import { Portrait } from "./Portrait";
import SheetPanel from "./SheetPanel";

import { errorText } from "../api/errors";
const BLANK: Persona = { name: "", pronouns: "", summary: "", birthdate: "", description: "" };

export function PCEditor({ scope, wid, onOpenLore, focus, focusNonce = 0, module = null }:
  { scope: EntityScope; wid: string;
    onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void;
    /** A PC to open on arrival — a `pcs:` chip beside a lore entry, or the
     *  holder or leader named by a ref field (#222). Without it those chips
     *  landed on the PC section and left the reader to find the record again,
     *  which is answering the question with the index. */
    focus?: string | null;
    /** Bumped per navigation, so following the same chip twice is two events
     *  rather than one no-op — same reason `GreetingEditor` carries one. */
    focusNonce?: number;
    module?: ModuleDetail | null }) {
  const worldScope = scope.kind === "world";
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [tags, setTags] = useState<Record<string, string>>({});
  const [detail, setDetail] = useState<PCDetail | null>(null);
  const [vid, setVid] = useState("");
  const [persona, setPersona] = useState<Persona>(BLANK);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [error, setError] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const lockReq = useRef(0);
  const [locked, setLocked] = useState<string | null>(null);       // campaign: locked version id
  const [worldVersions, setWorldVersions] = useState<VersionRef[]>([]);
  const [importVid, setImportVid] = useState("");
  const [newTag, setNewTag] = useState("");
  // The open version's images (#219). Held separately from `detail` rather than
  // read off it: an upload has to refresh the shelf without re-selecting the PC,
  // which would snap the form back to the default version.
  const [images, setImages] = useState<{ name: string; v: string }[]>([]);
  const [cropOpen, setCropOpen] = useState(false);
  const shelfFileRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => api.listPCs(scope).then(setPCs), [scope.kind, scope.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const reloadImages = useCallback((pid: string, version: string) => {
    if (!version) { setImages([]); return; }
    // Swallowed on purpose, and narrowly: the shelf is one section of a screen
    // whose subject is the persona. A listing that fails leaves the reader
    // their PC with no art shown, which is the same thing an empty store
    // looks like -- far better than replacing the whole record with a banner
    // about a directory scan. Every WRITE on this shelf reports.
    api.listPCImages(scope, pid, version).then(setImages).catch(() => setImages([]));
  }, [scope.kind, scope.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    reload();
    if (worldScope) api.listTags(wid).then(setTags);
    setWizardOpen(false); // a scope change can reuse this instance; never carry a wizard across it
  }, [wid, worldScope, reload]);

  // arrived via an owner chip or a ref field: open that PC
  useEffect(() => {
    if (focus) void select(focus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, focusNonce, scope.kind, scope.id]);

  async function select(pid: string, version?: string) {
    setError(null);
    const d = await api.readPC(scope, pid);
    setDetail(d);
    const v = d.versions.find((x) => x.id === (version ?? d.meta.default_version)) ?? d.versions[0];
    setVid(v?.id ?? "");
    setPersona(v?.persona ?? BLANK);
    setMode("view");
    setCropOpen(false);
    // `readPC` already carries this version's image names, but not their
    // cache-busting tokens, and a promote rewrites two files under stable
    // URLs -- so the shelf is loaded from the listing that reports them.
    reloadImages(pid, v?.id ?? "");
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
    setCropOpen(false);
    if (detail) reloadImages(detail.meta.id, id);   // art belongs to the version
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
    } catch (err: unknown) {
      setError(errorText(err));
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
    await api.deletePC(scope, detail.meta.id);
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

  // ---- images (#219): the primary image is the asset named "avatar" ----
  // `?v=` names the exact content state, so these cache immutable; an upload or
  // a promote refreshes the tokens through reloadImages/reload.
  const hasAvatar = images.some((i) => i.name === "avatar");
  const galleryNames = images
    .map((i) => i.name)
    .filter((n) => n.startsWith("gallery_"))
    .sort((a, b) => Number(a.slice("gallery_".length)) - Number(b.slice("gallery_".length)));
  const avatarFocus = detail?.versions.find((v) => v.id === vid)?.avatar_focus ?? null;
  // Absent key = never reviewed, "" = reviewed and deliberately undescribed.
  const descriptions = detail?.versions.find((v) => v.id === vid)?.image_descriptions ?? {};
  // Only ever called from inside the `detail &&` branch, so the id is there;
  // taking it as an argument says that instead of papering it over with a
  // `?? ""` that would build a URL with an empty path segment.
  const imgSrc = (pid: string, n: string) => {
    const base = api.actorImageUrl(scope, "pcs", pid, vid, n);
    const v = images.find((i) => i.name === n)?.v;
    return v ? `${base}?v=${v}` : base;
  };

  /** Re-read the open version in place. `select()` would snap back to the
   *  default version, which is the wrong answer after editing another one's art. */
  async function refreshImages() {
    if (!detail) return;
    const d = await api.readPC(scope, detail.meta.id);
    setDetail(d);              // picks up avatar_focus, which the listing has no room for
    reloadImages(detail.meta.id, vid);
    await reload();            // the rail's portrait comes from the summary
  }

  async function onShelfAdd(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    // First image becomes the portrait; after that they queue in the gallery,
    // same rule the character and entity shelves use.
    const next = hasAvatar
      ? `gallery_${galleryNames.reduce((m, n) => Math.max(m, Number(n.slice("gallery_".length))), 0) + 1}`
      : "avatar";
    try {
      await api.putPCImage(scope, detail.meta.id, vid, next, file);
      await refreshImages();
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      e.target.value = "";   // same file twice in a row must still fire onChange
    }
  }

  async function describeImage(name: string, description: string) {
    if (!detail) return;
    await api.setPCImageDescription(scope, detail.meta.id, vid, name, description);
    await refreshImages();
  }

  /** World scope only: that is where the draft route is, and offering a button
   *  that 404s campaign-side would be worse than not offering one. */
  async function draftDescription(name: string): Promise<string> {
    if (!detail) return "";
    return (await api.draftPCImageDescription(wid, detail.meta.id, vid, name)).description;
  }

  async function promoteImage(name: string) {
    if (!detail) return;
    setError(null);
    try {
      await api.promotePCImage(scope, detail.meta.id, vid, name);
      await refreshImages();
    } catch (err: unknown) {
      setError(errorText(err));
    }
  }

  async function removeImage(name: string) {
    if (!detail) return;
    setError(null);
    try {
      await api.deletePCImage(scope, detail.meta.id, vid, name);
      await refreshImages();
    } catch (err: unknown) {
      setError(errorText(err));
    }
  }

  async function saveFocus(f: number) {
    if (!detail) return;
    setCropOpen(false);
    setError(null);
    try {
      await api.setPCAvatarFocus(scope, detail.meta.id, vid, f);
      await refreshImages();
    } catch (err: unknown) {
      setError(errorText(err));
    }
  }

  const versionName = (id: string | null) =>
    detail?.versions.find((v) => v.id === id)?.name ?? id ?? "";

  async function runPick() {
    if (!detail) return;
    if (!window.confirm(`Lock '${detail.meta.name}' to this version? Other versions are removed from the campaign.`)) return;
    try {
      await api.pickVersion(scope.id, "pcs", detail.meta.id, vid);
      await select(detail.meta.id, vid);
    } catch (err: unknown) {
      setError(errorText(err));
    }
  }

  async function runImport() {
    if (!detail || !importVid) return;
    if (!window.confirm("Replace the locked version with the world's copy?")) return;
    try {
      await api.importVersion(scope.id, "pcs", detail.meta.id, importVid);
      await select(detail.meta.id, importVid);
    } catch (err: unknown) {
      setError(errorText(err));
    }
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={newPC}>+ New PC</button>
        {worldScope && module && Object.values(module.sheets.sheet_types).some((st) => st.kind === "characters") && (
          <button className="subtle" onClick={() => setWizardOpen(true)}>+ New PC with sheet…</button>
        )}
        {pcs.map((p) => (
          <button
            key={p.id}
            className={"row" + (detail?.meta.id === p.id ? " active" : "")}
            onClick={() => select(p.id)}
          >
            {/* aria-hidden: the row is a button named from its contents, and a
                PC is picked by name -- a second reading of it as alt text is
                noise. `Portrait` falls back to initials on its own. */}
            <span className="pc-row-portrait" aria-hidden>
              <Portrait name={p.name} focus={p.avatar_focus}
                        src={p.has_avatar
                          ? api.actorImageUrl(scope, "pcs", p.id, p.default_version, "avatar")
                          : null} />
            </span>
            <span className="row-name">{p.name}</span>
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {wizardOpen && module && worldScope ? (
          <CreationWizard scope={scope} kind="pcs" module={module}
                          createRecord={(n) => (worldScope
                            ? api.createPC(wid, { name: n }).then((r) => r.pc)
                            : api.createCampaignPC(scope.id, { name: n }).then((r) => r.pc))}
                          deleteRecord={worldScope ? (id) => api.deletePC(scope, id).then(() => {}) : undefined}
                          onDone={async (id) => {
                            setWizardOpen(false);
                            await reload();
                            await select(id);
                            setMode("edit");
                          }}
                          onCancel={() => setWizardOpen(false)} />
        ) : !detail ? (
          <div className="editor-empty">Select or create a PC.</div>
        ) : mode === "view" ? (
          <div className="detail-view">
            {cropOpen && hasAvatar && (
              <AvatarFocusPicker src={imgSrc(detail.meta.id, "avatar")} initial={avatarFocus ?? 50}
                                 onSave={saveFocus} onClose={() => setCropOpen(false)} />
            )}
            <div className="detail-main">
              <div className="pc-head">
                {hasAvatar ? (
                  <button className="pc-head-art avatar-crop-btn" type="button"
                          aria-label="Adjust avatar crop" title="Adjust avatar crop"
                          onClick={() => setCropOpen(true)}>
                    <Portrait src={imgSrc(detail.meta.id, "avatar")} name={persona.name || detail.meta.name}
                              focus={avatarFocus} />
                  </button>
                ) : (
                  <span className="pc-head-art" aria-hidden>
                    <Portrait src={null} name={persona.name || detail.meta.name} />
                  </span>
                )}
                <h3>{persona.name || detail.meta.name}</h3>
              </div>
              {/* Write controls in the read-only view, deliberately, and the
                  same way `EntityEditor` does it: an image is a separate
                  resource that persists the moment it is chosen, with no Save
                  to wait for. Putting the shelf inside the form would file it
                  under the persona draft the form's Cancel throws away, which
                  is the opposite of what happens to an uploaded file. */}
              <div className="section-label">Images</div>
              <div className="images-shelf">
                {hasAvatar ? (
                  <figure className="shelf-tile avatar-tile">
                    <a href={imgSrc(detail.meta.id, "avatar")} target="_blank" rel="noreferrer">
                      <img alt="avatar" src={imgSrc(detail.meta.id, "avatar")} />
                    </a>
                    <figcaption>avatar</figcaption>
                    <button className="shelf-promote" onClick={() => removeImage("avatar")}>Remove</button>
                    <ImageDescriptionField key={`${vid}:avatar`} name="avatar" value={descriptions.avatar}
                                           onSave={(d) => describeImage("avatar", d)}
                                           onDraft={scope.kind === "world" ? () => draftDescription("avatar") : undefined} />
                  </figure>
                ) : (
                  <div className="shelf-tile shelf-empty">no avatar</div>
                )}
                {galleryNames.map((n) => (
                  <div className="shelf-tile" key={n}>
                    <a href={imgSrc(detail.meta.id, n)} target="_blank" rel="noreferrer"><img alt={n} src={imgSrc(detail.meta.id, n)} /></a>
                    <button className="shelf-promote" onClick={() => promoteImage(n)}>Set as avatar</button>
                    <button className="shelf-promote" onClick={() => removeImage(n)}>Remove</button>
                    <ImageDescriptionField key={`${vid}:${n}`} name={n} value={descriptions[n]}
                                           onSave={(d) => describeImage(n, d)}
                                           onDraft={scope.kind === "world" ? () => draftDescription(n) : undefined} />
                  </div>
                ))}
                <button className="shelf-add" onClick={() => shelfFileRef.current?.click()}>+ add</button>
                <input ref={shelfFileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp"
                       hidden aria-label="Add image" onChange={onShelfAdd} />
              </div>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{persona.description}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              {/* A campaign can already create a PC of its own, and `promote`
                  carries pcs — without this the only way to publish one was a
                  hand-built API call (Codex review). Promote is the only move
                  that applies to an actor, and `libraryStatus` says so, so this
                  renders nothing for a PC the campaign merely inherits. */}
              {!worldScope && detail && (
                <LibraryPanel key={`${scope.id}:pcs:${detail.meta.id}`}
                              cid={scope.id} kind="pcs" id={detail.meta.id}
                              onMoved={() => { void reload(); }} />
              )}
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
              {module && detail && (
                <SheetPanel scope={scope} module={module} kind="pcs" eid={detail.meta.id} />
                /* onOpenRef intentionally unset here: no cross-editor navigation target exists
                   yet from a character/PC sheet's ref chips (entity-form refs only; module-content
                   ref chips still preview correctly without it) */
              )}
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
