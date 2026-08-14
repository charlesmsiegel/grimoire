import type { Availability, SceneIdea, SceneIntentResult, SceneSuggestion } from "../api/client";

/** `kind` is closed: an open string would let an invalid actor kind through
 *  the seam and straight into addCastBatch. */
export type DraftCast = { kind: "characters" | "pcs"; id: string; name: string };

/** `client.ts`'s cast entries type `kind` as an open `string` (it comes off
 *  the wire). Narrow here rather than widen `DraftCast`: an entry whose kind
 *  is neither `"characters"` nor `"pcs"` is dropped rather than let through
 *  with a cast, which is the whole reason the union is closed. */
function narrowCast(cast: { kind: string; id: string; name: string }[]): DraftCast[] {
  return cast.filter(
    (c): c is DraftCast => c.kind === "characters" || c.kind === "pcs",
  );
}

type DraftBase = {
  /** editable in the confirm form */
  title: string;
  /** immutable: what an emptied title falls back to, so the fallback is
   *  executable from the draft alone rather than from whatever produced it */
  defaultTitle: string;
  /** native notation as typed or proposed — set_datetime canonicalizes it */
  date: string;
  /** a location id, "" for none */
  location: string;
  pcless: boolean;
};

/** Greeting drafts carry no premise and no cast BY CONSTRUCTION, because a
 *  greeting has neither to offer: its body is the post it would supply, and
 *  start_from_greeting seats its cast under locked-version rules a form must
 *  not re-implement.
 *
 *  That is a statement about the DRAFT, not about the scene. Since #90 the
 *  reader can decline the greeting in the confirm form and write a premise or
 *  cast the scene by hand instead; those live in that form's state, and when
 *  the greeting is declined nothing seats a cast on the reader's behalf. So
 *  do not read this as "a greeting scene has no premise and no cast" -- it
 *  means only that neither can be resolved this early. */
export type SceneDraft =
  | (DraftBase & { source: "greeting"; gid: string })
  | (DraftBase & {
      source: "generated" | "custom" | "saved"; premise: string; cast: DraftCast[];
      /** the ledger id this draft came from, set only for `"saved"` — the
       *  confirm form marks it used once the scene exists (#88) */
      lid?: string;
    });

export const BLANK_TITLE = "New scene";

export function greetingDraft(g: Availability, nextDate: string, pcless: boolean): SceneDraft {
  return { source: "greeting", gid: g.id, title: g.name, defaultTitle: g.name,
           date: nextDate, location: "", pcless };
}

export function suggestionDraft(s: SceneSuggestion, nextDate: string,
                                pcless: boolean): SceneDraft {
  return { source: "generated", title: s.title, defaultTitle: s.title,
           date: s.date || nextDate, location: s.location?.id ?? "",
           pcless, premise: s.premise, cast: narrowCast(s.cast) };
}

/** A saved ledger idea. Shaped like `suggestionDraft` — the server hands both
 *  back with cast and location resolved — plus the `lid` that lets the confirm
 *  form report which idea became the scene. `nextDate` is the fallback for the
 *  same reason it is there: the date saved with an idea is the one the model
 *  estimated the day it was saved, and it can be blank. */
export function savedDraft(idea: SceneIdea, nextDate: string, pcless: boolean): SceneDraft {
  return { source: "saved", lid: idea.id, title: idea.title, defaultTitle: idea.title,
           date: idea.date || nextDate, location: idea.location?.id ?? "",
           pcless, premise: idea.premise, cast: narrowCast(idea.cast) };
}

/** `typed` is always the premise. The extraction's job is metadata only — it
 *  never replaces what the user wrote. `intent` is null when there was no LLM
 *  connection, the call failed, or nothing was typed. */
export function customDraft(typed: string, intent: SceneIntentResult | null,
                            nextDate: string, pcless: boolean): SceneDraft {
  const title = intent?.title || BLANK_TITLE;
  return { source: "custom", title, defaultTitle: title,
           date: intent?.date || nextDate, location: intent?.location?.id ?? "",
           pcless, premise: typed, cast: narrowCast(intent?.cast ?? []) };
}
