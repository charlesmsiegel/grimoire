// One library entry (109): #/source/<SourceRef>?tab=<tab>. A character opens as its character sheet (portrait,
// name, what it is made of); a scenario, lorebook, location, item or any other entry opens as an entry sheet under
// a title band. Both have the same tabs, and the tab in view is in the address, so Back and a link land on it:
//
//   Sheet      what the revision composes to, field by field (046); a portrayal overrides, clears and resets here
//   Art        a character's picture slots as the library resolves them, and the way to the Art page
//   Edit       the structured editor, with drafts that survive a stale write (./editors.js, ./drafts.js)
//   Revisions  every version, and one of them compared with the current one field by field
//   Raw        the stored payload, edited exactly as written
//
// A pinned ref shows exactly that revision, read-only; an unpinned one shows the current version (the head). Every
// edit writes a new revision: nothing here changes or deletes an old one.

import { FAMILIES, modelOf, unsafeNumbers } from "./drafts.js";
import { askedTab, chatsOn, count, diffRevisions, kindLabel, loreBound, loreEntries, metaParts, nextVersion, opensWith, orderRevisions, tabsFor,
  versionOf } from "./sheet.js";
import { draftsOf, editTab, rawTab, staleDrafts, unsaved } from "./editors.js";
import { attachPanel, deriveForm, portrayalTools } from "./actions.js";
import { headerMarks } from "./marks.js";
import { coverBand, entryPictures, lorePictures, shownOf as coverOf } from "../art/covers.js";

const STATES = {
  inherited: "inherited from the base",
  pinned: "inherited from the pinned base; the base has a newer revision",
  overridden: "overridden here",
  restated: "restated here; it no longer follows the base",
  local: "this revision's own",
  cleared: "cleared here",
  missing: "only the base's newer revision has it",
};
const STATE_NOTES = ["pinned", "restated", "missing"];
const STATE_KINDS ={ overridden: "accent", restated: "accent", cleared: "bad", missing: "bad" };
// How the base's newer revision compares, for this field (046's upstream); "" when there is nothing to compare.
const UPSTREAM = {
  same: "The base's newer revision has the same value.",
  changed: "The base's newer revision has a different value; it arrives only with a reviewed rebase.",
  removed: "The base's newer revision no longer has it.",
  added: "Only the base's newer revision has it; it arrives only with a reviewed rebase.",
};
const NOT_OFFERED = { delete: "Deleting", art: "Pictures", import: "Importing", relations: "Typed relations",
  abilities: "Abilities and facets with fields", rebase: "Moving a chat to a newer revision", voice: "A voice" };
const SLOT_STATES = { shown: "Shown", hidden: "Hidden", unavailable: "Unavailable" };
const SLOT_SHAPES = { avatar: "source-shape-square", portrait: "source-shape-portrait" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const asked = ctx.path[0];
  if (!asked) {
    ctx.setTitle("Entry");
    root.append(ui.empty("No entry is named."), ui.link("Open Characters and lore", "sources"));
    return;
  }
  const read = await ctx.api("sources.read", { ref: asked });
  const pinned = read.entity + "@" + read.revision;
  const name = read.name || read.id || read.ref;
  ctx.setTitle(read.name || read.ref);
  const library = ctx.registered("sources");
  ctx.setCrumb(library ? crumbLink(ctx, "Characters and lore") : "Characters and lore", " / ",
    library ? crumbLink(ctx, read.source_title || read.source, { source: read.source }) : (read.source_title || read.source));

  // a character's picture slots, where the app reads them (114); a failure leaves the sheet without pictures
  const character = read.kind === "character";
  let art = null;
  let artError = null;
  if (character && ctx.has("media.art")) {
    try { art = await ctx.api("media.art", { subject: read.entity }); } catch (error) {
      if (error.reason === "left") throw error;
      artError = error;
    }
  }
  // what a chat with no name of its own is called elsewhere in the app: who or what it was started from
  const called = new Map();
  if (read.chats.some((chat) => !chat.label) && ctx.has("chats")) {
    const filter = ["character", "scenario"].includes(read.kind) ? { filter: read.kind, source: read.entity } : {};
    try {
      const answer = await ctx.api("chats", { ...filter, limit: 1000 });
      for (const chat of answer.chats) called.set(chat.chat, (chat.launch && chat.launch.name) || (chat.scope && chat.scope.title) || null);
    } catch (error) {
      if (error.reason === "left") throw error;  // otherwise such a chat is shown by its exact ref
    }
  }
  const titleOf = (chat) => chat.label || called.get(chat.chat) || null;
  const tabs = tabsFor(read, { art: character && ctx.has("media.art") });
  const tab = askedTab(ctx.query.get("tab"), tabs);
  const address = (which, extra = {}) => ctx.href("source", [asked], { tab: which === "sheet" ? undefined : which, ...extra });
  const page = { read, pinned, name, art, artError, address, tab, titleOf, marks: await headerMarks(ctx, read),
    cover: character ? null : await coverOf(ctx, read.entity, "cover", { fresh: true }) };  // the entry's cover, where the app reads covers

  const attach = read.actions.attach === null && ctx.has("sources.attach") && ctx.has("sources.chats") ? attachPanel(ctx, read, pinned) : null;
  const parts = [character ? characterHeader(ctx, page, attach) : entryHeader(ctx, page, attach)];
  if (attach) parts.push(attach.node);
  if (!read.is_head) {
    parts.push(ui.notice("This is " + read.revision + ", shown exactly as it was written. Only the current version, " + read.head
      + ", is edited; this one can still be attached as it is."),
    ui.actions(el("a", { class: "g-button", href: ctx.href("source", [read.entity]) }, "Show the current version")));
  }
  if (read.composition_error) {
    parts.push(ui.notice("This revision does not compose, so it cannot be played: " + describe(read.composition_error), { kind: "bad" }));
  }
  const drafts = draftsOf(ctx, read.entity);
  if (read.is_head) {
    const held = staleDrafts(ctx, read, drafts, address);
    if (held) parts.push(held);
    const open = unsaved(read, drafts).filter((which) => which !== tab);
    if (open.length) {
      parts.push(ui.notice(el("span", {}, "You have unsaved changes: ",
        ...open.flatMap((which, index) => [index ? " and " : "", el("a", { class: "g-link", href: address(which) }, which === "edit" ? "on the Edit tab" : "on the Raw tab")]),
        "."), { kind: "soft" }));
    }
  }
  const strip = ui.segments("Views of " + name, tabs, { value: tab, onchange: (which) => ctx.navigate(address(which)) });
  for (const node of strip.node.querySelectorAll("button")) node.setAttribute("data-focus-key", "source-tab:" + node.getAttribute("data-value"));
  parts.push(el("div", { class: "source-tabs" }, strip.node));

  let body;
  if (tab === "edit") body = editTab(ctx, read, drafts, address);
  else if (tab === "raw") body = rawTab(ctx, read, drafts);
  else if (tab === "revisions") body = await revisionsTab(ctx, page);
  else if (tab === "art") body = artTab(ctx, page);
  else body = sheetTab(ctx, page);
  body.setAttribute("role", "region");
  body.setAttribute("aria-label", (tabs.find((item) => item.value === tab) || tabs[0]).label);
  parts.push(body);
  root.classList.add("source-page");
  root.append(...parts);
}

// A link in the line above the title: it reads as that line does, not as a link in the text.
function crumbLink(ctx, text, query) {
  return ctx.ui.el("a", { class: "source-crumb", href: ctx.href("sources", [], query) }, text);
}

function describe(item) {
  if (typeof item === "string") return item;
  if (item && item.reason) return item.reason + (item.detail ? ": " + item.detail : item.message ? ": " + item.message : "");
  if (item && item.code) return item.code + (item.detail ? ": " + item.detail : "");
  return JSON.stringify(item);
}

// -- the two headers ------------------------------------------------------------------------------------

// The picture a character shows: its portrait slot, or its avatar when no portrait is placed.
function likeness(ctx, art) {
  if (!art) return undefined;
  for (const slot of ["portrait", "avatar"]) {
    const found = art.slots.find((item) => item.slot === slot && item.state === "shown" && item.image);
    if (found) return ctx.picture(found.image).catch(() => null);
  }
  return undefined;
}

function pills(ctx, read) {
  const { ui } = ctx;
  return [ui.pill(kindLabel(read.kind), { kind: "accent" }),
    read.derived ? ui.pill("Portrayal") : null,
    read.effective && read.effective.nature === "template" ? ui.pill("Template") : null,
    read.is_head ? null : ui.pill("Older version"),
    read.composition_error ? ui.pill("Does not compose", { kind: "bad" }) : null];
}

// Start a chat, Edit and Attach to a chat: each only where the app can do it for this entry.
function headerActions(ctx, page, attach) {
  const { ui } = ctx;
  const { el } = ui;
  const { read, address } = page;
  const nodes = [];
  if (["character", "scenario"].includes(read.kind) && ctx.registered("start") && !read.composition_error) {
    nodes.push(ui.buttonLink("Start a chat", "start", [], { source: read.entity }, { kind: "primary" }));
  }
  nodes.push(el("a", { class: "g-button", href: address("edit") }, "Edit"));
  if (attach) {
    const toggle = ui.button("Attach to a chat", () => { toggle.setAttribute("aria-expanded", String(attach.toggle())); });
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("data-focus-key", "source-attach");
    nodes.push(toggle);
  }
  return el("div", { class: "g-row source-header-actions" }, ...nodes);
}

function identity(ctx, page) {
  const { ui } = ctx;
  const { el } = ui;
  const { read, pinned } = page;
  return [el("div", { class: "g-muted source-meta" }, metaParts(read).join(" · ")),
    el("div", { class: "g-ref g-muted", title: "Its exact ref" }, pinned),
    read.derived && read.base ? el("div", { class: "g-muted" }, "Derived from ", ui.sourceLink(read.base, read.base), ".") : null];
}

function characterHeader(ctx, page, attach) {
  const { ui } = ctx;
  const { el } = ui;
  const { read, name, art, address } = page;
  const picture = ui.portrait(name, { key: read.entity, picture: likeness(ctx, art) });
  const face = art ? el("a", { class: "source-portrait", href: address("art"), "aria-label": "Pictures of " + name }, picture)
    : el("span", { class: "source-portrait" }, picture);
  return el("section", { class: "source-header", "aria-label": name }, face,
    el("div", { class: "source-header-text g-stack" }, el("div", { class: "g-row source-pills" }, ...pills(ctx, read), page.marks),
      el("h2", { class: "g-hero-title" }, name), ...identity(ctx, page)),
    headerActions(ctx, page, attach));
}

function entryHeader(ctx, page, attach) {
  const { ui } = ctx;
  const { el } = ui;
  const { read, name } = page;
  const marks = [...pills(ctx, read).slice(1), page.marks].filter(Boolean);
  return el("section", { class: "source-entry-header g-stack", "aria-label": name },
    coverBand(ctx, { subject: read.entity, name, tall: true, dashed: true, view: page.cover,
      use: "Shown across the top of its sheet and on its library tile" }, el("div", { class: "g-stack source-cover-text" },
      el("div", { class: "g-eyebrow source-cover-kind" }, kindLabel(read.kind)), el("h2", { class: "g-hero-title" }, name))),
    el("div", { class: "g-row g-between" },
      el("div", { class: "g-stack source-header-text" }, marks.length ? el("div", { class: "g-row source-pills" }, ...marks) : null, ...identity(ctx, page)),
      headerActions(ctx, page, attach)));
}

// -- the Sheet tab --------------------------------------------------------------------------------------

function label(field) {
  if (field.startsWith("section:")) return field.slice(8);
  if (field.startsWith("facet:")) return "Facet " + field.slice(6);
  return field[0].toUpperCase() + field.slice(1);
}

function shown(value) {
  return typeof value === "string" ? value : JSON.stringify(value);
}

const labelled = (el, title, ...children) => el("section", { class: "g-stack source-block" }, el("h3", { class: "g-eyebrow source-block-title" }, title), ...children);

function sheetTab(ctx, page) {
  const { ui } = ctx;
  const { el } = ui;
  const { read } = page;
  const main = el("div", { class: "source-main g-stack g-loose" });
  const parts = read.effective ? composed(ctx, read) : { top: written(ctx, read), rest: [] };
  main.append(...parts.top);
  const opens = opensWith(read);
  if (opens.length) {
    main.append(labelled(el, "Opens with", ...opens.map((text) => el("p", { class: "g-quote source-quote" }, text))));
  }
  const bound = loreBound(read);
  if (read.kind === "character" && read.payload && read.payload.card) {
    main.append(labelled(el, "Lorebooks bound", bound.length
      ? el("div", { class: "g-chips" }, bound.map((ref) => { const chip = ui.sourceLink(ref, ref); chip.classList.add("source-chip"); return chip; }))
      : el("span", { class: "g-muted" }, "None bound.")));
  }
  main.append(...parts.rest);
  const pictures = read.kind === "character" ? null : entryPictures(ctx, { subject: read.entity, source: read.source, name: page.name, kind: read.kind });
  if (pictures) main.append(pictures);
  return el("div", { class: "source-sheet" }, main, aside(ctx, page));
}

// One field of what the revision composes to. A portrayal's fields say where each comes from and carry its tools.
function fieldBlock(ctx, read, item, { lede = false, editable = false } = {}) {
  const { ui } = ctx;
  const { el } = ui;
  const facet = item.field.startsWith("facet:");
  const head = el("div", { class: "source-field-head" });
  if (!lede || read.derived) head.append(el("span", { class: "g-eyebrow source-label" }, label(item.field)));
  if (read.derived) {
    const state = ui.pill(item.state, { kind: STATE_KINDS[item.state] || "" });
    state.classList.add("source-state");
    state.title = STATES[item.state] || "";
    head.append(state);
    // the states a word does not explain say what they mean beside it
    if (STATE_NOTES.includes(item.state)) head.append(el("span", { class: "g-muted" }, STATES[item.state]));
  }
  const empty = item.value === null || item.value === undefined;
  const row = el("div", { class: "source-field" + (lede ? " source-field-lede" : "") }, head.childNodes.length ? head : null,
    el("div", { class: empty ? "g-muted" : lede ? "source-lede" : facet ? "source-value g-mono" : "source-value g-prose" },
      empty ? "(nothing)" : shown(item.value)),
    read.derived && item.source && item.source !== read.ref ? el("div", { class: "g-muted" }, "From ", ui.sourceLink(item.source, item.source)) : null,
    UPSTREAM[item.upstream] ? el("div", { class: "g-muted" }, UPSTREAM[item.upstream]) : null);
  if (editable) {
    const tools = portrayalTools(ctx, read, item);
    if (tools) row.append(tools);
  }
  return row;
}

// An entity that composes: `top` is its description, large, and its sections in two columns; `rest`, shown after
// what it opens with, is its facets and how it was composed.
function composed(ctx, read) {
  const { ui } = ctx;
  const { el } = ui;
  const view = read.effective;
  const editable = read.derived && read.is_head && read.actions.portrayal === null && ctx.has("sources.portrayal");
  const fields = view.fields;
  const nodes = [];
  const description = fields.find((item) => item.field === "description");
  if (description) nodes.push(fieldBlock(ctx, read, description, { lede: true, editable }));
  else if (!read.derived) nodes.push(el("p", { class: "g-muted" }, "No description yet."));
  const sections = fields.filter((item) => item.field.startsWith("section:"));
  if (sections.length) nodes.push(el("div", { class: "source-sections" }, sections.map((item) => fieldBlock(ctx, read, item, { editable }))));
  const rest = [];
  const others = fields.filter((item) => item.field.startsWith("facet:") || (read.derived && item.field === "name"));
  if (others.length) {
    rest.push(labelled(el, read.derived ? "Name and facets" : "Facets",
      el("div", { class: "source-sections" }, others.map((item) => fieldBlock(ctx, read, item, { editable })))));
  }
  const notes = [];
  if (view.chain && view.chain.length > 1) {
    notes.push(el("p", { class: "g-muted" }, "Composed from ",
      ...view.chain.flatMap((link, index) => [index ? " over " : "", ui.sourceLink(link.ref, link.ref)]), "."));
  }
  if (view.base_head) {
    notes.push(ui.notice("The base has a newer revision, " + view.base_head + ". This portrayal stays pinned to "
      + view.base + " until a reviewed rebase."));
  }
  for (const notice of view.notices || []) notes.push(ui.notice(describe(notice)));
  if (view.uncomposed && view.uncomposed.length) {
    notes.push(el("p", { class: "g-muted" }, "Kept but not composed: "
      + view.uncomposed.map((item) => item.field).join(", ") + " (kept for provenance; the Raw tab shows them)."));
  }
  if (notes.length) rest.push(el("div", { class: "g-stack source-notes" }, ...notes));
  return { top: nodes, rest };
}

// A scenario, a lorebook, an entity that does not compose, or a record with no layout: what its payload holds.
function written(ctx, read) {
  const { ui } = ctx;
  const { el } = ui;
  const model = FAMILIES.includes(read.family) ? modelOf(read) : null;
  if (!model) {
    return [el("p", { class: "g-muted" }, "This record has no sheet layout. What it stores is on the Raw tab.")];
  }
  const nodes = [];
  const lede = read.family === "scenario" ? model.premise : model.description;
  nodes.push(lede ? el("p", { class: "source-lede" }, lede)
    : el("p", { class: "g-muted" }, read.family === "scenario" ? "No premise yet." : "No description yet."));
  if (read.family === "scenario" && model.instructions) {
    nodes.push(labelled(el, "Instructions", el("div", { class: "g-muted" }, "Private to the narrator: never shown to the players."),
      el("div", { class: "source-value g-prose" }, model.instructions)));
  }
  if (read.family === "entity" && !model.locked && model.sections.length) {
    nodes.push(el("div", { class: "source-sections" }, model.sections.map((item) => el("div", { class: "source-field" },
      el("div", { class: "source-field-head" }, el("span", { class: "g-eyebrow source-label" }, item.name)),
      el("div", { class: "source-value g-prose" }, item.text || "(empty)")))));
  }
  if (read.family === "lorebook") {
    const entries = loreEntries(read);
    // each entry's own picture, on the current version (pages/art/covers.js): a slot beside it, filled once read
    const pictured = read.is_head ? lorePictures(ctx, { subject: read.entity, source: read.source, name: read.name || read.ref,
      revision: read.revision, reload: () => ctx.navigate(window.location.hash) }) : null;
    nodes.push(labelled(el, "Entries", entries.length ? el("div", { class: "g-stack" }, entries.map((entry, index) =>
      ui.card({}, el("div", { class: "source-lore-row" }, pictured ? pictured.slot(index, entry.keys.join(", ") || "entry " + (index + 1)) : null,
        el("div", { class: "g-stack source-lore" },
          el("div", { class: "g-row" }, el("span", { class: "g-mono g-muted source-keys" }, entry.keys.length ? entry.keys.join(", ") : "no keys"),
            entry.enabled ? null : ui.pill("Off"), entry.constant ? ui.pill("Always in context", { kind: "accent" }) : null),
          el("div", { class: "source-value source-lore-text" }, entry.content || "(empty)"))))), pictured ? pictured.note : null)
      : el("p", { class: "g-muted" }, model.locked ? "Its entries are not all plain text; the Raw tab shows them." : "No entries yet.")));
  }
  if (model.locked && read.family !== "lorebook") {
    nodes.push(el("p", { class: "g-muted" }, "Part of it is not plain text; the Raw tab shows it as written."));
  }
  return nodes;
}

// The right column: the chats that use it, its portrayals, entries of the same name and the links its source
// declares.
function aside(ctx, page) {
  const { ui } = ctx;
  const { el } = ui;
  const { read, pinned, titleOf } = page;
  const column = el("aside", { class: "source-aside g-stack g-loose" });
  const chats = read.chats.map((chat) => {
    const detail = (chat.active ? "on " + chat.revisions.join(", ") : "detached") + (chat.problem ? " · " + chat.problem : "");
    const title = titleOf(chat);
    const body = [el("span", { class: "g-row g-between" }, el("span", { class: title ? "g-title" : "g-ref" }, title || chat.chat),
      el("span", { class: "g-muted" }, day(chat.last_activity))), el("span", { class: "g-muted" }, detail)];
    return ctx.registered("chat") ? el("a", { class: "g-rowcard source-chat", href: ctx.href("chat", [chat.chat]), title: chat.chat }, el("span", { class: "g-stack g-grow source-chat-text" }, ...body))
      : el("div", { class: "g-rowcard source-chat", title: chat.chat }, el("span", { class: "g-stack g-grow source-chat-text" }, ...body));
  });
  column.append(labelled(el, "In these chats", ...(chats.length ? chats : [el("span", { class: "g-muted" }, "Not in any chat yet.")])));
  if (read.family === "entity") {
    const derive = deriveForm(ctx, read, pinned);
    column.append(labelled(el, "Portrayals",
      ...read.portrayals.map((ref) => { const row = ui.sourceLink(ref, ref); row.classList.add("g-rowcard"); return row; }),
      !read.portrayals.length && !derive ? el("span", { class: "g-muted" }, "None derived from it.") : null, derive));
  }
  if (read.namesakes.length) {
    column.append(labelled(el, "Same name, other entries",
      el("p", { class: "g-muted" }, "These share its name and are different entries: a name never decides which one a chat uses."),
      ...read.namesakes.map((ref) => { const row = ui.sourceLink(ref, ref); row.classList.add("g-rowcard"); return row; })));
  }
  if (read.links.length) {
    column.append(labelled(el, "Links its source declares", ...read.links.map((link) =>
      el("div", { class: "source-link" }, ui.sourceLink(link.subject, link.subject), " " + link.relation + " ", ui.sourceLink(link.object, link.object),
        el("span", { class: "g-muted" }, " (declared by " + link.declared_by + ")")))));
  }
  const limits = Object.entries(read.unavailable || {}).map(([key, reason]) =>
    el("div", { class: "source-limit" }, el("strong", {}, NOT_OFFERED[key] || key), el("div", { class: "g-muted" }, reason)));
  if (read.actions.attach !== null) limits.unshift(el("div", { class: "source-limit" }, el("strong", {}, "Attaching"), el("div", { class: "g-muted" }, read.actions.attach)));
  if (limits.length) column.append(el("details", { class: "source-limits" }, el("summary", {}, "Not offered here"), ...limits));
  return column;
}

// A moment as a date, in the reader's own form; the text as written when it is not a date.
function day(text) {
  const then = Date.parse(text);
  return Number.isFinite(then) ? new Date(then).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : String(text || "");
}

// -- the Art tab ----------------------------------------------------------------------------------------

// A character's picture slots as the library resolves them now. Placing, cropping and hiding are the Art page's.
function artTab(ctx, page) {
  const { ui } = ctx;
  const { el } = ui;
  const { read, art, artError, name } = page;
  const box = el("div", { class: "source-art g-stack g-loose" });
  if (artError || !art) {
    box.append(artError ? ui.errorBox(artError) : ui.empty("This app cannot read its pictures."));
    return box;
  }
  const cards = art.slots.map((slot) => {
    const frame = el("div", { class: "source-slot-frame " + (SLOT_SHAPES[slot.slot] || "source-shape-picture"), "aria-hidden": "true" },
      slot.state === "shown" ? "" : slot.state === "hidden" ? "Hidden" : "?");
    if (slot.state === "shown" && slot.image) {
      ctx.picture(slot.image).then((url) => { if (url) frame.replaceChildren(el("img", { src: url, alt: "" })); }, () => {});
    }
    const from = slot.placed_by && slot.placed_by !== read.entity ? "Inherited from " + slot.placed_by : slot.placed_by ? "Placed on this character" : null;
    return ui.card({}, el("div", { class: "g-stack source-slot" }, frame,
      el("div", { class: "g-row g-between" }, el("strong", {}, (slot.slot[0].toUpperCase() + slot.slot.slice(1)).replaceAll("_", " ")),
        el("span", { class: slot.state === "unavailable" ? "source-bad" : "g-muted" }, SLOT_STATES[slot.state] || slot.state)),
      slot.state === "unavailable" ? el("div", { class: "g-muted" }, [slot.reason, slot.note].filter(Boolean).join(": ")) : null,
      from ? el("div", { class: "g-muted" }, from) : null));
  });
  box.append(labelled(el, "Where these pictures show", cards.length ? el("div", { class: "source-slots" }, cards)
    : el("p", { class: "g-muted" }, "No picture is placed for " + name + " yet.")));
  for (const problem of art.problems || []) box.append(ui.notice(describe(problem), { kind: "bad" }));
  if (art.conflicts && art.conflicts.length) {
    box.append(ui.notice("Two devices placed different pictures in: " + art.conflicts.join(", ") + ". Devices and sync settles it."));
  }
  if (ctx.registered("art")) {
    box.append(ui.actions(ui.buttonLink("Place, crop or hide pictures on the Art page", "art", [], { scope: read.source, subject: read.entity })));
  }
  return box;
}

// -- the Revisions tab ----------------------------------------------------------------------------------

// Every version, newest first, and one of them compared with the current version field by field. The one compared
// is in the address (?tab=revisions&rev=<revision>); an older one can be restored as a new version.
async function revisionsTab(ctx, page) {
  const { ui } = ctx;
  const { el } = ui;
  const { read, address, name, titleOf } = page;
  const revisions = orderRevisions(read.revisions);  // as written: the app's own list is sorted as text (r10 before r2)
  const asked = ctx.query.get("rev");
  const before = revisions.at(-2);
  const chosen = revisions.includes(asked) ? asked : !read.is_head ? read.revision : before || read.head;
  const timeline = el("div", { class: "source-timeline g-stack", role: "list" }, [...revisions].reverse().map((revision) => {
    const on = chatsOn(read, revision);
    const row = el("a", { class: "source-rev", role: "listitem", href: address("revisions", { rev: revision }), title: read.entity + "@" + revision },
      el("span", { class: "source-rev-dot", "aria-hidden": "true" }),
      el("span", { class: "g-stack source-rev-text" },
        el("span", { class: "g-mono" }, revision + (revision === read.head ? " · current" : "")),
        el("span", { class: "source-rev-title" }, "Version " + versionOf(revisions, revision) + (revision === read.head ? ", what plays now" : "")),
        on.length ? el("span", { class: "g-muted" }, "Chats on it: " + on.map((chat) => titleOf(chat) || chat.chat).join(", ")) : null));
    if (revision === chosen) row.setAttribute("aria-current", "true");
    return row;
  }));
  const side = el("div", { class: "source-compare g-stack" });
  const box = el("div", { class: "source-revisions" }, timeline, side);
  if (chosen === read.head) {
    side.append(el("h3", { class: "g-heading" }, "Version " + versionOf(revisions, chosen) + " is the current version"),
      el("p", { class: "g-muted" }, revisions.length > 1 ? "Choose an older version to compare it with this one."
        : "It is the only version so far. Every save adds one, and none is ever changed."));
    return box;
  }
  const older = chosen === read.revision ? read : await ctx.api("sources.read", { ref: read.entity + "@" + chosen });
  const head = read.is_head ? read : await ctx.api("sources.read", { ref: read.entity });
  const changes = diffRevisions(read.family, older.payload, head.payload);
  side.append(el("h3", { class: "g-heading" }, "Version " + versionOf(revisions, chosen) + " compared with the current version " + versionOf(revisions, head.head)),
    el("div", { class: "source-compare-key g-muted" }, el("span", { class: "source-before source-swatch" }, chosen + " had"),
      el("span", { class: "source-after source-swatch" }, head.head + " has")));
  const sideOf = (change, value, which) => el("div", { class: "source-diff-side " + which + (value === null ? " g-muted" : "")
    + (change.stored ? " source-diff-stored" : "") }, value === null ? "(not there)" : value);
  for (const change of changes) {
    side.append(el("div", { class: "g-stack source-diff" }, el("span", { class: "g-eyebrow" }, change.field),
      el("div", { class: "source-diff-pair" }, sideOf(change, change.before, "source-before"), sideOf(change, change.after, "source-after"))));
  }
  if (!changes.length) side.append(el("p", { class: "g-muted" }, "Nothing it stores differs from the current version."));
  const actions = [];
  const can = changes.length && head.actions.save_raw === null && ctx.has("sources.save_raw") && !unsafeNumbers(older.payload);
  if (can) {
    const next = nextVersion(head);
    actions.push(ui.button("Restore this as a new version", () => ctx.act(async () => {
      const yes = await ui.confirm("Restore " + name + " as it was in version " + versionOf(revisions, chosen) + " (" + chosen + ")? It is saved as version "
        + next + ". The current version stays as it is, and chats keep the one they started with.", { confirmLabel: "Restore as version " + next });
      if (!yes) return;
      try {
        const answer = await ctx.api("sources.save_raw", { ref: head.entity, head: head.head, etag: head.etag, expected: head.manifest_etag,
          payload: older.payload });
        ctx.notify("Restored as " + answer.ref.split("@").pop() + ".");
        ctx.navigate(ctx.href("source", [head.entity]));
      } catch (error) {
        if (error.reason !== "changed") throw error;
        ctx.notify("This entry changed since the page read it, so nothing was restored. It is read again.", { bad: true });
        ctx.navigate(ctx.href("source", [head.entity], { tab: "revisions", rev: chosen }));
      }
    })));
  }
  if (chosen !== read.revision) actions.push(el("a", { class: "g-button", href: ctx.href("source", [read.entity + "@" + chosen]) }, "Open this version"));
  if (actions.length) side.append(ui.actions(...actions));
  return box;
}
