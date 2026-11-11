// Sprint 113's campaign page with canned answers, in fakedom: the list (its scenes in progress, search, filters
// and sorting), what each view of the hub offers for an open action, that every write names exactly what was
// shown (the frame's fingerprint, the effect's), and the page's pure logic. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { byText, install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
install("http://127.0.0.1:1");
const ui = await import(pathToFileURL(path.join(STATIC, "shell", "ui.js")).href);
const page = await import(pathToFileURL(path.join(STATIC, "pages", "campaign", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "campaign", "logic.js")).href);

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

const LISTED = { campaigns: [{ campaign: "c1", title: "Harbor", world: "world:quay", chats: [
  { chat: CHAT, label: "The lantern tide", name: "Mara", problem: null, last_activity: "2026-01-05T10:00:00Z",
    cast: [{ role: "character", ref: "card:mara/character/mara", name: "Mara" }] },
  { chat: "campaign:c1#chat/t0", label: null, name: "Mara", problem: "transcript_damaged", last_activity: "2026-01-02T10:00:00Z", cast: [] }] }],
complete: true };

function context(path_, query, api, { scene = null, routes = ["chat"] } = {}) {
  const drafts = new Map();  // the shell keeps a draft across re-renders and navigation
  const seen = { notices: [], went: [], counts: [], title: null, layout: null };
  const ctx = {
    ui, path: path_, query: new URLSearchParams(query),
    href: (route, segments = [], query_ = {}) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/")
      + "?" + new URLSearchParams(Object.entries(query_ || {}).filter(([, value]) => value !== undefined)).toString(),
    // an app with the inbox serves its feed and has its page: both, or neither
    has: (op) => (op === "open" && scene !== null) || (op === "inbox.list" && routes.includes("inbox")),
    registered: (route) => routes.includes(route),
    setTitle(text) { seen.title = text; }, setActions() {}, setCrumb() {}, styles() {}, layout(name) { seen.layout = name; },
    setCount(route, value) { seen.counts.push([route, value]); }, navigate(target) { seen.went.push(target); },
    notify: (text) => seen.notices.push(text),
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api,
  };
  return { ctx, seen };
}

async function mounted(audience, shown, scene = null, routes = ["chat"], knowledge = null, named = true) {
  const calls = [];
  const root = ui.el("div", {});
  const { ctx, seen } = context(["c1"], { ...(named ? { chat: CHAT } : {}), ...(audience === "director" ? { audience } : {}) }, async (op, args) => {
    calls.push([op, args]);
    if (op === "open") return scene;
    if (op === "campaign.chats") return LISTED;
    if (op === "campaign.knowledge") return knowledge ? knowledge(args) : { actors: [], table_events: [] };
    const now = typeof shown === "function" ? shown() : shown;
    return op === "campaign.hub" ? now : { done: op === "campaign.recover_effects" ? { settled: [] } : {}, hub: now };
  }, { scene, routes });
  await page.mount(root, ctx);
  return { root, calls, notices: seen.notices, seen };
}

const READS = new Set(["campaign.hub", "campaign.chats", "campaign.knowledge"]);
const writes = (calls) => calls.filter(([op]) => !READS.has(op));
// a card of the hub, by its name
const section = (root, title) => root.querySelector('section[aria-label="' + title + '"]') || undefined;
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("the director decides among every result the frame allows, citing what was shown", async () => {
  const { root, calls } = await mounted("director", hub("director"));
  const action = section(root, "Open action");
  assert.ok(action.classList.contains("g-accent"), "the open action is the card in accent");
  assert.ok(byText(action, "h2", "slip past the lantern").length, "the action is the card's title");
  assert.ok(byText(action, "span", "Labelled: sneak (confirmed)").length);
  assert.ok(byText(action, "strong", "Labelling this action is not available yet").length, "named, never a button");
  const result = action.querySelector("select");
  assert.deepEqual(result.querySelectorAll("option").map((node) => node.getAttribute("value")), ["failure", "partial", "success"]);
  result.value = "partial";
  action.querySelectorAll("input").find((node) => node.getAttribute("value") === "lantern").checked = true;
  byText(action, "button", "Decide")[0].click();
  await tick();
  assert.deepEqual(writes(calls), [["campaign.decide", { chat: CHAT, audience: "director", result: "partial", cites: ["lantern"],
    consequences: [], basis: "adjudicated" }]]);
  assert.ok(!byText(action, "button", "Consent").length, "the director gives no player's consent");
});

test("the acting player consents only to a setback, naming the frame's fingerprint, after saying what it decides", async () => {
  const { root, calls } = await mounted("table", hub("table"));
  const action = section(root, "Open action");
  const options = action.querySelector("select").querySelectorAll("option").map((node) => node.getAttribute("value"));
  assert.deepEqual(options, ["failure", "partial"]);
  assert.ok(!byText(action, "button", "Decide").length, "the table decides nothing as the director");
  action.querySelector("select").value = "failure";
  action.querySelectorAll("input").find((node) => node.getAttribute("value") === "seen").checked = true;
  byText(action, "button", "Consent")[0].click();
  await tick();
  const dialog = document.body.querySelector("dialog");
  assert.ok(dialog.textContent.includes("Consent to failure"), dialog.textContent);
  byText(dialog, "button", "Consent").at(-1).click();
  await tick();
  assert.deepEqual(writes(calls), [["campaign.decide", { chat: CHAT, audience: "table", result: "failure", cites: [], consequences: ["seen"],
    basis: "consent", fingerprint: FRAMED.fingerprint }]]);
});

test("a consent the person declines writes nothing", async () => {
  const { root, calls } = await mounted("table", hub("table"));
  byText(section(root, "Open action"), "button", "Consent")[0].click();
  await tick();
  byText(document.body.querySelector("dialog"), "button", "Cancel")[0].click();
  await tick();
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
  const block = section(root, "Changes waiting on approval");
  assert.ok(block.textContent.includes("Turn 1 · effect 1 · proposed"), block.textContent);
  assert.ok(block.textContent.includes("Your key: not given") && block.textContent.includes("Director: not yet"), block.textContent);
  assert.ok(!byText(root, "button", "Approve").length);
  assert.ok(section(root, "Turns and effects").textContent.includes("Effect 1 (proposed) is waiting on approval"), "said once, decided in one place");
  byText(block, "button", "Endorse as shown")[0].click();
  await tick();
  byText(document.body.querySelector("dialog"), "button", "Endorse").at(-1).click();
  await tick();
  assert.deepEqual(writes(calls), [["campaign.endorse_effect", { chat: CHAT, audience: "table", turn: "t1.000001", number: 1,
    fingerprint: effect.fingerprint }]]);
});

test("the director's view approves a waiting change and is never offered the player's key", async () => {
  const effect = { number: 1, state: "proposed", reason: null, summary: "Anya pays Mara a coin.", proposed_by: "director",
    sequence: 1, operations: [], holdings: null, waivers: [], endorsers: [{ contribution: "t1.000001", principal: "human:owner", given: true }],
    approved_by: null, event: null, fingerprint: "e".repeat(64), accepted_on: null };
  const accepted = { ...effect, number: 2, state: "accepted", approved_by: "human:owner", summary: "Mara pockets it." };
  const turns = [{ turn: "t1.000001", number: 1, open: false, kept: true, passed: false, post: null, labels: [], effects: [effect, accepted], intentions: [] }];
  const { root, calls } = await mounted("director", hub("director", { turns, open_action: null }));
  const block = section(root, "Changes waiting on approval");
  assert.ok(!block.textContent.includes("Mara pockets it."), "an accepted effect waits on nothing");
  assert.ok(byText(section(root, "Turns and effects"), "strong", "Effect 2 (accepted)").length);
  assert.ok(!byText(root, "button", "Endorse").length);
  byText(block, "button", "Approve and accept")[0].click();
  await tick();
  byText(document.body.querySelector("dialog"), "button", "Approve").at(-1).click();
  await tick();
  assert.deepEqual(writes(calls), [["campaign.approve_effect", { chat: CHAT, audience: "director", turn: "t1.000001", number: 1 }]]);
});

const scene = { turns: [{ turn: "t1.000001", number: 1, kept: { lines: [] }, post: { text: "I press a coin into Mara's hand" } },
  { turn: "t1.000002", number: 2, kept: null, post: { text: "I slip past the lantern" } }] };

test("an effect is proposed for a kept turn by default, never for an action still undecided", async () => {
  const open = { ...hub("director").open_action, turn: "t1.000002" };
  const { root } = await mounted("director", hub("director", { open_action: open }), scene);
  const choice = section(root, "Propose an effect").querySelector("select");
  assert.deepEqual(choice.querySelectorAll("option").map((node) => node.getAttribute("value")), ["t1.000001"]);
  assert.equal(choice.value, "t1.000001");
  assert.ok(section(root, "Open action").textContent.includes("Open action · turn 2"), "the turn is named by its number where it is known");
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
  const card = section(root, "Changes waiting on approval");
  const shown = card.textContent;
  assert.ok(byText(card, "button", "Endorse as shown").length, "beside the key it is given with");
  assert.ok(shown.includes("move 3 coin from Anya to Mara (by Anya)"), shown);
  assert.ok(shown.includes("adjust Mara's chit from 2 to 0"), shown);
  assert.ok(shown.includes("Holdings note: the harbour fee"), shown);
});

test("a proposed labelling shows its factors and stakes beside the button that confirms it", async () => {
  const label = { label: "sneak", state: "proposed", reason: null, against: null,
    factors: [{ id: "lantern", bearing: "hinders", text: "The lantern is lit." }],
    stakes: [{ id: "seen", text: "Anya is seen at the hatch." }] };
  const open = { ...hub("table").open_action, label, framed: null };
  const { root, calls } = await mounted("table", hub("table", { open_action: open }));
  const action = section(root, "Open action");
  assert.ok(action.textContent.includes("What bears on it") && action.textContent.includes("At stake"));
  assert.ok(action.textContent.includes("The lantern is lit.") && action.textContent.includes("hinders"), action.textContent);
  assert.ok(action.textContent.includes("Anya is seen at the hatch."), action.textContent);
  assert.equal(action.querySelectorAll("input").length, 0, "nothing is ticked before there is a frame to decide");
  byText(action, "button", "Confirm this labelling")[0].click();
  await tick();
  assert.deepEqual(writes(calls), [["campaign.confirm", { chat: CHAT, audience: "table" }]]);
  // the director's view waits on the table for it, and is not offered the table's confirmation
  const director = await mounted("director", hub("director", { open_action: open }));
  assert.ok(!byText(section(director.root, "Open action"), "button", "Confirm").length);
});

test("with an inbox, the table's view links to the item to decide there, and the director's deciding stays", async () => {
  const label = { label: "sneak", state: "proposed", reason: null, against: null,
    factors: [{ id: "lantern", bearing: "hinders", text: "The lantern is lit." }], stakes: [] };
  const effect = { number: 1, state: "proposed", reason: null, summary: "Anya pays Mara a coin.", proposed_by: "director",
    sequence: 1, operations: [], holdings: null, waivers: [], endorsers: [{ contribution: "t0.000001", principal: "human:owner", given: false }],
    approved_by: null, event: null, fingerprint: "e".repeat(64), accepted_on: null };
  const turns = [{ turn: "t0.000001", number: 1, open: false, kept: true, passed: false, post: null, labels: [], effects: [effect], intentions: [] }];
  const waiting = { open_action: { ...hub("table").open_action, label, framed: null }, turns };
  const item = (id) => "#/inbox?" + new URLSearchParams({ item: id, chat: CHAT }).toString();  // the link names the chat too
  const { root, calls } = await mounted("table", hub("table", waiting), null, ["chat", "inbox"]);
  // what confirming locks in is still read here; the confirming itself is the inbox's
  const action = section(root, "Open action");
  assert.ok(action.textContent.includes("Waiting on you to confirm how this was read.") && action.textContent.includes("The lantern is lit."));
  assert.ok(!byText(action, "button", "Confirm").length, "nothing is confirmed in place");
  const confirm = byText(action, "a", "Decide in Needs you")[0];
  assert.equal(confirm.getAttribute("href"), item("label:" + CHAT + ":t1.000001"));
  assert.ok(confirm.classList.contains("g-primary"));
  // the change is shown in full, with the same link in place of the endorsement
  const block = section(root, "Changes waiting on approval");
  assert.ok(block.textContent.includes("Your key: not given"));
  assert.ok(!byText(block, "button", "Endorse").length);
  assert.equal(byText(block, "a", "Decide in Needs you")[0].getAttribute("href"), item("effect:" + CHAT + ":t0.000001:1"));
  assert.deepEqual(writes(calls), [], "the table's view writes nothing");

  // the director's view links to no inbox item: its own keys and decisions stay where they are
  const director = await mounted("director", hub("director", { ...waiting, turns: [{ ...turns[0], effects: [{ ...effect, endorsers: [{ ...effect.endorsers[0], given: true }] }] }] }),
    null, ["chat", "inbox"]);
  assert.ok(!byText(director.root, "a", "Decide in Needs you").length);
  assert.ok(byText(section(director.root, "Changes waiting on approval"), "button", "Approve and accept").length);
  const framed = await mounted("director", hub("director"), null, ["chat", "inbox"]);
  assert.ok(byText(section(framed.root, "Open action"), "button", "Decide").length, "the director decides in place");

  // a change the hub says cannot be endorsed now is named as unavailable, never linked
  const paused = hub("table", { ...waiting, actions: hub("table").actions.map((entry) => (entry.op === "endorse_effect" || entry.op === "confirm"
    ? { op: entry.op, available: false, reason: "play is paused" } : entry)) });
  const held = await mounted("table", paused, null, ["chat", "inbox"]);
  assert.ok(!byText(held.root, "a", "Decide in Needs you").length);
  assert.ok(byText(held.root, "strong", "Endorsing this effect is not available yet").length);
});

// -- the hub's frame: cover, chats, views --------------------------------------------------------------------

test("the hub opens under its campaign's cover, with its chats as chips and the two views as segments", async () => {
  const { root, seen } = await mounted("table", hub("table"));
  assert.equal(seen.title, "Harbor");
  assert.equal(seen.layout, "page");
  const cover = root.querySelector("div.g-cover");
  assert.ok(cover.textContent.includes("Harbor") && cover.textContent.includes("world:quay · 2 chats"), cover.textContent);
  assert.ok(!byText(cover, "button", "Change cover").length, "no cover can be set yet, so none is offered");
  const chips = root.querySelectorAll("a.campaign-chip");
  assert.deepEqual(chips.map((node) => node.getAttribute("aria-current")), ["page", null]);
  assert.ok(chips[0].textContent.includes("The lantern tide"));
  assert.ok(chips[1].textContent.includes("transcript_damaged"), "a chat's problem is said on its chip");
  assert.equal(chips[1].getAttribute("href"), "#/campaign/c1?" + new URLSearchParams({ chat: "campaign:c1#chat/t0" }).toString());
  assert.equal(byText(root, "a", "Open the chat")[0].getAttribute("href"), "#/chat/" + encodeURIComponent(CHAT));
  const views = root.querySelectorAll("button.g-segment");
  assert.deepEqual(views.slice(0, 2).map((node) => [node.textContent, node.getAttribute("aria-pressed")]), [["Table view", "true"], ["Director view", "false"]]);
  views[1].click();
  assert.deepEqual(seen.went, ["#/campaign/c1?" + new URLSearchParams({ chat: CHAT, audience: "director" }).toString()]);
});

test("a campaign opened with no chat named shows the hub of the chat played last", async () => {
  const { root, calls } = await mounted("table", hub("table"), null, ["chat"], null, false);
  assert.deepEqual(calls.find(([op]) => op === "campaign.hub")[1], { chat: CHAT, audience: "table" });
  assert.ok(section(root, "Cast and stage"));
});

test("an address naming a chat of another campaign opens no hub", async () => {
  const calls = [];
  const root = ui.el("div", {});
  const { ctx } = context(["c1"], { chat: "campaign:c2#chat/t9" }, async (op) => { calls.push(op); return {}; });
  await page.mount(root, ctx);
  assert.deepEqual(calls, []);
  assert.ok(root.textContent.includes("not in the campaign it names"));
});

// -- who sees what -----------------------------------------------------------------------------------------

test("the table's view shows neither world truth, NPC goals nor a proposal, even from a hub that carries them", async () => {
  const leaked = hub("table", { truth: { events: [{ summary: "The hold is empty.", moment: { sequence: 1 }, event: "e1" }], revoked: [], relations: [],
    claims: [], claims_complete: true }, goals: [{ actor: { ref: MARA, name: "Mara" }, usable: true, problem: null, goals: [], limits: [] }] });
  const { root, calls } = await mounted("table", leaked, scene);
  for (const title of ["World truth", "NPC goals", "Propose an effect"]) assert.equal(section(root, title), undefined, title);
  assert.ok(!root.textContent.includes("The hold is empty."));
  assert.ok(!calls.some(([op]) => op === "open"), "the table's view does not read the transcript's turns");
  // nor does a director's address show them over a hub answered for the table
  const mixed = await mounted("director", leaked, scene);
  for (const title of ["World truth", "NPC goals", "Propose an effect"]) assert.equal(section(mixed.root, title), undefined, title);
  assert.ok(!byText(mixed.root, "button", "Decide").length && !byText(mixed.root, "button", "Approve").length);
});

test("the director's view adds world truth and NPC goals, each marked as the director's alone", async () => {
  const truth = { events: [{ summary: "The hold is empty.", moment: { sequence: 1 }, event: "e1" }], revoked: [], relations: [],
    claims: [{ standing: "refuted", text: "The hold carries salt.", contested: true, held: [{ holder: { ref: MARA, name: "Mara" }, stance: "believed" }] }],
    claims_complete: true };
  const goals = [{ actor: { ref: MARA, name: "Mara" }, usable: true, problem: null,
    goals: [{ aim: "Be paid before the tide", priority: 1, approaches: [] }], limits: [{ rule: "leave the hatch open", when: [] }] }];
  const { root } = await mounted("director", hub("director", { truth, goals }));
  const world = section(root, "World truth");
  assert.ok(world.classList.contains("campaign-private") && section(root, "NPC goals").classList.contains("campaign-private"));
  assert.ok(world.textContent.includes("The hold is empty.") && world.textContent.includes("Mara believed it (their belief)"), world.textContent);
  assert.ok(section(root, "NPC goals").textContent.includes("Never: leave the hatch open"));
  assert.ok(!section(root, "Cast and stage").classList.contains("campaign-private"));
});

test("knowledge is one perspective at a time: the table reads its own characters', the director anyone's", async () => {
  const known = (args) => ({ actors: [{ actor: { ref: args.actor || ANYA, name: args.actor === MARA ? "Mara" : "Anya" },
    beliefs: [{ stance: "believed", text: args.actor ? "The hatch is open." : "The lantern is lit." }], observations: [], sent: [], inherited: null }],
  table_events: args.actor ? [] : [{ summary: "Anya paid Mara." }] });
  const table = await mounted("table", hub("table"), null, ["chat"], known);
  await tick();
  const card = section(table.root, "Knowledge");
  assert.deepEqual(card.querySelectorAll("button.g-segment").map((node) => node.textContent), ["The table", "Anya"],
    "another player's or the director's character is not offered at the table");
  assert.ok(card.textContent.includes("None of it is world truth"));
  assert.ok(card.textContent.includes("Anya believed: The lantern is lit.") && card.textContent.includes("Anya paid Mara."), card.textContent);
  byText(card, "button", "Anya")[0].click();
  await tick();
  assert.deepEqual(table.calls.at(-1), ["campaign.knowledge", { chat: CHAT, audience: "table", actor: ANYA }]);
  assert.ok(byText(card, "h3", "Anya's perspective").length && card.textContent.includes("The hatch is open."));
  assert.ok(!card.textContent.includes("The lantern is lit."), "one perspective replaces the other");

  const director = await mounted("director", hub("director"), null, ["chat"], known);
  const theirs = section(director.root, "Knowledge");
  assert.deepEqual(theirs.querySelectorAll("button.g-segment").map((node) => node.textContent), ["The table", "Anya", "Mara"]);
  byText(theirs, "button", "Mara")[0].click();
  await tick();
  assert.deepEqual(director.calls.at(-1), ["campaign.knowledge", { chat: CHAT, audience: "director", actor: MARA }]);
  assert.ok(byText(theirs, "h3", "Mara's perspective").length);
});

test("a perspective the hub refuses is said in the card, and the rest of the hub stands", async () => {
  const refuse = () => { throw Object.assign(new Error("this player controls no character in the campaign"), { reason: "not_authorized" }); };
  const { root } = await mounted("table", hub("table"), null, ["chat"], refuse);
  await tick();
  assert.ok(section(root, "Knowledge").textContent.includes("not_authorized"), section(root, "Knowledge").textContent);
  assert.ok(section(root, "Cast and stage") && section(root, "Open action"));
});

test("cast and stage, holdings and powers and obstacles each have their card", async () => {
  const stage = { areas: [{ id: "deck", label: "The deck" }, { id: "hold", label: "The hold" }],
    placements: [{ who: { ref: ANYA, name: "Anya" }, area: "deck", holder: null, basis: "placed" }], ways: [{ sides: ["deck", "hold"], state: "barred" }] };
  const obstacles = [{ obstacle: "hold", goal: "Get into the cargo hold.", status: "open", problem: null,
    approaches: [{ id: "bribe", text: "Pay Mara to look away.", risks: ["debt"], costs: [{ id: "c", text: "coin × 5" }] }], facts: [], attempts: [], due: [] }];
  const { root } = await mounted("table", hub("table", { stage, obstacles, holdings: { [ANYA]: { counts: { coin: 4 } } },
    items: { [ANYA]: { held: [{ item: "i1", name: "A brass key" }], equipped: {} } },
    abilities: { [ANYA]: [{ ability: "a1", name: "Quiet step", description: "Moves unheard.", effects: [], limits: [], costs: [], prerequisites: [] }] },
    party: { present: null, memberships: [{ member: { ref: ANYA, name: "Anya" }, party: { ref: "p", name: "The crew" }, status: "active", since: 1 }] } }));
  const cast = section(root, "Cast and stage");
  assert.deepEqual(cast.querySelectorAll("span.campaign-name").map((node) => node.textContent), ["Anya", "Mara"]);
  assert.ok(byText(cast, "span", "your character · persona").length && byText(cast, "span", "the director's").length);
  assert.ok(cast.textContent.includes("active in The crew since 1") && cast.textContent.includes("in The deck (placed)")
    && cast.textContent.includes("The deck to The hold"), cast.textContent);
  const holdings = section(root, "Holdings and powers").textContent;
  assert.ok(holdings.includes("coin × 4") && holdings.includes("A brass key") && holdings.includes("Quiet step") && holdings.includes("Moves unheard."), holdings);
  const shown = section(root, "Obstacles").textContent;
  assert.ok(shown.includes("Get into the cargo hold.") && shown.includes("risks debt") && shown.includes("costs coin × 5"), shown);
  assert.ok(byText(section(root, "Not available yet"), "strong", "Revoke effect is not available yet").length);
});

// -- the list ------------------------------------------------------------------------------------------------

const item = (campaign, title, at, extra = {}) => ({ campaign, title, world: extra.world || null, chats: [
  { chat: "campaign:" + campaign + "#chat/t1", label: extra.label || null, name: extra.name || "Mara", problem: null, last_activity: at,
    cast: [{ role: "character", ref: "card:" + campaign + "/character/mara", name: extra.name || "Mara" }] }, ...(extra.more || [])] });
const older = (campaign, n) => ({ chat: "campaign:" + campaign + "#chat/o" + n, label: null, name: "Mara", problem: null, last_activity: "2025-12-0" + n + "T00:00:00Z", cast: [] });
const CAMPAIGNS = [item("ash", "Ashfall", "2026-01-03T10:00:00Z", { more: [older("ash", 1), older("ash", 2)] }),
  item("brine", "Brine", "2026-01-05T10:00:00Z", { world: "world:quay", label: "The chart room" }),
  item("cinder", null, "2026-01-04T10:00:00Z", { name: "Oren" })];
const quiet = (overrides = {}) => hub("table", { open_action: null, ...overrides });
const HUBS = { ash: quiet({ paused: true }), brine: hub("table"),
  cinder: quiet({ open_action: { ...hub("table").open_action, framed: null, label: { label: "bribe", state: "proposed", reason: null, factors: [], stakes: [], against: null } } }) };

async function listed(campaigns = CAMPAIGNS, hubs = HUBS, complete = true, gate = null) {
  const calls = [];
  const root = ui.el("div", {});
  const { ctx, seen } = context([], {}, async (op, args) => {
    calls.push([op, args]);
    if (op === "campaign.chats") return { campaigns, complete };
    await gate;  // a test may hold the hubs back
    const found = hubs[args.chat.slice("campaign:".length).split("#")[0]];
    if (!found) throw Object.assign(new Error("unreadable"), { reason: "chat_unreadable" });
    return found;
  });
  await page.mount(root, ctx);
  const names = () => root.querySelectorAll("a.campaign-rowlink").map((node) => node.textContent);
  return { root, calls, seen, names };
}
const settle = async () => { for (let n = 0; n < 6; n += 1) await tick(); };

test("the list counts the campaigns for the rail and shows them newest first, before any hub has answered", async () => {
  let release;
  const { root, seen, names } = await listed(CAMPAIGNS, HUBS, true, new Promise((resolve) => { release = resolve; }));
  assert.deepEqual(seen.counts, [["campaign", { value: 3, label: "3 campaigns" }]]);
  assert.equal(seen.layout, "page");
  assert.deepEqual(names(), ["Brine", "cinder", "Ashfall"], "a campaign with no title goes by its id");
  assert.deepEqual(root.querySelectorAll("th").map((node) => node.textContent.replace(/[▲▼]/g, "")), ["Campaign", "Last interaction", "Chats", "Cast"],
    "who a campaign waits on is not a column until a hub says so");
  assert.equal(root.querySelectorAll("button.g-segment").length, 0, "no filter is offered on what is not known yet");
  assert.equal(root.querySelector("a.campaign-rowlink").getAttribute("href"), "#/campaign/brine?");
  assert.equal(root.querySelector('section[aria-label="Scenes in progress"]'), null);
  release();
  await settle();
  assert.ok(root.querySelector('section[aria-label="Scenes in progress"]'), "the hubs' answers are drawn when they come");
  assert.ok(root.querySelectorAll("th").some((node) => node.textContent.startsWith("Waiting on")));
});

test("scenes in progress are the campaigns whose newest chat has something open, your turn first", async () => {
  const { root, calls } = await listed();
  await settle();
  assert.deepEqual(calls.filter(([op]) => op === "campaign.hub").map(([, args]) => args).sort((a, b) => a.chat.localeCompare(b.chat)),
    ["ash", "brine", "cinder"].map((id) => ({ chat: "campaign:" + id + "#chat/t1" })), "each asked as the table, never as the director");
  const scenes = root.querySelector('section[aria-label="Scenes in progress"]');
  const cards = scenes.querySelectorAll("article");
  assert.deepEqual(cards.map((node) => node.getAttribute("aria-label")), ["cinder", "Brine"], "what waits on you comes before what was played last");
  assert.ok(cards[0].textContent.includes("Your turn") && cards[0].textContent.includes("Confirm how the action was read"), cards[0].textContent);
  assert.ok(cards[1].textContent.includes("The chart room") && cards[1].textContent.includes("slip past the lantern")
    && cards[1].textContent.includes("Waiting on the director"), cards[1].textContent);
  assert.equal(byText(cards[1], "a", "Play")[0].getAttribute("href"), "#/chat/" + encodeURIComponent("campaign:brine#chat/t1"));
  assert.equal(byText(cards[1], "a", "Hub")[0].getAttribute("href"), "#/campaign/brine");
  assert.ok(scenes.textContent.includes("2 campaigns have a scene in progress"));
});

test("once every campaign is checked the list filters on Needs you and Paused, and a search narrows both parts", async () => {
  const { root, names } = await listed();
  await settle();
  const segments = () => root.querySelectorAll("button.g-segment");
  assert.deepEqual(segments().map((node) => node.textContent), ["All 3", "Needs you 1", "Paused 1"]);
  assert.ok(byText(root, "span", "Paused").some((node) => node.classList.contains("g-pill")), "a paused campaign says so in its row");
  segments()[1].click();
  assert.deepEqual(names(), ["cinder"]);
  assert.equal(segments()[1].getAttribute("aria-pressed"), "true");
  segments()[2].click();
  assert.deepEqual(names(), ["Ashfall"]);
  assert.equal(root.querySelector('section[aria-label="Scenes in progress"]'), null, "a paused campaign with nothing open has no scene");
  segments()[0].click();
  const search = root.querySelector("input");
  assert.equal(search.getAttribute("aria-label"), "Search campaigns and worlds");
  search.value = "QUAY";
  search.dispatchEvent({ type: "input" });
  assert.deepEqual(names(), ["Brine"], "a world is searched too");
  assert.deepEqual(root.querySelectorAll("article").map((node) => node.getAttribute("aria-label")), ["Brine"]);
  assert.ok(root.textContent.includes("1 of 3 campaigns"));
  search.value = "oren";
  search.dispatchEvent({ type: "input" });
  assert.deepEqual(names(), ["cinder"], "and a campaign's cast");
  search.value = "nothing like it";
  search.dispatchEvent({ type: "input" });
  assert.ok(root.textContent.includes("No campaign matches."));
  assert.equal(root.querySelector("table"), null);
});

test("a column header sorts the table, turns round on a second click and says its direction", async () => {
  const { root, names } = await listed();
  await settle();
  const header = (label) => root.querySelectorAll("th").find((node) => node.textContent.startsWith(label));
  const sortBy = (label) => header(label).querySelector("button.g-sort").click();
  assert.equal(header("Last interaction").getAttribute("aria-sort"), "descending");
  assert.ok(header("Last interaction").textContent.includes("▼"));
  assert.equal(header("Campaign").getAttribute("aria-sort"), "none");
  assert.equal(header("Cast").getAttribute("aria-sort"), null, "a column that does not sort has no button and no direction");
  assert.equal(header("Cast").querySelector("button"), null);
  sortBy("Campaign");
  assert.deepEqual(names(), ["Ashfall", "Brine", "cinder"]);
  assert.equal(header("Campaign").getAttribute("aria-sort"), "ascending");
  assert.ok(header("Campaign").textContent.includes("▲") && !header("Last interaction").textContent.includes("▼"));
  assert.equal(document.activeElement, header("Campaign").querySelector("button"), "the keyboard stays on the column sorted");
  sortBy("Campaign");
  assert.deepEqual(names(), ["cinder", "Brine", "Ashfall"]);
  assert.equal(header("Campaign").getAttribute("aria-sort"), "descending");
  sortBy("Chats");
  assert.deepEqual(names(), ["Ashfall", "Brine", "cinder"], "most chats first, then by name");
  sortBy("Waiting on");
  assert.deepEqual(names(), ["cinder", "Brine", "Ashfall"], "what waits on you, then on the director, then nothing");
  sortBy("Last interaction");
  assert.deepEqual(names(), ["Brine", "cinder", "Ashfall"]);
});

test("a campaign whose hub cannot be read stays in the list, with no scene", async () => {
  const { root, names } = await listed(CAMPAIGNS, { brine: HUBS.brine });
  await settle();
  assert.deepEqual(names(), ["Brine", "cinder", "Ashfall"]);
  assert.deepEqual(root.querySelectorAll("article").map((node) => node.getAttribute("aria-label")), ["Brine"]);
});

test("an empty library says so and clears the count; a list cut short says so and offers no filter that would miscount", async () => {
  const none = await listed([], {});
  assert.ok(none.root.textContent.includes("No campaign has a chat in this library yet."));
  assert.deepEqual(none.seen.counts, [["campaign", null]]);
  const cut = await listed(CAMPAIGNS, HUBS, false);
  await settle();
  assert.ok(cut.root.textContent.includes("Only the campaigns of the most recent chats are listed."));
  assert.equal(cut.root.querySelectorAll("button.g-segment").length, 0);
  assert.ok(cut.root.querySelector('section[aria-label="Scenes in progress"]'), "what was checked is still shown");
});

// -- the pure logic ------------------------------------------------------------------------------------------

test("a row gathers a campaign's chats: its newest, its last activity, its cast once each", () => {
  const row = logic.summarize({ campaign: "c1", title: null, world: "world:quay", chats: [
    { chat: "a", label: "Second night", name: "Mara", problem: "x", last_activity: "2026-01-02T00:00:00Z", cast: [{ ref: "r1", name: "Mara" }, { ref: "r2", name: null }] },
    { chat: "b", label: null, name: "Mara", problem: null, last_activity: "2026-01-03T00:00:00Z", cast: [{ ref: "r1", name: "Mara" }] }] });
  assert.deepEqual([row.name, row.chats, row.last, row.newest.chat, row.problems], ["c1", 2, "2026-01-03T00:00:00Z", "a", 1]);
  assert.deepEqual(row.cast, [{ name: "Mara", key: "r1" }, { name: "r2", key: "r2" }]);
  assert.deepEqual(logic.summarize({ campaign: "c2", chats: [] }).newest, null);
});

test("who a scene waits on is read from the table's hub alone", () => {
  const effect = (given, approved) => ({ state: approved ? "approved" : "proposed", endorsers: [{ principal: "human:owner", given }], approved_by: approved });
  const turn = (...effects) => ({ turns: [{ turn: "t", number: 1, effects }] });
  assert.deepEqual(logic.waiting(quiet()), { who: null, text: "" });
  assert.equal(logic.waiting(null).who, null);
  assert.equal(logic.waiting(hub("table")).who, "director");
  assert.equal(logic.waiting(hub("table", { open_action: { ...hub("table").open_action, outcome: { result: "success" } } })).who, null, "a decided action waits on no one");
  assert.equal(logic.waiting(HUBS.cinder).who, "you");
  assert.equal(logic.waiting(quiet({ open_action: { turn: "t", actor: null, label: null, framed: null, outcome: null } })).who, "labelling");
  assert.deepEqual(logic.waiting(quiet(turn(effect(false, null)))), { who: "you", text: "1 change waiting on your key" });
  assert.deepEqual(logic.waiting(quiet(turn(effect(true, null), effect(true, null)))), { who: "director", text: "2 changes waiting on the director" });
  assert.equal(logic.waiting(quiet(turn({ state: "accepted", endorsers: [], approved_by: "x" }))).who, null);
  assert.equal(logic.waiting({ ...hub("table"), ...turn(effect(false, null)) }).who, "you", "your key comes before the director's decision");
  assert.deepEqual(logic.situation(hub("table", { paused: true })), { open: true, who: "director", waiting: "The director's decision", paused: true,
    actor: "Anya", line: "slip past the lantern" });
  assert.equal(logic.situation(null), null);
});

test("sorting is stable by name, and each column starts in its own direction", () => {
  const rows = [{ campaign: "b", name: "beta", last: "2", chats: 1 }, { campaign: "a", name: "Alpha", last: "2", chats: 3 }, { campaign: "c", name: "gamma", last: "1", chats: 3 }];
  const at = new Map([["c", { who: "you" }], ["a", { who: null }]]);
  const order = (sort) => logic.sorted(rows, at, sort).map((row) => row.campaign);
  assert.deepEqual(order(logic.DEFAULT_SORT), ["a", "b", "c"]);
  assert.deepEqual(order({ column: "last", direction: "ascending" }), ["c", "a", "b"]);
  assert.deepEqual(order({ column: "name", direction: "descending" }), ["c", "b", "a"]);
  assert.deepEqual(order({ column: "chats", direction: "descending" }), ["a", "c", "b"]);
  assert.deepEqual(order({ column: "waiting", direction: "descending" }), ["c", "a", "b"]);
  assert.deepEqual(order({ column: "cast", direction: "ascending" }), ["a", "b", "c"], "an unknown column leaves the order by name");
  assert.deepEqual(rows.map((row) => row.campaign), ["b", "a", "c"], "the rows given are not reordered");
  const [name, last, chats, cast] = logic.columns(false);
  assert.deepEqual(logic.columns(true).map((column) => column.id), ["name", "last", "waiting", "chats", "cast"]);
  assert.deepEqual(logic.nextSort(logic.DEFAULT_SORT, name), { column: "name", direction: "ascending" });
  assert.deepEqual(logic.nextSort(logic.DEFAULT_SORT, last), { column: "last", direction: "ascending" });
  assert.deepEqual(logic.nextSort({ column: "name", direction: "ascending" }, chats), { column: "chats", direction: "descending" });
  assert.deepEqual(logic.nextSort(logic.DEFAULT_SORT, cast), logic.DEFAULT_SORT, "a column that does not sort changes nothing");
  assert.deepEqual([logic.ariaSort(logic.DEFAULT_SORT, last), logic.ariaSort(logic.DEFAULT_SORT, name)], ["descending", "none"]);
  assert.deepEqual([logic.arrow(logic.DEFAULT_SORT, last), logic.arrow({ column: "last", direction: "ascending" }, last), logic.arrow(logic.DEFAULT_SORT, name)], ["▼", "▲", ""]);
});

test("the filters count only what every campaign was checked for", () => {
  const rows = CAMPAIGNS.map(logic.summarize);
  const at = new Map(Object.entries(HUBS).map(([id, value]) => [id, logic.situation(value)]));
  assert.deepEqual(logic.filters(rows, at, false), [{ value: "all", label: "All 3" }]);
  assert.deepEqual(logic.filters(rows, at, true).map((entry) => entry.label), ["All 3", "Needs you 1", "Paused 1"]);
  assert.deepEqual(logic.narrowed(rows, at, { filter: "needs" }).map((row) => row.campaign), ["cinder"]);
  assert.deepEqual(logic.narrowed(rows, at, { filter: "paused" }).map((row) => row.campaign), ["ash"]);
  assert.deepEqual(logic.narrowed(rows, at, { search: "  chart ROOM " }).map((row) => row.campaign), ["brine"]);
  assert.deepEqual(logic.narrowed(rows, new Map(), { filter: "needs" }), [], "a campaign no hub answered for needs nothing that is known");
  assert.deepEqual(logic.inProgress(rows, at).map((entry) => entry.row.campaign), ["cinder", "brine"]);
  assert.deepEqual(logic.inProgress(rows, new Map()), []);
});

test("what each view of the hub may show", () => {
  const table = logic.hubSections(hub("table"), "table");
  assert.deepEqual(table, ["action", "cast", "holdings", "changes", "knowledge", "turns", "sources", "unavailable"]);
  const director = logic.hubSections(hub("director"), "director");
  assert.deepEqual(director, ["action", "cast", "holdings", "changes", "truth", "goals", "knowledge", "propose", "turns", "sources", "unavailable"]);
  const only = ["truth", "goals", "propose"];
  // both the address and the hub's own answer must say director
  assert.ok(!logic.hubSections(hub("table"), "director").some((id) => only.includes(id)));
  assert.ok(!logic.hubSections(hub("director"), "table").some((id) => only.includes(id)));
  assert.ok(!logic.hubSections(hub("table", { truth: {}, goals: [] }), "table").some((id) => only.includes(id)));
  assert.ok(logic.hubSections(hub("table", { obstacles: [{}], problems: [{ code: "effect_elsewhere" }] }), "table").join().includes("action,recover,cast,holdings,changes,obstacles"));
  assert.deepEqual(logic.knowledgeTabs(hub("table"), "table").map((entry) => entry.value), ["", ANYA]);
  assert.deepEqual(logic.knowledgeTabs(hub("table"), "director").map((entry) => entry.value), ["", ANYA], "a table hub offers the table's perspectives whatever the address says");
  assert.deepEqual(logic.knowledgeTabs(hub("director"), "director").map((entry) => entry.value), ["", ANYA, MARA]);
  const others = hub("table", { cast: [{ actor: ANYA, name: "Anya", controller: "human:other", role: "player" }] });
  assert.deepEqual(logic.knowledgeTabs(others, "table").map((entry) => entry.value), [""], "another player's character is not this table's to read");
  assert.deepEqual(["favours", "hinders", "prevents", "odd"].map(logic.leaning), ["for", "against", "against", ""]);
});

test("a time is said as a day", () => {
  const now = new Date(2026, 0, 10, 15, 0, 0);
  const at = (day, hour = 9) => new Date(2026, 0, day, hour).toISOString();
  assert.deepEqual([at(10), at(9, 23), at(7), at(11)].map((stamp) => logic.when(stamp, now)), ["Today", "Yesterday", "3 days ago", "Today"]);
  assert.ok(logic.when(at(1), now).includes("2026"));
  assert.deepEqual([logic.when("", now), logic.when("not a time", now), logic.when(null, now)], ["", "", ""]);
});
