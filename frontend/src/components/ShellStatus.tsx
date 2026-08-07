import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/** Where the user currently is, for the global status bar to name.
 *  Only a page that owns a campaign can know this — the router knows the cid,
 *  not the campaign's name, and nothing outside CampaignView knows which scene
 *  is open — so the page publishes it upward rather than the bar guessing. */
export type ShellContext = { campaign: string; scene: string } | null;

type Ctx = { context: ShellContext; setContext: (next: ShellContext) => void };

// A no-op default rather than a thrown error: every editor test renders its
// component bare, and a page publishing context must not require the whole
// shell to be mounted around it just to be tested.
const ShellStatusCtx = createContext<Ctx>({ context: null, setContext: () => {} });

export function ShellStatusProvider({ children }: { children: ReactNode }) {
  const [context, setContext] = useState<ShellContext>(null);
  const value = useMemo(() => ({ context, setContext }), [context]);
  return <ShellStatusCtx.Provider value={value}>{children}</ShellStatusCtx.Provider>;
}

export function useShellStatus(): Ctx {
  return useContext(ShellStatusCtx);
}

/** Publish `context` for as long as the calling component is mounted.
 *
 *  The cleanup clearing it is the point: the status bar outlives every page,
 *  so without it a campaign name would keep sitting in the bar after you
 *  navigated to Config. Depending on the two strings rather than the object
 *  keeps a caller that rebuilds the literal every render from looping. */
export function usePublishShellContext(context: ShellContext): void {
  const { setContext } = useShellStatus();
  const campaign = context?.campaign ?? "";
  const scene = context?.scene ?? "";
  useEffect(() => {
    if (!campaign) return;
    setContext({ campaign, scene });
    return () => setContext(null);
  }, [campaign, scene, setContext]);
}
