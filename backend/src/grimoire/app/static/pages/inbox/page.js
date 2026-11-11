// Needs you: the one place where anything waiting on the person is decided (#/inbox, ?item=<id> opens on one
// item). The list is `inbox.list`'s, in its order; exactly one open item is expanded, with what it holds shown in
// full and its actions, and the others are rows that expand on a click. Deciding makes the item's own op with
// its own arguments (feed.js), so it is the same write as deciding where the thing lives; what was decided stays
// on the page, struck through, with everything the decision set aside. The page keeps no list of its own: after
// every decision it reads the feed again. Everything from the library is set as text only.

import { chatTitle } from "../chats/model.js";
import { effectDetails, namer, styles as campaignStyles, weighed } from "../campaign/shown.js";
import { chosenReport, conflictVersions, resolvedReport } from "../sync/versions.js";
import { actionButton, countLabel, feedNotes, loadFeed, perform, whereHref } from "./feed.js";

export async function mount(root, ctx) {
  ctx.styles("page.css");
  ctx.setTitle("Needs you");
  ctx.setCrumb("Play");
  ctx.layout("reading");
  const { ui } = ctx;
  const { el } = ui;
  let feed = null;
  let focus = ctx.query.get("item");  // the item to expand: the one asked for, else the first that is open
  const decided = [];                 // what was decided on this visit: { order, title, text, report }
  const said = new Map();             // item id -> what a try that decided nothing answered
  let lastChat;                       // the chat played last, for the way back once nothing waits

  const head = el("div", { class: "inbox-head" });
  const notes = el("div", { class: "g-stack inbox-notes" });
  const list = el("div", { class: "inbox-list" });
  root.append(el("div", { class: "inbox-page" }, head, notes, list));

  const decide = (item, action) => ctx.act(async () => {
    let outcome;
    try {
      outcome = await perform(ctx, item, action);
    } catch (error) {
      // refused (a version that moved on, a key already given): show the list as it stands now, then the reason
      if (error && error.reason !== "left") { try { feed = await loadFeed(ctx); draw(); } catch (again) { /* the refusal is what is shown */ } }
      throw error;
    }
    if (!outcome) return;
    const report = reportOf(item, action, outcome.result);
    if (outcome.decided) {
      decided.push({ order: item.order, title: item.title, text: outcome.text, report });
      said.delete(item.id);
      feed = { ...feed, items: feed.items.filter((other) => other.id !== item.id) };
      focus = null;
    } else {
      said.set(item.id, report || ui.notice(outcome.text, { kind: "bad" }));
    }
    draw();  // what was decided is on the page before the list is read again: a refresh that fails loses nothing
    const waiting = [...list.querySelectorAll("button")];  // drawn just now: off until the list is read again
    for (const node of waiting) node.disabled = true;
    try { feed = await loadFeed(ctx); } finally { for (const node of waiting) node.disabled = false; }
    draw();
  });

  // What a decision set aside, in full: sync's own reports, and a hub that could not be read again.
  function reportOf(item, action, result) {
    if (action.op === "media.resolve") return resolvedReport(ctx, item.conflict.path, result);
    if (action.op === "media.choose") return chosenReport(ctx, result);
    if (action.op.startsWith("campaign.") && result && result.hub === null) {
      return ui.notice("Done, but the campaign could not be read again.", { kind: "bad" });
    }
    return null;
  }

  // What an item holds, in full, read before it is decided.
  function held(item) {
    if (item.kind === "conflict") return conflictVersions(ctx, item.conflict);
    if (item.kind === "action") return weighed(ui, item.action.label.factors, item.action.label.stakes, false).node;
    if (item.kind === "approval") {
      campaignStyles();
      const shown = item.effect;
      return el("div", { class: "inbox-held" }, effectDetails(ui, shown.effect, namer(shown.names), shown.player));
    }
    return null;
  }

  function expanded(item) {
    const there = whereHref(ctx, item);
    const title = el("h2", { class: "inbox-title", tabindex: "-1" }, item.title);
    const card = ui.card({ kind: "accent", tag: "section", label: item.title },
      el("div", { class: "inbox-meta" }, ui.pill(item.label, { kind: "accent" }), el("span", { class: "inbox-where" }, item.where.text)),
      title,
      el("p", { class: "inbox-detail" }, item.detail),
      held(item),
      item.notes.map((text) => el("p", { class: "g-muted inbox-note" }, text)),
      said.get(item.id) || null,
      el("div", { class: "inbox-actions" },
        item.actions.map((action, index) => actionButton(ctx, action, index, () => decide(item, action))),
        there ? el("a", { class: "inbox-goto" + (item.actions.length ? "" : " inbox-only"), href: there }, item.route.label) : null));
    card.classList.add("inbox-open");
    card.setAttribute("data-item", item.id);
    return { card, title };
  }

  function collapsed(item) {
    return el("button", { type: "button", class: "inbox-row", "data-item": item.id, "data-focus-key": "inbox:row:" + item.id,
      "aria-expanded": "false", onclick: () => { focus = item.id; draw({ moveFocus: true }); } },
      ui.pill(item.label), el("span", { class: "inbox-row-text" }, el("span", { class: "inbox-row-title g-clip" }, item.title),
        el("span", { class: "inbox-where g-clip" }, item.where.text)),
      el("span", { class: "inbox-decide" }, "Decide"));
  }

  function done(entry) {
    return el("div", { class: "inbox-settled" },
      el("div", { class: "inbox-done" }, el("span", { class: "g-dot g-good", "aria-hidden": "true" }),
        el("span", { class: "inbox-struck g-clip" }, entry.title), el("span", { class: "inbox-result" }, entry.text)),
      entry.report);
  }

  function empty() {
    const card = ui.card({ kind: "dashed", tag: "section", label: "Nothing needs you" }, el("div", { class: "inbox-empty-title" }, "Nothing needs you"));
    card.classList.add("inbox-empty");
    if (lastChat) card.append(ui.buttonLink("Back to " + chatTitle(lastChat), "chat", [lastChat.chat], undefined, { kind: "primary" }));
    else if (lastChat === null && ctx.registered("start")) card.append(ui.buttonLink("New chat", "start", [], undefined, { kind: "primary" }));
    return card;
  }

  function draw({ moveFocus = false } = {}) {
    const open = feed.items;
    const at = open.find((item) => item.id === focus) || open[0] || null;
    head.replaceChildren(...[el("span", { class: "inbox-count", role: "status" }, open.length ? countLabel(open.length) : "All clear"),
      open.length ? el("span", { class: "inbox-sub" }, "Work down the list. Each one is decided right here.") : null].filter(Boolean));
    notes.replaceChildren(...feedNotes(feed).map((note) => ui.notice(note.text, { kind: note.bad ? "bad" : "" })));
    // decided items stay where they were in the list, each before whatever now holds its place
    const lines = [...decided.map((entry) => ({ order: entry.order, rank: 0, entry })),
      ...open.map((item) => ({ order: item.order, rank: 1, item }))].sort((a, b) => a.order - b.order || a.rank - b.rank);
    let heading = null;
    const nodes = lines.map((line) => {
      if (line.entry) return done(line.entry);
      if (line.item !== at) return collapsed(line.item);
      const shown = expanded(line.item);
      heading = shown.title;
      return shown.card;
    });
    if (!open.length) nodes.push(empty());
    list.replaceChildren(...nodes);
    if (moveFocus && heading) heading.focus();
  }

  const recent = async () => {
    if (!ctx.has("chats") || !ctx.registered("chat")) return undefined;
    try { return (await ctx.api("chats", { filter: "recent", limit: 1 })).chats[0] || null; }
    catch (error) { if (error && error.reason === "left") throw error; return undefined; }
  };
  [feed, lastChat] = await Promise.all([loadFeed(ctx), recent()]);
  draw();
}
