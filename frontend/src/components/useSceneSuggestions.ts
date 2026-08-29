import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SceneSuggestion } from "../api/client";

/** The generated half of the picker, and the one thing in it that costs money.
 *
 *  **One call fires on open, and it is the only unprompted one.** #319 put it
 *  behind a button, on the argument that a generation should not run on the
 *  path taken to start every scene; what that cost was the picker itself. Its
 *  four slots are 2 greetings + 2 ideas, and this call produces both — the
 *  ideas are its output, the greeting ranking rides along — so a picker that
 *  has not made it has no ideas to show and no ordering to show greetings in,
 *  and falls back to the first four the store listed, alphabetically. That is
 *  not a cheaper picker, it is a wrong one. The spend is real and unchanged;
 *  what it buys is now the default view rather than a second press.
 *
 *  There are still three states, and the picker draws each differently:
 *  `asked` false is "nobody has asked" — reachable only without an LLM
 *  connection now, since the open-call sets it; `suggestions === null` is
 *  "generating"; and `[]` is "asked, nothing to offer" — a reply with no
 *  ideas, or one that failed. `picks` follows the same three states, because
 *  the greeting ranking rides on the same call.
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
  // Whether a RANKED reply has actually landed, which is not the same question
  // as whether anyone has pressed. The greeting order is half of what the
  // press buys and only a `rank=true` request fetches it (the route sends no
  // greeting candidates otherwise), so a first press that fails must leave the
  // next one ranked. Tracking `asked` alone would promote the button to an
  // unranked regenerate after a failure, and a picker with more than two
  // greetings would then never get the ordering it is waiting on.
  const [ranked, setRanked] = useState(false);
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
        if (rank) { setPicks(r.greeting_picks ?? []); setRanked(true); }
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

  // Back to idle whenever the question changes, and the effect below then asks
  // the new one. Ordered that way on purpose: effects run in declaration order,
  // so the clear lands before the ask and cannot wipe the pending state it
  // sets. Bumping `seq` here is what stops a reply to the OLD question from
  // landing on the new one -- and, since a discarded reply's `finally` no
  // longer clears `busy`, this clears it.
  useEffect(() => {
    seq.current++;
    setAsked(false);
    setRanked(false);
    setSuggestions([]);
    setPicks([]);
    setNextDate("");
    setError(null);
    setBusy(false);
  }, [cid, afterSid, offscreen]);

  // The picker opens on an answer. Its four slots are 2 greetings + 2 ideas,
  // and BOTH halves come out of this one call -- the ideas are its output and
  // the greeting ranking rides along -- so a picker that has not made it shows
  // neither: no ideas, and greetings in whatever order the store listed them,
  // which is the first four alphabetically. That was the reader's report, and
  // it is what #319 traded away when it put this behind a button.
  //
  // So the call is back on open, and the cost that motivated #319 is real and
  // unchanged: opening this chooser is one model call, on the path taken to
  // start every scene. What is still deliberate is that this is the ONLY
  // unprompted one -- `suggest` (the Regenerate button) is the reader's, and
  // the opener in `CastPanel` generates nothing until asked.
  //
  // Keyed to the QUESTION, not to a boolean: it re-asks when the campaign, the
  // reference scene or the mode changes, because each is a different question
  // and the reset effect above has just cleared the last one's answer. A ref
  // rather than `asked`, because StrictMode double-invokes effects in dev and
  // no re-render separates the two -- the flag would still read false on the
  // second pass and buy the same answer twice.
  const autoAsked = useRef("");
  useEffect(() => {
    if (!ready) return;
    const question = `${cid}/${afterSid ?? ""}/${offscreen}`;
    if (autoAsked.current === question) return;
    autoAsked.current = question;
    // The pending pair, exactly as `suggest`'s first press sets them: the
    // picker draws "Generating…" and "Choosing…" rather than two empty groups
    // it is about to fill, and `rankPending` holds the greeting cards back so
    // they cannot shuffle under the reader once the ranking lands.
    setAsked(true);
    setSuggestions(null);
    setPicks(null);
    run("", true);
  }, [cid, afterSid, offscreen, ready, run]);

  /** The button — every press of it, the first and the fifth.
   *
   *  It ranks until a ranking lands, then regenerates. The first press is the
   *  expensive half of the prompt (it orders the greeting cards as well as
   *  writing the ideas) and reports pending on both lists, so the picker shows
   *  "Generating…" and "Choosing…" rather than two empty groups it is about to
   *  fill. Once a ranked reply HAS landed, later presses ask for ideas alone:
   *  re-ranking would reshuffle the greeting cards under the reader's cursor,
   *  and what is on screen stays there while the new ideas load.
   *
   *  One entry point rather than two, because "is this press a ranking?" is a
   *  question only the hook can answer -- the picker knows that somebody
   *  pressed, not that a reply ever came back. Splitting it left a first press
   *  that failed promoting the button to an unranked regenerate, which the
   *  route answers with no greeting candidates at all, so a picker with more
   *  than two greetings could never earn the ordering it was waiting on.
   */
  const suggest = useCallback((direction: string) => {
    // Guarded, not merely delegated: `run` no-ops without a connection, and
    // setting the pending state around a call that never happens would leave
    // "Generating…" on screen forever.
    if (!ready) return;
    setAsked(true);
    if (!ranked) { setSuggestions(null); setPicks(null); }
    run(direction, !ranked);
  }, [ready, ranked, run]);

  return { asked, suggestions, picks, nextDate, busy, error, suggest };
}

/** The shape `SceneIdeaPicker` needs to render this data — shared so the
 *  picker's props and the hook's return value cannot drift apart. */
export type SceneSuggestionsState = ReturnType<typeof useSceneSuggestions>;
