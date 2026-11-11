// The dialogs and the one write that tags and pins share, wherever an entry shows (Characters and lore, an
// entry's sheet). They are built from the shell's own pieces only, so they need no page's style sheet.
//
// Every write names the etag the page read (docs/SHELF.md). When the marks moved on meanwhile (another tab, the
// other device) the app answers `changed`: nothing was written, and the caller reads the shelf again.

import { knownTags, parseTags } from "./shelf.js";

// A form in a modal: its fields, Cancel and one action. A refusal is shown in the modal, where the person is, and
// what they typed stays for another try. `work(modal)` closes the modal itself when it is done.
export function formDialog(ctx, title, fields, label, work, { extra = [] } = {}) {
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
  modal.foot.append(...extra, ui.button("Cancel", () => modal.close()), go);
  return { ...modal, problem, go };
}

// A field of tags typed with commas between them, and under it the tags already in use as chips: a chip is
// pressed while the field holds its tag, and pressing it puts the tag in or takes it out.
export function tagField(ctx, label, { value = [], known = [], hint } = {}) {
  const { ui } = ctx;
  const { el } = ui;
  const field = ui.field(label, { value: value.join(", "), hint: hint || "Separate tags with commas." });
  const chips = known.map((tag) => {
    const chip = ui.selectable(tag, { chip: true, onclick: () => {
      const now = parseTags(field.value);
      const held = now.find((item) => item.toLowerCase() === tag.toLowerCase());
      field.value = (held ? now.filter((item) => item !== held) : [...now, tag]).join(", ");
      show();
    } });
    chip.setAttribute("aria-label", "Tag " + tag);
    return { tag, chip };
  });
  function show() {
    const now = parseTags(field.value).map((item) => item.toLowerCase());
    for (const { tag, chip } of chips) chip.setAttribute("aria-pressed", String(now.includes(tag.toLowerCase())));
  }
  field.input.addEventListener("input", show);
  show();
  const node = el("div", { class: "g-stack" }, field.node,
    chips.length ? el("div", { class: "g-chips", role: "group", "aria-label": "Tags in use" }, chips.map((item) => item.chip)) : null);
  return { node, field, get tags() { return parseTags(field.value); } };
}

// Edit one entry's tags. `entry` is { ref, name }; `marks` what the shelf says of it now. `done(changed)` runs
// after a save (changed: false) or after the app said the marks had moved on (changed: true): either way the
// caller reads the shelf again.
export function tagDialog(ctx, shelf, entry, marks, done) {
  const input = tagField(ctx, "Tags", { value: marks.tags, known: knownTags(shelf),
    hint: "Separate tags with commas. Tags are your own notes: they never change the entry or its versions." });
  input.field.input.setAttribute("data-focus-key", "sources-tags-field");
  const dialog = formDialog(ctx, "Tags for " + (entry.name || entry.ref), [input.node], "Save tags", async (modal) => {
    try {
      await ctx.api("shelf.set_tags", { ref: entry.ref, tags: input.tags, expected: marks.etag });
    } catch (error) {
      if (error.reason !== "changed") throw error;
      modal.close();
      ctx.notify("These tags changed since this page read them, so nothing was saved. They are shown as they are now.", { bad: true });
      await done(true);
      return;
    }
    modal.close();
    await done(false);
  });
  input.field.input.focus();
  return dialog;
}

// Pin or unpin one entry. Resolves true when the pin was written, false when the marks had moved on (said in a
// notice; the caller reads the shelf again either way). Any other refusal is thrown for ctx.act to show.
export async function setPinned(ctx, ref, marks, pinned) {
  try {
    await ctx.api("shelf.pin_entry", { ref, pinned, expected: marks.etag });
    return true;
  } catch (error) {
    if (error.reason !== "changed") throw error;
    ctx.notify("This entry's tags or pin changed since this page read them, so nothing was written. They are shown as they are now.", { bad: true });
    return false;
  }
}

// A Pin / Pinned toggle for `what` ("Mara"): pressed while pinned.
export function pinToggle(ctx, pinned, what, onclick, { size = "small" } = {}) {
  const button = ctx.ui.button(pinned ? "Pinned" : "Pin", onclick, { size });
  button.setAttribute("aria-pressed", String(Boolean(pinned)));
  button.setAttribute("aria-label", "Pin " + what);
  return button;
}
