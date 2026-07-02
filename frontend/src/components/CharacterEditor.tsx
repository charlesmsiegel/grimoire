import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Card, type CharacterDetail, type CharacterSummary, type ChubImportResult, type ChubUnlinkedVersion } from "../api/client";
import { Field } from "./Field";
import { OwnedLorePanel } from "./OwnedLorePanel";
import { TaglinePrompt } from "./TaglinePrompt";

const TEXT_FIELDS: { key: string; label: string; area?: boolean }[] = [
  { key: "description", label: "Description", area: true },
  { key: "personality", label: "Personality", area: true },
  { key: "scenario", label: "Scenario", area: true },
  { key: "first_mes", label: "First message", area: true },
  { key: "mes_example", label: "Example dialogue", area: true },
  { key: "system_prompt", label: "System prompt", area: true },
  { key: "post_history_instructions", label: "Post-history instructions", area: true },
  { key: "creator_notes", label: "Creator notes", area: true },
];

function describeChubResult(result: ChubImportResult): string {
  const parts: string[] = [];
  if (result.gallery.attempted > 0) {
    parts.push(`${result.gallery.stored}/${result.gallery.attempted} gallery image${result.gallery.attempted === 1 ? "" : "s"}`);
  }
  if (result.lore.lorebooks_found > 0) {
    const n = result.lore.created.length;
    parts.push(`${result.lore.lorebooks_found} lorebook${result.lore.lorebooks_found === 1 ? "" : "s"} (${n} ${n === 1 ? "entry" : "entries"}) added to world lore`);
  }
  const lead = result.updated ? "Updated this version from URL" : "Downloaded from URL";
  return parts.length ? `${lead} — ${parts.join(", ")}` : lead;
}

type Mode = "grid" | "detail" | "edit";

export function CharacterEditor({ wid, resetSignal, focus, onOpenLore }:
  { wid: string; resetSignal?: number; focus?: { cid: string; vid: string } | null;
    onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void }) {
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [vid, setVid] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [greetings, setGreetings] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("grid");
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const versionFileRef = useRef<HTMLInputElement>(null);
  const avatarRef = useRef<HTMLInputElement>(null);
  const [avatarBust, setAvatarBust] = useState(0);
  const [bookMsg, setBookMsg] = useState<string | null>(null);
  const [localizeProg, setLocalizeProg] = useState<{ done: number; total: number } | null>(null);
  const [localizeMsg, setLocalizeMsg] = useState<string | null>(null);
  const [galleryProg, setGalleryProg] = useState<{ done: number; total: number } | null>(null);
  const [unlinkedVersions, setUnlinkedVersions] = useState<ChubUnlinkedVersion[] | null>(null);
  const [bulkLocalize, setBulkLocalize] = useState<{ current: number; cards: number } | null>(null);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [birthdate, setBirthdate] = useState("");
  const [tagline, setTagline] = useState("");
  const [taglineBusy, setTaglineBusy] = useState(false);
  const taglineReq = useRef(0);
  const [taglinePrompt, setTaglinePrompt] = useState<{ cid: string; name: string } | null>(null);

  const reload = useCallback(() => api.listCharacters(wid).then(setChars), [wid]);
  useEffect(() => {
    reload();
  }, [reload]);

  // re-clicking the Characters tab (resetSignal bumps) returns to the grid
  useEffect(() => {
    setMode("grid");
    setDetail(null);
    setCard(null);
  }, [resetSignal]);

  // arrived via a present-character link: open that character at the given version
  useEffect(() => {
    if (focus) focusCharacter(focus.cid, focus.vid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasAvatar = (detail && card)
    ? (detail.versions.find((v) => v.id === vid)?.images ?? []).includes("avatar")
    : false;
  const chubSource = detail?.versions.find((v) => v.id === vid)?.chub_source ?? "";
  const isChub = detail?.versions.find((v) => v.id === vid)?.is_chub ?? false;
  const galleryImages = (detail?.versions.find((v) => v.id === vid)?.images ?? [])
    .filter((n) => n.startsWith("gallery_"))
    .sort((a, b) => Number(a.slice("gallery_".length)) - Number(b.slice("gallery_".length)));

  function loadVersion(d: CharacterDetail, id: string) {
    const v = d.versions.find((x) => x.id === id) ?? d.versions[0];
    setVid(v.id);
    setCard(v.card);
    setGreetings(v.card.data.alternate_greetings ?? []);
    setBookMsg(null);
    setLocalizeMsg(null);
    setLocalizeProg(null);
  }

  // Download every remote image referenced in the card's text into the local
  // asset store and rewrite the text to it, driving a progress bar from the
  // server's SSE events. Reloads the version afterward so the rewrites show.
  async function runLocalize(cid: string, version: string) {
    setLocalizeMsg(null);
    setLocalizeProg({ done: 0, total: 0 });
    let finalMsg = "";
    try {
      await api.localizeImages(wid, cid, version, (e) => {
        if (e.error) {
          finalMsg = `Localize failed: ${e.error.detail}`;
        } else if (e.summary) {
          const s = e.summary;
          finalMsg =
            s.total === 0
              ? "No remote images found"
              : `Localized ${s.localized} image${s.localized === 1 ? "" : "s"}` +
                (s.skipped ? `, skipped ${s.skipped}` : "") +
                (s.failed ? `, ${s.failed} failed` : "") +
                (s.capped ? " (download cap reached)" : "");
        } else if (typeof e.done === "number") {
          setLocalizeProg({ done: e.done, total: e.total ?? 0 });
        } else if (typeof e.total === "number") {
          setLocalizeProg({ done: 0, total: e.total });
        }
      });
      // show the rewritten text + any new images for the version we localized
      const d = await api.readCharacter(wid, cid);
      setDetail(d);
      loadVersion(d, version);  // clears localizeMsg, so set the summary after it
    } catch (err: any) {
      finalMsg = `Localize failed: ${err.detail ?? String(err)}`;
    } finally {
      setLocalizeProg(null);
      if (finalMsg) setLocalizeMsg(finalMsg);
    }
  }

  // Localize a batch of freshly-imported cards back-to-back, accumulating one
  // aggregate summary. Stays on the grid (no card is open), so progress and the
  // result render in the grid toolbar rather than a single card's localize block.
  async function runBulkLocalize(cards: { cid: string; version: string }[]) {
    setImportMsg(null);
    let localized = 0, skipped = 0, failed = 0;
    for (let i = 0; i < cards.length; i++) {
      setBulkLocalize({ current: i + 1, cards: cards.length });
      try {
        await api.localizeImages(wid, cards[i].cid, cards[i].version, (e) => {
          if (e.summary) {
            localized += e.summary.localized;
            skipped += e.summary.skipped;
            failed += e.summary.failed;
          }
        });
      } catch {
        failed += 1;  // a whole card's localize failing shouldn't abort the batch
      }
    }
    setBulkLocalize(null);
    setImportMsg(
      `Localized ${localized} image${localized === 1 ? "" : "s"} across ${cards.length} cards` +
      (skipped ? `, skipped ${skipped}` : "") + (failed ? `, ${failed} failed` : ""),
    );
  }

  const bookCount = card?.data.character_book?.entries?.length ?? 0;
  // localize reads/writes the server-stored card, so running it with unsaved
  // editor changes would discard them — guard the button when the form is dirty.
  // Compare through buildCard()'s normalization (it always rewrites
  // alternate_greetings) so an unedited card isn't flagged as dirty.
  const storedCard = detail?.versions.find((v) => v.id === vid)?.card;
  const normalizedStored = storedCard && {
    ...storedCard,
    data: { ...storedCard.data, alternate_greetings: (storedCard.data.alternate_greetings ?? []).filter((g) => g.trim() !== "") },
  };
  const dirty = !!(card && normalizedStored && JSON.stringify(buildCard()) !== JSON.stringify(normalizedStored));

  function localizeControls(blocked: boolean, blockedHint?: string) {
    if (!detail) return null;
    return (
      <div className="localize-block">
        <button className="subtle" type="button" disabled={!!localizeProg || blocked}
                onClick={() => runLocalize(detail.meta.id, vid)}>
          {localizeProg ? "Localizing…" : "Localize images"}
        </button>
        {localizeProg && (
          <div className="localize-progress">
            <progress value={localizeProg.done} max={localizeProg.total || 1} />
            <span className="field-hint">{localizeProg.done}/{localizeProg.total}</span>
          </div>
        )}
        {!localizeProg && blocked && blockedHint && <span className="field-hint">{blockedHint}</span>}
        {localizeMsg && <span className="field-hint">{localizeMsg}</span>}
      </div>
    );
  }

  async function select(cid: string) {
    setError(null);
    const d = await api.readCharacter(wid, cid);
    setDetail(d);
    setBirthdate(d.meta.birthdate ?? "");
    loadVersion(d, d.meta.default_version);
    loadTagline(cid);
    return d;
  }

  // Fetch the tagline independently of the (synchronous) card load so a slow GET
  // never wedges stale card data next to fresh meta during navigation. The req token
  // drops a slow earlier response so selecting A then B can't leave A's tagline on B.
  function loadTagline(cid: string) {
    const req = ++taglineReq.current;
    setTagline("");
    api.getCharacterTagline(wid, cid)
      .then((r) => { if (taglineReq.current === req) setTagline(r.tagline); })
      .catch(() => { if (taglineReq.current === req) setTagline(""); });
  }

  async function saveTagline() {
    if (!detail) return;
    try {
      await api.setCharacterTagline(wid, detail.meta.id, tagline.trim());
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function regenerateTagline() {
    if (!detail) return;
    setTaglineBusy(true);
    try {
      const r = await api.generateCharacterTagline(wid, detail.meta.id);
      setTagline(r.tagline);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setTaglineBusy(false);
    }
  }

  async function saveBirthdate(value: string) {
    if (!detail) return;
    setBirthdate(value);
    try {
      await api.setCharacterBirthdate(wid, detail.meta.id, value);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function openDetail(cid: string) {
    window.scrollTo(0, 0);
    const d = await select(cid);
    setMode("detail");
    return d;
  }

  async function focusCharacter(cid: string, vid: string) {
    window.scrollTo(0, 0);
    setError(null);
    const d = await api.readCharacter(wid, cid);
    setDetail(d);
    setBirthdate(d.meta.birthdate ?? "");
    loadVersion(d, d.versions.some((v) => v.id === vid) ? vid : d.meta.default_version);
    loadTagline(cid);
    setMode("detail");
  }

  async function openEdit(cid: string) {
    window.scrollTo(0, 0);
    await select(cid);
    setMode("edit");
  }

  function backToGrid() {
    window.scrollTo(0, 0);
    setDetail(null);
    setCard(null);
    setMode("grid");
    reload();
  }

  function setField(key: string, value: unknown) {
    if (!card) return;
    setCard({ ...card, data: { ...card.data, [key]: value } });
  }

  function buildCard(): Card {
    return { ...card!, data: { ...card!.data, alternate_greetings: greetings.filter((g) => g.trim() !== "") } };
  }

  async function newCharacter() {
    const name = window.prompt("New character name?")?.trim();
    if (!name) return;
    const { character } = await api.createCharacter(wid, { name });
    await reload();
    await openEdit(character);
  }

  async function save() {
    if (!detail || !card) return;
    setError(null);
    try {
      await api.updateVersion(wid, detail.meta.id, vid, buildCard());
      await select(detail.meta.id);
      await reload();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function addVersion() {
    if (!detail) return;
    const name = window.prompt("New version name?")?.trim();
    if (!name) return;
    const { version } = await api.createVersion(wid, detail.meta.id, { name, card: buildCard() });
    await select(detail.meta.id);
    loadVersion(await api.readCharacter(wid, detail.meta.id), version);
  }

  async function onImportVersion(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    try {
      const { version } = await api.importCharacter(wid, file, formatOf(file), detail.meta.id);
      const d = await api.readCharacter(wid, detail.meta.id);
      setDetail(d);
      loadVersion(d, version);
      await reload();
      await runLocalize(detail.meta.id, version);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }

  async function setDefault() {
    if (!detail) return;
    await api.setDefaultVersion(wid, detail.meta.id, vid);
    await select(detail.meta.id);
  }

  async function deleteCharacter(cid: string, name: string) {
    if (!window.confirm(`Delete character '${name}'?`)) return;
    await api.deleteCharacter(wid, cid);
    backToGrid();
  }

  async function importBook() {
    if (!detail) return;
    setBookMsg(null);
    try {
      const { created } = await api.importCharacterBook(wid, detail.meta.id, vid);
      setBookMsg(`Imported ${created.length} entr${created.length === 1 ? "y" : "ies"} to world lore`);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function onAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    try {
      await api.putImage(wid, detail.meta.id, vid, "avatar", file);
      await select(detail.meta.id);
      await reload();
      setAvatarBust((n) => n + 1);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }

  async function removeAvatar() {
    if (!detail) return;
    await api.deleteImage(wid, detail.meta.id, vid, "avatar");
    await select(detail.meta.id);
    await reload();
    setAvatarBust((n) => n + 1);
  }

  function formatOf(file: File): string {
    const ext = file.name.split(".").pop()?.toLowerCase();
    return ext === "png" ? "png" : ext === "charx" ? "charx" : "json";
  }

  async function onImport(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    setError(null);
    setImportMsg(null);
    const failures: string[] = [];
    const imported: { cid: string; version: string }[] = [];
    for (const file of files) {
      try {
        const { character, version } = await api.importCharacter(wid, file, formatOf(file));
        imported.push({ cid: character, version });
      } catch (err: any) {
        failures.push(`${file.name}: ${err.detail ?? String(err)}`);
      }
    }
    e.target.value = "";
    await reload();
    if (failures.length) setError(`Could not import — ${failures.join("; ")}`);
    else if (imported.length === 1) {
      // single import: open the card so its localize progress shows inline
      const d = await openDetail(imported[0].cid);
      setTaglinePrompt({ cid: imported[0].cid, name: d.meta.name });
      await runLocalize(imported[0].cid, imported[0].version);
    } else if (imported.length > 1) {
      await runBulkLocalize(imported);
    }
  }

  async function downloadFromChub() {
    const url = window.prompt("Card URL (chub.ai link or a direct URL)?")?.trim();
    if (!url) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, url);
      await reload();
      const d = await openDetail(result.character);
      setTaglinePrompt({ cid: result.character, name: d.meta.name });
      setImportMsg(describeChubResult(result));
      await runLocalize(result.character, result.version);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function downloadVersionFromChub() {
    if (!detail) return;
    const url = window.prompt("Card URL (chub.ai link or a direct URL)?")?.trim();
    if (!url) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, url, detail.meta.id, vid);
      const d = await api.readCharacter(wid, detail.meta.id);
      setDetail(d);
      loadVersion(d, result.version);
      await reload();
      setImportMsg(describeChubResult(result));
      await runLocalize(detail.meta.id, result.version);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  // Linking is per-version, and the detail view may be showing a
  // non-default version -- reload via loadVersion(d, vid) rather than
  // select() (which always snaps back to the default version).
  async function linkChub() {
    if (!detail) return;
    const url = window.prompt("Card URL (chub.ai link or a direct URL)?")?.trim();
    if (!url) return;
    setError(null);
    try {
      await api.setCharacterChubSource(wid, detail.meta.id, vid, url);
      const d = await api.readCharacter(wid, detail.meta.id);
      setDetail(d);
      loadVersion(d, vid);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function unlinkChub() {
    if (!detail) return;
    setError(null);
    try {
      await api.clearCharacterChubSource(wid, detail.meta.id, vid);
      const d = await api.readCharacter(wid, detail.meta.id);
      setDetail(d);
      loadVersion(d, vid);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function downloadChubGallery() {
    if (!detail) return;
    setError(null);
    setImportMsg(null);
    setGalleryProg({ done: 0, total: 0 });
    let finalMsg = "";
    try {
      await api.downloadCharacterChubGallery(wid, detail.meta.id, vid, (e) => {
        if (e.error) {
          finalMsg = `Gallery download failed: ${e.error.detail}`;
        } else if (e.summary) {
          const s = e.summary;
          finalMsg =
            s.attempted === 0
              ? "No gallery images found on chub.ai"
              : `${s.stored}/${s.attempted} gallery image${s.attempted === 1 ? "" : "s"} downloaded`;
        } else if (typeof e.done === "number") {
          setGalleryProg({ done: e.done, total: e.total ?? 0 });
        } else if (typeof e.total === "number") {
          setGalleryProg({ done: 0, total: e.total });
        }
      });
      // refresh so newly downloaded images show without navigating away and back
      const d = await api.readCharacter(wid, detail.meta.id);
      setDetail(d);
      loadVersion(d, vid);
      setAvatarBust((n) => n + 1); // bust the cache in case a re-download overwrote images in place
    } catch (err: any) {
      finalMsg = `Gallery download failed: ${err.detail ?? String(err)}`;
    } finally {
      setGalleryProg(null);
      if (finalMsg) setImportMsg(finalMsg);
    }
  }

  async function downloadChubLorebooks() {
    if (!detail) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.downloadCharacterChubLorebooks(wid, detail.meta.id, vid);
      const n = result.created.length;
      setImportMsg(
        result.lorebooks_found === 0
          ? "No linked lorebooks found on chub.ai"
          : `${result.lorebooks_found} lorebook${result.lorebooks_found === 1 ? "" : "s"} (${n} ${n === 1 ? "entry" : "entries"}) added to world lore`,
      );
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function checkChubLinks() {
    setError(null);
    try {
      const { versions } = await api.findChubUnlinked(wid);
      setUnlinkedVersions(versions);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  const avatarSrc = (cid: string, version: string, bust = false) =>
    api.imageUrl(wid, cid, version, "avatar") + (bust ? `?v=${avatarBust}` : "");

  if (mode === "grid" || !detail || !card) {
    return (
      <div className="character-editor">
        {taglinePrompt && (
          <TaglinePrompt wid={wid} cid={taglinePrompt.cid} name={taglinePrompt.name}
                         onSaved={(t) => setTagline(t)}
                         onClose={() => setTaglinePrompt(null)} />
        )}
        <div className="grid-toolbar">
          <button className="primary" onClick={newCharacter}>+ New character</button>
          <button className="subtle" onClick={() => fileRef.current?.click()}>Import card</button>
          <input ref={fileRef} type="file" accept=".json,.png,.charx" multiple hidden aria-label="Import character card" onChange={onImport} />
          <button className="subtle" onClick={downloadFromChub}>Download from URL</button>
          <button className="subtle" onClick={checkChubLinks}>Check chub.ai links</button>
          {bulkLocalize && (
            <span className="field-hint">Localizing card {bulkLocalize.current}/{bulkLocalize.cards}…</span>
          )}
          {!bulkLocalize && importMsg && <span className="field-hint">{importMsg}</span>}
        </div>
        {unlinkedVersions !== null && (
          <div className="chub-unlinked-list">
            {unlinkedVersions.length === 0 ? (
              <div className="field-hint">All versions are linked to chub.ai</div>
            ) : (
              <>
                <div className="field-hint">
                  {unlinkedVersions.length} version{unlinkedVersions.length === 1 ? "" : "s"} not linked to chub.ai:
                </div>
                <div className="chips">
                  {unlinkedVersions.map((u) => (
                    <button key={`${u.character}:${u.version}`} className="chip"
                            onClick={() => focusCharacter(u.character, u.version)}>
                      {u.character_name} ({u.version_name})
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
        {error && <div className="banner">{error}</div>}
        {chars.length === 0 ? (
          <div className="editor-empty">No characters yet. Create one or import a card.</div>
        ) : (
          <div className="char-grid">
            {chars.map((c) => (
              <div key={c.id} className="char-card">
                <button className="char-card-main" onClick={() => openDetail(c.id)}>
                  {c.has_avatar
                    ? <img className="char-card-avatar" alt="" src={avatarSrc(c.id, c.default_version)} />
                    : <div className="char-card-avatar char-card-avatar-empty">no avatar</div>}
                  <span className="char-card-name">{c.name}</span>
                </button>
                <div className="char-card-actions">
                  <button className="subtle" onClick={() => openEdit(c.id)}>Edit</button>
                  <button className="subtle" onClick={() => deleteCharacter(c.id, c.name)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (mode === "detail") {
    const tags = card.data.tags ?? [];
    return (
      <div className="character-editor">
        {taglinePrompt && (
          <TaglinePrompt wid={wid} cid={taglinePrompt.cid} name={taglinePrompt.name}
                         onSaved={(t) => setTagline(t)}
                         onClose={() => setTaglinePrompt(null)} />
        )}
        <div className="editor-body">
          <button className="subtle back" onClick={backToGrid}>‹ All characters</button>
          {error && <div className="banner">{error}</div>}
          {importMsg && <span className="field-hint">{importMsg}</span>}
          <div className="detail">
            <div className="detail-head">
              {hasAvatar
                ? <img className="detail-avatar" alt="" src={avatarSrc(detail.meta.id, vid, true)} />
                : <div className="detail-avatar avatar-empty">no avatar</div>}
              <div className="detail-meta">
                <h3>{card.data.name || detail.meta.name}</h3>
                {tagline && <div className="detail-text tagline">{tagline}</div>}
                {card.data.creator ? <div className="field-hint">by {card.data.creator}</div> : null}
                {tags.length > 0 && (
                  <div className="chips">{tags.map((t) => <span className="chip" key={t}>{t}</span>)}</div>
                )}
                {detail.versions.length > 1 && (
                  <select value={vid} onChange={(e) => loadVersion(detail, e.target.value)} aria-label="Version">
                    {detail.versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div className="detail-actions">
                <button className="primary" onClick={() => setMode("edit")}>Edit</button>
                <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>
              </div>
            </div>

            <div className="chub-source-block">
              {chubSource ? (
                <>
                  <a className="field-hint"
                     href={chubSource.startsWith("http") ? chubSource : `https://chub.ai/characters/${chubSource}`}
                     target="_blank" rel="noreferrer">
                    {chubSource}
                  </a>
                  <button className="subtle" type="button" onClick={unlinkChub}>Unlink</button>
                  {isChub && (
                    <>
                      <button className="subtle" type="button" disabled={!!galleryProg} onClick={downloadChubGallery}>
                        {galleryProg ? "Downloading…" : "Download gallery"}
                      </button>
                      <button className="subtle" type="button" onClick={downloadChubLorebooks}>Download linked lorebooks</button>
                      {galleryProg && (
                        <div className="localize-progress">
                          <progress value={galleryProg.done} max={galleryProg.total || 1} />
                          <span className="field-hint">{galleryProg.done}/{galleryProg.total}</span>
                        </div>
                      )}
                    </>
                  )}
                </>
              ) : (
                <button className="subtle" type="button" onClick={linkChub}>Link to URL</button>
              )}
            </div>

            {galleryImages.length > 0 && (
              <div className="detail-field">
                <div className="role">Gallery</div>
                <div className="gallery-grid">
                  {galleryImages.map((name) => {
                    const src = `${api.imageUrl(wid, detail.meta.id, vid, name)}?v=${avatarBust}`;
                    return (
                      <a key={name} href={src} target="_blank" rel="noreferrer">
                        <img className="gallery-thumb" alt="" src={src} />
                      </a>
                    );
                  })}
                </div>
              </div>
            )}

            {localizeControls(false)}

            {onOpenLore && (
              <OwnedLorePanel
                wid={wid}
                ownerRef={`characters:${detail.meta.id}`}
                onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                onNewEntry={() => onOpenLore({ newOwner: `characters:${detail.meta.id}` })}
              />
            )}

            {TEXT_FIELDS.map((f) => {
              const val = (card.data[f.key] as string) ?? "";
              return val.trim() ? (
                <div className="detail-field" key={f.key}>
                  <div className="role">{f.label}</div>
                  <div className="detail-text">{val}</div>
                </div>
              ) : null;
            })}

            {greetings.length > 0 && (
              <div className="detail-field">
                <div className="role">Alternate greetings</div>
                {greetings.map((g, i) => <div className="detail-text detail-greeting" key={i}>{g}</div>)}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // mode === "edit"
  return (
    <div className="character-editor">
      {taglinePrompt && (
        <TaglinePrompt wid={wid} cid={taglinePrompt.cid} name={taglinePrompt.name}
                       onSaved={(t) => setTagline(t)}
                       onClose={() => setTaglinePrompt(null)} />
      )}
      <div className="editor-body">
        <button className="subtle back" onClick={backToGrid}>‹ All characters</button>
        <div className="form">
          {error && <div className="banner">{error}</div>}
          <div className="picker">
            <select value={vid} onChange={(e) => loadVersion(detail, e.target.value)} aria-label="Version">
              {detail.versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}{v.id === detail.meta.default_version ? " (default)" : ""}
                </option>
              ))}
            </select>
            <button className="subtle" onClick={addVersion}>+ Version</button>
            <button className="subtle" onClick={() => versionFileRef.current?.click()}>Import version</button>
            <input ref={versionFileRef} type="file" accept=".json,.png,.charx" hidden
                   aria-label="Import version" onChange={onImportVersion} />
            <button className="subtle" onClick={setDefault}>Set default</button>
            <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>
            <button className="subtle" onClick={downloadVersionFromChub}>Download version from URL</button>
            {importMsg && <span className="field-hint">{importMsg}</span>}
          </div>

          <div className="avatar-block">
            {hasAvatar ? (
              <img className="avatar" alt="avatar" src={avatarSrc(detail.meta.id, vid, true)} />
            ) : (
              <div className="avatar avatar-empty" aria-label="no avatar">no avatar</div>
            )}
            <div className="avatar-actions">
              <button className="subtle" type="button" onClick={() => avatarRef.current?.click()}>
                {hasAvatar ? "Replace" : "Upload"}
              </button>
              {hasAvatar && <button className="subtle" type="button" onClick={removeAvatar}>Remove</button>}
              <input ref={avatarRef} type="file" accept="image/*" hidden
                     aria-label="Upload avatar" onChange={onAvatar} />
            </div>
          </div>

          {localizeControls(dirty, "Save your changes before localizing images")}

          <Field label="Name">
            <input type="text" value={card.data.name ?? ""} onChange={(e) => setField("name", e.target.value)} />
          </Field>
          <Field label="Creator">
            <input type="text" value={card.data.creator ?? ""} onChange={(e) => setField("creator", e.target.value)} />
          </Field>
          <Field label="Birthdate">
            <input type="date" aria-label="Birthdate" value={birthdate}
                   onChange={(e) => saveBirthdate(e.target.value)} />
          </Field>
          <Field label="Tagline" hint="one-line identity for the off-scene cast">
            <textarea aria-label="Tagline" value={tagline} rows={2}
                      onChange={(e) => setTagline(e.target.value)} />
          </Field>
          <div className="form-actions">
            <button className="subtle" type="button" disabled={taglineBusy} onClick={regenerateTagline}>
              {taglineBusy ? "Generating…" : "Generate"}
            </button>
            <button className="subtle" type="button" onClick={saveTagline}>Save tagline</button>
          </div>
          <Field label="Tags" hint="comma-separated">
            <input
              type="text"
              value={(card.data.tags ?? []).join(", ")}
              onChange={(e) => setField("tags", e.target.value.split(",").map((t) => t.trim()).filter(Boolean))}
            />
          </Field>
          {TEXT_FIELDS.map((f) => (
            <Field key={f.key} label={f.label}>
              <textarea
                value={(card.data[f.key] as string) ?? ""}
                rows={f.key === "description" ? 6 : 3}
                onChange={(e) => setField(f.key, e.target.value)}
              />
            </Field>
          ))}
          <Field label="Alternate greetings" hint="each greeting may span multiple lines">
            <div className="greeting-list">
              {greetings.map((g, i) => (
                <div className="greeting-row" key={i}>
                  <textarea
                    aria-label={`Greeting ${i + 1}`}
                    value={g}
                    rows={3}
                    onChange={(e) => setGreetings(greetings.map((x, j) => (j === i ? e.target.value : x)))}
                  />
                  <button className="subtle" type="button"
                          onClick={() => setGreetings(greetings.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </div>
              ))}
              <button className="subtle" type="button" onClick={() => setGreetings([...greetings, ""])}>
                + Add greeting
              </button>
            </div>
          </Field>

          {bookCount > 0 && (
            <div className="book-import">
              <button className="subtle" type="button" onClick={importBook}>
                Import {bookCount} embedded lore {bookCount === 1 ? "entry" : "entries"} to world
              </button>
              {bookMsg && <span className="field-hint">{bookMsg}</span>}
            </div>
          )}

          <div className="form-actions">
            <button className="primary" onClick={save}>Save version</button>
          </div>
        </div>
      </div>
    </div>
  );
}
