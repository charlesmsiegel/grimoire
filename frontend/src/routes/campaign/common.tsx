/**
 * Shared bits for the per-campaign sub-views.
 *
 * The chain-aware badge wraps the simple `SourceBadge` from the play view so
 * resolved entities everywhere (Cast / World / …) render with the same glyphs
 * regardless of whether the caller has a single source or a full chain (spec
 * 14 §Source attribution).
 */

import type { Loadable } from "../../api/useApi";
import type { ResolutionSource } from "../../api/types";
import { SourceBadge } from "./SourceBadge";

interface LoadingProps<T> {
  state: Loadable<T>;
  children: (data: T) => React.ReactNode;
  /** Override for the empty-list case; receives the list. */
  emptyMessage?: string;
}

export function Loading<T>({ state, children, emptyMessage }: LoadingProps<T>) {
  if (state.status === "loading" || state.status === "idle") {
    return <p className="muted">Loading…</p>;
  }
  if (state.status === "error") {
    return (
      <p className="error" role="alert">
        Failed to load: {state.error.message}
      </p>
    );
  }
  if (Array.isArray(state.data) && state.data.length === 0 && emptyMessage) {
    return <p className="muted">{emptyMessage}</p>;
  }
  return <>{children(state.data)}</>;
}

interface ChainBadgeProps {
  chain: ResolutionSource[];
  overrides?: string[];
}

export function ChainBadge({ chain, overrides }: ChainBadgeProps) {
  const top = chain[0];
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

interface TabsProps<K extends string> {
  tabs: { key: K; label: string }[];
  active: K;
  onSelect: (key: K) => void;
  ariaLabel: string;
}

export function Tabs<K extends string>({ tabs, active, onSelect, ariaLabel }: TabsProps<K>) {
  return (
    <div className="tab-row" role="tablist" aria-label={ariaLabel}>
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={active === t.key}
          className={active === t.key ? "tab active" : "tab"}
          onClick={() => onSelect(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
