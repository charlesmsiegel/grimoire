import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import ShortcutsHelp from "./ShortcutsHelp";
import { useHotkeys, type Hotkey } from "./useHotkeys";

function Bind({ keys, modal }: { keys: Hotkey[]; modal?: boolean }) {
  useHotkeys(keys, { modal });
  return null;
}

const NEW_SCENE: Hotkey = {
  keys: "n", label: "New scene", group: "IN THIS SCENE", run: () => {},
};

function press(key: string, on: Window | Element = window) {
  fireEvent.keyDown(on, { key });
}

test("? opens the sheet and Escape closes it", () => {
  render(<ShortcutsHelp />);
  expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
  press("?");
  expect(screen.getByRole("dialog", { name: /keyboard/i })).toBeTruthy();
  press("Escape");
  expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
});

test("? closes it again, and so does the scrim", () => {
  const { container } = render(<ShortcutsHelp />);
  press("?");
  press("?");
  expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
  press("?");
  fireEvent.mouseDown(container.querySelector(".shortcuts-scrim")!);
  expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
});

test("it lists what is bound right now, under its own heading", () => {
  render(<><Bind keys={[NEW_SCENE]} /><ShortcutsHelp /></>);
  press("?");
  const sheet = screen.getByRole("dialog", { name: /keyboard/i });
  expect(sheet.textContent).toContain("IN THIS SCENE");
  expect(sheet.textContent).toContain("New scene");
  expect(screen.getByText("N")).toBeTruthy();
  // Its own binding is in the list, because a sheet you can only find by
  // accident is not documentation.
  expect(sheet.textContent).toContain("Keyboard shortcuts");
});

test("a binding with no label stays out of it", () => {
  render(<><Bind keys={[{ keys: "escape", run: () => {} }]} /><ShortcutsHelp /></>);
  press("?");
  expect(screen.getByRole("dialog", { name: /keyboard/i }).textContent).not.toContain("Esc");
});

// A shortcut whose control is disabled is still worth naming — the reader is
// looking for what the key IS — but saying so is the difference between "there
// is no such key" and "not right now".
test("a binding that cannot fire right now is shown as inert", () => {
  render(<><Bind keys={[{ ...NEW_SCENE, enabled: false }]} /><ShortcutsHelp /></>);
  press("?");
  expect(screen.getByText("New scene").closest(".shortcuts-row")!.className).toContain("off");
});

test("the sheet is unmounted while closed, not hidden", () => {
  const { container } = render(<><Bind keys={[NEW_SCENE]} /><ShortcutsHelp /></>);
  expect(container.querySelector(".shortcuts-scrim")).toBeNull();
});

// It is one of the two bindings that outlive an overlay: a reader who cannot
// remember how to close the thing in front of them is exactly who needs it.
test("it opens over an overlay, and lists that overlay's own keys", () => {
  render(
    <>
      <Bind keys={[NEW_SCENE]} />
      <Bind keys={[{ keys: "escape", label: "Close the dossier", group: "THIS PANEL", run: () => {} }]} modal />
      <ShortcutsHelp />
    </>,
  );
  press("?");
  const sheet = screen.getByRole("dialog", { name: /keyboard/i });
  expect(sheet.textContent).toContain("Close the dossier");
  // ...and Escape reaches the sheet, not the panel under it.
  press("Escape");
  expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
});

test("? typed into prose is a question mark", () => {
  render(<><ShortcutsHelp /><textarea aria-label="prose" /></>);
  const field = screen.getByLabelText("prose");
  field.focus();
  press("?", field);
  expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
});

// The sheet is opened FROM under an overlay (it is `global`), so it is read at
// exactly the moment half its rows cannot fire. Listing them as live is the
// one lie a sheet like this must not tell — the reader presses the key, and
// nothing happens, twice.
test("a binding the overlay on top is holding off is inert too", () => {
  render(
    <>
      <Bind keys={[NEW_SCENE]} />
      <Bind keys={[{ keys: "mod+k", label: "Go anywhere", group: "ANYWHERE", global: true, run: () => {} }]} />
      <Bind keys={[{ keys: "escape", label: "Close the dossier", group: "THIS PANEL", run: () => {} }]} modal />
      <ShortcutsHelp />
    </>,
  );
  press("?");
  // By row rather than by text: "Keyboard shortcuts" is both the sheet's title
  // and one of its rows.
  const row = (label: string) => [...document.querySelectorAll(".shortcuts-row")]
    .find((r) => r.querySelector(".shortcuts-label")?.textContent === label)!.className;
  expect(row("New scene")).toContain("off");
  // The overlay's own key, and the two that outlive one, are still live.
  expect(row("Close the dossier")).not.toContain("off");
  expect(row("Go anywhere")).not.toContain("off");
  expect(row("Keyboard shortcuts")).not.toContain("off");
});

// It calls itself a modal dialog; a modal dialog that never takes focus is a
// claim screen readers act on and sighted keyboard users cannot verify — Tab
// would still walk the page behind it.
test("it takes focus, and gives it back to where the reader was", () => {
  render(<><button>somewhere</button><ShortcutsHelp /></>);
  const before = screen.getByText("somewhere");
  before.focus();
  press("?");
  expect(document.activeElement).toBe(screen.getByRole("dialog", { name: /keyboard/i }));
  press("Escape");
  expect(document.activeElement).toBe(before);
});

// Opening the sheet re-registers its own scope, which is how it becomes the
// thing on top -- and would reorder its own rows between the first opening and
// every later one if the list were read in that order.
test("the rows are in the same order every time it opens", () => {
  render(<><Bind keys={[NEW_SCENE]} /><ShortcutsHelp /></>);
  const labels = () => [...document.querySelectorAll(".shortcuts-label")].map((e) => e.textContent);
  press("?");
  const first = labels();
  press("Escape");
  press("?");
  expect(labels()).toEqual(first);
  expect(first.length).toBeGreaterThan(1);
});

// The sheet advertises what you can press RIGHT NOW, and a turn finishing or a
// panel opening underneath moves that while it is up. A snapshot taken when it
// opened would keep saying the old answer until it was closed and reopened
// (PR #400 review).
test("a row that goes inert while the sheet is open says so without reopening", () => {
  function Toggling() {
    const [on, setOn] = useState(true);
    useHotkeys([{ ...NEW_SCENE, enabled: on }]);
    return <button onClick={() => setOn(false)}>disable</button>;
  }
  render(<><Toggling /><ShortcutsHelp /></>);
  press("?");
  const row = () => screen.getByText("New scene").closest(".shortcuts-row")!.className;
  expect(row()).not.toContain("off");
  fireEvent.click(screen.getByText("disable"));
  expect(row()).toContain("off");
});

test("a scope that mounts while the sheet is open is listed", () => {
  function Later() {
    const [there, setThere] = useState(false);
    return (
      <>
        <button onClick={() => setThere(true)}>mount</button>
        {there && <Bind keys={[{ keys: "escape", label: "Close the dossier", group: "THIS PANEL", run: () => {} }]} modal />}
      </>
    );
  }
  render(<><Later /><ShortcutsHelp /></>);
  press("?");
  expect(screen.queryByText("Close the dossier")).toBeNull();
  fireEvent.click(screen.getByText("mount"));
  expect(screen.getByText("Close the dossier")).toBeTruthy();
});

// `aria-modal` does not contain anything by itself: without a tabbable control
// and something holding Tab, focus walks straight out of the sheet into the
// page behind the scrim.
describe("containment", () => {
  test("it has a close control that closes it", () => {
    render(<ShortcutsHelp />);
    press("?");
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
  });

  test("Tab stays inside it", () => {
    render(<><button>behind</button><ShortcutsHelp /></>);
    press("?");
    const sheet = screen.getByRole("dialog", { name: /keyboard/i });
    const close = screen.getByRole("button", { name: /close/i });
    fireEvent.keyDown(sheet, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    // ...and back out of the only control it has, rather than into the page.
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(sheet.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(screen.getByText("behind"));
  });
});

// The sheet draws above every other overlay, so it has to answer Escape above
// them too -- including one that mounts asynchronously while it is up (an
// import finishing behind it). Newest-registration alone would hand Escape to
// a prompt the reader cannot even see (PR #400 review).
test("a modal that mounts while the sheet is up does not steal Escape", () => {
  const beneath = vi.fn();
  function Later() {
    const [there, setThere] = useState(false);
    return (
      <>
        <button onClick={() => setThere(true)}>mount</button>
        {there && <Bind keys={[{ keys: "escape", run: beneath }]} modal />}
      </>
    );
  }
  render(<><Later /><ShortcutsHelp /></>);
  press("?");
  fireEvent.click(screen.getByText("mount"));
  press("Escape");
  expect(beneath).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog", { name: /keyboard/i })).toBeNull();
  // ...and once the sheet is gone, it is that overlay's key again.
  press("Escape");
  expect(beneath).toHaveBeenCalledTimes(1);
});

// The dimming is a colour, and a colour is not an answer for anyone using a
// screen reader: without a state in the accessibility tree the sheet reads a
// shortcut that dispatch will refuse exactly like one it will run (PR #400
// review).
test("an unreachable row says so in the accessibility tree, not only in colour", () => {
  render(
    <>
      <Bind keys={[{ ...NEW_SCENE, enabled: false }]} />
      <Bind keys={[{ keys: "mod+k", label: "Go anywhere", group: "ANYWHERE", run: () => {} }]} />
      <ShortcutsHelp />
    </>,
  );
  press("?");
  const row = (label: string) => screen.getByText(label).closest(".shortcuts-row")!;
  expect(row("New scene").getAttribute("aria-disabled")).toBe("true");
  expect(row("Go anywhere").getAttribute("aria-disabled")).toBeNull();
});

test("the sections read most-specific first", () => {
  render(
    <>
      <Bind keys={[{ keys: "x", label: "Anywhere key", group: "ANYWHERE", run: () => {} }]} />
      <Bind keys={[NEW_SCENE]} />
      <Bind keys={[{ keys: "escape", label: "Close", group: "THIS PANEL", run: () => {} }]} />
      <ShortcutsHelp />
    </>,
  );
  press("?");
  const heads = [...screen.getByRole("dialog", { name: /keyboard/i })
    .querySelectorAll(".shortcuts-group")].map((h) => h.textContent);
  expect(heads).toEqual(["THIS PANEL", "IN THIS SCENE", "ANYWHERE"]);
});
