import { type Dispatch, type MutableRefObject, useCallback } from "react";

import { campaignApi } from "../../api/campaign";
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
  }, [campaignId, refresh, stateRef]);

  const suppressDrift = useCallback(
    (ref: string) => {
      dispatch({ type: "drift-suppress", ref });
    },
    [dispatch],
  );

  return { setActivePC, submit, advance, regenerate, undo, endScene, suppressDrift };
}
