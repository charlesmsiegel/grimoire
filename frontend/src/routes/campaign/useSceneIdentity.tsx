import { useCallback, useRef, useState } from "react";
import type { SceneKey } from "./campaignTypes";

export function useSceneIdentity({ cid }: {
  cid: string;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  // `activeId` for callers that must know what is selected NOW rather than what
  // was selected when they captured it — an awaited handler holds a stale one.
  // Every write goes through `setActive` so the two cannot drift: they are one
  // fact, and the rename/delete/first-send paths set it without going anywhere
  // near `selectScene`.
  const activeIdRef = useRef<string | null>(null);
  // Which campaign the active scene belongs to. Scene ids repeat freely between
  // campaigns, so "the sid in the URL is already the active one" is only true
  // if the active scene is also THIS campaign's — A/scenes/s1 → B/scenes/s1
  // otherwise reads as "already there" and B's transcript never loads.
  // Taken from the render that selected it, not `cidRef`: a handler awaited
  // across a campaign switch belongs to the campaign it started in, which is
  // the campaign whose scene it is selecting.
  const activeCidRef = useRef<string | null>(null);
  const selectIdentity = useCallback((scope: SceneKey | null) => {
    const id = scope?.sid ?? null;
    activeIdRef.current = id;
    activeCidRef.current = scope?.cid ?? null;
    setActiveId(id);
  }, []);
  // Same idea for the campaign: router reuses this component across
  // /campaigns/A → /campaigns/B, and scene ids repeat freely between
  // campaigns, so "still the same sid" is not "still the same scene". A
  // handler that captured A must compare against where the router is NOW,
  // which the render it was created in cannot tell it.
  const cidRef = useRef(cid);
  cidRef.current = cid;
  const currentCampaign = useCallback(() => cidRef.current, []);
  const selected = useCallback((): SceneKey | null => activeIdRef.current && activeCidRef.current
    ? { cid: activeCidRef.current, sid: activeIdRef.current } : null, []);
  return { activeId, currentCampaign, selected, selectIdentity };

}
