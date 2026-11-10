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
  factors: [{ id: "lantern", bearing: "hinders", text: "The lantern is lit." }],
  stakes: [{ id: "seen", text: "Anya is seen at the hatch." }],
  classification: "uncertain", allowed: ["failure", "partial", "success"], fingerprint: "f".repeat(64),
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

async function mounted(audience, shown, scene = null, routes = ["chat"]) {
  const calls = [];
  const drafts = new Map();  // the shell keeps a draft across re-renders and navigation
  const root = ui.el("div", {});
  const ctx = {
    ui, path: ["c1"], query: new URLSearchParams({ chat: CHAT, ...(audience === "director" ? { audience } : {}) }),
    href: (route, segments = [], query = {}) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/")
      + "?" + new URLSearchParams(Object.entries(query).filter(([, value]) => value !== undefined)).toString(),
    has: (op) => op === "open" && scene !== null, registered: (route) => routes.includes(route), setTitle() {}, setActions() {}, styles() {}, notify() {},
    draft: (key) => ({ get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api: async (op, args) => {
      calls.push([op, args]);
      if (op === "open") return scene;
      const now = typeof shown === "function" ? shown() : shown;
      return op === "campaign.hub" ? now : { done: op === "campaign.recover_effects" ? { settled: [] } : {}, hub: now };
    },
  };
  const notices = [];
  ctx.notify = (text) => notices.push(text);
  await page.mount(root, ctx);
  return { root, calls, notices };
}

const writes = (calls) => calls.filter(([op]) => op !== "campaign.hub");
const section = (root, title) => root.querySelectorAll("section").find((node) => node.querySelector("h2").textContent === title);

test("the director decides among every result the frame allows, citing what was shown", async () => {
  const { root, calls } = await mounted("director", hub("director"));
  const action = section(root, "Open action");
  assert.ok(byText(action, "strong", "Labelling this action is not available yet").length, "named, never a button");
  const result = action.querySelector("select");
  assert.deepEqual(result.querySelectorAll("option").map((node) => node.getAttribute("value")), ["failure", "partial", "success"]);
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
  assert.deepEqual(options, ["failure", "partial"]);
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

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const scene = { turns: [{ turn: "t1.000001", number: 1, kept: { lines: [] }, post: { text: "I press a coin into Mara's hand" } },
  { turn: "t1.000002", number: 2, kept: null, post: { text: "I slip past the lantern" } }] };

test("an effect is proposed for a kept turn by default, never for an action still undecided", async () => {
  const open = { ...hub("director").open_action, turn: "t1.000002" };
  const { root } = await mounted("director", hub("director", { open_action: open }), scene);
  const choice = section(root, "Propose an effect").querySelector("select");
  assert.deepEqual(choice.querySelectorAll("option").map((node) => node.getAttribute("value")), ["t1.000001"]);
  assert.equal(choice.value, "t1.000001");
});

test("a proposal being built survives another write, and the one made is cleared", async () => {
  const { root, calls } = await mounted("director", hub("director"), scene);
  const propose = () => section(root, "Propose an effect");
  byText(propose(), "button", "Add an operation")[0].click();
  const summary = propose().querySelector("textarea");
  summary.value = "Anya pays Mara a coin.";
  summary.dispatchEvent({ type: "input" });  // typed: the draft holds it
  byText(section(root, "Open action"), "button", "Decide")[0].click();  // another write: the hub is drawn again
  await tick();
  assert.equal(propose().querySelectorAll("div").filter((node) => node.className === "campaign-row").length, 1);
  assert.equal(propose().querySelector("textarea").value, "Anya pays Mara a coin.");
  byText(propose(), "button", "Propose").at(-1).click();
  await tick();
  const [op, args] = writes(calls).at(-1);
  assert.equal(op, "campaign.propose_effect");
  assert.equal(args.turn, "t1.000001");
  assert.equal(args.operations.length, 1);
  assert.equal(propose().querySelector("textarea").value, "", "the proposal made is not left to be sent twice");
  assert.equal(propose().querySelectorAll("div").filter((node) => node.className === "campaign-row").length, 0);
});

test("a cut-short acceptance is the director's to settle, and the page says what was settled", async () => {
  const problems = [{ code: "effect_unsettled", detail: "t1.000001:e-1:unpublished" }, { code: "effect_untagged", detail: "t1.000001:e-2" }];
  const table = await mounted("table", hub("table", { problems }));
  const shown = section(table.root, "Acceptance cut short");
  assert.ok(shown && !byText(shown, "button", "Settle").length, "the table is told, and offered nothing");
  const { root, calls, notices } = await mounted("director", hub("director", { problems }));
  const recover = section(root, "Acceptance cut short");
  const boxes = recover.querySelectorAll("input");
  assert.equal(boxes.length, 2, "the player's key and the owner's word on the untagged one, each asked apart");
  boxes[1].checked = true;
  byText(recover, "button", "Settle by what landed")[0].click();
  await tick();
  assert.deepEqual(writes(calls), [["campaign.recover_effects", { chat: CHAT, audience: "director", abort: false, player_key: false, own: true }]]);
  assert.deepEqual(notices, ["Nothing was settled."]);
});

test("a problem elsewhere in the campaign's continuity offers no recovery", async () => {
  const { root } = await mounted("director", hub("director", { problems: [{ code: "effects", detail: "receipt_mismatch:e-9" },
    { code: "effects_unreadable", detail: "x" }] }));
  assert.equal(section(root, "Acceptance cut short"), undefined);
});

test("with the review screen in the app, a character behind its source links to its source-update tab for this chat", async () => {
  const { root } = await mounted("table", hub("table"), null, ["chat", "import"]);
  const link = byText(section(root, "Sources"), "a", "Review the update")[0];
  assert.equal(link.getAttribute("href"), "#/import?" + new URLSearchParams({ tab: "rebase", chat: CHAT }).toString());
});

test("a holdings change is shown in full beside the endorsement: who gives, who gets, the counts and who acts", async () => {
  const anya = { ref: ANYA, name: "Anya" }, mara = { ref: MARA, name: "Mara" };
  const effect = { number: 1, state: "proposed", reason: null, summary: "Anya settles up.", proposed_by: "director",
    sequence: 1, operations: [], waivers: [], endorsers: [{ contribution: "t1.000001", principal: "human:owner", given: false }],
    holdings: { note: "the harbour fee", lines: [
      { kind: "move", holder: anya, item: "coin", amount: 3, seen: null, to: null, target: mara, agent: anya, backed_by: "t1.000001" },
      { kind: "adjust", holder: mara, item: "chit", amount: null, seen: 2, to: 0, target: null, agent: null, backed_by: null }] },
    approved_by: null, event: null, fingerprint: "e".repeat(64), accepted_on: null };
  const turns = [{ turn: "t1.000001", number: 1, open: false, kept: true, passed: false, post: null, labels: [], effects: [effect] }];
  const { root } = await mounted("table", hub("table", { turns, open_action: null }));
  const shown = section(root, "Turns and effects").textContent;
  assert.ok(shown.includes("move 3 coin from Anya to Mara (by Anya)"), shown);
  assert.ok(shown.includes("adjust Mara's chit from 2 to 0"), shown);
  assert.ok(shown.includes("Holdings note: the harbour fee"), shown);
});

test("a proposed labelling shows its factors and stakes beside the button that confirms it", async () => {
  const label = { label: "sneak", state: "proposed", reason: null, against: null,
    factors: [{ id: "lantern", bearing: "hinders", text: "The lantern is lit." }],
    stakes: [{ id: "seen", text: "Anya is seen at the hatch." }] };
  const open = { ...hub("table").open_action, label, framed: null };
  const { root } = await mounted("table", hub("table", { open_action: open }));
  const action = section(root, "Open action");
  assert.ok(byText(action, "button", "Confirm this labelling").length);
  assert.ok(action.textContent.includes("The lantern is lit.") && action.textContent.includes("hinders"), action.textContent);
  assert.ok(action.textContent.includes("Anya is seen at the hatch."), action.textContent);
});
