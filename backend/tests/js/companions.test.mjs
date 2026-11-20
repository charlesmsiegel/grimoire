// Sprint 115's companions page (050) without the app: the capture form as a request (each gap named before
// anything is asked), a fresh attempt id per review, and the sentences a capture and a companion are said in.
// Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "companions", "logic.js")).href);

const form = { trainer: "campaign:c/character/mara", creature: "campaign:c/creature/g1", outcome: "success",
  device: "ordinary-ball", capacity: "1", placement: "auto", turn: "t-1" };

test("the form is the capture request, and each gap is named first", () => {
  const { request } = logic.requestOf(form, { chat: "campaign:c#chat/t" });
  assert.deepEqual(request, { chat: "campaign:c#chat/t", audience: "director", trainer: form.trainer, creature: form.creature,
    outcome: "success", device: "ordinary-ball", capacity: 1, placement: "auto", turn: "t-1" });
  assert.equal(logic.requestOf({ ...form, device: "" }, { chat: "c" }).request.device, null, "no device: none is spent");
  assert.equal(logic.requestOf({ ...form, trainer: "" }, { chat: "c" }).error, "Choose the trainer.");
  assert.equal(logic.requestOf({ ...form, creature: "" }, { chat: "c" }).error, "Choose the creature.");
  assert.equal(logic.requestOf({ ...form, turn: "" }, { chat: "c" }).error, "Choose the turn it happened in.");
  for (const capacity of ["", "-1", "1.5", "1001", "two"]) {
    assert.match(logic.requestOf({ ...form, capacity }, { chat: "c" }).error, /whole number from 0 to 1000/, capacity);
  }
  assert.equal(logic.requestOf({ ...form, capacity: " 0 " }, { chat: "c" }).request.capacity, 0);
});

test("each review is a new attempt", () => {
  const one = logic.newAttemptId(), two = logic.newAttemptId();
  assert.match(one, /^capture-[a-z0-9]+-[a-z0-9]+$/);
  assert.notEqual(one, two);
});

test("a capture's review says what it does and spends", () => {
  const plan = { outcome: "success", placement: "party", device: "ordinary-ball", spends: 1, writes: true,
    trainer: { name: "Mara" }, creature: { name: "Snik", species: "Goblin" } };
  assert.equal(logic.planText(plan), "Mara captures Snik (Goblin): it joins Mara's active party. It spends one ordinary-ball from Mara's holdings.");
  assert.equal(logic.planText({ ...plan, placement: "reserve", spends: 0 }), "Mara captures Snik (Goblin): it joins Mara's reserve.");
  assert.equal(logic.planText({ ...plan, outcome: "failed" }), "Snik (Goblin) gets away: no companion and no party change. It spends one ordinary-ball from Mara's holdings.");
  assert.match(logic.planText({ ...plan, outcome: "declined", writes: false, spends: 0 }), /^Nothing is written: a capture not attempted/);
  assert.match(logic.planText({ ...plan, already_applied: true }), /already made: making it again changes nothing and spends nothing/);
});

test("a companion is its own individual, with its species and where it travels", () => {
  assert.equal(logic.companionText({ name: "Snik", species: "Goblin", placement: "active" }), "Snik · Goblin · active party");
  assert.equal(logic.companionText({ actor: "campaign:c/creature/g2", placement: "reserve" }), "campaign:c/creature/g2 · reserve");
});
