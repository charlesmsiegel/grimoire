/** Dirty state compares JSON values, not object insertion history. Clearing
 *  and re-entering an optional bound may move its key, but does not change
 *  the form. This projection is only for comparison; saves keep their own
 *  member order, and arrays (fields, groups) remain ordered here too. */
export function moduleFormFingerprint(form: unknown): string | undefined {
  return JSON.stringify(form, (_key: string, value: unknown) => {
    if (value === null || typeof value !== "object" || Array.isArray(value)) return value;
    const entries = Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
    return Object.fromEntries(entries);
  });
}
