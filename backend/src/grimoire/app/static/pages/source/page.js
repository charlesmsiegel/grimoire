// One source entry (109): #/source/<SourceRef>. A pinned ref shows exactly that revision; an unpinned one shows
// the head. It shows what the revision composes to field by field (046), edits the head with drafts that survive
// a stale write (the etag the page read is named on every save), derives portrayals, overrides, clears and resets
// their fields, and attaches the exact revision shown to a chat. Every edit writes a new revision: nothing here
// changes or deletes an old one. What a draft changes, and how it is held over a newer head, is ./drafts.js.

import { FAMILIES, changesOf, fieldsOf, modelOf, rebase, rebaseRaw, unsafeNumbers } from "./drafts.js";

const STATES = {
  inherited: "inherited from the base",
  pinned: "inherited from the pinned base; the base has a newer revision",
  overridden: "overridden here",
  restated: "restated here; it no longer follows the base",
  local: "this revision's own",
  cleared: "cleared here",
  missing: "only the base's newer revision has it",
};
// How the base's newer revision compares, for this field (046's upstream); "" when there is nothing to compare.
const UPSTREAM = {
  same: "The base's newer revision has the same value.",
  changed: "The base's newer revision has a different value; it arrives only with a reviewed rebase.",
  removed: "The base's newer revision no longer has it.",
  added: "Only the base's newer revision has it; it arrives only with a reviewed rebase.",
};
const NATURES = [["individual", "An individual"], ["template", "A template"]];
const NOT_OFFERED = { delete: "Deleting", art: "Pictures", import: "Importing", relations: "Typed relations",
  abilities: "Abilities and facets with fields", rebase: "Moving a chat to a newer revision", voice: "A voice" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  const asked = ctx.path[0];
  if (!asked) {
    ctx.setTitle("Source");
    root.append(ui.empty("No source is named."), ui.link("Open the library", "sources"));
    return;
  }
  const read = await ctx.api("sources.read", { ref: asked });
  const pinned = read.entity + "@" + read.revision;
  ctx.setTitle(read.name || read.ref);

  root.append(header(ctx, read, pinned));
  if (read.composition_error) {
    root.append(ui.notice("This revision does not compose, so it cannot be played: " + describe(read.composition_error), { kind: "bad" }));
  }
  if (read.effective) root.append(effective(ctx, read));

  const drafts = draftsOf(ctx, read.entity);
  if (read.is_head) {
    const stale = staleDrafts(ctx, read, drafts);
    if (stale) root.append(stale);
  }
  const structured = structuredEditor(ctx, read, drafts);
  if (structured) root.append(structured);
  const raw = rawEditor(ctx, read, drafts);
  if (raw) root.append(raw);
  const derive = deriveForm(ctx, read, pinned);
  if (derive) root.append(derive);
  root.append(attachForm(ctx, read, pinned));
  root.append(related(ctx, read));
  root.append(notOffered(ctx, read));
}

// -- what is shown --------------------------------------------------------------------------------------

function header(ctx, read, pinned) {
  const { ui } = ctx;
  const { el } = ui;
  const sourceRow = ctx.registered("sources")
    ? el("a", { class: "g-link", href: ctx.href("sources", [], { source: read.source }) }, read.source_title || read.source)
    : el("span", {}, read.source_title || read.source);
  const facts = el("dl", { class: "source-facts" },
    el("dt", {}, "Exact ref"), el("dd", { class: "g-ref" }, pinned),
    el("dt", {}, "Source"), el("dd", {}, sourceRow, " ", el("span", { class: "g-muted" }, read.source)),
    el("dt", {}, "Kind"), el("dd", {}, read.kind + (read.family ? " (" + read.family + ")" : "") + (read.derived ? ", a derived portrayal" : "")),
    el("dt", {}, "Revision"), el("dd", {}, read.revision + (read.is_head ? ", the head" : ", an older revision")));
  const revisions = el("div", { class: "g-actions", role: "group", "aria-label": "Revisions" },
    read.revisions.map((revision) => {
      const node = el("a", { class: "g-button", href: ctx.href("source", [read.entity + "@" + revision]),
        title: read.entity + "@" + revision }, revision);
      if (revision === read.revision) node.setAttribute("aria-current", "page");
      return node;
    }));
  const box = ui.section(null, facts, revisions);
  if (!read.is_head) {
    box.append(ui.notice("This is " + read.revision + ", shown exactly as it was written. Only the head, " + read.head
      + ", is edited; this revision can still be attached as it is."),
    ui.actions(el("a", { class: "g-button", href: ctx.href("source", [read.entity]) }, "Show the head")));
  }
  if (read.derived && read.base) {
    box.append(el("p", {}, "Derived from ", ui.sourceLink(read.base, read.base), "."));
  }
  return box;
}

function effective(ctx, read) {
  const { ui } = ctx;
  const { el } = ui;
  const view = read.effective;
  const editable = read.derived && read.is_head && read.actions.portrayal === null && ctx.has("sources.portrayal");
  const rows = view.fields.map((item) => {
    const row = el("div", { class: "source-field" },
      el("div", { class: "source-field-head" }, el("strong", {}, label(item.field)), " ",
        el("span", { class: "source-state source-state-" + item.state }, item.state), " ",
        el("span", { class: "g-muted" }, STATES[item.state] || "")),
      el("div", { class: "source-value" }, shown(item.value)),
      item.source ? el("div", { class: "g-muted" }, "From ", ui.sourceLink(item.source, item.source)) : null,
      UPSTREAM[item.upstream] ? el("div", { class: "g-muted" }, UPSTREAM[item.upstream]) : null);
    if (editable) {
      const tools = portrayalTools(ctx, read, item);
      if (tools) row.append(tools);
    }
    return row;
  });
  const box = ui.section(read.derived ? "What this portrayal shows" : "Fields", ...rows);
  if (view.chain && view.chain.length > 1) {
    box.append(el("p", { class: "g-muted" }, "Composed from ",
      ...view.chain.flatMap((link, index) => [index ? " over " : "", ui.sourceLink(link.ref, link.ref)]), "."));
  }
  if (view.base_head) {
    box.append(ui.notice("The base has a newer revision, " + view.base_head + ". This portrayal stays pinned to "
      + view.base + " until a reviewed rebase."));
  }
  for (const notice of view.notices || []) box.append(ui.notice(describe(notice)));
  if (view.uncomposed && view.uncomposed.length) {
    box.append(el("p", { class: "g-muted" }, "Kept but not composed: "
      + view.uncomposed.map((item) => item.field).join(", ") + " (kept for provenance; the raw editor shows them)."));
  }
  return box;
}

function label(field) {
  if (field.startsWith("section:")) return "Section " + field.slice(8);
  if (field.startsWith("facet:")) return "Facet " + field.slice(6);
  return field[0].toUpperCase() + field.slice(1);
}

function shown(value) {
  if (value === null || value === undefined) return "(nothing)";
  return typeof value === "string" ? value : JSON.stringify(value);
}


function describe(item) {
  if (typeof item === "string") return item;
  if (item && item.reason) return item.reason + (item.message ? ": " + item.message : "");
  return JSON.stringify(item);
}

function related(ctx, read) {
  const { ui } = ctx;
  const { el } = ui;
  const box = ui.section("Where it is used");
  const chats = read.chats.map((chat) => {
    const title = chat.label || chat.chat;
    const detail = "binds " + chat.revisions.join(", ") + (chat.active ? "" : " (detached)") + (chat.problem ? " · " + chat.problem : "");
    return ctx.registered("chat") ? el("a", { href: ctx.href("chat", [chat.chat]), title: chat.chat }, ui.rowText(title, detail))
      : el("div", { title: chat.chat }, ui.rowText(title, detail));
  });
  box.append(el("h3", {}, "Chats"), ui.list(chats, { empty: "No chat binds it." }));
  if (read.portrayals.length) {
    box.append(el("h3", {}, "Portrayals derived from it"), ui.list(read.portrayals.map((ref) => ui.sourceLink(ref, ref))));
  }
  if (read.namesakes.length) {
    box.append(el("h3", {}, "Same name, other entries"),
      el("p", { class: "g-muted" }, "These share its name and are different entries: a name never decides which one a chat uses."),
      ui.list(read.namesakes.map((ref) => ui.sourceLink(ref, ref))));
  }
  if (read.links.length) {
    box.append(el("h3", {}, "Links its source declares"), ui.list(read.links.map((link) =>
      el("span", {}, ui.sourceLink(link.subject, link.subject), " " + link.relation + " ", ui.sourceLink(link.object, link.object),
        el("span", { class: "g-muted" }, " (declared by " + link.declared_by + ")")))));
  }
  return box;
}

function notOffered(ctx, read) {
  const { ui } = ctx;
  const { el } = ui;
  const items = Object.entries(read.unavailable).map(([key, reason]) =>
    el("div", { class: "g-unavailable", role: "note" }, el("strong", {}, NOT_OFFERED[key] || key), el("div", { class: "g-muted" }, reason)));
  return ui.section("Not offered here", ...items);
}

// -- drafts ---------------------------------------------------------------------------------------------

// One draft per entity and editor, kept per tab until it is saved or discarded (see ./drafts.js for its shape).
function draftsOf(ctx, entity) {
  const keep = (name, valid) => {
    const store = ctx.draft("source:" + entity + ":" + name);
    return {
      get() {
        let value = null;
        try { value = JSON.parse(store.get() || "null"); } catch { return null; }
        return value && typeof value === "object" && typeof value.base === "string" && valid(value) ? value : null;
      },
      set(value) { store.set(JSON.stringify(value)); },
      clear() { store.clear(); },
    };
  };
  return {
    form: keep("form", (value) => value.start && value.model && typeof value.start === "object" && typeof value.model === "object"),
    raw: keep("raw", (value) => typeof value.start === "string" && typeof value.text === "string"),
  };
}

// Drafts that started from an older head. One whose changes the head already has (it was saved, and the page left
// before the answer came) is dropped; any other is shown with what changed under it and what it changes, and is
// kept on the new head (its own changes laid over it) or discarded. A part changed on both sides is a conflict:
// the draft's text for it is shown to copy, and the draft cannot be kept as it is.
function staleDrafts(ctx, read, drafts) {
  const { ui } = ctx;
  const { el } = ui;
  const box = ui.section("Your draft started from an older revision");
  let shown = false;
  const form = drafts.form.get();
  if (form && form.base !== read.head && FAMILIES.includes(read.family)) {
    let merged = null;
    try { merged = rebase(read, form); } catch { merged = null; }
    let pending = null;
    try { pending = merged ? fieldsOf(read, merged.model) : null; } catch { pending = { invalid: true }; }
    if (merged && !merged.conflicts.length && pending && !Object.keys(pending).length) drafts.form.clear();
    else {
      shown = true;
      box.append(...staleParts(ctx, read, "edit", form.base, merged && merged.changed, merged ? merged.mine : null,
        merged ? merged.conflicts : null, merged ? conflictText(read, form.model, merged.conflicts) : JSON.stringify(form.model, null, 2),
        merged && !merged.conflicts.length
          ? () => drafts.form.set({ base: read.head, start: modelOf(read), model: merged.model }) : null,
        () => drafts.form.clear()));
    }
  } else if (form && form.base !== read.head) {
    drafts.form.clear();  // a draft of an editor this record no longer has
  }
  const raw = drafts.raw.get();
  if (raw && raw.base !== read.head) {
    const merged = rebaseRaw(read, raw);
    if (!merged.error && !merged.conflicts.length && JSON.stringify(merged.payload) === JSON.stringify(read.payload)) drafts.raw.clear();
    else {
      shown = true;
      if (merged.error) box.append(ui.notice(merged.error, { kind: "bad" }));
      box.append(...staleParts(ctx, read, "raw edit", raw.base, merged.changed || null, merged.mine || null,
        merged.conflicts || null, raw.text,
        !merged.error && !merged.conflicts.length ? () => drafts.raw.set({ base: read.head, start: JSON.stringify(read.payload), text: merged.text }) : null,
        () => drafts.raw.clear()));
    }
  }
  return shown ? box : null;
}

function staleParts(ctx, read, what, base, changed, mine, conflicts, kept, onKeep, onDiscard) {
  const { ui } = ctx;
  const { el } = ui;
  const list = (items) => (items && items.length ? items.join(", ") : "nothing");
  const parts = [el("p", {}, "Your " + what + " started from " + base + "; the head is now " + read.head + "."),
    el("p", {}, "Changed since: " + (changed ? list(changed) : "not known") + "."),
    el("p", { class: "g-muted" }, "Your draft changes: " + (mine ? list(mine) : "not known") + ".")];
  if (conflicts && conflicts.length) {
    parts.push(ui.notice("Changed under your draft as well: " + conflicts.join(", ") + ". Your text for "
      + (conflicts.length === 1 ? "it" : "them") + " is below to copy; then discard the draft and edit the head.", { kind: "bad" }));
  }
  if (!onKeep) {
    const copy = el("textarea", { class: "g-input source-raw", readonly: true, "aria-label": "Your draft" });
    copy.value = kept;
    parts.push(copy);
  }
  parts.push(ui.actions(
    onKeep ? ui.button("Keep my changes on " + read.head, () => { onKeep(); ctx.navigate(ctx.href("source", [read.entity])); }, { kind: "primary" }) : null,
    ui.button("Discard my draft", async () => {
      if (!onKeep && !await ui.confirm("Discard your draft? Copy any text you want to keep first.", { confirmLabel: "Discard it", danger: true })) return;
      onDiscard();
      ctx.navigate(ctx.href("source", [read.entity]));
    })));
  if (onKeep) {
    parts.push(el("p", { class: "g-muted" }, "Keeping lays only your own changes over " + read.head
      + "; everything else stays as the head has it. Nothing is saved until you save."));
  }
  return parts;
}

// The draft's own text for the parts in conflict, for the person to copy.
function conflictText(read, model, conflicts) {
  return JSON.stringify(Object.fromEntries(conflicts.map((part) => [part, partOf(read.family, model, part)])), null, 2);
}

function partOf(family, model, part) {
  if (part in model && typeof model[part] !== "object") return model[part];
  const list = family === "entity" ? model.sections : family === "scenario" ? model.openings : model.entries;
  const match = /^(section|opening|entry) (.+)$/.exec(part);
  if (!match) return null;
  const found = family === "entity" ? list.find((item) => item.name === match[2] || item.slot === match[2])
    : family === "scenario" ? list.find((item) => item.slot === match[2]) || list[Number(match[2]) - 1]
      : list[Number(match[2]) - 1];
  if (!found) return null;
  const { slot: _slot, original: _original, ...shown } = found;
  return shown;
}

// What a save sends, against the head this page read: a refusal "changed" keeps the draft and reads the head again.
async function save(ctx, read, op, args, draft) {
  try {
    const answer = await ctx.api(op, { ref: read.entity, head: read.head, etag: read.etag, expected: read.manifest_etag, ...args });
    draft.clear();
    ctx.notify("Saved as " + answer.ref.split("@").pop() + ".");
    ctx.navigate(ctx.href("source", [read.entity]));
  } catch (error) {
    if (error.reason === "changed") {
      ctx.notify("This entry changed since the page read it. Your draft is kept; check what changed and save again.", { bad: true });
      ctx.navigate(ctx.href("source", [read.entity]));  // the head as it is now, so the draft is held against it
      return;
    }
    throw error;
  }
}

// -- the structured editor ------------------------------------------------------------------------------

function structuredEditor(ctx, read, drafts) {
  const { ui } = ctx;
  const { el } = ui;
  if (!FAMILIES.includes(read.family)) return null;
  if (!read.is_head) return null;
  if (read.actions.save !== null) return ui.section("Edit", el("p", { class: "g-muted" }, read.actions.save));
  if (!ctx.has("sources.save")) return ui.section("Edit", ui.unavailable("Editing", "This app does not serve sources.save."));
  const draft = drafts.form.get();
  if (draft && draft.base !== read.head) return ui.section("Edit", el("p", { class: "g-muted" }, "Keep or discard your draft above first."));
  const start = draft ? draft.start : modelOf(read);
  let model = draft ? draft.model : modelOf(read);
  const keep = () => drafts.form.set({ base: read.head, start, model });
  const body = el("div", {});
  const box = ui.section(read.derived ? "Edit this portrayal" : "Edit", body);
  const render = () => {
    body.replaceChildren(...formFor(ctx, read, model, keep, render));
  };
  render();
  const saveButton = ui.button("Save as a new revision", () => ctx.act(async () => {
    const fields = fieldsOf(read, model);
    if (!Object.keys(fields).length) { ctx.notify("Nothing changed."); return; }
    await save(ctx, read, "sources.save", { fields }, drafts.form);
  }), { kind: "primary" });
  const discard = ui.button("Discard my changes", () => { drafts.form.clear(); model = modelOf(read); render(); });
  box.append(ui.actions(saveButton, discard));
  if (draft && changesOf(read.family, start, model).length) box.prepend(ui.notice("Your unsaved draft is shown."));
  if (read.derived) {
    box.append(el("p", { class: "g-muted" }, "A description or section written here overrides the base's; one left blank, or a section"
      + " removed, inherits again. Clearing an inherited field is done in the list above, never by leaving it blank."));
  }
  if (read.family === "entity") {
    box.append(el("p", { class: "g-muted" }, "Facets are kept as they are; edit them in the raw editor"
      + (read.derived ? ", or clear and reset inherited ones above." : ".")));
  }
  return box;
}

function bound(ctx, labelText, value, onchange, options = {}) {
  const field = ctx.ui.field(labelText, { value, ...options });
  field.input.addEventListener("input", () => onchange(field.value));
  return field.node;
}

const LOCKED = { entity: "sections", scenario: "openings", lorebook: "entries" };

function formFor(ctx, read, model, keep, render) {
  const { ui } = ctx;
  const { el } = ui;
  const set = (target, key) => (value) => { target[key] = value; keep(); };
  const nodes = [];
  const locked = model.locked
    ? el("p", { class: "g-muted" }, "Its " + LOCKED[read.family] + " are not all plain text, so they are edited in the raw editor; "
      + "a save here leaves them exactly as they are.") : null;
  if (read.family === "entity") {
    nodes.push(bound(ctx, "Name", model.name, set(model, "name")));
    const nature = ui.choice("Nature", NATURES.map(([value, labelText]) => ({ value, label: labelText })),
      { value: model.nature, onchange: set(model, "nature") });
    nodes.push(nature.node, bound(ctx, "Description", model.description, set(model, "description"), { multiline: true }));
    if (locked) return [...nodes, locked];
    model.sections.forEach((item, index) => {
      const group = el("fieldset", { class: "source-group" }, el("legend", {}, "Section " + (item.name || index + 1)));
      if (item.slot === null) group.append(bound(ctx, "Section name", item.name, set(item, "name")));
      group.append(bound(ctx, item.slot === null ? "Section text" : "Text", item.text, set(item, "text"), { multiline: true }),
        ui.actions(ui.button("Remove this section", () => { model.sections.splice(index, 1); keep(); render(); })));
      nodes.push(group);
    });
    nodes.push(ui.actions(ui.button("Add a section", () => { model.sections.push({ slot: null, name: "", text: "" }); keep(); render(); })));
  } else if (read.family === "scenario") {
    nodes.push(bound(ctx, "Title", model.title, set(model, "title")),
      bound(ctx, "Premise", model.premise, set(model, "premise"), { multiline: true }),
      bound(ctx, "Instructions", model.instructions, set(model, "instructions"), { multiline: true,
        hint: "Private to the narrator: never shown to the players." }));
    if (locked) return [...nodes, locked];
    model.openings.forEach((item, index) => {
      nodes.push(el("fieldset", { class: "source-group" }, el("legend", {}, "Opening " + (index + 1) + (item.slot ? " (" + item.slot + ")" : "")),
        bound(ctx, "Text", item.text, set(item, "text"), { multiline: true }),
        ui.actions(ui.button("Remove this opening", () => { model.openings.splice(index, 1); keep(); render(); }))));
    });
    nodes.push(ui.actions(ui.button("Add an opening", () => { model.openings.push({ slot: null, text: "" }); keep(); render(); })));
  } else if (read.family === "lorebook") {
    nodes.push(bound(ctx, "Name", model.name, set(model, "name")),
      bound(ctx, "Description", model.description, set(model, "description"), { multiline: true }));
    if (locked) return [...nodes, locked];
    model.entries.forEach((item, index) => {
      nodes.push(el("fieldset", { class: "source-group" }, el("legend", {}, "Entry " + (index + 1)),
        bound(ctx, "Keys", item.keys, set(item, "keys"), { multiline: true, hint: "One key per line. Lore reaches play when a key is mentioned." }),
        bound(ctx, "Content", item.content, set(item, "content"), { multiline: true,
          hint: item.slot === null ? undefined : "Emptying it removes the entry." }),
        checkbox(ctx, "Always in context (constant)", item.constant, set(item, "constant")),
        checkbox(ctx, "Enabled", item.enabled, set(item, "enabled")),
        ui.actions(ui.button("Remove this entry", () => { model.entries.splice(index, 1); keep(); render(); }))));
    });
    nodes.push(ui.actions(ui.button("Add an entry", () => {
      model.entries.push({ slot: null, original: null, keys: "", content: "", constant: false, enabled: true }); keep(); render();
    })));
  }
  return nodes;
}

function checkbox(ctx, labelText, checked, onchange) {
  const { el, id } = ctx.ui;
  const inputId = id("check");
  const input = el("input", { type: "checkbox", id: inputId });
  input.checked = !!checked;
  input.addEventListener("change", () => onchange(input.checked));
  return el("div", { class: "g-field source-check" }, input, el("label", { for: inputId }, labelText));
}

// -- the raw editor -------------------------------------------------------------------------------------

function rawEditor(ctx, read, drafts) {
  const { ui } = ctx;
  const { el } = ui;
  if (!read.is_head) return null;
  if (read.actions.save_raw !== null) return ui.section("Raw payload", el("p", { class: "g-muted" }, read.actions.save_raw));
  if (!ctx.has("sources.save_raw")) return ui.section("Raw payload", ui.unavailable("The raw editor", "This app does not serve sources.save_raw."));
  if (unsafeNumbers(read.payload)) {
    return ui.section("Raw payload", ui.unavailable("The raw editor for this record",
      "It holds a whole number too large for the browser to keep exactly, so saving it here would change that number."));
  }
  const draft = drafts.raw.get();
  if (draft && draft.base !== read.head) return ui.section("Raw payload", el("p", { class: "g-muted" }, "Keep or discard your draft above first."));
  const start = draft ? draft.start : JSON.stringify(read.payload);
  const field = ui.field("Payload (JSON)", { multiline: true, value: draft ? draft.text : JSON.stringify(read.payload, null, 2),
    hint: "Saved exactly as written, unknown fields included" + (read.schema ? ", in " + read.schema : "") + "." });
  field.input.classList.add("source-raw");
  field.input.addEventListener("input", () => drafts.raw.set({ base: read.head, start, text: field.value }));
  const extra = Object.keys((read.envelope && read.envelope.extra) || {}).filter((key) => key !== "schema");
  const saveButton = ui.button("Save raw as a new revision", () => ctx.act(async () => {
    let payload;
    try {
      payload = JSON.parse(field.value);
    } catch (error) {
      field.setError("Not JSON: " + error.message);
      return;
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) { field.setError("The payload must be a JSON object."); return; }
    if (unsafeNumbers(payload)) { field.setError("A whole number here is too large to be kept exactly."); return; }
    field.setError("");
    await save(ctx, read, "sources.save_raw", { payload }, drafts.raw);
  }));
  const discard = ui.button("Discard my changes", () => { drafts.raw.clear(); field.input.value = JSON.stringify(read.payload, null, 2); field.setError(""); });
  const box = ui.section("Raw payload", field.node, ui.actions(saveButton, discard));
  if (extra.length) box.append(el("p", { class: "g-muted" }, "Kept on every new revision as they are: " + extra.join(", ") + "."));
  return box;
}

// -- portrayals -----------------------------------------------------------------------------------------

function portrayalTools(ctx, read, item) {
  const { ui } = ctx;
  const { el } = ui;
  const [kind, name] = item.field.startsWith("section:") ? ["section", item.field.slice(8)]
    : item.field.startsWith("facet:") ? ["facet", item.field.slice(6)] : [item.field, undefined];
  if (!["section", "facet", "description"].includes(kind)) return null;
  const inherited = item.state === "inherited" || item.state === "pinned";
  const own = item.state === "overridden" || item.state === "restated";
  const what = (kind === "description" ? "the description" : kind + " " + name) + " of " + (read.name || read.entity);
  const run = (edit, extra = {}, draft = { clear() {} }) => ctx.act(async () => {
    const args = { edit, ...extra };
    if (name !== undefined) args.name = name;
    await save(ctx, read, "sources.portrayal", args, draft);  // the draft is cleared only once it is saved
  });
  const tools = [];
  if (inherited && kind !== "facet") {
    const draft = ctx.draft("source:" + read.entity + ":override:" + item.field);
    const field = ui.field("Override " + (kind === "description" ? "the description" : "section " + name),
      { multiline: true, draft, value: typeof item.value === "string" ? item.value : "" });
    tools.push(el("details", { class: "source-override" }, el("summary", {}, "Override"), field.node,
      ui.actions(ui.button("Save the override", () => {
        if (!field.value.trim()) { field.setError("An override needs text. To stop showing it, clear it instead."); return; }
        field.setError("");
        return run("override_" + kind, { text: field.value }, draft);
      }, { kind: "primary" }))));
  }
  if (inherited) {
    tools.push(ui.button("Clear", async () => {
      const yes = await ui.confirm("Clear " + what + "? The new revision stops showing it; the base keeps it, and Reset brings it back.",
        { confirmLabel: "Clear it", danger: true });
      if (yes) await run("clear_" + kind);
    }));
  }
  if (own || item.state === "cleared") {
    tools.push(ui.button("Reset", async () => {
      const yes = await ui.confirm("Reset " + what + " to inherit from the base again? "
        + (item.state === "cleared" ? "The clear is lifted." : "The override is dropped from the new revision; " + read.revision + " keeps it."),
      { confirmLabel: "Reset it" });
      if (yes) await run("reset_" + kind);
    }));
  }
  return tools.length ? ui.actions(...tools) : null;
}

function deriveForm(ctx, read, pinned) {
  const { ui } = ctx;
  const { el } = ui;
  if (read.family !== "entity") return null;
  if (read.actions.derive !== null) return ui.section("Derive a portrayal", el("p", { class: "g-muted" }, read.actions.derive));
  if (!ctx.has("sources.derive") || !ctx.has("sources.catalog")) return null;
  const box = ui.section("Derive a portrayal");
  box.append(el("p", { class: "g-muted" }, "A portrayal inherits every field of exactly " + pinned
    + " and overrides or clears only what you change. A newer revision of the base reaches it only through a reviewed rebase."));
  const name = ui.field("Name (optional)", { draft: ctx.draft("source:" + read.entity + ":derive:name") });
  const holder = el("div", {});
  box.append(holder, name.node);
  const load = ui.button("Choose where it goes", () => ctx.act(async () => {
    const catalog = await ctx.api("sources.catalog", { limit: 1 });
    const targets = catalog.sources.filter((source) => source.namespace !== "card" && source.namespace !== "import");
    if (!targets.length) { holder.replaceChildren(ui.notice("Make a source in the library first, such as a world.")); return; }
    const picked = ui.choice("Source", targets.map((source) => ({ value: source.source, label: (source.title || source.source) + " (" + source.source + ")" })));
    const go = ui.button("Derive from " + read.revision, () => ctx.act(async () => {
      const target = targets.find((source) => source.source === picked.value);
      const args = { base: pinned, source: target.source, expected: target.etag };
      if (name.value.trim()) args.name = name.value.trim();
      try {
        const answer = await ctx.api("sources.derive", args);
        name.clear();
        ctx.navigate(ctx.href("source", [answer.ref]));
      } catch (error) {
        if (error.reason === "changed") { ctx.notify("That source changed since it was listed; choose it again.", { bad: true }); holder.replaceChildren(); return; }
        throw error;
      }
    }), { kind: "primary" });
    holder.replaceChildren(picked.node, ui.actions(go));
  }));
  box.append(ui.actions(load));
  return box;
}

// -- attaching ------------------------------------------------------------------------------------------

function attachForm(ctx, read, pinned) {
  const { ui } = ctx;
  const { el } = ui;
  const box = ui.section("Attach to a chat");
  if (read.actions.attach !== null) { box.append(el("p", { class: "g-muted" }, read.actions.attach)); return box; }
  if (!ctx.has("sources.attach") || !ctx.has("sources.chats")) { box.append(ui.unavailable("Attaching", "This app does not serve sources.attach.")); return box; }
  box.append(el("p", { class: "g-muted" }, "Attaches exactly " + pinned + ". The chat keeps that revision until a reviewed rebase."));
  const holder = el("div", {});
  box.append(holder);
  const load = ui.button("Choose a chat", () => ctx.act(() => listChats(ctx, read, pinned, holder)));
  box.append(ui.actions(load));
  return box;
}

async function listChats(ctx, read, pinned, holder, keep) {
  const { ui } = ctx;
  const { el } = ui;
  const answer = await ctx.api("sources.chats", { limit: 50 });
  if (!answer.chats.length) { holder.replaceChildren(ui.empty("No chat to attach it to yet.")); return; }
  const picked = ui.choice("Chat", answer.chats.map((chat) => ({ value: chat.chat, label: (chat.label || chat.chat) + " · " + chat.last_activity })),
    { value: keep || answer.chats[0].chat });
  const go = ui.button("Attach " + read.revision, () => ctx.act(async () => {
    const chat = answer.chats.find((item) => item.chat === picked.value);
    const joins = read.kind === "character" ? " It joins as a new actor that has witnessed nothing so far." : "";
    const yes = await ui.confirm("Attach " + (read.name || read.entity) + " at exactly " + pinned + " to " + (chat.label || chat.chat) + "?" + joins,
      { confirmLabel: "Attach " + read.revision });
    if (!yes) return;
    let done;
    try {
      done = await ctx.api("sources.attach", { ref: pinned, chat: chat.chat, expected: chat.etag });
    } catch (error) {
      if (error.reason === "changed") {
        await listChats(ctx, read, pinned, holder, chat.chat);
        holder.prepend(ui.notice("That chat changed since it was listed; it is read again. Attach again to use it as it is now.", { kind: "bad" }));
        return;
      }
      throw error;
    }
    const where = ctx.registered("chat") ? el("a", { class: "g-link", href: ctx.href("chat", [done.chat]) }, "Open the chat") : null;
    holder.replaceChildren(ui.notice("Attached " + done.ref + " to " + (chat.label || chat.chat) + " as its " + done.role + "."), where || "");
    ctx.notify("Attached " + done.ref + ".");
  }), { kind: "primary" });
  holder.replaceChildren(picked.node, ui.actions(go));
}
