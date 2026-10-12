import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { UNSAFE_DataRouterContext, UNSAFE_LocationContext, useBlocker } from "react-router-dom";

type ArtNavigation = {
  provided: boolean;
  locationKey: string | null;
  registerDraftGuard(guard: () => boolean, onDiscard?: () => void): () => void;
  requestArtTransition(action: () => void): void;
};
const Context = createContext<ArtNavigation>({
  provided: false,
  locationKey: null,
  registerDraftGuard: () => () => {},
  requestArtTransition: (action) => action(),
});
/** Exactly one route blocker, irrespective of how many Art entries are visible.
 *  The callback confirms before either URL or local selection changes. Reset
 *  after a refusal restores Back's history position without touching the draft.
 *  Plain MemoryRouter consumers retain local guards; production uses data routing. */
export function ArtNavigationProvider({ children }: { children: ReactNode }) {
  // Read only accepted locations. A refused Back keeps its key, draft and
  // viewer; an accepted same-record transition must retire the old surface too.
  const locationKey = useContext(UNSAFE_LocationContext)?.location.key ?? null;
  const transitioning = useRef(false);
  const guards = useRef(new Map<() => boolean, (() => void) | undefined>());
  const allow = useCallback(() => transitioning.current || Array.from(guards.current.keys()).every((guard) => guard()), []);
  const navigation = useMemo<ArtNavigation>(() => ({
    provided: true,
    locationKey,
    registerDraftGuard: (guard, onDiscard) => { guards.current.set(guard, onDiscard); return () => { guards.current.delete(guard); }; },
    requestArtTransition: (action) => {
      if (!allow()) return;
      // The local action may synchronously replace tab/version in the URL.
      // Its guards already allowed that same action; route blocking must not
      // ask again before React has committed the discarded draft's state.
      const previous = transitioning.current;
      transitioning.current = true;
      try {
        // Decisions are pure until every guard agrees. Retire all accepted
        // drafts once, including a library editor beside the clicked shelf.
        if (!previous) guards.current.forEach((discard) => discard?.());
        action();
      } finally { transitioning.current = previous; }
    },
  }), [allow, locationKey]);
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  return <Context.Provider value={navigation}>
    {dataRouter && <RouteGuard allow={allow} />}{children}
  </Context.Provider>;
}
function RouteGuard({ allow }: { allow: () => boolean }) {
  const blocker = useBlocker(() => !allow());
  useEffect(() => { if (blocker.state === "blocked") blocker.reset(); }, [blocker]);
  return null;
}
export const useArtNavigation = () => useContext(Context);
