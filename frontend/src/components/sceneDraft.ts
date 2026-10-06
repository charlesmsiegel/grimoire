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
 *  not re-implement. The location is not in that set: since #218 a greeting
 *  records the setting it opens at, so there is a real answer to pre-fill with
 *  rather than a rule against guessing one.
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
           date: nextDate, location: g.location ?? "", pcless };
}

/** A generated card. Its own date wins, and `nextDate` is the fallback --
 *  except for an ANCHORED card, which never borrows it (Decision 23).
 *  `nextDate` is the model's "if none is used" estimate and knows nothing of
 *  the anchor, so a card that reads "before The coronation" with its date
 *  blanked (no date, or one the anchor check rejected) would otherwise be
 *  pre-filled with a date that may sit after the very event its reason names.
 *  An empty date leaves the confirm form to the reader instead. */
export function suggestionDraft(s: SceneSuggestion, nextDate: string,
                                pcless: boolean): SceneDraft {
  return { source: "generated", title: s.title, defaultTitle: s.title,
           date: s.date || (s.time_anchor ? "" : nextDate), location: s.location?.id ?? "",
           pcless, premise: s.premise, cast: narrowCast(s.cast) };
}

/** A saved ledger idea. Shaped like `suggestionDraft` — the server hands both
 *  back with cast and location resolved — plus the `lid` that lets the confirm
 *  form report which idea became the scene.
 *
 *  The date precedence is INVERTED from `suggestionDraft`, deliberately:
 *  `nextDate` wins here and the idea's own date is only the fallback. A
 *  generated suggestion's date came out of this minute's snapshot; a saved
 *  idea's is a fossil of whenever it was saved, and the campaign has been
 *  played since. `set_datetime` accepts a date earlier than the campaign's
 *  current moment without complaint (it reports `advanced: false` and moves
 *  on), so preferring the stored one would quietly pre-fill the confirm form
 *  with a date before the scene it follows — and the reader has to notice, in
 *  a field they did not fill in, to avoid it.
 *
 *  `anchor_date` beats both (Decision 23). It is not a fossil: the server
 *  derives it on THIS read, and only for an idea anchored `on` an occurrence
 *  that is still live and dated (§17.1), so it is the anchor's current day,
 *  and `nextDate`, which is not anchor-aware, would contradict the reason the
 *  idea was saved for. An event moved to another day reads as dangling (§17),
 *  so a rescheduled anchor supplies no `anchor_date`.
 *
 *  Without an `anchor_date`, an ANCHORED idea (`before`/`after`, or `on` an
 *  occurrence that has passed) gets an empty date, as `suggestionDraft` gives
 *  an anchored card with none: `nextDate` may sit on the wrong side of the
 *  event the idea's reason names, and the idea's own date is the fossil above.
 *  Only an un-anchored idea -- or a dangling one (its event deleted, or moved
 *  to another day), which reads back with no `time_anchor` -- falls through to
 *  the inverted precedence; a moved one's Stale hint names the new day
 *  (§31, Slice G deviations). */
export function savedDraft(idea: SceneIdea, nextDate: string, pcless: boolean): SceneDraft {
  const date = idea.anchor_date || (idea.time_anchor ? "" : nextDate || idea.date);
  return { source: "saved", lid: idea.id, title: idea.title, defaultTitle: idea.title,
           date, location: idea.location?.id ?? "",
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
