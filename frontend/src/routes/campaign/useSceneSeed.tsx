import { useEffect, useState } from "react";
import type { Location, NavigateFunction } from "react-router-dom";

export function useSceneSeed({ location, sid, cid, navigate }: {
  location: Location<unknown>;
  sid: string | undefined;
  cid: string;
  navigate: NavigateFunction;
}) {

  // Campaign-scoped, like `loaded`, `rollingFor` and the alternate set, and for
  // the reason all three carry a cid: scene ids repeat between campaigns. The
  // premise is recorded before the relist that follows it, so a reader who
  // switches campaigns during that await leaves it behind — and a sid-only
  // match then hands campaign A's generated premise to an empty scene of B's
  // that happens to wear the same id (codex review).
  const [seedPrompt, setSeedPrompt] =
    useState<{ cid: string; sid: string; prompt: string } | null>(null);
  // A scene created from the Scenes page arrives here as a NAVIGATION, not
  // through this page's own `sceneCreated`, so the premise it was created from
  // rides the history entry (see `ScenesView`'s chooser). `sid` comes off
  // `useMatch` above, so it is already the scene being landed on -- this does
  // not have to wait for `activeId` to catch up.
  //
  // Adopted once, then cleared off the entry, because history outlives the
  // visit: left in place, a premise the reader had since edited or emptied
  // would be reinstated in the opener box by any Back into this entry or any
  // reload of it, silently discarding what they typed over it. The seed is a
  // handoff, not a property of the address.
  const handoff = (location.state as { seedPrompt?: string } | null)?.seedPrompt;
  useEffect(() => {
    if (!handoff || !sid) return;
    setSeedPrompt({ cid, sid, prompt: handoff });
    // Search and hash carried along: this replace exists to drop the STATE, and
    // rebuilding the entry from the pathname alone would quietly discard the
    // rest of the address. Nothing on this route reads a query param today,
    // which is exactly why a silent drop here would go unnoticed later.
    void navigate(location.pathname + location.search + location.hash,
      { replace: true, state: null });
  }, [cid, sid, handoff, location.pathname, location.search, location.hash, navigate]);
  return { setSeedPrompt, seedPrompt };
}
