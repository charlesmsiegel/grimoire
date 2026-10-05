import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { markdownImageComponents } from "../markdown/MarkdownImage";
import remarkGfm from "remark-gfm";
import { api, type EntityScope, type ModuleDetail, type PCDetail, type PCRevision, type Persona,
         type VersionRef } from "../api/client";
import { errorText } from "../api/errors";
import { thumbSet } from "../api/thumbs";
import { AvatarFocusPicker } from "../components/AvatarFocusPicker";
import { BirthdateDisplay, BirthdatePicker } from "../components/BirthdatePicker";
import { Field } from "../components/Field";
import { LibraryPanel } from "../components/LibraryPanel";
import { OwnedLorePanel } from "../components/OwnedLorePanel";
import { ColumnSection, PageShell } from "../components/PageShell";
import SheetPanel from "../components/SheetPanel";
import { initialsOf } from "../components/Portrait";
import { sectionHref } from "../worldPaths";
import { PCArtTab } from "../components/pc/PCArtTab";
import { MobileArtEntry } from "../components/MobileArtEntry";
import { whenLabel } from "../components/turnLabels";

const BLANK: Persona = {
  name: "", pronouns: "", summary: "", description: "", birthdate: "", goals: "", player_notes: "",
};
type Tab = "persona" | "lore" | "art" | "sheet";
type PCImage = { name: string; v: string };

/** A PC record is the unit of navigation and writing. The section page owns
 * only its index and creation; this page owns one record's persona, version,
 * campaign state, art, lore, and sheet, all addressed through the same scope.
 * React Router reuses a route element when params change, so the key retires
 * old reads and edit state before another record can receive a write. Image
 * listings have a second request token because versions change in place. */
export default function PCPage({ campaign = false }: { campaign?: boolean }) {
  const { wid = "", cid = "", pid = "" } = useParams();
  return <PCRecord key={`${campaign ? `campaign:${cid}` : `world:${wid}`}/${pid}`} campaign={campaign} />;
}

function PCRecord({ campaign }: { campaign: boolean }) {
  const { wid: worldParam = "", cid = "", pid = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const scope: EntityScope = campaign ? { kind: "campaign", id: cid } : { kind: "world", id: worldParam };
  const [wid, setWid] = useState(worldParam);
  const [detail, setDetail] = useState<PCDetail | null>(null);
  const [vid, setVid] = useState("");
  const [persona, setPersona] = useState<Persona>(BLANK);
  const [mode, setMode] = useState<"view" | "edit">(
    (location.state as { newPC?: boolean } | null)?.newPC ? "edit" : "view");
  // Creation is a one-shot instruction. Reopening this history entry later is
  // a normal read, and must not silently put an existing PC back in edit mode.
  useEffect(() => {
    if ((location.state as { newPC?: boolean } | null)?.newPC) {
      navigate(location.pathname, { replace: true, state: null });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [tab, setTab] = useState<Tab>("persona");
  const [error, setError] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [images, setImages] = useState<PCImage[]>([]);
  const [cropOpen, setCropOpen] = useState(false);
  const [module, setModule] = useState<ModuleDetail | null>(null);
  const [tags, setTags] = useState<Record<string, string>>({});
  const [newTag, setNewTag] = useState("");
  const [locked, setLocked] = useState<string | null>(null);
  const [worldVersions, setWorldVersions] = useState<VersionRef[]>([]);
  const [importVid, setImportVid] = useState("");
  // Revision history (#67): the selected version's earlier texts, and the one
  // being looked at instead of the current text. `null` revisions is "not
  // loaded / could not load", which the column says rather than "none".
  const [revisions, setRevisions] = useState<PCRevision[] | null>(null);
  const [revisionError, setRevisionError] = useState<string | null>(null);
  const [revision, setRevision] = useState<(PCRevision & { persona: Persona }) | null>(null);
  const imageRequest = useRef(0);
  const recordRequest = useRef(0);
  const selectedVid = useRef("");
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  const loadImages = useCallback(async (version: string) => {
    const request = ++imageRequest.current;
    setImages([]);
    setImageError(null);
    if (!version) return;
    try {
      const listing = await api.listPCImages(scope, pid, version);
      if (live.current && request === imageRequest.current) setImages(listing);
    } catch (err: unknown) {
      if (live.current && request === imageRequest.current) setImageError(errorText(err));
    }
  }, [scope.kind, scope.id, pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const read = useCallback(async (version?: string, preserveMode = false) => {
    const request = ++recordRequest.current;
    const next = await api.readPC(scope, pid);
    if (!live.current || request !== recordRequest.current) return;
    setDetail(next);
    const selected = next.versions.find((v) => v.id === (version ?? next.meta.default_version)) ?? next.versions[0];
    setVid(selected?.id ?? "");
    selectedVid.current = selected?.id ?? "";
    setPersona(selected?.persona ?? BLANK);
    if (!preserveMode) setMode("view");
    void loadImages(selected?.id ?? "");
    if (campaign) {
      const roster = await api.listAppearances(cid);
      if (live.current && request === recordRequest.current) {
        setLocked(roster.find((r) => r.kind === "pcs" && r.id === pid)?.version ?? null);
      }
    }
  }, [scope.kind, scope.id, pid, loadImages, campaign, cid]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let active = true;
    api.readPC(scope, pid).then((next) => {
      if (!active) return;
      setDetail(next);
      const v = next.versions.find((x) => x.id === next.meta.default_version) ?? next.versions[0];
      setVid(v?.id ?? ""); selectedVid.current = v?.id ?? "";
      setPersona(v?.persona ?? BLANK);
      void loadImages(v?.id ?? "");
    }).catch((err: unknown) => { if (active) setError(errorText(err)); });
    if (campaign) {
      api.getCampaign(cid).then((c) => { if (active) setWid(c.meta.world); })
        .catch((err: unknown) => { if (active) setError(errorText(err)); });
      api.listAppearances(cid).then((roster) => {
        if (active) setLocked(roster.find((r) => r.kind === "pcs" && r.id === pid)?.version ?? null);
      }).catch((err: unknown) => { if (active) setError(errorText(err)); });
    } else {
      api.listTags(worldParam).then((t) => { if (active) setTags(t); })
        .catch((err: unknown) => { if (active) setError(errorText(err)); });
    }
    return () => { active = false; };
  }, [scope.kind, scope.id, pid, loadImages]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!wid) return;
    let active = true;
    const moduleId = campaign
      ? api.getCampaignModule(cid).then((r) => r.resolved)
      : Promise.all([api.getWorldSheetsIndex(wid), api.listModules()])
          .then(([index, installed]) => index.default || index.modules[0] || installed[0]?.id || null);
    moduleId.then((id) => id ? api.readModule(id) : null)
      .then((m) => { if (active) setModule(m); })
      .catch(() => { if (active) setModule(null); });
    if (campaign) api.readPC({ kind: "world", id: wid }, pid)
      .then((w) => { if (active) setWorldVersions(w.versions.map((v) => ({ id: v.id, name: v.name }))); })
      .catch(() => { if (active) setWorldVersions([]); });
    return () => { active = false; };
  }, [wid, campaign, cid, pid]);

  // Keyed on `detail` as well as `vid`: every save, restore and version
  // create ends in a fresh `read`, so a new detail is exactly "history may have
  // moved". A stale answer for a version since switched away from is dropped.
  useEffect(() => {
    if (!vid || !detail) return;
    let active = true;
    setRevisionError(null);
    api.listPCRevisions(scope, pid, vid)
      .then((list) => { if (active) setRevisions(list); })
      .catch((err: unknown) => { if (active) { setRevisions(null); setRevisionError(errorText(err)); } });
    return () => { active = false; };
  }, [vid, detail]); // eslint-disable-line react-hooks/exhaustive-deps

  async function openRevision(rev: PCRevision) {
    setError(null);
    try {
      const persona = await api.readPCRevision(scope, pid, vid, rev.id);
      if (live.current && selectedVid.current === vid) { setRevision({ ...rev, persona }); setTab("persona"); }
    } catch (err: unknown) { setError(errorText(err)); }
  }
  async function restoreRevision() {
    if (!revision) return;
    const rid = revision.id;
    await run(() => api.restorePCRevision(scope, pid, vid, rid), async () => {
      setRevision(null); await read(vid);
    });
  }

  function switchVersion(version: string) {
    setRevision(null);
    recordRequest.current++;
    imageRequest.current++;
    setVid(version);
    selectedVid.current = version;
    setPersona(detail?.versions.find((v) => v.id === version)?.persona ?? BLANK);
    setCropOpen(false);
    void loadImages(version);
  }
  async function run(action: () => Promise<unknown>, after?: () => Promise<void>) {
    setError(null);
    const request = recordRequest.current;
    try {
      await action();
      if (request === recordRequest.current) await (after ?? (() => read(vid)))();
    }
    catch (err: unknown) { setError(errorText(err)); }
  }
  async function savePersona() {
    await run(() => api.updatePCVersion(scope, pid, vid, persona), () => read(vid));
  }
  async function addVersion() {
    const name = window.prompt("New version name?")?.trim();
    if (!name) return;
    await run(async () => {
      const { version } = await api.createPCVersion(scope, pid, { name, persona });
      await read(version); setMode("edit");
    }, () => Promise.resolve());
  }
  async function saveTags(next: string[]) {
    await run(() => api.updatePC(scope, pid, { tags: next }), () => read(vid, true));
  }
  async function deletePC() {
    if (!detail || !window.confirm(`Delete PC '${detail.meta.name}'?`)) return;
    await run(() => api.deletePC(scope, pid), () => {
      navigate(sectionHref(scope, { kind: "section", at: "pcs" }));
      return Promise.resolve();
    });
  }
  async function pickVersion() {
    if (!window.confirm(`Lock '${detail?.meta.name}' to this version? Other versions are removed from the campaign.`)) return;
    await run(() => api.pickVersion(cid, "pcs", pid, vid));
  }
  async function importVersion() {
    if (!importVid || !window.confirm("Replace the locked version with the world's copy?")) return;
    await run(() => api.importVersion(cid, "pcs", pid, importVid), () => read(importVid));
  }

  const version = detail?.versions.find((v) => v.id === vid);
  const name = persona.name || detail?.meta.name || pid;
  const avatar = images.find((i) => i.name === "avatar");
  const firstGallery = images.filter((i) => i.name.startsWith("gallery_"))
    .sort((a, b) => Number(a.name.slice(8)) - Number(b.name.slice(8)))[0];
  const preview = avatar ?? firstGallery;
  const imageUrl = (image: PCImage, w?: number) =>
    api.actorImageUrl(scope, "pcs", pid, vid, image.name, { w, v: image.v });
  const back = sectionHref(scope, { kind: "section", at: "pcs" });
  const column = <>
    <Link className="column-back" to={back}>‹ All PCs</Link>
    {avatar
      ? <button className="identity-art avatar-crop-btn" type="button"
                aria-label="Adjust avatar crop" onClick={() => setCropOpen(true)}>
          <img className="detail-avatar" alt="" decoding="async"
               {...thumbSet((w) => imageUrl(avatar, w), "240px")}
               style={version?.avatar_focus == null ? undefined
                 : { objectPosition: `${version.avatar_focus}% ${version.avatar_focus}%` }} />
        </button>
      : <div className="identity-art identity-art-empty" aria-hidden>{initialsOf(name)}</div>}
    <h2 className="identity-name">{name}</h2>
    {detail && <ColumnSection label="Version">
      <select value={vid} onChange={(e) => switchVersion(e.target.value)} aria-label="Version">
        {detail.versions.map((v) => <option key={v.id} value={v.id}>
          {v.name}{v.id === detail.meta.default_version ? " (default)" : ""}
        </option>)}
      </select>
      {(scope.kind === "world" || !locked) && <button className="subtle" onClick={() => void addVersion()}>+ Version</button>}
      <button className="subtle" onClick={() => void run(() => api.updatePC(scope, pid, { default_version: vid }))}>Set default</button>
    </ColumnSection>}
    {campaign && detail && <ColumnSection label="Campaign version">
      {locked ? <>
        <div className="field-hint">Locked to {detail.versions.find((v) => v.id === locked)?.name ?? locked}.</div>
        <select aria-label="Import version" value={importVid} onChange={(e) => setImportVid(e.target.value)}>
          <option value="">— world version —</option>
          {worldVersions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
        <button className="subtle" disabled={!importVid} onClick={() => void importVersion()}>Import from world</button>
      </> : detail.versions.length > 1
        ? <button className="subtle" onClick={() => void pickVersion()}>Pick this version</button>
        : <div className="field-hint">Single version; it locks when first used in a scene.</div>}
    </ColumnSection>}
    {persona.pronouns && <ColumnSection label="Pronouns"><div className="field-hint">{persona.pronouns}</div></ColumnSection>}
    {persona.summary && <ColumnSection label="Summary"><div className="field-hint">{persona.summary}</div></ColumnSection>}
    {persona.goals && <ColumnSection label="Goals"><div className="field-hint">{persona.goals}</div></ColumnSection>}
    {persona.player_notes && <ColumnSection label="Notes for the narrator">
      <div className="field-hint">{persona.player_notes}</div></ColumnSection>}
    {persona.birthdate && <ColumnSection label="Birthdate"><BirthdateDisplay scope={scope} value={persona.birthdate} /></ColumnSection>}
    {detail && <ColumnSection label="Tags"><div className="chips">
      {detail.meta.tags.map((t) => <span key={t} className="chip on">{campaign ? t : tags[t] ?? t}</span>)}
      {!detail.meta.tags.length && <span className="field-hint">no tags</span>}
    </div></ColumnSection>}
    {detail && <ColumnSection label="History">
      {revisionError ? <div className="field-hint">Could not load history: {revisionError}</div>
        : revisions === null ? null
        : revisions.length === 0 ? <div className="field-hint">No earlier revisions.</div>
        : <div className="pc-history">{revisions.map((r) =>
            <button key={r.id} type="button" disabled={mode === "edit"}
                    title={mode === "edit" ? "Save or cancel the edit first" : undefined}
                    className={"subtle history-row" + (revision?.id === r.id ? " active" : "")}
                    onClick={() => void openRevision(r)}>
              {whenLabel(r.saved)}{r.name !== name ? ` · ${r.name}` : ""}
            </button>)}</div>}
    </ColumnSection>}
    {campaign && detail && <LibraryPanel key={`${cid}:pcs:${pid}`} cid={cid} kind="pcs" id={pid}
                                        onMoved={() => { void read(vid, true); }} />}
  </>;

  return <PageShell column={column} columnLabel="PC" className="character-page pc-page">
    {cropOpen && avatar && <AvatarFocusPicker src={imageUrl(avatar)} initial={version?.avatar_focus ?? 50}
      onSave={(focus) => void run(() => api.setPCAvatarFocus(scope, pid, vid, focus), async () => {
        setCropOpen(false); await read(vid, true);
      })} onClose={() => setCropOpen(false)} />}
    <div className="screen-head"><div>
      <div className="eyebrow">{campaign ? "Campaign PC" : "World PC"}</div>
      <h1 className="screen-title">{detail ? name : "PC"}</h1>
    </div>{detail && scope.kind === "world" && <button className="subtle" onClick={() => void deletePC()}>Delete PC</button>}</div>
    {error && <div className="banner">{error}</div>}
    {detail && <>
      <MobileArtEntry name={name} image={preview ? (w?: number) => imageUrl(preview, w) : null}
                      onOpenArt={() => setTab("art")} />
      <div className="card-tabs" role="tablist" aria-label="PC">
        {(["persona", "lore", "art", "sheet"] as Tab[])
          .filter((t) => t !== "sheet" || module)
          .map((t) => <button key={t} role="tab" aria-selected={tab === t}
            className={"tab" + (tab === t ? " active" : "")} onClick={() => setTab(t)}>
            {t === "persona" ? "Persona" : t === "lore" ? "Lore" : t === "art" ? `Art ${images.length}` : "Sheet"}
          </button>)}
      </div>
      <div className="card-pane-body" role="tabpanel">
        {tab === "persona" && (mode === "view" ? revision ? <div className="pc-revision">
          <div className="banner">Earlier text, replaced {whenLabel(revision.saved)}. Read-only.</div>
          <div className="form-actions">
            <button className="subtle" onClick={() => setRevision(null)}>Back to current</button>
            <button className="primary" onClick={() => void restoreRevision()}>Restore this text</button>
          </div>
          <h3>{revision.persona.name}</h3>
          {([["Pronouns", revision.persona.pronouns], ["Summary", revision.persona.summary],
             ["Goals", revision.persona.goals], ["Notes for the narrator", revision.persona.player_notes],
             ["Birthdate", revision.persona.birthdate]] as const)
            .filter(([, v]) => v).map(([label, v]) =>
              <div key={label} className="side-section"><h4>{label}</h4><div className="field-hint">{v}</div></div>)}
          <div className="detail-rendered"><Markdown components={markdownImageComponents} remarkPlugins={[remarkGfm]}>{revision.persona.description}</Markdown></div>
        </div> : <>
          <div className="form-actions"><button className="subtle" onClick={() => setMode("edit")}>Edit</button></div>
          <div className="detail-rendered"><Markdown components={markdownImageComponents} remarkPlugins={[remarkGfm]}>{persona.description}</Markdown></div>
        </> : <div className="form">
          <Field label="Name"><input type="text" value={persona.name} onChange={(e) => setPersona({ ...persona, name: e.target.value })} /></Field>
          <Field label="Pronouns"><input type="text" value={persona.pronouns} onChange={(e) => setPersona({ ...persona, pronouns: e.target.value })} /></Field>
          <Field label="Summary"><input type="text" value={persona.summary} onChange={(e) => setPersona({ ...persona, summary: e.target.value })} /></Field>
          {/* Single-line on purpose: both are frontmatter scalars, which the
              store folds to one line (#65). */}
          <Field label="Goals"><input type="text" value={persona.goals ?? ""}
            onChange={(e) => setPersona({ ...persona, goals: e.target.value })} /></Field>
          <Field label="Notes for the narrator"><input type="text" value={persona.player_notes ?? ""}
            onChange={(e) => setPersona({ ...persona, player_notes: e.target.value })} /></Field>
          <Field label="Birthdate"><BirthdatePicker scope={scope} value={persona.birthdate ?? ""}
            onChange={(birthdate) => setPersona({ ...persona, birthdate })} ariaLabel="Birthdate" /></Field>
          <Field label="Description"><textarea value={persona.description} rows={8}
            onChange={(e) => setPersona({ ...persona, description: e.target.value })} /></Field>
          <Field label="Tags"><div className="chips">
            {campaign ? <>
              {detail.meta.tags.map((t) => <button key={t} className="chip on"
                onClick={() => void saveTags(detail.meta.tags.filter((x) => x !== t))}>{t}</button>)}
              <input aria-label="New tag" value={newTag} onChange={(e) => setNewTag(e.target.value)} />
              <button className="subtle" disabled={!newTag.trim()} onClick={() => {
                void saveTags([...detail.meta.tags, newTag.trim()]); setNewTag("");
              }}>Add</button>
            </> : Object.keys(tags).sort().map((t) => <button key={t}
              className={"chip" + (detail.meta.tags.includes(t) ? " on" : "")}
              onClick={() => void saveTags(detail.meta.tags.includes(t)
                ? detail.meta.tags.filter((x) => x !== t) : [...detail.meta.tags, t])}>{tags[t]}</button>)}
          </div></Field>
          <div className="form-actions">
            <button className="subtle" onClick={() => void read(vid).catch((err: unknown) => setError(errorText(err)))}>Cancel</button>
            <button className="primary" onClick={() => void savePersona()}>Save persona</button>
          </div>
        </div>)}
        {tab === "lore" && <OwnedLorePanel scope={scope} ownerRef={`pcs:${pid}`}
          hrefFor={(id) => sectionHref(scope, { kind: "record", at: "lore", rid: id })}
          newHref={sectionHref(scope, { kind: "section", at: "lore", newOwner: `pcs:${pid}` })} />}
        {tab === "art" && <PCArtTab key={vid} scope={scope} wid={wid} pid={pid} vid={vid}
          images={images} descriptions={version?.image_descriptions ?? {}}
          imageError={imageError} avatarFocus={version?.avatar_focus ?? null}
          onRefresh={() => vid === selectedVid.current ? read(vid, true) : Promise.resolve()}
          onError={setError} />}
        {tab === "sheet" && module && <SheetPanel scope={scope} module={module} kind="pcs" eid={pid} />}
      </div>
    </>}
  </PageShell>;
}
