// Sprint 116's Time page without the app: its logic (exact spans of campaign time, ids, the words each change is
// reviewed in, a commitment's deadline state, the coverage rows), and the page with canned answers: the table's
// view reads nothing of the director's; an uninitialized clock is said, with no activity offered and no estimate;
// a commit refused as changed reads everything again and drops the stale review; and the owner's own character's
// decision is made only with the owner's key, given on the review.
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
const page = await import(pathToFileURL(path.join(STATIC, "pages", "time", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "time", "logic.js")).href);

const CHAT = "campaign:c1#chat/t1";
const ANYA = { actor: "campaign:c1/character/anya", name: "Anya Rook", kind: "character" };
const MARA = { actor: "campaign:c1/character/mara", name: "Mara Vell", kind: "character" };
const TAMSIN = { actor: "campaign:c1/character/tamsin", name: "Tamsin Hale", kind: "character" };
const LISTED = { complete: true, campaigns: [{ campaign: "c1", chats: [{ chat: CHAT }] }] };
const NOW = { date: "1024-03-01", time: "10:00", fixed_day: 1, minute: 600 };
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async () => { for (let n = 0; n < 30; n += 1) await tick(); };

function overview({ started = true, etag = "e1", commitments = [] } = {}) {
  return {
    campaign: "c1", chat: CHAT, etag, owner: "human:owner",
    clock: started ? { started: true, calendar: { provider: "gregorian" }, now: NOW, deadlines: [] } : { started: false, calendar: null, now: null, deadlines: [] },
    cast: [{ ...ANYA, controller: "human:owner", role: "player", downtime: { available: true, controller: "human:owner" } },
      { ...MARA, controller: "director", role: "npc", downtime: { available: true, controller: "director" } },
      { ...TAMSIN, controller: "human:bob", role: "player",
        downtime: { available: false, controller: "human:bob", reason: "not_your_character", detail: "a character human:bob plays is theirs" } }],
    downtime: { etag, started, activities: [], kinds: ["other", "study"] },
    offscreen: { etag, started, plans: [], pending: [], skips: [], max_budget: 32, max_steps: 16, priority: [0, 100],
      adoption: { available: false, reason: started ? "no_group" : "uninitialized", detail: started ? "No group." : "Start the clock first." } },
    commitments: { etag, commitments, problems: [], citable: [{ event: "ev-1", summary: "Anya promises", at: 4, turn_number: 2 }],
      citable_total: 1, max_cites: 16, kinds: ["promise", "mission", "objective"], decisions: ["abandoned", "accepted", "refused"],
      outcomes: ["fulfilled", "lapsed", "partially_fulfilled"], parties: [ANYA, MARA], owner: "human:owner" },
  };
}

function promise(deciders) {
  const offer = { operation: "o1", status: "offered", support: "supported", outcome: "applied", cited: [{ event: "ev-1", summary: "Anya promises" }], clock_at: null, note: "" };
  return { commitment: "map", kind: "promise", summary: "Bring the map", owed_by: [{ ...ANYA, controller: "human:owner" }], owed_to: [MARA],
    status: "offered", certain: true, overdue: false, late: false, at: 4, deadline: null, offer, steps: [], next: ["accepted", "lapsed", "refused"],
    deciders, voidable: [] };
}

// The page mounted for c1's chat in `audience`; `answer(op, args)` answers every time.* op it is asked.
async function mounted(audience, answer) {
  const root = ui.el("div", {});
  document.body.append(root);
  const asked = [];
  const notes = [];
  const ctx = {
    ui, path: ["c1"], query: new URLSearchParams({ chat: CHAT, ...(audience === "director" ? { audience } : {}) }),
    href: (route, segments = []) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/"),
    registered: () => false, setTitle() {}, setCrumb() {}, styles() {}, layout() {}, navigate() {}, notify: (text) => notes.push(text),
    draft: (key) => ({ key, get: () => undefined, set() {}, clear() {} }),
    act: async (work) => { try { return await work(); } catch (error) { notes.push(error.reason || String(error)); return undefined; } },
    api: async (op, args) => {
      asked.push({ op, args });
      if (op === "campaign.chats") return LISTED;
      if (op === "time.coverage") return { features: [] };
      return answer(op, args);
    },
  };
  await page.mount(root, ctx);
  return { root, asked, notes };
}

const card = (root, title) => root.querySelector('section[aria-label="' + title + '"]');
const button = (root, label) => root.querySelectorAll("button").find((node) => node.textContent === label);
const group = (root, title) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === title);
const review = (root) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Review this change");
function control(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}
function type(root, label, value) { const node = control(root, label); node.value = value; node.dispatchEvent({ type: "input" }); }

test("a span of campaign time is exact whole days and hours, never an estimate", () => {
  assert.equal(logic.spanMinutes("20", ""), 20 * 1440);
  assert.equal(logic.spanMinutes("", "3"), 180);
  assert.equal(logic.spanMinutes("1", "2"), 1440 + 120);
  for (const [days, hours] of [["0", "0"], ["", ""], ["1.5", ""], ["-1", ""], ["about 3", ""], ["2-3", ""]]) {
    assert.equal(logic.spanMinutes(days, hours), null, days + "/" + hours);
  }
  assert.equal(logic.spanText(20 * 1440), "20 days");
  assert.equal(logic.spanText(1440 + 180 + 1), "1 day 3 hours 1 minute");
  assert.equal(logic.spanText(0), "no time");
  assert.equal(logic.parseTime("09:30"), 570);
  assert.equal(logic.parseTime("24:00"), null);
});

test("a new record's id is its words as a slug with a unique tail", () => {
  assert.equal(logic.slug("  Learn the tide-cant!  "), "learn-the-tide-cant");
  assert.equal(logic.slug("???"), "");
  assert.match(logic.recordId("Bring the map", "commitment"), /^bring-the-map-[a-z0-9]{1,6}$/);
  assert.match(logic.recordId("???", "commitment"), /^commitment-[a-z0-9]{1,6}$/);
  assert.ok(logic.recordId("x".repeat(200), "c").length <= 47);
  assert.notEqual(logic.newOperationId(), logic.newOperationId());
});

test("each change is reviewed in words that say what it does, and a flag is only a flag", () => {
  const before = NOW, after = { date: "1024-03-21", time: "10:00" };
  const pursued = logic.downtimeLines({ operation: "pursue" }, { elapsed: 28800, effort: 28800, credited: 28800, completes: true, before, after,
    flags: [{ deadline: "map-due", label: "Map due" }] }, { activity: { intent: "Learn the tide cant" } });
  assert.deepEqual(pursued, [
    "Spend 20 days on “Learn the tide cant”: the clock moves from 1024-03-01 10:00 to 1024-03-21 10:00, and 20 days of effort is credited.",
    "It completes the activity.",
    "It crosses Map due: that is a flag only. Nothing happens by itself; decide in play what follows."]);
  const less = logic.downtimeLines({ operation: "pursue" }, { elapsed: 1440, effort: 60, credited: 60, completes: false, before, after, flags: [] }, { activity: { intent: "x" } });
  assert.ok(less[0].includes("less effort than the time that passes") && less[1] === "It does not complete the activity yet.");
  assert.match(logic.downtimeLines({ operation: "begin" }, { already_applied: true })[0], /already made/);
  const refused = logic.commitmentLines({ operation: "record" }, { kind: "record", status: "refused", from: "offered", decided_by: [MARA], note: "",
    cites: [{ event: "ev-2", summary: "Mara says no" }] }, { commitment: { summary: "Bring the map" } });
  assert.deepEqual(refused, ["Record “Bring the map” as refused, as Mara Vell decided (it is offered now).", "Citing: Mara says no."]);
  const skip = logic.offscreenLines({}, { kind: "skip", before, after, budget: 4, developments: [{ plan: "docks", step: "step-1", label: "Bribe", due: NOW }],
    deferred: [], deadlines: [{ plan: "docks", deadline: "map-due", outcome: "missed", label: "Map due" }] });
  assert.ok(skip.includes("docks missed Map due."));
  assert.ok(skip.at(-1).includes("delivers no knowledge"), "a development is the director's alone");
});

test("a commitment's deadline is read as the calendar holds it now, never as an all-clear", () => {
  const due = { deadline: "map-due", found: true, label: "Map due", passed: true, due: { date: "1024-03-10", time: "00:00" } };
  assert.equal(logic.deadlineState({ deadline: due, overdue: false, late: false }).state, "passed");
  assert.equal(logic.deadlineState({ deadline: due, overdue: true, late: false }).state, "overdue");
  assert.equal(logic.deadlineState({ deadline: due, overdue: false, late: true }).state, "late");
  assert.equal(logic.deadlineState({ deadline: { ...due, passed: false }, overdue: false, late: false }).text, "Due by Map due, 1024-03-10 00:00.");
  assert.equal(logic.deadlineState({ deadline: { deadline: "gone", found: false }, overdue: false, late: false }).state, "missing");
  assert.equal(logic.deadlineState({ deadline: null }).state, "none");
  assert.equal(logic.outcomeText("applied"), "");
  assert.match(logic.outcomeText("unsupported"), /^Skipped/);
  assert.deepEqual(logic.recordable({ next: ["accepted", "lapsed"], deciders: { available: false } }, ["lapsed"]), ["lapsed"]);
});

test("coverage rows are here, on a registered screen, or waiting with a reason", () => {
  const rows = logic.coverageRows([
    { slice: "074", status: "here" }, { slice: "081", status: "screen", route: "story" },
    { slice: "085", status: "screen", route: "providers" }, { slice: "075", status: "unavailable", reason: "later" }], (route) => route === "story");
  assert.deepEqual(rows.here.map((item) => item.slice), ["074"]);
  assert.deepEqual(rows.elsewhere.map((item) => item.slice), ["081"]);
  assert.deepEqual(rows.waiting.map((item) => [item.slice, item.reason]), [["085", "its page is not in this app"], ["075", "later"]]);
});

test("the table's view reads nothing of the director's and offers nothing", async () => {
  const { root, asked } = await mounted("table", () => { throw new Error("the table reads no time op"); });
  assert.deepEqual(asked.map((item) => item.op), ["campaign.chats", "time.coverage"]);
  assert.ok(card(root, "Time and commitments").textContent.includes("director's"));
  assert.equal(card(root, "Time and commitments").querySelectorAll("button").length, 0);
  assert.ok(!card(root, "Downtime") && !card(root, "Commitments"));
});

test("an uninitialized clock is said, and no time is spent or estimated", async () => {
  const { root } = await mounted("director", (op) => {
    assert.equal(op, "time.overview");
    const answer = overview({ started: false });
    answer.downtime.activities = [{ activity: "a1", actor: ANYA, kind: "study", intent: "Learn the tide cant", required: 60, progress: 0,
      remaining: 60, status: "active", outcome: "", settled_by: null, settled_at: null, log: [], whose: { available: true, controller: "human:owner" } }];
    return answer;
  });
  assert.ok(card(root, "Campaign clock").textContent.includes("not started yet"));
  const downtime = card(root, "Downtime");
  assert.ok(downtime.textContent.includes("Pursuing an activity is not available yet."));
  const groups = downtime.querySelectorAll("div").map((node) => node.getAttribute("aria-label")).filter(Boolean);
  assert.ok(groups.includes("Begin an activity") && groups.includes("Abandon an activity"), "beginning spends no time");
  assert.ok(!groups.includes("Pursue an activity"), "nothing is pursued without the clock");
  assert.ok(card(root, "Offscreen plans").textContent.includes("Start the clock first."));
});

test("a commit refused as changed reads everything again and drops the review", async () => {
  let reads = 0;
  const { root, notes } = await mounted("director", (op, args) => {
    if (op === "time.overview") { reads += 1; return overview({ etag: "e" + reads }); }
    if (op === "time.downtime_plan") {
      assert.equal(args.expected, "e1");
      return { plan: { kind: "begin", activity: args.activity, already_applied: false, before: NOW, after: NOW, flags: [], files: ["campaigns/c1/scope.json"] }, etag: "e1" };
    }
    if (op === "time.downtime_commit") { const error = new Error("changed"); error.reason = "changed"; throw error; }
    throw new Error("unexpected " + op);
  });
  const begin = root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Begin an activity");
  type(begin, "What they set out to do", "Learn the tide cant");
  type(begin.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Effort it takes"), "Days", "20");
  button(begin, "Review").click();
  await settle();
  assert.ok(review(root).textContent.includes("Begin “Learn the tide cant” (study) for Anya Rook: 20 days of effort required."));
  button(review(root), "Make this change").click();
  await settle();
  assert.equal(reads, 2, "read again");
  assert.ok(!review(root), "the stale review is gone");
  assert.deepEqual(notes, ["changed"]);
  const again = group(root, "Begin an activity");
  assert.equal(control(again, "What they set out to do").value, "Learn the tide cant", "what was typed is still there");
  assert.equal(control(group(again, "Effort it takes"), "Days").value, "20");
  assert.equal(document.activeElement, control(again, "For"), "the focus is back on the form");
});

test("the owner's own character's decision is made only with the owner's key", async () => {
  const sent = [];
  const { root, notes } = await mounted("director", (op, args) => {
    if (op === "time.overview") return overview({ commitments: [promise({ available: true, keys: [ANYA] })] });
    if (op === "time.commitment_plan") {
      return { plan: { kind: "record", commitment: "map", status: args.status, from: "offered", note: "", already_applied: false, keys: [ANYA],
        reviewed_controls: [["anya", "human:owner"]], backing: [{ ...ANYA, turn: "t1.2", turn_number: 2, text: "I promise to bring the map." }],
        decided_by: [{ ...ANYA, controller: "human:owner" }], cites: [{ event: "ev-1", summary: "Anya promises" }], files: ["campaigns/c1/scope.json"] }, etag: "e1" };
    }
    if (op === "time.commitment_commit") { sent.push(args); return { done: { already_applied: false } }; }
    throw new Error("unexpected " + op);
  });
  const item = card(root, "Commitments").querySelector("article");
  assert.ok(item.textContent.includes("A decision for Anya Rook is yours"));
  const box = item.querySelectorAll("input").find((node) => node.getAttribute("value") === "ev-1");
  box.checked = true;
  box.dispatchEvent({ type: "change" });
  button(item, "Review").click();
  await settle();
  assert.ok(review(root), "reviewed: " + notes.join(" / "));
  assert.ok(review(root).textContent.includes("This is your own character's decision: making it gives your key for Anya Rook"));
  assert.ok(!button(review(root), "Make this change"));
  assert.ok(review(root).textContent.includes("These words of yours become the recorded backing of this decision:"));
  assert.ok(review(root).querySelector("blockquote").textContent === "I promise to bring the map.");
  assert.ok(review(root).querySelector("figcaption").textContent === "Anya Rook, turn 2");
  button(review(root), "Make this change with my key").click();
  await settle();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].endorse, true);
  assert.equal(sent[0].status, "accepted");
  assert.deepEqual(sent[0].reviewed_controls, [["anya", "human:owner"]], "the commit carries the controls it was reviewed under");
});

test("Cancel keeps what was typed and puts the focus back on the form", async () => {
  const { root } = await mounted("director", (op, args) => {
    if (op === "time.overview") return overview();
    if (op === "time.downtime_plan") {
      return { plan: { kind: "begin", activity: args.activity, already_applied: false, before: NOW, after: NOW, flags: [], files: ["campaigns/c1/scope.json"] }, etag: "e1" };
    }
    throw new Error("unexpected " + op);
  });
  const begin = group(root, "Begin an activity");
  control(begin, "For").value = MARA.actor;
  control(begin, "For").dispatchEvent({ type: "change" });
  type(begin, "What they set out to do", "Map the sewers");
  type(group(begin, "Effort it takes"), "Hours", "6");
  button(begin, "Review").click();
  await settle();
  assert.ok(review(root).textContent.includes("for Mara Vell: 6 hours of effort required"));
  assert.equal(document.activeElement, review(root), "the review takes the focus");
  button(review(root), "Cancel").click();
  await settle();
  const back = group(root, "Begin an activity");
  assert.equal(control(back, "For").value, MARA.actor);
  assert.equal(control(back, "What they set out to do").value, "Map the sewers");
  assert.equal(control(group(back, "Effort it takes"), "Hours").value, "6");
  assert.equal(document.activeElement, control(back, "For"));
});

test("a character another person plays is not offered an activity, and theirs are not run here", async () => {
  const { root } = await mounted("director", (op) => {
    assert.equal(op, "time.overview");
    const answer = overview();
    answer.downtime.activities = [{ activity: "a1", actor: TAMSIN, kind: "travel", intent: "Walk to the coast", required: 60, progress: 0,
      remaining: 60, status: "active", outcome: "", settled_by: null, settled_at: null, log: [],
      whose: { available: false, controller: "human:bob", reason: "not_your_character", detail: "a character human:bob plays is theirs" } }];
    return answer;
  });
  const downtime = card(root, "Downtime");
  const options = control(group(downtime, "Begin an activity"), "For").querySelectorAll("option").map((node) => node.textContent);
  assert.deepEqual(options, ["Anya Rook", "Mara Vell (NPC)"]);
  assert.ok(downtime.textContent.includes("An activity for Tamsin Hale is not available yet.a character human:bob plays is theirs"));
  const walk = downtime.querySelector("article");
  assert.ok(walk.textContent.includes("Pursuing or abandoning it here is not available yet."));
  assert.ok(!group(downtime, "Pursue an activity") && !group(downtime, "Abandon an activity"), "nothing of Tamsin's is run here");
});

test("only the first pending advance is offered to apply; the rest wait for it", async () => {
  const { root } = await mounted("director", () => {
    const answer = overview();
    answer.offscreen.pending = [{ advance: "a1", before: NOW, after: { date: "1024-03-02", time: "10:00" }, flags: [] },
      { advance: "a2", before: NOW, after: { date: "1024-03-03", time: "10:00" }, flags: [] }];
    return answer;
  });
  const offscreen = card(root, "Offscreen plans");
  assert.ok(group(offscreen, "Apply the advance to 1024-03-02 10:00"));
  assert.ok(!group(offscreen, "Apply the advance to 1024-03-03 10:00"));
  assert.ok(offscreen.textContent.includes("each later one waits for the one before it"));
});
