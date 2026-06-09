/**
 * Total token-usage bar for the Inspector panel. The numerator is the sum of
 * the actual source tokens going into the prompt (including the system and
 * player-input blocks that live outside any tier budget); the denominator is
 * the total tier budget. The per-tier budget breakdown is exposed via the
 * bar's title (hover) so the inline block stays compact.
 */

import type { ContextSourceExplanation, ContextTier, PreviewSummary } from "../../../api/inspector";

const TIERS: ContextTier[] = ["lock-in", "spotlight", "background", "archive"];

const TIER_LABELS: Record<ContextTier, string> = {
  "lock-in": "lock-in",
  spotlight: "spotlight",
  background: "background",
  archive: "archive",
};

interface Props {
  summary: PreviewSummary | null;
  sources?: ContextSourceExplanation[];
  loading?: boolean;
}

export function TokenBars({ summary, sources, loading }: Props) {
  if (!summary) {
    return (
      <p className="empty-state inspector-empty">
        {loading ? "Computing preview…" : "Type to preview the next prompt."}
      </p>
    );
  }
  // Prefer the comprehensive source-token sum (every block, not just tiered
  // content); fall back to per-tier usage when no source list is available.
  const used =
    sources && sources.length > 0
      ? sources.reduce((acc, s) => acc + (s.tokens ?? 0), 0)
      : TIERS.reduce((acc, t) => acc + (summary.per_tier_tokens[t] ?? 0), 0);
  const budget = TIERS.reduce((acc, t) => acc + (summary.per_tier_budget[t] ?? 0), 0);
  const ratio = budget > 0 ? Math.min(1, used / budget) : 0;
  const over = budget > 0 && used > budget;
  const breakdown = TIERS.map(
    (t) => `${TIER_LABELS[t]} ${(summary.per_tier_tokens[t] ?? 0).toLocaleString()}`,
  ).join(" · ");
  return (
    <div
      className={`inspector-token-total ${over ? "is-over" : ""}`}
      aria-label="Per-tier token usage"
      title={breakdown}
    >
      <span className="inspector-token-bar" aria-hidden>
        <span className="inspector-token-fill" style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
      </span>
      <span className="inspector-token-counts">
        {used.toLocaleString()} / {budget.toLocaleString()}
      </span>
    </div>
  );
}
