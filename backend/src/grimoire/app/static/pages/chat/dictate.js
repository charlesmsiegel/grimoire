// Speech to draft in a chat (080, docs/SPEECH.md): press Dictate, speak, press Stop dictating, and what was heard
// goes into the composer as unsent text to read and edit. Nothing is posted: Post is still the only way a reply
// leaves the composer, and saying "post it" is answered with a reminder, not a post.
//
// It records through 061's private route (the dictation.* ops over the voice.* session): on this device only,
// chosen in Settings (Voice), with the microphone open only between Dictate and Stop (or Esc, or the recording
// limit, or leaving the page). The capture token and sequence are latched when recording starts, so a recording
// made before a change of settings is dropped. A long post is fine: pauses do not end the recording.
//
// Every result says what was heard, verbatim, and what it did to the draft: added it, made a spoken correction
// ("no, I meant the west gate" changes "the east gate"), or scratched the last passage. Undo puts the draft back;
// the other reading (keep the words as said, or use them as a correction) is one press; a scene name that sounds
// like part of what was heard is offered, never put in by itself. The player's own edits always win: a button
// that would act on text the player has since changed says so and does nothing.

import { base64, canRecord, record as browserRecord } from "../voice/audio.js";

const STATUS_TEXT = {
  off: "Dictation is off: voice input is off.",
  recording_off: "Dictation is off: the microphone is off.",
  unavailable: "Dictation isn't available: the speech recognizer isn't installed on this device.",
  backend_missing: "Dictation isn't available: the chosen recognizer isn't on this device.",
  privacy_refused: "Dictation isn't available: the recognizer doesn't promise to stay on this device.",
  spending_unsupported: "Dictation isn't available: that recognizer costs money.",
  closed: "Dictation has closed.",
};
const RESULT_TEXT = {
  stale_capture: "Not used: the voice settings changed while you were dictating. Dictate again.",
  already_processed: "Not used: that recording was already handled. Dictate again.",
  backend_failed: "The recognizer couldn't hear that recording. Your reply is unchanged.",
  invalid_output: "The recognizer gave an answer that couldn't be used. Your reply is unchanged.",
};
const quoted = (text) => "“" + text + "”";
const MAX_DRAFT = 20000;  // the composer's own limit (grimoire.speech.dictation.MAX_DRAFT)

// `text` with the `find` that starts at `at` replaced by `put`, or null when `find` is not there: a suggestion
// replaces exactly the words it matched, never the same letters elsewhere ("mara bellows").
export function replaceAt(text, at, find, put) {
  if (typeof text !== "string" || !Number.isInteger(at) || at < 0 || !find || text.slice(at, at + find.length) !== find) return null;
  return text.slice(0, at) + put + text.slice(at + find.length);
}

// What a revision did, in a sentence after "Heard “…”."
export function describe(revision) {
  const check = revision.uncertain ? " The recognizer wasn't sure: check it." : "";
  switch (revision.kind) {
    case "added": return "Added to your reply to check before you post." + check;
    case "corrected": return "Changed " + quoted(revision.before) + " to " + quoted(revision.after) + "." + check;
    case "scratched": return "Removed " + quoted(revision.before) + ".";
    case "not_posted": return "Voice doesn't post: press Post when your reply is ready.";
    case "too_long": return "Not added: your reply would be longer than the message box allows.";
    default: return "No words were heard.";
  }
}

// `composer` is the page's ui.field; `names()` answers the scene's names (for suggestions only). `record` and
// `recordable` are the browser's unless a test supplies its own.
export function makeDictation(ctx, { composer, names = () => [], record = browserRecord, recordable = canRecord() } = {}) {
  const { ui } = ctx;
  const { el } = ui;
  if (!ctx.has("dictation.status")) return { node: null };
  ctx.styles("dictate.css");
  const state = { status: null, recorder: null, starting: false, attempt: 0, hearing: false, latched: null,
    passage: null, applied: null, putting: false, disposed: false };
  const statusLine = el("p", { class: "chat-dictate-status", role: "status" }, "Checking dictation…");
  const note = el("div", { class: "chat-dictate-note", role: "log", "aria-live": "polite", "aria-label": "Dictation" });
  const dictate = ui.button("Dictate", () => (state.recorder ? stop() : start()), { title: "Speak a draft; Esc cancels" });
  const cancel = ui.button("Cancel", () => drop(true), { size: "small", title: "Esc" });
  const bar = el("div", { class: "chat-dictate-bar" });
  const settings = el("span", { class: "chat-dictate-settings" });
  const node = el("section", { class: "chat-dictate", "aria-label": "Dictation" }, bar, statusLine, settings, note);

  const ready = () => Boolean(state.status && state.status.status === "ready");
  function draw() {
    if (state.disposed) return;
    const recording = Boolean(state.recorder);
    dictate.textContent = recording ? "Stop dictating" : "Dictate";
    dictate.setAttribute("aria-pressed", String(recording));
    dictate.disabled = state.hearing || state.starting;
    const shown = recording ? [dictate, cancel] : ready() && recordable ? [dictate] : [];
    if (shown.length !== bar.children.length || shown.some((control, at) => bar.children[at] !== control)) bar.replaceChildren(...shown);
    const status = state.status ? state.status.status : null;
    const language = state.status ? " Listens for " + state.status.language.name + "." : "";
    statusLine.textContent = recording ? "Recording: speak, then press Stop dictating. Esc cancels. Up to "
        + state.status.max_seconds + " seconds."
      : state.starting ? "Opening the microphone…"
      : state.hearing ? "Working out what you said…"
      : !state.status ? "Dictation isn't available just now. Typing works as always."
      : status === "ready" && !recordable ? "This browser can't record, so dictation isn't available here."
      : status === "ready" ? "Dictate puts what you say in your reply to check and edit. Nothing is posted until you press Post." + language
      : (STATUS_TEXT[status] || "Dictation isn't available here.") + " Typing works as always.";
    // where voice can be turned on: Settings (Voice), when this app has it and dictation is not ready
    const offerSettings = Boolean(state.status) && status !== "ready" && ctx.has("voice.configure")
      && ["off", "recording_off"].includes(status);
    settings.replaceChildren(...(offerSettings ? [ui.link("Turn on voice input in Settings", "voice")] : []));
  }

  async function refresh({ draw: drawn = true } = {}) {
    try { state.status = await ctx.api("dictation.status", {}); } catch (error) { state.status = null; }
    if (drawn) draw();
  }

  function tell(text, ...controls) {
    note.replaceChildren(el("p", {}, text, ...controls.flatMap((control) => [" ", control])));
  }

  async function start() {
    if (state.starting || state.recorder || state.hearing || !ready() || !recordable) return;
    state.starting = true;
    const mine = ++state.attempt;
    draw();
    try {
      // the sequence and token as they are now (another tab, or the Voice page, may have used the last ones),
      // latched now, never after: a change of settings while recording makes this recording stale
      await refresh({ draw: false });
      if (mine !== state.attempt || state.disposed || !ready()) return;
      state.latched = { token: state.status.capture_token, sequence: state.status.next_sequence };
      const opened = await record({ maxSeconds: state.status.max_seconds, onlimit: () => stop() });
      // cancelled, or the page left, while the browser was asking for the microphone: never record
      if (mine !== state.attempt || state.disposed) { await opened.cancel(); return; }
      state.recorder = opened;
    } catch (error) {
      if (mine === state.attempt && !state.disposed) tell("The microphone couldn't be used. Typing works as always.");
    } finally { state.starting = false; draw(); }
  }

  // Esc or Cancel while recording: the microphone closes and nothing is sent.
  async function drop(said) {
    state.attempt += 1;
    const active = state.recorder;
    state.recorder = null;
    draw();
    if (active) await active.cancel();
    if (said) tell("Cancelled: nothing was sent.");
  }

  async function stop() {
    const active = state.recorder;
    if (!active) return;
    state.recorder = null;
    state.hearing = true;
    draw();
    const mine = state.attempt;
    try {
      const audio = await active.stop();
      if (audio === null) { tell("Nothing was recorded. Press Dictate, speak, then Stop dictating."); return; }
      const sent = composer.value;
      const asked = { draft: sent, last: lastPassage(), names: sceneNames() };
      const heard = await ctx.api("dictation.hear", { ...asked, audio: base64(audio),
        sequence: state.latched.sequence, capture_token: state.latched.token });
      if (state.disposed || mine !== state.attempt) return;
      if (heard.status !== "ok") { tell(RESULT_TEXT[heard.status] || STATUS_TEXT[heard.status] || "Not used."); return; }
      let revision = heard.revision;
      // typed in while it was being heard: the same words, against the reply as it is now, and again if typing
      // goes on meanwhile; a revision is only ever put in over the very text it was made from
      let base = sent;
      for (let tries = 0; composer.value !== base && tries < 3; tries += 1) {
        base = composer.value;
        revision = (await ctx.api("dictation.revise", { draft: base, last: lastPassage(), names: sceneNames(),
          heard: heard.heard, confidence: heard.confidence })).revision;
        if (state.disposed || mine !== state.attempt) return;
      }
      if (composer.value !== base) {
        tell("Heard " + quoted(heard.heard) + ". Not added: you were still typing. Dictate again when you're ready.");
        return;
      }
      show(revision);
    } catch (error) {
      if (!state.disposed) tell("Dictation didn't answer. Your reply is unchanged.");
    } finally {
      // the next sequence and token come with the status: Dictate stays disabled until they are here
      if (!state.disposed) await refresh({ draw: false });
      state.hearing = false;
      draw();
    }
  }

  function sceneNames() {
    const found = [];
    for (const name of names() || []) {
      if (typeof name === "string" && name.trim() && name.length <= 80 && !found.includes(name.trim())) found.push(name.trim());
    }
    return found.slice(0, 64);
  }

  // Put `text` in the composer; its own input event saves it as the chat's draft.
  function write(text) {
    composer.value = text;
    state.putting = true;
    try { composer.input.dispatchEvent(new Event("input")); } finally { state.putting = false; }
  }
  function put(text, passage, previous) {
    write(text);
    state.passage = passage;
    state.applied = { text, previous };
  }
  // The reply as dictation last left it: anything else (typing, Post, another assist) means the last dictated
  // passage and the offers about it no longer apply, so a correction or "scratch that" can never reach typed text.
  const current = () => Boolean(state.applied) && composer.value === state.applied.text;
  const lastPassage = () => (current() ? state.passage : null);
  composer.input.addEventListener("input", () => {
    if (!state.putting) { state.applied = null; state.passage = null; }
  }, { signal: ctx.signal });
  function changed() { tell("Your reply has changed since, so nothing was done: edit it there."); }

  function show(revision) {
    const heardText = "Heard " + quoted(revision.heard) + ". ";
    const previous = { text: composer.value, passage: lastPassage() };
    const changes = ["added", "corrected", "scratched"].includes(revision.kind);
    if (changes) put(revision.text, revision.passage, previous);
    const controls = [];
    if (changes) controls.push(ui.button("Undo", () => undo(), { size: "small" }));
    const other = revision.alternative;
    if (other && ["added", "corrected"].includes(other.kind)) {
      const label = other.kind === "added" ? "Keep as words" : "Replace " + quoted(other.before) + " instead";
      controls.push(ui.button(label, () => {
        // the other reading starts from the same draft this one did
        if (changes ? !current() : composer.value !== previous.text) { changed(); return; }
        show({ ...other, alternative: null, base: previous });
      }, { size: "small" }));
    }
    for (const suggestion of revision.suggestions || []) {
      controls.push(ui.button("Use " + suggestion.name + " for " + quoted(suggestion.heard), () => named(suggestion), { size: "small" }));
    }
    if (revision.base && state.applied) state.applied.previous = revision.base;  // Undo after choosing the other reading goes all the way back
    tell((revision.kind === "nothing" ? "" : heardText) + describe(revision), ...controls);
  }

  function undo() {
    if (!current() || !state.applied.previous) { changed(); return; }
    const { previous } = state.applied;
    write(previous.text);
    state.passage = previous.passage;
    state.applied = { text: previous.text, previous: null };  // what was dictated before is still the last passage
    tell("Undone: your reply is as it was.");
  }

  function named(suggestion) {
    if (!current()) { changed(); return; }
    const text = replaceAt(composer.value, suggestion.at, suggestion.heard, suggestion.name);
    if (text === null) { changed(); return; }
    if (text.length > MAX_DRAFT) { tell("Not changed: your reply would be longer than the message box allows."); return; }
    const passage = replaceAt(state.passage, suggestion.offset, suggestion.heard, suggestion.name);
    put(text, passage === null ? state.passage : passage, { text: composer.value, passage: state.passage });
    tell("Changed " + quoted(suggestion.heard) + " to " + quoted(suggestion.name) + ".",
      ui.button("Undo", () => undo(), { size: "small" }));
  }

  document.addEventListener("keydown", (event) => {
    if (ctx.signal.aborted || event.defaultPrevented) return;
    if (event.key === "Escape" && (state.recorder || state.starting)) { event.preventDefault(); drop(true); }
  }, { signal: ctx.signal });
  ctx.signal.addEventListener("abort", () => {
    state.disposed = true;
    state.attempt += 1;
    if (state.recorder) state.recorder.cancel();  // leaving the page closes the microphone, unsent
    state.recorder = null;
  }, { once: true });

  // Another action on the page (Post, Ask for a reply...) is starting, and it turns the page's buttons off while
  // it runs, Stop dictating and Cancel included: so a recording ends first, unsent, rather than leave the
  // microphone open with no way to close it.
  async function end() {
    if (!state.recorder && !state.starting) return;
    await drop(false);
    tell("Stopped dictating: nothing recorded was used.");
  }

  draw();
  refresh();
  return { node, end };
}
