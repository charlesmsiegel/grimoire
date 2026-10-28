import { ApiError } from "../../api/client";
import { isAbortError } from "../../api/stream";
import type { ProposalRecord } from "../../api/client";
import type { RunRegistry } from "../../runs/RunRegistryProvider";
import type { TurnRequest } from "./campaignTypes";
import { createStreamFrames } from "./streamFrames";
import type { RunSession } from "./useRunSession";

/** Independent facts: a done frame may be followed by a socket abort. */
export type TurnObservation = {
  finished: boolean;
  errored: boolean;
  proposed: boolean;
  beforeResponse: boolean;
  refused: boolean;
};

type StreamCapabilities = {
  session: Pick<RunSession, "renderFrame" | "resetParts" | "confirmRun" | "handle">;
  registry: Pick<RunRegistry, "attach" | "consume">;
  fail: (err: unknown, retryable?: boolean, from?: string) => void;
  setProposalNow: (record: ProposalRecord | null) => void;
  providerSucceeded: () => void;
};

// `rerolling` decides what a failure OFFERS, and it turns on whether any
// narration arrived — the two reroll failures want opposite things:
//
// - some narration, then the error. The backend persisted that partial, so
//   the slot is FULL. Retry would append a second generation, move the slot
//   and retire the set the reroll was building. The transcript still ends on
//   an assistant post, so the gutter's own ↻ is there and replaces in place;
//   the banner offers nothing.
// - nothing at all. The backend archived and removed the old reply, so the
//   slot is EMPTY and the transcript ends on the player's post — `canReroll`
//   is false and there is no ↻ to fall back on. `/retry` streams straight
//   into the empty slot, so Retry is both safe and the only way forward.
//
// Trimmed, matching `streaming.py`'s `watcher.narration.strip()`: deltas that
// are only whitespace persist nothing, so that slot is empty too, and reading
// them as a landed partial takes away the one button that refills it.
// The focused result is updated in place as well as returned: the caller's
// finally needs received facts even if a reporting callback unexpectedly throws.
export async function observeTurnStream(
  { scope: { cid, sid: id }, start, recoverText: onPromptUnstored, ephemeral, rerolling }: Pick<
    TurnRequest, "scope" | "start" | "recoverText" | "ephemeral" | "rerolling">,
  { signal, attempt }: { signal: AbortSignal; attempt: string }, observed: TurnObservation,
  { session, registry, fail, setProposalNow, providerSucceeded }: StreamCapabilities,
): Promise<TurnObservation> {
  const frames = createStreamFrames(session.renderFrame);
  session.resetParts();
  // Evaluate at failure time: narration can arrive between request and error.
  const retryable = () => !ephemeral && !(rerolling && frames.text().trim().length > 0);
  // Three separate questions, and none of them is "did the promise resolve".
  // `finished`: a `done` frame arrived, which the backend sends only after
  // finalize has persisted. `errored`: an error frame arrived, so the backend
  // ran its own handler before sending it. Review caught that a proxy closing
  // the body cleanly before `done` resolves `streamPost` normally, which used
  // to count as a landed turn while the reply was still in the flush.
  // Whether this stream answered with a roll proposal rather than narration.
  // A director turn that did is a turn whose note was SPENT, even though the
  // transcript did not grow -- see the recovery condition below.
  // The server answered with a status instead of a stream. A fourth question,
  // because it is the one outcome that is *complete* on arrival: `streamPost`
  // throws a non-2xx before any body exists, so there is no stream that was
  // cut short, no partial in flight and nothing for the flush poll to wait on.
  try {
    await start((e) => {
      if (frames.accept(e)) { /* preview frame */ } else if (e.run) {
        // The leading frame confirms the run id, so Stop can skip the
        // discovery round trip. It is a shortcut, not the mechanism: the
        // attempt id above already names this turn.
        session.confirmRun(attempt, e.run.id);
        registry.attach(cid, id, e.run.id);
      } else if (e.error) {
        fail(e.error, retryable(), "scene");
        observed.errored = true;
        // The post is gone from the transcript, so the composer has to give
        // the player their words back. Otherwise a failed send destroys what
        // they typed and Retry cannot help — it calls /retry, which has no
        // prompt of its own and 400s on a scene with nothing else in it (#95).
        if (e.error.post_returned) onPromptUnstored?.();
      } else if (e.proposal) {
        // Live from the stream, so it outranks any read still in flight.
        observed.proposed = true;
        setProposalNow({ id: e.proposal.id, status: "pending", payload: e.proposal, resolution: null });
      } else if (e.done) {
        observed.finished = true;
        // A turn that worked is as much news about the connection as one
        // that failed, and only in one direction does the shell find out on
        // its own: `fail` announces, so a Retry that succeeds without leaving
        // the page would otherwise leave the dot red until an unrelated
        // navigation. Gated on having reported a provider failure, so an
        // ordinary turn does not re-read the config every time (#146).
        providerSucceeded();
      }
    }, signal, attempt, (index) => {
      // The WIRE index, so a resume asks for one past what was really read.
      // Counting the events this callback's sibling surfaced would undercount
      // by every heartbeat and replay text mid-reply.
      const runId = session.handle()?.id;
      if (runId) registry.consume(runId, index);
    });
  } catch (err: unknown) {
    // A cancel is the user getting what they asked for, so it raises no error
    // banner. `done` is parsed before the body reports EOF and Stop stays live
    // until it does, so a press in that gap aborts a turn that is already
    // written — `finished` below is what keeps that from being refunded as a
    // cancellation and spending its response override twice (#95).
    //
    // `fail`, not `setError`: a failure carries whether GENERATING is the
    // recovery it wants, and a reroll that already landed a partial does not
    // want one — Retry would append past the reply it just parked.
    // `!ephemeral`, for the reason the other `retryable: false` sites give
    // (Codex review, round 3): the banner's Retry GENERATES, down `/retry`,
    // which regenerates from the stored transcript. A director note is in no
    // transcript, so Retry would quietly produce an undirected continuation
    // — or, in a scene with nothing else, fail with "nothing to retry". The
    // note is back in the composer by then, and Send is its retry.
    if (!isAbortError(err)) fail(err, retryable(), "scene");
    // A failure before the response may leave nothing stored — the same
    // position as a rollback, and the same remedy. Review caught
    // that Stop pressed during connection setup, or a server that is simply
    // down, lost the prompt exactly as the rollback did: the composer was
    // cleared, and the refresh below finds no post to restore it from. The
    // response is the line, because `post_chat` appends before returning one.
    // Deliberately not restoring here. `beforeResponse` says no response
    // arrived, which is not the same as the request never arriving: the
    // server can append the post and then have the abort beat its headers
    // back to the browser. Deciding now would put text in the composer that
    // is also in the transcript, and the next Send would duplicate it. The
    // refresh below settles it (review, #95).
    if (typeof err === "object" && err !== null && "beforeResponse" in err && err.beforeResponse) observed.beforeResponse = true;
    // A refusal reaches the server but may still leave nothing behind: every
    // 4xx `post_chat` raises comes from a check that runs *before* the post is
    // appended, so the prompt was cleared out of a composer and stored
    // nowhere — the same position a rollback leaves the player in. Not
    // assumed, though: a 500 out of `build_messages` lands here too, and that
    // one raises *after* the append. The transcript settles which, exactly as
    // it does for `beforeResponse` (review, #95).
    else if (err instanceof ApiError) observed.refused = true;
  }
  return observed;
}
