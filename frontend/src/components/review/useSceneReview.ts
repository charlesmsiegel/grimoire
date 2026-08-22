// The end-of-scene review, whole: its state, the requests that fill and commit
// it, and the counts the column and the panel read off it.
//
// This is one *mode* of the campaign view rather than a widget inside it — you
// are not playing while you are reviewing — and it moved out of `CampaignView`
// as a hook rather than as props (#378) precisely because a component holding
// none of this and taking thirty props would have moved the lines without
// moving the problem. What is left in the parent is what genuinely straddles
// the two modes: the banner it reports failures through, the scene it absorbs,
// and the fact that a saved review invalidates every panel on the page.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  api, type EditConflict, type SceneAbsorb,
} from "../../api/client";
import {
  approvedByDefault, drawerKey, EDIT_GROUPS, editBand, isUncited, SCENE_STAMPED,
  type EditRow,
} from "./editRows";

export type SceneReview = ReturnType<typeof useSceneReview>;

/** How far a scene has moved since its stored review was prepared. */
type StaleReview = { prepared_posts: number; current_posts: number };

export function useSceneReview({ cid, activeId, rolling, fail, clearError, dismissError,
                                 onSaved }: {
  cid: string;
  /** The scene on the rail. A review is absorbed FROM it and then outlives it,
   *  which is what `absorbSid` is for. */
  activeId: string | null;
  /** A roll is resolving into the transcript this absorb would snapshot. */
  rolling: boolean;
  /** The page's shared error banner. `from` tags the operation, because the
   *  banner outlives a scene switch and this review's failures have to be
   *  separable from a chat error raised somewhere else. */
  fail: (e: any, retryable?: boolean, from?: string) => void;
  /** Clear the banner outright — what End scene does on the way in, because it
   *  is the page's own recovery from whatever was last on screen. */
  clearError: () => void;
  /** Drop a banner this review raised, by tag. Narrowed to the two tags this
   *  review actually raises, so the rule is the type rather than a promise in
   *  a comment: the banner is shared, and an untagged chat error — whose Retry
   *  generates another scene reply — is not this review's to take. */
  dismissError: (from: "audit" | "dossiers") => void;
  /** A committed review rewrote every file behind the page's panels. */
  onSaved: () => void;
}) {
  const [absorb, setAbsorb] = useState<SceneAbsorb | null>(null);
  // The scene this review was absorbed FROM. Switching scenes leaves the panel
  // open, so saving against the currently selected scene would commit scene A's
  // review onto scene B (#235).
  const [absorbSid, setAbsorbSid] = useState<string | null>(null);
  // Which stored review this panel is showing (#396). The review lives on disk
  // now, and Cancel is addressed to it by GENERATION rather than by run id: an
  // absorb and the retries of its phases all belong to one review, and the
  // stored payload names no producer of its own.
  const [generation, setGeneration] = useState<string | null>(null);
  // A review the transcript has moved out from under. Not an error and not a
  // review: the scene was played on after the absorb landed, so what is stored
  // summarises posts that are no longer there. Surfaced beside End scene so
  // the reviewer is told to re-run rather than shown a panel whose save is
  // going to be refused.
  const [staleReview, setStaleReview] = useState<StaleReview | null>(null);
  // Which review is open, readable AFTER an await. A scoped retry (audit or
  // dossiers) gets a budget of its own, so it can still be in flight minutes
  // later -- long enough for the reviewer to Discard, absorb another scene, and
  // be sitting in a *different* review when the answer lands. Applying it then
  // writes one scene's phase report and staged edits into another scene's
  // review, and that review's save commits them.
  //
  // `commit_token` rather than the `absorb` object: it is minted per absorb
  // (`<epoch>-<uuid4>`, so unique even across two absorbs of the same scene)
  // and survives the object being replaced, which typing in the one-line or
  // summary field does on every keystroke. Object identity would drop a
  // perfectly good answer the moment the reviewer edited the summary while
  // waiting.
  const openReviewRef = useRef<string | null>(null);
  useEffect(() => { openReviewRef.current = absorb?.commit_token ?? null; }, [absorb]);
  // Whether ANYTHING is open, readable synchronously inside the adoption pass
  // below. `openReviewRef` is not that question -- it is which review -- and
  // the adoption pass has to know before it installs a stored one, because a
  // review deliberately survives a scene switch and replacing it would throw
  // away whatever the reviewer has already judged.
  const hasReviewRef = useRef(false);
  useEffect(() => { hasReviewRef.current = absorb !== null; }, [absorb]);
  // What Discard and a replacing absorb have to name, readable after an await.
  const generationRef = useRef<string | null>(null);
  useEffect(() => { generationRef.current = generation; }, [generation]);
  // A Stop the reader pressed while the absorb was still running. The request
  // it stops is not this one's to reject -- it will come back `review_stale` or
  // `review_cancelled`, both of which are the answer the reader ASKED for, and
  // reporting either through the banner would be telling them their own Stop
  // failed. Bumped rather than a boolean, so a Stop belongs to one absorb: a
  // flag would still be set when the next End scene answered.
  const absorbStopRef = useRef(0);
  // Which absorb the READER stopped, as against which ones are merely
  // superseded. `absorbStopRef` answers "is this answer still wanted", and
  // three things move it: a Stop, a Cancel, and the campaign reset. Only the
  // first of those wants the run DELETED, and `endScene`'s `hold` -- the
  // precancel that fires when a Stop beat the 202 -- had no way to tell them
  // apart: leaving a campaign mid-absorb sent a Discard for the campaign just
  // left, unlinking the whole end-of-scene generation the reset exists to
  // preserve, silently, because the failure path is campaign-guarded.
  const stoppedGenRef = useRef<number | null>(null);
  // Resolves the placeholder hold below, once the Stop that had nothing to name
  // has either been sent or become impossible.
  const stopWaitRef = useRef<(() => void) | null>(null);
  // A Discard still on its way to the server. `DELETE .../pending-review`
  // answers only once the runs it flagged have really stopped, and until it
  // does they are still holding the scene's exclusion key -- so an End scene
  // issued in that window is refused with `run_in_flight` by a review the
  // reader has already dismissed. Held rather than awaited at the Cancel
  // itself, because closing the panel must stay instant: a retry runs on an
  // unbounded budget, and Cancel is the only way out of a request that may
  // never answer.
  const discardRef = useRef<Promise<unknown> | null>(null);
  // ...and which scene it is freeing, as STATE rather than a ref, because the
  // page renders on it: until that DELETE answers the runs it flagged are
  // still holding the scene, so a composer unlocked in the meantime offers a
  // send the server refuses.
  const [settlingSid, setSettlingSid] = useState<string | null>(null);
  // Which scene the stale notice above belongs to. Read rather than cleared on
  // a scene change: the adoption effect re-runs for the new scene, but nothing
  // in it clears a notice when that scene has neither a pending nor a live
  // review -- so scene A's "the transcript changed" sat beside B's End scene
  // claiming B had moved, which invites an absorb nobody needed.
  const [staleSid, setStaleSid] = useState<string | null>(null);

  function noteDiscard(p: Promise<unknown>, sid: string | null) {
    discardRef.current = p;
    setSettlingSid(sid);
    void p.catch(() => {}).then(() => {
      if (discardRef.current === p) {
        discardRef.current = null;
        setSettlingSid((s) => (s === sid ? null : s));
      }
    });
  }

  /** Raise or drop the stale-review notice, always with the scene it is about.
   *  One setter for the pair so they cannot come apart -- a notice whose sid is
   *  stale is a notice shown beside the wrong scene. */
  function setStale(stale: StaleReview | null, sid: string | null) {
    setStaleReview(stale);
    setStaleSid(stale ? sid : null);
  }

  /** Whether a review run is holding this scene against play.
   *
   *  One question, asked in one place, because there are four ways to be
   *  holding it and the page needs the answer rather than the enumeration: an
   *  absorb this browser started, one it merely found and is waiting out, a
   *  scoped retry, and a Discard whose DELETE has not yet answered. All four
   *  are `review` runs on the server, sharing one exclusion key with `turn` --
   *  so a send during any of them is refused with `run_in_flight`, and a
   *  rename or a roll with `scene_busy`.
   *
   *  Scoped to the scene the review belongs to. A review outlives a scene
   *  switch, so work still running for scene A must not lock scene B: the
   *  server would allow a turn there, and the reader is entitled to play it.
   */
  function holdsScene(sid: string | null): boolean {
    if (!sid) return false;
    if (settlingSid === sid) return true;
    const busy = absorbing || adopting || retryingAudit || retryingDossiers;
    return busy && (absorbSid ?? sid) === sid;
  }
  // …and which retry, within one review. `openReviewRef` cannot separate two
  // retries of the SAME review: both capture the same token, so both pass that
  // check whatever order they answer in, and a first request that returns
  // second overwrites the fresher generation the reviewer is already looking
  // at. `disabled` on the buttons is the visible half of the fix and this is
  // the load-bearing half — it does not rest on React having re-rendered the
  // button between two fast clicks.
  const auditRetryRef = useRef(0);
  const dossierRetryRef = useRef(0);
  // The in-flight request behind each latch. The generation above stops a stale
  // ANSWER from landing; this stops the WORK. They are not the same thing: the
  // endpoint runs one LLM call per present NPC on a fresh `absorb_budget`, and
  // `0` means that budget is unbounded, so a retry nobody is waiting for any
  // more goes on spending time and credits until it finishes on its own.
  const auditAbortRef = useRef<AbortController | null>(null);
  const dossierAbortRef = useRef<AbortController | null>(null);
  // The campaign as of the latest render, for continuations to check themselves
  // against. `cid` closed over inside an async function is the campaign that
  // STARTED it, which is exactly what makes it a usable comparison.
  const campaignRef = useRef(cid);
  campaignRef.current = cid;
  const [retryingAudit, setRetryingAudit] = useState(false);
  const [retryingDossiers, setRetryingDossiers] = useState(false);
  const [absorbing, setAbsorbing] = useState(false);
  // Waiting on a run this browser did not start -- the adoption pass below.
  // Its OWN flag rather than `absorbing`, because the two are released by
  // different things: this one is released by the effect's cleanup when the
  // scene changes under it, and folding them into one leaves "Ending…" latched
  // over every scene in the campaign when a reader switches away mid-wait.
  const [adopting, setAdopting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editRows, setEditRows] = useState<EditRow[]>([]);
  /** Which drawer of the review is open: a group key, "uncited", or
   *  "chronicle" (the scene summary itself). */
  const [reviewSection, setReviewSection] = useState("uncited");
  /** The quote of the row the reviewer picked, highlighted in the transcript
   *  pane beside it. */
  const [reviewQuote, setReviewQuote] = useState("");
  // Whether the collapsed low-confidence rows are showing (#110). Rows stay in
  // `editRows` at their original index either way: the conflict verdicts the
  // server sends back are bound to positions in the submitted batch, so the
  // routing is a rendering decision and never a reordering one.
  const [editFailures, setEditFailures] = useState<
    { id: string; reason: string; kind: "conflict" | "error"; label: string }[]>([]);
  // Rows the server refused because their target moved since the scene was
  // absorbed (#111), each already bound to its index in `editRows`. The save is
  // rejected whole and before anything is written, so this is a state the
  // review sits IN rather than a report of what landed: it clears a row at a
  // time as the reviewer keeps, replaces or merges.
  const [conflicts, setConflicts] =
    useState<{ row: number; conflict: EditConflict }[]>([]);
  // A failed SAVE gets its own surface, not the shared `error` banner: that
  // banner's Retry is wired to chat generation, so pointing a save failure at
  // it invites the user to generate another reply with the review still open.
  const [saveError, setSaveError] = useState<string | null>(null);

  // Every in-flight operation that rewrites the open review. `saveAbsorb`'s
  // conflict bookkeeping is built on "`saving` latches the panel for the whole
  // round-trip" -- it resolves the server's batch indices against `editRows`
  // as the array the batch was built from. A scoped retry outside that latch
  // makes the comment false: rebuild the rows mid-PUT and a clean save commits
  // the pre-retry batch (dropping what was just retried), while a refused one
  // binds its indices to rows that have since moved. So the three share one
  // latch rather than each holding its own.
  const reviewBusy = saving || retryingAudit || retryingDossiers;

  // Just the requests, no state. Split out because unmount needs exactly this
  // half: leaving the campaign section entirely (to Configuration, say) does not
  // re-run the `[cid]` effect, it destroys the component -- and SPA navigation
  // does not cancel a fetch, so without an unmount cleanup the retry keeps
  // running with nobody left to receive it and no disconnect for the server to
  // notice. Setting state from a cleanup on an unmounted component is the one
  // thing that must NOT happen here, hence the split.
  //
  // It abandons the POLL, not the run: a retry is a detached `review` run now
  // (#396), so the server goes on making it and goes on holding the scene's
  // exclusion key. That is deliberate rather than an oversight -- the only
  // thing that stops a review run is `discardReview`, which DELETES the
  // review, and destroying a reviewer's staged edits because they visited
  // Configuration would be a far worse answer than a retry finishing into the
  // record. Coming back to the scene re-adopts that record, and Cancel there
  // is the escape hatch for a retry that never lands.
  function abortRetries() {
    auditAbortRef.current?.abort();
    dossierAbortRef.current?.abort();
    auditAbortRef.current = dossierAbortRef.current = null;
  }

  // Closing or replacing a review abandons any retry still running for the old
  // one. Bumping the generations makes those answers land on a `!== gen` check
  // and be dropped -- which is also what stops their `finally` from clearing a
  // latch the NEW review now owns -- and clearing the latches here rather than
  // waiting for that `finally` is what keeps the new review's buttons live.
  // Waiting would disable them for as long as the abandoned request takes:
  // the whole absorb budget, or forever, since `absorb_budget = 0` means the
  // retry it gets is unbounded too.
  //
  // A scoped failure belongs to the review just as much as the latch does, so
  // it is dropped here too. Left standing, the banner outlives the review it
  // reports on -- Cancel, a successful save or a campaign switch all leave
  // "the dossier retry failed" on screen for a review that no longer exists,
  // and the cid effect below carries it into the NEXT campaign. Only the two
  // tags this panel raises are cleared: the banner is shared, and an untagged
  // chat error (with its generate-a-reply Retry) is not this review's to take.
  function releaseRetries() {
    auditRetryRef.current++;
    dossierRetryRef.current++;
    abortRetries();
    setRetryingAudit(false);
    setRetryingDossiers(false);
    dismissError("audit");
    dismissError("dossiers");
  }

  useEffect(() => {
    // A review is campaign-scoped state. The route has no `key`, so React Router
    // reuses this component for campaign A -> B (browser Back between two
    // campaigns does it), leaving `absorb`/`absorbSid` pointing at A while `cid`
    // is B -- and every request they drive, the scoped retries and the SAVE
    // alike, would then be posted to B. Scene ids repeat across campaigns, so
    // those requests succeed rather than 404.
    releaseRetries();
    // ...and the ABSORB, which `releaseRetries` does not cover. A campaign
    // switch mid-absorb left `absorbing` set, and `endScene` refuses every call
    // while it is -- so B could not end any scene until A's poll settled, which
    // for an unbounded or wedged absorb is never. Bumped as well as cleared, so
    // the old continuation is inert in both directions: its guarded `finally`
    // cannot take "Ending…" off a fresh absorb here, and coming BACK to A does
    // not let its answer install into a mount that has since been reset.
    // The run itself is left alone deliberately -- it is still preparing A's
    // review and that review is still wanted, on disk, where A's next mount
    // adopts it.
    absorbStopRef.current++;
    setAbsorbing(false);
    setAdopting(false);
    setAbsorb(null);
    setAbsorbSid(null);
    setGeneration(null);
    setStaleReview(null);
    setStaleSid(null);
    // Scene ids repeat across campaigns -- a fork has the same ones by
    // construction -- and `holdsScene` compares them bare, so a Discard still
    // in flight for campaign A would lock the same-id scene in B for as long
    // as the DELETE takes to answer. The request is left to finish; only this
    // page's belief that it is holding something is dropped.
    setSettlingSid(null);
    // ...including the promise `endScene` waits on before it posts. Left here,
    // End scene in the NEW campaign blocks on the old one's DELETE -- which
    // itself waits up to the server's cancellation timeout per flagged run --
    // and never posts at all if that request hangs.
    discardRef.current = null;
    releaseStopWait();
    setEditRows([]);
    setConflicts([]);
    setSaveError(null);
    // Leaving the campaign section entirely unmounts instead of re-running this,
    // so the release above never happens on that path — abort here or the retry
    // outlives the screen that could use it.
    return abortRetries;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid]);

  /** Install a review that came off the store, wherever it came from. */
  function openReview(review: SceneAbsorb, sid: string, gen: string | null) {
    setAbsorb(review);
    setAbsorbSid(sid);
    setGeneration(gen);
    setStale(null, null);
    setEditRows(review.edits.map((e) => ({ ...e, approved: approvedByDefault(e) })));
    // A fresh review opens on whichever drawer needs a person, which
    // `openSection` works out — but the *stored* choice has to be reset, or
    // the drawer the last review left open is the one this one lands in.
    setReviewSection("uncited");
    setReviewQuote("");
  }

  // Adopt whatever this scene already has (#396). The review is durable now, so
  // three states are reachable on a fresh mount that were not before: one
  // waiting to be saved, one the transcript has moved out from under, and one
  // still being generated by a run this browser never started -- which is
  // exactly what a locked phone leaves behind, and the case the whole feature
  // exists for.
  //
  // ONLY when nothing is open. A review deliberately outlives a scene switch,
  // so adopting on every scene change would replace the one on screen with the
  // one belonging to whatever the reader just clicked -- throwing away every
  // proposal they had judged.
  useEffect(() => {
    if (!activeId) return;
    const sid = activeId;
    let dropped = false;
    void (async () => {
      if (hasReviewRef.current) return;
      let pending;
      try {
        pending = await api.pendingReview(cid, sid);
      } catch {
        // A failed request is not an answer of "no review" -- the next mount
        // asks again. Reading it as "none" would leave a durable review
        // invisible with End scene the only way back to it, which re-runs the
        // whole absorb over a review that is sitting on disk.
        return;
      }
      if (dropped || campaignRef.current !== cid || hasReviewRef.current) return;
      if (pending.review) {
        openReview(pending.review, sid, pending.generation);
        return;
      }
      if (pending.stale) {
        // A review that is there and unusable, and nothing more is recorded
        // about it. Holding its generation would be state nothing reads: there
        // is no panel here, so no Cancel to name it with, and End scene does
        // not delete on the way in -- a fresh absorb replaces the record. What
        // the reader needs is the notice, which is what this is.
        setStale(pending.stale, sid);
        return;
      }
      let live;
      try {
        live = await api.liveReview(cid, sid);
      } catch {
        return;
      }
      if (dropped || campaignRef.current !== cid || hasReviewRef.current) return;
      if (!live) {
        // "No live run" is not "no review". The absorb can LAND between the two
        // reads above -- the store said nothing was ready, and by the time this
        // asked, the run had finished and `liveReview` filtered it out for
        // being terminal. Returning there left a completed review invisible for
        // as long as the scene stayed selected (this effect is keyed on it),
        // with End scene enabled to run the whole absorb again and replace it.
        // One more read closes the window rather than narrowing it: whatever
        // the run did, the store now has the answer.
        const settled = await api.pendingReview(cid, sid).catch(() => null);
        if (dropped || campaignRef.current !== cid || hasReviewRef.current) return;
        if (settled?.review) openReview(settled.review, sid, settled.generation);
        else if (settled?.stale) setStale(settled.stale, sid);
        return;
      }
      // Still generating. Show the panel as busy and wait it out -- the run is
      // the server's, and this client is a subscriber that can come and go.
      // Captured, not bumped: this wait does not supersede anything, it just
      // has to notice being superseded. `stopAbsorb` moves the counter, and
      // the run it stopped then answers `review_cancelled` -- which is the
      // reader's own request coming back, not a failure to report at them.
      // `endScene` guards its two exits for exactly this reason; this one is
      // reachable the same way, from the Stop offered beside "Ending…".
      const stopGen = absorbStopRef.current;
      setAdopting(true);
      setAbsorbSid(sid);
      setGeneration(live.review_generation ?? null);
      try {
        await api.awaitRun(cid, sid, live);
        const landed = await api.pendingReview(cid, sid);
        if (dropped || campaignRef.current !== cid || hasReviewRef.current
            || absorbStopRef.current !== stopGen) return;
        if (landed.review) openReview(landed.review, sid, landed.generation);
        else setStale(landed.stale, sid);
      } catch (err: unknown) {
        if (dropped || campaignRef.current !== cid
            || absorbStopRef.current !== stopGen) return;
        // THE STORE IS THE ANSWER, not the run record -- `api.absorbScene`
        // documents why and this path needs it more, not less: it is the
        // locked-phone case itself, so the wait it is recovering from is the
        // one most likely to have outlived the run's retention window. Without
        // this the reader is told "no such run" over a scene whose finished
        // review is sitting in its sidecar.
        const landed = await api.pendingReview(cid, sid).catch(() => null);
        if (dropped || campaignRef.current !== cid || hasReviewRef.current
            || absorbStopRef.current !== stopGen) return;
        if (landed?.review) {
          openReview(landed.review, sid, landed.generation);
          return;
        }
        // `false`, for the scoped retries' reason: the banner's Retry runs the
        // CHAT retry, so offering it here would answer a failed absorb by
        // generating one more reply into the scene being finished.
        fail(err, false);
      } finally {
        // Only for a wait that is still THIS effect's. A wait that has been
        // dropped can settle long after the next scene's adoption has set the
        // flag for itself, and clearing it there would take "Ending…" off a
        // scene that is still being absorbed. The dropped case is released by
        // the cleanup below instead, which runs before the next effect body
        // and so cannot clobber it.
        if (!dropped) setAdopting(false);
      }
    })();
    return () => { dropped = true; setAdopting(false); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, activeId]);

  /** Take the review. */
  async function endScene() {
    // `rolling` too, not only `sceneLocked`. Absorb takes its transcript
    // snapshot once, so a swap committing after it means the review summarises
    // the take the reader replaced — and saving that review marks the *swapped*
    // transcript absorbed, with staged edits derived from narration it never
    // read. The same reasoning as the rule above, for the other latch.
    if (!activeId || absorbing || rolling) return;
    // Release the outgoing review's retries BEFORE issuing the absorb that will
    // replace it, not after. A retry still running here is answering a review
    // this call is about to discard, so leaving it up meant two expensive
    // pipelines against the same scene at once -- duplicate dossier calls, and
    // with `absorb_budget = 0` neither one bounded.
    //
    // Released rather than blocked: adding `reviewBusy` to this button's
    // disabled condition would close the escape hatch. A wedged retry on an
    // unbounded budget is exactly when the reader needs End scene, which is the
    // same reason Cancel stays live during a retry.
    releaseRetries();
    const stopGen = ++absorbStopRef.current;
    setAbsorbing(true);
    // The scene being absorbed, recorded BEFORE the request rather than when it
    // answers. `absorbSid` is what scopes the scene lock, and it can be holding
    // some earlier review's scene -- a stale record adopted on another scene,
    // say -- so leaving it until the review lands unlocks the composer for the
    // whole of an absorb, which is the one window it exists to cover.
    setAbsorbSid(activeId);
    clearError();
    setStale(null, null);
    setEditFailures([]);
    setConflicts([]);
    try {
      // Nothing is deleted on the way in, deliberately. A fresh absorb
      // REPLACES whatever is stored for this scene
      // (`pending_reviews.publish`), so a delete first would buy nothing --
      // and it would cost something real: the re-absorb below asks for
      // confirmation, and a reader who declines would have lost the review
      // they were looking at to a question they answered "no" to.
      // A Discard still settling holds this scene's exclusion key until the
      // server has really stopped what it flagged. Waited on here rather than
      // at the Cancel that sent it, so closing a panel stays instant and only
      // the one operation that actually needs the scene free pays for it.
      const settling = discardRef.current;
      if (settling) {
        await settling.catch(() => {});
        if (campaignRef.current !== cid || absorbStopRef.current !== stopGen) return;
      }
      // The generation, minutes before the review. It is what `stopAbsorb`
      // names, and without it a reader watching "Ending…" has no way to stop a
      // run that is holding their scene against play.
      // A Stop can land BEFORE the POST answers: `setAbsorbing(true)` is
      // synchronous, so the button renders while the request is still doing
      // its campaign-lock snapshot and building the prompt. `stopAbsorb` has
      // no generation to name then and sends nothing -- so without this the
      // absorb runs to completion holding the scene's exclusion key, with no
      // Stop button left (absorbing is false) and End scene answering
      // `run_in_flight`: a scene that is neither playable nor stoppable.
      //
      // So the Stop is honoured HERE instead, the moment the review has a name
      // -- the same precancel the backend keeps for a turn stopped before its
      // run was reserved (`runs.cancel_or_precancel`).
      const hold = (g: string) => {
        if (absorbStopRef.current === stopGen) {
          setGeneration(g);
          return;
        }
        // Superseded, but by what? A campaign switch and a second End scene
        // both move the counter and neither wants this run's review deleted --
        // it is still being prepared and still wanted, on disk, where the next
        // mount adopts it. Only the Stop that named THIS absorb does.
        if (stoppedGenRef.current !== stopGen) return;
        // Reported, not swallowed. `stopAbsorb` had no generation to name when
        // the reader pressed Stop, so this is the ONLY cancellation this Stop
        // ever gets: a network or busy failure here leaves an unbounded run
        // holding the scene with nothing left to stop it, and the reader
        // believing they stopped it. Same reasoning `stopAbsorb` carries for
        // its own DELETE, one step earlier.
        const stopping = api.discardReview(cid, activeId, g);
        // Installed BEFORE the placeholder is released, so the hold passes from
        // one to the other without a gap for a second End scene to slip into.
        noteDiscard(stopping.catch(() => {}), activeId);
        releaseStopWait();
        void stopping.catch((err: unknown) => {
          if (campaignRef.current === cid) fail(err, false);
        });
      };
      let a;
      try {
        a = await api.absorbScene(cid, activeId, false, hold);
      } catch (err: any) {
        if (err?.kind !== "already_absorbed") throw err;
        if (!window.confirm(
          "This scene has already been absorbed. Absorbing again re-proposes every " +
          "change from scratch, so appended lore and plot beats can end up duplicated. " +
          "Absorb it again?")) return;
        a = await api.absorbScene(cid, activeId, true, hold);
      }
      // The review belongs to the campaign that asked for it. An absorb is the
      // slowest request in the app -- several LLM calls -- so there is ample
      // room to switch campaigns while it runs, and the `[cid]` effect that
      // clears review state cannot touch a request already in flight. Installing
      // this would put A's summary, timeline and staged edits in front of B,
      // where Save posts them to B: scene ids repeat across campaigns and a
      // fresh commit token matches, so nothing downstream would refuse them.
      if (campaignRef.current !== cid) return;
      // A Stop that landed while this was in flight, on the SUCCESS path as
      // well as the failure one. The DELETE it sent removes the record, so a
      // panel opened here would be showing a review that is not stored -- and
      // saving it would go through, undoing the Stop the reader pressed.
      if (absorbStopRef.current !== stopGen) return;
      // ...and not over a review that is already open. Leaving the scene and
      // coming back while this generates starts the adoption pass on the SAME
      // run, and the two waiters poll on independently-phased cadences: the
      // adoption pass can win, open the panel, and have the reader approving
      // rows for seconds before this answer arrives and rebuilds every one of
      // them with `approvedByDefault`.
      if (hasReviewRef.current) return;
      openReview(a.review, activeId, a.generation);
    } catch (err: any) {
      // Same guard on the failure path: A's banner over B is the same category
      // of wrong answer, just a cheaper one.
      if (campaignRef.current !== cid) return;
      // ...and a Stop the reader pressed is not a failure to report. The
      // request comes back refused either way -- the record it was going to
      // write is gone -- and a banner there tells them their own Stop broke
      // something.
      if (absorbStopRef.current !== stopGen) return;
      // `false`, for the scoped retries' reason: the banner's Retry runs the
      // CHAT retry, so it would answer a failed absorb by generating one more
      // reply into the scene the user was trying to finish. End scene is its
      // own recovery, and it is still right there.
      fail(err, false);
    } finally {
      // Only the newest absorb owns the latch. A superseded one -- stopped, or
      // replaced by a second End scene -- can still be polling on its 1-5s
      // cadence, and clearing the flag when it finally answers would take
      // "Ending…" and the Stop button off a run that is still going, unlock
      // the composer over a scene the server refuses, and leave an unbounded
      // absorb with nothing left to stop it. Every other latch in this file
      // guards its `finally` the same way.
      if (absorbStopRef.current === stopGen) setAbsorbing(false);
      // Whatever happened, this absorb will not be naming a review any more, so
      // a Stop still waiting on one is waiting on nothing. The precancel runs
      // on the way IN to this block, so by here the ordinary case has already
      // handed the hold over and this covers only the paths where no name ever
      // arrived.
      releaseStopWait();
    }
  }

  /** Let go of the hold a Stop with nothing to name is keeping. */
  function releaseStopWait() {
    stopWaitRef.current?.();
    stopWaitRef.current = null;
  }

  // Commit is replayable server-side and plot movements append a beat per apply,
  // so a second PUT of the same review duplicates them (#235) -- the `saving`
  // latch is what keeps a double-click from being a double-commit. A failed save
  // leaves the review standing so it can be retried rather than silently lost.
  async function saveAbsorb() {
    const sid = absorbSid ?? activeId;
    if (!absorb || !sid || saving) return;
    setSaving(true);
    setSaveError(null);
    // captured before editRows is cleared below -- failures only carry
    // id/reason/kind, so the row's label has to come from what was on screen.
    const labels = new Map(editRows.map((e) => [e.id, e.label]));
    try {
      const res = await api.saveChronicle(cid, sid, {
        one_line: absorb.one_line, summary: absorb.summary, keywords: absorb.keywords,
        timeline_events: absorb.timeline_events,
        edits: editRows.filter((e) => e.approved).map(({ approved, ...e }) => e),
        // Same token on every attempt, so the retry below cannot commit twice
        // when the first PUT landed and only its response was lost (#235).
        commit_token: absorb.commit_token });
      setEditFailures(res.failures.map((f) => ({ ...f, label: labels.get(f.id) ?? f.id })));
      releaseRetries();
      setAbsorb(null);
      setAbsorbSid(null);
      // The server cleared the record as part of the commit, so this is the
      // panel catching up rather than a second delete.
      setGeneration(null);
      setEditRows([]);
      setConflicts([]);
      onSaved();
    } catch (err: any) {
      // A contradiction is not a failed save (#111): the server refused the
      // batch before writing anything, so the review stands exactly as it was
      // and the same commit token is still good. Show the rows, let the
      // reviewer answer each one, and save again -- no `saveError`, whose
      // "Try saving again" would just re-post the batch that was refused.
      if (err?.kind === "edit_conflicts") {
        // Resolve each verdict to the ROW it belongs to, here and now, while
        // `editRows` is still the exact array this batch was built from
        // (`saving` latches the panel for the whole round-trip). The server
        // stamps a batch index; `approvedIdx` is that batch's row numbers, so
        // the two line up positionally even when the response has dropped the
        // unconflicted rows in between. Storing row numbers rather than the
        // raw verdicts also survives what comes next: unapproving a row is the
        // keep answer, and it would shift every batch index after it.
        const approvedIdx = editRows.flatMap((r, i) => (r.approved ? [i] : []));
        const rows = ((err.body?.conflicts ?? []) as EditConflict[])
          .map((c) => ({ row: approvedIdx[c.index] ?? -1, conflict: c }))
          .filter((p) => p.row >= 0);
        setConflicts(rows);
        // A refusal on a collapsed row has to be answerable, and the save is
        // refused whole -- so leaving the section shut would leave the panel
        // insisting something is unanswered with nothing on screen to answer.
        // Latched here rather than derived from `conflicts`: a derived flag
        // goes false the instant the reviewer clicks Keep stored (which
        // unapproves the row and drops its verdict), collapsing the section
        // and the row they are looking at out from under them.
        // A conflict on a withheld row: open the drawer holding it, or the panel
        // insists something is unanswered with nothing on screen to answer.
        const stuck = rows.find(({ row }) => editRows[row] && drawerKey(editRows[row]) !== "uncited"
                                             && editBand(editRows[row]) === "low");
        if (stuck) setReviewSection("low");
        setSaveError(null);
        return;
      }
      setSaveError(err.detail ?? String(err));
    } finally {
      setSaving(false);
    }
  }

  /** Stop an absorb that is still running, before there is a review to discard.
   *
   *  The escape hatch a detached review needs and a synchronous one did not: a
   *  `review` holds the scene's exclusion key for as long as it runs, so an
   *  absorb on an unbounded budget can lock a scene against play indefinitely
   *  -- and until it lands there is no panel, and so no Cancel. The same
   *  reasoning the Cancel button carries for a wedged retry, one step earlier.
   */
  async function stopAbsorb() {
    const sid = absorbSid ?? activeId;
    const gen = generationRef.current;
    // Recorded BEFORE the bump, so it names the absorb being stopped. This is
    // the reader's statement, and the only one that authorises a DELETE.
    stoppedGenRef.current = absorbStopRef.current;
    absorbStopRef.current++;          // this absorb's answer is no longer wanted
    // BOTH flags. An absorb this browser started sets `absorbing`; one it
    // merely found and waited out sets `adopting`, and Stop is offered for
    // either -- so clearing only the first leaves "Ending…" on screen over a
    // run that has just been told to stop.
    setAbsorbing(false);
    setAdopting(false);
    setGeneration(null);
    if (!gen) {
      // Stopped before the 202 named the review, so there is nothing to send
      // yet -- `endScene`'s precancel sends it the moment there is. The scene
      // is still HELD in the meantime, and saying otherwise is what let a
      // second End scene in this window skip the wait `endScene` takes for
      // exactly this and be refused `run_in_flight` instead. Held on a
      // placeholder the precancel resolves, and that `endScene`'s `finally`
      // resolves too, so an absorb that never answers cannot leave the scene
      // locked for the rest of the session.
      if (sid) noteDiscard(new Promise<void>((r) => { stopWaitRef.current = r; }), sid);
      return;
    }
    if (!sid) return;
    const stopping = api.discardReview(cid, sid, gen);
    noteDiscard(stopping, sid);
    try {
      await stopping;
    } catch (err: unknown) {
      // Reported, unlike the panel's Cancel: there the record is refused at
      // save anyway, but here the reader is being told a scene is free that
      // may still be held -- and the next thing they do is try to play in it.
      if (campaignRef.current === cid) fail(err, false);
    }
  }

  /** Throw the review away. Not disabled by `reviewBusy`: a retry runs on the
   *  absorb budget, which is unbounded at 0, so this is the only way out of a
   *  request that may never answer. Safe because `releaseRetries` invalidates
   *  that request on the way out. */
  function discard() {
    releaseRetries();
    // Cancel is also a Stop, and has to say so in the one place the rest of
    // this file reads. `endScene` and the adoption pass are both still capable
    // of answering here: the adoption pass runs on the SAME run when the
    // reader leaves the scene and comes back mid-absorb, and it can win and
    // put the panel up while `endScene` is still polling. Without this bump
    // that answer arrives after the DELETE, finds no review open, and installs
    // the one the reader just threw away -- back on screen, and savable,
    // because a record the DELETE removed no longer has a watermark to refuse
    // it with. Same statement `stopAbsorb` makes, for the same reason.
    absorbStopRef.current++;
    // ...and the latches those two would have cleared in a `finally` that is
    // now guarded against them. Left set, "Ending…" outlives the review it was
    // announcing and End scene never comes back.
    setAbsorbing(false);
    setAdopting(false);
    // The review is on disk (#396), so throwing it away is a request and not a
    // `setState`. The panel closes now either way -- the reviewer asked for it
    // -- but the request is NOT fire-and-forget: a DELETE that fails leaves the
    // record on disk with a watermark that still matches (nothing was played,
    // the reader was reading), so the next mount adopts and re-opens the review
    // they dismissed, and the run it was going to stop keeps holding the scene
    // against play. That is the same thing `stopAbsorb` reports for, and the
    // reader is owed it here for the same reason: the next thing they do is try
    // to play in a scene they were told was free.
    const sid = absorbSid ?? activeId;
    const gen = generationRef.current;
    if (sid && gen) {
      const stopping = api.discardReview(cid, sid, gen);
      noteDiscard(stopping.catch(() => {}), sid);
      void stopping.catch((err: unknown) => {
        if (campaignRef.current === cid) fail(err, false);
      });
    }
    setAbsorb(null);
    setAbsorbSid(null);
    setGeneration(null);
    setStale(null, null);
    setEditRows([]);
    setEditFailures([]);
    setSaveError(null);
    setConflicts([]);
    setReviewQuote("");
  }

  /** Retype the chronicle the absorb wrote. The only public way to change
   *  `absorb`, deliberately: a raw setter would also let a caller clear the
   *  review, and clearing it without `releaseRetries` is the bug the whole
   *  generation/abort apparatus above exists to prevent. Closing a review is
   *  `discard`, `saveAbsorb` or the `[cid]` effect, all three of which release. */
  function editChronicle(patch: Partial<Pick<SceneAbsorb, "one_line" | "summary">>) {
    setAbsorb((a) => (a ? { ...a, ...patch } : a));
  }

  /** Contradictions by staged-edit id (#78). Empty for the ordinary end-of-scene
   *  absorb, which has no later scene to disagree with. */
  const contradictionById = useMemo(
    () => new Map((absorb?.contradictions ?? []).map((c) => [c.id, c])),
    [absorb]);

  // Conflicts still showing, keyed by their row. Already bound to a row when
  // the refusal arrived; all that is left is to drop the ones whose row has
  // since been unapproved, which IS the keep answer.
  const conflictByRow = useMemo(() => {
    const out = new Map<number, EditConflict>();
    for (const { row, conflict } of conflicts) {
      if (editRows[row]?.approved) out.set(row, conflict);
    }
    return out;
  }, [conflicts, editRows]);

  /** Set one row's verdict. Exclusive: approving clears a rejection and vice
   *  versa, so a row can never be counted in two columns at once. */
  function decide(i: number, verdict: "approved" | "rejected" | "undecided") {
    setEditRows((rows) => rows.map((r, j) => (j === i ? {
      ...r,
      approved: verdict === "approved",
      rejected: verdict === "rejected",
      // Only a verdict the reviewer *gave* folds the row away. Rows arrive
      // pre-approved by band (`approvedByDefault`), and folding those would
      // hide the bulk of a good absorb behind an Undo apiece — the collapse is
      // there to clear what you have finished with, not to hide what you have
      // not started.
      judged: verdict !== "undecided",
    } : r)));
  }

  /** Edit one row's text in place. */
  function editRow(i: number, patch: Partial<EditRow>) {
    setEditRows((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }

  /** Merge one row's payload — the extra fields a new-record proposal carries. */
  function editPayload(i: number, patch: Record<string, unknown>) {
    setEditRows((rows) => rows.map((r, j) =>
      (j === i ? { ...r, payload: { ...r.payload, ...patch } } : r)));
  }

  // The reviewer's answer to one conflict. **keep** is not here: it unapproves
  // the row, which drops it from the batch entirely -- the stored value wins by
  // the edit never being sent. `replace` keeps the staged text, `merge` swaps in
  // the draft the server prefilled from both sides for the reviewer to trim.
  //
  // `resolve_from` rides along because the flag alone is not standing
  // permission: it records WHICH value was on screen when they answered, so a
  // save that lands after the record has moved again is refused instead of
  // overwriting something nobody saw.
  function resolveConflict(i: number, conflict: EditConflict,
                           resolve: "replace" | "merge", after?: string) {
    setEditRows((rows) => rows.map((r, j) => (j === i
      ? { ...r, resolve, resolve_from: conflict.stored,
          ...(after === undefined ? {} : { after }) }
      : r)));
    // By row, not by edit id — the duplicate-id case: a sibling row sharing
    // this one's id keeps its own conflict and its own unanswered badge.
    setConflicts((cs) => cs.filter((c) => c.row !== i));
  }

  /** Approve every proposal the transcript backs, and leave the ones it does
   *  not. The whole routing argument in one button: a cited row is one the
   *  reviewer can check *later* if they want to; an uncited one is the only
   *  kind they cannot, so it is the only kind this refuses to answer for. */
  function approveAllCited() {
    setEditRows((rows) => rows.map((r) =>
      (isUncited(r) ? r : { ...r, approved: true, rejected: false })));
  }

  // Replaces absorb.mechanics with a fresh audit and swaps in its sheet
  // proposals, leaving every other staged edit (prose/relationship/etc.)
  // exactly as the reviewer had it.
  async function retryAudit() {
    // `absorbSid`, not `activeId` — the same reason saveAbsorb uses it. A review
    // survives a scene switch (only Discard or a successful save clears it), so
    // reading the rail would audit whatever the user has since opened and write
    // that scene's verdict, sheet edits and phase row into this review.
    const sid = absorbSid ?? activeId;
    if (!sid) return;
    const review = absorb?.commit_token ?? null;
    const gen = ++auditRetryRef.current;
    // Clear THIS retry's own previous failure on the way in -- otherwise it
    // outlives the attempt that fixed it, and a recovery reads as a second
    // failure. Scoped by `from`, because the banner is shared with
    // operations that have nothing to do with this review.
    dismissError("audit");
    const ctl = new AbortController();
    auditAbortRef.current = ctl;
    setRetryingAudit(true);
    try {
      const res = await api.retryAudit(cid, sid, ctl.signal);
      // Superseded by a later click on the same review — see `auditRetryRef`.
      if (auditRetryRef.current !== gen) return;
      // The review this answer was asked for is gone (discarded, or saved and
      // replaced by another absorb) -- see `openReviewRef`. Dropping it is the
      // whole fix: `setAbsorb`'s own null-check passes once a NEW review is
      // open, so "is anything open" is not the question.
      if (openReviewRef.current !== review) return;
      // The audit phase row is a projection of `mechanics` (backend:
      // _phase_report), so it has to move with it — otherwise the panel keeps
      // reporting a budget that ran out for a step this retry has since run.
      setAbsorb((a) => (a ? { ...a, mechanics: res.mechanics,
        phases: a.phases.map((p) => (p.name === "audit"
          ? { ...p, status: res.mechanics.status, reason: res.mechanics.reason,
              attempted: res.mechanics.attempted,
              budget_exhausted: res.mechanics.budget_exhausted }
          : p)) } : a));
      // A retry that FAILED replaces nothing. `_run_audit` reports a failure as
      // a status rather than raising, so a wedged call, a budget refusal and an
      // unparseable verdict all arrive here as `edits: []` — and dropping the
      // sheet rows for that deletes every proposal the absorb made and puts
      // nothing in their place, recoverable only by re-running the whole
      // absorb. `retryDossiers` has always been careful about this for its own
      // rows; this is the same loss. The stored fold agrees
      // (`pending_reviews.merge_audit`), so a reload shows what the screen did.
      //
      // An audit that RAN and found nothing does replace them: "nothing is
      // wrong with the sheets" is this run's answer, and the reviewer is
      // entitled to see it rather than the last run's proposals.
      const said = res.mechanics.status !== "failed" && res.mechanics.status !== "skipped";
      if (said) {
        setEditRows((rows) => [
          ...rows.filter((r) => r.kind !== "sheet"),
          ...res.edits.map((e) => ({ ...e, approved: approvedByDefault(e) })),
        ]);
        // Conflicts are bound to row numbers, and this rebuilds the array — so
        // any that survived a refusal would now point at whichever row inherited
        // their index. Sheet edits never conflict, so there is nothing to carry
        // over; the next save re-reports whatever is still drifted.
        setConflicts([]);
      }
    } catch (err: any) {
      // The same two guards the success path takes, for the same reason: an
      // answer -- failure included -- that belongs to a superseded retry or a
      // review that is gone must not reach the screen. Cancel stays enabled
      // during a retry by design, so a request abandoned that way and rejecting
      // later would otherwise drop its banner over a replacement review.
      if (auditRetryRef.current !== gen || openReviewRef.current !== review) return;
      // `false`: the banner's Retry runs the CHAT retry, which generates
      // another scene reply. Offering it for a failed audit would extend the
      // very scene whose end-of-scene review is open, and still not re-run the
      // audit. The scoped Retry button in the notice is the recovery.
      fail(err, false, "audit");
    } finally {
      // Only the newest retry owns the latch: an older one clearing it would
      // re-enable the button while its successor is still in flight.
      if (auditRetryRef.current === gen) setRetryingAudit(false);
    }
  }

  // The dossier phase's sibling to retryAudit (#286). Replaces `absorb.dossiers`
  // with a fresh run of that phase and swaps in its staged dossiers, leaving
  // every other staged edit (prose/relationship/sheet/…) exactly as the reviewer
  // had it.
  //
  // The backend re-runs every present NPC, but only the ones it actually
  // re-proposed are swapped here: a retry answers for those and says nothing
  // about the rest. It reports per-NPC failures inside a 200, so an
  // unconditional rebuild would let a retry that failed for Mara delete Mara's
  // perfectly good proposal from the first pass and put nothing in its place —
  // turning "retry the one we missed" into a net loss. An NPC the retry did
  // prepare is replaced, including over a row the reviewer had retyped: that is
  // the fresh proposal they asked for.
  async function retryDossiers() {
    // `absorbSid`, not `activeId` — retryAudit's reason, verbatim: a review
    // survives a scene switch, so reading the rail would build dossiers from
    // whatever the user has since opened and stage them into this review.
    const sid = absorbSid ?? activeId;
    if (!sid) return;
    const review = absorb?.commit_token ?? null;
    const gen = ++dossierRetryRef.current;
    // Clear THIS retry's own previous failure on the way in -- otherwise it
    // outlives the attempt that fixed it, and a recovery reads as a second
    // failure. Scoped by `from`, because the banner is shared with
    // operations that have nothing to do with this review.
    dismissError("dossiers");
    const ctl = new AbortController();
    dossierAbortRef.current = ctl;
    setRetryingDossiers(true);
    try {
      const res = await api.retryDossiers(cid, sid, ctl.signal);
      // Both guards, in the order they can fail: superseded by a later retry of
      // THIS review, then belonging to a review that is no longer open. The
      // token cannot do the first job -- two retries of one review carry the
      // same token -- so a first request answering second would otherwise
      // overwrite the fresher generation on screen. retryAudit's reasons.
      if (dossierRetryRef.current !== gen) return;
      if (openReviewRef.current !== review) return;
      // The dossiers phase row is a projection of `dossiers` (backend:
      // _phase_report), so it has to move with it — otherwise the panel keeps
      // reporting a budget that ran out for a step this retry has since run.
      setAbsorb((a) => (a ? { ...a, dossiers: res.dossiers,
        phases: a.phases.map((p) => (p.name === "dossiers"
          ? { ...p, status: res.dossiers.status, reason: res.dossiers.reason,
              attempted: res.dossiers.attempted,
              budget_exhausted: res.dossiers.budget_exhausted }
          : p)) } : a));
      setEditRows((rows) => {
        // `proposed` is the phase's own list of NPCs it prepared a dossier for
        // — the same list its status is computed from, so this cannot drift
        // from what the notice above says. It includes an NPC whose paragraph
        // came back unchanged, which carries no edit: dropping that row is
        // right, because "unchanged" is this run's answer for them.
        const reproposed = new Set(res.dossiers.proposed);
        return [
          ...rows.filter((r) => r.kind !== "dossier" || !reproposed.has(r.target.id)),
          ...res.edits.map((e) => ({ ...e, approved: true })),
        ];
      });
      // Rebuilding the array invalidates row-bound conflicts — retryAudit's
      // reason. Answered ones already live on the row (`resolve`/`resolve_from`)
      // and are untouched; the unanswered badges dropped here come back on the
      // next save, which re-checks every edit against what is stored.
      setConflicts([]);
    } catch (err: any) {
      if (dossierRetryRef.current !== gen || openReviewRef.current !== review) return;
      fail(err, false, "dossiers");   // retryAudit's reasons, both of them
    } finally {
      if (dossierRetryRef.current === gen) setRetryingDossiers(false);
    }
  }

  /** The review's half of `adoptSceneId`. A scene's id is its filename, so a
   *  rename mints a new one, and `scene_refs.repoint` carries every *persisted*
   *  reference across — but three of this review's live only in this browser,
   *  where no server-side repointer can see them:
   *
   *  - `absorbSid`, the id an open review's save and audit retry POST;
   *  - `payload.scene` on each staged plot or commitment edit, which
   *    absorb.materialize embedded and apply_edits passes straight to
   *    plot.set_movement / commitments.set_movement / facts.record — so a save
   *    after a rename would file the movement under a scene that is gone. All
   *    three kinds, because each stamps its record with the scene it came from
   *    (#115, #114). A fact row needs nothing beyond its payload: its staged
   *    `before` is a `conflicts.fact_line`, which carries no scene id at all —
   *    deliberately, so that the whole class of staleness the commitment
   *    fingerprint forces on this function cannot arise for facts;
   *  - the staged CONFLICT BASIS of a commitment row. `conflicts.commitment_line`
   *    ends `[N beats, last moved in <scene>]`, and `scene_refs.repoint` rewrites
   *    that scene id in the stored record — so a row left holding the old id no
   *    longer matches what the store says and saves as a spurious conflict, on a
   *    commitment nobody touched. `resolve_from` gets the same treatment: it is
   *    the value the reviewer was shown, and it is compared the same way.
   *
   *  The page's other id-keyed state — the reroll alternates, the loaded-window
   *  token, a parked prompt — is the caller's, in `adoptSceneId`. */
  function sceneRenamed(oldId: string, newId: string) {
    // Anchored to the END of the line, so a beat that happens to quote the old
    // scene id in its own text is left alone — only the fingerprint moves. The
    // beat count sits in front of this and is not matched: it is "1 beat" in the
    // singular and "N beats" otherwise, and matching the plural alone silently
    // skipped every commitment with exactly one beat.
    const from = `, last moved in ${oldId}]`;
    const to = `, last moved in ${newId}]`;
    const repoint = (v: string) => (v.endsWith(from) ? v.slice(0, -from.length) + to : v);
    setAbsorbSid((s) => (s === oldId ? newId : s));
    setEditRows((rows) => rows.map((r) => {
      if (!SCENE_STAMPED.includes(r.kind)) return r;
      const next = { ...r };
      if (r.payload?.scene === oldId) next.payload = { ...r.payload, scene: newId };
      if (r.kind === "commitment") {
        next.before = repoint(r.before);
        if (r.resolve_from !== undefined) next.resolve_from = repoint(r.resolve_from);
      }
      return next;
    }));
    // An UNANSWERED conflict carries the same fingerprint, and it is the value
    // `resolveConflict` copies into `resolve_from` when the reviewer clicks
    // Replace. The server's own repoint has already moved the stored record onto
    // the new id, so a stale snapshot here means the retry is refused as changed
    // again — the reviewer answering a conflict that no longer exists, twice.
    // It is also what the panel SHOWS them, so leaving it stale would display an
    // id no scene has.
    // No kind check needed: `repoint` only rewrites a string ENDING in the
    // commitment fingerprint's suffix, and a plot conflict's `stored` is a
    // `plot_line`, which does not carry one.
    setConflicts((cs) => cs.map(({ row, conflict }) => (
      { row, conflict: { ...conflict, stored: repoint(conflict.stored) } })));
  }

  // ---- what the column and the panel count off the rows ------------------

  const budgetCutPhases = useMemo(
    () => (absorb?.phases ?? []).filter((p) => p.budget_exhausted),
    [absorb?.phases]);
  const approvedCount = editRows.filter((e) => e.approved).length;
  const rejectedCount = editRows.filter((e) => e.rejected).length;
  const undecidedCount = editRows.length - approvedCount - rejectedCount;
  const uncitedRows = editRows.flatMap((e, i) => (isUncited(e) ? [[e, i] as const] : []));
  /** How many proposals each store drawer holds, for the column's counts. */
  const groupCounts = EDIT_GROUPS.map((g) => ({
    ...g, n: editRows.filter((e) => drawerKey(e) === g.key).length,
  })).filter((g) => g.n > 0);
  // The low-confidence rows, each carrying the index it holds in `editRows`
  // (#110). Kept as pairs rather than filtered into a second array: every
  // handler on a row addresses it positionally, and a row rendered under its
  // position in the FILTERED list would edit whichever row happened to sit
  // there in the real one.
  const lowRows = useMemo(
    () => editRows.flatMap((e, i) => (editBand(e) === "low" ? [[e, i] as const] : [])),
    [editRows]);
  // The drawer to open when a review arrives: NEEDS YOU when it has anything in
  // it, otherwise the first store that does. Landing on an empty NEEDS YOU
  // would make a fully-cited absorb look like it proposed nothing.
  const defaultSection = uncitedRows.length > 0 ? "uncited"
    : lowRows.length > 0 ? "low"
    : (groupCounts[0]?.key ?? "uncited");
  const openSection = editRows.some((e) => drawerKey(e) === reviewSection)
    ? reviewSection : defaultSection;
  /** The rows the open drawer shows, each carrying the index it holds in
   *  `editRows` — which is what the conflict verdicts (#111) and the submitted
   *  batch are both keyed on, so it travels with the row rather than being
   *  recomputed from this list's own ordering. */
  const shownRows = editRows.flatMap((e, i) =>
    (drawerKey(e) === openSection ? [[e, i] as const] : []));
  /** Show a drawer. Takes the key and nothing else: the raw setter would also
   *  accept an updater function, and `openSection` above is the RESOLVED
   *  drawer, so a caller reading the previous value out of one would not get
   *  back the value it had just set. */
  const openDrawer = (key: string) => setReviewSection(key);

  return {
    absorb, absorbSid, generation, holdsScene,
    /** The "this scene moved after its review was prepared" notice — and only
     *  when the scene it was raised for is the one on screen. */
    staleReview: staleSid !== null && staleSid === activeId ? staleReview : null,
    /** Whether THIS scene is the one a Discard is still settling for. The play
     *  controls treat that as the scene being held, because it is; End scene
     *  does not, because End scene is the one operation that WAITS for it
     *  rather than being refused by it -- the same reason Cancel is not
     *  disabled by `reviewBusy`.
     *
     *  Scoped to the scene rather than answered campaign-wide, for the reason
     *  `holdsScene` is: scene ids repeat across campaigns and a review outlives
     *  a scene switch, so a Discard settling for scene A must not waive scene
     *  B's lock -- which on B is not this Discard at all but the shielded-abort
     *  window (#95) that End scene must never be pressed inside. */
    settlesScene: (sid: string | null) => !!sid && settlingSid === sid,
    dismissStale: () => setStale(null, null),
    editRows, reviewQuote, setReviewQuote,
    editChronicle, openSection, openDrawer,
    editFailures, dismissFailures: () => setEditFailures([]),
    conflictByRow, contradictionById, saveError,
    // One flag to the panel: "a review is being made for this scene", however
    // it started. The reader does not care whether this browser asked for it.
    absorbing: absorbing || adopting,
    saving, reviewBusy, retryingAudit, retryingDossiers,
    endScene, stopAbsorb, saveAbsorb, discard, decide, editRow, editPayload,
    resolveConflict,
    approveAllCited, retryAudit, retryDossiers, sceneRenamed,
    budgetCutPhases, approvedCount, rejectedCount, undecidedCount,
    uncitedRows, lowRows, groupCounts, shownRows,
  };
}
