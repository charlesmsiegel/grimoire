import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api, type ChubImportResult, type CharacterSummary, type ChubUnlinkedVersion,
  type EntityScope, type ModuleDetail, type UndescribedImage,
} from "../api/client";
import { errorText } from "../api/errors";
import { isAbortError, type TaglineBatchEvent } from "../api/stream";
import CreationWizard from "./CreationWizard";
import { DescribeQueue } from "./DescribeQueue";
import { ErrorNote } from "./ErrorNote";
import { TaglinePrompt } from "./TaglinePrompt";
import { UrlImportPrompt } from "./UrlImportPrompt";
import { ImportVersionDialog, type ImportChoice } from "./character/ImportVersionDialog";
import { characterHref, focusStyle, formatOf, initialsOf } from "./character/shared";

/** The world's (or campaign's) roster, as cards.
 *
 *  Half of what used to be `CharacterEditor`: the grid and every bulk operation
 *  that acts on a roster rather than on one record. The other half is
 *  `routes/CharacterPage`, which owns a route now — so opening a character here
 *  is a navigation, and this component no longer has modes.
 *
 *  What went with the modes is the machinery that existed only because one
 *  long-lived component was showing a record from a scope that could change
 *  underneath it: `adopt`, `scopeRef`, `keepVisible`'s pending reveal. A close
 *  that has to un-filter the character it is handing back is still needed and
 *  is `reveal` below — the page passes the id back through `location.state`,
 *  which is the same fact travelling by a route rather than by a ref.
 */
export function CharacterGrid(
  { scope, wid, resetSignal, reveal, module = null }: {
    scope: EntityScope;
    wid: string;
    resetSignal?: number;
    /** A character to keep visible even if the appeared filter would hide it —
     *  handed back by their page on close. */
    reveal?: string | null;
    module?: ModuleDetail | null;
  },
) {
  const worldScope = scope.kind === "world";
  const navigate = useNavigate();
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [undescribed, setUndescribed] = useState<UndescribedImage[]>([]);
  const [describeOpen, setDescribeOpen] = useState(false);
  const [unlinkedVersions, setUnlinkedVersions] = useState<ChubUnlinkedVersion[] | null>(null);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [bulkLocalize, setBulkLocalize] = useState<{ current: number; cards: number } | null>(null);
  const [bulkUrl, setBulkUrl] =
    useState<{ current: number; total: number; name: string; step: string } | null>(null);
  const [urlPromptOpen, setUrlPromptOpen] = useState(false);
  const [taglineQueue, setTaglineQueue] = useState<{ cid: string; name: string }[]>([]);
  const [taglineBatch, setTaglineBatch] =
    useState<{ done: number; total: number; name: string } | null>(null);
  const [taglineBatchMsg, setTaglineBatchMsg] = useState<string | null>(null);
  const taglineAbort = useRef<AbortController | null>(null);
  const [appeared, setAppeared] = useState<Set<string> | null>(null);
  const [rosterFailed, setRosterFailed] = useState(false);
  const [showAll, setShowAll] = useState(false);
  /** A single-file import waiting on the dialog that says where it lands. */
  const [pendingImport, setPendingImport] = useState<File | null>(null);

  const liveScope = useRef(scope);
  liveScope.current = scope;

  const untagged = worldScope ? chars.filter((c) => !c.tagline) : [];

  // `adopt`'s rule for the roster: the read is async, so this can be showing
  // another library by the time it lands, and installing A's cards under B's
  // handlers is how an action on a shared slug mutates one while displaying the
  // other.
  const reload = useCallback(() => {
    const from = scope;
    return api.listCharacters(from).then((rows) => {
      if (liveScope.current.kind === from.kind && liveScope.current.id === from.id) setChars(rows);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.id]);

  const reloadUndescribed = useCallback(() => {
    const from = scope;
    const current = () => liveScope.current.kind === from.kind && liveScope.current.id === from.id;
    api.listUndescribedImages(from)
      .then((q) => { if (current()) setUndescribed(q); })
      .catch(() => { if (current()) setUndescribed([]); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.id]);

  useEffect(() => { reloadUndescribed(); }, [reloadUndescribed]);

  useEffect(() => {
    void reload();
    setWizardOpen(false);
    // `showAll` is a statement about one campaign's inherited roster; left
    // standing it opens the next campaign on its whole world instead of on its
    // own cast, which is the state this filter exists to avoid.
    setShowAll(false);
    setDescribeOpen(false);
    setUndescribed([]);
    // A report is a claim about one library ("Derived 12 taglines"); left
    // standing it becomes a claim about whichever library is showing now.
    setTaglineBatchMsg(null);
  }, [reload]);

  // A derive dies with the view that started it — nothing here is a detached
  // run, so leaving takes the progress line and the Stop button with it, and a
  // stream nobody can see or stop, still spending a provider call per
  // character, is the one outcome worse than having to click Derive again.
  useEffect(() => () => taglineAbort.current?.abort(), []);

  // Who is actually in a scene here. Re-read on `resetSignal` too, so returning
  // after playing picks up the actors that scene introduced.
  useEffect(() => {
    let alive = true;
    setAppeared(null);
    setRosterFailed(false);
    if (scope.kind !== "campaign") return;
    api.listAppearances(scope.id)
      .then((roster) => {
        if (!alive) return;
        // A ROSTER ENTRY IS NOT AN APPEARANCE: `transitions.leave` drops a scene
        // from an actor's record but keeps the record, because the entry is
        // also what locks them to a version. This grid answers "who is in this
        // campaign", and the answer to that is the scene list.
        setAppeared(new Set(roster
          .filter((r) => r.kind === "characters" && (r.scenes?.length ?? 0) > 0)
          .map((r) => r.id)));
      })
      // An unreadable roster must not hide the records it was meant to narrow:
      // the filter is withdrawn entirely. Tracked separately from "still
      // loading" so the grid can wait for one and not the other.
      .catch(() => { if (alive) setRosterFailed(true); });
    return () => { alive = false; };
  }, [scope.kind, scope.id, resetSignal]);

  // A character handed back by their own page has to survive the filter, or
  // landing on a grid that hides them reads as the record having been deleted.
  useEffect(() => {
    if (!reveal || appeared === null) return;
    if (!appeared.has(reveal)) setShowAll(true);
  }, [reveal, appeared]);

  async function newCharacter() {
    const name = window.prompt("New character name?")?.trim();
    if (!name) return;
    // `scope`, not `wid`: in campaign scope this makes a character who exists
    // only in this campaign — an NPC who walked on mid-scene and was never in
    // the library (#60).
    const { character } = await api.createCharacter(scope, { name });
    await reload();
    navigate(characterHref(scope, character));
  }

  async function deleteCharacter(cid: string, name: string) {
    const where = worldScope ? "the library" : "this campaign";
    if (!window.confirm(`Delete character '${name}' from ${where}?`)) return;
    await api.deleteCharacter(scope, cid);
    await reload();
  }

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    // A single card is asked where it lands — a new character, or a named
    // version of one you already have. A thirty-card drop is answered by making
    // thirty characters, because a dialog per file is worse than no dialog.
    if (files.length === 1) setPendingImport(files[0]);
    else void importMany(files);
  }

  async function importMany(files: File[]) {
    setError(null);
    setImportMsg(null);
    const failures: string[] = [];
    const imported: { cid: string; version: string }[] = [];
    for (const file of files) {
      try {
        const { character, version } = await api.importCharacter(wid, file, formatOf(file));
        imported.push({ cid: character, version });
      } catch (err: unknown) {
        failures.push(`${file.name}: ${errorText(err)}`);
      }
    }
    await reload();
    if (failures.length) setError(`Could not import — ${failures.join("; ")}`);
    if (imported.length) await runBulkLocalize(imported);
  }

  async function confirmImport(choice: ImportChoice) {
    const file = pendingImport;
    setPendingImport(null);
    if (!file) return;
    setError(null);
    setImportMsg(null);
    try {
      const { character, version } = await api.importCharacter(
        wid, file, formatOf(file), choice.into ?? undefined, choice.versionName || undefined);
      await reload();
      await runLocalize(character, version);
      if (choice.into === null) {
        const name = await api.readCharacter(scope, character)
          .then((d) => d.meta.name).catch(() => character);
        setTaglineQueue([{ cid: character, name }]);
      }
      navigate(characterHref(scope, character, version));
    } catch (err: unknown) { setError(err); }
  }

  async function runLocalize(cid: string, version: string) {
    try {
      await api.localizeImages(wid, cid, version, () => {});
    } catch { /* reported by the character's own page, which shows the images */ }
  }

  /** Localize a batch of freshly-imported cards back to back, accumulating one
   *  aggregate summary. Nothing is open, so progress renders in the toolbar. */
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
      `Localized ${localized} image${localized === 1 ? "" : "s"} across ${cards.length} cards`
      + (skipped ? `, skipped ${skipped}` : "") + (failed ? `, ${failed} failed` : ""));
  }

  /** Per URL: import (the backend downloads the avatar, chub gallery and
   *  related lorebooks inside that one call), localize, then import the card's
   *  embedded book. Failures record and continue — one bad URL shouldn't sink
   *  the batch. */
  async function runBulkUrlImport(urls: string[]) {
    setError(null);
    setImportMsg(null);
    const failures: string[] = [];
    const added: { cid: string; name: string }[] = [];
    let localized = 0, gallery = 0, lore = 0;
    for (let i = 0; i < urls.length; i++) {
      setBulkUrl({ current: i + 1, total: urls.length, name: urls[i], step: "importing" });
      let result: ChubImportResult;
      try {
        result = await api.importCharacterFromChub(wid, urls[i]);
      } catch (err: unknown) {
        failures.push(`${urls[i]}: ${errorText(err)}`);
        continue;
      }
      gallery += result.gallery.stored;
      lore += result.lore.created.length;
      let name = result.character;
      try {
        name = (await api.readCharacter(scope, result.character)).meta.name;
      } catch { /* fall back to the id */ }
      setBulkUrl({ current: i + 1, total: urls.length, name, step: "localizing images" });
      try {
        await api.localizeImages(wid, result.character, result.version, (e) => {
          if (e.summary) localized += e.summary.localized;
        });
      } catch (err: unknown) {
        failures.push(`${name}: localize failed (${errorText(err)})`);
      }
      setBulkUrl({ current: i + 1, total: urls.length, name, step: "importing lorebook" });
      try {
        const { created } = await api.importCharacterBook(wid, result.character, result.version);
        lore += created.length;
      } catch (err: unknown) {
        failures.push(`${name}: lorebook import failed (${errorText(err)})`);
      }
      added.push({ cid: result.character, name });
      await reload();  // the new card appears in the grid as it lands
    }
    setBulkUrl(null);
    const parts = [`Added ${added.length}/${urls.length} character${urls.length === 1 ? "" : "s"}`];
    if (gallery) parts.push(`${gallery} gallery image${gallery === 1 ? "" : "s"}`);
    if (localized) parts.push(`${localized} image${localized === 1 ? "" : "s"} localized`);
    if (lore) parts.push(`${lore} lore entr${lore === 1 ? "y" : "ies"} imported`);
    setImportMsg(parts.join(" · ") + (failures.length ? ` · failed — ${failures.join("; ")}` : ""));
    setTaglineQueue(added);
  }

  /** Derive a tagline for every character in the world that has none (#57).
   *
   *  The route writes each sentence as it lands, so there is nothing to save
   *  afterwards and nothing to lose if the run dies part-way. A provider
   *  failure arrives as a FRAME, not a rejection: the response is a 200 the
   *  moment the first character is attempted, so the run reports what it
   *  managed and names what stopped it. */
  async function deriveTaglines() {
    setError(null);
    setTaglineBatchMsg(null);
    setTaglineBatch({ done: 0, total: untagged.length, name: "" });
    const ctl = new AbortController();
    taglineAbort.current = ctl;
    const from = scope;
    const here = () => liveScope.current.kind === from.kind && liveScope.current.id === from.id;
    // Counted from the frames rather than the summary alone: a run the user
    // stops never sends one, and "you spent forty calls, here is nothing" is
    // not an acceptable answer to Stop.
    const run = { written: 0, reasons: {} as Record<string, number>,
                  failed: null as { detail: string; kind: string } | null,
                  started: false, ended: false };
    try {
      await api.generateWorldTaglines(wid, (e: TaglineBatchEvent) => {
        run.started = true;
        if (e.total !== undefined) setTaglineBatch({ done: 0, total: e.total, name: "" });
        if (e.done !== undefined) {
          const done = e.done, name = e.name ?? "";
          setTaglineBatch((p) => ({ done, total: p?.total ?? done, name }));
        }
        if (e.tagline) run.written += 1;
        if (e.skipped) run.reasons[e.skipped] = (run.reasons[e.skipped] ?? 0) + 1;
        if (e.error) run.failed = e.error;
        if (e.summary) run.ended = true;
      }, ctl.signal);
    } catch (err: unknown) {
      // An abort is the user's own Stop, not a failure — everything already
      // written is still written, and the report below says how much.
      if (!isAbortError(err)) {
        setError(err);
        if (!run.started) return;
      }
    } finally {
      taglineAbort.current = null;
      setTaglineBatch(null);
      // Swallowed rather than surfaced: the taglines are already written, and
      // letting a failed roster GET throw out of `finally` would take the
      // report with it.
      if (here()) await reload().catch(() => {});
    }
    if (!here()) return;
    const parts = [`Derived ${run.written} tagline${run.written === 1 ? "" : "s"}`];
    for (const [reason, n] of Object.entries(run.reasons)) parts.push(`${n} ${reason}`);
    if (run.failed) parts.push(`stopped — ${run.failed.detail}`);
    if (run.failed || !run.ended) parts.push("run it again to pick up the rest");
    setTaglineBatchMsg(parts.join(" · "));
  }

  async function checkChubLinks() {
    setError(null);
    try {
      const { versions } = await api.findChubUnlinked(wid);
      setUnlinkedVersions(versions);
    } catch (err: unknown) { setError(err); }
  }

  if (wizardOpen && module && worldScope) {
    return (
      <div className="character-editor">
        <CreationWizard scope={scope} kind="characters" module={module}
                        createRecord={(n) => api.createCharacter(scope, { name: n }).then((r) => r.character)}
                        deleteRecord={(id) => api.deleteCharacter(scope, id).then(() => {})}
                        onDone={(id) => void (async () => {
                          setWizardOpen(false);
                          await reload();
                          navigate(characterHref(scope, id));
                        })()}
                        onCancel={() => setWizardOpen(false)} />
      </div>
    );
  }

  // The filter is offered only where "appeared" means something and the roster
  // actually loaded; everywhere else `shown` is simply every card.
  const filterable = !worldScope && appeared !== null;
  const appearedChars = filterable ? chars.filter((c) => appeared.has(c.id)) : chars;
  const shown = filterable && !showAll ? appearedChars : chars;
  // Campaign scope has no verdict yet while the roster is in flight. Painting
  // the grid anyway shows every inherited character for as long as that read
  // takes and then yanks most of them away. A FAILED read is not this state: it
  // has its answer, which is "do not filter".
  const rosterPending = !worldScope && appeared === null && !rosterFailed;

  return (
    <div className="character-editor">
      {taglineQueue.length > 0 && (
        <TaglinePrompt key={taglineQueue[0].cid} wid={wid} cid={taglineQueue[0].cid}
                       name={taglineQueue[0].name}
                       onSaved={() => { void reload(); }}
                       onClose={() => setTaglineQueue((q) => q.slice(1))} />
      )}
      {urlPromptOpen && (
        <UrlImportPrompt onClose={() => setUrlPromptOpen(false)}
                         onSubmit={(urls) => void runBulkUrlImport(urls)} />
      )}
      {pendingImport && (
        <ImportVersionDialog fileName={pendingImport.name} characters={chars}
                             onCancel={() => setPendingImport(null)}
                             onConfirm={(c) => void confirmImport(c)} />
      )}
      {describeOpen && (
        <DescribeQueue key={`${scope.kind}:${scope.id}`} scope={scope} wid={wid} queue={undescribed}
                       onSaved={reloadUndescribed}
                       onClose={() => { setDescribeOpen(false); reloadUndescribed(); }} />
      )}

      <div className="grid-toolbar">
        {/* Both scopes since #60. In campaign scope the label says whose
            character it will be: the world's roster is the library, and this
            one deliberately is not in it until somebody publishes it. */}
        <button className="primary" onClick={() => void newCharacter()}>
          {worldScope ? "+ New character" : "+ New NPC (this campaign)"}
        </button>
        {worldScope && <>
          {module && Object.values(module.sheets.sheet_types).some((st) => st.kind === "characters") && (
            <button className="subtle" onClick={() => setWizardOpen(true)}>+ New character with sheet…</button>
          )}
          <button className="subtle" onClick={() => fileRef.current?.click()}>Import card</button>
          <input ref={fileRef} type="file" accept=".json,.png,.charx" multiple hidden
                 aria-label="Import character card" onChange={onPickFiles} />
          <button className="subtle" onClick={() => setUrlPromptOpen(true)}>Download from URL</button>
          <button className="subtle" onClick={() => void checkChubLinks()}>Check chub.ai links</button>
        </>}
        {undescribed.length > 0 && (
          <button className="subtle" onClick={() => setDescribeOpen(true)}>
            ▶ Describe images ({undescribed.length})
          </button>
        )}
        {worldScope && untagged.length > 0 && (
          <button className="subtle" disabled={taglineBatch !== null}
                  onClick={() => void deriveTaglines()}>
            ▶ Derive taglines ({untagged.length})
          </button>
        )}
        {taglineBatch && (<>
          <span className="field-hint">
            Deriving taglines {taglineBatch.done}/{taglineBatch.total}
            {taglineBatch.name ? ` — ${taglineBatch.name}` : ""}…
          </span>
          <button className="subtle" onClick={() => taglineAbort.current?.abort()}>Stop</button>
        </>)}
        {!taglineBatch && taglineBatchMsg && <span className="field-hint">{taglineBatchMsg}</span>}
        {bulkLocalize && (
          <span className="field-hint">Localizing card {bulkLocalize.current}/{bulkLocalize.cards}…</span>
        )}
        {bulkUrl && (
          <span className="field-hint">
            Adding {bulkUrl.current}/{bulkUrl.total} — {bulkUrl.name}: {bulkUrl.step}…
          </span>
        )}
        {filterable && (
          <div className="chips" role="group" aria-label="Show">
            <button className={"chip" + (showAll ? "" : " on")} aria-pressed={!showAll}
                    onClick={() => setShowAll(false)}>
              Appeared ({appearedChars.length})
            </button>
            <button className={"chip" + (showAll ? " on" : "")} aria-pressed={showAll}
                    onClick={() => setShowAll(true)}>
              All ({chars.length})
            </button>
          </div>
        )}
        {!bulkLocalize && importMsg && <span className="field-hint">{importMsg}</span>}
      </div>

      {unlinkedVersions !== null && (
        <div className="chub-unlinked-list">
          {unlinkedVersions.length === 0 ? (
            <div className="field-hint">All versions are linked to chub.ai</div>
          ) : <>
            <div className="field-hint">
              {unlinkedVersions.length} version{unlinkedVersions.length === 1 ? "" : "s"} not linked to chub.ai:
            </div>
            <div className="chips">
              {unlinkedVersions.map((u) => (
                <button key={`${u.character}:${u.version}`} className="chip"
                        onClick={() => navigate(characterHref(scope, u.character, u.version))}>
                  {u.character_name} ({u.version_name})
                </button>
              ))}
            </div>
          </>}
        </div>
      )}

      {error != null && <div className="banner"><ErrorNote err={error} /></div>}

      {rosterPending ? null : shown.length === 0 ? (
        <div className="editor-empty">
          {chars.length === 0
            ? "No characters yet. Create one or import a card."
            : "No one has appeared in this campaign yet — show All to see the world's roster."}
        </div>
      ) : (
        <div className="char-grid">
          {shown.map((c) => (
            <div key={c.id} className="char-card">
              <button className="char-card-main" onClick={() => navigate(characterHref(scope, c.id))}>
                {c.has_avatar
                  ? <img className="char-card-avatar" alt="" style={focusStyle(c.avatar_focus)}
                         src={api.actorImageUrl(scope, "characters", c.id, c.default_version, "avatar")
                              + (c.avatar_v ? `?v=${c.avatar_v}` : "")} />
                  : <div className="initials-avatar" aria-hidden>{initialsOf(c.name)}</div>}
                <span className="char-card-name">{c.name}</span>
                {c.tagline ? <span className="char-card-tagline">{c.tagline}</span> : null}
                {((c.gallery_count ?? 0) > 0 || (c.localized_count ?? 0) > 0
                  || (c.greeting_count ?? 0) > 0 || c.versions.length > 1) && (
                  <span className="char-card-badges">
                    {c.versions.length > 1 && (
                      <span className="chip">{c.versions.length} versions</span>
                    )}
                    {(c.greeting_count ?? 0) > 0 && (
                      <span className="chip">{c.greeting_count} greeting{c.greeting_count === 1 ? "" : "s"}</span>
                    )}
                    {(c.gallery_count ?? 0) > 0 && <span className="chip">{c.gallery_count} gallery</span>}
                    {(c.localized_count ?? 0) > 0 && <span className="chip">{c.localized_count} localized</span>}
                  </span>
                )}
              </button>
              <div className="char-card-actions">
                {/* Both scopes since #60: in campaign scope this removes the
                    character from THIS campaign and leaves the library's alone.
                    Shipping a create with no delete left an NPC invented by
                    mistake unremovable. */}
                <button className="subtle" onClick={() => void deleteCharacter(c.id, c.name)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default CharacterGrid;
