// 109's source page drafts (static/pages/source/drafts.js) under Node's own test runner: what a structured save
// sends, and how a draft is held over a newer head. Run by test_library_pages.py.
import assert from "node:assert/strict";
import { test } from "node:test";

import { changesOf, fieldsOf, modelOf, rebase, rebaseRaw, unsafeNumbers } from
  "../../src/grimoire/app/static/pages/source/drafts.js";

const entity = (head, payload) => ({ family: "entity", head, payload });
const lorebook = (head, entries, extra = {}) => ({ family: "lorebook", head, payload: { name: "Book", entries, ...extra } });
const scenario = (head, payload) => ({ family: "scenario", head, payload });

// A draft started on `read`, edited by `change(model)`.
function drafted(read, change) {
  const start = modelOf(read);
  const model = structuredClone(start);
  change(model);
  return { base: read.head, start, model };
}

test("a draft held over a newer head keeps the other writer's changes and sends only its own", () => {
  const r3 = entity("r3", { name: "Mara", description: "A", sections: { personality: "Wry." } });
  const draft = drafted(r3, (model) => { model.name = "Mara Vell"; });
  const r4 = entity("r4", { name: "Mara", description: "B", sections: { personality: "Wry.", history: "New." } });
  const merged = rebase(r4, draft);
  assert.deepEqual(merged.conflicts, []);
  assert.deepEqual(merged.mine, ["name"]);
  assert.deepEqual(merged.changed, ["description", "sections"]);
  assert.equal(merged.model.description, "B");
  assert.deepEqual(fieldsOf(r4, merged.model), { name: "Mara Vell" });
});

test("a part changed differently on both sides is a conflict; the same change is not", () => {
  const r3 = entity("r3", { name: "Mara", sections: { personality: "Wry." } });
  const draft = drafted(r3, (model) => { model.name = "Mara Vell"; model.sections[0].text = "Sharp."; });
  const both = rebase(entity("r4", { name: "Mara Rook", sections: { personality: "Kind." } }), draft);
  assert.deepEqual(both.conflicts, ["name", "section personality"]);
  // the draft was saved and the page left before the answer: the head already has every change
  const saved = entity("r4", { name: "Mara Vell", sections: { personality: "Sharp." } });
  const again = rebase(saved, draft);
  assert.deepEqual(again.conflicts, []);
  assert.deepEqual(fieldsOf(saved, again.model), {});
});

test("an entry the edit leaves alone is sent as its index alone, and only what changed is sent", () => {
  const blank = { keys: [], content: "", enabled: false };
  const inert = { keys: ["red\ndragon", " ?! "], content: "Old dragon lore.", extensions: { uid: 7 } };
  const r1 = lorebook("r1", [blank, inert]);
  const renamed = drafted(r1, (model) => { model.name = "Dragons"; });
  assert.deepEqual(fieldsOf(r1, renamed.model), { name: "Dragons" });
  const edited = drafted(r1, (model) => { model.entries[1].content = "New lore."; });
  assert.deepEqual(fieldsOf(r1, edited.model), { entries: [{ index: 0 }, { index: 1, content: "New lore." }] });
  const emptied = drafted(r1, (model) => { model.entries[1].content = " "; });
  assert.deepEqual(fieldsOf(r1, emptied.model), { entries: [{ index: 0 }] });
  const added = drafted(r1, (model) => { model.entries.push({ slot: null, original: null, keys: "bell\n", content: "The bell.", constant: true, enabled: true }); });
  assert.deepEqual(fieldsOf(r1, added.model).entries.at(-1), { keys: ["bell"], content: "The bell.", constant: true, enabled: true });
});

test("entries are found in a newer head by their exact content, wherever they moved", () => {
  const first = { keys: ["bell"], content: "The bell." };
  const second = { keys: ["tide"], content: "The tide." };
  const r1 = lorebook("r1", [first, second]);
  const draft = drafted(r1, (model) => { model.entries[1].content = "The tide, retold."; });
  // another writer added an entry ahead of them
  const inserted = { keys: ["gull"], content: "Gulls." };
  const r2 = lorebook("r2", [inserted, first, second]);
  const merged = rebase(r2, draft);
  assert.deepEqual(merged.conflicts, []);
  assert.deepEqual(fieldsOf(r2, merged.model).entries, [{ index: 0 }, { index: 1 }, { index: 2, content: "The tide, retold." }]);
  // a draft that only renames keeps the added entry and sends nothing about entries
  const rename = rebase(r2, drafted(r1, (model) => { model.name = "Harbor"; }));
  assert.deepEqual(fieldsOf(r2, rename.model), { name: "Harbor" });
  // the edited entry changed in the head too: a conflict, not a guess
  const changed = lorebook("r2", [first, { ...second, content: "The tide, theirs." }]);
  assert.deepEqual(rebase(changed, draft).conflicts, ["entry 2"]);
});

test("openings are matched by id and sections by name", () => {
  const r1 = scenario("r1", { title: "Storm", openings: [{ id: "first", text: "Rain.", group_only: true }, { id: "second", text: "Wind." }] });
  const draft = drafted(r1, (model) => { model.openings[1].text = "Wind, and thunder."; });
  const r2 = scenario("r2", { title: "Storm", openings: [{ id: "second", text: "Wind." }, { id: "first", text: "Rain.", group_only: true }] });
  const merged = rebase(r2, draft);
  assert.deepEqual(merged.conflicts, []);
  assert.deepEqual(fieldsOf(r2, merged.model).openings, [{ id: "second", text: "Wind, and thunder." }, { id: "first", text: "Rain." }]);
  assert.deepEqual(changesOf("scenario", draft.start, draft.model), ["opening second"]);
  const removed = drafted(entity("r1", { name: "Mara", sections: { a: "A.", b: "B." } }), (model) => { model.sections.splice(0, 1); });
  assert.deepEqual(rebase(entity("r2", { name: "Mara", sections: { a: "A.", b: "B.", c: "C." } }), removed).model.sections.map((item) => item.name), ["b", "c"]);
});

test("sections that are not all text are left exactly as they are", () => {
  const read = entity("r1", { name: "Mara", sections: { personality: "Wry.", notes: { by: "importer" } } });
  const model = modelOf(read);
  assert.equal(model.locked, true);
  model.name = "Mara Vell";
  assert.deepEqual(fieldsOf(read, model), { name: "Mara Vell" });
});

test("a raw draft lays its own keys over a newer head", () => {
  const r1 = { family: "other", head: "r1", payload: { a: 1, b: { x: 1 } } };
  const draft = { base: "r1", start: JSON.stringify(r1.payload), text: JSON.stringify({ a: 2, b: { x: 1 } }) };
  const r2 = { family: "other", head: "r2", payload: { a: 1, b: { x: 1 }, c: "new" } };
  const merged = rebaseRaw(r2, draft);
  assert.deepEqual(merged.payload, { a: 2, b: { x: 1 }, c: "new" });
  assert.deepEqual([merged.mine, merged.changed, merged.conflicts], [["a"], ["c"], []]);
  assert.deepEqual(rebaseRaw({ ...r2, payload: { a: 3, b: { x: 1 } } }, draft).conflicts, ["a"]);
  assert.ok(rebaseRaw(r2, { ...draft, text: "{" }).error);
});

test("a whole number the browser cannot keep exactly is noticed", () => {
  assert.equal(unsafeNumbers({ extensions: { uid: JSON.parse("9007199254740993") } }), true);
  assert.equal(unsafeNumbers({ uid: 9007199254740991, n: 1.5, list: [1, "2"] }), false);
});
