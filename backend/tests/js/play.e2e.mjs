// Sprint 111's pages end to end in the shell (108), against a real app server, in fakedom: Start Chat from a
// character played as a persona, post with a typed mode, wait for the answer, pause, skip a line, ask again, step
// through the takes and keep one by its keys, strike, see a reply fail and ask again, pass, add a card, then
// find the chat under All and under Continue on Start Chat. A second test mends a chat whose added card is gone.
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

async function boot() {
  const window = install(env.GRIMOIRE_APP);
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
  const character = await until(() => choice(main, "Character"), 15000, "the Start Chat form");
  assert.equal(window.location.hash, "#/start");
  // a premise written and left unsent stays a draft when a character is started instead
  pick(choice(main, "Start from"), "premise");
  type(await until(() => main.querySelector("textarea"), 15000, "the premise field"), "A lighthouse keeper's last night.");
  pick(choice(main, "Start from"), "character");
  pick(await until(() => choice(main, "Character"), 15000, "the character picker"), env.GRIMOIRE_CHARACTER);
  await until(() => byText(main, "legend", "Opening").length, 15000, "the openings");
  assert.equal(main.querySelectorAll("input").filter((node) => node.type === "radio" && node.checked).length, 1);
  byText(main, "button", "Start Chat").at(-1).click();
  await until(() => byText(body, "div", "Choose who you play first.").length, 15000, "the persona refusal");
  assert.equal(window.location.hash, "#/start", "no chat is started without a persona");
  pick(choice(main, "You play"), env.GRIMOIRE_PERSONA);
  byText(main, "button", "Start Chat").at(-1).click();
  await until(() => window.location.hash.startsWith("#/chat/"), 15000, "the new chat");
  const chatHash = window.location.hash;
  // the greeting kept as the chat's first line (the Start Chat page also showed it, as an opening to pick)
  await until(() => main.querySelectorAll("div.chat-line").some((node) => node.textContent.includes("You came back. I didn't think you would.")), 15000, "the greeting");
  // Anya is the persona: in the cast, in the scene bar and over the composer
  assert.ok(byText(main.querySelector("ul.chat-cast"), "li", "Anya Rook").some((node) => node.textContent.includes("You")), "Anya is the persona");
  assert.ok(byText(main, "span", "Anya · you").some((node) => node.classList.contains("chat-chip")), "the scene bar marks who you play");
  assert.ok(main.querySelector("label").textContent.endsWith("Playing Anya Rook"), "the composer says who you play");
  assert.equal(body.querySelector("div.g-crumb").textContent, "Chats");
  assert.ok(!byText(main, "button", "Story").length, "a tab with nothing to show is not there");
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
  assert.ok(!press("c").defaultPrevented && label().includes("Take 1 of 2"), "a key that is not the take's does nothing");
  // Enter keeps the take on show, not the newest
  press("Enter");
  await until(() => kept(main).some((node) => node.textContent.includes("Gone with the tide.")), 15000, "the kept line");
  await until(() => idle(body), 15000, "the keep");
  assert.ok(!main.textContent.includes("Out with the tide.") && !takes(main).length, "the other take is gone with the turn");
  assert.ok(byText(main, "dd", "2 kept").length, "Details counts the turns kept");

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

  // the same chat from All, and under Continue on Start Chat for Mara
  window.location.hash = "#/chats?filter=all";
  await until(() => main.querySelectorAll("a.chats-row").some((node) => node.getAttribute("href") === chatHash), 15000, "the chat under All");
  assert.equal(byText(main, "button", "All")[0].getAttribute("aria-pressed"), "true", "the old address of every chat still opens it");
  assert.equal(byText(body.querySelector("nav"), "a", "Chats")[0].querySelector("span.g-nav-count").textContent, "1");
  assert.ok(main.querySelector("a.chats-row").querySelector("time"), "the row says when it was played");
  window.location.hash = "#/start?source=" + encodeURIComponent(env.GRIMOIRE_CHARACTER);
  await until(() => byText(main, "h2", "Continue").length, 15000, "Continue on Start Chat");
  assert.ok(main.querySelectorAll("a").some((node) => node.getAttribute("href") === chatHash));
  pick(choice(main, "Start from"), "premise");
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
  await until(() => byText(main, "legend", "Opening").length, 15000, "the openings");
  pick(choice(main, "You play"), env.GRIMOIRE_PERSONA);
  byText(main, "button", "Start Chat").at(-1).click();
  await until(() => window.location.hash.startsWith("#/chat/"), 15000, "the new chat");
  const finish = await until(() => byText(main, "button", "Finish starting")[0], 15000, "Finish starting");
  assert.ok(!main.querySelectorAll("div.chat-line").length, "nothing opened the chat yet");
  finish.click();
  await until(() => main.querySelectorAll("div.chat-line").some((node) => node.textContent.includes("You came back. I didn't think you would."))
    && !byText(main, "button", "Finish starting").length && idle(body), 15000, "the opening, finished");
});
