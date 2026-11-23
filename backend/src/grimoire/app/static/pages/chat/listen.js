// 116 (sprint 083's performance): Listen to the scene. The chat's accepted text, read aloud with each speaker in the
// voice cast for them (docs/LISTEN.md, docs/VOICE_CASTING.md). This is not 067's Read aloud (./voice.js), which
// reads one reply in the browser's own voice: this plans the whole scene with voicecast.plan, shows every cue with
// its voice (or "text only" and why) and, on request, the rules its choice looked at; then plays it, a window of
// cues at a time from voicecast.render, asking for the next window while the last cue of this one plays. Every
// window names the plan's fingerprint, so a scene or casting that moved on between two windows is refused
// `changed`, planned again and said so, never played out of step. Audio off (or a browser that cannot play it here)
// reads the scene as text. Nothing here writes a turn, a source or the casting: casting is on the Voices page.
// Everything shown from the library is set as text.

//: Cues one render covers (the server takes 1 to 12): small, so the first voice is heard soon.
export const WINDOW = 4;
//: How long a cue that stays text is shown before the next, while the rest of the scene plays (milliseconds).
export const TEXT_PAUSE = 700;

// Why a cue stays text, in the words the panel shows (the plan's reasons, then the render's).
export const REASONS = {
  not_cast: "nobody cast a voice for this speaker",
  action: "an action: shown, not spoken",
  no_speech: "only stage directions, so nothing is spoken",
  audio_off: "audio is off: read as text",
  too_long: "too long to speak in one go",
  engine_unavailable: "this device has no such voice engine now",
  voice_unavailable: "the engine has no such voice",
  synthesis_failed: "the voice engine failed on this line",
  invalid_audio: "the engine's audio could not be used",
  not_played: "this browser did not play it",
};
const RULES = {
  actor: "this campaign's own choice for the actor",
  revision: "the revision the line was spoken in",
  character_any_revision: "the character at any revision",
  voice_profile: "the voice profile the line was written with",
  voice_profile_any_revision: "that profile's character at any revision",
  narrator: "the narrator of this campaign",
  narrator_every_campaign: "the narrator of every campaign",
};

export function reasonText(reason) { return REASONS[reason] || reason || ""; }
export function ruleText(rule) { return RULES[rule] || rule; }

// Who a `shared` warning names, by the speaker names the plan's cues give (an actor ref, or "narrator").
export function sharedText(plan) {
  const names = new Map();
  for (const cue of plan.cues) names.set(cue.actor || "narrator", cue.speaker);
  return plan.shared.map((item) => item.voice + " is cast for " + item.speakers.map((who) => names.get(who) || who).join(" and ")
    + ": they may sound alike.");
}

// Where play goes on in a new plan after one was refused `changed`: the cue that was next, found by its item, or
// the start. A cue whose text changed is a new cue, so it is found only when the item is still there.
export function resumeAt(plan, item) {
  if (!item) return 0;
  const found = plan.cues.findIndex((cue) => cue.item === item);
  return found < 0 ? 0 : found;
}

// What a plan was made from, as the chat page shows it: each turn's posts, its kept take's lines (ids and texts)
// and whether that take has an outcome. A change to any of these (a line corrected, a take kept again, an
// action's outcome decided) makes a plan stale, whatever the count of lines.
export function sceneKey(scene) {
  return JSON.stringify(scene.turns.map((turn) => [turn.turn, Boolean(turn.set_aside),
    (turn.posts || (turn.post ? [turn.post] : [])).map((post) => [post.actor, post.text, Boolean(post.private)]),
    turn.kept ? [turn.kept.take, turn.kept.lines.map((line) => [line.line, line.text]), turn.kept.outcome != null] : null]));
}

// What the panel says for a refusal of plan or render; `changed` is handled by planning again.
export function refusalText(error) {
  if (error && error.reason === "turn_fork") return "This chat forked: two devices played the same turn. Its branches are not one scene to read aloud until the fork is resolved.";
  if (error && error.reason === "invalid_field") return "The scene cannot be read aloud: " + (error.message || "a request was refused") + ".";
  return null;
}

// A WAV of one silent sample: what the Play press itself starts, muted, so the element may play later.
function silence() {
  const view = new DataView(new ArrayBuffer(45));
  const text = (at, word) => { for (let i = 0; i < word.length; i += 1) view.setUint8(at + i, word.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 37, true); text(8, "WAVE"); text(12, "fmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, 8000, true); view.setUint32(28, 8000, true);
  view.setUint16(32, 1, true); view.setUint16(34, 8, true); text(36, "data"); view.setUint32(40, 1, true); view.setUint8(44, 128);
  return new Uint8Array(view.buffer);
}

// Plays base64 WAV through one <audio> element on blob: URLs (the page's policy allows media from blob: only), or
// null where this browser has no audio element. play(base64) -> { done: Promise<"ended"|"failed"|"stopped">,
// stop() }. One element serves the whole scene, its source swapped per cue: some browsers (iOS Safari) let an
// element play without a tap only once a tap has started it, so play.unlock() is called from the Play press
// itself, before anything is awaited, and starts that element on a muted silent sample.
export function browserPlayer() {
  if (typeof globalThis.Audio !== "function" || typeof globalThis.Blob !== "function"
    || typeof globalThis.URL?.createObjectURL !== "function") return null;
  let media = null, ending = null;
  const element = () => { if (!media) media = new Audio(); return media; };
  const play = (base64) => {
    const audio = element();
    if (ending) ending("stopped");  // one sound at a time
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
    let finish;
    const done = new Promise((resolve) => { finish = resolve; });
    const ended = () => end("ended"), failed = () => end("failed");
    const end = (how) => {
      if (!finish) return;
      const resolve = finish;
      finish = null;
      if (ending === end) ending = null;
      audio.removeEventListener("ended", ended);
      audio.removeEventListener("error", failed);
      try { audio.pause(); } catch { /* already gone */ }
      URL.revokeObjectURL(url);
      resolve(how);
    };
    ending = end;
    audio.addEventListener("ended", ended);
    audio.addEventListener("error", failed);
    audio.muted = false;
    audio.src = url;
    try {
      const started = audio.play();
      if (started && typeof started.catch === "function") started.catch(() => end("failed"));
    } catch { end("failed"); }
    return { done, stop: () => end("stopped") };
  };
  play.unlock = () => {
    if (ending) return;  // already playing: the element was started by a tap before
    const audio = element();
    const url = URL.createObjectURL(new Blob([silence()], { type: "audio/wav" }));
    audio.muted = true;
    audio.src = url;
    try {
      const started = audio.play();
      if (started && typeof started.then === "function") started.then(() => URL.revokeObjectURL(url), () => URL.revokeObjectURL(url));
    } catch { URL.revokeObjectURL(url); }
  };
  return play;
}

// makeListen(ctx, chat, { player, pause }) -> { node, update(scene), stop() }, or { node: null } where the app does
// not serve 083's plan and render. `player` plays one segment (browserPlayer() by default; null: audio cannot be
// played here); `pause` is TEXT_PAUSE.
export function makeListen(ctx, chat, { player = browserPlayer(), pause = TEXT_PAUSE } = {}) {
  if (!ctx.has("voicecast.plan") || !ctx.has("voicecast.render")) return { node: null, update() {}, stop() {} };
  ctx.styles("listen.css");
  const { ui } = ctx;
  const { el } = ui;
  const alive = () => !(ctx.signal && ctx.signal.aborted);
  // plan: voicecast.plan's answer; at: the cue playing or next; heard: what each cue did when it was reached
  const state = { plan: null, planning: false, playing: false, at: 0, current: -1, heard: new Map(), audio: Boolean(player),
    refused: null, turns: null, stale: false, why: new Set() };
  let session = 0, sound = null, drawn = null;
  const rows = new Map();  // each cue's row as last drawn, with what it showed

  const status = el("p", { class: "chat-listen-status", role: "status", "aria-live": "polite", tabindex: "-1" });
  const warnings = el("div", { class: "chat-listen-warnings" });
  const now = el("div", { class: "chat-listen-now", hidden: true });
  const cues = el("ol", { class: "chat-listen-cues", "aria-label": "Cues of the scene" });
  const planButton = ui.button("Plan the scene", () => replan({ say: true }));
  const playButton = ui.button("Play", () => {
    // the element is started within the press itself, before anything is awaited (see browserPlayer)
    if (player && state.audio && typeof player.unlock === "function") player.unlock();
    play();
  }, { kind: "primary" });
  const stopButton = ui.button("Stop", () => { stop("Stopped."); playButton.focus(); });
  // Stopping is never locked: another chat action (ctx.act) disables the page's buttons while it runs, but sound
  // that keeps playing must stay stoppable, so Stop ignores being disabled. It is shown only while playing.
  Object.defineProperty(stopButton, "disabled", { configurable: true, get: () => false, set: () => {} });
  const audioId = ui.id("listen-audio");
  const audioBox = el("input", { type: "checkbox", id: audioId, checked: state.audio || undefined, disabled: !player || undefined,
    onchange: () => { state.audio = audioBox.checked; draw(); } });
  audioBox.checked = state.audio;
  const audioField = el("div", { class: "chat-listen-audio" }, audioBox,
    el("label", { for: audioId }, player ? "Play the voices (off: read as text)" : "This browser cannot play the voices here: the scene reads as text"));
  const castLink = ctx.registered("voices") ? ui.buttonLink("Cast voices", "voices", [], { chat }, { size: "small" }) : null;
  const node = el("details", { class: "chat-listen" }, el("summary", {}, "Listen to the scene"),
    el("div", { class: "chat-listen-body", role: "group", "aria-label": "Listen to the scene" },
      el("p", { class: "g-muted chat-listen-plain" },
        "The kept lines of this chat, each speaker in the voice cast for them on this device. Voices are local: nothing is sent, billed or kept, and nothing here changes the story."),
      audioField,
      el("div", { class: "g-actions chat-listen-actions" }, planButton, playButton, stopButton, castLink),
      status, warnings, now, cues));

  // Focus moves only from inside this panel (or from nowhere): a person typing in the composer keeps their place.
  const ours = () => { const at = document.activeElement; return !at || at === document.body || node.contains(at); };
  function say(text, { focus = false } = {}) {
    status.textContent = text;
    if (focus && alive() && ours()) status.focus();
  }

  function stopSound() { if (sound) { const playing = sound; sound = null; playing.stop(); } }

  // Stop playing (a press, leaving the page, or a refusal); `text` is said when given.
  function stop(text) {
    session += 1;
    stopSound();
    state.playing = false;
    state.current = -1;
    if (text) say(text);
    draw();
  }
  if (ctx.signal) ctx.signal.addEventListener("abort", () => { session += 1; stopSound(); }, { once: true });

  async function replan({ say: announce = false, resume = null, why = "" } = {}) {
    if (state.planning) return;
    state.planning = true;
    draw();
    try {
      const plan = await ctx.api("voicecast.plan", { chat });
      if (!alive()) return;
      state.plan = plan;
      state.refused = null;
      state.stale = false;
      state.heard = new Map();
      state.why = new Set();
      state.at = resumeAt(plan, resume);
      const voiced = plan.cues.filter((cue) => cue.status === "voiced").length;
      const told = why || (plan.cues.length ? plan.cues.length + " cue" + (plan.cues.length === 1 ? "" : "s") + ", " + voiced + " with a voice." : "Nothing has been said in this chat yet.");
      if (announce || why) say(told, { focus: Boolean(why) });
    } catch (error) {
      if (!alive() || error.reason === "left") return;
      state.plan = null;
      state.refused = refusalText(error) || ("The scene could not be planned: " + ui.errorText(error));
      say(state.refused, { focus: true });
    } finally {
      state.planning = false;
      if (alive()) draw();
    }
    if (alive() && announce && state.plan && !state.playing) playButton.focus();
  }

  // One window from `start`, as an answer or an error (never a rejection: a window asked ahead may be dropped).
  function renderWindow(start, fingerprint) {
    return ctx.api("voicecast.render", { chat, start, count: WINDOW, audio: state.audio && Boolean(player), fingerprint })
      .then((answer) => ({ answer }), (error) => ({ error }));
  }

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function play() {
    if (state.playing || state.planning) return;
    if (!state.plan || state.stale) await replan({ resume: state.plan && state.plan.cues[state.at] ? state.plan.cues[state.at].item : null });
    if (!alive() || !state.plan) return;
    if (!state.plan.cues.length) { say("Nothing to play: no line in this scene is cued yet."); return; }
    const mine = ++session;
    const plan = state.plan;
    if (state.at >= plan.cues.length) state.at = 0;
    state.playing = true;
    const audio = state.audio && Boolean(player);
    say(audio ? "Playing." : "Reading as text.");
    draw();
    if (document.activeElement === playButton) stopButton.focus();  // Play is off while it plays
    let pending = renderWindow(state.at, plan.fingerprint);
    try {
      while (pending) {
        const { answer, error } = await pending;
        if (mine !== session || !alive()) return;
        if (error) { await refused(error); return; }
        pending = null;
        const segments = answer.segments;
        for (let index = 0; index < segments.length; index += 1) {
          const segment = segments[index];
          // the next window is asked for while this window's last cue plays
          if (index === segments.length - 1 && answer.next !== null && !pending) pending = renderWindow(answer.next, plan.fingerprint);
          state.current = segment.cue;
          draw();
          let how = segment.status === "audio" ? "ended" : "text";
          if (segment.audio && audio) {
            sound = player(segment.audio);
            how = await sound.done;
            sound = null;
          } else {
            await wait(pause);  // a cue not played is still given time to be read, with audio on or off
          }
          if (mine !== session || !alive()) return;
          state.heard.set(segment.cue, { status: how === "failed" ? "text_only" : segment.status,
            reason: how === "failed" ? "not_played" : segment.reason, voice: segment.voice });
          state.at = segment.cue + 1;
        }
        if (!pending && answer.next !== null) pending = renderWindow(answer.next, plan.fingerprint);  // an empty window
      }
      state.playing = false;
      state.current = -1;
      state.at = 0;
      say(audio ? "Played to the end." : "Read to the end as text: nothing was synthesized.");
    } finally {
      if (mine === session && alive()) {
        state.playing = false;
        const wasOnStop = document.activeElement === stopButton;
        draw();
        if (wasOnStop) playButton.focus();
      }
    }
  }

  // A window was refused: the scene or its casting moved on (`changed`: plan again, and say so), a fork, or another.
  async function refused(error) {
    const next = state.plan && state.plan.cues[state.at] ? state.plan.cues[state.at].item : null;
    stop();
    if (error.reason === "left") return;
    if (error.reason === "turn_fork") state.plan = null;  // a forked chat is no one scene: nothing to play from
    if (error.reason === "changed") {
      await replan({ resume: next,
        why: "The scene or its casting changed while it played, so it was planned again. Press Play to go on." });
      return;
    }
    state.refused = refusalText(error) || ("Playing stopped: " + ui.errorText(error));
    say(state.refused, { focus: true });
    draw();
  }

  function voiceOf(cue) {
    const heard = state.heard.get(cue.index);
    if (heard && heard.status === "text_only") return ui.pill("Text only");
    if (heard && heard.status === "audio") return ui.pill(heard.voice || "Played", { kind: "good" });
    return cue.status === "voiced" ? ui.pill(cue.voice.engine + "/" + cue.voice.voice, { kind: "accent" }) : ui.pill("Text only");
  }

  function cueRow(cue) {
    const heard = state.heard.get(cue.index);
    const reason = heard && heard.reason ? heard.reason : cue.status === "voiced" ? null : cue.reason;
    const open = state.why.has(cue.index);
    const whyId = "listen-why-" + cue.index;
    const toggle = ui.button(open ? "Hide why" : "Why this voice", () => {
      if (state.why.has(cue.index)) state.why.delete(cue.index); else state.why.add(cue.index);
      draw();
      const fresh = rows.get(cue.index);  // the row is drawn again: the focus goes to its own toggle
      if (fresh) fresh.row.querySelector("button").focus();
    }, { size: "small" });
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-controls", whyId);
    toggle.setAttribute("aria-label", (open ? "Hide why " : "Why ") + cue.speaker + " sounds this way");
    const row = el("li", { class: "chat-listen-cue", "data-cue": String(cue.index) },
      el("div", { class: "chat-listen-head" }, el("span", { class: "chat-listen-speaker" }, cue.speaker), voiceOf(cue), toggle),
      el("p", { class: "chat-listen-text g-prose" }, cue.text),
      reason ? el("p", { class: "g-muted chat-listen-reason" }, "Text only: " + reasonText(reason) + ".") : null,
      cue.voice && cue.voice.rights !== "engine"
        ? el("p", { class: "g-muted" }, "A " + cue.voice.rights + " voice" + (cue.voice.note ? ": " + cue.voice.note : "") + ".") : null);
    if (open) {
      row.append(el("div", { id: whyId, class: "chat-listen-why" }, el("p", {}, cue.why),
        el("ul", { "aria-label": "Rules looked at" }, cue.considered.map((item) =>
          el("li", {}, ruleText(item.rule) + " (" + item.names + "): " + (item.voice || "not cast"))))));
    }
    return row;
  }

  function draw() {
    const plan = state.plan;
    planButton.textContent = plan ? "Plan again" : "Plan the scene";
    planButton.disabled = state.planning || state.playing;
    // at rest no button is off: Play is not offered where a refusal left no plan, and Stop only while it plays
    playButton.disabled = state.planning || state.playing;
    playButton.hidden = Boolean(state.refused && !plan);
    playButton.textContent = plan && state.at > 0 && state.at < plan.cues.length ? "Play from " + plan.cues[state.at].speaker : "Play";
    stopButton.hidden = !state.playing;
    audioBox.disabled = !player || state.playing;
    warnings.replaceChildren(...(plan ? [
      ...sharedText(plan).map((text) => ui.notice(text, { kind: "soft" })),
      ...plan.problems.filter((problem) => problem.code.startsWith("casting_")).map((problem) =>
        ui.notice("The casting file cannot be read (" + problem.code.slice("casting_".length) + "): every speaker stays text until it is mended on the Voices page.", { kind: "bad" })),
      ...(plan.problems.some((problem) => problem.code === "outcomes_unreadable")
        ? [ui.notice("The outcomes of decided actions cannot be read now, so they are left out; the rest of the scene still plays.", { kind: "soft" })] : []),
      ...(state.stale && !state.playing ? [ui.notice("The scene has changed since it was planned; Play plans it again.", { kind: "soft" })] : []),
    ] : []));
    const current = plan && state.current >= 0 ? plan.cues[state.current] : null;
    now.hidden = !current;
    now.replaceChildren(...(current ? [el("span", { class: "g-eyebrow" }, "Now reading"),
      el("span", { class: "chat-listen-speaker" }, current.speaker), el("p", { class: "chat-listen-text g-prose" }, current.text)] : []));
    // a row is built again only when what it shows changes, so focus inside the others stays where it is
    if (!plan) { rows.clear(); drawn = null; cues.replaceChildren(); return; }
    if (drawn !== plan) { rows.clear(); drawn = plan; }
    const shown = plan.cues.map((cue) => {
      const heard = state.heard.get(cue.index);
      const key = JSON.stringify([heard ? [heard.status, heard.reason, heard.voice] : null, state.why.has(cue.index)]);
      let held = rows.get(cue.index);
      if (!held || held.key !== key) { held = { key, row: cueRow(cue) }; rows.set(cue.index, held); }
      const reading = state.current === cue.index;
      held.row.classList.toggle("chat-listen-current", reading);
      if (reading) held.row.setAttribute("aria-current", "true"); else held.row.removeAttribute("aria-current");
      return held.row;
    });
    const present = cues.children;
    if (shown.length !== present.length) cues.replaceChildren(...shown);
    else shown.forEach((row, at) => { if (present[at] !== row) present[at].replaceWith(row); });  // only what changed
  }

  draw();
  return {
    node,
    // The chat was drawn again: a plan of another scene is stale (render's fingerprint refuses it anyway).
    update(scene) {
      const turns = sceneKey(scene);
      if (state.turns !== null && turns !== state.turns && state.plan) state.stale = true;
      state.turns = turns;
      draw();
    },
    stop() { stop(); },
  };
}
