/** The edit form's address for one task's override row (spec 3.3): the key
 *  encoded whole, as every route link has always encoded it. */
export const taskHash = (key: string) => `#task-${encodeURIComponent(key)}`;

/** The Advanced section itself, for "N task overrides active". */
export const ADVANCED_HASH = "#advanced";

/** The task a hash names, or null -- for a hash that names none, and for one
 *  whose escape is malformed (`#task-%ZZ`), which `decodeURIComponent` would
 *  throw on: a bad link opens the page, never crashes it. */
export function taskFromHash(hash: string): string | null {
  if (!hash.startsWith("#task-")) return null;
  const raw = hash.slice("#task-".length);
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}
