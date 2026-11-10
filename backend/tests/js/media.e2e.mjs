// 114's Art and Sync pages end to end in fakedom, against a real app server over a synthetic library: add a
// picture straight into a character's portrait slot, see it listed in the gallery with where it is used, then
// name this device and pair it from the Sync page, which says what sync cannot do and lists nothing in conflict.
// Run by test_media_pages.py, which passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_SCOPE and GRIMOIRE_SUBJECT.
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
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d,
  ...new TextEncoder().encode("IHDR synthetic")]);

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

function section(root, title) {
  return root.querySelectorAll("section").find((node) => {
    const heading = node.querySelector("h2");
    return heading && heading.textContent === title;
  });
}

test("a picture added to a portrait, then this device named and paired", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  const nav = body.querySelector("nav");
  assert.ok(byText(nav, "a", "Art")[0] && byText(nav, "a", "Sync")[0], "Art and Sync are in the menu");

  // one character's slots: nothing placed yet, nothing substituted
  const scope = process.env.GRIMOIRE_SCOPE, subject = process.env.GRIMOIRE_SUBJECT;
  window.location.hash = "#/art?scope=" + encodeURIComponent(scope) + "&subject=" + encodeURIComponent(subject);
  const portrait = await until(() => section(main, "portrait"), 15000, "the portrait slot");
  assert.ok(portrait.textContent.includes("Nothing placed"));
  assert.equal(portrait.querySelector("img"), null);

  // add a new picture straight into it: the slot now shows it, from this library's own bytes
  const form = byText(portrait, "details", "Add a new picture to portrait")[0];
  const file = form.querySelectorAll("input").find((node) => node.getAttribute("type") === "file");
  file.files = [{ size: PNG.length, arrayBuffer: async () => PNG.buffer.slice(0) }];
  type(form.querySelectorAll("input").find((node) => node.getAttribute("type") === "text"), "Mara at the harbor");
  byText(form, "button", "Add picture")[0].click();
  const shown = await until(() => {
    const now = section(main, "portrait");
    return now && now.textContent.includes("Shown") && now.querySelector("img");
  }, 15000, "the placed picture");
  assert.ok(shown.getAttribute("src").startsWith("blob:"));
  assert.ok(section(main, "portrait").textContent.includes("Mara at the harbor"));

  // the scope's gallery lists it with where it is used
  window.location.hash = "#/art?scope=" + encodeURIComponent(scope);
  const pictures = await until(() => {
    const found = section(main, "Pictures");
    return found && found.querySelector("li") && found;
  }, 15000, "the gallery");
  assert.ok(byText(pictures, "div", "Used in portrait (set)").length);
  assert.ok(byText(section(main, "Who uses these pictures"), "a", "").length);

  // Sync: what it cannot do is said plainly, and nothing is in conflict
  window.location.hash = "#/sync";
  const conflicts = await until(() => section(main, "Conflicts"), 15000, "the Sync page");
  assert.ok(conflicts.textContent.includes("Nothing is in conflict."));
  assert.ok(main.textContent.includes("not encrypted"));
  assert.ok(byText(section(main, "Not in this app yet"), "strong", "Unpairing is not available yet").length);

  // name this device, then pair it: the key is shown once
  const device = section(main, "This device");
  type(device.querySelector("input"), "desk-e2e");
  byText(device, "button", "Name it")[0].click();
  await until(() => {
    const now = section(main, "This device");
    return now && now.textContent.includes("Named desk-e2e");
  }, 15000, "the named device");
  const pairing = byText(section(main, "Devices"), "details", "Pair a device")[0];
  type(pairing.querySelector("input"), "phone");
  byText(pairing, "button", "Pair")[0].click();
  const key = await until(() => section(main, "Type this key on phone"), 15000, "the new key");
  assert.match(key.textContent, /[0-9a-f]{64}/);
  const peers = section(main, "Devices");
  assert.ok(peers.textContent.includes("phone") && peers.textContent.includes("It dials this device."));
});
