/**
 * Diff between two previews, or a preview and a prior canonical turn.
 */

import type {
  ContextDiff,
  ContextSourceExplanation,
  ContextTier,
} from "../../../api/inspector";

const TIERS: ContextTier[] = ["lock-in", "spotlight", "background", "archive"];

/**
 * Pick the best display label for a source row. ``kind`` is always a
 * non-null string so the return type is guaranteed string even if
 * ``summary`` is empty or ``owner_id`` is null.
 */
function labelFor(e: ContextSourceExplanation): string {
  if (e.summary) return e.summary;
  if (e.owner_id) return e.owner_id;
  return e.kind;
}

interface Props {
  diff: ContextDiff | null;
}

export function DiffView({ diff }: Props) {
  if (!diff) {
    return <p className="inspector-empty">No diff loaded.</p>;
  }
  return (
    <div className="inspector-diff">
      <DiffSection
        label="Added"
        rows={diff.entities_added.map((e) => ({
          key: e.source_id,
          label: labelFor(e),
        }))}
        kind="added"
      />
      <DiffSection
        label="Removed"
        rows={diff.entities_removed.map((e) => ({
          key: e.source_id,
          label: labelFor(e),
        }))}
        kind="removed"
      />
      <DiffSection
        label="Tier changed"
        rows={diff.entities_changed_tier.map((e) => ({
          key: e.source_id,
          label: `${labelFor(e)} → ${e.tier}`,
        }))}
        kind="changed"
      />
      <section className="inspector-diff-budget">
        <h4>Budget shifts</h4>
        <ul>
          {TIERS.map((tier) => {
            const v = diff.budget_shifts[tier] ?? 0;
            const sign = v > 0 ? "+" : "";
            return (
              <li key={tier} className={v === 0 ? "" : v > 0 ? "is-grew" : "is-shrunk"}>
                <strong>{tier}</strong>: {sign}
                {v.toLocaleString()} tokens
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

function DiffSection({
  label,
  rows,
  kind,
}: {
  label: string;
  rows: { key: string; label: string }[];
  kind: "added" | "removed" | "changed";
}) {
  return (
    <section className={`inspector-diff-section inspector-diff-${kind}`}>
      <h4>
        {label} <span className="inspector-diff-count">({rows.length})</span>
      </h4>
      {rows.length === 0 ? (
        <p className="inspector-empty">None.</p>
      ) : (
        <ul>
          {rows.map((r) => (
            <li key={r.key || r.label}>{r.label}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
