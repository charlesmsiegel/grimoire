// What the Chats list is made of, as pure functions over the `chats` op's entries (see docs/TEXT_API.md): a
// chat's title, who is in it and where, and searching, sorting and grouping a list of them. Nothing here
// touches the page, so it runs under `node --test` (backend/tests/js/chats.test.mjs). Home reads a chat the
// same way, so a chat is named and dated alike wherever it shows.

export const GROUP_ROWS = 5;   // rows a group shows before "Show N more"
export const FLAT_ROWS = 30;   // rows the ungrouped list shows before "Show N more"

export const FILTERS = [["all", "All"], ["character", "By character"], ["scenario", "By scenario"]];
export const GROUPS = [["campaign", "Campaign"], ["character", "Character"], ["none", "None"]];
// "Longest" needs a turn count, which the list does not carry yet: add ["longest", "Longest"] here and a
// branch in sortChats when it does.
export const SORTS = [["recent", "Recent"], ["name", "A–Z"]];

const NO_CAMPAIGN = "Not in a campaign", NO_CHARACTER = "No character", EVERY = "All chats";

// The view an address asks for: `?filter=character&source=<SourceRef>`. "recent" (the old default) and
// anything unknown are every chat; a source only counts with the filter it belongs to.
export function viewOf(query) {
  const asked = query.get("filter");
  const filter = asked === "character" || asked === "scenario" ? asked : "all";
  return { filter, source: filter === "all" ? null : query.get("source") || null };
}

// A choice kept between visits (a draft), or the first one when it names nothing the page offers.
export function oneOf(options, value) {
  return options.some(([name]) => name === value) ? value : options[0][0];
}

// What a chat is called: its label, else what it was started from, else its exact ref.
export function chatTitle(chat) {
  return chat.label || (chat.launch && chat.launch.name)
    || (chat.launch && chat.launch.kind === "premise" ? "Premise chat" : chat.chat);
}

// Where a chat is played: its campaign's title, else its world, else "No world".
export function chatWhere(chat) {
  return chat.scope && (chat.scope.title || chat.scope.world) ? chat.scope.title || chat.scope.world : "No world";
}

function bound(chat, role) {
  return (chat.sources || []).filter((item) => item.role === role);
}

// The characters a chat is with (not the persona playing in it).
export function chatCharacters(chat) {
  return bound(chat, "character").map((item) => ({ name: item.name || item.ref, key: item.ref }));
}

// The faces a chat shows: its characters, then the persona; a chat that binds no one shows its own initial.
export function chatFaces(chat) {
  const seen = new Set();
  const faces = [];
  for (const item of [...bound(chat, "character"), ...bound(chat, "persona")]) {
    if (seen.has(item.ref)) continue;
    seen.add(item.ref);
    faces.push({ name: item.name || item.ref, key: item.ref });
  }
  return faces.length ? faces : [{ name: chatTitle(chat), key: chat.chat }];
}

// Everything a chat binds besides the persona, by name (a missing name shows the exact ref).
export function chatNames(chat) {
  return (chat.sources || []).filter((item) => item.role !== "persona").map((item) => item.name || item.ref);
}

// The muted line under a chat's title: where it is and what is in it.
export function chatMeta(chat) {
  return [chatWhere(chat), chatNames(chat).join(", ")].filter(Boolean).join(" · ");
}

// "A", "A and B", "A, B and C".
export function listNames(names) {
  if (names.length < 2) return names.join("");
  return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

// The chats whose title, campaign, world or bound names contain every word of `query` (case is ignored).
export function searchChats(chats, query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return chats.slice();
  return chats.filter((chat) => {
    const text = [chatTitle(chat), chatWhere(chat), ...(chat.sources || []).map((item) => item.name || "")].join(" ").toLowerCase();
    return words.every((word) => text.includes(word));
  });
}

// A new list in the order asked: "recent" is latest activity first (a chat with no known activity last),
// "name" is by title. Chats that tie keep the order they came in, so the order is the same every time.
export function sortChats(chats, sort) {
  const rows = chats.map((chat, index) => ({ chat, index }));
  const byName = (a, b) => chatTitle(a.chat).localeCompare(chatTitle(b.chat), undefined, { sensitivity: "base", numeric: true });
  const byRecent = (a, b) => {
    const x = a.chat.last_activity || "", y = b.chat.last_activity || "";
    return x === y ? 0 : x > y ? -1 : 1;  // UTC timestamps of one fixed shape sort as text
  };
  rows.sort((a, b) => (sort === "name" ? byName(a, b) : byRecent(a, b)) || a.index - b.index);
  return rows.map((row) => row.chat);
}

function groupOf(chat, group) {
  if (group === "campaign") {
    const scope = chat.scope;
    return scope && scope.title ? { key: "campaign:" + scope.campaign, label: scope.title } : { key: "", label: NO_CAMPAIGN };
  }
  if (group === "character") {
    const first = chatCharacters(chat)[0];
    // by exact ref: two characters of one name are two groups
    return first ? { key: "character:" + first.key, label: first.name } : { key: "", label: NO_CHARACTER };
  }
  return { key: "all", label: EVERY };
}

// The list as groups, [{key, label, chats}], each group where its first chat stands in the list, so the sort
// orders the groups too. "campaign" groups by the campaign a chat belongs to, "character" by the first
// character it is with, and "none" is one group of everything. No chats is no groups.
export function groupChats(chats, group) {
  const groups = new Map();
  for (const chat of chats) {
    const { key, label } = groupOf(chat, group);
    if (!groups.has(key)) groups.set(key, { key, label, chats: [] });
    groups.get(key).chats.push(chat);
  }
  return [...groups.values()];
}

// The rows of a group that show: the first few, or all of them once it is expanded, and how many more wait.
export function shownRows(chats, group, expanded) {
  const cap = group === "none" ? FLAT_ROWS : GROUP_ROWS;
  const rows = expanded ? chats : chats.slice(0, cap);
  return { rows, more: chats.length - rows.length };
}

export function plural(count, one, many) {
  return count + " " + (count === 1 ? one : many);
}

// The line over the results: "58 chats in 25 groups", or "58 chats" when nothing groups them.
export function countLabel(chats, groups, group) {
  return plural(chats, "chat", "chats") + (group === "none" || !chats ? "" : " in " + plural(groups, "group", "groups"));
}

// How long ago a chat was last played, in words. `timestamp` is the op's UTC "YYYY-MM-DDTHH:MM:SSZ"; one that
// cannot be read gives "" (nothing is shown rather than a guess).
export function when(timestamp, now = Date.now()) {
  const at = Date.parse(timestamp || "");
  if (Number.isNaN(at)) return "";
  const minutes = Math.floor((now - at) / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return plural(minutes, "minute", "minutes") + " ago";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return plural(hours, "hour", "hours") + " ago";
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 30) return days + " days ago";
  return new Date(at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
