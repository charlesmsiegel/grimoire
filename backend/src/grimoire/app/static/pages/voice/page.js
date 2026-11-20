// Voice (061): private, local voice input, over the voice adapter's ops (docs/SPEECH.md).
//
// Speech input is off or on this device only: no online speech service is offered, and none is used when the
// local recognizer is missing, slow or fails. The microphone is a separate switch, off by default, and it is
// opened only while Record is pressed. Recordings are never kept; the last transcript may be, only
// for this launch, is listed, and is deleted with one button. The recognizer's cost on this device (load time,
// memory, how long the last recording took) is shown as measured, and the longest recording and the
// recognizer's memory are the person's to limit. Typing and playing are the same whichever is chosen.
//
// Every choice resets with the app: the next launch starts with voice off. Everything from the recognizer is
// set as text only.

import { base64, canRecord, record } from "./audio.js";

const KEEP_LABELS = { provisional_transcript: "The last transcript" };
const STATUS_TEXT = {
  ready: "Ready. Press Record to speak.",
  off: "Speech input is off.",
  recording_off: "The microphone is off, so nothing can be recorded.",
  unavailable: "The speech recognizer is not installed on this device.",
  backend_missing: "The chosen recognizer is not on this device.",
  privacy_refused: "That recognizer does not promise to stay on this device, so it is not used.",
  spending_unsupported: "That recognizer costs money, which voice input does not spend.",
  closed: "Voice input has closed.",
};
const RESULT_TEXT = {
  stale_capture: "The settings changed while you were recording, so that recording was dropped. Record again.",
  already_processed: "That recording was already handled. Record again.",
  nothing_recorded: "Nothing was recorded. Press Record and speak, then Stop.",
  backend_failed: "The recognizer could not hear that recording. Nothing was kept. Try again, or type instead.",
  invalid_output: "The recognizer gave an answer the app could not use. Nothing was kept.",
};
const LIMITS = [5, 10, 15, 30, 60];

const seconds = (value) => value === null || value === undefined ? "not measured yet"
  : value < 1 ? Math.round(value * 1000) + " ms" : value.toFixed(1) + " s";
const mib = (bytes) => bytes === null || bytes === undefined ? "not reported by this system" : (bytes / (1024 * 1024)).toFixed(0) + " MiB";

export function statusText(status) { return STATUS_TEXT[status] || "Not ready (" + status + ")."; }
export function resultText(status) { return RESULT_TEXT[status] || statusText(status); }

// The disclosure lines for the chosen route: where it runs, whether it uses the network, what it keeps, cost.
export function facts(disclosure) {
  return [
    ["Runs on", disclosure.locality === "local" ? "this device" : disclosure.locality === "remote" ? "another computer" : "unknown"],
    ["Network", disclosure.network === false ? "none" : disclosure.network === true ? "yes" : "unknown"],
    ["Keeps your audio", disclosure.retains_inputs === false ? "no" : disclosure.retains_inputs === true ? "yes" : "unknown"],
    ["Cost", disclosure.billing === "free" ? "free" : disclosure.billing === "paid" ? "paid" : "unknown"],
  ];
}

export async function mount(root, ctx) {
  ctx.styles("page.css");
  ctx.setTitle("Voice");
  ctx.layout("page");
  const { ui } = ctx;
  const { el } = ui;
  let status = null;
  let recorder = null;  // { stop, cancel } while recording
  let heard = null;  // the last answer to a recording: { status, text }
  const page = el("div", { class: "g-stack g-loose voice-page" });
  root.replaceChildren(page);
  ctx.signal.addEventListener("abort", () => { if (recorder) { recorder.cancel(); recorder = null; } });

  const load = async () => { status = await ctx.api("voice.status", {}); draw(); };
  const choose = (change) => ctx.act(async () => {
    await drop();
    status = await ctx.api("voice.configure", change);
    heard = null;
    draw();
  });

  const draw = () => {
    const backend = status.backends.find((item) => item.id === "pocketsphinx") || status.backends.find((item) => item.features.includes("stt") && item.id !== "unsupported");
    page.replaceChildren(intro(), inputSection(backend), status.mode === "local_only" ? keptSection() : null,
      backend && backend.available ? costSection(backend) : null, status.stt.status === "ready" ? trySection() : null, notYet());
  };

  const intro = () => ui.card({ kind: "hero", label: "Voice stays on this device" },
    ui.eyebrow("Private voice", { accent: true }),
    el("h2", { class: "g-heading" }, "Voice input stays on this device"),
    el("p", { class: "g-muted" }, "Speech is recognized by a program on this device, or not at all. The app never sends your voice to an online service, and typing works the same either way. These choices reset when the app restarts."));

  const inputSection = (backend) => {
    const card = ui.card({ label: "Speech input" }, el("h2", { class: "g-heading" }, "Speech input"));
    if (!backend || !backend.available) {
      card.append(ui.unavailable("Speech input on this device",
        "No local speech recognizer is installed. Install the optional pocketsphinx package on this device to use it; the app does not switch to an online service instead."));
      return card;
    }
    const mode = ui.segments("Speech input", [{ value: "off", label: "Off" }, { value: "local_only", label: "On this device only" }],
      { value: status.mode, onchange: (value) => choose(value === "off" ? { mode: "off" } : { mode: "local_only", backend: backend.id }) });
    card.append(mode.node);
    if (status.mode === "local_only") {
      card.append(el("dl", { class: "voice-facts" }, facts(status.stt).flatMap(([term, value]) => [el("dt", {}, term), el("dd", {}, value)])));
      const id = ui.id("voice-mic");
      const mic = el("input", { type: "checkbox", id, "data-focus-key": "voice:recording" });
      mic.checked = status.recording;
      mic.addEventListener("change", () => choose({ recording: mic.checked }));
      card.append(el("div", { class: "g-row voice-check" }, mic,
        el("label", { for: id }, "Let the app use the microphone while I press Record")));
    }
    const said = status.mode === "off" ? "off" : status.stt.status;
    card.append(ui.notice(statusText(said), { kind: said === "ready" ? "soft" : "" }));
    return card;
  };

  const keptSection = () => {
    const card = ui.card({ label: "What is kept" }, el("h2", { class: "g-heading" }, "What is kept"),
      el("p", { class: "g-muted" }, "Recordings are never kept: each is forgotten once it is recognized. If you choose to keep the last transcript, it is held in this app's memory until it restarts, and is never written to your library."));
    for (const kind of status.keepable) {
      const id = ui.id("voice-keep");
      const box = el("input", { type: "checkbox", id, "data-focus-key": "voice:keep:" + kind });
      box.checked = status.retain.includes(kind);
      box.addEventListener("change", () => {
        const next = new Set(status.retain);
        if (box.checked) next.add(kind); else next.delete(kind);
        choose({ retain: [...next] });
      });
      card.append(el("div", { class: "g-row voice-check" }, box, el("label", { for: id }, "Keep " + KEEP_LABELS[kind].toLowerCase())));
    }
    card.append(el("p", { class: "voice-kept" }, status.kept.length ? "Kept now: " + status.kept.map((kind) => KEEP_LABELS[kind].toLowerCase()).join(", ") + "." : "Kept now: nothing."),
      ui.actions(ui.button("Delete voice data now", () => ctx.act(async () => {
        await drop();
        status = await ctx.api("voice.delete", {});
        heard = null;
        draw();
        ctx.notify("Voice data deleted. Nothing is kept now.");
      }), { kind: "danger" })));
    return card;
  };

  const costSection = (backend) => {
    const measured = backend.measurements || {};
    const limit = ui.choice("Longest recording", LIMITS.map((value) => ({ value: String(value), label: value + " seconds" })),
      { value: String(backend.max_seconds), onchange: (value) => choose({ backend: backend.id, max_seconds: Number(value) }) });
    const card = ui.card({ label: "This device's cost" }, el("h2", { class: "g-heading" }, "This device's cost"),
      el("p", { class: "g-muted" }, "Measured on this device as it happens, not estimated."),
      el("dl", { class: "voice-facts" },
        el("dt", {}, "Recognizer"), el("dd", {}, measured.loaded ? "loaded" : "not loaded"),
        el("dt", {}, "Loading took"), el("dd", {}, seconds(measured.load_seconds)),
        el("dt", {}, "Memory it added"), el("dd", {}, measured.loaded ? mib(measured.load_memory) : "none now"),
        el("dt", {}, "Last recording"), el("dd", {}, measured.last_audio_seconds ? seconds(measured.last_audio_seconds) + " of speech in " + seconds(measured.last_seconds) : "none yet")),
      status.mode === "local_only" ? limit.node : null);
    if (measured.loaded) {
      card.append(ui.actions(ui.button("Free the recognizer's memory", () => ctx.act(async () => {
        await drop();
        status = await ctx.api("voice.unload", {});
        draw();
      }))));
    }
    return card;
  };

  const trySection = () => {
    const card = ui.card({ label: "Try it" }, el("h2", { class: "g-heading" }, "Try it"));
    if (!canRecord()) {
      card.append(ui.notice("This browser cannot record here. Typing works as always.", {}));
      return card;
    }
    const output = el("div", { class: "voice-output", "aria-live": "polite" });
    if (heard) {
      if (heard.status === "ok") {
        output.append(el("p", { class: "voice-heard" }, heard.text || "(nothing was recognized)"),
          el("p", { class: "g-muted" }, "Recognized on this device. Nothing was posted: copy it into a chat if you want it."));
        if (heard.text && globalThis.navigator && navigator.clipboard) {
          output.append(ui.actions(ui.button("Copy", () => navigator.clipboard.writeText(heard.text).then(() => ctx.notify("Copied.")), { size: "small" })));
        }
      } else output.append(ui.notice(resultText(heard.status), { kind: "bad" }));
    }
    const backend = status.backends.find((item) => item.id === status.selected);
    const button = ui.button(recorder ? "Stop" : "Record", () => (recorder ? finish() : start(backend)), { kind: "primary" });
    button.setAttribute("aria-pressed", String(Boolean(recorder)));
    card.append(el("p", { class: "g-muted" }, "Up to " + backend.max_seconds + " seconds. The microphone is open only while this says Stop."), ui.actions(button), output);
    return card;
  };

  // The capture token and sequence are latched when recording starts: a change of settings in the meantime
  // makes the recording stale, and it is dropped rather than recognized under the new settings.
  let latched = null;
  let starting = false;  // a second press while the microphone is being opened is not a second recorder
  const start = async (backend) => {
    if (starting || recorder) return;
    starting = true;
    try {
      latched = { token: status.capture_token, sequence: status.next_sequence };
      recorder = await record({ maxSeconds: backend.max_seconds, onlimit: () => finish() });
      heard = null;
      draw();
    } catch (error) {
      recorder = null;
      ctx.notify("The microphone could not be opened: " + (error && error.message ? error.message : "permission refused") + ". Typing works as always.", { bad: true });
    } finally { starting = false; }
    // the page was left while the microphone was opening: close it at once
    if (recorder && ctx.signal.aborted) { recorder.cancel(); recorder = null; }
  };
  // Stop: send what was recorded, or nothing when nothing was captured. The page is drawn again whatever
  // happens, so Stop never stays on screen for a microphone that is closed.
  const finish = () => ctx.act(async () => {
    const active = recorder;
    if (!active) return;
    recorder = null;
    try {
      const audio = await active.stop();
      if (audio === null) { heard = { status: "nothing_recorded" }; return; }
      heard = await ctx.api("voice.transcribe", { sequence: latched.sequence, capture_token: latched.token, audio: base64(audio) });
      status = await ctx.api("voice.status", {});
    } finally { draw(); }
  });
  const drop = async () => { if (recorder) { const active = recorder; recorder = null; await active.cancel(); } };

  const notYet = () => el("section", { class: "g-stack", "aria-label": "Not in this app yet" },
    el("h2", { class: "g-heading" }, "Not in this app yet"),
    el("div", { class: "voice-grid" },
      ui.unavailable("Speech output", "No speech synthesizer is offered on this device yet; replies are shown as text."),
      ui.unavailable("Online speech", "No online speech service is offered: voice input runs on this device or not at all."),
      ui.unavailable("Speaking into a chat", "Copy a transcript into a chat's message box; hands-free play is a later step.")));

  try { await load(); }
  catch (error) { if (!(error && error.reason === "left")) page.replaceChildren(ui.errorBox(error)); }
}
