// What Characters and lore draws of the shelf (docs/SHELF.md): the row of tag chips with "Save this filter", the
// saved filters under the shelves, each entry's own tags and pin, and Select mode with its bar of bulk actions.
// `view` is the page's (page.js): its ctx, address, shelf (./shelf.js), tags, catalog and `refresh`, which reads
// the shelf again and draws again after a write.
//
// The bar offers Pin and Tag only. The design also shows Move, Lorebook and Delete; the library has no way to move
// an entry between sources, to bind a lorebook to several entries at once or to delete an entry (every revision
// is kept), so those are not offered until it does.

import { filterEntries } from "./catalog.js";
import { bulkSummary, filterQuery, knownTags, marksOf, sameFilter, suggestName, tagChips, tagsParam, toggleTag, worthSaving } from "./shelf.js";
import { formDialog, pinToggle, setPinned, tagDialog, tagField } from "./tagging.js";

const MAX_LIST = 1000;       // the most entries one catalog request returns
const MAX_COUNTED = 6;       // saved filters with a search of their own are counted for this many searches

function key(node, name) { node.setAttribute("data-focus-key", name); return node; }
const count = (n, one, many) => n + " " + (n === 1 ? one : many);

// -- the tag chips and "Save this filter" ---------------------------------------------------------------

// The tags carried by `entries` (what this shelf and role list), each with its count; choosing one adds it to
// the address, and several narrow together. "Save this filter" shows when this view is not the whole library
// and is not saved yet.
export function tagRow(view, entries) {
  const { ctx, shelf, address, tags } = view;
  const { ui } = ctx;
  const { el } = ui;
  const chips = tagChips(shelf, entries, tags);
  const canSave = ctx.has("shelf.save_filter") && worthSaving(view.current) && !shelf.filters.some((saved) => sameFilter(saved, view.current));
  if (!chips.length && !canSave) return null;
  return el("div", { class: "g-row sources-tagrow" },
    chips.length ? el("span", { class: "g-muted sources-taglabel", "aria-hidden": "true" }, "Tags") : null,
    chips.length ? el("div", { class: "g-chips", role: "group", "aria-label": "Tags: entries must carry every chosen tag" }, chips.map((chip) => {
      const node = ui.selectable([el("span", {}, chip.tag), el("span", { class: "g-muted sources-chipcount" }, String(chip.count))], { chip: true, selected: chip.chosen,
        onclick: () => ctx.navigate(address({ tags: tagsParam(toggleTag(tags, chip.tag)), limit: "" })) });
      node.setAttribute("aria-label", chip.tag + ", " + count(chip.count, "entry", "entries"));
      node.setAttribute("data-tag", chip.tag);
      return key(node, "sources-tag:" + chip.tag);
    })) : null,
    canSave ? key(ui.button("Save this filter", () => saveDialog(view), { kind: "linklike" }), "sources-save-filter") : null);
}

function saveDialog(view) {
  const { ctx, current } = view;
  const { ui } = ctx;
  const name = ui.field("Name", { value: suggestName(current, view.roleLabel), required: true,
    hint: "Keeps this role, shelf, tags, search and order under a name. The entries are not changed." });
  formDialog(ctx, "Save this filter", [name.node], "Save filter", async (modal) => {
    const saved = await ctx.api("shelf.save_filter", { name: name.value, role: current.role, shelf: current.shelf, source: current.source,
      tags: current.tags, query: current.query, sort: current.sort });
    modal.close();
    ctx.notify("Saved the filter " + saved.name + ".");
    await view.refresh();
  });
  name.input.focus();
}

// -- the saved filters ----------------------------------------------------------------------------------

// The entries a saved filter lists, where this page can count them: from the catalog it read when the filter
// has the same search, else from one more request for that search (a few searches at most). A catalog too
// long for one request is not counted.
function countSaved(view, saved, into) {
  const of = (catalog) => {
    if (!catalog || catalog.truncated) return;
    into.textContent = String(filterEntries(catalog.entries, { role: saved.role, shelf: saved.source || saved.shelf, tags: saved.tags, marks: view.shelf }).length);
  };
  if ((saved.query || "") === (view.query || "")) { of(view.catalog); return; }
  if (!view.searches) view.searches = new Map();
  if (!view.searches.has(saved.query)) {
    if (view.searches.size >= MAX_COUNTED) return;
    view.searches.set(saved.query, view.ctx.api("sources.catalog", { query: saved.query, limit: MAX_LIST }).catch(() => null));
  }
  view.searches.get(saved.query).then(of, () => {});
}

export function savedFilters(view) {
  const { ctx, shelf, address } = view;
  const { ui } = ctx;
  const { el } = ui;
  const rows = shelf.filters.map((saved) => {
    const counted = el("span", { class: "g-muted" });
    countSaved(view, saved, counted);
    const link = el("a", { class: "sources-shelf sources-saved-link", href: address({ ...filterQuery(saved), limit: "" }), title: saved.name + ": " + describe(saved) },
      el("span", { class: "g-clip" }, saved.name), counted);
    if (sameFilter(saved, view.current)) link.setAttribute("aria-current", "page");
    const editable = ctx.has("shelf.rename_filter") && ctx.has("shelf.delete_filter");
    const edit = editable ? key(ui.button("Edit", () => editDialog(view, saved), { kind: "linklike", size: "small" }), "sources-saved-edit:" + saved.id) : null;
    if (edit) edit.setAttribute("aria-label", "Rename or delete the saved filter " + saved.name);
    return el("div", { class: "sources-saved-row" }, link, edit);
  });
  return el("nav", { class: "sources-shelves sources-saved", "aria-label": "Saved filters" }, ui.eyebrow("Saved filters"),
    rows.length ? rows : el("p", { class: "g-muted sources-saved-none" }, "None yet. Choose a role, a shelf or tags, then Save this filter."));
}

// What a saved filter keeps, in words, for its link's title.
function describe(saved) {
  const parts = [saved.role, saved.source || saved.shelf, saved.tags.length ? "tags " + saved.tags.join(" + ") : "",
    saved.query ? "search " + JSON.stringify(saved.query) : "", saved.sort ? "sorted by " + saved.sort : ""].filter(Boolean);
  return parts.join(" · ") || "Everything";
}

function editDialog(view, saved) {
  const { ctx } = view;
  const { ui } = ctx;
  const name = ui.field("Name", { value: saved.name, required: true });
  const stale = async (modal) => {
    modal.close();
    ctx.notify("This saved filter changed since this page read it, so nothing was written. It is shown as it is now.", { bad: true });
    await view.refresh();
  };
  let dialog = null;
  const remove = ui.button("Delete", async () => {
    const sure = await ui.confirm("Delete the saved filter " + JSON.stringify(saved.name) + "? No entry, tag or pin is changed.",
      { confirmLabel: "Delete filter", danger: true });
    if (!sure) return;
    try {
      await ctx.api("shelf.delete_filter", { id: saved.id, expected: saved.etag });
    } catch (error) {
      if (error.reason === "left") return;
      if (error.reason === "changed") { await stale(dialog); return; }
      dialog.problem.replaceChildren(ui.errorBox(error));
      return;
    }
    dialog.close();
    ctx.notify("Deleted the saved filter " + saved.name + ".");
    await view.refresh();
  }, { kind: "danger" });
  dialog = formDialog(ctx, "Saved filter " + saved.name, [name.node], "Rename", async (modal) => {
    try {
      await ctx.api("shelf.rename_filter", { id: saved.id, name: name.value, expected: saved.etag });
    } catch (error) {
      if (error.reason !== "changed") throw error;
      await stale(modal);
      return;
    }
    modal.close();
    await view.refresh();
  }, { extra: [remove] });
}

// -- one entry's tags and pin ---------------------------------------------------------------------------

// Under an entry's name: its tags (a button that opens the tag editor, "Add tags" when it has none) and its
// Pin toggle. Where the app only reads the shelf, the tags are plain text and there is no toggle.
export function entryMarks(view, entry, { tags = true, pin = true } = {}) {
  const { ctx, shelf } = view;
  if (!shelf || shelf.failed) return null;
  const { ui } = ctx;
  const { el } = ui;
  const marks = marksOf(shelf, entry.ref);
  const name = entry.name || entry.ref;
  const nodes = [];
  if (tags) {
    const text = marks.tags.join(", ");
    if (ctx.has("shelf.set_tags")) {
      const edit = key(el("button", { type: "button", class: "sources-tags sources-tagsbtn" + (text ? "" : " sources-notags"),
        onclick: () => tagDialog(ctx, view.shelf, { ref: entry.ref, name }, marksOf(view.shelf, entry.ref), () => view.refresh()) },
      text || "Add tags"), "sources-tags:" + entry.ref);
      edit.setAttribute("aria-label", (text ? "Tags of " + name + ": " + text + ". Edit tags" : "Add tags to " + name));
      nodes.push(edit);
    } else if (text) nodes.push(el("span", { class: "sources-tags" }, text));
  }
  if (pin && ctx.has("shelf.pin_entry")) {
    const toggle = key(pinToggle(ctx, marks.pinned, name, () => ctx.act(async () => {
      const now = marksOf(view.shelf, entry.ref);
      await setPinned(ctx, entry.ref, now, !now.pinned);
      await view.refresh();
    })), "sources-pin:" + entry.ref);
    toggle.classList.add("sources-pin");
    nodes.push(toggle);
  }
  if (!nodes.length) return null;
  return nodes.length === 1 ? nodes[0] : el("div", { class: "g-row sources-marks" }, nodes);
}

// -- Select mode ----------------------------------------------------------------------------------------

// Which entries are selected, kept for the tab (a draft) so choosing a tag or another shelf keeps the selection.
// Each is kept by its exact ref, with the name it showed, so the bar can name an entry that is not in view now.
export function selection(view, redraw) {
  const { ctx } = view;
  const kept = ctx.draft("sources:select");
  const picked = new Map();  // ref -> name
  let on = false;
  try {
    const state = JSON.parse(kept.get() || "null");
    if (state && state.on) { on = true; for (const [ref, name] of state.picked || []) picked.set(String(ref), String(name)); }
  } catch (error) { /* nothing kept */ }
  const save = () => kept.set(on ? JSON.stringify({ on, picked: [...picked] }) : "");
  const picks = {
    available: Boolean(view.shelf) && !view.shelf.failed && (ctx.has("shelf.pin_entry") || ctx.has("shelf.edit_tags")),
    note: "",
    onchange: null,  // the bar's own redraw: a pick changes the bar, not the whole page
    get on() { return on && picks.available; },
    get size() { return picked.size; },
    has: (ref) => picked.has(ref),
    refs: () => [...picked.keys()],
    nameOf: (ref) => picked.get(ref) || ref,
    toggleMode() { on = !on; picked.clear(); picks.note = ""; save(); redraw(); },
    toggle(entry) {
      if (!picked.delete(entry.ref)) picked.set(entry.ref, entry.name || entry.ref);
      picks.note = "";
      save();
      if (picks.onchange) picks.onchange();
      return picked.has(entry.ref);
    },
    drop(ref) { picked.delete(ref); save(); },
    addAll(entries) { for (const entry of entries) picked.set(entry.ref, entry.name || entry.ref); picks.note = ""; save(); redraw(); },
    // In Select mode a tile is a toggle, not a link: it shows a ring while its entry is selected.
    asPick(node, link, entry) {
      const show = () => {
        const chosen = picked.has(entry.ref);
        link.setAttribute("aria-pressed", String(chosen));
        node.classList.toggle("sources-picked", chosen);
      };
      link.setAttribute("role", "button");
      key(link, "sources-pick:" + entry.ref);
      link.addEventListener("click", (event) => { event.preventDefault(); picks.toggle(entry); show(); });
      link.addEventListener("keydown", (event) => { if (event.key === " ") { event.preventDefault(); picks.toggle(entry); show(); } });
      const tags = marksOf(view.shelf, entry.ref).tags.join(", ");
      if (tags) node.append(ctx.ui.el("span", { class: "sources-tags" }, tags));
      show();
    },
  };
  return picks;
}

// The toggle a row of the list has in Select mode, where a tile is its own.
export function pickButton(view, entry) {
  const { ctx, picks } = view;
  const button = ctx.ui.button("", () => { picks.toggle(entry); show(); }, { size: "small" });
  function show() {
    const chosen = picks.has(entry.ref);
    button.textContent = chosen ? "Selected" : "Select";
    button.setAttribute("aria-pressed", String(chosen));
    if (button.parentNode && button.parentNode.parentNode) button.parentNode.parentNode.classList.toggle("sources-picked-row", chosen);
  }
  button.setAttribute("aria-label", "Select " + (entry.name || entry.ref));
  button.classList.add("sources-pickbtn");
  show();
  return key(button, "sources-pick:" + entry.ref);
}

// The bar at the foot of the page in Select mode: how many are selected, Select all shown, and what can be done
// to all of them at once. Each action goes entry by entry, each at the etag this page read, and says how many it
// changed and which it could not.
export function bulkBar(view) {
  const { ctx, picks } = view;
  const { ui } = ctx;
  const { el } = ui;
  const status = el("span", { class: "sources-bar-count", role: "status" });
  const note = el("span", { class: "g-muted sources-bar-note", role: "status" }, picks.note);
  const allPinned = () => picks.size > 0 && picks.refs().every((ref) => marksOf(view.shelf, ref).pinned);

  async function each(verb, work) {
    await ctx.act(async () => {
      const failures = [];
      let done = 0;
      for (const ref of picks.refs()) {
        try {
          await work(ref, marksOf(view.shelf, ref));
          done += 1;
          picks.drop(ref);  // what was refused stays selected, to look at or try again
        } catch (error) {
          if (error && error.reason === "left") throw error;
          failures.push({ ref, name: picks.nameOf(ref), reason: (error && error.reason) || "failed" });
        }
      }
      picks.note = bulkSummary(verb, done, failures);
      if (failures.length) ctx.notify(picks.note, { bad: true });
      await view.refresh();
    });
  }

  const pin = ctx.has("shelf.pin_entry") ? key(ui.button("Pin", () => {
    const pinned = !allPinned();
    return each(pinned ? "Pinned" : "Unpinned", (ref, marks) => ctx.api("shelf.pin_entry", { ref, pinned, expected: marks.etag }));
  }), "sources-bulk-pin") : null;
  const tag = ctx.has("shelf.edit_tags") ? key(ui.button("Tag…", () => {
    const n = picks.size;
    const add = tagField(ctx, "Add tags", { known: knownTags(view.shelf),
      hint: "Separate tags with commas. Each selected entry gets these and keeps its others." });
    const remove = ui.field("Remove tags", { hint: "Tags to take away from the selected entries that carry them." });
    formDialog(ctx, "Tag " + count(n, "entry", "entries"), [add.node, remove.node], "Apply to " + count(n, "entry", "entries"), async (modal) => {
      const adding = add.tags, removing = remove.value.split(",").map((text) => text.replace(/\s+/g, " ").trim()).filter(Boolean);
      if (!adding.length && !removing.length) { add.field.setError("Name at least one tag to add or remove."); return; }
      modal.close();
      await each("Changed the tags of", (ref, marks) => ctx.api("shelf.edit_tags", { ref, add: adding, remove: removing, expected: marks.etag }));
    });
    add.field.input.focus();
  }), "sources-bulk-tag") : null;

  function show() {
    status.textContent = picks.size + " selected";
    note.textContent = picks.note;
    for (const button of [pin, tag]) if (button) button.disabled = picks.size === 0;
    if (pin) pin.textContent = allPinned() ? "Unpin" : "Pin";
  }
  picks.onchange = show;
  show();
  return el("div", { class: "sources-bar", role: "region", "aria-label": "Selected entries" }, status,
    key(ui.button("Select all shown", () => picks.addAll(view.shown), { kind: "linklike" }), "sources-select-all"), note, pin, tag);
}
