/**
 * Full-page overlay for deep inspection of the next-post context.
 *
 * Master/detail over the comprehensive source list (full precise text +
 * pin/exclude), plus a verbatim "raw messages" view of the assembled
 * prompt fetched on demand. Uses the codebase modal-backdrop/modal idiom.
 */

import { useEffect, useState } from "react";

import {
  inspectorApi,
  type ContextSourceExplanation,
  type ContextTier,
  type PreviewDetail,
  type PreviewSummary,
} from "../../../api/inspector";
import { REASON_LABELS } from "../../observability/inclusionReasonLabels";
import { CloseIcon } from "../../../components/icons";
import { PinControls } from "./PinControls";
import { chunkLabel, isPinnable } from "./sourceKinds";

const TIERS: ContextTier[] = ["lock-in", "spotlight", "background", "archive"];
const TIER_ORDER: Record<ContextTier, number> = {
  "lock-in": 0,
  spotlight: 1,
  background: 2,
  archive: 3,
};

interface Props {
  campaignId: string;
  sessionId: string;
  handle: string;
  sources: ContextSourceExplanation[];
  summary: PreviewSummary | null;
  onClose: () => void;
  onChanged: () => void;
}

export function InspectorOverlay({
  campaignId,
  sessionId,
  handle,
  sources,
  summary,
  onClose,
  onChanged,
}: Props) {
  const sorted = [...sources].sort(
    (a, b) => TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || b.tokens - a.tokens,
  );
  const [selectedId, setSelectedId] = useState<string | null>(sorted[0]?.source_id ?? null);
  const [raw, setRaw] = useState(false);
  const [detail, setDetail] = useState<PreviewDetail | null>(null);
  const [rawErr, setRawErr] = useState<string | null>(null);

  const selected = sorted.find((s) => s.source_id === selectedId) ?? null;

  // A new preview handle (pin/exclude or a live refresh) invalidates any cached
  // raw messages — drop them so a stale prompt is never shown.
  useEffect(() => {
    setDetail(null);
    setRawErr(null);
  }, [handle]);

  // Fetch the verbatim prompt for the current handle whenever the raw view is
  // on and we don't already have it. Re-runs after the reset above, so a
  // handle change refetches instead of showing the previous prompt.
  useEffect(() => {
    if (!raw || detail) return;
    let cancelled = false;
    inspectorApi
      .getPreview(campaignId, handle, sessionId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) setRawErr(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [raw, detail, campaignId, handle, sessionId]);

  const toggleRaw = () => setRaw((v) => !v);

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Context for next post"
    >
      <div className="modal inspector-overlay">
        <header className="inspector-overlay-header">
          <h2>Context for next post</h2>
          <div className="inspector-overlay-tiers">
            {TIERS.map((t) => (
              <span key={t} className="inspector-overlay-tier-chip">
                {t} {(summary?.per_tier_tokens[t] ?? 0).toLocaleString()}
              </span>
            ))}
          </div>
          <div className="inspector-overlay-actions">
            <button type="button" onClick={toggleRaw}>
              {raw ? "By source" : "Raw messages"}
            </button>
            <button type="button" onClick={onClose} aria-label="Close">
              <CloseIcon />
            </button>
          </div>
        </header>

        {raw ? (
          <div className="inspector-overlay-raw">
            {rawErr && <p className="inspector-error">{rawErr}</p>}
            {!detail ? (
              <p className="empty-state inspector-empty">Loading messages…</p>
            ) : (
              <ol className="inspector-raw-messages">
                {detail.messages.map((m, i) => (
                  <li key={i} className="inspector-raw-message">
                    <header>
                      <span className="inspector-raw-role">{m.role}</span>
                      {typeof m.metadata?.tier === "string" && (
                        <span className="inspector-raw-tier">{m.metadata.tier}</span>
                      )}
                    </header>
                    <pre>{m.content}</pre>
                  </li>
                ))}
              </ol>
            )}
          </div>
        ) : (
          <div className="inspector-overlay-body">
            <ul className="inspector-overlay-list" aria-label="Sources">
              {sorted.map((s) => (
                <li key={s.source_id || `${s.kind}:${s.owner_id}`}>
                  <button
                    type="button"
                    className={`inspector-source-toggle${s.source_id === selectedId ? " is-active" : ""}`}
                    onClick={() => setSelectedId(s.source_id)}
                  >
                    <span className="inspector-source-name">
                      <span className="inspector-source-tier-badge">{s.tier}</span>{" "}
                      {chunkLabel(s).label}
                      {chunkLabel(s).detail && (
                        <span className="inspector-source-detail"> · {chunkLabel(s).detail}</span>
                      )}
                    </span>
                    <span className="inspector-source-tokens">{s.tokens.toLocaleString()} tok</span>
                  </button>
                </li>
              ))}
              {sorted.length === 0 && <li className="empty-state inspector-empty">No sources.</li>}
            </ul>
            <div className="inspector-overlay-detail">
              {selected ? (
                <>
                  <ul className="inspector-reason-list">
                    {selected.inclusion_reasons.map((r) => (
                      <li key={r} className="inspector-reason">
                        {REASON_LABELS[r] ?? r}
                      </li>
                    ))}
                  </ul>
                  <pre className="inspector-overlay-text">
                    {selected.text || "No text captured for this source."}
                  </pre>
                  {isPinnable(selected.kind) && (
                    <PinControls campaignId={campaignId} source={selected} onChanged={onChanged} />
                  )}
                </>
              ) : (
                <p className="empty-state inspector-empty">Select a source.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
