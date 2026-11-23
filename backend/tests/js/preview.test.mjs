// The chat page's Preview context (pages/chat/preview.js, 116 over 085): only the chat's NPCs are offered; the
// table view shows no NPC's context; the director's view sends the composer's unsent draft and never changes it;
// lore triggered, memory recalled and recalls the budget sheds are told apart; an NPC that would not hear the draft
// keeps its saved context; a refusal is said with its reason; and an answer for an earlier draft or NPC is dropped.
// Run by test_app_shell_js.py under fakedom. Every name is made up.
import assert from "node:assert/strict";
import { test } from "node:test";

import { byText, install, until } from "./fakedom.mjs";
import * as ui from "../../src/grimoire/app/static/shell/ui.js";
import { current, makePreview, npcsOf, recallWhy, sortRecalls } from "../../src/grimoire/app/static/pages/chat/preview.js";

const CHAT = "campaign:c#chat/one", MARA = "campaign:c/character/mara", TAMSIN = "campaign:c/character/tamsin";
const scene = () => ({ cast: [{ actor: "campaign:c/character/anya", name: "Anya Rook", role: "player", persona: true },
  { actor: MARA, name: "Mara Vell", role: "npc" }, { actor: TAMSIN, name: "Tamsin Hale", role: "npc" }] });
const LORE = { line: "The lighthouse has been dark for a year.", lane: "lore", tokens: 9, included: true, reason: null, why: "triggered", words: [] };
const MEMORY = { line: "You were told: a promise about a compass.", lane: "memory", tokens: 10, included: true, reason: null, why: "shared_words", words: ["compass"] };
const SHED = { line: "You were told: an old compass rumor.", lane: "memory", tokens: 8, included: false, reason: "lane_cap", why: "shared_words", words: ["compass"] };
const answer = (actor, { heard = true, recalls = [LORE, MEMORY], shed = 0 } = {}) => ({ chat: CHAT, sent: false, written: false, estimated: true,
  actor, draft: { heard, kind: "speech" }, recalls, shed_recalls: shed, more_recalls: 0, tokens: { now: 100, with_draft: 120 },
  changes: { added: [], removed: [] }, context: { budget: { max_tokens: 4000 } } });

function panel({ reply, has = true, mode } = {}) {
  install("http://localhost");
  const controller = new AbortController();
  const asked = [];
  const composer = ui.field("Your turn", { value: "Is the lighthouse lit?", multiline: true });
  const ctx = { ui, signal: controller.signal, styles() {}, registered: (route) => route === "providers",
    has: (op) => has && op === "providers.preview_context",
    api: (op, args) => { asked.push({ op, args }); return reply ? reply(args) : Promise.resolve(answer(args.actor)); } };
  const made = makePreview(ctx, CHAT, mode ? { composer, mode } : { composer });
  if (made.node) document.body.append(made.node, composer.node);
  const root = made.node;
  const button = (name) => byText(root, "button", name).find((node) => node.textContent === name);
  const status = () => root.querySelector("p.chat-preview-status").textContent;
  const box = () => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === "Preview an NPC's context");
  const director = () => button("Director view").click();
  return { made, root, composer, asked, button, status, box, director, controller };
}

test("only the chat's NPCs, and the recalls told apart", () => {
  assert.deepEqual(npcsOf(scene()).map((item) => item.name), ["Mara Vell", "Tamsin Hale"]);
  const sorted = sortRecalls({ recalls: [SHED, LORE, MEMORY] });
  assert.deepEqual([sorted.shed, sorted.lore, sorted.memory, sorted.other], [[SHED], [LORE], [MEMORY], []]);
  assert.equal(recallWhy(LORE), "your draft triggers this lore (exact)");
  assert.equal(recallWhy(SHED), "your draft shares compass (an estimate); left out: its lane is full");
  assert.equal(current({ serial: 1, actor: MARA, draft: "a" }, { serial: 1, actor: MARA, draft: "a" }), true);
  assert.equal(current({ serial: 1, actor: MARA, draft: "a" }, { serial: 1, actor: MARA, draft: "b" }), false);
});

test("no panel where the app does not serve the preview", () => {
  const p = panel({ has: false });
  assert.equal(p.made.node, null);
  p.made.update(scene());
});

test("the table view shows no NPC's context; the director's previews the draft and leaves it as it is", async () => {
  const p = panel();
  p.made.update(scene());
  assert.equal(p.box().hidden, true);
  assert.match(p.root.textContent, /is the director's\. Switch to the director view/);
  assert.equal(p.asked.length, 0, "the table view asks for nothing");
  p.director();
  assert.equal(p.box().hidden, false);
  const select = p.box().querySelector("select");
  assert.deepEqual(select.querySelectorAll("option").map((node) => node.textContent), ["Mara Vell", "Tamsin Hale"]);
  assert.equal(p.root.querySelectorAll("a").find((node) => node.textContent === "Full context").getAttribute("href"), "#/providers?chat=" + encodeURIComponent(CHAT));
  p.composer.input.focus();  // the person went back to the composer: an answer never takes the focus from it
  p.button("Preview context").click();
  await until(() => p.status().startsWith("An estimate for Mara Vell"), 2000, "the preview");
  assert.ok(document.activeElement === p.composer.input);
  assert.deepEqual(p.asked, [{ op: "providers.preview_context", args: { chat: CHAT, actor: MARA, draft: "Is the lighthouse lit?", mode: "auto" } }]);
  assert.equal(p.composer.input.value, "Is the lighthouse lit?", "the composer is never changed");
  assert.match(p.root.querySelector('ul[aria-label="Lore the draft triggers"]').textContent, /lighthouse has been dark.*\(exact\)/);
  assert.match(p.root.querySelector('ul[aria-label="Memories it likely recalls"]').textContent, /promise about a compass.*\(an estimate\)/);
  assert.equal(p.root.querySelector('div[role="alert"]'), null, "nothing is shed");
  // back to the table view: the preview is gone from the screen
  p.button("Table view").click();
  assert.equal(p.box().hidden, true);
  assert.equal(p.root.querySelector('ul[aria-label="Lore the draft triggers"]'), null);
});

test("a shed recall is a warning, an NPC that would not hear keeps its context, and a refusal is said", async () => {
  let next = (args) => Promise.resolve(answer(args.actor, { recalls: [SHED, LORE], shed: 1 }));
  const p = panel({ reply: (args) => next(args) });
  p.made.update(scene());
  p.director();
  p.button("Preview context").click();
  await until(() => p.root.querySelector('div[role="alert"]'), 2000, "the warning");
  assert.match(p.root.querySelector('div[role="alert"]').textContent, /points at something Mara Vell will not get/);
  assert.match(p.root.querySelector('ul[aria-label="Left out by the budget"]').textContent, /old compass rumor.*its lane is full/);
  next = (args) => Promise.resolve(answer(args.actor, { heard: false, recalls: [] }));
  p.button("Preview context").click();
  await until(() => /would not hear this draft/.test(p.root.textContent), 2000);
  next = () => Promise.reject(Object.assign(new Error("this draft is a table request"), { reason: "table_request" }));
  p.button("Preview context").click();
  await until(() => /could not be previewed: table_request/.test(p.status()), 2000);
  p.composer.value = "   ";
  p.button("Preview context").click();
  assert.match(p.status(), /Write a draft in the composer first/);
  assert.equal(p.composer.value, "   ");
});

test("an answer for an earlier draft or another NPC is dropped", async () => {
  let release;
  const p = panel({ reply: (args) => new Promise((resolve) => { release = () => resolve(answer(args.actor)); }) });
  p.made.update(scene());
  p.director();
  p.button("Preview context").click();
  p.composer.value = "Something else now";  // typed on while the preview was built
  release();
  await until(() => /changed while the preview was built, so it was dropped/.test(p.status()), 2000);
  assert.equal(p.root.querySelector('ul[aria-label="Lore the draft triggers"]'), null);
  p.button("Preview context").click();
  const select = p.box().querySelector("select");
  select.value = TAMSIN;  // another NPC chosen while Mara's preview was on its way
  select.dispatchEvent({ type: "change" });
  release();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(p.root.querySelector('ul[aria-label="Lore the draft triggers"]'), null, "Mara's late answer is not shown for Tamsin");
  p.button("Preview context").click();
  release();
  await until(() => p.status().startsWith("An estimate for Tamsin Hale"), 2000);
  assert.equal(p.asked.at(-1).args.actor, TAMSIN);
});

test("the draft is sent as Post sends it: trimmed, in the composer's mode; a changed mode drops a late answer", async () => {
  let release, typed = "speech";
  const p = panel({ mode: () => typed, reply: (args) => new Promise((resolve) => { release = () => resolve(answer(args.actor)); }) });
  p.made.update(scene());
  p.director();
  p.composer.value = "  Is the lighthouse lit?\n";
  p.button("Preview context").click();
  assert.deepEqual(p.asked.at(-1).args, { chat: CHAT, actor: MARA, draft: "Is the lighthouse lit?", mode: "speech" });
  assert.equal(p.composer.value, "  Is the lighthouse lit?\n", "the composer is never changed");
  typed = "action";  // the composer's mode was switched while the preview was built
  release();
  await until(() => /changed while the preview was built, so it was dropped/.test(p.status()), 2000);
  assert.equal(p.root.querySelector('ul[aria-label="Lore the draft triggers"]'), null);
  p.button("Preview context").click();
  assert.equal(p.asked.at(-1).args.mode, "action");
  p.composer.value = "Is the lighthouse lit?  ";  // spaces at the end change nothing post would read
  release();
  await until(() => p.status().startsWith("An estimate for Mara Vell"), 2000);
});

test("the chosen NPC leaving the scene clears what was shown for them", async () => {
  const p = panel();
  p.made.update(scene());
  p.director();
  p.button("Preview context").click();
  await until(() => p.status().startsWith("An estimate for Mara Vell"), 2000);
  const without = scene();
  without.cast = without.cast.filter((member) => member.actor !== MARA);
  p.made.update(without);
  assert.equal(p.status(), "");
  assert.equal(p.root.querySelector('ul[aria-label="Lore the draft triggers"]'), null);
  assert.equal(p.box().querySelector("select").value, TAMSIN);
});
