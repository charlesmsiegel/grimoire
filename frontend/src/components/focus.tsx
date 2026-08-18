import {
  createContext, useCallback, useContext, useMemo, useState, type ReactNode,
} from "react";

/** Where the preference lives between reloads. A reading posture, not a route:
 *  someone who plays on a phone turns this on once and expects it to still be
 *  on tomorrow, and the restore control is always on screen, so a persisted
 *  "on" can never strand anyone in a view they cannot leave. */
const KEY = "grimoire.focus";

type Ctx = {
  /** Chrome collapsed: no app header, no context column, no scene bar. */
  focus: boolean;
  setFocus: (on: boolean) => void;
};

// An inert default rather than a throw, the same bargain `palette.tsx` makes:
// every route and every shell is rendered bare in its own test, and reading
// this preference must not require the whole app around it. Bare, the answer
// is "not in focus mode", which is the layout those tests already assert on.
const FocusCtx = createContext<Ctx>({ focus: false, setFocus: () => {} });

function load(): boolean {
  // Storage throws rather than returning null in a locked-down WebView, and a
  // display preference is not worth a blank screen.
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function FocusProvider({ children }: { children: ReactNode }) {
  const [focus, setState] = useState(load);
  const setFocus = useCallback((on: boolean) => {
    setState(on);
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* see load() */ }
  }, []);
  const value = useMemo<Ctx>(() => ({ focus, setFocus }), [focus, setFocus]);
  return <FocusCtx.Provider value={value}>{children}</FocusCtx.Provider>;
}

export function useFocus(): Ctx {
  return useContext(FocusCtx);
}

/** The one control that is on screen in focus mode, and the reason focus mode
 *  can hide everything else: a fixed pill that puts the bars back.
 *
 *  Rendered first in the shell so it is also first in the tab order — the way
 *  out of a mode has to be reachable without tabbing through the transcript
 *  that mode exists to show. */
export function FocusRestore() {
  const { focus, setFocus } = useFocus();
  if (!focus) return null;
  return (
    <button type="button" className="focus-restore" onClick={() => setFocus(false)}
            title="Show the toolbars" aria-label="Leave focus mode">
      <span aria-hidden>☰</span>
    </button>
  );
}
