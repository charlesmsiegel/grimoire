// The shell's design primitives (108): what every page builds its screen from, so the app reads as one product
// and stays accessible. Everything shown from the library (card text, model output) is only ever set as text:
// no primitive here turns a string into markup. Styles are the `g-` classes in shell.css.

import { href } from "./router.js";

// el(tag, attrs, ...children): attrs are attributes, except `class`, `on<event>` (a listener) and `text`. A
// `style` attribute is refused: the page's policy drops inline styles, so it would only look like it worked.
export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "style") throw new TypeError("el: inline styles are refused by the page's policy; use a class");
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
  }
  return node;
}

// A button. kind: "primary", "danger", "dashed" (an "add" action), "linklike" (reads as a link) or plain;
// size: "small" or "large". A button for an action the app does not have is not made: use unavailable()
// instead, so nothing on screen pretends to work.
export function button(label, onclick, { kind = "", size = "", title, disabled = false, describedBy } = {}) {
  return el("button", { type: "button", class: classes("g-button", kind && "g-" + kind, size && "g-" + size), onclick, title,
    disabled, "aria-describedby": describedBy }, label);
}

// A link that looks like a button: for going somewhere (Start a chat), where a button is for doing something.
export function buttonLink(label, route, segments = [], query = undefined, { kind = "", size = "" } = {}) {
  return el("a", { class: classes("g-button", kind && "g-" + kind, size && "g-" + size), href: href(route, segments, query) }, label);
}

function classes(...names) { return names.filter(Boolean).join(" "); }

// A link to a route of the app (see router.js).
export function link(label, route, segments = [], query = undefined) {
  return el("a", { class: "g-link", href: href(route, segments, query) }, label);
}

// A link to a source by its exact ref. With no name (a missing or broken source) it shows the ref itself:
// a source is never replaced by another of the same name.
export function sourceLink(ref, name) {
  const label = name || ref;
  const node = link(label, "source", [ref]);
  node.title = ref;
  if (!name) node.classList.add("g-ref");
  return node;
}

let uid = 0;
export function id(prefix = "g") { uid += 1; return prefix + "-" + uid; }

// A labelled text field. `draft` (ctx.draft(key)) keeps what was typed across navigation until it is cleared.
// `error` shows under the field and is announced, tied to it by aria-describedby.
export function field(label, { value = "", draft, multiline = false, placeholder, required = false, hint,
  name, type = "text", autocomplete = "off" } = {}) {
  const inputId = id("field");
  const hintId = hint ? id("hint") : undefined;
  const errorId = id("error");
  const input = multiline
    ? el("textarea", { id: inputId, class: "g-input", placeholder, required, name, "aria-describedby": hintId })
    : el("input", { id: inputId, class: "g-input", type, placeholder, required, name, autocomplete, "aria-describedby": hintId });
  input.value = draft ? draft.get() || value : value;
  if (draft) {
    input.addEventListener("input", () => draft.set(input.value));
    // The shell puts focus back here when the person returns to this page (see shell.js, restoreFocus).
    input.setAttribute("data-focus-key", "draft:" + draft.key);
  }
  const error = el("div", { id: errorId, class: "g-field-error", role: "alert", hidden: true });
  const wrap = el("div", { class: "g-field" }, el("label", { for: inputId }, label),
    hint ? el("div", { id: hintId, class: "g-muted" }, hint) : null, input, error);
  return {
    node: wrap, input,
    get value() { return input.value; },
    set value(text) { input.value = text; if (draft) draft.set(text); },
    setError(message) {
      error.textContent = message || "";
      error.hidden = !message;
      input.setAttribute("aria-invalid", message ? "true" : "false");
      input.setAttribute("aria-describedby", [hintId, message ? errorId : null].filter(Boolean).join(" ") || errorId);
    },
    clear() { input.value = ""; if (draft) draft.clear(); },
  };
}

// A labelled choice. options: [{value, label}].
export function choice(label, options, { value = "", onchange } = {}) {
  const selectId = id("choice");
  const select = el("select", { id: selectId, class: "g-input" });
  for (const option of options) {
    const node = el("option", { value: option.value }, option.label);
    if (option.value === value) node.selected = true;
    select.append(node);
  }
  if (onchange) select.addEventListener("change", () => onchange(select.value));
  return { node: el("div", { class: "g-field" }, el("label", { for: selectId }, label), select), select,
    get value() { return select.value; } };
}

// A titled section of a page.
export function section(title, ...children) {
  return el("section", { class: "g-section" }, title ? el("h2", {}, title) : null, ...children);
}

// A list of items, each a button or a link (a whole row is one target, easy to tap).
export function list(items, { empty = "Nothing here yet." } = {}) {
  if (!items.length) return el("p", { class: "g-muted" }, empty);
  return el("ul", { class: "g-list" }, items.map((item) => el("li", {}, item)));
}

// A row's content: a title and a muted line under it.
export function rowText(title, detail) {
  return [el("span", { class: "g-row-title" }, title), detail ? el("span", { class: "g-muted g-row-detail" }, detail) : null];
}

// A notice. kind: "soft" (something to know), "bad" (something went wrong: announced) or plain.
export function notice(text, { kind = "" } = {}) {
  return el("div", { class: "g-notice" + (kind ? " g-" + kind : ""), role: kind === "bad" ? "alert" : "status" }, text);
}

// -- the shared pieces of the design (see docs/APP_SHELL.md) ----------------------------------------------

// A small uppercase label over a group or a title.
export function eyebrow(text, { accent = false } = {}) {
  return el("div", { class: classes("g-eyebrow", accent && "g-accent") }, text);
}

// A pill: a kind or a state in a word or two. kind: "accent", "bad", "good" or plain.
export function pill(text, { kind = "" } = {}) {
  return el("span", { class: classes("g-pill", kind && "g-" + kind) }, text);
}

// A card. kind: "hero", "accent", "dashed", "flush" (no padding: for a head strip and rows) or plain.
export function card({ kind = "", tag = "div", label } = {}, ...children) {
  return el(tag, { class: classes("g-card", ...String(kind).split(" ").filter(Boolean).map((name) => "g-" + name)), "aria-label": label }, ...children);
}

// A segmented control: a few choices in one strip, one of them chosen. options: [{value, label}]. Choosing
// one calls onchange(value); `set(value)` moves the choice without calling it.
export function segments(label, options, { value, onchange } = {}) {
  let chosen = value === undefined && options.length ? options[0].value : value;
  const buttons = options.map((option) => el("button", { type: "button", class: "g-segment", "data-value": option.value,
    "aria-pressed": String(option.value === chosen), onclick: () => { if (option.value !== chosen) { set(option.value); if (onchange) onchange(option.value); } } },
    option.label));
  function set(next) {
    chosen = next;
    for (const node of buttons) node.setAttribute("aria-pressed", String(node.getAttribute("data-value") === String(next)));
  }
  return { node: el("div", { class: "g-segments", role: "group", "aria-label": label }, buttons), set, get value() { return chosen; } };
}

// One of a set of selectable cards (or chips, with chip: true): the chosen one shows in accent.
export function selectable(content, { selected = false, onclick, chip = false, title } = {}) {
  return el("button", { type: "button", class: classes("g-select", chip && "g-chip"), "aria-pressed": String(Boolean(selected)), onclick, title },
    content);
}

// The colour of a placeholder picture, one of twelve, always the same for the same key (a ref or a name), so
// a character keeps its colour everywhere it shows.
export function hue(key) {
  let sum = 2166136261;
  for (const char of String(key || "")) sum = Math.imul(sum ^ char.codePointAt(0), 16777619) >>> 0;
  sum = (sum ^ (sum >>> 15)) >>> 0;
  return "g-hue-" + (sum % 12);
}

function initial(name) {
  const first = [...String(name || "").trim()][0];
  return first ? first.toUpperCase() : "?";
}

// Put a picture in a placeholder once it loads: `picture` is a promise of an image URL (ctx.picture(ref)).
// A picture that cannot load leaves the placeholder as it is.
function fill(node, picture) {
  if (!picture || typeof picture.then !== "function") return node;
  picture.then((url) => { if (url) node.replaceChildren(el("img", { src: url, alt: "" })); }, () => {});
  return node;
}

// A character's avatar: its picture, or its initial on its own colour. size: 22, 30, 36, 40 or 56.
export function avatar(name, { size = 40, key, picture } = {}) {
  return fill(el("span", { class: classes("g-avatar", "g-size-" + size, hue(key || name)), "aria-hidden": "true" }, initial(name)), picture);
}

// A few avatars, overlapping: people: [{name, key, picture}].
export function avatars(people, { size = 30, limit = 3 } = {}) {
  return el("span", { class: "g-avatars" }, people.slice(0, limit).map((person) => avatar(person.name, { size, key: person.key, picture: person.picture })));
}

// A 3:4 portrait: the picture, or the initial on the character's colour.
export function portrait(name, { key, picture } = {}) {
  return fill(el("span", { class: classes("g-portrait", hue(key || name)), "aria-hidden": "true" }, initial(name)), picture);
}

// A tile in a grid: a portrait with a name and a line under it, the whole of it one link.
export function tile(name, target, { key, picture, detail } = {}) {
  return el("a", { class: "g-tile", href: target }, portrait(name, { key, picture }),
    el("span", { class: "g-tile-name" }, name), detail ? el("span", { class: "g-muted" }, detail) : null);
}

// A cover band: a world's, a campaign's or an entry's picture behind its name, or its colour until it has one.
export function cover({ key, picture, tall = false } = {}, ...children) {
  const band = el("div", { class: classes("g-cover", tall && "g-tall", hue(key)) }, ...children);
  if (picture && typeof picture.then === "function") {
    picture.then((url) => { if (url) band.prepend(el("img", { src: url, alt: "" })); }, () => {});
  }
  return band;
}

// A search box: a labelled text input with no visible label. oninput gets the text as it is typed.
export function search(label, { value = "", oninput, placeholder } = {}) {
  const input = el("input", { type: "search", class: "g-input g-searchbox", "aria-label": label, placeholder: placeholder || label,
    autocomplete: "off" });
  input.value = value;
  if (oninput) input.addEventListener("input", () => oninput(input.value));
  return input;
}

// A modal with a fixed head and foot and a body that scrolls. Returns { dialog, body, foot, close }; Escape and
// the backdrop close it, and `onclose` runs once however it closes.
export function modal(title, { onclose } = {}) {
  const returnFocus = document.activeElement;
  const body = el("div", { class: "g-modal-body" });
  const foot = el("div", { class: "g-modal-foot" });
  const dialog = el("dialog", { class: "g-dialog g-modal", "aria-label": title },
    el("div", { class: "g-modal-head" }, el("h2", {}, title)), body, foot);
  let closed = false;
  const close = (left) => {
    if (closed) return;
    closed = true;
    openConfirmations.delete(close);
    dialog.close(); dialog.remove();
    if (left !== null && returnFocus && returnFocus.focus) returnFocus.focus();  // null: the page was left
    if (onclose) onclose();
  };
  openConfirmations.add(close);  // left with its page, like a confirmation
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
  document.body.append(dialog);
  dialog.showModal();
  return { dialog, body, foot, close };
}

// What a refusal or failure says, with the server's reason so it can be acted on. The draft that was being
// sent is not touched: the page keeps it for another try.
export function errorText(error) {
  if (error && error.reason) return error.reason + ": " + (error.message || "");
  return (error && error.message) || String(error);
}

export function errorBox(error) {
  return notice(errorText(error), { kind: "bad" });
}

// A feature that is not in this app yet, said plainly with what it waits on: never a button that fails.
export function unavailable(what, blocker) {
  return el("div", { class: "g-unavailable", role: "note" },
    el("strong", {}, what + " is not available yet."), blocker ? el("div", { class: "g-muted" }, blocker) : null);
}

export function empty(text) {
  return el("p", { class: "g-muted g-empty" }, text);
}

// A row of actions.
export function actions(...children) {
  return el("div", { class: "g-actions" }, ...children);
}

// Every confirmation still open, so the shell can answer them "no" when the person leaves the page that asked
// (a modal left behind would block the next page, and a late "yes" would resume an action on a page now gone).
const openConfirmations = new Set();

export function closeConfirmations() {
  for (const done of [...openConfirmations]) done(null);  // no: and focus stays with the new page
}

// Ask before something that cannot be undone. Resolves true or false; Escape, the backdrop and leaving the page
// say no. The dialog names exactly what is about to happen (and at which revision, when that matters).
export function confirm(message, { confirmLabel = "Confirm", danger = false } = {}) {
  return new Promise((resolve) => {
    const returnFocus = document.activeElement;
    const dialog = el("dialog", { class: "g-dialog", "aria-label": "Confirm" });
    const done = (answer) => {
      if (!openConfirmations.delete(done)) return;  // answered once
      dialog.close(); dialog.remove();
      if (answer !== null && returnFocus && returnFocus.focus) returnFocus.focus();
      resolve(answer === true);
    };
    openConfirmations.add(done);
    dialog.append(el("p", {}, message), actions(button("Cancel", () => done(false)),
      button(confirmLabel, () => done(true), { kind: danger ? "danger" : "primary" })));
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); done(false); });
    // A click on the dialog itself, not on its content, is a click on the backdrop around it.
    dialog.addEventListener("click", (event) => { if (event.target === dialog) done(false); });
    document.body.append(dialog);
    dialog.showModal();
  });
}
