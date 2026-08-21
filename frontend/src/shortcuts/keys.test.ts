import { chordOf, formatChord, isTypingTarget } from "./keys";

function key(init: Partial<KeyboardEventInit> & { key: string }): KeyboardEvent {
  return new KeyboardEvent("keydown", init);
}

test("a bare modifier press is not a chord", () => {
  for (const k of ["Control", "Shift", "Alt", "Meta"]) {
    expect(chordOf(key({ key: k }))).toBe("");
  }
});

test("Cmd and Ctrl are the same modifier", () => {
  expect(chordOf(key({ key: "Enter", metaKey: true }))).toBe("mod+enter");
  expect(chordOf(key({ key: "Enter", ctrlKey: true }))).toBe("mod+enter");
});

test("a named key keeps its name, lowercased", () => {
  expect(chordOf(key({ key: "Escape" }))).toBe("escape");
  expect(chordOf(key({ key: "ArrowDown" }))).toBe("arrowdown");
});

// Shift is folded into the character a printable key produces, because that is
// what the reader pressed: "?" IS shift+/ on a US layout and something else on
// a German one, and a binding that named both would only fire on one of them.
test("shift is part of the character, not a modifier on it", () => {
  expect(chordOf(key({ key: "?", shiftKey: true }))).toBe("?");
  expect(chordOf(key({ key: "N", shiftKey: true }))).toBe("n");
});

// ...but a key whose identity does NOT change under shift still needs it named,
// or shift+Enter and Enter would be the same chord.
test("shift is named for keys it cannot change", () => {
  expect(chordOf(key({ key: "Enter", shiftKey: true }))).toBe("shift+enter");
  expect(chordOf(key({ key: "Enter", shiftKey: true, metaKey: true }))).toBe("mod+shift+enter");
});

test("alt is named", () => {
  expect(chordOf(key({ key: "ArrowLeft", altKey: true }))).toBe("alt+arrowleft");
});

describe("isTypingTarget", () => {
  function el(html: string): HTMLElement {
    const host = document.createElement("div");
    host.innerHTML = html;
    return host.firstElementChild as HTMLElement;
  }

  test("prose fields are typing targets", () => {
    expect(isTypingTarget(el("<textarea></textarea>"))).toBe(true);
    expect(isTypingTarget(el("<input />"))).toBe(true);
    expect(isTypingTarget(el('<input type="search" />'))).toBe(true);
    expect(isTypingTarget(el('<input type="number" />'))).toBe(true);
    expect(isTypingTarget(el("<select></select>"))).toBe(true);
    expect(isTypingTarget(el('<div contenteditable="true"></div>'))).toBe(true);
  });

  // A checkbox takes space, not letters, so a shortcut on "n" is not
  // clobbering anything by firing while one has focus.
  test("controls that are not prose are not", () => {
    expect(isTypingTarget(el('<input type="checkbox" />'))).toBe(false);
    expect(isTypingTarget(el("<button></button>"))).toBe(false);
    expect(isTypingTarget(el("<div></div>"))).toBe(false);
  });

  // `keydown` fired on `window` (which is what every one of these listeners
  // sees when nothing has focus) has a target with no tagName at all.
  test("a non-element target is not", () => {
    expect(isTypingTarget(null)).toBe(false);
    expect(isTypingTarget(window)).toBe(false);
    expect(isTypingTarget(document)).toBe(false);
  });
});

describe("formatChord", () => {
  const platform = Object.getOwnPropertyDescriptor(window.navigator, "platform");
  function pretend(value: string) {
    Object.defineProperty(window.navigator, "platform", { value, configurable: true });
  }
  afterEach(() => {
    if (platform) Object.defineProperty(window.navigator, "platform", platform);
  });

  test("mod is the key the reader actually has", () => {
    pretend("MacIntel");
    expect(formatChord("mod+enter")).toBe("⌘ ⏎");
    pretend("Win32");
    expect(formatChord("mod+enter")).toBe("Ctrl ⏎");
  });

  test("letters are shown as the reader would type them", () => {
    pretend("Win32");
    expect(formatChord("n")).toBe("N");
    expect(formatChord("?")).toBe("?");
    expect(formatChord("escape")).toBe("Esc");
  });
});
