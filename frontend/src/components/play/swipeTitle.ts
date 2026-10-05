import type { ResponseSettingsRecord, ResponseSwipe, ResponseVariant } from "../../api/types";

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
    settings ? `Length: ~${settings.words} words, ${settings.paragraphs} paragraphs` : "",
  ].filter(Boolean);
}

/** The counter's `title`: the active variant's provenance, or `undefined` when
 *  there is nothing to say (no active variant, a variant from before `made_by`
 *  existed, or one that recorded no keys). A resume-composed variant was
 *  written under `resume_settings`, every other under `settings`. */
export function swipeTitle(swipe: ResponseSwipe): string | undefined {
  const variant = swipe.active === null ? undefined : swipe.variants[swipe.active];
  if (!variant?.made_by) return undefined;
  const settings = variant.made_by.composed === "resume" ? swipe.resume_settings : swipe.settings;
  const lines = madeByLines(variant.made_by, settings);
  return lines.length ? lines.join("\n") : undefined;
}
