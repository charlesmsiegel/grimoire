/**
 * Subscribes the active-campaign WebSocket and routes events into the store.
 */

import { useEffect, useRef, useState } from "react";

import { CampaignSocket, campaignStreamUrl, type WSMessage, type WSStatus } from "../ws/client";
import { useStore } from "./useStore";

export function useCampaignStream(campaignId: string | null): WSStatus {
  const { dispatch } = useStore();
  const socketRef = useRef<CampaignSocket | null>(null);
  const [status, setStatus] = useState<WSStatus>("idle");

  useEffect(() => {
    if (!campaignId) {
      setStatus("idle");
      return;
    }
    const socket = new CampaignSocket({ url: campaignStreamUrl(campaignId) });
    socketRef.current = socket;
    const offStatus = socket.onStatus(setStatus);
    const offMessage = socket.onMessage((message) => handleMessage(message, dispatch));
    socket.connect();
    return () => {
      offStatus();
      offMessage();
      socket.close();
      socketRef.current = null;
    };
  }, [campaignId, dispatch]);

  return status;
}

function handleMessage(
  message: WSMessage,
  dispatch: ReturnType<typeof useStore>["dispatch"],
): void {
  switch (message.type) {
    case "drift_detected": {
      const ref = typeof message.character_ref === "string" ? message.character_ref : null;
      const score = typeof message.score === "number" ? message.score : null;
      if (ref && score !== null) {
        dispatch({ type: "drift-alert", alert: { character_ref: ref, score } });
      }
      break;
    }
    case "review_item_added": {
      const item = message.item;
      if (item && typeof item === "object") {
        const obj = item as Record<string, unknown>;
        if (typeof obj.id === "string" && typeof obj.summary === "string") {
          dispatch({ type: "push-review", item: { id: obj.id, summary: obj.summary } });
        }
      }
      break;
    }
    case "turn_complete": {
      // Future: extract budget/cost into status. For now we just clear the queue depth bump.
      break;
    }
    default:
      // Ignored events propagate via direct subscriptions in view components.
      break;
  }
}
