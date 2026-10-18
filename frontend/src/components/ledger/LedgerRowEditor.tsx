import { useEffect, useRef, useState } from "react";

/** One field in a ledger record's form.
 *
 *  A tiny spec rather than five hand-written forms: the five editable sections
 *  differ only in which fields they carry, and a form per section is five
 *  places for the save button, the busy state and the delete confirmation to
 *  drift apart while claiming to be one editor. The same argument the table
 *  above it already makes for having one row renderer.
 */
export type Field =
  | { key: string; label: string; kind: "text" | "textarea"; hint?: string; placeholder?: string }
  | { key: string; label: string; kind: "select"; options: readonly string[]; hint?: string }
  | { key: string; label: string; kind: "meter"; hint?: string }
  /** Which of the campaign's scenes a record is filed under, picked from
   *  `scenes` by title -- the stored value is the scene's id, a filename
   *  nobody types correctly. */
  | { key: string; label: string; kind: "scene"; hint?: string };

/** A scene as the picker offers it. */
export type SceneOption = { id: string; title: string };

export type Draft = Record<string, string>;

/** The row's own editor, opened underneath it.
 *
 *  Underneath rather than in a dialog, because the thing being corrected is
 *  usually legible only in context — a fact reads differently beside the fact
 *  that superseded it, and a thread's status means little without the beat
 *  above it. A modal would cover exactly what the reader is checking against.
 */
export function LedgerRowEditor(
  { fields, initial, scenes, busy, error, errorAction, onSave, onCancel, onDelete, deleteLabel }: {
    fields: readonly Field[];
    initial: Draft;
    /** The campaign's scenes, for a `scene` field. Null while they are read,
     *  or when they could not be: the field is then the plain id box it used
     *  to be, so a correction never waits on a list. */
    scenes?: readonly SceneOption[] | null;
    busy: boolean;
    /** A refusal from the server — a 409 on a fact already retired, say. Shown
     *  in the editor rather than at the top of the page, because that is where
     *  the reader is looking and which row it belongs to is otherwise a guess. */
    error?: string | null | undefined;
    /** The one way forward a refusal offers, as a button beside it -- a delete
     *  refused for merged records offers "Delete anyway". */
    errorAction?: { label: string; run: () => void } | undefined;
    onSave: (draft: Draft) => void;
    onCancel: () => void;
    /** Absent for a record that cannot be removed — the chronicle line, whose
     *  record belongs to a scene rather than to the ledger. */
    onDelete?: (() => void) | undefined;
    deleteLabel?: string;
  },
) {
  const [draft, setDraft] = useState<Draft>(initial);
  const [confirming, setConfirming] = useState(false);

  /** Only the fields this edit actually moved.
   *
   *  The whole form used to go out, which quietly made every save a
   *  last-writer-wins overwrite of the record as it looked when the editor
   *  OPENED. The campaign lock serializes the writes but not the browser's
   *  earlier read, so an absorb landing in between — advancing a thread's
   *  status and scene, say — was reverted by a save that only meant to fix the
   *  title. Sending nothing about a field the reader did not touch is what
   *  makes the store's "keep what is stored" default do the right thing.
   *
   *  A field the reader EMPTIED is dirty and is sent as `""`, which is how a
   *  deadline gets cleared — the difference between omitted and blank is
   *  load-bearing all the way down to `commitments.set_movement`. */
  const dirty = (d: Draft): Draft =>
    Object.fromEntries(Object.entries(d).filter(([k, v]) => v !== (initial[k] ?? "")));
  const first = useRef<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(null);

  // Focused on open rather than with `autoFocus`, which steals focus on page
  // load; this editor exists only because the reader just asked for it.
  useEffect(() => { first.current?.focus(); }, []);

  const set = (key: string, value: string) => setDraft((d) => ({ ...d, [key]: value }));
  // Scene IDs are unique, titles are not: repeatedly accepting "New scene"
  // must still leave the reader able to choose the record they mean.
  const titleCounts = new Map<string, number>();
  for (const scene of scenes ?? []) {
    titleCounts.set(scene.title, (titleCounts.get(scene.title) ?? 0) + 1);
  }

  return (
    <div className="ledger-editor">
      {fields.map((f, i) => (
        <label className="ledger-field" key={f.key}>
          <span className="data-label">{f.label}</span>
          {f.kind === "textarea" ? (
            <textarea
              ref={i === 0 ? (first as React.RefObject<HTMLTextAreaElement>) : undefined}
              aria-label={f.label} rows={3} value={draft[f.key] ?? ""}
              placeholder={f.placeholder} disabled={busy}
              onChange={(e) => set(f.key, e.target.value)} />
          ) : f.kind === "select" ? (
            <select
              ref={i === 0 ? (first as React.RefObject<HTMLSelectElement>) : undefined}
              aria-label={f.label} value={draft[f.key] ?? ""} disabled={busy}
              onChange={(e) => set(f.key, e.target.value)}>
              {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : f.kind === "scene" && scenes ? (
            <select
              aria-label={f.label} value={draft[f.key] ?? ""} disabled={busy}
              onChange={(e) => set(f.key, e.target.value)}>
              <option value="">No scene</option>
              {scenes.map((s) => <option key={s.id} value={s.id}>
                {s.title && (titleCounts.get(s.title) ?? 0) > 1
                  ? `${s.title} (${s.id})` : s.title || s.id}
              </option>)}
              {/* A record filed under a scene the list no longer has (deleted,
                  or renamed since the absorb wrote it) keeps that value until
                  the reader picks another, rather than silently losing it. */}
              {draft[f.key] && !scenes.some((s) => s.id === draft[f.key]) && (
                <option value={draft[f.key]}>{draft[f.key]}</option>
              )}
            </select>
          ) : f.kind === "meter" ? (
            <input
              ref={i === 0 ? (first as React.RefObject<HTMLInputElement>) : undefined}
              type="number" min={0} max={5} aria-label={f.label} disabled={busy}
              value={draft[f.key] ?? "0"}
              onChange={(e) => set(f.key, e.target.value)} />
          ) : (
            <input
              ref={i === 0 ? (first as React.RefObject<HTMLInputElement>) : undefined}
              type="text" aria-label={f.label} value={draft[f.key] ?? ""}
              placeholder={f.kind === "text" ? f.placeholder : undefined} disabled={busy}
              onChange={(e) => set(f.key, e.target.value)}
              onKeyDown={(e) => {
                // `busy` here as well as on the button: only the buttons were
                // disabled, so Enter twice during a slow save sent the write
                // twice — two threads, or the same beat appended twice.
                // `isComposing` keeps an IME's commit from submitting.
                if (e.key === "Enter" && !busy && !e.nativeEvent.isComposing) onSave(dirty(draft));
              }} />
          )}
          {f.hint && <span className="field-hint">{f.hint}</span>}
        </label>
      ))}

      {error && (
        <p className="ledger-editor-error">
          {error}
          {errorAction && (
            <>
              {" "}
              <button className="subtle danger" type="button" disabled={busy}
                      onClick={errorAction.run}>{errorAction.label}</button>
            </>
          )}
        </p>
      )}

      <div className="ledger-editor-actions">
        <button className="primary" type="button" disabled={busy}
                onClick={() => onSave(dirty(draft))}>{busy ? "Saving…" : "Save"}</button>
        <button className="subtle" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
        {onDelete && (confirming ? (
          <>
            {/* Two clicks rather than a `window.confirm`: a delete here is not
                the same act as retiring or closing, and the difference is worth
                a sentence the reader can read in place. */}
            <span className="field-hint">
              Removes the row outright — this is for a record that should never
              have existed, not one that ended.
            </span>
            <button className="subtle danger" type="button" disabled={busy}
                    onClick={onDelete}>{deleteLabel ?? "Delete"}</button>
            <button className="subtle" type="button" disabled={busy}
                    onClick={() => setConfirming(false)}>Keep it</button>
          </>
        ) : (
          <button className="subtle danger" type="button" disabled={busy}
                  onClick={() => setConfirming(true)}>{deleteLabel ?? "Delete"}</button>
        ))}
      </div>
    </div>
  );
}

export default LedgerRowEditor;
