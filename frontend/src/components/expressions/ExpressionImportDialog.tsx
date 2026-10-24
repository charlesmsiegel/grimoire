import { useEffect, useRef, useState } from "react";
import { api, type ExpressionImportPreview, type ExpressionImportSelection, type ExpressionTarget } from "../../api/client";
import { errorText } from "../../api/errors";
import { expressionsChanged } from "../../appEvents";

type Choice = ExpressionImportSelection & { selected: boolean };
export function ExpressionImportDialog({ target, outfit, onImported, onClose }: {
  target: ExpressionTarget; outfit: string; onImported: () => void; onClose: () => void;
}) {
  const [mode, setMode] = useState<"grimoire" | "sillytavern">("grimoire");
  const [destination, setDestination] = useState(outfit || "everyday");
  const [preview, setPreview] = useState<ExpressionImportPreview | null>(null);
  const [choices, setChoices] = useState<Choice[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(false);
  const request = useRef(0);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  function install(result: ExpressionImportPreview) {
    setPreview(result);
    setChoices(result.images.map(row => ({ file: row.file, outfit: row.outfit, expression: row.expression ?? "",
      selected: !row.ambiguous, overwrite: false })));
  }
  async function inspect(file: File) {
    const own = ++request.current;
    setBusy(true); setError(null); setPreview(null);
    try { const result = await api.previewExpressionPack(target, file, mode, destination); if (live.current && own === request.current) install(result); }
    catch (err) { if (live.current && own === request.current) setError(errorText(err)); }
    finally { if (live.current && own === request.current) setBusy(false); }
  }
  async function inspectFolder(files: File[]) {
    const own = ++request.current;
    setBusy(true); setError(null); setPreview(null);
    try { const result = await api.previewExpressionFolder(target, files, mode, destination); if (live.current && own === request.current) install(result); }
    catch (err) { if (live.current && own === request.current) setError(errorText(err)); }
    finally { if (live.current && own === request.current) setBusy(false); }
  }
  const selected = choices.filter(row => row.selected);
  const outfits = Object.entries(preview?.metadata.outfits ?? {});
  const valid = (selected.length > 0 || outfits.length > 0) && selected.every(row => /^[a-z][a-z0-9_-]{0,63}$/.test(row.outfit) && /^[a-z][a-z0-9_-]{0,63}$/.test(row.expression))
    && new Set(selected.map(row => `${row.outfit}/${row.expression}`)).size === selected.length;
  async function publish() {
    if (!preview || !valid || busy) return;
    setBusy(true); setError(null);
    try {
      await api.publishExpressionPack(target, preview, selected.map(({ file, outfit, expression, overwrite }) => ({ file, outfit, expression, overwrite })));
      // Publication outlives this dialog and its original version editor.
      // Notify whichever collection is mounted now, even after A → B → A.
      expressionsChanged();
      // A replacement dialog may now be open; only this dialog may close itself.
      if (live.current) onImported();
    } catch (err) { if (live.current) setError(errorText(err)); }
    finally { if (live.current) setBusy(false); }
  }
  function patch(index: number, changed: Partial<Choice>) {
    setChoices(current => current.map((row, i) => i === index ? { ...row, ...changed } : row));
  }
  return <section className="expression-import" aria-label="Import expression pack">
    <h3>Import expressions</h3>
    <p>Review the mappings before importing. Select one image per outfit and expression. Replacements require permission in the list below.</p>
    {error && <div role="alert">{error}</div>}
    <fieldset disabled={busy}><legend>Choose a pack</legend>
      <label>Pack format<select aria-label="Pack format" value={mode} onChange={e => { setMode(e.target.value as typeof mode); setPreview(null); }}>
        <option value="grimoire">Grimoire (outfit folders)</option><option value="sillytavern">SillyTavern (flat images)</option>
      </select></label>
      {mode === "sillytavern" && <label>Import into outfit<input value={destination} onChange={e => { setDestination(e.target.value); setPreview(null); }} /></label>}
      <label>Expression ZIP<input type="file" accept=".zip" onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void inspect(file); }} /></label>
      <label>Expression folder<input type="file" multiple ref={node => { node?.setAttribute("webkitdirectory", ""); }}
        onChange={e => { const files = Array.from(e.target.files ?? []); e.target.value = ""; if (files.length) void inspectFolder(files); }} /></label>
    </fieldset>
    {busy && <p role="status">Processing expressions…</p>}
    {preview && <>
      {preview.issues.length > 0 && <ul>{preview.issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
      {outfits.length > 0 && <div><p>New outfits include their saved settings, even without sprites. Existing outfit settings are kept.</p>
        <ul>{outfits.map(([id, metadata]) => <li key={id}>{metadata.label ?? id} ({id})</li>)}</ul>
        {preview.metadata.default_outfit && <p>Default outfit: {preview.metadata.default_outfit}</p>}</div>}
      <p className="field-hint">Preview expires {new Date(preview.expires_at * 1000).toLocaleTimeString()}. Unselected images are skipped.</p>
      <div className="expression-import-rows">{choices.map((row, index) => <fieldset disabled={busy} key={row.file}>
        <legend>{row.file}</legend>
        <label><input type="checkbox" aria-label={`Select ${row.file}`} checked={row.selected} onChange={e => patch(index, { selected: e.target.checked })} />Import this image</label>
        <label>Outfit<input value={row.outfit} onChange={e => patch(index, { outfit: e.target.value })} /></label>
        <label>Expression<input value={row.expression} onChange={e => patch(index, { expression: e.target.value })} /></label>
        <label><input type="checkbox" checked={row.overwrite} onChange={e => patch(index, { overwrite: e.target.checked })} />Replace existing sprite</label>
      </fieldset>)}</div>
      {!valid && <p>Select images with valid, unique outfit/expression destinations.</p>}
      <button disabled={busy || !valid} onClick={() => void publish()}>{choices.length ? "Import selected sprites" : "Import outfits"}</button>
    </>}
    <button onClick={onClose}>{busy ? "Close" : "Cancel import"}</button>
  </section>;
}
