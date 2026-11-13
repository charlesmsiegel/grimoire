// Chats (Sprint 111): every chat of the library, or the chats of one character or scenario, searched, sorted
// and grouped, and the way to a new chat. Which chats are listed lives in the address (#/chats,
// #/chats?filter=character&source=<SourceRef>), so a deep link and Back both land on the same list; how they are
// searched, grouped and sorted is kept for the tab. The list's logic is model.js. Every value shown comes from
// the library and is set as text.
//
// Where the app keeps pins (docs/SHELF.md), each row has a Pin toggle, the pinned chats are lifted into a
// "Pinned" group at the top, and "Pinned" (#/chats?filter=pinned) lists them alone. A pin is the person's note
// beside the chat: it changes nothing of the chat itself.

import { FILTERS, GROUPS, PINNED, PINNED_FILTER, SORTS, chatFaces, chatMeta, chatTitle, groupChats, oneOf, pinnedOnly,
  plural, shownRows, viewOf, when } from "./model.js";
import { chatMarks, pinnedChats, problemText, readShelf } from "../sources/shelf.js";

import { makePager, savedFilters, writeFilters } from "./workspace.js";

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  ctx.setTitle("Chats");
  ctx.setCrumb("Play");
  if (ctx.has("sync")) ctx.setActions(syncButton(ctx));

  const pins = ctx.has("shelf.read");
  const parsed = viewOf(ctx.query, { pins });
  const filter = ["campaign", "world"].includes(ctx.query.get("filter")) ? ctx.query.get("filter") : parsed.filter;
  const source = parsed.source;
  const address = (changes) => {
    const query = { filter, source, match: ctx.query.get("match") || undefined, campaign: ctx.query.get("campaign") || undefined, world: ctx.query.get("world") || undefined, ...changes };
    if (query.filter === "all") { delete query.filter; delete query.source; }
    if (!query.source) delete query.source;
    return ctx.href("chats", [], query);
  };
  const kept = { q: ctx.draft("chats:q"), group: ctx.draft("chats:group"), sort: ctx.draft("chats:sort") };
  const state = { q: kept.q.get(), group: oneOf(GROUPS, kept.group.get()), sort: oneOf(SORTS, kept.sort.get()),
    collapsed: new Set(), expanded: new Set() };
  let page = null, offset = 0, sourceOffset = 0, comparisonOffset = 0, timer;
  const request = makePager(ctx.api);
  let chats = null;  // not read yet
  let shelf = null;  // the pins, where the app keeps them

  const search = ui.search("Search chats", { value: state.q, placeholder: "Search chats by title, campaign or character",
    oninput: (text) => { state.q = text; kept.q.set(text); clearTimeout(timer); timer = setTimeout(() => load(), 200); } });
  search.classList.add("chats-search");
  search.setAttribute("data-focus-key", "chats:q");
  const top = el("div", { class: "g-row" }, search,
    ctx.registered("start") && ctx.has("play.start") ? ui.buttonLink("New chat", "start", [], undefined, { kind: "primary" }) : null);

  const options = (pairs) => pairs.map(([value, label]) => ({ value, label }));
  const which = ui.segments("Which chats", options([...(pins ? [...FILTERS, PINNED_FILTER] : FILTERS), ["campaign", "Campaign"], ["world", "World"]]), { value: filter,
    onchange: (value) => ctx.navigate(address({ filter: value, source: undefined })) });
  const grouping = ui.segments("Group by", options(GROUPS), { value: state.group,
    onchange: (value) => { state.group = value; kept.group.set(value); render(); } });
  const sorting = ui.segments("Sort", options(SORTS), { value: state.sort,
    onchange: (value) => { state.sort = value; kept.sort.set(value); load(); } });
  const picker = el("div", { class: "chats-picker" });  // the character or scenario whose chats these are
  const controls = el("div", { class: "g-row" }, which.node, el("span", { class: "g-grow" }),
    el("span", { class: "chats-label", "aria-hidden": "true" }, "Group by"), grouping.node,
    el("span", { class: "chats-label", "aria-hidden": "true" }, "Sort"), sorting.node);
  const status = el("div", { class: "chats-status", role: "status" });
  const unread = el("div", { class: "chats-unread" });  // pins that could not be read are said, not hidden
  const results = el("div", { class: "chats-results" });
  root.append(el("div", { class: "chats-page" }, top, controls, picker, unread, status, results));

  const pager = el("div", { class: "g-row", "aria-label": "Chat pages" });
  const comparison = el("div", { class: "chats-comparisons" });
  const saved = el("div", { class: "g-row" });
  const storageStatus = el("div", { role: "status" });
  results.parentNode.append(pager, comparison, saved, storageStatus);
  let storage;
  try { storage = window.localStorage; } catch (_) { storage = { getItem() { throw Error("Storage disabled"); }, setItem() { throw Error("Storage disabled"); } }; }
  let filters = savedFilters(storage);
  const selection = () => ({ q: state.q, group: state.group, sort: state.sort, filter, source,
    match: ctx.query.get("match") || "exact", campaign: ctx.query.get("campaign"), world: ctx.query.get("world") });
  function savedControls() {
    storageStatus.textContent = filters.error || "Saved filters are device-local and not synchronized.";
    const name = el("input", { type: "text", "aria-label": "Saved filter name", maxlength: "100" });
    saved.replaceChildren(name, ui.button("Save filter", () => {
      if (!name.value.trim()) { storageStatus.textContent = "Enter a filter name."; return; }
      const values = [...filters.values.filter(x => x.name !== name.value.trim()), { name: name.value.trim(), selection: selection() }];
      const error = writeFilters(storage, values);
      if (error) { storageStatus.textContent = error; return; }
      filters = { values, error: null }; savedControls();
    }));
    for (const item of filters.values) {
      saved.append(ui.button("Apply " + item.name, () => {
        const value = item.selection;
        kept.q.set(value.q || ""); kept.group.set(oneOf(GROUPS, value.group)); kept.sort.set(oneOf(SORTS, value.sort));
        ctx.navigate(ctx.href("chats", [], { filter: value.filter, source: value.source, match: value.match,
          campaign: value.campaign, world: value.world }));
      }), ui.button("Delete " + item.name, () => {
        const values = filters.values.filter(x => x !== item);
        const error = writeFilters(storage, values);
        if (error) { storageStatus.textContent = error; return; }
        filters = { values, error: null }; savedControls();
      }));
    }
  }
  savedControls();
  shelf = await readShelf(ctx);
  await load();

  async function load(at = 0, sourcesAt = 0, comparisonsAt = 0, focus = false) {
    const focusKey = document.activeElement?.getAttribute("data-focus-key");
    const args = { filter, source: source || undefined, q: state.q, sort: state.sort,
      match: ctx.query.get("match") || "exact", campaign: ctx.query.get("campaign") || undefined,
      world: ctx.query.get("world") || undefined, pins: [...pinnedChats(shelf)],
      offset: at, source_offset: sourcesAt, comparison_offset: comparisonsAt,
      source_q: ctx.query.get("source_q") || "",
      snapshot: (at || sourcesAt || comparisonsAt) && page ? page.snapshot : undefined };
    const response = await request(args);
    if (!response) return;
    if (response.error) {
      status.textContent = response.error.reason === "changed" ? "Navigation changed. Restart to see the current library." : String(response.error.message || response.error);
      if (response.error.reason === "changed") {
        pager.replaceChildren(ui.button("Restart browsing", () => load(0, 0, 0, true)));
        results.replaceChildren(); comparison.replaceChildren();
      }
      return;
    }
    page = response.result; offset = at; sourceOffset = sourcesAt; comparisonOffset = comparisonsAt;
    chats = page.chats;
    if (filter === "all" && !state.q.trim()) ctx.setCount("chats", { value: page.total, label: plural(page.total, "chat", "chats") });
    picker.replaceChildren();
    if (filter === "character" || filter === "scenario") {
      const choices = [{ value: "", label: "Choose a " + filter }, ...page.sources.map(item => ({value:item.ref,label:(item.name || "Missing source") + " · " + item.ref}))];
      if (source && !choices.some(x => x.value === source)) { if (choices.length > 50) choices.pop(); choices.push({value:source,label:source}); }
      picker.append(ui.choice(filter, choices, { value: source || "", onchange: value => ctx.navigate(address({source:value || undefined})) }).node,
        ui.choice("Source match", [{value:"exact",label:"Exact pinned source"},{value:"derived",label:"Include validated descendant portrayals"}], {
          value: ctx.query.get("match") || "exact", onchange: value => ctx.navigate(address({match:value})) }).node);
      const sourceSearch = ui.search("Search source references", {value:ctx.query.get("source_q") || "", oninput:text => {
        clearTimeout(timer); timer = setTimeout(() => { ctx.query.set("source_q", text); load(); }, 200);
      }});
      sourceSearch.setAttribute("data-focus-key", "chats:source-search");
      picker.append(sourceSearch);
      if (sourceOffset) picker.append(ui.button("Previous sources", () => load(offset, Math.max(0, sourceOffset - 50), comparisonOffset)));
      if (page.source_next !== null) picker.append(ui.button("Next sources", () => load(offset, page.source_next, comparisonOffset)));
    }
    if (filter === "campaign" || filter === "world") {
      picker.append(ui.search(filter === "campaign" ? "Exact campaign ID" : "Exact world source ID", {
        value:ctx.query.get(filter) || "", oninput:value => { clearTimeout(timer); timer = setTimeout(() => ctx.navigate(address({[filter]:value})), 300); }
      }));
    }
    pager.replaceChildren();
    if (offset) pager.append(ui.button("Previous chats", () => load(Math.max(0, offset - 50), 0, 0, true)));
    if (page.next !== null) pager.append(ui.button("Next chats", () => load(page.next, 0, 0, true)));
    comparison.replaceChildren();
    if (source) {
      comparison.append(el("h2", {}, "Matched source revisions and validated ancestry"));
      for (const item of page.comparisons) comparison.append(el("p", {}, item.ref + " · " + (item.path.join(" → ") || "Unavailable: " + item.problem)));
      if (comparisonOffset) comparison.append(ui.button("Previous comparisons", () => load(offset, sourceOffset, Math.max(0, comparisonOffset - 50))));
      if (page.comparison_next !== null) comparison.append(ui.button("Next comparisons", () => load(offset, sourceOffset, page.comparison_next)));
    }
    for (const problem of page.diagnostics) comparison.append(el("p", {}, problem.ref + ": " + problem.code));
    render();
    if (focus) (results.querySelector("a") || pager.querySelector("button"))?.focus();
    else if (focusKey === "chats:source-search") picker.querySelector("[data-focus-key=\"chats:source-search\"]")?.focus();
  }

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
    if (filter === PINNED) await load(); else render();
    const again = Array.from(results.querySelectorAll("button.chats-pin")).find((node) => node.getAttribute("data-focus-key") === "chats:pin:" + chat.chat);
    if (again) again.focus();
  }

  function render() {
    if (chats === null) return;  // typed in before the list arrived: it is searched when it does
    const pinned = pinnedChats(shelf);
    const listed = filter === PINNED ? pinnedOnly(chats, pinned) : chats;
    const found = listed;
    const groups = groupChats(found, state.group, shelf ? pinned : null);
    const now = Date.now();
    const said = problemText(shelf);
    unread.replaceChildren(...(said ? [ui.notice(said, { kind: "bad" })] : []));
    status.textContent = page ? page.total + " chats in this result · showing " + (found.length ? offset + 1 : 0) + "–" + (offset + found.length) : "";
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
