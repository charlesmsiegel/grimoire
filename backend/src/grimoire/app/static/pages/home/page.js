// Home (108): where the app opens. The chat played last, ready to continue; the chats played before it; and
// the library's characters. A library with no chat yet points to a new chat and to Import instead. A screen
// this app does not have yet is said to be not in this app, never shown as a link that fails. Every value
// shown comes from the library and is set as text.

import { chatCharacters, chatFaces, chatMeta, chatTitle, chatWhere, listNames, when } from "../chats/model.js";
import { itemHref, loadFeed, served as inboxServed } from "../inbox/feed.js";

const RECENT = 3;   // the row cards under "Recent chats"
const SHELF = 11;   // characters shown, so the grid with its Import tile is twelve
const TILTS = 3;    // home-tilt-0..2 in page.css

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const library = ctx.library || {};
  ctx.styles("page.css");
  ctx.layout("page");
  ctx.setTitle(greeting(new Date().getHours()));
  ctx.setCrumb(library.device ? "Library on “" + library.device + "”" : "Library on a device with no name yet");

  const [chats, shelf] = await Promise.all([recentChats(ctx), characters(ctx)]);
  const page = el("div", { class: "home-page" });
  if (!library.generation) page.append(noModel(ctx));
  page.append(chats.error ? ui.errorBox(chats.error) : hero(ctx, chats));
  const columns = [chats.list && chats.list.length ? recent(ctx, chats.list) : null, needsYou(ctx)].filter(Boolean);
  if (columns.length) page.append(el("div", { class: "g-columns" }, columns));
  page.append(yourCharacters(ctx, shelf));
  root.append(page);
}

export function greeting(hour) {
  return hour >= 5 && hour < 12 ? "Good morning" : hour >= 12 && hour < 18 ? "Good afternoon" : "Good evening";
}

// A link to another screen when this app has it; otherwise its name, said to be not in this app yet.
function way(ctx, route, label, make) {
  return ctx.registered(route) ? make() : ctx.ui.el("div", { class: "home-missing" }, label + " (not in this app yet)");
}

// The chats played last: { list } (null when this app cannot list or open chats), or { error }.
async function recentChats(ctx) {
  if (!ctx.has("chats") || !ctx.registered("chat")) return { list: null };
  try { return { list: (await ctx.api("chats", { filter: "recent", limit: RECENT })).chats }; }
  catch (error) { if (error && error.reason === "left") throw error; return { error }; }
}

// The library's characters: { entries, titles } (null entries when this app has no library screen), or { error }.
async function characters(ctx) {
  if (!ctx.has("sources.catalog") || !ctx.registered("source")) return { entries: null };
  try {
    const catalog = await ctx.api("sources.catalog", { kind: "character", limit: SHELF });
    return { entries: catalog.entries.filter((entry) => !entry.problem),
      titles: new Map(catalog.sources.map((source) => [source.source, source.title || source.source])) };
  } catch (error) { if (error && error.reason === "left") throw error; return { error }; }
}

function noModel(ctx) {
  const { ui } = ctx;
  return ui.el("div", { class: "g-notice g-soft home-notice", role: "status" }, "No model is set up, so nobody will answer in a chat yet.",
    way(ctx, "providers", "Set up a model", () => ui.link("Set up a model", "providers")));
}

function action(ctx, route, label, kind) {
  return way(ctx, route, label, () => {
    const node = ctx.ui.buttonLink(label, route, [], undefined, { kind });
    node.classList.add("home-action");
    return node;
  });
}

// The card at the top: the chat played last, or, before any chat, where to begin.
function hero(ctx, chats) {
  const { ui } = ctx;
  const { el } = ui;
  const chat = chats.list && chats.list[0];
  let text, cast = null, label;
  if (chat) {
    const names = chatCharacters(chat).map((person) => person.name);
    const meta = [chatWhere(chat), names.length ? "with " + listNames(names) : "", when(chat.last_activity)].filter(Boolean).join(" · ");
    const resume = ui.buttonLink("Continue chat", "chat", [chat.chat], undefined, { kind: "primary" });
    resume.classList.add("home-action");
    label = "Continue where you left off";
    // The chat's last line goes under the meta line, as a g-quote, once the list of chats carries one.
    text = [ui.eyebrow(label, { accent: true }), el("h2", { class: "g-hero-title" }, chatTitle(chat)),
      el("div", { class: "home-meta" }, meta, chat.problem ? [" ", ui.pill(chat.problem, { kind: "bad" })] : null),
      el("div", { class: "home-actions" }, resume, action(ctx, "start", "Start something new", ""))];
    // Placeholders in each character's own colour: the list of chats names no picture to show instead.
    cast = el("div", { class: "home-cast", "aria-hidden": "true" }, chatFaces(chat).slice(0, TILTS).map((person, index) =>
      el("span", { class: "home-face home-tilt-" + index }, ui.portrait(person.name, { key: person.key }))));
  } else if (chats.list) {
    label = "Start here";
    text = [ui.eyebrow(label, { accent: true }), el("h2", { class: "g-hero-title" }, "Your first chat"),
      el("p", { class: "home-lead" }, "Nothing has been played in this library yet. Start a chat from a character, a scenario or a "
        + "premise, or import a character card first."),
      el("div", { class: "home-actions" }, action(ctx, "start", "New chat", "primary"), action(ctx, "import", "Import a character card", ""))];
  } else {
    label = "Chats";
    text = [ui.eyebrow(label, { accent: true }), el("h2", { class: "g-hero-title" }, "Chats"),
      el("div", { class: "home-missing" }, "Chats (not in this app yet)"),
      el("div", { class: "home-actions" }, action(ctx, "import", "Import a character card", ""))];
  }
  const card = ui.card({ kind: "hero", tag: "section", label }, el("div", { class: "home-hero-text" }, text), cast);
  card.classList.add("home-hero");
  return card;
}

function heading(ctx, title, more) {
  const { el } = ctx.ui;
  return el("div", { class: "g-row g-between" }, el("h2", { class: "g-heading" }, title), more);
}

function more(ctx, route, label) {
  return way(ctx, route, label, () => ctx.ui.el("a", { class: "home-more", href: ctx.href(route) }, label));
}

// Recent chats: the chats played last, each a row card linked by its exact ref.
function recent(ctx, chats) {
  const { ui } = ctx;
  const { el } = ui;
  const rows = chats.map((chat) => {
    const played = when(chat.last_activity);
    return el("li", {}, el("a", { class: "g-rowcard", href: ctx.href("chat", [chat.chat]) }, ui.avatars(chatFaces(chat), { size: 30 }),
      el("span", { class: "g-grow home-rowtext" }, el("span", { class: "g-title g-clip" }, chatTitle(chat)),
        el("span", { class: "g-muted g-clip" }, [chatMeta(chat), chat.problem].filter(Boolean).join(" · "))),
      played ? el("time", { class: "home-when", datetime: chat.last_activity }, played) : null));
  });
  return el("section", { class: "g-stack" }, heading(ctx, "Recent chats", more(ctx, "chats", "All chats")), el("ul", { class: "home-rows" }, rows));
}

// Needs you: the top open item of the inbox (pages/inbox/feed.js), in the column beside Recent chats, as one
// card that opens the inbox on it; or that nothing does. The feed is read after Home is drawn, so a slow one
// never holds the page up, and one that cannot be read says so here and nowhere else.
function needsYou(ctx) {
  if (!inboxServed(ctx)) return null;
  const { ui } = ctx;
  const { el } = ui;
  const body = el("div", { class: "home-needs", "aria-live": "polite" }, el("div", { class: "g-muted" }, "Checking what is waiting…"));
  loadFeed(ctx).then((feed) => {
    const [top] = feed.items;
    if (!top) {
      const none = ui.card({ kind: "dashed" }, "Nothing needs you.");
      none.classList.add("home-nothing");
      body.replaceChildren(none);
      return;
    }
    const rest = feed.items.length - 1;
    body.replaceChildren(el("a", { class: "g-card g-accent home-top", href: itemHref(ctx, top.id) },
      el("span", { class: "home-top-meta" }, ui.pill(top.label, { kind: "accent" }), el("span", { class: "home-top-where" }, top.where.text)),
      el("span", { class: "g-heading home-top-title" }, top.title),
      el("span", { class: "home-top-foot" }, el("span", { class: "g-muted" }, rest ? "and " + rest + " more" : ""),
        el("span", { class: "home-top-decide" }, "Decide"))));
  }, (error) => {
    if (error && error.reason === "left") return;
    body.replaceChildren(el("div", { class: "g-muted" }, "What is waiting could not be read: " + ui.errorText(error)));
  });
  return el("section", { class: "g-stack" }, heading(ctx, "Needs you", more(ctx, "inbox", "Open the inbox")), body);
}

// Your characters: a portrait tile for each, by its exact ref, ending in the way to import another.
function yourCharacters(ctx, shelf) {
  const { ui } = ctx;
  const { el } = ui;
  const tiles = (shelf.entries || []).map((entry) => ui.tile(entry.name || entry.ref, ctx.href("source", [entry.ref]),
    { key: entry.ref, detail: shelf.titles.get(entry.source) || entry.source }));
  const add = ctx.registered("import")
    ? el("a", { class: "home-import", href: ctx.href("import") }, "Import a character card")
    : el("div", { class: "home-import home-missing" }, "Import a character card (not in this app yet)");
  return el("section", { class: "g-stack" }, heading(ctx, "Your characters", more(ctx, "sources", "Open the library")),
    shelf.error ? ui.errorBox(shelf.error) : null, el("div", { class: "g-tiles" }, tiles, add));
}
