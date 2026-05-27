/**
 * Context Inspector panel.
 *
 * Live-preview view of what the LLM will see on the next turn:
 *   - per-tier token bars
 *   - source list with per-source inclusion reasons + pin/exclude
 *   - diff view (against the previous preview by default)
 *
 * Mounts inside the campaign Play view. The hosting view supplies the
 * current draft player input + session id; this panel debounces those
 * into POST /preview calls and renders the result.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  inspectorApi,
  type ContextDiff,
  type ContextSourceExplanation,
} from "../../../api/inspector";
import { DiffView } from "./DiffView";
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

type Tab = "sources" | "diff";

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

  const [tab, setTab] = useState<Tab | null>(null);
  const [explanations, setExplanations] = useState<ContextSourceExplanation[]>([]);
  const [explainErr, setExplainErr] = useState<string | null>(null);
  const [diff, setDiff] = useState<ContextDiff | null>(null);
  const [diffErr, setDiffErr] = useState<string | null>(null);

  // Track the previous handle in a ref so diff always compares against
  // the handle immediately before the current one — not the very first
  // handle ever rendered. Refs avoid the stale-closure trap that the
  // earlier two-useEffect implementation fell into.
  const prevHandleRef = useRef<string | null>(null);
  const lastHandleRef = useRef<string | null>(null);

  useEffect(() => {
    if (!live.handle || live.handle === lastHandleRef.current) return;
    prevHandleRef.current = lastHandleRef.current;
    lastHandleRef.current = live.handle;
  }, [live.handle]);

  // Refresh source explanations whenever the handle changes.
  useEffect(() => {
    if (!live.handle) return;
    let cancelled = false;
    inspectorApi
      .explain(campaignId, live.handle, sessionId)
      .then((rows) => {
        if (!cancelled) {
          setExplanations(rows);
          setExplainErr(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setExplainErr(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, sessionId, live.handle]);

  // Pin/exclude changes invalidate the cached preview — re-fire the live
  // hook to pick up the new state.
  const handleChanged = useCallback(() => {
    live.refresh();
  }, [live]);

  const computeDiff = useCallback(async () => {
    const baseline = prevHandleRef.current;
    if (!live.handle || !baseline) {
      setDiffErr("Need at least two previews before diff.");
      return;
    }
    setDiffErr(null);
    try {
      const d = await inspectorApi.diff(campaignId, baseline, live.handle, sessionId);
      setDiff(d);
    } catch (err) {
      setDiffErr(err instanceof Error ? err.message : String(err));
    }
  }, [campaignId, live.handle, sessionId]);

  return (
    <aside className="inspector-panel" aria-label="Context inspector">
      <div className="scene-setting-block" aria-label="Context inspector">
        {live.error && <p className="inspector-error">{live.error}</p>}

        <div className="scene-setting-entry scene-setting-entry-full">
          <span className="scene-setting-label">
            Tokens{live.loading ? " …" : ""}
          </span>
          <TokenBars summary={live.summary} loading={live.loading} />
        </div>

        <div className="scene-setting-entry scene-setting-entry-full">
          <nav className="inspector-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "sources"}
              className={tab === "sources" ? "is-active" : ""}
              onClick={() => setTab(tab === "sources" ? null : "sources")}
            >
              Sources ({explanations.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "diff"}
              className={tab === "diff" ? "is-active" : ""}
              onClick={() => {
                if (tab === "diff") {
                  setTab(null);
                } else {
                  setTab("diff");
                  void computeDiff();
                }
              }}
            >
              Diff
            </button>
          </nav>
        </div>

        {tab === "sources" && (
          <div className="scene-setting-entry scene-setting-entry-full inspector-tab-body">
            {explainErr && <p className="inspector-error">{explainErr}</p>}
            <SourceList
              campaignId={campaignId}
              sources={explanations}
              onChanged={handleChanged}
            />
          </div>
        )}

        {tab === "diff" && (
          <div className="scene-setting-entry scene-setting-entry-full inspector-tab-body">
            {diffErr && <p className="inspector-error">{diffErr}</p>}
            <DiffView diff={diff} />
            <button
              type="button"
              className="inspector-refresh"
              onClick={() => void computeDiff()}
            >
              Refresh diff
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
