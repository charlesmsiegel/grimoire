import { Link } from "react-router-dom";

import { libraryApi } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";

interface Props {
  kindPlural: string;
  assetId: string;
}

export function VariantsPanel({ kindPlural, assetId }: Props) {
  const { data, loading, error, reload } = useResource(
    () => libraryApi.variants(kindPlural, assetId),
    [kindPlural, assetId],
  );

  return (
    <section className="variants-panel">
      <p className="variants-intro">
        Entities across settings sharing the asset id <code>{assetId}</code>. Each variant is fully
        independent — editing one has no effect on others.
      </p>
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No other settings declare an asset with this id."
        onRetry={reload}
      >
        <ul className="variants-list">
          {data?.map((entity) => (
            <li key={entity.id}>
              <Link
                to={`/library/settings/${encodeURIComponent(entity.setting_id ?? "")}/${kindPlural}/${encodeURIComponent(entity.asset_id)}`}
              >
                <strong>{entity.name || entity.asset_id}</strong>
                <span className="variant-source"> — {entity.setting_id}</span>
              </Link>
              {entity.body && <p className="variant-snippet">{entity.body.slice(0, 180)}…</p>}
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </section>
  );
}
