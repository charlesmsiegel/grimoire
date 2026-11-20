// The chat page's hypothetical-reply panel (pages/chat/imagine.js, 073): who can be asked, what holds it back,
// that asking never touches the composer's draft, and that a reply is marked as outside the story, copied or
// discarded, and kept for this tab only. Run by test_app_shell_js.py under fakedom. Every name is made up.
import assert from "node:assert/strict";
import { test } from "node:test";

import { byText, install } from "./fakedom.mjs";
import * as ui from "../../src/grimoire/app/static/shell/ui.js";
import { blocked, makeImagine, MAX_REPLIES, missed, speakersOf, usageText } from "../../src/grimoire/app/static/pages/chat/imagine.js";

const MARA = "actor:mara", TAMSIN = "actor:tamsin";
const scene = (state = {}, cast = null) => ({
  cast: cast || [{ actor: "actor:anya", name: "Anya Rook", role: "player", persona: true },
    { actor: MARA, name: "Mara Vell", role: "npc", persona: false },
    { actor: TAMSIN, name: "Tamsin Hale", role: "npc", persona: false }],
  state: { seated: true, waiting: [], open_turn: null, ...state },
});
const answer = (speaker, name, text, line = "I sank the Gull.") => ({
  canonical: false, saved: false, speaker: { actor: speaker, name }, line: { kind: "speech", text: line },
  replies: text ? [{ speaker: name, actor: speaker, text }] : [], outcome: text ? "complete" : "failed",
  calls: text ? [] : [{ status: "failed", reason: "provider_error" }], usage: { attempts: 1 },
});
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

let serial = 0;
function panel({ chat = `chat:imagine/${serial++}`, has = true, generates = true, reply, mode = () => "auto" } = {}) {
  install("http://localhost");
  const controller = new AbortController();
  const asked = [];
  const composer = ui.field("Your turn", { value: "My draft line", multiline: true });
  const ctx = {
    ui, signal: controller.signal, styles() {},
    has: (op) => has && op === "play.imagine",
    api: async (op, args) => { asked.push({ op, args }); return reply ? reply(args) : answer(args.speaker, "Mara Vell", "Then you owe me a ship."); },
  };
  const made = makeImagine(ctx, chat, { composer, generates: () => generates, mode });
  if (made.node) document.body.append(made.node, composer.node);
  const root = made.node;
  const button = (name) => byText(root, "button", name).at(-1);
  const type = (value) => { const input = root.querySelector('[name="imagine-line"]'); input.value = value; input.dispatchEvent({ type: "input" }); };
  return { made, root, composer, asked, controller, button, type, ctx };
}

test("only the chat's NPCs can be asked, and what holds play up holds this too", () => {
  assert.deepEqual(speakersOf(scene()).map((person) => person.name), ["Mara Vell", "Tamsin Hale"]);
  assert.equal(blocked(scene()), null);
  assert.match(blocked(scene({ seated: false })), /seat/);
  assert.match(blocked(scene({ waiting: ["paused"] })), /held up/);
  assert.match(blocked(scene({ open_turn: "t3" })), /Keep a take or pass/);
  assert.match(blocked(scene({}, [{ actor: "actor:anya", name: "Anya Rook", role: "player" }])), /No one/);
  assert.equal(missed({ calls: [{ status: "rejected", reason: "held_by_boundary" }] }), "No reply came (held_by_boundary).");
  assert.equal(missed({ calls: [] }), "No reply came.");
  assert.equal(usageText({ attempts: 1, input_tokens: 812, output_tokens: 40 }), "1 model call, 812 tokens in and 40 out");
  assert.equal(usageText({ attempts: 2, input_tokens: null, output_tokens: 3 }), "1 model call (2 attempts), tokens not reported");
  assert.equal(usageText({ attempts: 0 }), "");
});

test("no panel where the app does not serve the hypothetical", () => {
  const { made } = panel({ has: false });
  assert.equal(made.node, null);
  made.update(scene());  // harmless
});

test("a reply is asked for the chosen NPC, in the composer's mode, marked as outside the story; the draft is untouched", async () => {
  let mode = "speech";
  const p = panel({ mode: () => mode });
  p.made.update(scene());
  p.type('"I was the one who sank the Gull."');
  const select = p.root.querySelector("select");
  select.value = TAMSIN;
  select.dispatchEvent({ type: "change" });
  p.button("Imagine a reply").click();
  await tick();
  assert.deepEqual(p.asked, [{ op: "play.imagine", args: { chat: p.asked[0].args.chat, speaker: TAMSIN,
    text: '"I was the one who sank the Gull."', mode: "speech" } }]);
  assert.match(p.root.textContent, /Hypothetical · not in the story/);
  assert.match(p.root.textContent, /Mara Vell might say:Then you owe me a ship\./);
  assert.equal(p.composer.input.value, "My draft line");
  assert.match(p.root.textContent, /1 model call/);
  // Ask again reads the line as it was read the first time, whatever the composer's mode is now
  mode = "action";
  p.button("Ask again").click();
  await tick();
  assert.equal(p.asked[1].args.mode, "speech");
  assert.equal(p.asked[1].args.speaker, TAMSIN);
  // Use my draft copies the draft into the panel and leaves the draft as it is
  p.button("Use my draft").click();
  assert.equal(p.root.querySelector('[name="imagine-line"]').value, "My draft line");
  assert.equal(p.composer.input.value, "My draft line");
});

test("copy puts the reply on the clipboard, or says how to copy it by hand", async () => {
  const p = panel();
  p.made.update(scene());
  p.type("I confess.");
  p.button("Imagine a reply").click();
  await tick();
  const copied = [];
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async (text) => { copied.push(text); } } } });
  p.button("Copy reply").click();
  await tick();
  assert.deepEqual(copied, ["Then you owe me a ship."]);
  assert.match(p.root.textContent, /Copied\. The scene is unchanged\./);
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  p.button("Copy reply").click();
  await tick();
  assert.match(p.root.textContent, /select the reply to copy it/);
});

test("nothing is asked while play is held up, an earlier turn is open, or no model is set up", async () => {
  for (const state of [{ open_turn: "t2" }, { waiting: ["paused"] }, { seated: false }]) {
    const p = panel();
    p.made.update(scene(state));
    p.type("I confess.");
    assert.equal(p.button("Imagine a reply").getAttribute("aria-disabled"), "true");
    assert.equal(p.button("Imagine a reply").disabled, false, "play's own controls are the only ones turned off");
    assert.equal(p.root.querySelector("p.chat-imagine-why").hidden, false, "it says why it is off");
    assert.equal(p.root.querySelector("p.chat-imagine-why").textContent, blocked(scene(state)));
    p.button("Imagine a reply").click();
    await tick();
    assert.equal(p.asked.length, 0);
    assert.match(p.root.querySelector("p.chat-imagine-status").textContent, new RegExp(blocked(scene(state)).slice(0, 12)));
    p.made.update(scene());  // what stood in the way is gone
    assert.equal(p.button("Imagine a reply").getAttribute("aria-disabled"), null);
    assert.equal(p.root.querySelector("p.chat-imagine-status").textContent, "", "the old reason goes with the hold");
    assert.equal(p.root.querySelector("p.chat-imagine-why").hidden, true);
  }
  const off = panel({ generates: false });
  off.made.update(scene());
  assert.equal(off.button("Imagine a reply").getAttribute("aria-disabled"), "true");
  assert.match(off.root.textContent, /No model is set up/);
});

test("a blank line asks nothing; a failed or refused reply says why", async () => {
  const p = panel({ reply: (args) => answer(args.speaker, "Mara Vell", null) });
  p.made.update(scene());
  p.type("   ");
  p.button("Imagine a reply").click();
  await tick();
  assert.equal(p.asked.length, 0);
  assert.match(p.root.textContent, /Type what you would say or do\./);
  p.type("I confess.");
  p.button("Imagine a reply").click();
  await tick();
  assert.match(p.root.textContent, /No reply came \(provider_error\)\./);
  assert.equal(byText(p.root, "button", "Copy reply").length, 0);

  const refused = panel({ reply: async () => { throw Object.assign(new Error("would not hear it"), { reason: "not_heard" }); } });
  refused.made.update(scene());
  refused.type("I whisper to Tamsin.");
  refused.button("Imagine a reply").click();
  await tick();
  assert.match(refused.root.textContent, /Nothing was imagined/);
  assert.equal(refused.root.querySelectorAll("li").length, 0);
});

test("ask again asks the same line of the same NPC; discard removes one, and only the newest few are kept", async () => {
  let count = 0;
  const p = panel({ reply: (args) => answer(args.speaker, "Mara Vell", "Variant " + ++count) });
  p.made.update(scene());
  p.type("I confess.");
  p.button("Imagine a reply").click();
  await tick();
  p.button("Ask again").click();
  await tick();
  assert.deepEqual(p.asked.map((item) => item.args.text), ["I confess.", "I confess."]);
  assert.deepEqual(p.asked.map((item) => item.args.speaker), [MARA, MARA]);
  assert.deepEqual(p.root.querySelectorAll("blockquote").map((node) => node.textContent), ["Variant 2", "Variant 1"]);
  byText(p.root, "button", "Discard").filter((node) => node.textContent === "Discard")[0].click();
  assert.deepEqual(p.root.querySelectorAll("blockquote").map((node) => node.textContent), ["Variant 1"]);
  for (let i = 0; i < MAX_REPLIES + 2; i++) { p.button("Imagine a reply").click(); await tick(); }
  assert.equal(p.root.querySelectorAll("li").length, MAX_REPLIES);
  p.button("Discard all").click();
  assert.equal(p.root.querySelectorAll("li").length, 0);
});

test("a chat's replies come back with the chat in this tab and never show in another chat", async () => {
  const first = panel({ chat: "chat:imagine/kept" });
  first.made.update(scene());
  first.type("I confess.");
  first.button("Imagine a reply").click();
  await tick();
  first.controller.abort();
  const other = panel();
  other.made.update(scene());
  assert.equal(other.root.querySelectorAll("li").length, 0);
  const again = panel({ chat: "chat:imagine/kept" });
  again.made.update(scene());
  assert.equal(again.root.querySelectorAll("li").length, 1);
  assert.equal(again.root.querySelector('[name="imagine-line"]').value, "I confess.");
});

test("an earlier reply stays, and says why a new one cannot be asked, once a turn opens", async () => {
  const p = panel();
  p.made.update(scene());
  p.type("I confess.");
  p.button("Imagine a reply").click();
  await tick();
  p.made.update(scene({ open_turn: "t4" }));
  assert.equal(p.root.querySelectorAll("li").length, 1);
  assert.match(p.root.querySelector("p.chat-imagine-why").textContent, /Keep a take or pass/);
  assert.equal(p.button("Ask again").getAttribute("aria-disabled"), "true");
  p.button("Ask again").click();
  await tick();
  assert.equal(p.asked.length, 1, "nothing is asked while the turn is open");
});

test("a reply that arrives after the page was left is dropped, and is not there when the chat comes back", async () => {
  let release;
  const chat = "chat:imagine/left";
  const p = panel({ chat, reply: (args) => new Promise((resolve) => { release = () => resolve(answer(args.speaker, "Mara Vell", "Late.")); }) });
  p.made.update(scene());
  p.type("I confess.");
  p.button("Imagine a reply").click();
  await tick();
  p.controller.abort();
  release();
  await tick();
  assert.equal(p.root.querySelectorAll("li").length, 0);
  const back = panel({ chat });
  back.made.update(scene());
  assert.equal(back.root.querySelectorAll("li").length, 0);
});
