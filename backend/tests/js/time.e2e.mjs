// Sprint 116's Time page end to end, in fakedom against a real app server: from a campaign's hub to its time and
// commitments in the director's view; a commitment offered with a new deadline and refused by its NPC obligor; a
// plan adopted for the harbour guild under its leader's decision, racing that deadline; the owner's own promise
// accepted with the owner's key, the review showing the words it rests on; Tamsin, Bob's character, offered no
// activity here; a review cancelled and one gone stale (refused as changed and read again), each keeping what was
// typed and the focus on its form; a 20-day study begun for the owner's character and pursued across the deadline,
// each change reviewed first (the deadline is flagged and nothing happens by itself), and the same commit sent
// again changing nothing; the refused commitment still shown
// with its passed deadline and not overdue; the calendar's pending advance applied to the plan, with its
// developments; the table's view, which sees only that all of this is the director's; and the Wave 9 coverage card.
// Run by test_app_time.py, which serves the synthetic library of time_fixtures.py with the clock started on
// 1024-03-01 10:00, and passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT and GRIMOIRE_CAMPAIGN.
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

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const group = (root, title) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === title);
const article = (root, start) => root.querySelectorAll("article").find((node) => (node.getAttribute("aria-label") || "").startsWith(start));
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));
const accessible = (where) => assert.deepEqual(audit(document.body).map((violation) => where + ": " + describe(violation)), []);

// the control of the field labelled `text` in `root`
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

function pick(root, label, value) {
  const node = control(root, label);
  const option = node.querySelectorAll("option").find((item) => item.textContent.includes(value) || item.getAttribute("value") === value);
  assert.ok(option, label + " offers " + value);
  node.value = option.getAttribute("value");
  node.dispatchEvent({ type: "change" });
}

// tick the checkbox whose label reads `text` in the fieldset whose legend reads `legend`
function tick(root, legend, text) {
  const set = root.querySelectorAll("fieldset").find((node) => node.querySelector("legend").textContent === legend);
  assert.ok(set, "a set " + legend);
  const label = set.querySelectorAll("label").find((node) => node.textContent.includes(text));
  assert.ok(label, legend + " offers " + text);
  const box = label.querySelector("input");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
}

async function review(main, form, said) {
  byText(form, "button", "Review").at(-1).click();
  const box = await until(() => group(main, "Review this change"), 15000, "the review");
  for (const line of [].concat(said)) assert.ok(box.textContent.includes(line), "the review says " + line + ": " + box.textContent);
  assert.equal(document.activeElement, box, "the review takes the focus");
  return box;
}

async function make(main, box, label = "Make this change") {
  const button = box.querySelectorAll("button").find((node) => node.textContent === label);
  assert.ok(button, "the review offers " + label + ": " + box.textContent);
  button.click();
  await until(() => !group(main, "Review this change"), 15000, "the change to be made");
  await idle();
  assert.ok(document.activeElement.classList.contains("time-now"), "after a change the focus is on the clock");
}

test("a campaign's downtime, offscreen plans and commitments, each change reviewed before it is made", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  // a call the page does not make, as another tab of the same director would
  const api = async (op, args) => (await globalThis.fetch("/api/" + op, { method: "POST",
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": process.env.GRIMOIRE_TOKEN },
    body: JSON.stringify({ campaign, chat, audience: "director", ...args }) })).json();
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the director's hub links to Time, for the same chat and view
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat) + "&audience=director";
  await until(() => section(main, "Cast and stage"), 15000, "the hub");
  const link = byText(main, "a", "Time").find((node) => node.getAttribute("href").startsWith("#/time/"));
  assert.ok(link, "the hub links to Time");
  assert.ok(link.getAttribute("href").includes("audience=director") && link.getAttribute("href").includes("chat="));
  link.click();
  const clock = await until(() => section(main, "Campaign clock"), 15000, "the Time page");
  await until(() => section(main, "Commitments") && section(main, "Wave 9 features"), 15000, "every card");
  assert.equal(view(main, "Director view").getAttribute("aria-pressed"), "true");
  assert.ok(clock.textContent.includes("Now: 1024-03-01 10:00"));
  accessible("the director's Time page");

  // a commitment offered with a new deadline: Mara owes Anya the harbour map by 1024-03-10
  const offer = group(main, "Offer a commitment");
  pick(offer, "Kind", "mission");
  type(offer, "What is owed", "Bring Anya the harbour map");
  tick(offer, "Owed by", "Mara Vell");
  tick(offer, "Owed to", "Anya Rook");
  tick(offer, "Citing", "Mara asks the party");
  pick(offer, "Deadline", "new");
  type(offer, "New deadline: what is due", "Map due");
  type(offer, "New deadline: date", "1024-03-10");
  type(offer, "New deadline: time", "00:00");
  await make(main, await review(main, offer, ["Offer the mission “Bring Anya the harbour map”, owed by Mara Vell to Anya Rook.",
    "It schedules a new deadline, Map due, 1024-03-10 00:00."]));
  await until(() => section(main, "Campaign clock").textContent.includes("Map due"), 15000, "the deadline on the clock");
  const map = () => article(section(main, "Commitments"), "Mission: Bring Anya the harbour map");
  assert.ok(map().textContent.includes("Due by Map due, 1024-03-10 00:00."));

  // a plan for the harbour guild, under its leader Mara's decision, racing the map's deadline (and missing it)
  const adopt = group(main, "Adopt a plan");
  assert.ok(adopt, "adopting a plan is offered: " + section(main, "Offscreen plans").textContent);
  pick(adopt, "Group and leader", "Harbour Guild, as Mara Vell decides");
  type(adopt, "Goal", "Control the harbour");
  type(adopt, "What the leader decides", "Seize the docks");
  type(adopt, "Step 1", "Gather the bribe money");
  type(group(adopt, "Step 1 takes"), "Days", "5");
  type(adopt, "Step 2", "Take the docks");
  type(group(adopt, "Step 2 takes"), "Days", "10");
  pick(adopt, "Races the deadline", "Map due");
  await make(main, await review(main, adopt, ["Harbour Guild adopts the plan “Control the harbour”, as Mara Vell decides: “Seize the docks”.",
    "Gather the bribe money, due 1024-03-06 10:00", "Take the docks, due 1024-03-16 10:00", "would not finish in time"]));
  const plan = article(section(main, "Offscreen plans"), "Plan: Control the harbour");
  assert.ok(plan && plan.textContent.includes("Harbour Guild: Control the harbour") && byText(plan, "span", "Pending").length);

  // Mara refuses, citing what she said
  const record = map().querySelectorAll("details").find((node) => node.textContent.includes("Record what happened next"));
  record.open = true;
  const step = group(record, "Record a step of Bring Anya the harbour map");
  pick(step, "What happened", "refused");
  tick(step, "Citing", "Mara says she will not carry the map");
  await make(main, await review(main, step, "Record “Bring Anya the harbour map” as refused, as Mara Vell decided (it is offered now)."));
  await until(() => byText(map(), "span", "Refused").length, 15000, "the refusal");

  // Anya's own promise to Mara, accepted with the owner's key, resting on the words Anya said
  const promised = group(main, "Offer a commitment");
  pick(promised, "Kind", "promise");
  type(promised, "What is owed", "Bring Mara the harbour map");
  tick(promised, "Owed by", "Anya Rook");
  tick(promised, "Owed to", "Mara Vell");
  tick(promised, "Citing", "Mara asks the party");
  await make(main, await review(main, promised, "Offer the promise “Bring Mara the harbour map”, owed by Anya Rook to Mara Vell."));
  const promise = () => article(section(main, "Commitments"), "Promise: Bring Mara the harbour map");
  const decide = promise().querySelectorAll("details").find((node) => node.textContent.includes("Record what happened next"));
  decide.open = true;
  const accept = group(decide, "Record a step of Bring Mara the harbour map");
  pick(accept, "What happened", "accepted");
  tick(accept, "Citing", "Anya promises");
  const keyed = await review(main, accept, ["This is your own character's decision: making it gives your key for Anya Rook",
    "These words of yours become the recorded backing of this decision:"]);
  assert.equal(keyed.querySelector("blockquote").textContent, "I promise to bring you the harbour map by the festival.");
  assert.ok(keyed.querySelector("figcaption").textContent.startsWith("Anya Rook, turn "));
  assert.ok(!keyed.querySelectorAll("button").some((node) => node.textContent === "Make this change"), "only with the key");
  await make(main, keyed, "Make this change with my key");
  await until(() => byText(promise(), "span", "Accepted").length, 15000, "the acceptance");
  assert.ok(promise().textContent.includes("Anya Rook"), promise().textContent);

  // Tamsin is Bob's character: what she spends her time on is his choice, not made here
  const downtime = section(main, "Downtime");
  assert.ok(downtime.textContent.includes("An activity for Tamsin Hale is not available yet."), downtime.textContent);
  assert.ok(!control(group(main, "Begin an activity"), "For").querySelectorAll("option").some((node) => node.textContent.includes("Tamsin")));

  // a 20-day study for Anya, begun and then pursued across the map's deadline
  const fill = () => {
    const begin = group(main, "Begin an activity");
    pick(begin, "For", "Anya Rook");
    pick(begin, "Kind", "study");
    type(begin, "What they set out to do", "Learn the tide cant");
    type(group(begin, "Effort it takes"), "Days", "20");
    return begin;
  };
  const typed = () => {
    const begin = group(main, "Begin an activity");
    assert.equal(control(begin, "For").value, control(begin, "For").querySelectorAll("option").find((node) => node.textContent === "Anya Rook").getAttribute("value"));
    assert.equal(control(begin, "Kind").value, "study");
    assert.equal(control(begin, "What they set out to do").value, "Learn the tide cant", "what was typed is kept");
    assert.equal(control(group(begin, "Effort it takes"), "Days").value, "20");
    assert.equal(document.activeElement, control(begin, "For"), "the focus is back on the form");
  };
  const BEGIN = "Begin “Learn the tide cant” (study) for Anya Rook: 20 days of effort required. No time passes; the clock stays at 1024-03-01 10:00.";
  // cancelled: nothing is made, and the form is as it was typed
  byText(await review(main, fill(), BEGIN), "button", "Cancel")[0].click();
  await until(() => !group(main, "Review this change"), 15000, "the review to close");
  typed();
  // gone stale: another change made after the review is seen, and the review is read again from the new records
  const stale = await review(main, group(main, "Begin an activity"), BEGIN);
  const now = await api("time.overview", {});
  const elsewhere = await api("time.commitment_commit", { operation: "offer", operation_id: "e2e-elsewhere",
    expected: now.commitments.etag, commitment: "lantern", kind: "promise", summary: "Lend Mara a lantern",
    owed_by: [now.cast.find((item) => item.name === "Mara Vell").actor], owed_to: [now.cast.find((item) => item.name === "Anya Rook").actor],
    cites: ["ev-asks"] });
  assert.equal(elsewhere.done.already_applied, false, JSON.stringify(elsewhere));
  stale.querySelectorAll("button").find((node) => node.textContent === "Make this change").click();
  await until(() => !group(main, "Review this change"), 15000, "the stale review to go");
  await idle();
  assert.ok(article(section(main, "Commitments"), "Promise: Lend Mara a lantern"), "the records were read again");
  assert.ok(!article(section(main, "Downtime"), "Activity: Learn the tide cant"), "the stale review made nothing");
  typed();
  await make(main, await review(main, group(main, "Begin an activity"), BEGIN));
  const study = () => article(section(main, "Downtime"), "Activity: Learn the tide cant");
  assert.ok(study().textContent.includes("no time of 20 days") && byText(study(), "span", "Active").length);

  const pursue = group(main, "Pursue an activity");
  type(group(pursue, "Time that passes"), "Days", "20");
  type(pursue, "Note", "Anya follows the cant");
  const reviewed = await review(main, pursue, ["Spend 20 days on “Learn the tide cant”: the clock moves from 1024-03-01 10:00 to 1024-03-21 10:00, and 20 days of effort is credited.",
    "It completes the activity.", "It crosses Map due: that is a flag only. Nothing happens by itself"]);
  // the commit is kept as it was sent, to send it again
  const realFetch = globalThis.fetch;
  let sent = null;
  globalThis.fetch = (url, options) => {
    if (String(url).includes("/api/time.downtime_commit")) sent = options;
    return realFetch(url, options);
  };
  await make(main, reviewed);
  globalThis.fetch = realFetch;
  assert.ok(sent, "the commit was sent");
  await until(() => byText(study(), "span", "Complete").length, 15000, "the study complete");
  assert.ok(section(main, "Campaign clock").textContent.includes("Now: 1024-03-21 10:00"));
  assert.ok(byText(section(main, "Campaign clock"), "span", "Flagged").length, "the deadline is flagged");
  assert.ok(section(main, "Campaign clock").textContent.includes("Nothing happens by itself"));

  // the same commit sent again is the same change: nothing more is written
  const again = await (await globalThis.fetch("/api/time.downtime_commit", sent)).json();
  assert.equal(again.done.already_applied, true, JSON.stringify(again));
  const after = await (await globalThis.fetch("/api/time.overview", { ...sent, body: JSON.stringify({ campaign, chat, audience: "director" }) })).json();
  assert.equal(after.clock.now.date, "1024-03-21", "time passed once");
  assert.equal(after.downtime.activities[0].progress, 20 * 1440, "effort credited once");

  // the refused commitment still shows its deadline, now passed, and it is not overdue
  assert.ok(map().textContent.includes("Due by Map due, 1024-03-10 00:00: the deadline has passed."), map().textContent);
  assert.ok(!byText(map(), "span", "Overdue").length, "a refused commitment is never overdue");

  // the pursuit's advance is pending for the plans; applied, the guild's steps develop and it misses the deadline
  const offscreen = section(main, "Offscreen plans");
  const pending = group(offscreen, "Apply the advance to 1024-03-21 10:00");
  assert.ok(pending, "the pursuit's advance is pending: " + offscreen.textContent);
  await make(main, await review(main, pending, ["Apply the calendar's advance from 1024-03-01 10:00 to 1024-03-21 10:00",
    "Gather the bribe money", "Take the docks", "missed Map due",
    "A development is the director's alone: it delivers no knowledge, event or rumor, and no character learns of it."]));
  const developed = article(section(main, "Offscreen plans"), "Plan: Control the harbour");
  assert.equal(byText(developed, "span", "Developed").filter((node) => node.classList.contains("g-pill")).length, 2);
  assert.ok(developed.textContent.includes("would not finish in time"));
  assert.ok(section(main, "Offscreen plans").textContent.includes("Every advance of the clock is applied to the plans."));
  accessible("the Time page after its changes");

  // the Wave 9 coverage card: what is on other screens is linked; what is not is named, with why
  const features = section(main, "Wave 9 features");
  const story = byText(features, "a", "Story threads");
  assert.ok(story.length && story[0].getAttribute("href").startsWith("#/story/" + encodeURIComponent(chat)), "story threads, for this chat");
  assert.ok(byText(features, "a", "Hypothetical reply").length && byText(features, "a", "Live context preview").length);
  const rolls = byText(features, "a", "Recorded rolls");
  assert.ok(rolls.length && rolls[0].getAttribute("href").startsWith("#/rules/"), "recorded rolls, on this campaign's Rules page");
  assert.ok(byText(features, "a", "World authoring proposals").length, "world authoring, from the library");
  const attitudes = byText(features, "a", "Relationships and attitudes");
  assert.ok(attitudes.length && attitudes[0].getAttribute("href").startsWith("#/attitudes/" + encodeURIComponent(chat)), "attitudes, for this chat");
  assert.ok(byText(features, "a", "Cross-campaign scene transfer").length);
  assert.ok(!features.textContent.includes("is not available yet"), "every Wave 9 first slice is on a screen");
  assert.ok(features.textContent.includes("Downtime") && features.textContent.includes("Quests and commitments"));

  // the table's view: only that these are the director's
  view(main, "Table view").click();
  const table = await until(() => section(main, "Time and commitments"), 15000, "the table's view");
  assert.ok(table.textContent.includes("director's"));
  assert.equal(table.querySelectorAll("button").length, 0, "the table changes nothing here");
  for (const title of ["Campaign clock", "Downtime", "Offscreen plans", "Commitments"]) assert.ok(!section(main, title), "the table sees no " + title);
  assert.ok(!main.textContent.includes("harbour map") && !main.textContent.includes("tide cant"), "nor anything of them");
  await until(() => section(main, "Wave 9 features"), 15000, "the coverage card for the table");
  accessible("the table's Time page");
});
