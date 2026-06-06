import { type Dispatch, type MutableRefObject, useCallback, useMemo } from "react";

import { campaignApi } from "../../api/campaign";
import { newSceneApi } from "../../api/campaign/newScene";
import type { PlayAction, PlayState } from "./playReducer";

export function usePlayCommands(
  campaignId: string,
  dispatch: Dispatch<PlayAction>,
  stateRef: MutableRefObject<PlayState>,
  pendingExpressionRef: MutableRefObject<{ pcRef: string; emotion: string } | null>,
  refresh: () => Promise<void>,
) {
  const setActivePC = useCallback(
    async (ref: string) => {
      dispatch({ type: "set-active-pc", ref });
      try {
        await campaignApi.setActivePC(campaignId, ref);
      } catch {
        // Non-fatal: server still records the post under the chosen ref.
      }
      await refresh();
    },
    [campaignId, refresh, dispatch],
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
    [campaignId, stateRef, pendingExpressionRef],
  );

  const advance = useCallback(async () => {
    const scene = stateRef.current.scene;
    if (!scene) return;
    await campaignApi.advance(campaignId, scene.id);
  }, [campaignId, stateRef]);

  const direct = useCallback(
    async (text?: string) => {
      const scene = stateRef.current.scene;
      if (!scene) return;
      await campaignApi.submitDirection(campaignId, scene.id, text || undefined);
    },
    [campaignId, stateRef],
  );

  const undo = useCallback(async () => {
    await campaignApi.undo(campaignId, 1);
    await refresh();
  }, [campaignId, refresh]);

  const endScene = useCallback(async () => {
    const scene = stateRef.current.scene;
    if (!scene) return;
    await campaignApi.endScene(campaignId, scene.id);
    await refresh();
    dispatch({ type: "start-new-scene" });
    try {
      const resp = await newSceneApi.suggest(campaignId);
      dispatch({ type: "suggestions-loaded", suggestions: resp });
    } catch {
      await refresh();
    }
  }, [campaignId, refresh, stateRef, dispatch]);

  const analyzeScene = useCallback(async () => {
    const scene = stateRef.current.scene;
    if (!scene) return;
    await campaignApi.analyzeScene(campaignId, scene.id);
    await refresh();
  }, [campaignId, refresh, stateRef]);

  const deleteScene = useCallback(async () => {
    const scene = stateRef.current.scene;
    if (!scene) return;
    await campaignApi.deleteScene(campaignId, scene.id);
    await refresh();
    dispatch({ type: "start-new-scene" });
    try {
      const resp = await newSceneApi.suggest(campaignId);
      dispatch({ type: "suggestions-loaded", suggestions: resp });
    } catch {
      await refresh();
    }
  }, [campaignId, refresh, stateRef, dispatch]);

  const newScene = useCallback(async () => {
    dispatch({ type: "start-new-scene" });
    try {
      const resp = await newSceneApi.suggest(campaignId);
      dispatch({ type: "suggestions-loaded", suggestions: resp });
    } catch {
      await refresh();
    }
  }, [campaignId, dispatch, refresh]);

  const suppressDrift = useCallback(
    (ref: string) => {
      dispatch({ type: "drift-suppress", ref });
    },
    [dispatch],
  );

  // Memoize the container object, not just its members. Each command above is
  // already useCallback-stable; without this wrapper the returned object would
  // still be a fresh literal every render, which churns the `play` identity in
  // usePlayState (its useMemo depends on `commands`) on every keystroke and
  // defeats the ScenePane memoization downstream.
  return useMemo(
    () => ({
      setActivePC,
      submit,
      advance,
      direct,
      undo,
      endScene,
      analyzeScene,
      deleteScene,
      newScene,
      suppressDrift,
    }),
    [
      setActivePC,
      submit,
      advance,
      direct,
      undo,
      endScene,
      analyzeScene,
      deleteScene,
      newScene,
      suppressDrift,
    ],
  );
}
