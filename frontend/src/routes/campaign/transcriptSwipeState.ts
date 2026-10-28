import type { Message } from "../../api/client";
import { swipeTitle } from "../../components/play/swipeTitle";
import { ROLL_SPEAKER, TRANSITION_SPEAKER } from "../../components/play/TranscriptPost";
import type { ResponseSwipeState } from "../../components/play/useResponseSwipe";
import type { ScopedAlternates } from "./campaignShared";

export function transcriptSwipeTarget({ messages, firstIndex, transcriptIsActive, closed, hasUserPost }: {
  messages: Message[];
  firstIndex: number;
  transcriptIsActive: boolean;
  closed: boolean;
  hasUserPost: boolean | null;
}) {
  // rerolling regenerates the trailing assistant run; a run that reaches the
  // first message is the opener and is not rerollable. Trailing scene
  // transitions are stepped OVER (they are not model output and the backend
  // preserves them), so the reroll affordance hangs off the last generated
  // message beneath them rather than off the true last message. A manual dice
  // roll (backend tags it speaker "Roll") still blocks reroll outright — its
  // entry lives on in rolls.json and the line must stay in lockstep.
  // window-relative; `rerollAt` below is the absolute index the rendered posts
  // carry. Both the run and the transitions above it live in the loaded tail,
  // so a windowed transcript reaches the same answer as a whole one.
  let index = messages.length - 1;
  while (index >= 0 && messages[index].speaker === TRANSITION_SPEAKER) index--;
  const candidate = index >= 0 ? messages[index] : null;
  const assistant = candidate?.role === "assistant" ? candidate : null;
  const canReroll = transcriptIsActive && !closed && assistant !== null
    && assistant.speaker !== ROLL_SPEAKER &&
    // "not the opener" — the regenerate route refuses an all-assistant
    // transcript, so this has to be true of the WHOLE scene, not the window.
    // Only the server can say (an offscreen scene stores no user posts however
    // long it runs, so "there is history above" proves nothing); the local
    // scan is the fallback for an unwindowed read, which holds everything.
    (hasUserPost ?? messages.some((x) => x.role === "user"));
  const rerollAt = candidate === null ? -1 : firstIndex + index;

  // The ledger's swipe target (spec §6): the post Reroll hangs off, when it is
  // a response. Nothing after it can share its id (only transitions follow, and
  // they are not responses), so it is also the last part of that response —
  // the one `TranscriptPost` hangs the response's controls off. Only while the
  // posts on screen are the active scene's: the read is addressed by
  // `activeId`, and a target taken from another scene's transcript would ask
  // about a response this scene does not have. A trailing reply with no
  // response id keeps the legacy alternates arrows below.
  const swipeTarget = transcriptIsActive && assistant?.response_id ? assistant : null;
  return { index, rerollAt, canReroll, swipeTarget };
}

export function legacySwipeState({ alternates, cid, activeId, loaded, closed, candidateIndex }: {
  alternates: ScopedAlternates;
  cid: string;
  activeId: string | null;
  loaded: { cid: string; sid: string; token: number } | null;
  closed: boolean;
  candidateIndex: number;
}) {
  // The swipe control hangs off the same message as Reroll — they act on the
  // same generation, so it renders against `rerollAt` (absolute), not
  // `rerollIndex` (window-relative), like every other per-post affordance.
  // Shown with two or more variants to tour, and also with a single one while
  // nothing is live: that is a reroll whose stream died, and the one affordance
  // that puts the lost reply back.
  // Scope gate: a set fetched for another campaign or scene is not this one's,
  // whatever order the responses came back in.
  const altCount =
    alternates.cid === cid && alternates.sid === activeId
      && loaded?.cid === cid && loaded?.sid === alternates.sid
      && loaded.token === alternates.window
      ? alternates.alternates.length : 0;
  const canSwipe = !closed && candidateIndex >= 0
    && (altCount > 1 || (altCount > 0 && alternates.active === null));
  // Wraps, so ‹/› tour the set. With the slot empty, ‹ reaches for the newest
  // variant and › for the oldest, which is what "one step off nothing" means.
  function stepAlternate(delta: number) {
    if (alternates.active === null) {
      if (delta > 0) return 0;
      return altCount - 1;
    }
    return (alternates.active + delta + altCount) % altCount;
  }
  // What the counter says on hover. The guidance leads: while cycling, "which
  // instruction produced this take" is the thing the preview cannot tell you,
  // and it is the only place the stored hint is visible at all.
  //
  // The model follows it whenever the variant carries one (#77). Not filtered
  // against the campaign's current model, deliberately: the control only
  // appears while there are takes to tour, which is precisely when "which model
  // produced this one" is the question — and a take generated somewhere else
  // reads no differently from one that was not, so this is the only place it
  // can be said. Every variant archived before the override existed, and every
  // one reconciled out of the transcript, stores "" and adds no line.
  const altTitle = (() => {
    if (alternates.active === null) return "no alternate is showing — the last reroll didn't land";
    const variant = alternates.alternates[alternates.active];
    if (!variant) return undefined;
    const lines = [
      variant.guidance ? `Guided: ${variant.guidance}` : "",
      variant.model ? `Model: ${variant.model}` : "",
    ].filter(Boolean);
    return lines.length ? `${lines.join("\n")}\n\n${variant.preview}` : variant.preview;
  })();
  return { altCount, canSwipe, altTitle, stepAlternate };
}

export function ledgerSwipeState({ responseSwipe, closed, canReroll, responseDisabled,
  hasAbsorb, hasProposal, canGenerate }: {
  responseSwipe: ResponseSwipeState;
  closed: boolean;
  canReroll: boolean;
  responseDisabled: boolean;
  hasAbsorb: boolean;
  hasProposal: boolean;
  canGenerate: boolean;
}) {
  // The ledger's arrows, worked out once for the row, the keys and the gesture.
  // Shown with two complete variants to tour, or one that can be generated from
  // (the ledger's `can_reroll` and this
  // view's `canReroll`, so › has somewhere to go). An active variant that is not complete has no
  // place among the complete ones to count from, so it shows none — the
  // Response variants disclosure still lists it.
  //
  // `blocked` is every refusal the client can know about: the transcript's own
  // guards; a landed review, whose watermark digests the content a swipe would
  // change; any proposal, which `activate` would supersede; the server's
  // `editable` and `round_open`; and a read still outstanding, whose count may
  // be stale. Generating also needs what the reroll needs. The server still
  // answers a race with 409, which `mutateResponse` reports.
  const { swipe: ledgerSwipe, complete: swipeComplete, position: swipePosition,
    pending: swipePending } = responseSwipe;
  const swipeCount = swipeComplete.length;
  const swipeShown = !closed && !!ledgerSwipe && swipePosition !== null
    && (swipeCount >= 2 || (swipeCount === 1 && ledgerSwipe.can_reroll && canReroll));
  const swipeGenerates = swipeShown && swipePosition === swipeCount - 1;
  const swipeTip = ledgerSwipe ? swipeTitle(ledgerSwipe) : undefined;
  // Keep writing, on the swipe target: what a generating › needs, minus the
  // arrows' own position rules. A hand-edited reply (`edited`, active null) is
  // continued from the trim, so it does not block; a non-complete active
  // variant does, as the server refuses it (`variant_incomplete`).
  const activeVariant = ledgerSwipe && ledgerSwipe.active !== null
    ? ledgerSwipe.variants[ledgerSwipe.active] : undefined;

  const swipeBlocked = responseDisabled || hasAbsorb || hasProposal || swipePending
    || !ledgerSwipe?.editable || !!ledgerSwipe.round_open;
  const swipePrevDisabled = swipeBlocked || swipePosition === 0;
  const swipeNextDisabled = swipeBlocked
    || (swipeGenerates && (!canGenerate || !canReroll || !ledgerSwipe?.can_reroll));
  const extendDisabled = swipeBlocked || !canReroll || !ledgerSwipe?.can_reroll
    || (activeVariant !== undefined && activeVariant.status !== "complete");
  return { swipeComplete, swipePosition, swipeCount, swipeShown, swipeGenerates,
    swipeTip, swipePrevDisabled, swipeNextDisabled, extendDisabled };
}
