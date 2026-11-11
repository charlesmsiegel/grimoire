// The shelf as the pages read it: tags and pins on entries, pins on chats and saved filters (docs/SHELF.md).
// Pure functions over a `shelf.read` answer (no DOM), tested on their own (tests/js/library.unit.mjs), and one
// reader that asks the app for it. Characters and lore, Chats and an entry's sheet all read the shelf here, so a
// tag or a pin means the same thing on each.
//
// A mark is found by the entry's exact unpinned ref or the chat's exact ref, never by a name. A mark about
// something the library no longer lists matches nothing here, so it is simply not shown.

export const PINNED = "pinned";  // the shelf of every pinned entry, and the Chats filter of every pinned chat
const NONE = Object.freeze({ tags: Object.freeze([]), pinned: false, etag: null });

// The shelf of a library that has none, or of an app that does not serve it.
export function emptyShelf() {
  return { entries: new Map(), chats: new Map(), filters: [], problems: [], limits: {}, failed: null };
}

// A `shelf.read` answer, with entries and chats by their exact ref.
export function indexShelf(answer) {
  const shelf = emptyShelf();
  for (const item of (answer && answer.entries) || []) shelf.entries.set(item.ref, { tags: item.tags || [], pinned: Boolean(item.pinned), etag: item.etag || null });
  for (const item of (answer && answer.chats) || []) shelf.chats.set(item.chat, { pinned: Boolean(item.pinned), etag: item.etag || null });
  shelf.filters = ((answer && answer.filters) || []).slice();
  shelf.problems = ((answer && answer.problems) || []).slice();
  shelf.limits = (answer && answer.limits) || {};
  return shelf;
}

// What the person noted about one entry: { tags, pinned, etag }. Nothing noted is no tags, not pinned, no etag.
export function marksOf(shelf, ref) {
  return (shelf && shelf.entries.get(ref)) || NONE;
}

export function chatMarks(shelf, chat) {
  return (shelf && shelf.chats.get(chat)) || NONE;
}

// The refs of the pinned chats, for model.js's grouping.
export function pinnedChats(shelf) {
  const pinned = new Set();
  if (shelf) for (const [chat, marks] of shelf.chats) if (marks.pinned) pinned.add(chat);
  return pinned;
}

const fold = (text) => String(text).normalize("NFC").toLowerCase();

// The tags a person typed, separated by commas: trimmed, single-spaced, and one of each whatever its capitals
// (the first spelling typed wins). The app checks each name again and refuses the ones it does not keep.
export function parseTags(text) {
  const seen = new Set();
  const tags = [];
  for (const part of String(text || "").split(",")) {
    const tag = part.replace(/\s+/g, " ").trim();
    if (!tag || seen.has(fold(tag))) continue;
    seen.add(fold(tag));
    tags.push(tag);
  }
  return tags;
}

// The tags an address asks for (`tags=crew,noir`), and the same list as an address says it.
export function askedTags(text) { return parseTags(text); }
export function tagsParam(tags) { return (tags || []).join(","); }

// `tags` with `tag` added, or taken away when it is there.
export function toggleTag(tags, tag) {
  return tags.includes(tag) ? tags.filter((item) => item !== tag) : [...tags, tag];
}

// Whether an entry carries every one of `tags` (several tags combine with AND; no tags is every entry).
export function hasTags(shelf, ref, tags) {
  if (!tags || !tags.length) return true;
  const own = marksOf(shelf, ref).tags;
  return tags.every((tag) => own.includes(tag));
}

// The tag chips over `entries`: every tag one of them carries, with how many carry it, A to Z, then every
// chosen tag none of them carries (with 0), so a chosen tag can always be unchosen.
export function tagChips(shelf, entries, chosen = []) {
  const counts = new Map();
  for (const entry of entries) for (const tag of marksOf(shelf, entry.ref).tags) counts.set(tag, (counts.get(tag) || 0) + 1);
  for (const tag of chosen) if (!counts.has(tag)) counts.set(tag, 0);
  return [...counts].map(([tag, count]) => ({ tag, count, chosen: chosen.includes(tag) }))
    .sort((a, b) => a.tag.localeCompare(b.tag, undefined, { sensitivity: "base" }) || (a.tag < b.tag ? -1 : 1));
}

// Every tag in use on the shelf, A to Z: what a tag editor offers.
export function knownTags(shelf) {
  const tags = new Set();
  if (shelf) for (const marks of shelf.entries.values()) for (const tag of marks.tags) tags.add(tag);
  return [...tags].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }) || (a < b ? -1 : 1));
}

// What a view of Characters and lore is, as a saved filter keeps it: one role, one shelf or one source, the
// tags, the search and the order. `shelf` here is the page's own: "", "cards", "pinned" or a source's id.
export function filterOf({ role = "", shelf = "", source = "", tags = [], query = "", sort = "" } = {}) {
  return { role: role || "", shelf: source ? "" : shelf || "", source: source || null,
    tags: [...tags].sort((a, b) => (fold(a) < fold(b) ? -1 : fold(a) > fold(b) ? 1 : 0)), query: (query || "").trim(), sort: sort || "" };
}

// Whether two views are the same one (tags in any order).
export function sameFilter(a, b) {
  const x = filterOf(a), y = filterOf(b);
  return x.role === y.role && x.shelf === y.shelf && x.source === y.source && x.query === y.query && x.sort === y.sort
    && x.tags.length === y.tags.length && x.tags.every((tag, index) => tag === y.tags[index]);
}

// Whether a view is anything but the whole library in its usual order: only then is there a filter to save.
export function worthSaving(view) {
  const { role, shelf, source, tags, query } = filterOf(view);
  return Boolean(role || shelf || source || tags.length || query);
}

// The address query that opens a saved filter (see page.js: role, q, source, shelf, tags, sort).
export function filterQuery(saved) {
  const view = filterOf(saved);
  return { role: view.role, q: view.query, source: view.source || "", shelf: view.shelf, tags: tagsParam(view.tags), sort: view.sort };
}

// A name to offer for a view: its tags, then its role ("crew + noir characters").
export function suggestName(view, roleLabel) {
  const { tags, query } = filterOf(view);
  const parts = [tags.join(" + "), query ? JSON.stringify(query) : "", roleLabel ? roleLabel.toLowerCase() : ""].filter(Boolean);
  return parts.join(" ").slice(0, 60);
}

// What a bulk action did, in a sentence: "Pinned 3 entries." and, when some were refused, how many and which.
export function bulkSummary(verb, done, failures) {
  const said = [verb + " " + done + (done === 1 ? " entry." : " entries.")];
  if (failures.length) {
    said.push(failures.length + (failures.length === 1 ? " was" : " were") + " not changed: "
      + failures.map((failure) => failure.name + " (" + failure.reason + ")").join(", ") + ".");
  }
  return said.join(" ");
}

// Ask the app for the shelf. null when the app does not serve it (nothing about tags or pins is then shown);
// a failure to read it is an empty shelf that says so (`failed`), so the page it is on still opens.
export async function readShelf(ctx) {
  if (!ctx.has("shelf.read")) return null;
  try {
    return indexShelf(await ctx.api("shelf.read", {}));
  } catch (error) {
    if (error && error.reason === "left") throw error;
    return { ...emptyShelf(), failed: error };
  }
}

// What to say of the shelf files that could not be read, or "" when all could.
export function problemText(shelf) {
  if (!shelf) return "";
  if (shelf.failed) return "Your tags and pins could not be read (" + (shelf.failed.reason || shelf.failed.message || "unknown") + "), so none are shown.";
  if (!shelf.problems.length) return "";
  const named = shelf.problems.slice(0, 3).map((problem) => problem.path + " (" + problem.reason + ")").join(", ");
  const more = shelf.problems.length > 3 ? " and " + (shelf.problems.length - 3) + " more" : "";
  return (shelf.problems.length === 1 ? "One file of your tags and pins" : shelf.problems.length + " files of your tags and pins")
    + " could not be read and " + (shelf.problems.length === 1 ? "is" : "are") + " left as " + (shelf.problems.length === 1 ? "it is" : "they are")
    + ": " + named + more + ". What it says is not shown until the file is mended or removed.";
}
