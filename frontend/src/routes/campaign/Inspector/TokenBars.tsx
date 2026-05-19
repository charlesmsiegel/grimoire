/**
 * Per-tier token usage bars for the Inspector panel.
 *
 * Each tier renders as a labelled horizontal bar showing
 * used/budget; tiers over budget glow red.
 */

import type { ContextTier, PreviewSummary } from "../../../api/inspector";

const TIERS: ContextTier[] = ["lock-in", "spotlight", "background", "archive"];

const TIER_LABELS: Record<ContextTier, string> = {
  "lock-in": "Lock-in",
  spotlight: "Spotlight",
  background: "Background",
  archive: "Archive",
};

interface Props {
  summary: PreviewSummary | null;
  loading?: boolean;
}

export function TokenBars({ summary, loading }: Props) {
  if (!summary) {
    return (
      <p className="inspector-empty">
        {loading ? "Computing preview…" : "Type to preview the next prompt."}
      </p>
    );
  }
  return (
    <ul className="inspector-token-bars" aria-label="Per-tier token usage">
      {TIERS.map((tier) => {
        const used = summary.per_tier_tokens[tier] ?? 0;
        const budget = summary.per_tier_budget[tier] ?? 0;
        const ratio = budget > 0 ? Math.min(1, used / budget) : 0;
        const over = budget > 0 && used > budget;
        return (
          <li key={tier} className={`inspector-token-row ${over ? "is-over" : ""}`}>
            <span className="inspector-token-label">{TIER_LABELS[tier]}</span>
            <span className="inspector-token-bar" aria-hidden>
              <span
                className="inspector-token-fill"
                style={{ width: `${(ratio * 100).toFixed(1)}%` }}
              />
            </span>
            <span className="inspector-token-counts">
              {used.toLocaleString()} / {budget.toLocaleString()}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
