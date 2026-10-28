import type { ProposalRecord } from "../../api/client";
import {
  api
} from "../../api/client";
import { STOP_POLLS } from "./campaignShared";
import type { Current, SceneKey } from "./campaignTypes";
import type { RunSession } from "./useRunSession";
import type { TranscriptRead } from "./useTranscriptWindow";
export type ProposalReads = {
  claimProposalRead: () => number;
  applyProposalRead: (claim: number, record: ProposalRecord | null) => void;
};
export type FlushCapabilities = ProposalReads & {
  session: Pick<RunSession, "mounted" | "observerActive">;
  selected: () => SceneKey | null;
  readTranscript: TranscriptRead;
};

// How long to keep looking for a cancelled turn's partial. Aborting rejects
// the fetch the instant the socket is torn down here, but the backend only
// then notices the disconnect and runs its shielded flush — so the refresh
// that follows a Stop can legitimately read a transcript the partial has not
// reached yet, and the text would sit on disk while the screen denied it
// existed until some later refresh.
//
// The budget has to clear the backend's own cancellation grace, which review
// caught an earlier flat 1.5s falling short of: `llm._settle` gives an
// unresponsive provider up to `_CLOSE_TIMEOUT` (5s) to unwind and `_aclose`
// another 5s, and on the common path — cancellation arriving while waiting on
// the next delta — that unwinding happens *before* the write. Hence a
// doubling delay: the ordinary case (a provider that lets go at once) still
// resolves on the first retry, and the tail covers the documented worst case
// instead of giving up in the middle of it. Still bounded, because past this
// the write is contending on the store lock, which no amount of polling here
// will shorten, and the player's next action refreshes anyway.
const FLUSH_POLL_MS = 250;
const FLUSH_POLL_MAX_MS = 2000;
const FLUSH_POLL_BUDGET_MS = 12000;

export async function pollFlush(scope: SceneKey, seen: number, capabilities: FlushCapabilities) {
  const { session, selected, claimProposalRead, applyProposalRead, readTranscript } = capabilities;
  const { cid, sid: id } = scope;
  // Stop the moment this poll stops being the thing that owns the view. A new
  // turn (abortRef) or a different scene (selected) has its own refresh,
  // and carrying on would have `readTranscript` clear the new stream's live
  // preview — or, worse, yank the player back to the scene they just left.
  // Refs, not state: this loop runs across renders and a captured
  // `busy`/`activeId` would be stale.
  //
  // Handed to `readTranscript` as well as checked here, because checking only
  // here leaves a window review caught: the check passes, then `getScene` is
  // awaited, and a turn starting during that await still gets its preview
  // cleared by the response.
  const owns = () => {
    if (!session.mounted() || session.observerActive()) return false;
    const current = selected();
    return current?.cid === cid && current.sid === id;
  };
  let wait = FLUSH_POLL_MS;
  let waited = 0;
  // The length the screen was last refreshed to, so a tick can tell a
  // transcript that moved some other way than growing from one that has not
  // moved at all (below).
  let onScreen = seen;
  while (waited < FLUSH_POLL_BUDGET_MS) {
    await new Promise((r) => setTimeout(r, wait));
    waited += wait;
    wait = Math.min(wait * 2, FLUSH_POLL_MAX_MS);
    if (!owns()) return;
    // What a tick asks is two questions, not a scene. Each used to be a whole
    // `selectScene` -- the transcript window, the cast, the roster, the
    // briefing, the column's reads, a dozen requests -- and a Stop that
    // waited out the budget cost eight of them to learn one number. The
    // transcript's LENGTH is what says the flush landed, so that is what is
    // polled, and the full refresh runs once, on the tick that sees it grow.
    //
    // The proposal is the other question, and it is still asked every tick,
    // because it is the one flush that need not grow the transcript: a reply
    // that was nothing but a roll fence persists a proposal and no post.
    // Fired and not awaited, exactly as `selectScene` fires it, and claimed
    // BEFORE the length read, so the settling read after growth outranks it
    // however late it lands.
    const claim = claimProposalRead();
    api.getRollProposal(cid, id)
      .then((r) => { if (owns()) applyProposalRead(claim, r.record); })
      .catch(() => { });
    // A read that fails is a tick that learned nothing, not the end of the
    // wait: the flush this is watching for happens on the server whether or
    // not one GET made it there, and throwing here would escape a `finally`
    // (review, #95). Keep polling until the budget runs out.
    const n = await api.getScene(cid, id, { limit: 1 })
      .then((s) => s.total ?? s.messages.length)
      .catch(() => -1);
    if (!owns()) return;
    const grew = n > seen;
    // Growth is what this waits for, but not the only thing the server can do
    // meanwhile: a detached turn that fails after its connection has gone
    // rolls its post back, and the transcript SHRINKS. The per-tick refresh
    // this replaced put that on screen as a side effect; without it the post
    // stayed, still offering Edit and Cut against an index the file no longer
    // has. So a length that moved without growing is re-read once, and the
    // wait goes on.
    if (grew || (n >= 0 && n !== onScreen)) {
      const shown = await readTranscript(scope, { stillWanted: owns }).catch(() => -1);
      if (!owns()) return;
      // A refresh that failed has not put the partial on screen, so the wait
      // goes on and the next tick tries again, as it always did.
      if (shown < 0) continue;
      onScreen = shown;
      // The flush can land between the length read and this refresh.
      // Observed growth also settles even if the refresh then sees a shrink.
      if (grew || shown > seen) return void await settleProposal(scope, owns, { claimProposalRead, applyProposalRead });
    }
  }
}

// The last word on the proposal, once the transcript proves the flush is done.
//
// `selectScene` fires its proposal fetch and awaits only `getScene`, so on the
// tick that catches the flush the two requests raced it independently — and
// `finalize` writes the proposal *before* the narration, so the read that saw
// the transcript grow is the one guaranteed to be after both writes, while the
// proposal read beside it may have landed before either. Its late `null` then
// clears a chip that does exist, and the poll has already stopped looking:
// a roll the player has to answer, invisible until something else refreshes.
// One awaited read, after growth, settles it (review, #95).
// Reaching the server is load-bearing here and is now the endpoint's own
// guarantee: `getRollProposal` never coalesces, so this cannot be handed the
// promise `selectScene` started before the flush — the stale answer it exists
// to overrule — and its claim, being newer, genuinely describes a newer read.
export async function settleProposal(scope: SceneKey, owns: Current,
  { claimProposalRead, applyProposalRead }: ProposalReads) {
  const { cid, sid: id } = scope;
  const claim = claimProposalRead();   // issued after selectScene's, so it wins
  const r = await api.getRollProposal(cid, id).catch(() => null);
  if (r && owns()) applyProposalRead(claim, r.record);
}

export async function stopRun(
  handle: { cid: string; sid: string; attempt: string; id: string | null },
) {
  // Terminal acknowledgement is the release signal. A run that landed just
  // before the press already has that answer; an unreachable or still-running
  // turn must reject so its caller keeps the scene locked.
  const { cid: from, sid, attempt } = handle;
  try {
    // No leading frame yet: cancel by ATTEMPT rather than looking the run up.
    // A lookup is a one-shot question, and the POST being stopped may have
    // been accepted and then blocked in the server's setup -- so "no run for
    // this attempt" does not mean nothing will happen, and the reservation
    // that follows would detach a turn the player had already stopped. The
    // attempt route is remembered by the server and consumed when the
    // reservation happens, which is the only version of this without a race.
    // BY ID WHEN THERE IS ONE, on every round and not just the first. An
    // adopted run is recorded with `attempt: ""` -- the provider was
    // recreated empty, so this client never knew the id the backend reserved
    // under -- and re-asking by that empty attempt matches nothing, so every
    // poll answered null and a cancellation that was completing normally was
    // reported unconfirmed. The id is the better handle wherever it exists;
    // the attempt is what covers the window before any frame named one.
    const ask = () => (handle.id
      ? api.cancelRun(from, sid, handle.id)
      : api.cancelAttempt(from, sid, attempt));
    let state = (await ask()).run?.state;
    // The server's wait is bounded (30s) and can answer while the run is
    // still unwinding. Taking that as "it is over" is what re-enabled Send
    // over a run that still held the scene, which is the whole failure this
    // wait exists to prevent -- so ask again rather than assume.
    //
    // NO RUN AT ALL IS THE SAME ANSWER, and review caught that it was read
    // as the opposite. A precancel recorded before the POST reserved returns
    // `run: null`, which is not "there is nothing to stop" -- it is
    // "cancellation is pending": the original request is still in its
    // synchronous setup and will consume that record when it reserves.
    // Resolving on it settled the attempt and unlocked the composer while
    // that was still to happen, so the next Send raced the very request it
    // had just cancelled.
    //
    // A short wait between rounds only on that branch: an unwinding run is
    // already being waited on server-side, but a pending one needs the other
    // request to get somewhere first, and four immediate re-asks would all
    // arrive before it did.
    for (let tries = 0;
      (state === "running" || state === undefined) && tries < STOP_POLLS;
      tries++) {
      if (state === undefined) await new Promise((r) => setTimeout(r, 100));
      state = (await ask()).run?.state;
    }
    if (state === "running" || state === undefined) {
      // Out of rounds with the run still going, or still not reserved.
      // Resolving here would settle the turn on a guess: the backend still
      // holds the scene (or is about to), so Send comes back and the next
      // turn is refused. Exhaustion is an UNCONFIRMED cancellation, which is
      // the same answer as one that never arrived -- and it takes the same
      // path below.
      throw new Error("the run did not stop");
    }
  } catch {
    // The cancel never reached the server -- connectivity dropped as the
    // phone was backgrounded, most likely. Swallowing this and settling would
    // be the worst answer available: aborting the subscriber does NOT stop a
    // detached run, so the provider keeps spending, persists a reply the
    // player stopped, and holds the scene against the next send. Re-thrown so
    // the teardown leaves the turn unsettled and the composer keeps saying
    // Stop, which is the truth: it has not been stopped yet.
    throw new Error("the stop request could not be delivered");
  }
}
