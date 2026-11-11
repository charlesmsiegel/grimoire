// 114's Art and Sync pages end to end in fakedom, against a real app server over a synthetic library: add a
// picture straight into a character's portrait slot through the picture picker, re-frame it, see it listed in
// the gallery with where it is used, then
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
  // Art has left the rail (it is reached from a character, and the command palette lists it); sync stays
  const nav = body.querySelector("nav");
  assert.ok(byText(nav, "a", "Devices and sync")[0], "sync is in the menu");
  assert.ok(!nav.querySelectorAll("a").some((node) => node.textContent.trim() === "Art"), "Art is not in the menu");
  const listed = (await (await fetch("/shell/pages")).json()).pages.find((page) => page.id === "art");
  assert.equal(listed.slot, "hidden");
  assert.ok(!(listed.missing || []).length && !listed.unavailable, "Art still opens");

  // one character's slots: nothing placed yet, nothing substituted
  const scope = process.env.GRIMOIRE_SCOPE, subject = process.env.GRIMOIRE_SUBJECT;
  window.location.hash = "#/art?scope=" + encodeURIComponent(scope) + "&subject=" + encodeURIComponent(subject);
  const slot = (name) => main.querySelector('div[data-slot="' + name + '"]');
  const labelled = (label) => main.querySelectorAll("section").find((node) => node.getAttribute("aria-label") === label);
  const numbers = (dialog) => dialog.querySelectorAll("input").filter((node) => node.getAttribute("type") === "number");
  const picked = () => body.querySelector("dialog").querySelectorAll("button.art-pick")
    .filter((node) => node.getAttribute("aria-pressed") === "true" && !node.hidden).map((node) => node.getAttribute("aria-label"));
  const portrait = await until(() => slot("portrait"), 15000, "the portrait slot");
  assert.ok(portrait.textContent.includes("Nothing placed"));
  assert.equal(portrait.querySelector("img"), null);
  assert.ok(portrait.querySelector("div.art-frame").classList.contains("art-aspect-3-4"), "a portrait is framed 3 by 4");
  assert.ok(slot("avatar").querySelector("div.art-frame").classList.contains("art-aspect-1-1"), "an avatar is framed square");
  assert.ok(portrait.textContent.includes("On their sheet and on stage"), "the slot says where it shows");

  // add a new picture straight into it, through the picker: the slot now shows it, from this library's own bytes
  byText(portrait, "button", "Choose")[0].click();
  let dialog = await until(() => body.querySelector("dialog"), 15000, "the picker");
  assert.equal(dialog.getAttribute("aria-label"), "Choose a picture for Mara Vell: portrait");
  const file = dialog.querySelectorAll("input").find((node) => node.getAttribute("type") === "file");
  file.files = [{ name: "harbor.png", size: PNG.length, arrayBuffer: async () => PNG.buffer.slice(0) }];
  file.dispatchEvent({ type: "change" });
  type(dialog.querySelectorAll("input").find((node) => node.getAttribute("type") === "text"), "Mara at the harbor");
  byText(dialog, "button", "Use this picture")[0].click();
  const shown = await until(() => {
    const now = slot("portrait");
    return !body.querySelector("dialog") && now && now.textContent.includes("Shown") && now.querySelector("img");
  }, 15000, "the placed picture");
  assert.ok(shown.getAttribute("src").startsWith("blob:"));
  assert.ok(slot("portrait").textContent.includes("Mara at the harbor"));

  // a standalone card's slot inherits nothing, so it offers no Hide and no way back to an inherited picture;
  // Crop opens the picker on the picture shown, so saving a focus alone keeps that picture
  assert.ok(!byText(slot("portrait"), "button", "Hide").length && !byText(slot("portrait"), "button", "Use inherited").length);
  byText(slot("portrait"), "button", "Crop").find((node) => !node.disabled).click();
  dialog = await until(() => body.querySelector("dialog"), 15000, "the picker for a crop");
  assert.deepEqual(picked(), ["Mara at the harbor · " + scope]);
  assert.ok(!byText(dialog, "button", "Hide this slot").length && !byText(dialog, "button", "Use the inherited picture").length);
  type(numbers(dialog)[4], "25");
  type(numbers(dialog)[5], "40");
  byText(dialog, "button", "Use this picture")[0].click();
  await until(() => !body.querySelector("dialog") && byText(slot("portrait"), "button", "Crop").find((node) => !node.disabled), 15000, "the saved focus");
  await until(() => slot("portrait").querySelector("img"), 15000, "the picture after saving");
  assert.ok(slot("portrait").textContent.includes("Mara at the harbor"), "the same picture after saving");
  byText(slot("portrait"), "button", "Crop").find((node) => !node.disabled).click();
  dialog = await until(() => body.querySelector("dialog"), 15000, "the picker again");
  assert.deepEqual([numbers(dialog)[4].value, numbers(dialog)[5].value], ["25", "40"], "the focus was stored");
  assert.deepEqual(picked(), ["Mara at the harbor · " + scope]);
  byText(dialog, "button", "Cancel")[0].click();
  await until(() => !body.querySelector("dialog"), 15000, "the picker closed");

  // the subject's own pictures are listed under its slots, with a tile to add more
  assert.ok(byText(labelled("All pictures"), "button", "Drop pictures here, or choose files").length);
  assert.ok(byText(labelled("All pictures"), "span", "Used in portrait").length);

  // the scope's gallery lists it with where it is used
  window.location.hash = "#/art?scope=" + encodeURIComponent(scope);
  const pictures = await until(() => {
    const found = labelled("Pictures");
    return found && found.querySelector("div.art-card") && found;
  }, 15000, "the gallery");
  assert.ok(byText(pictures, "span", "Used in portrait of Mara Vell").length);
  assert.ok(byText(pictures, "span", "Mara at the harbor").length);
  assert.ok(byText(labelled("Who uses these pictures"), "a", "Mara Vell").length);

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
  // the key is shown as soon as the pairing answers; the list of devices follows when the page reloads
  await until(() => {
    const peers = section(main, "Devices");
    return peers && peers.textContent.includes("phone") && peers.textContent.includes("It dials this device.");
  }, 15000, "the paired device listed");
});
