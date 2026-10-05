import { useState } from "react";
import { api, type RegexImportRow, type RegexScope } from "../api/client";
import { errorText } from "../api/errors";
import { useHotkeys } from "../shortcuts/useHotkeys";

/** The file's contents as text. A `FileReader` rather than `Blob.text()`, which
 *  not every environment this runs in has. */
const readText = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
  reader.onerror = () => reject(reader.error ?? new Error("The file could not be read."));
  reader.readAsText(file);
});

/** The script's own pattern, for a row that will not translate: the preview
 *  lists it beside the reason so the player can decide what to rewrite by hand. */
const findRegexOf = (original: unknown): string | null => {
  const f = typeof original === "object" && original !== null
    ? (original as { findRegex?: unknown }).findRegex : undefined;
  return typeof f === "string" ? f : null;
};

/** Brings SillyTavern regex scripts into one level. The file is read and parsed
 *  here and only its JSON is sent, so a file that is not JSON never reaches the
 *  server. Every row the translator could carry across is offered, checked;
 *  one it could not is listed with its reason and cannot be chosen. Nothing is
 *  written until `Import selected`, and `onDone` follows a landed write. */
export function RegexImportDialog({ scope, onDone, onClose }: {
  scope: RegexScope; onDone: () => void; onClose: () => void;
}) {
  const [rows, setRows] = useState<RegexImportRow[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Escape is what the Cancel button is, and Cancel is not disabled mid-import:
  // the write either lands (and onDone follows) or it does not, whatever is on screen.
  useHotkeys([{ keys: "escape", whileTyping: true, run: onClose }], { modal: true });

  async function choose(file: File | undefined) {
    if (!file) return;
    setError(null); setRows(null); setPicked(new Set());
    let data: unknown;
    try {
      data = JSON.parse(await readText(file));
    } catch {
      setError("That file is not valid JSON.");
      return;
    }
    setBusy(true);
    try {
      const out = await api.previewRegexImport(data);
      setRows(out.rows);
      setPicked(new Set(out.rows.filter((r) => r.rule !== null).map((r) => r.index)));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const toggle = (index: number, on: boolean) =>
    setPicked((p) => { const n = new Set(p); if (on) n.add(index); else n.delete(index); return n; });

  const chosen = (rows ?? []).filter((r) => picked.has(r.index) && r.rule !== null);

  async function submit() {
    setBusy(true); setError(null);
    try {
      await api.importRegex(scope, chosen.map((r) => r.rule!));
      onDone();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-modal="true" aria-label="Import from SillyTavern">
      <div className="tagline-modal import-dialog regex-import">
        <h3>Import from SillyTavern</h3>
        <p className="field-hint">
          Choose a regex script file, a list of them, or a SillyTavern settings export. Nothing is
          written until you import.
        </p>
        <input type="file" accept=".json,application/json" aria-label="SillyTavern regex file"
               disabled={busy} onChange={(e) => void choose(e.target.files?.[0])} />
        {error && <div className="banner" role="alert">{error}</div>}
        {rows && (rows.length === 0 ? (
          <p className="field-hint">That file holds no regex scripts.</p>
        ) : (
          <ul className="regex-import-rows">
            {rows.map((r) => {
              const original = r.rule === null ? findRegexOf(r.original) : null;
              return (
                <li key={r.index} className={r.rule === null ? "off" : undefined}>
                  <label className="regex-check">
                    <input type="checkbox" aria-label={`Import ${r.name}`}
                           checked={picked.has(r.index)} disabled={r.rule === null || busy}
                           onChange={(e) => toggle(r.index, e.target.checked)} />
                    <strong>{r.name}</strong>
                    <span className="chip">{r.verdict === "untranslatable" ? "won't translate" : r.verdict}</span>
                  </label>
                  {original && <code className="regex-code">{original}</code>}
                  {r.notes.map((n) => <div key={n} className="field-hint">{n}</div>)}
                </li>
              );
            })}
          </ul>
        ))}
        <div className="form-actions">
          <button className="subtle" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || chosen.length === 0} onClick={() => void submit()}>
            Import selected
          </button>
        </div>
      </div>
    </div>
  );
}
