import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  api, type Appearance, type Card, type Casefile, type CharacterDetail,
  type CharacterSummary, type EntityScope, type Greeting, type LoreEntryDraft,
  type ModuleDetail, type VersionRef,
} from "../api/client";
import { errorText } from "../api/errors";
import { AvatarFocusPicker } from "../components/AvatarFocusPicker";
import { CalendarDatePicker } from "../components/CalendarDatePicker";
import { ErrorNote } from "../components/ErrorNote";
import { LibraryPanel } from "../components/LibraryPanel";
import { OwnedLorePanel } from "../components/OwnedLorePanel";
import { ColumnSection, PageShell } from "../components/PageShell";
import { useEntityKinds } from "../components/useEntityKinds";
import { ArtTab } from "../components/character/ArtTab";
import { CampaignSection } from "../components/character/CampaignSection";
import { CardTab } from "../components/character/CardTab";
import { GreetingsTab } from "../components/character/GreetingsTab";
import { ImportVersionDialog, type ImportChoice } from "../components/character/ImportVersionDialog";
import { TaglineSection } from "../components/character/TaglineSection";
import { VersionList } from "../components/character/VersionList";
import { VoiceAnchorSection } from "../components/character/VoiceAnchorSection";
import {
  avatarSrc, buildCard, characterHref, charactersHref,
  ExportMenu, focusStyle, formatOf, initialsOf,
} from "../components/character/shared";

type CardTabKey = "card" | "lore" | "greetings" | "art";

/** One character, on a page of its own.
 *
 *  This used to be the middle third of a three-pane view nested inside the
 *  world page, and the arithmetic is the whole argument for moving it: at a
 *  1600px viewport the app rail and the world index took 510px, the identity
 *  aside and a campaign-state pane took another 574px, and the card — the thing
 *  the screen is for — was left with 433px, its prose wrapping at 47 characters.
 *  At 1280px the same grid (`274px | minmax(0,1fr) | 300px`) collapsed the
 *  middle to 113px and the description to one word per line.
 *
 *  So it is a page now, in the shape `PageShell` describes: the 274px context
 *  column indexes THIS character (versions, campaign state, voice) and main is
 *  the card, at a measure chosen to be read rather than merely to be wide.
 *
 *  There is also no edit MODE. Every field is read-only until you ask, and then
 *  it is a textarea in the same place, sized to its content — see
 *  `EditableField`. One field is open at a time, which is `editing` below: a
 *  save PUTs the whole card, so two open fields is how one clobbers the other.
 */
/** The route element: one mount per character.
 *
 *  React Router reuses an element when only a param changes, so walking from
 *  one character to another — a search hit, an owner chip, the palette — kept
 *  every piece of this page's state alive across the change: the previous
 *  card on screen under the new name until the read landed, an open editor
 *  belonging to the character you left, and a `TaglineSection` that reads on
 *  mount and would then have saved the old text against the new id.
 *
 *  Keying on the record makes the change a remount, which retires all of it at
 *  once — including every in-flight read, whose `live` flag then correctly
 *  reads false. That is the same reason this page could drop the request-token
 *  discipline the old editor needed: it is per-record now, so long as this key
 *  says so.
 */
export default function CharacterPage({ campaign = false }: { campaign?: boolean }) {
  const { wid = "", cid = "", eid = "" } = useParams();
  return <CharacterRecord key={`${campaign ? cid : wid}/${eid}`} campaign={campaign} />;
}

function CharacterRecord({ campaign }: { campaign: boolean }) {
  const { wid: widParam = "", cid: cidParam = "", eid = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const scope: EntityScope = campaign
    ? { kind: "campaign", id: cidParam }
    : { kind: "world", id: widParam };
  const worldScope = !campaign;

  /** The world behind the record — the same id in world scope, and the
   *  campaign's world in campaign scope. Several routes (export, localize,
   *  chub, taglines, birthdate) hang off `/worlds` whatever scope you read in. */
  const [wid, setWid] = useState(campaign ? "" : widParam);
  const [campaignName, setCampaignName] = useState("");
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [vid, setVid] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [greetings, setGreetings] = useState<string[]>([]);
  const [birthdate, setBirthdate] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [notFound, setNotFound] = useState(false);
  const [tab, setTab] = useState<CardTabKey>("card");
  /** Which field is open for editing, `"<section>:<key>"`. One at a time. */
  const [editing, setEditing] = useState<string | null>(null);
  /** True while a whole-card write is in flight.
   *
   *  Every field save PUTs the ENTIRE card built from the `card` in state, so
   *  two of them in flight at once is a lost update: the second was built
   *  before the first landed, and overwrites it. One field being open is not
   *  enough to prevent that — opening a second field closes the first, which
   *  can happen while the first is still saving. So the page holds the lock,
   *  and every edit affordance is disabled until the write has been re-read. */
  const [saving, setSaving] = useState(false);
  const [cropOpen, setCropOpen] = useState(false);
  const [voiceAnchorCap, setVoiceAnchorCap] = useState<number | null>(null);
  const [module, setModule] = useState<ModuleDetail | null>(null);

  // campaign-scope state
  const [locked, setLocked] = useState<string | null>(null);
  const [worldVersions, setWorldVersions] = useState<VersionRef[]>([]);
  const [campaignState, setCampaignState] =
    useState<{ scenes: string[]; casefile: Casefile | null } | null>(null);

  // cross-record context
  const [worldGreetings, setWorldGreetings] = useState<Greeting[]>([]);
  const [imageAppearances, setImageAppearances] = useState<Appearance[]>([]);
  const [loreCount, setLoreCount] = useState<number | null>(null);
  const [roster, setRoster] = useState<CharacterSummary[]>([]);

  // import / localize / chub / lorebook progress, all of which report inline
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [localizeProg, setLocalizeProg] = useState<{ done: number; total: number } | null>(null);
  const [localizeMsg, setLocalizeMsg] = useState<string | null>(null);
  const [galleryProg, setGalleryProg] = useState<{ done: number; total: number } | null>(null);
  const [bookReview, setBookReview] = useState<LoreEntryDraft[] | null>(null);
  const [bookMsg, setBookMsg] = useState<string | null>(null);
  const bookKinds = useEntityKinds((bookReview?.length ?? 0) > 0);
  const [importFile, setImportFile] = useState<File | null>(null);
  const versionFileRef = useRef<HTMLInputElement>(null);

  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  // ---- reads -------------------------------------------------------------

  useEffect(() => {
    void (async () => {
      try {
        const c = await api.getConfig();
        if (live.current) setVoiceAnchorCap(c.voice_anchor_cap ?? null);
      } catch { /* no warning, which is the correct degradation */ }
    })();
  }, []);

  useEffect(() => {
    if (!campaign) {
      setWid(widParam);
      let alive = true;
      Promise.all([api.getWorldSheetsIndex(widParam), api.listModules()])
        .then(([index, installed]) => {
          const mid = index.default || index.modules[0] || installed[0]?.id || "";
          return mid ? api.readModule(mid) : null;
        })
        .then((m) => { if (alive) setModule(m); })
        .catch(() => { if (alive) setModule(null); });
      return () => { alive = false; };
    }
    let alive = true;
    api.getCampaign(cidParam)
      .then((c) => { if (alive) { setWid(c.meta.world); setCampaignName(c.meta.name); } })
      .catch(() => { if (alive) { setWid(""); setCampaignName(""); } });
    api.getCampaignModule(cidParam)
      .then(({ resolved }) => (resolved ? api.readModule(resolved) : null))
      .then((m) => { if (alive) setModule(m); })
      .catch(() => { if (alive) setModule(null); });
    return () => { alive = false; };
  }, [campaign, cidParam, widParam]);

  /** Install a version's card into the form state. The `?v=` in the URL is the
   *  source of truth for which one is open, so this follows it rather than
   *  setting it — see the effect below. */
  const loadVersion = useCallback((d: CharacterDetail, id: string) => {
    const v = d.versions.find((x) => x.id === id) ?? d.versions[0];
    if (!v) return;
    setVid(v.id);
    setCard(v.card);
    setGreetings(v.card.data.alternate_greetings ?? []);
    setLocalizeMsg(null);
    setLocalizeProg(null);
  }, []);

  const wanted = params.get("v") ?? "";

  /** Re-read the character and re-install the open version. Every write ends
   *  here; nothing else is allowed to patch `detail` in place, so what is on
   *  screen after a save is what the store actually holds. */
  const reload = useCallback(async (): Promise<CharacterDetail | null> => {
    try {
      const d = await api.readCharacter(scope, eid);
      if (!live.current) return null;
      setDetail(d);
      setBirthdate(d.meta.birthdate ?? "");
      setNotFound(false);
      return d;
    } catch (err: unknown) {
      if (live.current) { setError(err); setNotFound(true); }
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.id, eid]);

  useEffect(() => {
    void (async () => {
      const d = await reload();
      if (d) loadVersion(d, wanted || d.meta.default_version);
    })();
    // Deliberately NOT re-run on `wanted`: the version effect below owns that,
    // and re-reading the whole character on every version click would throw
    // away an in-flight edit for no new information.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);

  // Follow `?v=` when it changes under us — a version click, or the back button.
  useEffect(() => {
    if (!detail || !wanted || wanted === vid) return;
    if (!detail.versions.some((v) => v.id === wanted)) return;
    loadVersion(detail, wanted);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, detail]);

  // Campaign scope: which version this campaign is locked to, the world's
  // versions for the import picker, and the campaign's record of the character.
  /** Bumped by anything that changes what this campaign has locked, so the
   *  effect below re-runs. `refresh()` re-reads the CHARACTER; the lock lives
   *  in the appearance record, which is a different read — without this, Pick
   *  left the page still offering "+ New version" and the backend answered the
   *  button it should not have drawn with a 409. */
  const [lockEpoch, setLockEpoch] = useState(0);

  useEffect(() => {
    if (worldScope || !eid) { setCampaignState(null); setLocked(null); return; }
    let alive = true;
    setCampaignState(null);
    void (async () => {
      const appearances = await api.listAppearances(scope.id).catch(() => []);
      if (!alive) return;
      const entry = appearances.find((r) => r.kind === "characters" && r.id === eid);
      setLocked(entry?.version ?? null);
      const scenes = entry?.scenes ?? [];
      if (scenes.length === 0) { setCampaignState({ scenes, casefile: null }); return; }
      // There is no campaign-scoped casefile route; the one that exists is
      // nested under a scene and checks the character is cast in it, which is
      // its access control as much as its correctness condition. The record it
      // returns is campaign-scoped (`store/casefile.build` says so), so asking
      // through the newest scene they are cast in returns the campaign's
      // current state and the membership check passes by construction.
      try {
        const casefile = await api.getCasefile(scope.id, scenes[scenes.length - 1], "characters", eid);
        if (alive) setCampaignState({ scenes, casefile });
      } catch {
        // A scene deleted out from under the appearance record, a hand-edited
        // state file: the section still knows which scenes they are in and says
        // only that, rather than claiming there is no recorded state.
        if (alive) setCampaignState({ scenes, casefile: null });
      }
    })();
    return () => { alive = false; };
  }, [worldScope, scope.id, eid, lockEpoch]);

  useEffect(() => {
    if (worldScope || !wid || !eid) { setWorldVersions([]); return; }
    let alive = true;
    api.readCharacter({ kind: "world", id: wid }, eid)
      .then((w) => { if (alive) setWorldVersions(w.versions.map((v) => ({ id: v.id, name: v.name }))); })
      .catch(() => { if (alive) setWorldVersions([]); });
    return () => { alive = false; };
  }, [worldScope, wid, eid]);

  useEffect(() => {
    if (!eid) return;
    let alive = true;
    if (wid) {
      api.listImageAppearances(wid, eid)
        .then((a) => { if (alive) setImageAppearances(a); })
        .catch(() => { if (alive) setImageAppearances([]); });
    }
    api.listGreetings(scope).then((g) => { if (alive) setWorldGreetings(g); })
      .catch(() => { if (alive) setWorldGreetings([]); });
    api.listCharacters(scope).then((r) => { if (alive) setRoster(r); })
      .catch(() => { if (alive) setRoster([]); });
    api.listEntities(scope, "lore")
      .then((items) => {
        if (!alive) return;
        const ref = `characters:${eid}`;
        setLoreCount(items.filter((e) =>
          (e.owners ?? "").split(",").map((o) => o.trim()).includes(ref)).length);
      })
      // A count is an ornament on a tab; failing to read one must not cost the
      // tab, which still opens the panel that reports the failure itself.
      .catch(() => { if (alive) setLoreCount(null); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.id, wid, eid]);

  // A review describes one version's stored book; rows left up across a version
  // switch would commit the previous card's entries. The counter invalidates a
  // PARSE STILL IN FLIGHT across that switch too — clearing the rows alone
  // would let its late response repopulate them with the old version's
  // entries, under a version whose own button is showing.
  const bookReq = useRef(0);
  useEffect(() => { bookReq.current++; setBookReview(null); setBookMsg(null); }, [vid]);
  useEffect(() => { setEditing(null); }, [vid]);

  // ---- writes ------------------------------------------------------------

  /** Save one field. Patches the card the page is holding and PUTs the whole
   *  thing, which is the only write the version route offers — so it matters
   *  that exactly one field is ever open, and that the result is re-read rather
   *  than assumed. */
  async function saveField(patch: Record<string, unknown>): Promise<boolean> {
    if (!detail || !card || saving) return false;
    setError(null);
    setSaving(true);
    const next = { ...card, data: { ...card.data, ...patch } };
    try {
      await api.updateVersion(scope, detail.meta.id, vid, buildCard(next, greetings));
      // #13: the card's name is not the character's. The grid tile, the cast
      // panel and every `meta.name` prompt section read the CONTAINER name, so
      // a renamed card has to carry the new name over or the two diverge for
      // good. Only from the default version — a sibling version's card name is
      // that version's business — and only when this edit actually changed it.
      if ("name" in patch) {
        const stored = (detail.versions.find((v) => v.id === vid)?.card.data.name ?? "").trim();
        const renamed = typeof patch.name === "string" ? patch.name.trim() : "";
        if (renamed && renamed !== stored && renamed !== detail.meta.name
            && vid === detail.meta.default_version) {
          await api.setCharacterName(scope, detail.meta.id, renamed);
        }
      }
      const d = await reload();
      if (d) loadVersion(d, vid);
      return true;
    } catch (err: unknown) {
      setError(err);
      return false;
    } finally {
      if (live.current) setSaving(false);
    }
  }

  /** The greeting list is the one card field that is not a string, so it saves
   *  through its own path rather than `saveField`'s patch. */
  async function saveGreetings(next: string[]): Promise<boolean> {
    if (!detail || !card || saving) return false;
    setError(null);
    setSaving(true);
    try {
      await api.updateVersion(scope, detail.meta.id, vid, buildCard(card, next));
      setGreetings(next);
      const d = await reload();
      if (d) loadVersion(d, vid);
      return true;
    } catch (err: unknown) {
      setError(err);
      return false;
    } finally {
      if (live.current) setSaving(false);
    }
  }

  async function refresh() {
    const d = await reload();
    if (d) loadVersion(d, vid);
  }

  function openVersion(id: string) {
    const next = new URLSearchParams(params);
    next.set("v", id);
    setParams(next, { replace: true });
  }

  async function saveBirthdate(value: string) {
    setBirthdate(value);
    try {
      await api.setCharacterBirthdate(wid, eid, value);
    } catch (err: unknown) { setError(err); }
  }

  async function runLocalize(version: string) {
    setLocalizeMsg(null);
    setLocalizeProg({ done: 0, total: 0 });
    let finalMsg = "";
    try {
      await api.localizeImages(wid, eid, version, (e) => {
        if (e.error) finalMsg = `Localize failed: ${e.error.detail}`;
        else if (e.summary) {
          const s = e.summary;
          finalMsg = s.total === 0 ? "No remote images found"
            : `Localized ${s.localized} image${s.localized === 1 ? "" : "s"}`
              + (s.skipped ? `, skipped ${s.skipped}` : "")
              + (s.failed ? `, ${s.failed} failed` : "")
              + (s.capped ? " (download cap reached)" : "");
        } else if (typeof e.done === "number") setLocalizeProg({ done: e.done, total: e.total ?? 0 });
        else if (typeof e.total === "number") setLocalizeProg({ done: 0, total: e.total });
      });
      await refresh();
    } catch (err: unknown) {
      finalMsg = `Localize failed: ${errorText(err)}`;
    } finally {
      if (live.current) {
        setLocalizeProg(null);
        if (finalMsg) setLocalizeMsg(finalMsg);
      }
    }
  }

  function onImportVersionFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) setImportFile(file);
  }

  async function confirmImportVersion(choice: ImportChoice) {
    const file = importFile;
    setImportFile(null);
    if (!file) return;
    setError(null);
    try {
      const { version } = await api.importCharacter(
        wid, file, formatOf(file), eid, choice.versionName || undefined);
      const d = await reload();
      if (d) loadVersion(d, version);
      openVersion(version);
      await runLocalize(version);
    } catch (err: unknown) { setError(err); }
  }

  async function runPick() {
    if (!detail) return;
    if (!window.confirm(`Lock '${detail.meta.name}' to this version? Other versions are removed from the campaign.`)) return;
    try {
      await api.pickVersion(scope.id, "characters", eid, vid);
      setLockEpoch((n) => n + 1);
      await refresh();
    } catch (err: unknown) { setError(err); }
  }

  async function importFromWorld(fromVid: string) {
    if (!window.confirm("Replace the locked version with the world's copy?")) return;
    try {
      await api.importVersion(scope.id, "characters", eid, fromVid);
      setLockEpoch((n) => n + 1);
      await refresh();
    } catch (err: unknown) { setError(err); }
  }

  async function deleteCharacter() {
    if (!detail) return;
    // The two scopes delete different things, and the prompt has to say which:
    // in a campaign this removes the character from that campaign only.
    const where = worldScope ? "the library" : "this campaign";
    if (!window.confirm(`Delete character '${detail.meta.name}' from ${where}?`)) return;
    try {
      await api.deleteCharacter(scope, eid);
      navigate(charactersHref(scope));
    } catch (err: unknown) { setError(err); }
  }

  async function reviewBook() {
    const stored = detail?.versions.find((v) => v.id === vid)?.card;
    if (!stored) return;
    setBookMsg(null);
    try {
      const mine = ++bookReq.current;
      const file = new File([JSON.stringify(stored)], "card.json", { type: "application/json" });
      const { entries } = await api.lorebookParse(wid, file, "json");
      if (live.current && mine === bookReq.current) setBookReview(entries);
    } catch (err: unknown) { setError(err); }
  }

  async function commitBook() {
    if (!bookReview) return;
    try {
      const { created } = await api.lorebookImport(wid, bookReview);
      setBookReview(null);
      // `lorebook.commit` drops entries already in the world, so a second
      // import of an unchanged book legitimately creates nothing. "Imported 0
      // entries" reads as a failure; say what actually happened instead.
      setBookMsg(created.length === 0
        ? "Already in the world — nothing new to import"
        : `Imported ${created.length} entr${created.length === 1 ? "y" : "ies"}`);
    } catch (err: unknown) { setError(err); }
  }

  // ---- render ------------------------------------------------------------

  // A trailing slash matches `:eid` as the empty string, which is a request for
  // the roster rather than for a character nobody named.
  if (!eid) return <Navigate to={charactersHref(scope)} replace />;

  if (notFound || !detail || !card) {
    return (
      <PageShell columnLabel="Character" column={
        <Link className="column-back" to={charactersHref(scope)}>‹ All characters</Link>
      }>
        <div className="screen-head"><h1 className="screen-title">Character</h1></div>
        {error != null
          ? <div className="banner"><ErrorNote err={error} /></div>
          : <p className="field-hint">Reading…</p>}
      </PageShell>
    );
  }

  const version = detail.versions.find((v) => v.id === vid);
  const name = card.data.name || detail.meta.name;
  const hasAvatar = (version?.images ?? []).includes("avatar");
  const avatarFocus = version?.avatar_focus ?? null;
  const imageTokens = version?.image_v ?? {};
  const galleryImages = (version?.images ?? [])
    .filter((n) => n.startsWith("gallery_"))
    .sort((a, b) => Number(a.slice("gallery_".length)) - Number(b.slice("gallery_".length)));
  const firstMes = (card.data.first_mes as string) ?? "";
  const greetingCount = (firstMes.trim() ? 1 : 0) + greetings.length;
  const artCount = (hasAvatar ? 1 : 0) + galleryImages.length;
  // `scope.id` is a slug and a poor heading, but a heading with the wrong
  // subject would be worse than a plain one, so it stands in only when the
  // campaign's name could not be read.
  const campaignLabel = campaignName || scope.id;

  const column = <>
    {/* `reveal` is what stops the campaign grid's appeared filter swallowing a
        character reached by a link and never played — landing on a grid that
        hides them reads as the record having been deleted. */}
    <Link className="column-back" to={charactersHref(scope)}
          state={{ reveal: eid }}>‹ All characters</Link>

    {hasAvatar
      ? <button className="identity-art avatar-crop-btn" type="button"
                aria-label="Adjust avatar crop" title="Adjust avatar crop"
                onClick={() => setCropOpen(true)}>
          <img className="detail-avatar" alt="" style={focusStyle(avatarFocus)}
               src={avatarSrc(scope, eid, vid, imageTokens.avatar)} />
        </button>
      : <div className="identity-art identity-art-empty" aria-hidden>{initialsOf(name)}</div>}

    <h2 className="identity-name">{name}</h2>
    {card.data.creator ? <div className="detail-byline">by {String(card.data.creator)}</div> : null}

    {worldScope && <TaglineSection wid={wid} cid={eid} onError={setError} />}

    <VersionList scope={scope} detail={detail} vid={vid} locked={locked}
                 campaignLabel={campaignLabel} worldVersions={worldVersions}
                 onPick={() => void runPick()}
                 onImportFromWorld={(v) => void importFromWorld(v)}
                 onOpenVersion={openVersion}
                 onImportFile={() => versionFileRef.current?.click()}
                 busy={saving}
                 onChanged={refresh} onError={setError} />
    <input ref={versionFileRef} type="file" accept=".json,.png,.charx" hidden
           aria-label="Import version" onChange={onImportVersionFile} />

    {!worldScope && <CampaignSection label={campaignLabel} name={name} state={campaignState} />}

    <VoiceAnchorSection key={`${scope.kind}:${scope.id}:${eid}`}
                        scope={scope} cid={eid} cap={voiceAnchorCap} onError={setError} />

    {worldScope && (
      <ColumnSection label="Birthdate">
        {/* Persist only complete dates: the picker emits "" for every
            intermediate state, which must never blank the stored value. */}
        <CalendarDatePicker scope={{ kind: "world", id: wid }} value={birthdate}
                            onChange={(v) => { setBirthdate(v); if (v) void saveBirthdate(v); }}
                            ariaLabel="Birthdate" />
        {birthdate && <button className="subtle" type="button"
                              onClick={() => void saveBirthdate("")}>Clear</button>}
      </ColumnSection>
    )}

    {/* An emergent NPC's way into the library (#60). Only promote can apply to
        an actor, and `libraryStatus` is what says so, so this renders nothing
        for a character the campaign merely inherits. */}
    {!worldScope && (
      <LibraryPanel key={`${scope.id}:characters:${eid}`}
                    cid={scope.id} kind="characters" id={eid}
                    onMoved={() => { void refresh(); }} />
    )}
  </>;

  // Pinned, and the argument the whole page is making. Which way it points
  // depends on the scope, and the two are opposites: a world record is shared
  // by every campaign built on this world, while a campaign's copy is a fork
  // that leaves the world's original alone.
  const footer = (
    <p className={"reach-warning" + (worldScope ? " shared" : "")}>
      {worldScope
        ? "Edits here reach every campaign using this world."
        : "This campaign's own copy. Edits here leave the world record untouched."}
    </p>
  );

  return (
    <PageShell column={column} footer={footer} columnLabel="Character" className="character-page">
      {cropOpen && hasAvatar && (
        <AvatarFocusPicker src={avatarSrc(scope, eid, vid, imageTokens.avatar)}
                           initial={avatarFocus ?? 50}
                           onSave={(f) => void (async () => {
                             setCropOpen(false);
                             try {
                               await api.setAvatarFocus(scope, eid, vid, f);
                               await refresh();
                             } catch (err: unknown) { setError(err); }
                           })()}
                           onClose={() => setCropOpen(false)} />
      )}
      {importFile && (
        <ImportVersionDialog fileName={importFile.name} characters={roster}
                             fixedTo={{ id: eid, name: detail.meta.name }}
                             onCancel={() => setImportFile(null)}
                             onConfirm={(c) => void confirmImportVersion(c)} />
      )}

      <div className="screen-head">
        <div>
          <div className="eyebrow">
            {worldScope ? "World record · shared · sent to the model" : `Campaign copy · ${campaignLabel}`}
          </div>
          <h1 className="screen-title">{name}</h1>
        </div>
        <div className="screen-head-actions">
          {worldScope && <ExportMenu wid={wid} cid={eid} vid={vid} />}
          <button className="subtle" onClick={() => void deleteCharacter()}>Delete</button>
        </div>
      </div>

      {error != null && <div className="banner"><ErrorNote err={error} /></div>}
      {importMsg && <p className="field-hint">{importMsg}</p>}

      <div className="card-tabs" role="tablist" aria-label="Card">
        {([["card", "Card", null],
           ["lore", "Lore", loreCount],
           ["greetings", "Greetings", greetingCount],
           ["art", "Art", artCount]] as [CardTabKey, string, number | null][])
          .map(([key, label, count]) => (
            <button key={key} role="tab" aria-selected={tab === key}
                    className={"tab" + (tab === key ? " active" : "")}
                    onClick={() => setTab(key)}>
              {label}{count === null ? "" : ` ${count}`}
            </button>
          ))}
      </div>

      <div className="card-pane-body" role="tabpanel">
        {tab === "card" && (
          <CardTab scope={scope} wid={wid} cid={eid} vid={vid} card={card} detail={detail}
                   worldGreetings={worldGreetings} module={module}
                   editing={editing} onEditingChange={setEditing} busy={saving}
                   onSaveField={saveField} onRefresh={refresh} onError={setError}
                   galleryProg={galleryProg} setGalleryProg={setGalleryProg}
                   setImportMsg={setImportMsg}
                   onOpenGreeting={(gid) => navigate(
                     worldScope ? `/worlds/${wid}?section=greetings&id=${gid}`
                                : `/campaigns/${scope.id}/world?section=greetings&id=${gid}`)}
                   bookCount={version?.importable_lore ?? 0}
                   bookReview={bookReview} bookKinds={bookKinds} bookMsg={bookMsg}
                   onReviewBook={() => void reviewBook()}
                   onCommitBook={() => void commitBook()}
                   onPatchBook={(i, patch) => setBookReview(
                     (cur) => cur!.map((e, j) => (j === i ? { ...e, ...patch } : e)))}
                   onCancelBook={() => setBookReview(null)} />
        )}

        {tab === "lore" && (
          <OwnedLorePanel
            scope={scope}
            ownerRef={`characters:${eid}`}
            onOpenEntry={(id) => navigate(worldScope
              ? `/worlds/${wid}?section=lore&id=${id}`
              : `/campaigns/${scope.id}/world?section=lore&id=${id}`)}
            onNewEntry={() => navigate(worldScope
              ? `/worlds/${wid}?section=lore&owner=characters:${eid}`
              : `/campaigns/${scope.id}/world?section=lore&owner=characters:${eid}`)}
          />
        )}

        {tab === "greetings" && (
          <GreetingsTab name={name} firstMes={firstMes} greetings={greetings}
                        editing={editing} onEditingChange={setEditing} busy={saving}
                        onSaveFirstMes={(v) => saveField({ first_mes: v })}
                        onSaveGreetings={saveGreetings} />
        )}

        {tab === "art" && (
          <ArtTab scope={scope} wid={wid} cid={eid} vid={vid}
                  hasAvatar={hasAvatar} galleryImages={galleryImages}
                  imageTokens={imageTokens}
                  descriptions={version?.image_descriptions ?? {}}
                  appearances={imageAppearances}
                  worldScope={worldScope}
                  localizeProg={localizeProg} localizeMsg={localizeMsg}
                  onLocalize={() => void runLocalize(vid)}
                  onRefresh={refresh} onError={setError}
                  onOpenGreeting={(gid) => navigate(worldScope
                    ? `/worlds/${wid}?section=greetings&id=${gid}`
                    : `/campaigns/${scope.id}/world?section=greetings&id=${gid}`)} />
        )}
      </div>
    </PageShell>
  );
}

export { characterHref };
