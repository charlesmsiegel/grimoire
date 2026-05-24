import { type Dispatch, useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import { campaignApi } from "../../api/campaign";
import { markEnd, markStart } from "../../state/perf";
import type { PlayAction, PlayState } from "./playReducer";

export function usePlayDataLoader(
  campaignId: string,
  dispatch: Dispatch<PlayAction>,
  stateRef: React.MutableRefObject<PlayState>,
) {
  const [searchParams] = useSearchParams();
  const sceneJumpId = searchParams.get("scene");

  const lastSceneIdRef = useRef<string | null>(null);
  const sceneJumpPendingRef = useRef(false);

  useEffect(() => {
    const newId = stateRef.current.scene?.id ?? null;
    if (newId !== lastSceneIdRef.current) {
      if (sceneJumpPendingRef.current) {
        markEnd("scene:jump");
        sceneJumpPendingRef.current = false;
      }
      lastSceneIdRef.current = newId;
    }
  }, [stateRef.current.scene]); // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = useCallback(async () => {
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
      const explicitScene = sceneJumpId
        ? scenes.find((s) => s.id === sceneJumpId)
        : null;
      const pcScene = active?.current_scene_id
        ? scenes.find((s) => s.id === active.current_scene_id)
        : null;
      const fallback = scenes.find((s) => !s.closed) ?? scenes[scenes.length - 1] ?? null;
      const targetScene = explicitScene ?? pcScene ?? fallback;
      let scene = null;
      let posts: Awaited<ReturnType<typeof campaignApi.getScene>>["posts"] = [];
      if (targetScene) {
        const detail = await campaignApi.getScene(campaignId, targetScene.id);
        scene = detail.scene;
        posts = detail.posts;
      }
      dispatch({ type: "loaded", pcs, activePcRef, scene, posts });
    } catch (e) {
      dispatch({ type: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [campaignId, sceneJumpId, dispatch]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return refresh;
}
