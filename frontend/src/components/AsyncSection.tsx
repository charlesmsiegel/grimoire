/**
 * The app-wide async boundary for `useResource` state: loading line, error
 * line with Retry, optional empty-list message, then the render-prop children
 * with non-null data. Keeps showing stale data during reloads (useResource
 * only nulls data before the first resolution).
 */

import type { Resource } from "../api/useResource";
import { EmptyState } from "./EmptyState";

interface AsyncSectionProps<T> {
  state: Pick<Resource<T>, "data" | "error" | "loading"> & { reload?: () => void };
  /** Shown when the loaded data is an empty array. */
  emptyMessage?: string;
  children: (data: T) => React.ReactNode;
}

export function AsyncSection<T>({ state, emptyMessage, children }: AsyncSectionProps<T>) {
  if (state.data === null) {
    if (state.error) {
      return (
        <div className="async-status async-error" role="alert">
          <p>Failed to load: {state.error.message}</p>
          {state.reload && (
            <button type="button" onClick={state.reload}>
              Retry
            </button>
          )}
        </div>
      );
    }
    return (
      <p className="async-status" role="status">
        Loading…
      </p>
    );
  }
  if (Array.isArray(state.data) && state.data.length === 0 && emptyMessage) {
    return <EmptyState message={emptyMessage} />;
  }
  return <>{children(state.data)}</>;
}
