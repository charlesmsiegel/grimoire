import { useEffect, useRef, useState } from "react";
import { api, type NativeActorPreview, type NativeActorTarget } from "../../api/client";
import { errorText } from "../../api/errors";

export function NativeActorPack({ target }: { target: NativeActorTarget }) {
  const [actorId, setActorId] = useState(`${target.actor_id}-copy`);
  const [preview, setPreview] = useState<NativeActorPreview | null>(null);
  const [disposition, setDisposition] = useState<"bind" | "detached">("detached");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imported, setImported] = useState(false);
  const live = useRef(false);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const destination = { ...target, actor_id: actorId };
  async function inspect(file: File) {
    setBusy(true); setError(null); setPreview(null); setImported(false);
    try { const result = await api.previewNativeActor(destination, file); if (live.current) { setPreview(result); setDisposition("detached"); } }
    catch (err) { if (live.current) setError(errorText(err)); }
    finally { if (live.current) setBusy(false); }
  }
  async function publish() {
    if (!preview || busy) return;
    setBusy(true); setError(null);
    try { await api.publishNativeActor(destination, preview.token, disposition); if (live.current) { setImported(true); setPreview(null); } }
    catch (err) { if (live.current) setError(errorText(err)); }
    finally { if (live.current) setBusy(false); }
  }
  return <details className="native-actor-pack"><summary>Full character / PC pack</summary>
    <p>Includes all versions, ordinary art and expression outfits. Existing local actors are never replaced.</p>
    <a href={api.nativeActorExportUrl(target)} download>Export all versions and assets</a>
    {error && <p role="alert">{error}</p>}
    {imported && <p role="status">Imported {actorId}. Open it from the character or PC list.</p>}
    <fieldset disabled={busy}><legend>Import a native pack into this {target.scope}</legend>
      <label>Destination actor ID<input value={actorId} onChange={e => { setActorId(e.target.value); setPreview(null); }} /></label>
      <label>Native character ZIP<input type="file" accept=".zip" onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void inspect(file); }} /></label>
      {preview && <>
        <p>Versions: {preview.versions.join(", ")}</p>
        <label>World inheritance<select aria-label="World inheritance" value={disposition} onChange={e => setDisposition(e.target.value as typeof disposition)}>
          <option value="detached">Detached copy of the exported appearance</option>
          {preview.can_bind && <option value="bind">Keep inheritance from the matching world character</option>}
        </select></label>
        {preview.has_dependency && !preview.can_bind && <p>The exported world dependency does not match here. Import as detached to retain its visible sprites.</p>}
        <button onClick={() => void publish()}>Import full actor pack</button>
      </>}
    </fieldset>
    {busy && <p role="status">Processing actor pack…</p>}
  </details>;
}
