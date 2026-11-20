// Sprint 115's chronicle page without the app: its logic (the times a person types, the minutes an advance comes
// to, a detail's name as an id, the sentence each clock change is reviewed in), and its memory card with canned
// answers (a late answer never replaces the character chosen since; an unsent account is its editor's own, and
// what is typed while one is being saved is kept).
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
const page = await import(pathToFileURL(path.join(STATIC, "pages", "chronicle", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "chronicle", "logic.js")).href);

const CHAT = "campaign:c1#chat/t1";
const ANYA = "campaign:c1/character/anya", BEA = "campaign:c1/character/bea";
const LISTED = { complete: true, campaigns: [{ campaign: "c1", chats: [{ chat: CHAT }] }] };
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function dossier(actor, editor, content) {
  return { chat: CHAT, actor, name: actor, whose: editor === "director" ? "npc" : "player", editor, topics: [],
    omitted: 0, corrected: 0, citable_total: 1, max_text: 4000,
    citable: [{ evidence: "e-" + content, type: "perception", channel: "sight", content, moment: { sequence: 1 } }] };
}

// The page mounted in the table's view of a chat whose cast is the player's own `cast`; `memory(args)` answers
// chronicle.memory (and may return a promise the test settles), `remember(args)` chronicle.remember. `drafts` is
// the shell's store, kept across mounts.
async function mounted(cast, memory, drafts = new Map(), remember = null) {
  const root = ui.el("div", {});
  const ctx = {
    ui, path: ["c1"], query: new URLSearchParams({ chat: CHAT }),
    href: (route, segments = []) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/"),
    registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {}, notify() {},
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api: async (op, args) => {
      if (op === "campaign.chats") return LISTED;
      if (op === "chronicle.coverage") return { features: [] };
      if (op === "campaign.hub") return { player: "owner", cast: cast.map((actor) => ({ actor, name: actor, role: "player", controller: "owner" })) };
      if (op === "chronicle.memory") return memory(args);
      if (op === "chronicle.remember" && remember) return remember(args);
      throw new Error("unexpected " + op);
    },
  };
  const done = page.mount(root, ctx);
  return { root, done };
}

const pane = (root) => root.querySelector('section[aria-label="Memory"]');
const accountField = (root) => {
  const label = pane(root).querySelectorAll("label").find((node) => node.textContent === "The account");
  return pane(root).querySelectorAll("textarea,input").find((node) => node.getAttribute("id") === label.getAttribute("for"));
};

test("a 24-hour time is minutes into the day, and anything else is refused", () => {
  assert.equal(logic.parseTime("00:00"), 0);
  assert.equal(logic.parseTime(" 9:30 "), 570);
  assert.equal(logic.parseTime("23:59"), 1439);
  for (const bad of ["24:00", "12:60", "9.30", "noon", "", undefined]) assert.equal(logic.parseTime(bad), null, String(bad));
  assert.equal(logic.clockTime(570), "09:30");
  assert.equal(logic.clockTime(0), "00:00");
});

test("an advance is whole days, hours and minutes adding up to at least a minute", () => {
  assert.equal(logic.advanceMinutes("1", "2", "3"), 1440 + 120 + 3);
  assert.equal(logic.advanceMinutes("", "", "45"), 45);
  assert.equal(logic.advanceMinutes("0", "0", "0"), null);
  assert.equal(logic.advanceMinutes("1.5", "", ""), null);
  assert.equal(logic.advanceMinutes("-1", "", ""), null);
});

test("a detail's name becomes an id the store accepts, or nothing", () => {
  assert.equal(logic.topicId("Visitor identity"), "visitor-identity");
  assert.equal(logic.topicId("  Café — owner?  "), "cafe-owner");
  assert.equal(logic.topicId("???"), "");
  assert.equal(logic.topicId("x".repeat(300)).length, 100);
  assert.notEqual(logic.newOperationId(), logic.newOperationId());
});

test("each clock change is reviewed in words that say what it does", () => {
  const before = { date: "1024-03-01", time: "10:00" }, after = { date: "1024-03-06", time: "10:00" };
  assert.equal(logic.planText({ operation: "initialize" }, { after: before }), "Start the clock at 1024-03-01 10:00.");
  assert.equal(logic.planText({ operation: "schedule", label: "The ship sails", kind: "deadline", date: "1024-03-05", minute: 570 }, { after: before }),
    "Schedule \"The ship sails\" (deadline) for 1024-03-05 09:30. The clock stays at 1024-03-01 10:00.");
  assert.equal(logic.planText({ operation: "advance" }, { before, after }), "Advance time from 1024-03-01 10:00 to 1024-03-06 10:00.");
  assert.match(logic.planText({ operation: "advance" }, { already_applied: true }), /already made/);
});

test("an answer for the character chosen before is never drawn over the one chosen since", async () => {
  let release;
  const first = new Promise((resolve) => { release = () => resolve(dossier(ANYA, "you", "Anya's own sight")); });
  let asked = 0;
  const { root, done } = await mounted([ANYA, BEA], (args) => {
    asked += 1;
    return asked === 1 ? first : dossier(args.actor, "you", args.actor === ANYA ? "Anya's own sight" : "Bea's own sight");
  });
  const chip = () => pane(root) && pane(root).querySelectorAll("a.chronicle-chip").find((node) => node.textContent.startsWith(BEA));
  for (let n = 0; n < 50 && !chip(); n += 1) await tick();
  chip().click();  // Bea is chosen while Anya's answer is still on its way
  for (let n = 0; n < 20 && !pane(root).textContent.includes("Bea's own sight"); n += 1) await tick();
  release();
  await done;
  await tick();
  assert.ok(pane(root).textContent.includes("Bea's own sight"), "Bea's memory stays");
  assert.ok(!pane(root).textContent.includes("Anya's own sight"), "Anya's late answer is dropped");
});

test("an unsent account is its editor's: a character handed to another controller does not show it", async () => {
  const drafts = new Map();
  const before = await mounted([ANYA], () => dossier(ANYA, "director", "seen"), drafts);
  await before.done;
  const field = accountField(before.root);
  field.value = "The director's private note.";
  field.dispatchEvent({ type: "input" });

  const after = await mounted([ANYA], () => dossier(ANYA, "you", "seen"), drafts);
  await after.done;
  assert.equal(accountField(after.root).value, "", "the new controller does not see it");
  const again = await mounted([ANYA], () => dossier(ANYA, "director", "seen"), drafts);
  await again.done;
  assert.equal(accountField(again.root).value, "The director's private note.", "it is still the director's");
});

test("what is typed while an account is being saved is kept as the next draft", async () => {
  let release;
  const drafts = new Map();
  const { root, done } = await mounted([ANYA], () => dossier(ANYA, "you", "seen"), drafts, (args) =>
    new Promise((resolve) => { release = () => resolve({ done: { account: args.operation_id }, memory: dossier(ANYA, "you", "seen") }); }));
  await done;
  const field = (name) => {
    const label = pane(root).querySelectorAll("label").find((node) => node.textContent === name);
    return pane(root).querySelectorAll("textarea,input").find((node) => node.getAttribute("id") === label.getAttribute("for"));
  };
  const typeInto = (name, value) => { const node = field(name); node.value = value; node.dispatchEvent({ type: "input" }); };
  typeInto("What it is about", "Coin");
  typeInto("The account", "Mara gave the coin back.");
  const cite = () => {
    const box = pane(root).querySelectorAll("input").find((node) => node.getAttribute("value") === "e-seen");
    box.checked = true;
    box.dispatchEvent({ type: "change" });
  };
  cite();
  pane(root).querySelectorAll("button").find((node) => node.textContent === "Remember this").click();
  await tick();
  typeInto("The account", "Mara gave the coin back, then left.");  // typed while the save is on its way
  release();
  for (let n = 0; n < 20; n += 1) await tick();
  assert.equal(field("The account").value, "Mara gave the coin back, then left.");

  typeInto("The account", "Unchanged this time.");
  cite();
  pane(root).querySelectorAll("button").find((node) => node.textContent === "Remember this").click();
  await tick();
  release();
  for (let n = 0; n < 20; n += 1) await tick();
  assert.equal(field("The account").value, "", "what was sent, and only that, is cleared");
});
