// The shell (108) end to end against a real app server, in fakedom: boot from the printed address, find a chat
// from the menu, leave a half-written post, go elsewhere and come back to it intact, then post it.
// Run by test_app_shell_js.py, which serves a synthetic library and passes GRIMOIRE_APP (base URL) and
// GRIMOIRE_TOKEN, and GRIMOIRE_CHAT, the card chat to play; the shell's own "/static/..." imports are mapped onto the packaged files.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { byText, install, until } from "./fakedom.mjs";
import { RESERVED } from "../../src/grimoire/app/static/shell/router.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static") + path.sep).href;
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    if (specifier.startsWith("/static/")) return next(${JSON.stringify(STATIC)} + specifier.slice(8), context);
    return next(specifier, context);
  }`));

const base = process.env.GRIMOIRE_APP;
const token = process.env.GRIMOIRE_TOKEN;

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

// Home's starting points and the reserved routes they open (home/page.js); each later sprint registers one.
const STARTS = { start: "Start a chat", chats: "Continue a chat", import: "Import", sources: "Your library", providers: "Set up a model" };

test("from the printed address to a kept draft and a post", { skip: !base }, async () => {
  // a reserved route no page in this app has registered yet (read from the live registry, as the shell does)
  const registry = await (await fetch(base + "/shell/pages", { cache: "no-store" })).json();
  const taken = new Set(registry.pages.map((page) => page.route));
  const waiting = Object.keys(RESERVED).filter((route) => !taken.has(route));
  const waitingStart = Object.keys(STARTS).find((route) => !taken.has(route));
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(token);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");

  // the token is gone from the address, and the app opens on Home
  await until(() => window.location.hash === "#/home" && byText(main, "h2", "What next").length, 15000, "Home");
  assert.equal(window.sessionStorage.getItem("grimoire-token"), token);
  const nav = body.querySelector("nav");
  const chatsItem = byText(nav, "a", "Chats")[0];
  assert.ok(chatsItem, "Chats is in the menu");
  // a screen no sprint has added yet is said to be unavailable, not linked; a registered one is a link
  for (const [route, title] of Object.entries(STARTS)) {
    if (taken.has(route)) assert.ok(byText(main, "a", title).length, title + " is linked");
    else assert.ok(byText(main, "div", title + " (not in this app yet)").length, title + " is unavailable");
  }
  assert.ok(waitingStart === undefined || !byText(main, "a", STARTS[waitingStart]).length);

  chatsItem.click();
  await until(() => byText(main, "a", "By character").length && main.querySelector("ul"), 15000, "the chat list");
  assert.equal(nav.querySelector("a[aria-current]").textContent, "Chats");
  // the rail: New chat, the groups, the chats played last and whether a model will answer
  assert.ok(!taken.has("start") || byText(nav, "a", "New chat")[0].getAttribute("href") === "#/start");
  assert.ok(!byText(nav, "a", "Start Chat").length, "Start Chat is the New chat button, not a second item");
  for (const label of ["Play", "System"]) assert.ok(byText(nav, "div", label).length, label + " is a group of the rail");
  await until(() => byText(nav, "div", "Jump back in").length, 15000, "the chats played last");
  assert.ok(nav.querySelectorAll("a.g-nav-recent").some((node) => node.getAttribute("href") === "#/chat/" + encodeURIComponent(process.env.GRIMOIRE_CHAT)));
  assert.ok(byText(nav, "div", "Model ready").length || byText(nav, "div", "No model set up").length);
  // the menu button opens the rail, and the rail's own button closes it (on a phone the open rail covers the header)
  const menu = body.querySelector("button.g-menu");
  menu.click();
  assert.ok(nav.classList.contains("g-open") && menu.getAttribute("aria-expanded") === "true");
  nav.querySelector("button.g-nav-close").click();
  assert.ok(!nav.classList.contains("g-open") && menu.getAttribute("aria-expanded") === "false");
  // the header's theme button names the theme it switches to, and switches
  const theme = body.querySelector("button.g-theme");
  assert.equal(theme.textContent, "Dark");
  theme.click();
  assert.equal(document.documentElement.getAttribute("data-theme"), "dark");
  assert.equal(theme.textContent, "Light");
  theme.click();
  const chatHash = "#/chat/" + encodeURIComponent(process.env.GRIMOIRE_CHAT);
  const first = main.querySelector("ul").querySelectorAll("a").find((node) => node.getAttribute("href") === chatHash);
  assert.ok(first, "the card chat is listed, linked by its exact ref");
  // the palette finds the same chat by a word of its name, and Enter opens it
  const name = first.querySelector("span").textContent;
  window.location.hash = "#/home";
  await until(() => byText(main, "h2", "What next").length, 15000, "Home before the palette");
  for (const listener of document.listeners.get("keydown")) listener({ key: "k", ctrlKey: true, preventDefault() {} });
  const box = await until(() => body.querySelector("dialog") && body.querySelector("dialog").querySelector("input"), 15000, "the palette");
  assert.equal(document.activeElement, box);
  assert.ok(byText(body.querySelector("dialog"), "li", "Theme: dark").length, "commands are listed before anything is typed");
  let kept = false;  // Ctrl+K again stays the app's: the browser's own would take focus out of the dialog
  for (const listener of document.listeners.get("keydown")) listener({ key: "k", ctrlKey: true, preventDefault() { kept = true; } });
  assert.ok(kept && body.querySelectorAll("dialog").length === 1, "a second Ctrl+K opens nothing and is kept");
  const composing = { key: "Enter", isComposing: true, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  body.querySelector("dialog").dispatchEvent(composing);  // Enter that confirms an input method's text
  assert.ok(!composing.defaultPrevented && body.querySelector("dialog"), "composing text chooses nothing");
  type(box, "zzzz");
  await until(() => byText(body.querySelector("dialog"), "div", "Nothing matches.").length, 15000, "no results");
  assert.equal(box.getAttribute("aria-activedescendant"), null, "nothing is active with no results");
  assert.equal(box.getAttribute("aria-expanded"), "false");
  type(box, name.split(/\s+/)[0]);
  await until(() => byText(body.querySelector("dialog"), "li", "chat").length, 15000, "a chat in the results");
  const option = byText(body.querySelector("dialog"), "li", name).at(-1);
  assert.ok(option, "the chat is found by its name");
  option.click();
  await until(() => window.location.hash === chatHash && !body.querySelector("dialog"), 15000, "the chat from the palette");
  const composer = await until(() => main.querySelector("textarea"), 15000, "the chat");
  assert.equal(window.location.hash, chatHash);
  assert.equal(document.activeElement, body.querySelector("h1"), "focus moves to the new page's heading");
  composer.focus();
  type(composer, "Hello over the shell.");

  // go elsewhere by the menu and come back: the draft is still there, and so is the person, in the composer
  byText(body.querySelector("nav"), "a", "Home").at(0).focus();  // a click on a link takes focus first
  window.location.hash = "#/home";
  await until(() => byText(main, "h2", "What next").length, 15000, "Home again");
  assert.equal(document.activeElement, body.querySelector("h1"), "a page with no control to return to starts at its heading");
  window.location.hash = chatHash;
  const again = await until(() => main.querySelector("textarea"), 15000, "the chat again");
  assert.equal(again.value, "Hello over the shell.");
  await until(() => document.activeElement === again, 15000, "focus back in the composer");

  // a person who picks a control while the page still loads keeps it: the remembered composer does not take over
  window.location.hash = "#/home";
  await until(() => byText(main, "h2", "What next").length, 15000, "Home once more");
  const fetchNow = globalThis.fetch;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  globalThis.fetch = async (url, options) => {  // hold the chat's answers back, as a slow device would
    if (String(url).startsWith("/api/") && !String(url).startsWith("/api/library")) await held;
    return fetchNow(url, options);
  };
  window.location.hash = chatHash;
  const allChats = await until(() => byText(main, "a", "All chats").at(-1), 15000, "the chat, still loading");
  allChats.focus();
  release();
  globalThis.fetch = fetchNow;
  await until(() => byText(main, "button", "Pause").length, 15000, "the scene after a slow load");
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(document.activeElement, allChats, "the control chosen during the load keeps focus");

  // post it: the post shows in the chat and the draft is cleared
  const before = main.querySelectorAll("div").filter((node) => node.className === "chat-post").length;
  await until(() => byText(main, "button", "Pause").length, 15000, "the scene");
  const seat = byText(main, "button", "Take your seat").at(-1);  // a fresh device seats its player once
  if (seat) {
    seat.click();
    await until(() => !byText(main, "button", "Take your seat").length, 15000, "a seat");
  }
  byText(main, "button", "Post").at(-1).click();
  await until(() => main.querySelectorAll("div").filter((node) => node.className === "chat-post").length > before, 30000, "the post");
  await until(() => !body.querySelectorAll("button").some((node) => node.disabled), 30000, "the answer");
  assert.equal(main.querySelector("textarea").value, "");
  assert.equal(window.sessionStorage.getItem("grimoire-draft:chat:composer:" + decodeURIComponent(chatHash.split("/")[2])), null);

  // an address no page owns says so; a reserved one names its sprint
  if (waiting.length) {
    window.location.hash = "#/" + waiting[0];
    await until(() => byText(main, "strong", "is not available yet").length, 15000, "an unregistered reserved route");
    assert.ok(byText(main, "div", "Sprint " + RESERVED[waiting[0]].owner).length);
  }
  window.location.hash = "#/nowhere";
  await until(() => body.querySelector("h1").textContent === "Not found", 15000, "an unknown route");
});
