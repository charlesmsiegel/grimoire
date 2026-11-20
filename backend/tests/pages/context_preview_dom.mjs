// The Models and costs page's context panel (112) and its draft preview (085), run in the stand-in DOM with a
// fake ctx.api: no browser, no network. Fails (exit 1) when the preview asks for nothing typed, hides a recalled
// memory the budget sheds, says an unheard draft changed the context, draws a stale answer, or drops a tactics save
// or a context read made around a preview.
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { install } from "../js/fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
install("http://127.0.0.1:9");
const ui = await import(pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static", "shell", "ui.js")).href);
const { contextPanel } = await import(process.argv[2]);

const view = { label: "x", actor: "q/character/npc", system: "You are Mara.", heard: "Anya: <b>compass</b>?",
  items: [], omitted: [], budget: { tokens: 10, max_tokens: 1500, measurement: "estimated", lane_caps: {}, item_overhead: 1 } };
const heard = { estimated: true, sent: false, written: false, actor: "q/character/npc", draft: { heard: true, kind: "speech" },
  tokens: { now: 10, with_draft: 22 }, more_recalls: 0, shed_recalls: 1, context: view,
  recalls: [
    { line: "You were told: the <i>silver</i> compass", lane: "memory", tokens: 9, included: true, reason: null, why: "shared_words", words: ["compass", "silver"], provenance: {} },
    { line: "The lighthouse has been dark for a year.", lane: "lore", tokens: 9, included: false, reason: "lane_cap", why: "triggered", words: [], provenance: {} }],
  changes: { added: [{ line: "Added lore", lane: "lore", tokens: 3, provenance: {} }], removed: [] } };
const unheard = { ...heard, draft: { heard: false, kind: "speech" }, recalls: [], shed_recalls: 0, changes: { added: [], removed: [] }, tokens: { now: 10, with_draft: 10 } };
// the list the server cut: every listed recall is packed, but it counts three it shed
const cut = { ...heard, recalls: [heard.recalls[0]], more_recalls: 70, shed_recalls: 3 };

const calls = [], saved = [];
let answer = heard, hold = null, holdRead = null, refuseSave = null;
const api = async (op, args) => {
  calls.push([op, args]);
  if (op === "providers.context") {
    const read = { actors: [{ actor: "q/character/npc", name: "Mara" }, { actor: "q/character/b", name: "Bo" }],
      context: args.actor ? view : null, agenda: args.actor ? { editable: true, aim: "Keep the light", actions: ["wait"] } : null,
      routes: [], etag: "e1", writable: true };
    if (holdRead) return new Promise((resolve) => { const release = holdRead; holdRead = null; release(() => resolve(read)); });
    return read;
  }
  if (op === "providers.save_agenda") { if (refuseSave) throw refuseSave; return { etag: "e2" }; }
  if (op === "providers.preview_context") {
    if (hold) return new Promise((resolve) => { const release = hold; hold = null; release(() => resolve(answer)); });
    return answer;
  }
  throw new Error(op);
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
const panel = contextPanel({ ui, api }, (result) => saved.push(result));
document.body.append(panel.node);
const form = () => panel.node.querySelector('div[aria-label="Draft preview"]');
const textarea = () => form().querySelector("textarea");
const press = (root) => [...root.querySelectorAll("button")].find((item) => item.textContent === "Preview with draft").click();
const submit = () => press(form());
const choose = async (value) => { const select = panel.node.querySelector("select"); select.value = value; select.dispatchEvent({ type: "change" }); await tick(); };

panel.changeChat("c1");
await tick();
assert.equal(form().hidden, true, "no draft box before an NPC is chosen");

await choose("q/character/npc");
assert.equal(form().hidden, false);
assert.equal(panel.node.querySelectorAll("form").length, 1, "the preview saves nothing, so only the tactics are a form");

// nothing typed: nothing asked
submit();
await tick();
assert.ok(!calls.some(([op]) => op === "providers.preview_context"));
assert.match(form().textContent, /Write a draft to preview/);

textarea().value = '"Did you keep the silver compass?"';
submit();
await tick();
const [op, args] = calls.at(-1);
assert.equal(op, "providers.preview_context");
assert.deepEqual(args, { chat: "c1", actor: "q/character/npc", draft: '"Did you keep the silver compass?"' });
const shown = form().textContent;
assert.match(shown, /Estimate: built as if this draft were posted now/);
assert.match(shown, /Nothing was posted, saved or sent/);
assert.match(shown, /your draft shares compass, silver · in context/);
assert.match(shown, /your draft triggers this lore · left out: its lane is full/);
assert.match(shown, /points at something this NPC will not get/);
assert.match(shown, /22 \/ 1500 context tokens with the draft, 10 now/);
assert.ok(shown.includes("the <i>silver</i> compass") && !form().querySelector("i"), "library text is text, never markup");

// an NPC that would not hear the draft: its context stays as saved, and nothing is said to have changed
answer = unheard;
submit();
await tick();
assert.match(form().textContent, /would not hear this draft, so its context stays as saved/);
assert.doesNotMatch(form().textContent, /Likely recalled|Changes from the current context/);

// a preview answered after another NPC was chosen is not drawn, and the draft is kept for the next NPC
answer = heard;
let release = null;
hold = (resolve) => { release = resolve; };
submit();
await tick();
await choose("q/character/b");
release();
await tick();
assert.doesNotMatch(form().textContent, /Likely recalled/);
assert.equal(textarea().value, '"Did you keep the silver compass?"');

// the warning counts what the server shed, not what survived its cut list
await choose("q/character/npc");
answer = cut;
submit();
await tick();
assert.match(form().textContent, /points at 3 things this NPC will not get/);
assert.match(form().textContent, /and 70 more/);

// a tactics save after a preview is still reported and refreshes the page; a refused save says why
const tactics = () => panel.node.querySelector("form");
answer = heard;
submit();
await tick();
tactics().dispatchEvent({ type: "submit", preventDefault() {} });
await tick();
assert.deepEqual(saved, [{ etag: "e2" }], "the saved tactics reach the page");
submit();
await tick();
refuseSave = Object.assign(new Error("the settings changed; reload before saving tactics"), { reason: "changed" });
tactics().dispatchEvent({ type: "submit", preventDefault() {} });
await tick();
assert.match(tactics().textContent, /the settings changed/);
refuseSave = null;

// a preview asked while the context is being read does not leave the read unanswered
let releaseRead = null;
holdRead = (resolve) => { releaseRead = resolve; };
[...panel.node.querySelectorAll("button")].find((item) => item.textContent === "Refresh context").click();
await tick();
submit();
await tick();
releaseRead();
await tick();
assert.doesNotMatch(panel.node.textContent, /Reading current context/);
assert.match(panel.node.textContent, /Selected context and provenance/);

// a refusal is said, with its reason's words
const refusing = contextPanel({ ui, api: async (name, given) => {
  if (name === "providers.preview_context") { const error = new Error("turn_open: the last turn is still open"); error.reason = "turn_open"; throw error; }
  return api(name, given); } }, () => {});
document.body.append(refusing.node);
refusing.changeChat("c1");
await tick();
const other = refusing.node.querySelector("select");
other.value = "q/character/npc";
other.dispatchEvent({ type: "change" });
await tick();
const otherForm = refusing.node.querySelector('div[aria-label="Draft preview"]');
otherForm.querySelector("textarea").value = "Hello";
press(otherForm);
await tick();
assert.match(otherForm.textContent, /could not be previewed: turn_open: the last turn is still open/);

console.log("ok");
