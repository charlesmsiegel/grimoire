import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/** Where the user currently is, for the header's ⌘K pill to name.
 *  Only a page that owns a campaign can know this — the router knows the cid,
 *  not the campaign's name, and nothing outside CampaignView knows which scene
 *  is open — so the page publishes it upward rather than the chrome guessing. */
export type ShellContext = { campaign: string; scene: string } | null;

type Ctx = {
  context: ShellContext;
  setContext: (next: ShellContext) => void;
  /** How full the last prompt left the context budget, 0–100. `null` when
   *  nothing has been sent yet, or when no budget is configured — the header
   *  then says nothing rather than claiming 0%. */
  usage: number | null;
  setUsage: (next: number | null) => void;
};

// A no-op default rather than a thrown error: every editor test renders its
// component bare, and a page publishing context must not require the whole
// shell to be mounted around it just to be tested.
const ShellStatusCtx = createContext<Ctx>({
  context: null, setContext: () => {}, usage: null, setUsage: () => {},
});

export function ShellStatusProvider({ children }: { children: ReactNode }) {
  const [context, setContext] = useState<ShellContext>(null);
  const [usage, setUsage] = useState<number | null>(null);
  const value = useMemo(() => ({ context, setContext, usage, setUsage }), [context, usage]);
  return <ShellStatusCtx.Provider value={value}>{children}</ShellStatusCtx.Provider>;
}

export function useShellStatus(): Ctx {
  return useContext(ShellStatusCtx);
}

/** Publish `context` for as long as the calling component is mounted.
 *
 *  The cleanup clearing it is the point: the chrome outlives every page, so
 *  without it a campaign name would keep sitting in the pill after you
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

/** Same contract for the context-budget percentage the header shows beside the
 *  model. Cleared on unmount for the same reason: a percentage that outlives
 *  the campaign it was measured in is a lie about the page you are on. */
export function usePublishContextUsage(usage: number | null): void {
  const { setUsage } = useShellStatus();
  useEffect(() => {
    setUsage(usage);
    return () => setUsage(null);
  }, [usage, setUsage]);
}
