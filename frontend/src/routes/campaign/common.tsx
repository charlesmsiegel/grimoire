/**
 * Shared bits for the per-campaign sub-views.
 *
 * The chain-aware badge wraps the simple `SourceBadge` from the play view so
 * resolved entities everywhere (Cast / World / …) render with the same glyphs
 * regardless of whether the caller has a single source or a full chain (spec
 * 14 §Source attribution).
 */

import type { ResolutionSource } from "../../api/types";
import { SourceBadge } from "./SourceBadge";

interface ChainBadgeProps {
  chain: ResolutionSource[];
  overrides?: string[];
}

export function ChainBadge({ chain, overrides }: ChainBadgeProps) {
  const top = chain?.[0];
  if (!top) {
    return <SourceBadge source="emergent" />;
  }
  const hasOverride = (overrides && overrides.length > 0) || top.override_applied;
  if (hasOverride) {
    return <SourceBadge source="override" detail={top.world_id ?? undefined} />;
  }
  if (top.layer === "emergent") {
    return <SourceBadge source="emergent" />;
  }
  const detail =
    top.layer === "library_snapshot" && top.version != null
      ? `${top.world_id ?? ""} v${top.version}`.trim()
      : (top.world_id ?? undefined);
  return <SourceBadge source="library" detail={detail} />;
}

export { Tabs } from "../../components/Tabs";
