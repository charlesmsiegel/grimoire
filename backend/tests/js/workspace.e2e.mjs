// Sprint 065's first useful proof end to end, in fakedom against a real app server: a half-written post in one
// chat, then that same thread reached from Chats, from its character's and its scenario's pages, from its
// campaign's hub and from its world (the World chooser on Chats and a world entry's page), each by the chat's exact
// reference, with the composer still holding the draft every time and nothing posted.
// Run by test_scene_workspace_js.py, which serves a synthetic library and passes GRIMOIRE_APP, GRIMOIRE_TOKEN,
// GRIMOIRE_CHAT (the campaign chat), GRIMOIRE_CHARACTER and GRIMOIRE_SCENARIO (sources it binds, unpinned),
// GRIMOIRE_CAMPAIGN and GRIMOIRE_WORLD (its campaign and its campaign's world).
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
const env = (name) => process.env["GRIMOIRE_" + name];
const DRAFT = "I check the ledger again for the missing key.";

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

function pick(select, value) {
  select.value = value;
  select.dispatchEvent({ type: "change" });
}

test("Chats, Characters, Scenarios, Campaigns and Worlds reach the same thread without losing its draft", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(env("TOKEN"));
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  const chatHash = "#/chat/" + encodeURIComponent(env("CHAT"));
  const composer = () => main.querySelector(".chat-composer")?.querySelector("textarea");

  // the draft: typed, never posted
  window.location.hash = chatHash;
  type(await until(composer, 15000, "the chat"), DRAFT);

  // From wherever the list is, the thread's own row (its exact ref) opens it with the draft still there.
  async function rowToThread(where) {
    const row = await until(() => main.querySelectorAll("a.chats-row").find((node) => node.getAttribute("href") === chatHash),
      15000, "the thread listed " + where);
    row.focus();
    row.click();  // a link: the shell keeps every chat's composer draft across the move
    const box = await until(composer, 15000, "the thread from " + where);
    assert.equal(window.location.hash, chatHash, where + " opens the same thread");
    assert.equal(box.value, DRAFT, where + " keeps the draft");
  }
  async function follow(label, where) {
    const link = await until(() => byText(main, "a", label)[0], 15000, label + " on " + where);
    const to = link.getAttribute("href");
    link.click();
    return to;
  }

  // 1. Chats
  window.location.hash = "#/chats";
  await rowToThread("Chats");

  // 2. the character's page: its chats by exact reference, and with derived portrayals
  window.location.hash = "#/source/" + encodeURIComponent(env("CHARACTER"));
  const derived = await until(() => byText(main, "a", "Include derived portrayals")[0], 15000, "the derived link");
  assert.ok(derived.getAttribute("href").includes("match=derived"));
  const exact = await follow("Browse these chats", "the character");
  assert.ok(exact.startsWith("#/chats?filter=character&source="), exact);
  await rowToThread("the character's chats");
  window.location.hash = derived.getAttribute("href");
  await until(() => byText(main, "h2", "Matched source revisions and validated ancestry").length, 15000, "the comparisons");
  await rowToThread("the character's chats with derived portrayals");

  // 3. the scenario's page
  window.location.hash = "#/source/" + encodeURIComponent(env("SCENARIO"));
  assert.ok((await follow("Browse these chats", "the scenario")).startsWith("#/chats?filter=scenario&source="));
  await rowToThread("the scenario's chats");

  // 4. the campaign's hub
  window.location.hash = "#/campaign/" + encodeURIComponent(env("CAMPAIGN"));
  const hubChip = await until(() => main.querySelectorAll("a.campaign-chip").find((node) => node.getAttribute("href").includes(encodeURIComponent(env("CHAT")))),
    15000, "the thread among the campaign's chats");
  assert.ok(hubChip);
  const toWorld = await until(() => byText(main, "a", "Chats in this world")[0], 15000, "the hub's world link");
  const worldHref = toWorld.getAttribute("href");
  assert.equal(await follow("Browse in Chats", "the hub"), "#/chats?filter=campaign&campaign=" + encodeURIComponent(env("CAMPAIGN")));
  await rowToThread("the campaign's chats");

  // 5. the world: from the hub, from the World chooser on Chats, and from a world entry's own page
  window.location.hash = worldHref;
  await rowToThread("the world's chats");
  window.location.hash = "#/chats?filter=world";
  const chooser = await until(() => main.querySelectorAll("select").find((node) => node.querySelectorAll("option").some((option) => option.getAttribute("value") === env("WORLD"))),
    15000, "the World chooser listing the world");
  pick(chooser, env("WORLD"));
  await until(() => window.location.hash.includes("world=" + encodeURIComponent(env("WORLD"))), 15000, "the chosen world");
  await rowToThread("the World chooser");
  window.location.hash = "#/source/" + encodeURIComponent(env("CHARACTER"));
  await follow("Chats in this world", "a world entry");
  await rowToThread("a world entry's page");

  // the draft is still the composer's alone (the harness checks that nothing was posted)
  assert.equal(composer().value, DRAFT);
});
