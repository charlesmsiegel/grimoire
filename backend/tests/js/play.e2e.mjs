// Sprint 111's pages end to end in the shell (108), against a real app server, in fakedom: Start Chat from a
// character played as a persona, post with a typed mode, skip a line, ask again, keep, strike, add a card, then
// find the chat under All and under Continue on Start Chat.
// Run by test_app_play_js.py, which serves a synthetic card library with a scripted model and passes
// GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHARACTER (Mara), GRIMOIRE_PERSONA (Anya) and GRIMOIRE_NEWCOMER (Tamsin).
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

const idle = (body) => !body.querySelectorAll("button").some((node) => node.disabled && node.textContent !== "Post");
const takes = (main) => main.querySelectorAll("article.chat-take");
const kept = (main) => main.querySelectorAll("div.chat-line").filter((node) => !takes(main).some((take) => take.contains(node)));

test("Start Chat, play a turn, correct it and find the chat again", { skip: !env.GRIMOIRE_APP }, async () => {
  const window = install(env.GRIMOIRE_APP);
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  const nav = await until(() => byText(body.querySelector("nav"), "a", "Start Chat").length && body.querySelector("nav"), 15000, "the menu");

  // Chats has the way to Start Chat, and an All tab beside Recent
  byText(nav, "a", "Chats")[0].click();
  await until(() => byText(main, "a", "All").length && byText(main, "p", "No chats yet").length, 15000, "an empty chat list");
  const startLink = await until(() => byText(body.querySelector("header"), "a", "Start Chat")[0], 15000, "Start Chat in the header");
  startLink.click();

  // pick Mara, played as Anya, with her default opening
  const character = await until(() => choice(main, "Character"), 15000, "the Start Chat form");
  assert.equal(window.location.hash, "#/start");
  pick(character, env.GRIMOIRE_CHARACTER);
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
  assert.ok(byText(main, "li", "Anya Rook").some((node) => node.textContent.includes("you")), "Anya is the persona");

  // post as speech: the post is answered at once, as a take to keep
  const composer = main.querySelector("textarea");
  type(composer, "\"Is the cargo safe?\"");
  pick(choice(main, "How to read it") || main.querySelector("select.chat-mode"), "speech");
  byText(main, "button", "Post").at(-1).click();
  assert.ok(byText(main, "button", "Pause")[0].disabled, "the chat's buttons are off while the post is sent");
  await until(() => takes(main).some((node) => node.textContent.includes("Safe as the tide.")), 30000, "the first take");
  await until(() => idle(body) && !byText(main, "button", "Pause")[0].disabled, 30000, "the answer");
  assert.equal(main.querySelector("textarea").value, "");
  assert.ok(byText(main, "div", "Keep a take or pass this turn before you post again.").length);

  // skip its line: nobody hears it, and there is nothing to keep; then ask again and keep
  byText(takes(main).at(-1), "button", "Skip")[0].click();
  await until(() => !takes(main).some((node) => node.textContent.includes("Safe as the tide.")), 15000, "the skipped line");
  assert.ok(!byText(main, "button", "Keep").length, "a take with its only line skipped has nothing to keep");
  await until(() => idle(body), 15000, "the skip");
  byText(main, "button", "Answer")[0].click();
  await until(() => takes(main).some((node) => node.textContent.includes("Gone with the tide.")), 30000, "the second take");
  await until(() => idle(body), 15000, "the second answer");
  byText(takes(main).at(-1), "button", "Keep")[0].click();
  await until(() => kept(main).some((node) => node.textContent.includes("Gone with the tide.")), 15000, "the kept line");

  // strike asks once more before it acts
  const line = kept(main).find((node) => node.textContent.includes("Gone with the tide."));
  byText(line, "button", "Strike")[0].click();
  const sure = await until(() => byText(main, "button", "Strike it")[0], 15000, "the strike confirmation");
  assert.ok(kept(main).some((node) => node.textContent.includes("Gone with the tide.")), "nothing is struck before the confirmation");
  sure.click();
  await until(() => !kept(main).some((node) => node.textContent.includes("Gone with the tide.")), 15000, "the struck line");

  // add Tamsin: a newcomer joins the cast
  const add = main.querySelector("details.chat-add");
  add.open = true;
  add.dispatchEvent({ type: "toggle" });
  // the cards are read once, and the picker is drawn open again when they arrive
  const card = await until(() => choice(main.querySelector("details.chat-add"), "Card to add"), 15000, "the card picker");
  await until(() => idle(body), 15000, "the cards");
  pick(card, "character " + env.GRIMOIRE_NEWCOMER);
  byText(main.querySelector("details.chat-add"), "button", "Add")[0].click();
  await until(() => byText(main.querySelector("ul.chat-cast"), "li", "Tamsin Hale").length, 15000, "the newcomer");
  assert.ok(byText(main, "button", "Ask Tamsin Hale").length && byText(main, "button", "Ask Mara Vell").length);

  // the same chat from All, and under Continue on Start Chat for Mara
  window.location.hash = "#/chats?filter=all";
  await until(() => main.querySelectorAll("a").some((node) => node.getAttribute("href") === chatHash), 15000, "the chat under All");
  window.location.hash = "#/start?source=" + encodeURIComponent(env.GRIMOIRE_CHARACTER);
  await until(() => byText(main, "h2", "Continue").length, 15000, "Continue on Start Chat");
  assert.ok(main.querySelectorAll("a").some((node) => node.getAttribute("href") === chatHash));
});
