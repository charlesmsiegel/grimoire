// The inbox ("Needs you") end to end, in fakedom against a real app server whose library has four things
// waiting: a chat that forked, a picture in a keep-both conflict with a paired device, an action whose labelling
// waits on the player, and a change waiting on the player's key. From Home's card and the rail's count to the
// inbox, where exactly one item is expanded; the chat's tray and the sync page point to it; each item is decided
// there by its own op (a cancelled confirmation decides nothing), and what a decision set aside is listed; then
// the empty state. Run by test_app_inbox.py, which serves that library (with the other device reachable) and
// passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT (the chat with the action and the change) and GRIMOIRE_PEER.
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
const peer = process.env.GRIMOIRE_PEER;

test("four things wait: found from Home, the rail, a chat and sync, and each decided in the inbox", { skip: !base }, async () => {
  const window = install(base);
  // the app is opened on Chats, a page that reads no feed: the count in the rail is the shell's own doing
  window.location.hash = "#/chats?token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  const nav = body.querySelector("nav");
  const rail = () => nav.querySelectorAll("a").find((node) => node.getAttribute("data-route") === "inbox");
  const counted = (count) => until(() => {
    const badge = rail() && rail().querySelector("span.g-nav-count");
    return count ? badge && badge.textContent === String(count) && badge : rail() && !badge;
  }, 20000, "the rail to count " + count);
  const idle = () => until(() => !main.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
  const answerDialog = async (label) => {
    const dialog = await until(() => body.querySelector("dialog"), 15000, "the confirmation");
    const text = dialog.textContent;
    byText(dialog, "button", label).at(-1).click();
    return text;
  };

  // the rail counts what waits from the start, before the inbox or Home was ever opened, between Home and Chats
  await until(() => window.location.hash === "#/chats", 15000, "Chats");
  const badge = await counted(4);
  assert.ok(badge.classList.contains("g-bad") && badge.getAttribute("aria-label") === "4 things need you");
  const play = nav.querySelectorAll("a.g-nav-item").map((node) => node.getAttribute("data-route"));
  assert.deepEqual(play.slice(0, 3), ["home", "inbox", "chats"]);
  window.location.hash = "#/home";

  // Home shows the top item only, as one card that opens the inbox on it
  const top = await until(() => main.querySelector("a.home-top"), 20000, "Home's card");
  assert.ok(top.classList.contains("g-accent") && top.querySelector("span.g-pill").textContent === "Fork");
  assert.ok(top.textContent.includes("went two ways") && top.textContent.includes("and 3 more") && top.textContent.includes("Decide"));
  assert.ok(top.getAttribute("href").startsWith("#/inbox?item=fork"));
  assert.equal(byText(main, "a", "Open the inbox")[0].getAttribute("href"), "#/inbox");

  // the chat says what waits on it in one row above the composer; a labelling with factors to read is decided
  // in the inbox, not from the row
  window.location.hash = "#/chat/" + encodeURIComponent(chat);
  const tray = await until(() => main.querySelector("div.chat-tray"), 20000, "the chat's tray");
  assert.equal(tray.querySelector("span.g-pill").textContent, "Action");
  assert.ok(tray.querySelector("span.chat-tray-title").textContent.includes("five coins"));
  assert.equal(tray.querySelectorAll("button").length, 0);
  assert.ok(byText(tray, "a", "Decide")[0].getAttribute("href").startsWith("#/inbox?item=label"));
  const more = byText(tray, "a", "+1 more waiting")[0];
  assert.equal(more.getAttribute("href"), "#/inbox");

  // Devices and sync decides nothing: one card says how many wait, and where
  window.location.hash = "#/sync";
  const waiting = await until(() => main.querySelector('section[aria-label="Waiting from sync"]'), 20000, "the sync page");
  assert.ok(waiting.textContent.includes("2 things from sync are waiting"));
  assert.equal(main.querySelectorAll("article").length, 0, "no conflict or fork is decided here");
  byText(waiting, "a", "Decide in Needs you")[0].click();

  // the inbox: four items in order, exactly one expanded
  await until(() => window.location.hash === "#/inbox" && main.querySelector("section.inbox-open"), 20000, "the inbox");
  assert.equal(body.querySelector("h1").textContent, "Needs you");
  assert.ok(main.querySelector("span.inbox-count").textContent === "4 things need you");
  assert.ok(main.textContent.includes("Work down the list. Each one is decided right here."));
  const open = () => main.querySelectorAll("section.inbox-open");
  const rows = () => main.querySelectorAll("button.inbox-row");
  const kinds = () => main.querySelectorAll("section.inbox-open,button.inbox-row").map((node) => node.querySelector("span.g-pill").textContent);
  assert.deepEqual(kinds(), ["Fork", "Conflict", "Action", "Your approval"]);
  assert.equal(open().length, 1);
  assert.equal(open()[0].querySelector("span.g-pill").textContent, "Fork", "the first is the one expanded");
  assert.ok(open()[0].classList.contains("g-accent") && open()[0].querySelector("span.g-pill").classList.contains("g-accent"));
  assert.equal(rows().length, 3);
  assert.ok(rows().every((node) => node.textContent.includes("Decide") && !node.querySelector("p")), "a collapsed row is its kind, title and where");

  // a click expands another, and the first collapses
  const row = (text) => rows().find((node) => node.textContent.includes(text));
  row("avatar picture").click();
  await until(() => open().length === 1 && open()[0].querySelector("span.g-pill").textContent === "Conflict", 15000, "the conflict expanded");
  assert.equal(rows().length, 3);
  assert.ok(rows().some((node) => node.querySelector("span.g-pill").textContent === "Fork"), "the fork is a row again");
  let card = open()[0];
  assert.equal(document.activeElement, card.querySelector("h2"), "focus goes to what was opened");
  assert.ok(card.querySelector("p.inbox-detail").textContent.includes("Both versions are kept"));
  // every version is shown, side by side, with whose it is
  for (const holder of ["This device", peer]) assert.ok(byText(card, "div", holder).length, holder);
  assert.deepEqual(card.querySelectorAll("button").map((node) => node.textContent), ["Keep this device's", "Keep " + peer + "'s"]);
  assert.ok(card.querySelectorAll("button")[0].classList.contains("g-primary") && !card.querySelectorAll("button")[1].classList.contains("g-primary"));
  assert.ok(byText(card, "a", "Open where it happened")[0].getAttribute("href").startsWith("#/art?subject="));

  // the same confirmation as ever: saying no decides nothing
  byText(card, "button", "Keep " + peer + "'s")[0].click();
  assert.ok((await answerDialog("Cancel")).includes("is set aside here and on " + peer));
  await idle();
  assert.equal(main.querySelector("span.inbox-count").textContent, "4 things need you");
  byText(open()[0], "button", "Keep " + peer + "'s")[0].click();
  await answerDialog("Keep it");
  const settled = await until(() => main.querySelector("div.inbox-settled"), 20000, "the conflict decided");
  assert.ok(settled.querySelector("span.g-dot").classList.contains("g-good"));
  assert.ok(settled.querySelector("span.inbox-struck").textContent.includes("avatar picture"));
  assert.equal(settled.querySelector("span.inbox-result").textContent, "Kept " + peer + "'s version");
  // what was not kept is listed, never lost unseen
  const report = settled.querySelector('section[aria-label="Resolved"]');
  assert.ok(report.textContent.includes("Not kept:") && byText(report, "div", "This device").length);
  await idle();
  await until(() => main.querySelector("span.inbox-count").textContent === "3 things need you", 15000, "the list read again");
  await counted(3);

  // the change is shown in full before the key is given, and endorsed exactly as shown
  row("Anya pays Mara a coin").click();
  await until(() => open().length === 1 && open()[0].querySelector("span.g-pill").textContent === "Your approval", 15000, "the change expanded");
  card = open()[0];
  assert.ok(card.textContent.includes("begin knows: Mara Vell → Anya Rook (by Anya Rook)"), card.textContent);
  assert.ok(card.textContent.includes("Your key: not given") && card.textContent.includes("Director: not yet"));
  byText(card, "button", "Endorse as shown")[0].click();
  assert.ok((await answerDialog("Endorse")).includes("exactly as shown: Anya pays Mara a coin"));
  await until(() => byText(main, "span", "Your key is given").length, 20000, "the endorsement");
  await idle();

  // the labelling shows what confirming locks in beside the button that confirms it
  await until(() => rows().length === 1 && open().length === 1, 15000, "two left");
  (row("five coins") || { click() {} }).click();
  await until(() => open()[0].querySelector("span.g-pill").textContent === "Action", 15000, "the action expanded");
  card = open()[0];
  assert.ok(card.textContent.includes("What bears on it") && card.textContent.includes("Mara owes a favour."));
  assert.ok(card.querySelector("p.inbox-detail").textContent.includes("Get into the cargo hold."));
  assert.ok(byText(card, "a", "Open where it happened")[0].getAttribute("href").startsWith("#/campaign/"));
  byText(card, "button", "Confirm the labelling")[0].click();
  await until(() => byText(main, "span", "Labelling confirmed").length, 20000, "the confirmation");
  await idle();

  // the fork: one branch goes on, and the turns set aside are listed
  await until(() => open().length === 1 && rows().length === 0, 15000, "one left");
  card = open()[0];
  assert.equal(card.querySelector("span.g-pill").textContent, "Fork");
  assert.deepEqual(card.querySelectorAll("button").map((node) => node.textContent).sort(), ["Go on with " + peer + "'s", "Go on with this device's"]);
  byText(card, "button", "Go on with this device's")[0].click();
  assert.ok((await answerDialog("Go on with it")).includes("no one hears them"));
  const chosen = await until(() => main.querySelector('section[aria-label="Branch chosen"]'), 20000, "the choice");
  assert.ok(chosen.textContent.includes("Set aside, heard by no one:"));
  await idle();

  // nothing is left: the empty state, with the way back to the chat played last, and no count in the rail
  const empty = await until(() => main.querySelector("section.inbox-empty"), 15000, "the empty state");
  assert.ok(empty.classList.contains("g-dashed") && empty.textContent.includes("Nothing needs you"));
  const back = empty.querySelector("a");
  assert.ok(back.textContent.startsWith("Back to ") && back.getAttribute("href").startsWith("#/chat/"));
  assert.equal(main.querySelector("span.inbox-count").textContent, "All clear");
  assert.equal(main.querySelectorAll("div.inbox-settled").length, 4, "what was decided stays on the page, struck through");
  assert.equal(open().length + rows().length, 0);
  await counted(0);

  // and everywhere else agrees, because each reads the same feed
  window.location.hash = "#/home";
  await until(() => byText(main, "div", "Nothing needs you.").length, 20000, "Home with nothing waiting");
  assert.equal(main.querySelector("a.home-top"), null);
  window.location.hash = "#/sync";
  const quiet = await until(() => main.querySelector('section[aria-label="Waiting from sync"]'), 20000, "the sync page again");
  assert.ok(quiet.textContent.includes("Nothing from sync is waiting") && !byText(quiet, "a", "Decide in Needs you").length);
  window.location.hash = "#/chat/" + encodeURIComponent(chat);
  await until(() => main.querySelector("div.chat-composer"), 20000, "the chat again");
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal(main.querySelector("div.chat-tray"), null, "nothing waits on this chat now");
});
