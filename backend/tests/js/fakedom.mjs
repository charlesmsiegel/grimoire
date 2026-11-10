// A small stand-in for the browser, enough to run the shell (108) and its pages under Node against a real
// app server: elements, text, attributes, events, a few selectors, focus, the address and sessionStorage.
// Test infrastructure only; it models what the shell uses, not the whole DOM.

const BOOLEAN = new Set(["hidden", "disabled", "required", "selected"]);
const REFLECTED = ["id", "title", "href", "type", "placeholder", "name", "src", "alt", "rel"];

class Node {
  constructor(document) { this.ownerDocument = document; this.parentNode = null; this.childNodes = []; }
  get textContent() { return this.childNodes.map((child) => child.textContent).join(""); }
  set textContent(text) { this.replaceChildren(text === "" ? [] : new Text(this.ownerDocument, String(text))); }
  _adopt(nodes) {
    return nodes.flat().map((node) => {
      const child = typeof node === "string" ? new Text(this.ownerDocument, node) : node;
      if (child.parentNode) child.remove();
      child.parentNode = this;
      return child;
    });
  }
  append(...nodes) { this.childNodes.push(...this._adopt(nodes)); }
  prepend(...nodes) { this.childNodes.unshift(...this._adopt(nodes)); }
  replaceChildren(...nodes) {
    for (const child of this.childNodes) child.parentNode = null;
    this.childNodes = [];
    this.append(...nodes);
  }
  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.childNodes;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentNode = null;
  }
  replaceWith(node) {
    const parent = this.parentNode;
    if (!parent) return;
    const [child] = parent._adopt([node]);
    parent.childNodes.splice(parent.childNodes.indexOf(this), 1, child);
    this.parentNode = null;
  }
  contains(node) {
    for (let at = node; at; at = at.parentNode) if (at === this) return true;
    return false;
  }
}

class Text extends Node {
  constructor(document, text) { super(document); this.data = text; }
  get textContent() { return this.data; }
  set textContent(text) { this.data = String(text); }
}

function matches(element, selector) {
  const match = /^([a-z0-9]*)((?:\.[\w-]+)*)(?:\[([\w-]+)(?:="([^"]*)")?\])?$/.exec(selector.trim());
  if (!match) throw new Error("fakedom: unsupported selector " + selector);
  const [, tag, classes, attribute, value] = match;
  if (tag && element.tagName.toLowerCase() !== tag) return false;
  for (const name of classes.split(".").filter(Boolean)) if (!element.classList.contains(name)) return false;
  if (attribute && (!element.hasAttribute(attribute) || (value !== undefined && element.getAttribute(attribute) !== value))) return false;
  return true;
}

export class Element extends Node {
  constructor(document, tag) {
    super(document);
    this.tagName = tag.toUpperCase();
    this.attributes = new Map();
    this.listeners = new Map();
    this._value = "";
    this.files = [];
    this.open = false;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  get className() { return this.getAttribute("class") || ""; }
  set className(value) { this.setAttribute("class", value); }
  get classList() {
    const element = this;
    const names = () => element.className.split(/\s+/).filter(Boolean);
    return {
      contains: (name) => names().includes(name),
      add: (name) => { if (!names().includes(name)) element.className = [...names(), name].join(" "); },
      remove: (name) => { element.className = names().filter((item) => item !== name).join(" "); },
      toggle(name, force) {
        const on = force === undefined ? !names().includes(name) : force;
        if (on) this.add(name); else this.remove(name);
        return on;
      },
    };
  }
  get dataset() {
    const data = {};
    for (const [name, value] of this.attributes) if (name.startsWith("data-")) data[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    return data;
  }
  get value() {
    if (this.tagName === "SELECT") {
      const options = this.querySelectorAll("option");
      const chosen = options.find((option) => option.selected) || options[0];
      return chosen ? chosen.getAttribute("value") || "" : "";
    }
    return this._value;
  }
  set value(text) {
    if (this.tagName === "SELECT") { for (const option of this.querySelectorAll("option")) option.selected = option.getAttribute("value") === text; return; }
    this._value = String(text);
  }
  get children() { return this.childNodes.filter((child) => child instanceof Element); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  dispatchEvent(event) {
    event.target = event.target || this;
    for (const listener of this.listeners.get(event.type) || []) listener(event);
    return !event.defaultPrevented;
  }
  click() {
    if (this.disabled) return;
    const event = { type: "click", defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    this.dispatchEvent(event);
    if (!event.defaultPrevented && this.tagName === "A" && this.hasAttribute("href")) this.ownerDocument.defaultView.location.hash = this.getAttribute("href");
  }
  focus() { this.ownerDocument.activeElement = this; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelectorAll(selector) {
    const found = [];
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (!(child instanceof Element)) continue;
        if (selector.split(",").some((part) => matches(child, part))) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
for (const name of BOOLEAN) {
  Object.defineProperty(Element.prototype, name, {
    get() { return this.hasAttribute(name); },
    set(value) { if (value) this.setAttribute(name, ""); else this.removeAttribute(name); },
  });
}
for (const name of REFLECTED) {
  Object.defineProperty(Element.prototype, name, {
    get() { return this.getAttribute(name) || ""; },
    set(value) { this.setAttribute(name, value); },
  });
}

class Storage {
  constructor() { this.items = new Map(); }
  getItem(key) { return this.items.has(key) ? this.items.get(key) : null; }
  setItem(key, value) { this.items.set(key, String(value)); }
  removeItem(key) { this.items.delete(key); }
}

// Install a document and window as globals. `base` is the app server's address: every fetch of a path goes
// there. Returns the window, whose `location.hash` setter fires hashchange as a browser would.
export function install(base) {
  const listeners = new Map();
  const window = {
    scrollY: 0,
    scrollTo(_x, y) { this.scrollY = y; },
    addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); },
    sessionStorage: new Storage(),
  };
  let hash = "";
  window.location = {
    pathname: "/",
    get hash() { return hash; },
    set hash(value) {
      const next = value.startsWith("#") ? value : "#" + value;
      if (next === hash) return;
      hash = next;
      setTimeout(() => { for (const listener of listeners.get("hashchange") || []) listener({ type: "hashchange" }); }, 0);
    },
    replace(url) { this.hash = url.slice(url.indexOf("#") === -1 ? url.length : url.indexOf("#")); },
    reload() {},
  };
  const document = {
    title: "", activeElement: null, listeners: new Map(),
    createElement: (tag) => new Element(document, tag),
    createTextNode: (text) => new Text(document, text),
    addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); },
    defaultView: window,
  };
  document.head = new Element(document, "head");
  document.body = new Element(document, "body");
  Object.defineProperty(document.body, "scrollHeight", { get: () => 1000 });
  window.document = document;
  const realFetch = globalThis.fetch;
  Object.assign(globalThis, {
    window, document, sessionStorage: window.sessionStorage,
    history: { replaceState(_state, _title, url) { hash = url.includes("#") ? url.slice(url.indexOf("#")) : ""; } },
    CSS: { escape: (text) => String(text).replace(/["\\]/g, "\\$&") },
    fetch: (url, options) => realFetch(typeof url === "string" && url.startsWith("/") ? base + url : url, options),
  });
  return window;
}

// Every element under `root` whose own text (its descendants' text) includes `text`, innermost last.
export function byText(root, tag, text) {
  return root.querySelectorAll(tag).filter((node) => node.textContent.includes(text));
}

// Wait until `check()` returns something truthy, or fail after `ms`.
export async function until(check, ms = 15000, what = "the condition") {
  const end = Date.now() + ms;
  for (;;) {
    const value = check();
    if (value) return value;
    if (Date.now() > end) {
      const notices = document.body.querySelectorAll("div").filter((node) => node.getAttribute("role") === "alert");
      throw new Error("timed out waiting for " + what + (notices.length ? "; shown: " + notices.map((node) => node.textContent).join(" | ") : ""));
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}
