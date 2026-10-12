import type { ArtCreated, ArtLinkResult, ArtProvenance, ArtSourceRef, DescriptionSaveResult,
  DescriptionState, RecordArtImage, RecordArtRead, RecordArtTarget } from "../../api/types";

export type ImageViewerCloseReason = "unavailable";
/** What a surface's `onRemove` settled as: `"changed"` only when its fresh
 *  re-read still holds the key (the version guard refused). */
export type RemoveOutcome = "removed" | "changed";
export const REMOVED_IMAGE_NOTICE = "This image is no longer available.";

/** Display data stays independent of record CRUD. The same object can have
 *  several occurrences, crops, and destinations; identity here names the
 *  occurrence, while version names the bytes whose geometry is being shown. */
export type ImageDescriptor = {
  key: string; originalUrl: string; thumbnailUrl: string; label: string; alt: string; version: string;
  recordLabel?: string; primaryLabel?: "Avatar" | "Primary image";
  downloadUrl?: string; recordDestination?: string; destinationLabel?: string; imageId?: string | null;
  width?: number | null; height?: number | null; animated?: boolean;
  description?: string; described?: boolean; descriptionAvailable?: boolean; descriptionState?: DescriptionState;
  role?: "primary" | "gallery" | "related" | "library" | "inline";
  provenance?: ArtProvenance;
  /** Present only where the opening surface can remove this picture: the
   *  sentence its inline confirmation asks. */
  removal?: { question: string };
};

export type RecordArtAdapter = {
  target: RecordArtTarget;
  read(options?: { fresh?: boolean; signal?: AbortSignal }): Promise<RecordArtRead>;
  append(file: File): Promise<ArtCreated>;
  reuse(source: ArtSourceRef, intent: "gallery" | "primary", expectedPrimary?: string | null): Promise<ArtLinkResult>;
  /** Takes only what the delete and its placement check read, so a gallery row
   *  carrying a placement can be removed without fabricating the rest. */
  remove(image: Pick<RecordArtImage, "name" | "v" | "placement">): Promise<{ ok: boolean }>;
  promote(image: RecordArtImage, expectedPrimary: string | null): Promise<{ ok: boolean }>;
  setFocus(image: RecordArtImage, focus: number): Promise<{ ok: boolean }>;
  setDescription(image: RecordArtImage, text: string, expectedDescriptionRev: string): Promise<DescriptionSaveResult>;
  finishDescription(image: RecordArtImage, text: string, expectedDescriptionRev: string): Promise<DescriptionSaveResult>;
  draftDescription?: (image: RecordArtImage) => Promise<string>;
};

export function recordArtKey(target: RecordArtTarget): string {
  return JSON.stringify([target.scope.kind, target.scope.id, target.kind, target.record_id, target.version ?? null]);
}
