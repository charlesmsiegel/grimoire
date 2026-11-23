// Sprint 116's Rules page end to end, in fakedom against a real app server: a module is started from the d20
// template, a mistake in it is saved (a draft keeps any text), shown by its path with a hint and refused for
// publishing, then fixed and published as reviewed; the published revision is tried, a sheet filled and a check
// graded from entered dice, both shown as not saved. Then, from a campaign's hub, the player rolls a Stealth check
// for their own character backed by the action they typed: reviewed first, it fails, and asking again reads the same
// failure back with no new die. The director rolls for the NPC in the director's view; the table can neither roll
// for an NPC nor see that roll.
// Run by test_app_rules.py, which serves a synthetic library whose card chat has a kept turn by the seated player,
// with a scripted draw (4, then 18), and passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT, GRIMOIRE_CAMPAIGN and
// GRIMOIRE_NPC (Mara Vell's ref).
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
const npc = process.env.GRIMOIRE_NPC;
const SLIP = "I slip past the guard while he yawns.";

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const group = (main, title) => main.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === title);
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));
const pressed = (main, label) => { const found = view(main, label); return Boolean(found) && found.getAttribute("aria-pressed") === "true"; };
const press = (root, label) => {
  const found = byText(root, "button", label).filter((node) => node.textContent === label);
  assert.ok(found.length, "a button " + label);
  found.at(-1).click();
};

// the input of the field labelled `text` in `root`
function input(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

function type(root, label, value) {
  const node = input(root, label);
  node.value = value;
  node.dispatchEvent({ type: "input" });
}

function choose(root, label, value) {
  const node = input(root, label);
  node.value = value;
  node.dispatchEvent({ type: "change" });
}

test("a module authored and published as reviewed, tried, and a campaign's rolls recorded once", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the rail lists Rules under Library
  const rail = byText(body, "a", "Rules").find((node) => node.getAttribute("href") === "#/rules");
  assert.ok(rail, "the rail links the Rules page");
  rail.click();
  const open = await until(() => section(main, "Open a module"), 15000, "the Rules page");
  assert.ok(open.textContent.includes("lists no drafts or module ids"), "what cannot be listed is said");

  // start a draft from the d20 template, reviewed first
  const start = group(main, "A new draft");
  type(start, "New module id", "duel");
  assert.equal(input(start, "Start from").value, "d20_basic");
  press(start, "Review");
  const startReview = await until(() => group(main, "Review this draft"), 15000, "the start review");
  assert.ok(startReview.textContent.includes("config/mechanics/drafts/duel.json"), startReview.textContent);
  press(startReview, "Start this draft");
  const draft = await until(() => {
    const found = section(main, "Draft duel");
    return found && found.querySelectorAll("label").some((node) => node.textContent === "Module JSON") && found;
  }, 15000, "the draft");
  assert.equal(window.location.hash, "#/rules?module=duel");
  await idle();
  const original = input(draft, "Module JSON").value;
  assert.ok(original.includes("natural + stealth + dex_mod"));

  // a mistake is checked by its path with a hint, saved as typed, and refused for publishing
  type(draft, "Module JSON", original.replace("natural + stealth + dex_mod", "natural + stelth + dex_mod"));
  press(draft, "Check");
  const problems = await until(() => group(main, "Problems"), 15000, "the problems");
  assert.ok(problems.textContent.includes("checks.stealth.total"), problems.textContent);
  assert.ok(problems.textContent.includes("Did you mean stealth?"), "the hint is shown");
  press(draft, "Publish");
  await until(() => draft.textContent.includes("Save the draft first"), 15000, "unsaved text never publishes");
  press(draft, "Save");
  const saveReview = await until(() => group(main, "Review this save"), 15000, "the save review");
  assert.ok(saveReview.textContent.includes("As saved it has 1 problem"), saveReview.textContent);
  press(saveReview, "Save the draft");
  await until(() => !group(main, "Review this save"), 15000, "the save");
  await idle();
  press(draft, "Publish");
  await until(() => draft.textContent.includes("A draft with any problem does not publish"), 15000, "the refusal");
  await idle();

  // fixed, saved and published as reviewed
  type(draft, "Module JSON", original);
  press(draft, "Check");
  await until(() => group(main, "Problems") && group(main, "Problems").textContent.includes("no problem"), 15000, "no problem");
  press(draft, "Save");
  press(await until(() => group(main, "Review this save"), 15000, "the save review"), "Save the draft");
  await until(() => !group(main, "Review this save"), 15000, "the save");
  await idle();
  press(draft, "Publish");
  const publishReview = await until(() => group(main, "Review this publication"), 15000, "the publish review");
  assert.match(publishReview.textContent, /as revision sha256:[0-9a-f]{64}/);
  assert.match(publishReview.textContent, /config\/mechanics\/modules\/duel\/[0-9a-f]{64}\.json/);
  press(publishReview, "Publish");
  await until(() => !group(main, "Review this publication"), 15000, "the publication");
  await idle();
  const revisions = section(main, "Published revisions");
  const link = await until(() => revisions.querySelectorAll("a").find((node) => node.textContent.startsWith("Revision ")), 15000, "the revision");

  // try the published revision: a sheet and a graded check, neither saved
  link.click();
  const tryIt = await until(() => section(main, "Try it"), 15000, "the revision");
  assert.ok(main.textContent.includes("read-only"));
  assert.ok(tryIt.textContent.includes("Not saved") && tryIt.textContent.includes("086 has no sheet store"));
  choose(tryIt, "Sheet type", "warrior");
  const fill = await until(() => group(tryIt, "Fill a Warrior sheet"), 15000, "the Warrior sheet");
  type(fill, "Dexterity", "14");
  type(fill, "Stealth", "3");
  press(fill, "Fill the sheet");
  const sheet = await until(() => group(tryIt, "The sheet"), 15000, "the sheet");
  assert.ok(sheet.textContent.includes("The sheet fits") && sheet.textContent.includes("dex_mod 2"), sheet.textContent);
  assert.ok(sheet.textContent.includes("Not saved"));
  assert.equal(document.activeElement, sheet, "the filled sheet takes the focus");
  const grade = group(tryIt, "Grade a check");
  choose(grade, "Check", "stealth");
  type(grade, "Dice as rolled", "9");
  press(grade, "Grade");
  const graded = await until(() => group(tryIt, "The graded check"), 15000, "the graded check");
  assert.ok(graded.textContent.includes("Stealth: success") && graded.textContent.includes("total 14"), graded.textContent);
  assert.ok(graded.textContent.includes("Not saved") && graded.textContent.includes("Confirmed under revision"));
  assert.equal(document.activeElement, graded, "the graded check takes the focus");
  await idle();

  // from the campaign hub to its rolls, for the same chat
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat);
  await until(() => section(main, "Cast and stage"), 15000, "the hub");
  const rulesLink = byText(main, "a", "Rules").find((node) => (node.getAttribute("href") || "").startsWith("#/rules/"));
  assert.ok(rulesLink && rulesLink.getAttribute("href").includes("chat="), "the hub links its rolls for the chat");
  rulesLink.click();
  const ready = () => {
    const found = section(main, "Roll a check");
    return found && found.querySelectorAll("label").some((node) => node.textContent === "Roll for") && found;
  };
  let roller = await until(ready, 15000, "the rolls");
  assert.ok(pressed(main, "Table view"));
  assert.ok(roller.textContent.includes("A ruleset pinned for this campaign") && roller.textContent.includes("reference profile"));
  assert.ok(roller.textContent.includes("milestone 3"), "narration review is named as not wired");
  const who = input(roller, "Roll for");
  assert.deepEqual(who.querySelectorAll("option").map((node) => node.textContent), ["Anya Rook (yours)"], "the table rolls only for its own");

  // the player's Stealth check, backed by what they typed: reviewed, rolled once, read back
  press(roller, "Fill in the example");
  type(roller, "What your character does", SLIP);
  press(roller, "Review");
  const rollReview = await until(() => group(main, "Review this roll"), 15000, "the roll review");
  assert.match(rollReview.textContent, /Roll 1d20 for Anya Rook under attempt roll-/);
  assert.ok(rollReview.textContent.includes("Backed by your action") && rollReview.textContent.includes(SLIP));
  press(rollReview, "Roll");
  const rolled = await until(() => group(main, "The roll"), 15000, "the roll");
  assert.ok(rolled.textContent.includes("Rolled") && rolled.textContent.includes("4 → 7, failure"), rolled.textContent);
  await idle();
  const recorded = await until(() => section(main, "Recorded rolls").querySelectorAll("article").length === 1 && section(main, "Recorded rolls"), 15000, "the record");
  assert.ok(recorded.textContent.includes("failure") && recorded.textContent.includes(SLIP));
  press(group(main, "The roll"), "Ask again");
  await until(() => group(main, "The roll") && group(main, "The roll").textContent.includes("Read back from the record"), 15000, "the read-back");
  const back = group(main, "The roll");
  assert.ok(back.textContent.includes("No die was drawn") && back.textContent.includes("4 → 7, failure"), back.textContent);
  await idle();
  assert.equal(section(main, "Recorded rolls").querySelectorAll("article").length, 1, "asking again records nothing");

  // the director rolls for the NPC: nothing backs it, and it is the director's attempt
  view(main, "Director view").click();
  roller = await until(() => pressed(main, "Director view") && ready()
    && input(ready(), "Roll for").querySelectorAll("option").length === 2 && ready(), 15000, "the director's rolls");
  choose(roller, "Roll for", npc);
  roller = section(main, "Roll a check");
  press(roller, "Fill in the example");
  press(roller, "Review");
  const npcReview = await until(() => group(main, "Review this roll"), 15000, "the NPC's review: " + document.body.textContent.slice(-1500));
  assert.match(npcReview.textContent, /for Mara Vell under attempt director:roll-/);
  assert.ok(npcReview.textContent.includes("Nothing backs it"));
  press(npcReview, "Roll");
  const npcRoll = await until(() => group(main, "The roll"), 15000, "the NPC's roll");
  assert.ok(npcRoll.textContent.includes("18 → 21, success"), npcRoll.textContent);
  await idle();
  await until(() => section(main, "Recorded rolls").querySelectorAll("article").length === 2, 15000, "both rolls listed");

  // back at the table: the director's roll is not shown, and there is still no NPC to roll for
  view(main, "Table view").click();
  const table = await until(() => {
    const found = section(main, "Recorded rolls");
    return found && pressed(main, "Table view") && found.querySelectorAll("article").length === 1 && found;
  }, 15000, "the table's rolls: " + (section(main, "Recorded rolls") || main).textContent);
  assert.equal(table.querySelectorAll("article").length, 1);
  assert.ok(!table.textContent.includes("Mara Vell") && !table.textContent.includes("director:"), "the director's roll is the director's");
  assert.deepEqual(input(section(main, "Roll a check"), "Roll for").querySelectorAll("option").map((node) => node.textContent), ["Anya Rook (yours)"]);
});
