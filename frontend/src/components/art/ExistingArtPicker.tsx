import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../../api/client";
import { errorText } from "../../api/errors";
import { ART_RECORD_KINDS, type ArtLinkResult, type ArtSourceRef } from "../../api/types";
import { useHotkeys } from "../../shortcuts/useHotkeys";
import { localImageDownloadUrl } from "./recordArtAdapter";
import { ImageTile } from "./ImageTile";
import { ImageViewer } from "./ImageViewer";
import { useArtModal } from "./useArtModal";
import { useImageViewer } from "./useImageViewer";
import { recordArtKey, type ImageDescriptor, type RecordArtAdapter } from "./types";

type SourceRow = { image: ImageDescriptor; source: ArtSourceRef; kind: string; text: string };
export function ExistingArtPicker({ adapter, expectedPrimary, onCreated, onClose }: {
  adapter: RecordArtAdapter; expectedPrimary: string | null;
  onCreated: (result: ArtLinkResult) => Promise<void> | void; onClose: () => void;
}) {
  // Freeze both destination context and primary expectation at opening. A
  // shared refresh behind the picker cannot silently retarget the operation.
  const [destination] = useState(adapter), [primaryAtOpen] = useState(expectedPrimary);
  const scope = destination.target.scope;
  const [rows, setRows] = useState<SourceRow[] | null>(null), [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SourceRow | null>(null), [primary, setPrimary] = useState(false);
  const [query, setQuery] = useState(""), [kind, setKind] = useState("all"), [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false), live = useRef(true);
  const modal = useArtModal(undefined, undefined, 'input[aria-label="Search art"]');
  const viewer = useImageViewer("picker/" + recordArtKey(destination.target));
  useHotkeys([
    { keys: "escape", whileTyping: true, enabled: !busy, run: onClose },
    { keys: "tab", whileTyping: true, run: () => modal.trap(false) },
    { keys: "shift+tab", whileTyping: true, run: () => modal.trap(true) },
  ], { modal: true });
  // The accepted route owns the picker. Submitted reuse stays captured, but
  // leaving must not trap navigation or let its callback close a later picker.
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    let live = true; setRows(null); setError(null);
    async function read() {
      const [gallery, library] = await Promise.all([
        scope.kind === "world" ? api.listWorldImages(scope.id, true) : api.listCampaignGallery(scope.id, true),
        scope.kind === "world" ? api.listWorldLibrary(scope.id, true) : api.listCampaignImages(scope.id, true).then((result) => result.images),
      ]);
      const records: SourceRow[] = gallery.filter((row) => (ART_RECORD_KINDS as readonly string[]).includes(row.kind)
        && row.provenance !== "world-original" && row.placement?.surface === "record" && row.v)
        .map(row => {
          const actor = row.kind === "characters" || row.kind === "pcs";
          const recordLabel = row.record_name + (actor ? " / " + row.vid : "");
          const label = recordLabel + " · " + row.name;
          return { kind: row.kind, source: row.placement!, text: label + " " + row.description,
            image: { key: JSON.stringify(row.placement), label, recordLabel,
              primaryLabel: actor ? "Avatar" as const : "Primary image" as const,
              alt: row.description || label, originalUrl: row.url, thumbnailUrl: row.thumb, downloadUrl: localImageDownloadUrl(row.url),
              role: row.name === "avatar" ? "primary" as const : "gallery" as const,
              version: row.v!, imageId: row.image_id, description: row.description, described: row.described, provenance: row.provenance } };
        });
      // Combined gallery's older library rows have no byte token. The explicit
      // library listing supplies tokens and effective campaign visibility;
      // never recover mutation ownership by parsing a serving URL.
      const libraryRows: SourceRow[] = library.map((row) => {
        const source: ArtSourceRef = { surface: "library", scope, name: row.name, expected_v: row.v };
        const url = (w?: number) => scope.kind === "world"
          ? api.worldImageUrl(scope.id, row.name, { v: row.v, w }) : api.campaignImageUrl(scope.id, row.name, { v: row.v, w });
        const inherited = "inherited" in row && row.inherited;
        const label = (scope.kind === "world" || inherited ? "World library" : "Campaign library") + " · " + row.name;
        return { kind: "library", source, text: label + " " + (row.description ?? ""), image: {
          key: JSON.stringify(source), label, alt: row.description || label, originalUrl: url(), thumbnailUrl: url(160), downloadUrl: localImageDownloadUrl(url()),
          version: row.v, imageId: row.image_id, role: "library", description: row.description, described: row.described,
          provenance: scope.kind === "world" ? "world" : inherited ? "inherited" : "campaign",
        } };
      });
      if (live) setRows([...records, ...libraryRows]);
    }
    void read().catch((reason) => { if (live) setError(errorText(reason)); });
    return () => { live = false; };
  }, [scope, reload]);
  const filtered = rows?.filter((row) => (kind === "all" || row.kind === kind) && row.text.toLowerCase().includes(query.trim().toLowerCase())) ?? [];
  async function save() {
    if (!selected || busy) return;
    setBusy(true); setError(null);
    try { const result = await destination.reuse(selected.source, primary ? "primary" : "gallery", primaryAtOpen); if (live.current) await onCreated(result); }
    catch (reason) { if (live.current) setError(errorText(reason)); }
    finally { if (live.current) setBusy(false); }
  }
  return createPortal(<div ref={modal.root} className="tagline-modal-backdrop art-picker-backdrop" role="dialog" aria-modal="true" aria-label="Use existing art">
    <div className="tagline-modal art-picker">
      <h3>Use existing art</h3>
      {viewer.notice && <p role="status">{viewer.notice}</p>}
      <label>Search art<input aria-label="Search art" value={query} disabled={busy} onChange={(event) => setQuery(event.target.value)} /></label>
      <label>Art source<select aria-label="Art source" value={kind} disabled={busy} onChange={(event) => setKind(event.target.value)}>
        <option value="all">All sources</option>{[...ART_RECORD_KINDS, "library"].map((value) => <option key={value} value={value}>{value}</option>)}
      </select></label>
      {error && <p role="alert">{error}</p>}
      {!rows && !error && <p role="status">Loading art...</p>}
      {rows && !filtered.length && <p>No matching images</p>}
      <div className="art-image-grid">{filtered.map((row) => <div key={row.image.key}>
        <ImageTile image={row.image} onOpen={(opener) => viewer.open(filtered.map((item) => item.image), row.image.key, opener)} />
        <label><input type="radio" name="existing-art" aria-label={"Select " + row.image.label} disabled={busy}
          checked={selected?.image.key === row.image.key} onChange={() => setSelected(row)} />Select</label>
      </div>)}</div>
      {selected && <p>Selected: {selected.image.label}</p>}
      <label><input type="checkbox" checked={primary} disabled={busy} onChange={(event) => setPrimary(event.target.checked)} />Make primary</label>
      <div className="form-actions">
        <button disabled={!selected || busy} onClick={() => { void save(); }}>Add selected image</button>
        <button disabled={busy} onClick={() => { setSelected(null); setReload((n) => n + 1); }}>Reload art</button>
        <button disabled={busy} onClick={onClose}>Cancel</button>
      </div>
    </div>
    {viewer.viewerProps && <ImageViewer {...viewer.viewerProps} />}
  </div>, modal.portal);
}
