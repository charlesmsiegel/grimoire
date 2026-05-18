import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { libraryApi, type LibraryEntity } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";
import { diffVariants, formatValue } from "./variantDiff";

interface Props {
  kindPlural: string;
  assetId: string;
}

export function VariantsPanel({ kindPlural, assetId }: Props) {
  const { data, loading, error, reload } = useResource(
    () => libraryApi.variants(kindPlural, assetId),
    [kindPlural, assetId],
  );
  const [showDiff, setShowDiff] = useState(false);

  // Each consecutive pair (A,B) produces a diff entry.
  const pairs = useMemo(() => {
    if (!data || data.length < 2) return [];
    const out: { a: LibraryEntity; b: LibraryEntity }[] = [];
    for (let i = 0; i < data.length - 1; i += 1) {
      const a = data[i];
      const b = data[i + 1];
      if (a && b) out.push({ a, b });
    }
    return out;
  }, [data]);

  return (
    <section className="variants-panel">
      <p className="variants-intro">
        Entities across worlds sharing the asset id <code>{assetId}</code>. Each variant is fully
        independent — editing one has no effect on others.
      </p>
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No other worlds declare an asset with this id."
        onRetry={reload}
      >
        {data && data.length >= 2 && (
          <div className="variants-controls">
            <button
              type="button"
              className="variants-diff-toggle"
              onClick={() => setShowDiff((prev) => !prev)}
              aria-pressed={showDiff}
            >
              {showDiff ? "Hide diff" : "Show diff"}
            </button>
          </div>
        )}
        <ul className="variants-list">
          {data?.map((entity) => (
            <li key={entity.id}>
              <Link
                to={`/library/worlds/${encodeURIComponent(entity.world_id ?? "")}/${kindPlural}/${encodeURIComponent(entity.asset_id)}`}
              >
                <strong>{entity.name || entity.asset_id}</strong>
                <span className="variant-source"> — {entity.world_id}</span>
              </Link>
              {entity.body && <p className="variant-snippet">{entity.body.slice(0, 180)}…</p>}
            </li>
          ))}
        </ul>
        {showDiff && pairs.length > 0 && (
          <div className="variants-diff-section">
            <h4 className="variants-diff-heading">Pairwise diffs</h4>
            {pairs.map(({ a, b }) => (
              <VariantPairDiff key={`${a.id}::${b.id}`} a={a} b={b} />
            ))}
          </div>
        )}
      </AsyncBoundary>
    </section>
  );
}

function VariantPairDiff({ a, b }: { a: LibraryEntity; b: LibraryEntity }) {
  const diff = useMemo(() => diffVariants(a, b), [a, b]);
  const labelA = a.world_id ?? "(no world)";
  const labelB = b.world_id ?? "(no world)";
  return (
    <article className="variant-diff" aria-label={`Diff ${labelA} vs ${labelB}`}>
      <header className="variant-diff-header">
        <span>
          <strong>{labelA}</strong> vs <strong>{labelB}</strong>
        </span>
        <span className="variant-diff-body-meta">
          body: {diff.bodyLengthA} → {diff.bodyLengthB} chars (
          {diff.bodyLengthDelta >= 0 ? "+" : ""}
          {diff.bodyLengthDelta})
        </span>
      </header>
      {diff.rows.length === 0 ? (
        <p className="variant-diff-empty">Frontmatter is identical.</p>
      ) : (
        <ul className="variant-diff-rows">
          {diff.rows.map((row) => (
            <li key={row.key} className="variant-diff-row">
              <code className="variant-diff-key">{row.key}</code>
              <span className="variant-diff-values">
                <span className="variant-diff-side">
                  <em>A=</em>
                  <code>{formatValue(row.a)}</code>
                </span>
                <span className="variant-diff-side">
                  <em>B=</em>
                  <code>{formatValue(row.b)}</code>
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
