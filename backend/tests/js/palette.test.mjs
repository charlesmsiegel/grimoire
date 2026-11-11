// The palette (108): ranking and merging, what its sources' answers become, the keys that open it and the theme,
// and the dialog itself in fakedom with sources that answer late, fail or ask again. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const shell = path.join(here, "..", "..", "src", "grimoire", "app", "static", "shell");
const { GROUPS, LINE_MIN, THEMES, characterItems, fold, isTyping, lineItems, lineNote, merge, openPalette, opensPalette, queryLength, rank,
  readTheme, score, setTheme } = await import(pathToFileURL(path.join(shell, "palette.js")).href);

const item = (label, keywords) => ({ label, keywords, run() {} });
const of = (group, label, more = {}) => ({ label, group, run() {}, ...more });
const labels = (items) => items.map((entry) => entry.label);
const pause = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

test("folding takes out case and accents, as the app's line search does", () => {
  assert.equal(fold("SÉRAPHINE"), "seraphine");
  assert.equal(fold("ﬁne Ångström"), "fine angstrom");  // the same samples as test_app_shell_lines.py
  assert.equal(queryLength("  Á  b "), 2);
  assert.equal(queryLength("́́"), 0, "a mark alone is nothing to search for");
  assert.equal(LINE_MIN, 2);
});

test("equal matches are listed pages, chats, characters, commands, whatever order they arrived in; lines come last", () => {
  assert.deepEqual(GROUPS, ["page", "chat", "character", "command"]);
  const named = [of("command", "Mara theme"), of("character", "Mara Vell"), of("page", "Mara page"), of("chat", "Mara Vell"),
    of("chat", "The tide and Mara")];
  const lines = [of("line", "“Mara was never here.”", { line: true }), of("line", "“Ask the tide.”", { line: true })];
  const found = merge("mara", named, lines);
  assert.deepEqual(found.map((entry) => entry.group), ["page", "chat", "character", "command", "chat", "line", "line"]);
  assert.equal(found.at(-1).label, "“Ask the tide.”", "a line is not ranked again: the app matched it by its words");
  // a better match still comes first, whatever its group
  assert.deepEqual(labels(merge("theme", [of("page", "A theme park"), of("command", "Theme: dark")])), ["Theme: dark", "A theme park"]);
  // before anything is typed: every named item, grouped, and no line
  assert.deepEqual(merge("  ", named, lines).map((entry) => entry.group), ["page", "chat", "chat", "character", "command"]);
  assert.equal(merge("", Array.from({ length: 40 }, (_, n) => of("page", "p" + n))).length, 40);
});

test("the list holds twelve: lines keep four places when both have more, and take what the names leave", () => {
  const names = Array.from({ length: 30 }, (_, n) => of("chat", "tide " + n));
  const lines = Array.from({ length: 12 }, (_, n) => of("line", "line " + n, { line: true }));
  const full = merge("tide", names, lines);
  assert.equal(full.length, 12);
  assert.deepEqual(labels(full.slice(8)), ["line 0", "line 1", "line 2", "line 3"]);
  assert.deepEqual(labels(full.slice(0, 2)), ["tide 0", "tide 1"]);
  const few = merge("tide", names.slice(0, 3), lines);
  assert.deepEqual(few.map((entry) => entry.group), [...Array(3).fill("chat"), ...Array(9).fill("line")]);
  assert.deepEqual(labels(merge("tide", names, lines.slice(0, 1)).slice(-2)), ["tide 10", "line 0"]);
  assert.equal(merge("zzz", names, []).length, 0);
});

test("a catalog's characters become items by name, each with its source; one with a problem is left out", () => {
  const opened = [];
  const items = characterItems({ sources: [{ source: "local:mine", title: "My cards" }, { source: "st:pack", title: null }],
    entries: [{ ref: "local:mine/character/mara", source: "local:mine", name: "Mara Vell", problem: null },
      { ref: "st:pack/character/mara", source: "st:pack", name: "Mara Vell", problem: null },
      { ref: "st:pack/character/x1", source: "st:pack", name: null, problem: null },
      { ref: "st:pack/character/gone", source: "st:pack", name: "Gone", problem: "entity_dropped" }] }, (ref) => opened.push(ref));
  assert.deepEqual(items.map((entry) => [entry.label, entry.detail, entry.group]), [
    ["Mara Vell", "character · My cards", "character"], ["Mara Vell", "character · st:pack", "character"],
    ["st:pack/character/x1", "character · st:pack", "character"]]);
  items[1].run();
  assert.deepEqual(opened, ["st:pack/character/mara"], "a namesake opens its own sheet, by its ref");
  assert.deepEqual(labels(rank("pack x1", items)), ["st:pack/character/x1"], "found by its ref too");
  assert.deepEqual(characterItems({}, () => {}), []);
});

test("a line that was said shows its words in quotation marks, then who said it, the chat and the turn", () => {
  const opened = [];
  const answer = { lines: [{ chat: "campaign:q1#chat/t1", title: "Harbor night", turn: 7, speaker: "Mara Vell", text: "Safe as the tide." },
    { chat: "campaign:q2#chat/t2", title: "Mara Vell", turn: 1, speaker: null, text: "…the lamps gutter…" }] };
  const items = lineItems(answer, (line) => opened.push([line.chat, line.turn]));
  assert.deepEqual(items.map((entry) => [entry.label, entry.detail, entry.line, entry.group]), [
    ["“Safe as the tide.”", "Mara Vell · Harbor night · turn 7", true, "line"],
    ["“…the lamps gutter…”", "Narrator · Mara Vell · turn 1", true, "line"]]);
  items[0].run();
  assert.deepEqual(opened, [["campaign:q1#chat/t1", 7]]);
  assert.deepEqual(lineItems({}, () => {}), []);
});

test("a line search a cap stopped says how far it got", () => {
  assert.equal(lineNote({ stopped: null, searched: 3, chats: 3 }), "");
  assert.equal(lineNote({ stopped: "time", searched: 80, chats: 300 }), "Still reading older chats: 80 of 300 chats looked through so far.");
  assert.equal(lineNote({ stopped: "chats", searched: 2000, chats: 2400 }), "Only the 2000 most recent chats were looked through for what was said.");
  assert.equal(lineNote({ stopped: "unreadable", searched: 9, chats: 10 }), "Some chats could not be read: 9 of 10 chats looked through for what was said.");
});

// -- the dialog, in fakedom ----------------------------------------------------------------------------------
install("http://127.0.0.1:1");
Object.assign(Object.getPrototypeOf(document.createElement("dialog")), {
  showModal() { this.open = true; }, close() { this.open = false; } });

function opened(sources, options = {}) {
  for (const old of document.body.querySelectorAll("dialog")) old.remove();
  const dialog = openPalette({ items: () => [of("page", "Chats"), of("command", "Theme: dark")], sources, ...options });
  const input = dialog.querySelector("input");
  return { dialog, input,
    type(text) { input.value = text; input.dispatchEvent({ type: "input" }); },
    shown: () => dialog.querySelectorAll("li").map((node) => node.textContent),
    status: () => dialog.querySelector("div.g-palette-status").textContent };
}

test("the palette says what it searches, and lists every page and command before anything is typed", () => {
  const palette = opened([]);
  assert.equal(palette.input.getAttribute("aria-label"), "Search pages, chats, characters, commands and anything that was said");
  assert.equal(palette.input.getAttribute("placeholder"), "Search pages, chats, characters and anything that was said");
  assert.deepEqual(palette.shown(), ["Chats", "Theme: dark"]);
});

test("a source that fails is named, and the other sources' results stay", async () => {
  const palette = opened([
    { name: "Chats", search: async () => [of("chat", "Tide at night", { detail: "chat" })] },
    { name: "Characters", search: async () => { throw new Error("the library is busy"); } },
    { name: "What was said", lines: true, search: async (query) => ({ items: query === "tide"
      ? [of("line", "“The tide is in.”", { line: true, detail: "Mara · Tide at night · turn 2" })] : [] }) },
  ]);
  palette.type("tide");
  await pause();
  assert.deepEqual(palette.shown(), ["Tide at night chat", "“The tide is in.” Mara · Tide at night · turn 2"]);
  assert.equal(palette.status(), "Characters could not be searched: the library is busy");
  assert.ok(palette.dialog.querySelectorAll("li")[1].querySelector("span.g-palette-line"), "a line is shown as story text");
  assert.ok(!palette.dialog.querySelectorAll("li")[0].querySelector("span.g-palette-line"));
  palette.type("zzz");
  await pause();
  assert.equal(palette.status(), "Nothing matches. Characters could not be searched: the library is busy");
});

test("a line search waits for the typing to rest, is not asked below its least length, and a late answer is dropped", async () => {
  const asked = [];
  const answers = new Map();
  const palette = opened([{ name: "What was said", lines: true, min: 2, wait: 30, search: (query) => {
    asked.push(query);
    return new Promise((resolve) => answers.set(query, () => resolve([of("line", "“" + query + "”", { line: true })])));
  } }]);
  palette.type("t");
  await pause(60);
  assert.deepEqual(asked, [], "one character asks nothing");
  assert.deepEqual(palette.shown(), ["Theme: dark", "Chats"]);
  for (const text of ["ti", "tid", "tide"]) palette.type(text);
  assert.equal(palette.status(), "Searching…");
  await pause(60);
  assert.deepEqual(asked, ["tide"], "typing on asks once, for what was typed last");
  palette.type("tides");
  await pause(60);
  assert.deepEqual(asked, ["tide", "tides"]);
  answers.get("tide")();  // the older answer arrives after the newer question
  await pause();
  assert.deepEqual(palette.shown(), [], "an answer to what is no longer typed is not shown");
  answers.get("tides")();
  await pause();
  assert.deepEqual(palette.shown(), ["“tides”"]);
  assert.equal(palette.status(), "");
  palette.type("");
  await pause(60);
  assert.deepEqual(asked, ["tide", "tides"]);
  assert.deepEqual(palette.shown(), ["Chats", "Theme: dark"]);
});

test("a source that is not done yet is asked again, a bounded number of times, showing how far it got", async () => {
  let calls = 0;
  const palette = opened([{ name: "What was said", lines: true, search: async () => {
    calls += 1;
    return { items: Array.from({ length: Math.min(calls, 3) }, (_, n) => of("line", "line " + n, { line: true })),
      note: "read " + calls, again: true };
  } }], { again: 4 });
  palette.type("li");
  await pause(20);
  assert.equal(calls, 5, "once, and four times more");
  assert.deepEqual(palette.shown(), ["line 0", "line 1", "line 2"]);
  assert.equal(palette.status(), "read 5");
});

test("Enter runs the active option and closes the palette; an answer after that is for nobody", async () => {
  const ran = [];
  let late;
  const palette = opened([
    { name: "Chats", search: async () => [of("chat", "Tide at night", { run: () => ran.push("chat") })] },
    { name: "What was said", lines: true, search: () => new Promise((resolve) => { late = resolve; }) },
  ]);
  palette.type("tide");
  await pause();
  palette.dialog.dispatchEvent({ type: "keydown", key: "Enter", preventDefault() {} });
  assert.deepEqual(ran, ["chat"]);
  assert.ok(!document.body.querySelector("dialog"));
  late([of("line", "late", { line: true })]);
  await pause();
  assert.ok(!document.body.querySelector("dialog"));
});

test("every word must match; a word the name starts with ranks first; ties keep their order", () => {
  const items = [item("Chats"), item("Seraphine at the tide"), item("Mara's ledger"), item("Library setup", "setup")];
  assert.deepEqual(rank("", items).map((entry) => entry.label), items.map((entry) => entry.label));
  assert.deepEqual(rank("tide sera", items).map((entry) => entry.label), ["Seraphine at the tide"]);
  assert.deepEqual(rank("se", items).map((entry) => entry.label), ["Seraphine at the tide", "Library setup"]);
  assert.equal(score("zzz", "Chats"), null);
  assert.ok(score("ch", "Chats") > score("ts", "Chats"));
  assert.equal(score("seraphine", "SÉRAPHINE"), score("seraphine", "seraphine"), "case and accents do not count");
  assert.equal(rank("a", Array.from({ length: 40 }, (_, n) => item("a" + n))).length, 12);
});

test("Ctrl+K, Cmd+K or slash opens the palette, never while typing", () => {
  assert.ok(opensPalette({ key: "k", ctrlKey: true }, false));
  assert.ok(!opensPalette({ key: "k", ctrlKey: true }, true));  // a Mac text box deletes to the line's end
  assert.ok(!opensPalette({ key: "k", metaKey: true }, true));
  assert.ok(opensPalette({ key: "K", metaKey: true }, false));
  assert.ok(!opensPalette({ key: "k", ctrlKey: true, altKey: true }, false));
  assert.ok(opensPalette({ key: "/" }, false));
  assert.ok(!opensPalette({ key: "/" }, true));
  assert.ok(isTyping({ tagName: "textarea" }) && isTyping({ tagName: "INPUT" }) && !isTyping({ tagName: "BUTTON" }) && !isTyping(null));
});

test("a theme is kept and applied; anything else is the system's", () => {
  const items = new Map();
  const storage = { getItem: (key) => items.get(key) ?? null, setItem: (key, value) => items.set(key, value) };
  const attributes = new Map();
  const root = { setAttribute: (name, value) => attributes.set(name, value), removeAttribute: (name) => attributes.delete(name) };
  assert.deepEqual(THEMES, ["system", "light", "dark"]);
  assert.equal(readTheme(storage), "system");
  assert.equal(setTheme("dark", root, storage), "dark");
  assert.equal(attributes.get("data-theme"), "dark");
  assert.equal(readTheme(storage), "dark");
  assert.equal(setTheme("neon", root, storage), "system");
  assert.ok(!attributes.has("data-theme"));
  items.set("grimoire-theme", "neon");
  assert.equal(readTheme(storage), "system");
  assert.equal(readTheme({ getItem() { throw new Error("blocked"); } }), "system");
});

test("the theme showing is the person's choice, or the system's when they made none", async () => {
  const { shownTheme } = await import(pathToFileURL(path.join(shell, "palette.js")).href);
  const storage = { value: null, getItem() { return this.value; }, setItem(_key, value) { this.value = value; } };
  assert.equal(shownTheme(storage, true), "dark");
  assert.equal(shownTheme(storage, false), "light");
  storage.value = "light";
  assert.equal(shownTheme(storage, true), "light");
});
