// A lorebook's entry pictures on its sheet (pages/art/covers.js, lorePictures) with canned answers, in fakedom:
// a picture is put beside an entry only while the page shows the lorebook revision the app numbered the entries
// in. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { byText, install, until } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
install("http://127.0.0.1:1");
const ui = await import(pathToFileURL(path.join(STATIC, "shell", "ui.js")).href);
const { href } = await import(pathToFileURL(path.join(STATIC, "shell", "router.js")).href);
const { lorePictures } = await import(pathToFileURL(path.join(STATIC, "pages", "art", "covers.js")).href);

const LORE = "world:harbor/lorebook/harbor-lore";
const entries = (revision, slots) => ({ subject: LORE, revision, orphans: [], art: { slots: [], own: [] },
  entries: slots.map((slot, index) => ({ index, slot, picture: { slot, state: "empty", etag: null } })) });

function context(answers) {
  const calls = [];
  const ctx = { ui, href, signal: { aborted: false }, styles() {}, registered: () => true,
    has: (op) => op.startsWith("media."),
    api: async (op, args) => { calls.push([op, args]); return answers.shift(); }, act: (work) => work() };
  return { ctx, calls };
}

test("an entry's picture slot is its own while the page shows the revision the entries were numbered in", async () => {
  const { ctx, calls } = context([entries("r2", ["entry.aaa", "entry.bbb"])]);
  const pictures = lorePictures(ctx, { subject: LORE, source: "world:harbor", name: "Harbor lore", revision: "r2" });
  const first = pictures.slot(0, "lighthouse");
  const second = pictures.slot(1, "quay");
  await until(() => first.querySelector("button"), 2000, "the slots");
  assert.deepEqual(calls, [["media.entries", { subject: LORE }]]);
  assert.equal(first.querySelector("button").getAttribute("data-focus-key"), "art-slot:" + LORE + ":entry.aaa");
  assert.equal(second.querySelector("button").getAttribute("data-focus-key"), "art-slot:" + LORE + ":entry.bbb");
  assert.equal(pictures.note.hidden, true);
});

test("a lorebook that changed since the page was drawn pairs no picture with an entry by its place", async () => {
  // the page drew r2 (lighthouse, quay); another tab reordered the entries, so the head is r3 (quay, lighthouse)
  let reloads = 0;
  const { ctx } = context([entries("r3", ["entry.bbb", "entry.aaa"])]);
  const pictures = lorePictures(ctx, { subject: LORE, source: "world:harbor", name: "Harbor lore", revision: "r2",
    reload: () => { reloads += 1; } });
  const first = pictures.slot(0, "lighthouse");
  const second = pictures.slot(1, "quay");
  await until(() => !pictures.note.hidden, 2000, "the note");
  assert.equal(first.querySelector("button"), null, "no slot of another entry is offered beside this one");
  assert.equal(second.querySelector("button"), null);
  assert.ok(pictures.note.textContent.includes("has changed since this page was drawn"));
  byText(pictures.note, "button", "Show it as it is now")[0].click();
  assert.equal(reloads, 1);
});
