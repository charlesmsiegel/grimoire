// Sprint 115's Prep page without the app: its logic (a ruleset form's values, the words an estimate is said in, the
// guard on late answers), and the page with canned answers: a check answered after another character was chosen is
// dropped; an estimate answered after the brief changed is never offered for approval; what is typed while a scene
// is being prepared is kept.
// Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
install("http://127.0.0.1:1");
const ui = await import(pathToFileURL(path.join(STATIC, "shell", "ui.js")).href);
const page = await import(pathToFileURL(path.join(STATIC, "pages", "prep", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "prep", "logic.js")).href);

const CHAT = "campaign:c1#chat/t1";
const ANYA = "campaign:c1/character/anya", BEA = "campaign:c1/character/bea";
const LISTED = { complete: true, campaigns: [{ campaign: "c1", chats: [{ chat: CHAT }] }] };
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async (n = 30) => { for (let i = 0; i < n; i += 1) await tick(); };

const D20 = {
  family: "d20", edition: "5e-2024", title: "D20 ability check", module: "reference:062/ruleset/d20@5e-2024", notes: [],
  action: [{ name: "ability", label: "Ability", type: "choice", options: ["dexterity", "strength"] }, { name: "dc", label: "DC", type: "whole" }],
  result: [{ name: "die", label: "The d20 as rolled", type: "whole" }, { name: "success", label: "Success", type: "yesno" }],
  example: { action: { ability: "dexterity", dc: 15 }, result: { die: 12, success: false } },
};
const RULES = { chat: CHAT, audience: "table", pinned: null, not_pinned: "Nothing pins a ruleset.", noncanonical: "A proposal only.",
  profiles: ["d20", "wod", "pbta", "ars"].map((family) => ({ ...D20, family })),
  actors: [{ actor: ANYA, name: "Anya", role: "player", yours: true }, { actor: BEA, name: "Bea", role: "player", yours: true }] };
const EVIDENCE = { revision: "r", evidence: [{ evidence: "secret-1", kind: "secret", source: "campaign:c1#proposition/oil", text: "The oil was sold." }],
  missing: [], omitted: {}, cast: [{ actor: ANYA, name: "Anya" }], locations: [{ actor: "campaign:c1/location/quay", name: "The Quay" }] };
const PREPARATION = { chat: CHAT, evidence: EVIDENCE, model: null, proposals: [], spent: [], saved: false, note: "Nothing here is saved." };
const PLAN = { request: "x", expected: "token", replay: false, blocked: null, snapshot: "r",
  call: { stage: "planning", calls: 1, prompt_tokens: 500, reply_tokens_at_most: 800, attempts_at_most: 1, route: "prep@x/m",
    cost_at_most: "0.0135", cost_state: "estimated", local: false } };

// The page mounted for `audience`; `answer(op, args)` answers every op but campaign.chats (and may return a
// promise the test settles). `drafts` is the shell's store, kept across mounts.
async function mounted(audience, answer, drafts = new Map()) {
  const root = ui.el("div", {});
  const ctx = {
    ui, path: ["c1"], query: new URLSearchParams({ chat: CHAT, audience }),
    href: (route, segments = []) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/"),
    registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {}, notify() {},
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api: async (op, args) => (op === "campaign.chats" ? LISTED : answer(op, args)),
  };
  await page.mount(root, ctx);
  return root;
}

const section = (root, title) => root.querySelector('section[aria-label="' + title + '"]');
const group = (root, title) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === title);
const button = (root, label) => root.querySelectorAll("button").find((node) => node.textContent === label);
function control(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}
function set(root, text, value, type = "input") {
  const node = control(root, text);
  node.value = value;
  node.dispatchEvent({ type });
}

test("a form's text becomes the values 062 takes, or says what is wrong", () => {
  const fields = [{ name: "dc", type: "whole" }, { name: "dice", type: "dice" }, { name: "ok", type: "yesno" },
    { name: "note", type: "text", optional: true }, { name: "skill", type: "text" }, { name: "stat", type: "choice" }];
  const typed = { dc: " -3 ", dice: "3, 4 6", ok: "no", note: "", skill: "stealth", stat: "wisdom" };
  assert.deepEqual(logic.formValues(fields, (name) => typed[name]),
    { values: { dc: -3, dice: [3, 4, 6], ok: false, note: null, skill: "stealth", stat: "wisdom" }, errors: {} });
  const bad = logic.formValues(fields, (name) => ({ dc: "1.5", dice: "three", ok: "", note: "", skill: " ", stat: "" })[name]);
  assert.deepEqual(Object.keys(bad.errors).sort(), ["dc", "dice", "ok", "skill", "stat"]);
  assert.equal(logic.fieldText({ type: "yesno" }, true), "yes");
  assert.equal(logic.fieldText({ type: "dice" }, [10, 6]), "10, 6");
  assert.equal(logic.fieldText({ type: "text" }, null), "");
});

test("an estimate is said as a cost at most, or as unpriced, and where the call goes", () => {
  const lines = logic.estimateLines(PLAN.call);
  assert.equal(lines[0], "One planning call to prep@x/m.");
  assert.match(lines[1], /About 500 prompt tokens .*at most 800 reply tokens, in at most 1 attempt\./);
  assert.match(lines[2], /costs at most 0\.0135 .*not a bill/);
  assert.equal(lines[3], "It leaves this computer for the configured provider.");
  const unpriced = logic.estimateLines({ ...PLAN.call, cost_state: "unpriced", cost_at_most: null, local: true });
  assert.ok(unpriced.includes("No price is set for this model, so its cost cannot be estimated."));
  assert.ok(unpriced.includes("It goes to a model on this computer."));
  assert.equal(logic.usageLine({ sent: 1, tokens: { input_tokens: 410, output_tokens: 120 }, cost: "0.003" }),
    "1 call sent · 410 prompt tokens · 120 reply tokens · 0.003");
  assert.equal(logic.usageLine({ sent: 0 }), "");
  assert.notEqual(logic.newRequestId(), logic.newRequestId());
  assert.match(logic.newRequestId(), /^[a-z0-9._-]+$/);
});

test("the guard names only the newest request as current", () => {
  const gate = logic.latest();
  const first = gate.next();
  assert.ok(gate.current(first));
  const second = gate.next();
  assert.ok(!gate.current(first) && gate.current(second));
  gate.bump();
  assert.ok(!gate.current(second));
});

test("a check answered after another character was chosen is never shown as that one's", async () => {
  let release;
  const root = await mounted("table", (op, args) => {
    if (op === "prep.rulesets") return RULES;
    if (op === "prep.ruleset_check") {
      return new Promise((resolve) => { release = () => resolve({ valid: true, canonical: false, edition: "5e-2024", actor: args.actor,
        action: args.action, result: args.result, noncanonical: "A proposal only." }); });
    }
    throw new Error("unexpected " + op);
  });
  const rules = section(root, "Ruleset contracts");
  button(rules, "Fill in the example").click();
  button(rules, "Check this action").click();
  await settle(5);
  set(rules, "For", BEA, "change");  // Bea is chosen while Anya's check is still on its way
  release();
  await settle();
  assert.equal(group(rules, "Check result"), undefined, "Anya's late answer is not shown as Bea's");
  assert.ok(rules.textContent.includes("check it again") || !rules.textContent.includes("It fits"));
  // checked again for Bea, the answer is shown
  button(rules, "Check this action").click();
  await settle(5);
  release();
  await settle();
  assert.ok(group(rules, "Check result").textContent.includes("It fits the 5e-2024 contract."));
});

test("an estimate answered after the brief changed is never offered for approval", async () => {
  let release;
  const drafts = new Map();
  const root = await mounted("director", (op) => {
    if (op === "prep.rulesets") return { ...RULES, audience: "director" };
    if (op === "prep.catalogs") return { campaign: "c1", catalogs: [], chats: [], more_catalogs: false, more_chats: false, change: "", estimate: "" };
    if (op === "prep.preparation") return PREPARATION;
    if (op === "prep.prepare_plan") return new Promise((resolve) => { release = () => resolve({ plan: PLAN, evidence: EVIDENCE }); });
    throw new Error("unexpected " + op);
  }, drafts);
  const prep = section(root, "Scene preparation");
  set(prep, "What the scene should be about", "A night on the quay");
  button(prep, "Estimate the call").click();
  await settle(5);
  set(prep, "What the scene should be about", "A morning at the market");  // typed while the estimate is out
  release();
  await settle();
  assert.equal(group(prep, "Approve this call"), undefined, "the old brief's estimate is not offered");
  assert.ok(prep.textContent.includes("The brief changed while the call was being estimated"));
  assert.equal(control(prep, "What the scene should be about").value, "A morning at the market");
  button(prep, "Estimate the call").click();
  await settle(5);
  release();
  await settle();
  const approval = group(prep, "Approve this call");
  assert.ok(approval && approval.textContent.includes("A morning at the market"));
  assert.ok(approval.textContent.includes("costs at most 0.0135"));
});

test("what is typed while a scene is being prepared is kept as the next brief", async () => {
  let release;
  const drafts = new Map();
  const prepared = { ...PREPARATION, proposals: [{ request: "x", brief: "A night on the quay", snapshot: "r", current: true, outcome: null,
    usage: { sent: 1, tokens: { input_tokens: 410, output_tokens: 120 }, cost: "0.003" },
    draft: { premise: { text: "Mara waits on the quay", evidence: [{ evidence: "secret-1", kind: "secret", source: "campaign:c1#proposition/oil" }] },
      cast: [{ actor: ANYA, name: "Anya" }], location: { actor: "campaign:c1/location/quay", name: "The Quay" },
      questions: [], clues: [{ text: "Oil on the steps", evidence: [{ evidence: "secret-1", kind: "secret", source: "s" }] }],
      tensions: [], stakes: [], directions: [] } }] };
  const root = await mounted("director", (op) => {
    if (op === "prep.rulesets") return { ...RULES, audience: "director" };
    if (op === "prep.catalogs") return { campaign: "c1", catalogs: [], chats: [], more_catalogs: false, more_chats: false, change: "", estimate: "" };
    if (op === "prep.preparation") return PREPARATION;
    if (op === "prep.prepare_plan") return { plan: PLAN, evidence: EVIDENCE };
    if (op === "prep.prepare") return new Promise((resolve) => { release = () => resolve({ done: { request: "x", replay: false }, preparation: prepared }); });
    throw new Error("unexpected " + op);
  }, drafts);
  const prep = section(root, "Scene preparation");
  set(prep, "What the scene should be about", "A night on the quay");
  button(prep, "Estimate the call").click();
  await settle();
  button(group(prep, "Approve this call"), "Approve and prepare").click();
  await settle(5);
  release();
  await settle();
  const scene = prep.querySelectorAll("article").find((node) => node.getAttribute("aria-label") === "Prepared scene x");
  assert.ok(scene.textContent.includes("Mara waits on the quay") && scene.textContent.includes("secret-1 · secret"));
  assert.equal(control(prep, "What the scene should be about").value, "", "the brief that was sent is cleared");

  // a second time: the person starts the next brief while the call is out, and it is kept
  set(prep, "What the scene should be about", "A night on the quay");
  button(prep, "Estimate the call").click();
  await settle();
  button(group(prep, "Approve this call"), "Approve and prepare").click();
  await settle(5);
  set(prep, "What the scene should be about", "The next morning");  // typed while the call is out
  release();
  await settle();
  assert.equal(control(prep, "What the scene should be about").value, "The next morning");
});
