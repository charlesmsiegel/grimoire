// The campaign pages' pure logic: no DOM and no calls, so it is tested by itself (tests/js/campaign.test.mjs).
// What the list shows of a campaign, how it is searched, filtered and sorted, which scenes are in progress and
// who each waits on, and what each view of the hub may show.

// The most campaigns the list asks a hub for (each is one `campaign.hub` call, as the table sees it).
export const CHECKED = 24;

export function campaignName(item) { return item.title || item.campaign; }

export function chatName(chat) { return chat.label || chat.name || chat.chat; }

// One row of the list, from one campaign of `campaign.chats` (its chats come newest first).
export function summarize(item) {
  const chats = item.chats || [];
  const cast = [];
  const seen = new Set();
  for (const chat of chats) {
    for (const member of chat.cast || []) {
      if (seen.has(member.ref)) continue;
      seen.add(member.ref);
      cast.push({ name: member.name || member.ref, key: member.ref });
    }
  }
  const newest = chats[0] || null;
  const last = chats.map((chat) => chat.last_activity || "").sort().at(-1) || "";
  return { campaign: item.campaign, name: campaignName(item), world: item.world || null, chats: chats.length, newest,
    last, cast, problems: chats.filter((chat) => chat.problem).length,
    text: [item.campaign, item.title, item.world, ...chats.map(chatName), ...cast.map((member) => member.name)]
      .filter(Boolean).join("\n").toLowerCase() };
}

// Who an open scene waits on, read from the hub as the table sees it: `who` is "you" (the player has
// something to give), "director", "labelling" (no one has said how the action reads yet) or null (nothing is
// waiting). An effect that waits on the player's own key comes before anything that waits on someone else.
export function waiting(hub) {
  if (!hub) return { who: null, text: "" };
  const action = hub.open_action && !hub.open_action.outcome ? hub.open_action : null;
  const pending = pendingEffects(hub);
  const mine = pending.filter(({ effect }) => effect.endorsers.some((key) => key.principal === hub.player && !key.given));
  const said = [];
  let who = null;
  if (action && action.label && !action.framed) { who = "you"; said.push("Confirm how the action was read"); }
  if (mine.length) { who = "you"; said.push(count(mine.length, "change") + " waiting on your key"); }
  if (action && action.framed) { who = who || "director"; said.push("The director's decision"); }
  if (action && !action.label) { who = who || "labelling"; said.push("The action is not labelled yet"); }
  const theirs = pending.length - mine.length;
  if (theirs) { who = who || "director"; said.push(count(theirs, "change") + " waiting on the director"); }
  return { who, text: said.join(" · ") };
}

// The effects still waiting on a key, oldest turn first.
export function pendingEffects(hub) {
  const found = [];
  for (const turn of hub.turns || []) {
    for (const effect of turn.effects || []) if (effect.state === "proposed" || effect.state === "approved") found.push({ turn, effect });
  }
  return found;
}

// What the list knows of a checked campaign: whether its newest chat has a scene in progress (an action
// still open, or a change still waiting on a key), who it waits on, and whether play is paused there.
export function situation(hub) {
  if (!hub) return null;
  const wait = waiting(hub);
  const action = hub.open_action && !hub.open_action.outcome ? hub.open_action : null;
  const actor = action && action.actor ? action.actor.name || action.actor.ref : null;
  return { open: wait.who !== null, who: wait.who, waiting: wait.text, paused: Boolean(hub.paused), actor,
    line: action && action.framed ? action.framed.intent : null };
}

// The scenes in progress, as cards: "Your turn" first, then whichever was played last.
export function inProgress(rows, situations) {
  return rows.map((row) => ({ row, at: situations.get(row.campaign) })).filter((item) => item.at && item.at.open)
    .sort((a, b) => Number(b.at.who === "you") - Number(a.at.who === "you") || compare(b.row.last, a.row.last)
      || compare(a.row.name, b.row.name));
}

export function turnText(at) {
  return at.who === "you" ? "Your turn" : at.who === "director" ? "Waiting on the director" : "Waiting on a labelling";
}

// The filters the list offers. "Needs you" and "Paused" are read from each campaign's hub, so they are offered
// only once every campaign listed has been checked; until then, and beyond CHECKED campaigns, there is "All".
export function filters(rows, situations, complete) {
  const all = { value: "all", label: "All " + rows.length };
  if (!complete || !rows.length) return [all];
  const count_ = (test) => rows.filter((row) => test(situations.get(row.campaign))).length;
  return [all, { value: "needs", label: "Needs you " + count_(needsYou) }, { value: "paused", label: "Paused " + count_(isPaused) }];
}

const needsYou = (at) => Boolean(at && at.who === "you");
const isPaused = (at) => Boolean(at && at.paused);

// The rows a search and a filter leave. Search reads a campaign's name, id and world, its chats and its cast.
export function narrowed(rows, situations, { search = "", filter = "all" } = {}) {
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  const test = filter === "needs" ? needsYou : filter === "paused" ? isPaused : null;
  return rows.filter((row) => words.every((word) => row.text.includes(word)) && (!test || test(situations.get(row.campaign))));
}

// The table's columns. `first` is the direction a first click sorts in; a column with none does not sort.
// "Waiting on" is there only when a hub was read for some campaign.
export function columns(checked) {
  return [{ id: "name", label: "Campaign", first: "ascending" }, { id: "last", label: "Last interaction", first: "descending" },
    ...(checked ? [{ id: "waiting", label: "Waiting on", first: "descending" }] : []),
    { id: "chats", label: "Chats", first: "descending" }, { id: "cast", label: "Cast", first: null }];
}

export const DEFAULT_SORT = { column: "last", direction: "descending" };

// Clicking a column's header: the column already sorted turns round, another starts in its own first direction.
export function nextSort(sort, column) {
  if (!column.first) return sort;
  if (sort.column !== column.id) return { column: column.id, direction: column.first };
  return { column: column.id, direction: sort.direction === "ascending" ? "descending" : "ascending" };
}

export function ariaSort(sort, column) { return sort.column === column.id ? sort.direction : "none"; }

export function arrow(sort, column) { return sort.column !== column.id ? "" : sort.direction === "ascending" ? "▲" : "▼"; }

const RANK = { you: 3, director: 2, labelling: 1 };

// The rows in the order asked. Ties fall back to the name, then the id, so the order never depends on how the
// rows came. Descending "Waiting on" puts what waits on you first; a campaign waiting on nothing comes last.
export function sorted(rows, situations, sort) {
  const rank = (row) => { const at = situations.get(row.campaign); return at && at.who ? RANK[at.who] : 0; };
  const by = { name: (a, b) => compare(a.name.toLowerCase(), b.name.toLowerCase()), last: (a, b) => compare(a.last, b.last),
    chats: (a, b) => a.chats - b.chats, waiting: (a, b) => rank(a) - rank(b) }[sort.column];
  const way = sort.direction === "descending" ? -1 : 1;
  const tie = (a, b) => compare(a.name.toLowerCase(), b.name.toLowerCase()) || compare(a.campaign, b.campaign);
  return [...rows].sort((a, b) => (by ? way * by(a, b) : 0) || tie(a, b));
}

function compare(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

// A time as the list says it: today, yesterday, a few days ago, or its date. `now` is a Date.
export function when(stamp, now = new Date()) {
  const at = new Date(stamp);
  if (!stamp || Number.isNaN(at.getTime())) return "";
  const day = (date) => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  const days = Math.round((day(now) - day(at)) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return days + " days ago";
  return at.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// -- the hub ----------------------------------------------------------------------------------------------

// What a view of the hub may show, in order. World truth, NPC goals and proposing an effect are the
// director's alone: they are shown only when the director's view was asked for AND the hub answered as the
// director's, so a table hub never shows them whatever it carries.
export function hubSections(hub, audience) {
  const director = isDirector(hub, audience);
  const codes = new Set((hub.problems || []).map((problem) => problem.code));
  const cut = codes.has("effect_unsettled") || codes.has("effect_untagged") || codes.has("effect_elsewhere");
  return ["action", ...(cut ? ["recover"] : []), "cast", "holdings", "changes", ...(hub.obstacles && hub.obstacles.length ? ["obstacles"] : []),
    ...(director ? ["truth", "goals"] : []), "knowledge", ...(director ? ["propose"] : []), "turns", "sources", "unavailable"];
}

export function isDirector(hub, audience) {
  return audience === "director" && hub.audience === "director";
}

// Whose knowledge a view may ask for: the table's, each character the player plays, and (the director only)
// anyone else in the cast. Another character's perspective asked at the table is refused by the hub as well.
export function knowledgeTabs(hub, audience) {
  const director = isDirector(hub, audience);
  const tabs = [{ value: "", label: "The table" }];
  for (const member of hub.cast || []) {
    if (director || (member.role === "player" && member.controller === hub.player)) tabs.push({ value: member.actor, label: member.name || member.actor });
  }
  return tabs;
}

// How a factor bears on the action (029's bearings): for it, against it, or (a word this page does not know) neither.
export function leaning(bearing) {
  return bearing === "favours" ? "for" : bearing === "hinders" || bearing === "prevents" ? "against" : "";
}

export function count(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
