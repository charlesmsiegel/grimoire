// The chat page's story logic (pages/chat/story.js): where a sitting ends and how this device remembers it,
// the timeline's entries and their order, what "Bookmark this moment" marks, and the recap's heading.
// Run by test_app_shell_js.py with Node's own test runner. Every name here is made up.
import assert from "node:assert/strict";
import { test } from "node:test";

import { closedThrough, hasBookmark, newestKept, recapHeading, sitting, storyKey, timeline }
  from "../../src/grimoire/app/static/pages/chat/story.js";

class Storage {
  constructor() { this.items = new Map(); }
  getItem(key) { return this.items.has(key) ? this.items.get(key) : null; }
  setItem(key, value) { this.items.set(key, String(value)); }
}

const kept = (...ids) => ({ lines: ids.map((line) => ({ line })) });
const turn = (id, more = {}) => ({ turn: id, post: null, kept: null, open: false, passed: false, ...more });

test("a sitting ends at the newest closed turn, and the story is read again when a line changes", () => {
  const scene = { turns: [turn("t1", { kept: kept("a") }), turn("t2", { passed: true }), turn("t3", { open: true })] };
  assert.equal(closedThrough(scene), "t2");
  assert.equal(closedThrough({ turns: [turn("t1", { open: true })] }), null);
  assert.equal(closedThrough({ turns: [] }), null);
  assert.equal(newestKept(scene).turn, "t1");
  assert.equal(newestKept({ turns: [turn("t1", { passed: true })] }), null);
  const struck = { turns: [turn("t1", { kept: kept() }), scene.turns[1], scene.turns[2]] };
  assert.notEqual(storyKey(scene), storyKey(struck), "a struck line changes what the recap may quote");
  assert.equal(storyKey(scene), storyKey({ turns: scene.turns.map((item) => ({ ...item })) }));
});

test("the first visit knows no last time; the next session starts from where the last one ended", () => {
  const local = new Storage();
  const first = sitting(local, new Storage(), "chat-a");
  assert.equal(first.since, null);
  first.note("t2");
  first.note("t3");
  // the same session again (a reload, or back from another page): the same sitting
  const tab = new Storage();
  const second = sitting(local, tab, "chat-a");
  assert.equal(second.since, "t3", "a new session starts from where the last one ended");
  second.note("t5");
  const again = sitting(local, tab, "chat-a");
  assert.equal(again.since, "t3", "a reload within the session does not move the mark");
  again.note("t6");
  assert.equal(sitting(local, new Storage(), "chat-a").since, "t6");
  assert.equal(sitting(local, new Storage(), "chat-b").since, null, "each chat has its own mark");
});

test("a session that played nothing keeps the mark, and a browser that keeps nothing has none", () => {
  const local = new Storage();
  sitting(local, new Storage(), "chat-a").note("t4");
  const idle = sitting(local, new Storage(), "chat-a");
  assert.equal(idle.since, "t4");
  idle.note("t4");  // nothing new was closed
  assert.equal(sitting(local, new Storage(), "chat-a").since, "t4");
  const none = sitting(null, null, "chat-a");
  none.note("t9");
  assert.equal(none.since, null);
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(sitting(broken, broken, "chat-a").since, null);
  local.setItem("grimoire.chat.left:chat-c", "{not json");
  assert.equal(sitting(local, new Storage(), "chat-c").since, null);
  local.setItem("grimoire.chat.left:chat-d", JSON.stringify({ previous: 7, current: ["x"] }));
  assert.equal(sitting(local, new Storage(), "chat-d").since, null, "only a turn id is a mark");
});

test("the timeline lists bookmarks and the last-time entry in the order of the story", () => {
  const story = { last_time: { turn: "t3", number: 3 }, bookmarks: [
    { id: "b3", turn: "t9", label: "Lost", number: null, missing: true, etag: "e3" },
    { id: "b2", turn: "t3", label: null, number: 3, missing: false, etag: "e2" },
    { id: "b1", turn: "t1", label: "The forged seal", number: 1, missing: false, etag: "e1" }] };
  const entries = timeline(story);
  assert.deepEqual(entries.map((entry) => [entry.label, entry.at]), [
    ["Bookmark: The forged seal", "Turn 1"], ["Bookmark", "Turn 3"], ["Last time you played", "Turn 3"],
    ["Bookmark: Lost", "Its turn is no longer in this chat"]]);
  assert.deepEqual(entries.map((entry) => entry.key), ["bookmark:b1", "bookmark:b2", "last", "bookmark:b3"]);
  assert.equal(entries[0].name, "The forged seal");
  assert.equal(entries[1].name, "", "an unnamed bookmark is renamed from nothing");
  assert.deepEqual(timeline({ last_time: null, bookmarks: [] }), []);
  assert.ok(hasBookmark(story, "t3") && !hasBookmark(story, "t2") && !hasBookmark(null, "t3"));
});

test("the recap says whether it is from last time or only the latest lines", () => {
  assert.equal(recapHeading({ basis: "sitting", lines: [] }), "The last lines from last time");
  assert.equal(recapHeading({ basis: "latest", lines: [] }), "The latest lines so far");
});
