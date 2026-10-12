import { ApiError } from "../../api/client";

/** The viewer's removal copy (2026-10-12 viewer removal design, §1.2–§1.3).
 *  The viewer itself only shows a descriptor's `removal.question` and what an
 *  `onRemove` rejected with; the surfaces that supply removal build both here,
 *  so every surface asks the same question in the same words. */

export const CHANGED_NOT_REMOVED = "This picture changed since it was loaded, so it was not removed.";
export const EDIT_FIRST = "Finish or cancel the edit in the Art panel first.";
export const IN_COLLECTION = "This picture is part of a collection, so it can't be deleted from the library.";
export const REMOVE_FAILED = "Could not remove this picture.";

/** A slot as a sentence names it: the primary image by the record's own word
 *  for it, `gallery_3` as "gallery 3", anything else with its underscores
 *  read as spaces. */
export function slotPhrase(name: string, primaryLabel?: string): string {
  if (name === "avatar") return (primaryLabel ?? "Primary image").toLowerCase();
  const gallery = /^gallery_(\d+)$/.exec(name);
  if (gallery) return `gallery ${gallery[1]}`;
  return name.replace(/_/g, " ");
}

export type RemovalSubject =
  | { surface: "record"; scope: "world" | "campaign"; record: string; name: string; primaryLabel?: string }
  | { surface: "campaign-library"; inherited: boolean; name: string }
  | { surface: "world-library"; name: string };

/** The confirmation sentence, by what the server will do with the request. */
export function removalQuestion(q: RemovalSubject): string {
  switch (q.surface) {
    case "record": {
      const slot = slotPhrase(q.name, q.primaryLabel);
      return q.scope === "world"
        ? `Remove ${q.record}'s ${slot}?`
        : `Hide ${q.record}'s ${slot} only in this campaign? Any inherited world image in this slot is hidden too.`;
    }
    case "campaign-library":
      return q.inherited
        ? `Hide ${q.name} in this campaign? The world keeps it.`
        : `Remove ${q.name} from this campaign's library?`;
    case "world-library":
      return `Delete ${q.name} from the world library? Every campaign on this world loses it.`;
  }
}

/** What a failed removal says. A bare error code is never shown. */
export function removalError(error: unknown): string {
  if (!(error instanceof ApiError)) return REMOVE_FAILED;
  if (error.kind === "image_in_collection" || error.detail === "image_in_collection") return IN_COLLECTION;
  return error.detail || REMOVE_FAILED;
}
