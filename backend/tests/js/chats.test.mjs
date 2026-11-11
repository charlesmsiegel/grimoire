// The Chats list's own logic (pages/chats/model.js): what a chat is called and who is in it, and searching,
// sorting, grouping and paging a list of chats. Synthetic chats in the shape the `chats` op answers.
import assert from "node:assert/strict";
import { test } from "node:test";

import { FILTERS, FLAT_ROWS, GROUPS, GROUP_ROWS, PINNED, PINNED_FILTER, SORTS, chatCharacters, chatFaces, chatMeta, chatTitle, chatWhere, countLabel,
  groupChats, listNames, oneOf, pinnedOnly, plural, searchChats, shownRows, sortChats, viewOf, when }
  from "../../src/grimoire/app/static/pages/chats/model.js";
import { greeting } from "../../src/grimoire/app/static/pages/home/page.js";

const MARA = { role: "character", ref: "card:mara/character/mara", name: "Mara Vell" };
const TWIN = { role: "character", ref: "world:salt/character/mara", name: "Mara Vell" };  // another Mara: a different ref
const OREN = { role: "character", ref: "card:oren/character/oren", name: "Oren Tash" };
const ANYA = { role: "persona", ref: "card:anya/character/anya", name: "Anya Rook" };
const PIER = { role: "scenario", ref: "card:pier/scenario/pier", name: "The Pier" };
const HARBOR = { campaign: "harbor", title: "Harbor", world: "world:salt" };

function chat(id, { label = null, at = null, scope = { campaign: id, title: null, world: null }, sources = [], launch = null, problem = null } = {}) {
  return { chat: "campaigns/" + id + "/chats/" + id, label, last_activity: at, scope, launch, sources, problem };
}

const tide = chat("c1", { label: "The Lantern Tide", at: "2026-03-04T10:00:00Z", scope: HARBOR, sources: [MARA, OREN, ANYA] });
const ledger = chat("c2", { label: "salt ledger", at: "2026-03-05T09:00:00Z", sources: [OREN, ANYA] });
const watch = chat("c3", { label: "A Long Watch", at: "2026-03-01T08:00:00Z", scope: HARBOR, sources: [TWIN, PIER] });
const premise = chat("c4", { at: "2026-03-02T08:00:00Z", launch: { kind: "premise", ref: null, name: null } });
const nameless = chat("c5", { scope: null, launch: { kind: "character", ref: MARA.ref, name: "Mara Vell" }, sources: [MARA] });
const ALL = [tide, ledger, watch, premise, nameless];
const ids = (chats) => chats.map((item) => item.chat.split("/").at(-1));

test("a chat is named by its label, then what started it, then its exact ref", () => {
  assert.equal(chatTitle(tide), "The Lantern Tide");
  assert.equal(chatTitle(nameless), "Mara Vell");
  assert.equal(chatTitle(premise), "Premise chat");
  assert.equal(chatTitle(chat("c9")), "campaigns/c9/chats/c9");
});

test("where a chat is and who is in it", () => {
  assert.equal(chatWhere(tide), "Harbor");
  assert.equal(chatWhere(chat("c9", { scope: { campaign: "c9", title: null, world: "world:salt" } })), "world:salt");
  assert.equal(chatWhere(ledger), "No world");
  assert.equal(chatWhere(nameless), "No world");
  assert.deepEqual(chatCharacters(tide), [{ name: "Mara Vell", key: MARA.ref }, { name: "Oren Tash", key: OREN.ref }]);
  // faces: the characters, then the persona, each once; a chat that binds no one shows itself
  assert.deepEqual(chatFaces(tide).map((face) => face.name), ["Mara Vell", "Oren Tash", "Anya Rook"]);
  assert.deepEqual(chatFaces(chat("c9", { sources: [MARA, MARA] })).length, 1);
  assert.deepEqual(chatFaces(premise), [{ name: "Premise chat", key: premise.chat }]);
  // a source with no name shows its exact ref, never another's name
  const broken = chat("c9", { sources: [{ role: "character", ref: "card:gone/character/gone", name: null }] });
  assert.deepEqual(chatCharacters(broken), [{ name: "card:gone/character/gone", key: "card:gone/character/gone" }]);
  // the meta line leaves the persona out, as the list always has
  assert.equal(chatMeta(tide), "Harbor · Mara Vell, Oren Tash");
  assert.equal(chatMeta(watch), "Harbor · Mara Vell, The Pier");
  assert.equal(chatMeta(premise), "No world");
  assert.equal(listNames([]), "");
  assert.equal(listNames(["A"]), "A");
  assert.equal(listNames(["A", "B"]), "A and B");
  assert.equal(listNames(["A", "B", "C"]), "A, B and C");
});

test("the address names which chats are listed", () => {
  const view = (text) => viewOf(new URLSearchParams(text));
  assert.deepEqual(view(""), { filter: "all", source: null });
  assert.deepEqual(view("filter=all"), { filter: "all", source: null });
  assert.deepEqual(view("filter=recent&limit=200"), { filter: "all", source: null }, "an old address is every chat");
  assert.deepEqual(view("filter=nonsense&source=card:x/character/x"), { filter: "all", source: null }, "a source needs its filter");
  assert.deepEqual(view("filter=character"), { filter: "character", source: null });
  assert.deepEqual(view("filter=scenario&source=card:pier/scenario/pier"), { filter: "scenario", source: "card:pier/scenario/pier" });
  // what the page offers: no filter, grouping or sort that needs data the list does not carry
  assert.deepEqual(FILTERS.map(([name]) => name), ["all", "character", "scenario"]);
  // the pinned chats are a filter only where the app keeps pins; without them the address is every chat
  assert.deepEqual(PINNED_FILTER, ["pinned", "Pinned"]);
  assert.deepEqual(view("filter=pinned"), { filter: "all", source: null });
  const kept = (text) => viewOf(new URLSearchParams(text), { pins: true });
  assert.deepEqual(kept("filter=pinned&source=card:x/character/x"), { filter: PINNED, source: null }, "pinned chats are of no one source");
  assert.deepEqual(kept("filter=character&source=card:x/character/x"), { filter: "character", source: "card:x/character/x" });
  assert.deepEqual(kept(""), { filter: "all", source: null });
  assert.deepEqual(GROUPS.map(([, label]) => label), ["Campaign", "Character", "None"]);
  assert.deepEqual(SORTS.map(([, label]) => label), ["Recent", "A–Z"]);
  assert.equal(oneOf(GROUPS, "character"), "character");
  assert.equal(oneOf(GROUPS, ""), "campaign");
  assert.equal(oneOf(SORTS, "longest"), "recent", "a kept choice the page no longer offers falls back");
});

test("search matches the title, the campaign or a bound name, every word, whatever the case", () => {
  assert.deepEqual(ids(searchChats(ALL, "")), ids(ALL));
  assert.deepEqual(ids(searchChats(ALL, "   ")), ids(ALL));
  assert.notEqual(searchChats(ALL, ""), ALL, "a new list, never the one given");
  assert.deepEqual(ids(searchChats(ALL, "LANTERN")), ["c1"]);
  assert.deepEqual(ids(searchChats(ALL, "harbor")), ["c1", "c3"]);
  assert.deepEqual(ids(searchChats(ALL, "oren")), ["c1", "c2"]);
  assert.deepEqual(ids(searchChats(ALL, "anya")), ["c1", "c2"], "the persona is found too");
  assert.deepEqual(ids(searchChats(ALL, "harbor mara")), ["c1", "c3"]);
  assert.deepEqual(ids(searchChats(ALL, "harbor oren")), ["c1"]);
  assert.deepEqual(ids(searchChats(ALL, "premise")), ["c4"]);
  assert.deepEqual(searchChats(ALL, "zzzz"), []);
  assert.deepEqual(searchChats([], "mara"), []);
  // a ref is not searched: a nameless source does not match by the words of its ref
  assert.deepEqual(searchChats([chat("c9", { label: "X", sources: [{ role: "character", ref: "card:gone/character/gone", name: null }] })], "gone"), []);
});

test("sorting by what was played last, or by name", () => {
  assert.deepEqual(ids(sortChats(ALL, "recent")), ["c2", "c1", "c4", "c3", "c5"], "latest first; no known activity last");
  assert.deepEqual(ids(sortChats(ALL, "name")), ["c3", "c5", "c4", "c2", "c1"], "by title, whatever the case");
  assert.deepEqual(ids(ALL), ["c1", "c2", "c3", "c4", "c5"], "the list given is not reordered");
  assert.deepEqual(ids(sortChats(ALL, "longest")), ids(sortChats(ALL, "recent")), "an order the list cannot give is the default");
  // ties keep the order they came in
  const same = [chat("b", { label: "Same", at: "2026-01-01T00:00:00Z" }), chat("a", { label: "same", at: "2026-01-01T00:00:00Z" })];
  assert.deepEqual(ids(sortChats(same, "recent")), ["b", "a"]);
  assert.deepEqual(ids(sortChats(same, "name")), ["b", "a"]);
  assert.deepEqual(ids(sortChats([chat("x", { label: "Chat 10" }), chat("y", { label: "Chat 9" })], "name")), ["y", "x"], "numbers in order");
  assert.deepEqual(sortChats([], "recent"), []);
});

test("grouping by campaign, by character, or not at all", () => {
  const shape = (groups) => groups.map((group) => [group.label, ids(group.chats)]);
  assert.deepEqual(shape(groupChats(ALL, "campaign")), [["Harbor", ["c1", "c3"]], ["Not in a campaign", ["c2", "c4", "c5"]]]);
  // groups stand where their first chat does, so the sort orders them too
  assert.deepEqual(shape(groupChats(sortChats(ALL, "recent"), "campaign")),
    [["Not in a campaign", ["c2", "c4", "c5"]], ["Harbor", ["c1", "c3"]]]);
  // two campaigns of one title are two groups: the campaign decides, not its name
  const other = chat("c6", { label: "Elsewhere", scope: { campaign: "harbor-2", title: "Harbor", world: null } });
  const twice = groupChats([tide, other], "campaign");
  assert.deepEqual(shape(twice), [["Harbor", ["c1"]], ["Harbor", ["c6"]]]);
  assert.notEqual(twice[0].key, twice[1].key);
  // by the first character a chat is with, by exact ref: the two Maras are two groups; no character is its own
  assert.deepEqual(shape(groupChats(ALL, "character")),
    [["Mara Vell", ["c1", "c5"]], ["Oren Tash", ["c2"]], ["Mara Vell", ["c3"]], ["No character", ["c4"]]]);
  assert.deepEqual(shape(groupChats(ALL, "none")), [["All chats", ["c1", "c2", "c3", "c4", "c5"]]]);
  for (const group of ["campaign", "character", "none"]) assert.deepEqual(groupChats([], group), [], "no chats, no groups");
  // every chat is in exactly one group
  for (const group of ["campaign", "character", "none"]) {
    assert.deepEqual(groupChats(ALL, group).flatMap((item) => ids(item.chats)).sort(), ids(ALL));
    assert.equal(new Set(groupChats(ALL, group).map((item) => item.key)).size, groupChats(ALL, group).length);
  }
});

test("pinned chats are lifted into a Pinned group at the top, each listed once", () => {
  const pinned = new Set([watch.chat, ledger.chat, "campaigns/gone/chats/gone"]);  // a pin on a chat that is not listed lists nothing
  const recent = sortChats(ALL, "recent");
  const byCampaign = groupChats(recent, "campaign", pinned);
  assert.deepEqual(byCampaign.map((group) => [group.key, group.label, ids(group.chats)]), [
    ["pinned", "Pinned", ["c2", "c3"]],            // in the list's own order: the one played last first
    ["campaign:harbor", "Harbor", ["c1"]],         // the pinned chat left its campaign's group
    ["", "Not in a campaign", ["c4", "c5"]],
  ]);
  assert.equal(byCampaign.flatMap((group) => group.chats).length, ALL.length, "no chat is listed twice or lost");
  // a group whose every chat is pinned is not shown empty
  assert.deepEqual(groupChats([tide, watch], "campaign", new Set([tide.chat, watch.chat])).map((group) => group.label), ["Pinned"]);
  // ungrouped, the pinned chats still come first
  assert.deepEqual(groupChats(recent, "none", pinned).map((group) => [group.label, ids(group.chats)]),
    [["Pinned", ["c2", "c3"]], ["All chats", ["c1", "c4", "c5"]]]);
  // nothing pinned, or no pins kept: the groups are as they were
  assert.deepEqual(groupChats(recent, "campaign", new Set()), groupChats(recent, "campaign"));
  assert.deepEqual(groupChats(recent, "campaign", null), groupChats(recent, "campaign"));
  assert.equal(countLabel(ALL.length, byCampaign.length, "campaign"), "5 chats in 3 groups");
  // the Pinned filter: those chats alone, searched and sorted like any list
  assert.deepEqual(ids(pinnedOnly(ALL, pinned)), ["c2", "c3"]);
  assert.deepEqual(pinnedOnly(ALL, new Set()), []);
  assert.deepEqual(groupChats(sortChats(pinnedOnly(ALL, pinned), "name"), "character", pinned).map((group) => [group.label, ids(group.chats)]),
    [["Pinned", ["c3", "c2"]]]);
  assert.deepEqual(shownRows(byCampaign[0].chats, "campaign", false), { rows: [ledger, watch], more: 0 });
});

test("a group shows five rows, the ungrouped list thirty, then the rest on request", () => {
  const many = Array.from({ length: 40 }, (_, index) => chat("m" + index));
  assert.equal(GROUP_ROWS, 5);
  assert.equal(FLAT_ROWS, 30);
  assert.deepEqual([shownRows(many, "campaign", false).rows.length, shownRows(many, "campaign", false).more], [5, 35]);
  assert.deepEqual([shownRows(many, "character", false).rows.length, shownRows(many, "character", false).more], [5, 35]);
  assert.deepEqual([shownRows(many, "none", false).rows.length, shownRows(many, "none", false).more], [30, 10]);
  assert.deepEqual([shownRows(many, "campaign", true).rows.length, shownRows(many, "campaign", true).more], [40, 0]);
  assert.deepEqual(shownRows(many.slice(0, 5), "campaign", false).more, 0, "exactly five has nothing more");
  assert.deepEqual(shownRows(many.slice(0, 6), "campaign", false).more, 1);
  assert.deepEqual(shownRows([], "none", false), { rows: [], more: 0 });
  assert.deepEqual(ids(shownRows(many, "campaign", false).rows), ["m0", "m1", "m2", "m3", "m4"], "the first ones, in order");
});

test("the count line", () => {
  assert.equal(countLabel(58, 25, "campaign"), "58 chats in 25 groups");
  assert.equal(countLabel(1, 1, "character"), "1 chat in 1 group");
  assert.equal(countLabel(58, 1, "none"), "58 chats");
  assert.equal(countLabel(0, 0, "campaign"), "0 chats");
  assert.equal(plural(1, "chat", "chats"), "1 chat");
  assert.equal(plural(0, "chat", "chats"), "0 chats");
});

test("when a chat was last played, in words", () => {
  const now = Date.parse("2026-03-10T12:00:00Z");
  assert.equal(when("2026-03-10T11:59:30Z", now), "Just now");
  assert.equal(when("2026-03-10T12:05:00Z", now), "Just now", "a clock a little ahead is not the future");
  assert.equal(when("2026-03-10T11:59:00Z", now), "1 minute ago");
  assert.equal(when("2026-03-10T11:15:00Z", now), "45 minutes ago");
  assert.equal(when("2026-03-10T11:00:00Z", now), "1 hour ago");
  assert.equal(when("2026-03-09T12:30:00Z", now), "23 hours ago");
  assert.equal(when("2026-03-09T12:00:00Z", now), "Yesterday");
  assert.equal(when("2026-03-08T11:00:00Z", now), "2 days ago");
  assert.equal(when("2026-02-09T12:00:00Z", now), "29 days ago");
  assert.match(when("2025-06-15T12:00:00Z", now), /2025/, "older than a month is a date");
  assert.equal(when(null, now), "");
  assert.equal(when("not a time", now), "");
});

test("Home greets by the hour", () => {
  assert.deepEqual([0, 4, 5, 11, 12, 17, 18, 23].map(greeting),
    ["Good evening", "Good evening", "Good morning", "Good morning", "Good afternoon", "Good afternoon", "Good evening", "Good evening"]);
});
