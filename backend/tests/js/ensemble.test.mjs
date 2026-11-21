// Sprint 115's ensemble page (051) without the app: the saved plan read back (what no longer fits the cast is
// dropped), the form as a preview request (each refusal names what to fix), and the sentences a proposed exchange
// is said in. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "ensemble", "logic.js")).href);

const ANYA = "campaign:c/character/anya", MARA = "campaign:c/character/mara", TAM = "campaign:c/character/tam";
const present = [{ actor: ANYA, name: "Anya", role: "player" }, { actor: MARA, name: "Mara", role: "npc" },
  { actor: TAM, name: "Tamsin", role: "npc" }];
const bid = (more) => ({ kind: "answer", addressees: [], audience: [], reason: "Because.", grounding: "", heard: "", beat: "", ...more });

test("a plan starts with the player speaking and nobody bidding", () => {
  assert.deepEqual(logic.blankState(present), { initiator: ANYA, addressees: [], floor: "", player_speaking: false, bids: {} });
  assert.equal(logic.defaultGrounding("silence"), "listening");
  assert.equal(logic.defaultGrounding("answer"), "addressed");
  assert.equal(logic.defaultGrounding("interject"), "heard");
});

test("a saved plan is read back with whatever no longer fits the cast dropped", () => {
  const saved = JSON.stringify({ initiator: "campaign:c/character/gone", addressees: [MARA, "campaign:c/character/gone"],
    floor: "campaign:c/character/gone", player_speaking: true,
    bids: { [MARA]: bid({ addressees: [ANYA, "x"] }), "campaign:c/character/gone": bid({}) } });
  const state = logic.readState(saved, present);
  assert.equal(state.initiator, ANYA);
  assert.deepEqual(state.addressees, [MARA]);
  assert.equal(state.floor, "");
  assert.equal(state.player_speaking, true);
  assert.deepEqual(Object.keys(state.bids), [MARA]);
  assert.deepEqual(state.bids[MARA].addressees, [ANYA]);
  assert.deepEqual(logic.readState("not json", present), logic.blankState(present));
  assert.deepEqual(logic.readState("", present), logic.blankState(present));
});

test("the form is the preview request, and each gap is named before anything is asked", () => {
  const state = { ...logic.blankState(present), addressees: [MARA], floor: MARA,
    bids: { [MARA]: bid({ addressees: [ANYA] }), [TAM]: bid({ kind: "aside", addressees: [MARA], audience: [MARA], grounding: "heard", heard: "2" }) } };
  const { request } = logic.requestOf(state, present, "campaign:c#chat/t");
  assert.equal(request.audience, "director");
  assert.equal(request.floor, MARA);
  assert.deepEqual(request.bids[0], { actor: MARA, kind: "answer", addressees: [ANYA], audience: [MARA, ANYA, TAM],
    reason: "Because.", grounding: "addressed" }, "no audience ticked: everyone here hears it");
  assert.deepEqual(request.bids[1].audience, [TAM, MARA], "a speaker and its addressees always hear it");
  assert.equal(request.bids[1].heard_index, 1, "the line a person counts from 1 is 051's index from 0");

  assert.equal(logic.requestOf({ ...state, bids: { [MARA]: bid({ reason: " " }) } }, present, "c").error, "Mara's bid needs its reason.");
  assert.match(logic.requestOf({ ...state, bids: { [TAM]: bid({ grounding: "heard", heard: "0" }) } }, present, "c").error, /which line it heard/);
  assert.match(logic.requestOf({ ...state, bids: { [TAM]: bid({ kind: "nonverbal", grounding: "listening" }) } }, present, "c").error, /needs its beat/);
  const beat = logic.requestOf({ ...state, bids: { [TAM]: bid({ kind: "nonverbal", grounding: "listening", beat: " shrugs " }) } }, present, "c");
  assert.equal(beat.request.bids[0].nonverbal, "shrugs");
  assert.deepEqual(logic.requestOf({ ...state, bids: { [MARA]: bid({ kind: "" }) } }, present, "c").request.bids, [], "no bid: not part of it");
});

test("a proposed exchange is said in plain sentences", () => {
  const anya = { actor: ANYA, name: "Anya" }, mara = { actor: MARA, name: "Mara" };
  assert.equal(logic.decisionText({ position: 1, name: "Mara", kind: "answer", addressees: [anya] }), "1. Mara answers, to Anya.");
  assert.equal(logic.decisionText({ name: "Tamsin", kind: "nonverbal", addressees: [], beat: "shrugs" }), "Tamsin makes a nonverbal beat: shrugs.");
  assert.equal(logic.heardText({ audience: [anya, mara] }), "Heard by Anya, Mara.");
  assert.equal(logic.viewText({ name: "Anya", sees: [] }), "Anya is aware of nothing in this exchange.");
  assert.equal(logic.viewText({ name: "Tamsin", sees: [{ name: "Mara", kind: "answer", reason: null }, { name: "Tamsin", kind: "aside", reason: "Mine." }] }),
    "Tamsin is aware of: Mara answers; Tamsin adds a side remark (its own reason: Mine.).");
});
