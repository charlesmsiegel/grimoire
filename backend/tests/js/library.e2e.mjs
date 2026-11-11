// 109's Library pages end to end in fakedom against a real app server: Characters and lore (roles with their
// counts, shelves, search, the grid and the table, a new source and a new entry made in their dialogs), then a
// character sheet and an entry sheet by their tabs: two same-named characters listed apart, a portrayal derived
// from one Mara with an override and an explicit clear, an edit that goes stale and is kept as a draft, then saved
// over the new head, the exact revision attached to a chat, and an older version compared and restored. Then the
// person's own notes (docs/SHELF.md): tagging and pinning an entry from its tile and from its sheet, Select mode
// with a bulk tag and a bulk pin that names the entry it could not change, tag chips that narrow together, a
// filter saved, opened, renamed and deleted, and a chat pinned into the "Pinned" group of Chats.
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
const LORE = "card:mara-vell/lorebook/mara-vell-lore";
const at = (ref, query = "") => "#/source/" + encodeURIComponent(ref) + query;

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

function choose(select, value) {
  select.value = value;
  select.dispatchEvent({ type: "change" });
}

// The labelled block of a sheet whose heading is `title` ("Portrayals", "In these chats").
function block(root, title) {
  return root.querySelectorAll("section.source-block").find((node) => node.querySelector("h3").textContent === title);
}

// The field of the sheet labelled `name` ("Description", "personality"), and the state its pill says.
function fieldRow(root, name) {
  return root.querySelectorAll("div.source-field").find((row) => {
    const label = row.querySelector("span.source-label");
    return label && label.textContent === name;
  });
}
const stateOf = (root, name) => fieldRow(root, name).querySelector("span.source-state").textContent;

// The segment of a segmented control that is chosen now.
const pressed = (group) => group.querySelectorAll("button").find((node) => node.getAttribute("aria-pressed") === "true").textContent;
const group = (root, label) => root.querySelectorAll("div.g-segments").find((node) => node.getAttribute("aria-label") === label);
const alerts = (root) => root.querySelectorAll("div").filter((node) => node.getAttribute("role") === "alert" && !node.hidden && node.textContent);

// The entries in view, as the grid shows them: each tile's link carries its exact ref as its title.
const tiles = (root) => root.querySelectorAll("div.sources-tile");
const refOf = (tile) => tile.querySelector("a.g-tile").getAttribute("title");
const nameOf = (tile) => tile.querySelector("span.g-tile-name").textContent;

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
    return root && root !== before && root.className.split(" ").includes("g-page") && ready(root) && root;
  }, 15000, what);
}

const library = (root) => root.querySelector("div.sources-results");  // the library appends everything at once
const entry = (root) => root.querySelector("div.source-tabs");         // and so does an entry's page

test("the library from the menu to an exact attach", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(token);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home" && main.children[0] && main.children[0].children.length, 15000, "Home");
  const go = (hash, ready, what) => step(main, () => { window.location.hash = hash; }, ready, what);
  const press = (scope, label, ready, what) => step(main, () => byText(scope, "button", label).at(-1).click(), ready, what);
  const dialog = (what) => until(() => body.querySelector("dialog"), 15000, what);

  // the menu lists the library, and it opens on everything, as a grid
  const inMenu = byText(body.querySelector("nav"), "a", "Characters and lore")[0];
  assert.ok(inMenu, "the library is in the menu");
  let page = await step(main, () => inMenu.click(), library, "the library");
  assert.equal(body.querySelector("h1").textContent, "Characters and lore");
  const everything = tiles(page).length;
  assert.equal(pressed(group(page, "Which entries")), "Everything " + everything);
  assert.equal(pressed(group(page, "View")), "Grid");
  assert.ok(page.querySelector("div.sources-count").textContent.includes(everything + " entries"));
  // every role's count is real: they add up to everything
  const counts = group(page, "Which entries").querySelectorAll("button").map((node) => Number(node.textContent.split(" ").pop()));
  assert.equal(counts.slice(1).reduce((sum, n) => sum + n, 0), counts[0]);
  // nothing of the design that the app cannot do yet is on the page
  for (const absent of ["Save this filter", "Add cover", "Move", "Delete"]) assert.equal(byText(page, "button", absent).length, 0, absent);
  // nothing is tagged, pinned or saved yet, and the page says so with real counts
  assert.equal(page.querySelector("div.sources-tagrow"), null, "no tag is in use, so there are no chips");
  assert.equal(byText(page.querySelector("nav.sources-shelves"), "a", "Pinned")[0].textContent, "Pinned0");
  assert.ok(byText(page.querySelector("nav.sources-saved"), "p", "None yet.").length);

  // characters: three entries named Mara, each its own exact ref, each said to share its name
  page = await go("#/sources?role=character", library, "the characters");
  assert.equal(pressed(group(page, "Which entries")), "Characters " + tiles(page).length);
  const maras = tiles(page).filter((tile) => nameOf(tile) === "Mara");
  assert.equal(maras.length, 3);
  assert.equal(new Set(maras.map(refOf)).size, 3);
  for (const tile of maras) {
    const note = tile.querySelector("span.sources-namesakes");
    assert.ok(note.textContent.includes("Same name as 2 other entries"), "a namesake is said to be another entry");
    assert.ok(note.getAttribute("title").includes("A different entry: names never decide which one."));
  }
  // a character can be chatted with from its tile, by its exact ref
  for (const tile of tiles(page)) {
    assert.equal(tile.querySelector("a.sources-chat").getAttribute("href"), "#/start?source=" + encodeURIComponent(refOf(tile)));
    assert.ok(tile.querySelector("span.g-portrait"), "a character is a portrait tile");
  }
  // one of them is in a chat, and says so; played last, it is listed first
  assert.equal(pressed(group(page, "Sort")), "Recent");
  assert.ok(tiles(page)[0].textContent.includes("1 chat"), tiles(page)[0].textContent);

  // the old address of the personas still opens: they are the characters
  const characters = tiles(page).map(refOf).sort();
  page = await go("#/sources?role=persona", library, "the personas");
  assert.deepEqual(tiles(page).map(refOf).sort(), characters);
  assert.ok(byText(page, "p", "Any character can be your persona.").length);

  // A to Z, from the control: the order is in the address, and names are in order
  page = await step(main, () => byText(group(page, "Sort"), "button", "A-Z")[0].click(), library, "A to Z");
  assert.ok(window.location.hash.includes("sort=name") && window.location.hash.includes("role=persona"));
  const names = tiles(page).map(nameOf);
  assert.deepEqual(names, [...names].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" })));

  // a search narrows the list by name or ref
  page = await go("#/sources?role=character", library, "the characters again");
  const search = page.querySelector("form");
  type(search.querySelector("input"), "seraphine");
  page = await step(main, () => search.dispatchEvent({ type: "submit", preventDefault() {} }), library, "a search");
  assert.ok(window.location.hash.includes("q=seraphine"));
  assert.ok(tiles(page).length && tiles(page).every((tile) => nameOf(tile) === "Seraphine"));
  assert.ok(page.querySelector("div.sources-count").textContent.includes('matches "seraphine"'));
  page = await go("#/sources?q=" + encodeURIComponent("no such entry"), library, "a search that finds nothing");
  assert.equal(tiles(page).length, 0);
  assert.ok(byText(page, "p", 'Nothing matches "no such entry".').length);

  // shelves: a world's own entries, and every imported card
  page = await go("#/sources", library, "the whole library");
  const shelves = page.querySelector("nav.sources-shelves");
  assert.equal(shelves.querySelector("a[aria-current]").textContent, "Everything" + everything);
  const saltmarch = shelves.querySelectorAll("a").find((row) => row.getAttribute("title") === "world:saltmarch");
  const held = Number(saltmarch.querySelectorAll("span")[1].textContent);
  page = await step(main, () => saltmarch.click(), library, "Saltmarch");
  const own = tiles(page).map(refOf);
  assert.ok(own.length === held && own.every((ref) => ref.startsWith("world:saltmarch/")), own.join(", "));
  assert.ok(page.querySelector("nav.sources-shelves").querySelector("a[aria-current]").textContent.startsWith("Saltmarch"));
  assert.equal(page.querySelector("section.g-card").querySelector("h2").textContent, "Saltmarch");
  assert.equal(pressed(group(page, "Which entries")), "Everything " + held, "the roles count what this shelf holds");
  page = await step(main, () => byText(page.querySelector("nav.sources-shelves"), "a", "Imported cards")[0].click(), library, "the imported cards");
  assert.ok(window.location.hash.includes("shelf=cards"));
  assert.ok(tiles(page).length && tiles(page).every((tile) => refOf(tile).startsWith("card:")));

  // lore: the card's lorebook, a text tile that names its kind and offers no chat
  page = await go("#/sources?role=lore", library, "the lore");
  const book = tiles(page).find((tile) => refOf(tile) === LORE);
  assert.ok(book, "the card's lorebook is listed");
  assert.equal(book.querySelector("span.g-tile-text").querySelector("span.g-eyebrow").textContent, "Lorebook");
  assert.equal(book.querySelector("a.sources-chat"), null);

  // the same entries as a table
  page = await step(main, () => byText(group(page, "View"), "button", "List")[0].click(), library, "the list");
  assert.ok(window.location.hash.includes("view=list") && window.location.hash.includes("role=lore"));
  assert.equal(tiles(page).length, 0);
  const rows = page.querySelector("table.g-table").querySelector("tbody").querySelectorAll("tr");
  assert.ok(rows.some((row) => row.querySelector("a.sources-name").getAttribute("title") === LORE));
  assert.deepEqual(page.querySelectorAll("th").map((node) => node.textContent), ["Picture", "Name", "Kind", "Tags", "Source", "Version", "Chats", "Last played", "Actions"]);
  assert.ok(rows.every((row) => row.querySelectorAll("td")[2].textContent === "Lorebook"));

  // a new source, made in its dialog; a refusal is shown there and nothing is lost
  page = await go("#/sources", library, "the whole library again");
  byText(page, "button", "New source")[0].click();
  let asked = await dialog("the new source dialog");
  type(asked.querySelectorAll("input")[0], "card:harbor");
  byText(asked, "button", "Create source")[0].click();
  await until(() => alerts(asked).length, 15000, "the refusal");
  assert.ok(alerts(asked)[0].textContent.includes("reserved_namespace"));
  assert.equal(asked.querySelectorAll("input")[0].value, "card:harbor", "what was typed is kept");
  type(asked.querySelectorAll("input")[0], "world:harbor");
  type(asked.querySelectorAll("input")[1], "Harbour");
  page = await press(asked, "Create source", library, "the new source");
  assert.equal(body.querySelector("dialog"), null);
  assert.ok(window.location.hash.includes("source=world%3Aharbor"));
  assert.equal(page.querySelector("section.g-card").querySelector("h2").textContent, "Harbour");
  assert.ok(byText(page, "p", "Nothing here yet.").length);

  // renamed from its shelf
  byText(page, "button", "Rename")[0].click();
  asked = await dialog("the rename dialog");
  assert.equal(asked.querySelector("input").value, "Harbour");
  type(asked.querySelector("input"), "Harbor");
  page = await press(asked, "Rename", library, "the renamed source");
  assert.equal(page.querySelector("section.g-card").querySelector("h2").textContent, "Harbor");

  // a new entry in it: a scenario, which opens as an entry sheet under its title band
  byText(page, "button", "New entry here")[0].click();
  asked = await dialog("the new entry dialog");
  assert.equal(asked.querySelectorAll("select")[0].value, "world:harbor", "the shelf in view is where it goes");
  choose(asked.querySelectorAll("select")[1], "scenario");
  assert.deepEqual(asked.querySelectorAll("label").map((node) => node.textContent), ["Source", "Kind", "Title", "Premise", "Id (optional)"]);
  type(asked.querySelector("input"), "Night watch");
  type(asked.querySelector("textarea"), "The lamps are out along the quay.");
  page = await press(asked, "Create", entry, "the new scenario");
  assert.equal(decodeURIComponent(window.location.hash), "#/source/world:harbor/scenario/night-watch@r1");
  const band = page.querySelector("div.g-cover");
  assert.equal(band.querySelector("div.g-eyebrow").textContent, "Scenario");
  assert.equal(band.querySelector("h2").textContent, "Night watch");
  assert.equal(byText(band, "button", "").length, 0, "the band has no cover controls yet");
  assert.ok(page.querySelector("p.source-lede").textContent.includes("The lamps are out"));
  assert.ok(page.querySelector("div.source-meta").textContent.startsWith("Harbor · version 1"));
  assert.equal(byText(page, "a", "Start a chat")[0].getAttribute("href"), "#/start?source=" + encodeURIComponent("world:harbor/scenario/night-watch"));
  assert.deepEqual(group(page, "Views of Night watch").querySelectorAll("button").map((node) => node.textContent), ["Sheet", "Edit", "Revisions", "Raw"]);

  // the character sheet of the card's Mara: who she is, what she opens with, the chat she is in
  page = await go(at(MARA), entry, "Mara Vell");
  assert.equal(page.querySelector("section.source-header").querySelector("h2").textContent, "Mara Vell");
  assert.equal(page.querySelector("div.source-meta").textContent, "Imported card · version 1 · 1 chat");
  assert.ok(byText(page, "div", MARA + "@r1").length, "its exact ref is shown");
  assert.deepEqual(group(page, "Views of Mara Vell").querySelectorAll("button").map((node) => node.textContent), ["Sheet", "Art", "Edit", "Revisions", "Raw"]);
  assert.equal(pressed(group(page, "Views of Mara Vell")), "Sheet");
  assert.ok(page.querySelector("div.source-lede").textContent.includes("A smuggler of the Silver Guild"));
  assert.ok(fieldRow(page, "personality").textContent.includes("Wry, patient"));
  assert.ok(block(page, "Opens with").querySelectorAll("p.g-quote").some((quote) => quote.textContent === "The tide is late tonight."));
  assert.equal(block(page, "Lorebooks bound").querySelector("a").getAttribute("href"), at(LORE + "@r1"));
  assert.equal(block(page, "In these chats").querySelectorAll("a").length, 1);
  assert.equal(byText(page, "a", "Start a chat")[0].getAttribute("href"), "#/start?source=" + encodeURIComponent(MARA));

  // its pictures, and the way to the Art page for exactly this character
  page = await step(main, () => byText(group(page, "Views of Mara Vell"), "button", "Art")[0].click(), entry, "the Art tab");
  assert.ok(window.location.hash.endsWith("?tab=art"));
  assert.equal(pressed(group(page, "Views of Mara Vell")), "Art");
  assert.ok(page.querySelector("div.source-art"));
  assert.equal(byText(page, "a", "on the Art page")[0].getAttribute("href"),
    "#/art?scope=" + encodeURIComponent("card:mara-vell") + "&subject=" + encodeURIComponent(MARA));

  // derive a portrayal of exactly the card's Mara r1
  page = await go(at(MARA), entry, "Mara Vell again");
  const derive = block(page, "Portrayals");
  byText(derive, "button", "Derive a portrayal")[0].click();
  const target = await until(() => derive.querySelector("select"), 15000, "the target sources");
  choose(target, "world:harbor");
  type(derive.querySelector("input"), "Mara of the quay");
  page = await press(derive, "Derive from r1", entry, "the portrayal");
  assert.equal(decodeURIComponent(window.location.hash), "#/source/" + PORTRAYAL + "@r1");
  assert.ok(byText(page.querySelector("section.source-header"), "span", "Portrayal").length);
  assert.equal(stateOf(page, "personality"), "inherited");

  // override a section: a new revision r2
  const personality = fieldRow(page, "personality");
  type(personality.querySelector("textarea"), "Sharp, and done waiting.");
  page = await press(personality, "Save the override", entry, "r2");
  assert.ok(page.querySelector("div.source-meta").textContent.includes("version 2"));
  assert.ok(byText(page, "div", PORTRAYAL + "@r2").length);
  assert.equal(stateOf(page, "personality"), "overridden");

  // clear the description, after a confirmation that names what happens: r3
  byText(fieldRow(page, "Description"), "button", "Clear")[0].click();
  let ask = await dialog("the confirmation");
  assert.ok(ask.textContent.includes("Clear the description of Mara of the quay?"));
  page = await press(ask, "Clear it", entry, "r3");
  assert.ok(byText(page, "div", PORTRAYAL + "@r3").length);
  assert.equal(stateOf(page, "Description"), "cleared");
  assert.equal(stateOf(page, "personality"), "overridden");

  // the Edit tab: a rename typed here goes stale, as the head moves on (another tab overrides the description)
  // before it is saved
  page = await step(main, () => byText(page.querySelector("section.source-header"), "a", "Edit")[0].click(), entry, "the Edit tab");
  assert.ok(window.location.hash.endsWith("?tab=edit"));
  let editor = page.querySelector("div.source-edit");
  assert.ok(editor.querySelector("div.source-savebar").textContent.includes(
    "Saves as version 4. Older versions never change, and chats keep the one they started with."));
  assert.ok(byText(editor, "button", "Add a section").length && byText(editor, "button", "Discard").length);
  type(editor.querySelector("input"), "Mara of the lamps");
  const seen = await call("sources.read", { ref: PORTRAYAL });
  await call("sources.portrayal", { ref: PORTRAYAL, head: seen.head, etag: seen.etag, expected: seen.manifest_etag,
    edit: "override_description", text: "Pale from the lamps." });
  page = await press(editor, "Save as version 4", entry, "the stale draft");
  assert.ok(byText(body, "div", "This entry changed since the page read it. Your draft is kept").length);
  let stale = page.querySelector("section.source-stale");
  assert.ok(stale, "the draft is said to be stale");
  assert.ok(stale.textContent.includes("started from r3; the head is now r4"));
  assert.ok(stale.textContent.includes("Changed since: description."));
  assert.ok(stale.textContent.includes("Your draft changes: name."));
  page = await go(at(PORTRAYAL, "?tab=edit"), entry, "the Edit tab, waiting");
  assert.ok(!page.querySelector("div.source-edit").querySelector("input"), "the editor waits until the draft is kept or discarded");
  stale = page.querySelector("section.source-stale");
  page = await press(stale, "Keep my changes on r4", entry, "the kept draft");
  assert.ok(window.location.hash.endsWith("?tab=edit"), "keeping a draft opens the editor that holds it");
  editor = page.querySelector("div.source-edit");
  assert.equal(editor.querySelector("input").value, "Mara of the lamps");
  assert.ok(byText(editor, "div", "Your unsaved draft is shown.").length);
  // elsewhere on the entry, the unsaved draft is pointed to
  page = await go(at(PORTRAYAL), entry, "the sheet, with a draft waiting");
  assert.equal(byText(page, "a", "on the Edit tab")[0].getAttribute("href"), at(PORTRAYAL, "?tab=edit"));
  page = await go(at(PORTRAYAL, "?tab=edit"), entry, "the Edit tab, with its draft");
  page = await press(page.querySelector("div.source-edit"), "Save as version 5", entry, "r5");
  assert.ok(byText(page, "div", PORTRAYAL + "@r5").length);
  assert.ok(page.querySelector("div.source-meta").textContent.includes("version 5"));
  assert.equal(body.querySelector("h1").textContent, "Mara of the lamps");
  assert.equal(byText(page, "a", "on the Edit tab").length, 0);
  // only the rename was laid over r4: the other tab's description stands, and the section override too
  assert.equal(stateOf(page, "Description"), "overridden");
  assert.ok(fieldRow(page, "Description").textContent.includes("Pale from the lamps."));
  assert.equal(stateOf(page, "personality"), "overridden");

  // attach exactly r5 to the card chat
  const opener = byText(page.querySelector("section.source-header"), "button", "Attach to a chat")[0];
  const attach = page.querySelector("section.source-attach");
  assert.ok(attach.hidden);
  opener.click();
  assert.ok(!attach.hidden && opener.getAttribute("aria-expanded") === "true");
  assert.ok(attach.textContent.includes("Attaches exactly " + PORTRAYAL + "@r5."));
  const chats = await until(() => attach.querySelector("select"), 15000, "the chats");
  choose(chats, CHAT);
  byText(attach, "button", "Attach r5")[0].click();
  ask = await dialog("the attach confirmation");
  assert.ok(ask.textContent.includes("at exactly " + PORTRAYAL + "@r5"));
  assert.ok(ask.textContent.includes("witnessed nothing"));
  byText(ask, "button", "Attach r5")[0].click();
  await until(() => byText(attach, "div", "Attached " + PORTRAYAL + "@r5").length, 15000, "the attach");
  await until(idle(body), 15000, "the page to settle");

  // read again: the chat is on r5
  page = await go("#/sources", library, "the library again");
  page = await go(at(PORTRAYAL), entry, "the portrayal again");
  assert.ok(block(page, "In these chats").textContent.includes("on r5"));
  assert.ok(page.querySelector("div.source-meta").textContent.endsWith("a portrayal · 1 chat"));

  // its versions, newest first, and one of them compared with the current one field by field
  page = await go(at(PORTRAYAL, "?tab=revisions&rev=r2"), entry, "the revisions");
  const timeline = page.querySelector("div.source-timeline").querySelectorAll("a.source-rev");
  assert.deepEqual(timeline.map((row) => row.querySelector("span.g-mono").textContent), ["r5 · current", "r4", "r3", "r2", "r1"]);
  assert.equal(page.querySelector("a[aria-current]").querySelector("span.g-mono").textContent, "r2");
  assert.ok(timeline[0].textContent.includes("Chats on it:"), "the version a chat is on says so");
  const compare = page.querySelector("div.source-compare");
  assert.equal(compare.querySelector("h3").textContent, "Version 2 compared with the current version 5");
  const changed = Object.fromEntries(compare.querySelectorAll("div.source-diff").map((row) => [row.querySelector("span.g-eyebrow").textContent,
    [row.querySelector("div.source-before").textContent, row.querySelector("div.source-after").textContent]]));
  assert.deepEqual(changed.Name, ["Mara of the quay", "Mara of the lamps"]);
  assert.deepEqual(changed.Description, ["(not there)", "Pale from the lamps."]);
  assert.ok(!("Section personality" in changed), "a field that is the same in both is not listed");
  assert.ok(byText(compare, "button", "Restore this as a new version").length);

  // an older revision is shown exactly, not edited
  page = await step(main, () => byText(compare, "a", "Open this version")[0].click(), entry, "r2");
  assert.equal(decodeURIComponent(window.location.hash), "#/source/" + PORTRAYAL + "@r2");
  assert.ok(byText(page.querySelector("section.source-header"), "span", "Older version").length);
  assert.ok(page.querySelector("div.source-meta").textContent.includes("version 2 of 5"));
  assert.ok(byText(page, "div", "shown exactly as it was written").length);
  assert.equal(stateOf(page, "Description"), "inherited");
  assert.equal(byText(fieldRow(page, "Description"), "button", "").length, 0, "an older revision has no tools");
  page = await go(at(PORTRAYAL + "@r2", "?tab=edit"), entry, "r2's Edit tab");
  assert.ok(!page.querySelector("div.source-edit").querySelector("input"), "an older revision has no editor");
  assert.equal(byText(page.querySelector("div.source-edit"), "a", "Edit the current version")[0].getAttribute("href"), at(PORTRAYAL, "?tab=edit"));
  page = await go(at(PORTRAYAL + "@r2", "?tab=raw"), entry, "r2's Raw tab");
  assert.equal(page.querySelector("textarea"), null, "an older revision's payload is read, not edited");
  assert.ok(page.querySelector("pre.source-payload").textContent.includes('"name": "Mara of the quay"'));

  // the lorebook's entry sheet lists its entries, with their keys
  page = await go(at(LORE), entry, "the lorebook");
  assert.equal(page.querySelector("div.g-cover").querySelector("h2").textContent, "Harbor lore");
  const entries = block(page, "Entries").querySelectorAll("div.source-lore");
  assert.deepEqual(entries.map((row) => row.querySelector("span.source-keys").textContent), ["lighthouse", "guild, Silver Guild"]);
  assert.ok(entries[0].textContent.includes("The lighthouse has been dark for a year."));
  assert.ok(byText(entries[1], "span", "Off").length, "an entry that is off says so");
  assert.equal(byText(page, "a", "Start a chat").length, 0, "a lorebook starts no chat");

  // its Raw tab edits the payload as written
  page = await go(at(LORE, "?tab=raw"), entry, "the lorebook's Raw tab");
  const raw = page.querySelector("textarea.source-raw");
  assert.equal(JSON.parse(raw.value).name, "Harbor lore");
  assert.ok(byText(page, "button", "Save raw as version 2").length);
  type(raw, "{ not json");
  byText(page, "button", "Save raw as version 2")[0].click();
  await until(() => alerts(page).some((node) => node.textContent.includes("Not JSON")), 15000, "the raw editor's refusal");
  await until(idle(body), 15000, "the page to settle");
  byText(page, "button", "Discard")[0].click();
  assert.equal(JSON.parse(raw.value).name, "Harbor lore");

  // renamed elsewhere; the older revision still shows what it held, and is restored as a new version
  const lore = await call("sources.read", { ref: LORE });
  await call("sources.save", { ref: LORE, head: lore.head, etag: lore.etag, expected: lore.manifest_etag, fields: { name: "Mara's lore, renamed" } });
  page = await go(at(LORE + "@" + lore.head), entry, "the older lorebook revision");
  assert.equal(page.querySelector("div.g-cover").querySelector("h2").textContent, lore.payload.name);
  assert.equal(block(page, "Entries").querySelectorAll("div.source-lore").length, 2, "an older lorebook revision shows what it holds");
  page = await go(at(LORE, "?tab=revisions"), entry, "the lorebook's revisions");
  const restore = page.querySelector("div.source-compare");
  assert.equal(restore.querySelector("h3").textContent, "Version 1 compared with the current version 2", "the version before is compared first");
  assert.deepEqual(restore.querySelectorAll("div.source-diff").map((row) => row.textContent), ["Name" + lore.payload.name + "Mara's lore, renamed"]);
  byText(restore, "button", "Restore this as a new version")[0].click();
  ask = await dialog("the restore confirmation");
  assert.ok(ask.textContent.includes("It is saved as version 3. The current version stays as it is"));
  page = await press(ask, "Restore as version 3", entry, "the restored lorebook");
  assert.equal(body.querySelector("h1").textContent, lore.payload.name);
  assert.ok(page.querySelector("div.source-meta").textContent.includes("version 3"));
  await until(idle(body), 15000, "the page to settle");

  // -- tags, pins and saved filters: the person's own notes about entries (docs/SHELF.md) ----------------------
  const now = () => main.children[0];
  const drawn = (what, check) => until(() => check(now()) && now(), 15000, what);  // drawn again in place: no new page
  const tileOf = (root, ref) => tiles(root).find((tile) => refOf(tile) === ref);
  const tagsOn = (root, ref) => tileOf(root, ref).querySelector("button.sources-tagsbtn");
  const pinOn = (root, ref) => tileOf(root, ref).querySelector("button.sources-pin");
  const chipOf = (root, tag) => root.querySelectorAll("button.g-chip").find((node) => node.getAttribute("data-tag") === tag);
  const shelfLink = (label) => byText(now().querySelector("nav.sources-shelves"), "a", label)[0];
  const saved = () => now().querySelector("nav.sources-saved");
  const bar = () => now().querySelector("div.sources-bar");
  const barCount = () => bar().querySelector("span.sources-bar-count").textContent;
  const barNote = () => bar().querySelector("span.sources-bar-note").textContent;
  const dialogs = () => body.querySelectorAll("dialog");
  const before = await call("sources.read", { ref: MARA });

  // tag one character from its tile: a refused name is said in the dialog, and what was typed stays
  page = await go("#/sources?role=character", library, "the characters, to tag them");
  assert.equal(tagsOn(page, MARA).textContent, "Add tags");
  tagsOn(page, MARA).focus();
  tagsOn(page, MARA).click();
  let tagging = await dialog("the tag editor");
  assert.ok(tagging.textContent.includes("they never change the entry or its versions"));
  type(tagging.querySelector("input"), "x".repeat(41));
  byText(tagging, "button", "Save tags")[0].click();
  await until(() => alerts(tagging).some((node) => node.textContent.includes("invalid_tag")), 15000, "the refused tag");
  assert.equal(tagging.querySelector("input").value, "x".repeat(41));
  type(tagging.querySelector("input"), " crew ,  Slow  burn, CREW");
  byText(tagging, "button", "Save tags")[0].click();
  page = await drawn("the tags on the tile", (root) => tileOf(root, MARA) && tagsOn(root, MARA).textContent === "crew, Slow burn");
  assert.equal(dialogs().length, 0);
  assert.equal(document.activeElement, tagsOn(page, MARA), "focus is back on the control that opened the editor");
  assert.equal(chipOf(page, "crew").textContent, "crew1");
  assert.equal(chipOf(page, "Slow burn").getAttribute("aria-pressed"), "false");
  // the tags are notes beside the entry: no new version, and the entry's own record is as it was
  const after = await call("sources.read", { ref: MARA });
  assert.deepEqual([after.revisions, after.etag, after.manifest_etag], [before.revisions, before.etag, before.manifest_etag]);

  // pin it from its tile: the toggle is pressed, the Pinned shelf counts it, and focus stays on the toggle
  assert.equal(pinOn(page, MARA).getAttribute("aria-pressed"), "false");
  pinOn(page, MARA).focus();
  pinOn(page, MARA).click();
  page = await drawn("the pin", (root) => tileOf(root, MARA) && pinOn(root, MARA).getAttribute("aria-pressed") === "true");
  assert.equal(pinOn(page, MARA).textContent, "Pinned");
  assert.equal(pinOn(page, MARA).getAttribute("aria-label"), "Pin Mara Vell");
  assert.equal(document.activeElement, pinOn(page, MARA));
  assert.equal(shelfLink("Pinned").textContent, "Pinned1");
  await until(idle(body), 15000, "the page to settle");

  // Select mode: a tile is a toggle with a ring, and the bar counts what is selected
  const selectButton = byText(page, "button", "Select")[0];
  assert.equal(selectButton.getAttribute("aria-pressed"), "false");
  selectButton.click();
  await drawn("Select mode", (root) => root.querySelector("div.sources-bar"));
  assert.equal(selectButton.textContent, "Done selecting");
  assert.equal(selectButton.getAttribute("aria-pressed"), "true");
  assert.equal(barCount(), "0 selected");
  assert.ok(byText(bar(), "button", "Pin")[0].disabled && byText(bar(), "button", "Tag…")[0].disabled);
  // the library cannot move, bind in bulk or delete entries, so the bar does not offer to
  for (const absent of ["Move", "Lorebook", "Delete"]) assert.equal(byText(bar(), "button", absent).length, 0, absent);
  const pick = (ref) => tileOf(now(), ref).querySelector("a.g-tile");
  assert.equal(pick(MARA).getAttribute("role"), "button");
  assert.equal(pick(MARA).getAttribute("aria-pressed"), "false");
  assert.equal(tileOf(now(), MARA).querySelector("button"), null, "a tile's own controls wait while selecting");
  const hash = window.location.hash;
  pick(MARA).click();
  assert.equal(pick(MARA).getAttribute("aria-pressed"), "true");
  assert.ok(tileOf(now(), MARA).className.split(" ").includes("sources-picked"), "a selected tile has its ring");
  assert.equal(window.location.hash, hash, "a tile does not open while selecting");
  assert.equal(barCount(), "1 selected");
  pick(MARA).click();
  assert.equal(barCount(), "0 selected");
  byText(bar(), "button", "Select all shown")[0].click();
  const shownNow = tiles(now()).length;
  await until(() => barCount() === shownNow + " selected", 15000, "everything shown selected");
  assert.ok(tiles(now()).every((tile) => tile.querySelector("a.g-tile").getAttribute("aria-pressed") === "true"));

  // a bulk tag: each selected entry gets the tag and keeps its others
  byText(bar(), "button", "Tag…")[0].click();
  tagging = await dialog("the bulk tag dialog");
  assert.equal(tagging.getAttribute("aria-label"), "Tag " + shownNow + " entries");
  byText(tagging, "button", "Apply to " + shownNow + " entries")[0].click();
  await until(() => alerts(tagging).some((node) => node.textContent.includes("Name at least one tag")), 15000, "nothing named");
  type(tagging.querySelectorAll("input")[0], "harbor");
  byText(tagging, "button", "Apply to " + shownNow + " entries")[0].click();
  await until(() => bar() && barNote() === "Changed the tags of " + shownNow + " entries.", 15000, "the bulk tag");
  assert.equal(barCount(), "0 selected");
  let kept = await call("shelf.read", {});
  assert.equal(kept.entries.length, shownNow);
  assert.deepEqual(kept.entries.find((item) => item.ref === MARA).tags, ["crew", "harbor", "Slow burn"]);
  assert.ok(kept.entries.every((item) => item.tags.includes("harbor")));

  // a bulk pin says which entry it could not change (it was tagged elsewhere meanwhile), and leaves it selected
  byText(bar(), "button", "Select all shown")[0].click();
  await until(() => barCount() === shownNow + " selected", 15000, "everything selected again");
  const behind = kept.entries.find((item) => item.ref !== MARA);
  await call("shelf.edit_tags", { ref: behind.ref, add: ["elsewhere"], expected: behind.etag });
  byText(bar(), "button", "Pin")[0].click();
  await until(() => bar() && barNote().startsWith("Pinned " + (shownNow - 1) + " entries. 1 was not changed:"), 15000, "the bulk pin");
  assert.ok(barNote().endsWith("(changed)."), barNote());
  assert.equal(barCount(), "1 selected");
  assert.ok(alerts(body).some((node) => node.textContent.includes("1 was not changed")), "what was refused is announced");
  assert.equal(pick(behind.ref).getAttribute("aria-pressed"), "true");
  await until(idle(body), 15000, "the page to settle");
  byText(bar(), "button", "Pin")[0].click();  // the page has read its marks again: now it goes through
  await until(() => bar() && barNote() === "Pinned 1 entry.", 15000, "the pin that was refused");
  kept = await call("shelf.read", {});
  assert.ok(kept.entries.every((item) => item.pinned) && kept.entries.find((item) => item.ref === behind.ref).tags.includes("elsewhere"));
  selectButton.click();
  await drawn("the end of Select mode", (root) => !root.querySelector("div.sources-bar"));
  assert.equal(selectButton.textContent, "Select");
  assert.equal(pick(MARA).getAttribute("role"), null);

  // the tag chips count what is in view, and several narrow together (AND)
  page = now();
  assert.equal(chipOf(page, "harbor").textContent, "harbor" + shownNow);
  page = await step(main, () => chipOf(page, "crew").click(), library, "the crew");
  assert.ok(window.location.hash.includes("tags=crew") && window.location.hash.includes("role=character"));
  assert.deepEqual(tiles(page).map(refOf), [MARA]);
  assert.equal(chipOf(page, "crew").getAttribute("aria-pressed"), "true");
  assert.equal(pressed(group(page, "Which entries")), "Characters 1", "the roles count what the tags leave");
  page = await step(main, () => chipOf(page, "elsewhere").click(), library, "crew and elsewhere");
  assert.ok(window.location.hash.includes("tags=" + encodeURIComponent("crew,elsewhere")));
  assert.equal(tiles(page).length, 0);
  assert.ok(byText(page, "p", "Nothing here carries all of those tags.").length);
  page = await step(main, () => chipOf(page, "elsewhere").click(), library, "the crew alone again");

  // save this view as a filter: it is listed with its count, and is the one in view
  byText(page, "button", "Save this filter")[0].click();
  let naming = await dialog("the save dialog");
  assert.equal(naming.querySelector("input").value, "crew characters");
  type(naming.querySelector("input"), "The crew");
  byText(naming, "button", "Save filter")[0].click();
  page = await drawn("the saved filter", (root) => byText(root.querySelector("nav.sources-saved"), "a", "The crew").length);
  const crew = () => byText(saved(), "a", "The crew")[0];
  assert.equal(crew().textContent, "The crew1");
  assert.equal(crew().getAttribute("aria-current"), "page");
  assert.equal(byText(page, "button", "Save this filter").length, 0, "this view is saved already");
  // a second filter of the same name is refused in its dialog
  page = await go("#/sources?shelf=pinned", library, "the pinned shelf");
  assert.equal(tiles(page).length, shownNow, "every character was pinned");
  assert.equal(page.querySelector("nav.sources-shelves").querySelector("a[aria-current]").textContent, "Pinned" + shownNow);
  byText(page, "button", "Save this filter")[0].click();
  naming = await dialog("the second save dialog");
  type(naming.querySelector("input"), "the CREW");
  byText(naming, "button", "Save filter")[0].click();
  await until(() => alerts(naming).some((node) => node.textContent.includes("filter_name_taken")), 15000, "the name that is taken");
  byText(naming, "button", "Cancel")[0].click();
  // unpinning an entry on the Pinned shelf takes it off the shelf, in place
  pinOn(page, behind.ref).click();
  page = await drawn("one fewer pinned", (root) => tiles(root).length === shownNow - 1);
  assert.equal(tileOf(page, behind.ref), undefined);
  assert.equal(shelfLink("Pinned").textContent, "Pinned" + (shownNow - 1));
  await until(idle(body), 15000, "the page to settle");
  // from there the saved filter opens its own view: its role, its tags and nothing of this one
  page = await step(main, () => crew().click(), library, "the saved filter's view");
  assert.ok(window.location.hash.includes("role=character") && window.location.hash.includes("tags=crew") && !window.location.hash.includes("shelf="));
  assert.deepEqual(tiles(page).map(refOf), [MARA]);
  // the list shows the tags in their own column
  page = await step(main, () => byText(group(page, "View"), "button", "List")[0].click(), library, "the crew as a list");
  const cells = page.querySelector("table.g-table").querySelector("tbody").querySelector("tr").querySelectorAll("td");
  assert.equal(cells[3].querySelector("button.sources-tagsbtn").textContent, "crew, harbor, Slow burn");
  assert.equal(cells.at(-1).querySelector("button.sources-pin").getAttribute("aria-pressed"), "true");
  // rename it, then delete it after one confirmation that names it
  byText(saved(), "button", "Edit")[0].click();
  let editing = await dialog("the saved filter's dialog");
  type(editing.querySelector("input"), "Crew only");
  byText(editing, "button", "Rename")[0].click();
  await drawn("the renamed filter", (root) => byText(root.querySelector("nav.sources-saved"), "a", "Crew only").length);
  assert.equal(byText(saved(), "a", "The crew").length, 0);
  byText(saved(), "button", "Edit")[0].click();
  editing = await dialog("the saved filter's dialog again");
  byText(editing, "button", "Delete")[0].click();
  const sure = await until(() => dialogs().length === 2 && dialogs()[1], 15000, "the confirmation");
  assert.ok(sure.textContent.includes('Delete the saved filter "Crew only"? No entry, tag or pin is changed.'));
  byText(sure, "button", "Delete filter")[0].click();
  await drawn("no saved filter", (root) => byText(root.querySelector("nav.sources-saved"), "p", "None yet.").length);
  assert.equal(dialogs().length, 0);
  assert.deepEqual((await call("shelf.read", {})).filters, []);

  // the sheet shows the same tags as pills and the same pin; they belong to the entry, whatever version shows
  const marks = (root) => root.querySelector("span.source-marks");
  const pills = (root) => marks(root).querySelectorAll("a.source-tag").map((node) => node.textContent);
  page = await go(at(MARA), entry, "Mara's sheet");
  assert.deepEqual(pills(page), ["crew", "harbor", "Slow burn"]);
  assert.equal(marks(page).querySelector("a.source-tag").getAttribute("href"), "#/sources?tags=crew");
  const sheetPin = () => marks(now()).querySelector("button.source-pin");
  assert.equal(sheetPin().getAttribute("aria-pressed"), "true");
  sheetPin().click();
  await until(() => sheetPin().getAttribute("aria-pressed") === "false" && sheetPin().textContent === "Pin", 15000, "unpinned from the sheet");
  await until(idle(body), 15000, "the page to settle");
  byText(marks(now()), "button", "Edit tags")[0].click();
  tagging = await dialog("the sheet's tag editor");
  assert.ok(byText(tagging, "button", "elsewhere").length, "the tags already in use are offered");
  type(tagging.querySelector("input"), "crew");
  byText(tagging, "button", "Save tags")[0].click();
  await until(() => pills(now()).join() === "crew", 15000, "the sheet's tags");
  page = await go(at(LORE + "@r1"), entry, "an older version of the lorebook");
  assert.deepEqual(pills(page), []);
  byText(marks(page), "button", "Add tags")[0].click();
  tagging = await dialog("the older version's tag editor");
  type(tagging.querySelector("input"), "old lore");
  byText(tagging, "button", "Save tags")[0].click();
  await until(() => pills(now()).join() === "old lore", 15000, "the lorebook's tag");
  page = await go(at(LORE), entry, "the lorebook's current version");
  assert.deepEqual(pills(page), ["old lore"], "a tag is the entry's, not one version's");
  assert.deepEqual((await call("sources.read", { ref: LORE })).revisions, ["r1", "r2", "r3"], "tagging wrote no version");

  // -- pinned chats ------------------------------------------------------------------------------------------
  const chatList = (root) => root.querySelector("ul.chats-rows");
  const chatPins = () => now().querySelectorAll("button.chats-pin");
  const linkBeside = (button) => button.parentNode.querySelector("a.chats-row").getAttribute("href");
  const pinOf = (ref) => chatPins().find((button) => linkBeside(button) === "#/chat/" + encodeURIComponent(ref));
  const heads = () => now().querySelectorAll("button.chats-head").map((node) => node.querySelector("span.g-title").textContent);
  const etags = async () => Object.fromEntries((await call("sources.chats", {})).chats.map((item) => [item.chat, item.etag]));
  const chatsBefore = await etags();
  page = await go("#/chats", chatList, "the chats");
  const everyChat = chatPins().length;
  assert.ok(everyChat >= 2 && chatPins().every((button) => button.getAttribute("aria-pressed") === "false" && button.textContent === "Pin"));
  assert.ok(!heads().includes("Pinned"));
  assert.ok(byText(group(page, "Which chats"), "button", "Pinned").length, "the Pinned filter is offered");
  pinOf(CHAT).focus();
  pinOf(CHAT).click();
  await until(() => heads()[0] === "Pinned" && pinOf(CHAT).getAttribute("aria-pressed") === "true", 15000, "the pinned chat");
  assert.equal(pinOf(CHAT).textContent, "Pinned");
  assert.equal(document.activeElement, pinOf(CHAT), "focus follows the chat into its new group");
  assert.equal(linkBeside(chatPins()[0]), "#/chat/" + encodeURIComponent(CHAT), "the pinned chat is lifted to the top");
  assert.equal(chatPins().length, everyChat, "and is listed once");
  assert.ok(now().querySelector("div.chats-status").textContent.startsWith(everyChat + " chats in "));
  assert.deepEqual(await etags(), chatsBefore, "pinning a chat does not touch the chat");
  await until(idle(body), 15000, "the page to settle");
  // Pinned alone, from its segment: the filter is in the address
  page = await step(main, () => byText(group(now(), "Which chats"), "button", "Pinned")[0].click(), chatList, "the pinned chats");
  assert.equal(window.location.hash, "#/chats?filter=pinned");
  assert.equal(chatPins().length, 1);
  assert.deepEqual(heads(), ["Pinned"]);
  pinOf(CHAT).click();
  await until(() => byText(now(), "p", "No chat is pinned yet.").length, 15000, "nothing pinned");
  assert.deepEqual((await call("shelf.read", {})).chats, []);
  await until(idle(body), 15000, "the page to settle");
});
