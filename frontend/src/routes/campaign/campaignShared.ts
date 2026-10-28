import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { GroupSettings, ProposalRecord } from "../../api/client";
import {
  type SceneAlternates
} from "../../api/client";
import { type RewrittenPost } from "../../components/SceneInspector";
import { sceneNumber as numberOf } from "../sceneNumber";

// The transcript's four kinds of line — a roll, a transition, a director note
// and what a shown note is labelled — are defined beside the row that renders
// them (`components/play/TranscriptPost`), and imported above.
// The window token of a transcript that has been edited optimistically. Real
// tokens come from `++windowTokenRef` and so start at 1; this identifies posts
// that no fetch produced, so every readiness gate comparing against a fetch's
// token stays correctly closed while the ownership beside it stays readable.
// Not -1, which `NO_ALTERNATES.window` already uses — matching there would be a
// coincidence rather than a meaning.
export const OPTIMISTIC_TOKEN = -2;

// A scene with no reroll alternates — the initial state and what every failed
// fetch falls back to. `cid`/`sid` are the campaign and scene the set was
// fetched FOR: switching scenes does not cancel an in-flight fetch, so scene
// A's response can land after scene B's and would otherwise show A's indices
// against B — where clicking the control swaps by index and would hit the
// wrong variant.
//
// The campaign half is not redundant. React Router reuses this component
// between /campaigns/A and /campaigns/B, so during a campaign switch `cid` is
// already B while `activeId` and the loaded set are still A's — a sid-only
// gate compares two stale values, passes, and offers A's set against B.
//
// `window` is the third part of the key and the one campaign+scene cannot
// supply: a REFRESH of the scene already on screen (every reroll, roll and
// edit ends in one) leaves both halves equal, so they cannot tell a set
// fetched for the transcript being rendered from one fetched for the transcript
// still in flight. Reroll optimistically drops the trailing run, so that window
// is exactly when the messages on screen end at the user post — and a set
// matched against them hangs the picker off that post.
export type ScopedAlternates = SceneAlternates & {
  cid: string | null; sid: string | null; window: number;
};
export const NO_ALTERNATES: ScopedAlternates = {
  cid: null, sid: null, window: -1, active: null, alternates: [],
};
// No rewritten posts, as one object, so the memoized inspector is not handed a
// fresh empty list on every transcript read of a scene with none.
export const NO_REWRITES: RewrittenPost[] = [];

// Scene history loads a page at a time from the tail (#94). A scene that has
// run for months is hundreds of posts, and mounting all of them costs a
// visible pause on every scene switch; the reader almost always wants the
// recent end. Older posts arrive by scrolling up (or the button that scroll
// falls back to), which prepends the next page and holds the viewport still.
export const GROUP_ORDER_LABEL: Record<GroupSettings["order"], string> = {
  directed: "Directed", manual: "Manual", list: "List", natural: "Natural",
};
export const PAGE_SIZE = 60;

/** The cast column's moods while there are none to show. A module-level object
 *  so the memoized column is handed the same one every render. */
export const NO_MOODS: Readonly<Record<string, string>> = Object.freeze({});
// How many extra rounds Stop will ask the server before giving the composer
// back. The cancel route waits for the run itself, but that wait is bounded, so
// a provider slow to unwind can answer while still `running`; each round is
// another full server-side wait, so a handful covers minutes without the client
// having to know what the server's timeout is.
export const STOP_POLLS = 4;
// Scrolled this close to the top, "show me more" is what the reader means.
export const NEAR_TOP_PX = 120;
// ...and this close to the bottom, they are following the reply as it streams,
// so new content should keep scrolling itself into view. Farther up than this
// they are reading something specific and must not be yanked away from it.
export const NEAR_BOTTOM_PX = 80;

// Resolves when the signal aborts, for racing a wait that has no cancellation
// of its own against Stop. `once`, so a long-lived controller does not
// accumulate listeners across the turns that share it.
export function settleOn<T>(signal: AbortSignal, value: T): Promise<T> {
  return new Promise<T>((resolve) => {
    if (signal.aborted) resolve(value);
    else signal.addEventListener("abort", () => resolve(value), { once: true });
  });
}

// Where the scene on screen lives (#87). The scene is IN the URL rather than in
// component state, so a reload, a bookmark or a shared link comes back to the
// scene the reader was in — rather than to whichever scene was edited last,
// which is all `list_scenes`' updated-descending order can offer.
export function sceneUrl(cid: string, sid: string): string {
  return `/campaigns/${encodeURIComponent(cid)}/scenes/${encodeURIComponent(sid)}`;
}

// The scene rail lists scenes most-recently-edited first, but the displayed
// number must reflect story order — the id's own leading number (its
// filename stem is "<NNN>--<date>--<slug>"), never the list position, which
// drifts out of story order as soon as any earlier scene is re-edited.
export function sceneNumber(id: string, fallback: number): number {
  return numberOf(id) ?? fallback;
}

// A superseded or narrated proposal is finished. `declined` is NOT, and used to
// be filtered out with them: the backend keeps re-streaming a declined record's
// continuation on request (mechanics.post_roll_proposal), so a decline whose
// narration never landed is recoverable — but only if something renders it.
// Dropping it stranded the record with no way back, which Stop made easy to
// reach and an upstream failure could always reach; RollProposal now offers it
// the same Continue narration it offers a stopped accept.
export function liveProposal(record: ProposalRecord | null): ProposalRecord | null {
  return record && record.status !== "superseded" && record.status !== "narrated"
    ? record : null;
}

// All three sorts put the most-recent thing first, matching "updated" (the
// API's own order, most-recently-edited first — the existing default):
// "date" is latest in-story date first, "order" is the highest scene number
// first. Scenes with no in-story date always sort after every dated scene.

/** A function whose identity never changes and which always runs the latest
 *  closure it was handed.
 *
 *  For the handlers this view passes to memoized children — the transcript
 *  rows, the context column, the inspector. Every one of them is declared in
 *  the render body and closes over this render's state, so as a plain prop it
 *  is a new function on every keystroke and every streamed delta, and a
 *  memoized child compares it, finds it changed and re-renders anyway. Rather
 *  than a dependency list per handler, which is where a stale closure would
 *  come from, the ref follows the render and the wrapper never moves.
 *
 *  Updated in a layout effect, not during render: the wrapper is only ever
 *  called from an event handler, after the commit that installed its closure,
 *  and a render that React throws away must not leave its closure behind. */
export function useStableCallback<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const ref = useRef(fn);
  useLayoutEffect(() => { ref.current = fn; });
  return useCallback((...args: A) => ref.current(...args), []);
}

/** `useStableCallback` for a whole set of handlers at once: an object built on
 *  the first render whose members forward to the latest render's. The keys are
 *  fixed by that first render, so a call site hands in the same set every
 *  time. */
export type StableHandlers<T extends { [K in keyof T]: (...args: never[]) => void }> = {
  [K in keyof T]: (...args: Parameters<T[K]>) => void;
};

export function useStableHandlers<T extends { [K in keyof T]: (...args: never[]) => void }>(
  handlers: T,
): StableHandlers<T> {
  const ref = useRef(handlers);
  useLayoutEffect(() => { ref.current = handlers; });
  const [stable] = useState(() => {
    const out: StableHandlers<T> = { ...handlers };
    for (const key in handlers) {
      if (!Object.prototype.hasOwnProperty.call(handlers, key)) continue;
      out[key] = (...args: Parameters<T[typeof key]>) => ref.current[key](...args);
    }
    return out;
  });
  return stable;
}

/** A manual task in flight: which surface started it, in which scene. */
export function taskKey(origin: "strip" | "inspector", cid: string, sid: string, task: string): string {
  return `${origin}:${cid}/${sid}:${task}`;
}