import { useMemo } from "react";
import type { ProposalRecord, SceneAbsorb } from "../../api/client";
import {
  type Message
} from "../../api/client";
import {
  type TranscriptVariantSwipe
} from "../../components/play/TranscriptPost";
import { useResponseSwipe } from "../../components/play/useResponseSwipe";
import { NO_REROLL_ROUTE, type RerollRoute } from "../../components/RerollRoute";
import type { ScopedAlternates } from "./campaignShared";
import { ledgerSwipeState, legacySwipeState, transcriptSwipeTarget } from "./transcriptSwipeState";

export function useTranscriptSwipes({
  messages, alternates, transcriptIsActive, activeClosed, hasUserPost, firstIndex, cid, activeId, loaded,
  editing, busy, rolling, sceneLocked, renamesInFlight, absorb, proposal, canGenerate, rerollFromSwipe,
  mutateFromSwipe,
}: {
  messages: Message[];
  alternates: ScopedAlternates;
  transcriptIsActive: boolean;
  activeClosed: { sid: string; title: string; } | null;
  hasUserPost: boolean | null;
  firstIndex: number;
  cid: string;
  activeId: string | null;
  loaded: { cid: string; sid: string; token: number; } | null;
  editing: { index: number; text: string; } | null;
  busy: boolean;
  rolling: boolean;
  sceneLocked: boolean;
  renamesInFlight: number;
  absorb: SceneAbsorb | null;
  proposal: ProposalRecord | null;
  canGenerate: boolean;
  rerollFromSwipe: (id: string, guidance: string, route: RerollRoute) => Promise<void>;
  mutateFromSwipe: (id: string, variant: string) => Promise<void>;
}) {

  const { index: rerollIndex, rerollAt, canReroll, swipeTarget } = transcriptSwipeTarget({
    messages, firstIndex, transcriptIsActive, closed: !!activeClosed, hasUserPost,
  });
  const responseSwipe = useResponseSwipe({
    cid, sid: swipeTarget ? activeId : null, rid: swipeTarget?.response_id ?? null,
    content: swipeTarget?.content ?? null, windowToken: loaded?.token,
  });

  const { altCount, canSwipe, altTitle, stepAlternate } = legacySwipeState({
    alternates, cid, activeId, loaded, closed: !!activeClosed, candidateIndex: rerollIndex,
  });
  const editingAny = editing !== null;
  const responseDisabled = busy || rolling || sceneLocked || editingAny || renamesInFlight > 0;
  const { swipeComplete, swipePosition, swipeCount, swipeShown, swipeGenerates,
    swipeTip, swipePrevDisabled, swipeNextDisabled, extendDisabled } = ledgerSwipeState({
    responseSwipe, closed: !!activeClosed, canReroll, responseDisabled,
    hasAbsorb: !!absorb, hasProposal: proposal !== null, canGenerate,
  });
  // Memoized on the primitives above, so a render that changes none of them
  // hands the row the same object.
  const variantSwipe = useMemo<TranscriptVariantSwipe | null>(() => (
    swipeShown && swipePosition !== null
      ? {
        position: swipePosition, count: swipeCount, title: swipeTip,
        previousDisabled: swipePrevDisabled, nextDisabled: swipeNextDisabled,
        generates: swipeGenerates
      }
      : null
  ), [swipeShown, swipePosition, swipeCount, swipeTip, swipePrevDisabled, swipeNextDisabled,
    swipeGenerates]);

  /** ‹ / › on the last response, from the arrows and the touch swipe. Each
   *  re-checks its own disabled flag, so a gesture can do nothing the button
   *  beside it could not. At the newest, › generates — the reroll popover's
   *  plain submit, keeping the pending length chip. */
  function stepVariant(delta: -1 | 1) {
    const rid = swipeTarget?.response_id;
    if (!variantSwipe || !rid) return;
    if (delta < 0 ? variantSwipe.previousDisabled : variantSwipe.nextDisabled) return;
    if (delta > 0 && variantSwipe.generates) {
      void rerollFromSwipe(rid, "", NO_REROLL_ROUTE);
      return;
    }
    const vid = swipeComplete[variantSwipe.position + delta];
    if (vid) void mutateFromSwipe(rid, vid);
  }
  return {
    responseDisabled, rerollAt, canReroll, canSwipe, altCount, altTitle, editingAny, variantSwipe,
    swipeTarget, extendDisabled, responseSwipe, stepAlternate, stepVariant,
  };
}
