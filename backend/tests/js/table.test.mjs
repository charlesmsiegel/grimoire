// Sprint 115's shared turn (058) without the app: who is at the table, whose characters are whose, the draft key a
// player's unsent part is kept under, whether a turn can be posted now, and the words a review is said in.
// Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "chat", "table-logic.js")).href);

const OWNER = "human:owner";
const BOB = "human:bob";
const cast = [
  { actor: "campaign:c/character/anya", name: "Anya", role: "player", controller: OWNER, persona: true },
  { actor: "campaign:c/character/mara", name: "Mara", role: "npc", controller: "director" },
  { actor: "campaign:c/character/tam", name: "Tamsin", role: "player", controller: BOB },
];
const scene = (more = {}) => ({ cast, drafts: [], state: { open_turn: null, waiting: [] }, ...more });

test("a player is named as the screen names them", () => {
  assert.equal(logic.playerLabel(OWNER, OWNER), "you");
  assert.equal(logic.playerLabel(BOB, OWNER), "bob");
  assert.equal(logic.modeLabel("speech"), "Speech");
  assert.equal(logic.modeLabel("other"), "other");
});

test("each player drafts only for the characters they play", () => {
  assert.deepEqual(logic.partsOf(scene(), OWNER).map((item) => item.name), ["Anya"]);
  assert.deepEqual(logic.partsOf(scene(), BOB).map((item) => item.name), ["Tamsin"]);
  assert.deepEqual(logic.partsOf(scene(), "human:carol"), []);
});

test("the shared turn shows only for a table of more than one person or character", () => {
  const alone = scene({ cast: cast.slice(0, 2) });
  assert.equal(logic.sharedTable(alone, [OWNER]), false);
  assert.equal(logic.sharedTable(alone, [OWNER, BOB]), true, "a second player is at this device");
  assert.equal(logic.sharedTable(scene(), [OWNER]), true, "two characters people play");
  assert.equal(logic.sharedTable({ ...alone, drafts: [{ actor: "x" }] }, [OWNER]), true, "parts already held");
});

test("an unsent part is kept per chat, character and player", () => {
  const key = logic.partKey("campaign:c#chat/t", "campaign:c/character/tam", BOB);
  assert.equal(key, "table:campaign:c#chat/t:campaign:c/character/tam:human:bob");
  assert.notEqual(key, logic.partKey("campaign:c#chat/t", "campaign:c/character/tam", OWNER), "a character handed on never shows the last player's words");
  assert.notEqual(key, logic.partKey("campaign:c#chat/u", "campaign:c/character/tam", BOB));
});

test("drafts compare by order, character, writer, words and mode", () => {
  const one = [{ actor: "a", by: OWNER, text: "x", mode: "auto", name: "A" }, { actor: "b", by: BOB, text: "y", mode: "speech" }];
  assert.equal(logic.draftsKey(one), logic.draftsKey(one.map((item) => ({ ...item, name: "renamed" }))));
  assert.notEqual(logic.draftsKey(one), logic.draftsKey([...one].reverse()));
  assert.notEqual(logic.draftsKey(one), logic.draftsKey([{ ...one[0], text: "z" }, one[1]]));
  assert.equal(logic.draftsKey(undefined), "[]");
});

test("a turn is posted only once the last one is closed and play is not paused", () => {
  assert.equal(logic.postBlocker(scene()), null);
  assert.match(logic.postBlocker(scene({ state: { open_turn: "t2", waiting: [] } })), /Keep a take or pass the turn/);
  assert.match(logic.postBlocker(scene({ state: { open_turn: null, waiting: ["paused"] } })), /paused/);
});

test("a review carries exactly the drafts reviewed and says each part's character and player", () => {
  const review = { owner: OWNER, drafts: [
    { actor: "a", name: "Anya", by: OWNER, text: "x", mode: "auto", ready: true, controller: OWNER },
    { actor: "t", name: "Tamsin", by: BOB, text: "y", mode: "action", ready: false, controller: "director" },
  ] };
  assert.deepEqual(logic.reviewed(review), [{ actor: "a", by: OWNER, text: "x", mode: "auto" }, { actor: "t", by: BOB, text: "y", mode: "action" }]);
  assert.deepEqual(logic.reviewLines(review), ["1. Anya, by you (Auto)",
    "2. Tamsin, by bob (Action): no longer theirs to play, so the turn would be refused"]);
});

test("a character is handed to the director or someone at this device", () => {
  assert.deepEqual(logic.seatOptions([OWNER, BOB], OWNER), [{ value: "director", label: "The director (an NPC)" },
    { value: OWNER, label: "You" }, { value: BOB, label: "bob" }]);
});
