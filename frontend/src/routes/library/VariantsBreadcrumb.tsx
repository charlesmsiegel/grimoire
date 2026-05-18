import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, type LibraryEntity, libraryApi } from "../../api/library";

interface Props {
  kindPlural: string;
  assetId: string;
  currentWorldId: string;
}

/**
 * Spec 18 §Character variants — surface 'Drizzt (faerun) — also exists in:
 * mythic-europe.' on every entity editor. Renders nothing while loading,
 * on error, or when no other world declares the same asset id.
 */
export function VariantsBreadcrumb({ kindPlural, assetId, currentWorldId }: Props) {
  const [variants, setVariants] = useState<LibraryEntity[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    libraryApi
      .variants(kindPlural, assetId)
      .then((rows) => {
        if (!cancelled) setVariants(rows);
      })
      .catch((err) => {
        // Soft-fail: this is a read-only UX hint, not a primary surface.
        if (!cancelled && !(err instanceof ApiError)) setVariants([]);
        else if (!cancelled) setVariants([]);
      });
    return () => {
      cancelled = true;
    };
  }, [kindPlural, assetId]);

  const others = (variants ?? []).filter((v) => v.world_id && v.world_id !== currentWorldId);
  if (others.length === 0) return null;

  return (
    <p className="entity-variants-breadcrumb">
      Also exists in:{" "}
      {others.map((v, i) => (
        <span key={v.id}>
          {i > 0 && ", "}
          <Link
            to={`/library/worlds/${encodeURIComponent(v.world_id ?? "")}/${kindPlural}/${encodeURIComponent(v.asset_id)}`}
          >
            {v.world_id}
          </Link>
        </span>
      ))}
    </p>
  );
}
