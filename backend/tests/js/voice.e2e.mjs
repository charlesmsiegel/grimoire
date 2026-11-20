// The Voice page (061) end to end, in fakedom against a real app server whose library has a stand-in local
// recognizer: it starts off; "On this device only" shows the disclosure; the microphone switch makes it ready;
// Record opens a stand-in microphone, Stop closes it and the transcript is shown (nothing is posted); a kept
// transcript is listed and deleted; turning the microphone off makes Record go away. The microphone and Web
// Audio are stand-ins: no real audio device is used. Run by test_app_voice.py, which passes GRIMOIRE_APP and
// GRIMOIRE_TOKEN, and GRIMOIRE_VOICE_MISSING=1 for a library whose recognizer is not installed.
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

const base = process.env.GRIMOIRE_APP;
const missing = process.env.GRIMOIRE_VOICE_MISSING === "1";

// A microphone that plays a 300 Hz tone at 48 kHz, and counts what was opened and closed.
// With `microphone.hold` set, the next getUserMedia waits (a permission prompt) until `microphone.release()`.
const microphone = { opened: 0, stopped: 0, contexts: 0, closed: 0, hold: false, release: null };
function installMicrophone() {
  const track = { stop() { microphone.stopped += 1; } };
  // older Node has no navigator global, and fakedom does not make one
  if (!globalThis.navigator) Object.defineProperty(globalThis, "navigator", { configurable: true, writable: true, value: {} });
  Object.defineProperty(globalThis.navigator, "mediaDevices", { configurable: true, value: {
    async getUserMedia(constraints) {
      assert.ok(constraints.audio);
      if (microphone.hold) { microphone.hold = false; await new Promise((resolve) => { microphone.release = resolve; }); }
      microphone.opened += 1;
      return { getTracks: () => [track] };
    },
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

test("voice is chosen on this device only, records into a transcript, and keeps nothing it was not asked to", { skip: !base }, async () => {
  const window = install(base);
  installMicrophone();
  window.location.hash = "#/voice?token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => byText(main, "h2", "Voice input stays on this device").length, 15000, "the Voice page");
  const nav = body.querySelector("nav");
  assert.ok(nav.querySelectorAll("a").some((node) => node.getAttribute("data-route") === "voice"), "Voice is in the rail");
  assert.ok(byText(main, "strong", "Online speech is not available yet.").length, "online services are shown as not offered");
  if (missing) {
    assert.ok(main.textContent.includes("No local speech recognizer is installed"));
    assert.equal(byText(main, "button", "On this device only").length, 0);
    assert.equal(byText(main, "button", "Record").length, 0);
    return;
  }
  const idle = () => until(() => !main.querySelectorAll("button").some((node) => node.disabled), 15000, "the page to settle");
  const off = byText(main, "button", "Off")[0];
  assert.equal(off.getAttribute("aria-pressed"), "true");
  assert.ok(main.textContent.includes("Speech input is off."));
  assert.equal(byText(main, "button", "Record").length, 0, "nothing records while voice is off");

  byText(main, "button", "On this device only")[0].click();
  await until(() => main.querySelector("dl.voice-facts") && main.textContent.includes("The microphone is off"), 15000, "local only, microphone off");
  for (const line of ["this device", "none", "free"]) assert.ok(byText(main, "dd", line).length, line);
  assert.equal(byText(main, "button", "Record").length, 0);
  const mic = main.querySelectorAll("input").find((node) => node.getAttribute("data-focus-key") === "voice:recording");
  mic.checked = true;
  mic.dispatchEvent({ type: "change" });
  await until(() => byText(main, "button", "Record").length, 15000, "ready to record");
  assert.ok(main.textContent.includes("Kept now: nothing."));

  // keep the transcript, then record
  const keep = main.querySelectorAll("input").find((node) => node.getAttribute("data-focus-key") === "voice:keep:provisional_transcript");
  keep.checked = true;
  keep.dispatchEvent({ type: "change" });
  await idle();
  await until(() => byText(main, "button", "Record").length, 15000, "ready again");
  assert.equal(microphone.opened, 0, "the microphone is not opened before Record");
  byText(main, "button", "Record")[0].click();
  await until(() => byText(main, "button", "Stop").length, 15000, "recording");
  assert.equal(microphone.opened, 1);
  await new Promise((resolve) => setTimeout(resolve, 300));
  byText(main, "button", "Stop")[0].click();
  await until(() => byText(main, "p", "is the cargo safe").length, 15000, "the transcript");
  assert.equal(microphone.stopped, 1, "every track stopped");
  assert.equal(microphone.closed, microphone.contexts, "the audio context closed");
  assert.ok(main.textContent.includes("Nothing was posted"));
  await until(() => main.textContent.includes("Kept now: the last transcript."), 15000, "the kept transcript listed");

  byText(main, "button", "Delete voice data now")[0].click();
  await until(() => main.textContent.includes("Kept now: nothing."), 15000, "deleted");

  // Record pressed, then the microphone switched off while the browser is still asking: it never records
  microphone.hold = true;
  byText(main, "button", "Record")[0].click();
  await until(() => microphone.release, 15000, "the permission prompt");
  const micOff = main.querySelectorAll("input").find((node) => node.getAttribute("data-focus-key") === "voice:recording");
  micOff.checked = false;
  micOff.dispatchEvent({ type: "change" });
  await until(() => !byText(main, "button", "Record").length && main.textContent.includes("The microphone is off"), 15000, "microphone off during the prompt");
  microphone.release();
  await until(() => microphone.stopped === 2 && microphone.closed === microphone.contexts, 15000, "the late microphone closed at once");
  assert.equal(byText(main, "button", "Stop").length, 0, "no recording started");
  const micOn = main.querySelectorAll("input").find((node) => node.getAttribute("data-focus-key") === "voice:recording");
  micOn.checked = true;
  micOn.dispatchEvent({ type: "change" });
  await until(() => byText(main, "button", "Record").length, 15000, "ready again after the prompt");

  // the microphone off: Record goes away
  const micAgain = main.querySelectorAll("input").find((node) => node.getAttribute("data-focus-key") === "voice:recording");
  micAgain.checked = false;
  micAgain.dispatchEvent({ type: "change" });
  await until(() => !byText(main, "button", "Record").length && main.textContent.includes("The microphone is off"), 15000, "microphone off");
  assert.equal(microphone.opened, 2, "nothing else opened the microphone");
});
