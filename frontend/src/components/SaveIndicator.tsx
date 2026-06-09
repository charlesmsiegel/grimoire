/**
 * Save/loading status chip for auto-saved forms — the single implementation
 * behind both the campaign settings and app settings screens.
 */

export type SaveIndicatorStatus = "idle" | "loading" | "saving" | "saved" | "error";

export function SaveIndicator({
  status,
  error,
}: {
  status: SaveIndicatorStatus;
  error: string | null;
}) {
  if (status === "loading") return <small className="wizard-meta">Loading…</small>;
  if (status === "saving") return <small className="wizard-meta">Saving…</small>;
  if (status === "error") {
    return (
      <small className="wizard-error" role="alert">
        {error ?? "Save failed"}
      </small>
    );
  }
  if (status === "saved") return <small className="library-ok">Saved.</small>;
  return null;
}
