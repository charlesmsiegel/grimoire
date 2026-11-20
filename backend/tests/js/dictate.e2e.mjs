// 080's proving scenario through the real chat page in fakedom, against a real app server over a synthetic library
// whose voice has a stand-in local recognizer: dictation is off until voice is on in Settings; then the player
// dictates a line with an invented name, says "no, I meant the west gate" and "post it", fixes the name by typing,
// and presses Post. Until that press the scene has gained nothing; afterwards it has exactly the edited line.
// The microphone and Web Audio are stand-ins. Run by test_app_dictation.py, which passes GRIMOIRE_APP,
// GRIMOIRE_TOKEN and GRIMOIRE_CHAT, and checks afterwards what the library on disk gained.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { byText, install, until } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static") + path.sep).href;
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    if (specifier.startsWith("/static/")) return next(${JSON.stringify(STATIC)} + specifier.slice(8), context);
    return next(specifier, context);
  }`));

const env = process.env;
const base = env.GRIMOIRE_APP, CHAT = env.GRIMOIRE_CHAT;

async function call(op, args) {
  const response = await fetch(base + "/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": env.GRIMOIRE_TOKEN } });
  const answer = await response.json();
  assert.ok(response.ok, op + ": " + JSON.stringify(answer));
  return answer;
}
function type(input, text) { input.value = text; input.dispatchEvent({ type: "input" }); }

// A microphone that plays a 300 Hz tone at 48 kHz, and counts what was opened and closed.
const microphone = { opened: 0, stopped: 0, contexts: 0, closed: 0 };
function installMicrophone() {
  const track = { stop() { microphone.stopped += 1; } };
  if (!globalThis.navigator) Object.defineProperty(globalThis, "navigator", { configurable: true, writable: true, value: {} });
  Object.defineProperty(globalThis.navigator, "mediaDevices", { configurable: true, value: {
    async getUserMedia(constraints) { assert.ok(constraints.audio); microphone.opened += 1; return { getTracks: () => [track] }; },
  } });
  globalThis.AudioContext = class {
    constructor() { microphone.contexts += 1; this.sampleRate = 48000; this.destination = {}; this.at = 0; }
    createMediaStreamSource() { return { connect: () => {}, disconnect: () => {} }; }
    createScriptProcessor(size) {
      const node = { connect: () => { this.timer = setInterval(() => {
        const data = Float32Array.from({ length: size }, (_, i) => 0.3 * Math.sin(2 * Math.PI * 300 * (this.at + i) / 48000));
        this.at += size;
        if (node.onaudioprocess) node.onaudioprocess({ inputBuffer: { getChannelData: () => data } });
      }, 20); }, disconnect: () => clearInterval(this.timer) };
      return node;
    }
    async close() { clearInterval(this.timer); microphone.closed += 1; }
  };
}

test("a dictated draft with a spoken correction is editable, and nothing is posted until Post", { skip: !base }, async () => {
  const window = install(base);
  installMicrophone();
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const main = await until(() => document.body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  const before = await call("open", { chat: CHAT });

  // every launch starts with voice off: dictation says so and offers the settings, and records nothing
  window.location.hash = "#/chat/" + encodeURIComponent(CHAT);
  const strip = () => main.querySelector("section.chat-dictate");
  await until(() => strip() && strip().textContent.includes("Dictation is off: voice input is off."), 15000, "dictation off");
  assert.ok(byText(strip(), "a", "Turn on voice input in Settings").length, "where to turn it on");
  assert.equal(byText(strip(), "button", "Dictate").length, 0, "no control that cannot work");

  // on this device only, microphone on (what the Voice page does), then back to the chat
  await call("voice.configure", { mode: "local_only", backend: "pocketsphinx", recording: true });
  window.location.hash = "#/home";
  await until(() => !strip(), 15000, "left the chat");
  window.location.hash = "#/chat/" + encodeURIComponent(CHAT);
  await until(() => strip() && byText(strip(), "button", "Dictate").length, 15000, "ready to dictate");
  assert.ok(strip().textContent.includes("Nothing is posted until you press Post."));
  assert.ok(strip().textContent.includes("Listens for English (US)."));
  const composer = () => main.querySelector("div.chat-composer").querySelector("textarea");
  const note = () => strip().querySelector("div.chat-dictate-note").textContent;
  assert.equal(microphone.opened, 0, "the microphone is not opened before Dictate");

  async function dictate(expect) {
    const press = byText(strip(), "button", "Dictate")[0];
    assert.ok(!press.disabled, "Dictate is ready");
    press.click();
    await until(() => byText(strip(), "button", "Stop dictating").length, 15000, "recording");
    await new Promise((resolve) => setTimeout(resolve, 200));
    byText(strip(), "button", "Stop dictating")[0].click();
    await until(() => note().includes(expect) && byText(strip(), "button", "Dictate").some((node) => !node.disabled), 15000, expect);
  }

  await dictate("Heard “we meet quill on at the east gate”. Added to your reply");
  assert.equal(composer().value, "We meet quill on at the east gate");
  assert.equal(microphone.opened, 1);
  assert.equal(microphone.stopped, 1, "the microphone closed with Stop");
  assert.equal(microphone.closed, microphone.contexts);

  await dictate("Changed “the east gate” to “the west gate”.");
  assert.equal(composer().value, "We meet quill on at the west gate");
  assert.ok(byText(strip(), "button", "Keep as words").length && byText(strip(), "button", "Undo").length);

  await dictate("Voice doesn't post: press Post when your reply is ready.");
  assert.equal(composer().value, "We meet quill on at the west gate", "post it changed nothing");
  assert.equal((await call("open", { chat: CHAT })).turns.length, before.turns.length, "dictation posted nothing");

  // the invented name is the player's to spell: the draft is theirs to edit
  type(composer(), "We meet Quillon at the west gate");
  const post = byText(main.querySelector("div.chat-composer"), "button", "Post")[0];
  await until(() => !post.disabled, 15000, "Post");
  post.click();
  await until(() => composer().value === "", 30000, "the composer cleared by the post");
  const after = await call("open", { chat: CHAT });
  assert.equal(after.turns.length, before.turns.length + 1);
  assert.equal(after.turns.at(-1).post.text, "We meet Quillon at the west gate");
  assert.equal(microphone.opened, 3, "the microphone opened only for the three presses");
});
