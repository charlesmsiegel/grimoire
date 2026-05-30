import { type Dispatch, type MutableRefObject, useCallback } from "react";

import type { ApiPost } from "../../api/campaign";
import { campaignApi } from "../../api/campaign";
import { setPcExpression } from "../../api/expressions";
import { useCampaignEvent } from "../../state/useCampaignEvent";
import type { WSMessage } from "../../ws/client";
import type { PlayAction, PlayState } from "./playReducer";

const STREAM_EVENT_TYPES = [
  "turn_started",
  "turn_failed",
  "turn_timed_out",
  "turn_cancelled",
  "pre_roll_pending",
  "token",
  "turn_complete",
  "post_appended",
  "pc_post_appended",
  "scene_started",
  "scene_ended",
  "advance_disabled",
  "advance_enabled",
  "advance_requested",
  "image_ready",
  "drift_detected",
  "scene_file_changed",
  "alternate_added",
  "speaker_round_waiting",
] as const;

export function usePlayStreamEvents(
  campaignId: string,
  dispatch: Dispatch<PlayAction>,
  stateRef: MutableRefObject<PlayState>,
  pendingExpressionRef: MutableRefObject<{ pcRef: string; emotion: string } | null>,
  refresh: () => Promise<void>,
) {
  const onEvent = useCallback(
    (message: WSMessage) => {
      const cur = stateRef.current;
      switch (message.type) {
        case "turn_started":
          // Backend has begun the turn; show the "working" placeholder until
          // the first token streams back (stream-start) or the turn settles.
          dispatch({ type: "turn-pending" });
          return;
        case "turn_failed": {
          // The backend rolled the player's post back and produced no prose.
          // Surface it so the turn doesn't silently vanish (the #1 "I sent a
          // message and got nothing" confusion).
          const reason = typeof message.reason === "string" ? message.reason : "";
          dispatch({
            type: "turn-failed",
            message: reason
              ? `The narrator couldn't respond (${reason}). Your message wasn't saved — please try again.`
              : "The narrator couldn't respond. Your message wasn't saved — please try again.",
          });
          return;
        }
        case "turn_timed_out":
          dispatch({
            type: "turn-failed",
            message:
              "The turn timed out before the narrator responded. Your message wasn't saved — please try again.",
          });
          return;
        case "turn_cancelled":
        case "pre_roll_pending":
          // Handoff events: the turn won't stream prose from here (cancel, or a
          // pre-roll confirmation prompt that its own UI now owns). Clear the
          // placeholder so it can't get stuck — but these aren't errors.
          dispatch({ type: "turn-settled" });
          return;
        case "token": {
          const turn_id = typeof message.turn_id === "string" ? message.turn_id : null;
          const delta = typeof message.delta === "string" ? message.delta : null;
          if (!turn_id || delta === null) return;
          if (!cur.streaming) dispatch({ type: "stream-start", turn_id });
          dispatch({ type: "stream-delta", turn_id, delta });
          return;
        }
        case "turn_complete": {
          const turn_id = typeof message.turn_id === "string" ? message.turn_id : null;
          if (!turn_id) return;
          dispatch({ type: "stream-end", turn_id, post: null });
          dispatch({ type: "set-next-speaker", enabled: false });
          dispatch({ type: "set-speaker-round", active: false });
          if (cur.scene) {
            void campaignApi
              .getPostsPaginated(campaignId, cur.scene.id, { limit: 50 })
              .then((result) => {
                const lastOrder = cur.posts[cur.posts.length - 1]?.order_in_scene ?? 0;
                const newPosts = result.posts.filter((p) => p.order_in_scene > lastOrder);
                for (const p of newPosts) {
                  dispatch({ type: "append-post", post: p });
                }
              })
              .catch(() => void refresh());
          } else {
            void refresh();
          }
          return;
        }
        case "post_appended":
        case "pc_post_appended": {
          const raw = (message as { post?: unknown }).post;
          if (raw && typeof raw === "object") {
            const post = raw as ApiPost;
            dispatch({ type: "append-post", post });
            if (message.type === "pc_post_appended") {
              const pending = pendingExpressionRef.current;
              if (
                pending &&
                post.author_pc_ref === pending.pcRef &&
                pending.emotion !== "neutral"
              ) {
                pendingExpressionRef.current = null;
                void setPcExpression(campaignId, pending.pcRef, {
                  emotion: pending.emotion,
                  post_id: post.id,
                  scene_id: post.scene_id,
                  turn_id: post.turn_id,
                }).catch(() => {
                  // Non-fatal: sprite stays at last-known emotion.
                });
              }
            }
            return;
          }
          void refresh();
          return;
        }
        case "scene_started":
        case "scene_ended":
        case "scene_file_changed":
        case "alternate_added":
          void refresh();
          return;
        case "advance_disabled": {
          const reason = typeof message.reason === "string" ? message.reason : "";
          dispatch({ type: "set-advance", enabled: false, reason });
          return;
        }
        case "advance_enabled":
        case "advance_requested":
          dispatch({ type: "set-advance", enabled: true, reason: "" });
          return;
        case "image_ready": {
          const id = typeof message.image_id === "string" ? message.image_id : null;
          const url = typeof message.url === "string" ? message.url : null;
          const post_id = typeof message.post_id === "string" ? message.post_id : undefined;
          if (id && url) {
            dispatch({ type: "image-ready", image: { id, url, post_id } });
          }
          return;
        }
        case "drift_detected": {
          const ref = typeof message.character_ref === "string" ? message.character_ref : null;
          const score = typeof message.score === "number" ? message.score : null;
          if (ref && score !== null) dispatch({ type: "drift", ref, score });
          return;
        }
        case "speaker_round_waiting":
          dispatch({ type: "set-next-speaker", enabled: true });
          dispatch({ type: "set-speaker-round", active: true });
          return;
      }
    },
    [refresh, campaignId, dispatch, stateRef, pendingExpressionRef],
  );

  useCampaignEvent(STREAM_EVENT_TYPES, onEvent);
}
