// Sprint 116's Authoring page end to end, in fakedom against a real app server: from a world record's Library page
// to its source's authoring tools; a proposal built by hand is reviewed and discarded (nothing written); the model
// suggests three linked towns, a namesake of the world's guild and a brainstorm, estimated first and sent only once
// approved; the review shows the namesake; two of the three towns are accepted, the change set lists exactly their
// two entity files, one map revision and the manifest, and is written on Approve and write, with the new records
// linked; the read-only audit shows a cited finding and checks its evidence; a dependency is pinned as reviewed; and
// the shared card's page shows what removing it would leave missing in a chat.
// Run by test_app_authoring.py, which serves a synthetic library (authoring_app_fixtures.seed_vale over the desktop
// fixture, model settings over a scripted transport with one reply) and passes GRIMOIRE_APP, GRIMOIRE_TOKEN,
// GRIMOIRE_WORLD, GRIMOIRE_TOWN (Port Lantern's ref), GRIMOIRE_COAST, GRIMOIRE_SHARED (the card's source id) and
// GRIMOIRE_CHAT.
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
const world = process.env.GRIMOIRE_WORLD;
const town = process.env.GRIMOIRE_TOWN;
const coast = process.env.GRIMOIRE_COAST;
const shared = process.env.GRIMOIRE_SHARED;

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const labelled = (root, tag, title) => root.querySelectorAll(tag).find((node) => node.getAttribute("aria-label") === title);
const group = (root, title) => labelled(root, "div", title);
const button = (root, label) => root.querySelectorAll("button").find((node) => node.textContent === label);
const accessible = (where) => assert.deepEqual(audit(document.body).map((violation) => where + ": " + describe(violation)), []);
const proposals = (root) => root.querySelectorAll("article").filter((node) => (node.getAttribute("aria-label") || "").startsWith("Proposal "));

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

function choose(root, label, value) {
  const node = control(root, label);
  node.value = value;
  node.dispatchEvent({ type: "change" });
}

function tick(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a box labelled " + text);
  const box = label.querySelector("input");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
}

test("a world's authoring: a reviewed proposal written in part, an audit and upgrade previews", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // a world record's Library page links to its source's authoring tools
  window.location.hash = "#/source/" + encodeURIComponent(town);
  const link = await until(() => byText(main, "a", "Authoring").find((node) => (node.getAttribute("href") || "").startsWith("#/authoring/")), 15000, "the record's page");
  assert.equal(link.getAttribute("href"), "#/authoring/" + encodeURIComponent(world));
  link.click();
  const suggestions = await until(() => section(main, "World suggestions") && section(main, "Consistency audit") && section(main, "Source upgrades")
    && group(section(main, "Source upgrades"), "Optional dependencies") && section(main, "World suggestions"), 15000, "the authoring page");
  const home = window.location.hash;
  assert.ok(main.textContent.includes("The Vale"), "the page names the source");
  assert.ok(suggestions.textContent.includes("Nothing here is stored until you accept it"));
  for (const missing of ["Keeping a proposal across a restart", "Larger linked regions", "Campaign places and groups"]) {
    assert.ok(byText(suggestions, "strong", missing + " is not available yet.").length, missing);
  }
  accessible("the authoring page");

  // by hand: one town within the coast, reviewed, then discarded; the unsent builder survives leaving the page
  const builder = group(suggestions, "Build a proposal by hand");
  button(builder, "Add a place or group").click();
  type(group(suggestions, "Build a proposal by hand"), "Name", "Saltwatch");
  window.location.hash = "#/source/" + encodeURIComponent(town);
  await until(() => byText(main, "a", "Authoring").length, 15000, "the record's page again");
  window.location.hash = home;
  const again = await until(() => section(main, "World suggestions") && group(section(main, "World suggestions"), "Build a proposal by hand"), 15000, "the builder again");
  assert.equal(control(again, "Name").value, "Saltwatch", "an unsent candidate is kept");
  button(again, "Add a link").click();
  const linked = group(section(main, "World suggestions"), "Build a proposal by hand");
  choose(linked, "Relation", "within");
  choose(linked, "To", coast);
  button(linked, "Review this proposal").click();
  const hand = await until(() => proposals(section(main, "World suggestions"))[0], 15000, "the hand-built proposal");
  await idle();
  assert.ok(hand.textContent.includes("Built by you") && hand.textContent.includes("Saltwatch") && hand.textContent.includes("Not stored"), hand.textContent);
  assert.ok(byText(hand, "a", "Saltmarch Coast").length, "the link names the coast as it is in the world");
  assert.equal(document.activeElement, hand, "focus goes to the reviewed proposal");
  button(hand, "Discard").click();
  await idle();
  await until(() => proposals(section(main, "World suggestions"))[0].textContent.includes("Discarded: nothing of it was written."), 15000, "discarded");

  // the model: estimated, approved, then the proposal with the guild's namesake
  const ask = group(section(main, "World suggestions"), "Ask the model");
  type(ask, "What to suggest", "Three linked towns on the coast, and a guild");
  button(ask, "Estimate the call").click();
  const approval = await until(() => group(section(main, "World suggestions"), "Approve this call"), 15000, "the estimate");
  await idle();
  assert.ok(approval.textContent.includes("One planning call to vale@vale.example/vale-model"), approval.textContent);
  assert.ok(approval.textContent.includes("costs at most") && approval.textContent.includes("names of 3 records"), approval.textContent);
  button(approval, "Approve and suggest").click();
  const suggested = await until(() => proposals(section(main, "World suggestions")).find((node) => node.textContent.includes("Suggested by the model")), 15000, "the suggestion");
  await idle();
  const id = suggested.getAttribute("data-proposal");
  assert.ok(suggested.textContent.includes("1 call sent"), suggested.textContent);
  assert.ok(suggested.textContent.includes("Namesake") && byText(suggested, "a", "Lamplighters Guild").length, "the review shows the namesake");
  assert.ok(suggested.textContent.includes("A loose idea: it is never stored."), "the brainstorm cannot be accepted");
  assert.ok(!suggested.querySelectorAll("label").some((node) => node.textContent === "Accept A drowned bell tower"));

  // accept two of the three towns; the change set is exactly their two files, the map and the manifest
  tick(suggested, "Accept Brinewick");
  tick(suggested, "Accept Gullhaven");
  button(suggested, "Draft the change").click();
  const changes = await until(() => group(section(main, "World suggestions"), "The change set of " + id), 15000, "the change set");
  await idle();
  assert.equal(document.activeElement, changes, "focus goes to the change set");
  assert.ok(changes.textContent.includes("It writes exactly 4 files: 2 new records, one new revision of the world's map and the manifest."), changes.textContent);
  const summaries = changes.querySelectorAll("summary").map((node) => node.textContent);
  assert.equal(summaries.length, 4, summaries.join("\n"));
  assert.equal(summaries.filter((text) => text.startsWith("Creates sources/world/vale/_source/records/location/")).length, 2, summaries.join("\n"));
  assert.ok(summaries.some((text) => text.includes("/records/topology/")), "one map revision");
  assert.ok(summaries.includes("Updates the manifest sources/world/vale/_source/manifest.json"), summaries.join("\n"));
  assert.ok(changes.textContent.includes("Not stored: Tidemoor, The Lamplighters' Guild, A drowned bell tower."), changes.textContent);
  assert.ok(changes.querySelectorAll("pre").some((node) => node.textContent.includes('"name": "Brinewick"')), "the bytes are shown");
  accessible("the change set");
  button(changes, "Approve and write").click();
  const written = await until(() => group(section(main, "World suggestions"), "Written from " + id), 15000, "written");
  await idle();
  assert.equal(document.activeElement, written, "focus goes to what was written");
  for (const name of ["Brinewick", "Gullhaven"]) {
    assert.ok(byText(written, "a", name).some((node) => node.getAttribute("href").startsWith("#/source/")), name + " opens its Library page");
  }
  assert.ok(written.textContent.includes("with 3 links from this proposal"), written.textContent);
  assert.ok(!written.textContent.includes("Tidemoor"));

  // the audit: one bounded, read-only run with a cited finding, then a check of its evidence
  const audit = section(main, "Consistency audit");
  type(audit, "Records read at most", "100");
  button(audit, "Run the audit").click();
  const report = await until(() => group(section(main, "Consistency audit"), "Audit report"), 15000, "the report");
  await idle();
  assert.equal(document.activeElement, report);
  assert.ok(report.textContent.includes("Nothing written") && report.textContent.includes("Nothing was written"), report.textContent);
  const finding = report.querySelectorAll("article").find((node) => node.textContent.includes("missing link"));
  assert.ok(finding, report.textContent);
  assert.ok(byText(finding, "a", "Port Lantern").some((node) => node.getAttribute("href") === "#/source/" + encodeURIComponent(town)), "the citation opens the cited record");
  assert.ok(finding.textContent.includes("/payload/description: “Lamplighters Guild” (r1)"), finding.textContent);
  button(report, "Check for stale evidence").click();
  await idle();
  await until(() => group(section(main, "Consistency audit"), "Audit report").textContent.includes("Every finding's evidence is as it was."), 15000, "the evidence check");

  // a dependency pinned as reviewed
  const deps = group(section(main, "Source upgrades"), "Optional dependencies");
  assert.ok(deps.textContent.includes("This source declares no dependency."));
  choose(deps, "Pin a dependency at its current version", shared);
  button(group(section(main, "Source upgrades"), "Optional dependencies"), "Review the pin").click();
  const pin = await until(() => group(section(main, "Source upgrades"), "Review the pin"), 15000, "the pin's review");
  await idle();
  assert.ok(pin.textContent.includes("Replaces sources/world/vale/_source/manifest.json") && pin.textContent.includes("optional_dependencies"), pin.textContent);
  button(pin, "Pin it").click();
  await idle();
  const pinned = await until(() => group(section(main, "Source upgrades"), "Optional dependencies").textContent.includes("Pinned at its current version")
    && group(section(main, "Source upgrades"), "Optional dependencies"), 15000, "pinned");
  assert.equal(document.activeElement, pinned);
  assert.ok(byText(section(main, "Source upgrades"), "strong", "Previewing an update is not available yet.").length);
  accessible("the audit and the pin");

  // the shared card: not a world, and removing it would leave a chat's reference missing
  window.location.hash = "#/authoring/" + encodeURIComponent(shared);
  await until(() => section(main, "World suggestions") && byText(section(main, "World suggestions"), "strong", "World suggestions for this source is not available yet.").length, 15000, "the card's page");
  const removal = await until(() => section(main, "Source upgrades") && group(section(main, "Source upgrades"), "If this source were removed"), 15000, "the card's removal preview");
  assert.equal(section(main, "World suggestions").querySelectorAll("button,input,textarea,select").length, 0);
  const chat = removal.querySelectorAll("article").find((node) => (node.getAttribute("aria-label") || "").startsWith("References in campaigns/") && node.textContent.includes("chat"));
  assert.ok(chat, removal.textContent);
  assert.ok(chat.textContent.includes("Left missing"), chat.textContent);
  assert.ok(byText(removal, "strong", "Removing or disabling this source is not available yet.").length);
  accessible("the card's upgrades");
});
