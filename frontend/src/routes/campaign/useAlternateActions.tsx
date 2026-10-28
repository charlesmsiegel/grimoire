import type { Dispatch, SetStateAction } from "react";
import type { ProposalRecord } from "../../api/client";
import {
  api
} from "../../api/client";
import { errorText } from "../../api/errors";
import type { PlayError } from "../../components/play/PlayBanners";
import { useResponseSwipe } from "../../components/play/useResponseSwipe";
import type { ScopedAlternates } from "./campaignShared";
import type { Current } from "./campaignTypes";

export function useAlternateActions({
  activeId, busy, rolling, sceneLocked, editing, transcriptIsActive, takeRollLatch, cid, currentCampaign,
  selected, selectScene, fail, responseSwipe, alternates, clearRerollRetry, setError, setProposalNow,
  invalidateTranscript, askAfterPost,
}: {
  activeId: string | null;
  busy: boolean;
  rolling: boolean;
  sceneLocked: boolean;
  editing: { index: number; text: string; } | null;
  transcriptIsActive: boolean;
  takeRollLatch: (forSid: string) => () => void;
  cid: string;
  currentCampaign: () => string;
  selected: () => Readonly<{ cid: string; sid: string; }> | null;
  selectScene: (id: string, stillWanted?: Current, renamed?: boolean) => Promise<number>;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  responseSwipe: ReturnType<typeof useResponseSwipe>;
  alternates: ScopedAlternates;
  clearRerollRetry: (sid: string) => void;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  setProposalNow: (record: ProposalRecord | null) => void;
  invalidateTranscript: () => void;
  askAfterPost: (id: string, upto: number) => void;
}) {

  async function mutateResponse(id: string, variant?: string) {
    if (!activeId || busy || rolling || sceneLocked || editing || !transcriptIsActive) return;
    if (!variant && !window.confirm("Delete only this response? Later messages remain and will be marked as having changed context.")) return;
    const sid = activeId;
    const release = takeRollLatch(sid);
    try {
      if (variant) await api.activateResponseVariant(cid, sid, id, variant);
      else await api.deleteResponse(cid, sid, id);
      if (currentCampaign() === cid && selected()?.sid === sid) {
        try { await selectScene(sid); }
        catch (err: unknown) {
          if (currentCampaign() === cid && selected()?.sid === sid) {
            setError({ text: `Response saved, but the scene could not be re-read: ${errorText(err)}`, retryable: false });
          }
        }
      }
    } catch (err) { fail(err, false); }
    finally {
      release();
      // Success or refusal alike: a 409 means the server's state is not the one
      // the arrows were drawn from, and an activate that landed may have left
      // the content unchanged (a variant with the same text).
      responseSwipe.refresh();
    }
  }

  // Cycling is a swap, not a delete: whatever is on screen becomes an alternate
  // in the slot the chosen one vacates, so ‹ and › tour the set indefinitely.
  async function pickAlternate(index: number) {
    // `editing` as well as `rolling` — the guard runs both ways. An edit form
    // open on another post of this generation survives the swap: `selectScene`
    // refreshes the messages without clearing it, so the form rebinds to the
    // *promoted* variant's message at the same absolute index and Save would
    // overwrite it with a draft of the text that variant replaced.
    // `sceneLocked`, not just `busy`: `busy` clears the moment the socket dies
    // while the post-cancel flush is still writing to this scene, and a swap in
    // that window races the abort hook — landing first it loses the cancelled
    // partial the hook was about to persist, landing second it parks it. Same
    // rule every other transcript mutation outside `runStream` reads (#95).
    if (!activeId || busy || rolling || editing || sceneLocked) return;
    const sid = activeId;
    // Send the variant's id, taken from the same snapshot the index came from.
    // Retention shifts every index when a full set gains a take, so a position
    // this tab computed before another tab rerolled would name different text
    // by the time it arrives; an id that is no longer in the set 404s instead.
    const target = alternates.alternates[index];
    if (!target) return;
    // The in-flight-store-mutation latch doRoll uses, and every control that
    // must not fire alongside one already reads it. Without it a double-click
    // computes both indices from the same `alternates.active` snapshot — two ‹
    // clicks step back once — and the two selectScene refreshes can land out of
    // order. Taken for `sid`, so leaving the scene releases the controls there
    // rather than waiting on a request that no longer concerns the reader.
    const releaseLatch = takeRollLatch(sid);
    // Only the scene the swap was for, and only while it is still the one on
    // screen: the user may have moved on while the POST was in flight, and
    // refreshing would navigate them back to a scene they left. The campaign is
    // half of "the same scene" — `selectScene` is this render's closure, so in
    // campaign B it would fetch B's sid out of A.
    const stillHere = () => currentCampaign() === cid && (selected()?.sid ?? null) === sid;
    try {
      try {
        await api.pickAlternate(cid, sid, target.id);
      } catch (err: unknown) {
        // Scoped like every other write this component reports. Switching
        // scenes clears the banner deliberately — one scene's failure must not
        // offer its Retry against another — and a swap rejecting after the move
        // would put it straight back under a scene it has nothing to do with.
        if (!stillHere()) return;
        fail(err, false);
        // `promote` removes the live run and then appends the chosen one. If
        // the append is what failed, the slot is now EMPTY — the sidecar still
        // holds both variants, but the transcript does not, and leaving the old
        // reply on screen means every message index below it is a lie and an
        // edit saves over the wrong post. Re-read so the empty slot and the
        // control that refills it are what the reader sees.
        await selectScene(sid).catch(() => { });
        return;
      }
      // Past here the swap has COMMITTED, and nothing that fails below may be
      // reported as a failed swap: it would deny a change that happened, and
      // the recovery it implies — do it again — is not the one that helps.
      //
      // The swap is also the recovery the player chose over Retry. Leaving a
      // failed reroll's banner up offers a Retry that regenerates over the take
      // just selected, with the guidance that failed, undoing the choice.
      clearRerollRetry(sid);
      // Everything below writes shared view state, so it waits on the scope
      // check. The destination scene is deliberately usable while this POST is
      // open — that is the point of the scoped latch — so it can have raised a
      // banner of its own, and clearing it here would take a failure that has
      // nothing to do with this swap.
      if (!stillHere()) return;
      setError(null);
      // The backend retires the pending decision as part of the swap, so the
      // chip goes now rather than at the next read: left up it adjudicates
      // narration no longer on screen, and its 409 surfaces a Retry that
      // generates. Ordered before the refresh below, whose own proposal read is
      // issued after this and so puts the chip back if the swap was a no-op.
      setProposalNow(null);
      let seen = -1;
      try {
        seen = await selectScene(sid);
      } catch (err: unknown) {
        // Asked again, not only before the first read. `selectScene` calls
        // `selectIdentity`, so a retry issued after the reader moved on does not
        // merely refresh the wrong scene — it navigates them back to it, or in
        // a campaign switch installs the old campaign's scene id in the new
        // view. The banner below is scoped for the same reason.
        if (!stillHere()) return;
        // One more attempt before giving up — the read that failed is the only
        // thing between the reader and a transcript that is already correct on
        // disk.
        try {
          seen = await selectScene(sid);
        } catch {
          if (!stillHere()) return;
          // Still stale. Drop the readiness the counter and the ‹/› control
          // depend on, so nothing acts on a transcript the set no longer
          // indexes, and say what actually happened.
          invalidateTranscript();
          setError({
            text: `The take was swapped, but the scene could not be re-read: `
              + errorText(err), retryable: false
          });
        }
      }
      // A swap replaces the reply in place: the transcript is no longer than it
      // was, but the words the stored summary covers are a take the player
      // rejected. Same reasoning as `saveEdit` — the moved digest is what makes
      // the refold due, and without this ask the panel would show a summary of
      // the discarded take, flagged stale, until the next generated turn.
      // Scoped like every other write above, and skipped when neither re-read
      // produced a boundary.
      if (stillHere()) askAfterPost(sid, seen);   // #85, #84
    } finally {
      releaseLatch();
    }
  }
  return { pickAlternate, mutateResponse };
}
