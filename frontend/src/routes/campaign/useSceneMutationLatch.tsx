import { useCallback, useRef, useState } from "react";

export function useSceneMutationLatch({ cid, activeId }: {
  cid: string;
  activeId: string | null;
}) {

  // How many scene-renaming requests are in flight. A rename is a PUT that
  // moves the scene file, and until it answers nobody knows which id is
  // current — so a turn started inside that window can be handed the old one
  // and have its write land nowhere. `streamingId` closes the other direction
  // (rename blocked during a turn); this closes turn-blocked-during-a-rename
  // (review, #95). A counter, not a flag: the rail and the two date controls
  // can overlap, and the last one to finish must not unblock the others.
  //
  // Still a list of surfaces, which is the same shape of fragility the lock
  // has — but the guard is now at the one place turns start, so a surface that
  // forgets to report is a narrower miss than one that forgets to lock.
  const [renamesInFlight, setRenamesInFlight] = useState(0);
  // "A turn can still write to the scene on screen." NOT `busy`, which clears
  // the instant the socket dies while the shielded abort write lands seconds
  // later — the whole point of `streamingId`.
  //
  // Named and derived once because review has now found four surfaces sitting
  // on the wrong one of these, one at a time: rename, the two date controls,
  // End scene, and the dice roll. Every control that mutates *this* scene's
  // transcript without going through `runStream` reads this, so the next one
  // is a missing `sceneLocked` rather than a fifth rediscovery of the rule.
  //
  // It covers a run this component never started, too, and that is what makes
  // it right once a turn outlives its request: `attachToRun` sets
  // `streamingId` for an ADOPTED run exactly as `runStream` does for one it
  // began, so a turn recovered after a reload -- or one still generating when
  // the tab came back -- locks the scene the same way. The server refuses
  // these mutations either way (`scene_busy`, keyed on the `turn`/`review`
  // exclusion key); this is the affordance that stops the player being told no.
  // Declared just below `review`, because a live REVIEW holds the scene on
  // exactly the same terms -- see there.
  const markRenaming = useCallback(
    (active: boolean) => setRenamesInFlight((n) => n + (active ? 1 : -1)), []); // one getSceneChecks per popover session
  // The in-flight-store-mutation latch, shared by the dice roll, the check roll
  // and the alternate swap. Scoped to the scene that took it rather than being a
  // bare boolean: it was component-wide, so an operation belonging to a scene
  // the reader has left held every control in the scene they entered — Send,
  // Retry, reroll, edit, roll — until an unrelated request settled. Review found
  // that against the swap; the two roll paths had it already.
  //
  // The token is what makes releasing safe: a slow operation finishing after a
  // newer one took the latch must not clear the newer one's.
  const [rollingFor, setRollingFor] =
    useState<{ cid: string; sid: string; token: number } | null>(null);
  const rollTokenRef = useRef(0);
  const rolling = !!rollingFor && rollingFor.cid === cid && rollingFor.sid === activeId;

  function takeRollLatch(forSid: string) {
    const token = ++rollTokenRef.current;
    setRollingFor({ cid, sid: forSid, token });
    return () => setRollingFor((cur) => (cur?.token === token ? null : cur));
  }
  return { rolling, markRenaming, takeRollLatch, renamesInFlight };
}
