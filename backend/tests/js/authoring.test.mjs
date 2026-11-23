// Sprint 116's Authoring page without the app: its logic (the hand builder's stable keys and the proposal it sends, a
// suggestion's constraints, an audit's budget, the words a removal preview is said in), and the page with canned
// answers: a draft refused stale_review reviews the proposal again by itself; a write refused conflict does too and
// writes nothing; a brainstorm offers no Accept; an estimate answered after the ask changed is never offered for
// approval; an audit report answered after a newer run is dropped; a lost approval answer shows the suggestion that
// was made; a world with several maps asks which map a draft extends; the hand builder keeps and moves the focus.
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
const page = await import(pathToFileURL(path.join(STATIC, "pages", "authoring", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "authoring", "logic.js")).href);

const WORLD = "world:vale";
const COAST = "world:vale/location/saltmarch-coast@r1";
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async (n = 30) => { for (let i = 0; i < n; i += 1) await tick(); };

const BUDGET = { max_steps: { low: 1, high: 2000, default: 400 }, max_reads: { low: 0, high: 1000, default: 200 },
  max_findings: { low: 0, high: 200, default: 50 }, max_seconds: { low: 1, high: 60, default: 20 } };
const SOURCE = { source: WORLD, title: "The Vale", world: true, etag: "e1", records_truncated: false,
  records: [{ ref: COAST, kind: "location", name: "Saltmarch Coast", place: true }],
  suggest: { available: true, blocked: null }, proposals: [], audits: [],
  limits: { candidates: 16, links: 48, kinds: ["location", "organization"], roles: ["individual", "template", "brainstorm"], relations: ["road", "within"], budget: BUDGET },
  unavailable: [{ feature: "Larger linked regions", slice: "087", reason: "Not built." }], note: "Nothing here is stored until you accept it." };
const UPGRADES = { source: WORLD, written: false,
  removal: { action: "remove", complete: true, before: null, references: 0, missing: 0, attention: 0, statuses: {}, files: [], more_files: 0, entities: [], edited: [], problems: [] },
  dependencies: { loaded: true, etag: "e1", problem: null, declared: [], offered: [], limit: 32 },
  update: { available: false, reason: "No candidate library." }, apply: { available: false, reason: "Not built." } };
const candidate = (key, name, role = "individual", duplicates = []) => ({ key, kind: "location", name, role, description: "", storable: role !== "brainstorm", place: true, duplicates });
const PROPOSAL = { proposal: "hand-1", source: WORLD, origin: "human:owner", kind: "hand", state: "open", rationale: "",
  request: { request: "hand-1", brief: "By hand", constraints: [], max_candidates: 16 },
  candidates: [candidate("c1", "Brinewick", "individual", [{ existing: COAST, existing_name: "Saltmarch Coast", other: null }]), candidate("c2", "A bell", "brainstorm")],
  links: [], reviewed: true, basis: "b", draft: null, applied: null, usage: null, note: "" };
const DRAFT = { digest: "d1", changeset: "cs", title: "t", author: "human:owner", rationale: "", records: { c1: "world:vale/location/brinewick@r1" }, topology: null,
  files: [{ path: "sources/world/vale/_source/records/location/brinewick/r1.json", action: "create", change: "create", size: 10, sha256: "x", text: "{}", binary: false, diff: null, target: "", summary: "" }],
  preconditions: [], accepted: ["c1"], distinct: ["c1"], kept: [], dropped: [], surfaced: ["c1"], discarded: ["c2"], conflicts: [], blocked: [], applicable: true, needs_approval: [] };

async function mounted(answer, { drafts = new Map(), path: where = [WORLD] } = {}) {
  const root = ui.el("div", {});
  const ctx = {
    ui, path: where, query: new URLSearchParams(),
    href: (route, segments = []) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/"),
    registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {}, notify() {},
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch { return undefined; } },
    api: async (op, args) => answer(op, args),
  };
  await page.mount(root, ctx);
  return root;
}

const section = (root, title) => root.querySelector('section[aria-label="' + title + '"]');
const group = (root, title) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === title);
const button = (root, label) => root.querySelectorAll("button").find((node) => node.textContent === label);
function control(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}
function set(root, text, value, type = "input") {
  const node = control(root, text);
  node.value = value;
  node.dispatchEvent({ type });
}
function tickBox(root, text) {
  const box = root.querySelectorAll("label").find((node) => node.textContent === text).querySelector("input");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
}

test("the hand builder keeps stable keys, drops a removed candidate's links and sends only what was typed", () => {
  let state = logic.addCandidate(logic.addCandidate(logic.emptyBuilder()));
  assert.deepEqual(state.candidates.map((item) => item.key), ["c1", "c2"]);
  state = logic.removeCandidate(state, "c1");
  state = logic.addCandidate(state);
  assert.deepEqual(state.candidates.map((item) => item.key), ["c2", "c3"], "a key once given is not given again");
  state = logic.addLink(state);
  assert.deepEqual(state.links[0], { id: "l1", relation: "road", subject: "c2", object: "" });
  assert.deepEqual(logic.builderBody(state).errors, ["Name c2.", "Name c3.", "Choose what link l1 leads to."]);
  state = { ...state, candidates: state.candidates.map((item, n) => ({ ...item, name: n ? " Gullhaven " : "Brinewick" })),
    links: [{ ...state.links[0], relation: "within", object: COAST }] };
  assert.deepEqual(logic.builderBody(state).body, {
    candidates: [{ key: "c2", kind: "location", name: "Brinewick", role: "individual", description: "" },
      { key: "c3", kind: "location", name: "Gullhaven", role: "individual", description: "" }],
    links: [{ id: "l1", relation: "within", subject: "c2", object: COAST }], rationale: "" });
  assert.deepEqual(logic.removeCandidate(state, "c2").links, [], "a link to a removed candidate goes with it");
  // the far end of a link: the other candidates and the world's places, never a group or the link's own start
  const options = logic.endpointOptions(state, [{ ref: COAST, name: "Saltmarch Coast", place: true }, { ref: "world:vale/organization/g@r1", name: "G", place: false }], "c2");
  assert.deepEqual(options.map((item) => item.value), ["c3", COAST]);
  // a draft that cannot be read starts empty; one that can keeps only known kinds and roles
  assert.deepEqual(logic.restoreBuilder("{oops"), logic.emptyBuilder());
  const restored = logic.restoreBuilder(JSON.stringify({ candidates: [{ key: "c1", kind: "dragon", role: "boss", name: 3 }, { key: "Bad Key" }], links: [{ id: "l1", relation: "teleport" }] }));
  assert.deepEqual(restored.candidates, [{ key: "c1", kind: "location", role: "individual", name: "", description: "" }]);
  assert.equal(restored.links[0].relation, "road");
  assert.deepEqual(logic.builderBody(logic.emptyBuilder()).errors, ["Add at least one place or group."]);
});

test("constraints, budgets, citations and removal previews are said plainly", () => {
  assert.deepEqual(logic.constraintsOf(" one \n\n two\n"), ["one", "two"]);
  assert.deepEqual(logic.budgetValues(BUDGET, (name) => ({ max_reads: "10" })[name]), { values: { max_steps: 400, max_reads: 10, max_findings: 50, max_seconds: 20 } });
  assert.deepEqual(Object.keys(logic.budgetValues(BUDGET, (name) => ({ max_steps: "0", max_seconds: "1.5" })[name]).errors), ["max_steps", "max_seconds"]);
  assert.equal(logic.citationLine({ field: "/name", quote: "Guild", revision: "r1" }), "/name: “Guild” (r1)");
  assert.equal(logic.removalLine({ references: 0 }), "Nothing elsewhere in this library refers to this source.");
  assert.equal(logic.removalLine({ references: 3, missing: 2, attention: 2 }), "3 references elsewhere in this library, 2 would be left missing, 2 need attention.");
  assert.equal(logic.shortVersion("sha256:" + "a".repeat(64)), "sha256:aaaaaaaaaaaa…");
  assert.equal(logic.shortVersion(null), "none");
  assert.ok(!logic.acceptable({ role: "brainstorm", storable: false }) && logic.acceptable({ role: "individual", storable: true }));
  assert.notEqual(logic.newRequestId(), logic.newRequestId());
});

test("a draft the world outran is reviewed again by itself, and a refused write is too", async () => {
  const calls = [];
  let refuse = "stale_review";
  const root = await mounted((op, args) => {
    calls.push([op, args]);
    if (op === "authoring.source") return { ...SOURCE, proposals: [PROPOSAL] };
    if (op === "authoring.upgrades") return UPGRADES;
    if (op === "authoring.review") return { proposal: { ...PROPOSAL } };
    if (op === "authoring.draft") {
      if (refuse) { const reason = refuse; refuse = null; throw Object.assign(new Error("moved"), { reason }); }
      return { proposal: { ...PROPOSAL, draft: DRAFT } };
    }
    if (op === "authoring.apply") throw Object.assign(new Error("moved"), { reason: "conflict" });
    throw new Error("unexpected " + op);
  });
  const world = section(root, "World suggestions");
  let shown = world.querySelectorAll("article").find((node) => node.getAttribute("data-proposal") === "hand-1");
  assert.ok(!shown.querySelectorAll("label").some((node) => node.textContent === "Accept A bell"), "a brainstorm offers no Accept");
  tickBox(shown, "Accept Brinewick");
  tickBox(shown, "It is a different place: keep both");
  button(shown, "Draft the change").click();
  await settle();
  assert.deepEqual(calls.slice(-2).map(([op]) => op), ["authoring.draft", "authoring.review"], "a stale review is reviewed again");
  shown = world.querySelectorAll("article").find((node) => node.getAttribute("data-proposal") === "hand-1");
  assert.ok(shown.textContent.includes("reviewed again"), shown.textContent);
  // ticked again (a new review is a new decision), drafted, then the write meets a world that moved
  tickBox(shown, "Accept Brinewick");
  tickBox(shown, "It is a different place: keep both");
  button(shown, "Draft the change").click();
  await settle();
  assert.deepEqual(calls.at(-1), ["authoring.draft", { source: WORLD, proposal: "hand-1", accept: ["c1"], distinct: ["c1"] }]);
  const changes = group(world, "The change set of hand-1");
  assert.ok(changes.textContent.includes("It writes exactly 1 file: 1 new record and the manifest."), changes.textContent);
  button(changes, "Approve and write").click();
  await settle();
  assert.deepEqual(calls.slice(-2).map(([op]) => op), ["authoring.apply", "authoring.review"]);
  assert.deepEqual(calls.at(-2)[1], { source: WORLD, proposal: "hand-1", draft: "d1" }, "the write names the draft that was shown");
  assert.ok(world.textContent.includes("Nothing was written: the world changed since the draft"), world.textContent);
  assert.equal(group(world, "The change set of hand-1"), undefined);
});

test("an estimate answered after the ask changed is never offered, and a late audit report is dropped", async () => {
  let release;
  const runs = [];
  const root = await mounted((op, args) => {
    if (op === "authoring.source") return SOURCE;
    if (op === "authoring.upgrades") return UPGRADES;
    if (op === "authoring.suggest_plan") {
      return new Promise((resolve) => { release = () => resolve({ source: WORLD, plan: { request: args.request_id, expected: "t", replay: false, blocked: null,
        call: { stage: "planning", calls: 1, prompt_tokens: 5, route: "p@x/m", cost_state: "unpriced", local: false }, context: { records: 1, truncated: false, basis: "b" } } }); });
    }
    if (op === "authoring.audit") {
      return new Promise((resolve) => runs.push((findings) => resolve({ audit: { audit: "a" + runs.length, status: "finished", reason: "", said: "Nothing was written.",
        steps: 1, findings, rejected: [], read: [], unread: [], budget: {} } })));
    }
    throw new Error("unexpected " + op);
  });
  const ask = group(section(root, "World suggestions"), "Ask the model");
  set(ask, "What to suggest", "Towns");
  button(ask, "Estimate the call").click();
  await settle(5);
  set(ask, "What to suggest", "Villages");
  release();
  await settle();
  assert.equal(group(root, "Approve this call"), undefined, "the old ask's estimate is not offered");
  assert.ok(section(root, "World suggestions").textContent.includes("estimate it again"));

  const audit = section(root, "Consistency audit");
  set(audit, "Steps at most", "0");
  button(audit, "Run the audit").click();
  await settle();
  assert.equal(runs.length, 0, "a budget out of bounds is not sent");
  assert.ok(audit.textContent.includes("A whole number from 1 to 2000."));
  set(audit, "Steps at most", "");
  // fakedom's act does not hold a second press while the first runs; the page's own guard drops the older answer
  button(audit, "Run the audit").click();
  await settle(5);
  button(audit, "Run the audit").click();
  await settle(5);
  runs[1]([]);
  await settle();
  runs[0]([{ finding: "f1", category: "missing_link", summary: "Old", citations: [] }]);
  await settle();
  const report = group(audit, "Audit report");
  assert.ok(report.textContent.includes("No finding"), report.textContent);
  assert.ok(!report.textContent.includes("Old"));
});

test("a page that names no source, or a source that is not a world, offers no authoring control", async () => {
  const none = await mounted(() => { throw new Error("no call"); }, { path: [] });
  assert.ok(none.textContent.includes("Open a source's authoring tools"));
  const card = await mounted((op) => {
    if (op === "authoring.source") return { ...SOURCE, source: "card:mara", world: false, records: [] };
    if (op === "authoring.upgrades") return { ...UPGRADES, source: "card:mara" };
    throw new Error("unexpected " + op);
  }, { path: ["card:mara"] });
  for (const title of ["World suggestions", "Consistency audit"]) {
    assert.equal(section(card, title).querySelectorAll("button,input,textarea,select").length, 0, title);
    assert.ok(section(card, title).textContent.includes("is not available yet"));
  }
  assert.ok(section(card, "Source upgrades").textContent.includes("Previewing an update is not available yet."));
});

const PLAN = (request) => ({ source: WORLD, plan: { request, expected: "t", replay: false, blocked: null,
  call: { stage: "planning", calls: 1, prompt_tokens: 5, route: "p@x/m", cost_state: "unpriced", local: false }, context: { records: 1, truncated: false, basis: "b" } } });

test("an approval whose answer is lost shows the suggestion the app made, and sends nothing more", async () => {
  const calls = [];
  let made = null;
  const root = await mounted((op, args) => {
    calls.push(op);
    if (op === "authoring.source") return { ...SOURCE, proposals: made ? [made] : [] };
    if (op === "authoring.upgrades") return UPGRADES;
    if (op === "authoring.suggest_plan") return PLAN(args.request_id);
    if (op === "authoring.suggest") {
      // the call was made and kept, then the answer never came back
      made = { ...PROPOSAL, proposal: "model-1", kind: "model", origin: "model:p@x/m",
        request: { request: args.request_id, brief: args.brief, constraints: [], max_candidates: 16 } };
      throw Object.assign(new Error("the connection closed"), { reason: "network" });
    }
    throw new Error("unexpected " + op);
  });
  const world = section(root, "World suggestions");
  set(group(world, "Ask the model"), "What to suggest", "Towns");
  button(group(world, "Ask the model"), "Estimate the call").click();
  await settle();
  button(group(world, "Approve this call"), "Approve and suggest").click();
  await settle();
  assert.equal(calls.filter((op) => op === "authoring.suggest").length, 1);
  assert.equal(calls.at(-1), "authoring.source", "the page reads what the app holds now");
  const shown = world.querySelector('article[data-proposal="model-1"]');
  assert.ok(shown, "the suggestion that was made is shown");
  assert.ok(shown.textContent.includes("the suggestion was made") && shown.textContent.includes("Nothing more was sent"), shown.textContent);
  assert.equal(group(world, "Approve this call"), undefined, "the spent estimate is not offered again");
});

// the focused element is that one (an element's own diff is the whole tree, so only the key is said)
function same(actual, expected, message = "") {
  const key = (node) => (node && node.getAttribute ? node.getAttribute("data-focus-key") || node.tagName : String(node));
  assert.ok(actual === expected && expected, message + " (focus is on " + key(actual) + ", not " + key(expected) + ")");
}

test("a world with several maps asks which one a proposal's links go on, and sends it", async () => {
  const calls = [];
  const linked = { ...PROPOSAL, candidates: [candidate("c1", "Brinewick")],
    links: [{ link: "l1", relation: "within", subject: "c1", object: { record: COAST, name: "Saltmarch Coast" }, problem: null }] };
  const maps = [{ topology: "coast-map", ref: "world:vale/topology/coast-map@r1", name: "Coast map" },
    { topology: "sea-map", ref: "world:vale/topology/sea-map@r1", name: "Sea map" }];
  const root = await mounted((op, args) => {
    calls.push([op, args]);
    if (op === "authoring.source") return { ...SOURCE, maps, proposals: [linked] };
    if (op === "authoring.upgrades") return UPGRADES;
    if (op === "authoring.draft") {
      if (!args.topology) throw Object.assign(new Error("several maps"), { reason: "ambiguous_topology" });
      return { proposal: { ...linked, draft: { ...DRAFT, topology: "world:vale/topology/coast-map@r2" } } };
    }
    throw new Error("unexpected " + op);
  });
  document.body.append(root);  // focus is had only in the page
  const world = section(root, "World suggestions");
  let shown = world.querySelector('article[data-proposal="hand-1"]');
  tickBox(shown, "Accept Brinewick");
  button(shown, "Draft the change").click();
  await settle();
  assert.deepEqual(calls.filter(([op]) => op === "authoring.draft").at(-1)[1], { source: WORLD, proposal: "hand-1", accept: ["c1"], distinct: [] });
  shown = world.querySelector('article[data-proposal="hand-1"]');
  assert.ok(shown.textContent.includes("more than one map"), shown.textContent);
  const pick = shown.querySelector('[data-focus-key="authoring:map:hand-1"]');
  same(document.activeElement, pick, "the focus goes to the choice of map");
  pick.value = "coast-map";
  pick.dispatchEvent({ type: "change" });
  button(shown, "Draft the change").click();
  await settle();
  assert.deepEqual(calls.at(-1), ["authoring.draft", { source: WORLD, proposal: "hand-1", accept: ["c1"], distinct: [], topology: "coast-map" }]);
  assert.ok(group(world, "The change set of hand-1").textContent.includes("one new revision of the world's map"));
  root.remove();
});

test("the hand builder keeps the focus where it was, and moves it to a neighbour after a Remove", async () => {
  const root = await mounted((op) => {
    if (op === "authoring.source") return SOURCE;
    if (op === "authoring.upgrades") return UPGRADES;
    throw new Error("unexpected " + op);
  });
  document.body.append(root);  // focus is had only in the page
  const builder = () => group(section(root, "World suggestions"), "Build a proposal by hand");
  const keyed = (key) => builder().querySelector('[data-focus-key="authoring:' + key + '"]');
  button(builder(), "Add a place or group").click();
  same(document.activeElement, keyed("name:c1"));
  button(builder(), "Add a place or group").click();
  button(builder(), "Add a place or group").click();
  same(document.activeElement, keyed("name:c3"));
  const kind = keyed("kind:c2");
  kind.focus();
  kind.value = "organization";
  kind.dispatchEvent({ type: "change" });
  same(document.activeElement, keyed("kind:c2"), "a redraw keeps the focus on the same control");
  button(builder(), "Add a link").click();
  same(document.activeElement, keyed("relation:l1"));
  button(builder(), "Remove l1").click();
  same(document.activeElement, keyed("add-link"), "with no link left, the focus goes to adding one");
  button(builder(), "Remove c2").click();
  same(document.activeElement, keyed("name:c3"), "the next row's name");
  button(builder(), "Remove c3").click();
  same(document.activeElement, keyed("name:c1"), "else the row before");
  button(builder(), "Remove c1").click();
  same(document.activeElement, keyed("add-candidate"), "else adding another");
  for (const node of builder().querySelectorAll("input,select,textarea,button")) assert.ok(node.getAttribute("data-focus-key"), node.textContent);
  root.remove();
});
