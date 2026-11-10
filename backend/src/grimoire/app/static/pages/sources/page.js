// The Library (109): every source and its entries, by role, searched by name or ref. Each entry is listed by its
// exact, source-qualified ref: two characters of one name in two sources are two rows, never one. The filter
// lives in the address (#/sources?role=character&q=mara&source=<source>), so a deep link and Back land on it.

const ROLES = [["", "Everything"], ["character", "Characters"], ["persona", "Personas"], ["scenario", "Scenarios"],
  ["lore", "Lore"], ["world", "Worlds"]];
const KIND_OF = { character: "character", persona: "character", scenario: "scenario", lore: "lorebook" };
// What a new entry can be: its kind, and the structured field its first revision needs besides a name.
const NEW_KINDS = [["character", "Character"], ["location", "Location"], ["item", "Item"], ["scenario", "Scenario"],
  ["lorebook", "Lorebook"]];
const PAGE = 200, MAX_LIST = 1000;

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  const role = ROLES.some(([name]) => name && name === ctx.query.get("role")) ? ctx.query.get("role") : "";
  const query = ctx.query.get("q") || "";
  const only = ctx.query.get("source") || "";
  const asked = Number.parseInt(ctx.query.get("limit") || "", 10);
  const limit = Number.isInteger(asked) && asked >= 1 ? Math.min(asked, MAX_LIST) : PAGE;
  const address = (changes) => {
    const next = { role: role || undefined, q: query || undefined, source: only || undefined,
      limit: limit === PAGE ? undefined : limit, ...changes };
    for (const key of Object.keys(next)) if (next[key] === "" || next[key] === undefined) delete next[key];
    return ctx.href("sources", [], next);
  };

  ctx.setTitle("Library");
  const tabs = el("div", { class: "g-actions", role: "group", "aria-label": "Which entries" });
  for (const [name, label] of ROLES) {
    const tab = el("a", { class: "g-button", href: address({ role: name || undefined, limit: undefined }) }, label);
    if (name === role) tab.setAttribute("aria-current", "page");
    tabs.append(tab);
  }
  const search = ui.field("Search names and refs", { value: query, name: "q" });
  const form = el("form", { class: "sources-search", role: "search" }, search.node,
    el("button", { type: "submit", class: "g-button" }, "Search"));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    ctx.navigate(address({ q: search.value.trim() || undefined, limit: undefined }));
  });
  root.append(tabs, form);

  const args = { limit };
  if (query) args.query = query;
  if (KIND_OF[role]) args.kind = KIND_OF[role];
  if (only) args.source = only;
  const catalog = await ctx.api("sources.catalog", args);
  const sources = catalog.sources;
  const chosen = only ? sources.find((source) => source.source === only) : null;
  const worlds = new Set(sources.filter((source) => source.namespace === "world").map((source) => source.source));
  // A world's entries are listed one world at a time, filtered by the catalog itself, so a limit never hides them.
  const pickWorld = role === "world" && !only;
  const entries = role === "world" ? catalog.entries.filter((entry) => worlds.has(entry.source)) : catalog.entries;

  if (only) {
    root.append(ui.notice(chosen ? "Showing " + (chosen.title || chosen.source) + " (" + chosen.source + ") only."
      : "No source " + only + " is in this library.", { kind: chosen ? "" : "bad" }),
      ui.actions(el("a", { class: "g-button", href: address({ source: undefined, limit: undefined }) }, "Show every source")));
  }
  if (role === "persona") {
    root.append(el("p", { class: "g-muted" }, "Any character can be your persona. You choose it from the chat you play."));
  }
  for (const reason of catalog.excluded || []) root.append(ui.notice(describeExcluded(reason), { kind: "bad" }));

  if (pickWorld) {
    root.append(el("p", { class: "g-muted" }, "Choose a world below to see its locations, items and characters."));
  } else {
    const rows = entries.map((entry) => entryRow(ctx, entry));
    const heading = role === "world" ? "Entries of this world" : (ROLES.find(([name]) => name === role) || ROLES[0])[1];
    const listed = ui.section(heading, ui.list(rows, { empty: query ? "Nothing matches " + JSON.stringify(query) + "." : "Nothing here yet." }));
    if (catalog.truncated) {
      listed.append(el("p", { class: "g-muted" }, "Showing " + catalog.entries.length + " of " + catalog.total + " entries."));
      if (limit < MAX_LIST) listed.append(el("a", { class: "g-button", href: address({ limit: Math.min(limit * 4, MAX_LIST) }) }, "Show more"));
    }
    root.append(listed);
  }

  const shown = role === "world" ? sources.filter((source) => worlds.has(source.source)) : sources;
  root.append(ui.section(role === "world" ? "Worlds" : "Sources", ui.list(shown.map((source) =>
    el("a", { href: address({ source: source.source, limit: undefined }), title: source.source },
      ui.rowText(source.title || source.source, source.source + " · " + count(source.entities, "entry", "entries")))),
  { empty: "No sources yet." })));

  if (chosen && ctx.has("sources.rename_source")) root.append(renameForm(ctx, chosen));
  if (chosen && ctx.has("sources.create")) root.append(createEntryForm(ctx, chosen));
  if (ctx.has("sources.create_source")) root.append(createSourceForm(ctx, address));
}

function entryRow(ctx, entry) {
  const { el, rowText } = ctx.ui;
  const detail = [entry.kind, entry.source, entry.head || "no head"];
  if (entry.derived) detail.push("a portrayal");
  if (entry.problem) detail.push(entry.problem);
  const node = el("a", { href: ctx.href("source", [entry.ref]), title: entry.ref,
    class: entry.problem ? "sources-problem" : undefined }, rowText(entry.name || entry.ref, detail.join(" · ")));
  if (entry.namesakes && entry.namesakes.length) {
    node.append(el("span", { class: "g-muted g-row-detail" },
      "Same name as " + entry.namesakes.join(", ") + ". A different entry: names never decide which one."));
  }
  return node;
}

function describeExcluded(item) {
  if (typeof item === "string") return "Not shown: " + item;
  const what = item.source || item.ref || "a source";
  return "Not shown: " + what + (item.reason ? " (" + item.reason + ")" : "");
}

function count(n, one, many) { return n + " " + (n === 1 ? one : many); }

function renameForm(ctx, source) {
  const { ui } = ctx;
  const title = ui.field("Title", { value: source.title || "", draft: ctx.draft("sources:title:" + source.source) });
  const save = ui.button("Rename", () => ctx.act(async () => {
    try {
      await ctx.api("sources.rename_source", { source: source.source, title: title.value.trim() || null, expected: source.etag });
    } catch (error) {
      if (error.reason === "changed") {  // the typed title is kept: read the source again and let them retry
        ctx.notify("The source changed since this page read it. Your title is kept; rename it again.", { bad: true });
        ctx.navigate(window.location.hash);
        return;
      }
      throw error;
    }
    title.clear();
    ctx.notify("Renamed " + source.source + ".");
    ctx.navigate(window.location.hash);
  }));
  return ui.section("Rename " + source.source, title.node, ui.actions(save));
}

function createEntryForm(ctx, source) {
  const { ui } = ctx;
  const key = (field) => ctx.draft("sources:new:" + source.source + ":" + field);
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
  const create = ui.button("Create", () => ctx.act(async () => {
    const what = kind.value;
    const fields = what === "scenario" ? { title: name.value, premise: text.value } : { name: name.value };
    if (what !== "scenario" && text.value.trim()) fields.description = text.value;
    const args = { source: source.source, kind: what, fields, expected: source.etag };
    if (id.value.trim()) args.id = id.value.trim();
    let answer;
    try {
      answer = await ctx.api("sources.create", args);
    } catch (error) {
      if (error.reason === "changed") {  // the draft is kept: read the source again and let them retry
        ctx.notify("The source changed since this page read it. Your draft is kept; create it again.", { bad: true });
        ctx.navigate(window.location.hash);
        return;
      }
      throw error;
    }
    for (const field of ["kind", "name", "text", "id"]) key(field).clear();
    ctx.navigate(ctx.href("source", [answer.ref]));
  }), { kind: "primary" });
  return ui.section("New entry in " + (source.title || source.source), kind.node, name.node, text.node, id.node,
    ui.actions(create));
}

function createSourceForm(ctx, address) {
  const { ui } = ctx;
  const ref = ui.field("Source", { draft: ctx.draft("sources:new-source:ref"), required: true,
    hint: "Its exact ref, such as world:harbor. The card: namespace is the card importer's own." });
  const title = ui.field("Title (optional)", { draft: ctx.draft("sources:new-source:title") });
  const create = ui.button("Create source", () => ctx.act(async () => {
    const answer = await ctx.api("sources.create_source", { source: ref.value.trim(), title: title.value.trim() || null });
    ref.clear(); title.clear();
    ctx.navigate(address({ source: answer.source, role: undefined, q: undefined, limit: undefined }));
  }));
  return ui.section("New source", ref.node, title.node, ui.actions(create));
}
