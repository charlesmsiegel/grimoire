import { createContext } from "react";

import type { CampaignSocket, WSStatus } from "../ws/client";

export interface CampaignStreamContextValue {
  socket: CampaignSocket | null;
  status: WSStatus;
  campaignId: string | null;
}

export const CampaignStreamContext = createContext<CampaignStreamContextValue>({
  socket: null,
  status: "idle",
  campaignId: null,
});
