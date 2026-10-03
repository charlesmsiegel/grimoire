import type { TrackerSummary } from "../../api/client";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** `next`, but reusing every part of `prev` that reads the same.
 *
 *  The transcript rows are memoized on what they are handed, and a poll that
 *  finds nothing new would otherwise hand every row a fresh object and
 *  re-render all of them. With the identity kept, a fetch re-renders exactly the
 *  posts whose own entry moved -- and none when nothing did. */
export function shareSummary(prev: TrackerSummary | null, next: TrackerSummary): TrackerSummary {
  if (prev === null) return next;
  const entries: TrackerSummary["entries"] = {};
  for (const [key, entry] of Object.entries(next.entries))
    entries[key] = key in prev.entries && same(prev.entries[key], entry) ? prev.entries[key] : entry;
  const merged: TrackerSummary = {
    enabled: next.enabled,
    names: same(prev.names, next.names) ? prev.names : next.names,
    keys: same(prev.keys, next.keys) ? prev.keys : next.keys,
    entries: same(prev.entries, next.entries) ? prev.entries : entries,
    moods: same(prev.moods, next.moods) ? prev.moods : next.moods,
    labels: same(prev.labels, next.labels) ? prev.labels : next.labels,
  };
  const unchanged = merged.enabled === prev.enabled && merged.names === prev.names
    && merged.keys === prev.keys && merged.entries === prev.entries
    && merged.moods === prev.moods && merged.labels === prev.labels;
  return unchanged ? prev : merged;
}
