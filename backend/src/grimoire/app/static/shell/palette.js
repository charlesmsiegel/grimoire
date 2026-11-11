// The command palette, search, shortcuts and themes (108). One box finds any page, any chat or character by its
// name, any command and any line that was said; the keyboard reaches it from anywhere (Ctrl+K or Cmd+K, or "/"
// outside a text box). Ranking, merging and theme choice are pure functions, so they run under `node --test`; the
// dialog is built from ui.js.

import { button, el, id } from "./ui.js";

export const THEMES = Object.freeze(["system", "light", "dark"]);
const THEME_KEY = "grimoire-theme";
const LIMIT = 12;

// How well `text` matches `query`: null when some word of the query is missing, else a score (higher is
// better) that favours a word the text starts with and then a shorter text. Case and accents do not count.
export function score(query, text) {
  const words = fold(query).split(/\s+/).filter(Boolean);
  const target = fold(text);
  if (!words.length) return 0;
  let total = 0;
  for (const word of words) {
    const at = target.indexOf(word);
    if (at === -1) return null;
    total += at === 0 ? 3 : /\s/.test(target[at - 1]) ? 2 : 1;
  }
  return total - target.length / 1000;
}

// Text with case and accents taken out. The app's line search folds the same way (adapters/shell/lines.py).
export function fold(text) {
  return String(text || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

// How many characters of a query count: its folded text without spaces. A source asks for a least number.
export function queryLength(query) {
  return fold(query).replace(/\s+/g, "").length;
}

// The items that match, best first, at most `limit`: each item is { label, detail?, keywords?, run }. Items
// that match equally keep their given order (pages before chats before commands, as the caller lists them).
export function rank(query, items, limit = LIMIT) {
  return items
    .map((item, index) => ({ item, index, score: score(query, [item.label, item.keywords || ""].join(" ")) }))
    .filter((entry) => entry.score !== null)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, limit)
    .map((entry) => entry.item);
}

// The person's theme, "system" unless they picked one.
export function readTheme(storage) {
  try {
    const value = storage && storage.getItem(THEME_KEY);
    return THEMES.includes(value) ? value : "system";
  } catch (error) { return "system"; }
}

// Apply and keep a theme: `root` is the <html> element (its data-theme switches shell.css's tokens).
export function setTheme(choice, root, storage) {
  const theme = THEMES.includes(choice) ? choice : "system";
  if (root) {
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }
  try { if (storage) storage.setItem(THEME_KEY, theme); } catch (error) { /* this page keeps it */ }
  return theme;
}

// The theme showing now: the person's choice, or the system's when they have made none.
export function shownTheme(storage, prefersDark) {
  const choice = readTheme(storage);
  return choice === "system" ? (prefersDark ? "dark" : "light") : choice;
}

// Whether a key is Ctrl+K or Cmd+K.
export function isPaletteKey(event) {
  return (event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === "k";
}

// Whether a key press opens the palette: Ctrl+K, Cmd+K or "/", and only when the person is not typing. In a text
// box each is the person's own key: "/" is text, and Ctrl+K deletes to the end of the line on a Mac.
export function opensPalette(event, typing) {
  if (typing) return false;
  return isPaletteKey(event) || (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey);
}

// Whether the focus is in something the person types into, where a bare key is text, not a shortcut.
export function isTyping(node) {
  if (!node || !node.tagName) return false;
  const tag = node.tagName.toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || node.isContentEditable === true;
}

// What the palette lists, by what an item is (`group`), in the order equal matches are shown. A line that was
// said (`line: true`) is not ranked with them: the app matched it by its words, and it comes after everything else.
export const GROUPS = Object.freeze(["page", "chat", "character", "command"]);
const LINE_ROOM = 4;

// The list for a query: the named items that match, best first (equal matches as GROUPS orders them), then the
// lines the app found, in the order it gave them. At most `limit` in all; when both have more than fits, the
// lines keep LINE_ROOM places. Before anything is typed, every named item and no line.
export function merge(query, items, lines = [], limit = LIMIT) {
  const order = (item) => { const at = GROUPS.indexOf(item.group); return at === -1 ? GROUPS.length : at; };
  const named = [...items].sort((a, b) => order(a) - order(b));  // a stable sort: each group keeps its own order
  if (!String(query || "").trim()) return rank("", named, Infinity);
  const ranked = rank(query, named, limit);
  const said = lines.slice(0, Math.max(LINE_ROOM, limit - ranked.length));
  return [...ranked.slice(0, limit - said.length), ...said];
}

// The search over what was said (the app's shell.search_lines): asked from LINE_MIN characters up, once the
// typing has rested LINE_WAIT milliseconds, with at most LINE_MAX characters.
export const LINE_MIN = 2;
export const LINE_WAIT = 250;
export const LINE_MAX = 200;

// The library's characters as items, from sources.catalog's answer: each by its name (its exact ref where it has
// none), with the source it is in, since two sources may each have a character of one name. `open(ref)` opens one.
export function characterItems(catalog, open) {
  const titles = new Map((catalog.sources || []).map((source) => [source.source, source.title || source.source]));
  return (catalog.entries || []).filter((entry) => !entry.problem).map((entry) => ({
    label: entry.name || entry.ref, group: "character", keywords: entry.ref,
    detail: "character · " + (titles.get(entry.source) || entry.source),
    run: () => open(entry.ref),
  }));
}

// The lines shell.search_lines found, as items: the words in quotation marks, then who said them, in which chat
// and at which turn. `open(line)` opens the chat at the line.
export function lineItems(answer, open) {
  return (answer.lines || []).map((line) => ({
    label: "“" + line.text + "”", line: true, group: "line",
    detail: [line.speaker || "Narrator", line.title, "turn " + line.turn].join(" · "),
    run: () => open(line),
  }));
}

// What to say under the box about a line search that a cap stopped, or "" when every chat was looked through.
export function lineNote(answer) {
  const of = answer.searched + " of " + answer.chats + " chats";
  if (answer.stopped === "time") return "Still reading older chats: " + of + " looked through so far.";
  if (answer.stopped === "chats") return "Only the " + answer.searched + " most recent chats were looked through for what was said.";
  if (answer.stopped === "unreadable") return "Some chats could not be read: " + of + " looked through for what was said.";
  return "";
}

// One answer of a source, as the palette keeps it: a source may answer a plain list of items, or
// { items, note, again } (`note` is said under the box; `again` asks the palette to ask once more).
function answerOf(answer) {
  if (Array.isArray(answer)) return { items: answer, note: "", again: false };
  return { items: (answer && answer.items) || [], note: (answer && answer.note) || "", again: Boolean(answer && answer.again) };
}

const same = (a, b) => Boolean(a && b) && a.label === b.label && a.detail === b.detail && a.group === b.group;

// Open the palette. `items()` gives what is always there (pages, commands). Each of `sources` finds more for
// what is typed: { name, search(query), min, wait, lines }. `name` says what it searches ("Chats"); `search`
// answers items, or rejects; a query shorter than `min` characters is not asked; `wait` is how long the typing
// must rest first, in milliseconds (a source that asks the app on every query sets it); `lines` marks a source
// of lines that were said. Only the answer to what is typed now is shown, and a source that fails is named under
// the box while the others' results stay. Choosing an item closes the palette and runs it. Returns the dialog.
export function openPalette({ items, sources = [], label = "Search pages, chats, characters, commands and anything that was said",
  placeholder = "Search pages, chats, characters and anything that was said", again = 12 }) {
  const returnFocus = document.activeElement;
  const listId = id("palette");
  const input = el("input", { type: "search", class: "g-input g-palette-input", "aria-label": label,
    placeholder, autocomplete: "off", role: "combobox", "aria-expanded": "true", "aria-controls": listId });
  const results = el("ul", { id: listId, class: "g-palette-results", role: "listbox", "aria-label": "Results" });
  const status = el("div", { class: "g-muted g-palette-status", "aria-live": "polite" });
  const dialog = el("dialog", { class: "g-dialog g-palette", "aria-label": label }, input, status, results);
  let shown = [];
  let active = 0;
  let asked = 0;
  let timers = [];
  // per source, for what is typed now: its items, its note, why it failed, and whether its answer is awaited
  const found = new Map();
  const reset = () => { for (const source of sources) found.set(source, { items: [], note: "", error: null, pending: false }); };
  reset();

  const close = () => {
    if (!dialog.open) return;
    asked += 1;  // an answer still on its way is for nobody
    for (const timer of timers) clearTimeout(timer);
    dialog.close();
    dialog.remove();
    if (returnFocus && returnFocus.focus) returnFocus.focus();
  };
  const choose = (item) => { close(); item.run(); };
  const mark = () => {
    // With no results nothing is active: a stale activedescendant would point a screen reader at a removed node.
    input.removeAttribute("aria-activedescendant");
    input.setAttribute("aria-expanded", String(shown.length > 0));
    results.querySelectorAll("li").forEach((node, index) => {
      node.setAttribute("aria-selected", String(index === active));
      if (index === active) input.setAttribute("aria-activedescendant", node.id);
    });
  };
  const say = () => {
    const parts = [];
    if (!shown.length) parts.push(sources.some((source) => found.get(source).pending) ? "Searching…" : "Nothing matches.");
    for (const source of sources) {
      const state = found.get(source);
      if (state.error) parts.push(source.name + " could not be searched: " + state.error);
      else if (state.note) parts.push(state.note);
    }
    const text = parts.join(" ");
    if (status.textContent !== text) status.textContent = text;  // said once, not at every answer
  };
  const render = () => {
    // Before anything is typed the list is everything offered without asking the app, every page and command:
    // a cap there would hide the commands behind the pages once enough slices register one.
    const named = [...items()];
    const lines = [];
    for (const source of sources) (source.lines ? lines : named).push(...found.get(source).items);
    const before = shown[active];
    shown = merge(input.value, named, lines);
    // an answer that comes later adds to the list: the option the person had moved to stays the active one
    active = Math.max(0, shown.findIndex((item) => same(item, before)));
    results.replaceChildren(...shown.map((item) => el("li", { id: id("option"), role: "option", class: "g-palette-option",
      onclick: () => choose(item) }, el("span", { class: item.line ? "g-palette-line" : "g-palette-name" }, item.label),
      item.detail ? el("span", { class: "g-muted g-palette-detail" }, " " + item.detail) : null)));
    say();
    mark();
  };
  const ask = async (source, query, ticket, left) => {
    const state = found.get(source);
    try {
      const answer = answerOf(await source.search(query));
      if (ticket !== asked || !dialog.open) return;
      state.items = answer.items;
      state.note = answer.note;
      if (answer.again && left > 0) { render(); await ask(source, query, ticket, left - 1); return; }
    } catch (error) {
      if (ticket !== asked || !dialog.open) return;
      state.error = (error && error.message) || String(error);  // what it had found before, if anything, stays
    }
    state.pending = false;
    render();
  };
  const search = () => {
    const query = input.value;
    const ticket = ++asked;  // only the newest search's answer is shown
    for (const timer of timers) clearTimeout(timer);
    timers = [];
    reset();
    shown = [];
    for (const source of sources) {
      if (queryLength(query) < (source.min || 1)) continue;
      found.get(source).pending = true;
      if (source.wait) timers.push(setTimeout(() => { if (ticket === asked && dialog.open) ask(source, query, ticket, again); }, source.wait));
      else ask(source, query, ticket, again);
    }
    render();
  };

  input.addEventListener("input", search);
  dialog.addEventListener("keydown", (event) => {
    // While an input method composes (Japanese, Chinese...), Enter and the arrows pick its candidates, not ours.
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (shown.length) active = (active + (event.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length;
      mark();
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (shown[active]) choose(shown[active]);
    }
  });
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
  document.body.append(dialog);
  dialog.showModal();
  render();
  input.focus();
  return dialog;
}

// The keyboard shortcuts, as the help dialog lists them.
export const SHORTCUTS = Object.freeze([
  ["Ctrl+K, Cmd+K or /", "Search pages, chats, characters, commands and anything that was said, when you are not typing"],
  ["?", "These shortcuts"],
  ["Escape", "Close a dialog or the menu"],
]);

export function openShortcuts() {
  const returnFocus = document.activeElement;
  const done = () => { dialog.close(); dialog.remove(); if (returnFocus && returnFocus.focus) returnFocus.focus(); };
  const dialog = el("dialog", { class: "g-dialog", "aria-label": "Keyboard shortcuts" },
    el("h2", {}, "Keyboard shortcuts"),
    el("dl", { class: "g-shortcuts" }, SHORTCUTS.flatMap(([keys, what]) => [el("dt", {}, el("kbd", {}, keys)), el("dd", {}, what)])),
    button("Close", done));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); done(); });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}
