// Sprint 115's Forks page without the app: its logic (the words a record, a review or a sync is said in, the
// request a declaration makes, the guard against late answers), and the page mounted with canned answers: a late
// preview never draws over a choice made since, an unpreviewed alternative is kept per campaign, a retried
// declaration reuses its operation id, a stale one is reviewed again, and what is typed while a request is on
// its way is kept. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
install("http://127.0.0.1:1");
const ui = await import(pathToFileURL(path.join(STATIC, "shell", "ui.js")).href);
const page = await import(pathToFileURL(path.join(STATIC, "pages", "forks", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "forks", "logic.js")).href);

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async (n = 20) => { for (let i = 0; i < n; i += 1) await tick(); };

const SCENE = { path: "campaigns/c1/turns/t-1.000001.json", revision: "sha256:" + "1".repeat(64), kind: "scene", id: "t-1.000001",
  label: "Wren gives Tam the iron key.", chat: "campaign:c1#chat/t-1", number: 1 };
const LATER = { path: "campaigns/c1/turns/t-1.000002.json", revision: "sha256:" + "2".repeat(64), kind: "scene", id: "t-1.000002",
  label: "Tam says: I will pay Ines.", chat: "campaign:c1#chat/t-1", number: 2 };
const PAID = { path: "campaigns/c1/accepted/e-1.json", revision: "sha256:" + "3".repeat(64), kind: "accepted_event", id: "e-1",
  label: "tam pays ines.", chat: "campaign:c1#chat/t-1" };
const UNAVAILABLE = ["Restore one device's scene as a retained fork", "Merge both versions of a scene", "Repair what depends on a changed scene",
  "Write a continuation", "Promote an alternative into the story"].map((what) => ({ what, why: "no backend" }));

function scenesOf(campaign, etag = "sha256:" + "a".repeat(64)) {
  return { campaign, etag, coverage: "recorded_dependencies_only", records: [PAID, SCENE, LATER], dependencies: [], problems: [],
    declarable: ["dialogue", "causal_action", "asset"], actions: ["edit", "cut", "replace", "replay", "fork"], unavailable: UNAVAILABLE };
}
const NOTHING_DIVERGENT = { campaign: "c1", device: "desktop", peers: [], scenes: [], ancestry: [], held: [], unknown: false,
  retained: [], problems: [], fork_choices: [], unavailable: UNAVAILABLE.slice(0, 2) };
const preview = (request, impacts) => ({ campaign: "c1", etag: "x", action: request.action, roots: request.roots.map((item) => item.path),
  complete: true, problems: [], impacts, corrections: [], corrections_refused: null, rehearsal: null, written: false });
const impact = (target, certainty) => ({ target, reason: "scene", certainty, chain: [SCENE.path, target.path] });

// The page mounted in the director's view of `campaign`; `answers[op](args)` answers the story ops (and may
// return a promise the test settles). `drafts` is the shell's store, kept across mounts.
function mounted(answers, { campaign = "c1", drafts = new Map(), notes = [] } = {}) {
  const root = ui.el("div", {});
  const calls = [];
  const ctx = {
    ui, path: [campaign], query: new URLSearchParams({ audience: "director" }),
    href: (route, segments = []) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/"),
    registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {},
    notify: (text) => notes.push(text),
    draft: (key) => ({ key, get: () => drafts.get(key), set: (text) => drafts.set(key, text), clear: () => drafts.delete(key) }),
    act: async (work) => { try { return await work(); } catch (error) { notes.push(ui.errorText(error)); return undefined; } },
    api: async (op, args) => {
      calls.push({ op, args });
      if (op === "campaign.chats") return { complete: true, campaigns: [{ campaign, chats: [{ chat: "campaign:" + campaign + "#chat/t-1" }] }] };
      if (op === "forks.divergent") return answers.divergent ? answers.divergent(args) : NOTHING_DIVERGENT;
      if (op === "forks.scenes") return answers.scenes ? answers.scenes(args) : scenesOf(campaign);
      const name = op.replace("forks.", "");
      if (answers[name]) return answers[name](args);
      throw new Error("unexpected " + op);
    },
  };
  const done = page.mount(root, ctx);
  return { root, done, calls, notes };
}

const retcon = (root) => root.querySelector('section[aria-label="Review a retcon or fork"]');
const region = (root) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "What it would touch");
const box = (root, path_) => retcon(root).querySelectorAll("input").find((node) => node.getAttribute("value") === path_);
const button = (root, label) => root.querySelectorAll("button").find((node) => node.textContent === label);
const field = (root, text) => {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  return root.querySelectorAll("textarea,input,select").find((node) => node.getAttribute("id") === label.getAttribute("for"));
};
function tick_(root, path_, on = true) {
  const node = box(root, path_);
  node.checked = on;
  node.dispatchEvent({ type: "change" });
}
function type(node, text) { node.value = text; node.dispatchEvent({ type: "input" }); }
function choose(root, label, value) { const node = field(root, label); node.value = value; node.dispatchEvent({ type: "change" }); }

test("a record, a reason and a certainty are said in words", () => {
  assert.equal(logic.recordText(SCENE), "Scene 1: Wren gives Tam the iron key.");
  assert.equal(logic.recordText(PAID), "Accepted event: tam pays ines.");
  assert.equal(logic.recordText({ kind: "relationship", id: "r-1", path: "p" }), "Relationship: r-1");
  assert.equal(logic.recordText(null), "a record no longer in this campaign");
  assert.equal(logic.recordText({ ...SCENE, label: "x".repeat(200) }).length, "Scene 1: ".length + 90);
  assert.equal(logic.certaintyText("known"), "Known");
  assert.equal(logic.certaintyText("potential"), "Possible");
  assert.equal(logic.reasonText("possession"), "what someone holds because of it");
  assert.equal(logic.reasonText("something_new"), "something_new", "an unknown reason is shown as it is, never dropped");
  assert.equal(logic.count(1, "record"), "1 record");
  assert.equal(logic.count(4, "record"), "4 records");
});

test("taking another device's version is said with what it would break, and an unreadable review as such", () => {
  assert.equal(logic.takingText("phone", null), "Its version from phone cannot be read here.");
  assert.equal(logic.takingText("phone", { reasons: [], uncertain: false }), "Taking phone's version here leaves no recorded premise unmet.");
  assert.match(logic.takingText("phone", { reasons: ["accepted_scene_changed", "legacy_scene_history"], uncertain: true }),
    /would break what rests on it: an accepted event here rests on this scene.*; .*ancestry is uncertain\.$/);
  assert.equal(logic.syncText({ peer: "phone", status: null, held: [] }), "No sync with phone since the app started.");
  assert.equal(logic.syncText({ peer: "phone", status: "conflicts", held: ["a", "b"] }), "The last sync with phone held 2 records of this campaign for review.");
  assert.match(logic.syncText({ peer: "phone", status: "incomplete", held: [] }), /did not finish/);
});

test("a declaration names two different records as shown, or nothing", () => {
  const records = [PAID, SCENE, LATER];
  assert.deepEqual(logic.declaration(records, PAID.path, LATER.path, "dialogue"), {
    reason: "dialogue", source: { path: PAID.path, revision: PAID.revision }, dependent: { path: LATER.path, revision: LATER.revision } });
  assert.equal(logic.declaration(records, PAID.path, PAID.path, "dialogue"), null, "a record does not rest on itself");
  assert.equal(logic.declaration(records, "", LATER.path, "dialogue"), null);
  assert.equal(logic.declaration(records, "campaigns/c1/turns/gone.json", LATER.path, "dialogue"), null);
  assert.equal(logic.declarationText({ source: PAID, dependent: LATER, reason: "dialogue", already_applied: false }),
    "Record that Scene 2: Tam says: I will pay Ines. rests on Accepted event: tam pays ines. (dialogue).");
  assert.match(logic.declarationText({ already_applied: true }), /already recorded/);
  assert.notEqual(logic.newOperationId(), logic.newOperationId());
});

test("the guard draws an answer only while nothing newer was asked", () => {
  const guard = logic.latest();
  const first = guard.next();
  assert.ok(guard.current(first));
  const second = guard.next();
  assert.ok(!guard.current(first) && guard.current(second));
  guard.bump();
  assert.ok(!guard.current(second), "a changed choice drops the answer on its way");
});

test("the table's view is told the story is the director's, and asks for nothing of it", async () => {
  const root = ui.el("div", {});
  const asked = [];
  const ctx = {
    ui, path: ["c1"], query: new URLSearchParams(), href: () => "#", registered: () => false, setTitle() {}, setCrumb() {},
    styles() {}, layout() {}, navigate() {}, notify() {}, draft: () => ({ key: "k", get() {}, set() {}, clear() {} }), act: async (work) => work(),
    api: async (op) => { asked.push(op); return { complete: true, campaigns: [] }; },
  };
  await page.mount(root, ctx);
  assert.deepEqual(asked, ["campaign.chats"], "no story op is asked for the table");
  assert.ok(root.textContent.includes("are the director's"));
  assert.equal(root.querySelectorAll("section")[0].querySelectorAll("button").length, 0);
});

test("a late preview for the records picked before is never drawn over the choice made since", async () => {
  let release;
  const late = new Promise((resolve) => { release = resolve; });
  let asked = 0;
  const { root, done } = mounted({ preview: (args) => {
    asked += 1;
    return asked === 1 ? late.then(() => preview(args, [impact(PAID, "known")])) : preview(args, [impact(LATER, "potential")]);
  } });
  await done;
  tick_(root, SCENE.path);
  button(retcon(root), "Preview").click();
  await settle();
  tick_(root, PAID.path);  // the choice changes while the first answer is on its way
  release();
  await settle();
  assert.equal(region(root), undefined, "the answer for the earlier choice is dropped");
  button(retcon(root), "Preview").click();
  await settle();
  assert.ok(region(root).textContent.includes("Scene 2"), "the answer for the choice made since is drawn");
  assert.ok(!region(root).textContent.includes("Accepted event"));
});

test("an alternative not yet previewed is kept for its campaign, and only there", async () => {
  const drafts = new Map();
  const one = mounted({}, { drafts });
  await one.done;
  type(field(retcon(one.root), "Your alternative, in your own words (optional)"), "The key was only hidden.");
  const other = mounted({}, { campaign: "c2", drafts });
  await other.done;
  assert.equal(field(retcon(other.root), "Your alternative, in your own words (optional)").value, "", "another campaign's form is empty");
  const back = mounted({}, { drafts });
  await back.done;
  assert.equal(field(retcon(back.root), "Your alternative, in your own words (optional)").value, "The key was only hidden.");
});

test("a declaration retried after a failure is made with the operation id it was reviewed with", async () => {
  let commits = 0;
  const plan = (args) => ({ plan: { operation: "d", already_applied: false, source: PAID, dependent: LATER, reason: args.reason,
    files: ["campaigns/c1/continuity/d.json"], reaches: [] }, scenes: scenesOf("c1") });
  const { root, done, calls, notes } = mounted({ declare_plan: plan, declare_commit: () => {
    commits += 1;
    if (commits === 1) throw Object.assign(new Error("the connection dropped"), { reason: "unreachable" });
    return { done: { operation: "d", already_applied: true }, scenes: scenesOf("c1") };
  } });
  await done;
  const form = () => retcon(root).querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Record what rests on what");
  choose(form(), "This record", PAID.path);
  choose(form(), "rests on it (the record that depends)", LATER.path);
  button(form(), "Review").click();
  await settle();
  const review = () => retcon(root).querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Review this change");
  button(review(), "Make this change").click();
  await settle();
  assert.ok(review(), "a failed commit leaves the review up to retry");
  button(review(), "Make this change").click();
  await settle();
  const made = calls.filter((call) => call.op === "forks.declare_commit").map((call) => call.args);
  assert.equal(made.length, 2);
  assert.equal(made[0].operation_id, made[1].operation_id, "the retry names the same operation");
  assert.equal(made[0].expected, made[1].expected, "and the snapshot revision as reviewed");
  assert.equal(made[0].operation_id, calls.find((call) => call.op === "forks.declare_plan").args.operation_id);
  assert.ok(notes.some((note) => note.includes("already recorded")), notes.join("|"));
  assert.ok(!review(), "the review is closed once made");
});

test("a declaration the campaign moved past is reviewed again, never made", async () => {
  let reads = 0;
  const { root, done, calls } = mounted({
    scenes: () => { reads += 1; return scenesOf("c1", "sha256:" + String(reads).repeat(64)); },
    declare_plan: (args) => ({ plan: { operation: "d", already_applied: false, source: PAID, dependent: LATER, reason: args.reason,
      files: ["f"], reaches: [] }, scenes: scenesOf("c1") }),
    declare_commit: () => { throw Object.assign(new Error("the snapshot changed"), { reason: "changed" }); },
  });
  await done;
  const form = () => retcon(root).querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Record what rests on what");
  choose(form(), "This record", PAID.path);
  choose(form(), "rests on it (the record that depends)", LATER.path);
  button(form(), "Review").click();
  await settle();
  button(root, "Make this change").click();
  await settle();
  assert.ok(form(), "the form is back, to review against what the campaign is now");
  assert.equal(reads, 2, "the records were read again");
  assert.equal(calls.filter((call) => call.op === "forks.declare_commit").length, 1);
});

test("what is typed while a request is on its way is kept when its answer is drawn", async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { root, done } = mounted({ preview: (args) => pending.then(() => preview(args, [impact(PAID, "known")])) });
  await done;
  tick_(root, SCENE.path);
  button(retcon(root), "Preview").click();
  await settle();
  const alternative = field(retcon(root), "Your alternative, in your own words (optional)");
  type(alternative, "Typed while waiting.");
  release();
  await settle();
  assert.ok(region(root), "the answer is drawn");
  assert.equal(field(retcon(root), "Your alternative, in your own words (optional)").value, "Typed while waiting.");
  assert.ok(box(root, SCENE.path).checked, "the record picked stays picked");
});

test("a divergent scene's card picks it for a preview, and keeping one side is refused with the reason", async () => {
  const divergent = { ...NOTHING_DIVERGENT, peers: [{ peer: "phone", status: "conflicts", held: [SCENE.path, PAID.path] }],
    held: [{ path: PAID.path, kind: "accepted_event" }],
    scenes: [{ path: SCENE.path, kind: "scene", chat: SCENE.chat, number: 1, continues: null, uncertain: false, keep_one: "refused",
      detected: ["phone"], peers: ["phone"], their_deletion: false,
      mine: { etag: "a", deleted: false, peers: [], said: SCENE.label, set_aside: false, unreadable: false },
      theirs: [{ etag: "b", deleted: false, peers: ["phone"], said: "The key was destroyed.", set_aside: false, unreadable: false }],
      taking_theirs: [{ reasons: ["accepted_scene_changed"], uncertain: false, scenes: [SCENE.path], endpoints: [] }] }] };
  const { root, done, calls } = mounted({ divergent: () => divergent, preview: (args) => preview(args, [impact(PAID, "known")]) });
  await done;
  const article = root.querySelectorAll("article").find((node) => node.getAttribute("aria-label") === "Divergent: Scene 1 · t-1");
  assert.ok(article.textContent.includes("The key was destroyed.") && article.textContent.includes(SCENE.label));
  assert.ok(article.textContent.includes("Keeping one side: refused."));
  assert.ok(!article.textContent.includes("Refused: its ancestry"), "a certain ancestry is not called uncertain");
  button(article, "Preview a retcon of this scene").click();
  await settle();
  assert.ok(box(root, SCENE.path).checked, "the scene is shown picked");
  const asked = calls.filter((call) => call.op === "forks.preview").map((call) => call.args);
  assert.deepEqual(asked.at(-1).roots, [{ path: SCENE.path, revision: SCENE.revision }]);
  assert.ok(region(root).textContent.includes("Accepted event: tam pays ines."));
  for (const item of UNAVAILABLE) assert.ok(root.textContent.includes(item.what + " is not available yet."), item.what);
  assert.equal(root.querySelectorAll("button").filter((node) => /Merge|Restore|Repair|Promote|Write a continuation/.test(node.textContent)).length, 0,
    "nothing unavailable is offered as a control");
});

test("a review that cannot be read says why, and the divergent scenes are still shown", async () => {
  let reads = 0;
  const { root, done } = mounted({ scenes: () => {
    reads += 1;
    if (reads === 1) throw Object.assign(new Error("the campaign's records cannot be read"), { reason: "unreadable" });
    return scenesOf("c1");
  } });
  await done;
  assert.ok(retcon(root).textContent.includes("unreadable: the campaign's records cannot be read"));
  assert.ok(root.querySelector('section[aria-label="Divergent offline scenes"]').textContent.includes("No scene of this campaign is kept in two versions"));
  button(retcon(root), "Read again").click();
  await settle();
  assert.ok(box(root, SCENE.path), "read again, the records are offered");
});

// A kept scene as forks.divergent answers it; `extra` overrides.
const divergentScene = (path_, number, extra = {}) => ({ path: path_, kind: "scene", chat: SCENE.chat, number, keep_one: "refused",
  detected: ["phone"], peers: ["phone"], their_deletion: false, deleted_by: [], taking_deletion: null, uncertain: false, continues: null,
  mine: { etag: "a", deleted: false, peers: [], said: "mine", set_aside: false, unreadable: false },
  theirs: [{ etag: "b", deleted: false, peers: ["phone"], said: "theirs", set_aside: false, unreadable: false }],
  taking_theirs: [{ reasons: [], uncertain: false, scenes: [], endpoints: [] }], ...extra });
const article = (root, label) => root.querySelectorAll("article").find((node) => node.getAttribute("aria-label") === label);

test("uncertain ancestry and a divergent continuation are shown as refused, with why", async () => {
  // the real server's shape for a legacy payment (test_app_forks.py): 049's admission check sees nothing unmet,
  // and the uncertainty is the snapshot's, in `uncertain` and `ancestry`
  const ANCESTRY = "campaigns/c1/accepted/e-1.json: incomplete scene provenance: invalid or unsupported continuity_sources";
  const { root, done } = mounted({ divergent: () => ({ ...NOTHING_DIVERGENT, ancestry: [ANCESTRY],
    scenes: [divergentScene(SCENE.path, 1, { uncertain: true }), divergentScene(LATER.path, 2, { uncertain: true, continues: SCENE.path })] }) });
  await done;
  for (const label of ["Divergent: Scene 1 · t-1", "Divergent: Scene 2 · t-1"]) {
    const found = article(root, label);
    assert.ok(found.textContent.includes("Refused: its ancestry is uncertain"), label);
    assert.ok(!found.textContent.includes("leaves no recorded premise unmet"), "nothing calls taking it safe: " + found.textContent);
    assert.ok(found.textContent.includes("Taking phone's version here cannot be shown safe"));
  }
  assert.ok(article(root, "Divergent: Scene 2 · t-1").textContent.includes("Refused: a divergent continuation."));
  assert.ok(!article(root, "Divergent: Scene 1 · t-1").textContent.includes("divergent continuation"));
  const listed = root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Uncertain ancestry");
  assert.ok(listed && listed.textContent.includes(ANCESTRY), "the ancestry problems are listed");
});

test("a version that cannot be reviewed here counts as uncertain, and a certain one is not refused for it", async () => {
  const { root, done } = mounted({ divergent: () => ({ ...NOTHING_DIVERGENT,
    scenes: [divergentScene(SCENE.path, 1, { taking_theirs: [null] }), divergentScene(LATER.path, 2)] }) });
  await done;
  const first = article(root, "Divergent: Scene 1 · t-1");
  assert.ok(first.textContent.includes("Its version from phone cannot be read here.") && first.textContent.includes("Refused: its ancestry is uncertain"));
  const second = article(root, "Divergent: Scene 2 · t-1");
  assert.ok(second.textContent.includes("Taking phone's version here leaves no recorded premise unmet.") && !second.textContent.includes("ancestry is uncertain"));
});

test("a deletion is shown under the devices that deleted it, with what taking it would break", async () => {
  const { root, done } = mounted({ divergent: () => ({ ...NOTHING_DIVERGENT,
    scenes: [divergentScene(SCENE.path, 1, { theirs: [], taking_theirs: [], their_deletion: true, peers: ["phone", "tablet"],
      deleted_by: ["tablet"], breaks_recorded: true,
      taking_deletion: { reasons: ["accepted_scene_missing"], uncertain: false, breaks_recorded: true, scenes: [], endpoints: [] } }),
    divergentScene(LATER.path, 2, { mine: { etag: null, deleted: true, peers: [], said: null, set_aside: false, unreadable: false } })] }) });
  await done;
  const deleted = article(root, "Divergent: Scene 1 · t-1");
  const sides = deleted.querySelectorAll("div").filter((node) => node.classList.contains("forks-side"));
  assert.deepEqual(sides.map((node) => node.querySelector("div").textContent), ["This device", "tablet"], "titled by who deleted it, not every peer");
  assert.equal(sides[1].querySelector("p").textContent, "Deleted on tablet.");
  assert.ok(deleted.textContent.includes("Taking the deletion from tablet here would break what rests on it: an accepted event rests on a scene that would be missing."));
  assert.ok(deleted.textContent.includes("Refused: it would leave a recorded premise missing."), "a deletion leaving an event without its scene is refused");
  assert.ok(!deleted.textContent.includes("ancestry is uncertain") && !deleted.textContent.includes("not on record"),
    "what rests on it is on record: it is never called uncertain");
  const mine = article(root, "Divergent: Scene 2 · t-1").querySelectorAll("div").filter((node) => node.classList.contains("forks-side"))[0];
  assert.equal(mine.querySelector("p").textContent, "Deleted on this device.");
});

test("a review refused because the records moved on reads them again, and the next review is of now", async () => {
  let reads = 0;
  const { root, done, calls } = mounted({
    scenes: () => { reads += 1; return scenesOf("c1", "sha256:" + String(reads).repeat(64)); },
    declare_plan: (args) => {
      if (args.expected === "sha256:" + "1".repeat(64)) throw Object.assign(new Error("the records changed"), { reason: "changed" });
      return { plan: { operation: "d", already_applied: false, source: PAID, dependent: LATER, reason: args.reason, files: ["f"], reaches: [] },
        scenes: scenesOf("c1", args.expected) };
    },
  });
  await done;
  const form = () => retcon(root).querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Record what rests on what");
  choose(form(), "This record", PAID.path);
  choose(form(), "rests on it (the record that depends)", LATER.path);
  button(form(), "Review").click();
  await settle();
  assert.equal(reads, 2, "the records were read again");
  button(form(), "Review").click();  // the picks are kept across the reload
  await settle();
  const sent = calls.filter((call) => call.op === "forks.declare_plan").map((call) => call.args.expected);
  assert.deepEqual(sent, ["sha256:" + "1".repeat(64), "sha256:" + "2".repeat(64)], "the second review names the revision read since");
  assert.ok(retcon(root).querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Review this change"));
});

test("taking a version is said with its deletion and its uncertainty", () => {
  assert.equal(logic.takingText("phone", { reasons: [] }, { uncertain: true }),
    "Taking phone's version here cannot be shown safe: what rests on this scene is not on record.");
  assert.equal(logic.takingText("tablet", null, { deletion: true }), "Taking the deletion from tablet cannot be reviewed here.");
  assert.equal(logic.takingText("tablet", { reasons: [] }, { deletion: true }), "Taking the deletion from tablet here leaves no recorded premise unmet.");
});

test("a retained fork says whether it waits on sync, was reconciled, or play went on since", async () => {
  const fork = (operation, extra) => ({ operation, source_device: "desktop", devices: ["desktop", "phone"], source_revisions: 1,
    scenes: [{ path: SCENE.path, number: 1, chat: SCENE.chat, here: "restored",
      variants: [{ device: "desktop", selected: true, said: "Wren gives Tam the iron key." }, { device: "phone", selected: false, said: "The key is lost." }] }],
    ...extra });
  const { root, done } = mounted({ divergent: () => ({ ...NOTHING_DIVERGENT, retained: [
    fork("keep-1", { state: "waiting", reconciled_here: false }), fork("keep-2", { state: "reconciled", reconciled_here: true }),
    fork("keep-3", { state: "played_on", reconciled_here: true }), fork("keep-4", { state: "elsewhere", reconciled_here: false }),
    fork("keep-5", { reconciled_here: true })] }) });
  await done;
  const said = (operation) => root.querySelectorAll("article").find((node) => node.getAttribute("aria-label") === "Retained fork: " + operation);
  const pill = (operation) => said(operation).querySelector("span").textContent;
  assert.equal(pill("keep-1"), "Waiting on sync");
  assert.equal(pill("keep-2"), "Reconciled here");
  assert.equal(pill("keep-3"), "Reconciled, played on since", "a later turn is never shown as a fork still waiting");
  assert.ok(said("keep-3").textContent.includes("nothing of the fork is waiting"));
  assert.equal(pill("keep-4"), "Made on other devices");
  assert.ok(said("keep-4").textContent.includes("This fork was made between desktop and phone"));
  assert.equal(pill("keep-5"), "Reconciled here", "an answer without a state still reads");
});

test("a preview refused because the records moved on leaves no earlier report drawn as current", async () => {
  let reads = 0, asked = 0;
  const { root, done } = mounted({
    scenes: () => { reads += 1; return scenesOf("c1", "sha256:" + String(reads).repeat(64)); },
    preview: (args) => {
      asked += 1;
      if (asked === 2) throw Object.assign(new Error("the records changed"), { reason: "changed" });
      return preview(args, [impact(PAID, "known")]);
    },
  });
  await done;
  tick_(root, SCENE.path);
  button(retcon(root), "Preview").click();
  await settle();
  assert.ok(region(root), "the first report is drawn");
  button(retcon(root), "Preview").click();
  await settle();
  assert.equal(reads, 2, "the records were read again");
  assert.equal(region(root), undefined, "the report of the old records is gone");
  button(retcon(root), "Preview").click();
  await settle();
  assert.ok(region(root), "a preview of the records now is drawn");
});

test("a preview refused because a picked record is gone reads the records again and drops the pick", async () => {
  let reads = 0, asked = 0;
  const { root, done } = mounted({
    scenes: () => {
      reads += 1;
      const now = scenesOf("c1", "sha256:" + String(reads).repeat(64));
      return reads === 1 ? now : { ...now, records: [PAID, LATER] };
    },
    preview: (args) => {
      asked += 1;
      if (asked === 2) throw Object.assign(new Error("a record is gone"), { reason: "missing_record" });
      return preview(args, [impact(PAID, "known")]);
    },
  });
  await done;
  tick_(root, SCENE.path);
  button(retcon(root), "Preview").click();
  await settle();
  assert.ok(region(root), "the first report is drawn");
  button(retcon(root), "Preview").click();
  await settle();
  assert.equal(reads, 2, "the records were read again");
  assert.equal(region(root), undefined, "the report of the old records is gone");
  assert.equal(box(root, SCENE.path), undefined, "the record that is gone is no longer offered");
});
