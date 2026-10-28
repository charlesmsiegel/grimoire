import type { Dispatch, SetStateAction } from "react";
import { useLayoutEffect, useRef, useState } from "react";
import type { Message } from "../../api/client";
import {
  api
} from "../../api/client";
import { errorText } from "../../api/errors";
import { quotedIn } from "../../components/play/citation";
import type { PlayError } from "../../components/play/PlayBanners";
import { NEAR_BOTTOM_PX, NEAR_TOP_PX, OPTIMISTIC_TOKEN, PAGE_SIZE, useStableCallback } from "./campaignShared";
import type { Current, SceneKey, WindowKey } from "./campaignTypes";

export function useTranscriptWindow({
  cid, activeId, setError, selectIdentity, streaming, currentCampaign, onSceneSwitch, onReadRequested,
  onInstalled, setCitedQuote,
}: {
  cid: string;
  activeId: string | null;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  selectIdentity: (scope: Readonly<{ cid: string; sid: string; }> | null) => void;
  streaming: string;
  currentCampaign: () => string;
  onSceneSwitch: (_scope: Readonly<{ cid: string; sid: string; }>) => void;
  onReadRequested: (window: WindowKey, renamed: boolean) => void;
  onInstalled: (_window: WindowKey) => void;
  setCitedQuote: Dispatch<SetStateAction<string>>;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  // Which transcript is actually ON SCREEN, campaign and scene. `activeId` is
  // what the user picked; this is what has landed. They differ while a select is
  // in flight, and anything keyed to the transcript — the swipe control above
  // all — has to follow this one, or it renders against the previous scene's
  // posts. Both halves, for the reason the alternate state carries both: scene
  // ids repeat across campaigns, so A→B with a colliding id reads as a refresh
  // and a sid-only key never notices the transcript is still A's.
  const [loaded, setLoaded] = useState<{ cid: string; sid: string; token: number } | null>(null);
  // Which scene's transcript is on screen, for the one reader that has to keep
  // its answer through a rename: the empty-scene setup panel. `loaded` is the
  // answer for everything that ACTS on the posts, and a rename drops it on
  // purpose (`reviewSceneRenamed`) until the re-read lands. The setup panel acts
  // on no post, and a first date set -- one of its own fields -- is a rename, so
  // gating it on `loaded` would remount it mid-setup, throwing away what it
  // holds and re-reading the campaign's whole character list. This follows the
  // rename instead (`adoptSceneId`), and is otherwise set and cleared exactly
  // where `loaded` is.
  const [landedScene, setLandedScene] = useState<{ cid: string; sid: string } | null>(null);

  // "The transcript on screen is the active scene's own."
  //
  // They diverge for exactly one read: `runStream`'s finally refreshes the
  // TURN's scene, which is allowed to install over the scene the reader has
  // since opened (the pull-back is load-bearing — a failed turn's recovered
  // prompt has to appear against the scene it was written for). The resolver
  // then corrects `activeId` back to the URL's scene, and until that read lands
  // the previous scene's messages are still rendered.
  //
  // EVERY control that derives an index, an affordance or an optimistic removal
  // from the RENDERED messages and then writes to `activeId` has to read this,
  // or it shows the reader one scene's transcript and mutates another's. Named
  // once because doing it per control is how the second one got missed: the
  // edit form was gated and reroll was not, so Regenerate replaced a reply of
  // the active scene that the reader had never been shown (codex review, P1).
  //
  // Null `loaded` is UNKNOWN ownership, not safe ownership. This used to read
  // `!loaded ||`, on the reasoning that the only thing that nulls it is an
  // optimistic edit, which extends the active scene's own transcript. True at
  // the moment of the edit and false immediately after: nothing re-establishes
  // it when the reader navigates, so a send or reroll followed by opening
  // another scene left the previous scene's posts rendered, `activeId` moved,
  // and this predicate answering "active" — which is exactly the state it
  // exists to forbid (codex review, P1). `showOptimistically` now keeps the
  // ownership and drops only the fetch identity, so the honest answer is
  // available and the carve-out is gone.
  //
  // The other two nulls — a rename that outdated the set, a swap whose re-read
  // failed — mean "nobody knows what this transcript is" and are correctly
  // false here too.
  const transcriptIsActive =
    !!loaded && loaded.cid === cid && loaded.sid === activeId;

  // Every LOCAL edit to the transcript goes through here, and each one retires
  // `loaded`'s FETCH IDENTITY while keeping its ownership. Matching tokens only
  // prove a set and a *fetch* describe the same scene — an optimistic edit
  // changes the posts under both without touching either, so a
  // stale-but-consistent pair still passes. Reroll is the case that bites: it
  // removes the displayed reply, and the set keyed to that reply stays valid,
  // so the picker drops onto the user post above it.
  //
  // It used to null the whole record, which threw away the answer to a
  // different question — WHICH scene these posts are — and `transcriptIsActive`
  // had no choice but to guess.
  //
  // The owner is CARRIED, never re-derived. Stamping the active scene's id
  // looks equivalent and is not: Send stays enabled while a freshly selected
  // scene is still loading (see `runStream`'s baseline read), so the reader can
  // select B and send while B's GET is in flight — `selected` already says B
  // while the messages being extended are still A's. Deriving the owner there
  // labels A's posts as B's, which is worse than not knowing, because the guard
  // then believes them and offers edits whose indices address A against a file
  // that is B (codex review, P1).
  //
  // Null stays null for the same reason: an unowned transcript is one nothing
  // may act on, and inventing an owner is exactly the mistake above.
  //
  // Loading and paging deliberately do NOT come through here: they are what
  // `loaded` describes, and a prepend extends that transcript rather than
  // replacing it.
  function showOptimistically(edit: (m: Message[]) => Message[]) {
    setMessages(edit);
    setLoaded((cur) => (cur ? { ...cur, token: OPTIMISTIC_TOKEN } : null));
  }
  // `messages` is a WINDOW onto the transcript's tail, not the whole of it.
  // `firstIndex` is the absolute index of `messages[0]`, so every index this
  // component hands the API (edit, reroll) stays the index the backend means,
  // and `firstIndex > 0` is exactly "there are older posts to load".
  const [firstIndex, setFirstIndex] = useState(0);
  // The guard is the REF; the state only drives the button's label. Scroll
  // fires in bursts and setState is async, so two events in one tick would
  // both read `loadingOlder === false` and prepend the same page twice.
  const [loadingOlder, setLoadingOlder] = useState(false);
  const loadingOlderRef = useRef(false);
  // How much history is on screen. A refresh of the *same* scene (after a
  // reply, an edit, a roll) re-reads this much rather than snapping back to
  // one page, so pages the reader already scrolled up to survive the re-fetch.
  const windowSizeRef = useRef(PAGE_SIZE);
  // Whether a player turn exists ANYWHERE in the transcript (the server
  // answers for the whole of it; null until a windowed read says). Reroll
  // hangs off this: an offscreen scene never stores a user post, so no amount
  // of unloaded history above the window implies one exists.
  const [hasUserPost, setHasUserPost] = useState<boolean | null>(null);
  // The greeting the scene on screen was started from ("" for none). Read off
  // the same load as its transcript, so an empty scene opened for an adapted
  // greeting (#91) still offers the adaptation after a reload.
  const [openedFrom, setOpenedFrom] = useState("");
  // Every scene load takes the next token; a page that resolves after the
  // window it was asked for has moved on is DROPPED. Without it an older-page
  // request for scene A, still in flight when the reader switches to scene B,
  // prepends A's posts onto B and installs A's offset — after which an edit
  // sends B's id with an A-derived index and overwrites an unrelated post.
  const windowTokenRef = useRef(0);
  // The transcript length the server last reported. A turn that failed without
  // a response compares against this to find out whether its post landed after
  // all — `beforeResponse` proves only that no response arrived, not that the
  // request never did (review caught the difference). Counts, not content:
  // `post_chat` expands macros before storing, so what came back never has to
  // equal what was typed.
  const totalRef = useRef(0);
  // Which scene `totalRef` is a count of. It is written by whichever read
  // landed last, which is not necessarily the scene a turn is about to run on.
  const totalSceneRef = useRef<string | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);

  // The two places that open a scene without an awaiting caller — the resolver
  // and a rail click on the row already open. `selectScene`'s rejection used to
  // go nowhere from here (an unhandled rejection, no banner), which left a
  // transcript that could not be read looking like a scene with no posts. Not
  // retryable: the banner's Retry generates, and there is nothing to generate.
  //
  // Scoped to the read that failed. A rejection carries no window token of its
  // own, so an unscoped handler raises its banner over whatever is on screen
  // when it finally lands — and the scene switch that retired the read has
  // already cleared the errors belonging to it, so the banner sticks, blaming
  // the current scene for a failure in one the reader has left (codex review).
  const readScene = useStableCallback((scope: SceneKey) => {
    // Stable implementation, captured request identity: a delayed completion
    // may call this after navigation, but may never borrow the new route cid.
    const p = readTranscript(scope);
    // Minted synchronously by the call above, before its first await.
    const token = windowTokenRef.current;
    p.catch((err: unknown) => {
      if (currentCampaign() !== scope.cid || windowTokenRef.current !== token) return;
      setError({
        text: `The scene could not be read: ` + errorText(err),
        retryable: false
      });
    });
  });

  // Nothing is on screen: the campaign has no scenes, or the last one was just
  // deleted. Mirrors what `selectScene` installs, in reverse.
  function clearScene() {
    windowTokenRef.current += 1;   // drop any page still in flight for it
    selectIdentity(null);
    setMessages([]);
    setLoaded(null);
    setLandedScene(null);
    setFirstIndex(0);
    setHasUserPost(null);
  }

  // Following the tail is the default and stays the default — but only while
  // the reader is AT the tail. Two things now move the stream and they must
  // not fight: new content at the bottom (scroll to it) and a page of older
  // posts prepended at the top (hold the reader where they were, which means
  // restoring the distance from the BOTTOM, since everything above them just
  // grew). `atBottomRef` is written by onScroll below and starts true, so an
  // untouched stream — and jsdom, where every scroll metric is 0 — follows.
  const atBottomRef = useRef(true);
  // distance from the bottom captured just before a prepend, consumed once
  const prependAnchorRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = streamRef.current;
    if (!el) return;
    const anchor = prependAnchorRef.current;
    if (anchor !== null) {
      prependAnchorRef.current = null;
      el.scrollTop = el.scrollHeight - anchor;
      return;
    }
    if (atBottomRef.current) el.scrollTo({ top: el.scrollHeight });
  }, [messages, streaming]);

  function onStreamScroll() {
    const el = streamRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;
    if (el.scrollTop <= NEAR_TOP_PX) void loadOlder();
  }

  // `stillWanted`, when given, is re-asked immediately before any state is
  // applied. Only background refreshes need it — the post-cancel flush poll,
  // which can be running while the player starts a new turn or opens another
  // scene. Returns the transcript's total length, or -1 if it bowed out — to
  // this predicate or to a later select retiring its page.
  // `renamed` is for the one caller whose id CHANGES without the reader going
  // anywhere: a rename mints a new scene id, so the `id !== activeId` test below
  // reads it as a switch and throws away the turn state — a one-shot response
  // preset picked for the next reply, and an open roll form. Same scene, same
  // reader; only the filename moved.
  async function readTranscript(scope: SceneKey, options?: { stillWanted?: Current | undefined; renamed?: boolean }) {
    const { cid, sid: id } = scope;
    const stillWanted = options?.stillWanted;
    const renamed = options?.renamed ?? false;
    // This closure belongs to the campaign it was created in. A background
    // refresh outlives the view it started in — `runStream`'s finally and the
    // flush poll both call this for the scene the TURN owns, long after the
    // reader may have moved to another campaign — and every existing guard on
    // those paths compares scene ids, which repeat freely between campaigns.
    //
    // So A's refresh for "s1" passes `(selected()?.sid ?? null) === "s1"` while the
    // reader is on B's "s1", installs A's transcript under B's URL, and leaves
    // edits addressing B's file with A's message indices. The resolver could
    // not repair it either: `setActiveId` is handed the value it already has,
    // React bails out of the render, and the effect never re-runs (codex
    // review, P1).
    //
    // Checked here rather than at the two call sites for the reason the rest of
    // this file keeps re-learning: this is the one funnel every refresh passes
    // through, so a future background path cannot forget it.
    if (currentCampaign() !== cid) return -1;
    if (stillWanted && !stillWanted()) return -1;
    // selectScene also runs to *refresh* the current scene (runStream's
    // finally, doRoll/doCheck, saveEdit, …) — only an actual scene switch
    // should clear the chip/popover synchronously below; clearing on every
    // refresh would tear down and re-mount a live SSE-delivered proposal
    // for no reason (flicker, and a stale ref by the time the re-fetch lands).
    const switchingScenes = !renamed && id !== activeId;
    const token = ++windowTokenRef.current; // retires any page still in flight
    selectIdentity({ cid, sid: id });
    if (switchingScenes) {
      onSceneSwitch({ cid, sid: id });
      // a new scene opens at its most recent page, at the bottom
      windowSizeRef.current = PAGE_SIZE;
      atBottomRef.current = true;
    }
    onReadRequested({ cid, sid: id, token }, renamed);
    const scene = await api.getScene(cid, id, { limit: windowSizeRef.current });
    if (windowTokenRef.current !== token) return -1; // a later select already landed
    // Asked again here, not only on entry: this fetch is an await, and a turn
    // starting during it would otherwise have the stale response clear the new
    // stream's live preview (`setStreaming`) below. Distinct from the window
    // token above, which retires a page superseded by a *later select on this
    // same view*; this one retires a refresh whose whole reason for existing
    // has expired.
    if (stillWanted && !stillWanted()) return -1;
    setMessages(scene.messages);
    setLoaded({ cid, sid: id, token });
    setLandedScene({ cid, sid: id });
    // an unwindowed reply (no `offset`) is the whole transcript, which starts at 0
    setFirstIndex(scene.offset ?? 0);
    setHasUserPost(scene.has_user_message ?? null);
    setOpenedFrom(scene.meta.greeting ?? "");
    onInstalled({ cid, sid: id, token });
    // `total`, not `messages.length`: the fetch is windowed now (#94), so once a
    // transcript is longer than a page the window size is a constant and would
    // report no growth however much lands. The flush poll compares this across
    // ticks to notice a cancelled turn's partial arriving.
    totalRef.current = scene.total ?? scene.messages.length;
    totalSceneRef.current = id;
    return totalRef.current;
  }

  // Prepend the page of posts just above the window. Called by the scroll
  // handler and by the button it backs; either way it is a no-op once the top
  // of the transcript is on screen or another page is already in flight.
  async function loadOlder() {
    const id = activeId;
    if (!id || loadingOlderRef.current || firstIndex <= 0) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    const el = streamRef.current;
    const token = windowTokenRef.current;
    const from = `older:${cid}:${id}:${token}`;
    try {
      const page = await api.getScene(cid, id, { limit: PAGE_SIZE, before: firstIndex });
      // the reader moved on (another scene, or a refresh that re-read the
      // window): this page describes a transcript that is no longer on screen
      // A new campaign's list may still be pending, before its selectScene
      // can retire the window token. Its route already owns the screen.
      if (currentCampaign() !== cid || windowTokenRef.current !== token) return;
      // A different operation may have replaced this window's failure banner.
      setError((current) => current?.from === from ? null : current);
      if (!page.messages.length) {
        setFirstIndex(0); // the transcript shrank under us; nothing older is left
        return;
      }
      // Measure from the bottom, not the top: the prepend pushes everything
      // down by however tall the new posts render, and only the layout effect
      // (after paint, when that height exists) can undo it.
      if (el) prependAnchorRef.current = el.scrollHeight - el.scrollTop;
      windowSizeRef.current += page.messages.length;
      setMessages((m) => [...page.messages, ...m]);
      setFirstIndex(page.offset ?? 0);
    } catch (err: unknown) {
      // The scroll handler and the button both drop this promise, so a page
      // that would not load was an unhandled rejection and a button that went
      // back to its label as if nothing had been asked. Scoped like
      // `readScene`'s report: a page for a window the reader has left says
      // nothing about the one on screen.
      if (currentCampaign() === cid && windowTokenRef.current === token) {
        setError({ text: `Older posts could not be loaded: ${errorText(err)}`, retryable: false, from });
      }
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }

  /** Scroll the transcript to the post a citation was taken from.
   *
   *  Matched by substring against the post bodies, which is the only join
   *  available: the citation records the model's excerpt, not a post index, and
   *  an index would rot the moment a post was edited or a reroll replaced one.
   *  A quote that matches nothing scrolls nowhere rather than guessing — the
   *  transcript may simply be paged past it, and jumping to an arbitrary post
   *  would be worse than not moving.
   */
  function goToQuote(quote: string) {
    const needle = quote.trim().toLowerCase();
    if (!needle) return;
    if (!messages.some((m) => quotedIn(m, needle))) return;
    setCitedQuote(quote);
    // Queued behind the render that paints the highlight, so the element it
    // scrolls to is the one that is about to be marked.
    requestAnimationFrame(() => {
      const el = streamRef.current?.querySelector(".msg.cited") as HTMLElement | null;
      el?.scrollIntoView?.({ block: "center" });
    });
  }
  // Dropped, not re-keyed. The posts on screen are only known to be this
  // scene's *transcript as fetched* — and a swap can be in flight against the
  // old id, so what the backend holds may already differ from what is
  // rendered. Re-keying would carry that readiness across a rename and let
  // the renamed set's counter sit on pre-swap text; dropping it hides the
  // control until a real load lands, which the callers below arrange.
  function renameTranscript(scope: SceneKey, newId: string) {
    setLoaded((l) => l && l.cid === scope.cid && l.sid === scope.sid ? null : l);
    setLandedScene((l) => l && l.cid === scope.cid && l.sid === scope.sid
      ? { cid: scope.cid, sid: newId } : l);
  }
  function verifiedTotal(scope: SceneKey): number | null {
    // Preserve the last verified read's scene semantics; optimistic arrays are
    // never a baseline. The captured campaign is checked at the read boundary.
    return totalSceneRef.current === scope.sid ? totalRef.current : null;
  }
  const followTail = () => { atBottomRef.current = true; };
  const windowToken = () => windowTokenRef.current;
  const invalidateTranscript = () => setLoaded(null);
  return {
    readScene, transcriptIsActive, loaded, clearScene, readTranscript, renameTranscript, verifiedTotal,
    followTail, windowToken, invalidateTranscript, showOptimistically, messages, firstIndex, hasUserPost,
    landedScene, goToQuote, openedFrom, streamRef, onStreamScroll, loadOlder, loadingOlder,
  };
}

export type TranscriptRead = (scope: SceneKey, options?: { stillWanted?: Current | undefined; renamed?: boolean }) => Promise<number>;
