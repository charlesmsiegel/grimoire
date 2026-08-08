import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SceneSuggestion } from "../api/client";
import { errMsg } from "./errMsg";

/** The generated half of the picker. `null` on suggestions/picks means "still
 *  generating"; `[]` means "nothing to offer" (no key, empty, or failed) — the
 *  picker renders those two states differently, so they must stay distinct. */
export function useSceneSuggestions(cid: string, afterSid: string | null,
                                    ready: boolean, offscreen: boolean) {
  const [suggestions, setSuggestions] = useState<SceneSuggestion[] | null>(ready ? null : []);
  const [picks, setPicks] = useState<string[] | null>(ready ? null : []);
  const [nextDate, setNextDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Only the NEWEST request may write state. The initial ranked fetch and a
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
        setError(errMsg(err));
      })
      .finally(() => { if (mine === seq.current) setBusy(false); });
  }, [cid, afterSid, ready, offscreen]);

  useEffect(() => { run("", true); }, [run]);

  // `useState` above runs once, at mount. `ready` can flip false -> true on a
  // MOUNTED hook -- App resolves its config fetch asynchronously while nothing
  // gates the chooser on it -- and the [] that meant "no connection, nothing to
  // offer" would otherwise persist as "nothing to offer" through the fetch that
  // flip just triggered, showing neither hint. `refresh` never changes `ready`,
  // so this does not fire (and cannot blank the cards) on a regenerate.
  useEffect(() => {
    if (ready) { setSuggestions(null); setPicks(null); }
  }, [ready]);

  const refresh = useCallback((direction: string) => run(direction, false), [run]);
  return { suggestions, picks, nextDate, busy, error, refresh };
}

/** The shape `SceneIdeaPicker` needs to render this data — shared so the
 *  picker's props and the hook's return value cannot drift apart. */
export type SceneSuggestionsState = ReturnType<typeof useSceneSuggestions>;
