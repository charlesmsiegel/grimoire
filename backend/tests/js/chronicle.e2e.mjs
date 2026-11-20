// Sprint 115's Chronicle page end to end, in fakedom against a real app server: from a campaign's hub to its
// chronicle; the table sees only its own character's memory and an unsent account survives leaving the page; the
// director starts the clock, schedules a deadline and advances past it, each change reviewed before it is made;
// the director writes what an NPC remembers, citing its evidence; and features with no screen yet are named.
// Run by test_app_chronicle.py, which serves a synthetic library whose card chat has a kept turn by the seated
// player and confirmed evidence for both cast members, and passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT,
// GRIMOIRE_CAMPAIGN and GRIMOIRE_NPC (Mara Vell's ref).
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

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const group = (main, title) => main.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === title);
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));

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

async function reviewAndMake(main, form, said) {
  byText(form, "button", "Review").at(-1).click();
  const box = await until(() => group(main, "Review this change"), 15000, "the review");
  assert.ok(box.textContent.includes(said), "the review says " + said + ": " + box.textContent);
  byText(box, "button", "Make this change").at(-1).click();
  await until(() => !group(main, "Review this change"), 15000, "the change to be made");
  await idle();
  return box.textContent;
}

test("a campaign's clock and memory, each change reviewed before it is made", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the campaign hub links to its chronicle, for the same chat
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat);
  await until(() => section(main, "Cast and stage"), 15000, "the hub");
  const link = byText(main, "a", "Chronicle").find((node) => node.getAttribute("href").startsWith("#/chronicle/"));
  assert.ok(link, "the hub links to the chronicle");
  link.click();
  const memory = await until(() => section(main, "Memory"), 15000, "the chronicle");
  const chronicleHash = window.location.hash;

  // the table's view: the clock is the director's, and only the player's own character's memory is here
  assert.equal(view(main, "Table view").getAttribute("aria-pressed"), "true");
  assert.ok(section(main, "Campaign clock").textContent.includes("director's"));
  assert.equal(section(main, "Campaign clock").querySelectorAll("button").length, 0, "the table changes no clock");
  const chips = await until(() => memory.querySelectorAll("a.chronicle-chip").length && memory.querySelectorAll("a.chronicle-chip"), 15000, "whose memory");
  assert.deepEqual(chips.map((node) => node.textContent), ["Anya Rook · yours"], "an NPC's memory is the director's");
  const form = await until(() => group(main, "Write an account"), 15000, "the account form");
  assert.ok(form.textContent.includes("Anya feels the weight of the coin"), "Anya's own evidence is offered");
  assert.ok(!form.textContent.includes("Sister Maren"), "and nobody else's");

  // an account typed and not sent is still there after the person leaves the page and comes back
  type(form, "The account", "Mara gave the coin back.");
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign);
  await until(() => section(main, "Cast and stage"), 15000, "the hub again");
  window.location.hash = chronicleHash;
  const back = await until(() => group(main, "Write an account"), 15000, "the account form again");
  assert.equal(input(back, "The account").value, "Mara gave the coin back.");

  // the director's view: start the clock, schedule a deadline and advance past it, each reviewed first
  view(main, "Director view").click();
  const clock = await until(() => section(main, "Campaign clock") && group(main, "Start the clock") && section(main, "Campaign clock"), 15000, "the director's clock");
  assert.ok(clock.textContent.includes("no clock yet"));
  const start = group(main, "Start the clock");
  type(start, "Date", "1024-03-01");
  type(start, "Time", "10:00");
  await reviewAndMake(main, start, "Start the clock at 1024-03-01 10:00.");
  await until(() => section(main, "Campaign clock").textContent.includes("Now: 1024-03-01 10:00"), 15000, "the clock started");

  // a deadline reviewed and then cancelled is still being written; it is cleared only once it is scheduled
  type(group(main, "Schedule a deadline"), "What is due", "The ship sails");
  type(group(main, "Schedule a deadline"), "Due date", "1024-03-05");
  byText(group(main, "Schedule a deadline"), "button", "Review").at(-1).click();
  const cancelled = await until(() => group(main, "Review this change"), 15000, "the review");
  byText(cancelled, "button", "Cancel").at(-1).click();
  const schedule = await until(() => group(main, "Schedule a deadline"), 15000, "the form again");
  assert.equal(input(schedule, "What is due").value, "The ship sails");
  assert.equal(input(schedule, "Due date").value, "1024-03-05");
  await reviewAndMake(main, schedule, "Schedule \"The ship sails\" (deadline) for 1024-03-05 00:00. The clock stays at 1024-03-01 10:00.");
  await until(() => section(main, "Campaign clock").textContent.includes("The ship sails"), 15000, "the deadline");
  assert.equal(input(group(main, "Schedule a deadline"), "What is due").value, "", "a scheduled deadline's draft is gone");
  assert.ok(byText(section(main, "Campaign clock"), "span", "Pending").length);

  const advance = group(main, "Advance time");
  type(advance, "Days", "5");
  const reviewed = await reviewAndMake(main, advance, "Advance time from 1024-03-01 10:00 to 1024-03-06 10:00.");
  assert.ok(reviewed.includes("It flags: The ship sails."), "the review names the deadline it flags");
  const advanced = await until(() => section(main, "Campaign clock").textContent.includes("Now: 1024-03-06 10:00") && section(main, "Campaign clock"), 15000, "time advanced");
  assert.ok(byText(advanced, "span", "Flagged").length && advanced.textContent.includes("Nothing happens by itself"));

  // the director writes what the NPC remembers, citing the evidence it rests on
  const memoryNow = section(main, "Memory");
  const mara = await until(() => memoryNow.querySelectorAll("a.chronicle-chip").find((node) => node.textContent.startsWith("Mara Vell")), 15000, "the NPC");
  assert.ok(mara.getAttribute("href").includes(encodeURIComponent(npc)));
  mara.click();
  const npcForm = await until(() => {
    const found = group(main, "Write an account");
    return found && found.textContent.includes("Sister Maren") && found;
  }, 15000, "the NPC's evidence");
  assert.ok(!npcForm.textContent.includes("Anya feels the weight"), "an NPC cites only its own evidence");
  type(npcForm, "What it is about", "Visitor identity");
  type(npcForm, "The account", "The visitor was Sister Maren in disguise.");
  const unmasked = npcForm.querySelectorAll("input").find((node) => node.getAttribute("value") === "unmasked");
  unmasked.checked = true;
  unmasked.dispatchEvent({ type: "change" });
  byText(npcForm, "button", "Remember this").at(-1).click();
  await idle();
  const topic = await until(() => section(main, "Memory").querySelectorAll("article").find((node) => node.getAttribute("aria-label") === "Remembered: visitor-identity"), 15000, "the account");
  assert.ok(topic.textContent.includes("The visitor was Sister Maren in disguise.") && topic.textContent.includes("Current"));
  assert.ok(topic.textContent.includes("Rests on: The hood falls in the crypt"));

  // what Wave 8 has on other screens is linked; what has none yet is named, never offered
  const features = section(main, "Campaign features");
  assert.ok(byText(features, "a", "Private local voice").length);
  assert.ok(byText(features, "strong", "Reviewing a fork or retcon is not available yet").length);
  const world = byText(features, "a", "What an NPC thinks others think").find((node) => node.getAttribute("href").startsWith("#/world/" + encodeURIComponent(campaign)));
  assert.ok(world && world.getAttribute("href").includes("chat="), "the World page is linked for this campaign and chat");
});
