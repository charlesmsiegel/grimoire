// The source page's Edit and Raw tabs (109), and the drafts they keep. Every save writes a new revision against
// the head this page read (its etag is named on the save): a refusal "changed" keeps the draft and reads the head
// again, where the draft is held against it. What a draft changes, and how it is laid over a newer head, is
// ./drafts.js.

import { FAMILIES, addedId, changesOf, draftParts, fieldsOf, modelOf, rebase, rebaseRaw, stale, unsafeNumbers } from "./drafts.js";
import { nextVersion } from "./sheet.js";

const NATURES = [["individual", "An individual"], ["template", "A template"]];
const LOCKED = { entity: "sections", scenario: "openings", lorebook: "entries" };
const KEEPS = "Older versions never change, and chats keep the one they started with.";

// One draft per entity and editor, kept per tab until it is saved or discarded (see ./drafts.js for its shape).
export function draftsOf(ctx, entity) {
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
      // The draft as stored now, and a clear that happens only if it is still that: what is typed while a save is
      // on its way is kept, and is held against the new head like any other draft.
      snapshot() { return store.get(); },
      clearIf(snapshot) { if (store.get() !== snapshot) return false; store.clear(); return true; },
    };
  };
  return {
    form: keep("form", (value) => value.start && value.model && typeof value.start === "object" && typeof value.model === "object"),
    raw: keep("raw", (value) => typeof value.start === "string" && typeof value.text === "string"),
  };
}

// Which editors hold an unsaved draft of this head: ["edit", "raw"], for the note above the tabs.
export function unsaved(read, drafts) {
  const found = [];
  if (!read.is_head) return found;
  const form = drafts.form.get();
  if (form && !stale(form, read) && FAMILIES.includes(read.family)) {
    try { if (changesOf(read.family, form.start, form.model).length) found.push("edit"); } catch { /* not a draft of this editor */ }
  }
  const raw = drafts.raw.get();
  if (raw && !stale(raw, read)) {
    let same = false;
    try { same = JSON.stringify(JSON.parse(raw.text)) === raw.start; } catch { same = false; }
    if (!same) found.push("raw");
  }
  return found;
}

// Drafts that started from an older head. One whose changes the head already has (it was saved, and the page left
// before the answer came) is dropped; any other is shown with what changed under it and what it changes, and is
// kept on the new head (its own changes laid over it) or discarded. A part changed on both sides is a conflict:
// the draft's text for it is shown to copy, and the draft cannot be kept as it is.
export function staleDrafts(ctx, read, drafts, address) {
  const { ui } = ctx;
  const { el } = ui;
  const box = ui.card({ kind: "accent", tag: "section", label: "Your draft started from an older version" },
    el("h2", { class: "g-heading" }, "Your draft started from an older version"));
  box.classList.add("source-stale");
  let shown = false;
  const form = drafts.form.get();
  if (form && stale(form, read) && FAMILIES.includes(read.family)) {
    let merged = null;
    try { merged = rebase(read, form); } catch { merged = null; }
    let pending = null;
    try { pending = merged ? fieldsOf(read, merged.model) : null; } catch { pending = { invalid: true }; }
    if (merged && !merged.conflicts.length && pending && !Object.keys(pending).length) drafts.form.clear();
    else {
      shown = true;
      box.append(...staleParts(ctx, read, "edit", form, merged && merged.changed, merged ? merged.mine : null,
        merged ? merged.conflicts : null, JSON.stringify(draftParts(read.family, form.start, form.model), null, 2),
        merged && !merged.conflicts.length
          ? () => drafts.form.set({ base: read.head, etag: read.etag, start: modelOf(read), model: merged.model }) : null,
        () => drafts.form.clear(), address("edit")));
    }
  } else if (form && stale(form, read)) {
    drafts.form.clear();  // a draft of an editor this record no longer has
  }
  const raw = drafts.raw.get();
  if (raw && stale(raw, read)) {
    const merged = rebaseRaw(read, raw);
    if (!merged.error && !merged.conflicts.length && JSON.stringify(merged.payload) === JSON.stringify(read.payload)) drafts.raw.clear();
    else {
      shown = true;
      if (merged.error) box.append(ui.notice(merged.error, { kind: "bad" }));
      box.append(...staleParts(ctx, read, "raw edit", raw, merged.changed || null, merged.mine || null,
        merged.conflicts || null, raw.text,
        !merged.error && !merged.conflicts.length ? () => drafts.raw.set({ base: read.head, etag: read.etag, start: JSON.stringify(read.payload), text: merged.text }) : null,
        () => drafts.raw.clear(), address("raw")));
    }
  }
  return shown ? box : null;
}

function staleParts(ctx, read, what, draft, changed, mine, conflicts, kept, onKeep, onDiscard, editor) {
  const { ui } = ctx;
  const { el } = ui;
  const list = (items) => (items && items.length ? items.join(", ") : "nothing");
  const parts = [el("p", {}, draft.base === read.head
    ? "Your " + what + " started from " + read.head + " as it was then; " + read.head + " has been changed in place since."
    : "Your " + what + " started from " + draft.base + "; the head is now " + read.head + "."),
    el("p", {}, "Changed since: " + (changed ? list(changed) : "not known") + "."),
    el("p", { class: "g-muted" }, "Your draft changes: " + (mine ? list(mine) : "not known") + ".")];
  if (conflicts && conflicts.length) {
    parts.push(ui.notice("Changed under your draft as well: " + conflicts.join(", ") + ". Everything your draft changes is below to copy; then discard the draft and edit the current version.", { kind: "bad" }));
  }
  if (!onKeep) {
    const copy = el("textarea", { class: "g-input source-raw", readonly: true, "aria-label": "Your draft" });
    copy.value = kept;
    parts.push(copy);
  }
  parts.push(ui.actions(
    onKeep ? ui.button("Keep my changes on " + read.head, () => { onKeep(); ctx.navigate(editor); }, { kind: "primary" }) : null,
    ui.button("Discard my draft", async () => {
      if (!onKeep && !await ui.confirm("Discard your draft? Copy any text you want to keep first.", { confirmLabel: "Discard it", danger: true })) return;
      onDiscard();
      ctx.navigate(editor);
    })));
  if (onKeep) {
    parts.push(el("p", { class: "g-muted" }, "Keeping lays only your own changes over " + read.head
      + "; everything else stays as the current version has it. Nothing is saved until you save."));
  }
  return parts;
}

// What a save sends, against the head this page read: a refusal "changed" keeps the draft and reads the head again.
export async function save(ctx, read, op, args, draft, onKept = () => {}) {
  try {
    const sent = draft.snapshot ? draft.snapshot() : null;
    const answer = await ctx.api(op, { ref: read.entity, head: read.head, etag: read.etag, expected: read.manifest_etag, ...args });
    if (!draft.clearIf) draft.clear();
    else if (!draft.clearIf(sent)) onKept();
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

// The bar an editor ends in: what a save does, Discard and the save itself. It stays in view as the form scrolls.
function saveBar(ctx, read, label, onSave, onDiscard) {
  const { ui } = ctx;
  const next = nextVersion(read);
  return ui.el("div", { class: "source-savebar" },
    ui.el("span", { class: "g-muted g-grow" }, "Saves as version " + next + ". " + KEEPS),
    ui.button("Discard", onDiscard),
    ui.button(label + " " + next, onSave, { kind: "primary" }));
}

// -- the Edit tab ---------------------------------------------------------------------------------------

export function editTab(ctx, read, drafts, address) {
  const { ui } = ctx;
  const { el } = ui;
  const box = el("div", { class: "source-edit g-stack" });
  const say = (...nodes) => { box.append(...nodes); return box; };
  if (!read.is_head) {
    return say(el("p", { class: "g-muted" }, read.actions.save_raw || "Only the current version is edited."),
      ui.actions(el("a", { class: "g-button", href: ctx.href("source", [read.entity], { tab: "edit" }) }, "Edit the current version")));
  }
  if (!FAMILIES.includes(read.family) || read.actions.save !== null) {
    return say(el("p", { class: "g-muted" }, read.actions.save || "This record has no structured editor."),
      ui.actions(el("a", { class: "g-button", href: address("raw") }, "Open the raw editor")));
  }
  if (!ctx.has("sources.save")) return say(ui.unavailable("Editing", "This app does not serve sources.save."));
  const draft = drafts.form.get();
  if (draft && stale(draft, read)) return say(el("p", { class: "g-muted" }, "Keep or discard your draft above first."));
  const start = draft ? draft.start : modelOf(read);
  let model = draft ? draft.model : modelOf(read);
  const keep = () => drafts.form.set({ base: read.head, etag: read.etag, start, model });
  const body = el("div", { class: "g-stack" });
  const render = () => { body.replaceChildren(...formFor(ctx, read, model, keep, render)); };
  render();
  if (draft && changesOf(read.family, start, model).length) box.append(ui.notice("Your unsaved draft is shown."));
  box.append(body);
  if (read.derived) {
    box.append(el("p", { class: "g-muted" }, "A description or section written here overrides the base's; one left blank, or a section"
      + " removed, inherits again. Clearing an inherited field is done on the Sheet tab, never by leaving it blank."));
  }
  if (read.family === "entity") {
    box.append(el("p", { class: "g-muted" }, "Facets are kept as they are; edit them on the Raw tab"
      + (read.derived ? ", or clear and reset inherited ones on the Sheet tab." : ".")));
  }
  box.append(saveBar(ctx, read, "Save as version", () => ctx.act(async () => {
    const fields = fieldsOf(read, model);
    if (!Object.keys(fields).length) { ctx.notify("Nothing changed."); return; }
    // the items this save adds, by the draft's own id for each: if the draft is edited again before the answer, it
    // is kept, and these tell its next rebase that they landed (see ./drafts.js)
    const list = model.sections || model.openings || model.entries;
    const adding = Object.fromEntries(list.filter((item) => item.slot === null && item.added).map((item) => [item.added, { ...item }]));
    await save(ctx, read, "sources.save", { fields }, drafts.form, () => {
      const kept = drafts.form.get();
      if (kept && Object.keys(adding).length) drafts.form.set({ ...kept, sent: { ...(kept.sent || {}), ...adding } });
    });
  }), () => { drafts.form.clear(); model = modelOf(read); render(); }));
  return box;
}

function bound(ctx, labelText, value, onchange, options = {}) {
  const field = ctx.ui.field(labelText, { value, ...options });
  field.input.addEventListener("input", () => onchange(field.value));
  if (options.prose) field.input.classList.add("source-prose-input");
  return field.node;
}

// One section, opening or entry of the form: a card with its name and Remove in a strip over its fields.
function part(ctx, title, onRemove, removeLabel, ...children) {
  const { ui } = ctx;
  const { el } = ui;
  return ui.card({ kind: "flush" }, el("div", { class: "g-card-head" },
    typeof title === "string" ? el("span", { class: "g-eyebrow" }, title) : title,
    ui.button("Remove", onRemove, { kind: "linklike", size: "small", title: removeLabel })),
  el("div", { class: "source-part-body", role: "group", "aria-label": removeLabel.replace(/^Remove /, "") }, ...children));
}

// A text box with no visible label of its own (its card's strip names it).
function area(ctx, label, value, onchange, placeholder) {
  const node = ctx.ui.el("textarea", { class: "g-input source-prose-input", "aria-label": label, placeholder });
  node.value = value;
  node.addEventListener("input", () => onchange(node.value));
  return node;
}

function formFor(ctx, read, model, keep, render) {
  const { ui } = ctx;
  const { el } = ui;
  const set = (target, key) => (value) => { target[key] = value; keep(); };
  const nodes = [];
  const locked = model.locked
    ? el("p", { class: "g-muted" }, "Its " + LOCKED[read.family] + " are not all plain text, so they are edited on the Raw tab; "
      + "a save here leaves them exactly as they are.") : null;
  const heading = (text) => el("div", { class: "source-edit-heading" }, text);
  if (read.family === "entity") {
    nodes.push(bound(ctx, "Name", model.name, set(model, "name")));
    const nature = ui.choice("Nature", NATURES.map(([value, labelText]) => ({ value, label: labelText })),
      { value: model.nature, onchange: set(model, "nature") });
    nodes.push(nature.node, bound(ctx, "Description", model.description, set(model, "description"), { multiline: true, prose: true }));
    if (locked) return [...nodes, locked];
    nodes.push(heading("Sections"));
    model.sections.forEach((item, index) => {
      const remove = () => { model.sections.splice(index, 1); keep(); render(); };
      if (item.slot === null) {
        const name = el("input", { class: "g-input source-part-name", type: "text", "aria-label": "Section name", placeholder: "Section name",
          autocomplete: "off" });
        name.value = item.name;
        name.addEventListener("input", () => set(item, "name")(name.value));
        nodes.push(part(ctx, name, remove, "Remove this new section", area(ctx, "Section text", item.text, set(item, "text"))));
      } else {
        nodes.push(part(ctx, item.name, remove, "Remove section " + item.name, area(ctx, "Section " + item.name, item.text, set(item, "text"))));
      }
    });
    nodes.push(ui.actions(ui.button("Add a section", () => { model.sections.push({ slot: null, added: addedId(), name: "", text: "" }); keep(); render(); },
      { kind: "dashed" })));
  } else if (read.family === "scenario") {
    nodes.push(bound(ctx, "Title", model.title, set(model, "title")),
      bound(ctx, "Premise", model.premise, set(model, "premise"), { multiline: true, prose: true }),
      bound(ctx, "Instructions", model.instructions, set(model, "instructions"), { multiline: true,
        hint: "Private to the narrator: never shown to the players." }));
    if (locked) return [...nodes, locked];
    nodes.push(heading("Openings"));
    model.openings.forEach((item, index) => {
      const title = "Opening " + (index + 1) + (item.slot ? " (" + item.slot + ")" : "");
      nodes.push(part(ctx, title, () => { model.openings.splice(index, 1); keep(); render(); }, "Remove opening " + (index + 1),
        area(ctx, title, item.text, set(item, "text"))));
    });
    nodes.push(ui.actions(ui.button("Add an opening", () => { model.openings.push({ slot: null, added: addedId(), text: "" }); keep(); render(); },
      { kind: "dashed" })));
  } else if (read.family === "lorebook") {
    nodes.push(bound(ctx, "Name", model.name, set(model, "name")),
      bound(ctx, "Description", model.description, set(model, "description"), { multiline: true, prose: true }));
    if (locked) return [...nodes, locked];
    nodes.push(heading("Entries"));
    model.entries.forEach((item, index) => {
      nodes.push(part(ctx, "Entry " + (index + 1), () => { model.entries.splice(index, 1); keep(); render(); }, "Remove entry " + (index + 1),
        bound(ctx, "Keys", item.keys, set(item, "keys"), { multiline: true, hint: "One key per line. Lore reaches play when a key is mentioned." }),
        bound(ctx, "Content", item.content, set(item, "content"), { multiline: true, prose: true,
          hint: item.slot === null ? undefined : "Emptying it removes the entry." }),
        checkbox(ctx, "Always in context (constant)", item.constant, set(item, "constant")),
        checkbox(ctx, "Enabled", item.enabled, set(item, "enabled"))));
    });
    nodes.push(ui.actions(ui.button("Add an entry", () => {
      model.entries.push({ slot: null, added: addedId(), original: null, keys: "", content: "", constant: false, enabled: true }); keep(); render();
    }, { kind: "dashed" })));
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

// -- the Raw tab ----------------------------------------------------------------------------------------

export function rawTab(ctx, read, drafts) {
  const { ui } = ctx;
  const { el } = ui;
  const box = el("div", { class: "source-rawtab g-stack" });
  const extra = Object.keys((read.envelope && read.envelope.extra) || {}).filter((key) => key !== "schema");
  const stored = () => {
    const payload = el("pre", { class: "source-payload", tabindex: "0", "aria-label": "The payload of " + read.revision + " as written" });
    payload.textContent = JSON.stringify(read.payload, null, 2);
    return payload;
  };
  const exact = unsafeNumbers(read.payload)
    ? el("p", { class: "g-muted" }, "A number in it is too large for the browser to show exactly.") : null;
  const why = !read.is_head ? read.actions.save_raw
    : read.actions.save_raw !== null ? read.actions.save_raw : null;
  if (why) { box.append(stored(), el("p", { class: "g-muted" }, why), exact || ""); return box; }
  if (!ctx.has("sources.save_raw")) {
    box.append(stored(), ui.unavailable("The raw editor", "This app does not serve sources.save_raw."), exact || "");
    return box;
  }
  if (exact) {
    box.append(stored(), ui.unavailable("The raw editor for this record",
      "It holds a number too large for the browser to keep exactly, so saving it here would change that number."));
    return box;
  }
  const draft = drafts.raw.get();
  if (draft && stale(draft, read)) { box.append(el("p", { class: "g-muted" }, "Keep or discard your draft above first.")); return box; }
  const start = draft ? draft.start : JSON.stringify(read.payload);
  const field = ui.field("Payload (JSON)", { multiline: true, value: draft ? draft.text : JSON.stringify(read.payload, null, 2),
    hint: "Saved exactly as written, unknown fields included" + (read.schema ? ", in " + read.schema : "") + "." });
  field.input.classList.add("source-raw");
  field.input.setAttribute("data-focus-key", "source-raw");
  field.input.setAttribute("spellcheck", "false");
  field.input.addEventListener("input", () => drafts.raw.set({ base: read.head, etag: read.etag, start, text: field.value }));
  box.append(field.node);
  if (extra.length) box.append(el("p", { class: "g-muted" }, "Kept on every new version as they are: " + extra.join(", ") + "."));
  box.append(saveBar(ctx, read, "Save raw as version", () => ctx.act(async () => {
    let payload;
    try {
      payload = JSON.parse(field.value);
    } catch (error) {
      field.setError("Not JSON: " + error.message);
      return;
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) { field.setError("The payload must be a JSON object."); return; }
    if (unsafeNumbers(payload)) { field.setError("A number here is too large to be kept exactly; write it as text, or leave it as it was."); return; }
    field.setError("");
    await save(ctx, read, "sources.save_raw", { payload }, drafts.raw);
  }), () => { drafts.raw.clear(); field.input.value = JSON.stringify(read.payload, null, 2); field.setError(""); }));
  return box;
}
