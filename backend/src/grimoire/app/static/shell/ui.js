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

// A button. kind: "primary", "danger" or plain. A button for an action the app does not have is not made:
// use unavailable() instead, so nothing on screen pretends to work.
export function button(label, onclick, { kind = "", title, disabled = false, describedBy } = {}) {
  return el("button", { type: "button", class: "g-button" + (kind ? " g-" + kind : ""), onclick, title,
    disabled, "aria-describedby": describedBy }, label);
}

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
  if (draft) input.addEventListener("input", () => draft.set(input.value));
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

export function notice(text, { kind = "" } = {}) {
  return el("div", { class: "g-notice" + (kind ? " g-" + kind : ""), role: kind === "bad" ? "alert" : "status" }, text);
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
