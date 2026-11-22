// Sprint 116's attitudes page (078) without the app: degrees by dimension, whom an attitude may point at, the form
// as a check request (each gap named) and the words a verdict is said in. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "attitudes", "logic.js")).href);

const MARA = "campaign:c/character/mara", ANYA = "campaign:c/character/anya", GUILD = "campaign:c/group/guild";
const cast = [{ actor: MARA, name: "Mara" }, { actor: ANYA, name: "Anya" }];
const groups = [{ actor: GUILD, name: "The guild" }];

test("trust and affection run from -3 to 3; the rest are intensities from 0", () => {
  assert.deepEqual(logic.degreeOptions("trust", ["trust", "affection"]).map((item) => item.value), ["", "-3", "-2", "-1", "0", "1", "2", "3"]);
  assert.equal(logic.degreeOptions("trust", ["trust", "affection"])[5].label, "+1");
  assert.deepEqual(logic.degreeOptions("resentment", ["trust", "affection"]).map((item) => item.value), ["", "0", "1", "2", "3"]);
});

test("an attitude points at another of the cast or a group, never its holder", () => {
  assert.deepEqual(logic.towardOptions(cast, groups, MARA).map((item) => item.value), ["", ANYA, GUILD]);
  assert.equal(logic.towardOptions(cast, groups, MARA)[2].label, "The guild (a group)");
});

test("the form is the check request, with the held attitudes, and each gap is named", () => {
  const form = { id: "a1", holder: MARA, toward: ANYA, dimension: "resentment", degree: "3", account: "  Resents it. ",
    evidence: ["heard"], beliefs: [] };
  const held = [{ id: "a0", holder: MARA, toward: GUILD, dimension: "rivalry", account: "x", evidence: ["heard"], beliefs: [] }];
  const { request } = logic.requestOf(form, { chat: "campaign:c#chat/t", held });
  assert.deepEqual(request, { chat: "campaign:c#chat/t", audience: "director", held,
    attitude: { id: "a1", holder: MARA, toward: ANYA, dimension: "resentment", account: "Resents it.", evidence: ["heard"], beliefs: [], degree: 3 } });
  assert.notEqual(request.held[0], held[0], "the held list is copied, never shared");
  assert.ok(!("degree" in logic.requestOf({ ...form, degree: "" }, { chat: "c", held: [] }).request.attitude));
  assert.equal(logic.requestOf({ ...form, holder: "" }, { chat: "c", held: [] }).error, "Choose whose attitude it is.");
  assert.equal(logic.requestOf({ ...form, toward: "" }, { chat: "c", held: [] }).error, "Choose whom the attitude is toward.");
  assert.equal(logic.requestOf({ ...form, dimension: "" }, { chat: "c", held: [] }).error, "Choose what the attitude is.");
  assert.equal(logic.requestOf({ ...form, account: " " }, { chat: "c", held: [] }).error, "Write the NPC's own reading of it.");
  assert.equal(logic.requestOf({ ...form, account: "x".repeat(401) }, { chat: "c", held: [] }).error, "The account is at most 400 characters.");
  assert.equal(logic.requestOf({ ...form, evidence: [] }, { chat: "c", held: [] }).error,
    "Pick what it rests on: the NPC's own evidence or beliefs.");
  assert.match(logic.newAttitudeId(), /^att-[a-z0-9]+-[a-z0-9]+$/);
});

test("a verdict is said with 078's reason kept", () => {
  assert.ok(logic.verdictText({ admitted: true }).startsWith("078 admits it"));
  assert.equal(logic.verdictText({ admitted: false, reason: "unavailable_grounds" }),
    "078 refuses it: it rests on something this NPC does not hold, or held only later (unavailable_grounds).");
  assert.equal(logic.verdictText({ admitted: false, reason: "brand_new" }), "078 refuses it: brand_new (brand_new).");
  assert.equal(logic.headText({ dimension: "trust", toward_name: "Anya", degree: -2, account: "Wary." }), "Trust toward Anya (-2): Wary.");
  assert.equal(logic.headText({ dimension: "affection", toward: ANYA, degree: 1, account: "Fond." }), "Affection toward " + ANYA + " (+1): Fond.");
  assert.equal(logic.headText({ dimension: "debt", toward_name: "Anya", degree: null, account: "Owes her." }), "Debt (feels it owes them) toward Anya: Owes her.");
});
