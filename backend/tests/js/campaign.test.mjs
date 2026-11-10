// Sprint 113's campaign page with a canned hub, in fakedom: what each view offers for an open action, and that
// every write names exactly what was shown (the frame's fingerprint, the effect's). Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { byText, install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
install("http://127.0.0.1:1");
const ui = await import(path.join(STATIC, "shell", "ui.js"));
const page = await import(path.join(STATIC, "pages", "campaign", "page.js"));

const CHAT = "campaign:c1#chat/t1";
const ANYA = "campaign:c1/character/anya", MARA = "campaign:c1/character/mara";
const FRAMED = {
  attempt: "t1.000001", actor: { ref: ANYA, name: "Anya" }, intent: "slip past the lantern", moment: { sequence: 1 },
  factors: [{ id: "lantern", bearing: "against", text: "The lantern is lit." }],
  stakes: [{ id: "seen", text: "Anya is seen at the hatch." }],
  classification: "uncertain", allowed: ["success", "partial", "failure"], fingerprint: "f".repeat(64),
};

function hub(audience, overrides = {}) {
  return {
    chat: CHAT, audience, director: "human:owner", player: "human:owner", persona: ANYA, paused: false, stage: null,
    cast: [{ actor: ANYA, name: "Anya", controller: "human:owner", role: "player", on_stage: true, absent: null },
      { actor: MARA, name: "Mara", controller: "director", role: "npc", on_stage: true, absent: null }],
    party: { present: null, memberships: [] }, holdings: {}, items: {}, abilities: {},
    sources: [{ role: "character", binding: "catalog:a/character/anya@r1", pinned: true, resolved: "catalog:a/character/anya@r1",
      broken: null, head: "catalog:a/character/anya@r2", problem: null, behind: true, actor: { ref: ANYA, name: "Anya" },
      notices: [{ code: "instance_source_drift", detail: "" }], handoff: { api: "review", op: "rebase.review", args: { actor: ANYA } } }],
    obstacles: [], turns: [], problems: [],
    open_action: { turn: "t1.000001", actor: { ref: ANYA, name: "Anya" },
      label: { label: "sneak", state: "confirmed", reason: null, factors: [], stakes: [], against: null }, framed: FRAMED, outcome: null },
    actions: [{ op: "label", available: false, reason: "no labelling model is configured for this app" },
      ...["confirm", "decide", "propose_effect", "endorse_effect", "approve_effect", "recover_effects"].map((op) => ({ op, available: true }))],
    unavailable: [{ feature: "revoke_effect", blocker: "035", reason: "not offered yet" }],
    ...(audience === "director" ? { truth: { events: [], revoked: [], relations: [], claims: [], claims_complete: true }, goals: [] } : {}),
    ...overrides,
  };
}

async function mounted(audience, shown) {
  const calls = [];
  const root = ui.el("div", {});
  const ctx = {
    ui, path: ["c1"], query: new URLSearchParams({ chat: CHAT, ...(audience === "director" ? { audience } : {}) }),
    href: (route, segments = [], query = {}) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/")
      + "?" + new URLSearchParams(Object.entries(query).filter(([, value]) => value !== undefined)).toString(),
    has: () => false, registered: (route) => route === "chat", setTitle() {}, setActions() {}, styles() {}, notify() {},
    draft: () => ({ get: () => "", set() {}, clear() {} }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api: async (op, args) => {
      calls.push([op, args]);
      return op === "campaign.hub" ? shown : { done: {}, hub: shown };
    },
  };
  await page.mount(root, ctx);
  return { root, calls };
}

const writes = (calls) => calls.filter(([op]) => op !== "campaign.hub");
const section = (root, title) => root.querySelectorAll("section").find((node) => node.querySelector("h2").textContent === title);

test("the director decides among every result the frame allows, citing what was shown", async () => {
  const { root, calls } = await mounted("director", hub("director"));
  const action = section(root, "Open action");
  assert.ok(byText(action, "strong", "Labelling this action is not available yet").length, "named, never a button");
  const result = action.querySelector("select");
  assert.deepEqual(result.querySelectorAll("option").map((node) => node.getAttribute("value")), ["success", "partial", "failure"]);
  result.value = "partial";
  action.querySelectorAll("input").find((node) => node.getAttribute("value") === "lantern").checked = true;
  byText(action, "button", "Decide")[0].click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(writes(calls), [["campaign.decide", { chat: CHAT, audience: "director", result: "partial", cites: ["lantern"],
    consequences: [], basis: "adjudicated" }]]);
  assert.ok(!byText(action, "button", "Consent").length, "the director gives no player's consent");
});

test("the acting player consents only to a setback, naming the frame's fingerprint, after saying what it decides", async () => {
  const { root, calls } = await mounted("table", hub("table"));
  const action = section(root, "Open action");
  const options = action.querySelector("select").querySelectorAll("option").map((node) => node.getAttribute("value"));
  assert.deepEqual(options, ["partial", "failure"]);
  action.querySelector("select").value = "failure";
  byText(action, "button", "Consent")[0].click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  const dialog = document.body.querySelector("dialog");
  assert.ok(dialog.textContent.includes("Consent to failure"), dialog.textContent);
  byText(dialog, "button", "Consent").at(-1).click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(writes(calls), [["campaign.decide", { chat: CHAT, audience: "table", result: "failure", cites: [], consequences: [],
    basis: "consent", fingerprint: FRAMED.fingerprint }]]);
});

test("a consent the person declines writes nothing", async () => {
  const { root, calls } = await mounted("table", hub("table"));
  byText(section(root, "Open action"), "button", "Consent")[0].click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  byText(document.body.querySelector("dialog"), "button", "Cancel")[0].click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(writes(calls), []);
});

test("a character behind its source hands off to the review screen, named as unavailable while it is not here", async () => {
  const { root } = await mounted("table", hub("table"));
  const sources = section(root, "Sources");
  assert.ok(sources.textContent.includes("pinned behind its source"));
  assert.ok(byText(sources, "strong", "Reviewing this rebase is not available yet").length);
  assert.ok(!byText(sources, "button", "Rebase").length, "the hub rebases nothing");
});

test("the endorsement names the effect's own fingerprint, and the table is never offered the director's key", async () => {
  const effect = { number: 1, state: "proposed", reason: null, summary: "Anya pays Mara a coin.", proposed_by: "director",
    sequence: 1, operations: [], holdings: null, waivers: [], endorsers: [{ contribution: "t1.000001", principal: "human:owner", given: false }],
    approved_by: null, event: null, fingerprint: "e".repeat(64), accepted_on: null };
  const turns = [{ turn: "t1.000001", number: 1, open: false, kept: true, passed: false, post: null, labels: [], effects: [effect] }];
  const { root, calls } = await mounted("table", hub("table", { turns, open_action: null }));
  const block = section(root, "Turns and effects");
  assert.ok(!byText(block, "button", "Approve").length);
  byText(block, "button", "Endorse as shown")[0].click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  byText(document.body.querySelector("dialog"), "button", "Endorse").at(-1).click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(writes(calls), [["campaign.endorse_effect", { chat: CHAT, audience: "table", turn: "t1.000001", number: 1,
    fingerprint: effect.fingerprint }]]);
});
