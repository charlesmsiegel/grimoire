/**
 * Standard empty-state line. One class, one wording style — replaces the
 * scattered per-view `*-empty` paragraphs.
 */

export function EmptyState({
  message,
  action,
}: {
  message: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <p className="empty-state">
      {message}
      {action}
    </p>
  );
}
