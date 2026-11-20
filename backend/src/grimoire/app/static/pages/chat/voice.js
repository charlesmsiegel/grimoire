// Voice in a chat (067, docs/SPEECH.md): an optional assistive control beside the composer, never a replacement
// for it. There is no always-on microphone: the microphone is asked for only when the person presses Listen once
// (or Alt+V, or a switch mapped to that key), and released when that one bounded window ends: on its own after the
// speech stops, at the window's limit, on a second press, or on Back to typing (Esc). The window's audio goes to
// speech.listen, which decides whether it is attributable before anything recognizes it.
//
// What comes back is only a hypothesis. The strip shows every result as a caption (role=log, read by screen
// readers). It acts only on an instant command (pause, stop, resume, cancel, or what the page itself handles); it
// places other words in the composer as a draft to review, never posting; and a command that needs its own button
// (delete the scene, undo, reroll) is only named. One uncertain safe command waits for Yes or No, by key, pointer
// or a second spoken window. Read aloud plays a line through the configured local voice, with Pause and Read again,
// its text kept on screen as the caption.

const MAX_CAPTIONS = 20;
const TARGET_RATE = 16000;
// While listening, speech is a chunk 9 dB above the quietest chunk so far (never below -60 dBFS), as the server's
// analysis counts it, so quiet speech is not taken for silence; used only to end the window by itself.
const FLOOR_MARGIN = 9;
const ABSOLUTE_FLOOR = -60;
const END_SILENCE = 1.2;  // seconds of quiet after speech that end the window by themselves
const NOTHING_HEARD = 5;  // seconds with no speech that end the window

const STATUS_TEXT = {
  ready: "Voice is ready: press Listen once or Alt+V, then speak. Typing always works.",
  recording_off: "Voice is off. Typing works as always.",
  off: "Voice is off. Typing works as always.",
  unavailable: "Voice isn't available here. Typing works as always.",
  backend_missing: "Voice isn't available here. Typing works as always.",
  privacy_refused: "Voice isn't set up for private, on-device use. Typing works as always.",
  consent_required: "Voice needs your consent before audio leaves this device. Typing works as always.",
  spending_unsupported: "Voice that costs money isn't supported. Typing works as always.",
  closed: "Voice is closed. Typing works as always.",
};
const BLOCKED_TEXT = {
  no_speech: "Didn't hear anyone speak.",
  speech_at_open: "Not used: speech had already started when listening began. Press, wait for \u201cListening\u201d, then speak.",
  late_onset: "Not used: the speech started too long after you pressed Listen.",
  multiple_segments: "Not used: more than one stretch of speech was heard.",
  cut_off: "Not used: listening ended before the speech did.",
  too_long: "Not used: listening ran too long.",
  unreadable_audio: "Not used: the recording couldn't be read.",
  stale_capture: "Not used: voice settings changed while listening.",
  stale_confirmation: "Not used: that question had already closed.",
};
const SPOKEN = { pause: "Paused reading.", stop: "Stopped reading.", resume: "Resumed reading.", cancel: "Cancelled." };

// -- the recording: Float32 samples at the device's rate to a 16 kHz mono PCM16 WAV, as base64 -------------------
export function encodeWav(samples, rate) {
  if (!(samples instanceof Float32Array) || !(rate > 0)) throw new TypeError("encodeWav: Float32 samples and a rate");
  const count = Math.floor(samples.length * TARGET_RATE / rate);
  const bytes = new Uint8Array(44 + count * 2);
  const view = new DataView(bytes.buffer);
  const text = (at, s) => { for (let i = 0; i < s.length; i += 1) bytes[at + i] = s.charCodeAt(i); };
  text(0, "RIFF"); view.setUint32(4, 36 + count * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, TARGET_RATE, true); view.setUint32(28, TARGET_RATE * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i += 1) {
    const at = i * rate / TARGET_RATE;
    const low = Math.floor(at);
    const high = Math.min(low + 1, samples.length - 1);
    const value = samples[low] + (samples[high] - samples[low]) * (at - low);
    view.setInt16(44 + i * 2, Math.max(-32768, Math.min(32767, Math.round(value * 32767))), true);
  }
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function level(chunk) {
  let sum = 0;
  for (const value of chunk) sum += value * value;
  return chunk.length && sum ? 10 * Math.log10(sum / chunk.length) : -Infinity;
}

// The browser's microphone for one window, or null where there is none. `record({ maxSeconds, until, onOpen })`
// asks for the microphone, calls `onOpen` once samples are being taken, collects them until `until` resolves, the
// speech has ended, or the limit; then releases it.
export function browserRecorder() {
  const media = globalThis.navigator && globalThis.navigator.mediaDevices;
  const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!media || typeof media.getUserMedia !== "function" || !Context) return null;
  return async ({ maxSeconds, until, onOpen = () => {} }) => {
    // Made while the press still counts as a gesture: after a slow permission prompt it would start suspended.
    const context = new Context();
    context.resume();
    let stream;
    try { stream = await media.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } }); }
    catch (error) { await context.close(); throw error; }
    const chunks = [];
    let heard = false;
    let quiet = 0;
    let floor = Infinity;
    try {
      const source = context.createMediaStreamSource(stream);
      const node = context.createScriptProcessor(4096, 1, 1);
      await new Promise((resolve) => {
        const limit = setTimeout(resolve, maxSeconds * 1000);
        until.then(resolve);
        node.onaudioprocess = (event) => {
          const chunk = Float32Array.from(event.inputBuffer.getChannelData(0));
          chunks.push(chunk);
          const seconds = chunk.length / context.sampleRate;
          const db = level(chunk);
          floor = Math.min(floor, db);
          if (db >= Math.max(floor + FLOOR_MARGIN, ABSOLUTE_FLOOR)) { heard = true; quiet = 0; } else quiet += seconds;
          if ((heard && quiet >= END_SILENCE) || (!heard && quiet >= NOTHING_HEARD)) { clearTimeout(limit); resolve(); }
        };
        source.connect(node);
        node.connect(context.destination);
        onOpen();
      });
      node.onaudioprocess = null;
      source.disconnect();
      node.disconnect();
    } finally {
      for (const track of stream.getTracks()) track.stop();  // the microphone goes off with the window
      await context.close();
    }
    const samples = new Float32Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
    let at = 0;
    for (const chunk of chunks) { samples.set(chunk, at); at += chunk.length; }
    return { samples, rate: context.sampleRate };
  };
}

// `composer` is the page's ui.field; `onCommand(command)` lets the page act on an instant command it owns and
// answers what it did (a sentence) or null. `record` and `play` are the browser's unless a test supplies its own.
export function makeVoice(ctx, { composer, onCommand = () => null, record = browserRecorder(), play = browserPlayer() } = {}) {
  const { ui } = ctx;
  const { el } = ui;
  if (!ctx.has("speech.status")) return { node: null, readAloud: () => {} };
  ctx.styles("voice.css");
  const state = { status: null, listening: null, pending: null, player: null, lastText: "", disposed: false,
    generation: 0, resumeReading: false, hearing: false, readSeq: 0 };
  const statusLine = el("p", { class: "chat-voice-status", role: "status" }, "Checking voice…");
  const captions = el("ol", { class: "chat-voice-captions", role: "log", "aria-live": "polite", "aria-label": "Voice captions" });
  const question = el("div", { class: "chat-voice-question", role: "group", "aria-label": "Confirm what you said" });
  const listen = ui.button("Listen once", () => (state.listening ? state.listening.finish() : open("pointer")),
    { title: "Alt+V. Listens once, then stops." });
  listen.setAttribute("aria-keyshortcuts", "Alt+V");
  const back = ui.button("Back to typing", () => backToTyping(), { title: "Esc while listening" });
  const read = ui.button("Read aloud", () => readAloud(state.lastText), { size: "small" });
  const pause = ui.button("Pause reading", () => togglePause(), { size: "small" });
  const replay = ui.button("Read again", () => readAgain(), { size: "small" });
  const bar = el("div", { class: "chat-voice-bar" });
  const node = el("section", { class: "chat-voice", "aria-label": "Voice" },
    bar, statusLine, question, captions);

  function say(text) {
    captions.append(el("li", {}, text));
    while (captions.children.length > MAX_CAPTIONS) captions.children[0].remove();
  }
  function ready(feature) { return Boolean(state.status && state.status[feature].status === "ready"); }
  function draw() {
    const stt = state.status ? state.status.stt.status : null;
    // "Listening" only once the microphone is taking samples: speech before that would be cut at its start
    statusLine.textContent = state.listening ? (state.listening.live ? "Listening… speak now, or press Alt+V or Esc to stop."
      : "Opening the microphone… wait for \u201cListening\u201d.")
      : state.hearing ? "Working out what was said… Esc goes back to typing."
      : STATUS_TEXT[stt] || "Voice isn't available here. Typing works as always.";
    listen.textContent = state.listening ? "Stop listening" : (state.pending ? "Answer by voice" : "Listen once");
    // Only what can be used is shown, never a control that cannot work; playback only once something was read.
    const listens = Boolean(state.listening) || (ready("stt") && Boolean(record));
    const shown = [...(listens ? [listen, back] : []), ...(ready("tts") && play ? [read] : []),
      ...(state.player ? [pause, replay] : [])];
    // put back only when the set changes: re-inserting the pressed button would take focus from it
    if (shown.length !== bar.children.length || shown.some((control, at) => bar.children[at] !== control)) bar.replaceChildren(...shown);
    const held = state.listening ? !state.resumeReading : Boolean(state.player && state.player.paused);
    pause.textContent = held ? "Resume reading" : "Pause reading";
    // Built once per question, so a redraw keeps focus on Yes or No
    const asking = state.pending ? state.pending.token : null;
    if ((asking || "") !== (question.getAttribute("data-asking") || "")) {
      question.setAttribute("data-asking", asking || "");
      question.replaceChildren(...(state.pending ? [
        el("p", {}, "Did you mean “" + state.pending.text + "”?"),
        ui.button("Yes", () => answer("yes"), { kind: "primary", size: "small" }),
        ui.button("No", () => answer("no"), { size: "small" })] : []));
    }
  }

  async function refresh() {
    try { state.status = await ctx.api("speech.status", {}); state.pending = state.status.pending; }
    catch (error) { state.status = null; }
    if (!state.disposed) draw();
  }

  // One window: the token is latched when it opens, never after; a pending question's token goes with it.
  async function open(trigger) {
    // one window at a time, through recognition: a second press waits until the first is answered
    if (state.listening || state.hearing || !ready("stt") || !record) return;
    const capture = state.status.capture_token;
    const proposal = state.pending ? state.pending.token : null;
    let finish;
    const until = new Promise((resolve) => { finish = resolve; });
    const opened = { finish, cancelled: false, live: false };
    const generation = state.generation;
    state.listening = opened;
    // Reading aloud would be in the window from its first instant (and so block it): it waits while listening,
    // and goes on afterwards unless what was said paused, stopped or cancelled it.
    state.resumeReading = Boolean(state.player && !state.player.paused);
    if (state.resumeReading) state.player.pause(false);
    draw();
    try { await listenOnce(opened, generation, trigger, capture, proposal, until); }
    finally {
      if (state.resumeReading && state.player && !state.disposed) state.player.resume();
      state.resumeReading = false;
      if (!state.disposed) draw();
    }
  }
  async function listenOnce(opened, generation, trigger, capture, proposal, until) {
    let audio = null;
    try {
      const taken = await record({ maxSeconds: state.status.max_window, until,
        onOpen: () => { opened.live = true; if (!state.disposed) draw(); } });
      if (!opened.cancelled) audio = encodeWav(taken.samples, taken.rate);
    } catch (error) {
      say("The microphone couldn't be used. Typing works as always.");
    } finally {
      state.listening = null;
      draw();
    }
    if (!audio || state.disposed) return;
    let heard;
    state.hearing = true;
    draw();
    try {
      heard = await ctx.api("speech.listen", { audio, trigger, capture_token: capture, proposal_token: proposal });
    } catch (error) { say("Voice didn't answer. Typing works as always."); return; }
    finally { state.hearing = false; }
    // Back to typing while this was being heard: the person chose typing, so the answer is dropped unseen
    if (generation !== state.generation || state.disposed) return;
    state.status.capture_token = heard.capture_token;
    state.pending = heard.pending;
    show(heard);
    draw();
    if (state.pending) question.querySelector("button").focus();
  }

  function show(heard) {
    const caption = heard.caption ? "Heard “" + heard.caption + "”" : null;
    if (heard.status === "blocked") { say(BLOCKED_TEXT[heard.reason] || "Not used."); return; }
    if (heard.status === "refused") { say(STATUS_TEXT[heard.reason] || "Voice couldn't be used just now."); return; }
    if (heard.action === "act") { say((caption ? caption + ". " : "") + perform(heard.command)); return; }
    if (heard.action === "review") { say(caption + ". Waiting for your yes."); return; }
    if (heard.action === "draft") { draftIn(heard.draft); say(caption + ". Put in your reply to check before you post."); return; }
    if (heard.command) { say(caption + ". That needs its own button; voice can name it but not do it."); return; }
    say((caption ? caption + ". " : "") + "Not sure what you meant, so nothing was done.");
    if (heard.caption) offerDraft(heard.caption);
  }

  function perform(command) {
    if (command.name === "pause" || command.name === "stop") {
      state.resumeReading = false;
      state.readSeq += 1;  // a reading still being made does not start after this
      if (state.player) state.player.pause(command.name === "stop");
      draw();
      return SPOKEN[command.name];
    }
    if (command.name === "resume") { state.resumeReading = false; if (state.player) state.player.resume(); draw(); return SPOKEN.resume; }
    if (command.name === "cancel") { backToTyping(); return SPOKEN.cancel; }
    return onCommand(command) || "This page has nothing to do for that, so nothing was done.";
  }

  function draftIn(text) {
    const before = composer.value;
    composer.value = before ? before + "\n\n" + text : text;
    composer.input.dispatchEvent(new Event("input"));  // the composer's own input event saves the draft
  }
  function offerDraft(text) {
    const use = ui.button("Use as draft", () => { draftIn(text); use.remove(); composer.input.focus(); }, { size: "small" });
    captions.children[captions.children.length - 1].append(" ", use);
  }

  async function answer(word) {
    const pending = state.pending;
    if (!pending) return;
    const generation = state.generation;
    state.pending = null;
    draw();
    let result;
    try { result = await ctx.api("speech.confirm", { answer: word, token: pending.token }); }
    catch (error) { say("Voice didn't answer. Typing works as always."); return; }
    if (generation !== state.generation || state.disposed) return;
    state.status.capture_token = result.capture_token;
    say(result.action === "act" ? "Confirmed. " + perform(result.command) : word === "no" ? "Okay, nothing was done." : "That question had closed; nothing was done.");
    draw();
  }

  // Back to typing is instant: the open window is dropped unsent, the question closed, the composer focused.
  async function backToTyping() {
    state.generation += 1;
    state.resumeReading = false;
    state.readSeq += 1;
    if (state.listening) { state.listening.cancelled = true; state.listening.finish(); }
    state.pending = null;
    composer.input.focus();
    draw();
    try { state.status = await ctx.api("speech.recover", {}); state.pending = null; } catch (error) { /* typing needs nothing */ }
    if (!state.disposed) draw();
  }

  async function readAloud(text) {
    if (!ready("tts") || !play) return;
    if (!text) { say("Nothing to read yet."); return; }
    const asked = ++state.readSeq;
    let spoken;
    try { spoken = await ctx.api("speech.speak", { text }); } catch (error) { say("Read-aloud didn't answer."); return; }
    // a newer press (another Read aloud, a pause, a stop, Back to typing) wins over this late answer
    if (asked !== state.readSeq || state.disposed) return;
    if (spoken.status !== "ok" || !spoken.audio) { say("Read-aloud isn't available just now; the text stays on screen."); return; }
    if (state.player) state.player.dispose();
    // never into an open window: synthesis that finishes while listening starts when the window closes
    const waits = Boolean(state.listening);
    state.player = play(spoken.audio, () => { draw(); }, { start: !waits });
    if (waits) state.resumeReading = true;
    say((waits ? "Reading after listening: " : "Reading: ") + spoken.caption);
    draw();
  }
  function togglePause() {
    if (!state.player) return;
    if (state.listening) {  // reading waits for the window; this press says whether it goes on afterwards
      state.resumeReading = !state.resumeReading;
      if (!state.resumeReading) state.readSeq += 1;
      say(state.resumeReading ? "Reading goes on after listening." : "Reading stays paused.");
    } else {
      if (!state.player.paused) state.readSeq += 1;  // pausing also stops a reading still being made
      if (state.player.paused) state.player.resume(); else state.player.pause(false);
      say(state.player.paused ? "Paused reading." : "Resumed reading.");
    }
    draw();
  }
  function readAgain() {
    if (!state.player) return;
    if (state.listening) { state.player.pause(true); state.resumeReading = true; say("Reading again after listening."); }
    else { state.player.restart(); say("Reading again."); }
    draw();
  }

  document.addEventListener("keydown", (event) => {
    if (ctx.signal.aborted || event.defaultPrevented) return;
    // KeyV as well as the key's character: on a Mac, Option+V types a character instead of "v"
    if (event.altKey && !event.ctrlKey && !event.metaKey && (event.code === "KeyV" || event.key === "v" || event.key === "V")) {
      event.preventDefault();
      if (state.listening) state.listening.finish(); else open("keyboard");
    } else if (event.key === "Escape" && (state.listening || state.pending || state.hearing)) {
      event.preventDefault();
      backToTyping();
    }
  }, { signal: ctx.signal });
  ctx.signal.addEventListener("abort", () => {
    state.disposed = true;
    if (state.listening) { state.listening.cancelled = true; state.listening.finish(); }
    if (state.player) state.player.dispose();
  }, { once: true });

  draw();
  refresh();
  return {
    node,
    // The page tells the strip which line Read aloud reads: the latest reply, set as text.
    setLatest(text) { state.lastText = typeof text === "string" ? text : ""; draw(); },
    readAloud,
  };
}

// Plays base64 WAV through an <audio> element on a blob: URL (the page's policy allows media from blob: only).
export function browserPlayer() {
  if (typeof globalThis.Audio !== "function" || typeof globalThis.URL?.createObjectURL !== "function") return null;
  return (base64, changed, { start = true } = {}) => {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
    const audio = new Audio(url);
    audio.addEventListener("ended", changed);
    if (start) audio.play().catch(() => changed());
    return {
      get paused() { return audio.paused; },
      pause(stop) { audio.pause(); if (stop) audio.currentTime = 0; },
      resume() { audio.play().catch(() => changed()); },
      restart() { audio.currentTime = 0; audio.play().catch(() => changed()); },
      dispose() { audio.pause(); URL.revokeObjectURL(url); },
    };
  };
}
