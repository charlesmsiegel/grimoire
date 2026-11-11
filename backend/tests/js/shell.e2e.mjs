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

// Home's ways on to other screens and the reserved routes they open (home/page.js); each later sprint registers
// one. "Set up a model" is only there while no model is set up.
const STARTS = { start: "Start something new", chats: "All chats", import: "Import a character card", sources: "Open the library" };

function pick(select, value) {
  select.value = value;
  select.dispatchEvent({ type: "change" });
}

test("from the printed address to a kept draft and a post", { skip: !base }, async () => {
  // a reserved route no page in this app has registered yet (read from the live registry, as the shell does)
  const registry = await (await fetch(base + "/shell/pages", { cache: "no-store" })).json();
  const taken = new Set(registry.pages.map((page) => page.route));
  const waiting = Object.keys(RESERVED).filter((route) => !taken.has(route));
  const waitingStart = Object.keys(STARTS).find((route) => !taken.has(route));
  const chatHash = "#/chat/" + encodeURIComponent(process.env.GRIMOIRE_CHAT);
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(token);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");

  // the token is gone from the address, and the app opens on Home
  await until(() => window.location.hash === "#/home" && byText(main, "h2", "Your characters").length, 15000, "Home");
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
  if (byText(nav, "div", "No model set up").length) {
    assert.ok(byText(main, taken.has("providers") ? "a" : "div", "Set up a model").length, "Home says no model will answer");
  } else assert.ok(!byText(main, "a", "Set up a model").length);
  // Home: the chat played last to continue, the chats played last by their exact refs, and the characters
  assert.equal(body.querySelector("h1").textContent.slice(0, 5), "Good ");
  assert.ok(body.querySelector("div.g-crumb").textContent.startsWith("Library on "));
  const hero = main.querySelector("section.home-hero");
  assert.ok(byText(hero, "div", "Continue where you left off").length);
  const recentRows = main.querySelectorAll("a.g-rowcard");
  assert.ok(recentRows.length >= 1 && recentRows.length <= 3, "up to three recent chats");
  assert.ok(recentRows.some((node) => node.getAttribute("href") === chatHash), "the card chat is a recent chat, by its exact ref");
  assert.equal(byText(hero, "a", "Continue chat")[0].getAttribute("href"), recentRows[0].getAttribute("href"), "Continue is the chat played last");
  assert.equal(hero.querySelector("h2").textContent, recentRows[0].querySelector("span.g-title").textContent);
  assert.ok(hero.querySelectorAll("span.home-face").length >= 1 && hero.querySelectorAll("span.home-face").length <= 3, "its cast, as portraits");
  assert.ok(!hero.querySelector("img") && !hero.querySelector("p.g-quote"), "no picture or last line the list does not give");
  // Needs you: the inbox's top item, or that nothing waits; the rail lists the inbox between Home and Chats, with
  // no count while nothing waits
  if (taken.has("inbox")) {
    assert.ok(byText(main, "h2", "Needs you").length);
    await until(() => byText(main, "div", "Nothing needs you.").length, 15000, "Home's inbox card");
    assert.equal(main.querySelector("a.home-top"), null);
    assert.equal(byText(main, "a", "Open the inbox")[0].getAttribute("href"), "#/inbox");
    const play = nav.querySelectorAll("a.g-nav-item").map((node) => node.getAttribute("data-route"));
    assert.ok(play.indexOf("home") < play.indexOf("inbox") && play.indexOf("inbox") < play.indexOf("chats"));
    assert.equal(byText(nav, "a", "Needs you")[0].querySelector("span.g-nav-count"), null);
  } else assert.ok(!byText(main, "h2", "Needs you").length, "nothing is shown for an inbox this app does not have");
  if (taken.has("source") && taken.has("sources")) {
    const tiles = main.querySelectorAll("a.g-tile");
    assert.ok(tiles.length && tiles.every((node) => node.getAttribute("href").startsWith("#/source/")), "characters, each by its exact ref");
  }
  if (taken.has("import")) assert.equal(main.querySelector("a.home-import").getAttribute("href"), "#/import");

  chatsItem.click();
  await until(() => byText(main, "button", "By character").length && main.querySelector("ul.chats-rows"), 15000, "the chat list");
  const current = nav.querySelector("a[aria-current]");
  assert.equal(current.getAttribute("data-route"), "chats");
  // the list: every chat, counted in the rail, grouped by campaign and sorted by what was played last
  const rows = () => main.querySelectorAll("a.chats-row");
  const titles = () => rows().map((node) => node.querySelector("span.g-title").textContent);
  const pressed = (label) => byText(main, "button", label).filter((node) => node.classList.contains("g-segment")).at(-1);
  const total = rows().length;
  assert.ok(total >= 2, "the library's chats");
  assert.equal(current.querySelector("span.g-nav-count").textContent, String(total), "the rail counts the chats");
  assert.equal(pressed("All").getAttribute("aria-pressed"), "true");
  assert.equal(pressed("Campaign").getAttribute("aria-pressed"), "true");
  assert.equal(pressed("Recent").getAttribute("aria-pressed"), "true");
  assert.ok(!byText(main, "button", "Longest").length && !byText(main, "button", "Needs you").length);
  // pins are kept (docs/SHELF.md): the Pinned filter is offered, and every row has its own Pin toggle, unpressed
  assert.equal(byText(main, "button", "Pinned").length, 1, "the Pinned filter");
  assert.equal(main.querySelectorAll("button.chats-pin").length, rows().length);
  assert.ok(main.querySelectorAll("button.chats-pin").every((node) => node.getAttribute("aria-pressed") === "false" && node.textContent === "Pin"));
  const heads = () => main.querySelectorAll("button.chats-head");
  assert.equal(main.querySelector("div.chats-status").textContent, total + " chats in " + heads().length + " groups");
  assert.ok(byText(main, "button", "Not in a campaign").length, "a chat outside any campaign has its own group");
  assert.ok(rows().every((node) => main.querySelectorAll("ul.chats-rows").some((list) => list.contains(node))), "rows are list items");
  // a group hides and shows in place
  heads()[0].click();
  assert.equal(heads()[0].getAttribute("aria-expanded"), "false");
  assert.ok(byText(heads()[0], "span", "Show").length && main.querySelector("div.chats-body").hidden);
  heads()[0].click();
  assert.ok(heads()[0].getAttribute("aria-expanded") === "true" && !main.querySelector("div.chats-body").hidden);
  // ungrouped, by name; the search narrows it; nothing found says so
  pressed("None").click();
  assert.equal(heads().length, 1);
  assert.equal(main.querySelector("div.chats-status").textContent, total + " chats");
  pressed("A–Z").click();
  assert.deepEqual(titles(), [...titles()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true })));
  const searchBox = main.querySelector("input.chats-search");
  type(searchBox, titles()[0]);
  assert.ok(rows().length >= 1 && rows().length < total, "the search narrows the list");
  type(searchBox, "zzzz no such chat");
  assert.ok(!rows().length && byText(main, "p", "No chats match.").length);
  // what was typed and chosen is still there after going elsewhere, and is put back as it was
  window.location.hash = "#/home";
  await until(() => byText(main, "h2", "Your characters").length, 15000, "Home in between");
  window.location.hash = "#/chats";
  await until(() => byText(main, "p", "No chats match.").length, 15000, "the same search again");
  assert.equal(main.querySelector("input.chats-search").value, "zzzz no such chat");
  assert.equal(pressed("None").getAttribute("aria-pressed"), "true");
  type(main.querySelector("input.chats-search"), "");
  pressed("Campaign").click();
  pressed("Recent").click();
  assert.equal(rows().length, total);
  // one character's chats: the filter is in the address, and the character is chosen by its exact ref
  pressed("By character").click();
  const picker = await until(() => window.location.hash === "#/chats?filter=character" && main.querySelector("select"), 15000, "the character picker");
  await until(() => byText(main, "p", "Choose a character to see its chats.").length, 15000, "nothing chosen yet");
  const refs = picker.querySelectorAll("option").map((node) => node.getAttribute("value")).filter(Boolean);
  assert.ok(refs.length, "the characters some chat binds");
  let found = false;
  for (const ref of refs) {
    const before = main.querySelector("div.chats-page");  // the page is drawn again at the new address
    pick(main.querySelector("select"), ref);
    await until(() => window.location.hash === "#/chats?filter=character&source=" + encodeURIComponent(ref)
      && main.querySelector("div.chats-page") !== before && rows().length, 15000, "the chats of " + ref);
    assert.equal(main.querySelector("select").value, ref);
    found = found || rows().some((node) => node.getAttribute("href") === chatHash);
  }
  assert.ok(found, "the card chat is found under its character");
  window.location.hash = "#/chats?filter=character&source=" + encodeURIComponent("card:nobody/character/nobody");
  await until(() => byText(main, "p", "No chats here.").length, 15000, "a character no chat binds");
  assert.equal(main.querySelector("select").value, "card:nobody/character/nobody", "the picker names the exact ref asked for");
  pressed("All").click();
  await until(() => window.location.hash === "#/chats" && rows().length === total, 15000, "every chat again");
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
  const first = rows().find((node) => node.getAttribute("href") === chatHash);
  assert.ok(first, "the card chat is listed, linked by its exact ref");
  // the palette finds the same chat by a word of its name, and Enter opens it
  const name = first.querySelector("span.g-title").textContent;
  window.location.hash = "#/home";
  await until(() => byText(main, "h2", "Your characters").length, 15000, "Home before the palette");
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
  await until(() => byText(main, "h2", "Your characters").length, 15000, "Home again");
  assert.equal(document.activeElement, body.querySelector("h1"), "a page with no control to return to starts at its heading");
  window.location.hash = chatHash;
  const again = await until(() => main.querySelector("textarea"), 15000, "the chat again");
  assert.equal(again.value, "Hello over the shell.");
  await until(() => document.activeElement === again, 15000, "focus back in the composer");

  // a person who picks a control while the page still loads keeps it: the remembered composer does not take over
  window.location.hash = "#/home";
  await until(() => byText(main, "h2", "Your characters").length, 15000, "Home once more");
  const fetchNow = globalThis.fetch;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  globalThis.fetch = async (url, options) => {  // hold the chat's answers back, as a slow device would
    if (String(url).startsWith("/api/") && !String(url).startsWith("/api/library")) await held;
    return fetchNow(url, options);
  };
  window.location.hash = chatHash;
  // the chat's side panel is there before the scene: its Details tab is where Pause is
  const details = await until(() => main.querySelectorAll("button").find((node) => node.textContent === "Details"), 15000, "the chat, still loading");
  assert.ok(!byText(main, "button", "Pause").length, "the scene has not arrived");
  details.focus();
  details.click();
  release();
  globalThis.fetch = fetchNow;
  await until(() => byText(main, "button", "Pause").length, 15000, "the scene after a slow load");
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(document.activeElement, details, "the control chosen during the load keeps focus");
  // the way back to the list is the crumb over the title
  assert.ok(body.querySelector("div.g-crumb").querySelectorAll("a").some((node) => node.textContent === "Chats" && node.getAttribute("href") === "#/chats"));

  // post it: the post shows in the chat and the draft is cleared
  const posts = () => main.querySelectorAll("div.chat-post").length;
  const before = posts();
  const seat = byText(main, "button", "Take your seat").at(-1);  // a fresh device seats its player once
  if (seat) {
    seat.click();
    await until(() => !byText(main, "button", "Take your seat").length, 15000, "a seat");
  }
  const send = byText(main, "button", "Post").at(-1);
  await until(() => !send.disabled, 15000, "a seat to post from");
  send.click();
  await until(() => posts() > before, 30000, "the post");
  // the answer is a take to keep or pass: until then Post stays off, and nothing else does
  await until(() => main.querySelector("article.chat-take") && !body.querySelectorAll("button").some((node) => node.disabled && node !== send), 30000, "the answer");
  assert.ok(send.disabled, "nothing is posted while a take is pending");
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
