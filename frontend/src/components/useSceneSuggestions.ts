import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SceneSuggestion } from "../api/client";

/** The generated half of the picker, and the one thing in it that costs money.
 *
 *  **Nothing here fires on its own.** Picking a scene mode used to spend a
 *  generation before the reader had asked for anything: the ranking is an LLM
 *  call, and it ran on the way to the picker whether or not the ideas were
 *  wanted, on the path taken to start every scene. The call is the same one it
 *  always was; what changed is that `suggest` is now the only thing that
 *  starts it, and a button is the only thing that calls `suggest`.
 *
 *  So there are three states, not two, and the picker draws each differently:
 *  `asked` false is "nobody has asked" (no cards, no spinner, a button);
 *  `suggestions === null` is "generating"; and `[]` is "asked, nothing to
 *  offer" — a reply with no ideas, or one that failed. `picks` follows the
 *  same three states, because the greeting ranking rides on the same call.
 *
 *  A ranking is remembered for as long as the picker is open on the same
 *  reference scene, and re-earned otherwise: **Back** keeps it (that is why
 *  this hook lives in `NewSceneChooser` and not in the pane — issue #319),
 *  and a different campaign, a different reference scene or a switch between
 *  PC and offscreen drops it, because each of those is a different question
 *  and stale cards would answer the wrong one. Closing the chooser drops it
 *  with the component, which is the answer to "is a ranking remembered for
 *  this scene, or re-earned?": re-earned. A ranking is made against the
 *  transcript as it stood, and a campaign gets played between two openings of
 *  this modal, so a remembered one would quietly answer a question the reader
 *  has moved on from. Remembering an idea on purpose already has a surface
 *  that says so — **Save** files it in the ledger (#88), which survives
 *  everything and belongs to the reader rather than to a cache. */
export function useSceneSuggestions(cid: string, afterSid: string | null,
                                    ready: boolean, offscreen: boolean) {
  // `false`/`[]`/`[]`: idle, which is what every mount starts at now.
  const [asked, setAsked] = useState(false);
  const [suggestions, setSuggestions] = useState<SceneSuggestion[] | null>([]);
  const [picks, setPicks] = useState<string[] | null>([]);
  const [nextDate, setNextDate] = useState("");
  const [busy, setBusy] = useState(false);
  // The raw rejection, so the picker can tell an unreachable model from any
  // other refusal and offer the local-connection recovery (#210).
  const [error, setError] = useState<unknown>(null);

  // Only the NEWEST request may write state. The first ranked fetch and a
  // regenerate race freely, and without this a slow first reply lands after the
  // directed one and silently replaces it with undirected cards.
  const seq = useRef(0);

  const run = useCallback((direction: string, rank: boolean) => {
    if (!ready) return;
    const mine = ++seq.current;
    setBusy(true);
    setError(null);
    api.sceneSuggestions(cid, afterSid ?? undefined, offscreen, direction, rank)
      .then((r) => {
        if (mine !== seq.current) return;
        setSuggestions(r.suggestions);
        // A rank=false reply carries no picks; writing its empty list would
        // wipe the ranking the greeting cards are ordered by.
        if (rank) setPicks(r.greeting_picks ?? []);
        // Likewise: a refresh that estimates no date must not clear a good one.
        if (r.next_date) setNextDate(r.next_date);
      })
      .catch((err) => {
        if (mine !== seq.current) return;
        setSuggestions([]);
        if (rank) setPicks([]);
        setError(err);
      })
      .finally(() => { if (mine === seq.current) setBusy(false); });
  }, [cid, afterSid, ready, offscreen]);

  // Back to idle whenever the question changes. While the call fired on mount,
  // a new `cid` re-ran the mount effect and the answer replaced itself; with
  // nothing firing on its own, campaign A's cards would simply sit there in
  // campaign B until someone pressed. Bumping `seq` here is what stops a reply
  // to the OLD question from landing on the new one -- and, since a discarded
  // reply's `finally` no longer clears `busy`, this clears it.
  useEffect(() => {
    seq.current++;
    setAsked(false);
    setSuggestions([]);
    setPicks([]);
    setNextDate("");
    setError(null);
    setBusy(false);
  }, [cid, afterSid, offscreen]);

  /** The button. Ranked (`rank=true`), because this is the call that also
   *  orders the greeting cards, and pending on both lists so the picker shows
   *  "Generating…" rather than an empty group it is about to fill. */
  const suggest = useCallback((direction: string) => {
    // Guarded, not merely delegated: `run` no-ops without a connection, and
    // setting the pending state around a call that never happens would leave
    // "Generating…" on screen forever.
    if (!ready) return;
    setAsked(true);
    setSuggestions(null);
    setPicks(null);
    run(direction, true);
  }, [ready, run]);

  /** Regenerate. Unranked: the greeting order is already earned, and the cards
   *  on screen stay there while the new ones load. */
  const refresh = useCallback((direction: string) => run(direction, false), [run]);
  return { asked, suggestions, picks, nextDate, busy, error, suggest, refresh };
}

/** The shape `SceneIdeaPicker` needs to render this data — shared so the
 *  picker's props and the hook's return value cannot drift apart. */
export type SceneSuggestionsState = ReturnType<typeof useSceneSuggestions>;
