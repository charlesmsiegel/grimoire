import { useState } from "react";
import { api, type RetiredNote } from "../../api/client";
import { ErrorNote } from "../ErrorNote";

/** Where a note applies, as a reader says it. */
function where(note: RetiredNote): string {
  return note.scope === "global" ? "Library settings" : `Campaign “${note.scope_name}”`;
}

/** What the old model settings could not carry over (inference slice I,
 *  ratification item 7), under the Models heading. The notes live in the
 *  library's retirement record, so a note dismissed here is gone on every
 *  device; each is worded as permanent ("— this was not carried over"), never
 *  as something to come back to. Draws nothing when there is nothing to say. */
export function RetiredNotes({ notes, onDismissed }: {
  notes: RetiredNote[];
  /** Called with a note's id once the server has recorded it dismissed. */
  onDismissed: (id: string) => void;
}) {
  const [error, setError] = useState<unknown>(null);
  // Every note whose dismissal is in flight, so each button waits on its own.
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());

  if (notes.length === 0) return null;

  async function dismiss(id: string) {
    setBusy((prev) => new Set(prev).add(id));
    setError(null);
    try {
      await api.dismissRetiredNote(id);
      onDismissed(id);
    } catch (err) {
      setError(err);
    } finally {
      setBusy((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  return (
    <section className="banner retired-notes" aria-label="Not carried over">
      <p>
        When these settings moved to the new layout, a few things could not come with
        them. To get one back, set it again on the route, preset or model it names.
      </p>
      <ul>
        {notes.map((n) => (
          <li key={n.id}>
            <span className="field-hint">{where(n)}: </span>
            {n.text}{" "}
            <button type="button" className="subtle" disabled={busy.has(n.id)}
                    onClick={() => void dismiss(n.id)}>
              Dismiss
            </button>
          </li>
        ))}
      </ul>
      {error != null && <ErrorNote err={error} />}
    </section>
  );
}
