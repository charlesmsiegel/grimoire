import type { Dispatch, SetStateAction } from "react";
import type { ProposalRecord } from "../../api/client";
import {
  api
} from "../../api/client";
import { isAbortError } from "../../api/stream";
import type { PlayError } from "../../components/play/PlayBanners";
import type { RunRegistry } from "../../runs/RunRegistryProvider";
import type { SceneKey } from "./campaignTypes";
import { createStreamFrames } from "./streamFrames";
import type { RunSession } from "./useRunSession";
import type { TranscriptRead } from "./useTranscriptWindow";
export type AttachmentCapabilities = {
  session: Pick<RunSession, "beginAttach" | "renderFrame" | "resetParts" | "clearPreview"
    | "clearObserver" | "takeCancellation" | "forgetHandle" | "releaseSceneLock" | "releaseComposer">;
  registry: RunRegistry;
  fail: (err: unknown, retryable?: boolean, from?: string) => void;
  recoverPrompt: (cid: string, sid: string, text: string, director?: boolean) => void;
  setProposalNow: (record: ProposalRecord | null) => void;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  readTranscript: TranscriptRead;
  bumpReplayEpoch: () => void;
};

/** Read a run's buffered frames from where we left off, then keep reading.
 *
 *  The same rendering the live path does, driven from the run's buffer
 *  instead of from the POST's own response: deltas accumulate into the
 *  streaming preview, a proposal opens its chip, and `done` or an error ends
 *  it. The cursor is `resumeFrom` -- one past the last frame actually read --
 *  so nothing is replayed and nothing is skipped.
 */
export async function attachObservedRun(scope: SceneKey, runId: string, {
  session, registry, fail, recoverPrompt, setProposalNow, setError, readTranscript, bumpReplayEpoch,
}: AttachmentCapabilities): Promise<void> {
  const { cid, sid } = scope;

  const controller = session.beginAttach({ cid, sid }, registry.pending(cid, sid)?.attempt ?? "", runId);
  const frames = createStreamFrames(session.renderFrame);
  session.resetParts();
  let finished = false;
  try {
    await api.attachRun(cid, sid, runId, registry.resumeFrom(runId), (e) => {
      if (frames.accept(e)) { /* preview frame */ }
      else if (e.error) {
        fail(e.error, true, "scene");
        finished = true;
        // An ADOPTED run can fail the same way a watched one does, and it
        // rolls the post back the same way -- `post_returned` says so. The
        // registry is holding the only remaining copy of those words at that
        // point (the transcript no longer has them, and the component that
        // typed them may be long gone), and the `settle` below is about to
        // drop it. Recovered first, so the settle retires an entry whose
        // text has already been handed back.
        if (e.error.post_returned) {
          const held = registry.pending(cid, sid)?.text;
          if (held) recoverPrompt(cid, sid, held);
        }
      } else if (e.proposal) {
        setProposalNow({
          id: e.proposal.id, status: "pending",
          payload: e.proposal, resolution: null
        });
      } else if (e.done) { finished = true; }
    }, controller.signal, (index) => registry.consume(runId, index));
  } catch (err) {
    // A reattach that fails is not a failed turn: the run is on the server
    // and the reply is heading for the transcript either way. Left unsettled
    // so the next visibility change tries again.
    //
    // AND LEFT LOCKED, which review caught was not the case. Clearing `busy`
    // here put Send back over a run that still owns the scene -- and the next
    // send's `registry.begin()` overwrites this scene's one pending entry, so
    // if the original run then rolls its post back, the words it was holding
    // are gone with it. The same posture as an unconfirmed Stop, for the same
    // reason: nothing has established what became of that run, so nothing may
    // act as though something had.
    //
    // `abortRef` alone is cleared, because the adoption pass -- which is what
    // resolves this -- bows out while it is set.
    if (!isAbortError(err)) {
      session.clearPreview();
    }
    session.clearObserver();
    // UNLESS a Stop is what aborted it. `runStream`'s finally is where a
    // pending cancel is normally waited on, and an adopted run never goes
    // through it -- so without this the player presses Stop, the backend
    // duly cancels, and the composer stays locked forever on a turn that is
    // over. Confirmed the same way and unconfirmed the same way: the wait is
    // what makes the button honest, here as there.
    const pending = session.takeCancellation();
    if (pending) {
      try {
        await pending;
        registry.settle(cid, sid);
        session.forgetHandle();
        session.releaseSceneLock();
        session.clearPreview();
        session.releaseComposer();
      } catch {
        setError({
          text: "could not reach the server to stop this turn — it "
            + "may still be generating", retryable: false
        });
      }
    }
    return;
  }
  if (finished) registry.settle(cid, sid);
  session.clearObserver();
  session.releaseComposer();
  session.releaseSceneLock();
  // The refetch FIRST, and the preview cleared after it. Clearing first
  // leaves a window with the text nowhere -- and that window is not
  // theoretical: the aborted turn's flush poll is still refreshing this
  // scene, and `selectScene`'s window token retires whichever refetch loses.
  // Holding the preview means the reader sees the reply either way.
  await readTranscript({ cid, sid }).catch(() => -1);
  session.clearPreview();
  // The REPLAY PANEL does not read the transcript, it reads the replay
  // session -- and it loads that once, on `cid`/`sid`. So an adopted run
  // that happened to be a replay turn refreshes everything the panel does
  // not look at: `session.pending` stays false and it keeps offering
  // "Replay next turn", which the already-pending session then 409s.
  //
  // Bumped unconditionally rather than only for replay runs, because from
  // here a run is just a run -- `attachRun` carries no kind, and inventing
  // one to avoid a re-mount that costs one GET would be the wrong trade.
  bumpReplayEpoch();
}
