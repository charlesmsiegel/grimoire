// Sprint 115's captured companions (050) end to end, in fakedom against a real app server: a campaign's hub links to
// its companions; the table's view shows nothing; the director sees the creatures nobody has captured, with their
// species; a capture being written survives leaving the page; a review asked before the form changed is never
// drawn; the capture is reviewed and made as reviewed; a commit whose answer was lost is made again from the same
// review and spends nothing twice; what a capture does not do is said. Run by test_app_companions.py, which serves a
// synthetic card chat whose campaign holds two goblins and Mara Vell's three balls, and passes GRIMOIRE_APP,
// GRIMOIRE_TOKEN, GRIMOIRE_CHAT, GRIMOIRE_CAMPAIGN and GRIMOIRE_TRAINER (Mara Vell's ref).
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
const campaign = env.GRIMOIRE_CAMPAIGN;

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));
const alerts = () => document.body.querySelectorAll("div").filter((node) => node.getAttribute("role") === "alert").map((node) => node.textContent).join(" | ");

function control(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

function pick(root, label, value) {
  const select = control(root, label);
  assert.ok(select.querySelectorAll("option").some((node) => node.getAttribute("value") === value), label + " offers " + value);
  select.value = value;
  select.dispatchEvent({ type: "change" });
}

function pickText(root, label, text) {
  const option = control(root, label).querySelectorAll("option").find((node) => node.textContent.startsWith(text));
  assert.ok(option, label + " offers " + text);
  pick(root, label, option.getAttribute("value"));
}

function type(root, label, value) {
  const node = control(root, label);
  node.value = value;
  node.dispatchEvent({ type: "input" });
}

// Hold back the app's answers to one op until the returned function is called.
function holdBack(prefix) {
  const fetchNow = globalThis.fetch;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  globalThis.fetch = async (url, options) => { if (String(url).startsWith(prefix)) await held; return fetchNow(url, options); };
  return () => { globalThis.fetch = fetchNow; release(); };
}

// The next call of one op reaches the app, which does it, but its answer is lost on the way back.
function loseAnswer(prefix) {
  const fetchNow = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (!String(url).startsWith(prefix)) return fetchNow(url, options);
    globalThis.fetch = fetchNow;
    await (await fetchNow(url, options)).text();
    throw new TypeError("the connection dropped");
  };
}

test("the director records a capture, reviewed first and made once", { skip: !env.GRIMOIRE_APP }, async () => {
  const window = install(env.GRIMOIRE_APP);
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the campaign hub links to its companions, for the same chat
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat);
  await until(() => section(main, "Cast and stage"), 15000, "the hub");
  const link = byText(main, "a", "Companions").find((node) => node.getAttribute("href").startsWith("#/companions/"));
  assert.ok(link, "the hub links to the companions");
  link.click();

  // the table's view: companions and captures are the director's
  const tableView = await until(() => section(main, "Companions"), 15000, "the table's view");
  assert.ok(tableView.textContent.includes("are the director's"));
  assert.equal(tableView.querySelectorAll("button").length, 0, "the table records no capture");
  assert.ok(!section(main, "Record a capture"));

  view(main, "Director view").click();
  const form = await until(() => section(main, "Record a capture"), 15000, "the capture form");
  const roster = section(main, "Companions").textContent;
  assert.ok(roster.includes("Nobody has captured a companion") && roster.includes("Snik · Goblin") && roster.includes("Grub · Goblin"));
  assert.ok(roster.includes("no NPC speaks for it, nobody's control of any character changes, and no character learns of it"));

  // choosing a trainer redraws the devices offered, not the form: the person stays in the Trainer box
  const trainerBox = control(form, "Trainer");
  trainerBox.focus();
  pick(form, "Trainer", env.GRIMOIRE_TRAINER);
  const again = await until(() => section(main, "Record a capture"), 15000, "the form for Mara");
  // (compared by identity: assert.equal would print two whole fakedom trees on failure)
  assert.ok(document.activeElement === trainerBox && again.contains(trainerBox), "the Trainer box keeps the focus");
  pickText(again, "Creature", "Snik");
  pickText(again, "Device used", "ordinary-ball (3 held)");
  type(again, "How many the trainer's active party holds", "1");

  // the capture being written survives leaving the page
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat);
  await until(() => section(main, "Cast and stage"), 15000, "the hub again");
  window.location.hash = "#/companions/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat) + "&audience=director";
  const back = await until(() => section(main, "Record a capture"), 15000, "the form again");
  assert.equal(control(back, "Trainer").value, env.GRIMOIRE_TRAINER);
  assert.equal(control(back, "How many the trainer's active party holds").value, "1");
  assert.ok(control(back, "Device used").value === "ordinary-ball");

  // a review asked before the form changed is never drawn
  const release = holdBack("/api/companions.capture_plan");
  byText(back, "button", "Review the capture").at(-1).click();
  type(back, "How many the trainer's active party holds", "2");
  release();
  await idle();
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.ok(!section(main, "Review this capture"), "the review of the form as it was is not shown");

  // reviewed, then made as reviewed
  byText(back, "button", "Review the capture").at(-1).click();
  const review = await until(() => section(main, "Review this capture"), 15000, "the review");
  assert.ok(review.textContent.includes("Mara Vell captures Snik (Goblin): it joins Mara Vell's active party. It spends one ordinary-ball from Mara Vell's holdings."), review.textContent);
  assert.ok(review.textContent.includes("no NPC speaks for it"));

  // the answer to the commit is lost: the same review, made again, spends nothing twice
  loseAnswer("/api/companions.capture_commit");
  byText(review, "button", "Make this change").at(-1).click();
  await idle();
  assert.ok(section(main, "Review this capture"), "a lost answer keeps the review: " + alerts());
  byText(section(main, "Review this capture"), "button", "Make this change").at(-1).click();
  await until(() => !section(main, "Review this capture"), 15000, "the capture made");
  await until(() => byText(body, "div", "That capture was already made; nothing was spent or written again.").length, 15000, "the retry's answer");
  const made = section(main, "Companions").textContent;
  assert.ok(made.includes("Mara Vell's companions") && made.includes("Snik · Goblin · active party"), made);
  assert.ok(made.includes("Creatures nobody has captured") && made.includes("Grub · Goblin"));
  // 021 takes one accepted change a turn: the next capture waits for the next turn of play
  assert.ok(section(main, "Record a capture").textContent.includes("Every turn of this chat already holds an accepted change"));
  assert.ok(![...window.sessionStorage.items.keys()].some((key) => key.startsWith("grimoire-draft:companions:")), "a made capture's draft is gone");
});
