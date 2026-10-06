import type { ResponseSettingsRecord, ResponseSwipe, ResponseVariant } from "../../api/types";

const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

/** The settings a variant was composed under. A resume-composed variant
 *  carries its own copy (`made_by.settings`), because a later roll fence
 *  recomposes and overwrites the record's single `resume_settings`; one written
 *  before that copy existed falls back to `resume_settings`, and every other
 *  variant was written under `settings`. One choice, for the counter's title
 *  and the variants disclosure alike. */
export function settingsFor(
  madeBy: ResponseVariant["made_by"],
  record: { settings?: ResponseSettingsRecord | null; resume_settings?: ResponseSettingsRecord | null },
): ResponseSettingsRecord | null | undefined {
  if (madeBy?.settings) return madeBy.settings;
  return madeBy?.composed === "resume" ? record.resume_settings : record.settings;
}

/** The tooltip lines for one variant's provenance, in the shape `altTitle`
 *  gives the legacy alternates: one line each, an absent key skipped rather
 *  than filled in. `settings` are the ones the variant was composed under, and
 *  a recorded `style_id` of "" means no style was applied, so it adds no line. */
export function madeByLines(
  madeBy: ResponseVariant["made_by"],
  settings: ResponseSettingsRecord | null | undefined,
): string[] {
  if (!madeBy) return [];
  return [
    madeBy.guidance ? `Guided: ${madeBy.guidance}` : "",
    madeBy.note ? `Note: ${madeBy.note}` : "",
    madeBy.model ? `Model: ${madeBy.model}` : "",
    madeBy.connection ? `Via: ${madeBy.connection}` : "",
    settings?.style_id ? `Style: ${settings.style_id}` : "",
    settings ? `Length: ~${plural(settings.words, "word")}, ${plural(settings.paragraphs, "paragraph")}` : "",
  ].filter(Boolean);
}

/** The counter's `title`: the active variant's provenance, or `undefined` when
 *  there is nothing to say (no active variant, a variant from before `made_by`
 *  existed, or one that recorded no keys). */
export function swipeTitle(swipe: ResponseSwipe): string | undefined {
  const variant = swipe.active === null ? undefined : swipe.variants[swipe.active];
  if (!variant?.made_by) return undefined;
  const lines = madeByLines(variant.made_by, settingsFor(variant.made_by, swipe));
  return lines.length ? lines.join("\n") : undefined;
}
