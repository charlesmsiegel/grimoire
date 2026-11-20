// 073's proving scenario through the real chat page in fakedom against a real app server over a synthetic
// library and a scripted model: the player asks how Mara Vell might take a confession, sees one possible reply
// marked as outside the story, copies it, discards it, and posts their own line instead. The draft is never
// touched and the transcript never shows the hypothetical. Run by test_imagine_page.py, which seats this device
// at GRIMOIRE_CHAT, passes it, GRIMOIRE_APP and GRIMOIRE_TOKEN, and checks afterwards what the library gained.
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
const base = env.GRIMOIRE_APP, CHAT = env.GRIMOIRE_CHAT;
const MIGHT = "Then you owe me a ship.", REAL = "Fair winds tonight.";

async function call(op, args) {
  const response = await fetch(base + "/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": env.GRIMOIRE_TOKEN } });
  const answer = await response.json();
  assert.ok(response.ok, op + ": " + JSON.stringify(answer));
  return answer;
}
function type(input, text) { input.value = text; input.dispatchEvent({ type: "input" }); }

test("a possible reply is shown, copied and discarded, and the real scene goes on unchanged", { skip: !base }, async () => {
  const window = install(base);
  const copied = [];
  Object.defineProperty(globalThis, "navigator", { configurable: true,
    value: { clipboard: { writeText: async (text) => { copied.push(text); } } } });
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  window.location.hash = "#/chat/" + encodeURIComponent(CHAT);
  const composer = () => main.querySelector("div.chat-composer");
  const panel = await until(() => composer() && main.querySelector("details.chat-imagine"), 15000, "the panel beside the composer");
  const draft = () => composer().querySelector("textarea");
  type(draft(), "A draft I have not finished");
  const before = await call("open", { chat: CHAT });

  const go = await until(() => { const node = byText(panel, "button", "Imagine a reply")[0];
    return node && panel.querySelector("select") && !node.getAttribute("aria-disabled") && node; },
    15000, "Imagine a reply");
  assert.equal(panel.querySelector("select").querySelectorAll("option").map((node) => node.textContent).join(), "Mara Vell");
  type(panel.querySelector('[name="imagine-line"]'), '"I was the one who sank the Gull."');
  go.click();
  const reply = await until(() => panel.querySelector("blockquote"), 30000, "the possible reply");
  assert.equal(reply.textContent, MIGHT);
  assert.match(panel.textContent, /Hypothetical · not in the story/);
  assert.match(panel.textContent, /Mara Vell might say:/);
  assert.equal(draft().value, "A draft I have not finished", "the draft is the player's own");
  assert.ok(!main.querySelector("section.chat-log").textContent.includes(MIGHT), "the transcript never shows it");
  const after = await call("open", { chat: CHAT });
  assert.equal(JSON.stringify(after.turns), JSON.stringify(before.turns), "no turn was written for it");

  // the composer's mode is how the line is read: an unquoted line, posted as speech, is a line said aloud
  const speech = await until(() => byText(composer(), "button", "Speech")[0], 15000, "the composer's modes");
  speech.click();
  type(panel.querySelector('[name="imagine-line"]'), "I sank the Gull, Mara.");
  go.click();
  await until(() => panel.querySelectorAll("li").length === 2, 30000, "the second possible reply");
  assert.match(panel.querySelectorAll("li")[0].textContent, /If you said: I sank the Gull, Mara\./);
  byText(panel.querySelectorAll("li")[0], "button", "Discard").find((node) => node.textContent === "Discard").click();
  byText(composer(), "button", "Auto")[0].click();

  byText(panel, "button", "Copy reply")[0].click();
  await until(() => copied.length, 15000, "the copy");
  assert.deepEqual(copied, [MIGHT]);
  byText(panel, "button", "Discard").find((node) => node.textContent === "Discard").click();
  assert.equal(panel.querySelectorAll("blockquote").length, 0);

  // the player's own line, posted and answered as any other: the confession was never part of it
  type(draft(), '"Any news from the harbor?"');
  const post = byText(composer(), "button", "Post")[0];
  await until(() => !post.disabled, 15000, "Post");
  post.click();
  await until(() => main.querySelector("section.chat-log").textContent.includes(REAL), 30000, "the real reply");
  assert.ok(!main.querySelector("section.chat-log").textContent.includes("sank the Gull"));
});
