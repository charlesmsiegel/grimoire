import { fireEvent, render, screen } from "@testing-library/react";
import { PHONE_PX } from "../shell/tabs";
import { stylesheet } from "../testkit/stylesheet";
import { FocusProvider, FocusRestore, useFocus } from "./focus";

function Probe() {
  const { focus, setFocus } = useFocus();
  return (
    <>
      <span data-testid="state">{focus ? "on" : "off"}</span>
      <button onClick={() => setFocus(true)}>enter</button>
    </>
  );
}

const wrapped = () => (
  <FocusProvider><FocusRestore /><Probe /></FocusProvider>
);

beforeEach(() => localStorage.clear());

test("focus is off by default, and the restore pill only exists once it is on", () => {
  render(wrapped());
  expect(screen.getByTestId("state")).toHaveTextContent("off");
  // Unrendered, not hidden: in focus mode this is the FIRST tab stop, and a
  // control that is always in the tree would be one more thing between the
  // reader and the composer at every other moment.
  expect(screen.queryByRole("button", { name: /leave focus mode/i })).toBeNull();

  fireEvent.click(screen.getByText("enter"));
  expect(screen.getByTestId("state")).toHaveTextContent("on");
  fireEvent.click(screen.getByRole("button", { name: /leave focus mode/i }));
  expect(screen.getByTestId("state")).toHaveTextContent("off");
});

test("the preference survives a reload", () => {
  const first = render(wrapped());
  fireEvent.click(screen.getByText("enter"));
  first.unmount();

  render(wrapped());
  expect(screen.getByTestId("state")).toHaveTextContent("on");
  expect(screen.getByRole("button", { name: /leave focus mode/i })).toBeInTheDocument();
});

test("a component outside the provider reads 'not in focus mode' rather than throwing", () => {
  // Every route and every editor is rendered bare in its own test; reading a
  // display preference must not require the shell around it.
  render(<Probe />);
  expect(screen.getByTestId("state")).toHaveTextContent("off");
});

test("storage that refuses to answer is not a blank screen", () => {
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  render(wrapped());
  expect(screen.getByTestId("state")).toHaveTextContent("off");
  // Still togglable for this session — it just will not be remembered.
  fireEvent.click(screen.getByText("enter"));
  expect(screen.getByTestId("state")).toHaveTextContent("on");
  get.mockRestore();
  set.mockRestore();
});

/* ---- the way IN, which on a phone is the whole of whether focus mode exists ---- */

/* The tests above cover the way out. This block covers the way in, and it has
   to read the stylesheet rather than the DOM: the control was never removed
   from the markup, it was hidden by a media query, and jsdom runs no media
   queries — so every DOM assertion about it passed while a phone had no way
   into focus mode at all.

   What went wrong is worth keeping written down. The phone rule dropped `.kbar`,
   `.header-rail`, `.header-theme` and `.header-focus` together, on the stated
   grounds that the last two are "in Configuration". That is true of the theme
   picker and false of focus mode, which Configuration has never carried — so
   below `PHONE_PX` the only remaining entry was the palette's own `action:focus`
   row, reached through a `.kbar` the same rule hides and otherwise through a
   chord a phone has no keyboard for. The exit kept its 44px phone target while
   the entrance had none, which is backwards: `focus.tsx` and the scene bar both
   argue focus mode is FOR the 375px case. */

// Off disk, through the testkit, which carries the reasons: vitest stubs CSS
// imports to an empty string, and comments come out before any rule is read.
// The composer suite (`routes/composerLayout.test.ts`) reads the same file
// with the same tools, which is why they are no longer defined here.
const { css, atWidth, bodiesNaming, declares } = stylesheet();

test("nothing in the stylesheet hides the control that enters focus mode", () => {
  const hidden = bodiesNaming(css, "header-focus")
    .filter((body) => declares(body, "display") === "none");
  expect(hidden).toEqual([]);
});

test("below PHONE_PX the way in is a touch target, not a 10px word", () => {
  // The same 44px floor `.focus-restore` takes twelve lines up, and for a
  // sharper version of the same reason: missing the exit costs a tap on the
  // pill that is still on screen, and missing the entrance means never
  // finding the mode.
  const phone = atWidth(PHONE_PX);
  const bodies = bodiesNaming(phone, "header-focus")
    .filter((body) => declares(body, "width") || declares(body, "height"));
  expect(bodies).not.toEqual([]);
  for (const body of bodies) {
    expect(declares(body, "width")).toBe("44px");
    expect(declares(body, "height")).toBe("44px");
  }
});

test("the word gives way to the glyph, so 44px costs the header no more than that", () => {
  // Two spans and a media query rather than two components: which one shows is
  // a width question, and a width question the CSS can answer is one React
  // should not be re-answering with a resize listener.
  const phone = atWidth(PHONE_PX);
  expect(bodiesNaming(phone, "header-focus-word")
    .some((b) => declares(b, "display") === "none")).toBe(true);
  expect(bodiesNaming(phone, "header-focus-glyph")
    .some((b) => declares(b, "display") === "flex")).toBe(true);
  // And the glyph is the one hidden at every width above it.
  expect(bodiesNaming(css, "header-focus-glyph")
    .some((b) => declares(b, "display") === "none")).toBe(true);
});

test("the pill yields the room, so the way in is never pushed off a 320px screen", () => {
  // Codex caught this on the PR that restored the button, and it was real:
  // `.scene-pill` is `flex: none`, so at 320px the widest true pill (an
  // unpriced scene's `NOT REPORTED · CTX 100%`) plus the wordmark, the dot,
  // the gutters and a 44px target overran the viewport by 28px -- and a flex
  // row spends its overrun on the LAST item, which is this control. Measured
  // in a browser: 320px fit exactly until the button came back.
  //
  // jsdom runs no layout engine, so this asserts the declarations that make
  // the overflow impossible rather than the geometry -- `pickerLayout.test.ts`
  // says why, about the same bug one surface over. What must hold is that the
  // pill can give width up: a truncated number is a worse read, an untappable
  // control is not a control.
  const phone = atWidth(PHONE_PX);
  const [body] = bodiesNaming(phone, "scene-pill");
  expect(body).toBeDefined();
  expect(declares(body, "min-width")).toBe("0");
  // `flex: none` is what the desktop rule sets and what must not survive here;
  // `min-width: 0` alone would still leave a rigid item rigid.
  expect(declares(body, "flex")).not.toBe("none");
  expect(declares(body, "text-overflow")).toBe("ellipsis");
});
