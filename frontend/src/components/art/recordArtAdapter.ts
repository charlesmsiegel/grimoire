import { api, ApiError } from "../../api/client";
import { ART_RECORD_KINDS, type DescriptionRecovery, type DescriptionSaveResult,
  type DescriptionState, type RecordArtImage, type RecordArtTarget } from "../../api/types";
import { artRecordHref } from "../../worldPaths";
import { recordArtKey, type ImageDescriptor, type RecordArtAdapter } from "./types";

function capturedTarget(target: RecordArtTarget): RecordArtTarget {
  if (!ART_RECORD_KINDS.includes(target.kind) || !["world", "campaign"].includes(target.scope.kind)
      || !target.scope.id || !target.record_id) throw new Error("Invalid record art target");
  const actor = target.kind === "characters" || target.kind === "pcs";
  if (actor ? typeof target.version !== "string" || !target.version : target.version !== undefined) {
    throw new Error("Actor art needs a selected version; entity art has no version");
  }
  return Object.freeze({ ...target, scope: Object.freeze({ ...target.scope }) });
}

function checkImage(target: RecordArtTarget, image: RecordArtImage): void {
  if (recordArtKey(target) !== recordArtKey(image.placement.target) || image.name !== image.placement.name) {
    throw new Error("This image belongs to a different record or version");
  }
}

export function isDescriptionState(value: unknown): value is DescriptionState {
  if (typeof value !== "object" || value === null) return false;
  const state = value as Record<string, unknown>;
  return (state.scope === null || state.scope === "shared" || state.scope === "legacy")
    && typeof state.text === "string" && typeof state.described === "boolean" && Array.isArray(state.conflicts)
    && state.conflicts.every((c: unknown) => {
      if (typeof c !== "object" || c === null) return false;
      const candidate = c as Record<string, unknown>;
      return typeof candidate.text === "string" && typeof candidate.from === "string";
    });
}

function confirmedPartial(error: unknown): DescriptionSaveResult | null {
  if (!(error instanceof ApiError) || error.status !== 500 || error.kind !== "description_cleanup_incomplete"
      || error.body?.committed !== true || typeof error.body.confirmed_text !== "string") return null;
  const state = error.body.recovery;
  let recovery: DescriptionRecovery = { available: false };
  if (typeof state === "object" && state !== null) {
    const current = state as Record<string, unknown>;
    if (current.available === true && typeof current.description_rev === "string"
        && isDescriptionState(current.effective_description) && isDescriptionState(current.write_target_description)) {
      recovery = { available: true, description_rev: current.description_rev,
        effective_description: current.effective_description, write_target_description: current.write_target_description };
    }
  }
  return { status: "partial", kind: "description_cleanup_incomplete", committed: true,
    confirmed_text: error.body.confirmed_text, recovery };
}

export function createRecordArtAdapter(address: RecordArtTarget): RecordArtAdapter {
  const target = capturedTarget(address);
  async function save(image: RecordArtImage, text: string, revision: string, cleanupOnly: boolean): Promise<DescriptionSaveResult> {
    checkImage(target, image);
    if (!revision) throw new Error("A readable captured description revision is required");
    try {
      await api.setRecordArtDescription(target, image, text, revision, cleanupOnly);
      return { status: "ok" };
    } catch (error) {
      const partial = confirmedPartial(error);
      if (partial) return partial;
      throw error;
    }
  }
  return {
    target,
    read: options => api.readRecordArt(target, options),
    append: file => api.appendRecordArt(target, file),
    reuse: (source, intent, expectedPrimary) => {
      if (intent === "primary" && expectedPrimary === undefined) throw new Error("The displayed primary expectation is required");
      return api.reuseRecordArt(target, source, intent, expectedPrimary);
    },
    remove: image => { checkImage(target, image); return api.removeRecordArt(target, image); },
    promote: (image, expectedPrimary) => { checkImage(target, image); return api.promoteRecordArt(target, image, expectedPrimary); },
    setFocus: (image, focus) => {
      checkImage(target, image);
      if (image.name !== "avatar") throw new Error("Choose a primary before adjusting its crop");
      return api.setRecordArtFocus(target, image, focus);
    },
    setDescription: (image, text, revision) => save(image, text, revision, false),
    finishDescription: (image, text, revision) => save(image, text, revision, true),
    ...(target.scope.kind === "world" ? {
      draftDescription: async (image: RecordArtImage) => {
        checkImage(target, image);
        return (await api.draftRecordArtDescription(target, image)).description;
      },
    } : {}),
  };
}

export function localImageDownloadUrl(url: string): string | undefined {
  if (!url.startsWith("/api/")) return undefined;
  const parsed = new URL(url, "http://localhost");
  parsed.searchParams.delete("w");
  parsed.searchParams.delete("t");
  parsed.searchParams.set("download", "1");
  return parsed.pathname + "?" + parsed.searchParams.toString();
}

export function toImageDescriptor(image: RecordArtImage, identity?: { name?: string; versionName?: string }): ImageDescriptor {
  const target = image.placement.target, actor = target.kind === "characters" || target.kind === "pcs";
  const recordLabel = (identity?.name || target.record_id) + (actor ? " / " + (identity?.versionName || target.version) : "");
  const label = image.name === "avatar" ? "Primary image" : `Gallery image ${image.name.replace(/^gallery_/, "")}`;
  return { key: JSON.stringify([recordArtKey(image.placement.target), image.placement.name]),
    originalUrl: image.url, thumbnailUrl: image.thumb, downloadUrl: localImageDownloadUrl(image.url),
    recordDestination: artRecordHref(target), recordLabel, primaryLabel: actor ? "Avatar" : "Primary image",
    label, alt: image.description.trim() || label, version: image.v, imageId: image.image_id,
    width: image.width, height: image.height, animated: image.animated,
    description: image.description, described: image.described, descriptionAvailable: image.description_available, descriptionState: image.effective_description,
    role: image.name === "avatar" ? "primary" : "gallery", provenance: image.provenance };
}
