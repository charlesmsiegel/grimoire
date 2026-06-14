/**
 * Context Inspector panel — live preview of everything the LLM will see on
 * the next turn: total token budget, a comprehensive source list (each row
 * expandable to its precise text, with pin/exclude), and an Expand button
 * that opens a full-page overlay for deep reading.
 *
 * Mounts inside the campaign Play view; the host supplies the draft player
 * input + session id, which this panel debounces into POST /preview calls.
 */

import { useCallback, useState } from "react";

import { inspectorApi, type ContextSourceExplanation } from "../../../api/inspector";
import { useResource } from "../../../api/useResource";
import { InspectorOverlay } from "./InspectorOverlay";
import { SourceList } from "./SourceList";
import { TokenBars } from "./TokenBars";
import { useLivePreview } from "./useLivePreview";

interface Props {
  campaignId: string;
  playerInput: string;
  sessionId: string;
  pcRef?: string | null;
  enabled?: boolean;
}

export function InspectorPanel({
  campaignId,
  playerInput,
  sessionId,
  pcRef,
  enabled = true,
}: Props) {
  const live = useLivePreview({
    campaignId,
    playerInput,
    sessionId,
    pcRef: pcRef ?? undefined,
    enabled,
  });

  const [expanded, setExpanded] = useState(false);

  const handle = live.handle;
  const explain = useResource(
    useCallback(
      () =>
        handle
          ? inspectorApi.explain(campaignId, handle, sessionId)
          : Promise.resolve<ContextSourceExplanation[]>([]),
      [campaignId, sessionId, handle],
    ),
  );
  const explanations = explain.data ?? [];
  const explainErr = explain.error?.message ?? null;

  const handleChanged = useCallback(() => {
    live.refresh();
  }, [live]);

  return (
    <aside className="inspector-panel" aria-label="Context inspector">
      <div className="scene-setting-block" aria-label="Context inspector">
        {live.error && <p className="inspector-error">{live.error}</p>}

        <div className="scene-setting-entry scene-setting-entry-full">
          <span className="scene-setting-label">Next-post context{live.loading ? " …" : ""}</span>
          <TokenBars summary={live.summary} sources={explanations} loading={live.loading} />
        </div>

        <div className="scene-setting-entry scene-setting-entry-full inspector-tab-body">
          {explainErr && <p className="inspector-error">{explainErr}</p>}
          <SourceList campaignId={campaignId} sources={explanations} onChanged={handleChanged} />
        </div>

        {live.handle && explanations.length > 0 && (
          <div className="scene-setting-entry scene-setting-entry-full">
            <button
              type="button"
              className="inspector-expand-btn"
              onClick={() => setExpanded(true)}
            >
              ⤢ Expand full context
            </button>
          </div>
        )}
      </div>

      {expanded && live.handle && (
        <InspectorOverlay
          campaignId={campaignId}
          sessionId={sessionId}
          handle={live.handle}
          sources={explanations}
          summary={live.summary}
          onClose={() => setExpanded(false)}
          onChanged={handleChanged}
        />
      )}
    </aside>
  );
}
