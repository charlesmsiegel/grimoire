// Chats (Sprint 111): every chat of the library, or the chats of one character or scenario, searched, sorted
// and grouped, and the way to a new chat. Which chats are listed lives in the address (#/chats,
// #/chats?filter=character&source=<SourceRef>), so a deep link and Back both land on the same list; how they are
// searched, grouped and sorted is kept for the tab. The list's logic is model.js. Every value shown comes from
// the library and is set as text.
//
// Where the app keeps pins (docs/SHELF.md), each row has a Pin toggle, the pinned chats are lifted into a
// "Pinned" group at the top, and "Pinned" (#/chats?filter=pinned) lists them alone. A pin is the person's note
// beside the chat: it changes nothing of the chat itself.

import { FILTERS, GROUPS, PINNED, PINNED_FILTER, SORTS, chatFaces, chatMeta, chatTitle, countLabel, groupChats, oneOf, pinnedOnly,
  plural, searchChats, shownRows, sortChats, viewOf, when } from "./model.js";
import { chatMarks, pinnedChats, problemText, readShelf } from "../sources/shelf.js";

const MAX_LIST = 1000;  // the most one list request returns

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  ctx.setTitle("Chats");
  ctx.setCrumb("Play");
  if (ctx.has("sync")) ctx.setActions(syncButton(ctx));

  const pins = ctx.has("shelf.read");
  const { filter, source } = viewOf(ctx.query, { pins });
  const address = (changes) => {
    const query = { filter, source, ...changes };
    if (query.filter === "all") { delete query.filter; delete query.source; }
    if (!query.source) delete query.source;
    return ctx.href("chats", [], query);
  };
  const kept = { q: ctx.draft("chats:q"), group: ctx.draft("chats:group"), sort: ctx.draft("chats:sort") };
  const state = { q: kept.q.get(), group: oneOf(GROUPS, kept.group.get()), sort: oneOf(SORTS, kept.sort.get()),
    collapsed: new Set(), expanded: new Set() };
  let chats = null;  // not read yet
  let shelf = null;  // the pins, where the app keeps them

  const search = ui.search("Search chats", { value: state.q, placeholder: "Search chats by title, campaign or character",
    oninput: (text) => { state.q = text; kept.q.set(text); render(); } });
  search.classList.add("chats-search");
  search.setAttribute("data-focus-key", "chats:q");
  const top = el("div", { class: "g-row" }, search,
    ctx.registered("start") && ctx.has("play.start") ? ui.buttonLink("New chat", "start", [], undefined, { kind: "primary" }) : null);

  const options = (pairs) => pairs.map(([value, label]) => ({ value, label }));
  const which = ui.segments("Which chats", options(pins ? [...FILTERS, PINNED_FILTER] : FILTERS), { value: filter,
    onchange: (value) => ctx.navigate(address({ filter: value, source: undefined })) });
  const grouping = ui.segments("Group by", options(GROUPS), { value: state.group,
    onchange: (value) => { state.group = value; kept.group.set(value); render(); } });
  const sorting = ui.segments("Sort", options(SORTS), { value: state.sort,
    onchange: (value) => { state.sort = value; kept.sort.set(value); render(); } });
  const picker = el("div", { class: "chats-picker" });  // the character or scenario whose chats these are
  const controls = el("div", { class: "g-row" }, which.node, el("span", { class: "g-grow" }),
    el("span", { class: "chats-label", "aria-hidden": "true" }, "Group by"), grouping.node,
    el("span", { class: "chats-label", "aria-hidden": "true" }, "Sort"), sorting.node);
  const status = el("div", { class: "chats-status", role: "status" });
  const unread = el("div", { class: "chats-unread" });  // pins that could not be read are said, not hidden
  const results = el("div", { class: "chats-results" });
  root.append(el("div", { class: "chats-page" }, top, controls, picker, unread, status, results));

  // Every chat is read once (newest activity first, as far as one list request goes): it is the list itself,
  // the rail's count, and the characters and scenarios the picker offers.
  const [every, marks] = await Promise.all([ctx.api("chats", { filter: "recent", limit: MAX_LIST }), readShelf(ctx)]);
  shelf = marks;
  const capped = every.chats.length >= MAX_LIST;  // there may be more than one request lists: the count is not known
  if (!capped) ctx.setCount("chats", { value: every.chats.length, label: plural(every.chats.length, "chat", "chats") });
  if (filter === "all" || filter === PINNED) {
    chats = every.chats;
  } else {
    const choices = [{ value: "", label: "Choose a " + filter }];
    for (const item of every.sources.filter((entry) => entry.role === filter)) choices.push({ value: item.ref, label: item.name || item.ref });
    // A linked source the list does not name (not bound by any chat, or not in this library) is still the one
    // shown: the picker says its exact ref rather than silently showing another.
    if (source && !choices.some((option) => option.value === source)) choices.push({ value: source, label: source });
    picker.append(ui.choice(filter === "character" ? "Character" : "Scenario", choices, {
      value: source || "", onchange: (value) => ctx.navigate(address({ source: value || undefined })),
    }).node);
    chats = source ? (await ctx.api("chats", { filter, source, limit: MAX_LIST })).chats : [];
  }
  render();

  // Pin or unpin one chat at the etag this page read, then read the pins again and draw the list again, with
  // focus back on that chat's toggle wherever its row now stands.
  async function pin(chat) {
    const held = chatMarks(shelf, chat.chat);
    try {
      await ctx.api("shelf.pin_chat", { chat: chat.chat, pinned: !held.pinned, expected: held.etag });
    } catch (error) {
      if (error.reason !== "changed") throw error;
      ctx.notify("This chat's pin changed since this page read it, so nothing was written. It is shown as it is now.", { bad: true });
    }
    const fresh = await readShelf(ctx);
    if (fresh) shelf = fresh;
    render();
    const again = results.querySelectorAll("button.chats-pin").find((node) => node.getAttribute("data-focus-key") === "chats:pin:" + chat.chat);
    if (again) again.focus();
  }

  function render() {
    if (chats === null) return;  // typed in before the list arrived: it is searched when it does
    const pinned = pinnedChats(shelf);
    const listed = filter === PINNED ? pinnedOnly(chats, pinned) : chats;
    const found = sortChats(searchChats(listed, state.q), state.sort);
    const groups = groupChats(found, state.group, shelf ? pinned : null);
    const now = Date.now();
    const said = problemText(shelf);
    unread.replaceChildren(...(said ? [ui.notice(said, { kind: "bad" })] : []));
    status.textContent = !listed.length ? ""
      : countLabel(found.length, groups.length, state.group) + (!capped ? "" : filter === "all" ? ", the " + MAX_LIST + " played last"
        : filter === PINNED ? ", among the " + MAX_LIST + " played last" : "");
    const none = listed.length ? "No chats match. Start one, or clear the search."
      : filter === PINNED ? "No chat is pinned yet. Pin one from its row under All to keep it here."
        : filter === "all" ? "No chats yet. Start one, or sync with your other device."
          : source ? "No chats here." : "Choose a " + filter + " to see its chats.";
    const row = { shelf, pin: shelf && !shelf.failed && ctx.has("shelf.pin_chat") ? pin : null };
    results.replaceChildren(...(found.length ? groups.map((group) => groupCard(ctx, group, state, now, row)) : [ui.empty(none)]));
  }
}

// One group as a card: a head strip that shows or hides it, its first rows, and "Show N more" for the rest.
// Showing, hiding and showing more change the card in place, so focus stays where the person is.
function groupCard(ctx, group, state, now, row) {
  const { ui } = ctx;
  const { el } = ui;
  const list = el("ul", { class: "chats-rows" });
  const body = el("div", { class: "chats-body" }, list);
  const toggle = el("span", { class: "chats-toggle" });
  const head = el("button", { type: "button", class: "chats-head", "data-focus-key": "chats:group:" + state.group + ":" + group.key,
    onclick: () => { if (!state.collapsed.delete(group.key)) state.collapsed.add(group.key); show(); } },
  el("span", { class: "g-title" }, group.label),
  el("span", { class: "chats-count" }, plural(group.chats.length, "chat", "chats")), toggle);
  function show() {
    const open = !state.collapsed.has(group.key);
    body.hidden = !open;
    head.setAttribute("aria-expanded", String(open));
    toggle.textContent = open ? "Hide" : "Show";
  }
  const { rows, more } = shownRows(group.chats, state.group, state.expanded.has(group.key));
  list.append(...rows.map((chat) => chatItem(ctx, chat, now, row)));
  if (more) {
    const rest = el("button", { type: "button", class: "chats-more", onclick: () => {
      state.expanded.add(group.key);
      const added = group.chats.slice(rows.length).map((chat) => chatItem(ctx, chat, now, row));
      list.append(...added);
      rest.remove();
      const first = added[0].querySelector("a");
      if (first) first.focus();
    } }, "Show " + more + " more");
    body.append(rest);
  }
  show();
  return ui.card({ kind: "flush", tag: "section", label: group.label }, el("h2", { class: "chats-heading" }, head), body);
}

// One chat as a row of a group: who is in it, its title and any problem, where it is, and when it was last
// played. The row is one link, by the chat's exact ref, with the chat's Pin toggle beside it where the app
// keeps pins. (The chat's last line belongs between the title and the meta line once the list carries one.)
function chatItem(ctx, chat, now, row = {}) {
  const { ui } = ctx;
  const { el } = ui;
  const played = when(chat.last_activity, now);
  const link = el("a", { class: "chats-row", href: ctx.href("chat", [chat.chat]) },
    el("span", { class: "chats-faces" }, ui.avatars(chatFaces(chat), { size: 30 })),
    el("span", { class: "chats-text" },
      el("span", { class: "chats-titleline" }, el("span", { class: "g-title g-clip" }, chatTitle(chat)),
        chat.problem ? ui.pill(chat.problem, { kind: "bad" }) : null),
      el("span", { class: "chats-meta g-clip" }, chatMeta(chat))),
    played ? el("time", { class: "chats-when", datetime: chat.last_activity }, played) : null);
  let toggle = null;
  if (row.pin) {
    const pinned = chatMarks(row.shelf, chat.chat).pinned;
    toggle = ui.button(pinned ? "Pinned" : "Pin", () => ctx.act(() => row.pin(chat)), { size: "small" });
    toggle.classList.add("chats-pin");
    toggle.setAttribute("aria-pressed", String(pinned));
    toggle.setAttribute("aria-label", "Pin " + chatTitle(chat));
    toggle.setAttribute("data-focus-key", "chats:pin:" + chat.chat);
  }
  return el("li", { class: "chats-item" }, link, toggle);
}

// One chat as a row of a plain list (ui.list): its label (or what it was started from), where it is, who is in
// it and any problem. Start Chat lists a selection's chats with it.
export function chatRow(ctx, chat) {
  const { el, rowText } = ctx.ui;
  return el("a", { href: ctx.href("chat", [chat.chat]) }, rowText(chatTitle(chat), [chatMeta(chat), chat.problem].filter(Boolean).join(" · ")));
}

function syncButton(ctx) {
  return ctx.ui.button("Sync", () => ctx.act(async () => {
    const peers = (ctx.library && ctx.library.peers) || [];
    if (!peers.length) { ctx.notify("No paired device is configured for this app.", { bad: true }); return; }
    for (const peer of peers) {
      const report = await ctx.api("sync", { peer: peer.peer });
      ctx.notify(peer.peer + ": " + report.status + (report.reason ? " (" + report.reason + ")" : "")
        + (report.conflicts.length ? ", " + report.conflicts.length + " in conflict" : ""), { bad: report.status !== "synced" });
    }
    await ctx.refreshLibrary();
    ctx.navigate(window.location.hash);
  }));
}
