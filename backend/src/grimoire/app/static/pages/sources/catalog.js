// What "Characters and lore" shows of a `sources.catalog` answer, as pure functions (no DOM), so they are tested on
// their own (tests/js/library.unit.mjs): the role an entry is listed under, the shelves and their counts, which
// entries a role and a shelf leave, their order, and the line under each name.
//
// How often and how lately an entry was played is not in the catalog: it is counted from the `chats` op's list,
// and only when that list is whole (see playsOf). Without it nothing here invents a count or an order by play.

import { PINNED, hasTags, marksOf } from "./shelf.js";

export const ROLES = [["", "Everything"], ["character", "Characters"], ["scenario", "Scenarios"], ["lore", "Lore"],
  ["world", "World entries"]];
const ROLE_OF_KIND = { character: "character", scenario: "scenario", lorebook: "lore" };
const KINDS = { character: "Character", scenario: "Scenario", lorebook: "Lorebook", location: "Location", item: "Item" };
export const CARDS = "cards";  // the shelf of every imported card's source
export const SORTS = [["recent", "Recent"], ["name", "A-Z"], ["played", "Most played"]];
export const PAGE = 48;

// The role an entry is listed under: a character, a scenario, lore (a lorebook), or a world entry (a location, an
// item and every other kind).
export function roleOf(entry) {
  return ROLE_OF_KIND[entry.kind] || "world";
}

// The role an address asks for. "persona" is the characters: any character can be a persona.
export function askedRole(text) {
  if (text === "persona") return { role: "character", persona: true };
  return { role: ROLES.some(([name]) => name && name === text) ? text : "", persona: false };
}

export function kindLabel(kind) {
  const text = String(kind || "");
  return KINDS[text] || (text ? text[0].toUpperCase() + text.slice(1) : "Entry");
}

export function count(n, one, many) { return n + " " + (n === 1 ? one : many); }

// Whether an entry stands on a shelf. `source` is one source's shelf, by its exact id; else `shelf` is a built-in
// one: "" everything, CARDS every imported card, PINNED every entry the person pinned (by `marks`, the shelf of
// ./shelf.js). The two are told apart by which is given, never by the id's spelling: a source may be named
// "cards" or "pinned".
export function onShelf(entry, shelf, marks, source = "") {
  if (source) return entry.source === source;
  if (!shelf) return true;
  if (shelf === CARDS) return String(entry.source).startsWith("card:");
  if (shelf === PINNED) return marksOf(marks, entry.ref).pinned;
  return false;  // a built-in shelf this page does not know lists nothing
}

// The shelves: Everything, Imported cards (when there are any), then one for each source that is not a card's, the
// worlds first, each with how many of `entries` stand on it. With `marks` (the app keeps pins), Pinned comes
// second, counting the listed entries that are pinned. A source's shelf says so (`source: true`), its id being the
// source's own.
export function shelvesOf(sources, entries, marks) {
  const per = new Map();
  for (const entry of entries) per.set(entry.source, (per.get(entry.source) || 0) + 1);
  const shelves = [{ id: "", label: "Everything", count: entries.length }];
  if (marks) shelves.push({ id: PINNED, label: "Pinned", count: entries.filter((entry) => marksOf(marks, entry.ref).pinned).length });
  const cards = sources.filter((source) => source.namespace === "card");
  if (cards.length) shelves.push({ id: CARDS, label: "Imported cards", count: cards.reduce((sum, source) => sum + (per.get(source.source) || 0), 0) });
  const rest = sources.filter((source) => source.namespace !== "card")
    .map((source) => ({ id: source.source, label: source.title || source.source, count: per.get(source.source) || 0, source: true,
      world: source.namespace === "world" }));
  rest.sort((a, b) => Number(b.world) - Number(a.world) || a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
  return [...shelves, ...rest.map(({ world: _world, ...shelf }) => shelf)];
}

// How many of `entries` each role lists: { "": all, character, scenario, lore, world }.
export function countRoles(entries) {
  const counts = Object.fromEntries(ROLES.map(([name]) => [name, 0]));
  for (const entry of entries) { counts[""] += 1; counts[roleOf(entry)] += 1; }
  return counts;
}

// The entries a shelf, a role and tags leave: an entry must carry every one of `tags`.
export function filterEntries(entries, { role = "", shelf = "", source = "", tags = [], marks = null } = {}) {
  return entries.filter((entry) => onShelf(entry, shelf, marks, source) && (!role || roleOf(entry) === role) && hasTags(marks, entry.ref, tags));
}

// How often and how lately each entry is in a chat, from the `chats` op's list: Map(ref -> { count, last }). The
// list is whole only when it holds fewer chats than were asked for; otherwise this is null, and nothing about
// play is shown or sorted by.
export function playsOf(chats, limit) {
  if (!Array.isArray(chats) || chats.length >= limit) return null;
  const plays = new Map();
  for (const chat of chats) {
    for (const ref of new Set((chat.sources || []).map((source) => source.ref))) {
      const found = plays.get(ref) || { count: 0, last: "" };
      found.count += 1;
      if (typeof chat.last_activity === "string" && chat.last_activity > found.last) found.last = chat.last_activity;
      plays.set(ref, found);
    }
  }
  return plays;
}

// The orders on offer: by name always; by play only when play is known.
export function sortsFor(plays) {
  return SORTS.filter(([name]) => name === "name" || plays).map(([value, label]) => ({ value, label }));
}

// The order an address asks for, or the first on offer that it could mean.
export function askedSort(text, plays) {
  const sorts = sortsFor(plays);
  return sorts.some((sort) => sort.value === text) ? text : sorts[0].value;
}

const byName = (a, b) => (a.name || "").localeCompare(b.name || "", undefined, { sensitivity: "base" }) || a.ref.localeCompare(b.ref);

// `entries` in an order: "name" (A to Z, an unnamed entry by its ref), "recent" (played last first, then the
// rest by name) or "played" (in the most chats first). An order by play without `plays` is by name.
export function sortEntries(entries, sort, plays) {
  const sorted = [...entries];
  if (!plays || sort === "name") return sorted.sort(byName);
  const of = (entry) => plays.get(entry.ref) || { count: 0, last: "" };
  if (sort === "played") return sorted.sort((a, b) => of(b).count - of(a).count || byName(a, b));
  return sorted.sort((a, b) => (of(a).last < of(b).last ? 1 : of(a).last > of(b).last ? -1 : byName(a, b)));
}

const startOfDay = (time) => { const date = new Date(time); date.setHours(0, 0, 0, 0); return date.getTime(); };

// A moment as a person says it: today, yesterday, "3 days ago", or its date. "" when it is not a moment.
export function when(text, now = Date.now()) {
  const then = Date.parse(text);
  if (!Number.isFinite(then)) return "";
  const days = Math.round((startOfDay(now) - startOfDay(then)) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return days + " days ago";
  return new Date(then).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

// The line under an entry's name: where it lives, then its chats and when it was last played, when play is known.
export function metaLine(entry, title, plays, now = Date.now()) {
  const parts = [title || entry.source];
  if (entry.derived) parts.push("a portrayal");
  const play = plays ? plays.get(entry.ref) : null;
  if (play && play.count) parts.push(count(play.count, "chat", "chats"), when(play.last, now));
  return parts.filter(Boolean).join(" · ");
}

// Whether a "Chat" button belongs under an entry: a character or a scenario that resolves.
export function canChat(entry) {
  return (entry.kind === "character" || entry.kind === "scenario") && !entry.problem;
}
