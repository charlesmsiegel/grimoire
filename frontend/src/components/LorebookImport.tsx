import { useRef, useState } from "react";
import { api, type EntityKind, type LoreEntryDraft } from "../api/client";

const FORMATS = ["lorebook", "json", "png", "charx"];

export function LorebookImport({ wid }: { wid: string }) {
  const [format, setFormat] = useState("lorebook");
  const [entries, setEntries] = useState<LoreEntryDraft[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function parse() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setError(null);
    setResult(null);
    try {
      const { entries } = await api.lorebookParse(wid, file, format);
      setEntries(entries);
    } catch (err: any) {
      setEntries(null);
      setError(err.detail ?? String(err));
    }
  }

  function patch(i: number, patch: Partial<LoreEntryDraft>) {
    setEntries((cur) => cur!.map((e, j) => (j === i ? { ...e, ...patch } : e)));
  }

  async function commit() {
    if (!entries) return;
    setError(null);
    try {
      const { created } = await api.lorebookImport(wid, entries);
      setResult(`Imported ${created.length} ${created.length === 1 ? "entry" : "entries"}.`);
      setEntries(null);
      if (fileRef.current) fileRef.current.value = "";
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div>
      {error && <div className="banner">{error}</div>}
      {result && <div className="banner">{result}</div>}

      <div className="picker">
        <input ref={fileRef} type="file" aria-label="Lorebook or card file" />
        <select value={format} onChange={(e) => setFormat(e.target.value)} aria-label="Source format">
          {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <button className="primary" onClick={parse}>Parse</button>
      </div>
      <div className="field-hint">
        Pick a standalone lorebook <code>.json</code> (format “lorebook”) or a character card
        (json / png / charx) to pull its embedded world-info. Parsing writes nothing — review and
        route each entry, then import.
      </div>

      {entries && (
        <>
          {entries.length === 0 ? (
            <div className="editor-empty">No importable entries found in that file.</div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Name</th><th>Keys</th><th>Category</th><th>Body</th></tr>
              </thead>
              <tbody>
                {entries.map((e, i) => (
                  <tr key={i}>
                    <td>
                      <input type="text" aria-label={`name ${i}`} value={e.name}
                             onChange={(ev) => patch(i, { name: ev.target.value })} />
                    </td>
                    <td>
                      <input type="text" aria-label={`keys ${i}`} value={e.keys.join(",")}
                             onChange={(ev) => patch(i, { keys: ev.target.value.split(",").map((k) => k.trim()).filter(Boolean) })} />
                    </td>
                    <td>
                      <select aria-label={`category ${i}`} value={e.category}
                              onChange={(ev) => patch(i, { category: ev.target.value as EntityKind })}>
                        <option value="lore">lore</option>
                        <option value="locations">locations</option>
                      </select>
                    </td>
                    <td>{e.body.length > 80 ? e.body.slice(0, 80) + "…" : e.body}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {entries.length > 0 && (
            <div className="form-actions">
              <button className="primary" onClick={commit}>Import {entries.length} entries</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
