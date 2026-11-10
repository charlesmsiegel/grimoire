// The app shell (108): the frame every page lives in. It owns the navigation, the routes, focus and scroll
// across navigation, drafts, notices and the one client of the app server. A page is a folder of its own
// (static/pages/<id>/: page.json and an ES module); the shell lists it from GET /shell/pages and, when its
// route opens, imports the module and calls its mount(root, ctx). See docs/APP_SHELL.md for the contract.

import * as api from "./api.js";
import { Drafts } from "./drafts.js";
import { href, pageFor, parse, reserved as reservedRoute, takeToken } from "./router.js";
import * as ui from "./ui.js";

const { el } = ui;
const SLOT_LABELS = { home: "", play: "Play", library: "Library", campaign: "Campaign", media: "Art and devices",
  settings: "Settings", hidden: "" };
const NOTICE_MS = 8000;

const state = {
  registry: { pages: [], slots: [] }, library: null, ops: new Set(), modules: new Map(),
  current: null,  // { key, cleanup, controller, root }
  scroll: new Map(),  // location hash -> scrollY, restored on return
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
// Everything a page can reach outside its own root goes through here, and stops once the page is left: a request
// that answers after the person has moved on rejects (reason "left") instead of writing into the next page.
function context(location, page, controller, root) {
  const { signal } = controller;
  const live = () => !signal.aborted;
  const settle = async (promise) => {
    const value = await promise;
    if (signal.aborted) throw new api.ApiError("left", "the page was left before the app answered", 0);
    return value;
  };
  let busy = false;  // this page's own: a slow action on a page that was left never blocks the next one
  const ctx = {
    route: location.route, path: location.path, query: location.query, page, signal,
    ui, href, ApiError: api.ApiError,
    api: (op, args) => settle(api.call(op, args, signal)),
    picture: (ref) => settle(api.picture(ref)),
    get library() { return state.library; },
    async refreshLibrary() { await settle(loadLibrary()); return state.library; },
    has: (op) => state.ops.has(op),
    registered: (route) => Boolean(pageFor(state.registry.pages, route)),
    navigate(target) { if (!live()) return; if (window.location.hash === target) route(); else window.location.hash = target; },
    setTitle(text) { if (live()) setTitle(text || page.title); },
    setActions(...nodes) { if (live()) frame.actions.replaceChildren(...nodes); },
    notify(text, options) { return live() ? notify(text, options) : null; },
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
      if (busy || !live()) return undefined;
      busy = true;
      const buttons = [...root.querySelectorAll("button"), ...frame.actions.querySelectorAll("button")].filter((node) => !node.disabled);
      for (const node of buttons) node.disabled = true;
      try { return await work(); }
      catch (error) { if (live()) notify(ui.errorText(error), { bad: true }); return undefined; }
      finally { busy = false; for (const node of buttons) node.disabled = false; }
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
  if (current.ready) state.scroll.set(current.key, window.scrollY);
  current.controller.abort();
  current.root.remove();  // detached: whatever the page still does lands nowhere
  state.current = null;
  if (typeof current.cleanup === "function") {
    try { current.cleanup(); } catch (error) { console.error("grimoire: a page's cleanup failed", error); }
  }
}

function setTitle(title) {
  frame.title.textContent = title;
  document.title = title + " · Grimoire";
}

function show(title, ...children) {
  setTitle(title);
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
    const reserved = reservedRoute(location.route);
    if (reserved) show(reserved.what, ui.unavailable(reserved.what, "Sprint " + reserved.owner + " has not added this screen to the app yet."));
    else show("Not found", ui.notice("There is no page at " + key + "."), ui.actions(ui.link("Go home", defaultRoute() || "home")));
    focusTitle();
    return;
  }
  const blocked = blocker(page);
  if (blocked) { show(page.title, ui.unavailable(page.title, blocked)); focusTitle(); return; }
  const controller = new AbortController();
  const root = el("div", { class: "g-page", "data-page": page.id });
  const current = { key, cleanup: null, controller, root };
  state.current = current;
  show(page.title, el("p", { class: "g-muted" }, "Loading…"));
  let module;
  try {
    module = state.modules.get(page.module) || await import(page.module);
    state.modules.set(page.module, module);
  } catch (error) {
    // A module that failed to import stays failed in this document (the browser caches the failure), so the
    // way out is a reload, not "try again".
    if (!controller.signal.aborted) {
      show(page.title, ui.errorBox(new Error("this page could not load: " + error.message)),
        ui.actions(ui.button("Reload the app", () => window.location.reload())));
      focusTitle();
    }
    return;
  }
  if (controller.signal.aborted) return;
  frame.main.replaceChildren(root);
  window.scrollTo(0, 0);  // a page may scroll itself while it mounts (a chat to its newest turn)
  try {
    const cleanup = await module.mount(root, context(location, page, controller, root));
    if (controller.signal.aborted) { if (typeof cleanup === "function") cleanup(); return; }
    current.cleanup = cleanup;
  } catch (error) {
    if (controller.signal.aborted) return;
    root.replaceChildren(ui.errorBox(error), ui.actions(ui.button("Try again", () => route())));
  }
  focusTitle();
  if (state.scroll.has(key)) window.scrollTo(0, state.scroll.get(key));  // back where the person left it
  current.ready = true;
}

function focusTitle() {
  // Move focus to the new page's heading, so a screen reader announces where the person is now and keyboard
  // users start at the top of the page instead of on a link that is gone.
  if (document.activeElement && frame.main.contains(document.activeElement) && document.activeElement !== frame.main) return;
  frame.title.focus({ preventScroll: true });
}

// Remember where the person is on each address as they scroll, so Back and Forward return them there (the
// browser's own restoration does not know a page re-renders after the hash changes).
// Only once a page is mounted and back in place: the shell's own scroll to the top while a page loads is not the
// person's.
let scrollPending = false;
function recordScroll() {
  if (scrollPending) return;
  scrollPending = true;
  setTimeout(() => {
    scrollPending = false;
    const current = state.current;
    if (current && current.ready) state.scroll.set(current.key, window.scrollY);
  }, 100);
}

async function loadLibrary() {
  state.library = await api.call("library", {});
  state.ops = new Set(state.library.ops || []);
}

// -- start ----------------------------------------------------------------------------------------
// A "#token=..." in the address (the one the app printed, or a new one pasted into an open tab after a
// restart) becomes this tab's key and leaves the address. Returns whether there was one.
function adoptToken() {
  const taken = takeToken(window.location.hash);
  if (!taken) return false;
  api.setToken(taken.token);
  history.replaceState(null, "", window.location.pathname + (taken.hash || ""));
  return true;
}

// Load what the app serves (its pages and its library) and open the address. False when it could not.
async function connect() {
  if (!api.token()) {
    show("Grimoire", el("p", {}, "Open the address the app printed when it started: it carries this session's key."));
    return false;
  }
  try {
    const [registry] = await Promise.all([api.registry(), loadLibrary()]);
    state.registry = registry;
  } catch (error) {
    show("Grimoire", ui.errorBox(error), ui.actions(ui.button("Try again", () => window.location.reload())));
    return false;
  }
  buildNav();
  await route();
  return true;
}

async function start() {
  adoptToken();
  buildFrame();
  try { history.scrollRestoration = "manual"; } catch (error) { /* an older browser restores on its own */ }
  window.addEventListener("scroll", recordScroll);
  window.addEventListener("hashchange", async () => {
    if (adoptToken()) { await leave(); await connect(); return; }  // a new serve: a new key and maybe new pages
    route();
  });
  await connect();
}

start();
