import type { Dispatch, SetStateAction } from "react";
import type { NavigateFunction } from "react-router-dom";
import { ReplayPanel } from "../../components/ReplayPanel";
import { sceneUrl } from "./campaignShared";

export function CampaignReplay({
  activeId, replayEpoch, cid, replayAt, busy, rolling, canGenerate, takeRollLatch, adoptPendingRun, setReplayAt, activeDone, activeClosed, openBranch, navigate, refreshAfterReplay, fail
}: {
  fail: (err: unknown, retryable?: boolean, from?: string) => void;
  activeId: string;
  replayEpoch: number;
  cid: string;
  replayAt: number | null;
  busy: boolean;
  rolling: boolean;
  canGenerate: boolean;
  takeRollLatch: (forSid: string) => () => void;
  adoptPendingRun: (cid: string, sid: string) => Promise<void>;
  setReplayAt: Dispatch<SetStateAction<number | null>>;
  activeDone: boolean;
  activeClosed: { sid: string; title: string } | null;
  openBranch: (id: string) => Promise<void>;
  navigate: NavigateFunction;
  refreshAfterReplay: (asked?: boolean) => void;
}) {
  return (
    <ReplayPanel key={`${activeId}:${replayEpoch}`}
      cid={cid} sid={activeId} startAt={replayAt}
      disabled={busy || rolling} generationDisabled={!canGenerate}
      // The same latch every other write to this transcript
      // takes: a replayed turn is a generation into the scene
      // on screen, and while one runs nothing else here may
      // offer to start a second.
      latch={() => takeRollLatch(activeId)}
      // A replay stream that ended with no answer leaves a
      // run that may still own the scene. This is the same
      // pass mount and `visibilitychange` run; calling it
      // now is the difference between the panel re-enabling
      // itself over that run and the view taking it back.
      onUnanswered={() => { adoptPendingRun(cid, activeId).catch((err: unknown) => fail(err, false, "scene")); }}
      onStartHandled={() => setReplayAt(null)}
      // Only an unabsorbed scene branches; an absorbed one's
      // branch is a campaign fork, which "Fork first" is.
      branchable={!activeDone}
      // A closed branch refuses every generation here; the
      // panel keeps only accept and stop, as the server does.
      closed={!!activeClosed}
      // The replay runs in the sibling; this scene was not
      // touched, so the reader goes where the walk is.
      onBranched={(branched) => { openBranch(branched).catch((err: unknown) => fail(err, false, "scene")); }}
      // Into the SAME scene in the copy, not the campaign's
      // front door: a fork copies the scenes wholesale, so
      // this id is there, and the reader asked to replay one
      // particular post. `replayAt` is deliberately left
      // standing — the scene did not change, so the dialog
      // they were looking at is waiting for them in the copy.
      onForked={(forked) => void navigate(sceneUrl(forked, activeId))}
      onChanged={refreshAfterReplay} />
  );
}
