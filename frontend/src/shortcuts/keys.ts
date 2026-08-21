/** What a keystroke is called, whether it landed in prose, and how to print it
 *  — the half of the shortcut layer that knows nothing about React.
 *
 *  A *chord* is the string a binding names: `"n"`, `"?"`, `"escape"`,
 *  `"mod+enter"`. It is derived from the event rather than from a key code, so
 *  a binding is written the way the reader would describe it. */

const MODIFIERS = new Set(["Control", "Shift", "Alt", "Meta", "OS", "AltGraph"]);

/** The chord `e` produces, or `""` for a keystroke that is not one: a bare
 *  modifier held down on its own. */
export function chordOf(e: KeyboardEvent): string {
  const key = e.key;
  if (!key || MODIFIERS.has(key)) return "";
  const parts: string[] = [];
  // Cmd and Ctrl are one modifier, the way every ⌘K in this app already
  // treats them: the same binding has to be typeable on both platforms.
  if (e.metaKey || e.ctrlKey) parts.push("mod");
  if (e.altKey) parts.push("alt");
  // Shift is folded into the character a printable key produces, because that
  // is what the reader pressed: "?" IS shift+/ on a US layout and something
  // else on a German one, so naming both would fire on only one of them. A key
  // whose identity shift cannot change ("Enter") still needs it named, or
  // shift+Enter — a newline in the composer — would be the send chord.
  if (e.shiftKey && key.length > 1) parts.push("shift");
  parts.push(key.toLowerCase());
  return parts.join("+");
}

/** Input types that take words. The rest (`checkbox`, `radio`, `range`,
 *  `color`, buttons) take clicks and arrow keys, and a letter pressed over one
 *  is not being typed at anything. */
const PROSE_INPUTS = new Set([
  "text", "search", "url", "email", "password", "tel", "number",
  "date", "datetime-local", "month", "time", "week",
]);

/** Whether `node` is somewhere a keystroke means "a character", so a shortcut
 *  must keep its hands off it. */
export function isTypingTarget(node: EventTarget | null | undefined): boolean {
  const el = node as HTMLElement | null;
  // `keydown` fired at the window — which is what these listeners see whenever
  // nothing on the page holds focus — has a target with no tag at all.
  if (!el || typeof el.tagName !== "string") return false;
  const tag = el.tagName.toLowerCase();
  if (tag === "textarea" || tag === "select") return true;
  if (tag === "input") return PROSE_INPUTS.has(((el as HTMLInputElement).type || "text").toLowerCase());
  // `isContentEditable` is a live computation the attribute alone cannot
  // answer (it inherits), and jsdom implements it; the attribute is the
  // fallback for anywhere that does not.
  if (el.isContentEditable) return true;
  const editable = el.getAttribute?.("contenteditable");
  if (editable === "" || editable === "true") return true;
  const role = el.getAttribute?.("role");
  return role === "textbox" || role === "searchbox" || role === "combobox";
}

const APPLE = /mac|iphone|ipad|ipod/i;

/** Which key "mod" is on this machine. Read at call time rather than at import
 *  so a test can pretend, and so the Android WebView — where `platform` is set
 *  by the system, not by the bundle — answers for itself. */
function onApple(): boolean {
  if (typeof navigator === "undefined") return false;
  return APPLE.test(navigator.platform || navigator.userAgent || "");
}

const GLYPHS: Record<string, string> = {
  alt: "⌥", shift: "⇧", enter: "⏎", escape: "Esc", backspace: "⌫", tab: "⇥",
  arrowup: "↑", arrowdown: "↓", arrowleft: "←", arrowright: "→", " ": "Space",
};

/** A chord as the help overlay shows it: `"mod+enter"` → `⌘ ⏎` or `Ctrl ⏎`. */
export function formatChord(chord: string): string {
  return chord.split("+").map((part) => {
    if (part === "mod") return onApple() ? "⌘" : "Ctrl";
    if (part === "alt" && !onApple()) return "Alt";
    if (GLYPHS[part]) return GLYPHS[part];
    // A letter is shown the way a keycap is, without claiming shift: "N" is
    // the key, not the capital.
    return part.length === 1 ? part.toUpperCase() : part[0].toUpperCase() + part.slice(1);
  }).join(" ");
}
