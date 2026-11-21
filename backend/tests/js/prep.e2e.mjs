// Sprint 115's Prep page end to end, in fakedom against a real app server: from a campaign's hub to its prep; the
// table checks an action for its own character against a ruleset contract (a proposal only), sees 062's refusal,
// and finds catalogs and scene preparation to be the director's; an unsent value survives leaving the page; the
// director sees each catalog and each binding's pinned revision and cost, with what is not available named; and
// prepares one scene: estimated, approved, then shown with the evidence each item rests on, and accepted.
// Run by test_app_prep_page.py, which serves a synthetic library (prep_app_fixtures.stocked, one published catalog,
// model settings over a scripted transport with one reply) and passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT,
// GRIMOIRE_CAMPAIGN, GRIMOIRE_CATALOG and GRIMOIRE_CHARACTER (the card's pinned character ref).
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { audit, describe } from "./a11y.mjs";
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
const catalog = process.env.GRIMOIRE_CATALOG;
const character = process.env.GRIMOIRE_CHARACTER;

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const labelled = (root, tag, title) => root.querySelectorAll(tag).find((node) => node.getAttribute("aria-label") === title);
const group = (root, title) => labelled(root, "div", title);
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));
const button = (root, label) => root.querySelectorAll("button").find((node) => node.textContent === label);
// the structural accessibility audit (a11y.mjs) of the whole screen as it stands
const accessible = (where) => assert.deepEqual(audit(document.body).map((violation) => where + ": " + describe(violation)), []);

function control(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

function type(root, label, value) {
  const node = control(root, label);
  node.value = value;
  node.dispatchEvent({ type: "input" });
}

test("a campaign's prep: ruleset contracts, catalogs and one prepared scene", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the campaign hub links to its prep, for the same chat
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat);
  await until(() => section(main, "Cast and stage"), 15000, "the hub");
  const link = byText(main, "a", "Prep").find((node) => (node.getAttribute("href") || "").startsWith("#/prep/"));
  assert.ok(link, "the hub links to the prep page");
  assert.ok(link.getAttribute("href").includes(encodeURIComponent(chat)), "for the same chat");
  link.click();
  const rules = await until(() => section(main, "Ruleset contracts") && group(main, "Check an action") && section(main, "Ruleset contracts"), 15000, "the prep page");
  const prepHash = window.location.hash;

  // the table's view: catalogs and scene preparation are the director's, with no control
  assert.equal(view(main, "Table view").getAttribute("aria-pressed"), "true");
  for (const title of ["Catalogs", "Scene preparation"]) {
    assert.ok(section(main, title).textContent.includes("director's"), title + " is the director's");
    assert.equal(section(main, title).querySelectorAll("button,input,textarea,select").length, 0, "the table has no " + title + " control");
  }
  assert.ok(rules.textContent.includes("A ruleset pinned for this campaign is not available yet."));
  assert.ok(rules.textContent.includes("no dice are rolled"));
  // the table proposes only for its own character
  const who = control(rules, "For");
  assert.deepEqual(who.querySelectorAll("option").map((node) => node.textContent), ["Anya Rook (yours)"]);

  // 062's example fits, and is a proposal only
  button(rules, "Fill in the example").click();
  button(rules, "Check this action").click();
  const result = await until(() => group(rules, "Check result"), 15000, "the check");
  await idle();
  assert.ok(result.textContent.includes("It fits the 5e-2024 contract.") && result.textContent.includes("Proposal only"), result.textContent);
  assert.ok(result.textContent.includes("dexterity") && result.textContent.includes("nothing is resolved or recorded"));
  // a die no d20 shows is 062's refusal, in its own words; the value stays for another try
  type(rules, "The d20 as rolled", "25");
  button(rules, "Check this action").click();
  const refusal = await until(() => group(rules, "Check result") && group(rules, "Check result").textContent.includes("refuses") && group(rules, "Check result"), 15000, "the refusal");
  await idle();
  assert.ok(refusal.textContent.includes("invalid_proposal"), refusal.textContent);
  assert.equal(control(rules, "The d20 as rolled").value, "25");
  accessible("the table's prep");

  // an unsent value is still there after the person leaves the page and comes back
  type(rules, "DC", "17");
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign);
  await until(() => section(main, "Cast and stage"), 15000, "the hub again");
  window.location.hash = prepHash;
  const back = await until(() => section(main, "Ruleset contracts") && group(main, "Check an action"), 15000, "the form again");
  assert.equal(control(back, "DC").value, "17");
  assert.equal(control(back, "The d20 as rolled").value, "25");

  // the director's view: the catalogs and what the chat binds from them
  view(main, "Director view").click();
  const catalogs = await until(() => section(main, "Catalogs") && labelled(section(main, "Catalogs"), "article", "Catalog " + catalog) && section(main, "Catalogs"), 15000, "the catalogs");
  const harbor = labelled(catalogs, "article", "Catalog " + catalog);
  assert.ok(harbor.textContent.includes("The harbor") && harbor.textContent.includes("2 records available"), harbor.textContent);
  assert.ok(harbor.textContent.includes("Excludes: card:anya-rook/character/anya-rook"));
  const binding = group(catalogs, "Binding " + character);
  assert.ok(binding, "the card's character binding is shown");
  assert.ok(binding.textContent.includes("Pinned at r1") && binding.textContent.includes("Available in " + catalog), binding.textContent);
  assert.ok(/Mara Vell's context takes about \d+ tokens from it/.test(binding.textContent), binding.textContent);
  assert.ok(byText(catalogs, "a", "Mara Vell").some((node) => node.getAttribute("href").startsWith("#/source/")), "a record opens its Library page");
  assert.ok(catalogs.textContent.includes("Excluded from discovery by " + catalog + "; this chat's binding still holds."));
  for (const missing of ["Detaching a binding here", "Package distribution", "Following a catalog's head"]) {
    assert.ok(byText(catalogs, "strong", missing + " is not available yet.").length, missing);
  }
  assert.equal(catalogs.querySelectorAll("button").length, 0, "no binding is changed here");

  // scene preparation: the evidence, an estimate, the approval, then the scene with its grounds
  const prep = await until(() => section(main, "Scene preparation") && group(section(main, "Scene preparation"), "The evidence") && section(main, "Scene preparation"), 15000, "the evidence");
  const directorHash = window.location.hash;
  assert.ok(directorHash.includes("audience=director"));
  assert.ok(prep.textContent.includes("Nothing here is saved"));
  assert.ok(prep.textContent.includes("secret-1") && prep.textContent.includes("The harbor master sold the lighthouse oil to smugglers."));
  assert.ok(prep.textContent.includes("Places: The Quay"));
  assert.ok(!prep.textContent.includes("Mara owes the harbor guild a debt."), "what Anya was told is no secret");
  type(prep, "What the scene should be about", "A tense night on the quay");
  // the brief survives leaving the page, too
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign);
  await until(() => section(main, "Cast and stage"), 15000, "the hub again");
  window.location.hash = directorHash;
  const again = await until(() => section(main, "Scene preparation") && group(section(main, "Scene preparation"), "Prepare a scene") && section(main, "Scene preparation"), 15000, "the brief again");
  assert.equal(control(again, "What the scene should be about").value, "A tense night on the quay");

  button(again, "Estimate the call").click();
  const approval = await until(() => group(section(main, "Scene preparation"), "Approve this call"), 15000, "the estimate");
  await idle();
  assert.ok(approval.textContent.includes("One planning call to prep@"), approval.textContent);
  assert.ok(approval.textContent.includes("costs at most") && approval.textContent.includes("not a bill"));
  assert.ok(approval.textContent.includes("It leaves this computer for the configured provider."));
  button(approval, "Approve and prepare").click();
  const scene = await until(() => section(main, "Scene preparation").querySelectorAll("article")
    .find((node) => (node.getAttribute("aria-label") || "").startsWith("Prepared scene prep-")), 15000, "the prepared scene");
  await idle();
  assert.ok(scene.textContent.includes("Mara waits on the quay as the smugglers' boat readies"), scene.textContent);
  assert.ok(scene.textContent.includes("secret-1 · secret") && scene.textContent.includes("goal-1 · goal"));
  assert.ok(scene.textContent.includes("Where: The Quay") && scene.textContent.includes("Who: Mara Vell"));
  assert.ok(scene.textContent.includes("1 call sent"));
  assert.equal(control(section(main, "Scene preparation"), "What the scene should be about").value, "", "the brief that was sent is cleared");

  // accepted for the session only
  for (const mark of ["Its stakes are credible", "It leaves the players' choices open", "Every item rests on the evidence it names"]) {
    const label = scene.querySelectorAll("label").find((node) => node.textContent === mark);
    label.querySelector("input").checked = true;
  }
  button(scene, "Accept").click();
  await idle();
  await until(() => section(main, "Scene preparation").textContent.includes("Accepted for this session only"), 15000, "accepted");
  accessible("the director's prep");
});
