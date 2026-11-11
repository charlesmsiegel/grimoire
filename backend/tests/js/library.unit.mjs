// 109's library pages' pure modules under Node's own test runner: the source page's drafts (what a structured save
// sends, and how a draft is held over a newer head), what its sheet shows and how two revisions compare, and which
// entries Characters and lore lists, counts and in what order. Run by test_library_pages.py.
import assert from "node:assert/strict";
import { test } from "node:test";

import { changesOf, draftParts, fieldsOf, modelOf, rebase, rebaseRaw, stale, unsafeNumbers } from
  "../../src/grimoire/app/static/pages/source/drafts.js";
import { askedTab, chatsOn, diffRevisions, loreBound, loreEntries, metaParts, nextVersion, opensWith, tabsFor, versionOf } from
  "../../src/grimoire/app/static/pages/source/sheet.js";
import { CARDS, askedRole, askedSort, canChat, countRoles, filterEntries, kindLabel as catalogKind, metaLine, playsOf, roleOf, shelvesOf,
  sortEntries, sortsFor, when } from "../../src/grimoire/app/static/pages/sources/catalog.js";

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
  assert.match(rebaseRaw(r2, { ...draft, text: '{"a":1e400,"b":{"x":1}}' }).error, /too large/);
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

// -- Characters and lore: which entries are listed, counted and in what order (static/pages/sources/catalog.js) --

const entryOf = (ref, name, extra = {}) => {
  const [source, kind] = ref.split("/");
  return { ref, source, kind, name, problem: null, derived: false, ...extra };
};
const SHELF = [
  entryOf("card:anya/character/anya", "Anya"),
  entryOf("card:anya/lorebook/anya-lore", "Quay lore"),
  entryOf("world:harbor/character/mara", "mara"),
  entryOf("world:harbor/scenario/watch", "Night watch"),
  entryOf("world:harbor/location/quay", "The quay"),
  entryOf("world:realm/item/ledger", null),
];
const SOURCES = [
  { source: "card:anya", title: "Anya", namespace: "card" },
  { source: "import:run", title: "Receipts", namespace: "import" },
  { source: "world:realm", title: null, namespace: "world" },
  { source: "world:harbor", title: "Harbor", namespace: "world" },
];

test("an entry is listed under one role, and the roles count every entry once", () => {
  assert.deepEqual(SHELF.map(roleOf), ["character", "lore", "character", "scenario", "world", "world"]);
  assert.deepEqual(countRoles(SHELF), { "": 6, character: 2, scenario: 1, lore: 1, world: 2 });
  assert.deepEqual(countRoles([]), { "": 0, character: 0, scenario: 0, lore: 0, world: 0 });
});

test("the address's role is one the page lists: a persona is a character, and anything else is everything", () => {
  assert.deepEqual(askedRole("lore"), { role: "lore", persona: false });
  assert.deepEqual(askedRole("persona"), { role: "character", persona: true });
  assert.deepEqual(askedRole("constructor"), { role: "", persona: false });
  assert.deepEqual(askedRole(null), { role: "", persona: false });
});

test("the shelves are everything, the imported cards and each other source, worlds first, with their counts", () => {
  assert.deepEqual(shelvesOf(SOURCES, SHELF), [
    { id: "", label: "Everything", count: 6 },
    { id: CARDS, label: "Imported cards", count: 2 },
    { id: "world:harbor", label: "Harbor", count: 3 },
    { id: "world:realm", label: "world:realm", count: 1 },
    { id: "import:run", label: "Receipts", count: 0 },
  ]);
  // no imported card, no shelf of them
  assert.deepEqual(shelvesOf(SOURCES.slice(1), []).map((shelf) => shelf.id), ["", "world:harbor", "world:realm", "import:run"]);
});

test("a shelf and a role each narrow the entries, and together leave only what both hold", () => {
  const refs = (found) => found.map((entry) => entry.ref);
  assert.equal(filterEntries(SHELF).length, 6);
  assert.deepEqual(refs(filterEntries(SHELF, { shelf: CARDS })), ["card:anya/character/anya", "card:anya/lorebook/anya-lore"]);
  assert.deepEqual(refs(filterEntries(SHELF, { shelf: "world:harbor", role: "character" })), ["world:harbor/character/mara"]);
  assert.deepEqual(refs(filterEntries(SHELF, { role: "world" })), ["world:harbor/location/quay", "world:realm/item/ledger"]);
  assert.deepEqual(filterEntries(SHELF, { shelf: "world:nowhere" }), []);
});

const CHATS = [
  { chat: "c1", last_activity: "2026-03-02T10:00:00Z", sources: [{ role: "character", ref: "world:harbor/character/mara" },
    { role: "persona", ref: "card:anya/character/anya" }] },
  { chat: "c2", last_activity: "2026-03-05T10:00:00Z", sources: [{ role: "character", ref: "world:harbor/character/mara" },
    { role: "persona", ref: "world:harbor/character/mara" }, { role: "scenario", ref: "world:harbor/scenario/watch" }] },
];

test("play is counted from the chats, once a chat, and only when the list of chats is whole", () => {
  const plays = playsOf(CHATS, 1000);
  assert.deepEqual(plays.get("world:harbor/character/mara"), { count: 2, last: "2026-03-05T10:00:00Z" });
  assert.deepEqual(plays.get("card:anya/character/anya"), { count: 1, last: "2026-03-02T10:00:00Z" });
  assert.equal(plays.get("card:anya/lorebook/anya-lore"), undefined);
  assert.equal(playsOf(CHATS, 2), null, "a list as long as was asked for may be cut short");
  assert.equal(playsOf(null, 1000), null);
});

test("the orders by play are offered only when play is known", () => {
  const plays = playsOf(CHATS, 1000);
  assert.deepEqual(sortsFor(plays).map((sort) => sort.value), ["recent", "name", "played"]);
  assert.deepEqual(sortsFor(null).map((sort) => sort.value), ["name"]);
  assert.equal(askedSort(null, plays), "recent");
  assert.equal(askedSort("played", plays), "played");
  assert.equal(askedSort("played", null), "name");
  assert.equal(askedSort("nonsense", plays), "recent");
});

test("entries are put in order by name, by last play or by how many chats, never changing the list given", () => {
  const plays = playsOf(CHATS, 1000);
  const refs = (found) => found.map((entry) => entry.ref.split("/").pop());
  const before = refs(SHELF);
  assert.deepEqual(refs(sortEntries(SHELF, "name", plays)), ["ledger", "anya", "mara", "watch", "anya-lore", "quay"]);
  assert.deepEqual(refs(sortEntries(SHELF, "recent", plays)), ["mara", "watch", "anya", "ledger", "anya-lore", "quay"]);
  assert.deepEqual(refs(sortEntries(SHELF, "played", plays)), ["mara", "anya", "watch", "ledger", "anya-lore", "quay"]);
  assert.deepEqual(refs(sortEntries(SHELF, "played", null)), refs(sortEntries(SHELF, "name", null)), "no play, no order by play");
  assert.deepEqual(refs(SHELF), before);
});

test("a moment is said as a person says it, and something that is no moment is said as nothing", () => {
  const noon = (day) => new Date(2026, 2, day, 12, 0, 0).getTime();
  const at = (day, hour) => new Date(2026, 2, day, hour, 0, 0).toISOString();
  assert.equal(when(at(10, 1), noon(10)), "today");
  assert.equal(when(at(9, 23), noon(10)), "yesterday");
  assert.equal(when(at(7, 12), noon(10)), "3 days ago");
  assert.ok(/2026/.test(when(at(1, 12), noon(10))), "an older day is its date");
  assert.equal(when("not a date", noon(10)), "");
  assert.equal(when(null, noon(10)), "");
});

test("the line under a name says where it lives, and its play only when play is known", () => {
  const plays = playsOf(CHATS, 1000);
  const now = Date.parse("2026-03-05T12:00:00Z");
  const mara = SHELF[2];
  assert.equal(metaLine(mara, "Harbor", null, now), "Harbor");
  assert.ok(metaLine(mara, "Harbor", plays, now).startsWith("Harbor · 2 chats · "));
  assert.equal(metaLine(SHELF[1], "Anya", plays, now), "Anya", "an entry in no chat shows no count");
  assert.equal(metaLine({ ...mara, derived: true }, null, null, now), "world:harbor · a portrayal");
});

test("a Chat button belongs to a character or a scenario that resolves", () => {
  assert.deepEqual(SHELF.map(canChat), [true, false, true, true, false, false]);
  assert.equal(canChat({ ...SHELF[0], problem: "entity_dropped" }), false);
  assert.equal(catalogKind("lorebook"), "Lorebook");
  assert.equal(catalogKind("receipt"), "Receipt");
});

// -- the sheet: tabs, versions, what it opens with and two revisions compared (static/pages/source/sheet.js) -----

test("an entry's tabs are the same five, with Art only for a character whose pictures the app reads", () => {
  const names = (read, options) => tabsFor(read, options).map((tab) => tab.value);
  assert.deepEqual(names({ kind: "character" }, { art: true }), ["sheet", "art", "edit", "revisions", "raw"]);
  assert.deepEqual(names({ kind: "character" }, { art: false }), ["sheet", "edit", "revisions", "raw"]);
  assert.deepEqual(names({ kind: "lorebook" }, { art: true }), ["sheet", "edit", "revisions", "raw"]);
  const tabs = tabsFor({ kind: "lorebook" });
  assert.equal(askedTab("raw", tabs), "raw");
  assert.equal(askedTab("art", tabs), "sheet", "a tab this entry does not have opens the sheet");
  assert.equal(askedTab(null, tabs), "sheet");
});

test("a version is a revision's place among its entity's revisions, and a save writes the next", () => {
  const revisions = ["v1-aa", "r2", "r3"];
  assert.equal(versionOf(revisions, "v1-aa"), 1);
  assert.equal(versionOf(revisions, "r3"), 3);
  assert.equal(versionOf(revisions, "r9"), null);
  assert.equal(nextVersion({ revisions }), 4);
});

test("the line under a sheet's name is made of what the answer holds, and nothing else", () => {
  const read = { source: "card:mara", source_title: "Mara Vell", revisions: ["r1", "r2"], revision: "r2", is_head: true, derived: false,
    chats: [{ active: true, revisions: ["r2"] }, { active: false, revisions: [] }] };
  assert.deepEqual(metaParts(read), ["Imported card", "version 2", "1 chat"]);
  assert.deepEqual(metaParts({ ...read, source: "world:harbor", source_title: "Harbor", revision: "r1", is_head: false, derived: true, chats: [] }),
    ["Harbor", "version 1 of 2", "a portrayal"]);
  assert.deepEqual(metaParts({ ...read, source: "world:realm", source_title: null, chats: undefined }), ["world:realm", "version 2"]);
  assert.deepEqual(chatsOn({ chats: [{ chat: "a", active: true, revisions: ["r1"] }, { chat: "b", active: true, revisions: ["r2"] },
    { chat: "c", active: false, revisions: [] }] }, "r2").map((chat) => chat.chat), ["b"]);
});

test("a sheet opens with a card's greetings or a scenario's openings, and names the lorebook a card binds", () => {
  const card = entity("r1", { name: "Mara", card: { greetings: [{ id: "first", text: "You came back." }, { id: "blank", text: " " }, null,
    { id: "alt", text: "The tide is late." }], lorebook: "card:mara/lorebook/lore@r1" } });
  assert.deepEqual(opensWith(card), ["You came back.", "The tide is late."]);
  assert.deepEqual(loreBound(card), ["card:mara/lorebook/lore@r1"]);
  assert.deepEqual(opensWith(entity("r1", { name: "Plain" })), []);
  assert.deepEqual(loreBound(entity("r1", { name: "Plain", card: "not an object" })), []);
  assert.deepEqual(opensWith(scenario("r1", { title: "Watch", openings: [{ id: "a", text: "The bell rings." }] })), ["The bell rings."]);
});

test("a lorebook's entries are listed with their keys and whether they are off or always in context", () => {
  const book = lorebook("r1", [{ keys: ["bell", "tide bell"], content: "It rings.", settings: { constant: true } },
    { keys: [], content: "Quiet.", enabled: false }]);
  assert.deepEqual(loreEntries(book), [{ keys: ["bell", "tide bell"], content: "It rings.", enabled: true, constant: true },
    { keys: [], content: "Quiet.", enabled: false, constant: false }]);
  assert.deepEqual(loreEntries(lorebook("r1", ["not an entry"])), [], "entries that are not plain are left to the raw view");
  assert.deepEqual(loreEntries(entity("r1", { name: "Mara" })), []);
});

test("two revisions of a character are compared field by field: changed, added and removed", () => {
  const before = { name: "Mara", description: "A smuggler.", sections: { personality: "Wry.", history: "Long." } };
  const after = { name: "Mara Vell", description: "A smuggler.", sections: { personality: "Sharp.", wants: "The ledger." } };
  assert.deepEqual(diffRevisions("entity", before, after), [
    { field: "Name", before: "Mara", after: "Mara Vell", stored: false },
    { field: "Section personality", before: "Wry.", after: "Sharp.", stored: false },
    { field: "Section wants", before: null, after: "The ledger.", stored: false },
    { field: "Section history", before: "Long.", after: null, stored: false },
  ]);
  assert.deepEqual(diffRevisions("entity", before, structuredClone(before)), []);
});

test("a key the editor has no field for is compared as it is written, whatever it is called", () => {
  const before = { name: "Mara", derivation: { base: "card:mara/character/mara@r1" } };
  const after = JSON.parse('{"name": "Mara", "derivation": {"base": "card:mara/character/mara@r1", "clear_description": true}, "__proto__": 1}');
  const changes = diffRevisions("entity", before, after);
  assert.deepEqual(changes.map((change) => [change.field, change.stored]), [["Stored field __proto__", true], ["Stored field derivation", true]]);
  assert.equal(changes[0].before, null);
  assert.equal(changes[0].after, "1");
  assert.ok(changes[1].after.includes('"clear_description": true'));
  // sections that are not all text are a stored field too, never dropped from the comparison
  const locked = diffRevisions("entity", { name: "Mara", sections: { a: "x" } }, { name: "Mara", sections: { a: { rich: true } } });
  assert.deepEqual(locked.map((change) => change.field), ["Stored field sections", "Section a"]);
});

test("scenarios, lorebooks and records with no editor are compared too", () => {
  assert.deepEqual(diffRevisions("scenario", { title: "Watch", openings: [{ id: "a", text: "Bell." }] },
    { title: "Watch", premise: "Fog.", openings: [{ id: "a", text: "Two bells." }] }), [
    { field: "Premise", before: null, after: "Fog.", stored: false },
    { field: "Opening a", before: "Bell.", after: "Two bells.", stored: false },
  ]);
  const entries = diffRevisions("lorebook", { name: "Book", entries: [{ keys: ["bell"], content: "It rings." }] },
    { name: "Book", entries: [{ keys: ["bell", "tide"], content: "It rings.", enabled: false }] });
  assert.deepEqual(entries, [{ field: "Entry 1", before: "Keys: bell\nIt rings.", after: "Keys: bell, tide\nIt rings.\n(off)", stored: false }]);
  assert.deepEqual(diffRevisions("other", { runs: 1 }, { runs: 2, note: "x" }), [
    { field: "Stored field note", before: null, after: "x", stored: true },
    { field: "Stored field runs", before: "1", after: "2", stored: true },
  ]);
  assert.deepEqual(diffRevisions("other", null, {}), []);
});
