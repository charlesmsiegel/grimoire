import { type LoreEntryDraft } from "../api/client";
import { kindOptions } from "./useEntityKinds";

/** The lorebook review table, extracted from `LorebookImport` so the character
 *  editor's embedded-book import can offer the same parse → review → re-route
 *  step instead of a blind one-click commit (#27). Presentational on purpose:
 *  the parents own where the entries came from (a picked file; a stored card's
 *  `character_book`) and what committing them means, this owns only the
 *  editable rows. `kinds` comes from the parent's own `useEntityKinds` call —
 *  a hook in here would fire the read while the table is unmounted. */
export function LoreReviewTable({ entries, kinds, onPatch, onCommit }: {
  entries: LoreEntryDraft[];
  kinds: readonly string[];
  onPatch: (i: number, patch: Partial<LoreEntryDraft>) => void;
  onCommit: () => void;
}) {
  if (entries.length === 0) {
    return <div className="editor-empty">No importable entries found.</div>;
  }
  return (
    <>
      <table className="table">
        <thead>
          <tr><th>Name</th><th>Keys</th><th>Category</th><th>Body</th></tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            // Rows are edited in place and never reordered or removed, so the
            // index is the row's stable identity here (as it was in
            // LorebookImport, where this table came from).
            // eslint-disable-next-line react/no-array-index-key
            <tr key={i}>
              <td>
                <input type="text" aria-label={`name ${i}`} value={e.name}
                       onChange={(ev) => onPatch(i, { name: ev.target.value })} />
              </td>
              <td>
                <input type="text" aria-label={`keys ${i}`} value={e.keys.join(",")}
                       onChange={(ev) => onPatch(i, { keys: ev.target.value.split(",").map((k) => k.trim()).filter(Boolean) })} />
              </td>
              <td>
                <select aria-label={`category ${i}`} value={e.category}
                        onChange={(ev) => onPatch(i, { category: ev.target.value })}>
                  {kindOptions(kinds, e.category).map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </td>
              <td>{e.body.length > 80 ? e.body.slice(0, 80) + "…" : e.body}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="form-actions">
        <button className="primary" onClick={onCommit}>
          Import {entries.length} {entries.length === 1 ? "entry" : "entries"}
        </button>
      </div>
    </>
  );
}
