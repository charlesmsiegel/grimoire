/**
 * Active-campaign WebSocket provider.
 *
 * One {@link CampaignSocket} per mounted campaign. The provider routes a small
 * subset of events into the global store (drift, review-queue) so the status
 * bar updates without view code subscribing. Components that need richer event
 * access import {@link useCampaignEvent} from `./useCampaignEvent`.
 *
 * Hooks live in `./useCampaignEvent` to keep this file component-only so
 * react-refresh stays happy.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { CampaignSocket, campaignStreamUrl } from "../ws/client";
import type { WSStatus } from "../ws/client";
import { CampaignStreamContext } from "./campaignStreamContext";
import { routeToStore } from "./useCampaignEvent";
import { useStore } from "./useStore";

export function CampaignStreamProvider({
  campaignId,
  children,
}: {
  campaignId: string | null;
  children: ReactNode;
}) {
  const { dispatch } = useStore();
  const [status, setStatus] = useState<WSStatus>("idle");
  const [socket, setSocket] = useState<CampaignSocket | null>(null);

  useEffect(() => {
    if (!campaignId) {
      setStatus("idle");
      setSocket(null);
      return;
    }
    const s = new CampaignSocket({ url: campaignStreamUrl(campaignId) });
    setSocket(s);
    const offStatus = s.onStatus(setStatus);
    const offMessage = s.onMessage((m) => routeToStore(m, dispatch));
    s.connect();
    return () => {
      offStatus();
      offMessage();
      s.close();
      setSocket(null);
    };
  }, [campaignId, dispatch]);

  const value = useMemo(() => ({ socket, status, campaignId }), [socket, status, campaignId]);

  return <CampaignStreamContext.Provider value={value}>{children}</CampaignStreamContext.Provider>;
}
