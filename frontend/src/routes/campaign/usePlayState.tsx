import { type Dispatch, useMemo, useReducer, useRef } from "react";

import {
  type PlayState,
  type PlayAction,
  type PendingTurn,
  type SceneImage,
  initialPlayState,
  playReducer,
} from "./playReducer";
import { usePlayDataLoader } from "./usePlayDataLoader";
import { usePlayStreamEvents } from "./usePlayStreamEvents";
import { usePlayCommands } from "./usePlayCommands";

export type { PlayState, PlayAction, PendingTurn, SceneImage };

export interface PlayApi {
  state: PlayState;
  dispatch: Dispatch<PlayAction>;
  setActivePC: (ref: string) => Promise<void>;
  submit: (text: string, emotion?: string) => Promise<void>;
  advance: () => Promise<void>;
  regenerate: () => Promise<void>;
  undo: () => Promise<void>;
  endScene: () => Promise<void>;
  deleteScene: () => Promise<void>;
  newScene: () => Promise<void>;
  refresh: () => Promise<void>;
  suppressDrift: (ref: string) => void;
}

export function usePlayState(campaignId: string): PlayApi {
  const [state, dispatch] = useReducer(playReducer, initialPlayState);
  const stateRef = useRef(state);
  stateRef.current = state;

  const pendingExpressionRef = useRef<{ pcRef: string; emotion: string } | null>(null);

  const refresh = usePlayDataLoader(campaignId, dispatch, stateRef);
  usePlayStreamEvents(campaignId, dispatch, stateRef, pendingExpressionRef, refresh);
  const commands = usePlayCommands(campaignId, dispatch, stateRef, pendingExpressionRef, refresh);

  return useMemo<PlayApi>(
    () => ({ state, dispatch, ...commands, refresh }),
    [state, dispatch, commands, refresh],
  );
}
