// The Voice page's own logic (pages/voice): the WAV the local recognizer takes, made from microphone samples,
// and the disclosure lines shown for a route. Synthetic samples only.
import assert from "node:assert/strict";
import { test } from "node:test";

import { RATE, base64, canRecord, downsample, join, pcm16, record, wav } from "../../src/grimoire/app/static/pages/voice/audio.js";
import { facts, resultText, statusText } from "../../src/grimoire/app/static/pages/voice/page.js";

test("downsampling averages each output frame and keeps the length in time", () => {
  const input = Float32Array.from({ length: 48000 }, (_, i) => (i % 3 === 0 ? 0.3 : 0.3));
  const out = downsample(input, 48000, RATE);
  assert.equal(out.length, 16000);
  assert.ok(Math.abs(out[100] - 0.3) < 1e-6);
  assert.deepEqual([...downsample(Float32Array.from([1, 0, 1, 0]), 32000, 16000)], [0.5, 0.5]);
  assert.equal(downsample(Float32Array.from([0.1, 0.2]), RATE, RATE).length, 2);
  // a microphone below 16 kHz is interpolated up, keeping the length in time
  assert.deepEqual([...downsample(Float32Array.from([0, 1, 0, 1]), 8000, 16000)], [0, 0.5, 1, 0.5, 0, 0.5, 1, 1]);
  assert.throws(() => downsample(new Float32Array(4), 0, RATE), RangeError);
});

test("samples become clipped 16-bit integers", () => {
  assert.deepEqual([...pcm16(Float32Array.from([0, 1, -1, 2, -2, 0.5]))], [0, 32767, -32768, 32767, -32768, 16384]);
});

test("a WAV is 16-bit mono at 16 kHz with the samples after a 44-byte header", () => {
  const bytes = wav(Int16Array.from([0, 1000, -1000]));
  const view = new DataView(bytes.buffer);
  const text = (at, length) => String.fromCharCode(...bytes.subarray(at, at + length));
  assert.equal(bytes.length, 50);
  assert.equal(text(0, 4), "RIFF");
  assert.equal(text(8, 4), "WAVE");
  assert.equal(view.getUint16(20, true), 1);  // PCM
  assert.equal(view.getUint16(22, true), 1);  // mono
  assert.equal(view.getUint32(24, true), RATE);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 6);
  assert.equal(view.getInt16(46, true), 1000);
  assert.equal(view.getInt16(48, true), -1000);
});

test("base64 round-trips long recordings", () => {
  const bytes = Uint8Array.from({ length: 100000 }, (_, i) => i % 251);
  assert.deepEqual(new Uint8Array(Buffer.from(base64(bytes), "base64")), bytes);
});

test("chunks join in order", () => {
  assert.deepEqual([...join([Float32Array.from([1, 2]), Float32Array.from([3])])], [1, 2, 3]);
});

test("recording needs a microphone API and Web Audio", () => {
  assert.equal(canRecord({}), false);
  assert.equal(canRecord({ navigator: { mediaDevices: { getUserMedia() {} } } }), false);
  assert.equal(canRecord({ navigator: { mediaDevices: { getUserMedia() {} } }, AudioContext: class {} }), true);
});

test("a local route is disclosed as this device, no network, no audio kept, free", () => {
  assert.deepEqual(facts({ locality: "local", network: false, retains_inputs: false, billing: "free" }),
    [["Runs on", "this device"], ["Network", "none"], ["Keeps your audio", "no"], ["Cost", "free"]]);
  assert.deepEqual(facts({ locality: "unknown", network: null, retains_inputs: null, billing: "unknown" }).map((row) => row[1]),
    ["unknown", "unknown", "unknown", "unknown"]);
});

test("every refusal reads as plain words", () => {
  assert.match(statusText("recording_off"), /microphone is off/);
  assert.match(statusText("unavailable"), /not installed/);
  assert.match(resultText("stale_capture"), /dropped/);
  assert.match(resultText("backend_failed"), /type instead/);
  assert.match(statusText("something_new"), /something_new/);
});

// A stand-in microphone scope: `feed(n)` delivers n samples at 48 kHz; it counts opened and closed parts.
function scope({ failProcessor = false } = {}) {
  const counts = { stopped: 0, closed: 0 };
  let node = null;
  const track = { stop() { counts.stopped += 1; } };
  return {
    counts,
    feed(n) { node.onaudioprocess({ inputBuffer: { getChannelData: () => new Float32Array(n).fill(0.25) } }); },
    navigator: { mediaDevices: { async getUserMedia() { return { getTracks: () => [track] }; } } },
    AudioContext: class {
      constructor() { this.sampleRate = 48000; this.destination = {}; }
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createScriptProcessor() {
        if (failProcessor) throw new Error("no processor");
        node = { connect() {}, disconnect() {} };
        return node;
      }
      async close() { counts.closed += 1; }
    },
  };
}

test("the limit closes the microphone by itself, and Stop still gives what was recorded", async () => {
  const place = scope();
  let told = 0;
  const recorder = await record({ maxSeconds: 1, onlimit: () => { told += 1; }, scope: place });
  place.feed(30000);
  place.feed(30000);  // past the 48 000-sample limit
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(told, 1);
  assert.deepEqual(place.counts, { stopped: 1, closed: 1 }, "closed at the limit, before anyone pressed Stop");
  const audio = await recorder.stop();
  assert.equal(audio.length, 44 + RATE * 2, "exactly the limit, at 16 kHz");
  assert.deepEqual(place.counts, { stopped: 1, closed: 1 }, "closed once");
});

test("Stop before any sound gives nothing to send, and a failed setup closes the microphone", async () => {
  const place = scope();
  const recorder = await record({ maxSeconds: 5, scope: place });
  assert.equal(await recorder.stop(), null);
  assert.deepEqual(place.counts, { stopped: 1, closed: 1 });
  const broken = scope({ failProcessor: true });
  await assert.rejects(record({ maxSeconds: 5, scope: broken }), /no processor/);
  assert.deepEqual(broken.counts, { stopped: 1, closed: 1 });
});
