// One source entry (109): #/source/<SourceRef>. A pinned ref shows exactly that revision; an unpinned one shows
// the head. It shows what the revision composes to field by field (046), edits the head with drafts that survive
// a stale write (the etag the page read is named on every save), derives portrayals, overrides, clears and resets
// their fields, and attaches the exact revision shown to a chat. Every edit writes a new revision: nothing here
// changes or deletes an old one.

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
    const stale = await staleDraft(ctx, read, drafts);
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

// One draft per entity and editor, kept until it is saved or discarded: { base: the head it started from, ... }.
function draftsOf(ctx, entity) {
  const keep = (name) => {
    const store = ctx.draft("source:" + entity + ":" + name);
    return {
      get() {
        let value = null;
        try { value = JSON.parse(store.get() || "null"); } catch { return null; }
        return value && typeof value === "object" && typeof value.base === "string" ? value : null;
      },
      set(value) { store.set(JSON.stringify(value)); },
      clear() { store.clear(); },
    };
  };
  return { form: keep("form"), raw: keep("raw") };
}

// A draft that started from an older head: say what changed under it, and let it be saved over the new head or
// discarded. Nothing is merged silently.
async function staleDraft(ctx, read, drafts) {
  const { ui } = ctx;
  const { el } = ui;
  const found = [["form", drafts.form.get()], ["raw", drafts.raw.get()]].filter(([, draft]) => draft && draft.base && draft.base !== read.head);
  if (!found.length) return null;
  const box = ui.section("Your draft started from an older revision");
  for (const [which, draft] of found) {
    let before = null;
    try {
      before = await ctx.api("sources.read", { ref: read.entity + "@" + draft.base });
    } catch (error) {
      if (error.reason === "left") throw error;
    }
    const changed = before ? changedKeys(before.payload, read.payload) : null;
    const mine = which === "form" ? editedKeys(read, draft.model) : ["the whole payload"];
    box.append(el("p", {}, "Your " + (which === "form" ? "edit" : "raw edit") + " started from " + draft.base
      + "; the head is now " + read.head + "."),
    changed === null ? ui.notice("The revision it started from could not be read, so what changed is not shown.")
      : el("p", {}, changed.length ? "Changed since: " + changed.join(", ") + "." : "Nothing in the payload changed since."),
    el("p", { class: "g-muted" }, "Your draft changes: " + (mine.length ? mine.join(", ") : "nothing") + "."),
    ui.actions(
      ui.button("Keep my draft on " + read.head, () => {
        drafts[which].set({ ...draft, base: read.head });
        ctx.navigate(window.location.hash);
      }, { kind: "primary" }),
      ui.button("Discard my draft", () => { drafts[which].clear(); ctx.navigate(window.location.hash); })));
  }
  box.append(el("p", { class: "g-muted" }, "Keeping it shows your draft in the editor below over " + read.head
    + "; saving it then writes a new revision with your values for the fields it changes."));
  return box;
}

function changedKeys(before, after) {
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  return [...keys].filter((key) => JSON.stringify(before[key]) !== JSON.stringify(after[key])).sort();
}

// What a save sends: whoever is editing an outdated draft is told so before this runs (staleDraft above).
async function save(ctx, read, op, args, draft) {
  try {
    const answer = await ctx.api(op, { ref: read.entity, head: read.head, etag: read.etag, expected: read.manifest_etag, ...args });
    draft.clear();
    ctx.notify("Saved as " + answer.ref.split("@").pop() + ".");
    ctx.navigate(ctx.href("source", [read.entity]));
  } catch (error) {
    if (error.reason === "changed") {  // the draft is kept: read again, and the page says what changed
      ctx.notify("This entry changed since the page read it. Your draft is kept; check what changed and save again.", { bad: true });
      ctx.navigate(ctx.href("source", [read.entity]));  // the head as it is now, so the draft is held against it
      return;
    }
    throw error;
  }
}

// -- the structured editor ------------------------------------------------------------------------------

function modelOf(read) {
  const payload = read.payload || {};
  if (read.family === "entity") {
    const sections = payload.sections && typeof payload.sections === "object" ? payload.sections : {};
    return { name: payload.name || "", nature: payload.nature || "individual", description: text(payload.description),
      sections: Object.entries(sections).map(([name, value]) => ({ name, text: text(value), kept: true })) };
  }
  if (read.family === "scenario") {
    return { title: text(payload.title), premise: text(payload.premise), instructions: text(payload.instructions),
      openings: (Array.isArray(payload.openings) ? payload.openings : []).map((item) => ({ id: item && item.id, text: text(item && item.text) })) };
  }
  if (read.family === "lorebook") {
    return { name: text(payload.name), description: text(payload.description),
      entries: (Array.isArray(payload.entries) ? payload.entries : []).map((entry) => ({
        original: JSON.stringify(entry), keys: (Array.isArray(entry && entry.keys) ? entry.keys : []).join("\n"),
        content: text(entry && entry.content), constant: !!(entry && entry.settings && entry.settings.constant === true),
        enabled: !(entry && entry.enabled === false) })) };
  }
  return null;
}

function text(value) { return typeof value === "string" ? value : ""; }

// The fields a save sends: only what the draft changed from the head, so everything else stays exactly as it is.
function fieldsOf(read, model) {
  const head = modelOf(read);
  const fields = {};
  const differs = (key) => JSON.stringify(model[key]) !== JSON.stringify(head[key]);
  if (read.family === "entity") {
    for (const key of ["name", "nature", "description"]) if (differs(key)) fields[key] = model[key];
    const sections = (items) => JSON.stringify(items.map((item) => [item.name, item.text]));
    if (sections(model.sections) !== sections(head.sections)) {
      fields.sections = {};
      for (const item of model.sections) {
        const name = item.name.trim();
        if (!name) {
          if (item.text.trim()) throw new Error("A new section needs a name.");
          continue;
        }
        if (name in fields.sections) throw new Error("Two sections are named " + JSON.stringify(name) + ".");
        fields.sections[name] = item.text;
      }
    }
  } else if (read.family === "scenario") {
    for (const key of ["title", "premise", "instructions"]) if (differs(key)) fields[key] = model[key];
    if (differs("openings")) {
      fields.openings = model.openings.filter((item) => item.text.trim())
        .map((item) => (item.id ? { id: item.id, text: item.text } : { text: item.text }));
    }
  } else if (read.family === "lorebook") {
    for (const key of ["name", "description"]) if (differs(key)) fields[key] = model[key];
    if (differs("entries")) fields.entries = entriesOf(read, model);
  }
  return fields;
}

// Each entry the draft kept from the head names that entry's index in the head, matched by its exact content,
// so its settings and extras stay. An entry that changed or went away under the draft is not guessed at.
function entriesOf(read, model) {
  const head = Array.isArray(read.payload.entries) ? read.payload.entries.map((entry) => JSON.stringify(entry)) : [];
  const used = new Set();
  const lost = [];
  const entries = [];
  model.entries.forEach((item, number) => {
    if (!item.content.trim()) return;  // an emptied entry is removed
    const entry = { keys: item.keys.split("\n").map((key) => key.trim()).filter(Boolean), content: item.content,
      constant: item.constant, enabled: item.enabled };
    if (item.original) {
      const index = head.findIndex((original, at) => original === item.original && !used.has(at));
      if (index < 0) { lost.push(number + 1); return; }
      used.add(index);
      entry.index = index;
    }
    entries.push(entry);
  });
  if (lost.length) {
    const error = new Error("Entr" + (lost.length === 1 ? "y " : "ies ") + lost.join(", ")
      + " changed or went away in the head since your draft; discard the draft or copy your text into a new entry.");
    error.reason = "entry_changed";
    throw error;
  }
  return entries;
}

function editedKeys(read, model) {
  if (!model) return [];
  try { return Object.keys(fieldsOf(read, model)); } catch { return ["entries"]; }
}

function structuredEditor(ctx, read, drafts) {
  const { ui } = ctx;
  const { el } = ui;
  if (!["entity", "scenario", "lorebook"].includes(read.family)) return null;
  if (!read.is_head) return null;
  if (read.actions.save !== null) return ui.section("Edit", el("p", { class: "g-muted" }, read.actions.save));
  if (!ctx.has("sources.save")) return ui.section("Edit", ui.unavailable("Editing", "This app does not serve sources.save."));
  const draft = drafts.form.get();
  if (draft && draft.base !== read.head) return ui.section("Edit", el("p", { class: "g-muted" }, "Keep or discard your draft above first."));
  const editing = draft;
  let model = editing ? editing.model : modelOf(read);
  const keep = () => drafts.form.set({ base: read.head, model });
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

function formFor(ctx, read, model, keep, render) {
  const { ui } = ctx;
  const { el } = ui;
  const set = (target, key) => (value) => { target[key] = value; keep(); };
  const nodes = [];
  if (read.family === "entity") {
    nodes.push(bound(ctx, "Name", model.name, set(model, "name")));
    const nature = ui.choice("Nature", NATURES.map(([value, labelText]) => ({ value, label: labelText })),
      { value: model.nature, onchange: set(model, "nature") });
    nodes.push(nature.node, bound(ctx, "Description", model.description, set(model, "description"), { multiline: true }));
    model.sections.forEach((item, index) => {
      const group = el("fieldset", { class: "source-group" }, el("legend", {}, "Section " + (item.name || index + 1)));
      if (!item.kept) group.append(bound(ctx, "Section name", item.name, set(item, "name")));
      group.append(bound(ctx, item.kept ? "Text" : "Section text", item.text, set(item, "text"), { multiline: true }),
        ui.actions(ui.button("Remove this section", () => { model.sections.splice(index, 1); keep(); render(); })));
      nodes.push(group);
    });
    nodes.push(ui.actions(ui.button("Add a section", () => { model.sections.push({ name: "", text: "", kept: false }); keep(); render(); })));
  } else if (read.family === "scenario") {
    nodes.push(bound(ctx, "Title", model.title, set(model, "title")),
      bound(ctx, "Premise", model.premise, set(model, "premise"), { multiline: true }),
      bound(ctx, "Instructions", model.instructions, set(model, "instructions"), { multiline: true,
        hint: "Private to the narrator: never shown to the players." }));
    model.openings.forEach((item, index) => {
      nodes.push(el("fieldset", { class: "source-group" }, el("legend", {}, "Opening " + (index + 1) + (item.id ? " (" + item.id + ")" : "")),
        bound(ctx, "Text", item.text, set(item, "text"), { multiline: true }),
        ui.actions(ui.button("Remove this opening", () => { model.openings.splice(index, 1); keep(); render(); }))));
    });
    nodes.push(ui.actions(ui.button("Add an opening", () => { model.openings.push({ id: null, text: "" }); keep(); render(); })));
  } else if (read.family === "lorebook") {
    nodes.push(bound(ctx, "Name", model.name, set(model, "name")),
      bound(ctx, "Description", model.description, set(model, "description"), { multiline: true }));
    model.entries.forEach((item, index) => {
      nodes.push(el("fieldset", { class: "source-group" }, el("legend", {}, "Entry " + (index + 1)),
        bound(ctx, "Keys", item.keys, set(item, "keys"), { multiline: true, hint: "One key per line. Lore reaches play when a key is mentioned." }),
        bound(ctx, "Content", item.content, set(item, "content"), { multiline: true }),
        checkbox(ctx, "Always in context (constant)", item.constant, set(item, "constant")),
        checkbox(ctx, "Enabled", item.enabled, set(item, "enabled")),
        ui.actions(ui.button("Remove this entry", () => { model.entries.splice(index, 1); keep(); render(); }))));
    });
    nodes.push(ui.actions(ui.button("Add an entry", () => {
      model.entries.push({ original: null, keys: "", content: "", constant: false, enabled: true }); keep(); render();
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
  const draft = drafts.raw.get();
  if (draft && draft.base !== read.head) return ui.section("Raw payload", el("p", { class: "g-muted" }, "Keep or discard your draft above first."));
  const editing = draft;
  const field = ui.field("Payload (JSON)", { multiline: true, value: editing ? editing.text : JSON.stringify(read.payload, null, 2),
    hint: "Saved exactly as written, unknown fields included" + (read.schema ? ", in " + read.schema : "") + "." });
  field.input.classList.add("source-raw");
  field.input.addEventListener("input", () => drafts.raw.set({ base: read.head, text: field.value }));
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
  if ((own && kind !== "facet") || item.state === "cleared") {
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
