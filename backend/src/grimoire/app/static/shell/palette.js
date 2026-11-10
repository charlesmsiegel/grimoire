// The command palette, search, shortcuts and themes (108). One box finds any page, any chat by its name and
// any command; the keyboard reaches it from anywhere (Ctrl+K or Cmd+K, or "/" outside a text box). Ranking and
// theme choice are pure functions, so they run under `node --test`; the dialog is built from ui.js.

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

function fold(text) {
  return String(text || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
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

// Whether a key press opens the palette: Ctrl+K or Cmd+K anywhere; "/" when the person is not typing.
export function opensPalette(event, typing) {
  if ((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === "k") return true;
  return event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey;
}

// Whether the focus is in something the person types into, where a bare key is text, not a shortcut.
export function isTyping(node) {
  if (!node || !node.tagName) return false;
  const tag = node.tagName.toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || node.isContentEditable === true;
}

// Open the palette. `items()` gives what is always there (pages, commands); `more(query)` may add items found
// for the query (chats), or reject. Choosing an item closes the palette and runs it. Returns the dialog.
export function openPalette({ items, more, label = "Search pages, chats and commands" }) {
  const returnFocus = document.activeElement;
  const listId = id("palette");
  const input = el("input", { type: "search", class: "g-input g-palette-input", "aria-label": label,
    placeholder: "Search", autocomplete: "off", role: "combobox", "aria-expanded": "true", "aria-controls": listId });
  const results = el("ul", { id: listId, class: "g-palette-results", role: "listbox", "aria-label": "Results" });
  const status = el("div", { class: "g-muted", "aria-live": "polite" });
  const dialog = el("dialog", { class: "g-dialog g-palette", "aria-label": label }, input, status, results);
  let shown = [];
  let active = 0;
  let found = [];
  let asked = 0;

  const close = () => {
    if (!dialog.open) return;
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
  const render = () => {
    shown = rank(input.value, [...items(), ...found]);
    active = 0;
    results.replaceChildren(...shown.map((item) => el("li", { id: id("option"), role: "option", class: "g-palette-option",
      onclick: () => choose(item) }, el("span", {}, item.label), item.detail ? el("span", { class: "g-muted" }, " " + item.detail) : null)));
    status.textContent = shown.length ? "" : "Nothing matches.";
    mark();
  };
  const search = async () => {
    const query = input.value;
    const ticket = ++asked;  // only the newest search's answer is shown
    found = [];
    render();
    if (!more || !query.trim()) return;
    try {
      const extra = await more(query);
      if (ticket === asked && dialog.open) { found = extra; render(); }
    } catch (error) {
      if (ticket === asked && dialog.open) status.textContent = "Chats could not be searched: " + (error.message || error);
    }
  };

  input.addEventListener("input", search);
  dialog.addEventListener("keydown", (event) => {
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
  ["Ctrl+K or Cmd+K", "Search pages, chats and commands"],
  ["/", "The same, when you are not typing"],
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
