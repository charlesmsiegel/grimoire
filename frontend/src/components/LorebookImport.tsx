import { useRef, useState } from "react";
import { api, type LoreEntryDraft } from "../api/client";
import { LoreReviewTable } from "./LoreReviewTable";
import { useEntityKinds } from "./useEntityKinds";

const FORMATS = ["lorebook", "json", "png", "charx"];

export function LorebookImport({ wid, onImported }: { wid: string; onImported?: () => void }) {
  const [format, setFormat] = useState("lorebook");
  const [entries, setEntries] = useState<LoreEntryDraft[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const kinds = useEntityKinds((entries?.length ?? 0) > 0);

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
      onImported?.();
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
        <LoreReviewTable entries={entries} kinds={kinds} onPatch={patch} onCommit={commit} />
      )}
    </div>
  );
}
