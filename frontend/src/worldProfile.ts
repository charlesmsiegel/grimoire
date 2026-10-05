import type { WorldProfile } from "./api/client";

/** A world profile (#38) as a form holds it: themes stay one comma-separated
 *  string while being typed, and become a list only when a request is built.
 *  Shared by the world shelf's create form and the overview's About editor. */
export type ProfileDraft = { genre: string; tone: string; themes: string; description: string };

export const EMPTY_DRAFT: ProfileDraft = { genre: "", tone: "", themes: "", description: "" };

export function profileOf(draft: ProfileDraft): WorldProfile {
  return { genre: draft.genre.trim(), tone: draft.tone.trim(), description: draft.description,
           themes: draft.themes.split(",").map((t) => t.trim()).filter(Boolean) };
}

export function draftOf(profile: WorldProfile): ProfileDraft {
  return { genre: profile.genre, tone: profile.tone, themes: profile.themes.join(", "),
           description: profile.description };
}
