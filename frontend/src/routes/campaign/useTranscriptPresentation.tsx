import { useMemo } from "react";
import type { TrackerSummary, UsagePostBucket } from "../../api/client";
import {
  type Message
} from "../../api/client";
import {
  isNarrator,
  TranscriptRun,
  type TranscriptActions, type TranscriptContext, type TranscriptReroll, type TranscriptRunData,
  type TranscriptVariantSwipe
} from "../../components/play/TranscriptPost";
import { type RerollRoute } from "../../components/RerollRoute";
import type { ScopedAlternates, StableHandlers } from "./campaignShared";

export function useTranscriptPresentation({
  cid, activeId, loaded, busy, rolling, transcriptIsActive, activeClosed, activeDone, responseDisabled,
  canGenerate, firstIndex, messages, rerollAt, canReroll, postChips, citedNeedle, lastOfResponse, tracker,
  trackerKeys, trackerRerun, canSwipe, alternates, altCount, altTitle, editingAny, sceneLocked, variantSwipe,
  rerollPrompt, rerollRoute, swipeTarget, extendDisabled, runs, plateAvatar, labels, transcriptActions,
  editing,
}: {
  cid: string;
  activeId: string | null;
  loaded: { cid: string; sid: string; token: number; } | null;
  busy: boolean;
  rolling: boolean;
  transcriptIsActive: boolean;
  activeClosed: { sid: string; title: string; } | null;
  activeDone: boolean;
  responseDisabled: boolean;
  canGenerate: boolean;
  firstIndex: number;
  messages: Message[];
  rerollAt: number;
  canReroll: boolean;
  postChips: Record<number, UsagePostBucket> | null;
  citedNeedle: string;
  lastOfResponse: Set<number>;
  tracker: TrackerSummary | null;
  trackerKeys: { [k: string]: string; };
  trackerRerun: { [k: string]: number; };
  canSwipe: boolean;
  alternates: ScopedAlternates;
  altCount: number;
  altTitle: string | undefined;
  editingAny: boolean;
  sceneLocked: boolean;
  variantSwipe: TranscriptVariantSwipe | null;
  rerollPrompt: string | null;
  rerollRoute: RerollRoute;
  swipeTarget: Message | null;
  extendDisabled: boolean;
  runs: TranscriptRunData[];
  plateAvatar: (run: Pick<TranscriptRunData, "actor" | "speaker" | "pc">, actorRef?: string) => string | null;
  labels: { user: string; assistant: string; };
  transcriptActions: StableHandlers<TranscriptActions>;
  editing: { index: number; text: string; } | null;
}) {
  const transcriptCtx = useMemo<TranscriptContext>(() => ({
    cid, sid: activeId ?? "",
    loadedCid: loaded?.cid ?? null, loadedSid: loaded?.sid ?? null,
    // A closed branch is read-only: no gutter actions, no response controls.
    busy, rolling, active: transcriptIsActive && !activeClosed, absorbed: activeDone,
    responseDisabled, generationDisabled: !canGenerate,
    lastIndex: firstIndex + messages.length - 1,
    rerollAt, canReroll, postChips, citedNeedle, lastOfResponse, tracker, trackerKeys,
    trackerRerun,
  }), [cid, activeId, loaded?.cid, loaded?.sid, busy, rolling, transcriptIsActive, activeDone,
    activeClosed, responseDisabled, canGenerate,
    firstIndex, messages.length, rerollAt, canReroll,
    postChips, citedNeedle, lastOfResponse, tracker, trackerKeys, trackerRerun]);

  const transcriptReroll = useMemo<TranscriptReroll>(() => ({
    swipe: canSwipe
      ? {
        active: alternates.active, count: altCount, title: altTitle,
        disabled: rolling || editingAny || sceneLocked
      }
      : null,
    variantSwipe,
    pop: rerollPrompt !== null ? { prompt: rerollPrompt, route: rerollRoute } : null,
    extend: swipeTarget ? { disabled: extendDisabled } : null,
  }), [canSwipe, alternates.active, altCount, altTitle, rolling, editingAny, sceneLocked,
    variantSwipe, rerollPrompt, rerollRoute, swipeTarget, extendDisabled]);

  // The rows themselves, as one list built only when one of its inputs moves.
  // Each row would skip a keystroke on its own; this skips even asking them,
  // which on a long window is a few hundred elements built and compared per
  // keystroke for nothing.
  //
  // The edit form and the reroll row's extras go to the one run that holds
  // them, and null to every other, so typing in either re-renders one run.
  const transcript = useMemo(() => runs.map((run) => {
    const holds = (index: number) => run.posts.some((p) => p.index === index);
    return (
      <TranscriptRun key={run.posts[0].index} run={run} avatar={plateAvatar(run, run.posts[0].m.actor_ref)}
        narrator={isNarrator(run, run.posts[0].m.actor_ref, labels.assistant)}
        ctx={transcriptCtx} actions={transcriptActions}
        editing={editing && holds(editing.index) ? editing : null}
        reroll={holds(rerollAt) ? transcriptReroll : null} />
    );
  }), [runs, plateAvatar, transcriptCtx, transcriptActions, editing, rerollAt, transcriptReroll,
    labels.assistant]);
  return { transcript };
}
