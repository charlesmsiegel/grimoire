// First run (108) end to end in fakedom: the app started with no library opens on Library setup; the person
// names this device, checks the suggested folder and makes the library there, and the app then serves it (Home
// and Chats become usable). Run by test_app_shell_js.py with GRIMOIRE_APP, GRIMOIRE_TOKEN and GRIMOIRE_PLACE,
// the folder the app suggests.
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

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

test("from no library to an open one", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");

  // with no library, the app opens on setup; Home and Chats wait on a library
  await until(() => window.location.hash === "#/setup" && main.querySelector("input"), 15000, "Library setup");
  const nav = body.querySelector("nav");
  assert.ok(byText(nav, "a", "Chats")[0].classList.contains("g-blocked"), "Chats waits on a library");
  const [folder, device] = main.querySelectorAll("input");
  assert.equal(folder.value, process.env.GRIMOIRE_PLACE, "the folder the app was started on is offered");

  // a relative path is refused with its reason, tied to the field
  type(folder, "relative/folder");
  byText(main, "button", "Check this folder").at(-1).click();
  await until(() => folder.getAttribute("aria-invalid") === "true", 15000, "the refusal");
  // a check whose folder was edited while it was out is dropped: no action is offered for a folder not typed
  type(folder, process.env.GRIMOIRE_PLACE);
  byText(main, "button", "Check this folder").at(-1).click();
  type(folder, process.env.GRIMOIRE_PLACE + "-other");
  await new Promise((resolve) => setTimeout(resolve, 1000));
  assert.equal(byText(main, "button", "Make the library here").length, 0, "the stale answer is dropped");
  type(folder, process.env.GRIMOIRE_PLACE);
  byText(main, "button", "Check this folder").at(-1).click();
  const make = await until(() => byText(main, "button", "Make the library here").at(-1), 15000, "the check");
  make.click();
  await until(() => byText(main, "div", "Name this device first").length, 15000, "the device is asked for");
  type(device, "desk");
  byText(main, "button", "Make the library here").at(-1).click();

  // the library is open: setup says which, and the pages that waited on it are usable
  await until(() => byText(main, "h2", "This library").length, 15000, "the open library");
  assert.ok(byText(main, "dd", "desk").length);
  assert.equal(window.sessionStorage.getItem("grimoire-draft:setup:device"), null, "the typed fields are cleared");
  const chats = await until(() => byText(body.querySelector("nav"), "a", "Chats")[0], 15000, "the menu again");
  assert.ok(!chats.classList.contains("g-blocked"));
  chats.click();
  await until(() => byText(main, "p", "No chats yet").length, 15000, "an empty chat list");
});
