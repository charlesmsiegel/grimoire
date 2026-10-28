import type { TurnObservation } from "./observeTurnStream";

type RecoveryEvidence = {
  ephemeral: boolean;
  rerolling: boolean;
  totalBefore: number | null;
  refreshed: boolean;
  seen: number;
};

/** Decide restoration and flush work from transcript evidence, without I/O. */
export function turnRecoveryPolicy(
  { finished, errored, proposed, beforeResponse, refused }: TurnObservation,
  { ephemeral, rerolling, totalBefore, refreshed, seen }: RecoveryEvidence,
): { restorePrompt: boolean; producedNothing: boolean; poll: boolean; baseline: number } {
  // Anything that ended without `done` may have left a partial the backend
  // is still flushing — a cancel, or a body cut short. An error frame is
  // NOT one of those: the backend ran its handler before sending it, so the
  // refresh above already sees whatever it wrote.
  //
  // Not gated on having seen text, either. Gating on `acc` was wrong and
  // review caught it: what reached the client is not what the backend has to
  // flush. `FenceWatcher.feed` returns "" for the whole of a roll fence and
  // withholds anything that might yet become one, so a reply that opens with
  // a fence streams nothing at all while the server still persists a
  // proposal — invisible until some later refresh under the old gate.
  // Now the transcript has answered it: no growth means the post never
  // landed, so the prompt exists nowhere and the composer has to have it
  // back. `seen < 0` is `selectScene` bowing out to a newer owner — it did
  // not look, so it cannot say.
  //
  // And when the transcript cannot answer — the refresh failed, or this
  // scene's length before the turn was never established — restore anyway.
  // Both mean the same thing: nothing proves the post landed. Erring the
  // other way risks a duplicate the player can see and delete; erring this
  // way destroys text that exists in no other place.
  //
  // Both outcomes that can leave the post unwritten ask the same question,
  // so they share the answer: the request that never arrived, and the one
  // the server refused. `nothingLanded` is the transcript saying it, out
  // loud — not merely the absence of evidence that it did.
  //
  // `seen < 0` belongs in `unverifiable`, and review caught that it was not
  // there. It is `selectScene` retiring its own read because a newer owner
  // took the view — the comment above already calls that "it did not look,
  // so it cannot say", which is the definition of unverifiable, but the code
  // said otherwise: the await did not throw, so `refreshed` was true, and a
  // prompt that genuinely never landed went unrestored because the read that
  // would have proved it was thrown away.
  const unverifiable = !refreshed || totalBefore === null || seen < 0;
  const nothingLanded = !unverifiable && seen <= totalBefore;
  // A stream that started and then stopped without either frame is the
  // third way to end up with no post, and review caught it as the one the
  // client had no answer for: the backend rolls the post back *before* it
  // yields the error frame, so a connection dropped in between leaves a
  // rollback that happened and a client that was never told. Nothing is set
  // — not errored, not beforeResponse, not refused — and the poll cannot help,
  // because it watches for growth and a rollback only ever shrinks.
  //
  // Only on positive proof, unlike the two above. Headers arrived, which
  // for a chat means `post_chat` already appended; so an unverifiable
  // refresh here means the post is most likely sitting in the transcript,
  // and restoring on a guess would duplicate it. `nothingLanded` is the
  // transcript saying the rollback ran.
  const interrupted = !finished && !errored && !beforeResponse && !refused;
  // Codex review (round 2, P1). `errored` is excluded above, because for
  // an ordinary post the error frame already recovers — the backend sets
  // `post_returned` and the handler acts on it mid-stream. An ephemeral
  // turn has no post to return, so that flag is NEVER set for one and
  // the error frame recovers nothing; a provider that dies before any
  // narration therefore destroyed the note. The transcript is the only
  // witness left, and for an ephemeral turn it is a clean one:
  // `nothingLanded` means the generation left no reply, with no post of
  // its own to confuse the count. Safe to add only here — with
  // `post_returned` unreachable for these turns, this cannot double up
  // with the in-stream recovery.
  const diedWithNothing = ephemeral && errored && nothingLanded;
  // A provider can finish CLEANLY and write nothing: an empty safety
  // response is a shape the backend supports, and it still ends the
  // stream with `done` (Codex review). `landed` is then true, so none of
  // the branches above fires and an ephemeral turn's note -- already
  // cleared out of the composer -- would exist nowhere at all, with no
  // reply to show for it either.
  //
  // `!proposed` is the exception that keeps this honest: a director turn
  // that answered with a roll proposal instead of narration DID produce
  // something, and its note was spent doing so. The transcript not
  // growing is not "nothing happened" in that case.
  const finishedWithNothing = ephemeral && !rerolling && finished && !errored
    && nothingLanded && !proposed;
  // Recorded for the RETURN as well as the recovery (Codex review). This
  // turn reached the provider and came back with nothing at all, so
  // reporting it as landed hands the caller a lie with a cost: `send`
  // spends the one-shot response-length override on it, and the override
  // was promised to the next REPLY. The note is being handed back for
  // another attempt; the knob chosen for that attempt goes with it.
  const restorePrompt = ((beforeResponse || refused) && (unverifiable || nothingLanded))
    || (interrupted && nothingLanded) || diedWithNothing || finishedWithNothing;
  // Nothing to wait for when nothing on the server can produce a partial: a
  // refusal never started a stream, and a request that verifiably never
  // arrived never made a turn.
  //
  // `beforeResponse` alone is not that proof, and review caught it standing in
  // for it. It says no *response* came back, which the server can do having
  // already appended the post and begun generating — and growth in the
  // refresh proves exactly that happened. So the poll is skipped only when
  // the transcript confirms the turn does not exist; an unverified guess
  // costs a few reads, while being wrong the other way loses the partial.
  //
  // The poll needs a length to watch for growth past, and a refresh that
  // failed did not produce one. Fall back to this scene's pre-turn length:
  // still the right question (did the flush land?), just measured from
  // where the turn started. `-1` only when even that is unknown, which
  // makes the first successful read count as growth — the poll refreshes
  // once and stops, which is what an unmeasurable scene can honestly do.
  return {
    restorePrompt,
    producedNothing: finishedWithNothing,
    poll: !finished && !errored && !refused && !(beforeResponse && nothingLanded),
    baseline: seen >= 0 ? seen : (totalBefore ?? -1),
  };
}
