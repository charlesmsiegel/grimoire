// 081's story threads page with canned answers, in fakedom: the threads that match, one followed to how it
// ended, each entry graded and linked to its turn, the table's view with no canon in it, and the page's pure
// logic. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { byText, install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
install("http://127.0.0.1:1");
const ui = await import(pathToFileURL(path.join(STATIC, "shell", "ui.js")).href);
const page = await import(pathToFileURL(path.join(STATIC, "pages", "story", "page.js")).href);
const logic = await import(pathToFileURL(path.join(STATIC, "pages", "story", "logic.js")).href);

const CHAT = "campaign:c1#chat/t1";
const ANYA = "campaign:c1/character/anya", MARA = "campaign:c1/character/mara";
const anya = { ref: ANYA, name: "Anya" }, mara = { ref: MARA, name: "Mara" };
const PROMISE = "Mara will bring the key back before the thaw";
const SOLD = "Mara sells the key at the night market";
const source = (number, extra = {}) => ({ chat: CHAT, turn: "t1." + number, number, set_aside: false, ...extra });
const at = (sequence) => ({ sequence, scene: "t1", turn: "t1." + sequence });

const SUMMARY = { proposition: "p-key", text: PROMISE, first: at(1), last: 9, standing: "refuted", decided: at(9),
  entries: 5, scenes: 1, contradictions: 1, actors: [anya, mara] };
const DIRECTOR = {
  proposition: "p-key", text: PROMISE, stated: at(1), provenance: "director", actors: [anya, mara],
  entries: [
    { kind: "stated", record: "proposition:p-key", grade: "proposal", moment: at(1), text: PROMISE, actors: [mara], source: source(1) },
    { kind: "report", record: "evidence:told", grade: "proposal", moment: at(1), text: PROMISE, actors: [anya, mara], source: source(1),
      holder: anya, attributed_to: mara, confirmed: true, sender: mara, sent_at: null },
    { kind: "belief", record: "belief:b-keep", grade: "belief", moment: at(2), text: PROMISE, actors: [mara], source: source(2, { set_aside: true }),
      holder: mara, stance: "believed", current: true, inherited: false, supersedes: null },
    { kind: "event", record: "event:e-sold", grade: "revoked", moment: at(9), text: SOLD, actors: [mara], source: source(9) },
    { kind: "ruling", record: "ruling:r-broken", grade: "confirmed", moment: at(9), text: PROMISE, actors: [], source: source(9),
      standing: "refuted", grounds: ["event:e-sold"] },
  ],
  scenes: [{ chat: CHAT, scene: "t1", first: 1, last: 9, turns: ["t1.1", "t1.2", "t1.9"],
    records: ["proposition:p-key", "evidence:told", "belief:b-keep", "event:e-sold", "ruling:r-broken"], actors: [anya, mara] }],
  outcome: { standing: "refuted", ruling: "r-broken", moment: at(9), grounds: [{ subject: "event:e-sold", entry: null }],
    revoked_grounds: ["e-sold"] },
  contradictions: [{ kind: "belief_against_canon", records: ["ruling:r-broken", "belief:b-keep"], moment: at(2) },
    { kind: "revoked_ground", records: ["ruling:r-broken", "event:e-sold"], moment: at(9) }],
};
DIRECTOR.outcome.grounds[0].entry = DIRECTOR.entries[3];
const TABLE = { proposition: "p-key", text: PROMISE, actors: [anya, mara], outcome: null, contradictions: [],
  entries: [DIRECTOR.entries[1]].map(({ sender, sent_at, ...entry }) => entry),
  scenes: [{ chat: CHAT, scene: "t1", first: 1, last: 1, turns: ["t1.1"], records: ["evidence:told"], actors: [anya, mara] }] };

function context(path_, query, api, routes = ["chat", "campaign"]) {
  const seen = { went: [], title: null, crumb: [] };
  const ctx = {
    ui, path: path_, query: new URLSearchParams(query),
    href: (route, segments = [], query_ = {}) => "#/" + [route, ...segments.map(encodeURIComponent)].join("/")
      + "?" + new URLSearchParams(Object.entries(query_ || {}).filter(([, value]) => value !== undefined)).toString(),
    registered: (route) => routes.includes(route),
    setTitle(text) { seen.title = text; }, setCrumb(...parts) { seen.crumb = parts; }, styles() {}, layout() {},
    navigate(target) { seen.went.push(target); },
    api,
  };
  return { ctx, seen };
}

async function mounted(query, answers = {}) {
  const calls = [];
  const root = ui.el("div", {});
  const { ctx, seen } = context([CHAT], query, async (op, args) => {
    calls.push([op, args]);
    const answer = answers[op];
    if (answer instanceof Error) throw answer;
    return typeof answer === "function" ? answer(args) : answer;
  });
  await page.mount(root, ctx);
  return { root, calls, seen };
}

const listing = (audience, threads, extra = {}) => ({ chat: CHAT, campaign: "c1", audience, actor: null,
  cast: [{ actor: ANYA, name: "Anya" }, { actor: MARA, name: "Mara" }], threads, total: threads.length, problems: [], ...extra });
const section = (root, title) => root.querySelector('section[aria-label="' + title + '"]') || undefined;

test("a promise is found and followed to how canon says it ended, each entry graded and linked to its turn", async () => {
  const { root, calls, seen } = await mounted({ audience: "director", q: "key", thread: "p-key" }, {
    "story.threads": listing("director", [SUMMARY]), "story.thread": { thread: DIRECTOR } });
  assert.deepEqual(calls, [["story.threads", { chat: CHAT, audience: "director", query: "key" }],
    ["story.thread", { chat: CHAT, audience: "director", proposition: "p-key" }]]);
  assert.equal(seen.title, "Story threads");
  const row = section(root, "Threads").querySelector("a.story-row");
  assert.equal(row.getAttribute("aria-current"), "true");
  assert.ok(row.textContent.includes("Refuted") && row.textContent.includes("1 contradiction"));

  const ended = section(root, "How it ended");
  assert.ok(byText(ended, "span", "Refuted").length, "canon's standing, in a word");
  const decided = ended.querySelectorAll("a").find((node) => node.textContent === "Turn 9");
  assert.equal(decided.getAttribute("href"), "#/chat/" + encodeURIComponent(CHAT) + "?turn=9");
  assert.ok(ended.textContent.includes(SOLD) && byText(ended, "span", "Revoked").length, "what it rests on, and that it was revoked");

  const said = section(root, "Contradictions").querySelectorAll("li").map((node) => node.textContent);
  assert.deepEqual(said, ["Mara still believes it, against canon's ruling.", "The ruling rests on an event that was revoked."]);

  const entries = section(root, "Through the story").querySelectorAll("li.story-entry");
  assert.deepEqual(entries.map((node) => node.querySelector("span.g-pill").textContent), ["Proposed", "Proposed", "Belief", "Revoked", "Canon"]);
  assert.ok(entries[1].textContent.includes("Anya heard it from Mara"));
  // a turn a fork choice set aside is said, never linked as if it were played
  assert.equal(entries[2].querySelector("a"), null);
  assert.ok(entries[2].textContent.includes("From a turn set aside"));
  assert.equal(entries[0].querySelector("a").getAttribute("href"), "#/chat/" + encodeURIComponent(CHAT) + "?turn=1");
});

test("the table's view shows what its character heard and none of canon", async () => {
  const { root, calls } = await mounted({ thread: "p-key" }, {
    "story.threads": listing("table", [{ ...SUMMARY, standing: null, decided: null, contradictions: 0, entries: 1 }]),
    "story.thread": { thread: TABLE } });
  assert.equal(calls[0][1].audience, "table");
  assert.ok(!root.textContent.includes("Refuted") && !root.textContent.includes(SOLD));
  assert.ok(section(root, "How it ended").textContent.includes("director's"));
  assert.equal(section(root, "Contradictions"), undefined);
  assert.equal(section(root, "Through the story").querySelectorAll("li.story-entry").length, 1);
});

test("searching, choosing a character and changing view each go to an address the page reads back", async () => {
  const { root, seen } = await mounted({}, { "story.threads": listing("table", [SUMMARY]) });
  assert.ok(byText(root, "p", "Choose a thread to follow it through the story.").length);
  const box = root.querySelector("input.g-searchbox");
  box.value = "  night market ";
  byText(root, "button", "Find")[0].click();
  assert.equal(seen.went.at(-1), "#/story/" + encodeURIComponent(CHAT) + "?q=night+market");
  const select = root.querySelector("select");
  select.value = MARA;
  select.dispatchEvent({ type: "change" });
  assert.equal(seen.went.at(-1), "#/story/" + encodeURIComponent(CHAT) + "?actor=" + encodeURIComponent(MARA));
  byText(root, "button", "Director view")[0].click();
  assert.equal(seen.went.at(-1), "#/story/" + encodeURIComponent(CHAT) + "?audience=director");
});

test("a problem of the thread's own answer is shown too", async () => {
  const { root } = await mounted({ audience: "director", thread: "p-key" }, {
    "story.threads": listing("director", [SUMMARY]), "story.thread": { thread: DIRECTOR, problems: [{ code: "turns_unread", detail: null }] } });
  assert.ok(root.textContent.includes("Some turns could not be numbered"));
});

test("a thread the view does not hold is said, and the answer's problems are shown", async () => {
  const gone = Object.assign(new Error("no thread"), { reason: "no_thread" });
  const { root } = await mounted({ thread: "p-stair" }, {
    "story.threads": listing("table", [], { problems: [{ code: "no_character", detail: null }] }), "story.thread": gone });
  assert.ok(root.textContent.includes("That thread is not in this view of the story."));
  assert.ok(root.textContent.includes("You play no character in this campaign yet"));
  assert.ok(root.textContent.includes("Nothing has been recorded"));
});

test("only the scenes with the chosen character are listed", async () => {
  const other = { ...DIRECTOR.scenes[0], chat: "campaign:c1#chat/t2", records: ["event:e-sold"], actors: [mara] };
  const { root } = await mounted({ audience: "director", thread: "p-key", actor: ANYA }, {
    "story.threads": listing("director", [SUMMARY], { actor: anya }),
    "story.thread": { thread: { ...DIRECTOR, scenes: [DIRECTOR.scenes[0], other] } } });
  const scenes = section(root, "Scenes");
  assert.ok(byText(scenes, "h2", "Scenes with Anya").length);
  assert.equal(scenes.querySelectorAll("li").length, 1);
  assert.ok(scenes.textContent.includes("This chat, turns 1, 9"), scenes.textContent);
});

test("the logic says each entry, each source and the address", () => {
  assert.equal(logic.entryLine({ kind: "belief", holder: anya, stance: "suspected", current: false, inherited: true }),
    "Anya suspected it, then changed their mind (before you played them)");
  assert.equal(logic.entryLine({ kind: "sent", sender: anya, holder: mara }), "Anya passed it on to Mara");
  assert.equal(logic.entryLine({ kind: "observation", holder: anya }), "Anya saw this");
  assert.deepEqual(logic.problemsOf({ problems: [{ code: "a" }] }, null, { problems: [{ code: "a" }, { code: "b" }] })
    .map((problem) => problem.code), ["a", "b"]);
  assert.equal(logic.entryLine({ kind: "ruling", standing: "established" }), "Canon ruled it established");
  assert.equal(logic.entryLine({ kind: "report", holder: anya, attributed_to: null, confirmed: false }), "Anya heard it (unconfirmed)");
  assert.deepEqual(logic.sourceOf({ source: null }), { link: null, text: "Recorded outside any chat" });
  assert.equal(logic.sourceOf({ source: { chat: CHAT, turn: "x", number: null, set_aside: false } }).link, null);
  assert.deepEqual(logic.address({ chat: CHAT, audience: "table", q: null, actor: null, thread: null }, { thread: "p" }),
    { segments: [CHAT], query: { audience: undefined, q: undefined, actor: undefined, thread: "p" } });
  assert.deepEqual(logic.readAddress([CHAT], new URLSearchParams("audience=everyone&q=%20key%20")),
    { chat: CHAT, audience: "table", q: "key", actor: null, thread: null });
  assert.equal(logic.standingText(null), null);
  assert.equal(logic.problemText({ code: "odd", detail: "x" }), "odd (x)");
});
