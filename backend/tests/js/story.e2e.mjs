// 081's story threads end to end, in fakedom against a real app server: from a campaign's hub to its story
// threads, an early promise found by how it ended, followed to the ruling that decided it, and back to the turn
// it was made at in the chat. The table's view never shows how canon says it ended.
// Run by test_story_page.py, which serves a synthetic library whose card chat has three played turns and a ledger
// that records the promise, its report to the player's character and canon's ruling, and passes GRIMOIRE_APP,
// GRIMOIRE_TOKEN, GRIMOIRE_CHAT, GRIMOIRE_CAMPAIGN, GRIMOIRE_PROMISE and GRIMOIRE_SOLD (the promise's text, and
// the summary of the event that broke it).
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
const chat = process.env.GRIMOIRE_CHAT;
const campaign = process.env.GRIMOIRE_CAMPAIGN;
const PROMISE = process.env.GRIMOIRE_PROMISE;
const SOLD = process.env.GRIMOIRE_SOLD;

const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;

test("a promise found by how it ended, followed to its ruling and back to its turn", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the hub links the chat's story threads, in the view it is in
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat) + "&audience=director";
  const link = await until(() => byText(main, "a", "Story threads")[0], 15000, "the hub's link");
  assert.equal(link.getAttribute("href"), "#/story/" + encodeURIComponent(chat) + "?audience=director");
  link.click();
  await until(() => section(main, "Threads"), 15000, "the story threads");
  assert.ok(main.querySelectorAll("a.story-row").some((row) => row.textContent.includes(PROMISE)));

  // how it ended finds the promise
  const box = main.querySelector("input.g-searchbox");
  box.value = "night market";
  byText(main, "button", "Find")[0].click();
  await until(() => window.location.hash.includes("q=night+market"), 15000, "the search");
  const row = await until(() => main.querySelectorAll("a.story-row").length === 1 && main.querySelector("a.story-row"), 15000, "one match");
  assert.ok(row.textContent.includes(PROMISE) && row.textContent.includes("Refuted"));
  row.click();
  const ended = await until(() => section(main, "How it ended"), 15000, "the thread");
  assert.ok(byText(ended, "span", "Refuted").length && ended.textContent.includes(SOLD));
  assert.ok(section(main, "Contradictions").textContent.includes("Mara Vell still believes it"));
  const entries = section(main, "Through the story").querySelectorAll("li.story-entry");
  assert.ok(entries.length >= 4);
  const made = entries[0].querySelector("a");
  assert.equal(made.getAttribute("href"), "#/chat/" + encodeURIComponent(chat) + "?turn=1");

  // the table's view of the same thread: what Anya heard, and nothing of canon
  byText(main, "button", "Table view")[0].click();
  await until(() => !window.location.hash.includes("audience=director") && section(main, "Threads"), 15000, "the table's view");
  await until(() => !main.textContent.includes("Refuted"), 15000, "no canon at the table");
  window.location.hash = "#/story/" + encodeURIComponent(chat) + "?thread=p-key";
  const told = await until(() => section(main, "Through the story"), 15000, "the table's thread");
  assert.ok(told.textContent.includes("Anya Rook heard it from Mara Vell"));
  assert.ok(!main.textContent.includes(SOLD), "how canon says it ended is the director's");

  // and back to the turn the promise was made at
  told.querySelector("a").click();
  await until(() => window.location.hash.startsWith("#/chat/"), 15000, "the chat");
  assert.ok(window.location.hash.endsWith("?turn=1"));
});
