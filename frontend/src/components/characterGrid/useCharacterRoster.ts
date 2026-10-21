import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { api, type CharacterSummary, type EntityScope, type RosterEntry } from "../../api/client";

/** Who has actually been in a scene of this campaign, out of its roster.
 *
 *  A ROSTER ENTRY IS NOT AN APPEARANCE: `transitions.leave` drops a scene from
 *  an actor's record but keeps the record, because the entry is also what
 *  locks them to a version. This grid answers "who is in this campaign", and
 *  the answer to that is the scene list. */
function appearedIn(roster: RosterEntry[]): Set<string> {
  return new Set(roster
    .filter((r) => r.kind === "characters" && (r.scenes?.length ?? 0) > 0)
    .map((r) => r.id));
}

/** The appeared set a campaign grid can paint with before its roster read
 *  lands: the remembered roster's, or `null` ("no verdict yet"). */
function rememberedAppeared(scope: EntityScope): Set<string> | null {
  if (scope.kind !== "campaign") return null;
  const roster = api.rememberedAppearances(scope.id);
  return roster ? appearedIn(roster) : null;
}

/** `next`, keeping `prev`'s row objects wherever a row is unchanged -- and
 *  `prev` itself when nothing is.
 *
 *  A revalidation is the common case now that a revisit paints remembered
 *  rows: the answer is usually the rows already drawn, as new objects. Kept
 *  as they arrived, every memoized card would see a new `c` and re-render,
 *  and the grid would pay for its whole roster again to draw the same thing;
 *  kept like this, a confirming read renders nothing and an edit renders the
 *  card it changed. Compared as JSON because a row is plain JSON, in the
 *  server's key order both times. */
function reuseRows(prev: CharacterSummary[], next: CharacterSummary[]): CharacterSummary[] {
  if (prev.length === 0) return next;
  const byId = new Map(prev.map((r) => [r.id, r]));
  let same = prev.length === next.length;
  const out = next.map((row, i) => {
    const old = byId.get(row.id);
    if (old && JSON.stringify(old) === JSON.stringify(row)) {
      if (prev[i] !== old) same = false;
      return old;
    }
    same = false;
    return row;
  });
  return same ? prev : out;
}

/** Calls `reset` IN THE RENDER in which `key` changes, before anything
 *  commits: the route keeps the grid's instance across a world switch, and a
 *  render of the old scope's state under the new scope -- which is what an
 *  effect-time reset left for a frame -- is a grid whose every card opens a
 *  character the world being shown does not have. Each hook resets its own
 *  state this way, so every one of them is reset in the same render. */
export function useResetOnKey(key: string, reset: () => void) {
  const [shownFor, setShownFor] = useState(key);
  if (shownFor !== key) {
    setShownFor(key);
    reset();
  }
}

/** One stay on one scope -- see `useCharacterRoster`'s visit fence. */
export type Visit = { key: string };

/** The fence the grid's async work checks before it writes: `visit` is the
 *  stay that started the work, `liveVisit` the stay still showing (or `null`
 *  once the roster has unmounted). */
export type VisitFence = {
  visit: Visit;
  liveVisit: MutableRefObject<Visit | null>;
};

/** The roster a grid draws -- its rows, the campaign's appeared set, and the
 *  error banner, all claims about one scope -- plus the visit fence every
 *  other read the grid makes is checked against.
 *
 *  A scope seen before paints on the first render, from the rows the api
 *  client remembers (see "remembered reads" in `api/client.ts`), and is
 *  revalidated by the same read a first visit makes. */
export function useCharacterRoster(scope: EntityScope, onListed?: () => void) {
  const scopeKey = `${scope.kind}:${scope.id}`;
  const [chars, setChars] = useState<CharacterSummary[]>(() => api.rememberedCharacters(scope) ?? []);
  /** Whether `chars` is an answer for this scope -- remembered or read -- as
   *  opposed to the empty array a first visit starts from. Until it is, the
   *  grid draws nothing: "No characters yet" over a list that has not arrived
   *  is a claim about the world nobody made. */
  const [charsKnown, setCharsKnown] = useState(() => api.rememberedCharacters(scope) !== undefined);
  const [error, setError] = useState<unknown>(null);
  const [appeared, setAppeared] = useState<Set<string> | null>(() => rememberedAppeared(scope));
  const [rosterFailed, setRosterFailed] = useState(false);

  // A new scope replaces what is drawn IN THE SAME RENDER (see `useResetOnKey`),
  // and the unreset rows would otherwise show until the new list landed.
  useResetOnKey(scopeKey, () => {
    const remembered = api.rememberedCharacters(scope);
    setChars(remembered ?? []);
    setCharsKnown(remembered !== undefined);
    setAppeared(rememberedAppeared(scope));
    setRosterFailed(false);
    // An error is a claim about the scope it happened in.
    setError(null);
  });

  // A visit is one stay on one scope. Its token changes only when the scope's
  // KEY does, so leaving and returning to the same world cannot revive an
  // older import's refresh or navigation, while a parent rebuilding its scope
  // object per render is still the same visit -- the fence never rests on an
  // object's identity, or on useMemo keeping its cache. Cleanup fences a
  // roster that unmounts while a refresh is still pending (#136).
  const visitRef = useRef<Visit | null>(null);
  if (visitRef.current?.key !== scopeKey) visitRef.current = { key: scopeKey };
  const visit = visitRef.current;
  const liveVisit = useRef<Visit | null>(visit);
  liveVisit.current = visit;
  useEffect(() => {
    liveVisit.current = visit;
    return () => { liveVisit.current = null; };
  }, [visit]);
  const onListedRef = useRef(onListed);
  onListedRef.current = onListed;

  // `adopt`'s rule for the roster: the read is async, so this can be showing
  // another library by the time it lands, and installing A's cards under B's
  // handlers is how an action on a shared slug mutates one while displaying the
  // other.
  const reload = useCallback(() => {
    const from = scope;
    const at = visit;
    return api.listCharacters(from).then((rows) => {
      if (liveVisit.current === at) {
        setChars((prev) => reuseRows(prev, rows));
        setCharsKnown(true);
      }
    });
  }, [scope, visit]);

  useEffect(() => {
    const at = visit;
    // `alive` as well as the scope test: a reader who leaves the roster while
    // its read is in flight has left for a section that may take no module,
    // and `onListed` landing then would start the page's module reads for it.
    let alive = true;
    const current = () => alive
      && liveVisit.current === at;
    // The revalidation, when this scope painted from remembered rows, and the
    // read, when it did not -- the same call either way.
    reload()
      .catch((err: unknown) => {
        // A read that failed has not confirmed what a remembered paint drew,
        // so that goes: no rows, and the failure said out loud, rather than a
        // cast on screen that nothing vouches for.
        if (!current()) return;
        setChars([]);
        setCharsKnown(true);
        setError(err);
      })
      .finally(() => { if (current()) onListedRef.current?.(); });
    return () => { alive = false; };
  }, [reload, visit]);

  // Who is actually in a scene here. Revalidated like the rows: a remembered
  // roster lets a revisit filter on its first render, and this read replaces it.
  useEffect(() => {
    let alive = true;
    if (scope.kind !== "campaign") return;
    api.listAppearances(scope.id)
      .then((roster) => { if (alive) setAppeared(appearedIn(roster)); })
      // An unreadable roster must not hide the records it was meant to narrow:
      // the filter is withdrawn entirely -- a remembered one included, which
      // this read failed to confirm. Tracked separately from "still loading"
      // so the grid can wait for one and not the other.
      .catch(() => {
        if (!alive) return;
        setAppeared(null);
        setRosterFailed(true);
      });
    return () => { alive = false; };
  }, [scope]);

  const fence: VisitFence = { visit, liveVisit };
  return { chars, charsKnown, error, setError, appeared, rosterFailed, reload, fence };
}
