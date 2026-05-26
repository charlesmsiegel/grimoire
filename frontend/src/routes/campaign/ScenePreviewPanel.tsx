import { type Dispatch, useCallback, useState } from "react";

import { newSceneApi } from "../../api/campaign/newScene";
import type {
  GeneratedSuggestion,
  PreviewResponse,
  SuggestResponse,
} from "../../api/campaign/types";
import type { PlayAction } from "./playReducer";

interface Props {
  campaignId: string;
  preview: PreviewResponse;
  suggestions: SuggestResponse | null;
  dispatch: Dispatch<PlayAction>;
  onSceneCreated: () => Promise<void>;
}

export function ScenePreviewPanel({
  campaignId,
  preview,
  suggestions,
  dispatch,
  onSceneCreated,
}: Props) {
  const [title, setTitle] = useState(preview.title);
  const [location, setLocation] = useState(preview.location_ref ?? "");
  const [creating, setCreating] = useState(false);

  const back = useCallback(() => {
    dispatch({ type: "back-to-picking" });
  }, [dispatch]);

  const confirm = useCallback(async () => {
    setCreating(true);
    dispatch({ type: "creating-scene" });
    try {
      // Exclude the chosen suggestion from unchosen backfill (issue #2)
      const allGenerated = suggestions?.generated ?? [];
      const chosenSummary = preview.title;
      const unchosen = allGenerated.filter(
        (g) => g.summary !== chosenSummary,
      );

      await newSceneApi.start(campaignId, {
        ...preview,
        title,
        location_ref: location || null,
        unchosen_generated: unchosen,
      });
      await onSceneCreated();
    } catch {
      // Restore to preview so user can retry (issue #7)
      dispatch({ type: "preview-loaded", preview });
    } finally {
      setCreating(false);
    }
  }, [campaignId, preview, title, location, suggestions, dispatch, onSceneCreated]);

  const sourceLabel =
    preview.first_post_source === "greeting"
      ? "Opening from greeting"
      : preview.first_post_source === "adapted_greeting"
        ? "Adapted from greeting"
        : "Opening will be generated";

  return (
    <div className="scene-preview-panel">
      <h2>Scene Preview</h2>

      <div className="preview-fields">
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Location
          <input
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </label>
        <div className="preview-row">
          <span className="preview-label">Time</span>
          <span>{preview.in_game_start ?? "Continuing from last scene"}</span>
        </div>
        <div className="preview-row">
          <span className="preview-label">Cast</span>
          <span>
            {preview.present_character_refs.length
              ? preview.present_character_refs.join(", ")
              : preview.present_pc_refs.join(", ")}
          </span>
        </div>
        <div className="preview-row">
          <span className="preview-label">First post</span>
          <span className="source-label">{sourceLabel}</span>
        </div>
      </div>

      <div className="preview-actions">
        <button onClick={back} disabled={creating}>
          Back
        </button>
        <button
          onClick={confirm}
          disabled={creating}
          className="primary"
        >
          {creating ? "Creating..." : "Start Scene"}
        </button>
      </div>
    </div>
  );
}
