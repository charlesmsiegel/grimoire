import { useEffect, useState } from "react";

/** What one stored image depicts, in the author's words.
 *
 *  One component for all four art surfaces — character versions, PC versions,
 *  entity galleries and the campaign's own library — because it is the same
 *  control four times over, and four copies of it would be four places to fix
 *  the next thing about it.
 *
 *  **`undefined` and `""` are different, and the UI has to say so.** A missing
 *  value means nobody has looked at this image yet; an empty string means
 *  somebody looked and decided it needs no description. Only the first belongs
 *  in a describe-everything queue, and only the second is a decision — so the
 *  collapsed label reads "Describe…" for one and "No description" for the
 *  other, rather than showing blank for both and losing the distinction the
 *  store went to the trouble of keeping. */
export function ImageDescriptionField({ name, value, onSave, onDraft }: {
  name: string;
  value: string | undefined;
  onSave: (description: string) => Promise<void>;
  /** Ask the model for a first draft, if the active connection can read an
   *  image. Absent = no draft button (the caller has no endpoint to offer). */
  onDraft?: () => Promise<string>;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(value ?? "");
  const [busy, setBusy] = useState<"save" | "draft" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-sync when the record reloads under us (a save elsewhere, a version
  // switch). Guarded on `open` so it cannot discard what is being typed.
  useEffect(() => { if (!open) setText(value ?? ""); }, [value, open]);

  const reviewed = value !== undefined;
  const label = value ? value : reviewed ? "No description" : "Describe…";

  if (!open) {
    return (
      <button className={"image-description" + (value ? "" : " image-description-empty")}
              type="button" title={value || undefined}
              aria-label={`Description of ${name}`}
              onClick={() => { setText(value ?? ""); setError(null); setOpen(true); }}>
        {label}
      </button>
    );
  }

  async function save(description: string) {
    setBusy("save");
    setError(null);
    try {
      await onSave(description);
      setOpen(false);
    } catch (err: unknown) {
      setError((err as { detail?: string })?.detail ?? String(err));
    } finally {
      setBusy(null);
    }
  }

  async function draft() {
    if (!onDraft) return;
    setBusy("draft");
    setError(null);
    try {
      setText(await onDraft());
    } catch (err: unknown) {
      setError((err as { detail?: string })?.detail ?? String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="image-description-editor">
      {error != null && <div className="banner">{error}</div>}
      <textarea value={text} rows={3} aria-label={`Description of ${name}`}
                placeholder="What does this picture show?"
                onChange={(e) => setText(e.target.value)} />
      <div className="form-actions">
        <button className="primary" type="button" disabled={busy !== null}
                onClick={() => { void save(text); }}>
          {busy === "save" ? "Saving…" : "Save"}
        </button>
        {/* Persists `""`, which is a decision and not a deletion: it takes the
            image out of the describe queue without offering it to the model. */}
        <button className="subtle" type="button" disabled={busy !== null}
                onClick={() => { void save(""); }}>No description</button>
        {onDraft && (
          <button className="subtle" type="button" disabled={busy !== null}
                  onClick={() => { void draft(); }}>
            {busy === "draft" ? "Looking…" : "Describe it for me"}
          </button>
        )}
        <button className="subtle" type="button" disabled={busy !== null}
                onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}
