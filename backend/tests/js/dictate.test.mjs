// 080's dictation beside the composer (pages/chat/dictate.js) over a fake app: nothing records until Dictate, the
// capture token and sequence are latched at Dictate, Esc and leaving the page send nothing, what was heard goes into
// the composer as editable text (never posted), Undo / the other reading / a name suggestion act only on the text
// they were offered for, and a draft typed in while a recording was heard is revised as it is now.
import { test } from "node:test";
import assert from "node:assert/strict";
import { install, byText } from "./fakedom.mjs";
import * as ui from "../../src/grimoire/app/static/shell/ui.js";
import { describe, makeDictation, replaceLast } from "../../src/grimoire/app/static/pages/chat/dictate.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function settle() { for (let i = 0; i < 8; i += 1) await tick(); }
const READY = { status: "ready", capture_token: "cap-1", next_sequence: 1, max_seconds: 15,
  language: { tag: "en-US", name: "English (US)", choices: ["en-US"] }, kept: [] };

function revision(fields) {
  return { kind: "added", text: "", passage: null, heard: "", before: null, after: null, uncertain: false,
    suggestions: [], alternative: null, ...fields };
}

function strip({ served = true, status = READY, answers = {}, recordable = true, recordFails = false, nothing = false } = {}) {
  install("http://localhost");
  const controller = new AbortController();
  const calls = [];
  const recordings = [];
  let sequence = status.next_sequence;
  const answer = { "dictation.status": () => ({ ...status, next_sequence: sequence }), ...answers };
  const ctx = {
    ui, signal: controller.signal, styles() {},
    has: (op) => served && (op.startsWith("dictation.") || op === "voice.configure"),
    async api(op, args) {
      calls.push({ op, args });
      if (op === "dictation.hear") sequence += 1;
      const reply = answer[op];
      return typeof reply === "function" ? reply(args) : reply;
    },
  };
  const composer = ui.field("Your turn", { multiline: true });
  const saved = [];
  composer.input.addEventListener("input", () => saved.push(composer.input.value));
  // The fake microphone: open until the strip stops or cancels it.
  const record = async ({ maxSeconds }) => {
    if (recordFails) throw new Error("denied");
    const opened = { maxSeconds, stopped: false, cancelled: false,
      async stop() { this.stopped = true; return nothing ? null : new Uint8Array([82, 73, 70, 70, 1, 2]); },
      async cancel() { this.cancelled = true; } };
    recordings.push(opened);
    return opened;
  };
  const made = makeDictation(ctx, { composer, names: () => ["Mara Vell", null, "Anya Rook"], record, recordable });
  if (made.node) document.body.append(made.node, composer.node);
  const press = (label) => { const found = byText(made.node, "button", label); assert.ok(found.length, "no button " + label); found[0].click(); };
  const key = (fields) => {
    const event = { type: "keydown", defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...fields };
    for (const listener of document.listeners.get("keydown") || []) listener(event);
    return event;
  };
  const ops = (name) => calls.filter((call) => call.op === name);
  const note = () => made.node.querySelector("div.chat-dictate-note").textContent;
  return { made, ctx, controller, calls, ops, recordings, composer, saved, press, key, note };
}

test("no dictation where the app does not serve it; off says why and where to turn it on", async () => {
  assert.equal(strip({ served: false }).made.node, null);
  const off = strip({ status: { ...READY, status: "off", capture_token: null, next_sequence: null } });
  await settle();
  assert.match(off.made.node.textContent, /Dictation is off: voice input is off\. Typing works as always\./);
  assert.equal(off.made.node.querySelectorAll("button").length, 0, "no control that cannot work");
  assert.equal(byText(off.made.node, "a", "Turn on voice input in Settings").length, 1);
  const missing = strip({ status: { ...READY, status: "unavailable", capture_token: null, next_sequence: null } });
  await settle();
  assert.match(missing.made.node.textContent, /speech recognizer isn't installed/);
  assert.equal(missing.made.node.querySelectorAll("a").length, 0, "settings cannot install a recognizer");
  const browser = strip({ recordable: false });
  await settle();
  assert.match(browser.made.node.textContent, /This browser can't record/);
  assert.equal(browser.made.node.querySelectorAll("button").length, 0);
});

test("Dictate records with the token latched at the press, and the words become editable unsent text", async () => {
  const s = strip({ answers: { "dictation.hear": (args) => ({ status: "ok", heard: "we head for the east gate", confidence: null,
    next_sequence: 2, kept: [], revision: revision({ text: args.draft + "We head for the east gate", passage: "We head for the east gate",
      heard: "we head for the east gate" }) }) } });
  await settle();
  assert.equal(s.recordings.length, 0, "nothing records before the press");
  assert.match(s.made.node.textContent, /Nothing is posted until you press Post\. Listens for English \(US\)\./);
  s.press("Dictate");
  await settle();
  assert.equal(s.recordings.length, 1);
  assert.equal(s.recordings[0].maxSeconds, 15);
  assert.equal(byText(s.made.node, "button", "Stop dictating")[0].getAttribute("aria-pressed"), "true");
  s.press("Stop dictating");
  await settle();
  const [hear] = s.ops("dictation.hear");
  assert.deepEqual([hear.args.sequence, hear.args.capture_token, hear.args.draft, hear.args.last], [1, "cap-1", "", null]);
  assert.deepEqual(hear.args.names, ["Mara Vell", "Anya Rook"], "only the scene's names, as text");
  assert.equal(hear.args.audio, "UklGRgEC");
  assert.equal(s.composer.value, "We head for the east gate");
  assert.deepEqual(s.saved, ["We head for the east gate"], "saved as the chat's draft through the composer's own event");
  assert.match(s.note(), /Heard “we head for the east gate”\. Added to your reply to check before you post\./);
  assert.equal(s.ops("post").length + s.ops("play.post").length, 0);
  // the next press latches the next sequence
  s.press("Dictate");
  await settle();
  s.press("Stop dictating");
  await settle();
  assert.equal(s.ops("dictation.hear")[1].args.sequence, 2);
  assert.equal(s.ops("dictation.hear")[1].args.last, "We head for the east gate");
});

test("Esc, Cancel and leaving the page close the microphone and send nothing", async () => {
  const s = strip({ answers: { "dictation.hear": () => { throw new Error("must not be sent"); } } });
  await settle();
  s.press("Dictate");
  await settle();
  assert.ok(s.key({ key: "Escape" }).defaultPrevented);
  await settle();
  assert.ok(s.recordings[0].cancelled && !s.recordings[0].stopped);
  assert.match(s.note(), /Cancelled: nothing was sent\./);
  s.press("Dictate");
  await settle();
  s.press("Cancel");
  await settle();
  assert.ok(s.recordings[1].cancelled);
  assert.ok(!s.key({ key: "Escape" }).defaultPrevented, "Esc is left alone when nothing is recording");
  s.press("Dictate");
  await settle();
  s.controller.abort();
  await settle();
  assert.ok(s.recordings[2].cancelled);
  assert.equal(s.ops("dictation.hear").length, 0);
});

test("a microphone that cannot open, an empty recording and a failing answer leave the reply as it was", async () => {
  const denied = strip({ recordFails: true });
  await settle();
  denied.press("Dictate");
  await settle();
  assert.match(denied.note(), /microphone couldn't be used/);
  const empty = strip({ nothing: true });
  empty.composer.value = "Typed";
  await settle();
  empty.press("Dictate");
  await settle();
  empty.press("Stop dictating");
  await settle();
  assert.match(empty.note(), /Nothing was recorded/);
  assert.equal(empty.ops("dictation.hear").length, 0);
  const stale = strip({ answers: { "dictation.hear": () => ({ status: "stale_capture", revision: null }) } });
  stale.composer.value = "Typed";
  await settle();
  stale.press("Dictate");
  await settle();
  stale.press("Stop dictating");
  await settle();
  assert.match(stale.note(), /voice settings changed/);
  assert.equal(stale.composer.value, "Typed");
  const broken = strip({ answers: { "dictation.hear": () => { throw new Error("down"); } } });
  broken.composer.value = "Typed";
  await settle();
  broken.press("Dictate");
  await settle();
  broken.press("Stop dictating");
  await settle();
  assert.match(broken.note(), /Dictation didn't answer\. Your reply is unchanged\./);
  assert.equal(broken.composer.value, "Typed");
  assert.ok(byText(broken.made.node, "button", "Dictate").length && !byText(broken.made.node, "button", "Dictate")[0].disabled);
});

test("typing while a recording is heard: the same words are revised against the reply as it is now", async () => {
  let release;
  const s = strip({ answers: {
    "dictation.hear": () => new Promise((resolve) => { release = resolve; }),
    "dictation.revise": (args) => ({ revision: revision({ text: args.draft + " We run", passage: "We run", heard: args.heard }) }),
  } });
  await settle();
  s.press("Dictate");
  await settle();
  s.press("Stop dictating");
  await settle();
  assert.ok(byText(s.made.node, "button", "Dictate")[0].disabled, "one recording at a time");
  s.composer.value = "Typed meanwhile.";
  release({ status: "ok", heard: "we run", confidence: 0.4, revision: revision({ text: "We run", passage: "We run", heard: "we run" }) });
  await settle();
  const [asked] = s.ops("dictation.revise");
  assert.deepEqual([asked.args.draft, asked.args.heard, asked.args.confidence], ["Typed meanwhile.", "we run", 0.4]);
  assert.equal(s.composer.value, "Typed meanwhile. We run");
});

test("a correction offers Undo and the words as said; a stale offer does nothing", async () => {
  const corrected = revision({ kind: "corrected", text: "We meet at the west gate", passage: "We meet at the west gate",
    heard: "no i meant the west gate", before: "the east gate", after: "the west gate",
    alternative: revision({ text: "We meet at the east gate no I meant the west gate", passage: "no I meant the west gate",
      heard: "no i meant the west gate" }) });
  const s = strip({ answers: { "dictation.hear": () => ({ status: "ok", heard: corrected.heard, revision: corrected }) } });
  s.composer.value = "We meet at the east gate";
  await settle();
  s.press("Dictate");
  await settle();
  s.press("Stop dictating");
  await settle();
  assert.equal(s.composer.value, "We meet at the west gate");
  assert.match(s.note(), /Changed “the east gate” to “the west gate”\./);
  s.press("Keep as words");
  assert.equal(s.composer.value, "We meet at the east gate no I meant the west gate");
  s.press("Undo");
  assert.equal(s.composer.value, "We meet at the east gate", "Undo goes back to before the recording");
  assert.match(s.note(), /Undone/);
  // dictate again, then edit by hand: the offers no longer apply to this text
  s.press("Dictate");
  await settle();
  s.press("Stop dictating");
  await settle();
  s.composer.value = "My own words";
  s.press("Undo");
  assert.equal(s.composer.value, "My own words");
  assert.match(s.note(), /Your reply has changed since/);
});

test("asking voice to post changes nothing, and the words can still be kept", async () => {
  const asked = revision({ kind: "not_posted", text: "We run", passage: "We run", heard: "post it",
    alternative: revision({ text: "We run post it", passage: "post it", heard: "post it" }) });
  const s = strip({ answers: { "dictation.hear": () => ({ status: "ok", heard: "post it", revision: asked }) } });
  s.composer.value = "We run";
  await settle();
  s.press("Dictate");
  await settle();
  s.press("Stop dictating");
  await settle();
  assert.equal(s.composer.value, "We run");
  assert.match(s.note(), /Voice doesn't post: press Post when your reply is ready\./);
  assert.equal(byText(s.made.node, "button", "Undo").length, 0);
  s.press("Keep as words");
  assert.equal(s.composer.value, "We run post it");
});

test("a scene name is only suggested, and used on a press", async () => {
  const heard = revision({ text: "Tell mara bell", passage: "Tell mara bell", heard: "tell mara bell", uncertain: true,
    suggestions: [{ heard: "mara bell", name: "Mara Vell" }] });
  const s = strip({ answers: { "dictation.hear": () => ({ status: "ok", heard: "tell mara bell", revision: heard }) } });
  await settle();
  s.press("Dictate");
  await settle();
  s.press("Stop dictating");
  await settle();
  assert.equal(s.composer.value, "Tell mara bell", "never put in by itself");
  assert.match(s.note(), /The recognizer wasn't sure: check it\./);
  s.press("Use Mara Vell for “mara bell”");
  assert.equal(s.composer.value, "Tell Mara Vell");
  s.press("Undo");
  assert.equal(s.composer.value, "Tell mara bell");
});

test("helpers: the last occurrence is replaced, and every kind is described", () => {
  assert.equal(replaceLast("a b a", "a", "c"), "a b c");
  assert.equal(replaceLast("a b", "x", "c"), null);
  for (const kind of ["added", "corrected", "scratched", "not_posted", "too_long", "nothing"]) {
    assert.ok(describe(revision({ kind, before: "x", after: "y" })).length > 5, kind);
  }
});
