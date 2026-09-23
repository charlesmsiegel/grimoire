import { memo, startTransition, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  api, type ChubImportResult, type CharacterSummary, type ChubUnlinkedVersion,
  type EntityScope, type ModuleDetail, type RosterEntry, type UndescribedImage,
} from "../api/client";
import { errorText } from "../api/errors";
import { thumbSet } from "../api/thumbs";
import { isAbortError, type TaglineBatchEvent } from "../api/stream";
import CreationWizard from "./CreationWizard";
import { DescribeQueue } from "./DescribeQueue";
import { ErrorNote } from "./ErrorNote";
import { TaglinePrompt } from "./TaglinePrompt";
import { UrlImportPrompt } from "./UrlImportPrompt";
import { ImportVersionDialog, type ImportChoice } from "./character/ImportVersionDialog";
import { characterHref, focusStyle, formatOf, initialsOf } from "./character/shared";

/** How wide a card's portrait is drawn: `.char-grid` is three columns of
 *  `.page-wide`, which stops growing at 1120px -- so at most
 *  (1120 - 68 padding - 36 gaps) / 3 on a desktop, and under a third of the
 *  viewport below that. A phone is spelled out rather than left to the third:
 *  there the page keeps only a 14px gutter, and at DPR 3 the slack in "33vw"
 *  is exactly the difference between the 512 bucket and the 1024 one.
 *  A roster is the one screen where a whole library's art can be on screen at
 *  once, so this is where a thumbnail the size of the card matters most. */
const CARD_SIZES = "(max-width: 720px) calc((100vw - 64px) / 3), (max-width: 1120px) 33vw, 340px";

/** How many cards a roster's first commit draws; the rest follow once that
 *  has painted.
 *
 *  Drawing a card is a DOM subtree, a portrait whose `srcset` the browser has
 *  to resolve, and its share of the grid's layout -- small once, but a roster
 *  is paid for all at once, and on a phone-class CPU the whole of a large one
 *  kept the first card off the screen for as long as the last one took. The
 *  grid is three columns wide at every size, so this is eight rows: more than
 *  the tallest viewport shows of a desktop's cards, and more than a phone's
 *  much smaller ones fill. A constant rather than a measurement of the
 *  viewport, because a grid that measured first would be a frame later for
 *  it; worth re-tuning if the card or the column count changes. */
const FIRST_PAINT_CARDS = 24;

/** Who has actually been in a scene of this campaign, out of its roster.
 *
 *  A ROSTER ENTRY IS NOT AN APPEARANCE: `transitions.leave` drops a scene from
 *  an actor's record but keeps the record, because the entry is also what
 *  locks them to a version. This grid answers "who is in this campaign", and
 *  the answer to that is the scene list. */
function appearedIn(roster: RosterEntry[]): Set<string> {
  return new Set(roster
    .filter((r) => r.kind === "characters" && (r.scenes?.length ?? 0) > 0)
    .map((r) => r.id));
}

/** The appeared set a campaign grid can paint with before its roster read
 *  lands: the remembered roster's, or `null` ("no verdict yet"). */
function rememberedAppeared(scope: EntityScope): Set<string> | null {
  if (scope.kind !== "campaign") return null;
  const roster = api.rememberedAppearances(scope.id);
  return roster ? appearedIn(roster) : null;
}

/** `next`, keeping `prev`'s row objects wherever a row is unchanged -- and
 *  `prev` itself when nothing is.
 *
 *  A revalidation is the common case now that a revisit paints remembered
 *  rows: the answer is usually the rows already drawn, as new objects. Kept
 *  as they arrived, every memoized card would see a new `c` and re-render,
 *  and the grid would pay for its whole roster again to draw the same thing;
 *  kept like this, a confirming read renders nothing and an edit renders the
 *  card it changed. Compared as JSON because a row is plain JSON, in the
 *  server's key order both times. */
function reuseRows(prev: CharacterSummary[], next: CharacterSummary[]): CharacterSummary[] {
  if (prev.length === 0) return next;
  const byId = new Map(prev.map((r) => [r.id, r]));
  let same = prev.length === next.length;
  const out = next.map((row, i) => {
    const old = byId.get(row.id);
    if (old && JSON.stringify(old) === JSON.stringify(row)) {
      if (prev[i] !== old) same = false;
      return old;
    }
    same = false;
    return row;
  });
  return same ? prev : out;
}

/** One card. Memoized, with only primitives and stable callbacks for props
 *  besides the row itself, because the grid re-renders for reasons no card
 *  cares about -- the describe count landing, the module context arriving
 *  after the list, a tagline batch's progress line ticking once per
 *  character -- and every card rebuilding its portrait's `srcset` for each of
 *  them is the grid paying for its whole roster to redraw a toolbar. */
const CharacterCard = memo(function CharacterCard(
  { c, scopeKind, scopeId, onDelete }: {
    c: CharacterSummary;
    scopeKind: EntityScope["kind"];
    scopeId: string;
    onDelete: (cid: string, name: string) => void;
  },
) {
  const scope: EntityScope = { kind: scopeKind, id: scopeId };
  return (
    <div className="char-card">
      <Link className="char-card-main" to={characterHref(scope, c.id)}>
        {c.has_avatar
          ? <img className="char-card-avatar" alt="" style={focusStyle(c.avatar_focus)}
                 loading="lazy" decoding="async"
                 {...thumbSet((w) => api.actorImageUrl(scope, "characters", c.id,
                                                       c.default_version, "avatar",
                                                       { w, v: c.avatar_v }),
                              CARD_SIZES)} />
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
      </Link>
      <div className="char-card-actions">
        {/* Both scopes since #60: in campaign scope this removes the
            character from THIS campaign and leaves the library's alone.
            Shipping a create with no delete left an NPC invented by
            mistake unremovable. */}
        <button className="subtle" onClick={() => onDelete(c.id, c.name)}>Delete</button>
      </div>
    </div>
  );
});

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
 *
 *  A scope seen before paints on the first render, from the rows the api
 *  client remembers (see "remembered reads" in `api/client.ts`), and is
 *  revalidated by the same read a first visit makes; so what a revisit shows
 *  before that read lands is at most one revalidation stale, and never
 *  another scope's.
 */
export function CharacterGrid(
  { scope: scopeProp, wid, reveal, module = null, onListed }: {
    scope: EntityScope;
    wid: string;
    /** A character to keep visible even if the appeared filter would hide it —
     *  handed back by their page on close. */
    reveal?: string | null;
    module?: ModuleDetail | null;
    /** Called once this scope's list read has settled (landed or failed): the
     *  moment the grid has what it needs to paint, and so the moment the page
     *  can start the reads the grid can do without for its first paint. */
    onListed?: () => void;
  },
) {
  // By value, so a caller that builds its scope object per render hands the
  // callbacks below the same identity every time -- the memoized cards take
  // them as props.
  const scope = useMemo<EntityScope>(() => ({ kind: scopeProp.kind, id: scopeProp.id }),
                                     [scopeProp.kind, scopeProp.id]);
  const scopeKey = `${scope.kind}:${scope.id}`;
  const worldScope = scope.kind === "world";
  const navigate = useNavigate();
  const [chars, setChars] = useState<CharacterSummary[]>(() => api.rememberedCharacters(scope) ?? []);
  /** Whether `chars` is an answer for this scope -- remembered or read -- as
   *  opposed to the empty array a first visit starts from. Until it is, the
   *  grid draws nothing: "No characters yet" over a list that has not arrived
   *  is a claim about the world nobody made. */
  const [charsKnown, setCharsKnown] = useState(() => api.rememberedCharacters(scope) !== undefined);
  const [error, setError] = useState<unknown>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  /** How long the describe backlog is, from its `?count=1` form; `null` until
   *  that lands, and after it fails -- either way no button. */
  const [undescribedCount, setUndescribedCount] = useState<number | null>(null);
  /** The backlog itself, fetched only when the reader opens it: non-null is
   *  "the queue is open". */
  const [describeQueue, setDescribeQueue] = useState<UndescribedImage[] | null>(null);
  const [describeLoading, setDescribeLoading] = useState(false);
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
  const [appeared, setAppeared] = useState<Set<string> | null>(() => rememberedAppeared(scope));
  const [rosterFailed, setRosterFailed] = useState(false);
  const [showAll, setShowAll] = useState(false);
  /** A single-file import waiting on the dialog that says where it lands. */
  const [pendingImport, setPendingImport] = useState<File | null>(null);
  /** Whether the whole roster is drawn, or only its first `FIRST_PAINT_CARDS`. */
  const [drawAll, setDrawAll] = useState(false);

  // A new scope replaces what is drawn IN THE SAME RENDER, before anything
  // commits: the route keeps this instance across a world switch, and a render
  // of the old scope's cards with the new scope's links -- which is what an
  // effect-time reset left for a frame, and what the unreset rows showed until
  // the new list landed -- is a grid whose every card opens a character the
  // world being shown does not have.
  const [shownFor, setShownFor] = useState(scopeKey);
  if (shownFor !== scopeKey) {
    setShownFor(scopeKey);
    const remembered = api.rememberedCharacters(scope);
    setChars(remembered ?? []);
    setCharsKnown(remembered !== undefined);
    setAppeared(rememberedAppeared(scope));
    setRosterFailed(false);
    setUndescribedCount(null);
    setDescribeQueue(null);
    // An error is a claim about the scope it happened in.
    setError(null);
    setDrawAll(false);
  }

  const liveScope = useRef(scope);
  liveScope.current = scope;
  const onListedRef = useRef(onListed);
  onListedRef.current = onListed;

  const untagged = useMemo(() => (worldScope ? chars.filter((c) => !c.tagline) : []),
                           [worldScope, chars]);

  // `adopt`'s rule for the roster: the read is async, so this can be showing
  // another library by the time it lands, and installing A's cards under B's
  // handlers is how an action on a shared slug mutates one while displaying the
  // other.
  const reload = useCallback(() => {
    const from = scope;
    return api.listCharacters(from).then((rows) => {
      if (liveScope.current.kind === from.kind && liveScope.current.id === from.id) {
        setChars((prev) => reuseRows(prev, rows));
        setCharsKnown(true);
      }
    });
  }, [scope]);

  const recountUndescribed = useCallback(() => {
    const from = scope;
    const current = () => liveScope.current.kind === from.kind && liveScope.current.id === from.id;
    Promise.resolve()
      .then(() => api.countUndescribedImages(from))
      .then((n) => { if (current()) setUndescribedCount(n); })
      .catch(() => { if (current()) setUndescribedCount(null); });
  }, [scope]);

  useEffect(() => { recountUndescribed(); }, [recountUndescribed]);

  useEffect(() => {
    const from = scope;
    const current = () => liveScope.current.kind === from.kind && liveScope.current.id === from.id;
    // The revalidation, when this scope painted from remembered rows, and the
    // read, when it did not -- the same call either way.
    reload()
      .catch((err: unknown) => {
        // A read that failed has not confirmed what a remembered paint drew,
        // so that goes: no rows, and the failure said out loud, rather than a
        // cast on screen that nothing vouches for.
        if (!current()) return;
        setChars([]);
        setCharsKnown(true);
        setError(err);
      })
      .finally(() => { if (current()) onListedRef.current?.(); });
    setWizardOpen(false);
    // `showAll` is a statement about one campaign's inherited roster; left
    // standing it opens the next campaign on its whole world instead of on its
    // own cast, which is the state this filter exists to avoid.
    setShowAll(false);
    // A report is a claim about one library ("Derived 12 taglines"); left
    // standing it becomes a claim about whichever library is showing now.
    setTaglineBatchMsg(null);
  }, [reload, scope]);

  // A derive dies with the view that started it — nothing here is a detached
  // run, so leaving takes the progress line and the Stop button with it, and a
  // stream nobody can see or stop, still spending a provider call per
  // character, is the one outcome worse than having to click Derive again.
  useEffect(() => () => taglineAbort.current?.abort(), []);

  // Who is actually in a scene here. Revalidated like the rows: a remembered
  // roster lets a revisit filter on its first render, and this read replaces it.
  useEffect(() => {
    let alive = true;
    if (scope.kind !== "campaign") return;
    api.listAppearances(scope.id)
      .then((roster) => { if (alive) setAppeared(appearedIn(roster)); })
      // An unreadable roster must not hide the records it was meant to narrow:
      // the filter is withdrawn entirely -- a remembered one included, which
      // this read failed to confirm. Tracked separately from "still loading"
      // so the grid can wait for one and not the other.
      .catch(() => {
        if (!alive) return;
        setAppeared(null);
        setRosterFailed(true);
      });
    return () => { alive = false; };
  }, [scope]);

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

  // Stable per scope, because every memoized card holds it.
  const deleteCharacter = useCallback(async (cid: string, name: string) => {
    const where = scope.kind === "world" ? "the library" : "this campaign";
    if (!window.confirm(`Delete character '${name}' from ${where}?`)) return;
    await api.deleteCharacter(scope, cid);
    await reload();
  }, [scope, reload]);
  const onDeleteCard = useCallback((cid: string, name: string) => {
    void deleteCharacter(cid, name);
  }, [deleteCharacter]);

  /** Open the describe queue: the backlog is fetched now, when somebody means
   *  to work through it, and not on every visit to label a button. The count
   *  is corrected from the list while it is at hand. */
  function openDescribe() {
    const from = scope;
    const current = () => liveScope.current.kind === from.kind && liveScope.current.id === from.id;
    setDescribeLoading(true);
    api.listUndescribedImages(from)
      .then((q) => {
        if (!current()) return;
        setDescribeQueue(q);
        setUndescribedCount(q.length);
      })
      .catch((err: unknown) => { if (current()) setError(err); })
      .finally(() => { if (current()) setDescribeLoading(false); });
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

  // The filter is offered only where "appeared" means something and the roster
  // actually loaded; everywhere else `shown` is simply every card.
  const filterable = !worldScope && appeared !== null;
  const appearedChars = useMemo(
    () => (filterable ? chars.filter((c) => appeared.has(c.id)) : chars),
    [filterable, chars, appeared],
  );
  const shown = filterable && !showAll ? appearedChars : chars;
  // The rest of the roster, once the first slice has been PAINTED: a frame's
  // callback runs just before the paint, and a task queued from it runs just
  // after. As a transition, so drawing a long roster yields to a click or a
  // keystroke rather than holding the page until the last card is in.
  const needsMore = !drawAll && shown.length > FIRST_PAINT_CARDS;
  useEffect(() => {
    if (!needsMore) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const frame = requestAnimationFrame(() => {
      timer = setTimeout(() => startTransition(() => setDrawAll(true)), 0);
    });
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); };
  }, [needsMore]);
  const drawn = needsMore ? shown.slice(0, FIRST_PAINT_CARDS) : shown;
  // Campaign scope has no verdict yet while the roster is in flight. Painting
  // the grid anyway shows every inherited character for as long as that read
  // takes and then yanks most of them away. A FAILED read is not this state: it
  // has its answer, which is "do not filter".
  const rosterPending = !worldScope && appeared === null && !rosterFailed;

  // Every hook above this line: the wizard below returns early.
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
      {describeQueue !== null && (
        <DescribeQueue key={scopeKey} scope={scope} wid={wid} queue={describeQueue}
                       onSaved={recountUndescribed}
                       onClose={() => { setDescribeQueue(null); recountUndescribed(); }} />
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
        {undescribedCount !== null && undescribedCount > 0 && (
          <button className="subtle" disabled={describeLoading} onClick={openDescribe}>
            ▶ Describe images ({undescribedCount})
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
                <Link key={`${u.character}:${u.version}`} className="chip"
                      to={characterHref(scope, u.character, u.version)}>
                  {u.character_name} ({u.version_name})
                </Link>
              ))}
            </div>
          </>}
        </div>
      )}

      {error != null && <div className="banner"><ErrorNote err={error} /></div>}

      {rosterPending || !charsKnown ? null : shown.length === 0 ? (
        <div className="editor-empty">
          {chars.length === 0
            ? "No characters yet. Create one or import a card."
            : "No one has appeared in this campaign yet — show All to see the world's roster."}
        </div>
      ) : (
        <div className="char-grid">
          {drawn.map((c) => (
            <CharacterCard key={c.id} c={c} scopeKind={scope.kind} scopeId={scope.id}
                           onDelete={onDeleteCard} />
          ))}
        </div>
      )}
    </div>
  );
}

export default CharacterGrid;
