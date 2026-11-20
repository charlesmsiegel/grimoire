// Every screen of the app (108–114) through the structural accessibility audit (a11y.mjs), in fakedom against a
// real app server: each route the shell registers, a chat and its campaign hub, each Import tab, and the shell's
// command palette and a modal while open. It asserts no violation of any rule the tree alone decides; layout,
// contrast and real assistive technology are the browser suite's and a person's (docs/ACCEPTANCE.md).
// Run by test_app_a11y_js.py, which serves desktop_library's synthetic library and passes GRIMOIRE_APP,
// GRIMOIRE_TOKEN, GRIMOIRE_CHAT and GRIMOIRE_CAMPAIGN; "/static/..." imports map onto the package.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { audit, describe } from "./a11y.mjs";
import { byText, install, until } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static") + path.sep).href;
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    if (specifier.startsWith("/static/")) return next(${JSON.stringify(STATIC)} + specifier.slice(8), context);
    return next(specifier, context);
  }`));

const base = process.env.GRIMOIRE_APP;
const chat = process.env.GRIMOIRE_CHAT;
const campaign = process.env.GRIMOIRE_CAMPAIGN;

// requests to the app still outstanding: a screen is not settled while one is (a loaded machine answers late)
let outstanding = 0;
function counting() {
  const fetch = globalThis.fetch;
  globalThis.fetch = (...args) => {
    outstanding += 1;
    return fetch(...args).finally(() => { outstanding -= 1; });
  };
}

// the screen has stopped changing: its text the same over a few checks, and no request outstanding
async function settled(what) {
  let last = null;
  let same = 0;
  await until(() => {
    const now = document.body.textContent + "|" + document.body.querySelectorAll("button").filter((node) => node.disabled).length;
    same = now === last && outstanding === 0 ? same + 1 : 0;
    last = now;
    return same >= 5 && !/Loading/.test(document.body.querySelector("main").textContent);
  }, 20000, what + " to settle");
}

test("every screen passes the structural accessibility audit", { skip: !base }, async () => {
  const window = install(base);
  counting();
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  const found = [];
  const check = async (where) => {
    await settled(where);
    for (const violation of audit(body)) found.push(where + ": " + describe(violation));
  };
  const visit = async (hash) => {
    window.location.hash = hash;
    await until(() => window.location.hash === hash, 15000, hash);
    await check(hash);
  };

  const routes = ["#/home", "#/chats", "#/start", "#/chat/" + encodeURIComponent(chat), "#/sources", "#/providers",
    "#/sync", "#/art", "#/campaign", "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat),
    "#/inbox", "#/setup", "#/nowhere"];
  for (const tab of ["card", "history", "v1", "changeset", "rebase"]) routes.push("#/import?tab=" + tab);
  for (const hash of routes) await visit(hash);

  // a source's own page: the first one the library lists
  await visit("#/sources");
  const sourceLink = body.querySelector("main").querySelectorAll("a").find((node) => (node.getAttribute("href") || "").startsWith("#/source/"));
  assert.ok(sourceLink, "the library lists a source");
  await visit(sourceLink.getAttribute("href"));
  for (const tab of ["art", "history"]) {
    const tabbed = sourceLink.getAttribute("href") + "?tab=" + tab;
    await visit(tabbed);
  }

  // the panels a screen opens in place: every collapsed disclosure on the chat, its campaign hub and a source, opened
  const disclosures = ["#/chat/" + encodeURIComponent(chat),
    "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat), sourceLink.getAttribute("href")];
  const opened = [];
  for (const hash of disclosures) {
    await visit(hash);
    const closed = body.querySelector("main").querySelectorAll("button").filter((node) => node.getAttribute("aria-expanded") === "false");
    for (const button of closed) {
      button.click();
      opened.push((button.textContent || "").trim());
      await check(hash + " with " + JSON.stringify((button.textContent || "").trim()) + " open");
    }
  }

  assert.ok(opened.length >= 2, "opened the screens' disclosures: " + opened.join(", "));

  // the command palette and a modal, while open
  await visit("#/home");
  for (const listener of document.listeners.get("keydown") || []) {
    listener({ type: "keydown", key: "k", ctrlKey: true, metaKey: false, preventDefault() {}, target: body });
  }
  const palette = await until(() => body.querySelector("dialog"), 5000, "the command palette").catch(() => null);
  if (palette) {
    await check("the command palette");
    palette.querySelector("input") && palette.querySelector("input").dispatchEvent({ type: "keydown", key: "Escape", preventDefault() {} });
    for (const listener of document.listeners.get("keydown") || []) listener({ type: "keydown", key: "Escape", preventDefault() {}, target: body });
  } else {
    found.push("the command palette: did not open on Ctrl+K");
  }
  await visit("#/sources");
  const make = byText(body.querySelector("main"), "button", "New source")[0];
  assert.ok(make, "the library offers New source");
  make.click();
  await until(() => body.querySelector("dialog"), 15000, "the New source dialog");
  await check("the New source dialog");

  assert.deepEqual(found, [], "accessibility violations:\n" + found.join("\n"));
});
