// The app shell (108): the frame every page lives in. It owns the navigation, the routes, focus and scroll
// across navigation, drafts, notices and the one client of the app server. A page is a folder of its own
// (static/pages/<id>/: page.json and an ES module); the shell lists it from GET /shell/pages and, when its
// route opens, imports the module and calls its mount(root, ctx). See docs/APP_SHELL.md for the contract.

import * as api from "./api.js";
import { Drafts } from "./drafts.js";
import { THEMES, isPaletteKey, isTyping, openPalette, openShortcuts, opensPalette, rank, readTheme, setTheme, shownTheme } from "./palette.js";
import { href, pageFor, parse, reserved as reservedRoute, takeToken } from "./router.js";
import * as ui from "./ui.js";

const { el } = ui;
// The rail's groups, each made of the registry's slots in order. A page says its slot; the shell says how the
// slots read. Start Chat is the rail's "New chat" button, not an item.
const GROUPS = [["Play", ["home", "play"]], ["Library", ["library"]], ["Table", ["campaign"]], ["System", ["media", "settings"]]];
const NEW_CHAT = "start";
const LAYOUTS = ["reading", "page", "wide", "full"];
const RECENT = 3;
const NOTICE_MS = 8000;

const state = {
  registry: { pages: [], slots: [] }, library: null, ops: new Set(), modules: new Map(),
  current: null,  // { key, cleanup, controller, root }
  counts: new Map(),  // route -> { value, label, bad }: the count beside a page in the rail
  focused: false,  // focus mode: the rail and the header are hidden
  scroll: new Map(),  // location hash -> scrollY, restored on return
  focus: new Map(),  // location hash -> { key, start, end }: the control the person was in, restored on return
};
const drafts = new Drafts(safeSessionStorage());
const frame = {};

function safeSessionStorage() {
  try { return window.sessionStorage; } catch (error) { return null; }
}

// The theme outlives the tab (it is the person's, not the session's): localStorage where the browser allows.
function safeLocalStorage() {
  try { return window.localStorage || null; } catch (error) { return null; }
}

// -- the frame ----------------------------------------------------------------------------------------
function buildFrame() {
  frame.title = el("h1", { id: "g-title", tabindex: "-1" }, "Grimoire");
  frame.crumb = el("div", { class: "g-crumb" });
  frame.menu = el("button", { type: "button", class: "g-button g-menu", "aria-expanded": "false", "aria-controls": "g-nav" }, "Menu");
  frame.actions = el("div", { class: "g-header-actions" });
  frame.search = el("button", { type: "button", class: "g-button g-search", title: "Search (Ctrl+K)", "aria-keyshortcuts": "Control+K Meta+K /" },
    el("span", {}, "Search"), el("kbd", {}, "Ctrl K"));
  frame.search.addEventListener("click", palette);
  frame.theme = el("button", { type: "button", class: "g-button g-theme" });
  frame.theme.addEventListener("click", toggleTheme);
  frame.nav = el("nav", { id: "g-nav", class: "g-nav", "aria-label": "Main" });
  frame.menu.addEventListener("click", () => setMenu(frame.menu.getAttribute("aria-expanded") !== "true"));
  // A tap on any link of the open rail closes it, the current page's too (its address does not change).
  frame.nav.addEventListener("click", (event) => { if (event.target && event.target.closest && event.target.closest("a")) setMenu(false); });
  frame.notices = el("div", { class: "g-notices", "aria-live": "polite" });
  frame.main = el("main", { id: "g-main", class: "g-main", tabindex: "-1" });
  frame.scroll = el("div", { class: "g-scroll" }, frame.notices, frame.main);
  frame.scroll.addEventListener("scroll", recordScroll);
  // A tap on the page beside the open rail closes it.
  frame.scroll.addEventListener("click", () => { if (frame.menu.getAttribute("aria-expanded") === "true") setMenu(false); });
  frame.leaveFocus = el("button", { type: "button", class: "g-button g-small g-leave-focus", hidden: true, onclick: () => setFocusMode(false) },
    "Leave focus · Esc");
  frame.app = el("div", { class: "g-app" },
    frame.nav,
    el("div", { class: "g-body" },
      el("header", { class: "g-header" }, frame.menu, el("div", { class: "g-header-text" }, frame.crumb, frame.title),
        frame.actions, frame.search, frame.theme),
      frame.scroll));
  document.body.replaceChildren(
    el("a", { class: "g-skip", href: "#g-main", onclick: (event) => { event.preventDefault(); frame.main.focus(); } }, "Skip to content"),
    frame.app, frame.leaveFocus);
  showTheme();
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && frame.menu.getAttribute("aria-expanded") === "true") { setMenu(false); frame.menu.focus(); return; }
    if (event.defaultPrevented) return;
    const typing = isTyping(document.activeElement);
    const opens = opensPalette(event, typing);
    if (document.body.querySelector("dialog.g-dialog")) {  // one dialog at a time
      // Ctrl+K is still the app's while one is open: the browser's own would take focus out of the dialog.
      if (isPaletteKey(event)) event.preventDefault();
      return;
    }
    if (event.key === "Escape" && state.focused) { setFocusMode(false); return; }
    if (opens) { event.preventDefault(); palette(); }
    else if (event.key === "?" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); openShortcuts(); }
  });
}

// -- theme and focus ---------------------------------------------------------------------------------------
function prefersDark() {
  try { return Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches); } catch (error) { return false; }
}

// The theme showing now: the one set on the page, else the person's stored choice or the system's.
function currentTheme() {
  const root = document.documentElement;
  const set = root && root.getAttribute("data-theme");
  return set === "dark" || set === "light" ? set : shownTheme(safeLocalStorage(), prefersDark());
}

// The header's button names the theme it switches to, as the design does ("Dark" while light shows).
function showTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  frame.theme.textContent = next === "dark" ? "Dark" : "Light";
  frame.theme.title = "Switch to the " + next + " theme";
}

function toggleTheme() {
  setTheme(currentTheme() === "dark" ? "light" : "dark", document.documentElement, safeLocalStorage());
  showTheme();
}

// Focus mode: only the page shows (a chat's story), with one button and Escape to leave it.
function setFocusMode(on) {
  state.focused = Boolean(on);
  frame.app.classList.toggle("g-focused", state.focused);
  frame.leaveFocus.hidden = !state.focused;
  const current = state.current;
  if (current && typeof current.onFocusMode === "function") {
    try { current.onFocusMode(state.focused); } catch (error) { console.error("grimoire: a page's focus-mode handler failed", error); }
  }
}

function setLayout(name) {
  for (const layout of LAYOUTS) frame.main.classList.toggle("g-layout-" + layout, layout === name);
}

function setMenu(open) {
  frame.menu.setAttribute("aria-expanded", String(open));
  frame.nav.classList.toggle("g-open", open);
}

function buildNav() {
  const usable = (route) => { const page = pageFor(state.registry.pages, route); return page && !blocker(page) ? page : null; };
  // On a phone the open rail covers the header and its Menu button, so it carries its own way out.
  const parts = [el("button", { type: "button", class: "g-button g-small g-nav-close", onclick: () => { setMenu(false); frame.menu.focus(); } }, "Close menu"),
    el("a", { class: "g-wordmark", href: "#/", "aria-label": "Grimoire, version 2: home" },
    el("span", { class: "g-wordmark-name" }, "Grimoire"), el("span", { class: "g-wordmark-tag" }, "v2"))];
  if (usable(NEW_CHAT)) parts.push(el("a", { class: "g-button g-primary g-nav-new", href: href(NEW_CHAT), "data-route": NEW_CHAT }, "New chat"));
  for (const [label, slots] of GROUPS) {
    const pages = state.registry.pages.filter((page) => slots.includes(page.slot) && page.route !== NEW_CHAT);
    if (!pages.length) continue;
    const items = pages.map((page) => {
      const blocked = blocker(page);
      const count = state.counts.get(page.route);
      const node = el("a", { class: "g-nav-item", href: href(page.route), "data-route": page.route }, el("span", {}, page.title),
        count ? el("span", { class: "g-nav-count" + (count.bad ? " g-bad" : ""), "aria-label": count.label }, String(count.value)) : null);
      if (blocked) { node.classList.add("g-blocked"); node.title = blocked; }
      return el("li", {}, node);
    });
    const labelId = ui.id("nav");
    parts.push(el("div", { class: "g-nav-group" }, el("div", { class: "g-eyebrow g-nav-label", id: labelId }, label),
      el("ul", { "aria-labelledby": labelId }, items)));
  }
  frame.recent = el("div", { class: "g-nav-group", hidden: true });
  parts.push(frame.recent, railFoot());
  frame.nav.replaceChildren(...parts);
  showRecent();
}

// The rail's foot: whether a model will answer, and this device.
function railFoot() {
  const library = state.library;
  if (!library) return el("div", { class: "g-nav-foot" }, el("div", { class: "g-muted" }, "No library is open yet."));
  const ready = Boolean(library.generation);
  const paired = (library.peers || []).filter((peer) => peer.paired).length;
  return el("div", { class: "g-nav-foot" },
    el("div", { class: "g-nav-status" }, el("span", { class: "g-dot " + (ready ? "g-good" : "g-bad") }),
      el("span", {}, ready ? "Model ready" : "No model set up")),
    el("div", { class: "g-muted" }, "Device " + (library.device ? "“" + library.device + "”" : "not named") + " · " + paired + " paired"));
}

// "Jump back in": the chats played last. Read again when the rail is rebuilt and after each navigation away
// from a chat, so a chat just played is at the top.
async function showRecent() {
  const box = frame.recent;
  const chatPage = pageFor(state.registry.pages, "chat");
  if (!box || !state.ops.has("chats") || !chatPage || blocker(chatPage)) return;
  let chats;
  try { chats = (await api.call("chats", { filter: "recent", limit: RECENT })).chats; } catch (error) { return; }
  if (box !== frame.recent) return;  // the rail was rebuilt meanwhile
  const labelId = ui.id("nav");
  const items = chats.map((chat) => {
    const title = chatTitle(chat);
    return el("li", {}, el("a", { class: "g-nav-recent", href: href("chat", [chat.chat]) }, ui.avatar(title, { size: 22, key: chat.chat }),
      el("span", { class: "g-nav-recent-title" }, title)));
  });
  box.replaceChildren(el("div", { class: "g-eyebrow g-nav-label", id: labelId }, "Jump back in"), el("ul", { "aria-labelledby": labelId }, items));
  box.hidden = !items.length;
}

function chatTitle(chat) {
  return chat.label || (chat.launch && chat.launch.name) || chat.chat;
}

function markNav(route) {
  for (const node of frame.nav.querySelectorAll("a[data-route]")) {
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

// -- search and commands ----------------------------------------------------------------------------------
// Everything the palette offers without asking the app: every page that can open, then the commands.
function paletteItems() {
  const opens = (page) => page.slot !== "hidden" || (reservedRoute(page.route) || { segments: "x" }).segments === "";
  const pages = state.registry.pages.filter((page) => opens(page) && !blocker(page))
    .map((page) => ({ label: page.title, detail: "page", keywords: page.route, run: () => go(href(page.route)) }));
  const themes = THEMES.map((theme) => ({ label: "Theme: " + theme, detail: theme === readTheme(safeLocalStorage()) ? "current" : "",
    keywords: "appearance dark light mode colours colors", run: () => { setTheme(theme, document.documentElement, safeLocalStorage()); showTheme(); } }));
  return [...pages, ...themes, { label: "Keyboard shortcuts", keywords: "help keys", run: openShortcuts }];
}

// Chats by name, read once per palette from the app's own list (the most recent first, up to its largest).
function chatSearch() {
  const chatPage = pageFor(state.registry.pages, "chat");
  if (!state.ops.has("chats") || !chatPage || blocker(chatPage)) return undefined;
  let listed = null;
  return async (query) => {
    listed = listed || api.call("chats", { filter: "recent", limit: 1000 }).then((answer) => answer.chats);
    const chats = (await listed).map((chat) => ({
      label: chatTitle(chat),
      detail: "chat" + (chat.scope && chat.scope.title ? " · " + chat.scope.title : ""),
      keywords: (chat.launch && chat.launch.name) || "",
      run: () => go(href("chat", [chat.chat])),
    }));
    return rank(query, chats);
  };
}

function palette() {
  if (document.body.querySelector("dialog.g-palette")) return;
  setMenu(false);
  openPalette({ items: paletteItems, more: chatSearch() });
}

function go(target) {
  if (window.location.hash === target) route(); else window.location.hash = target;
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
    // The rail's foot (model, device) is drawn from the library, so it is drawn again with it.
    async refreshLibrary() { await settle(loadLibrary()); buildNav(); markNav(location.route); return state.library; },
    // Load the app again (its pages, ops and library) and open `target`: after first run opens a library, the
    // pages that waited on it become usable.
    async restart(target) {
      if (!live()) return;
      if (target) history.replaceState(null, "", window.location.pathname + target);
      await leave();
      await connect();
    },
    has: (op) => state.ops.has(op),
    registered: (route) => Boolean(pageFor(state.registry.pages, route)),
    navigate(target) { if (!live()) return; if (window.location.hash === target) route(); else window.location.hash = target; },
    setTitle(text) { if (live()) setTitle(text || page.title); },
    setActions(...nodes) { if (live()) frame.actions.replaceChildren(...nodes); },
    // The line above the title: where this page sits ("Chats / Harbor"). Text, or links and text.
    setCrumb(...parts) { if (live()) frame.crumb.replaceChildren(...parts); },
    // How wide the page is: "reading", "page", "wide" or "full" (the page lays out the whole pane itself).
    layout(name) { if (live()) setLayout(name); },
    // Focus mode: hide the rail and the header. `onchange(on)` is told when it changes (Escape leaves it).
    focusMode(on, onchange) {
      if (!live()) return;
      if (onchange !== undefined) state.current.onFocusMode = onchange;
      setFocusMode(on);
    },
    // Scroll the page's pane: to a position, or to its end ("end").
    scrollTo(where) { if (live()) frame.scroll.scrollTop = where === "end" ? frame.scroll.scrollHeight : where; },
    // Show a count beside a page in the rail ({value, label, bad}), or clear it (null).
    setCount(route, count) { if (!live()) return; if (count && count.value) state.counts.set(route, count); else state.counts.delete(route); buildNav(); markNav(location.route); },
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
  const open = state.registry.pages.filter((page) => !blocker(page));
  const usable = open.filter((page) => page.slot !== "hidden");
  const home = usable.find((page) => page.route === "home");
  // First run serves only Library setup, which the rail does not list: it is still where the app opens.
  return (home || usable[0] || open.find((page) => page.route === "setup") || { route: null }).route;
}

async function leave() {
  const current = state.current;
  if (!current) return;
  if (current.ready) state.scroll.set(current.key, frame.scroll.scrollTop || 0);
  rememberFocus(current);  // even before the page is ready: a person may start typing while it still loads
  current.controller.abort();
  ui.closeConfirmations();
  current.root.remove();  // detached: whatever the page still does lands nowhere
  state.current = null;
  if (state.focused) setFocusMode(false);
  if (current.route === "chat") showRecent();
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
  frame.crumb.replaceChildren();
  setLayout(null);
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
  const current = { key, route: location.route, cleanup: null, controller, root, lastFocus: null };
  root.addEventListener("focusin", (event) => { current.lastFocus = event.target; });
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
  frame.scroll.scrollTop = 0;  // a page may scroll itself while it mounts (a chat to its newest turn)
  try {
    const cleanup = await module.mount(root, context(location, page, controller, root));
    if (controller.signal.aborted) { if (typeof cleanup === "function") cleanup(); return; }
    current.cleanup = cleanup;
  } catch (error) {
    if (controller.signal.aborted) return;
    root.replaceChildren(ui.errorBox(error), ui.actions(ui.button("Try again", () => route())));
  }
  // A person who already chose a control while the page mounted keeps it, caret and all: the place they left last
  // time is only where to start when they have not started anywhere yet.
  if (current.lastFocus || !restoreFocus(key, root)) focusTitle();
  if (state.scroll.has(key)) frame.scroll.scrollTop = state.scroll.get(key);  // back where the person left it
  current.ready = true;
}

// Which control the person was in when they left a page, by its data-focus-key (every field with a draft has one;
// a page may give one to any other control), and where the caret was. Ids are made fresh on every mount, so the
// key is what finds the same control again. The last control focused on the page counts, not whatever holds
// focus as the page goes: a click on a link in the menu moves focus there first.
function rememberFocus(current) {
  const active = document.activeElement;
  const control = active && current.root.contains(active) && active !== current.root ? active : current.lastFocus;
  const key = control && control.getAttribute ? control.getAttribute("data-focus-key") : null;
  if (!key || !current.root.contains(control)) { state.focus.delete(current.key); return; }
  const caret = typeof control.selectionStart === "number"
    ? { start: control.selectionStart, end: control.selectionEnd } : {};
  state.focus.set(current.key, { key, ...caret });
}

// Put the person back in the control they left, with the caret where it was; false when there is none to return
// to (a first visit, or the control is gone), so the shell focuses the page's heading instead.
function restoreFocus(key, root) {
  const kept = state.focus.get(key);
  if (!kept) return false;
  const control = [...root.querySelectorAll("[data-focus-key]")].find((node) => node.getAttribute("data-focus-key") === kept.key);
  if (!control || control.disabled) return false;
  control.focus({ preventScroll: true });
  if (typeof kept.start === "number" && typeof control.setSelectionRange === "function") {
    const end = String(control.value || "").length;
    try { control.setSelectionRange(Math.min(kept.start, end), Math.min(kept.end, end)); } catch (error) { /* not a text box */ }
  }
  return true;
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
    if (current && current.ready) state.scroll.set(current.key, frame.scroll.scrollTop || 0);
  }, 100);
}

// The library's own answer (device, peers, model), or null before one is open (first run: the app serves only
// its setup ops then, and says so in the registry's op list).
async function loadLibrary() {
  if (!state.ops.has("library")) { state.library = null; return; }
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
    state.registry = await api.registry();
    state.ops = new Set(state.registry.ops || []);
    await loadLibrary();
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
  if (document.documentElement) setTheme(readTheme(safeLocalStorage()), document.documentElement, null);
  buildFrame();
  try { history.scrollRestoration = "manual"; } catch (error) { /* an older browser restores on its own */ }
  window.addEventListener("hashchange", async () => {
    if (adoptToken()) { await leave(); await connect(); return; }  // a new serve: a new key and maybe new pages
    route();
  });
  await connect();
}

start();
