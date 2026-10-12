import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { errorText } from "../../api/errors";
import type { ArtCreated, ArtSourceRef, DescriptionRecovery, DescriptionSaveResult,
  RecordArtImage, RecordArtRead, RecordArtTarget } from "../../api/types";
import { onArtChanged } from "../../appEvents";
import { createRecordArtAdapter, isDescriptionState, toImageDescriptor } from "./recordArtAdapter";
import { useArtNavigation } from "./ArtNavigationProvider";
import { useImageViewer } from "./useImageViewer";
import { recordArtKey } from "./types";

type CaptionSnapshot = Extract<DescriptionRecovery, { available: true }>;
export type DescriptionDraft = {
  kind: "description"; image: RecordArtImage; text: string; initialText: string; revision: string; attemptedSave: boolean;
  partial: Extract<DescriptionSaveResult, { status: "partial" }> | null; conflict: CaptionSnapshot | null;
};
export type FocusDraft = { kind: "focus"; image: RecordArtImage; focus: number; initial: number };
type BatchResult = { key: string; file: File; status: "saved"; created: ArtCreated } | { key: string; file: File; status: "failed"; error: string };
type State = {
  owner: string; data: RecordArtRead | null; status: "idle" | "loading" | "empty" | "ready" | "failed";
  manage: boolean; draft: DescriptionDraft | FocusDraft | null; busy: string | null; error: string | null;
  readError: string | null; message: string | null; batch: BatchResult[];
  partialReuse: { created: ArtCreated; expectedPrimary: string | null } | null;
  uploadProgress: { filename: string; completed: number; total: number } | null;
};
const initialState = (owner: string): State => ({ owner, data: null, status: owner ? "loading" : "idle", manage: false,
  draft: null, busy: null, error: null, readError: null, message: null, batch: [], partialReuse: null, uploadProgress: null });
const partialReuseMessage = "Image added, but it could not be made primary. Retry promotion on the added image.";
function primaryIsCreated(data: RecordArtRead | null | undefined, created: ArtCreated): boolean {
  return !!data?.images.some(image => image.name === "avatar" && image.image_id === created.image_id && image.v === created.v);
}
function rowSnapshot(image: RecordArtImage): CaptionSnapshot | null {
  return image.description_available && image.description_rev && image.effective_description && image.write_target_description
    ? { available: true, description_rev: image.description_rev, effective_description: image.effective_description,
      write_target_description: image.write_target_description } : null;
}
function errorSnapshot(error: ApiError): CaptionSnapshot | null {
  const body = error.body;
  return body && typeof body.description_rev === "string" && isDescriptionState(body.effective_description)
    && isDescriptionState(body.write_target_description) ? { available: true, description_rev: body.description_rev,
      effective_description: body.effective_description, write_target_description: body.write_target_description } : null;
}

/** A controller belongs to a selected record/version, including its header.
 *  Reads may replace displayed metadata, never a draft's captured tokens.
 *  Writes capture the adapter before awaiting; stale results cannot migrate
 *  into another selected record or a library remounted after a store move. */
export function useRecordArt(target: RecordArtTarget | null, { onSaved, recordName, versionName }: { onSaved: () => Promise<void>; recordName?: string; versionName?: string }) {
  const scopeKind = target?.scope.kind, scopeId = target?.scope.id, kind = target?.kind;
  const rid = target?.record_id, version = target?.version;
  const adapter = useMemo(() => {
    if (!scopeKind || !scopeId || !kind || !rid) return null;
    const common = { scope: { kind: scopeKind, id: scopeId }, record_id: rid };
    return createRecordArtAdapter(kind === "characters" || kind === "pcs"
      ? { ...common, kind, version: version ?? "" } : { ...common, kind });
  }, [scopeKind, scopeId, kind, rid, version]);
  const targetKey = adapter ? recordArtKey(adapter.target) : "";
  // A departed record stays departed even when A -> B -> A selects its address
  // again. Old writes must not own the later A's draft or pending flag.
  const context = useRef({ target: targetKey, generation: 0 });
  if (context.current.target !== targetKey) {
    context.current = { target: targetKey, generation: context.current.generation + 1 };
  }
  const key = targetKey ? targetKey + "/" + context.current.generation : "";
  const owner = useRef(key); owner.current = key;
  const alive = useRef(true), reads = useRef(0), aborts = useRef(new Set<AbortController>());
  const batchSequence = useRef(0), editorGeneration = useRef(0), operationGeneration = useRef(0), descriptionReads = useRef(0);
  const pending = useRef({ owner: key, value: false });
  if (pending.current.owner !== key) pending.current = { owner: key, value: false };
  const saved = useRef(onSaved); saved.current = onSaved;
  const [state, setState] = useState<State>(() => initialState(key));
  const current = state.owner === key ? state : initialState(key);
  const held = useRef(current); held.current = current;
  const navigation = useArtNavigation(), viewer = useImageViewer(key);
  const closeViewer = viewer.close;
  const lastLocation = useRef(navigation.locationKey);
  const entryRef = useRef<HTMLButtonElement>(null);
  // Actor context columns become closed phone sheets; their main heading is a
  // surviving visible return target when the tile that opened Art disappears.
  const focusRef = useRef<HTMLHeadingElement>(null);
  const transitioning = useRef(false);
  const isCurrent = useCallback(() => alive.current && owner.current === key, [key]);
  const update = useCallback((patch: Partial<State> | ((previous: State) => State)) => {
    if (!alive.current || owner.current !== key) return;
    setState((previous) => {
      const mine = previous.owner === key ? previous : initialState(key);
      return typeof patch === "function" ? patch(mine) : { ...mine, ...patch };
    });
  }, [key]);
  const refresh = useCallback(async (fresh = true): Promise<RecordArtRead | null> => {
    if (!adapter) return null;
    const sequence = ++reads.current, abort = new AbortController();
    aborts.current.add(abort);
    try {
      const data = await adapter.read({ fresh, signal: abort.signal });
      // Callers that reconcile an editor must not receive an older snapshot
      // merely because the gallery itself correctly rejected that response.
      if (!alive.current || owner.current !== key || reads.current !== sequence) return null;
      update({ data, status: data.images.length ? "ready" : "empty", readError: null });
      return data;
    } catch (error) {
      if (!alive.current || owner.current !== key || abort.signal.aborted) return null;
      if (reads.current === sequence) update((previous) => ({ ...previous,
        status: "failed", readError: errorText(error) }));
      return null;
    } finally { aborts.current.delete(abort); }
  }, [adapter, key, update]);
  useEffect(() => {
    alive.current = true;
    const activeAborts = aborts.current;
    return () => { alive.current = false; activeAborts.forEach((abort) => abort.abort()); };
  }, []);
  useEffect(() => {
    update(initialState(key));
    void refresh(false);
    const unwatch = onArtChanged(() => { void refresh(true); });
    const activeAborts = aborts.current;
    return () => { unwatch(); activeAborts.forEach((abort) => abort.abort()); reads.current += 1; };
  }, [key, refresh, update]);
  useEffect(() => {
    if (lastLocation.current === navigation.locationKey) return;
    lastLocation.current = navigation.locationKey;
    if (!isCurrent()) return;
    // Record identity alone does not detect Back between two Art URLs for the
    // same version, or leaving Art while the header controller stays mounted.
    editorGeneration.current += 1;
    operationGeneration.current += 1;
    update({ manage: false, draft: null, error: null, message: null, batch: [], partialReuse: null, uploadProgress: null });
    // A route can change while this same record controller stays mounted.
    // Retire its old read and request metadata belonging to the accepted URL.
    void refresh(true);
    closeViewer();
  }, [navigation.locationKey, isCurrent, update, refresh, closeViewer]);
  const openPanel = useCallback(() => {
    // Initial loading is already owned by the controller; reopening a mounted
    // record's panel must bypass cached metadata without discarding its draft.
    if (held.current.data || held.current.status === "failed") void refresh(true);
  }, [refresh]);
  const closePanel = useCallback(() => {
    if (!isCurrent()) return;
    // Clear only after the panel actually leaves. A different navigation guard
    // may refuse after our confirmation, in which case the draft remains.
    editorGeneration.current += 1;
    update({ manage: false, draft: null, error: null });
    closeViewer();
  }, [isCurrent, update, closeViewer]);
  const allowDraft = useCallback(() => {
    const draft = held.current.draft;
    const dirty = draft?.kind === "description" ? draft.text !== draft.initialText || !!draft.partial || draft.attemptedSave
      : draft?.kind === "focus" && draft.focus !== draft.initial;
    return !dirty || window.confirm("Discard unsaved art changes? Saved images and descriptions will remain.");
  }, []);
  const discardDraft = useCallback(() => {
    if (!isCurrent()) return;
    editorGeneration.current += 1;
    update({ draft: null, error: null });
  }, [isCurrent, update]);
  useEffect(() => navigation.registerDraftGuard(allowDraft, discardDraft), [navigation, allowDraft, discardDraft]);
  function transition(action: () => void) {
    // Entries and their page's tab action may share a synchronous transition.
    // The outer action already checked every guard; held state only catches up
    // on the next render, so a nested check would ask about the same draft again.
    if (transitioning.current) { action(); return; }
    const go = () => {
      editorGeneration.current += 1;
      update({ draft: null, error: null });
      transitioning.current = true;
      try { action(); } finally { transitioning.current = false; }
    };
    if (navigation.provided) navigation.requestArtTransition(go);
    else if (allowDraft()) go();
  }
  async function afterSaved(): Promise<RecordArtRead | null> {
    const generation = editorGeneration.current;
    const owns = () => isCurrent() && editorGeneration.current === generation;
    const sequence = reads.current + 1;
    const data = await refresh(true);
    if (!owns()) return null;
    // A newer broadcast can retire this snapshot without failing the read.
    // Its own status reports any failure; do not invent a post-save failure.
    const superseded = reads.current !== sequence;
    try { await saved.current(); }
    catch { if (owns() && (data || superseded)) update({ message: "Saved, but the record could not be refreshed" }); }
    if (owns() && !data && !superseded) update({ message: "Saved, but the gallery could not be refreshed" });
    return owns() ? data : null;
  }
  async function mutation<T>(label: string, action: (owns: () => boolean) => Promise<T>, done?: (result: T, owns: () => boolean) => Promise<void> | void): Promise<T | undefined> {
    if (!adapter || pending.current.value) return undefined;
    const generation = operationGeneration.current;
    const draftGeneration = held.current.draft ? editorGeneration.current : null;
    // Viewing another image in this context leaves uploads/reuse owned. A
    // caption or crop result additionally belongs to its captured editor.
    const owns = () => isCurrent() && operationGeneration.current === generation
      && (draftGeneration === null || editorGeneration.current === draftGeneration);
    // Navigation may retire the surface without cancelling a submitted write.
    // Keep the pending exclusion until it settles, but never publish its UI
    // result into a later editor or continue a departed upload batch.
    pending.current.value = true; update({ busy: label, error: null, message: null });
    try {
      const result = await action(owns);
      if (!owns()) return undefined;
      if (done) await done(result, owns); else { update({ message: "Saved" }); await afterSaved(); }
      return owns() ? result : undefined;
    } catch (error) {
      if (!owns()) return undefined;
      const uncertain = !(error instanceof ApiError) || error.status >= 500;
      update({ error: errorText(error), message: uncertain
        ? "Could not confirm whether this change saved. It may have landed. Review the refreshed art before retrying."
        : null });
      if (error instanceof ApiError && error.kind === "stale_description") {
        const snapshot = errorSnapshot(error);
        update((previous) => ({ ...previous, draft: previous.draft?.kind === "description"
          ? { ...previous.draft, conflict: snapshot } : previous.draft }));
      }
      // A transport failure is ambiguous. Re-read, never repeat the write.
      if (uncertain || error instanceof ApiError && error.kind?.startsWith("stale_")) await refresh(true);
      return undefined;
    } finally { if (isCurrent()) { pending.current.value = false; update({ busy: null, uploadProgress: null }); } }
  }
  const describe = (image: RecordArtImage) => toImageDescriptor(image, { name: recordName, versionName });
  const images = current.data?.images ?? [], primary = images.find((image) => image.name === "avatar");
  function editDescription(image: RecordArtImage) {
    if (!image.description_available || !image.description_rev) {
      update({ error: "Description metadata is unavailable. Reload art to try again." }); return;
    }
    transition(() => update({ draft: { kind: "description", image, text: image.description,
      initialText: image.description, revision: image.description_rev!, attemptedSave: false, partial: null, conflict: null } }));
  }
  function changeDescription(text: string) {
    update((previous) => ({ ...previous, draft: previous.draft?.kind === "description" ? { ...previous.draft, text } : previous.draft }));
  }
  async function saveDescription(text?: string) {
    const draft = held.current.draft;
    if (!adapter || draft?.kind !== "description" || draft.partial || draft.conflict) return;
    const value = text ?? draft.text;
    // Explicit-empty is an edit too: a refusal must recover the attempted
    // value rather than the text that preceded the No description action.
    // A reviewed-empty save changes intent even when both text fields start
    // empty. Keep that refused or uncertain write dirty until resolved/saved.
    update(previous => ({ ...previous, draft: previous.draft?.kind === "description"
      ? { ...previous.draft, text: value, attemptedSave: true } : previous.draft }));
    await mutation("description", () => adapter.setDescription(draft.image, value, draft.revision), async (result, owns) => {
      if (result.status === "partial") {
        update((previous) => ({ ...previous, draft: previous.draft?.kind === "description"
          ? { ...previous.draft, text: value, partial: result, conflict: null } : previous.draft }));
        await afterSaved();
        if (!owns()) return;
        update({ message: "The shared description saved, but the local override could not be cleared" });
      } else { update({ draft: null, message: "Saved" }); await afterSaved(); }
    });
  }
  async function finishDescription() {
    const draft = held.current.draft;
    if (!adapter || draft?.kind !== "description" || !draft.partial?.recovery.available || draft.conflict) return;
    const partial = draft.partial;
    const recovery = partial.recovery;
    if (!recovery.available) return;
    await mutation("description", () => adapter.finishDescription(draft.image, partial.confirmed_text, recovery.description_rev), async (result, owns) => {
      if (result.status === "partial") {
        update((previous) => ({ ...previous, draft: previous.draft?.kind === "description" ? { ...previous.draft, partial: result } : previous.draft }));
        await afterSaved();
        if (!owns()) return;
        update({ message: "The shared description saved, but the local override could not be cleared" });
      } else {
        const data = await afterSaved();
        if (!owns()) return;
        // Text typed after the shared save remains unsaved. Show new metadata
        // for explicit reconciliation rather than silently authorizing a write.
        const row = data?.images.find((image) => image.name === draft.image.name && image.v === draft.image.v);
        update((previous) => ({ ...previous, message: data ? "Saved" : previous.message,
          draft: previous.draft?.kind === "description" && previous.draft.text !== partial.confirmed_text
            ? { ...previous.draft, partial: null, conflict: row ? rowSnapshot(row) : null } : null }));
      }
    });
  }
  async function reloadDescription() {
    const draft = held.current.draft;
    if (draft?.kind !== "description") return;
    const generation = editorGeneration.current, sequence = ++descriptionReads.current;
    const data = await refresh(true);
    if (!isCurrent() || editorGeneration.current !== generation || descriptionReads.current !== sequence || !data) return;
    const row = data.images.find((image) => image.name === draft.image.name);
    if (!row || row.v !== draft.image.v) { update({ error: "This image changed. Review the current image and choose whether to keep your draft for it." }); return; }
    const snapshot = rowSnapshot(row);
    if (!snapshot) { update({ error: "Description metadata is unavailable. Reload art to try again." }); return; }
    update((previous) => ({ ...previous, draft: previous.draft?.kind !== "description" ? previous.draft
      : previous.draft.partial && snapshot.write_target_description.described
        && snapshot.write_target_description.text === previous.draft.partial.confirmed_text
        ? { ...previous.draft, partial: { ...previous.draft.partial, recovery: snapshot }, conflict: null }
        : { ...previous.draft, conflict: snapshot } }));
  }
  function reconcileDescription(choice: "shared" | "draft") {
    // An explicit choice also retires any earlier in-flight reload of this
    // editor; arriving metadata cannot reopen a conflict just reconciled.
    descriptionReads.current += 1;
    update((previous) => {
      const draft = previous.draft;
      if (draft?.kind !== "description" || !draft.conflict) return previous;
      const snapshot = draft.conflict;
      return { ...previous, error: null, draft: { ...draft,
        text: choice === "shared" ? snapshot.write_target_description.text : draft.text,
        initialText: snapshot.effective_description.text, revision: snapshot.description_rev,
        image: { ...draft.image, effective_description: snapshot.effective_description, write_target_description: snapshot.write_target_description },
        partial: null, conflict: null } };
    });
  }
  function rebaseDraft() {
    // Adopting new bytes is a deliberate user decision, never a side effect of
    // a metadata refresh. Keep local work while capturing the current guards.
    // An older caption reload still captured the departed byte token.
    descriptionReads.current += 1;
    update((previous) => {
      const draft = previous.draft, row = previous.data?.images.find(image => image.name === draft?.image.name);
      if (!draft || !row || previous.readError) return previous;
      if (draft.kind === "focus") return { ...previous, error: null,
        draft: { ...draft, image: row, initial: row.focus ?? 50 } };
      const snapshot = rowSnapshot(row);
      if (!snapshot) return { ...previous, error: "Description metadata is unavailable. Reload art to try again." };
      return { ...previous, error: null, draft: { ...draft, image: row,
        revision: snapshot.description_rev, initialText: snapshot.effective_description.text, partial: null, conflict: null } };
    });
  }
  async function generateDescription() {
    const draft = held.current.draft;
    if (!adapter?.draftDescription || draft?.kind !== "description" || draft.partial || draft.conflict) return;
    await mutation("draft", () => adapter.draftDescription!(draft.image), (text) => { changeDescription(text); });
  }
  function editFocus(image: RecordArtImage) {
    if (image.name !== "avatar") return;
    transition(() => update({ draft: { kind: "focus", image, initial: image.focus ?? 50, focus: image.focus ?? 50 } }));
  }
  function changeFocus(focus: number) {
    update((previous) => ({ ...previous, draft: previous.draft?.kind === "focus" ? { ...previous.draft, focus } : previous.draft }));
  }
  async function saveFocus() {
    const draft = held.current.draft;
    if (!adapter || draft?.kind !== "focus") return;
    await mutation("focus", () => adapter.setFocus(draft.image, draft.focus), async () => {
      update({ draft: null, message: "Saved" }); await afterSaved();
    });
  }
  async function upload(files: File[], retryKey?: string) {
    if (!adapter || held.current.draft) return;
    // A retry replaces exactly its failed row. New batches retain earlier
    // failures so their original Files remain available for explicit retries.
    const previous = retryKey ? held.current.batch : held.current.batch.filter((row) => row.status === "failed");
    if (retryKey && !previous.some((row) => row.key === retryKey && row.status === "failed")) return;
    await mutation("upload", async (owns) => {
      const batch: BatchResult[] = [], batchKey = key + "/upload/" + ++batchSequence.current;
      let slot = 0;
      for (const file of files) {
        if (!owns()) break;
        const uploadKey = retryKey ?? batchKey + "/" + slot++;
        update({ uploadProgress: { filename: file.name, completed: batch.length, total: files.length } });
        try { batch.push({ key: uploadKey, file, status: "saved", created: await adapter.append(file) }); }
        catch (error) {
          const refused = error instanceof ApiError && error.status < 500;
          batch.push({ key: uploadKey, file, status: "failed", error: (refused ? "" :
            "Could not confirm upload. Check the refreshed art before retrying: ") + errorText(error) });
        }
        if (!owns()) break;
        update({ uploadProgress: { filename: file.name, completed: batch.length, total: files.length }, batch: retryKey
          ? previous.map((row) => row.key === retryKey ? batch[0] ?? row : row)
          : [...previous, ...batch] });
      }
      return batch;
    }, async (batch) => {
      const successes = batch.filter((row) => row.status === "saved").length;
      update({ message: "Added " + successes + " of " + batch.length + " images" });
      if (successes) await afterSaved();
      else await refresh(true);
    });
  }
  async function retryUpload(retryKey: string) {
    const row = held.current.batch.find((entry) => entry.key === retryKey && entry.status === "failed");
    if (row) await upload([row.file], retryKey);
  }
  async function reuse(source: ArtSourceRef, intent: "gallery" | "primary", expectedPrimary: string | null) {
    if (!adapter || held.current.draft) return undefined;
    return mutation("reuse", () => adapter.reuse(source, intent, expectedPrimary), async (result, owns) => {
      update({ partialReuse: result.status === "partial" ? { created: result.created, expectedPrimary } : null,
        message: result.status === "partial" ? partialReuseMessage : "Saved" });
      const data = await afterSaved();
      if (!owns()) return;
      // A failed promotion can leave a journal that this read rolls forward.
      // The created slot then holds the former primary, so it is no retry target.
      if (result.status === "partial" && primaryIsCreated(data, result.created))
        update(previous => ({ ...previous, partialReuse: null,
          message: previous.message === partialReuseMessage ? "Saved" : previous.message }));
    });
  }
  async function retryPromotion() {
    const partial = held.current.partialReuse;
    if (!adapter || !partial || held.current.draft) return;
    if (!held.current.readError && primaryIsCreated(held.current.data, partial.created)) {
      update({ partialReuse: null, error: null, message: "Saved" }); return;
    }
    const row = held.current.data?.images.find((image) => image.name === partial.created.name);
    if (!row) { update({ error: "Reload art to find the added image before retrying promotion." }); return; }
    const captured = { ...row, v: partial.created.v, placement: { ...row.placement, expected_v: partial.created.v } };
    await mutation("promote", () => adapter.promote(captured, partial.expectedPrimary), async () => {
      update({ partialReuse: null, message: "Saved" }); await afterSaved();
    });
  }
  function openImage(image: RecordArtImage, opener: HTMLElement) {
    transition(() => {
      const rows = image.provenance === "world-original" ? held.current.data?.world_originals ?? [] : held.current.data?.images ?? [];
      const descriptors = rows.map(describe);
      viewer.open(descriptors, describe(image).key, opener);
    });
  }
  const viewerProps = viewer.viewerProps && { ...viewer.viewerProps,
    images: (viewer.viewerProps.images.some((image) => image.provenance === "world-original")
      ? current.data?.world_originals ?? [] : images).map(describe) };
  return {
    ...current, entryRef, focusRef, describe, recordName, viewerNotice: viewer.notice, target: adapter?.target ?? null, adapter, images, primary, preview: primary ?? images[0] ?? null,
    uploadProgress: current.uploadProgress, count: current.data && !current.readError ? images.length : null, openPanel, closePanel, operations: current.data?.operations ?? [], refresh, transition, viewerProps,
    openImage, closeViewer, editDescription, changeDescription, saveDescription, finishDescription,
    reloadDescription, reconcileDescription, rebaseDraft, generateDescription, editFocus, changeFocus, saveFocus,
    cancelDraft: () => transition(() => {}), upload, retryUpload, reuse, retryPromotion,
    // Existing character-only copy/stream actions share pending-write and
    // navigation guards without inventing a generic greeting target.
    runRelated: async (label: string, action: () => Promise<unknown>) => {
      let job: Promise<unknown> | undefined;
      transition(() => { job = mutation(label, action, async () => { await afterSaved(); }); });
      await job;
    },
    setManage: (manage: boolean) => transition(() => update({ manage })),
    promote: async (image: RecordArtImage) => {
      if (adapter && !held.current.draft) await mutation("promote", () => adapter.promote(image, primary?.v ?? null), async () => {
        // The ordinary tile action can finish a partial reuse too. Clear only
        // recovery for these exact created bytes, leaving unrelated work alone.
        update((previous) => ({ ...previous, message: "Saved",
          partialReuse: previous.partialReuse?.created.name === image.name && previous.partialReuse.created.v === image.v
            ? null : previous.partialReuse }));
        await afterSaved();
      });
    },
    remove: async (image: RecordArtImage) => {
      if (adapter && !held.current.draft) await mutation("remove", () => adapter.remove(image));
    },
  };
}
export type RecordArtController = ReturnType<typeof useRecordArt>;
