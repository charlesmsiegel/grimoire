// The palette's pure parts (108): ranking, the keys that open it and the theme. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const shell = path.join(here, "..", "..", "src", "grimoire", "app", "static", "shell");
const { THEMES, isTyping, opensPalette, rank, readTheme, score, setTheme } = await import(path.join(shell, "palette.js"));

const item = (label, keywords) => ({ label, keywords, run() {} });

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
