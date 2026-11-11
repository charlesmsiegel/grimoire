// An entry's tags and pin in its sheet's header (docs/SHELF.md): the tags as pills, a control to edit them and
// a Pin toggle. They are the person's notes about the entry, kept by its unpinned ref: the same tags show on
// every version, and changing them writes no revision. Nothing is shown where the app does not keep them.
//
// The shelf is read, and the dialog and the pin write are shared with Characters and lore (../sources/).

import { marksOf, readShelf } from "../sources/shelf.js";
import { pinToggle, setPinned, tagDialog } from "../sources/tagging.js";

// The marks of `read` (a `sources.read` answer) as one node for the header's row of pills, or null. The node
// draws itself again after each write, with focus back on the control that was used.
export async function headerMarks(ctx, read) {
  let shelf = await readShelf(ctx);
  if (!shelf) return null;
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("marks.css");
  const ref = read.entity;
  const name = read.name || read.id || ref;
  const node = el("span", { class: "source-marks", role: "group", "aria-label": "Your tags and pin" });

  async function refresh(focus) {
    const fresh = await readShelf(ctx);
    if (fresh) shelf = fresh;
    draw();
    const again = focus && Array.from(node.querySelectorAll("button")).find((button) => button.getAttribute("data-focus-key") === focus);
    if (again) again.focus();
  }

  function draw() {
    if (shelf.failed) {
      node.replaceChildren(el("span", { class: "g-muted source-marks-note" }, "Your tags and pin could not be read."));
      return;
    }
    const marks = marksOf(shelf, ref);
    const parts = marks.tags.map((tag) => el("a", { class: "g-pill source-tag", title: "Entries tagged " + tag,
      href: ctx.registered("sources") ? ctx.href("sources", [], { tags: tag }) : undefined }, tag));
    if (ctx.has("shelf.set_tags")) {
      const edit = ui.button(marks.tags.length ? "Edit tags" : "Add tags", () => tagDialog(ctx, shelf, { ref, name }, marksOf(shelf, ref),
        () => refresh("source-tags")), { kind: "linklike", size: "small" });
      edit.setAttribute("data-focus-key", "source-tags");
      parts.push(edit);
    }
    if (ctx.has("shelf.pin_entry")) {
      const toggle = pinToggle(ctx, marks.pinned, name, () => ctx.act(async () => {
        const now = marksOf(shelf, ref);
        await setPinned(ctx, ref, now, !now.pinned);
        await refresh("source-pin");
      }));
      toggle.classList.add("source-pin");
      toggle.setAttribute("data-focus-key", "source-pin");
      parts.push(toggle);
    }
    node.replaceChildren(...parts);
  }

  draw();
  return node;
}
