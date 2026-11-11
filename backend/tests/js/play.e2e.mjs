// Sprint 111's pages end to end in the shell (108), against a real app server, in fakedom: Start a chat from a
// character played as a persona, post with a typed mode, wait for the answer, pause, skip a line, ask again, step
// through the takes and keep one by its keys, bookmark moments on the Story tab and jump to one, strike, see a
// reply fail and ask again, pass, add a card, read "Last time" as another day would show it, then find
// the chat under All and among the chats to continue on Start a chat. A second test mends a chat whose added card is gone.
// Run by test_app_play_js.py, which serves a synthetic card library with a scripted model and passes
// GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHARACTER (Mara), GRIMOIRE_PERSONA (Anya) and GRIMOIRE_NEWCOMER (Tamsin),
// or, for the second test, GRIMOIRE_BROKEN: a chat whose newcomer's card was removed, or, for the third,
// GRIMOIRE_UNFINISHED: the server's first opening is cut off, so Start Chat's start stops before it finishes,
// or, for the fourth, GRIMOIRE_NOMODEL: a chat in an app with no model set up.
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

const env = process.env;

// The select of the labelled field named `label`, under `root`.
function choice(root, label) {
  const field = root.querySelectorAll("div.g-field").find((node) => {
    const caption = node.querySelector("label");
    return caption && caption.textContent === label;
  });
  return field ? field.querySelector("select") : null;
}

function pick(select, value) {
  select.value = value;
  select.dispatchEvent({ type: "change" });
}

// Start a chat's controls: the "Start from" cards, the portrait tiles, the persona chips and the openings.
const kindCard = (main, kind) => main.querySelector('button.start-kind[data-kind="' + kind + '"]');
const tileOf = (main, ref) => main.querySelectorAll("button.start-pick").find((node) => node.getAttribute("data-ref") === ref);
const chipOf = (main, ref) => main.querySelectorAll("button.g-chip").find((node) => node.getAttribute("data-ref") === ref);
const openingsOf = (main) => main.querySelectorAll("button.start-opening");
const pressed = (node) => node.getAttribute("aria-pressed") === "true";

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

// The element under `root` whose text is exactly `text` (byText matches any text that includes it).
const exact = (root, tag, text) => root.querySelectorAll(tag).find((node) => node.textContent === text);

// A key pressed with focus where it is: every keydown listener of the document hears it, as in a browser.
function press(key, more = {}) {
  const event = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...more };
  for (const listener of document.listeners.get("keydown") || []) listener(event);
  return event;
}

// Hold back the app's answers to one op until the returned function is called.
function hold(prefix) {
  const fetchNow = globalThis.fetch;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  globalThis.fetch = async (url, options) => { if (String(url).startsWith(prefix)) await held; return fetchNow(url, options); };
  return () => { globalThis.fetch = fetchNow; release(); };
}

// Answer one op with a refusal, as a provider that is down would be; the returned function puts the app back.
function fail(prefix, refusal) {
  const fetchNow = globalThis.fetch;
  globalThis.fetch = async (url, options) => (String(url).startsWith(prefix)
    ? new Response(JSON.stringify(refusal), { status: 502, headers: { "Content-Type": "application/json" } }) : fetchNow(url, options));
  return () => { globalThis.fetch = fetchNow; };
}

const idle = (body) => !body.querySelectorAll("button").some((node) => node.disabled && node.textContent !== "Post");
const takes = (main) => main.querySelectorAll("article.chat-take");
const kept = (main) => main.querySelectorAll("div.chat-line").filter((node) => !takes(main).some((take) => take.contains(node)));

// What a browser keeps beyond the session: the chat page remembers there where this device left a chat.
class Kept {
  constructor() { this.items = new Map(); }
  getItem(key) { return this.items.has(key) ? this.items.get(key) : null; }
  setItem(key, value) { this.items.set(key, String(value)); }
  removeItem(key) { this.items.delete(key); }
}

async function boot() {
  const window = install(env.GRIMOIRE_APP);
  window.localStorage = new Kept();
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  return { window, body, main };
}

test("Start Chat, play a turn, correct it and find the chat again", { skip: !env.GRIMOIRE_APP || Boolean(env.GRIMOIRE_BROKEN || env.GRIMOIRE_UNFINISHED || env.GRIMOIRE_NOMODEL) }, async () => {
  const { window, body, main } = await boot();
  const nav = await until(() => byText(body.querySelector("nav"), "a", "New chat").length && body.querySelector("nav"), 15000, "the menu");

  // Chats has the way to a new chat beside its search, and All among its filters
  byText(nav, "a", "Chats")[0].click();
  await until(() => byText(main, "button", "All").length && byText(main, "p", "No chats yet").length, 15000, "an empty chat list");
  assert.equal(main.querySelector("div.chats-status").textContent, "", "nothing is counted in an empty list");
  const startLink = await until(() => byText(main, "a", "New chat")[0], 15000, "New chat on Chats");
  startLink.click();

  // pick Mara, played as Anya, with her default opening
  await until(() => tileOf(main, env.GRIMOIRE_CHARACTER), 15000, "the Start a chat page");
  assert.equal(window.location.hash, "#/start");
  assert.equal(body.querySelector("h1").textContent, "Start a chat");
  // four ways to start, the first chosen; the three steps and the summary of the new chat
  assert.deepEqual(main.querySelectorAll("button.start-kind").map((node) => node.querySelector("span").textContent),
    ["A character", "Character + lorebook", "A scenario", "A premise"]);
  assert.ok(pressed(kindCard(main, "character")) && !pressed(kindCard(main, "premise")));
  assert.deepEqual(main.querySelectorAll("h2").map((node) => node.textContent), ["1 · Start from", "2 · Choose a character", "3 · You play"]);
  const summary = main.querySelector("aside.start-summary");
  assert.ok(byText(summary, "div", "Choose what to start from").length, "nothing is picked yet");
  // a premise written and left unsent stays a draft when a character is started instead
  kindCard(main, "premise").click();
  assert.ok(pressed(kindCard(main, "premise")) && !pressed(kindCard(main, "character")));
  assert.ok(byText(main, "h2", "2 · Write the premise").length && !main.querySelectorAll("button.start-pick").length);
  type(await until(() => main.querySelector("textarea"), 15000, "the premise field"), "A lighthouse keeper's last night.");
  assert.ok(byText(summary, "div", "You in a new premise").length);
  // a scenario is chosen from the scenarios, a character from the characters
  kindCard(main, "scenario").click();
  assert.ok(byText(main, "h2", "2 · Choose a scenario").length && !tileOf(main, env.GRIMOIRE_CHARACTER));
  assert.ok(main.querySelectorAll("button.start-pick").length, "the library's scenario is offered");
  kindCard(main, "bundle").click();
  assert.ok(choice(main, "Lorebook"), "a character with a lorebook offers the lorebook choice");
  kindCard(main, "character").click();
  assert.ok(!choice(main, "Lorebook"));
  // the search narrows the tiles, and says so when nothing matches
  const search = main.querySelector("input.start-search");
  type(search, "no such card");
  assert.ok(!main.querySelectorAll("button.start-pick").length && byText(main, "div", "Nothing matches.").length);
  type(search, "mara");
  assert.equal(main.querySelectorAll("button.start-pick").length, 1, "one character matches");
  type(search, "");
  assert.equal(main.querySelectorAll("button.start-pick").length, 3, "every character again");
  tileOf(main, env.GRIMOIRE_CHARACTER).click();
  assert.ok(pressed(tileOf(main, env.GRIMOIRE_CHARACTER)));
  await until(() => openingsOf(main).length, 15000, "the openings");
  assert.ok(byText(summary, "div", "Opening").length);
  assert.equal(openingsOf(main).filter(pressed).length, 1, "the default opening is chosen");
  assert.ok(openingsOf(main).some((node) => node.textContent.includes("You came back. I didn't think you would.")));
  assert.ok(byText(summary, "div", "You meet Mara Vell").length);
  byText(main, "button", "Start chat").at(-1).click();
  await until(() => byText(body, "div", "Choose who you play first.").length, 15000, "the persona refusal");
  assert.equal(window.location.hash, "#/start", "no chat is started without a persona");
  chipOf(main, env.GRIMOIRE_PERSONA).click();
  assert.ok(pressed(chipOf(main, env.GRIMOIRE_PERSONA)) && main.querySelectorAll("button.g-chip").filter(pressed).length === 1);
  assert.ok(byText(summary, "div", "Anya Rook meets Mara Vell").length);
  byText(main, "button", "Start chat").at(-1).click();
  await until(() => window.location.hash.startsWith("#/chat/"), 15000, "the new chat");
  const chatHash = window.location.hash;
  // the greeting kept as the chat's first line (Start a chat also showed it, as an opening to pick)
  await until(() => main.querySelectorAll("div.chat-line").some((node) => node.textContent.includes("You came back. I didn't think you would.")), 15000, "the greeting");
  // Anya is the persona: in the cast, in the scene bar and over the composer
  assert.ok(byText(main.querySelector("ul.chat-cast"), "li", "Anya Rook").some((node) => node.textContent.includes("You")), "Anya is the persona");
  assert.ok(byText(main, "span", "Anya · you").some((node) => node.classList.contains("chat-chip")), "the scene bar marks who you play");
  assert.ok(main.querySelector("label").textContent.endsWith("Playing Anya Rook"), "the composer says who you play");
  assert.equal(body.querySelector("div.g-crumb").textContent, "Chats");
  assert.deepEqual(main.querySelector("div.chat-tabs").querySelectorAll("button").map((node) => node.textContent), ["Cast", "Story", "Details"]);
  // "Last time" opens the last lines under the scene bar. This device has no last time with this chat yet, so
  // they are the latest lines, and say so; Hide closes them and gives the button its focus back
  const lastTime = await until(() => { const node = exact(main, "button", "Last time"); return node && !node.hidden && node; }, 15000, "Last time");
  const recap = main.querySelector("div.chat-recap");
  assert.ok(recap.hidden && lastTime.getAttribute("aria-expanded") === "false", "the recap is closed until it is asked for");
  lastTime.click();
  assert.ok(!recap.hidden && lastTime.getAttribute("aria-expanded") === "true");
  assert.ok(byText(recap, "div", "The latest lines so far").length, "the recap says what it is");
  assert.deepEqual(recap.querySelectorAll("p.chat-recap-line").map((node) => node.textContent), ["Mara Vell: You came back. I didn't think you would."]);
  exact(recap, "button", "Hide").click();
  assert.ok(recap.hidden && document.activeElement === lastTime, "Hide closes the recap");
  exact(main, "button", "Story").click();
  await until(() => byText(main, "p", "No bookmarks yet.").length, 15000, "the Story tab, empty");
  assert.ok(exact(main, "button", "Bookmark this moment") && !main.textContent.includes("Last time you played"));
  exact(main, "button", "Cast").click();
  assert.ok(!byText(main, "div", "No model is set up").length, "a model is set up: no notice says otherwise");

  // focus mode hides the page's own scene bar and panel too, and Escape leaves it
  byText(body.querySelector("header"), "button", "Focus")[0].click();
  assert.ok(main.querySelector("div.chat-focused"), "the page is in focus mode");
  press("Escape");
  assert.ok(!main.querySelector("div.chat-focused"), "Escape leaves focus mode");

  // post as speech: the post is answered at once, as a take to keep. Pause is in the panel's Details.
  exact(main, "button", "Details").click();
  await until(() => byText(main, "button", "Pause").length, 15000, "Details");
  await until(() => byText(main, "a", "Mara Vell").some((node) => node.classList.contains("chat-bound") && node.textContent.includes("Character")), 15000, "what the chat plays from");
  assert.ok(byText(main, "dd", "Mara Vell").some((node) => node.textContent.includes("(character)")), "Details says what the chat started from");
  const composer = main.querySelector("textarea");
  type(composer, "\"Is the cargo safe?\"");
  const speech = byText(main, "button", "Speech")[0];
  speech.click();
  assert.equal(speech.getAttribute("aria-pressed"), "true");
  const send = byText(main, "button", "Post").at(-1);
  // the model's answer is held back, as a slow one would be, so the wait can be seen
  const release = hold("/api/generate");
  send.click();
  assert.ok(byText(main, "button", "Pause")[0].disabled, "the chat's buttons are off while the post is sent");
  await until(() => byText(main, "div", "The table is answering…").length, 15000, "the wait for an answer");
  assert.ok(send.disabled, "nothing is posted while a reply is being written");
  assert.equal(main.querySelector("textarea").placeholder, "The table is answering…");
  assert.ok(!takes(main).length);
  release();
  await until(() => takes(main).some((node) => node.textContent.includes("Safe as the tide.")), 30000, "the first take");
  await until(() => idle(body) && !byText(main, "button", "Pause")[0].disabled, 30000, "the answer");
  assert.ok(!byText(main, "div", "The table is answering…").length);
  assert.equal(main.querySelector("textarea").value, "");
  assert.ok(send.disabled, "nothing is posted while a take is pending");
  assert.equal(main.querySelector("textarea").placeholder, "Keep a take or pass the turn before you post again");
  assert.ok(main.querySelectorAll("div.chat-post").some((node) => node.textContent.includes("Anya Rook · you")), "the post is marked as yours");
  assert.ok(byText(takes(main)[0], "div", "Not part of the story until you keep it").length);
  assert.ok(!main.querySelector("button.chat-step"), "one take has nothing to step through");

  // pause: nothing new is played or kept, but its line can still be skipped, so nobody hears it
  byText(main, "button", "Pause")[0].click();
  const pausedBar = await until(() => main.querySelectorAll("div.chat-hold").find((node) => node.textContent.includes("Play is paused. Corrections still work.")), 15000, "the paused bar");
  await until(() => !byText(pausedBar, "button", "Resume")[0].disabled, 15000, "the pause");
  assert.equal(byText(main, "button", "Resume").length, 2, "Resume is on the bar and in Details");
  assert.equal(main.querySelector("textarea").placeholder, "Play is paused. Resume to post");
  assert.ok(byText(takes(main).at(-1), "button", "Keep this take")[0].disabled, "nothing is kept while play is paused");
  assert.ok(byText(takes(main).at(-1), "button", "Pass the turn")[0].disabled, "nothing is passed while play is paused");
  press("Enter");  // the keep key does nothing either
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.ok(takes(main).length && !kept(main).some((node) => node.textContent.includes("Safe as the tide.")), "Enter keeps nothing while play is paused");
  const skip = byText(takes(main).at(-1), "button", "Skip")[0];
  assert.ok(!skip.disabled, "a line can be skipped while play is paused");
  skip.click();
  await until(() => !main.textContent.includes("Safe as the tide."), 15000, "the skipped line");
  assert.ok(!byText(main, "button", "Keep this take").length, "a take with its only line skipped has nothing to keep");
  await until(() => byText(main, "div", "Nobody has answered this turn yet.").length, 15000, "the turn, still open");
  await until(() => !byText(main, "button", "Resume")[0].disabled, 15000, "the skip");
  byText(main, "button", "Resume")[0].click();
  await until(() => !byText(main, "button", "Resume").length && idle(body), 15000, "play resumed");
  assert.ok(!main.querySelector("div.chat-hold"), "nothing holds play up");
  // then ask again, and once more with the R key: two takes to step through
  byText(main, "button", "Ask for a reply")[0].click();
  await until(() => takes(main).some((node) => node.textContent.includes("Gone with the tide.")), 30000, "the second take");
  await until(() => idle(body), 15000, "the second answer");
  press("r");
  await until(() => takes(main).some((node) => node.textContent.includes("Out with the tide.")), 30000, "the third take");
  await until(() => idle(body), 15000, "the third answer");
  const label = () => main.querySelector("div.chat-stepper").textContent;
  assert.equal(takes(main).length, 1, "one card shows the take on show");
  assert.ok(label().includes("Take 2 of 2"), "the newest take is on show, and the skipped one is not counted");
  assert.ok(byText(takes(main)[0], "span", "← → step through takes").length, "the keys are named");
  press("ArrowLeft");
  assert.ok(label().includes("Take 1 of 2") && takes(main)[0].textContent.includes("Gone with the tide."), "the left arrow steps back");
  press("ArrowRight");
  assert.ok(label().includes("Take 2 of 2") && takes(main)[0].textContent.includes("Out with the tide."), "the right arrow steps on");
  main.querySelector("button[aria-label=\"Previous take\"]").click();
  assert.ok(label().includes("Take 1 of 2"), "the stepper's own buttons step too");
  // keys typed in the composer are text, and a key that is not the take's is left alone
  main.querySelector("textarea").focus();
  press("ArrowRight");
  press("p");
  assert.ok(label().includes("Take 1 of 2") && takes(main).length, "keys typed in the composer are text");
  body.querySelector("h1").focus();
  assert.ok(!press("x").defaultPrevented && label().includes("Take 1 of 2"), "a key that is not the take's does nothing");
  // Enter keeps the take on show, not the newest
  press("Enter");
  await until(() => kept(main).some((node) => node.textContent.includes("Gone with the tide.")), 15000, "the kept line");
  await until(() => idle(body), 15000, "the keep");
  assert.ok(!main.textContent.includes("Out with the tide.") && !takes(main).length, "the other take is gone with the turn");
  assert.ok(byText(main, "dd", "2 kept").length, "Details counts the turns kept");

  // the Story tab: bookmark this moment, name it, bookmark a line, jump to one, rename and remove
  exact(main, "button", "Story").click();
  const spine = () => main.querySelectorAll("li.chat-spine-item").map((node) => node.querySelector("span.chat-spine-label").textContent + " / " + node.querySelector("span.chat-spine-at").textContent);
  (await until(() => exact(main, "button", "Bookmark this moment"), 15000, "Bookmark this moment")).click();
  const naming = await until(() => main.querySelector("input.chat-spine-name"), 15000, "the new bookmark, ready to be named");
  await until(() => idle(body), 15000, "the bookmark");
  assert.deepEqual(spine(), ["Bookmark / Turn 2"], "the newest kept turn is marked, by its turn");
  assert.ok(byText(main, "p", "This moment is bookmarked.").length && !exact(main, "button", "Bookmark this moment"));
  type(main.querySelector("input.chat-spine-name"), "  The tide ");
  main.querySelector("input.chat-spine-name").dispatchEvent({ type: "keydown", key: "Enter", preventDefault() {} });
  await until(() => spine()[0] === "Bookmark: The tide / Turn 2", 15000, "the named bookmark");
  await until(() => idle(body), 15000, "the name");
  assert.ok(naming && !main.querySelector("input.chat-spine-name"), "the name field closes once saved");
  // a kept line has its own Bookmark, and a line of a marked turn says it is
  const greeting = kept(main).find((node) => node.textContent.includes("You came back."));
  assert.ok(kept(main).find((node) => node.textContent.includes("Gone with the tide.")).textContent.includes("Bookmarked"));
  byText(greeting, "button", "Bookmark")[0].click();
  await until(() => spine().length === 2, 15000, "the line's bookmark");
  await until(() => idle(body), 15000, "the second bookmark");
  assert.deepEqual(spine(), ["Bookmark / Turn 1", "Bookmark: The tide / Turn 2"], "the timeline is in the order of the story");
  // an entry jumps to its turn: its rows are marked, the first takes focus, and the entry is the current one
  const entries = () => main.querySelectorAll("li.chat-spine-item");
  entries()[0].querySelector("button.chat-spine-jump").click();
  const jumped = main.querySelectorAll("div.chat-jumped");
  assert.ok(jumped.length === 1 && jumped[0].textContent.includes("You came back.") && document.activeElement === jumped[0]);
  assert.equal(entries()[0].querySelector("button.chat-spine-jump").getAttribute("aria-current"), "true");
  assert.ok(entries()[0].classList.contains("chat-current") && !entries()[1].classList.contains("chat-current"));
  entries()[1].querySelector("button.chat-spine-jump").click();
  assert.ok(main.querySelectorAll("div.chat-jumped").every((node) => node.getAttribute("data-turn") !== jumped[0].getAttribute("data-turn")), "the mark moves with the jump");
  assert.ok(main.querySelectorAll("div.chat-jumped").some((node) => node.textContent.includes("Gone with the tide.")));
  // a rename the app refuses (the bookmark changed on another device, say) is said, and the story is read again
  const refuse = fail("/api/play.bookmark_rename", { error: "changed", message: "the bookmark changed since it was read" });
  byText(entries()[0], "button", "Rename")[0].click();
  type(await until(() => main.querySelector("input.chat-spine-name"), 15000, "the rename field"), "First words");
  byText(entries()[0], "button", "Save")[0].click();
  await until(() => body.querySelectorAll("div.g-bad").some((node) => node.textContent.includes("changed")), 15000, "the refused rename");
  await until(() => idle(body) && spine().length === 2, 15000, "the story, read again");
  assert.deepEqual(spine(), ["Bookmark / Turn 1", "Bookmark: The tide / Turn 2"], "nothing was renamed");
  refuse();
  byText(entries()[0], "button", "Rename")[0].click();
  type(await until(() => main.querySelector("input.chat-spine-name"), 15000, "the rename field again"), "First words");
  byText(entries()[0], "button", "Save")[0].click();
  await until(() => spine()[0] === "Bookmark: First words / Turn 1", 15000, "the renamed bookmark");
  await until(() => idle(body), 15000, "the rename");
  // remove asks once more before it acts
  byText(entries()[0], "button", "Remove")[0].click();
  assert.equal(spine().length, 2, "nothing is removed before the confirmation");
  byText(entries()[0], "button", "Remove it")[0].click();
  await until(() => spine().length === 1, 15000, "the removed bookmark");
  await until(() => idle(body), 15000, "the removal");
  assert.deepEqual(spine(), ["Bookmark: The tide / Turn 2"]);
  assert.ok(byText(kept(main).find((node) => node.textContent.includes("You came back.")), "button", "Bookmark").length, "its line can be bookmarked again");

  // strike asks once more before it acts
  const line = kept(main).find((node) => node.textContent.includes("Gone with the tide."));
  byText(line, "button", "Strike")[0].click();
  const sure = await until(() => byText(main, "button", "Strike it")[0], 15000, "the strike confirmation");
  assert.ok(kept(main).some((node) => node.textContent.includes("Gone with the tide.")), "nothing is struck before the confirmation");
  sure.click();
  await until(() => !kept(main).some((node) => node.textContent.includes("Gone with the tide.")), 15000, "the struck line");
  await until(() => idle(body), 15000, "the strike");

  // a reply that does not arrive: the post is kept, and the transcript says what happened and what to do
  const mend = fail("/api/generate", { error: "provider_failed", message: "the model did not answer" });
  type(main.querySelector("textarea"), "\"Then where is it?\"");
  byText(main, "button", "Post").at(-1).click();
  const failed = await until(() => main.querySelector("div.chat-failed"), 30000, "the failed reply");
  await until(() => idle(body), 15000, "the failure");
  assert.ok(failed.textContent.includes("The reply didn’t arrive") && failed.textContent.includes("provider_failed: the model did not answer"));
  assert.ok(main.querySelectorAll("div.chat-post").some((node) => node.textContent.includes("Then where is it?")), "the post is kept");
  assert.ok(!body.querySelectorAll("div.g-bad").some((node) => node.textContent.includes("provider_failed")), "the failure is said in the transcript, not in a notice too");
  assert.ok(byText(main.querySelector("div.chat-failed"), "button", "Pass the turn").length);
  const registry = await (await fetch("/shell/pages", { cache: "no-store" })).json();
  const check = byText(main.querySelector("div.chat-failed"), "a", "Check the model")[0];
  assert.equal(Boolean(check), registry.pages.some((page) => page.route === "providers"), "Check the model is there when the page is");
  if (check) assert.equal(check.getAttribute("href"), "#/providers");
  mend();
  byText(main.querySelector("div.chat-failed"), "button", "Try again")[0].click();
  await until(() => takes(main).some((node) => node.textContent.includes("Back with the tide.")), 30000, "the reply, asked again");
  await until(() => idle(body), 15000, "the fourth answer");
  assert.ok(!main.querySelector("div.chat-failed"), "the failure is gone once a reply arrived");
  // P passes the turn
  press("p");
  await until(() => byText(main, "div", "The turn was passed.").length && !takes(main).length, 15000, "the passed turn");
  await until(() => idle(body), 15000, "the pass");
  assert.ok(!byText(main, "button", "Post").at(-1).disabled, "the turn is closed: a post is taken again");

  // add Tamsin: a newcomer joins the cast
  exact(main, "button", "Cast").click();
  const add = await until(() => byText(main, "button", "Add a character")[0], 15000, "the Cast tab");
  add.click();
  const card = await until(() => choice(main.querySelector("div.chat-add"), "Card to add"), 15000, "the card picker");
  await until(() => idle(body), 15000, "the cards");
  pick(card, "character " + env.GRIMOIRE_NEWCOMER);
  main.querySelector("div.chat-add").querySelectorAll("button").find((node) => node.textContent === "Add").click();
  await until(() => byText(main.querySelector("ul.chat-cast"), "li", "Tamsin Hale").length, 15000, "the newcomer");
  await until(() => idle(body), 15000, "the add");
  const asks = main.querySelector("ul.chat-cast").querySelectorAll("button").filter((node) => node.textContent === "Ask to speak")
    .map((node) => node.getAttribute("aria-label"));
  assert.deepEqual(asks.sort(), ["Ask Mara Vell to speak", "Ask Tamsin Hale to speak"]);
  assert.ok(byText(main, "button", "Ask the director").length, "the director can be asked alone");
  assert.equal(byText(main.querySelector("ul.chat-cast"), "button", "Add picture").length, 3, "each of the cast can be given a picture");

  // another day: a new browser session opens the chat, and "Last time" quotes the last lines of the sitting
  // before, as this device remembers it. A struck line and a take nobody kept are not among them.
  const chatRef = decodeURIComponent(chatHash.slice("#/chat/".length));
  window.sessionStorage.removeItem("grimoire.chat.left:" + chatRef);
  window.location.hash = "#/chats";
  await until(() => main.querySelectorAll("a.chats-row").length, 15000, "the chat list");
  window.location.hash = chatHash;
  const later = await until(() => { const node = exact(main, "button", "Last time"); return node && !node.hidden && node; }, 15000, "Last time, another day");
  later.click();
  const said = main.querySelector("div.chat-recap");
  assert.ok(byText(said, "div", "The last lines from last time").length, "the recap is of the last sitting");
  assert.deepEqual(said.querySelectorAll("p.chat-recap-line").map((node) => node.textContent), [
    "Mara Vell: You came back. I didn't think you would.", "Anya Rook · you: Is the cargo safe?", "Anya Rook · you: Then where is it?"]);
  exact(main, "button", "Story").click();
  await until(() => main.querySelectorAll("li.chat-spine-item").length === 2, 15000, "the timeline, another day");
  assert.deepEqual(main.querySelectorAll("li.chat-spine-item").map((node) => node.querySelector("span.chat-spine-label").textContent + " / " + node.querySelector("span.chat-spine-at").textContent),
    ["Bookmark: The tide / Turn 2", "Last time you played / Turn 3"]);
  assert.ok(!main.querySelectorAll("li.chat-spine-item")[1].querySelectorAll("button.chat-tool").length, "the last-time entry is not a bookmark");

  // the same chat from All, and among the chats to continue on Start a chat for Mara
  window.location.hash = "#/chats?filter=all";
  await until(() => main.querySelectorAll("a.chats-row").some((node) => node.getAttribute("href") === chatHash), 15000, "the chat under All");
  assert.equal(byText(main, "button", "All")[0].getAttribute("aria-pressed"), "true", "the old address of every chat still opens it");
  assert.equal(byText(body.querySelector("nav"), "a", "Chats")[0].querySelector("span.g-nav-count").textContent, "1");
  assert.ok(main.querySelector("a.chats-row").querySelector("time"), "the row says when it was played");
  window.location.hash = "#/start?source=" + encodeURIComponent(env.GRIMOIRE_CHARACTER);
  const again = await until(() => byText(main, "div", "Or continue one already started from this pick").at(-1), 15000, "the chats to continue");
  assert.ok(main.querySelector("aside.start-summary").contains(again));
  assert.ok(main.querySelectorAll("a.start-related-row").some((node) => node.getAttribute("href") === chatHash));
  // the linked character is the one picked, the persona is still chosen, and the ones played last are listed first
  assert.ok(pressed(tileOf(main, env.GRIMOIRE_CHARACTER)) && pressed(chipOf(main, env.GRIMOIRE_PERSONA)));
  assert.equal(main.querySelectorAll("button.start-pick")[0].getAttribute("data-ref"), env.GRIMOIRE_CHARACTER, "the character played last is first");
  assert.equal(main.querySelectorAll("button.g-chip")[0].getAttribute("data-ref"), env.GRIMOIRE_PERSONA, "the persona played last is first");
  // a source the library cannot bind is still named by its exact ref, and its refusal is shown
  window.location.hash = "#/start?source=" + encodeURIComponent("card:nobody/character/nobody");
  const gone = await until(() => tileOf(main, "card:nobody/character/nobody"), 15000, "the unknown source");
  assert.ok(pressed(gone) && gone.textContent.includes("card:nobody/character/nobody"));
  await until(() => main.querySelector("aside.start-summary").querySelectorAll("div.g-bad").length, 15000, "the refusal of the unknown source");
  assert.ok(!openingsOf(main).length && !main.querySelectorAll("a.start-related-row").length);
  kindCard(main, "premise").click();
  const premise = await until(() => main.querySelector("textarea"), 15000, "the premise field again");
  assert.equal(premise.value, "A lighthouse keeper's last night.", "the unsent premise is still a draft");
});

test("a chat whose added card is gone shows what is missing and detaches it", { skip: !env.GRIMOIRE_BROKEN }, async () => {
  const { window, body, main } = await boot();
  await until(() => body.querySelector("nav"), 15000, "the menu");
  window.location.hash = "#/chat/" + encodeURIComponent(env.GRIMOIRE_BROKEN);
  // a bar over the story says so; the mending itself is in the panel's Details, which the bar's button opens
  const bar = await until(() => main.querySelectorAll("div.chat-hold").find((node) => node.textContent.includes("A source this chat binds is gone.")), 15000, "the broken binding");
  assert.ok(!byText(main, "button", "Detach").length && exact(main, "button", "Cast").getAttribute("aria-pressed") === "true");
  byText(bar, "button", "See what is missing")[0].click();
  // the list survives the redraw that follows the request
  const detach = await until(() => byText(main, "button", "Detach").length === 1 && idle(body) && byText(main, "button", "Detach")[0], 15000, "the repairs");
  assert.equal(exact(main, "button", "Details").getAttribute("aria-pressed"), "true", "the panel shows Details");
  assert.ok(main.querySelector("aside.chat-open"), "and is open where it lies over the story");
  detach.click();
  const yes = await until(() => body.querySelector("dialog") && byText(body.querySelector("dialog"), "button", "Detach")[0], 15000, "the confirmation");
  yes.click();
  await until(() => !byText(main, "button", "See what is missing").length && !byText(main, "button", "Detach").length && idle(body), 15000, "the detached card");
  assert.ok(!main.querySelectorAll("div.chat-hold").some((node) => node.textContent.includes("A source this chat binds is gone.")));
  assert.ok(!main.textContent.includes("Missing sources"));
  assert.deepEqual(body.querySelectorAll("div.g-bad").map((node) => node.textContent), [], "the detach shows no error");
});

test("with no model set up the chat says so, and a post still goes in", { skip: !env.GRIMOIRE_NOMODEL }, async () => {
  const { window, body, main } = await boot();
  await until(() => body.querySelector("nav"), 15000, "the menu");
  window.location.hash = "#/chat/" + encodeURIComponent(env.GRIMOIRE_NOMODEL);
  const notice = await until(() => main.querySelector("div.chat-nomodel"), 15000, "the no-model notice");
  assert.ok(notice.textContent.includes("No model is set up, so nobody will answer. You can still post."));
  const registry = await (await fetch("/shell/pages", { cache: "no-store" })).json();
  const setUp = byText(notice, "a", "Set one up")[0];
  assert.equal(Boolean(setUp), registry.pages.some((page) => page.route === "providers"), "Set one up is there when the page is");
  if (setUp) assert.equal(setUp.getAttribute("href"), "#/providers");
  await until(() => main.querySelectorAll("div.chat-line").length, 15000, "the chat");
  // nothing offers an answer that could not come
  assert.ok(byText(main, "button", "Ask for a reply")[0].hidden, "no reply is offered");
  assert.ok(!byText(main, "button", "Ask to speak").length && !byText(main, "button", "Ask the director").length);
  type(main.querySelector("textarea"), "\"Anyone here?\"");
  byText(main, "button", "Post").at(-1).click();
  await until(() => main.querySelectorAll("div.chat-post").some((node) => node.textContent.includes("Anyone here?")), 15000, "the post");
  // the turn is open with nobody to answer it: it is passed, and then a post is taken again
  const waits = await until(() => byText(main, "div", "Nobody has answered this turn yet.").at(-1), 15000, "the open turn");
  await until(() => idle(body), 15000, "the post, sent");
  assert.ok(!main.querySelector("div.chat-failed") && !byText(main, "div", "The table is answering…").length);
  assert.equal(main.querySelector("textarea").placeholder, "Pass the turn before you post again");
  assert.ok(byText(main, "button", "Post").at(-1).disabled);
  byText(waits, "button", "Pass the turn")[0].click();
  await until(() => byText(main, "div", "The turn was passed.").length && !byText(main, "button", "Post").at(-1).disabled, 15000, "the passed turn");
  assert.deepEqual(body.querySelectorAll("div.g-bad").map((node) => node.textContent), []);
});

test("a start that stops before it finishes is finished from the chat", { skip: !env.GRIMOIRE_UNFINISHED }, async () => {
  const { window, body, main } = await boot();
  await until(() => body.querySelector("nav"), 15000, "the menu");
  window.location.hash = "#/start?source=" + encodeURIComponent(env.GRIMOIRE_CHARACTER);
  await until(() => openingsOf(main).length, 15000, "the openings");
  chipOf(main, env.GRIMOIRE_PERSONA).click();
  byText(main, "button", "Start chat").at(-1).click();
  await until(() => window.location.hash.startsWith("#/chat/"), 15000, "the new chat");
  const finish = await until(() => byText(main, "button", "Finish starting")[0], 15000, "Finish starting");
  assert.ok(!main.querySelectorAll("div.chat-line").length, "nothing opened the chat yet");
  finish.click();
  await until(() => main.querySelectorAll("div.chat-line").some((node) => node.textContent.includes("You came back. I didn't think you would."))
    && !byText(main, "button", "Finish starting").length && idle(body), 15000, "the opening, finished");
});
