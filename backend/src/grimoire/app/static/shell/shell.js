// The app shell (108): the frame every page lives in. It owns the navigation, the routes, focus and scroll
// across navigation, drafts, notices and the one client of the app server. A page is a folder of its own
// (static/pages/<id>/: page.json and an ES module); the shell lists it from GET /shell/pages and, when its
// route opens, imports the module and calls its mount(root, ctx). See docs/APP_SHELL.md for the contract.

import * as api from "./api.js";
import { Drafts } from "./drafts.js";
import { RESERVED, href, pageFor, parse, takeToken } from "./router.js";
import * as ui from "./ui.js";

const { el } = ui;
const SLOT_LABELS = { home: "", play: "Play", library: "Library", campaign: "Campaign", media: "Art and devices",
  settings: "Settings", hidden: "" };
const NOTICE_MS = 8000;

const state = {
  registry: { pages: [], slots: [] }, library: null, ops: new Set(), modules: new Map(),
  current: null,  // { key, cleanup, controller }
  scroll: new Map(),  // location hash -> scrollY, restored on return
  busy: false,
};
const drafts = new Drafts(safeSessionStorage());
const frame = {};

function safeSessionStorage() {
  try { return window.sessionStorage; } catch (error) { return null; }
}

// -- the frame ----------------------------------------------------------------------------------------
function buildFrame() {
  frame.title = el("h1", { id: "g-title", tabindex: "-1" }, "Grimoire");
  frame.menu = el("button", { type: "button", class: "g-button g-menu", "aria-expanded": "false", "aria-controls": "g-nav" }, "Menu");
  frame.actions = el("div", { class: "g-header-actions" });
  frame.nav = el("nav", { id: "g-nav", class: "g-nav", "aria-label": "Main" });
  frame.notices = el("div", { class: "g-notices", "aria-live": "polite" });
  frame.main = el("main", { id: "g-main", class: "g-main", tabindex: "-1" });
  frame.menu.addEventListener("click", () => setMenu(frame.menu.getAttribute("aria-expanded") !== "true"));
  document.body.replaceChildren(
    el("a", { class: "g-skip", href: "#g-main", onclick: (event) => { event.preventDefault(); frame.main.focus(); } }, "Skip to content"),
    el("div", { class: "g-app" },
      el("header", { class: "g-header" }, frame.menu, frame.title, frame.actions),
      frame.nav,
      el("div", { class: "g-body" }, frame.notices, frame.main)));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && frame.menu.getAttribute("aria-expanded") === "true") { setMenu(false); frame.menu.focus(); }
  });
}

function setMenu(open) {
  frame.menu.setAttribute("aria-expanded", String(open));
  frame.nav.classList.toggle("g-open", open);
}

function buildNav() {
  const groups = [];
  for (const slot of state.registry.slots) {
    if (slot === "hidden") continue;
    const pages = state.registry.pages.filter((page) => page.slot === slot);
    if (!pages.length) continue;
    const items = pages.map((page) => {
      const blocked = blocker(page);
      const node = el("a", { class: "g-nav-item", href: href(page.route), "data-route": page.route }, page.title);
      if (blocked) { node.classList.add("g-blocked"); node.title = blocked; }
      return el("li", {}, node);
    });
    const label = SLOT_LABELS[slot] || "";
    const labelId = ui.id("nav");
    groups.push(el("div", { class: "g-nav-group" }, label ? el("div", { class: "g-nav-label", id: labelId }, label) : null,
      el("ul", { "aria-labelledby": label ? labelId : undefined }, items)));
  }
  frame.nav.replaceChildren(...groups);
}

function markNav(route) {
  for (const node of frame.nav.querySelectorAll(".g-nav-item")) {
    if (node.dataset.route === route) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  }
}

// Why a page cannot be used here, or null: it says so itself, or an op it needs is not served.
function blocker(page) {
  if (page.unavailable) return page.unavailable;
  if (page.missing && page.missing.length) return "This app does not have " + page.missing.join(", ") + " yet.";
  return null;
}

// -- notices ------------------------------------------------------------------------------------------
function notify(text, { bad = false } = {}) {
  const box = ui.notice(text, { kind: bad ? "bad" : "" });
  const dismiss = ui.button("Dismiss", () => box.remove());
  dismiss.classList.add("g-dismiss");
  box.append(dismiss);
  frame.notices.prepend(box);
  if (!bad) setTimeout(() => box.remove(), NOTICE_MS);
  return box;
}

// -- the context a page gets ------------------------------------------------------------------------------
function context(location, page, controller) {
  const root = frame.main;
  const ctx = {
    route: location.route, path: location.path, query: location.query, page, signal: controller.signal,
    ui, href, api: api.call, picture: api.picture, ApiError: api.ApiError,
    get library() { return state.library; },
    async refreshLibrary() { await loadLibrary(); return state.library; },
    has: (op) => state.ops.has(op),
    registered: (route) => Boolean(pageFor(state.registry.pages, route)),
    navigate(target) { if (window.location.hash === target) route(); else window.location.hash = target; },
    setTitle(text) { frame.title.textContent = text || page.title; document.title = (text || page.title) + " · Grimoire"; },
    setActions(...nodes) { frame.actions.replaceChildren(...nodes); },
    notify,
    // Link a style sheet from the page's own folder (the page's policy allows no inline styles), once.
    styles(file) {
      const url = "/static/pages/" + page.id + "/" + file;
      if (!document.head.querySelector('link[data-page-styles="' + CSS.escape(url) + '"]')) {
        document.head.append(el("link", { rel: "stylesheet", href: url, "data-page-styles": url }));
      }
    },
    draft: (key) => drafts.slot(page.id + ":" + key),
    // Run one action: while it runs the page's buttons are disabled (a second tap is not a second post), and a
    // refusal is shown with the server's reason. Returns the work's result, or undefined when it failed.
    async act(work) {
      if (state.busy || controller.signal.aborted) return undefined;
      state.busy = true;
      const buttons = [...root.querySelectorAll("button"), ...frame.actions.querySelectorAll("button")].filter((node) => !node.disabled);
      for (const node of buttons) node.disabled = true;
      try { return await work(); }
      catch (error) { if (!controller.signal.aborted) notify(ui.errorText(error), { bad: true }); return undefined; }
      finally { state.busy = false; for (const node of buttons) node.disabled = false; }
    },
  };
  return ctx;
}

// -- routing ----------------------------------------------------------------------------------------
function defaultRoute() {
  const usable = state.registry.pages.filter((page) => page.slot !== "hidden" && !blocker(page));
  const home = usable.find((page) => page.route === "home");
  return (home || usable[0] || { route: null }).route;
}

async function leave() {
  const current = state.current;
  if (!current) return;
  state.scroll.set(current.key, window.scrollY);
  current.controller.abort();
  state.current = null;
  if (typeof current.cleanup === "function") {
    try { current.cleanup(); } catch (error) { console.error("grimoire: a page's cleanup failed", error); }
  }
}

function show(title, ...children) {
  frame.title.textContent = title;
  document.title = title + " · Grimoire";
  frame.actions.replaceChildren();
  frame.main.replaceChildren(...children);
}

async function route() {
  await leave();
  setMenu(false);
  const location = parse(window.location.hash);
  if (location.route === null) {
    const fallback = defaultRoute();
    if (fallback) { window.location.replace(href(fallback)); return; }
    show("Grimoire", ui.empty("This app has no pages yet."));
    return;
  }
  const key = window.location.hash;
  markNav(location.route);
  const page = pageFor(state.registry.pages, location.route);
  if (!page) {
    const reserved = RESERVED[location.route];
    if (reserved) show(reserved.what, ui.unavailable(reserved.what, "Sprint " + reserved.owner + " has not added this screen to the app yet."));
    else show("Not found", ui.notice("There is no page at " + key + "."), ui.actions(ui.link("Go home", defaultRoute() || "home")));
    focusTitle();
    return;
  }
  const blocked = blocker(page);
  if (blocked) { show(page.title, ui.unavailable(page.title, blocked)); focusTitle(); return; }
  const controller = new AbortController();
  state.current = { key, cleanup: null, controller };
  show(page.title, el("p", { class: "g-muted" }, "Loading…"));
  let module;
  try {
    module = state.modules.get(page.module) || await import(page.module);
    state.modules.set(page.module, module);
  } catch (error) {
    if (!controller.signal.aborted) { show(page.title, ui.errorBox(new Error("this page could not load: " + error.message))); focusTitle(); }
    return;
  }
  if (controller.signal.aborted) return;
  frame.main.replaceChildren();
  window.scrollTo(0, 0);  // a page may scroll itself while it mounts (a chat to its newest turn)
  try {
    const cleanup = await module.mount(frame.main, context(location, page, controller));
    if (controller.signal.aborted) { if (typeof cleanup === "function") cleanup(); return; }
    state.current.cleanup = cleanup;
  } catch (error) {
    if (controller.signal.aborted) return;
    frame.main.replaceChildren(ui.errorBox(error), ui.actions(ui.button("Try again", () => route())));
  }
  focusTitle();
  if (state.scroll.has(key)) window.scrollTo(0, state.scroll.get(key));  // back where the person left it
}

function focusTitle() {
  // Move focus to the new page's heading, so a screen reader announces where the person is now and keyboard
  // users start at the top of the page instead of on a link that is gone.
  if (document.activeElement && frame.main.contains(document.activeElement) && document.activeElement !== frame.main) return;
  frame.title.focus({ preventScroll: true });
}

async function loadLibrary() {
  state.library = await api.call("library", {});
  state.ops = new Set(state.library.ops || []);
}

// -- start ----------------------------------------------------------------------------------------
async function start() {
  const taken = takeToken(window.location.hash);
  if (taken) {
    api.setToken(taken.token);
    history.replaceState(null, "", window.location.pathname + (taken.hash || ""));
  }
  buildFrame();
  if (!api.token()) {
    show("Grimoire", el("p", {}, "Open the address the app printed when it started: it carries this session's key."));
    return;
  }
  try {
    const [registry] = await Promise.all([api.registry(), loadLibrary()]);
    state.registry = registry;
  } catch (error) {
    show("Grimoire", ui.errorBox(error), ui.actions(ui.button("Try again", () => window.location.reload())));
    return;
  }
  buildNav();
  window.addEventListener("hashchange", () => { route(); });
  await route();
}

start();
