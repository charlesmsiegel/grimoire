import { type Dispatch, useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import type { ApiPost } from "../../api/campaign";
import { campaignApi } from "../../api/campaign";
import { fileUrl } from "../../api/files";
import { viewsApi } from "../../api/views";
import { markEnd, markStart } from "../../state/perf";
import { sceneImageKey, type PlayAction, type PlayState, type SceneImage } from "./playReducer";

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
      const explicitScene = sceneJumpId ? scenes.find((s) => s.id === sceneJumpId) : null;
      const pcScene = active?.current_scene_id
        ? scenes.find((s) => s.id === active.current_scene_id)
        : null;
      const fallback = scenes.find((s) => !s.closed) ?? scenes[scenes.length - 1] ?? null;
      const targetScene = explicitScene ?? pcScene ?? fallback;
      let scene = null;
      let posts: ApiPost[] = [];
      let hasMorePosts = false;
      let images: Record<string, SceneImage> | undefined;
      if (targetScene) {
        const detail = await campaignApi.getScene(campaignId, targetScene.id);
        scene = detail.scene;
        const paginated = await campaignApi.getPostsPaginated(campaignId, targetScene.id, {
          limit: 50,
        });
        posts = paginated.posts;
        hasMorePosts = paginated.has_more;
        // Hydrate the scene's persisted images so inline images survive a
        // reload / direct visit and don't depend on live `image_ready`
        // events alone. Best-effort: a failure keeps whatever live events
        // already delivered rather than failing the scene load.
        try {
          const rows = await viewsApi.listImages(campaignId, { sceneId: targetScene.id });
          images = {};
          for (const row of rows) {
            const image: SceneImage = {
              id: row.id,
              url: fileUrl(row.file_path),
              post_id: row.post_id ?? undefined,
              prompt: row.prompt || undefined,
            };
            images[sceneImageKey(image)] = image;
          }
        } catch (err) {
          console.warn(`Failed to load scene images: ${err instanceof Error ? err.message : err}`);
        }
      }
      dispatch({ type: "loaded", pcs, activePcRef, scene, posts, hasMorePosts, images });
    } catch (e) {
      dispatch({ type: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [campaignId, sceneJumpId, dispatch]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return refresh;
}
