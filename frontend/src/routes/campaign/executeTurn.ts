import type { Dispatch, SetStateAction } from "react";
import type { ProposalRecord } from "../../api/client";
import { api } from "../../api/client";
import { newAttemptId } from "../../api/stream";
import type { PlayError } from "../../components/play/PlayBanners";
import { canGenerateOnRoute } from "../../components/RerollRoute";
import type { RunRegistry } from "../../runs/RunRegistryProvider";
import type { AttachmentCapabilities } from "./attachObservedRun";
import { settleOn } from "./campaignShared";
import type { SceneKey, TurnRequest } from "./campaignTypes";
import { observeTurnStream, type TurnObservation } from "./observeTurnStream";
import type { FlushCapabilities } from "./runSettlement";
import { settleTurn } from "./settleTurn";
import type { RunSession } from "./useRunSession";
import type { TranscriptRead } from "./useTranscriptWindow";

export type { TurnRequest } from "./campaignTypes";
export type ExecutionCapabilities = {
  canGenerate: boolean;
  renamesInFlight: number;
  session: Pick<RunSession, "setNote" | "beginLive" | "renderFrame" | "resetParts"
    | "confirmRun" | "handle" | "clearObserver" | "takeCancellation" | "observerActive"
    | "turnToken" | "forgetHandle" | "clearPreview" | "releaseComposer" | "releaseSceneLock">;
  registry: RunRegistry;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  verifiedTotal: (scope: SceneKey) => number | null;
  fail: (err: unknown, retryable?: boolean, from?: string) => void;
  setProposalNow: (record: ProposalRecord | null) => void;
  providerSucceeded: () => void;
  readTranscript: TranscriptRead;
  refreshSwipe: () => void;
  attachment: AttachmentCapabilities;
  settlement: FlushCapabilities;
};

async function readTurnBaseline(
  scope: SceneKey, signal: AbortSignal, verifiedTotal: (scope: SceneKey) => number | null,
): Promise<number | null> {
  const { cid, sid: id } = scope;
  // How long this scene's transcript was before the turn wrote anything —
  // the baseline the pre-response restore below compares against.
  //
  // `totalRef` alone is not it. It holds whichever scene was read last, and a
  // turn can start on a scene that has not been read yet: Send stays enabled
  // while a freshly selected scene is still loading, and `send` creates a
  // scene and streams into it without a read in between. Measuring this
  // scene's growth against another scene's length decides the restore by
  // which transcript happened to be longer — restoring a prompt that landed
  // (the player sends it twice) or dropping one that never did (review, #95).
  // So: use the ref only when it is this scene's, and otherwise read the one
  // number needed, which costs a request only inside that window.
  //
  // Raced against Stop rather than cancelled by it. This read runs before the
  // POST, so the turn's controller has nothing to abort yet, and review caught
  // that a stalled preflight against an unhealthy server then hung the whole
  // turn here: outside the try, with `busy` set and no way to clear it. The
  // race gives the wait an exit; the stray GET resolves into nothing, and an
  // unknown baseline is the answer this already knows how to handle.
  // Cancelling it properly would mean threading a signal through `request`,
  // whose in-flight GET sharing hands one promise to several callers — one
  // caller's Stop must not abort another component's read.
  return verifiedTotal(scope) ?? await Promise.race([
    api.getScene(cid, id, { limit: 1 })
      .then((s) => s.total ?? s.messages.length)
      .catch(() => null),   // unknown — treated as unverifiable below
    settleOn(signal, null),
  ]);
}

// Returns whether the turn actually landed. Callers retain their one-shot
// response override when the request failed or an ephemeral reply was empty.
export async function executeTurn(request: TurnRequest, {
  canGenerate, renamesInFlight, session, registry, setError, verifiedTotal, fail,
  setProposalNow, providerSucceeded, readTranscript, refreshSwipe, attachment, settlement,
}: ExecutionCapabilities): Promise<boolean> {
  const { scope: { cid, sid: id }, durableText: recoverableText, note, route } = request;

  if (!canGenerateOnRoute(canGenerate, route)) return false;
  session.setNote(note);
  // The authoritative rename guard, not the ones in `send`/`retry`/`reroll`.
  // Those stop the optimistic UI work before it happens, but they are a list
  // of call sites — and review found the one that was missing: resolving a
  // roll proposal streams a continuation through here without going past any
  // of them. Every stream enters by this function, so this is where the
  // question belongs; the callers keep their checks so they can bail before
  // clearing a composer, not because they are the guarantee (#95).
  if (renamesInFlight) return false;

  // Minted here, before anything is sent. This is what makes the turn
  // addressable in the window between the server accepting it and its first
  // frame arriving -- see `runRef`.
  const attempt = newAttemptId();
  // Registered BEFORE the request goes out. Registering when the leading
  // frame arrives would leave nothing recorded in exactly the window this is
  // for: the server accepts the send, mutates the scene, and the response
  // dies before any frame reaches us.
  registry.begin({ cid, sid: id, attempt, text: recoverableText, runId: null });
  // The CAMPAIGN travels with the handle. React Router reuses this component
  // across `/campaigns/A` -> `/campaigns/B`, so `cid` is whichever campaign
  // is on screen when Stop is pressed -- and scene ids are campaign-local, so
  // cancelling A's turn through B's id reaches nothing and the turn runs on.
  const controller = session.beginLive({ cid, sid: id }, attempt);
  const streamToken = controller.token;
  setError(null);
  const totalBefore = await readTurnBaseline(request.scope, controller.signal, verifiedTotal);
  // Keep evidence here so even an unexpected observer rejection still tears
  // down using the facts already received (including done before socket EOF).
  const observed: TurnObservation = {
    finished: false, errored: false, proposed: false, beforeResponse: false, refused: false,
  };
  let producedNothing = false;
  try {
    await observeTurnStream(request, { signal: controller.signal, attempt }, observed, {
      session, registry, fail, setProposalNow, providerSucceeded,
    });
  } finally {
    // No return from finally: it would replace an in-flight error or result.
    // Terminal settlement keeps ownership checks and initial release atomic;
    // held/adopted/superseded turns retain their handle, preview and composer.
    producedNothing = await settleTurn(request,
      { stream: observed, totalBefore, token: streamToken, attempt },
      { session, registry, setError, attachment, readTranscript, settlement });
    // However it ended: a turn that landed on the swipe target's response, or
    // one that failed and left its content alone, both move what the swipe
    // read answers without necessarily moving the text it refetches on.
    refreshSwipe();
  }
  // Landed means the backend said so, not that the promise resolved -- and
  // not that it produced anything: an ephemeral turn that finished cleanly
  // with no reply is being recovered, not completed.
  return observed.finished && !observed.errored && !producedNothing;
}
