/** Detached runs: waiting for one (`awaitRunAt`), the start -> poll -> unwrap
 *  draft helper (`draftRun`), the streaming opener's recovery
 *  (`streamDraft`), and the scene run routes they all ask through.
 *
 *  Split out of `client.ts`, which re-exports the poll constants and
 *  `DraftScope` and spreads `sceneRunApi` into `api`. */
import { isProviderFailure } from "./errors";
import { invalidateConfigCache, noteRunSettled } from "./freshness";
import { type ChatEvent, type RunHandle, isAbortError, newAttemptId } from "./stream";
import { ApiError, RunEndedError, request, settlingOn, sleepUnlessAborted, streamGet,
  streamPost } from "./transport";
import type { EntityScope } from "./types";
import { configChanged } from "../appEvents";
import { encodeSegment } from "../urlSegment";

/** How often a computing run is polled while it works, and how far that backs
 *  off once it is clearly going to take a while.
 *
 *  The request is local and the answer is a few hundred bytes, so a second is
 *  cheap for the case that matters -- an audit retry that answers in ten -- but
 *  an absorb runs for minutes, and asking six hundred times to learn the same
 *  thing is noise in every log the user might read. So it stays at
 *  `RUN_POLL_MS` for the first `RUN_POLL_QUICK` asks and then settles at
 *  `RUN_POLL_SLOW_MS`, which is the most a reader ever waits to be told their
 *  review is ready. Exported so a test can drive the cadence rather than infer
 *  it from a sleep.
 */
export const RUN_POLL_MS = 1000;
export const RUN_POLL_QUICK = 10;
export const RUN_POLL_SLOW_MS = 5000;
/** How many consecutive polls may fail before the wait gives up. Generous,
 *  because the failures this rides out are exactly the ones the feature is
 *  about, and cheap, because a run that is really gone answers the same 404
 *  each time and the store is asked next. */
export const RUN_POLL_MISSES = 5;
/** How many times a durable READ is retried before its failure is reported.
 *  The store is where a detached run's answer lives, so a dropped fetch on the
 *  way to it is the same class of nothing-happened as a dropped poll. */
export const READ_RETRIES = 3;

/** Wait for a detached run to stop running, and answer with what it became.
 *
 *  A poll, not a held connection, and that is the whole point: the client may
 *  not be there for the whole of a ten-minute absorb -- a locked phone, a
 *  suspended tab, a closed laptop -- and nothing about the run depends on it
 *  being there. Coming back and asking again is a complete recovery.
 *
 *  A failed run is raised as the HTTP failure the same work would have been
 *  when these routes answered synchronously, status and kind included, so a
 *  caller's existing handling of `missing_key` or a timeout is unchanged --
 *  only where it reads them from moved.
 *
 *  `signal` stops the WAITING. It cannot stop the run: that is what detached
 *  means, and `api.discardReview` (or `api.cancelRun`) is how a caller says
 *  stop.
 */
export async function awaitRun(cid: string, sid: string, started: RunHandle,
                               signal?: AbortSignal): Promise<RunHandle> {
  return awaitRunAt(`/api/campaigns/${cid}/scenes/${sid}/runs`, started,
                    () => sceneRunApi.findRun(cid, sid).then((r) => r.run, () => null),
                    signal);
}

/** The same wait, over any subject's run routes.
 *
 *  `base` is where this subject's runs live -- `.../scenes/{sid}/runs` for a
 *  review, `/api/campaigns/{cid}/runs`, `/api/worlds/{wid}/runs` or `/api/runs`
 *  for a draft. `restill` is how to ask, by a different path, whether the run
 *  is still going; it is the last word before a run of failed polls is reported
 *  as a failure, and it answers `null` for "not running any more or not
 *  findable".
 *
 *  Split out rather than duplicated because the poll's whole value is in its
 *  three refusals to give up -- a failed poll is not a failed run, a 404 is,
 *  and running out of patience is neither -- and a second copy of that
 *  reasoning is a second place for it to be got wrong.
 */
export async function awaitRunAt(base: string, started: RunHandle,
                                 restill: () => Promise<RunHandle | null>,
                                 signal?: AbortSignal,
                                 onSettled?: () => void): Promise<RunHandle> {
  let run = started;
  let missed = 0;
  for (let asked = 0; run.state === "running"; asked++) {
    await sleepUnlessAborted(
      asked < RUN_POLL_QUICK ? RUN_POLL_MS : RUN_POLL_SLOW_MS, signal);
    try {
      run = (await request<{ run: RunHandle }>(
        "GET", `${base}/${run.id}`, undefined, { fresh: true, signal })).run;
      missed = 0;
    } catch (err) {
      missed = await weighPollFailure(err, run, missed, restill, onSettled);
    }
  }
  // Seen to end, landed or not: the only way here. An abort and a wait that
  // ran out of patience leave by throwing above, with the run still going,
  // and say nothing -- the run has not settled, only the waiting has.
  onSettled?.();
  if (run.state === "landed") return run;
  throw runFailure(run);
}

/** What one failed poll does to `awaitRunAt`'s wait: rethrown when it ends
 *  the wait, otherwise the new count of consecutive misses -- zero again once
 *  `restill` has vouched for the run. */
async function weighPollFailure(err: unknown, run: RunHandle, missed: number,
                                restill: () => Promise<RunHandle | null>,
                                onSettled?: () => void): Promise<number> {
  // A FAILED POLL IS NOT A FAILED RUN. The conditions this feature exists
  // for -- a backgrounded WebView, a suspended tab resuming into a dead
  // socket, a dropped localhost fetch -- all show up here, and ending the
  // wait for one of them reports a failure for a run that is still
  // generating: the reader gets a banner, the composer unlocks over a
  // scene the server is still holding, and nothing opens the review when
  // it does land.
  //
  // Three things are not transient and are not retried: an abort (the
  // caller saying stop), a 404 (the run is gone -- reaped, or never this
  // scene's -- which is decisive and sends the caller to the store, where
  // the answer actually lives), and running out of patience. Everything
  // else is asked again.
  if (isAbortError(err)) throw err;
  // Gone is over: a run is reaped only once it has ended.
  if (err instanceof ApiError && err.status === 404) { onSettled?.(); throw err; }
  if (missed + 1 <= RUN_POLL_MISSES) return missed + 1;
  // OUT OF PATIENCE IS NOT A TERMINAL STATE EITHER. Nothing here has
  // observed the run stop; all that is known is that this one URL has not
  // answered six times running. Reported as a failure, the caller clears
  // its latch and its Stop goes with it, over a run the server may hold
  // for as long as an unbounded budget allows.
  //
  // So the last word goes to a different question, asked of a different
  // path: is this scene still running a review at all? A "yes" resets the
  // patience and the wait goes on; anything else -- including this ask
  // failing too, which is the network really being gone -- lets the
  // original failure stand.
  const still = await restill();
  if (still?.id !== run.id || still.state !== "running") throw err;
  return 0;
}

/** The error a run seen to end other than `landed` is raised as: the HTTP
 *  failure the same work raised when these routes answered synchronously. */
function runFailure(run: RunHandle): RunEndedError {
  const error = run.error ?? undefined;
  const failure = new RunEndedError(error?.status ?? 409,
                                    error?.detail ?? `the run ended ${run.state}`,
                                    error?.kind ?? run.state, error);
  // The same announcement `requestRaw` makes for a non-2xx, and it has to be
  // repeated here because a detached failure never goes through that branch:
  // the 202 was a success and the provider failed minutes later. Without it a
  // real `auth` or `rate_limit` leaves the status bar showing the verdict from
  // before the failure until the next navigation, which is the exact staleness
  // #146 exists to remove.
  if (isProviderFailure(failure)) {
    invalidateConfigCache();
    configChanged();
  }
  return failure;
}

/** Where a subject's detached runs are addressed.
 *
 *  Three, matching the three non-scene subjects the backend mounts run routes
 *  for. A scene's runs are NOT reachable through this: a scene subject carries
 *  an identity as well as an id, and the one draft that has one -- the opener
 *  -- is a stream, so it uses `streamPost` and the scene run routes rather than
 *  this poll.
 */
export type DraftScope =
  | { at: "campaign"; id: string }
  | { at: "world"; id: string }
  | { at: "global" };

export function draftBase(scope: DraftScope): string {
  if (scope.at === "campaign") return `/api/campaigns/${encodeSegment(scope.id)}/runs`;
  if (scope.at === "world") return `/api/worlds/${encodeSegment(scope.id)}/runs`;
  return "/api/runs";
}

/** This attempt's run on that subject, or `null` if the subject has none.
 *
 *  Matched on the ATTEMPT, never on the kind. The design deliberately lets
 *  `draft` runs overlap on one coarse subject, so "the world's in-flight
 *  image-description run" is ambiguous the moment two images are being
 *  described at once -- and a caller that adopted the other image's run would
 *  offer its text for the wrong picture.
 *
 *  **A failed lookup is not an answer of "no run".** It throws, and the caller
 *  decides; three separate defects in the first phase of this work were a
 *  `.catch(() => null)` on exactly this question, each of which turned "I could
 *  not ask" into "there is nothing there". The retries are here for the same
 *  reason the poll has them: the conditions this whole feature exists for --
 *  a resuming tab, a dropped localhost fetch -- show up as a failed GET.
 */
export async function findDraftRun(scope: DraftScope, attempt: string,
                                   signal?: AbortSignal): Promise<RunHandle | null> {
  const path = `${draftBase(scope)}?attempt=${encodeURIComponent(attempt)}`;
  for (let asked = 0; ; asked++) {
    try {
      const found = await request<{ runs: RunHandle[] }>(
        "GET", path, undefined, { fresh: true, signal });
      // The newest, because an attempt id names one piece of work and the
      // server keys its index on it -- so this list is at most one long. Read
      // from the end rather than at 0 so a server that ever answered with more
      // hands back the live one rather than a retained corpse.
      return found.runs.length ? found.runs[found.runs.length - 1] : null;
    } catch (err) {
      if (isAbortError(err) || asked >= READ_RETRIES) throw err;
      await sleepUnlessAborted(RUN_POLL_MS, signal);
    }
  }
}

/** Start a computing draft, wait for it, and hand back what it produced.
 *
 *  THE shared helper. Twelve routes are `start -> poll -> unwrap` and nothing
 *  else, and writing that out twelve times is twelve subtly different readings
 *  of one contract -- which of them retries a dropped poll, which of them tells
 *  a 404 from a timeout, which of them re-finds a run whose 202 was lost. Each
 *  call site keeps the exact shape it returned when these routes blocked, so
 *  nothing above this line can tell that the work moved off the request.
 *
 *  `start` is given the attempt id to send, because how the POST is made
 *  differs -- a JSON body, a query string, a multipart upload -- and only that
 *  differs.
 *
 *  A START WITH NO RESPONSE IS AMBIGUOUS, and that is the case this exists for:
 *  the server can have accepted the POST, reserved the run and begun a
 *  provider call, and the 202 be lost on the way back. Reported as a failure,
 *  the caller shows an error over work that is running and the user starts it
 *  again -- paying twice. Only for a failure that carried NO reply: an
 *  `ApiError` means the server answered, and a 404 or a `missing_key` is the
 *  caller's to handle.
 */
export async function draftRun<T>(scope: DraftScope,
                                  start: (attempt: string) => Promise<{ run: RunHandle }>,
                                  opts?: { signal?: AbortSignal | undefined; attempt?: string | undefined }): Promise<T> {
  const attempt = opts?.attempt ?? newAttemptId();
  let started: RunHandle;
  try {
    started = (await start(attempt)).run;
  } catch (err) {
    if (err instanceof ApiError || isAbortError(err)) throw err;
    // Two different outcomes, and they are deliberately not collapsed. A
    // lookup that ANSWERED "no run" settles it: nothing was started, so the
    // start's own failure is the true one. A lookup that could not be made
    // settles nothing -- `findDraftRun` has already retried it -- and reports
    // the same start failure, because that is the one the caller actually
    // suffered and a "could not check" tells them nothing they can act on.
    const live = await findDraftRun(scope, attempt, opts?.signal).catch(() => null);
    if (!live) throw err;
    started = live;
  }
  // Noted only once the run is SEEN to end -- landed, failed or reaped: it may
  // have written after its 202 settled (a catalog refresh, a model test's
  // verdict). An abandoned wait (an abort, a connection that never came back)
  // has observed no end, and announcing one would let a page re-read before
  // the still-running draft writes.
  const run = await awaitRunAt(draftBase(scope), started,
                               () => findDraftRun(scope, attempt, opts?.signal)
                                 .catch(() => null),
                               opts?.signal, noteRunSettled);
  return runResult<T>(run);
}

/** What a landed run produced, as the shape its route promises.
 *
 *  `landed` is the only way out of `awaitRunAt`, so a result is what this run
 *  is. An empty object rather than a throw for a route that legitimately
 *  produced nothing -- the caller's own type says which fields it expects.
 *  The one place the wire's `Record<string, unknown>` is asserted to a route's
 *  shape, so every reader of a run's result trusts the server the same way. */
export function runResult<T>(run: RunHandle): T {
  return (run.result ?? {}) as T;
}

/** `draftRun`'s second half alone: follow a run already found by its attempt
 *  id to what it produced, for a remount that must not repeat the start. */
export async function followDraft<T>(scope: DraftScope, handle: RunHandle, attempt: string,
                                     signal?: AbortSignal): Promise<T> {
  const run = await awaitRunAt(draftBase(scope), handle,
                               () => findDraftRun(scope, attempt, signal).catch(() => null),
                               signal, noteRunSettled);
  return runResult<T>(run);
}

/** How many times a lost streaming draft is picked back up before its failure
 *  is reported. Small, because each attempt makes two requests and a run that
 *  is really gone answers the same way every time; more than one because the
 *  case this exists for -- a WebView resuming into a dead socket -- can drop
 *  the re-attach as easily as it dropped the stream. */
export const STREAM_REATTACH_TRIES = 3;

/** Read a streaming draft, and keep reading it across a lost connection.
 *
 *  The opener is the only member, and it is the one draft whose value is the
 *  text arriving word by word rather than a payload at the end -- so it gets
 *  `streamPost` and the run's frame buffer instead of `draftRun`'s poll. What
 *  it shares with the twelve is the whole point: it is detached, so the socket
 *  dying is no longer the generation dying, and this is what turns that into
 *  something the reader sees.
 *
 *  `consumed` is the last frame index actually DELIVERED, tracked from the
 *  server's `id:` lines rather than counted here. A per-event count would lag,
 *  because a heartbeat frame carries no event at all -- and resuming at a lagged
 *  cursor replays text already on the screen, mid-sentence.
 *
 *  An `ApiError` or an abort is the caller's and is re-raised untouched: the
 *  server answered (a missing key, a scene that vanished), or the caller said
 *  stop. Only a connection that died with no reply is picked back up.
 */
export async function streamDraft(cid: string, sid: string, path: string, body: unknown,
                                  onEvent: (e: ChatEvent) => void,
                                  signal?: AbortSignal): Promise<void> {
  const reader = new OpenerReader(cid, sid, onEvent, signal);
  let lost: unknown;
  try {
    await streamPost(path, body, reader.watch, signal, reader.attempt, reader.onIndex);
    if (reader.ended) return;
    lost = new Error(STREAM_CUT);
  } catch (err) {
    if (err instanceof ApiError || isAbortError(err)) throw err;
    lost = err;
  }
  for (let tries = 0; tries < STREAM_REATTACH_TRIES; tries++) {
    const step = await reader.reattach(lost);
    if (step === "done") return;
    if (step !== "retry") lost = step.lost;
  }
  throw lost;
}

/** What a stream that closed before its terminal frame is reported as. */
const STREAM_CUT = "the opener stream ended before the generation did";

/** One re-attach pass of `streamDraft`: the opener has been read to its end
 *  (`done`), the pass settled nothing and another may be spent (`retry`), or
 *  the stream was cut again and this is now the failure to report. */
type Reattach = "done" | "retry" | { lost: unknown };

/** The state `streamDraft` keeps across its re-attaches, and the steps that
 *  read and move it. A class rather than closures so each step can be named
 *  and read on its own; one instance lives for one opener. */
class OpenerReader {
  readonly attempt = newAttemptId();
  consumed = -1;
  // Whether a TERMINAL frame arrived, not whether the read loop ended. A proxy
  // or a WebView can close the response cleanly part-way through -- the reader
  // reports `done` and `streamPost` resolves, with no error to catch -- and a
  // recovery hung off the exception alone would return happily, leaving the
  // reader a half-written opener while the server finishes the rest into a
  // buffer nobody reads.
  ended = false;
  // Where this scene is addressed NOW, and the name that survives it moving.
  // Both are learned from the leading `run` frame, which arrives before any
  // delta and before anything can fail.
  private at: string;
  private identity = "";

  constructor(private readonly cid: string, sid: string,
              private readonly onEvent: (e: ChatEvent) => void,
              private readonly signal: AbortSignal | undefined) {
    this.at = sid;
  }

  readonly onIndex = (i: number) => { this.consumed = i; };

  readonly watch = (e: ChatEvent) => {
    if (e.done ?? e.error) this.ended = true;
    if (e.run?.scene_identity) this.identity = e.run.scene_identity;
    this.onEvent(e);
  };

  /** Find the run again and read what it buffered since `consumed`. */
  async reattach(lost: unknown): Promise<Reattach> {
    const run = await this.lookup();
    if (run === undefined) return "retry";
    // The server has never heard of this attempt, so nothing was started and
    // the original failure is the true one.
    if (!run) throw lost;
    // Terminal with nothing left in the buffer: the run finished while we were
    // away and every frame of it has already been delivered.
    if (run.state !== "running" && this.consumed + 1 >= run.next_index) return "done";
    try {
      await sceneRunApi.attachRun(this.cid, this.at, run.id, this.consumed + 1, this.watch,
                                  this.signal, this.onIndex);
      // Cut again, the same way. Another pass, bounded by the loop.
      return this.ended ? "done" : { lost: new Error(STREAM_CUT) };
    } catch (err) {
      // A rename can land in the gap between the lookup above and this attach,
      // and then it is the ATTACH that 404s -- so re-resolving only when the
      // lookup came back empty leaves that window open. Same recovery, one
      // step later; the loop bounds how often it can happen.
      if (err instanceof ApiError && err.kind === "run_gone" && await this.mayHaveMoved()) {
        return "retry";
      }
      if (err instanceof ApiError || isAbortError(err)) throw err;
      return { lost: err };
    }
  }

  /** This attempt's run, `null` when the server answered that there is none,
   *  or `undefined` when the lookup itself could not be made. */
  private async lookup(): Promise<RunHandle | null | undefined> {
    try {
      let run = (await sceneRunApi.findRun(this.cid, this.at, this.attempt, this.signal)).run;
      // THE SCENE MAY HAVE BEEN RENAMED. An opener holds no exclusion key --
      // deliberately, so it cannot refuse the `first-post` it exists to feed
      // -- so another tab may rename the scene while it runs, and a rename
      // mints a new `sid`. The run is indexed by the scene's IDENTITY, which
      // survives that, so the old id resolves to no run at all and every
      // re-attach would abandon a generation the server is still buffering.
      if (!run && await this.moved()) {
        run = (await sceneRunApi.findRun(this.cid, this.at, this.attempt, this.signal)).run;
      }
      return run;
    } catch (err) {
      if (isAbortError(err)) throw err;
      // NOT `.catch(() => null)`. A lookup that FAILED has not answered "no
      // run", so it settles nothing and must not end the recovery -- treating
      // it as an answer abandons a generation that is still going, and that
      // confusion is the single defect this feature's first phase shipped
      // three times. Asked again; if the tries run out, the failure reported
      // is the one that actually interrupted the reader.
      return undefined;
    }
  }

  /** `moved`, with a failure to ask counted as a reason to try again. */
  private async mayHaveMoved(): Promise<boolean> {
    try {
      return await this.moved();
    } catch (why) {
      if (isAbortError(why)) throw why;
      // Could not ASK whether it moved. That settles nothing either way,
      // so it must not end the recovery -- spend another attempt on it.
      return true;
    }
  }

  /** Point `at` at whatever this scene is called now. True if it moved.
   *
   *  `scene-by-identity` is the lookup that exists for exactly this -- it is
   *  what the Android notification tap already resolves through, for the same
   *  reason.
   *
   *  **A 404 is an ANSWER; nothing else is.** No scene carries this identity
   *  any more, so it did not move -- it is gone, and the caller's own failure
   *  is the true one. Every other outcome (a 409 while the scan could not read
   *  every candidate, a dropped fetch) is a failure to ASK, and this rethrows
   *  so the caller spends another attempt rather than concluding no-move.
   *  Swallowing those into `false` is the same "a failed discovery is not an
   *  answer of no run" defect `lookup` warns about, and I wrote it here while
   *  quoting the rule there.
   */
  private async moved(): Promise<boolean> {
    if (!this.identity) return false;
    let now: string;
    try {
      now = (await sceneRunApi.sceneByIdentity(this.cid, this.identity, this.signal)).id;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return false;
      throw err;
    }
    if (now === this.at) return false;
    this.at = now;
    return true;
  }
}

/** The four image-description surfaces, which differ only in their path.
 *
 *  A world's three describe art hanging off one of its records and a campaign's
 *  describes its own library, so the subject differs too -- but that is the
 *  whole of it, and four copies of the same `draftRun` call is four places for
 *  the shape to drift.
 */
export function describeImage(scope: DraftScope, path: string,
                              signal?: AbortSignal): Promise<{ description: string }> {
  return draftRun<{ description: string }>(scope, (attempt) =>
    request<{ run: RunHandle }>("POST", path, undefined, { attempt, signal }),
    { signal });
}

/** An `EntityScope` as the subject its detached runs belong to. The two are
 *  the same distinction wearing different names -- a world or a campaign --
 *  and the anchor route is the one draft that is reachable in both scopes. */
export function draftScopeOf(scope: EntityScope): DraftScope {
  return scope.kind === "world"
    ? { at: "world", id: scope.id }
    : { at: "campaign", id: scope.id };
}

export const sceneRunApi = {
  /** Ask a detached run to stop.
   *
   *  Closing the connection is no longer the cancel. A turn now outlives the
   *  socket that started it -- which is the whole point -- so aborting the
   *  fetch only drops this subscriber: the provider keeps generating, keeps
   *  spending, and persists a reply the player pressed Stop on. The run has to
   *  be told. Best-effort by design: it is a request, not a guarantee, and the
   *  run ends when its provider call unwinds.
   */
  cancelRun: (cid: string, sid: string, runId: string) =>
    request<{ run: RunHandle }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/runs/${runId}/cancel`),

  /** The run this attempt id produced, if the server got that far.
   *
   *  How Stop finds its target when the leading `run` frame never arrived --
   *  the server accepted and detached the turn, and the response died before
   *  the first frame. Without this, that window has nothing to cancel and the
   *  provider runs on after the player stopped it. Answers `{run: null}` on a
   *  scene with no such run rather than 404, so "the send never landed" is an
   *  ordinary answer and not an error to handle.
   */
  /** Stop the turn an attempt id names, whether or not it has a run yet.
   *
   *  Discovery alone is a one-shot lookup, and the POST it is stopping may
   *  have been accepted and then blocked in the server's synchronous setup --
   *  so "no run for this attempt" does not mean nothing is going to happen:
   *  the route can still reserve and detach a turn after the lookup returns.
   *  Recording the cancel against the attempt is what closes that, because the
   *  reservation consumes it.
   */
  cancelAttempt: (cid: string, sid: string, attempt: string) =>
    request<{ run: RunHandle | null }>(
      "POST",
      // A QUERY parameter, because the server takes an attempt id verbatim and
      // one containing `/` cannot survive a path segment -- the router matches
      // on the decoded path, so it would split and reach no route at all.
      `/api/campaigns/${cid}/scenes/${sid}/attempt-cancel`
        + `?attempt=${encodeURIComponent(attempt)}`),

  /** Whether this attempt's post is still in the scene, plus its run if one
   *  is still known.
   *
   *  The durable question, and the only one left once the run record has been
   *  reaped. `retained: false` after a failure means the backend took the
   *  player's post back off the transcript -- so the words exist nowhere but
   *  in this client, and belong back in the composer.
   */
  /** The scene an identity names right now.
   *
   *  What a completion-notification tap resolves through. The intent carries
   *  the identity precisely because a `sid` goes stale on rename and a
   *  notification can sit unread for a long time.
   */
  /** Attach to a run's buffered frames from `from` onward, INCLUSIVE.
   *
   *  The half of detachment the client side rests on. The backend keeps every
   *  frame a run has produced, so a client that was away -- a locked phone, a
   *  suspended tab, a dropped socket -- reads the ones it missed and then keeps
   *  reading live until the run ends. Without this the turn survives on the
   *  server and the screen never shows it.
   *
   *  `from` is one past the last frame actually read, never the run's
   *  `next_index`: that is the live tail, and resuming from it drops everything
   *  generated while the client was away, which is the whole reply.
   */
  attachRun: (cid: string, sid: string, runId: string, from: number,
              onEvent: (e: ChatEvent) => void, signal?: AbortSignal,
              onIndex?: (i: number) => void) =>
    // A GET, so nothing here goes through `writing`: the run's terminal frame
    // is the only word this client gets that what it wrote has landed.
    streamGet(`/api/campaigns/${cid}/scenes/${sid}/runs/${runId}/stream?from=${from}`,
              settlingOn(onEvent), signal, onIndex),

  // The THIRD hop of the notification tap, after the two the Android shell
  // makes, and it needs the same treatment: `safe_id` permits characters that
  // are reserved in a URI, so a raw `cid` with a `?` or `#` in it turns the
  // rest of this path into a query or a fragment and the backend is asked
  // about a truncated campaign. `encodeSegment` rather than
  // `encodeURIComponent` for the path part -- it is what every other route
  // here uses, so a segment cannot be encoded two different ways.
  sceneByIdentity: (cid: string, identity: string, signal?: AbortSignal) =>
    request<{ id: string }>(
      "GET",
      `/api/campaigns/${encodeSegment(cid)}/scene-by-identity`
      + `?identity=${encodeURIComponent(identity)}`,
      undefined, { fresh: true, signal }),

  attemptState: (cid: string, sid: string, attempt: string) =>
    request<{ attempt: string; retained: boolean; run: RunHandle | null }>(
      "GET",
      `/api/campaigns/${cid}/scenes/${sid}/attempt-state`
        + `?attempt=${encodeURIComponent(attempt)}`,
      undefined, { fresh: true }),

  // `attempt` is optional, and the difference matters. WITH one this asks
  // "what became of my send?"; WITHOUT one the route answers with the scene's
  // newest run, which is the only question a client that has lost its local
  // state can ask -- a full reload, or the Android WebView's renderer being
  // restarted, leaves the provider empty while the backend turn generates on.
  findRun: (cid: string, sid: string, attempt?: string, signal?: AbortSignal) =>
    request<{ run: RunHandle | null }>(
      "GET",
      `/api/campaigns/${cid}/scenes/${sid}/run`
      + (attempt ? `?attempt=${encodeURIComponent(attempt)}` : ""),
      undefined, { fresh: true, signal }),
};
