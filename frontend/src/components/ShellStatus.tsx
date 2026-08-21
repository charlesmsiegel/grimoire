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
  /** The model this campaign's scene turns will actually run on (#142), when
   *  that is not simply the active connection's. `null` outside a campaign, and
   *  the header falls back to the global one. */
  sceneModel: string | null;
  setSceneModel: (next: string | null) => void;
};

// A no-op default rather than a thrown error: every editor test renders its
// component bare, and a page publishing context must not require the whole
// shell to be mounted around it just to be tested.
const ShellStatusCtx = createContext<Ctx>({
  context: null, setContext: () => {}, usage: null, setUsage: () => {},
  sceneModel: null, setSceneModel: () => {},
});

export function ShellStatusProvider({ children }: { children: ReactNode }) {
  const [context, setContext] = useState<ShellContext>(null);
  const [usage, setUsage] = useState<number | null>(null);
  const [sceneModel, setSceneModel] = useState<string | null>(null);
  const value = useMemo(
    () => ({ context, setContext, usage, setUsage, sceneModel, setSceneModel }),
    [context, usage, sceneModel]);
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

/** Same contract again, for the model the open campaign's scene turns run on.
 *
 *  The header names the model you are about to spend on, and until #142 there
 *  was only one it could be. Now a campaign can route its scene turns to a
 *  different connection than the active one — so the header would have gone on
 *  naming a model this campaign never uses, which is worse than naming none.
 *  Only the page that knows the campaign can answer, hence publishing upward
 *  rather than the chrome resolving a cascade it has no cid for.
 */
export function usePublishSceneModel(model: string | null): void {
  const { setSceneModel } = useShellStatus();
  useEffect(() => {
    setSceneModel(model);
    return () => setSceneModel(null);
  }, [model, setSceneModel]);
}
