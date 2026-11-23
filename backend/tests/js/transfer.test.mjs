// Sprint 116's transfer page (084) without the app: each actor's choices (a namesake is never one), saved decisions
// read back, the plan request (each gap named), the words a plan is said in, and a copy answered after the page was
// left still clearing its draft. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "transfer", "logic.js")).href);
const { install } = await import("./fakedom.mjs");
install("http://127.0.0.1:1");
const ui = await import(pathToFileURL(path.join(STATIC, "shell", "ui.js")).href);
const page = await import(pathToFileURL(path.join(STATIC, "pages", "transfer", "page.js")).href);
const settle = async (n = 30) => { for (let i = 0; i < n; i += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };

const MARA = "campaign:a/character/mara", ANYA = "campaign:a/character/anya", GOB = "campaign:a/creature/gob";
const cast = [
  { actor: MARA, name: "Mara", role: "character", can_create: true, candidates: [],
    namesakes: [{ actor: "campaign:b/character/lamps", name: "Mara" }] },
  { actor: ANYA, name: "Anya", role: "persona", can_create: true,
    candidates: [{ actor: "campaign:b/character/anya", name: "Anya" }], namesakes: [] },
  { actor: GOB, name: "Gob", role: "actor", can_create: false, candidates: [], namesakes: [] },
];

test("an actor's choices are its candidates, new where it can be made, and leaving it out; never a namesake", () => {
  assert.deepEqual(logic.decisionOptions(cast[0]).map((item) => item.value), ["", "new", "leave"]);
  assert.deepEqual(logic.decisionOptions(cast[1]).map((item) => item.value), ["", "campaign:b/character/anya", "new", "leave"]);
  assert.equal(logic.decisionOptions(cast[1])[1].label, "Reuse Anya (already in the target)");
  assert.deepEqual(logic.decisionOptions(cast[2]).map((item) => item.value), ["", "leave"], "a creature can only be left out");
  assert.equal(logic.roleText("persona"), "your persona");
});

test("saved decisions are read back, dropping any the scan no longer offers", () => {
  const saved = JSON.stringify({ [MARA]: "campaign:b/character/lamps", [ANYA]: "campaign:b/character/anya", [GOB]: "new", other: "new" });
  assert.deepEqual(logic.readDecisions(saved, cast), { [MARA]: "", [ANYA]: "campaign:b/character/anya", [GOB]: "" });
  assert.deepEqual(logic.readDecisions("not json", cast), { [MARA]: "", [ANYA]: "", [GOB]: "" });
});

test("the plan request names every decision, and each gap before anything is asked", () => {
  const base = { chat: "campaign:a#chat/t", target: "b", cast, sources: ["card:x/lorebook/l@r1"], label: "  Copied ", operationId: "op-1" };
  assert.equal(logic.requestOf({ ...base, target: "", decisions: {} }).error, "Choose the campaign to copy into.");
  assert.equal(logic.requestOf({ ...base, decisions: { [MARA]: "new" } }).error, "Decide what happens to Anya.");
  const { request } = logic.requestOf({ ...base, decisions: { [MARA]: "new", [ANYA]: "campaign:b/character/anya", [GOB]: "leave" } });
  assert.deepEqual(request, { chat: "campaign:a#chat/t", target: "b", audience: "director", operation_id: "op-1",
    cast: { [MARA]: "new", [ANYA]: "campaign:b/character/anya", [GOB]: "leave" }, sources: ["card:x/lorebook/l@r1"], label: "Copied" });
  assert.ok(!("label" in logic.requestOf({ ...base, label: " ", decisions: { [MARA]: "new", [ANYA]: "leave", [GOB]: "leave" } }).request));
  assert.match(logic.newOperationId(), /^op-[a-z0-9]+-[a-z0-9]+$/);
});

test("a plan is said in words: where a post was, why one is omitted, what stays, what to know", () => {
  assert.equal(logic.whereText("t-c-2.000003"), "turn 3");
  assert.equal(logic.whereText("t-c-2.000003.p2"), "turn 3, post 2");
  assert.equal(logic.whereText("t-c-2.000002.1.1"), "turn 2, a kept line");
  assert.equal(logic.whereText("page 1 p000004"), "imported page 1, post 4");
  assert.equal(logic.omittedText({ where: "t-c-2.000003", reason: "private", missed: [] }),
    "turn 3: private (a whisper or a withheld action), never copied");
  assert.equal(logic.omittedText({ where: "t-c-2.000004", reason: "unheard", missed: [{ actor: ANYA, name: "Anya" }] }),
    "turn 4: not heard by Anya, so not copied");
  assert.equal(logic.leftText({ kind: "effect", where: "t-c-2.000002", detail: "ev-1" }),
    "turn 2: an accepted effect (ev-1) stays in the origin campaign");
  assert.equal(logic.noteText({ code: "world_differs", detail: "the target campaign plays another world, or none" }),
    "The target campaign plays another world, or none.");
  assert.equal(logic.noteText({ code: "odd", detail: "x" }), "odd: x");
  assert.equal(logic.reasonText({ reason: "different_individual" }),
    "that actor only shares the name: it is another individual (different_individual)");
  assert.equal(logic.reasonText({ reason: "new_reason" }), "(new_reason)");
});

test("a copy answered after the page was left still spends its draft, so it is not planned again as a second copy", async () => {
  const CHAT = "campaign:a#chat/t";
  const drafts = new Map([["transfer:" + CHAT, JSON.stringify({ target: "b", label: "", decisions: { [MARA]: "new" }, sources: [] })]]);
  const leaving = new AbortController();
  let release;
  const calls = [];
  const plan = { mapping: [], posts: [], label: "", sources: { carried: [], left_out: [] }, posts_total: 0, pages: 0, omitted: [],
    left: [], notes: [], files: [], review: "rv" };
  const answers = {
    "transfer.targets": { targets: [{ campaign: "b", title: "B", chats: 1, world: null }], not_available: { state: "x", chronology: "x", art: "x" } },
    "transfer.scan": { cast: [{ ...cast[0], active: true, problem: null }], sources: [], history: { turns: 1, page_posts: 0, pages: 0 },
      history_problems: [], notes: [] },
    "transfer.plan": { plan },
  };
  const root = ui.el("div", {});
  await page.mount(root, {
    ui, path: [CHAT], query: new URLSearchParams("audience=director"), signal: leaving.signal,
    href: () => "#", registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {}, notify() {},
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api: async (op, args) => {
      calls.push(op);
      if (op === "transfer.commit") return new Promise((resolve) => { release = () => resolve({ done: { chat: "campaign:b#chat/u", already_applied: false } }); });
      return answers[op];
    },
  });
  await settle();
  root.querySelectorAll("button").find((node) => node.textContent === "Plan the copy").click();
  await settle();
  root.querySelectorAll("button").find((node) => node.textContent === "Copy as reviewed").click();
  await settle(5);
  leaving.abort();  // the page is left while the copy is on its way
  release();
  await settle();
  assert.deepEqual(calls, ["transfer.targets", "transfer.scan", "transfer.plan", "transfer.commit"]);
  assert.equal(drafts.get("transfer:" + CHAT), undefined, "the decisions of a copy that was made are not kept");
});
