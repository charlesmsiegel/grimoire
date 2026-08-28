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

// ...and so does a printable key with another modifier already held. Bare,
// the reader is choosing a CHARACTER and shift is how their layout produces
// it; holding a modifier they are choosing a KEY, and every app on the machine
// spells that "⌘⇧F". Folding shift in there made ⌘F and ⌘⇧F one chord, which
// is why the design's Search shortcut could not be expressed at all.
test("shift is named once another modifier is holding the key down", () => {
  expect(chordOf(key({ key: "F", shiftKey: true, metaKey: true }))).toBe("mod+shift+f");
  expect(chordOf(key({ key: "f", metaKey: true }))).toBe("mod+f");
  expect(chordOf(key({ key: "F", shiftKey: true, altKey: true }))).toBe("alt+shift+f");
});

test("the two are still different chords, which is the whole point", () => {
  const find = chordOf(key({ key: "f", metaKey: true }));
  const search = chordOf(key({ key: "F", shiftKey: true, metaKey: true }));
  // ⌘F is the browser's Find and must never be taken; ⌘⇧F is ours.
  expect(find).not.toBe(search);
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

  test("alt is the glyph on a mac and the word everywhere else", () => {
    pretend("MacIntel");
    expect(formatChord("alt+arrowleft")).toBe("⌥ ←");
    pretend("Win32");
    expect(formatChord("alt+arrowleft")).toBe("Alt ←");
  });

  // A key with no glyph is still printable rather than raw: the sheet lists
  // whatever a page registered, and nothing here gets to render as "f7".
  test("a key with no glyph is capitalized, not dropped", () => {
    pretend("Win32");
    expect(formatChord("f7")).toBe("F7");
    expect(formatChord("mod+home")).toBe("Ctrl Home");
  });
});
