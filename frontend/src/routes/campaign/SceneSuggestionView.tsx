import { type Dispatch, useCallback, useState } from "react";

import { newSceneApi } from "../../api/campaign/newScene";
import type { GeneratedSuggestion, LedgerItem, SuggestResponse } from "../../api/campaign/types";
import type { PlayAction } from "./playReducer";

interface Props {
  campaignId: string;
  suggestions: SuggestResponse;
  dispatch: Dispatch<PlayAction>;
}

export function SceneSuggestionView({ campaignId, suggestions, dispatch }: Props) {
  const [customText, setCustomText] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const pickLedgerItem = useCallback(
    async (item: LedgerItem) => {
      const resp = await newSceneApi.preview(campaignId, {
        ledger_id: item.ledger_id,
      });
      dispatch({ type: "preview-loaded", preview: resp });
    },
    [campaignId, dispatch],
  );

  const pickGenerated = useCallback(
    async (suggestion: GeneratedSuggestion) => {
      const resp = await newSceneApi.preview(campaignId, {
        generated_suggestion: suggestion,
      });
      dispatch({ type: "preview-loaded", preview: resp });
    },
    [campaignId, dispatch],
  );

  const submitCustom = useCallback(async () => {
    if (!customText.trim()) return;
    const resp = await newSceneApi.preview(campaignId, {
      custom_text: customText.trim(),
    });
    dispatch({ type: "preview-loaded", preview: resp });
  }, [campaignId, customText, dispatch]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const resp = await newSceneApi.suggest(campaignId);
      dispatch({ type: "suggestions-loaded", suggestions: resp });
    } finally {
      setRefreshing(false);
    }
  }, [campaignId, dispatch]);

  return (
    <div className="scene-suggestion-view">
      <div className="suggestion-header">
        <h2>What happens next?</h2>
        <p>Pick a suggestion, or describe the next scene yourself.</p>
      </div>

      <div className="suggestion-list">
        {suggestions.ledger_picks.map((item, i) => (
          <button
            key={item.ledger_id}
            className={`suggestion-card${item.greeting_id ? " greeting" : ""}`}
            onClick={() => pickLedgerItem(item)}
          >
            <span className="suggestion-number">{i + 1}</span>
            <span className="suggestion-text">{item.summary}</span>
            {item.greeting_id && <span className="greeting-badge">Greeting</span>}
          </button>
        ))}

        {suggestions.generated.map((g, i) => (
          <button
            key={`gen-${i}`}
            className="suggestion-card generated"
            onClick={() => pickGenerated(g)}
          >
            <span className="suggestion-number">{suggestions.ledger_picks.length + i + 1}</span>
            <span className="suggestion-text">{g.summary}</span>
          </button>
        ))}
      </div>

      <div className="suggestion-footer">
        <input
          type="text"
          value={customText}
          onChange={(e) => setCustomText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submitCustom();
          }}
          placeholder="Or describe the next scene in your own words..."
          className="custom-scene-input"
        />
        <button onClick={refresh} disabled={refreshing} className="refresh-btn">
          {refreshing ? "..." : "Refresh"}
        </button>
      </div>
    </div>
  );
}
