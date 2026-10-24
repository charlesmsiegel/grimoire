import { useEffect, useState } from "react";
import { api, type ExpressionTarget } from "../../api/client";
import { ExpressionGrid } from "./ExpressionGrid";
import { ExpressionImportDialog } from "./ExpressionImportDialog";
import { OutfitEditor } from "./OutfitEditor";
import { NativeActorPack } from "./NativeActorPack";
import { SpritePreview } from "./SpritePreview";
import { useExpressions } from "./useExpressions";
import "./expressions.css";

type Props = { target: ExpressionTarget; active?: boolean; onCountChange?: (count: number | null) => void };

export function ExpressionsSection(props: Props) {
  return <Library key={JSON.stringify(props.target)} {...props} />;
}

function Library({ target, active = true, onCountChange }: Props) {
  const { collection, busy, error, reload, mutate, upload, cleanup, cleaned } = useExpressions(target);
  const [editing, setEditing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState("");
  const [newId, setNewId] = useState("");
  const [custom, setCustom] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  // Keep one collection alive for both the badge and editor so a late badge
  // read cannot overwrite a newer count after an outfit edit or import.
  useEffect(() => { onCountChange?.(collection && !error ? Object.keys(collection.outfits).length : null); }, [collection, error, onCountChange]);
  if (!active) return null;
  const id = collection && selected in collection.outfits ? selected : collection?.default_outfit ?? "";
  const outfit = collection?.outfits[id];
  return <section className="expressions-section" aria-label="Expression library">
    {error && <div role="alert">{error} <button disabled={busy} onClick={() => void reload()}>Reload library</button></div>}
    {!collection ? !error && <p>Loading expressions…</p> : <>
      {collection.warnings?.length > 0 && <aside aria-label="Expression configuration notices"><ul>
        {collection.warnings.map(warning => <li key={warning}>{warning}</li>)}
      </ul></aside>}
      <div className="expression-actions">
        <button disabled={busy} onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit expressions"}</button>
        <a href={api.expressionExportUrl(target)} download>Export expression pack</a>
        <button disabled={busy} onClick={() => setImporting(true)}>Import expression pack</button>
      </div>
      <NativeActorPack target={target} />
      {editing && <div><button disabled={busy} onClick={() => void cleanup()}>Clean up old revisions</button>
        <p className="field-hint">Keeps the current and previous revisions, active reads, and recent uploads. Use Image maintenance afterward to reclaim unused image files.</p>
        {cleaned !== null && <p role="status">Removed {cleaned} old revisions.</p>}</div>}
      {importing && <ExpressionImportDialog target={target} outfit={id} onClose={() => setImporting(false)}
        onImported={() => { setImporting(false); }} />}
      {Object.keys(collection.outfits).length ? <label>Outfit<select aria-label="Outfit" value={id} disabled={busy} onChange={e => { setSelected(e.target.value); setPreview(null); setCustom(""); }}>
        {Object.entries(collection.outfits).map(([key, value]) => <option key={key} value={key}>{value.label}{key === collection.default_outfit ? " (default)" : ""}</option>)}
      </select></label> : <p>No outfits yet.</p>}
      {editing && <fieldset disabled={busy}><legend>Add outfit</legend>
        <label>New outfit ID<input value={newId} onChange={e => setNewId(e.target.value)} placeholder="everyday" /></label>
        <button disabled={!/^[a-z][a-z0-9_-]{0,63}$/.test(newId)} onClick={() => void mutate([{ op: "create-outfit", outfit: newId, metadata: { label: newId } }])}>Add outfit</button>
      </fieldset>}
      {editing && collection.hidden_outfits.map(hidden => <button key={hidden} disabled={busy}
        onClick={() => void mutate([{ op: "restore-outfit", outfit: hidden }])}>Restore outfit {hidden}</button>)}
      {outfit && <>
        <p className="field-hint">{outfit.source} · Missing expressions use {outfit.fallback_expression} within this outfit.</p>
        {editing && <OutfitEditor key={`${id}/${collection.effective_revision}`} id={id} outfit={outfit} busy={busy} mutate={mutate} />}
        {preview && outfit.expressions[preview] && <SpritePreview target={target} id={id} label={preview} outfit={outfit} close={() => setPreview(null)} />}
        <ExpressionGrid target={target} id={id} outfit={outfit} labels={[...new Set([...collection.standard_labels, ...Object.keys(outfit.expressions), ...(custom ? [custom] : [])])]}
          editing={editing} busy={busy} upload={upload} mutate={mutate} preview={setPreview} />
        {editing && <>
          <label>Custom expression label<input value={custom} onChange={e => { if (!e.target.value || /^[a-z][a-z0-9_-]{0,63}$/.test(e.target.value)) setCustom(e.target.value); }} /></label>
          {(collection.hidden_expressions[id] ?? []).map(label => <button key={label} disabled={busy}
            onClick={() => void mutate([{ op: "restore-expression", outfit: id, expression: label }])}>Restore {label}</button>)}
        </>}
      </>}
    </>}
  </section>;
}
