import {
  api
} from "../../api/client";
import { findOrFenceAttempt } from "../../runs/recoverAttempt";
import type { RunRegistry } from "../../runs/RunRegistryProvider";
import { attachObservedRun, type AttachmentCapabilities } from "./attachObservedRun";
import { useStableCallback } from "./campaignShared";
import type { RunSession } from "./useRunSession";
import type { TranscriptRead } from "./useTranscriptWindow";

/** Resolve a send this client made and never heard the end of.
 *
 *  Runs on mount and whenever the tab becomes visible, which are the two
 *  moments a phone comes back. Four outcomes, and the fourth is the one an
 *  implementation forgets:
 *
 *  - **live** -- the turn is still generating. Attach to its buffer from one
 *    past the last frame we actually read and render the rest.
 *  - **terminal** -- it finished while we were away. Refetch the scene; the
 *    reply is in the transcript.
 *  - **gone** -- it finished, nobody attached, and the retention window
 *    passed. Indistinguishable from the above by looking at the registry, and
 *    surfacing the 404 would report a lost turn that landed perfectly.
 *  - **rolled back** -- it FAILED after the post was appended, the backend
 *    took the post off, and the record expired. The refetched transcript is
 *    correctly missing the post, so settling would discard the only surviving
 *    copy of what the player typed. `attempt-state` is what tells these last
 *    two apart, and where it cannot, the text is kept.
 */
export function useRunRecovery({ registry, session, attachment, recoverPrompt, readTranscript, fail }: {
  registry: RunRegistry;
  session: Pick<RunSession, "turnToken" | "observerActive" | "forgetHandle" | "releaseSceneLock" | "clearPreview" | "releaseComposer" | "lockPending">;
  attachment: AttachmentCapabilities;
  recoverPrompt: (pcid: string, sid: string, text: string, director?: boolean) => void;
  readTranscript: TranscriptRead;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
}) {

  const adoptPendingRun = useStableCallback(async (cid: string, sid: string) => {
    const held = registry.pending(cid, sid);
    const observedTurn = session.turnToken();
    const ownsObservation = () => !session.observerActive()
      && session.turnToken() === observedTurn;
    // NO HELD ATTEMPT IS NOT NOTHING TO DO. The provider is recreated empty by
    // a full reload -- and on Android the WebView's renderer can be restarted
    // out from under a perfectly healthy backend turn, which is precisely the
    // scenario this feature exists for. Returning here left that view showing
    // an unlocked composer over a stale transcript, and the next send refused
    // with `run_in_flight` by the run it could not see.
    //
    // The attemptless lookup is a different question -- "what is the newest
    // run on this scene?" -- and only its LIVE answer is acted on. A terminal
    // one belongs to a turn some other client owns the words for; there is
    // nothing here to restore and nothing to settle.
    if (!held) {
      if (session.observerActive()) return;
      let live;
      try {
        live = (await api.findRun(cid, sid)).run;
      } catch {
        return;                 // a failed lookup is not an answer; ask again later
      }
      if (!ownsObservation() || registry.pending(cid, sid)) return;
      if (live && live.state === "running") {
        // Only a TURN has frames to read. A `review` run is live work on this
        // scene too -- an absorb the reader started before the phone locked --
        // but its value is a payload the review panel fetches, not a stream,
        // and attaching to its (empty, never-`done`) buffer would leave this
        // view showing an endless empty reply over a scene that is being
        // absorbed perfectly well. It is left alone rather than released:
        // `useSceneReview` adopts it, and releasing here would unlock a
        // composer the server is refusing anyway.
        //
        // Every class the server names that is not a turn, rather than the one
        // that existed when this was written. A landed turn now schedules its
        // own rolling-summary and scene-break work (#397), and those are
        // `background` runs on this same scene -- so "not a review" stopped
        // being the same question as "has frames". A run with no class at all
        // still attaches, because `cls` is optional on the wire and the only
        // thing that ever omitted it was a turn.
        //
        // Belt and braces: the backend keeps both classes out of an attemptless
        // discovery in the first place (`runs.ATTACHABLE`).
        if (live.cls && live.cls !== "turn") return;
        await attachObservedRun({ cid, sid }, live.id, attachment);
        return;
      }
      // NOTHING IS RUNNING HERE, and that is a real answer -- so it is also the
      // release. A reattach that failed deliberately leaves this view locked
      // (see `attachToRun`'s catch), and with no pending attempt there is no
      // other pass that would ever let go. Unconditional because the ordinary
      // case has nothing to release and setting state to what it already is
      // costs nothing.
      session.forgetHandle();
      session.releaseSceneLock();
      session.clearPreview();
      session.releaseComposer();
      return;
    }
    // Not while this component is already driving a turn. Adoption is for a
    // send we LOST -- the socket died, the tab was suspended, the view
    // unmounted -- and running it alongside a live stream has two of them
    // rendering into one scene, refetching over each other and racing to
    // settle. `abortRef` is set for exactly as long as this view owns a
    // stream, which is the question being asked.
    if (session.observerActive()) return;
    // A reload has no component-local busy state, but this pending send still
    // owns the scene. Lock before the first await so an unavailable lookup or
    // fence cannot expose Send over work whose outcome remains unknown.
    session.lockPending({ cid, sid, attempt: held.attempt, id: held.runId });
    // Visibility can start another recovery while this pass awaits a request.
    // That pass may resolve this attempt and let the player send again. Every
    // continuation must still own this attempt and have no active observer;
    // otherwise settling by scene would erase the new send's durable words.
    const current = () => ownsObservation()
      && registry.pending(cid, sid)?.attempt === held.attempt;
    // By ATTEMPT, not by the run id we may or may not have seen. The id is a
    // shortcut; the attempt is what names this send in the window where no
    // frame ever arrived, which is the window worth recovering from.
    //
    // A TRANSPORT FAILURE IS NOT AN ANSWER. `visibilitychange` fires the
    // instant the tab comes forward, which is routinely before connectivity is
    // actually back -- so this request is more likely to fail here than
    // anywhere else in the app. Folding that into "no run" and carrying on
    // discards the only handle to a chat that may still be generating, and
    // then hands back a prompt that is sitting in the transcript, inviting the
    // player to send it twice. Left pending instead: the next visibility
    // change, or the next mount, asks again.
    let handle;
    try {
      handle = await findOrFenceAttempt(cid, sid, held.attempt);
    } catch {
      return;
    }
    if (!current()) return;
    if (handle && handle.state === "running") {
      // STILL GENERATING, and this is the case the whole feature is for. Read
      // the frames produced while we were away and keep reading live -- the
      // backend buffered every one of them. Returning here instead (which an
      // earlier revision of this function did, under a comment claiming
      // otherwise) leaves the turn running perfectly on the server and the
      // screen showing nothing until a manual reload.
      await attachObservedRun({ cid, sid }, handle.id, attachment);
      return;
    }
    // Either it finished, or its record is gone. Ask the durable question --
    // and, for the reason above, only act on an ANSWER. A failed request here
    // used to read as `retained: false`, which restores a prompt that may
    // still be in the transcript AND settles the attempt, so the second
    // recovery pass that would have got it right never runs.
    let durable: boolean;
    try {
      const state = await api.attemptState(cid, sid, held.attempt);
      if (!current()) return;
      // Execution and post presence answer different questions. Preserve the
      // later execution evidence too, rather than settling a still-live send
      // merely because its player post is already durable.
      if (state.run?.state === "running") {
        await attachObservedRun({ cid, sid }, state.run.id, attachment);
        return;
      }
      handle = state.run ?? handle;
      durable = state.retained;
    } catch {
      return;
    }
    // SETTLED only when the words are safe somewhere else. A post still in the
    // transcript is durable and this entry has nothing left to protect; a
    // rolled-back one leaves this as the only copy, and settling it would move
    // that copy into component state -- which is precisely what a reload or an
    // Android renderer restart destroys, the event the record exists to
    // survive. `recovered` keeps it, flagged, so recovery stops asking about a
    // run that is over while the text stays durable until a later send.
    if (durable) registry.settle(cid, sid);
    else registry.recovered(cid, sid);
    if (!durable && held.text) {
      // The post is not in the scene. Either it was rolled back or it never
      // landed -- and both mean the player's words exist only here. Ambiguity
      // resolves toward the recoverable error every time: wrong this way costs
      // one duplicate they can see and delete, wrong the other way costs them
      // their words with no trace at all.
      // Return the words before unlocking. A new Send replaces the registry
      // entry, so delaying this behind a refresh could lose or mix the words
      // while the player is already composing the next turn.
      recoverPrompt(cid, sid, held.text);
    }
    // Whatever this component was left holding for that send is resolved now.
    // An unconfirmed Stop deliberately leaves the composer locked and `runRef`
    // set (see `runStream`'s finally), on the grounds that the run may still be
    // generating; this is the pass that finally answers that question, so it
    // has to be the pass that lets go. Unconditional because the ordinary case
    // has nothing left to release -- `runStream` cleared it all -- and setting
    // state that is already that value is a no-op.
    session.forgetHandle();
    session.releaseSceneLock();
    session.clearPreview();
    session.releaseComposer();
    await readTranscript({ cid, sid }, { stillWanted: ownsObservation }).catch(() => -1);
    if (!ownsObservation()) return;
    // FAILED WHILE WE WERE AWAY, and the server knows why. The rest of recovery
    // only asks whether the post was retained, refetches and unlocks, so a
    // provider error, a store failure or a check that would not resolve simply
    // vanished: the player came back to a scene with no reply, no banner and
    // nothing to act on.
    //
    // AFTER the refresh, not before. `readTranscript` clears the banner on a
    // successful load -- which is right for an ordinary scene open and wrong
    // here, so this has to be the last word rather than the first.
    if (handle?.state === "failed" && handle.error) fail(handle.error, true, "scene");
  });
  return { adoptPendingRun };
}
