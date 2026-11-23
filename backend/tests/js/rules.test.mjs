// Sprint 116's Rules page without the app: its logic (a module id as 086 takes it, a sheet's typed values, dice as
// typed, the words a roll and a review are said in, problems grouped by their part), and the page with canned
// answers (a check answered after the text changed is never shown as the text's; unsaved text kept in the tab saves
// against the draft it was begun from; a roll reviewed again with the same values keeps its request id, and a
// changed one, or one after a roll is shown, gets a new id; a roll answered after another character was chosen is
// still shown; answers take the focus).
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
const page = await import(pathToFileURL(path.join(STATIC, "pages", "rules", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "rules", "logic.js")).href);

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async (until, rounds = 60) => { for (let n = 0; n < rounds && !until(); n += 1) await tick(); };

function context(pathParts, query, api, drafts = new Map(), notes = []) {
  return {
    ui, path: pathParts, query: new URLSearchParams(query),
    href: (route, segments = []) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/"),
    registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {}, notify: (text) => notes.push(text),
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api,
  };
}

function input(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

const button = (root, label) => root.querySelectorAll("button").filter((node) => node.textContent === label).at(-1);

test("a module id is 086's: lower case letters, digits and _, from a letter", () => {
  assert.equal(logic.moduleIdError("duel_rules"), "");
  assert.equal(logic.moduleIdError("a" + "1".repeat(63)), "");
  assert.match(logic.moduleIdError(""), /Give/);
  for (const bad of ["Duel", "1duel", "duel-rules", "a".repeat(65), "../x"]) assert.notEqual(logic.moduleIdError(bad), "", bad);
  assert.deepEqual(logic.recentIds('["b", "a", "BAD", 3]', "a"), ["a", "b"]);
  assert.deepEqual(logic.recentIds("not json", "c"), ["c"]);
  assert.equal(logic.recentIds(JSON.stringify(["a", "b", "c", "d", "e", "f", "g", "h", "i"]), "z").length, 8);
});

test("a sheet's typed values: empty takes its default, numbers are whole, a list is a line each", () => {
  const fields = [{ key: "dexterity", type: "number" }, { key: "hp", type: "resource" }, { key: "gear", type: "list" }, { key: "name", type: "text" }];
  const typed = { dexterity: " 14 ", hp: "", gear: "rope\n\n lantern ", name: "" };
  assert.deepEqual(logic.sheetValues(fields, (key) => typed[key]), { values: { dexterity: 14, gear: ["rope", "lantern"] }, errors: {} });
  assert.deepEqual(logic.sheetValues(fields, (key) => (key === "hp" ? "1.5" : "")).errors, { hp: "Give a whole number." });
  assert.equal(logic.fieldHint({ type: "number", min: 1, max: 20, default: 10 }), "A whole number from 1 to 20; empty takes 10");
  assert.equal(logic.fieldHint({ type: "list", max_items: 100 }), "One entry a line, at most 100");
});

test("dice as typed, and the words a roll is said in", () => {
  assert.deepEqual(logic.parseFaces("3, 5 6"), [3, 5, 6]);
  for (const bad of ["", "x", "3.5", "-1"]) assert.equal(logic.parseFaces(bad), null, bad);
  assert.equal(logic.diceText([20]), "1d20");
  assert.equal(logic.diceText([10, 10, 10, 10]), "4d10");
  assert.equal(logic.diceText([6, 6]), "2d6");
  assert.equal(logic.actionText("d20", { ability: "dexterity", skill: "stealth", dc: 15, modifier: 3 }), "Dexterity (stealth), DC 15, +3");
  assert.equal(logic.actionText("pbta", { stat: "cool", modifier: -1, trigger: "under fire" }), "Defy Danger with cool −1: under fire");
  assert.equal(logic.resultText({ family: "d20", faces: [4], result: { total: 7, success: false } }), "4 → 7, failure");
  assert.equal(logic.resultText({ family: "pbta", faces: [3, 5], result: { total: 9, outcome: "partial" } }), "3 + 5 → 9, partial");
  assert.equal(logic.planText({ dice: [20], name: "Anya Rook", attempt: "director:r1" }), "Roll 1d20 for Anya Rook under attempt director:r1.");
  assert.notEqual(logic.newRequestId(), logic.newRequestId());
  assert.match(logic.newRequestId(), /^roll-[a-z0-9]+-[a-z0-9]+$/);
  assert.equal(logic.shortRevision("sha256:" + "ab".repeat(32)), "abababababab");
});

test("problems are grouped by the part they are about, the whole file first named as such", () => {
  const groups = logic.problemGroups([{ path: "$", message: "not JSON" }, { path: "checks.stealth.total", message: "a" },
    { path: "sheets.warrior", message: "b" }, { path: "checks.arcana", message: "c" }]);
  assert.deepEqual(groups.map(([head, items]) => [head, items.length]), [["The whole file", 1], ["checks", 2], ["sheets", 1]]);
});

const AUTHORING = { templates: [], listed: { drafts: false, modules: false, revisions: true }, unavailable: [], not_saved: "Not saved." };
const TEXT = '{"id": "duel"}';

test("a check answered after the text changed is not shown as the text's problems", async () => {
  let release;
  const late = new Promise((resolve) => { release = () => resolve({ etag: "e1", problems: [{ path: "checks.stealth.total", message: "unknown name 'stelth' (did you mean 'stealth'?)", hint: "stealth" }] }); });
  const root = ui.el("div", {});
  const ctx = context([], { module: "duel" }, async (op) => {
    if (op === "rules.authoring") return AUTHORING;
    if (op === "rules.module") return { module: "duel", file: "config/mechanics/drafts/duel.json", draft: { text: TEXT, etag: "e1", bytes: TEXT.length }, revisions: [] };
    if (op === "rules.check") return late;
    throw new Error("unexpected " + op);
  });
  const done = page.mount(root, ctx);
  await settle(() => root.querySelectorAll("label").some((node) => node.textContent === "Module JSON"));
  await done;
  const editor = input(root, "Module JSON");
  editor.value = '{"id": "duel", "checks": {}}';
  editor.dispatchEvent({ type: "input" });
  button(root, "Check").click();
  await tick();
  editor.value = '{"id": "duel", "checks": {"x": 1}}';  // typed on while the check was on its way
  editor.dispatchEvent({ type: "input" });
  release();
  await settle(() => root.textContent.includes("check it again"));
  assert.ok(root.textContent.includes("The text changed while it was checked"));
  assert.ok(!root.textContent.includes("Did you mean stealth?"), "the late problems are not drawn");
  assert.ok(root.textContent.includes("changes that are not saved"));
});

const CHAT = "campaign:c1#chat/t1";
const PROFILE = { family: "d20", title: "D20", edition: "5e", module: "reference:062/ruleset/d20@5e",
  action: [{ name: "ability", label: "Ability", type: "text" }, { name: "dc", label: "DC", type: "whole" }],
  example: { ability: "dexterity", dc: 15 } };
const ROLLS = { campaign: "c1", chat: CHAT, audience: "table", rolls: [], profiles: [PROFILE], no_roll: "No Ars rolls.",
  module_note: "Not pinned.", unavailable: [],
  actors: [{ actor: "campaign:c1/character/anya", name: "Anya Rook", role: "player" }] };

test("a roll reviewed again with the same values keeps its request id; a changed one is a new request", async () => {
  const asked = [];
  const root = ui.el("div", {});
  const ctx = context(["c1"], { chat: CHAT }, async (op, args) => {
    if (op === "rules.authoring") return AUTHORING;
    if (op === "campaign.chats") return { complete: true, campaigns: [{ campaign: "c1", chats: [{ chat: CHAT }] }] };
    if (op === "rules.rolls") return ROLLS;
    if (op === "rules.roll_plan") {
      asked.push(args);
      return { plan: { family: "d20", edition: "5e", action: args.action, dice: [20], name: "Anya Rook", attempt: args.request_id,
        backing: { text: args.text, turn: "turn-1" }, turn: "turn-1", review: "sha256:r", files: ["authority/rolls/c1/"], already: null } };
    }
    throw new Error("unexpected " + op);
  });
  await page.mount(root, ctx);
  await settle(() => root.querySelectorAll("label").some((node) => node.textContent === "What your character does"));
  const roller = root.querySelector('section[aria-label="Roll a check"]');
  assert.deepEqual(input(roller, "Roll for").querySelectorAll("option").map((node) => node.textContent), ["Anya Rook (yours)"]);
  button(roller, "Fill in the example").click();
  const words = input(roller, "What your character does");
  words.value = "I slip past.";
  words.dispatchEvent({ type: "input" });
  button(roller, "Review").click();
  await settle(() => asked.length === 1 && root.textContent.includes("Review this roll"));
  assert.ok(root.textContent.includes("Roll 1d20 for Anya Rook under attempt " + asked[0].request_id + "."));
  assert.ok(root.textContent.includes("Backed by your action"));
  button(roller, "Review").click();
  await settle(() => asked.length === 2);
  assert.equal(asked[1].request_id, asked[0].request_id, "the same request keeps its id");
  const again = input(roller, "What your character does");
  again.value = "I wait for the guard to turn.";
  again.dispatchEvent({ type: "input" });
  button(roller, "Review").click();
  await settle(() => asked.length === 3);
  assert.notEqual(asked[2].request_id, asked[0].request_id, "a changed request is a new one");
  for (const args of asked) assert.ok(!("draw" in args) && !("faces" in args), "the page never chooses a draw");
});

const groupOf = (node, title) => node.querySelectorAll("div").find((item) => item.getAttribute("aria-label") === title);
const focused = () => document.activeElement && document.activeElement.textContent;

test("unsaved text kept in the tab saves against the draft it was begun from, not one saved elsewhere since", async () => {
  const drafts = new Map();
  let current = { text: TEXT, etag: "sha256:e1" };
  const saves = [];
  const api = async (op, args) => {
    if (op === "rules.authoring") return AUTHORING;
    if (op === "rules.module") return { module: "duel", file: "f", draft: { ...current, bytes: current.text.length }, revisions: [] };
    if (op === "rules.check") return { etag: null, problems: [] };
    if (op === "rules.save_plan") {
      saves.push(args.expected);
      if (args.expected !== current.etag) throw Object.assign(new Error("changed"), { reason: "changed" });
      return { plan: { module: "duel", file: "f", bytes: args.text.length, before: current.text.length, creates: false, unchanged: false },
        check: { problems: [] } };
    }
    throw new Error("unexpected " + op);
  };
  const ready = (node) => node.querySelectorAll("label").some((item) => item.textContent === "Module JSON");
  const first = ui.el("div", {});
  document.body.append(first);
  await page.mount(first, context([], { module: "duel" }, api, drafts));
  await settle(() => ready(first));
  const mine = '{"id": "duel", "name": "mine"}';
  const editor = input(first, "Module JSON");
  editor.value = mine;
  editor.dispatchEvent({ type: "input" });
  button(first, "Check").click();
  await settle(() => groupOf(first, "Problems"));
  assert.equal(focused(), "Problems", "the problems take the focus");
  first.remove();

  current = { text: '{"id": "duel", "name": "theirs"}', etag: "sha256:e2" };  // saved from somewhere else meanwhile
  const second = ui.el("div", {});
  document.body.append(second);
  await page.mount(second, context([], { module: "duel" }, api, drafts));
  await settle(() => ready(second));
  assert.equal(input(second, "Module JSON").value, mine, "the unsaved text is kept");
  assert.ok(second.textContent.includes("The saved draft changed since you began editing"), second.textContent);
  button(second, "Save").click();
  await settle(() => saves.length === 1);
  await tick();
  assert.deepEqual(saves, ["sha256:e1"], "the save is reviewed against the draft the text was begun from");
  assert.ok(!groupOf(second, "Review this save"), "nothing that saves over the newer draft is offered");
  button(second, "Keep my text over it").click();
  button(second, "Save").click();
  await settle(() => groupOf(second, "Review this save"));
  assert.deepEqual(saves, ["sha256:e1", "sha256:e2"]);
  assert.equal(focused(), "Review this save", "the review takes the focus");
  second.remove();
});

// like the app's draft store: an empty text is not kept (setting it clears the key)
class Store extends Map { set(key, value) { if (!value) { this.delete(key); return this; } return super.set(key, value); } }

function draftApi(state, saves) {
  return async (op, args) => {
    if (op === "rules.authoring") return AUTHORING;
    if (op === "rules.module") return { module: "duel", file: "f", draft: { ...state.current, bytes: state.current.text.length }, revisions: [] };
    if (op === "rules.check") return { etag: null, problems: [] };
    if (op === "rules.save_plan") {
      saves.push(args.expected);
      if (args.expected !== state.current.etag) throw Object.assign(new Error("changed"), { reason: "changed" });
      return { plan: { module: "duel", file: "f", bytes: args.text.length, before: state.current.text.length, creates: false, unchanged: false },
        check: { problems: [] } };
    }
    throw new Error("unexpected " + op);
  };
}
const editorReady = (node) => node.querySelectorAll("label").some((item) => item.textContent === "Module JSON");

test("an editor cleared and left unsaved comes back empty, not as the saved text", async () => {
  const drafts = new Store();
  const api = draftApi({ current: { text: TEXT, etag: "sha256:e1" } }, []);
  const first = ui.el("div", {});
  document.body.append(first);
  await page.mount(first, context([], { module: "duel" }, api, drafts));
  await settle(() => editorReady(first));
  const editor = input(first, "Module JSON");
  editor.value = "";
  editor.dispatchEvent(new Event("input"));
  first.remove();

  const second = ui.el("div", {});
  document.body.append(second);
  await page.mount(second, context([], { module: "duel" }, api, drafts));
  await settle(() => editorReady(second));
  assert.equal(input(second, "Module JSON").value, "", "the empty unsaved edit is kept");
  assert.ok(second.textContent.includes("has changes that are not saved"), second.textContent);
  button(second, "Discard my changes").click();
  assert.equal(input(second, "Module JSON").value, TEXT);
  second.remove();

  const third = ui.el("div", {});
  document.body.append(third);
  await page.mount(third, context([], { module: "duel" }, api, drafts));
  await settle(() => editorReady(third));
  assert.equal(input(third, "Module JSON").value, TEXT, "a discarded clear is forgotten");
  third.remove();
});

test("an unchanged editor refused as changed shows the newer draft, and never saves the old text over it", async () => {
  const state = { current: { text: TEXT, etag: "sha256:e1" } };
  const saves = [];
  const root = ui.el("div", {});
  document.body.append(root);
  await page.mount(root, context([], { module: "duel" }, draftApi(state, saves), new Store()));
  await settle(() => editorReady(root));
  state.current = { text: '{"id": "duel", "name": "theirs"}', etag: "sha256:e2" };  // saved from somewhere else meanwhile
  button(root, "Save").click();
  await settle(() => input(root, "Module JSON").value !== TEXT);
  assert.deepEqual(saves, ["sha256:e1"]);
  assert.equal(input(root, "Module JSON").value, state.current.text, "the editor shows the draft as it is now");
  assert.ok(root.textContent.includes("it is shown as it is now"), root.textContent);
  assert.ok(!groupOf(root, "Review this save"));
  button(root, "Save").click();
  await settle(() => saves.length === 2);
  assert.deepEqual(saves, ["sha256:e1", "sha256:e2"], "a later save is reviewed against the newer draft and its text");
  root.remove();
});

const TWO = { ...ROLLS, actors: [...ROLLS.actors, { actor: "campaign:c1/character/bea", name: "Bea Quill", role: "player" }] };

test("a roll answered after another character is chosen is still shown, and the next review is a new request", async () => {
  const asked = [], notes = [];
  let release;
  const rolling = new Promise((resolve) => { release = resolve; });
  const api = async (op, args) => {
    if (op === "campaign.chats") return { complete: true, campaigns: [{ campaign: "c1", chats: [{ chat: CHAT }] }] };
    if (op === "rules.rolls") return TWO;
    if (op === "rules.roll_plan") {
      asked.push(args);
      return { plan: { family: "d20", edition: "5e", action: args.action, dice: [20], name: args.actor.endsWith("bea") ? "Bea Quill" : "Anya Rook",
        actor: args.actor, attempt: args.request_id, backing: { text: args.text, turn: "turn-1" }, turn: "turn-1", review: "sha256:r",
        files: ["authority/rolls/c1/"], already: null, earlier: [] } };
    }
    if (op === "rules.roll") {
      await rolling;
      return { done: { attempt: args.request_id, drawn: true }, roll: { attempt: args.request_id, actor: args.actor, name: "Anya Rook",
        family: "d20", dice: [20], faces: [4], result: { total: 7, success: false }, outcome: "failure",
        resolution: { attempt: args.request_id, result: "failure" } } };
    }
    throw new Error("unexpected " + op);
  };
  const node = ui.el("div", {});
  document.body.append(node);
  await page.mount(node, context(["c1"], { chat: CHAT }, api, new Map(), notes));
  const roller = node.querySelector('section[aria-label="Roll a check"]');
  await settle(() => roller.querySelectorAll("label").some((item) => item.textContent === "What your character does"));
  button(roller, "Fill in the example").click();
  const words = input(roller, "What your character does");
  words.value = "I slip past.";
  words.dispatchEvent({ type: "input" });
  button(roller, "Review").click();
  await settle(() => groupOf(node, "Review this roll"));
  assert.equal(focused(), "Review this roll");
  button(groupOf(node, "Review this roll"), "Roll").click();
  await tick();
  const who = input(roller, "Roll for");  // Bea is chosen while Anya's roll is on its way
  who.value = "campaign:c1/character/bea";
  who.dispatchEvent({ type: "change" });
  release();
  await settle(() => groupOf(node, "The roll"));
  assert.ok(groupOf(node, "The roll").textContent.includes("Anya Rook · 1d20: 4 → 7, failure"), "the recorded roll is shown, with whose it is");
  assert.deepEqual(notes, ["Recorded for Anya Rook: 4 → 7, failure."]);

  // back to Anya: the same values reviewed again after a roll is shown are a new request, never the rolled one
  const again = input(roller, "Roll for");
  again.value = "campaign:c1/character/anya";
  again.dispatchEvent({ type: "change" });
  const text = input(roller, "What your character does");
  text.value = "I slip past.";
  text.dispatchEvent({ type: "input" });
  button(roller, "Review").click();
  await settle(() => asked.length === 2);
  assert.notEqual(asked[1].request_id, asked[0].request_id);
  node.remove();
});
