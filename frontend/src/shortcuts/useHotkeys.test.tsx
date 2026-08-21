import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { activeHotkeys } from "./registry";
import { useHotkeys, type Hotkey } from "./useHotkeys";

/** A component that binds and renders nothing — the shape every real caller
 *  has, since a binding belongs to whatever is already on screen. */
function Bind({ keys, modal }: { keys: Hotkey[]; modal?: boolean }) {
  useHotkeys(keys, { modal });
  return null;
}

function press(key: string, init: Partial<KeyboardEventInit> = {}, on: Window | Element = window) {
  return fireEvent.keyDown(on, { key, ...init });
}

test("a bound chord runs its action", () => {
  const run = vi.fn();
  render(<Bind keys={[{ keys: "n", run }]} />);
  press("n");
  expect(run).toHaveBeenCalledTimes(1);
});

test("an unbound chord runs nothing", () => {
  const run = vi.fn();
  render(<Bind keys={[{ keys: "n", run }]} />);
  press("m");
  press("n", { metaKey: true });
  expect(run).not.toHaveBeenCalled();
});

// The binding table is read at dispatch, not at registration, so a caller owes
// no memoization and `enabled` is never a render behind what the screen shows.
test("a binding disabled this render does not fire", () => {
  const run = vi.fn();
  function Toggling() {
    const [on, setOn] = useState(true);
    useHotkeys([{ keys: "n", enabled: on, run }]);
    return <button onClick={() => setOn(false)}>off</button>;
  }
  render(<Toggling />);
  press("n");
  expect(run).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("off"));
  press("n");
  expect(run).toHaveBeenCalledTimes(1);
});

test("a fresh closure is used, not the one registration saw", () => {
  const seen: number[] = [];
  function Counting() {
    const [n, setN] = useState(0);
    useHotkeys([{ keys: "n", run: () => seen.push(n) }]);
    return <button onClick={() => setN(n + 1)}>bump</button>;
  }
  render(<Counting />);
  fireEvent.click(screen.getByText("bump"));
  fireEvent.click(screen.getByText("bump"));
  press("n");
  expect(seen).toEqual([2]);
});

describe("typing", () => {
  function withField(keys: Hotkey[]) {
    render(<><Bind keys={keys} /><textarea aria-label="prose" /></>);
    const field = screen.getByLabelText("prose");
    field.focus();
    return field;
  }

  test("a letter typed into prose is prose", () => {
    const run = vi.fn();
    const field = withField([{ keys: "n", run }]);
    press("n", {}, field);
    expect(run).not.toHaveBeenCalled();
  });

  test("a binding may say it survives prose", () => {
    const send = vi.fn();
    const field = withField([{ keys: "mod+enter", whileTyping: true, run: send }]);
    press("Enter", { metaKey: true }, field);
    expect(send).toHaveBeenCalledTimes(1);
  });

  // Focus, not just the event's target: a `keydown` dispatched on `window`
  // while the composer holds the caret is still a keystroke inside prose.
  test("focus counts even when the event was fired at the window", () => {
    const run = vi.fn();
    withField([{ keys: "n", run }]);
    press("n");
    expect(run).not.toHaveBeenCalled();
  });
});

describe("modal scopes", () => {
  test("an overlay silences the view underneath it", () => {
    const below = vi.fn();
    const overlay = vi.fn();
    render(
      <>
        <Bind keys={[{ keys: "n", run: below }]} />
        <Bind keys={[{ keys: "escape", whileTyping: true, run: overlay }]} modal />
      </>,
    );
    press("n");
    expect(below).not.toHaveBeenCalled();
    press("Escape");
    expect(overlay).toHaveBeenCalledTimes(1);
  });

  test("a global binding outlives one", () => {
    const palette = vi.fn();
    render(
      <>
        <Bind keys={[{ keys: "mod+k", global: true, whileTyping: true, run: palette }]} />
        <Bind keys={[{ keys: "escape", run: () => {} }]} modal />
      </>,
    );
    press("k", { metaKey: true });
    expect(palette).toHaveBeenCalledTimes(1);
  });

  // Two overlays that live in different parts of the tree: the one that OPENED
  // last is the one on top, whatever order they mounted in. Mount order alone
  // would hand Escape to the drawer while the palette covers it.
  test("the overlay that opened last is the one Escape reaches", () => {
    const first = vi.fn();
    const second = vi.fn();
    function Two() {
      const [open, setOpen] = useState(false);
      useHotkeys([{ keys: "escape", enabled: open, run: second }], { modal: open });
      return <button onClick={() => setOpen(true)}>open</button>;
    }
    render(<><Two /><Bind keys={[{ keys: "escape", run: first }]} modal /></>);
    press("Escape");
    expect(first).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("open"));
    press("Escape");
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1);
  });

  test("a chord the overlay does not bind reaches nothing", () => {
    const below = vi.fn();
    render(
      <>
        <Bind keys={[{ keys: "escape", run: below }]} />
        <Bind keys={[{ keys: "n", run: () => {} }]} modal />
      </>,
    );
    press("Escape");
    expect(below).not.toHaveBeenCalled();
  });
});

test("the newest scope wins a chord two of them bind", () => {
  const older = vi.fn();
  const newer = vi.fn();
  render(<><Bind keys={[{ keys: "n", run: older }]} /><Bind keys={[{ keys: "n", run: newer }]} /></>);
  press("n");
  expect(newer).toHaveBeenCalledTimes(1);
  expect(older).not.toHaveBeenCalled();
});

test("an unmounted binding is gone", () => {
  const run = vi.fn();
  const { unmount } = render(<Bind keys={[{ keys: "n", run }]} />);
  unmount();
  press("n");
  expect(run).not.toHaveBeenCalled();
});

test("a chord that matched does not also reach the page", () => {
  render(<Bind keys={[{ keys: "n", run: () => {} }]} />);
  expect(press("n")).toBe(false);          // fireEvent returns false when prevented
  expect(press("m")).toBe(true);
});

// The composer's own Enter handler runs first and calls preventDefault; a
// window listener that ignored that would send the turn twice.
test("a keystroke the page already handled is left alone", () => {
  const run = vi.fn();
  render(<Bind keys={[{ keys: "enter", whileTyping: true, run }]} />);
  const e = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
  e.preventDefault();
  window.dispatchEvent(e);
  expect(run).not.toHaveBeenCalled();
});

// A held key repeats at ~30ms. `busy` cannot come back through the closure
// until React has re-rendered, so a send chord held down would send the turn
// several times over before the guard it reads caught up.
test("a held key fires once, not once per repeat", () => {
  const run = vi.fn();
  render(<Bind keys={[{ keys: "mod+enter", whileTyping: true, run }]} />);
  press("Enter", { metaKey: true });
  press("Enter", { metaKey: true, repeat: true });
  press("Enter", { metaKey: true, repeat: true });
  expect(run).toHaveBeenCalledTimes(1);
});

// An IME sends keydown for every keystroke that is still assembling a
// character; none of them is a chord the reader typed.
test("a composing keystroke is not a chord", () => {
  const run = vi.fn();
  render(<Bind keys={[{ keys: "n", run }]} />);
  press("n", { isComposing: true });
  expect(run).not.toHaveBeenCalled();
});

test("what is registered is what the help overlay can list", () => {
  render(
    <>
      <Bind keys={[{ keys: "n", label: "New scene", group: "IN THIS SCENE", run: () => {} }]} />
      <Bind keys={[{ keys: "x", run: () => {} }]} />
    </>,
  );
  expect(activeHotkeys().map((r) => r.key.keys)).toEqual(["n", "x"]);
});
