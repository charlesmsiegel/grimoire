// 109's Library pages end to end in fakedom against a real app server: two same-named characters listed apart,
// a new source, a portrayal derived from one Mara with an override and an explicit clear, an edit that goes
// stale and is kept as a draft, then saved over the new head, and the exact revision attached to a chat.
// Run by test_library_pages.py, which serves a synthetic library and passes GRIMOIRE_APP (base URL),
// GRIMOIRE_TOKEN and GRIMOIRE_CHAT (the card chat); the shell's "/static/..." imports map onto the packaged files.
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
const CHAT = process.env.GRIMOIRE_CHAT;
const MARA = "card:mara-vell/character/mara-vell";
const PORTRAYAL = "world:harbor/character/mara-of-the-quay";
const at = (ref) => "#/source/" + encodeURIComponent(ref);

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

function choose(select, value) {
  select.value = value;
  select.dispatchEvent({ type: "change" });
}

// The innermost section whose heading is `title`.
function section(root, title) {
  return root.querySelectorAll("section").filter((node) => {
    const heading = node.querySelector("h2");
    return heading && heading.textContent === title;
  }).at(-1);
}

// The row of the effective field list for `name` ("Description", "Section personality").
function fieldRow(root, name) {
  return root.querySelectorAll("div.source-field").find((row) => row.querySelector("strong").textContent === name);
}

async function call(op, args) {
  const response = await fetch("/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": token } });
  const answer = await response.json();
  assert.equal(response.status, 200, op + ": " + JSON.stringify(answer));
  return answer;
}

const idle = (body) => () => !body.querySelectorAll("button").some((node) => node.disabled);

// Run `act` (a click, a new address), then wait until the shell has mounted a new page and `ready(root)` holds on
// it: a page still showing is never typed into.
async function step(main, act, ready, what) {
  const before = main.children[0];
  await act();
  return until(() => {
    const root = main.children[0];
    return root && root !== before && root.className === "g-page" && ready(root) && root;
  }, 15000, what);
}

const library = (root) => section(root, "New source");    // the library's last section: it has mounted
const entry = (root) => section(root, "Not offered here");  // a source page's last section

test("the library from the menu to an exact attach", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(token);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home" && byText(main, "h2", "What next").length, 15000, "Home");
  const go = (hash, ready, what) => step(main, () => { window.location.hash = hash; }, ready, what);
  const press = (scope, label, ready, what) => step(main, () => byText(scope, "button", label).at(-1).click(), ready, what);

  // Home now links the library, and the menu lists it
  const fromHome = await until(() => byText(main, "a", "Your library")[0], 15000, "the library on Home");
  assert.ok(byText(body.querySelector("nav"), "a", "Library").length, "Library is in the menu");
  await step(main, () => fromHome.click(), (root) => library(root) && byText(root, "h2", "Everything").length, "the library");

  // characters: three entries named Mara, each its own exact ref, each naming the others
  let page = await go("#/sources?role=character", library, "the characters");
  const maras = section(page, "Characters").querySelector("ul").querySelectorAll("a")
    .filter((row) => row.querySelector("span").textContent === "Mara");
  assert.equal(maras.length, 3);
  assert.equal(new Set(maras.map((row) => row.getAttribute("href"))).size, 3);
  for (const row of maras) assert.ok(row.textContent.includes("Same name as"), "a namesake is said to be another entry");

  // a search narrows the list by name or ref
  const search = page.querySelector("form");
  type(search.querySelector("input"), "seraphine");
  page = await step(main, () => search.dispatchEvent({ type: "submit", preventDefault() {} }), library, "a search");
  assert.ok(window.location.hash.includes("q=seraphine"));
  const found = section(page, "Characters").querySelector("ul").querySelectorAll("a");
  assert.ok(found.length && found.every((row) => row.querySelector("span").textContent === "Seraphine"));

  // worlds: chosen one at a time, then that world's own entries
  page = await go("#/sources?role=world", library, "the worlds");
  assert.ok(byText(page, "p", "Choose a world below").length);
  const saltmarch = section(page, "Worlds").querySelectorAll("a").find((row) => row.getAttribute("title") === "world:saltmarch");
  page = await step(main, () => saltmarch.click(), library, "Saltmarch");
  const own = section(page, "Entries of this world").querySelectorAll("a").map((row) => row.getAttribute("title"));
  assert.ok(own.length && own.every((ref) => ref.startsWith("world:saltmarch/")), own.join(", "));

  // lore: the card's lorebook
  page = await go("#/sources?role=lore", library, "the lore");
  assert.ok(section(page, "Lore").querySelectorAll("a").some((row) => row.getAttribute("title") === "card:mara-vell/lorebook/mara-vell-lore"));

  // a new source for the portrayal to live in
  page = await go("#/sources", library, "the whole library");
  const fresh = section(page, "New source").querySelectorAll("input");
  type(fresh[0], "world:harbor");
  type(fresh[1], "Harbor");
  page = await press(section(page, "New source"), "Create source", library, "the new source");
  assert.ok(byText(page, "div", "Showing Harbor (world:harbor) only.").length);

  // derive a portrayal of exactly the card's Mara r1
  page = await go(at(MARA), entry, "Mara Vell");
  const derive = section(page, "Derive a portrayal");
  byText(derive, "button", "Choose where it goes")[0].click();
  const target = await until(() => derive.querySelector("select"), 15000, "the target sources");
  choose(target, "world:harbor");
  type(derive.querySelector("input"), "Mara of the quay");
  page = await press(derive, "Derive from r1", entry, "the portrayal");
  assert.equal(decodeURIComponent(window.location.hash), "#/source/" + PORTRAYAL + "@r1");
  assert.ok(section(page, "What this portrayal shows"));
  assert.equal(fieldRow(page, "Section personality").querySelector("span").textContent, "inherited");

  // override a section: a new revision r2
  const personality = fieldRow(page, "Section personality");
  type(personality.querySelector("textarea"), "Sharp, and done waiting.");
  page = await press(personality, "Save the override", entry, "r2");
  assert.ok(byText(page, "dd", "r2, the head").length);
  assert.equal(fieldRow(page, "Section personality").querySelector("span").textContent, "overridden");

  // clear the description, after a confirmation that names what happens: r3
  byText(fieldRow(page, "Description"), "button", "Clear")[0].click();
  const dialog = await until(() => body.querySelector("dialog"), 15000, "the confirmation");
  assert.ok(dialog.textContent.includes("Clear the description of Mara of the quay?"));
  page = await press(dialog, "Clear it", entry, "r3");
  assert.ok(byText(page, "dd", "r3, the head").length);
  assert.equal(fieldRow(page, "Description").querySelector("span").textContent, "cleared");
  assert.equal(fieldRow(page, "Section personality").querySelector("span").textContent, "overridden");

  // a rename typed here goes stale: the head moves on (another tab, say) before it is saved
  type(section(page, "Edit this portrayal").querySelector("input"), "Mara of the lamps");
  const seen = await call("sources.read", { ref: PORTRAYAL });
  await call("sources.save", { ref: PORTRAYAL, head: seen.head, etag: seen.etag, expected: seen.manifest_etag,
    fields: { name: "Mara Quayside" } });
  page = await press(section(page, "Edit this portrayal"), "Save as a new revision", entry, "the stale draft");
  assert.ok(byText(body, "div", "This entry changed since the page read it. Your draft is kept").length);
  const stale = section(page, "Your draft started from an older revision");
  assert.ok(stale, "the draft is said to be stale");
  assert.ok(stale.textContent.includes("started from r3; the head is now r4"));
  assert.ok(stale.textContent.includes("Changed since: name."));
  assert.ok(stale.textContent.includes("Your draft changes: name."));
  assert.ok(!section(page, "Edit").querySelector("input"), "the editor waits until the draft is kept or discarded");
  page = await press(stale, "Keep my draft on r4", entry, "the kept draft");
  const kept = section(page, "Edit this portrayal");
  assert.equal(kept.querySelector("input").value, "Mara of the lamps");
  page = await press(kept, "Save as a new revision", entry, "r5");
  assert.ok(byText(page, "dd", "r5, the head").length);
  assert.equal(body.querySelector("h1").textContent, "Mara of the lamps");
  // the override and the clear came through the rename
  assert.equal(fieldRow(page, "Description").querySelector("span").textContent, "cleared");
  assert.equal(fieldRow(page, "Section personality").querySelector("span").textContent, "overridden");

  // attach exactly r5 to the card chat
  const attach = section(page, "Attach to a chat");
  assert.ok(attach.textContent.includes("Attaches exactly " + PORTRAYAL + "@r5."));
  byText(attach, "button", "Choose a chat")[0].click();
  const chats = await until(() => attach.querySelector("select"), 15000, "the chats");
  choose(chats, CHAT);
  byText(attach, "button", "Attach r5")[0].click();
  const ask = await until(() => body.querySelector("dialog"), 15000, "the attach confirmation");
  assert.ok(ask.textContent.includes("at exactly " + PORTRAYAL + "@r5"));
  assert.ok(ask.textContent.includes("witnessed nothing"));
  byText(ask, "button", "Attach r5")[0].click();
  await until(() => byText(attach, "div", "Attached " + PORTRAYAL + "@r5").length, 15000, "the attach");
  await until(idle(body), 15000, "the page to settle");

  // read again: the chat binds r5, and an older revision is shown exactly, not edited
  page = await go("#/sources", library, "the library again");
  page = await go(at(PORTRAYAL), entry, "the portrayal again");
  assert.ok(section(page, "Where it is used").textContent.includes("binds r5"));
  page = await go(at(PORTRAYAL + "@r2"), entry, "r2");
  assert.ok(byText(page, "dd", "r2, an older revision").length);
  assert.ok(!section(page, "Edit this portrayal"), "an older revision has no editor");
  assert.ok(byText(page, "div", "shown exactly as it was written").length);
  assert.equal(fieldRow(page, "Description").querySelector("span").textContent, "inherited");
});
