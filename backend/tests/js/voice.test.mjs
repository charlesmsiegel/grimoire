// 067's voice strip (pages/chat/voice.js) over a fake app: no microphone opens without a gesture, one window is
// sent with the token latched when it opened, results are captions, only instant commands act, words become a
// draft to review, an uncertain safe command waits for Yes or No, Back to typing is instant, and read-aloud plays
// with Pause and Replay.
import { test } from "node:test";
import assert from "node:assert/strict";
import { install, byText } from "./fakedom.mjs";
import * as ui from "../../src/grimoire/app/static/shell/ui.js";
import { encodeWav, makeVoice } from "../../src/grimoire/app/static/pages/chat/voice.js";

const READY = { status: "ready", backend: "fake", input: "audio", locality: "local", network: false,
  retains_inputs: false, billing: "free", fallback: "text" };
const OFF = { ...READY, status: "recording_off", backend: null };
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function settle() { for (let i = 0; i < 6; i += 1) await tick(); }

function status({ stt = READY, tts = READY, pending = null, token = "cap-1" } = {}) {
  return { stt, tts, capture_token: token, pending, triggers: ["keyboard", "pointer", "switch"], max_window: 15 };
}

function strip({ served = true, answers = {}, initial = status(), recordFails = false, hold = false } = {}) {
  install("http://localhost");
  const controller = new AbortController();
  const calls = [];
  const recordings = [];
  const plays = [];
  const answer = { "speech.status": () => initial, "speech.recover": () => status({ token: "cap-2" }), ...answers };
  const ctx = {
    ui, signal: controller.signal, styles() {},
    has: (op) => served && op.startsWith("speech."),
    async api(op, args) { calls.push({ op, args }); const reply = answer[op]; return typeof reply === "function" ? reply(args) : reply; },
  };
  const composer = ui.field("Your turn", { multiline: true });
  const typed = [];
  const dispatch = composer.input.dispatchEvent.bind(composer.input);
  composer.input.dispatchEvent = (event) => dispatch({ type: event.type });
  composer.input.addEventListener("input", () => typed.push(composer.input.value));
  // The fake microphone: a window lasts until the strip finishes it, or ends at once when told to.
  const record = async ({ maxSeconds, until, onOpen }) => {
    const window = { maxSeconds, open: true, onOpen };
    recordings.push(window);
    if (recordFails) throw new Error("denied");
    if (!hold) onOpen();
    await (hold ? until : Promise.race([until, tick()]));
    window.open = false;
    return { samples: new Float32Array(4800).fill(0.1), rate: 48000 };
  };
  const play = (audio, changed, { start = true } = {}) => {
    const player = { audio, paused: !start, started: start, restarts: 0, disposed: false, changed,
      pause(stop) { this.paused = true; this.stopped = stop; }, resume() { this.paused = false; this.started = true; },
      restart() { this.restarts += 1; this.paused = false; }, dispose() { this.disposed = true; } };
    plays.push(player);
    return player;
  };
  const voice = makeVoice(ctx, { composer, record, play });
  if (voice.node) document.body.append(voice.node, composer.node);
  const press = (label) => { const found = byText(voice.node, "button", label); assert.ok(found.length, "no button " + label); found[0].click(); };
  const key = (fields) => {
    const event = { type: "keydown", altKey: false, ctrlKey: false, metaKey: false, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, ...fields };
    for (const listener of document.listeners.get("keydown") || []) listener(event);
    return event;
  };
  const captions = () => voice.node.querySelector("ol").children.map((item) => item.textContent);
  const ops = (name) => calls.filter((call) => call.op === name);
  return { voice, ctx, controller, calls, ops, recordings, plays, composer, typed, press, key, captions };
}

function heard(fields) {
  return { status: "routed", reason: null, caption: null, action: "none", command: null, draft: null, feedback: null,
    pending: null, capture_token: "cap-1", ...fields };
}

test("no voice where the app does not serve it, and nothing listens until a gesture", async () => {
  const hidden = strip({ served: false });
  assert.equal(hidden.voice.node, null);
  const off = strip({ initial: status({ stt: OFF, tts: { ...OFF, status: "off" } }) });
  await settle();
  assert.match(off.voice.node.textContent, /Voice is off\. Typing works as always\./);
  assert.equal(off.voice.node.querySelectorAll("button").length, 0, "no control that cannot work");
  const ready = strip();
  await settle();
  assert.equal(ready.recordings.length, 0, "no microphone before a press");
  assert.deepEqual(ready.calls.map((call) => call.op), ["speech.status"]);
  assert.equal(byText(ready.voice.node, "button", "Listen once")[0].getAttribute("aria-keyshortcuts"), "Alt+V");
  assert.equal(ready.voice.node.querySelector("ol").getAttribute("role"), "log");
});

test("one window: the token latched at the press, an instant command acts, its caption is shown", async () => {
  const s = strip({ answers: { "speech.listen": () => heard({ caption: "pause", action: "act",
    command: { name: "pause", argument: null, gate: "instant" } }) } });
  await settle();
  s.press("Listen once");
  await settle();
  assert.equal(s.recordings.length, 1);
  assert.equal(s.recordings[0].maxSeconds, 15);
  const [sent] = s.ops("speech.listen");
  assert.equal(sent.args.trigger, "pointer");
  assert.equal(sent.args.capture_token, "cap-1");
  assert.equal(sent.args.proposal_token, null);
  assert.ok(atob(sent.args.audio).startsWith("RIFF"));
  assert.deepEqual(s.captions().slice(-1), ["Heard “pause”. Paused reading."]);
  assert.equal(s.ops("post").length + s.ops("play.post").length, 0);
});

test("Alt+V opens a keyboard window and a second Alt+V ends it; words become a draft, never a post", async () => {
  const s = strip({ answers: { "speech.listen": () => heard({ caption: "I open the door", action: "draft", draft: "I open the door" }) } });
  await settle();
  s.composer.input.value = "Earlier words";
  const opened = s.key({ key: "v", code: "KeyV", altKey: true });
  assert.equal(opened.defaultPrevented, true);
  assert.equal(s.recordings.length, 1);
  assert.match(s.voice.node.textContent, /Listening/);
  await settle();
  assert.equal(s.ops("speech.listen")[0].args.trigger, "keyboard");
  assert.equal(s.composer.input.value, "Earlier words\n\nI open the door");
  assert.deepEqual(s.typed, ["Earlier words\n\nI open the door"], "the composer's own input event saved it");
  assert.match(s.captions().at(-1), /Put in your reply to check before you post/);
  // Option+V on a Mac types another character; the key's code still opens a window
  s.key({ key: "√", code: "KeyV", altKey: true });
  await settle();
  assert.equal(s.recordings.length, 2);
});

test("an uncertain safe command waits for Yes or No, by button or by a second spoken window", async () => {
  const pending = { token: "prop-1", command: { name: "navigate", argument: "inventory", gate: "instant" }, text: "open inventory" };
  let listened = 0;
  const s = strip({ answers: {
    "speech.listen": (args) => {
      listened += 1;
      if (listened !== 3) return heard({ caption: "open inventory", action: "review", pending });
      assert.equal(args.proposal_token, "prop-1");
      return heard({ status: "confirmed", caption: "yes", action: "act", command: pending.command });
    },
    "speech.confirm": (args) => ({ action: args.answer === "yes" && args.token === "prop-1" ? "act" : "none",
      command: args.answer === "yes" ? pending.command : null, pending: null, capture_token: "cap-1" }),
  } });
  await settle();
  s.press("Listen once");
  await settle();
  assert.match(s.voice.node.textContent, /Did you mean “open inventory”\?/);
  assert.equal(document.activeElement, byText(s.voice.node, "button", "Yes")[0], "focus stays on the Yes that is shown");
  assert.equal(byText(s.voice.node, "button", "Answer by voice").length, 1);
  s.press("Yes");
  await settle();
  assert.deepEqual(s.ops("speech.confirm")[0].args, { answer: "yes", token: "prop-1" });
  assert.match(s.captions().at(-1), /^Confirmed\. This page has nothing to do for that, so nothing was done\.$/);
  assert.doesNotMatch(s.voice.node.textContent, /Did you mean/);
  // answered by voice: the window carries the proposal's token
  s.press("Listen once");
  await settle();
  s.key({ key: "v", altKey: true });
  await settle();
  assert.equal(s.ops("speech.listen").length, 3);
  assert.match(s.captions().at(-1), /^Heard “yes”\./);
  // No closes it with nothing done
  s.press("Listen once");
  await settle();
  s.press("No");
  await settle();
  assert.match(s.captions().at(-1), /Okay, nothing was done\./);
});

test("Back to typing is instant: the open window is dropped unsent and the composer has focus", async () => {
  const s = strip({ hold: true, answers: { "speech.listen": () => heard({ caption: "stop", action: "act", command: { name: "stop", gate: "instant" } }) } });
  await settle();
  s.key({ key: "v", altKey: true });
  await settle();
  assert.equal(s.recordings[0].open, true, "the window stays open until it is ended");
  assert.match(s.voice.node.textContent, /Opening the microphone… wait for \u201cListening\u201d\./);
  assert.doesNotMatch(s.voice.node.textContent, /speak now/, "no cue to speak before the microphone is taking samples");
  s.recordings[0].onOpen();
  assert.match(s.voice.node.textContent, /Listening… speak now/);
  const escaped = s.key({ key: "Escape" });
  assert.equal(escaped.defaultPrevented, true);
  assert.equal(document.activeElement, s.composer.input);
  await settle();
  assert.equal(s.recordings[0].open, false, "the microphone is released");
  assert.equal(s.ops("speech.listen").length, 0, "the cancelled window is never sent");
  assert.equal(s.ops("speech.recover").length, 1);
  assert.equal(byText(s.voice.node, "button", "Listen once").length, 1);
});

test("blocked, refused and button-only results are only captions", async () => {
  const answers = [
    heard({ status: "blocked", reason: "speech_at_open" }),
    heard({ status: "refused", reason: "recording_off" }),
    heard({ caption: "delete scene", action: "show", command: { name: "delete_scene", argument: null, gate: "confirm" } }),
    heard({ caption: "um the uh", action: "show" }),
  ];
  const s = strip({ answers: { "speech.listen": () => answers.shift() } });
  await settle();
  for (let i = 0; i < 4; i += 1) { s.press("Listen once"); await settle(); }
  assert.deepEqual(s.captions(), [
    "Not used: speech had already started when listening began. Press, wait for \u201cListening\u201d, then speak.",
    "Voice is off. Typing works as always.",
    "Heard “delete scene”. That needs its own button; voice can name it but not do it.",
    "Heard “um the uh”. Not sure what you meant, so nothing was done. Use as draft",
  ]);
  assert.equal(s.composer.input.value, "");
  s.press("Use as draft");
  assert.equal(s.composer.input.value, "um the uh");
  assert.equal(document.activeElement, s.composer.input);
  assert.equal(s.ops("speech.confirm").length, 0);
});

test("a microphone that cannot open leaves typing as it was", async () => {
  const s = strip({ recordFails: true });
  await settle();
  s.press("Listen once");
  await settle();
  assert.deepEqual(s.captions(), ["The microphone couldn't be used. Typing works as always."]);
  assert.equal(s.ops("speech.listen").length, 0);
  assert.equal(byText(s.voice.node, "button", "Listen once").length, 1);
});

test("read aloud plays the latest reply with Pause, Resume and Read again, and says so when it cannot", async () => {
  const s = strip({ answers: { "speech.speak": (args) => ({ status: "ok", caption: args.text, audio: "UklGRg==" }),
    "speech.listen": () => heard({ caption: "stop", action: "act", command: { name: "stop", gate: "instant" } }) } });
  await settle();
  assert.equal(byText(s.voice.node, "button", "Pause reading").length, 0, "playback only once something was read");
  s.press("Read aloud");
  await settle();
  assert.equal(s.captions().at(-1), "Nothing to read yet.");
  assert.equal(s.ops("speech.speak").length, 0);
  s.voice.setLatest("The tide is out.");
  s.press("Read aloud");
  await settle();
  assert.deepEqual(s.ops("speech.speak")[0].args, { text: "The tide is out." });
  assert.equal(s.plays.length, 1);
  assert.equal(s.captions().at(-1), "Reading: The tide is out.");
  s.press("Pause reading");
  assert.equal(s.plays[0].paused, true);
  s.press("Resume reading");
  assert.equal(s.plays[0].paused, false);
  s.press("Read again");
  assert.equal(s.plays[0].restarts, 1);
  s.press("Listen once");
  await settle();
  assert.equal(s.plays[0].stopped, true, "a spoken stop stops reading");
  const off = strip({ answers: { "speech.speak": () => ({ status: "off", caption: "x", audio: null }) } });
  await settle();
  off.voice.setLatest("x");
  off.press("Read aloud");
  await settle();
  assert.equal(off.captions().at(-1), "Read-aloud isn't available just now; the text stays on screen.");
  s.controller.abort();
  assert.equal(s.plays[0].disposed, true);
});

test("the recording is a 16 kHz mono PCM16 WAV", () => {
  const second = new Float32Array(48000).fill(0.5);
  const bytes = Uint8Array.from(atob(encodeWav(second, 48000)), (c) => c.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  assert.equal(String.fromCharCode(...bytes.subarray(0, 4)), "RIFF");
  assert.equal(String.fromCharCode(...bytes.subarray(8, 12)), "WAVE");
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 32000);
  assert.equal(bytes.length, 44 + 32000);
  assert.equal(view.getInt16(44 + 100, true), Math.round(0.5 * 32767));
  assert.throws(() => encodeWav([0.1], 16000));
});

test("reading waits while a window listens, then goes on unless what was said paused it", async () => {
  const replies = [heard({ caption: "I wave", action: "draft", draft: "I wave" }),
    heard({ caption: "pause", action: "act", command: { name: "pause", argument: null, gate: "instant" } })];
  const s = strip({ answers: { "speech.speak": (args) => ({ status: "ok", caption: args.text, audio: "UklGRg==" }),
    "speech.listen": () => replies.shift() } });
  await settle();
  s.voice.setLatest("A long reply.");
  s.press("Read aloud");
  await settle();
  const player = s.plays[0];
  const seen = [];
  const pause = player.pause.bind(player);
  player.pause = (stop) => { seen.push("paused"); pause(stop); };
  s.press("Listen once");
  assert.deepEqual(seen, ["paused"], "the reading voice is not in the window");
  await settle();
  assert.equal(player.paused, false, "reading goes on after a draft");
  s.press("Listen once");
  await settle();
  assert.equal(player.paused, true, "a spoken pause keeps it paused");
});

test("an answer that arrives after Back to typing is dropped unseen", async () => {
  let reply;
  const s = strip({ answers: { "speech.listen": () => new Promise((resolve) => { reply = resolve; }) } });
  await settle();
  s.press("Listen once");
  await settle();
  assert.equal(s.ops("speech.listen").length, 1);
  assert.equal(s.key({ key: "Escape" }).defaultPrevented, true, "Esc works while the window is being heard");
  await settle();
  assert.equal(document.activeElement, s.composer.input);
  reply(heard({ caption: "I open the door", action: "draft", draft: "I open the door" }));
  await settle();
  assert.equal(s.composer.input.value, "", "no draft lands after the person chose typing");
  assert.equal(s.captions().length, 0);
});

test("synthesis that finishes while a window listens waits for the window to close", async () => {
  let spoken;
  const s = strip({ hold: true, answers: { "speech.speak": () => new Promise((resolve) => { spoken = resolve; }),
    "speech.listen": () => heard({ caption: "I wave", action: "draft", draft: "I wave" }) } });
  await settle();
  s.voice.setLatest("A reply.");
  s.press("Read aloud");
  await settle();
  s.key({ key: "v", altKey: true });
  s.recordings[0].onOpen();
  spoken({ status: "ok", caption: "A reply.", audio: "UklGRg==" });
  await settle();
  assert.equal(s.plays[0].started, false, "nothing plays into the open window");
  assert.equal(s.captions().at(-1), "Reading after listening: A reply.");
  s.key({ key: "v", altKey: true });  // ends the window
  await settle();
  assert.equal(s.plays[0].started, true);
  assert.equal(s.plays[0].paused, false);
});

test("one window at a time: a press while the last one is being heard opens nothing", async () => {
  let reply;
  const s = strip({ answers: { "speech.listen": () => new Promise((resolve) => { reply = resolve; }) } });
  await settle();
  s.press("Listen once");
  await settle();
  assert.match(s.voice.node.textContent, /Working out what was said/);
  s.key({ key: "v", altKey: true });
  s.press("Listen once");
  await settle();
  assert.equal(s.recordings.length, 1);
  reply(heard({ caption: "I wave", action: "draft", draft: "I wave" }));
  await settle();
  s.press("Listen once");
  await settle();
  assert.equal(s.recordings.length, 2);
});
