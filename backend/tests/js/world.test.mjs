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

const ROADS = "world:a/topology/roads@r1", SEA = "world:a/topology/sea@r1";

function places(etag = "g|", { topology = ROADS, blocked = false, road = "road" } = {}) {
  return { campaign: "c1", topologies: [{ topology, name: null }],
    topology: { topology, name: topology === ROADS ? "Roads" : "Sea", etag, missing: [],
      places: [{ place: TOWN, name: "Saltmarch", aliases: [], parent: null, missing: false },
        { place: OTHER, name: "Saltmarch", aliases: [], parent: null, missing: false },
        { place: PORT, name: "Port", aliases: [], parent: null, missing: false }],
      paths: [{ path: road, from: TOWN, to: PORT, two_way: false, requires: [], blocked, changed: blocked }] } };
}

function refused(reason) {
  const error = new Error(reason + " (canned)");
  error.reason = reason;
  return error;
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
  let busy = false;  // as the shell's act: one action at a time, a second is ignored while one runs
  const ctx = {
    ui, path: ["c1"], query,
    href: (route, segments = []) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/"),
    registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {}, notify() {},
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => { if (text) drafts.set(key, text); else drafts.delete(key); }, clear: () => drafts.delete(key) }),
    act: async (work) => {
      if (busy) return undefined;
      busy = true;
      try { return await work(); } catch { return undefined; } finally { busy = false; }
    },
    api: async (op, args) => {
      asked.push({ op, args });
      if (op === "campaign.chats") return { complete: true, campaigns: [{ campaign: "c1", chats: [{ chat: CHAT }] }] };
      if (answers[op]) return answers[op](args);
      if (op === "world.topologies") return { campaign: "c1", topologies: [{ topology: ROADS, name: "Roads" }] };
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
  assert.deepEqual(asked.map((item) => item.op), ["campaign.chats"], "no world op is called for the table");
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
  field(minds(first.root), "Whose belief").value = ANYA;
  button(minds(first.root), "Add this hypothesis").click();
  assert.ok(minds(first.root).textContent.includes("h1: what Anya believes, \"Anya knows the letter\", 70%"));
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
  assert.ok(minds(again.root).textContent.includes("h1: what Anya believes"), "the written hypotheses are kept for Mara");
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

const placesCard = (root) => section(root, "Places and routes");
const reviewOf = (card) => card.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Review this change");
const roadButton = (card, label) => card.querySelectorAll("button").find((node) => node.getAttribute("aria-label") === label);
const planOf = (args, topology) => ({ plan: { operation: args.operation_id, already_applied: false, files: ["campaigns/c1/x.json"],
  before: topology.paths[0], after: { ...topology.paths[0], blocked: args.blocked } }, topology });

test("a review refused as stale reads the roads again, and the next review is made against the new revision", async () => {
  let reads = 0;
  const plans = [];
  const { root, done } = await mounted({ answers: {
    "world.places": () => { reads += 1; return places(reads === 1 ? "g|" : "g|o2"); },
    "world.path_plan": (args) => {
      plans.push(args.expected);
      if (plans.length === 1) throw refused("changed");
      return planOf(args, places("g|o2").topology);
    },
  } });
  await done;
  roadButton(placesCard(root), "Block road").click();
  await settle();
  assert.equal(reads, 2, "the stale plan reads the topology again");
  assert.ok(!reviewOf(placesCard(root)), "no review is shown for the stale plan");
  roadButton(placesCard(root), "Block road").click();
  await settle();
  assert.deepEqual(plans, ["g|", "g|o2"], "the next review sends the revision read since");
  assert.ok(reviewOf(placesCard(root)));
});

test("a rumor's review refused as stale reads the rumors again, and the next one sends their new revision", async () => {
  const rumor = { rumor: "letter", sender: { actor: BEA, name: "Bea", role: "npc" }, recipient: { actor: MARA, name: "Mara", role: "npc" },
    attributed_to: null, source: { evidence: "x", content: "seen" }, text: "the banner fell", channel: "letter", confidence: 80, distortion: "none",
    faithful: true, provenance: "p", sent: null, due: null, route: { proximity: false, affiliation: false, channel: true, attention: true },
    status: "delivered", report: "r", belief: null, can_admit: true };
  let reads = 0;
  const plans = [];
  const { root, done } = await mounted({ answers: {
    "world.rumors": () => { reads += 1; return { campaign: "c1", now: null, rumors: [rumor], etag: "rumors:" + reads }; },
    "world.belief_plan": (args) => {
      plans.push(args.expected);
      if (plans.length === 1) throw refused("changed");
      return { plan: { belief: "admit-" + args.operation_id, holder: MARA, text: rumor.text, already_applied: false, files: ["f"] },
        rumors: { campaign: "c1", now: null, rumors: [rumor], etag: "rumors:" + reads } };
    },
  } });
  await done;
  const card = () => section(root, "Rumors");
  button(card(), "Review").click();
  await settle();
  assert.equal(reads, 2);
  button(card(), "Review").click();
  await settle();
  assert.deepEqual(plans, ["rumors:1", "rumors:2"]);
  assert.ok(reviewOf(card()));
});

test("a route answered before a road was blocked is not left on screen as current", async () => {
  const { root, done } = await mounted({ answers: {
    "world.route": () => ({ found: true, conditions: [], route: { places: [{ place: TOWN }, { place: PORT }], paths: ["road"] } }),
    "world.path_plan": (args) => planOf(args, places().topology),
    "world.path_commit": () => ({ done: { already_applied: false }, topology: places("g|o1", { blocked: true }).topology }),
  } });
  await done;
  button(placesCard(root), "Find a route").click();
  await settle();
  assert.ok(placesCard(root).textContent.includes("Route with no condition held"));
  roadButton(placesCard(root), "Block road").click();
  await settle();
  assert.ok(placesCard(root).textContent.includes("Route with no condition held"), "a review changes nothing yet");
  button(reviewOf(placesCard(root)), "Make this change").click();
  await settle();
  assert.ok(!placesCard(root).textContent.includes("Route with no condition held"), "the old route is gone once the road changed");
  assert.ok(placesCard(root).textContent.includes("Blocked"));
});

test("the route's two ends stay as picked across redraws and a return to the page", async () => {
  const drafts = new Map();
  const first = await mounted({ drafts, answers: { "world.find_place": () => ({ name: "Port", matches: [] }) } });
  await first.done;
  field(placesCard(first.root), "From").value = PORT;
  field(placesCard(first.root), "From").dispatchEvent({ type: "change" });
  field(placesCard(first.root), "To").value = OTHER;
  field(placesCard(first.root), "To").dispatchEvent({ type: "change" });
  typeInto(placesCard(first.root), "Place name", "Port");
  button(placesCard(first.root), "Find").click();  // an unrelated redraw
  await settle();
  assert.equal(field(placesCard(first.root), "From").value, PORT);
  assert.equal(field(placesCard(first.root), "To").value, OTHER);
  const again = await mounted({ drafts });
  await again.done;
  assert.equal(field(placesCard(again.root), "From").value, PORT);
  assert.equal(field(placesCard(again.root), "To").value, OTHER);
});

test("choosing another topology while an action runs shows its roads and drops the review of the first", async () => {
  let release;
  const { root, done } = await mounted({ answers: {
    "world.topologies": () => ({ campaign: "c1", topologies: [{ topology: ROADS, name: "Roads" }, { topology: SEA, name: "Sea" }] }),
    "world.places": (args) => places("g|", { topology: args.topology, road: args.topology === SEA ? "ferry" : "road" }),
    "world.path_plan": (args) => new Promise((resolve) => { release = () => resolve(planOf(args, places().topology)); }),
  } });
  await done;
  roadButton(placesCard(root), "Block road").click();  // the action is still running
  await settle(3);
  const choose = field(placesCard(root), "Topology");
  choose.value = SEA;
  choose.dispatchEvent({ type: "change" });
  await settle();
  assert.ok(roadButton(placesCard(root), "Block ferry"), "the sea's roads are shown");
  release();
  await settle();
  assert.ok(!reviewOf(placesCard(root)), "the first topology's late plan is not reviewed under the second");
  assert.equal(field(placesCard(root), "Topology").value, SEA);
  assert.ok(roadButton(placesCard(root), "Block ferry"));
});

test("a stale road change refused after another topology was chosen reads the one chosen last", async () => {
  let release;
  const { root, done } = await mounted({ answers: {
    "world.topologies": () => ({ campaign: "c1", topologies: [{ topology: ROADS, name: "Roads" }, { topology: SEA, name: "Sea" }] }),
    "world.places": (args) => places("g|", { topology: args.topology, road: args.topology === SEA ? "ferry" : "road" }),
    "world.path_plan": (args) => planOf(args, places().topology),
    "world.path_commit": () => new Promise((resolve, reject) => { release = () => reject(refused("changed")); }),
  } });
  await done;
  roadButton(placesCard(root), "Block road").click();
  await settle();
  button(reviewOf(placesCard(root)), "Make this change").click();  // the commit is still out
  await settle(3);
  const choose = field(placesCard(root), "Topology");
  choose.value = SEA;
  choose.dispatchEvent({ type: "change" });
  await settle();
  release();
  await settle();
  assert.equal(field(placesCard(root), "Topology").value, SEA, "the stale commit does not bring back the first topology");
  assert.ok(roadButton(placesCard(root), "Block ferry"), "the sea's roads are still shown");
});

test("a hypothesis about a nested belief reads outer to inner", () => {
  assert.equal(logic.whose(["Anya"]), "what Anya believes");
  assert.equal(logic.whose(["Anya", "Bea"]), "what Anya thinks Bea believes");
});

test("one section refused leaves the others working, and it can be read again", async () => {
  let rumorReads = 0;
  const { root, done } = await mounted({ answers: {
    "world.topologies": () => ({ campaign: "c1", topologies: [{ topology: ROADS, name: "Roads" }, { topology: SEA, name: "Sea" }] }),
    "world.places": (args) => { if (args.topology === ROADS) throw refused("unsupported_schema"); return places("g|", { topology: SEA, road: "ferry" }); },
    "world.rumors": () => { rumorReads += 1; if (rumorReads === 1) throw refused("ledger_unreadable"); return { campaign: "c1", now: null, rumors: [], etag: "r" }; },
  } });
  await done;
  await settle();
  const places_ = placesCard(root);
  assert.ok(places_.textContent.includes("unsupported_schema"), "the topology's refusal is shown in its card");
  assert.ok(field(places_, "Topology"), "the other topologies are still offered");
  assert.ok(section(root, "Rumors").textContent.includes("ledger_unreadable"));
  assert.ok(section(root, "Faction decision").querySelector("button"), "the faction section works");
  assert.ok(minds(root).textContent.includes("Mara's own sight"), "the minds section works");
  button(section(root, "Rumors"), "Read again").click();
  await settle();
  assert.ok(section(root, "Rumors").textContent.includes("No rumor has been sent"));
  const choose = field(placesCard(root), "Topology");
  choose.value = SEA;
  choose.dispatchEvent({ type: "change" });
  await settle();
  assert.ok(roadButton(placesCard(root), "Block ferry"));
});

test("a refused cast is shown in both sections that lay it out, and read again", async () => {
  let reads = 0;
  const { root, done } = await mounted({ answers: { "world.cast": () => { reads += 1; if (reads === 1) throw refused("missing_chat"); return { chat: CHAT, cast: CAST }; } } });
  await done;
  await settle();
  assert.ok(section(root, "Faction decision").textContent.includes("missing_chat"));
  assert.ok(placesCard(root).textContent.includes("Roads"), "the places still show");
  button(section(root, "Faction decision"), "Read again").click();
  await settle();
  assert.ok(button(section(root, "Faction decision"), "Show the decision"));
});

test("what is picked and ticked for a faction or a mind is kept across leaving the page", async () => {
  const drafts = new Map();
  const first = await mounted({ drafts });
  await first.done;
  await settle();
  const faction = section(first.root, "Faction decision");
  field(faction, "Leader").value = BEA;
  field(faction, "Leader").dispatchEvent({ type: "change" });
  const tickIn = (root, label, on) => {
    const box = root.querySelectorAll("label").find((node) => node.textContent === label).querySelector("input");
    box.checked = on;
    box.dispatchEvent({ type: "change" });
  };
  tickIn(faction, "Mara is a member", false);
  tickIn(faction, "Anya", true);
  const mind = minds(first.root);
  field(mind, "Whose belief").value = ANYA;
  field(mind, "Whose belief").dispatchEvent({ type: "change" });
  field(mind, "As they think this character believes it (optional)").value = BEA;
  field(mind, "As they think this character believes it (optional)").dispatchEvent({ type: "change" });
  tickIn(mind, "Mara's own sight (perception, sight)", true);

  const again = await mounted({ drafts });
  await again.done;
  await settle();
  const ticked = (root, label) => root.querySelectorAll("label").find((node) => node.textContent === label).querySelector("input").checked;
  const kept = section(again.root, "Faction decision");
  assert.equal(field(kept, "Leader").value, BEA);
  assert.equal(ticked(kept, "Mara is a member"), false);
  assert.equal(ticked(kept, "Bea is a member"), true);
  assert.equal(ticked(kept, "Anya"), true);
  const keptMind = minds(again.root);
  assert.equal(field(keptMind, "Whose belief").value, ANYA);
  assert.equal(field(keptMind, "As they think this character believes it (optional)").value, BEA);
  assert.equal(ticked(keptMind, "Mara's own sight (perception, sight)"), true);
});

test("a hypothesis can be about two characters, and what was laid out is what is sent", async () => {
  let sent = null;
  const { root, done } = await mounted({ answers: {
    "world.mind_preview": (args) => { sent = args; return { saved: false, owner: CAST[0], as_of: 2, admitted: ["h1"], unchanged: true, current: [], tactic: null, truth: { rulings: [], beliefs: [] } }; },
    "world.faction_preview": (args) => { sent = args; return { saved: false, group: "G", goals: [], policy: "p", proposal: "x", decision: { choice: "no", leader: CAST[1] }, members: [], dissent: [], agreeing: [], told: [] }; },
  } });
  await done;
  await settle();
  const mind = minds(root);
  field(mind, "Whose belief").value = ANYA;
  field(mind, "As they think this character believes it (optional)").value = BEA;
  typeInto(mind, "What Mara thinks they think", "Anya thinks Bea read it");
  typeInto(mind, "How sure", "0.6");
  const box = mind.querySelectorAll("input").find((node) => node.getAttribute("value") === "e-Mara");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
  button(mind, "Add this hypothesis").click();
  assert.ok(minds(root).textContent.includes("h1: what Anya thinks Bea believes"));
  button(minds(root), "Show what Mara would do").click();
  await settle();
  assert.deepEqual(sent.hypotheses[0].about, [ANYA, BEA]);

  const faction = section(root, "Faction decision");
  for (const [label, value] of [["Group", "G"], ["Goals", "a\nb"], ["How it decides", "p"], ["What is proposed", "x"], ["The leader's choice", "no"]]) typeInto(faction, label, value);
  field(faction, "Leader").value = BEA;
  field(faction, "Leader").dispatchEvent({ type: "change" });
  const member = faction.querySelectorAll("label").find((node) => node.textContent === "Mara is a member").querySelector("input");
  member.checked = false;
  member.dispatchEvent({ type: "change" });
  button(faction, "Show the decision").click();
  await settle();
  assert.equal(sent.leader, BEA);
  assert.deepEqual(sent.members.map((item) => item.actor), [BEA], "Mara, unticked, is not laid out as a member");
  assert.deepEqual(sent.goals, ["a", "b"]);
});

const TWO = { campaign: "c1", topologies: [{ topology: ROADS, name: "Roads" }, { topology: SEA, name: "Sea" }] };
const choose = (root, value) => { const node = field(placesCard(root), "Topology"); node.value = value; node.dispatchEvent({ type: "change" }); };

test("a road review answered after its topology was left and chosen again is not drawn over the newer read", async () => {
  let release, roadsReads = 0;
  const plans = [];
  const { root, done } = await mounted({ answers: {
    "world.topologies": () => TWO,
    "world.places": (args) => {
      if (args.topology === SEA) return places("s|", { topology: SEA, road: "ferry" });
      roadsReads += 1;
      return places(roadsReads === 1 ? "g|" : "g|o2", { topology: ROADS });
    },
    "world.path_plan": (args) => {
      plans.push(args.expected);
      if (plans.length === 1) return new Promise((resolve) => { release = () => resolve(planOf(args, places("g|").topology)); });
      return planOf(args, places(args.expected).topology);
    },
  } });
  await done;
  roadButton(placesCard(root), "Block road").click();  // reviewed against "g|", still out
  await settle(3);
  choose(root, SEA);
  await settle();
  choose(root, ROADS);  // the same topology again, read anew: "g|o2"
  await settle();
  release();
  await settle();
  assert.ok(!reviewOf(placesCard(root)), "the review of the read before is dropped, not shown again");
  roadButton(placesCard(root), "Block road").click();
  await settle();
  assert.deepEqual(plans, ["g|", "g|o2"], "the newer read stays shown: the next review is made against it");
});

test("a road change answered after its topology was left and chosen again reads it once more", async () => {
  let release, roadsReads = 0;
  const { root, done } = await mounted({ answers: {
    "world.topologies": () => TWO,
    "world.places": (args) => {
      if (args.topology === SEA) return places("s|", { topology: SEA, road: "ferry" });
      roadsReads += 1;
      return places("g|" + roadsReads, { topology: ROADS, blocked: roadsReads >= 3 });
    },
    "world.path_plan": (args) => planOf(args, places(args.expected).topology),
    "world.path_commit": () => new Promise((resolve) => { release = () => resolve({ done: { already_applied: false }, topology: places("g|old").topology }); }),
  } });
  await done;
  roadButton(placesCard(root), "Block road").click();
  await settle();
  button(reviewOf(placesCard(root)), "Make this change").click();  // the commit is still out
  await settle(3);
  choose(root, SEA);
  await settle();
  choose(root, ROADS);  // read while the change may not have landed yet
  await settle();
  assert.equal(roadsReads, 2);
  release();
  await settle();
  assert.equal(roadsReads, 3, "the topology chosen last is read once more after the change");
  assert.ok(roadButton(placesCard(root), "Open road"), "and what is shown is that read, with the road blocked");
});

test("a preview's truth replaces the one read with the NPC, and a ruling overtaken is shown as earlier", async () => {
  const refuted = { rulings: [{ proposition: "p1", text: "Anya knows", standing: "refuted", ruling: "r1", at: 2, earlier: [] }], beliefs: [] };
  const established = { rulings: [{ proposition: "p1", text: "Anya knows", standing: "established", ruling: "r2", at: 5,
    earlier: [{ ruling: "r1", standing: "refuted", at: 2 }] }], beliefs: [] };
  const { root, done } = await mounted({ answers: {
    "world.mind": (args) => ({ ...mind(args.owner, "Mara's own sight"), truth: refuted }),
    "world.mind_preview": () => ({ saved: false, owner: CAST[0], as_of: 2, admitted: ["h1"], unchanged: true, current: [], tactic: null, truth: established }),
  } });
  await done;
  await settle();
  const truth = () => minds(root).querySelector("details");
  assert.ok(truth().textContent.includes("\"Anya knows\" is refuted."));
  typeInto(minds(root), "What Mara thinks they think", "Anya read it");
  typeInto(minds(root), "How sure", "0.6");
  const box = minds(root).querySelectorAll("input").find((node) => node.getAttribute("value") === "e-Mara");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
  button(minds(root), "Add this hypothesis").click();
  button(minds(root), "Show what Mara would do").click();
  await settle();
  assert.ok(truth().textContent.includes("\"Anya knows\" is established."), "the truth the preview read is drawn");
  assert.ok(!truth().textContent.includes("is refuted."), "the refuted ruling is not shown as standing");
  assert.ok(truth().textContent.includes("Ruled earlier: refuted."));
  assert.ok(minds(root).textContent.includes("changed since Mara was read"));
});
