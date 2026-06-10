/**
 * Patch-mode diff for the structured override editor (issue #601): the form
 * starts from the cascade-resolved frontmatter, and only the keys that
 * changed are submitted. The backend shallow-merges them into the existing
 * override, so untouched keys keep whatever override they already carry.
 */

/** Shallow diff: changed/added keys verbatim; removed keys as null tombstones. */
export function overridePatch(
  initial: Record<string, unknown>,
  draft: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(draft)) {
    if (key === "id") continue;
    if (JSON.stringify(value) !== JSON.stringify(initial[key])) out[key] = value;
  }
  for (const key of Object.keys(initial)) {
    if (key === "id") continue;
    if (!(key in draft)) out[key] = null;
  }
  return out;
}
