/**
 * AuxInflightBadge — header pill in the SideHud showing the count of
 * auxiliary task results awaiting accept/discard. Click toggles a dropdown
 * that lists each result with a Discard control.
 *
 * Renders nothing when there are no in-flight results, so the header stays
 * clean during normal play.
 */

import { useCallback, useState } from "react";

import { auxiliaryApi } from "../../../api/auxiliary";
import { useAuxInflight } from "./useAuxInflight";

interface Props {
  campaignId: string;
}

const KIND_LABELS: Record<string, string> = {
  impersonate_pc: "Suggested post",
  rewrite_post: "Rewrite",
  continue_as: "Continue as",
  what_would_x_say: "Voice check",
  brainstorm: "Brainstorm",
  edit_prose: "Polish",
  translate: "Translation",
};

export function AuxInflightBadge({ campaignId }: Props) {
  const { results, refresh } = useAuxInflight(campaignId);
  const [expanded, setExpanded] = useState(false);

  const onDiscard = useCallback(
    async (resultId: string) => {
      try {
        await auxiliaryApi.discard(campaignId, resultId);
      } catch {
        // Refresh below will resync from the server regardless.
      }
      refresh();
    },
    [campaignId, refresh],
  );

  if (results.length === 0) return null;

  return (
    <div className="aux-inflight-badge">
      <button
        type="button"
        className="aux-inflight-badge-pill"
        aria-label={`${results.length} auxiliary task${results.length === 1 ? "" : "s"} awaiting review`}
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        <span aria-hidden="true">✨</span>
        <span className="aux-inflight-badge-count">{results.length}</span>
      </button>
      {expanded && (
        <ul className="aux-inflight-badge-list" role="list">
          {results.map((r) => (
            <li key={r.id}>
              <span className="aux-inflight-badge-kind">
                {KIND_LABELS[r.kind] ?? r.kind}
              </span>
              <button
                type="button"
                className="aux-inflight-badge-discard"
                onClick={() => void onDiscard(r.id)}
              >
                Discard
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
