// Sprint 115's preview of several NPCs answering at once (051) end to end, in fakedom against a real app server: a
// chat's Details link the director to it; the table's view shows nothing of the plan; the director authors a bid for
// each NPC on stage and previews the floor 051 gives them, as a proposal that writes nothing; the bids being written
// survive leaving the page; an answer to a preview asked before the plan changed is never drawn; running or accepting
// the exchange is named as not available, never offered. Run by test_app_ensemble.py, which serves a synthetic card
// chat with two NPCs (Mara Vell and Tamsin Hale) and Anya Rook as the owner's character, and passes GRIMOIRE_APP,
// GRIMOIRE_TOKEN and GRIMOIRE_CHAT.
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
const chat = env.GRIMOIRE_CHAT;

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const group = (root, label) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === label);
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));
const exact = (root, tag, text) => root.querySelectorAll(tag).find((node) => node.textContent === text);

// the control of the field labelled `text` under `root`
function control(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

function pick(root, label, value) {
  const select = control(root, label);
  select.value = value;
  select.dispatchEvent({ type: "change" });
}

function type(root, label, value) {
  const node = control(root, label);
  node.value = value;
  node.dispatchEvent({ type: "input" });
}

// tick the box for `name` in the group of checks whose legend is `legend`
function tick(root, legend, name) {
  const fieldset = root.querySelectorAll("fieldset").find((node) => node.querySelector("legend").textContent === legend);
  assert.ok(fieldset, "checks for " + legend);
  const box = fieldset.querySelectorAll("label").find((node) => node.textContent === name).querySelector("input");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
}

// Hold back the app's answers to one op until the returned function is called.
function holdBack(prefix) {
  const fetchNow = globalThis.fetch;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  globalThis.fetch = async (url, options) => { if (String(url).startsWith(prefix)) await held; return fetchNow(url, options); };
  return () => { globalThis.fetch = fetchNow; release(); };
}

test("the director previews how several NPCs answer, as a proposal that writes nothing", { skip: !env.GRIMOIRE_APP }, async () => {
  const window = install(env.GRIMOIRE_APP);
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the chat's Details links the director to the exchange
  window.location.hash = "#/chat/" + encodeURIComponent(chat);
  const details = await until(() => exact(main, "button", "Details"), 15000, "the chat");
  details.click();
  const link = await until(() => byText(main, "a", "Several NPCs answering")[0], 15000, "the director's link");
  assert.ok(link.getAttribute("href").startsWith("#/ensemble/") && link.getAttribute("href").includes("audience=director"));

  // the table's view shows nothing of how NPCs coordinate
  window.location.hash = "#/ensemble/" + encodeURIComponent(chat);
  const tableView = await until(() => section(main, "The exchange"), 15000, "the table's view");
  assert.ok(tableView.textContent.includes("director's plan"));
  assert.equal(tableView.querySelectorAll("button").length, 0);
  assert.ok(!section(main, "Plan the exchange"));

  view(main, "Director view").click();
  const plan = await until(() => section(main, "Plan the exchange"), 15000, "the director's plan");
  const here = section(main, "Who is here").textContent;
  assert.ok(here.includes("Mara Vell") && here.includes("Tamsin Hale") && here.includes("played by a person: never voiced"));
  assert.ok(!group(plan, "Anya Rook's bid"), "a player's character never bids");

  // the moment: Anya asks Mara, who holds the floor
  const moment = group(plan, "The moment");
  assert.equal(control(moment, "Who spoke").value, control(moment, "Who spoke").querySelectorAll("option").find((node) => node.textContent === "Anya Rook").getAttribute("value"));
  tick(moment, "Asked of", "Mara Vell");
  pick(moment, "Who holds the floor", control(moment, "Who holds the floor").querySelectorAll("option").find((node) => node.textContent === "Mara Vell").getAttribute("value"));

  // Mara answers Anya; Tamsin adds a side remark only Mara hears
  pick(group(plan, "Mara Vell's bid"), "Mara Vell's bid", "answer");
  const mara = group(plan, "Mara Vell's bid");
  tick(mara, "To", "Anya Rook");
  type(mara, "Why (private to the director and Mara Vell)", "She owes Anya an answer.");
  pick(group(plan, "Tamsin Hale's bid"), "Tamsin Hale's bid", "aside");
  const tamsin = group(plan, "Tamsin Hale's bid");
  tick(tamsin, "To", "Mara Vell");
  tick(tamsin, "Who would hear it (none ticked: everyone here)", "Tamsin Hale");
  tick(tamsin, "Who would hear it (none ticked: everyone here)", "Mara Vell");
  type(tamsin, "Why (private to the director and Tamsin Hale)", "She suspects the lamp.");
  type(tamsin, "Which line Tamsin Hale heard", "1");

  // the plan being written survives leaving the page
  window.location.hash = "#/chat/" + encodeURIComponent(chat);
  await until(() => exact(main, "button", "Details"), 15000, "the chat again");
  window.location.hash = "#/ensemble/" + encodeURIComponent(chat) + "?audience=director";
  const again = await until(() => section(main, "Plan the exchange"), 15000, "the plan again");
  assert.equal(control(group(again, "Tamsin Hale's bid"), "Why (private to the director and Tamsin Hale)").value, "She suspects the lamp.");
  assert.equal(control(group(again, "Mara Vell's bid"), "Mara Vell's bid").value, "answer");

  // an answer to a preview asked before the plan changed is never drawn
  const release = holdBack("/api/ensemble.preview");
  byText(again, "button", "Preview the exchange").at(-1).click();
  type(group(again, "Tamsin Hale's bid"), "Why (private to the director and Tamsin Hale)", "She suspects the lamp was moved.");
  release();
  await idle();
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.ok(!section(main, "Proposed exchange"), "the preview of the plan as it was is not shown");

  // the preview: the floor, who would be aware of what, and what is not available
  byText(again, "button", "Preview the exchange").at(-1).click();
  const proposed = await until(() => section(main, "Proposed exchange"), 15000, "the preview");
  const text = proposed.textContent;
  assert.ok(text.includes("Proposal") && text.includes("Not accepted: nothing is said in the chat and nothing is written."));
  assert.ok(text.includes("1. Mara Vell answers, to Anya Rook.") && text.includes("2. Tamsin Hale adds a side remark, to Mara Vell."));
  assert.ok(text.includes("Why, privately: She suspects the lamp was moved."));
  assert.ok(text.includes("ask the model 2 times"));
  assert.ok(text.includes("Anya Rook is aware of: Mara Vell answers."), "the side remark does not reach Anya: " + text);
  assert.ok(byText(proposed, "strong", "Running this exchange is not available yet").length);
  assert.ok(byText(proposed, "strong", "Accepting it into the story is not available yet").length);
  assert.ok(!byText(proposed, "button", "Run").length && !byText(proposed, "button", "Accept").length, "neither is offered as a control");

  // a bid 051 refuses says why, and the page keeps the plan
  pick(group(again, "Mara Vell's bid"), "Mara Vell's bid", "interject");
  assert.ok(!section(main, "Proposed exchange"), "a changed plan takes the old preview away");
  pick(group(again, "Mara Vell's bid"), "What Mara Vell's bid rests on", "intention");
  byText(again, "button", "Preview the exchange").at(-1).click();
  await until(() => body.querySelectorAll("div").some((node) => node.getAttribute("role") === "alert" && node.textContent.includes("bad_floor")), 15000, "051's refusal");
});
