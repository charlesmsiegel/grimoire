import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Read off disk, as pickerLayout.test.ts does: vitest stubs CSS imports to an
// empty string, so an import would make every case below pass vacuously.
const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "index.css"), "utf8");

// At phone width every screen's head is one `--panel` strip the content
// scrolls past. The panel is keyed to the head's markup, so a head shape the
// selector list misses keeps the desktop's 4px margin and reads as another
// app's screen -- which is what the global Costs report (a bare title) and
// Scenes (an eyebrow over a title row with actions) did.
const SHAPES = [
  ".page-wide > .eyebrow:first-child + .screen-title",
  ".page-wide > .eyebrow:first-child + .scenes-head",
  ".page-wide > .screen-title:first-child",
  ".page-wide > .shelf-head:first-child",
];

/** The phone rules, from the page box's own phone gutter onward. */
const phone = (() => {
  const start = css.indexOf(".page-wide { padding: 0 14px 18px; max-width: none; }");
  if (start < 0) throw new Error("no phone .page-wide rule");
  return css.slice(start);
})();

test.each(SHAPES)("panels the %s head shape at phone width", (shape) => {
  const panel = /([^{}]+)\{\s*margin: 0 -14px;[^}]*background: var\(--panel\);[^}]*\}/.exec(phone);
  expect(panel).not.toBeNull();
  const selectors = panel![1].split(",").map((s) => s.trim());
  expect(selectors).toContain(shape);
});
