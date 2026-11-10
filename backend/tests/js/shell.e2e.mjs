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

test("from the printed address to a kept draft and a post", { skip: !base }, async () => {
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
  // a screen no sprint has added yet is said to be unavailable, not linked
  assert.ok(byText(main, "div", "Import (not in this app yet)").length);

  chatsItem.click();
  await until(() => byText(main, "a", "By character").length && main.querySelector("ul"), 15000, "the chat list");
  assert.equal(nav.querySelector("a[aria-current]").textContent, "Chats");
  const chatHash = "#/chat/" + encodeURIComponent(process.env.GRIMOIRE_CHAT);
  const first = main.querySelector("ul").querySelectorAll("a").find((node) => node.getAttribute("href") === chatHash);
  assert.ok(first, "the card chat is listed, linked by its exact ref");
  first.click();
  const composer = await until(() => main.querySelector("textarea"), 15000, "the chat");
  assert.equal(window.location.hash, chatHash);
  assert.equal(document.activeElement, body.querySelector("h1"), "focus moves to the new page's heading");
  type(composer, "Hello over the shell.");

  // go elsewhere and come back: the draft is still there
  window.location.hash = "#/home";
  await until(() => byText(main, "h2", "What next").length, 15000, "Home again");
  window.location.hash = chatHash;
  const again = await until(() => main.querySelector("textarea"), 15000, "the chat again");
  assert.equal(again.value, "Hello over the shell.");

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
  window.location.hash = "#/import";
  await until(() => byText(main, "strong", "is not available yet").length, 15000, "an unregistered reserved route");
  assert.ok(byText(main, "div", "Sprint 110").length);
  window.location.hash = "#/nowhere";
  await until(() => body.querySelector("h1").textContent === "Not found", 15000, "an unknown route");
});
