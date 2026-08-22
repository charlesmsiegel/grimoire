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
  const [staleReview, setStaleReview] =
    useState<{ prepared_posts: number; current_posts: number } | null>(null);
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
    setAbsorb(null);
    setAbsorbSid(null);
    setGeneration(null);
    setStaleReview(null);
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
    setStaleReview(null);
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
        // A review that is there and unusable. Its GENERATION is still worth
        // holding: the record is on disk, and Discard is how the reader gets
        // rid of it without spending another absorb.
        setStaleReview(pending.stale);
        setAbsorbSid(sid);
        setGeneration(pending.generation);
        return;
      }
      let live;
      try {
        live = await api.liveReview(cid, sid);
      } catch {
        return;
      }
      if (dropped || campaignRef.current !== cid || hasReviewRef.current || !live) return;
      // Still generating. Show the panel as busy and wait it out -- the run is
      // the server's, and this client is a subscriber that can come and go.
      setAdopting(true);
      setAbsorbSid(sid);
      setGeneration(live.review_generation ?? null);
      try {
        await api.awaitRun(cid, sid, live);
        const landed = await api.pendingReview(cid, sid);
        if (dropped || campaignRef.current !== cid || hasReviewRef.current) return;
        if (landed.review) openReview(landed.review, sid, landed.generation);
        else setStaleReview(landed.stale);
      } catch (err: unknown) {
        if (dropped || campaignRef.current !== cid) return;
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
    setAbsorbing(true);
    clearError();
    setStaleReview(null);
    setEditFailures([]);
    setConflicts([]);
    try {
      // Nothing is deleted on the way in, deliberately. A fresh absorb
      // REPLACES whatever is stored for this scene
      // (`pending_reviews.publish`), so a delete first would buy nothing --
      // and it would cost something real: the re-absorb below asks for
      // confirmation, and a reader who declines would have lost the review
      // they were looking at to a question they answered "no" to.
      let a;
      try {
        a = await api.absorbScene(cid, activeId);
      } catch (err: any) {
        if (err?.kind !== "already_absorbed") throw err;
        if (!window.confirm(
          "This scene has already been absorbed. Absorbing again re-proposes every " +
          "change from scratch, so appended lore and plot beats can end up duplicated. " +
          "Absorb it again?")) return;
        a = await api.absorbScene(cid, activeId, true);
      }
      // The review belongs to the campaign that asked for it. An absorb is the
      // slowest request in the app -- several LLM calls -- so there is ample
      // room to switch campaigns while it runs, and the `[cid]` effect that
      // clears review state cannot touch a request already in flight. Installing
      // this would put A's summary, timeline and staged edits in front of B,
      // where Save posts them to B: scene ids repeat across campaigns and a
      // fresh commit token matches, so nothing downstream would refuse them.
      if (campaignRef.current !== cid) return;
      openReview(a.review, activeId, a.generation);
    } catch (err: any) {
      // Same guard on the failure path: A's banner over B is the same category
      // of wrong answer, just a cheaper one.
      if (campaignRef.current !== cid) return;
      // `false`, for the scoped retries' reason: the banner's Retry runs the
      // CHAT retry, so it would answer a failed absorb by generating one more
      // reply into the scene the user was trying to finish. End scene is its
      // own recovery, and it is still right there.
      fail(err, false);
    } finally {
      setAbsorbing(false);
    }
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

  /** Throw the review away. Not disabled by `reviewBusy`: a retry runs on the
   *  absorb budget, which is unbounded at 0, so this is the only way out of a
   *  request that may never answer. Safe because `releaseRetries` invalidates
   *  that request on the way out. */
  function discard() {
    releaseRetries();
    // The review is on disk (#396), so throwing it away is a request and not a
    // `setState`. Fire-and-forget: the panel closes now either way -- the
    // reviewer asked for it -- and a record left behind by a failed DELETE is
    // replaced by the next absorb or refused at save by its own watermark. It
    // is also what STOPS a retry still generating for this review, which
    // nothing else can do now that closing the connection is not a cancel.
    const sid = absorbSid ?? activeId;
    const gen = generationRef.current;
    if (sid && gen) void api.discardReview(cid, sid, gen).catch(() => {});
    setAbsorb(null);
    setAbsorbSid(null);
    setGeneration(null);
    setStaleReview(null);
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
      setEditRows((rows) => [
        ...rows.filter((r) => r.kind !== "sheet"),
        ...res.edits.map((e) => ({ ...e, approved: approvedByDefault(e) })),
      ]);
      // Conflicts are bound to row numbers, and this rebuilds the array — so
      // any that survived a refusal would now point at whichever row inherited
      // their index. Sheet edits never conflict, so there is nothing to carry
      // over; the next save re-reports whatever is still drifted.
      setConflicts([]);
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
    absorb, absorbSid, generation, staleReview,
    dismissStale: () => setStaleReview(null),
    editRows, reviewQuote, setReviewQuote,
    editChronicle, openSection, openDrawer,
    editFailures, dismissFailures: () => setEditFailures([]),
    conflictByRow, contradictionById, saveError,
    // One flag to the panel: "a review is being made for this scene", however
    // it started. The reader does not care whether this browser asked for it.
    absorbing: absorbing || adopting,
    saving, reviewBusy, retryingAudit, retryingDossiers,
    endScene, saveAbsorb, discard, decide, editRow, editPayload, resolveConflict,
    approveAllCited, retryAudit, retryDossiers, sceneRenamed,
    budgetCutPhases, approvedCount, rejectedCount, undecidedCount,
    uncitedRows, lowRows, groupCounts, shownRows,
  };
}
