import type { ApiPost, ApiScene, PCEntry } from "../../api/campaign";
import type { PreviewResponse, SuggestResponse } from "../../api/campaign/types";

export interface PendingTurn {
  turn_id: string;
  text: string;
  tracker_text?: string;
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
  // Transient, non-fatal notice for a turn that failed or timed out. Unlike
  // ``error`` (which replaces the whole view on a load failure), this renders
  // as an inline banner above the input so the user knows the turn didn't
  // silently vanish, and clears as soon as the next turn starts.
  turnError: string | null;
  streaming: PendingTurn | null;
  // True between ``turn_started`` and the first streamed token (or a
  // terminal turn event). Drives the "narrator is working" placeholder so
  // the user sees the backend is busy before any prose arrives.
  awaitingResponse: boolean;
  advanceEnabled: boolean;
  advanceReason: string;
  nextSpeakerEnabled: boolean;
  speakerRoundActive: boolean;
  images: Record<string, SceneImage>;
  driftWarnings: Record<string, { score: number; suppressed: boolean }>;
  hasMorePosts: boolean;
  mode: "play" | "suggesting" | "picking" | "previewing" | "creating";
  suggestions: SuggestResponse | null;
  preview: PreviewResponse | null;
}

export type PlayAction =
  | { type: "loading" }
  | {
      type: "loaded";
      pcs: PCEntry[];
      activePcRef: string | null;
      scene: ApiScene | null;
      posts: ApiPost[];
      hasMorePosts?: boolean;
    }
  | { type: "error"; message: string }
  | { type: "set-active-pc"; ref: string }
  | { type: "append-post"; post: ApiPost }
  | { type: "turn-pending" }
  | { type: "turn-settled" }
  | { type: "turn-failed"; message: string }
  | { type: "clear-turn-error" }
  | { type: "stream-start"; turn_id: string }
  | { type: "stream-delta"; turn_id: string; delta: string }
  | { type: "stream-end"; turn_id: string; post: ApiPost | null }
  | { type: "stream-end-if-turn"; turn_id: string }
  | { type: "set-scene"; scene: ApiScene; posts: ApiPost[] }
  | { type: "set-advance"; enabled: boolean; reason: string }
  | { type: "image-ready"; image: SceneImage }
  | { type: "drift"; ref: string; score: number }
  | { type: "drift-suppress"; ref: string }
  | { type: "start-new-scene" }
  | { type: "suggestions-loaded"; suggestions: SuggestResponse }
  | { type: "preview-loaded"; preview: PreviewResponse }
  | { type: "back-to-picking" }
  | { type: "creating-scene" }
  | { type: "prepend-posts"; posts: ApiPost[]; hasMore: boolean }
  | { type: "set-next-speaker"; enabled: boolean }
  | { type: "set-speaker-round"; active: boolean };

export const initialPlayState: PlayState = {
  pcs: [],
  activePcRef: null,
  scene: null,
  posts: [],
  loading: true,
  error: null,
  turnError: null,
  streaming: null,
  awaitingResponse: false,
  advanceEnabled: false,
  advanceReason: "",
  nextSpeakerEnabled: false,
  speakerRoundActive: false,
  images: {},
  driftWarnings: {},
  hasMorePosts: true,
  mode: "play",
  suggestions: null,
  preview: null,
};

function stripTrackerBlocks(text: string): { stripped: string; captured: string } {
  const pattern = /<!-- TRACKER -->([\s\S]*?)<!-- \/TRACKER -->/g;
  let captured = "";
  const stripped = text.replace(pattern, (_, inner: string) => {
    captured += inner;
    return "";
  });
  return { stripped, captured };
}

export function playReducer(state: PlayState, action: PlayAction): PlayState {
  switch (action.type) {
    case "loading":
      return { ...state, loading: true, error: null };
    case "loaded": {
      const presentCount = action.scene?.present_pc_refs.length ?? 0;
      const stickyDisabled = state.advanceReason !== "" && !state.advanceEnabled;
      let posts = action.posts;
      const newSceneId = action.scene?.id ?? null;
      const oldSceneId = state.scene?.id ?? null;
      if (newSceneId && newSceneId === oldSceneId) {
        const snapshotIds = new Set(action.posts.map((p) => p.id));
        const minNewOrder = action.posts.length > 0
          ? Math.min(...action.posts.map((p) => p.order_in_scene))
          : 0;
        const extra = state.posts.filter(
          (p) =>
            p.scene_id === newSceneId &&
            !snapshotIds.has(p.id) &&
            p.order_in_scene >= minNewOrder,
        );
        if (extra.length > 0) posts = [...action.posts, ...extra];
      }
      return {
        ...state,
        loading: false,
        error: null,
        pcs: action.pcs,
        activePcRef: action.activePcRef,
        scene: action.scene,
        posts,
        hasMorePosts: action.hasMorePosts ?? true,
        advanceEnabled: stickyDisabled ? false : presentCount >= 2,
        advanceReason: stickyDisabled ? state.advanceReason : "",
        mode: "play",
        suggestions: null,
        preview: null,
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
    case "turn-pending":
      return { ...state, awaitingResponse: true, turnError: null };
    case "turn-settled":
      return { ...state, awaitingResponse: false };
    case "turn-failed":
      return { ...state, awaitingResponse: false, streaming: null, turnError: action.message };
    case "clear-turn-error":
      return state.turnError === null ? state : { ...state, turnError: null };
    case "stream-start":
      return {
        ...state,
        awaitingResponse: false,
        turnError: null,
        streaming: { turn_id: action.turn_id, text: "" },
      };
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
      if (!streaming || streaming.turn_id !== action.turn_id)
        return { ...state, streaming: null, awaitingResponse: false };
      let posts = state.posts;
      if (action.post && !posts.some((p) => p.id === action.post!.id)) {
        posts = [...posts, action.post];
      }
      return { ...state, streaming: null, awaitingResponse: false, posts };
    }
    case "stream-end-if-turn": {
      // Clear the streaming indicator only if it belongs to this turn — used by
      // a failed per-post reroll, where the WS alternate_added event (which
      // clears on success) never arrives. The match runs against live reducer
      // state, not a render-time closure, so a reroll that streamed partial text
      // before failing is reliably cleared while a concurrent normal turn's
      // stream is left untouched.
      if (state.streaming?.turn_id !== action.turn_id) return state;
      return { ...state, streaming: null, awaitingResponse: false };
    }
    case "set-scene":
      return {
        ...state,
        scene: action.scene,
        posts: action.posts,
        hasMorePosts: true,
        advanceEnabled: action.scene.present_pc_refs.length >= 2,
        advanceReason: "",
        mode: "play",
        suggestions: null,
        preview: null,
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
    case "start-new-scene":
      return { ...state, mode: "suggesting", suggestions: null, preview: null };
    case "suggestions-loaded":
      return { ...state, mode: "picking", suggestions: action.suggestions };
    case "preview-loaded":
      return { ...state, mode: "previewing", preview: action.preview };
    case "back-to-picking":
      return { ...state, mode: "picking", preview: null };
    case "creating-scene":
      return { ...state, mode: "creating" };
    case "prepend-posts": {
      const existingIds = new Set(state.posts.map((p) => p.id));
      const novel = action.posts.filter((p) => !existingIds.has(p.id));
      return {
        ...state,
        posts: [...novel, ...state.posts],
        hasMorePosts: action.hasMore,
      };
    }
    case "set-next-speaker":
      return { ...state, nextSpeakerEnabled: action.enabled };
    case "set-speaker-round":
      return { ...state, speakerRoundActive: action.active };
  }
}
