/**
 * Loads + tracks the play state for a single campaign:
 *   - PCs and which one is currently active
 *   - the active scene (per-PC; first PC's current scene wins by default)
 *   - posts in the active scene + streaming buffer for the in-flight turn
 *   - generated images keyed by post id
 *   - whether the Advance button is enabled
 *
 * Stream events drive incremental updates so we don't refetch the scene every
 * turn. We do refetch when an event signals state we don't model locally yet
 * (e.g. a new scene started, or a retcon).
 */

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import { campaignApi, type ApiPost, type ApiScene, type PCEntry } from "../../api/campaign";
import { setPcExpression } from "../../api/expressions";
import { markEnd, markStart } from "../../state/perf";
import { useCampaignEvent } from "../../state/useCampaignEvent";
import type { WSMessage } from "../../ws/client";

export interface PendingTurn {
  turn_id: string;
  text: string;
  /** Concatenated JSON content of any `<!-- TRACKER -->...<!-- /TRACKER -->`
   * blocks stripped from the stream (extraction-modes `TOGETHER` mode). The
   * backend route to receive this is not yet wired; the buffer exists so the
   * markers don't leak into the narration display in the meantime. */
  tracker_text?: string;
}

/** Strip complete `<!-- TRACKER -->...<!-- /TRACKER -->` blocks from a chunk
 * of streamed text. Returns the stripped text plus the concatenated inner
 * content of every block that was matched.
 */
function stripTrackerBlocks(text: string): { stripped: string; captured: string } {
  const pattern = /<!-- TRACKER -->([\s\S]*?)<!-- \/TRACKER -->/g;
  let captured = "";
  const stripped = text.replace(pattern, (_, inner: string) => {
    captured += inner;
    return "";
  });
  return { stripped, captured };
}

export interface SceneImage {
  id: string;
  url: string;
  post_id?: string;
}

export interface PlayState {
  pcs: PCEntry[];
  activePcRef: string | null;
  scene: ApiScene | null;
  posts: ApiPost[];
  loading: boolean;
  error: string | null;
  streaming: PendingTurn | null;
  advanceEnabled: boolean;
  advanceReason: string;
  images: Record<string, SceneImage>;
  driftWarnings: Record<string, { score: number; suppressed: boolean }>;
}

type Action =
  | { type: "loading" }
  | {
      type: "loaded";
      pcs: PCEntry[];
      activePcRef: string | null;
      scene: ApiScene | null;
      posts: ApiPost[];
    }
  | { type: "error"; message: string }
  | { type: "set-active-pc"; ref: string }
  | { type: "append-post"; post: ApiPost }
  | { type: "stream-start"; turn_id: string }
  | { type: "stream-delta"; turn_id: string; delta: string }
  | { type: "stream-end"; turn_id: string; post: ApiPost | null }
  | { type: "set-scene"; scene: ApiScene; posts: ApiPost[] }
  | { type: "set-advance"; enabled: boolean; reason: string }
  | { type: "image-ready"; image: SceneImage }
  | { type: "drift"; ref: string; score: number }
  | { type: "drift-suppress"; ref: string };

const initial: PlayState = {
  pcs: [],
  activePcRef: null,
  scene: null,
  posts: [],
  loading: true,
  error: null,
  streaming: null,
  advanceEnabled: false,
  advanceReason: "",
  images: {},
  driftWarnings: {},
};

function reducer(state: PlayState, action: Action): PlayState {
  switch (action.type) {
    case "loading":
      return { ...state, loading: true, error: null };
    case "loaded": {
      // Preserve the server-pushed advance_disabled reason if one is set:
      // every refresh() would otherwise recompute `advanceEnabled` from
      // present_pc_refs and reset `advanceReason` to "", losing context the
      // server gave us about *why* the button is disabled.
      const presentCount = action.scene?.present_pc_refs.length ?? 0;
      const stickyDisabled = state.advanceReason !== "" && !state.advanceEnabled;
      return {
        ...state,
        loading: false,
        error: null,
        pcs: action.pcs,
        activePcRef: action.activePcRef,
        scene: action.scene,
        posts: action.posts,
        advanceEnabled: stickyDisabled ? false : presentCount >= 2,
        advanceReason: stickyDisabled ? state.advanceReason : "",
      };
    }
    case "error":
      return { ...state, loading: false, error: action.message };
    case "set-active-pc":
      return { ...state, activePcRef: action.ref };
    case "append-post":
      if (state.scene && action.post.scene_id !== state.scene.id) return state;
      if (state.posts.some((p) => p.id === action.post.id)) return state;
      return { ...state, posts: [...state.posts, action.post] };
    case "stream-start":
      return { ...state, streaming: { turn_id: action.turn_id, text: "" } };
    case "stream-delta": {
      if (!state.streaming || state.streaming.turn_id !== action.turn_id) return state;
      const combined = state.streaming.text + action.delta;
      const { stripped, captured } = stripTrackerBlocks(combined);
      return {
        ...state,
        streaming: {
          ...state.streaming,
          text: stripped,
          tracker_text: captured
            ? (state.streaming.tracker_text ?? "") + captured
            : state.streaming.tracker_text,
        },
      };
    }
    case "stream-end": {
      const streaming = state.streaming;
      if (!streaming || streaming.turn_id !== action.turn_id) return { ...state, streaming: null };
      let posts = state.posts;
      if (action.post && !posts.some((p) => p.id === action.post!.id)) {
        posts = [...posts, action.post];
      }
      return { ...state, streaming: null, posts };
    }
    case "set-scene":
      return {
        ...state,
        scene: action.scene,
        posts: action.posts,
        advanceEnabled: action.scene.present_pc_refs.length >= 2,
        advanceReason: "",
      };
    case "set-advance":
      return { ...state, advanceEnabled: action.enabled, advanceReason: action.reason };
    case "image-ready":
      return { ...state, images: { ...state.images, [action.image.id]: action.image } };
    case "drift": {
      const prev = state.driftWarnings[action.ref];
      return {
        ...state,
        driftWarnings: {
          ...state.driftWarnings,
          [action.ref]: { score: action.score, suppressed: prev?.suppressed ?? false },
        },
      };
    }
    case "drift-suppress": {
      const prev = state.driftWarnings[action.ref];
      if (!prev) return state;
      return {
        ...state,
        driftWarnings: { ...state.driftWarnings, [action.ref]: { ...prev, suppressed: true } },
      };
    }
  }
}

const STREAM_EVENT_TYPES = [
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
] as const;

export interface PlayApi {
  state: PlayState;
  setActivePC: (ref: string) => Promise<void>;
  submit: (text: string, emotion?: string) => Promise<void>;
  advance: () => Promise<void>;
  regenerate: () => Promise<void>;
  undo: () => Promise<void>;
  endScene: () => Promise<void>;
  refresh: () => Promise<void>;
  suppressDrift: (ref: string) => void;
}

export function usePlayState(campaignId: string): PlayApi {
  const [state, dispatch] = useReducer(reducer, initial);
  const stateRef = useRef(state);
  stateRef.current = state;

  // Holds the PC-picker emotion until the next pc_post_appended for the
  // matching PC, at which point we PATCH expression state with the post id
  // the backend just minted.
  const pendingExpressionRef = useRef<{ pcRef: string; emotion: string } | null>(null);

  // ``/campaigns/:id?scene=...`` lets TimelineView and other surfaces jump
  // the play view to a specific scene (spec frontend §9). We persist the
  // query param so a reload re-applies it.
  const [searchParams] = useSearchParams();
  const sceneJumpId = searchParams.get("scene");

  // Spec 14 §Performance budgets: scene jump < 500ms to render. We mark on
  // every scene-id transition (initial load, refresh, or scene_started event).
  // The reducer is what actually flips state.scene, so we close the span the
  // first effect after the new id appears.
  const lastSceneIdRef = useRef<string | null>(null);
  const sceneJumpPendingRef = useRef(false);
  useEffect(() => {
    const newId = state.scene?.id ?? null;
    if (newId !== lastSceneIdRef.current) {
      if (sceneJumpPendingRef.current) {
        markEnd("scene:jump");
        sceneJumpPendingRef.current = false;
      }
      lastSceneIdRef.current = newId;
    }
  }, [state.scene]);

  const refresh = useCallback(async () => {
    // Treat every refresh as a potential scene transition — the effect above
    // only closes the span when scene.id actually changes, so a refresh that
    // returns the same scene is a no-op for the measurement.
    if (!sceneJumpPendingRef.current) {
      markStart("scene:jump");
      sceneJumpPendingRef.current = true;
    }
    dispatch({ type: "loading" });
    try {
      const pcs = await campaignApi.listPCs(campaignId);
      const active = pcs.find((p) => p.active) ?? pcs[0] ?? null;
      const activePcRef = active?.character_ref ?? null;
      const scenes = await campaignApi.listScenes(campaignId);
      // Scene selection priority (spec frontend §8/§9):
      //   1. ``?scene=`` query param (TimelineView "Jump to scene").
      //   2. Active PC's ``current_scene_id`` (rich PC switcher restores
      //      that PC's last position when the user switches).
      //   3. First open scene; else last scene.
      const explicitScene = sceneJumpId
        ? scenes.find((s) => s.id === sceneJumpId)
        : null;
      const pcScene = active?.current_scene_id
        ? scenes.find((s) => s.id === active.current_scene_id)
        : null;
      const fallback = scenes.find((s) => !s.closed) ?? scenes[scenes.length - 1] ?? null;
      const targetScene = explicitScene ?? pcScene ?? fallback;
      let scene: ApiScene | null = null;
      let posts: ApiPost[] = [];
      if (targetScene) {
        const detail = await campaignApi.getScene(campaignId, targetScene.id);
        scene = detail.scene;
        posts = detail.posts;
      }
      dispatch({ type: "loaded", pcs, activePcRef, scene, posts });
    } catch (e) {
      dispatch({ type: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [campaignId, sceneJumpId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onEvent = useCallback(
    (message: WSMessage) => {
      const cur = stateRef.current;
      switch (message.type) {
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
          void refresh();
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
      }
    },
    [refresh, campaignId],
  );

  useCampaignEvent(STREAM_EVENT_TYPES, onEvent);

  const setActivePC = useCallback(
    async (ref: string) => {
      dispatch({ type: "set-active-pc", ref });
      try {
        await campaignApi.setActivePC(campaignId, ref);
      } catch {
        // Non-fatal: server still records the post under the chosen ref.
      }
      // Refresh so the active scene re-orients to the new PC's
      // ``current_scene_id`` (spec frontend §8).
      await refresh();
    },
    [campaignId, refresh],
  );

  const submit = useCallback(
    async (text: string, emotion?: string) => {
      const pcRef = stateRef.current.activePcRef;
      if (!pcRef || !text.trim()) return;
      if (emotion && emotion !== "neutral") {
        pendingExpressionRef.current = { pcRef, emotion };
      }
      try {
        await campaignApi.submitTurn(campaignId, pcRef, text);
      } catch (err) {
        pendingExpressionRef.current = null;
        throw err;
      }
    },
    [campaignId],
  );

  const advance = useCallback(async () => {
    const scene = stateRef.current.scene;
    if (!scene) return;
    await campaignApi.advance(campaignId, scene.id);
  }, [campaignId]);

  const regenerate = useCallback(async () => {
    await campaignApi.regenerate(campaignId);
  }, [campaignId]);

  const undo = useCallback(async () => {
    await campaignApi.undo(campaignId, 1);
    await refresh();
  }, [campaignId, refresh]);

  const endScene = useCallback(async () => {
    const scene = stateRef.current.scene;
    if (!scene) return;
    await campaignApi.endScene(campaignId, scene.id);
    await refresh();
  }, [campaignId, refresh]);

  const suppressDrift = useCallback((ref: string) => {
    dispatch({ type: "drift-suppress", ref });
  }, []);

  return useMemo<PlayApi>(
    () => ({
      state,
      setActivePC,
      submit,
      advance,
      regenerate,
      undo,
      endScene,
      refresh,
      suppressDrift,
    }),
    [state, setActivePC, submit, advance, regenerate, undo, endScene, refresh, suppressDrift],
  );
}
