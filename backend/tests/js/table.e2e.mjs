// Sprint 115's shared turn (058) end to end, in fakedom against a real app server: two people at one screen each
// draft their own character's part of one turn; only the person at the keyboard's own unsent parts are shown, each
// labelled with its character and its player; the drafts survive switching to another chat and back (the sprint's
// proving scenario); each part is held, the turn is reviewed and posted once, the table answers once, and play
// resumes with the next shared turn. Run by test_app_table.py, which serves a synthetic card chat where Anya Rook is
// the owner's and Tamsin Hale is Bob's, with a scripted model, and passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT,
// GRIMOIRE_STORM (another chat) and GRIMOIRE_BOB.
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
const ANYA = "Anya Rook's part, by you";
const TAMSIN = "Tamsin Hale's part, by bob";

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled && node.textContent !== "Post"), 30000, "the action to finish");
const shared = (main) => main.querySelectorAll("details").find((node) => node.getAttribute("aria-label") === "Shared turn" && !node.hidden);
const group = (root, label) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === label);
const segment = (root, label) => byText(root, "button", label).find((node) => node.classList.contains("g-segment") && node.textContent === label);

// Hold back the app's answers to one op until the returned function is called.
function holdBack(prefix) {
  const fetchNow = globalThis.fetch;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  globalThis.fetch = async (url, options) => { if (String(url).startsWith(prefix)) await held; return fetchNow(url, options); };
  return () => { globalThis.fetch = fetchNow; release(); };
}

// the input of the field labelled `text` under `root`, or undefined
function field(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  return label && root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

function type(node, value) {
  node.value = value;
  node.dispatchEvent({ type: "input" });
}

async function atKeyboard(main, label, expect) {
  const turn = await until(() => shared(main), 15000, "the shared turn");
  segment(turn, label).click();
  await until(() => field(shared(main), expect), 15000, label + " at the keyboard");
  return shared(main);
}

async function hold(main, part) {
  const box = group(shared(main), part);
  byText(box, "button", "Hold for the turn").at(-1).click();
  await idle();
}

async function postTurn(main, lines) {
  byText(shared(main), "button", "Review the turn").at(-1).click();
  const review = await until(() => group(shared(main), "Review this turn"), 15000, "the review");
  for (const line of lines) assert.ok(review.textContent.includes(line), "the review says " + line + ": " + review.textContent);
  assert.ok(review.textContent.includes("takes no label, framing or effect"));
  byText(review, "button", "Post this turn").at(-1).click();
  await until(() => !group(shared(main), "Review this turn"), 15000, "the turn to be posted");
  await idle();
}

test("two players draft one turn, switch chats without losing a draft, and resume play", { skip: !env.GRIMOIRE_APP }, async () => {
  const window = install(env.GRIMOIRE_APP);
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  window.location.hash = "#/chat/" + encodeURIComponent(env.GRIMOIRE_CHAT);
  let turn = await until(() => shared(main), 15000, "the shared turn in the chat");
  const chatTitle = document.title;
  assert.ok(turn.textContent.includes("kept by this app in memory until it is posted"), "where held parts are kept is said");
  assert.equal(segment(turn, "You").getAttribute("aria-pressed"), "true", "the owner is at the keyboard first");

  // each person sees only their own unsent part, labelled with its character and its player
  assert.ok(field(turn, ANYA) && !field(turn, TAMSIN));
  type(field(turn, ANYA), '"Is the lamp lit?"');
  turn = await atKeyboard(main, "bob", TAMSIN);
  assert.ok(!field(turn, ANYA), "Bob does not see the owner's unsent words");
  type(field(turn, TAMSIN), "*I check the lamp*");

  // switch to another chat and back: neither unsent part is lost, and Bob is still at the keyboard
  window.location.hash = "#/chat/" + encodeURIComponent(env.GRIMOIRE_STORM);
  await until(() => document.title !== chatTitle && shared(main) && !field(shared(main), TAMSIN), 15000, "the other chat");
  window.location.hash = "#/chat/" + encodeURIComponent(env.GRIMOIRE_CHAT);
  turn = await until(() => shared(main) && field(shared(main), TAMSIN) && shared(main), 15000, "the chat again");
  assert.equal(segment(turn, "bob").getAttribute("aria-pressed"), "true");
  assert.equal(field(turn, TAMSIN).value, "*I check the lamp*");
  turn = await atKeyboard(main, "You", ANYA);
  assert.equal(field(turn, ANYA).value, '"Is the lamp lit?"');

  // hold each part for the turn: the held list names each part's character and player, in posting order
  await hold(main, "Anya Rook's part");
  assert.equal(field(shared(main), ANYA).value, "", "a held part leaves the unsent box");
  turn = await atKeyboard(main, "bob", TAMSIN);
  await hold(main, "Tamsin Hale's part");
  turn = await until(() => shared(main).textContent.includes("2. Tamsin Hale") && shared(main), 15000, "both parts held");
  assert.ok(turn.textContent.includes("1. Anya Rook") && turn.textContent.includes("by you"));
  assert.ok(turn.textContent.includes("Held by you. Their words show only while they are at the keyboard."));
  assert.ok(!turn.textContent.includes("Is the lamp lit?"), "the owner's held words are not shown to Bob");
  assert.ok(turn.textContent.includes("*I check the lamp*"), "Bob sees his own");

  // review, post once; the table answers once
  await postTurn(main, ["1. Anya Rook, by you", "2. Tamsin Hale, by bob"]);
  await until(() => byText(main, "div", "The lamp gutters.").length, 30000, "the table's answer");
  assert.ok(byText(main, "div", "Is the lamp lit?").length && byText(main, "div", "I check the lamp").length, "both parts are in the story");
  assert.equal(shared(main).querySelector("summary").textContent, "Shared turn", "no part is held any more");
  assert.ok(shared(main).textContent.includes("No part is held for this turn yet."));

  // play resumes: a part held while the answer is still a take waits for it to be kept, then the next turn is posted
  turn = await atKeyboard(main, "You", ANYA);
  type(field(turn, ANYA), "*I trim the wick*");
  // words typed while the part is on its way to be held are the next draft, never cleared by the answer
  const release = holdBack("/api/draft");
  byText(group(shared(main), "Anya Rook's part"), "button", "Hold for the turn").at(-1).click();
  type(field(shared(main), ANYA), "*I trim the wick* and listen");
  release();
  await idle();
  await until(() => shared(main).textContent.includes("1. Anya Rook"), 15000, "the part held");
  assert.equal(field(shared(main), ANYA).value, "*I trim the wick* and listen", "what was typed meanwhile is kept");
  assert.ok(shared(main).textContent.includes("*I trim the wick*"));
  assert.ok(shared(main).textContent.includes("Keep a take or pass the turn before the next turn is posted"));
  assert.ok(!byText(shared(main), "button", "Review the turn").length, "no review is offered while the last turn is open");
  byText(main, "button", "Keep this take").at(-1).click();
  await until(() => byText(shared(main), "button", "Review the turn").length, 15000, "the take kept");
  await postTurn(main, ["1. Anya Rook, by you"]);
  await until(() => byText(main, "div", "Rain on the shutters.").length, 30000, "the table's next answer");

  // redraws keep the person's place: Enter on a keyboard segment leaves focus on it, and Bob typing while the chat
  // redraws under him (the owner keeps the take) stays in his box
  const toBob = segment(shared(main), "bob");
  toBob.focus();
  toBob.click();
  await until(() => field(shared(main), TAMSIN), 15000, "Bob at the keyboard");
  // (compared by identity: assert.equal would print two whole fakedom trees on failure)
  assert.ok(document.activeElement === toBob, "the segment pressed keeps the focus");
  assert.ok(shared(main).contains(toBob), "and is still the panel's own");
  const bobBox = field(shared(main), TAMSIN);
  bobBox.focus();
  type(bobBox, "*I listen at the shutters*");
  byText(main, "button", "Keep this take").at(-1).click();
  await until(() => !byText(main, "button", "Keep this take").length, 15000, "the take kept");
  await idle();
  assert.ok(document.activeElement === bobBox, "Bob is still typing in his part after the redraw");
  assert.ok(shared(main).contains(bobBox));

  // Bob holds his part, then Tamsin goes back to the director: the part is no longer his, Bob is no longer at
  // this keyboard, and whoever is takes it out of the turn for him, so the turn is not wedged
  await hold(main, "Tamsin Hale's part");
  await until(() => shared(main).textContent.includes("1. Tamsin Hale"), 15000, "Bob's part held");
  shared(main).querySelectorAll("details").find((node) => node.classList.contains("chat-table-seating")).open = true;
  const seat = field(shared(main), "Who plays Tamsin Hale");
  seat.value = "director";
  seat.dispatchEvent({ type: "change" });
  byText(shared(main), "button", "Hand over").at(-1).click();
  const dialog = await until(() => document.body.querySelector("dialog"), 15000, "the confirmation");
  byText(dialog, "button", "Hand over").at(-1).click();
  turn = await until(() => shared(main) && shared(main).textContent.includes("No longer theirs") && shared(main), 15000, "the part flagged");
  assert.ok(!segment(turn, "bob"), "Bob plays no character here any more");
  const out = turn.querySelectorAll("button").find((node) => node.getAttribute("aria-label") === "Take Tamsin Hale's part out of the turn");
  assert.ok(out, "the flagged part can be taken out");
  out.click();
  await until(() => shared(main).textContent.includes("No part is held for this turn yet."), 15000, "the part taken out");
  await idle();
  assert.ok(!shared(main).textContent.includes("No longer theirs"));
});
