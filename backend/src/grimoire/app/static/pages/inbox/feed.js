// The inbox feed: the one list of what waits on the person (`inbox.list`, docs/INBOX.md), and the one way an
// item is decided. Home, the chat tray, Devices and sync, the campaign hub and the inbox page all import this
// module, so none of them keeps a list or a way to decide of its own.
//
// Each item says how it is decided: the existing op and its exact arguments (etags, fingerprints), and the
// question that op's own screen asks first. `perform` asks that question and makes that one call, so deciding
// here is the same write, with the same refusals, as deciding where the thing lives.

export const OP = "inbox.list";
export const ROUTE = "inbox";

// Whether this app has an inbox to read and to link to.
export function served(ctx) {
  return ctx.has(OP) && ctx.registered(ROUTE);
}

export function countLabel(count) {
  return count === 1 ? "1 thing needs you" : count + " things need you";
}

// The feed: everything in the library that waits (and the rail's count is set from it), or, with `chat`, that
// chat's own items only (its tray), which says nothing of the rest and leaves the rail's count alone.
export async function loadFeed(ctx, { chat } = {}) {
  const feed = await ctx.api(OP, chat ? { chat } : {});
  if (!chat) ctx.setCount(ROUTE, feed.count ? { value: feed.count, label: countLabel(feed.count), bad: true } : null);
  return feed;
}

// The inbox's address, open on one item when its id is given. `chat` is the chat the item waits in: the whole
// list looks only at the chats played last, so the page reads that chat's own items too when the item is not
// among them (an approval waiting in an older chat is still decided from its link).
export function itemHref(ctx, id, chat) {
  return ctx.href(ROUTE, [], id ? (chat ? { item: id, chat } : { item: id }) : undefined);
}

// The ids of the items a campaign hub links to (as `inbox.list` makes them).
export const itemId = {
  label: (chat, turn) => "label:" + chat + ":" + turn,
  effect: (chat, turn, number) => "effect:" + chat + ":" + turn + ":" + number,
};

// What sync left waiting: forks and keep-both conflicts.
export function fromSync(feed) {
  return feed.items.filter((item) => item.kind === "fork" || item.kind === "conflict");
}

// Where an item happened, as an address, when this app has that screen.
export function whereHref(ctx, item) {
  const route = item.route;
  return route && ctx.registered(route.route) ? ctx.href(route.route, route.path, route.query) : null;
}

// What the feed could not read, as sentences: a list that stopped early says so, never passes for complete.
export function feedNotes(feed) {
  const notes = [];
  const unknown = [];
  for (const problem of feed.problems || []) {
    if (problem.source === "sync" && problem.code === "conflicts_unknown") unknown.push(problem.campaign);
    else if (problem.source === "sync") notes.push({ bad: true, text: "What sync left waiting could not be read (" + problem.code + ")." });
    else if (problem.source === "campaign") notes.push({ bad: true, text: "What a chat's campaign waits on could not be read (" + problem.code + "): " + problem.chat });
    else notes.push({ bad: true, text: "A chat could not be opened to check it (" + problem.code + "): " + problem.chat });
  }
  if (unknown.length) {
    notes.push({ bad: true, text: "This device's sync records for " + unknown.join(", ")
      + " cannot be read in full, so any of their files may be in conflict and not all can be listed." });
  }
  const seen = feed.inspected || {};
  if (seen.more_chats) notes.push({ bad: false, text: "Only the " + seen.chats + " chats played last are checked." });
  if (seen.more_items) notes.push({ bad: false, text: "Only the first " + feed.items.length + " are listed." });
  return notes;
}

// Decide an item one way. Asks the action's own question first; answers null when the person said no, else
// { decided, text, result }: whether the item is now decided, what to say of it, and the op's own answer.
// A refusal rejects, as any call does.
export async function perform(ctx, item, action) {
  const ask = action.confirm;
  if (ask && !(await ctx.ui.confirm(ask.message, { confirmLabel: ask.label, danger: Boolean(ask.danger) }))) return null;
  let result;
  try { result = await ctx.api(action.op, action.args); }
  catch (error) {
    // the op refused before writing anything, and the item says what to ask instead (a scene with no NPC who
    // can speak is answered by the director, as the chat page asks)
    const otherwise = action.otherwise;
    if (!otherwise || error.reason !== otherwise.reason) throw error;
    result = await ctx.api(action.op, otherwise.args);
  }
  // a resolve that could not reach the other device answers, with nothing changed: the item still waits
  const decided = action.op !== "media.resolve" || result.status === "resolved";
  return { decided, text: decided ? action.done : "Not decided: " + result.status + (result.reason ? " (" + result.reason + ")" : "") + ". Nothing changed.", result };
}

// A button for one action of an item: the first is the primary one, a dangerous one says so.
export function actionButton(ctx, action, index, onclick, { size = "" } = {}) {
  const node = ctx.ui.button(action.label, onclick, { kind: action.kind === "danger" ? "danger" : index === 0 ? "primary" : "", size });
  node.setAttribute("data-focus-key", "inbox:" + action.id);
  return node;
}
