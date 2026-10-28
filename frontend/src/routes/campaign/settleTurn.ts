import type { Dispatch, SetStateAction } from "react";
import { errorText } from "../../api/errors";
import type { PlayError } from "../../components/play/PlayBanners";
import { findOrFenceAttempt } from "../../runs/recoverAttempt";
import type { RunRegistry } from "../../runs/RunRegistryProvider";
import { attachObservedRun, type AttachmentCapabilities } from "./attachObservedRun";
import type { TurnRequest } from "./campaignTypes";
import type { TurnObservation } from "./observeTurnStream";
import { pollFlush, type FlushCapabilities } from "./runSettlement";
import { turnRecoveryPolicy } from "./turnRecoveryPolicy";
import type { RunSession } from "./useRunSession";
import type { TranscriptRead } from "./useTranscriptWindow";

type OwnershipCapabilities = {
  session: Pick<RunSession, "clearObserver" | "takeCancellation" | "observerActive" | "turnToken">;
  registry: Pick<RunRegistry, "settle" | "pending">;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  attachment: AttachmentCapabilities;
};

type SettlementRequest = Pick<TurnRequest, "scope" | "ephemeral" | "rerolling" | "recoverText">;

/** Resolve ownership and begin release in the same continuation. Returning an
 * ownership decision to an awaiting caller would let visibility attachment win
 * between the final witness check and destructive release (#95). */
export async function settleTurn(
  request: SettlementRequest, evidence: SettlementEvidence & { attempt: string },
  { session, registry, setError, attachment, readTranscript, settlement }:
    OwnershipCapabilities & ReleasedTurnCapabilities,
): Promise<boolean> {
  const { scope: { cid, sid: id } } = request;
  const { attempt, token: streamToken, stream: observed } = evidence;
  // The outcome is established, however it ended: a `done` frame, an error
  // frame the backend chose to send, or a refusal. Recovery is for the
  // sends whose outcome nobody ever learned, so anything that got this far
  // with an answer stops being one of those. A stream that was cut without
  // an answer deliberately does NOT settle -- that is the case the registry
  // exists for, and it is left for the adoption pass to resolve.
  const answered = observed.finished || observed.errored || observed.refused;
  if (answered) registry.settle(cid, id);
  // The socket is gone either way. Leaving `abortRef` set would block the
  // adoption pass which resolves this if the run ends on its own. An
  // unconfirmed Stop retains the composer, preview and handle below, so Stop
  // is still there and still knows what to address.
  session.clearObserver();
  // AHEAD OF EVERY RELEASE BELOW, `busy` included. Only a Stop sets this,
  // so an ordinary turn is unaffected and keeps releasing as early as it
  // always has. On a Stop it is the difference between the button lying and
  // not: the cancel route answers only once the run is really terminal --
  // after its abort hook has written the partial and freed the exclusion
  // key -- and `send` gates on `busy` alone, so clearing that first let the
  // player fire a turn the backend then refused with `run_in_flight`. The
  // streamed text stays on screen for the wait, which is the honest picture:
  // it is still being stopped.

  const pending = session.takeCancellation();
  if (pending) {
    // An unconfirmed cancel must not read as a finished one: `stopRun`
    // rethrows when it could not reach the server, and settling anyway
    // would put Send back over a run that is still generating. Reported,
    // not swallowed -- the player pressed a button and it did not work.
    try {
      await pending;
      // A CONFIRMED stop is an established outcome. The player asked for
      // it and the server said the run is terminal, so there is nothing
      // for recovery to resolve -- and leaving it pending would have the
      // adoption pass re-litigate a turn the player already ended.
      registry.settle(cid, id);
    } catch {
      // The player pressed a button and it did not work. Reported,
      // not swallowed -- and NOT released: the backend run may still
      // be generating and still holding the scene.
      setError({
        text: "could not reach the server to stop this turn — it "
          + "may still be generating", retryable: false
      });
      return false;
    }
  }
  if (!pending && !answered) {
    // THE STREAM DIED WITH NO ANSWER, AND THIS TAB IS STILL HERE. Mount and
    // `visibilitychange` are the only adoption triggers, and neither fires in
    // this case: the component stayed mounted and visible while the response
    // reset before its leading frame. The server accepted the POST and a run
    // is generating -- so both of the answers below are wrong. Treating it as
    // a failure restores the prompt and the player sends again, landing the
    // turn twice against a run that was always fine; leaving it locked strands
    // the scene until a remount.
    //
    // So ask, by attempt, before concluding anything. A run means attach to
    // it and skip the whole teardown, because the turn is still ours.
    // `abortRef` is already cleared above, which is what lets `attachToRun`
    // take the view back.
    //
    // And a lookup that FAILED is not an answer of "no run". The socket
    // dying and this request failing are the same outage, so of every call
    // in this file these two are the likeliest to fail together -- and
    // reading that as "no run" runs the teardown, unlocks the composer, and
    // lets the next Send overwrite the registry's one pending entry for this
    // scene while the original may still be generating. Held instead, like
    // an unconfirmed Stop: the composer stays locked, the send stays
    // pending, and `adoptPendingRun` resolves it when the tab next comes
    // forward.
    try {
      const live = await findOrFenceAttempt(cid, id, attempt);
      // Clearing the old abortRef allows visibility recovery to take over
      // during this request. Its observer or a newly submitted attempt now
      // owns the view; this continuation must neither attach again nor
      // tear down that owner's preview, handle or durable words.
      const superseded = session.observerActive() || session.turnToken() !== streamToken
        || registry.pending(cid, id)?.attempt !== attempt;
      if (superseded) return false;
      if (live && live.state === "running") {
        // Attachment now owns the turn. Its asynchronous failures are reported
        // below; this continuation never performs released-turn settlement.
        attachObservedRun({ cid, sid: id }, live.id, attachment)
          .catch((err: unknown) => setError({ text: errorText(err), retryable: false }));
        return false;
      }
    } catch {
      return false;
    }
  }
  // No await between discovery's ownership check and these initial releases.
  // settleReleasedTurn runs its handle/preview/composer mutations synchronously
  // before its first transcript-read await, preserving the original ordering.
  return settleReleasedTurn(request, evidence, { session, setError, readTranscript, settlement });
}

type ReleasedTurnCapabilities = {
  session: Pick<RunSession, "forgetHandle" | "clearPreview" | "releaseComposer"
    | "observerActive" | "turnToken" | "releaseSceneLock">;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  readTranscript: TranscriptRead;
  settlement: FlushCapabilities;
};

type SettlementEvidence = {
  stream: TurnObservation;
  totalBefore: number | null;
  token: number;
};

/** Refresh, restore undurable words, and wait for shielded backend writes. */
async function settleReleasedTurn(
  { scope, ephemeral, rerolling, recoverText: onPromptUnstored }: SettlementRequest,
  { stream: observed, totalBefore, token: streamToken }: SettlementEvidence,
  { session, setError, readTranscript, settlement }: ReleasedTurnCapabilities,
): Promise<boolean> {
  // Beside `abortRef`, and load-bearing for the same reason: left set, a
  // Stop pressed while the NEXT turn is still connecting would address this
  // one -- terminal by then, so the route would answer politely and the
  // live turn would keep generating with nothing to stop it.
  session.forgetHandle();
  session.clearPreview();
  session.releaseComposer();
  // NOT released with `busy`. Review caught that clearing it here unlocks
  // the scene while `on_abort` may still be writing to it: the poll below
  // exists precisely because the backend's shielded flush lands seconds
  // after the socket died, and a rename in that gap moves the file out from
  // under the very write the poll is waiting for. The lock is released at
  // the bottom of this block instead, once there is nothing left to land.
  // the reply is persisted as per-speaker posts — re-fetch to show them
  // (selectScene also bumps ctxKey and refreshes the player name)
  //
  // Guarded, because `setBusy(false)` above already re-enabled Send and this
  // fetch is an await: the player can start the next turn while it is in
  // flight, and the stale response would then apply `setMessages` over that
  // turn's optimistic post and `setStreaming("")` over its live preview.
  // Review caught this one on the immediate refresh after it had already
  // been fixed on the polling ones — the mechanism was sitting right here.
  //
  // Only `abortRef`, unlike the poll's predicate — and deliberately so,
  // even though it means this refresh can install the turn's scene over
  // one the reader has since opened. That pull-back is load-bearing: a
  // turn that failed parks the player's words under ITS scene, and the
  // composer is one shared box, so the view has to return to that scene
  // or the recovered prompt is either invisible or shown against the
  // wrong transcript ("a recovered prompt is never shown against the
  // scene the player moved to").
  //
  // Adding a scene check here was tried and reverted: it silently broke
  // prompt recovery, which is the one thing in this file that guards text
  // existing nowhere else. The divergence it would have prevented is
  // instead made harmless where it does damage — `saveEdit` refuses to
  // write while the transcript on screen belongs to another scene.
  //
  // Its failure is caught rather than thrown, because it is the *same*
  // failure the turn just had: a POST that never reached the server usually
  // means this GET will not either. Letting it escape from a `finally`
  // skipped the restoration below and replaced the original error on the
  // way out, so the one case that most needs the player's words back was
  // the one case that dropped them (review, #95).
  let seen = -1;
  let refreshed = false;
  try {
    seen = await readTranscript(scope, { stillWanted: () => !session.observerActive() });
    refreshed = true;
  } catch (err: unknown) {
    // Keep whatever the turn itself reported; say something if it reported
    // nothing, since the view is now showing a transcript it could not
    // confirm (a cancel raises no banner of its own).
    // Built here rather than through `fail`, which overwrites: the rule is
    // to keep whatever the turn reported. Retryable — this is the failure
    // path of a *generation*, so generating again is the right recovery.
    setError((cur) => cur ?? { text: errorText(err), retryable: true });
  }
  const recovery = turnRecoveryPolicy(observed, { ephemeral, rerolling, totalBefore, refreshed, seen });
  if (recovery.restorePrompt) onPromptUnstored?.();
  if (recovery.poll) await pollFlush(scope, recovery.baseline, settlement);
  // Now nothing else can write to this scene through this turn, so its file
  // is free to move again — unless a newer turn has claimed the lock in the
  // meantime. `busy` was cleared before the poll, so that is a real race and
  // clearing unconditionally would unlock the scene the *new* turn is
  // streaming into. Same token idiom as `windowTokenRef`.
  if (session.turnToken() === streamToken) session.releaseSceneLock();
  // NO `askAfterPost` HERE, and its absence is the point (#397). The
  // rolling-summary fold and the scene-break question used to be fired
  // from exactly this line, once the streaming promise settled — which is
  // precisely the thing that does not happen in the case detached turns
  // exist for. The phone locks, this JavaScript is suspended or the page
  // is gone, the turn lands server-side, and nothing here is left to ask:
  // the summary falls behind by however many turns were taken with the
  // app backgrounded, and the scene-break prompt never appears.
  //
  // So the backend schedules both itself, when the turn's terminal write
  // finishes (`streaming._fire_follow_up` → `scenes.schedule_follow_ups`),
  // with a boundary it reads under the lock that write held — strictly
  // better than the `seen` this side could offer, which is a read taken
  // after the scene was already released. Asking from here as well would
  // simply run each of them twice.
  //
  // Every other transcript write in this component still asks for itself,
  // and correctly so: an edit, a cut, a retcon, a roll and a check are all
  // requests the player is waiting on, so there is a client here by
  // construction. It is only the turn whose end this side can miss.
  return recovery.producedNothing;
}
