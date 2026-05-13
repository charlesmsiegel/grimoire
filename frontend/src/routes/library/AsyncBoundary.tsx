import type { ReactNode } from "react";

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
      <p className="library-status" role="status">
        Loading…
      </p>
    );
  }
  if (error) {
    return (
      <div className="library-status library-error" role="alert">
        <p>Failed to load: {error.message}</p>
        {onRetry && <button onClick={onRetry}>Retry</button>}
      </div>
    );
  }
  if (empty) {
    return <p className="library-status">{emptyMessage}</p>;
  }
  return <>{children}</>;
}
