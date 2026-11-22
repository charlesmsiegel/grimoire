// Sprint 116's attitude check (078) end to end, in fakedom against a real app server: the chat's Details link the
// director to it; the table's view shows nothing; a player character is never offered as a holder; the director
// checks Mara's resentment of Anya from the report Mara received (admitted) and the same attitude for Tamsin, who
// never heard it (refused, with 078's reason); grounds are only the holder's own; nothing is saved. Run by
// test_app_attitudes.py, which serves the card chat with two NPCs and the quay's evidence, passes GRIMOIRE_APP,
// GRIMOIRE_TOKEN and GRIMOIRE_CHAT, and checks afterwards that the library is unchanged.
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

const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const group = (root, label) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === label);
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));
const exact = (root, tag, text) => root.querySelectorAll(tag).find((node) => node.textContent === text);

function control(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent.startsWith(text));
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

const options = (select) => select.querySelectorAll("option").map((node) => ({ value: node.getAttribute("value"), text: node.textContent }));

function choose(root, label, text) {
  const select = control(root, label);
  const found = options(select).find((item) => item.text === text);
  assert.ok(found, label + " offers " + text);
  select.value = found.value;
  select.dispatchEvent({ type: "change" });
}

function type(root, label, value) {
  const node = control(root, label);
  node.value = value;
  node.dispatchEvent({ type: "input" });
}

function tick(root, startsWith) {
  const label = root.querySelectorAll("label").find((node) => node.textContent.startsWith(startsWith));
  assert.ok(label, "a ground " + startsWith);
  const box = label.querySelector("input");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
}

test("the director checks an NPC's attitude against its own evidence; nothing is saved", { skip: !env.GRIMOIRE_APP }, async () => {
  const window = install(env.GRIMOIRE_APP);
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  window.location.hash = "#/chat/" + encodeURIComponent(chat);
  const details = await until(() => exact(main, "button", "Details"), 15000, "the chat");
  details.click();
  const link = await until(() => byText(main, "a", "Attitudes")[0], 15000, "the director's link");
  assert.equal(link.getAttribute("href"), "#/attitudes/" + encodeURIComponent(chat));

  // the table's view is refused: it says whose this is and shows nothing
  window.location.hash = link.getAttribute("href");
  const tableView = await until(() => section(main, "Attitudes"), 15000, "the table's view");
  assert.ok(tableView.textContent.includes("is the director's"));
  assert.equal(tableView.querySelectorAll("button,select").length, 0);

  view(main, "Director view").click();
  const whose = await until(() => section(main, "Whose attitude"), 15000, "the director's view");
  assert.ok(whose.textContent.includes("Nothing is saved"));
  const holders = options(control(whose, "Whose attitude")).map((item) => item.text);
  assert.deepEqual(holders, ["Choose an NPC…", "Mara Vell", "Tamsin Hale"], "a player character is never a holder");
  assert.ok(whose.textContent.includes("Anya Rook · a player character's feelings are its player's: not offered"));
  assert.ok(byText(main, "strong", "Saving an attitude is not available yet").length);
  assert.ok(byText(main, "strong", "An NPC playing from its attitudes is not available yet").length);

  // Mara resents Anya, from the report Mara received
  choose(whose, "Whose attitude", "Mara Vell");
  const form = await until(() => { const node = section(main, "The attitude"); return node && group(node, "Mara Vell's attitude") ? node : null; }, 15000, "Mara's form");
  const fields = group(form, "Mara Vell's attitude");
  assert.ok(fields.textContent.includes("Your guild are thieves and you are their lackey."));
  assert.ok(!fields.textContent.includes("Cold rain"), "only Mara's own grounds are offered");
  const towards = options(control(fields, "Toward")).map((item) => item.text);
  assert.deepEqual(towards, ["Choose whom…", "Anya Rook", "Tamsin Hale", "The harbor guild (a group)"]);
  choose(fields, "Toward", "Anya Rook");
  choose(fields, "What Mara Vell feels", "Resentment");
  choose(fields, "Degree (optional)", "3");
  type(fields, "Mara Vell's own reading of it", "Resents being called a lackey in front of the dockhands.");
  tick(fields, "“Your guild are thieves");
  byText(form, "button", "Check it")[0].click();
  const verdict = await until(() => section(main, "Verdict"), 15000, "the verdict");
  assert.ok(verdict.textContent.includes("Admitted") && verdict.textContent.includes("078 admits it"), verdict.textContent);
  assert.ok(verdict.textContent.includes("Nothing was written"));
  assert.equal(document.activeElement, verdict.querySelector(".attitudes-verdict"), "focus moves to the verdict");
  const heldCard = section(main, "Held on this page");
  assert.ok(heldCard.textContent.includes("Mara Vell: Resentment toward Anya Rook (3): Resents being called a lackey"));

  // Tamsin never heard it: only Tamsin's own rain is offered, and it is about no one
  choose(whose, "Whose attitude", "Tamsin Hale");
  const other = await until(() => { const node = section(main, "The attitude"); return node && group(node, "Tamsin Hale's attitude") ? node : null; }, 15000, "Tamsin's form");
  const theirs = group(other, "Tamsin Hale's attitude");
  assert.ok(!theirs.textContent.includes("lackey"), "never another actor's evidence");
  choose(theirs, "Toward", "Anya Rook");
  choose(theirs, "What Tamsin Hale feels", "Resentment");
  type(theirs, "Tamsin Hale's own reading of it", "Resents the newcomer.");
  tick(theirs, "“Cold rain on the neck.”");
  byText(other, "button", "Check it")[0].click();
  const refusal = await until(() => { const node = section(main, "Verdict"); return node && node.textContent.includes("Refused") ? node : null; }, 15000, "the refusal");
  assert.ok(refusal.textContent.includes("(unrelated_grounds)"), refusal.textContent);
  assert.ok(section(main, "Held on this page").textContent.includes("Mara Vell: Resentment toward Anya Rook"), "the admitted one is still held");
});
