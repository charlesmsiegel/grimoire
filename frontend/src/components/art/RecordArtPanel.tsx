import { useEffect, useRef, useState, type ReactNode } from "react";
import { useArtNavigation } from "./ArtNavigationProvider";
import { AvatarFocusPicker } from "../AvatarFocusPicker";
import { ImageDescriptionField } from "../ImageDescriptionField";
import { ImageUsage } from "../ImageUsage";
import { DescriptionAlternatives, DescriptionConflictPanel } from "./DescriptionConflictPanel";
import { ExistingArtPicker } from "./ExistingArtPicker";
import { ImageTile } from "./ImageTile";
import { toImageDescriptor } from "./recordArtAdapter";
import { recordArtKey, type RecordArtAdapter } from "./types";
import type { RecordArtController } from "./useRecordArt";

export function RecordArtPanel({ controller: c, related }: { controller: RecordArtController; related?: ReactNode }) {
  const files = useRef<HTMLInputElement>(null);
  const navigation = useArtNavigation();
  // Back may accept another Art URL without changing the record/version.
  // A management picker belongs to the accepted route as well as the record.
  const owner = JSON.stringify([c.target ? recordArtKey(c.target) : "", navigation.locationKey]);
  const [picker, setPicker] = useState<{ owner: string; primary: string | null } | null>(null);
  useEffect(() => { setPicker(null); }, [owner]);
  const openPanel = c.openPanel, closePanel = c.closePanel;
  useEffect(() => { openPanel(); return closePanel; }, [openPanel, closePanel]);
  const draftStale = !!c.draft && !c.images.some((image) => image.name === c.draft?.image.name && image.v === c.draft.image.v);
  const descriptionAbsent = c.draft?.kind === "description" && !c.images.some((image) => image.name === c.draft?.image.name);
  const blocked = !!c.busy || !!c.draft;
  const permits = (operation: typeof c.operations[number]) => c.operations.includes(operation);
  const pickerAdapter: RecordArtAdapter | null = c.adapter && { ...c.adapter, reuse: async (source, intent, expected) => {
    const result = await c.reuse(source, intent, expected ?? null);
    if (!result) throw new Error("The request could not be confirmed. Close the picker and review the refreshed art before trying again.");
    return result;
  } };
  function chooseFiles() { c.transition(() => { c.setManage(true); files.current?.click(); }); }
  return <section className="record-art-panel" aria-label="Record art">
    <div className="form-actions">
      <button disabled={!!c.busy || !c.data} onClick={() => c.setManage(!c.manage)}>{c.manage ? "Done" : "Manage art"}</button>
      {(c.manage || c.status === "empty") && <>
        <button disabled={blocked || !permits("append")} onClick={chooseFiles}>Add images</button>
        <button disabled={blocked || !permits("reuse")} onClick={() => setPicker({ owner, primary: c.primary?.v ?? null })}>Use existing art</button>
      </>}
      <button disabled={!!c.busy} onClick={() => { void c.refresh(true); }}>Reload art</button>
    </div>
    <input ref={files} type="file" accept="image/*" multiple hidden aria-label="Image files" disabled={blocked}
      onChange={(event) => { const selected = Array.from(event.target.files ?? []); event.target.value = ""; if (selected.length) void c.upload(selected); }} />
    {c.status === "loading" && <p role="status">Loading art…</p>}
    {c.readError && <p className="banner" role="alert">Art could not be read: {c.readError}</p>}
    {draftStale && c.draft?.kind === "description" && <div role="alert">
      <p>This image changed or was removed while you were editing. Review the current image before applying your draft to it.</p>
      {c.images.some(image => image.name === c.draft?.image.name) && <button disabled={!!c.busy || !!c.readError}
        onClick={c.rebaseDraft}>Keep draft for current image</button>}
    </div>}
    {c.error && <p className="banner" role="alert">{c.error}</p>}
    {c.message && <p role="status">{c.message}</p>}
    {c.uploadProgress && <p role="status">Uploading {c.uploadProgress.filename}: {c.uploadProgress.completed} of {c.uploadProgress.total} files completed.</p>}
    {c.status === "empty" && !c.readError && <p>No images yet</p>}
    {c.batch.length > 0 && <ul className="art-upload-results">{c.batch.map((row) => <li key={row.key}>
      {row.file.name}: {row.status === "saved" ? "Added" : row.error}
      {row.status === "failed" && <button disabled={blocked} onClick={() => { void c.retryUpload(row.key); }}>Retry {row.file.name}</button>}
    </li>)}</ul>}
    {c.partialReuse && <div className="banner"><p>Image added, but it could not be made primary.</p>
      <button disabled={blocked} onClick={() => { void c.retryPromotion(); }}>Retry promotion</button></div>}
    <div className="art-image-grid">{c.images.map((image) => {
      const draft = c.draft?.kind === "description" && c.draft.image.name === image.name ? c.draft : null;
      return <article className="record-art-card" key={image.name}>
        <ImageTile image={c.describe(image)} onOpen={(opener) => c.openImage(image, opener)} />
        <p className="field-hint">{!image.description_available ? "Description metadata unavailable" : image.described ? image.description || "No description (reviewed)" : "Not described"}</p>
        {image.image_id && <ImageUsage imageId={image.image_id} />}
        {c.manage && <div className="record-art-actions">
          {image.name !== "avatar" && <button disabled={blocked || !permits("promote")} aria-label={"Make " + image.name + " primary"}
            onClick={() => { if (window.confirm("Make this image primary? Named narrative links may show different art after this change."))
              void c.promote(image); }}>Make primary</button>}
          {image.name === "avatar" && <button disabled={blocked || !permits("focus")} aria-label="Adjust primary crop"
            onClick={() => c.editFocus(image)}>Adjust crop</button>}
          <ImageDescriptionField name={image.name} value={!image.description_available ? "Description metadata unavailable" : image.described ? image.description : undefined}
            disabled={blocked || !permits("description") || !image.description_available} onEdit={() => c.editDescription(image)}
            onSave={(text) => c.saveDescription(text)} editor={draft ? { text: draft.text, onChange: c.changeDescription,
              busy: !!c.busy, blocked: !!draft.partial || !!draft.conflict || draftStale, onCancel: c.cancelDraft,
              onDraft: c.adapter?.draftDescription ? () => { void c.generateDescription(); } : undefined } : undefined} />
          {draft && <p className="field-hint">
            {draft.image.write_target_description?.scope === "shared"
              ? "Shared description edits apply wherever this image is used."
              : draft.image.write_target_description?.scope === "legacy"
                ? "This description applies only to this placement." : "Description save scope is unavailable."}
          </p>}
          {draft?.image.write_target_description?.scope === "shared" && draft.image.effective_description?.scope === "legacy"
            && <p className="field-hint">Saving also clears this placement's local description override.</p>}
          {draft && !draft.partial && <button disabled={!!c.busy} onClick={() => { void c.reloadDescription(); }}>Reload description</button>}
          {draft && image.effective_description?.scope === "legacy" && <dl>
            <dt>Current effective description</dt><dd>{image.effective_description.text || "No description (reviewed)"}</dd>
            <dt>{image.write_target_description?.scope === "shared" ? "Shared description"
              : image.write_target_description?.scope === "legacy" ? "Description for this placement"
                : "Description save scope unavailable"}</dt><dd>{image.write_target_description?.text || (image.write_target_description?.described ? "No description (reviewed)" : "Not described")}</dd>
          </dl>}
          {draft && !draft.partial && !draft.conflict && draft.image.write_target_description?.scope === "shared"
             && draft.image.write_target_description.conflicts.length > 0 && <section aria-label="Pending shared description conflicts">
               <p>Other shared descriptions are pending. Saving replaces them with this description.</p>
               <DescriptionAlternatives conflicts={draft.image.write_target_description.conflicts} />
             </section>}
           {draft?.partial && <div className="art-description-recovery">
            <h4>Apply saved description</h4>
            <p>Confirmed shared description: {draft.partial.confirmed_text || "No description (reviewed)"}</p>
            {draft.partial.recovery.available && <dl><dt>Effective description</dt><dd>{draft.partial.recovery.effective_description.text || "No description"}</dd>
              <dt>Shared description</dt><dd>{draft.partial.recovery.write_target_description.text || "No description"}</dd></dl>}
            <button disabled={!!c.busy} onClick={() => { void c.reloadDescription(); }}>Reload description</button>
            {draft.partial.recovery.available && !draft.conflict && <button disabled={!!c.busy}
              onClick={() => { void c.finishDescription(); }}>Finish applying saved description</button>}
          </div>}
          {draft?.conflict && <DescriptionConflictPanel snapshot={draft.conflict} draft={draft.text}
            onChoose={c.reconcileDescription} onCancel={c.cancelDraft} busy={!!c.busy} />}
          <button disabled={blocked || !permits("remove")} aria-label={"Remove " + image.name} onClick={() => {
            const role = image.name === "avatar" ? toImageDescriptor(image).primaryLabel : toImageDescriptor(image).label;
            const record = c.recordName || c.target?.record_id || "this record";
            const consequence = " The image remains in its other uses. Named narrative links here may stop showing it."
              + (image.name === "avatar" ? " No replacement primary is chosen." : "");
            // Removing a campaign-owned override also tombstones its world slot.
            const question = c.target?.scope.kind === "campaign"
              ? "Hide " + record + " " + role + " only in this campaign? Any inherited world image in this slot will also be hidden. World art remains in the world and other campaigns."
              : "Remove " + record + " " + role + " placement?";
            if (window.confirm(question + consequence))
              void c.remove(image);
          }}>Remove</button>
        </div>}
      </article>;
    })}</div>
    {descriptionAbsent && c.draft?.kind === "description" && <ImageDescriptionField name={c.draft.image.name}
      value={c.draft.initialText} onSave={(text) => c.saveDescription(text)}
      editor={{ text: c.draft.text, onChange: c.changeDescription, onCancel: c.cancelDraft, busy: !!c.busy, blocked: true }} />}
    {c.draft?.kind === "focus" && <AvatarFocusPicker src={c.draft.image.url} initial={c.draft.initial}
      value={c.draft.focus} onChange={c.changeFocus} aspect={c.target?.kind === "characters" || c.target?.kind === "pcs" ? 1 : 1.6}
      recovery={draftStale || c.readError ? <div role="alert"><p>{c.readError
          ? "Art could not be read. Reload art before applying your crop; your local crop is retained."
          : "This image changed or was removed. Review the current image before applying your crop."}</p>
        <button disabled={!!c.busy} onClick={() => { void c.refresh(true); }}>Reload art</button>
        {!c.readError && c.images.some(image => image.name === c.draft?.image.name) && <>
          <img className="art-recovery-preview" src={c.images.find(image => image.name === c.draft?.image.name)?.url} alt="Current primary art" />
          <button disabled={!!c.busy || !!c.readError} onClick={c.rebaseDraft}>Keep crop for current image</button>
        </>}</div> : undefined}
      label="Primary image" busy={!!c.busy} saveDisabled={draftStale || !!c.readError} error={c.error} onSave={() => { void c.saveFocus(); }} onClose={c.cancelDraft} />}
    {c.data?.world_originals.length ? <section aria-label="World originals"><h3>World originals</h3>
      <p className="field-hint">Read-only art from the world.</p><div className="art-image-grid">{c.data.world_originals.map((image) =>
        <ImageTile key={image.name} image={{ ...c.describe(image), role: "related" }} onOpen={(opener) => c.openImage(image, opener)} />)}</div></section> : null}
    {c.data?.world_originals_error && <p role="alert">World originals could not be read: {c.data.world_originals_error}</p>}
    {related}
    {picker?.owner === owner && pickerAdapter && <ExistingArtPicker adapter={pickerAdapter} expectedPrimary={picker.primary}
      onCreated={() => { setPicker(null); }} onClose={() => setPicker(null)} />}
  </section>;
}
