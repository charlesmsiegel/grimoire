export function ConfigSaveIndicator({
  status,
  error,
}: {
  status: "idle" | "loading" | "saving" | "saved" | "error";
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
