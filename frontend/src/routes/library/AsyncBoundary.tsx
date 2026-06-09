/**
 * Plain-children async boundary for library views. Renders the same shared
 * status markup as components/AsyncSection (the render-prop variant used by
 * the campaign views) so loading/error/empty look identical everywhere.
 */

import type { ReactNode } from "react";

import { EmptyState } from "../../components/EmptyState";

interface AsyncBoundaryProps {
  loading: boolean;
  error: Error | null;
  empty?: boolean;
  emptyMessage?: string;
  children: ReactNode;
  onRetry?: () => void;
}

export function AsyncBoundary({
  loading,
  error,
  empty,
  emptyMessage = "Nothing to show.",
  children,
  onRetry,
}: AsyncBoundaryProps) {
  if (loading) {
    return (
      <p className="async-status" role="status">
        Loading…
      </p>
    );
  }
  if (error) {
    return (
      <div className="async-status async-error" role="alert">
        <p>Failed to load: {error.message}</p>
        {onRetry && (
          <button type="button" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    );
  }
  if (empty) {
    return <EmptyState message={emptyMessage} />;
  }
  return <>{children}</>;
}
