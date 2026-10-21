/** The end-of-scene review: the absorb, its scoped retries, the stored
 *  review and the chronicle save, plus the opener and first post. Spread into
 *  `api` by `client.ts`. */
import { notifyShell } from "../notify";
import { READ_RETRIES, RUN_POLL_MS, awaitRun, runResult, sceneRunApi, streamDraft } from "../runs";
import { type ChatEvent, type OpenerContribution, type OpenerSpeaker, type RunHandle,
  isAbortError } from "../stream";
import { ApiError, request, sleepUnlessAborted } from "../transport";
import type { ChronicleEntry, Dossiers, Mechanics, SceneAbsorb, StagedEdit, TimelineEvent } from "../types";

/** The review still being prepared for this scene, if one is.
 *
 *  Asked when there is no stored review and the panel wants to know whether
 *  that means "none" or "not yet". The class check is the whole of it: the
 *  newest run on a scene is as likely to be a chat turn, and adopting one of
 *  those as a review would leave End Scene spinning over a reply.
 *
 *  A function rather than an inline `reviewApi` entry because the adoption
 *  below and the absorb call it too, and an object literal cannot refer to
 *  its own members while its type is still being inferred. */
async function liveReview(cid: string, sid: string): Promise<RunHandle | null> {
  // RETRIED, for `pendingReview`'s reason and with more riding on it. This is
  // the question the adoption pass asks FIRST, and a rejection is not an
  // answer of "nothing is running": read as one, it opens a stored payload a
  // live retry is about to replace, and the reviewer's approvals are then
  // saved over the retry that landed underneath them.
  for (let asked = 0; ; asked++) {
    try {
      const found = (await sceneRunApi.findRun(cid, sid)).run;
      return found && found.state === "running" && found.cls === "review"
        ? found : null;
    } catch (err) {
      const transient = !(err instanceof ApiError) || err.kind === "busy";
      if (!transient || isAbortError(err) || asked >= READ_RETRIES) throw err;
      await sleepUnlessAborted(RUN_POLL_MS);
    }
  }
}

/** What `pendingReview` answers. */
type PendingReview = {
  review: SceneAbsorb | null; generation: string | null;
  stale: { prepared_posts: number; current_posts: number } | null;
};

/** This scene's stored end-of-scene review, if one is waiting to be saved.
 *
 *  `review` is null and `stale` is set when the transcript has moved since
 *  the review was prepared -- the reviewer is told to re-run rather than
 *  shown a summary of posts that are no longer there. Both null means there
 *  is simply no review, which is the ordinary case on every mount. */
async function pendingReview(cid: string, sid: string): Promise<PendingReview> {
  // RETRIED, for `awaitRun`'s reason and in the place it matters most. This
  // read is the answer to "did the absorb land", and every caller treats a
  // rejection as "it did not": the latch clears, the panel never opens, and
  // the adoption effect that would have asked again does not re-run while
  // the same scene stays selected. So a dropped fetch on the one read that
  // happens right after a locked phone comes back loses a review that is
  // sitting on disk -- the loss this whole feature exists to prevent, in the
  // last two lines of it.
  //
  // Transient failures only. A `busy` 409 is the sidecar held for a moment
  // by a sync client and clears on its own; `review_unreadable` and a 404
  // are answers, and asking again just spends time before reporting them.
  for (let asked = 0; ; asked++) {
    try {
      return await request<PendingReview>(
        "GET", `/api/campaigns/${cid}/scenes/${sid}/pending-review`,
        undefined, { fresh: true });
    } catch (err) {
      const transient = !(err instanceof ApiError) || err.kind === "busy";
      if (!transient || isAbortError(err) || asked >= READ_RETRIES) throw err;
      await sleepUnlessAborted(RUN_POLL_MS);
    }
  }
}

/** The run a POST may have started, when its reply never arrived.
 *
 *  A POST WITH NO RESPONSE IS AMBIGUOUS. The server can have accepted it,
 *  reserved the run and started generating, and the 202 be lost on the way back
 *  -- a dropped link, a WebView backgrounded in the same second. Reported as a
 *  failure, the caller clears its latch for work that is running: the scene is
 *  held against play for as long as it takes, the next request is refused
 *  `run_in_flight`, and for an absorb there is not even a generation to offer a
 *  Stop with. For a scoped retry it is worse than that -- the run merges into
 *  the durable review while the panel keeps the phase it was retrying, and
 *  saving then commits those stale rows over the completed retry.
 *
 *  Only for a failure that carried NO reply. An `ApiError` means the server
 *  answered -- `already_absorbed`, a missing key, a busy scene -- and every one
 *  of those is the caller's to handle, not a run to adopt.
 *
 *  `kind` is what tells one review run from another: they all share the
 *  `review` class, so an absorb must not adopt a retry and a retry must not
 *  adopt an absorb.
 */
async function adoptLostStart(cid: string, sid: string, kind: string,
                              err: unknown): Promise<{ run: RunHandle;
                                                       generation: string }> {
  if (err instanceof ApiError || isAbortError(err)) throw err;
  const live = await liveReview(cid, sid).catch(() => null);
  if (live?.kind !== kind || !live.review_generation) throw err;
  return { run: live, generation: live.review_generation };
}

/** The stored review a scoped retry folded itself into, when its run is gone.
 *
 *  A retry is a detached run like the absorb, and it merges its phase into the
 *  durable record before its own run record is anything but a receipt. Runs
 *  stop being discoverable after the retention window, and a suspended tab --
 *  a locked phone, the case this whole feature exists for -- is away longer
 *  than that routinely. The poll then 404s on a retry that landed perfectly
 *  well, the panel keeps showing the phase it was retrying, and saving commits
 *  those stale rows and clears the record: the completed retry is gone, and
 *  nothing ever said so.
 *
 *  ONLY for `run_gone`, and that narrowness is the point. A run that ended
 *  `failed` never merged anything, so reading the record back would hand the
 *  panel the PRE-retry phase and present it as this retry's result -- a wrong
 *  answer dressed as a right one. A phase that ran and found nothing reports
 *  that as a status on a landed run and never reaches here at all.
 *
 *  Matched on the generation, for `absorbScene`'s reason: a scene can be
 *  holding a review this retry has nothing to do with.
 */
async function reapedPhase(cid: string, sid: string, generation: string,
                           err: unknown): Promise<SceneAbsorb> {
  if (!(err instanceof ApiError) || err.kind !== "run_gone") throw err;
  const stored = await pendingReview(cid, sid).catch(() => null);
  if (!stored?.review || stored.generation !== generation) throw err;
  return stored.review;
}

export const reviewApi = {
  /** The run this id names, right now. What a review is polled through.
   *
   *  A review is not a stream: End Scene is a form to read, not text arriving
   *  word by word, so there is nothing to tail and `attachRun` is the wrong
   *  shape for it. The server answers the POST with a run and the client asks
   *  about it until it stops running. */

  /** Wait for a detached run to stop, and answer with what it became.
   *
   *  Exported so the review panel can adopt a run it did not start -- one that
   *  was still generating when the phone locked, which is the case this whole
   *  feature exists for. */
  awaitRun: (cid: string, sid: string, run: RunHandle, signal?: AbortSignal) =>
    awaitRun(cid, sid, run, signal),

  liveReview,
  pendingReview,

  /** Throw the stored review away, and stop whatever is still making it.
   *
   *  Named by GENERATION rather than by run id: an absorb and the retries of
   *  its phases all belong to one review, and Cancel means "stop preparing
   *  this", not "stop the most recent thing". Answers only once the runs it
   *  flagged have really stopped, so the caller may start a fresh absorb
   *  immediately -- the scene's exclusion key is free by then. */
  discardReview: (cid: string, sid: string, generation: string) =>
    request<{ removed: boolean; stopped: number }>(
      "DELETE", `/api/campaigns/${cid}/scenes/${sid}/pending-review`
        + `?generation=${encodeURIComponent(generation)}`),

  // `force` re-runs an absorb the backend has already recorded in the chronicle;
  // without it that POST is a 409 (kind "already_absorbed") -- see #235.
  //
  // Detached (#396): the POST answers 202 the moment the run is reserved, so
  // there is no socket for a locked phone to take down, and the review is read
  // back off the store once the run lands. The wait is the client's now, and
  // it is a poll rather than a held connection precisely because the client
  // may not be there for the whole of it.
  //
  // `onStarted` is handed the review's generation the moment the POST answers,
  // minutes before the review itself. That is the only window in which the
  // caller can offer a way OUT: a `review` holds the scene against play for as
  // long as it runs, and `absorb_budget = 0` means nothing bounds that -- so a
  // panel with no generation to name has no way to stop what it started.
  absorbScene: async (cid: string, sid: string, force = false,
                      onStarted?: (generation: string) => void) => {
    let started: { run: RunHandle; generation: string };
    try {
      started = await request<{ run: RunHandle; generation: string }>(
        "POST", `/api/campaigns/${cid}/scenes/${sid}/absorb${force ? "?force=true" : ""}`);
    } catch (err) {
      // A POST WITH NO RESPONSE IS AMBIGUOUS. The server can have accepted it,
      // reserved the run and started generating, and the 202 be lost on the way
      // back -- a dropped link, a WebView backgrounded in the same second. The
      // caller then clears its latch for an absorb that is running: the scene
      // is held against play for as long as it takes, End scene answers
      // `run_in_flight`, and there is no generation to offer a Stop with,
      // because `onStarted` never fired.
      //
      // Only for a failure that carried NO reply. An `ApiError` means the
      // server answered -- `already_absorbed`, a missing key, a busy scene --
      // and every one of those is the caller's to handle, not a run to adopt.
      // ...and it has to be an ABSORB. `review` is the class a whole review's
      // runs share, so a scoped retry of some EARLIER review's phase wears it
      // too -- and adopting one would install that review's generation and
      // hand its summary back as this End scene's result.
      started = await adoptLostStart(cid, sid, "absorb", err);
    }
    onStarted?.(started.generation);
    try {
      await awaitRun(cid, sid, started.run);
    } catch (err) {
      // THE STORE IS THE ANSWER, not the run record. A run stops being
      // discoverable after `REAP_SECONDS`, and a suspended tab -- a locked
      // phone, exactly the case this feature is for -- can easily be away
      // longer than that: the poll then 404s on a run whose review landed
      // perfectly well and is sitting on disk. Failing there would report the
      // one loss this whole change exists to prevent.
      //
      // MATCHED ON THE GENERATION, not merely on something being stored. A
      // scene can already be holding an EARLIER review the reader never saved,
      // and handing that back for a run that genuinely failed would show them
      // a stale summary as this absorb's result -- a wrong answer presented as
      // a right one, which is worse than the failure it replaced.
      const recovered = await pendingReview(cid, sid).catch(() => null);
      if (!recovered?.review || recovered.generation !== started.generation) throw err;
      return notifyShell({ review: recovered.review, generation: recovered.generation });
    }
    const pending = await pendingReview(cid, sid);
    if (!pending.review || pending.generation !== started.generation) {
      // The run landed and this run's record is not what is there. Either the
      // scene moved on between the two -- a turn appended, a post was cut,
      // which is what the watermark exists to catch -- or the review was
      // discarded while it was being prepared. Reporting the refusal beats
      // handing the panel a null it would render as an empty review, or
      // somebody else's review as this one's.
      throw new ApiError(409, "the scene changed while the review was being "
        + "prepared — end the scene again", "review_stale",
        pending.stale ?? undefined);
    }
    // The detached run changed the pending-review queue after its POST
    // settled. Refresh chrome only once this generation is known to be stored,
    // before the caller opens its /wrap-up address against an old empty queue.
    return notifyShell({ review: pending.review, generation: pending.generation });
  },
  saveChronicle: (cid: string, sid: string,
                  body: { one_line: string; summary: string; keywords: string[];
                          timeline_events: TimelineEvent[]; edits: StagedEdit[];
                          commit_token?: string }) =>
    request<ChronicleEntry & { applied: string[];
      failures: { id: string; reason: string; kind: "conflict" | "error" }[];
      /** Records the save published to the world library. Absent on a token
       *  recorded before publishing existed, which replays without it. */
      published?: { kind: string; id: string }[];
      publish_failed?: { kind: string; id: string; reason: string }[] }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/chronicle`, body).then(notifyShell),
  getChronicle: (cid: string) =>
    request<ChronicleEntry[]>("GET", `/api/campaigns/${cid}/chronicle`),
  // Both scoped retries are detached runs of their own (#396), folded into the
  // stored review by the server as well as into the panel by the caller. The
  // `signal` no longer reaches the work -- it stops this client WAITING, which
  // is all an abort could ever do once the run outlives the request. What stops
  // the work is `discardReview`, which flags every run preparing that review.
  //
  // Each still answers with what THIS retry produced rather than with the
  // merged review, because that is the question the panel is asking: the
  // reviewer's own typing and approvals live only in the browser, so the merge
  // on screen has to be the local one and the stored merge is what a client
  // coming back later reads.
  retryAudit: async (cid: string, sid: string, signal?: AbortSignal) => {
    let started: { run: RunHandle; generation: string };
    try {
      started = await request<{ run: RunHandle; generation: string }>(
        "POST", `/api/campaigns/${cid}/scenes/${sid}/audit`, undefined, { signal });
    } catch (err) {
      started = await adoptLostStart(cid, sid, "audit", err);
    }
    try {
      const run = await awaitRun(cid, sid, started.run, signal);
      return runResult<{ mechanics: Mechanics; edits: StagedEdit[] }>(run);
    } catch (err) {
      // The server folded this retry into the stored review before the run
      // record was reaped, so the phase is on disk even though the run is not.
      const stored = await reapedPhase(cid, sid, started.generation, err);
      return { mechanics: stored.mechanics,
               edits: stored.edits.filter((e) => e.kind === "sheet") };
    }
  },
  // The dossier phase's sibling to retryAudit (#286): re-runs that phase alone,
  // on a fresh budget, without disturbing the rest of the open review.
  retryDossiers: async (cid: string, sid: string, signal?: AbortSignal) => {
    let started: { run: RunHandle; generation: string };
    try {
      started = await request<{ run: RunHandle; generation: string }>(
        "POST", `/api/campaigns/${cid}/scenes/${sid}/dossiers`, undefined, { signal });
    } catch (err) {
      started = await adoptLostStart(cid, sid, "dossiers", err);
    }
    try {
      const run = await awaitRun(cid, sid, started.run, signal);
      return runResult<{ dossiers: Dossiers; edits: StagedEdit[] }>(run);
    } catch (err) {
      const stored = await reapedPhase(cid, sid, started.generation, err);
      // `proposed` is the phase's own list of who it prepared a dossier for --
      // the same projection `pending_reviews.merge_dossiers` kept the rows by,
      // so this reads back exactly the rows that retry contributed.
      const proposed = new Set(stored.dossiers?.proposed ?? []);
      return { dossiers: stored.dossiers,
               edits: stored.edits.filter(
                 (e) => e.kind === "dossier" && proposed.has(e.target?.id)) };
    }
  },
  /** Generate a scene's first post. Detached server-side, so a backgrounded
   *  tab no longer loses it: `streamDraft` re-finds the run by its attempt id
   *  and reads the frames it missed. Nothing is written by this call — the
   *  text reaches the transcript through `firstPost`. */
  /** `adapt` rewrites the greeting the scene was started from (its stamp) for
   *  where the campaign stands now, and the server then ignores `prompt` (#91). */
  opener: (cid: string, sid: string, prompt: string, onEvent: (e: ChatEvent) => void,
           signal?: AbortSignal, completed: OpenerContribution[] = [], snapshot: OpenerSpeaker[] = [],
           adapt = false) =>
    streamDraft(cid, sid, `/api/campaigns/${cid}/scenes/${sid}/opener`,
                { prompt, completed, snapshot, adapt }, onEvent, signal),
  firstPost: (cid: string, sid: string, text: string,
              contributions?: OpenerContribution[], snapshot?: OpenerSpeaker[]) =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/scenes/${sid}/first-post`,
      { text, contributions, snapshot }),
};
