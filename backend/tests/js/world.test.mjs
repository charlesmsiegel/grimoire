// Sprint 115's World page without the app: its logic (conditions, confidences, places named apart when they share a
// name, the words a road change and a rumor are said in, hypotheses kept in a draft), and the page with canned
// answers (the table's view asks nothing; a late answer for the NPC chosen before is never drawn over the one
// chosen since, nor a late preview; a hypothesis being written is that NPC's own; a stale road change is read
// again). Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
install("http://127.0.0.1:1");
const ui = await import(pathToFileURL(path.join(STATIC, "shell", "ui.js")).href);
const page = await import(pathToFileURL(path.join(STATIC, "pages", "world", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "world", "logic.js")).href);

const CHAT = "campaign:c1#chat/t1";
const MARA = "campaign:c1/character/mara", BEA = "campaign:c1/character/bea", ANYA = "campaign:c1/character/anya";
const TOWN = "world:a/location/town@r1", OTHER = "import:b/location/town@r1", PORT = "world:a/location/port@r1";
const CAST = [{ actor: MARA, name: "Mara", role: "npc", agent: true }, { actor: BEA, name: "Bea", role: "npc", agent: true },
  { actor: ANYA, name: "Anya", role: "player", agent: true }];
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async (n = 30) => { for (let i = 0; i < n; i += 1) await tick(); };

function places(etag = "g|") {
  return { campaign: "c1", topologies: [{ topology: "world:a/topology/roads@r1", name: null }],
    topology: { topology: "world:a/topology/roads@r1", name: "Roads", etag, missing: [],
      places: [{ place: TOWN, name: "Saltmarch", aliases: [], parent: null, missing: false },
        { place: OTHER, name: "Saltmarch", aliases: [], parent: null, missing: false },
        { place: PORT, name: "Port", aliases: [], parent: null, missing: false }],
      paths: [{ path: "road", from: TOWN, to: PORT, two_way: false, requires: [], blocked: false, changed: false }] } };
}

function mind(owner, content) {
  const member = CAST.find((item) => item.actor === owner);
  return { chat: CHAT, owner: member, now: 3, max: 64, truth: { rulings: [], beliefs: [] },
    evidence: [{ evidence: "e-" + member.name, kind: "perception", channel: "sight", content, at: 2 }] };
}

// The page mounted for `audience`; `answers[op](args)` answers each op (and may return a promise the test
// settles). `drafts` is the shell's store, kept across mounts; `asked` lists every op called.
async function mounted({ audience = "director", answers = {}, drafts = new Map() } = {}) {
  const root = ui.el("div", {});
  const asked = [];
  const query = new URLSearchParams({ chat: CHAT });
  if (audience === "director") query.set("audience", "director");
  const ctx = {
    ui, path: ["c1"], query,
    href: (route, segments = []) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/"),
    registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {}, notify() {},
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api: async (op, args) => {
      asked.push(op);
      if (op === "campaign.chats") return { complete: true, campaigns: [{ campaign: "c1", chats: [{ chat: CHAT }] }] };
      if (answers[op]) return answers[op](args);
      if (op === "world.cast") return { chat: CHAT, cast: CAST };
      if (op === "world.places") return places();
      if (op === "world.rumors") return { campaign: "c1", now: null, rumors: [], etag: "rumors:1" };
      if (op === "world.mind") return mind(args.owner, args.owner === MARA ? "Mara's own sight" : "Bea's own sight");
      throw new Error("unexpected " + op);
    },
  };
  const done = page.mount(root, ctx);
  return { root, done, asked };
}

const section = (root, title) => root.querySelector('section[aria-label="' + title + '"]');
const minds = (root) => section(root, "What an NPC thinks");
const button = (root, text) => root.querySelectorAll("button").find((node) => node.textContent === text);
function field(root, name) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === name);
  return root.querySelectorAll("textarea,input,select").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}
const typeInto = (root, name, value) => { const node = field(root, name); node.value = value; node.dispatchEvent({ type: "input" }); };

test("conditions, confidences and hypothesis ids are read the way a person types them", () => {
  assert.deepEqual(logic.parseConditions(" ticket, night  ticket "), ["ticket", "night"]);
  assert.deepEqual(logic.parseConditions(""), []);
  assert.equal(logic.parseConfidence("0.8"), 0.8);
  assert.equal(logic.parseConfidence("80%"), 0.8);
  assert.equal(logic.parseConfidence(".5"), 0.5);
  for (const bad of ["1.5", "120%", "-0.1", "most", "", undefined]) assert.equal(logic.parseConfidence(bad), null, String(bad));
  assert.equal(logic.nextHypothesisId([]), "h1");
  assert.equal(logic.nextHypothesisId([{ id: "h2" }]), "h3");
  assert.equal(logic.heldFrom(["a", "b"], [{ evidence: "a", at: 2 }, { evidence: "b", at: 5 }, { evidence: "c", at: 9 }]), 5);
  assert.equal(logic.heldFrom([], []), null);
  assert.deepEqual(logic.readHypotheses("not json"), []);
  assert.deepEqual(logic.readHypotheses('[{"id":"h1"},{"nope":1},3]'), [{ id: "h1" }]);
  assert.equal(logic.percent(0.25), "25%");
  const guard = logic.latest();
  const first = guard.start(), second = guard.start();
  assert.ok(!guard.current(first) && guard.current(second));
});

test("two places of one name are told apart by their refs, and a road change is said in words", () => {
  const labels = logic.placeLabels(places().topology.places);
  assert.equal(labels.get(TOWN), "Saltmarch (" + TOWN + ")");
  assert.equal(labels.get(OTHER), "Saltmarch (" + OTHER + ")");
  assert.equal(labels.get(PORT), "Port");
  assert.equal(logic.placeLabels([{ place: TOWN, name: null, missing: true }]).get(TOWN), TOWN + " (missing)");
  const path = { path: "ferry", from: TOWN, to: PORT, two_way: true, requires: ["ticket"], blocked: true };
  assert.equal(logic.pathText(path, labels), "Saltmarch (" + TOWN + ") to Port, both ways, needs: ticket");
  assert.match(logic.pathPlanText({ already_applied: false, after: path }, labels), /^Block the road "ferry".*The topology itself is not changed\.$/);
  assert.match(logic.pathPlanText({ already_applied: true, after: path }, labels), /already made/);
});

test("a rumor is said as who told whom, by which channel and when it arrives", () => {
  const rumor = { sender: { name: "Bryn" }, recipient: { name: null, actor: MARA }, channel: "letter",
    sent: { date: "1203-03-01", time: "17:00" }, due: { date: "1203-03-22", time: "17:00" } };
  assert.equal(logic.rumorLine(rumor), "Bryn to " + MARA + " by letter, sent 1203-03-01 17:00, arriving 1203-03-22 17:00");
  assert.equal(logic.routeText({ proximity: false, affiliation: true, channel: true, attention: false }), "Carried by shared affiliation, a channel");
  assert.equal(logic.rumorStatus("on_the_way"), "On the way");
});

test("the table's view says only that the page is the director's, and asks for none of it", async () => {
  const { root, done, asked } = await mounted({ audience: "table" });
  await done;
  assert.deepEqual(asked, ["campaign.chats"], "no world op is called for the table");
  assert.ok(section(root, "World").textContent.includes("The World page is the director's"));
  assert.equal(root.querySelectorAll("section").length, 1);
  assert.equal(section(root, "World").querySelectorAll("button").length, 0);
});

test("an answer for the NPC chosen before is never drawn over the one chosen since", async () => {
  let release;
  const late = new Promise((resolve) => { release = () => resolve(mind(MARA, "Mara's own sight")); });
  let reads = 0;
  const { root, done } = await mounted({ answers: { "world.mind": (args) => {
    reads += 1;
    return reads === 1 ? late : mind(args.owner, args.owner === MARA ? "Mara's own sight" : "Bea's own sight");
  } } });
  const chip = () => minds(root) && minds(root).querySelectorAll("button").find((node) => node.textContent === "Bea");
  for (let n = 0; n < 50 && !chip(); n += 1) await tick();
  chip().click();  // Bea is chosen while Mara's answer is still on its way
  await settle();
  release();
  await done;
  await settle();
  assert.ok(minds(root).textContent.includes("Bea's own sight"), "Bea's mind stays");
  assert.ok(!minds(root).textContent.includes("Mara's own sight"), "Mara's late answer is dropped");
});

test("a preview that answers after another NPC was chosen is dropped", async () => {
  let release;
  const { root, done } = await mounted({ answers: { "world.mind_preview": (args) => new Promise((resolve) => {
    release = () => resolve({ saved: false, owner: CAST[0], as_of: 2, admitted: ["h1"], unchanged: true, truth: { rulings: [], beliefs: [] },
      current: [{ id: "h1", about: [{ actor: ANYA, name: "Anya" }], claim: "Mara's late theory", confidence: 0.8, evidence: [], at: 2 }],
      tactic: null });
  }) } });
  await done;
  await settle();
  const pane = minds(root);
  typeInto(pane, "What Mara thinks they think", "Anya read the letter");
  typeInto(pane, "How sure", "0.8");
  const box = pane.querySelectorAll("input").find((node) => node.getAttribute("value") === "e-Mara");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
  button(pane, "Add this hypothesis").click();
  button(minds(root), "Show what Mara would do").click();
  await settle(5);
  minds(root).querySelectorAll("button").find((node) => node.textContent === "Bea").click();
  await settle();
  release();
  await settle();
  assert.ok(minds(root).textContent.includes("Bea's own sight"));
  assert.ok(!minds(root).textContent.includes("Mara's late theory"), "Mara's late preview is not drawn under Bea");
});

test("a hypothesis being written is its NPC's own, and is kept across leaving the page", async () => {
  const drafts = new Map();
  const first = await mounted({ drafts });
  await first.done;
  await settle();
  typeInto(minds(first.root), "What Mara thinks they think", "Anya knows the letter");
  const box = minds(first.root).querySelectorAll("input").find((node) => node.getAttribute("value") === "e-Mara");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
  typeInto(minds(first.root), "How sure", "70%");
  field(minds(first.root), "About").value = ANYA;
  button(minds(first.root), "Add this hypothesis").click();
  assert.ok(minds(first.root).textContent.includes("h1: about Anya, \"Anya knows the letter\", 70%"));
  assert.equal(field(minds(first.root), "What Mara thinks they think").value, "", "the added claim is cleared from the form");
  typeInto(minds(first.root), "What Mara thinks they think", "half-written");
  minds(first.root).querySelectorAll("button").find((node) => node.textContent === "Bea").click();
  await settle();
  assert.equal(field(minds(first.root), "What Bea thinks they think").value, "", "Bea does not see Mara's draft");
  assert.ok(minds(first.root).textContent.includes("No hypothesis is written yet."));

  const again = await mounted({ drafts });
  await again.done;
  await settle();
  assert.equal(field(minds(again.root), "What Mara thinks they think").value, "half-written");
  assert.ok(minds(again.root).textContent.includes("h1: about Anya"), "the written hypotheses are kept for Mara");
});

test("a road change reviewed before another change is refused and read again, never made", async () => {
  let reads = 0;
  const { root, done } = await mounted({ answers: {
    "world.places": () => { reads += 1; return places(reads === 1 ? "g|" : "g|o2"); },
    "world.path_plan": (args) => ({ plan: { operation: args.operation_id, already_applied: false, files: ["campaigns/c1/x.json"],
      before: places().topology.paths[0], after: { ...places().topology.paths[0], blocked: true } }, topology: places().topology }),
    "world.path_commit": () => { const error = new Error("changed since review"); error.reason = "changed"; throw error; },
  } });
  await done;
  const card = section(root, "Places and routes");
  card.querySelectorAll("button").find((node) => node.getAttribute("aria-label") === "Block road").click();
  await settle();
  const review = card.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Review this change");
  assert.ok(review.textContent.includes("Block the road \"road\""));
  button(review, "Make this change").click();
  await settle();
  assert.equal(reads, 2, "the topology is read again");
  assert.ok(!card.querySelectorAll("div").some((node) => node.getAttribute("aria-label") === "Review this change"), "the stale review is gone");
});
