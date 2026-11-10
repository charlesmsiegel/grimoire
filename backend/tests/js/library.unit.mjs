// 109's source page drafts (static/pages/source/drafts.js) under Node's own test runner: what a structured save
// sends, and how a draft is held over a newer head. Run by test_library_pages.py.
import assert from "node:assert/strict";
import { test } from "node:test";

import { changesOf, draftParts, fieldsOf, modelOf, rebase, rebaseRaw, stale, unsafeNumbers } from
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

test("a list the head no longer lets the structured editor send is a conflict, not a dropped change", () => {
  const r1 = scenario("r1", { title: "Storm", openings: [{ id: "first", text: "Rain." }] });
  const draft = drafted(r1, (model) => { model.title = "Squall"; model.openings[0].text = "Rain, sideways."; });
  // another writer, in the raw editor, added an opening with no id: the head's openings are now locked
  const r2 = scenario("r2", { title: "Storm", openings: [{ id: "first", text: "Rain." }, { text: "Hail." }] });
  const merged = rebase(r2, draft);
  assert.equal(modelOf(r2).locked, true);
  assert.deepEqual(merged.conflicts, ["opening first"]);
  assert.deepEqual(merged.copies, { "opening first": { text: "Rain, sideways." } });
  assert.equal(merged.model.title, "Squall");
  // an opening the draft adds is kept to copy too, by its own text
  const added = rebase(r2, drafted(r1, (model) => { model.openings.push({ slot: null, text: "Thunder." }); }));
  assert.deepEqual(added.copies, { "opening 2 (new)": { text: "Thunder." } });
});

test("a save that landed before the page heard back is recognized, additions and lore edits included", () => {
  const r1s = scenario("r1", { title: "Storm", openings: [{ id: "first", text: "Rain." }] });
  const opening = drafted(r1s, (model) => { model.openings.push({ slot: null, text: "Thunder." }); });
  const r2s = scenario("r2", { title: "Storm", openings: [{ id: "first", text: "Rain." }, { id: "opening-2", text: "Thunder." }] });
  const sOut = rebase(r2s, opening);
  assert.deepEqual([sOut.conflicts, fieldsOf(r2s, sOut.model)], [[], {}]);

  const bell = { keys: ["bell"], content: "The bell." };
  const tide = { keys: ["tide"], content: "The tide." };
  const r1 = lorebook("r1", [bell, tide]);
  const draft = drafted(r1, (model) => {
    model.entries[1].content = "The tide, retold.";
    model.entries.push({ slot: null, original: null, keys: "gull\n", content: "Gulls.", constant: false, enabled: true });
  });
  const r2 = lorebook("r2", [bell, { ...tide, content: "The tide, retold." }, { keys: ["gull"], content: "Gulls." }]);
  const out = rebase(r2, draft);
  assert.deepEqual([out.conflicts, fieldsOf(r2, out.model)], [[], {}]);
  // an entry removed by the draft and already gone, with nothing changed in its place
  const removed = drafted(r1, (model) => { model.entries.splice(0, 1); });
  const gone = rebase(lorebook("r2", [tide]), removed);
  assert.deepEqual([gone.conflicts, fieldsOf(lorebook("r2", [tide]), gone.model)], [[], {}]);
  // but gone while another entry changed: it may have been changed, not removed, so it is a conflict
  const unsure = rebase(lorebook("r2", [{ ...tide, content: "Theirs." }]), removed);
  assert.deepEqual(unsure.conflicts, ["entry 1 (removed)"]);
});

test("an addition saved and edited again before the answer is updated in place, never added twice", () => {
  const r1 = scenario("r1", { title: "Storm", openings: [{ id: "first", text: "Rain." }] });
  const start = modelOf(r1);
  const sentItem = { slot: null, added: "a1", text: "Thunder." };
  const draft = { base: "r1", start, model: { ...structuredClone(start), openings: [...structuredClone(start.openings), { ...sentItem, text: "Thunder, closer." }] },
    sent: { a1: sentItem } };
  const r2 = scenario("r2", { title: "Storm", openings: [{ id: "first", text: "Rain." }, { id: "opening-2", text: "Thunder." }] });
  const merged = rebase(r2, draft);
  assert.deepEqual(merged.conflicts, []);
  assert.deepEqual(fieldsOf(r2, merged.model).openings, [{ id: "first", text: "Rain." }, { id: "opening-2", text: "Thunder, closer." }]);
  // the same for a lorebook entry
  const l1 = lorebook("l1", [{ keys: ["bell"], content: "The bell." }]);
  const ls = modelOf(l1);
  const entrySent = { slot: null, added: "b1", original: null, keys: "gull", content: "Gulls.", constant: false, enabled: true };
  const ldraft = { base: "l1", start: ls, model: { ...structuredClone(ls), entries: [...structuredClone(ls.entries), { ...entrySent, content: "Gulls, many." }] },
    sent: { b1: entrySent } };
  const l2 = lorebook("l2", [{ keys: ["bell"], content: "The bell." }, { keys: ["gull"], content: "Gulls." }]);
  const lmerged = rebase(l2, ldraft);
  assert.deepEqual([lmerged.conflicts, fieldsOf(l2, lmerged.model).entries], [[], [{ index: 0 }, { index: 1, content: "Gulls, many." }]]);
  // not found as it was sent (someone changed it since): a conflict, not a second copy
  const l3 = lorebook("l3", [{ keys: ["bell"], content: "The bell." }, { keys: ["gull"], content: "Gulls, theirs." }]);
  assert.deepEqual(rebase(l3, ldraft).conflicts, ["entry 2 (new)"]);
});

test("a draft that cannot be kept is given to copy whole, not only its conflicts", () => {
  const r1 = entity("r1", { name: "Mara", description: "A" });
  const draft = drafted(r1, (model) => { model.name = "Mara Vell"; model.description = "Mine."; });
  const merged = rebase(entity("r2", { name: "Mara", description: "Theirs." }), draft);
  assert.deepEqual(merged.conflicts, ["description"]);
  assert.deepEqual(draftParts("entity", draft.start, draft.model), { name: "Mara Vell", description: "Mine." });
});

test("identical entries are matched copy for copy, and a match that cannot be told is a conflict", () => {
  const same = { keys: ["a"], content: "A." };
  const r1 = lorebook("r1", [same, same]);
  const draft = drafted(r1, (model) => { model.entries[1].content = "Mine."; });
  // the other writer changed the second copy: which copy is left is not known, so nothing is guessed
  assert.deepEqual(rebase(lorebook("r2", [same, { ...same, content: "Theirs." }]), draft).conflicts, ["entry 2"]);
  // both copies still there (another entry added): the second copy is the second copy
  const r2 = lorebook("r2", [same, same, { keys: ["b"], content: "B." }]);
  const merged = rebase(r2, draft);
  assert.deepEqual([merged.conflicts, fieldsOf(r2, merged.model).entries], [[], [{ index: 0 }, { index: 1, content: "Mine." }, { index: 2 }]]);
  // the draft's own edit, already saved
  const saved = lorebook("r2", [same, { ...same, content: "Mine." }]);
  assert.deepEqual(rebase(saved, draft).conflicts, []);
});

test("a draft is stale when its revision moved on, or when that revision was changed in place", () => {
  const head = { head: "r3", etag: "e2" };
  assert.equal(stale({ base: "r3", etag: "e2" }, head), false);
  assert.equal(stale({ base: "r2", etag: "e2" }, head), true);
  assert.equal(stale({ base: "r3", etag: "e1" }, head), true);  // the file was edited by hand: same revision, new etag
  assert.equal(stale({ base: "r3" }, head), false);  // a draft kept before etags were recorded goes by its revision
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
  // any JSON key is a key, "__proto__" included
  const odd = { base: "r1", start: JSON.stringify({ name: "A" }), text: '{"name":"A","__proto__":{"extension":true}}' };
  const kept = rebaseRaw({ family: "other", head: "r2", payload: { name: "A", description: "B" } }, odd);
  assert.deepEqual([kept.conflicts, kept.mine], [[], ["__proto__"]]);
  assert.equal(kept.text, JSON.stringify(JSON.parse('{"name":"A","description":"B","__proto__":{"extension":true}}'), null, 2));
});

test("a section may be called by a name an object inherits", () => {
  const read = entity("r1", { name: "Mara", sections: { constructor: "Built.", toString: "Said." } });
  const edited = drafted(read, (model) => { model.sections[0].text = "Rebuilt."; });
  assert.deepEqual({ ...fieldsOf(read, edited.model).sections }, { constructor: "Rebuilt.", toString: "Said." });
});

test("a number the browser cannot keep exactly is noticed", () => {
  assert.equal(unsafeNumbers({ n: JSON.parse("1e400") }), true);
  assert.equal(unsafeNumbers([JSON.parse("-1e400")]), true);
  assert.equal(unsafeNumbers({ extensions: { uid: JSON.parse("9007199254740993") } }), true);
  assert.equal(unsafeNumbers({ uid: 9007199254740991, n: 1.5, list: [1, "2"] }), false);
});
