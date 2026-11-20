// Voice capture for the Voice page (061): the microphone's samples, made into the WAV the local recognizer takes.
//
// The microphone is opened only when the person presses Record, and closed (every track stopped, the audio
// context closed) when they stop, when the length limit is reached or when the page is left. Samples stay in
// this page's memory until they are sent to the app's own loopback server, which is on this device; nothing
// here talks to any other address. The pure helpers (downsample, pcm16, wav, base64) are tested under Node.

export const RATE = 16000;

// Resample from `from` Hz to `to` Hz: averaging each output frame's samples when going down (a simple
// low-pass and decimation), interpolating linearly when going up (a microphone below 16 kHz).
export function downsample(samples, from, to = RATE) {
  if (!(from > 0) || !(to > 0)) throw new RangeError("sample rates must be positive");
  if (from === to) return Float32Array.from(samples);
  const ratio = from / to;
  const length = Math.floor(samples.length / ratio);
  const out = new Float32Array(length);
  if (from < to) {
    for (let i = 0; i < length; i += 1) {
      const at = i * ratio;
      const left = Math.floor(at);
      const right = Math.min(samples.length - 1, left + 1);
      out[i] = samples[left] + (samples[right] - samples[left]) * (at - left);
    }
    return out;
  }
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(samples.length, Math.max(start + 1, Math.floor((i + 1) * ratio)));
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += samples[j];
    out[i] = sum / (end - start);
  }
  return out;
}

// Float samples in [-1, 1] as 16-bit signed integers (clipped).
export function pcm16(samples) {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.max(-1, Math.min(1, samples[i] || 0));
    out[i] = value < 0 ? Math.round(value * 0x8000) : Math.round(value * 0x7fff);
  }
  return out;
}

// 16-bit mono samples as a WAV file's bytes.
export function wav(samples, rate = RATE) {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  const text = (offset, value) => { for (let i = 0; i < value.length; i += 1) bytes[offset + i] = value.charCodeAt(i); };
  text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i += 1) view.setInt16(44 + i * 2, samples[i], true);
  return bytes;
}

export function base64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

// Join recorded chunks into one buffer.
export function join(chunks) {
  const out = new Float32Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
  let at = 0;
  for (const chunk of chunks) { out.set(chunk, at); at += chunk.length; }
  return out;
}

// Whether this browser can record at all (a secure context with a microphone API and Web Audio).
export function canRecord(scope = globalThis) {
  return Boolean(scope.navigator && scope.navigator.mediaDevices && scope.navigator.mediaDevices.getUserMedia
    && (scope.AudioContext || scope.webkitAudioContext));
}

// Start recording. At `maxSeconds` the microphone closes by itself and `onlimit` is told. Answers
// { stop(): Promise<wav bytes or null>, cancel() }: stop gives the recording (null when nothing was captured);
// cancel drops it. Either closes the microphone, if it is still open.
export async function record({ maxSeconds, onlimit, scope = globalThis } = {}) {
  const stream = await scope.navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const Context = scope.AudioContext || scope.webkitAudioContext;
  const chunks = [];
  let frames = 0;
  let open = true;
  let timer = null;
  let context = null;
  let source = null;
  let node = null;
  let closing = null;
  const close = () => {
    if (closing) return closing;
    open = false;
    clearTimeout(timer);
    closing = (async () => {
      try { if (source) source.disconnect(); if (node) node.disconnect(); } catch (error) { /* already disconnected */ }
      for (const track of stream.getTracks()) track.stop();
      try { if (context) await context.close(); } catch (error) { /* already closed */ }
    })();
    return closing;
  };
  // the limit closes the microphone here, whatever the page is doing, then tells the page
  const reached = () => {
    if (!open) return;
    close();
    if (onlimit) { const tell = onlimit; onlimit = null; tell(); }
  };
  try {
    context = new Context();
    source = context.createMediaStreamSource(stream);
    // ScriptProcessor is old but present in every browser and web view; it needs no extra module.
    node = context.createScriptProcessor(4096, 1, 1);
    const limit = Math.floor(maxSeconds * context.sampleRate);
    node.onaudioprocess = (event) => {
      if (!open) return;
      const input = event.inputBuffer.getChannelData(0);
      const room = limit - frames;
      chunks.push(Float32Array.from(room < input.length ? input.subarray(0, Math.max(0, room)) : input));
      frames += Math.max(0, Math.min(room, input.length));
      if (frames >= limit) reached();
    };
    source.connect(node);
    node.connect(context.destination);
    timer = setTimeout(reached, maxSeconds * 1000 + 250);
  } catch (error) {
    await close();
    throw error;
  }
  const rate = context.sampleRate;
  return {
    async stop() {
      await close();
      const samples = downsample(join(chunks), rate, RATE);
      return samples.length ? wav(pcm16(samples)) : null;
    },
    async cancel() { chunks.length = 0; await close(); },
  };
}
