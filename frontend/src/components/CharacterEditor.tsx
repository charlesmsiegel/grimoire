import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Appearance, type Card, type CharacterDetail, type CharacterSummary, type ChubImportResult, type ChubUnlinkedVersion, type EntityScope, type Greeting, type ModuleDetail, type VersionRef } from "../api/client";
import { AvatarFocusPicker } from "./AvatarFocusPicker";
import { CalendarDatePicker } from "./CalendarDatePicker";
import CreationWizard from "./CreationWizard";
import { Field } from "./Field";
import { GreetingMarkdown } from "./GreetingMarkdown";
import { HtmlNote } from "./HtmlNote";
import { OwnedLorePanel } from "./OwnedLorePanel";
import SheetPanel from "./SheetPanel";
import { TaglinePrompt } from "./TaglinePrompt";
import { UrlImportPrompt } from "./UrlImportPrompt";
import { scrollShellToTop } from "../shellScroll";

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

function focusStyle(f?: number | null): React.CSSProperties | undefined {
  return f == null ? undefined : { objectPosition: `${f}% ${f}%` };
}

export function CharacterEditor({ scope, wid, resetSignal, focus, onOpenLore, onOpenGreeting, module = null }:
  { scope: EntityScope; wid: string; resetSignal?: number; focus?: { cid: string; vid: string } | null;
    onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void;
    onOpenGreeting?: (gid: string) => void;
    module?: ModuleDetail | null }) {
  const worldScope = scope.kind === "world";
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [vid, setVid] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [greetings, setGreetings] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("grid");
  const [error, setError] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const versionFileRef = useRef<HTMLInputElement>(null);
  const avatarRef = useRef<HTMLInputElement>(null);
  const shelfFileRef = useRef<HTMLInputElement>(null);
  const [avatarBust, setAvatarBust] = useState(0);
  const [imageAppearances, setImageAppearances] = useState<Appearance[]>([]);
  const [worldGreetings, setWorldGreetings] = useState<Greeting[]>([]);
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
  const [taglineQueue, setTaglineQueue] = useState<{ cid: string; name: string }[]>([]);
  const [voiceAnchor, setVoiceAnchor] = useState("");
  const [anchorBusy, setAnchorBusy] = useState(false);
  const [anchorSaving, setAnchorSaving] = useState(false);
  const [anchorState, setAnchorState] = useState<"loading" | "ready" | "error">("loading");
  const anchorReq = useRef(0);
  // What the anchor textarea held when it was last in sync with the server --
  // set on a successful load and on a successful save. `voiceAnchor` differing
  // from it is an UNSAVED DRAFT, which `select()` must not silently discard.
  const anchorLoaded = useRef("");
  const [urlPromptOpen, setUrlPromptOpen] = useState(false);
  const [cropOpen, setCropOpen] = useState(false);
  const [bulkUrl, setBulkUrl] = useState<{ current: number; total: number; name: string; step: string } | null>(null);
  const lockReq = useRef(0);
  const [locked, setLocked] = useState<string | null>(null);       // campaign: locked version id
  const [worldVersions, setWorldVersions] = useState<VersionRef[]>([]);
  const [importVid, setImportVid] = useState("");
  // campaign: the ids that have ever been cast in this campaign. `null` while
  // the roster is still loading (or in world scope, where it has no meaning) --
  // distinct from the empty set, which is a campaign nobody has played yet, and
  // distinct again from `rosterFailed`, which is null for a third reason. Those
  // three have to stay separable: the empty set filters everything out, a
  // failure filters nothing, and loading shows no verdict at all.
  const [appeared, setAppeared] = useState<Set<string> | null>(null);
  const [rosterFailed, setRosterFailed] = useState(false);
  // Same token discipline as `lockReq`/`anchorReq`: the roster is re-read on
  // `resetSignal` as well as on a scope change, so two reads of the SAME
  // campaign can be in flight at once and the scope they carry cannot tell them
  // apart. Without this, a slow earlier read lands last and reinstates a roster
  // from before the scene that was just played.
  const rosterReq = useRef(0);
  // A campaign inherits its whole world's roster, most of which never walks
  // on. The grid therefore opens on the campaign's own cast and offers the
  // inherited remainder behind a toggle.
  const [showAll, setShowAll] = useState(false);
  // The character a close is handing back to the grid, held until there is a
  // roster to judge it against. See `keepVisible` below.
  const pendingReveal = useRef<string | null>(null);
  const [revealTick, setRevealTick] = useState(0);

  // LIVE mirrors of state that async continuations have to read. A handler
  // closes over the values from the render that created it, and everything
  // below runs after at least one `await` -- so by the time it resumes, its
  // `scope`, `detail` and `voiceAnchor` may all describe a screen the user has
  // already left. Refs assigned during render always hold the current value.
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const liveAnchor = useRef({ cid: "", text: "", state: "loading" as typeof anchorState });
  liveAnchor.current = { cid: detail?.meta.id ?? "", text: voiceAnchor, state: anchorState };
  // Which character is open, for the `resetSignal` effect: it closes one
  // WITHOUT going through `backToGrid`, and it cannot take `detail` as a
  // dependency (it would then re-fire on every character opened, sending the
  // reader back to the grid they just left).
  const liveDetailId = useRef("");
  liveDetailId.current = detail?.meta.id ?? "";

  /** Hand a character back to the grid without letting the appeared filter
   *  swallow it.
   *
   *  A character can be reached without going through the grid at all -- a
   *  greeting's present-character link, an owner chip, a chub-unlinked chip --
   *  and nothing says it has appeared in this campaign. Landing on a grid that
   *  filters it out reads as the record having been deleted, so the filter
   *  yields to it instead.
   *
   *  Recorded rather than decided here, because BOTH of the closes that call
   *  this can run while the roster read is still in flight (the `focus` route
   *  opens a character on mount, in parallel with that read) -- and a decision
   *  taken against no roster is no decision, it just lets the character vanish
   *  the moment the roster lands. The effect below applies it as soon as there
   *  is something to apply it against, whichever order the two arrive in. */
  function keepVisible(id: string) {
    if (!id || liveScope.current.kind !== "campaign") return;
    pendingReveal.current = id;
    setRevealTick((n) => n + 1);   // re-run the resolver even if `appeared` is unchanged
  }

  useEffect(() => {
    const pend = pendingReveal.current;
    if (pend === null || appeared === null) return;   // nothing pending, or no roster yet
    pendingReveal.current = null;
    if (!appeared.has(pend)) setShowAll(true);
  }, [appeared, revealTick]);

  /** Install a freshly-read character — unless the editor has since left the
   *  scope it was read from, in which case drop it.
   *
   *  The scope effect below clears the open character precisely so a stale id
   *  cannot be combined with the new scope on a write. A read still in flight
   *  when the scope changes puts it straight back: the continuation calls
   *  `setDetail` with scope A's record while the component renders under scope
   *  B, and the next save — the anchor PUT among them — addresses B by A's id.
   *  Guarded here rather than at each call site, because every one of them is
   *  the same shape and only one of them has to forget. */
  function adopt(d: CharacterDetail, from: EntityScope): boolean {
    if (liveScope.current.kind !== from.kind || liveScope.current.id !== from.id) return false;
    setDetail(d);
    return true;
  }

  const reload = useCallback(() => api.listCharacters(scope).then(setChars), [scope.kind, scope.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    reload();
    setWizardOpen(false); // a scope change can reuse this instance; never carry a wizard across it
    // ...and never carry an OPEN CHARACTER across it either. `detail.meta.id`
    // is combined with the CURRENT `scope` on every write, so a character left
    // open across a scope change addresses the new world or campaign by the old
    // one's id -- and where that id also exists there, the save lands on the
    // wrong record. Bumping the anchor token additionally orphans a GET still
    // in flight for the scope we just left, whose reply would otherwise fill
    // the textarea with the other scope's anchor.
    anchorReq.current++;
    // ...and never carry the grid FILTER across it either. `showAll` is a
    // statement about one campaign's inherited roster; left standing it opens
    // the next campaign on its whole world instead of on its own cast, which is
    // the state this filter exists to avoid. Reset here rather than beside the
    // roster read, which also re-runs on `resetSignal` -- re-clicking the tab
    // should not undo a toggle the reader just made.
    setShowAll(false);
    pendingReveal.current = null;
    // `setDetail(null)` below does not take effect until the next render, but
    // the `resetSignal` effect can run before that render -- in the same commit,
    // when a caller changes both props at once -- and would then read the ref
    // and hand the PREVIOUS scope's character to this one's filter, re-arming
    // the reveal this line just disarmed and opening the new campaign on its
    // whole inherited roster. Clearing the ref is immediate, so that window
    // closes (Codex review, round 3).
    liveDetailId.current = "";
    setDetail(null);
    setCard(null);
    setMode("grid");
    setVoiceAnchor("");
    setAnchorState("loading");
    setAnchorBusy(false);
    setAnchorSaving(false);
  }, [reload]);

  // Who has ever been cast here. Re-read on `resetSignal` as well as on a scope
  // change, so re-clicking the Characters tab after playing a scene picks up
  // the actors that scene introduced rather than showing a roster from before
  // it. World scope has no appearances at all, so it clears instead of fetching
  // -- and clearing matters, because this instance is reused across a scope
  // change and a campaign's set left standing would filter a world's grid.
  useEffect(() => {
    // Bumped before the early return too: a world scope must orphan a campaign
    // read still in flight, or that reply installs a campaign's `appeared` set
    // over a world's grid and filters records that have no appearances at all.
    const req = ++rosterReq.current;
    setAppeared(null);
    setRosterFailed(false);
    if (scope.kind !== "campaign") return;
    api.listAppearances(scope.id)
      .then((roster) => {
        if (rosterReq.current !== req) return;   // a later read owns the answer
        // A character closed before this landed is judged by the `keepVisible`
        // resolver above, which re-runs on this very state change.
        setAppeared(new Set(roster.filter((r) => r.kind === "characters").map((r) => r.id)));
      })
      // An unreadable roster must not hide the records it was meant to narrow:
      // the filter is withdrawn entirely and the grid shows everything. Tracked
      // separately from "still loading" so the grid can wait for one and not
      // the other.
      .catch(() => { if (rosterReq.current === req) setRosterFailed(true); });
  }, [scope.kind, scope.id, resetSignal]);  // eslint-disable-line react-hooks/exhaustive-deps

  // re-clicking the Characters tab (resetSignal bumps) returns to the grid.
  // This is a close like `backToGrid`'s, so it owes the character it closes the
  // same protection from the appeared filter -- it just gets there without
  // passing through that function.
  useEffect(() => {
    keepVisible(liveDetailId.current);
    setMode("grid");
    setDetail(null);
    setCard(null);
  }, [resetSignal]);  // eslint-disable-line react-hooks/exhaustive-deps

  // arrived via a present-character link: open that character at the given version
  useEffect(() => {
    if (focus) focusCharacter(focus.cid, focus.vid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // detail-view extras: tagged greeting images + world greetings featuring this character
  const detailCid = detail?.meta.id;
  useEffect(() => {
    if (!detailCid) return;
    if (worldScope) api.listImageAppearances(wid, detailCid).then(setImageAppearances).catch(() => setImageAppearances([]));
    api.listGreetings(scope).then(setWorldGreetings).catch(() => setWorldGreetings([]));
  }, [wid, worldScope, scope.kind, scope.id, detailCid]);  // eslint-disable-line react-hooks/exhaustive-deps

  const hasAvatar = (detail && card)
    ? (detail.versions.find((v) => v.id === vid)?.images ?? []).includes("avatar")
    : false;
  const avatarFocus = detail?.versions.find((v) => v.id === vid)?.avatar_focus ?? null;
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
    setCropOpen(false);
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
      const d = await api.readCharacter(scope, cid);
      if (!adopt(d, scope)) return;
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

  async function select(cid: string): Promise<CharacterDetail | null> {
    setError(null);
    const d = await api.readCharacter(scope, cid);
    if (!adopt(d, scope)) return null;
    setBirthdate(d.meta.birthdate ?? "");
    loadVersion(d, d.meta.default_version);
    // `select()` is the refresh EVERY other save runs (card version, avatar,
    // lock, import...), and none of them touch the anchor -- so reloading it
    // unconditionally discarded an anchor draft whenever the user edited the
    // card and the anchor in one sitting and saved the card first. Reload only
    // when there is nothing to lose: a different character, or no unsaved edit.
    // `focusCharacter` still reloads unconditionally -- that is navigation TO a
    // character, not a refresh of the one already open.
    //
    // Read LIVE, not from the closure. Every caller reaches here after awaiting
    // its own write, and a draft typed during that await is invisible to the
    // render snapshot the handler captured -- so the closed-over `voiceAnchor`
    // still equals `anchorLoaded` and the draft is judged absent, one keystroke
    // too late to save it.
    const live = liveAnchor.current;
    const keepDraft = live.cid === cid && live.state === "ready"
      && live.text !== anchorLoaded.current;
    if (!keepDraft) loadVoiceAnchor(cid);   // campaign-local characters need one too (#59)
    if (worldScope) loadTagline(cid);
    else await loadLockState(cid);
    return d;
  }

  async function loadLockState(cid: string) {
    // token drops a slow earlier response so selecting A then B can't show A's lock on B
    const req = ++lockReq.current;
    const roster = await api.listAppearances(scope.id).catch(() => []);
    if (lockReq.current !== req) return;
    setLocked(roster.find((r) => r.kind === "characters" && r.id === cid)?.version ?? null);
    setImportVid("");
    // the source world's versions feed the import picker; a deleted world char offers none
    api.readCharacter({ kind: "world", id: wid }, cid)
      .then((w) => { if (lockReq.current === req) setWorldVersions(w.versions.map((v) => ({ id: v.id, name: v.name }))); })
      .catch(() => { if (lockReq.current === req) setWorldVersions([]); });
  }

  async function runPick() {
    if (!detail) return;
    if (!window.confirm(`Lock '${detail.meta.name}' to this version? Other versions are removed from the campaign.`)) return;
    try {
      await api.pickVersion(scope.id, "characters", detail.meta.id, vid);
      await select(detail.meta.id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function runImport() {
    if (!detail || !importVid) return;
    if (!window.confirm("Replace the locked version with the world's copy?")) return;
    try {
      await api.importVersion(scope.id, "characters", detail.meta.id, importVid);
      await select(detail.meta.id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
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

  // Same request-token discipline as the tagline, and its own counter: the two
  // GETs race independently, so sharing one token would let the slower of a
  // single selection cancel the faster.
  //
  // Unlike the tagline this also tracks load STATE, because a blank anchor is
  // destructive: PUT "" deletes it. "" is the placeholder shown while the GET
  // is in flight and what a failed GET leaves behind, so saving from either
  // state would silently remove a stored anchor the user never saw. Save is
  // disabled until a load actually succeeds.
  function loadVoiceAnchor(cid: string) {
    const req = ++anchorReq.current;
    setVoiceAnchor("");
    setAnchorState("loading");
    // Bumping the token above just orphaned any in-flight generation, and an
    // orphan's `finally` no longer matches, so it will never clear this itself.
    // Without this line one abandoned Generate disables the button for every
    // character selected afterwards, until the editor remounts.
    setAnchorBusy(false);
    // Same for an orphaned SAVE: its `finally` no longer matches either, so
    // without this a PUT abandoned by navigation would leave Save disabled for
    // every character opened afterwards.
    setAnchorSaving(false);
    api.getCharacterVoiceAnchor(scope, cid)
      .then((r) => {
        if (anchorReq.current !== req) return;
        setVoiceAnchor(r.voice_anchor);
        anchorLoaded.current = r.voice_anchor;
        setAnchorState("ready");
      })
      .catch(() => { if (anchorReq.current === req) setAnchorState("error"); });
  }

  async function saveVoiceAnchor() {
    if (!detail || anchorState !== "ready" || anchorSaving) return;
    // One PUT at a time. Two overlapping saves race on the server, and the
    // SLOWER one wins the file -- so an edit made between them can end up
    // discarded while the editor still shows it. Blocking the second click is
    // enough here: the writes are whole-value, so there is nothing to merge.
    setAnchorSaving(true);
    // Tokened like the load and the generation -- CAPTURED, not bumped, since a
    // save invalidates nothing. The single-flight check above is not enough on
    // its own: leaving the character (or the scope) clears `anchorSaving`, so
    // the next save is free to start while this PUT is still open, and this
    // one's `finally` would then clear the flag out from under it. Two writes
    // for the same character overlap, the slower wins the file, and the editor
    // goes on showing the newer text.
    const req = anchorReq.current;
    try {
      // Trimmed to blank on purpose: a blank anchor removes it, which is how a
      // character opts back out of voice-drift detection. Only reachable once
      // the load succeeded, so the blank is the user's, not a placeholder.
      await api.setCharacterVoiceAnchor(scope, detail.meta.id, voiceAnchor.trim());
      if (anchorReq.current !== req) return;
      anchorLoaded.current = voiceAnchor;   // in sync again: no longer a draft
    } catch (err: any) {
      if (anchorReq.current === req) setError(err.detail ?? String(err));
    } finally {
      if (anchorReq.current === req) setAnchorSaving(false);
    }
  }

  async function regenerateVoiceAnchor() {
    // `anchorSaving` too: a generation landing around an open PUT swaps the
    // textarea for a fresh draft while Save returns to its idle label, so the
    // control says "saved" over a value that never was.
    if (!detail || anchorSaving) return;
    // Tokened like the GET: generation is slow, and without this a draft for
    // character A lands in character B's textarea if the user navigates while
    // it is in flight -- and Save writes it under B, since it reads the CURRENT
    // detail id.
    const req = ++anchorReq.current;
    setAnchorBusy(true);
    try {
      const r = await api.generateCharacterVoiceAnchor(scope, detail.meta.id);
      if (anchorReq.current !== req) return;
      // An empty completion is a failed generation, not a draft. Installing it
      // would arm the destructive save with a blank the user never wrote, and
      // one click would delete a stored anchor that generation failed to
      // replace. Keep the loaded value and state untouched.
      if (!r.voice_anchor.trim()) {
        setError("The model returned an empty voice anchor — nothing was changed.");
        return;
      }
      setVoiceAnchor(r.voice_anchor);
      setAnchorState("ready");
    } catch (err: any) {
      if (anchorReq.current === req) setError(err.detail ?? String(err));
    } finally {
      if (anchorReq.current === req) setAnchorBusy(false);
    }
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
    scrollShellToTop();
    const d = await select(cid);
    if (!d) return null;   // scope changed under the read; do not open anything
    setMode("detail");
    return d;
  }

  async function focusCharacter(cid: string, vid: string) {
    scrollShellToTop();
    setError(null);
    const d = await api.readCharacter(scope, cid);
    if (!adopt(d, scope)) return;
    setBirthdate(d.meta.birthdate ?? "");
    loadVersion(d, d.versions.some((v) => v.id === vid) ? vid : d.meta.default_version);
    loadVoiceAnchor(cid);   // campaign-local characters need one too (#59)
    if (worldScope) loadTagline(cid);
    else await loadLockState(cid);
    setMode("detail");
  }

  async function openEdit(cid: string) {
    scrollShellToTop();
    if (!(await select(cid))) return;   // scope changed under the read
    setMode("edit");
  }

  function backToGrid() {
    scrollShellToTop();
    keepVisible(liveDetailId.current);
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
      await api.updateVersion(scope, detail.meta.id, vid, buildCard());
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
    const { version } = await api.createVersion(scope, detail.meta.id, { name, card: buildCard() });
    if (!(await select(detail.meta.id))) return;   // scope changed under the read
    loadVersion(await api.readCharacter(scope, detail.meta.id), version);
  }

  async function onImportVersion(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    try {
      const { version } = await api.importCharacter(wid, file, formatOf(file), detail.meta.id);
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
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
    await api.setDefaultVersion(scope, detail.meta.id, vid);
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
      await api.putImage(scope, detail.meta.id, vid, "avatar", file);
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
    await api.deleteImage(scope, detail.meta.id, vid, "avatar");
    await select(detail.meta.id);
    await reload();
    setAvatarBust((n) => n + 1);
  }

  // Reload the open version in place (select() would snap back to the default version).
  async function refreshVersion() {
    if (!detail) return;
    const d = await api.readCharacter(scope, detail.meta.id);
    if (!adopt(d, scope)) return;
    loadVersion(d, vid);
    await reload();
    setAvatarBust((n) => n + 1);
  }

  async function promote(name: string) {
    if (!detail) return;
    setError(null);
    try {
      await api.promoteImage(scope, detail.meta.id, vid, name);
      await refreshVersion();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function copyFromGreeting(a: Appearance, slot: "avatar" | "gallery") {
    if (!detail) return;
    setError(null);
    try {
      await api.copyGreetingImage(scope, detail.meta.id, vid, { gid: a.gid, name: a.name, slot });
      await refreshVersion();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function saveFocus(f: number) {
    if (!detail) return;
    setCropOpen(false);
    setError(null);
    try {
      await api.setAvatarFocus(scope, detail.meta.id, vid, f);
      await refreshVersion();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function onShelfAdd(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    const next = hasAvatar
      ? `gallery_${galleryImages.reduce((m, n) => Math.max(m, Number(n.slice("gallery_".length))), 0) + 1}`
      : "avatar";
    try {
      await api.putImage(scope, detail.meta.id, vid, next, file);
      await refreshVersion();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
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
      if (d) setTaglineQueue([{ cid: imported[0].cid, name: d.meta.name }]);
      await runLocalize(imported[0].cid, imported[0].version);
    } else if (imported.length > 1) {
      await runBulkLocalize(imported);
    }
  }

  // Bulk pipeline for "Download from URL". Per URL: import (the backend already
  // downloads the avatar, chub gallery, and related chub lorebooks inside this
  // one call), localize embedded images, then import the card's embedded
  // character_book to world lore. Failures record and continue — one bad URL
  // shouldn't sink the batch. Tagline prompts queue up after the whole run.
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
      } catch (err: any) {
        failures.push(`${urls[i]}: ${err.detail ?? String(err)}`);
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
      } catch (err: any) {
        failures.push(`${name}: localize failed (${err.detail ?? String(err)})`);
      }
      setBulkUrl({ current: i + 1, total: urls.length, name, step: "importing lorebook" });
      try {
        const { created } = await api.importCharacterBook(wid, result.character, result.version);
        lore += created.length;
      } catch (err: any) {
        failures.push(`${name}: lorebook import failed (${err.detail ?? String(err)})`);
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
    if (urls.length === 1 && added.length === 1) await openDetail(added[0].cid);
    setTaglineQueue(added);
  }

  async function downloadVersionFromChub() {
    if (!detail) return;
    const url = window.prompt("Card URL (chub.ai link or a direct URL)?")?.trim();
    if (!url) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, url, detail.meta.id, vid);
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
      loadVersion(d, result.version);
      await reload();
      setImportMsg(describeChubResult(result));
      await runLocalize(detail.meta.id, result.version);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  // One-click re-import from the version's stored link — the backend matches
  // the source and overwrites this version in place instead of forking a new one.
  async function redownloadFromChub() {
    if (!detail || !chubSource) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, chubSource, detail.meta.id, vid);
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
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
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
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
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
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
      const d = await api.readCharacter(scope, detail.meta.id);
      if (!adopt(d, scope)) return;
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
    api.actorImageUrl(scope, cid, version, "avatar") + (bust ? `?v=${avatarBust}` : "");

  if (wizardOpen && module && worldScope) {
    return (
      <div className="character-editor">
        <CreationWizard scope={scope} kind="characters" module={module}
                        createRecord={(n) => api.createCharacter(wid, { name: n }).then((r) => r.character)}
                        deleteRecord={(id) => api.deleteCharacter(wid, id).then(() => {})}
                        onDone={async (id) => { setWizardOpen(false); await reload(); await openEdit(id); }}
                        onCancel={() => setWizardOpen(false)} />
      </div>
    );
  }

  if (mode === "grid" || !detail || !card) {
    // The filter is offered only where "appeared" means something and the
    // roster actually loaded; everywhere else `shown` is simply every card.
    const filterable = !worldScope && appeared !== null;
    const appearedChars = filterable ? chars.filter((c) => appeared.has(c.id)) : chars;
    const shown = filterable && !showAll ? appearedChars : chars;
    // Campaign scope has no verdict yet while the roster is in flight. Painting
    // the grid anyway shows every inherited character for as long as that read
    // takes and then yanks most of them away -- so the cards (and the "nobody
    // yet" line, which would be equally wrong) wait for the answer. A FAILED
    // read is not this state: it has its answer, which is "do not filter".
    const rosterPending = !worldScope && appeared === null && !rosterFailed;
    return (
      <div className="character-editor">
        {taglineQueue.length > 0 && (
          <TaglinePrompt key={taglineQueue[0].cid} wid={wid} cid={taglineQueue[0].cid} name={taglineQueue[0].name}
                         onSaved={(t) => { setTagline(t); reload(); }}
                         onClose={() => setTaglineQueue((q) => q.slice(1))} />
        )}
        {urlPromptOpen && (
          <UrlImportPrompt onClose={() => setUrlPromptOpen(false)} onSubmit={runBulkUrlImport} />
        )}
        <div className="grid-toolbar">
          {worldScope && <>
            <button className="primary" onClick={newCharacter}>+ New character</button>
            {worldScope && module && Object.values(module.sheets.sheet_types).some((st) => st.kind === "characters") && (
              <button className="subtle" onClick={() => setWizardOpen(true)}>+ New character with sheet…</button>
            )}
            <button className="subtle" onClick={() => fileRef.current?.click()}>Import card</button>
            <input ref={fileRef} type="file" accept=".json,.png,.charx" multiple hidden aria-label="Import character card" onChange={onImport} />
            <button className="subtle" onClick={() => setUrlPromptOpen(true)}>Download from URL</button>
            <button className="subtle" onClick={checkChubLinks}>Check chub.ai links</button>
          </>}

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
                <button className="char-card-main" onClick={() => openDetail(c.id)}>
                  {c.has_avatar
                    ? <img className="char-card-avatar" alt="" style={focusStyle(c.avatar_focus)}
                           src={avatarSrc(c.id, c.default_version, true)} />
                    : <div className="initials-avatar" aria-hidden>
                        {c.name.split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("")}
                      </div>}
                  <span className="char-card-name">{c.name}</span>
                  {c.tagline ? <span className="char-card-tagline">{c.tagline}</span> : null}
                  {((c.gallery_count ?? 0) > 0 || (c.localized_count ?? 0) > 0 || (c.greeting_count ?? 0) > 0) && (
                    <span className="char-card-badges">
                      {(c.greeting_count ?? 0) > 0 && (
                        <span className="chip">{c.greeting_count} greeting{c.greeting_count === 1 ? "" : "s"}</span>
                      )}
                      {(c.gallery_count ?? 0) > 0 && <span className="chip">{c.gallery_count} gallery</span>}
                      {(c.localized_count ?? 0) > 0 && <span className="chip">{c.localized_count} localized</span>}
                    </span>
                  )}
                </button>
                <div className="char-card-actions">
                  <button className="subtle" onClick={() => openEdit(c.id)}>Edit</button>
                  {worldScope && <button className="subtle" onClick={() => deleteCharacter(c.id, c.name)}>Delete</button>}
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
        {taglineQueue.length > 0 && (
          <TaglinePrompt key={taglineQueue[0].cid} wid={wid} cid={taglineQueue[0].cid} name={taglineQueue[0].name}
                         onSaved={(t) => { setTagline(t); reload(); }}
                         onClose={() => setTaglineQueue((q) => q.slice(1))} />
        )}
        {cropOpen && hasAvatar && (
          <AvatarFocusPicker src={avatarSrc(detail.meta.id, vid, true)}
                             initial={avatarFocus ?? 50}
                             onSave={saveFocus}
                             onClose={() => setCropOpen(false)} />
        )}
        <div className="editor-body">
          <button className="subtle back" onClick={backToGrid}>‹ All characters</button>
          {error && <div className="banner">{error}</div>}
          {importMsg && <span className="field-hint">{importMsg}</span>}
          <div className="detail">
            <div className="detail-head">
              {hasAvatar
                ? <button className="avatar-crop-btn" type="button" aria-label="Adjust avatar crop"
                          title="Adjust avatar crop" onClick={() => setCropOpen(true)}>
                    <img className="detail-avatar" alt="" style={focusStyle(avatarFocus)}
                         src={avatarSrc(detail.meta.id, vid, true)} />
                  </button>
                : <div className="initials-avatar detail" aria-hidden>
                    {(card.data.name || detail.meta.name).split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("")}
                  </div>}
              <div className="detail-meta">
                <h3 className="detail-name">{card.data.name || detail.meta.name}</h3>
                {tagline && <div className="detail-text tagline">{tagline}</div>}
                {card.data.creator ? <div className="detail-byline">by {card.data.creator}</div> : null}
                {tags.length > 0 && (
                  <div className="chips">{tags.map((t) => <span className="chip" key={t}>{t}</span>)}</div>
                )}
              </div>
              <div className="detail-actions">
                {detail.versions.length > 1 && (
                  <div>
                    <span className="segmented-caption">Version</span>
                    <div className="segmented" role="group" aria-label="Version">
                      {detail.versions.map((v) => (
                        <button key={v.id} aria-pressed={v.id === vid}
                                className={v.id === vid ? "active" : ""}
                                onClick={() => loadVersion(detail, v.id)}>
                          {v.name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                <button className="primary" onClick={() => setMode("edit")}>Edit</button>
                {worldScope && <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>}
              </div>
            </div>

            {!worldScope && (
              <div className="side-section">
                <h4>Version</h4>
                {locked ? (
                  <>
                    <span className="field-hint">Locked to <b>{detail.versions.find((v) => v.id === locked)?.name ?? locked}</b> for this campaign. </span>
                    <select aria-label="Import version" value={importVid}
                            onChange={(e) => setImportVid(e.target.value)}>
                      <option value="">— world version —</option>
                      {worldVersions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                    </select>
                    <button className="subtle" disabled={!importVid} onClick={runImport}>Import from world</button>
                  </>
                ) : detail.versions.length > 1 ? (
                  <>
                    <span className="field-hint">Picking locks the viewed version and removes the others from this campaign. </span>
                    <button className="subtle" onClick={runPick}>Pick this version</button>
                  </>
                ) : (
                  <span className="field-hint">Single version; it locks when first used in a scene.</span>
                )}
              </div>
            )}

            {module && detail && (
              <SheetPanel scope={scope} module={module} kind="characters" eid={detail.meta.id} />
              /* onOpenRef intentionally unset here: no cross-editor navigation target exists
                 yet from a character/PC sheet's ref chips (entity-form refs only; module-content
                 ref chips still preview correctly without it) */
            )}

            {worldScope && <div className="chub-source-block">
              {chubSource ? (
                <>
                  <a className="field-hint"
                     href={chubSource.startsWith("http") ? chubSource : `https://chub.ai/characters/${chubSource}`}
                     target="_blank" rel="noreferrer">
                    {chubSource}
                  </a>
                  <button className="subtle" type="button" onClick={redownloadFromChub}>Re-download</button>
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
            </div>}

            {(card.data.extensions?.sd_prompt) && (
              <div className="side-section">
                <h4>Image prompt</h4>
                <div className="field-hint">{card.data.extensions.sd_prompt}</div>
              </div>
            )}

            <div className="detail-field">
              <div className="section-label">Images</div>
              <div className="images-shelf">
                {hasAvatar ? (
                  <figure className="shelf-tile avatar-tile">
                    <a href={avatarSrc(detail.meta.id, vid, true)} target="_blank" rel="noreferrer">
                      <img alt="avatar image" src={avatarSrc(detail.meta.id, vid, true)} />
                    </a>
                    <figcaption>avatar</figcaption>
                  </figure>
                ) : (
                  <div className="shelf-tile shelf-empty">no avatar</div>
                )}
                {galleryImages.map((name) => {
                  const src = `${api.actorImageUrl(scope, detail.meta.id, vid, name)}?v=${avatarBust}`;
                  return (
                    <div className="shelf-tile" key={name}>
                      <a href={src} target="_blank" rel="noreferrer"><img alt={name} src={src} /></a>
                      <button className="shelf-promote" onClick={() => promote(name)}>Set as avatar</button>
                    </div>
                  );
                })}
                <button className="shelf-add" onClick={() => shelfFileRef.current?.click()}>+ add</button>
                <input ref={shelfFileRef} type="file" accept="image/*" hidden
                       aria-label="Add image" onChange={onShelfAdd} />
              </div>
            </div>

            {imageAppearances.length > 0 && (
              <div className="detail-field">
                <div className="section-label">Appears in</div>
                <div className="images-shelf">
                  {imageAppearances.map((a) => (
                    <div className="shelf-tile" key={`${a.gid}/${a.name}`}>
                      <a href={a.url} target="_blank" rel="noreferrer">
                        <img alt={`${a.greeting_name} art`} src={a.thumb ?? a.url} />
                      </a>
                      <button className="shelf-promote" onClick={() => copyFromGreeting(a, "avatar")}>Set as avatar</button>
                      <button className="shelf-promote" onClick={() => copyFromGreeting(a, "gallery")}>Add to gallery</button>
                      {onOpenGreeting && (
                        <button className="shelf-promote" onClick={() => onOpenGreeting(a.gid)}>{a.greeting_name}</button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {worldScope && localizeControls(false)}

            {onOpenLore && (
              <OwnedLorePanel
                scope={scope}
                ownerRef={`characters:${detail.meta.id}`}
                onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                onNewEntry={() => onOpenLore({ newOwner: `characters:${detail.meta.id}` })}
              />
            )}

            {TEXT_FIELDS.map((f) => {
              const val = (card.data[f.key] as string) ?? "";
              if (!val.trim()) return null;
              return (
                <div className="detail-field" key={f.key}>
                  <div className="section-label">{f.label}</div>
                  {f.key === "first_mes"
                    ? <GreetingMarkdown>{val}</GreetingMarkdown>
                    : f.key === "creator_notes"
                      ? <HtmlNote html={val} title="Creator notes" />
                      : <div className="detail-text">{val}</div>}
                </div>
              );
            })}

            {greetings.length > 0 && (
              <div className="detail-field">
                <div className="section-label">Alternate greetings</div>
                {greetings.map((g, i) => (
                  <blockquote className="greeting-quote" key={i}>
                    <GreetingMarkdown>{g}</GreetingMarkdown>
                  </blockquote>
                ))}
              </div>
            )}

            {(() => {
              // world greetings featuring this character — links, not card content
              const mine = worldGreetings.filter((g) => (g.present ?? []).includes(detail.meta.id));
              if (mine.length === 0) return null;
              return (
                <div className="detail-field">
                  <div className="section-label">World greetings</div>
                  <div className="chips">
                    {mine.map((g) => (
                      <button key={g.id} className="chip on" onClick={() => onOpenGreeting?.(g.id)}>
                        {g.character === detail.meta.id ? `★ ${g.name}` : g.name}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })()}
          </div>
        </div>
      </div>
    );
  }

  // mode === "edit"
  return (
    <div className="character-editor">
      {taglineQueue.length > 0 && (
        <TaglinePrompt key={taglineQueue[0].cid} wid={wid} cid={taglineQueue[0].cid} name={taglineQueue[0].name}
                       onSaved={(t) => { setTagline(t); reload(); }}
                       onClose={() => setTaglineQueue((q) => q.slice(1))} />
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
            {(worldScope || !locked) && <button className="subtle" onClick={addVersion}>+ Version</button>}
            {worldScope && <>
              <button className="subtle" onClick={() => versionFileRef.current?.click()}>Import version</button>
              <input ref={versionFileRef} type="file" accept=".json,.png,.charx" hidden
                     aria-label="Import version" onChange={onImportVersion} />
            </>}
            <button className="subtle" onClick={setDefault}>Set default</button>
            {worldScope && <>
              <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>
              <button className="subtle" onClick={downloadVersionFromChub}>Download version from URL</button>
            </>}
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

          {worldScope && localizeControls(dirty, "Save your changes before localizing images")}

          <Field label="Name">
            <input type="text" value={card.data.name ?? ""} onChange={(e) => setField("name", e.target.value)} />
          </Field>
          <Field label="Creator">
            <input type="text" value={card.data.creator ?? ""} onChange={(e) => setField("creator", e.target.value)} />
          </Field>
          {worldScope && <>
            <Field label="Birthdate">
              {/* Persist only complete dates: the picker emits "" for every
                  intermediate state, which must never blank the stored value.
                  (Tradeoff: retyping the year with month/day set can briefly
                  persist a transient valid year — accepted, no debouncing.) */}
              <CalendarDatePicker scope={{ kind: "world", id: wid }} value={birthdate}
                                  onChange={(v) => { setBirthdate(v); if (v) saveBirthdate(v); }}
                                  ariaLabel="Birthdate" />
              {birthdate && <button className="subtle" type="button"
                                    onClick={() => saveBirthdate("")}>Clear</button>}
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
          </>}
            <Field label="Voice anchor"
                   hint="how they SOUND — absorb checks each scene against this and flags drift; clear it to skip the check">
              {/* Disabled while BUSY as well as while loading: a generation in
                  flight will overwrite this box when it lands, so edits made
                  meanwhile would be silently discarded. */}
              <textarea aria-label="Voice anchor" value={voiceAnchor} rows={5}
                        disabled={anchorState === "loading" || anchorBusy}
                        onChange={(e) => setVoiceAnchor(e.target.value)} />
            </Field>
            {anchorState === "error" && (
              <p className="field-hint">Could not load the voice anchor — reopen this
                character to try again. Saving is disabled so a failed read cannot
                overwrite the stored anchor with a blank.</p>)}
            <div className="form-actions">
              {/* Also disabled while the initial GET is in flight: generating
                  bumps the request token and invalidates that load, so a
                  generation that then fails would strand `anchorState` in
                  "loading" — every control disabled, no error shown, and no way
                  out but reopening the character. */}
              <button className="subtle" type="button"
                      disabled={anchorBusy || anchorSaving || anchorState === "loading"}
                      onClick={regenerateVoiceAnchor}>
                {anchorBusy ? "Generating…" : "Generate"}
              </button>
              {/* Disabled while generating too, not just while loading. A save
                  that lands mid-generation persists the OLD text, and the
                  completion then replaces the textarea with a fresh draft — so
                  the save the user just watched succeed covers a value that is
                  no longer on screen, and the draft they can see is unsaved. */}
              <button className="subtle" type="button"
                      disabled={anchorState !== "ready" || anchorBusy || anchorSaving}
                      onClick={saveVoiceAnchor}>
                {anchorSaving ? "Saving…" : "Save voice anchor"}
              </button>
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

          {worldScope && bookCount > 0 && (
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
