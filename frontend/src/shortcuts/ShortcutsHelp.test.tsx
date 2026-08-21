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
