import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { PHONE_PX } from "../shell/tabs";
import { bodiesNaming, bodiesOf, declares, stylesheet } from "../testkit/stylesheet";

/* The composer below PHONE_PX, read from the stylesheet.

   The input bar is one flex row on a desktop: dice, the box, the speaker
   picker with its Respond-as, and Send. At 375px those controls are 390px of
   `flex: none` in a 343px bar, so the one item allowed to shrink -- the box --
   measured 30px wide, a character per line, and Send sat past the right edge
   of the screen. Focus mode made it worse by being right: it strips the
   chrome above the transcript so that the transcript and the place to write
   are what is left, and the place to write was the sliver.

   These cases hold the phone's answer to that -- the box on a row of its own,
   the controls under it -- against the same stylesheet `focus.test.tsx`
   reads, and for the same reason: nothing renders a 375px layout in jsdom,
   so the rules themselves are the only thing a test can hold. */

const { css, atWidth } = stylesheet();
const phone = atWidth(PHONE_PX);

test("below PHONE_PX the box has the bar's whole width, and the controls sit under it", () => {
  // The bar wraps and the box claims the first line of it entirely; `order`
  // puts it ahead of the dice button, which is before it in the DOM because
  // the popover it opens is anchored there.
  expect(bodiesOf(phone, ".inputbar").some((b) => declares(b, "flex-wrap") === "wrap")).toBe(true);
  const box = bodiesOf(phone, ".inputbar textarea");
  expect(box.some((b) => declares(b, "flex") === "1 1 100%" && declares(b, "order") === "-1"))
    .toBe(true);
});

test("the controls under the box are 44px touch targets, side by side", () => {
  expect(bodiesOf(phone, ".inputbar .send").some((b) => declares(b, "min-height") === "44px"))
    .toBe(true);
  // Stop beside Send rather than over it: stacked they were 112px of button
  // under the box, for a pair that only shows while a turn runs.
  expect(bodiesOf(phone, ".composer-run-actions")
    .some((b) => declares(b, "flex-direction") === "row")).toBe(true);
  // The speaker picker is the one control on the row that gives.
  expect(bodiesOf(phone, ".inputbar .form-actions select")
    .some((b) => declares(b, "min-width") === "0")).toBe(true);
});

test("nothing on the meta strip folds, and the number inputs are sized to their numbers", () => {
  // A nowrap row does not make its items nowrap: the inspector link folded
  // "What the model saw →" into four lines and stood taller than the box.
  expect(bodiesNaming(phone, "composer-link")
    .some((b) => declares(b, "white-space") === "nowrap")).toBe(true);
  // A bare `<input type="number">` is 185px wide by UA default; two of them
  // were most of why the strip scrolled 900px.
  expect(bodiesOf(css, ".response-target-input").some((b) => declares(b, "width") !== null))
    .toBe(true);
});

test("the box is 16px on a phone, so focusing it does not zoom the page", () => {
  // iOS Safari zooms in on focusing anything smaller. The box you tap to
  // write is the one control that must not do that.
  const sizes = bodiesOf(phone, ".inputbar textarea").map((b) => declares(b, "font-size"));
  expect(sizes).toContain("16px");
});

test("the viewport asks the browser to make room for the keyboard", () => {
  // Without it a mobile browser shrinks only the visual viewport when the
  // keyboard opens, and a composer at the foot of a 100%-height layout is
  // left behind the keys. The Android shell says the same with
  // `adjustResize`; this is the browser spelling.
  const html = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "..", "..", "index.html"), "utf8");
  const meta = /<meta name="viewport" content="([^"]+)"/.exec(html);
  expect(meta?.[1]).toContain("interactive-widget=resizes-content");
});
