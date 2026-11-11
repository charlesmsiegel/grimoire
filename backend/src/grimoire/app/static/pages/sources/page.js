// Characters and lore (109): every entry of the library, as portrait and text tiles or as a table. Each entry is
// listed by its exact, source-qualified ref: two characters of one name in two sources are two tiles, never one.
// What is in view lives in the address, so a deep link and Back land on it:
//
//   #/sources?role=character|scenario|lore|world   which kind of entry ("persona" is the characters)
//            &q=<text>                             a search over names and refs
//            &source=<source> | &shelf=cards       one shelf: one source, or every imported card
//            &sort=recent|name|played              the order (by play only when the chats list is whole)
//            &view=list                            a table instead of the grid
//            &limit=<n>                            how many are shown (48, then "Show more")
//
// Which entries a role and a shelf leave, their counts and their order are ./catalog.js.

import { CARDS, PAGE, ROLES, askedRole, askedSort, canChat, count, countRoles, filterEntries, kindLabel, metaLine, onShelf, playsOf,
  shelvesOf, sortEntries, sortsFor, when } from "./catalog.js";

// What a new entry can be: its kind, and the structured field its first revision needs besides a name.
const NEW_KINDS = [["character", "Character"], ["location", "Location"], ["item", "Item"], ["scenario", "Scenario"],
  ["lorebook", "Lorebook"]];
const MAX_LIST = 1000;       // the most entries one catalog request returns
const MAX_CHATS = 1000;      // the most chats one request lists: play is counted only when the list is whole
const NAMESPACES = { card: "Imported card", world: "World" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("wide");
  ctx.setTitle("Characters and lore");
  ctx.setCrumb("Library");
  const { role, persona } = askedRole(ctx.query.get("role"));
  const query = ctx.query.get("q") || "";
  const only = ctx.query.get("source") || "";
  const shelf = only || (ctx.query.get("shelf") === CARDS ? CARDS : "");
  const list = ctx.query.get("view") === "list";
  const asked = Number.parseInt(ctx.query.get("limit") || "", 10);
  const limit = Number.isInteger(asked) && asked >= 1 ? Math.min(asked, MAX_LIST) : PAGE;
  const address = (changes) => {
    const next = { role: persona ? "persona" : role, q: query, source: only, shelf: shelf === CARDS ? CARDS : "",
      sort: ctx.query.get("sort") || "", view: list ? "list" : "", limit: limit === PAGE ? "" : limit, ...changes };
    for (const key of Object.keys(next)) if (next[key] === "" || next[key] === undefined || next[key] === null) delete next[key];
    return ctx.href("sources", [], next);
  };

  const args = { limit: MAX_LIST };
  if (query) args.query = query;
  const [catalog, chats] = await Promise.all([ctx.api("sources.catalog", args), ctx.has("chats")
    ? ctx.api("chats", { limit: MAX_CHATS }).catch((error) => { if (error.reason === "left") throw error; return null; }) : null]);
  const sources = catalog.sources;
  const titles = new Map(sources.map((source) => [source.source, source.title || source.source]));
  const plays = playsOf(chats && chats.chats, MAX_CHATS);
  const sort = askedSort(ctx.query.get("sort"), plays);
  const chosen = only ? sources.find((source) => source.source === only) : null;
  const total = sources.reduce((sum, source) => sum + source.entities, 0);
  const onThisShelf = catalog.entries.filter((entry) => onShelf(entry, shelf));
  const counts = countRoles(onThisShelf);
  const found = sortEntries(filterEntries(catalog.entries, { role, shelf }), sort, plays);
  const shown = found.slice(0, limit);
  const view = { ctx, address, titles, plays, portrait: portraits(ctx), chat: ctx.registered("start") };

  // row 1: search, Import, New entry
  const search = ui.search("Search names and refs", { value: query, placeholder: "Search " + count(total, "entry", "entries") + " by name or ref" });
  search.setAttribute("name", "q");
  search.setAttribute("data-focus-key", "sources-search");
  const form = el("form", { class: "sources-search", role: "search" }, search);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    ctx.navigate(address({ q: search.value.trim(), limit: "" }));
  });
  const top = el("div", { class: "g-row sources-top" }, form,
    ctx.registered("import") ? ui.buttonLink("Import", "import") : null,
    ctx.has("sources.create") ? focusKey(ui.button("New entry", () => newEntry(ctx, sources, chosen), { kind: "primary" }), "sources-new") : null);

  // row 2: the roles with their counts, the order and the view
  const roles = ui.segments("Which entries", ROLES.map(([value, label]) => ({ value, label: label + " " + counts[value] })),
    { value: role, onchange: (value) => ctx.navigate(address({ role: value, limit: "" })) });
  const sorts = sortsFor(plays);
  const order = sorts.length > 1 ? ui.segments("Sort", sorts, { value: sort, onchange: (value) => ctx.navigate(address({ sort: value })) }) : null;
  const views = ui.segments("View", [{ value: "grid", label: "Grid" }, { value: "list", label: "List" }],
    { value: list ? "list" : "grid", onchange: (value) => ctx.navigate(address({ view: value === "list" ? "list" : "" })) });
  const controls = el("div", { class: "g-row sources-controls" }, roles.node, el("div", { class: "g-row sources-order" },
    order ? el("span", { class: "g-muted", "aria-hidden": "true" }, "Sort") : null, order ? order.node : null, views.node));

  const notes = [];
  if (persona) notes.push(el("p", { class: "g-muted sources-note" }, "Any character can be your persona. You choose it from the chat you play."));
  for (const reason of catalog.excluded || []) notes.push(ui.notice(describeExcluded(reason), { kind: "bad" }));
  if (catalog.truncated) {
    notes.push(ui.notice("Only the first " + catalog.entries.length + " of " + catalog.total + " entries are listed and counted here. Search to narrow them."));
  }
  if (only) notes.push(chosen ? sourceHead(ctx, chosen, sources, address) : el("div", {}, ui.notice("No source " + only + " is in this library.", { kind: "bad" }),
    ui.actions(el("a", { class: "g-button", href: address({ source: "", limit: "" }) }, "Show every source"))));

  // the shelves beside the results
  const shelves = el("nav", { class: "sources-shelves", "aria-label": "Shelves" }, ui.eyebrow("Shelves"),
    shelvesOf(sources, catalog.entries).map((item) => {
      const link = el("a", { class: "sources-shelf", title: item.id && item.id !== CARDS ? item.id : undefined,
        href: address({ source: item.id === CARDS ? "" : item.id, shelf: item.id === CARDS ? CARDS : "", limit: "" }) },
      el("span", { class: "g-clip" }, item.label), el("span", { class: "g-muted" }, String(item.count)));
      if (item.id === shelf) link.setAttribute("aria-current", "page");
      return link;
    }),
    ctx.has("sources.create_source") ? focusKey(ui.button("New source", () => newSource(ctx, address), { kind: "dashed", size: "small" }), "sources-new-source") : null);

  const results = el("div", { class: "sources-results g-stack" });
  results.append(el("div", { class: "g-muted sources-count", role: "status" }, summary(found.length, shown.length, query)));
  if (!found.length) results.append(el("p", { class: "g-muted" }, query ? "Nothing matches " + JSON.stringify(query) + "." : "Nothing here yet."));
  else results.append(list ? table(view, shown) : el("div", { class: "g-tiles g-dense sources-grid" }, shown.map((entry) => tile(view, entry))));
  if (found.length > shown.length) {
    results.append(el("a", { class: "g-button sources-more", href: address({ limit: Math.min(limit + PAGE, MAX_LIST) }) }, "Show more"));
  }
  root.classList.add("sources-page");
  root.append(top, controls, ...notes, el("div", { class: "sources-body" }, shelves, results));
}

function focusKey(node, key) { node.setAttribute("data-focus-key", key); return node; }

function summary(found, shown, query) {
  if (!found) return "No entries";
  const what = query ? count(found, "entry matches", "entries match") + " " + JSON.stringify(query) : count(found, "entry", "entries");
  return shown < found ? "Showing " + shown + " of " + what : what;
}

function describeExcluded(item) {
  if (typeof item === "string") return "Not shown: " + item;
  const what = item.source || item.ref || "a source";
  return "Not shown: " + what + (item.reason ? " (" + item.reason + ")" : "");
}

// A character's picture, read after the page shows: its portrait slot, or its avatar. What each character shows
// is remembered for a few moments, so changing the role or the order does not ask for every tile again.
const ART_KEPT = 20000;
const artOf = new Map();  // ref -> { at, image: a promise of an ImageRef or null }

function portraits(ctx) {
  if (!ctx.has("media.art")) return () => undefined;
  const waiting = [];
  let running = 0;
  const pump = () => {
    while (running < 3 && waiting.length) {
      running += 1;
      waiting.shift()().finally(() => { running -= 1; pump(); });
    }
  };
  const imageOf = (ref) => {
    const kept = artOf.get(ref);
    if (kept && Date.now() - kept.at < ART_KEPT) return kept.image;
    const image = new Promise((resolve) => {
      waiting.push(async () => {
        try {
          const art = await ctx.api("media.art", { subject: ref });
          const slot = ["portrait", "avatar"].map((name) => art.slots.find((item) => item.slot === name && item.state === "shown" && item.image))
            .find(Boolean);
          resolve(slot ? slot.image : null);
        } catch (error) {
          if (error && error.reason === "left") artOf.delete(ref);  // not an answer: ask again next time
          resolve(null);
        }
      });
      pump();
    });
    artOf.set(ref, { at: Date.now(), image });
    return image;
  };
  return (ref) => imageOf(ref).then((image) => (image ? ctx.picture(image) : null)).catch(() => null);
}

// What an entry is called where its name shows, and the lines under it.
function facts(view, entry) {
  const { el } = view.ctx.ui;
  const lines = [];
  if (entry.problem) lines.push(el("span", { class: "sources-problem" }, entry.problem));
  if (entry.namesakes && entry.namesakes.length) {
    lines.push(el("span", { class: "g-muted sources-namesakes", title: "Same name as " + entry.namesakes.join(", ")
      + ". A different entry: names never decide which one." }, "Same name as " + count(entry.namesakes.length, "other entry", "other entries")));
  }
  return lines;
}

function chatLink(view, entry) {
  if (!view.chat || !canChat(entry)) return null;
  const link = view.ctx.ui.buttonLink("Chat", "start", [], { source: entry.ref }, { size: "small" });
  link.setAttribute("aria-label", "Chat with " + (entry.name || entry.ref));
  link.classList.add("sources-chat");
  return link;
}

// One entry in the grid: a character is its portrait, anything else a text tile naming its kind.
function tile(view, entry) {
  const { ctx, titles, plays } = view;
  const { ui } = ctx;
  const { el } = ui;
  const name = entry.name || entry.ref;
  const target = ctx.href("source", [entry.ref]);
  const detail = metaLine(entry, titles.get(entry.source), plays);
  let link;
  if (entry.kind === "character") {
    link = ui.tile(name, target, { key: entry.ref, picture: entry.problem ? undefined : view.portrait(entry.ref), detail });
  } else {
    link = el("a", { class: "g-tile", href: target },
      el("span", { class: "g-tile-text sources-text-tile" }, el("span", { class: "g-eyebrow" }, kindLabel(entry.kind)),
        el("span", { class: "sources-text-title", "aria-hidden": "true" }, name)),
      el("span", { class: "g-tile-name" }, name), el("span", { class: "g-muted" }, detail));
  }
  link.title = entry.ref;
  if (!entry.name) link.classList.add("sources-unnamed");
  return el("div", { class: "sources-tile" }, link, ...facts(view, entry), chatLink(view, entry));
}

// The same entries as a table.
function table(view, entries) {
  const { ctx, titles, plays, address } = view;
  const { ui } = ctx;
  const { el } = ui;
  const head = ["", "Name", "Kind", "Source", "Version", ...(plays ? ["Chats", "Last played"] : []), ""];
  const rows = entries.map((entry) => {
    const name = entry.name || entry.ref;
    const play = plays ? plays.get(entry.ref) : null;
    const link = el("a", { class: "sources-name" + (entry.name ? "" : " g-ref"), href: ctx.href("source", [entry.ref]), title: entry.ref }, name);
    return el("tr", {},
      el("td", {}, ui.avatar(name, { size: 30, key: entry.ref, picture: entry.kind === "character" && !entry.problem ? view.portrait(entry.ref) : undefined })),
      el("td", {}, el("div", { class: "g-stack sources-cell" }, link, entry.derived ? el("span", { class: "g-muted" }, "a portrayal") : null, ...facts(view, entry))),
      el("td", { class: "g-muted sources-nowrap" }, kindLabel(entry.kind)),
      el("td", {}, el("a", { class: "g-link sources-source", href: address({ source: entry.source, shelf: "", limit: "" }), title: entry.source },
        titles.get(entry.source) || entry.source)),
      el("td", { class: "g-mono g-muted", title: entry.head || undefined }, entry.head ? String(entry.revisions) : "none"),
      plays ? el("td", {}, play ? String(play.count) : "0") : null,
      plays ? el("td", { class: "g-muted sources-nowrap" }, play ? when(play.last) : "") : null,
      el("td", {}, chatLink(view, entry)));
  });
  return ui.card({ kind: "flush" }, el("div", { class: "sources-table" }, el("table", { class: "g-table" },
    el("thead", {}, el("tr", {}, head.map((text, index) => el("th", { scope: "col" },
      text || el("span", { class: "sources-hidden" }, index ? "Chat" : "Picture"))))),
    el("tbody", {}, rows))));
}

// The one source a shelf shows: its name and exact ref, and what can be done to it.
function sourceHead(ctx, source, sources, address) {
  const { ui } = ctx;
  const { el } = ui;
  return ui.card({ tag: "section", label: source.title || source.source }, el("div", { class: "g-row g-between" },
    el("div", { class: "g-stack sources-source-head" }, ui.eyebrow(NAMESPACES[source.namespace] || "Source"),
      el("h2", { class: "g-heading" }, source.title || source.source),
      el("div", { class: "g-muted" }, el("span", { class: "g-ref" }, source.source), " · " + count(source.entities, "entry", "entries"))),
    el("div", { class: "g-row" },
      ctx.has("sources.rename_source") ? focusKey(ui.button("Rename", () => rename(ctx, source)), "sources-rename") : null,
      ctx.has("sources.create") ? focusKey(ui.button("New entry here", () => newEntry(ctx, sources, source)), "sources-new-here") : null,
      el("a", { class: "g-button", href: address({ source: "", limit: "" }) }, "Show every source"))));
}

// -- making and renaming --------------------------------------------------------------------------------

// A form in a modal: its fields, Cancel and one action. A refusal is shown in the modal, where the person is, and
// what they typed stays for another try.
function dialog(ctx, title, fields, label, work) {
  const { ui } = ctx;
  const modal = ui.modal(title);
  const problem = ui.el("div", {});
  const go = ui.button(label, async () => {
    if (go.disabled) return;
    go.disabled = true;
    problem.replaceChildren();
    try { await work(modal); } catch (error) {
      if (!error || error.reason !== "left") problem.replaceChildren(ui.errorBox(error));
    } finally { go.disabled = false; }
  }, { kind: "primary" });
  modal.body.append(...fields, problem);
  modal.foot.append(ui.button("Cancel", () => modal.close()), go);
  return modal;
}

// The source changed since this page read it: what was typed is kept, and the page reads the library again.
function reread(ctx, modal, message) {
  modal.close();
  ctx.notify(message, { bad: true });
  ctx.navigate(window.location.hash);
}

function rename(ctx, source) {
  const { ui } = ctx;
  const title = ui.field("Title", { value: source.title || "", draft: ctx.draft("sources:title:" + source.source) });
  dialog(ctx, "Rename " + source.source, [title.node], "Rename", async (modal) => {
    try {
      await ctx.api("sources.rename_source", { source: source.source, title: title.value.trim() || null, expected: source.etag });
    } catch (error) {
      if (error.reason !== "changed") throw error;
      reread(ctx, modal, "The source changed since this page read it. Your title is kept; rename it again.");
      return;
    }
    title.clear();
    modal.close();
    ctx.notify("Renamed " + source.source + ".");
    ctx.navigate(window.location.hash);
  });
}

function newEntry(ctx, sources, chosen) {
  const { ui } = ctx;
  if (!sources.length) {
    const modal = ui.modal("New entry");
    modal.body.append(ui.notice("An entry lives in a source, such as a world. Make a source first, with New source under the shelves."));
    modal.foot.append(ui.button("Close", () => modal.close()));
    return;
  }
  const key = (field) => ctx.draft("sources:new:" + field);
  const kept = key("source").get();
  const start = chosen ? chosen.source : sources.some((source) => source.source === kept) ? kept : sources[0].source;
  const where = ui.choice("Source", sources.map((source) => ({ value: source.source, label: (source.title || source.source) + " (" + source.source + ")" })),
    { value: start, onchange: (value) => key("source").set(value) });
  const kind = ui.choice("Kind", NEW_KINDS.map(([value, label]) => ({ value, label })), { value: key("kind").get() || "character",
    onchange: (value) => { key("kind").set(value); explain(); } });
  const name = ui.field("Name", { draft: key("name"), required: true });
  const text = ui.field("Description", { draft: key("text"), multiline: true });
  const id = ui.field("Id (optional)", { draft: key("id"), hint: "Left out, it is made from the name. An id is never reused." });
  const explain = () => {
    const what = kind.value;
    text.node.querySelector("label").textContent = what === "scenario" ? "Premise" : "Description";
    name.node.querySelector("label").textContent = what === "scenario" ? "Title" : "Name";
  };
  explain();
  dialog(ctx, "New entry", [where.node, kind.node, name.node, text.node, id.node], "Create", async (modal) => {
    const what = kind.value;
    const source = sources.find((item) => item.source === where.value);
    const fields = what === "scenario" ? { title: name.value, premise: text.value } : { name: name.value };
    if (what !== "scenario" && text.value.trim()) fields.description = text.value;
    const args = { source: source.source, kind: what, fields, expected: source.etag };
    if (id.value.trim()) args.id = id.value.trim();
    let answer;
    try {
      answer = await ctx.api("sources.create", args);
    } catch (error) {
      if (error.reason !== "changed") throw error;
      reread(ctx, modal, "The source changed since this page read it. Your draft is kept; create it again.");
      return;
    }
    for (const field of ["source", "kind", "name", "text", "id"]) key(field).clear();
    modal.close();
    ctx.navigate(ctx.href("source", [answer.ref]));
  });
}

function newSource(ctx, address) {
  const { ui } = ctx;
  const ref = ui.field("Source", { draft: ctx.draft("sources:new-source:ref"), required: true,
    hint: "Its exact ref, such as world:harbor. The card: namespace is the card importer's own." });
  const title = ui.field("Title (optional)", { draft: ctx.draft("sources:new-source:title") });
  dialog(ctx, "New source", [ref.node, title.node], "Create source", async (modal) => {
    const answer = await ctx.api("sources.create_source", { source: ref.value.trim(), title: title.value.trim() || null });
    ref.clear(); title.clear();
    modal.close();
    ctx.navigate(address({ source: answer.source, shelf: "", role: "", q: "", limit: "" }));
  });
}
