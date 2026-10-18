import { PHONE_PX } from "../shell/tabs";
import { bodiesOf, declares, stylesheet } from "../testkit/stylesheet";

/* The play view below PHONE_PX, read from the stylesheet -- jsdom lays nothing
   out, so the rules are what a test can hold (`composerLayout.test.ts` says
   the same of the composer). */

const { atWidth } = stylesheet();
const phone = atWidth(PHONE_PX);

test("the player's rule sits in the page gutter, so their prose is on everyone's measure", () => {
  // With no pull at all the player's prose sat 13px right of every other
  // speaker's: the two-column transcript the desktop rule exists to avoid.
  // The pull is the padding plus the 1px rule, so the prose lands where it
  // would with neither.
  const run = bodiesOf(phone, ".run.pc").find((b) => declares(b, "margin-left") !== null)!;
  const pull = -parseInt(declares(run, "margin-left")!, 10);
  expect(pull).toBe(parseInt(declares(run, "padding-left")!, 10) + 1);
  expect(pull).toBeLessThan(16);   // inside the page's gutter, not off the screen
});

test("the scene's head keeps its title to one line", () => {
  const title = bodiesOf(phone, ".scene-title");
  expect(title.some((b) => declares(b, "white-space") === "nowrap"
    && declares(b, "text-overflow") === "ellipsis")).toBe(true);
});

test("a condition's label sits over its value rather than running into it", () => {
  expect(bodiesOf(phone, ".conditions-label").map((b) => declares(b, "display"))).toContain("block");
});
