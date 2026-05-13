import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { CampaignSocket, campaignStreamUrl, type WSMessage, type WSStatus } from "../ws/client";
import { CampaignStreamContext } from "./campaignStreamContext";
import type { Action } from "./storeContext";
import { useStore } from "./useStore";

export function useCampaignStreamStatus(): WSStatus {
  return useContext(CampaignStreamContext).status;
}

export function useCampaignId(): string | null {
  return useContext(CampaignStreamContext).campaignId;
}

/**
 * Subscribe to one or more WebSocket event types. Pass `"*"` to receive every
 * message. The handler is read through a ref so callers can pass a fresh
 * closure each render without thrashing the subscription.
 */
export function useCampaignEvent(
  types: string | readonly string[],
  handler: (message: WSMessage) => void,
): void {
  const { socket } = useContext(CampaignStreamContext);
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  const typeKey = useMemo(() => {
    if (typeof types === "string") return types;
    return [...types].sort().join("|");
  }, [types]);

  useEffect(() => {
    if (!socket) return;
    const set = typeKey === "*" ? null : new Set(typeKey.split("|"));
    const off = socket.onMessage((m) => {
      if (set === null || set.has(m.type)) handlerRef.current(m);
    });
    return off;
  }, [socket, typeKey]);
}

export function routeToStore(message: WSMessage, dispatch: (a: Action) => void): void {
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
    default:
      break;
  }
}

/**
 * Legacy hook retained for non-Provider call sites: spins up its own socket
 * and returns its status. New code should mount {@link CampaignStreamProvider}
 * and use {@link useCampaignStreamStatus} / {@link useCampaignEvent} instead.
 */
export function useCampaignStream(campaignId: string | null): WSStatus {
  const { dispatch } = useStore();
  const [status, setStatus] = useState<WSStatus>("idle");
  const dispatchCb = useCallback((m: WSMessage) => routeToStore(m, dispatch), [dispatch]);

  useEffect(() => {
    if (!campaignId) {
      setStatus("idle");
      return;
    }
    const s = new CampaignSocket({ url: campaignStreamUrl(campaignId) });
    const offStatus = s.onStatus(setStatus);
    const offMessage = s.onMessage(dispatchCb);
    s.connect();
    return () => {
      offStatus();
      offMessage();
      s.close();
    };
  }, [campaignId, dispatchCb]);

  return status;
}
