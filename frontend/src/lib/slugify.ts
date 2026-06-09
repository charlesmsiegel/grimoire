/**
 * Derive a URL/file-safe id ([a-z0-9-]) from a human name.
 *
 * The single slugify for the frontend — library entity ids and campaign ids
 * must agree on spelling so the same name produces the same id everywhere.
 * Apostrophes vanish (rather than becoming dashes) so "Bryn's Hollow" →
 * "bryns-hollow". `maxLength` truncates without leaving a trailing dash;
 * campaign creation caps ids at 64 chars.
 */
export function slugify(name: string, opts: { maxLength?: number } = {}): string {
  const slug = name
    .toLowerCase()
    .replace(/['']/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (opts.maxLength === undefined) return slug;
  return slug.slice(0, opts.maxLength).replace(/-+$/, "");
}
