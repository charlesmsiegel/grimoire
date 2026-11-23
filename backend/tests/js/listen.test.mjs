// The chat page's Listen to the scene (pages/chat/listen.js, 116 over 083): what a cue says, where play goes on
// after a re-plan, and the player against a scripted app: windows asked in order with the plan's fingerprint, the
// next asked while the last cue of one plays, `changed` planned again, a fork said plainly, Stop and leaving the
// page stop the sound, a sound the browser did not play stays text, and no audio element reads as text. Run by
// test_app_shell_js.py under fakedom. Every name is made up.
import assert from "node:assert/strict";
import { test } from "node:test";

import { byText, install, until } from "./fakedom.mjs";
import * as ui from "../../src/grimoire/app/static/shell/ui.js";
import { browserPlayer, makeListen, reasonText, refusalText, resumeAt, sceneKey, sharedText, WINDOW } from "../../src/grimoire/app/static/pages/chat/listen.js";

const CHAT = "campaign:c#chat/one";
const cue = (index, speaker, status = "voiced", reason = null) => ({
  index, item: "item-" + index, speaker, actor: speaker === "Narrator" ? null : "campaign:c/character/" + speaker.toLowerCase(),
  text: speaker + " says line " + index + ".", status, reason, why: speaker + " is why.", considered: [{ rule: "actor", names: "campaign:c/character/x", voice: null }],
  voice: status === "voiced" ? { engine: "tone", voice: "kal", rights: "engine", note: null } : null,
});
const planOf = (cues, fingerprint = "sha256:one", shared = []) => ({ chat: CHAT, fingerprint, cues, shared, engines: [], problems: [] });
const scene = (turns = [{ number: 1, kept: { lines: [1] } }]) => ({ turns });

// A scripted app: plan answers `plans` in turn; render answers windows of the current plan unless `refuse` says so.
function app({ plans, refuse = () => null, has = true }) {
  const asked = [];
  let planned = 0;
  const api = async (op, args) => {
    asked.push({ op, args });
    if (op === "voicecast.plan") {
      const next = plans[Math.min(planned, plans.length - 1)];
      planned += 1;
      if (next instanceof Error) throw next;
      return next;
    }
    const plan = plans[Math.min(planned, plans.length) - 1];
    const error = refuse(args);
    if (error) throw error;
    const end = Math.min(args.start + args.count, plan.cues.length);
    const segments = plan.cues.slice(args.start, end).map((item) => ({ cue: item.index, item: item.item, speaker: item.speaker, text: item.text,
      status: item.status === "voiced" && args.audio ? "audio" : "text_only", reason: item.status === "voiced" ? (args.audio ? null : "audio_off") : item.reason,
      voice: item.voice ? "tone/" + item.voice.voice : null, audio: item.status === "voiced" && args.audio ? "UklGRg==" : null, start: 0, duration: 0.5 }));
    return { chat: CHAT, fingerprint: plan.fingerprint, total: plan.cues.length, start: args.start, next: end < plan.cues.length ? end : null, segments, problems: [] };
  };
  return { asked, api, has: (op) => has && op.startsWith("voicecast.") };
}
function refusal(reason) { const error = new Error(reason + " message"); error.reason = reason; error.message = reason + " message"; return error; }

// A player whose sounds the test ends, or fails.
function fakePlayer() {
  const sounds = [];
  const play = (audio) => {
    let resolve;
    const done = new Promise((ok) => { resolve = ok; });
    const sound = { audio, stopped: false, done, end: (how = "ended") => resolve(how), stop() { this.stopped = true; resolve("stopped"); } };
    sounds.push(sound);
    return sound;
  };
  return { sounds, play };
}

function panel({ plans, refuse, player = fakePlayer(), has = true, registered = false } = {}) {
  install("http://localhost");
  const controller = new AbortController();
  const scripted = app({ plans, refuse, has });
  const ctx = { ui, signal: controller.signal, styles() {}, has: scripted.has, registered: () => registered, api: scripted.api };
  const made = makeListen(ctx, CHAT, { player: player && player.play, pause: 0 });
  if (made.node) { document.body.append(made.node); made.node.open = true; }  // as a person opens it
  const button = (name) => byText(made.node, "button", name).find((node) => node.textContent.startsWith(name));
  const status = () => made.node.querySelector("p.chat-listen-status").textContent;
  const current = () => { const row = made.node.querySelector('li[aria-current="true"]'); return row && row.getAttribute("data-cue"); };
  return { made, asked: scripted.asked, controller, button, status, current, player };
}

test("what a cue and a plan say", () => {
  assert.equal(reasonText("not_cast"), "nobody cast a voice for this speaker");
  assert.equal(reasonText("audio_off"), "audio is off: read as text");
  assert.equal(reasonText("something_new"), "something_new");
  const plan = planOf([cue(0, "Mara"), cue(1, "Tamsin"), cue(2, "Narrator")], "sha256:x",
    [{ voice: "tone/kal", speakers: ["campaign:c/character/mara", "narrator"] }]);
  assert.deepEqual(sharedText(plan), ["tone/kal is cast for Mara and Narrator: they may sound alike."]);
  assert.equal(resumeAt(plan, "item-2"), 2);
  assert.equal(resumeAt(plan, "gone"), 0);
  assert.equal(resumeAt(plan, null), 0);
  assert.match(refusalText(refusal("turn_fork")), /forked/);
  assert.equal(refusalText(refusal("changed")), null);
});

test("no panel where the app does not serve the performance", () => {
  const p = panel({ plans: [planOf([])], has: false });
  assert.equal(p.made.node, null);
  p.made.update(scene());
});

test("a scene plays a window at a time, the next asked while the last cue of one plays", async () => {
  const cues = [cue(0, "Mara"), cue(1, "Anya", "text_only", "not_cast"), cue(2, "Tamsin"), cue(3, "Narrator"), cue(4, "Mara"), cue(5, "Tamsin")];
  const p = panel({ plans: [planOf(cues)], registered: true });
  p.made.update(scene());
  assert.ok(p.made.node.querySelectorAll("a").some((node) => node.textContent === "Cast voices"), "casting is a link to the Voices page");
  p.button("Plan the scene").click();
  await until(() => p.made.node.querySelectorAll("li.chat-listen-cue").length === 6, 2000, "the plan");
  assert.match(p.status(), /6 cues, 5 with a voice\./);
  assert.match(p.made.node.querySelectorAll("li.chat-listen-cue")[1].textContent, /Text only: nobody cast a voice for this speaker\./);
  // the rules looked at are shown on request, and hidden again
  p.button("Why this voice").click();
  assert.match(p.made.node.textContent, /Mara is why\./);
  assert.ok(document.activeElement === p.button("Hide why"), "the toggle keeps the focus though its row is drawn again");
  p.button("Hide why").click();
  assert.doesNotMatch(p.made.node.textContent, /Mara is why\./);
  p.button("Play").focus();
  p.button("Play").click();
  await until(() => p.player.sounds.length === 1, 2000, "the first sound");
  assert.equal(p.current(), "0");
  assert.ok(document.activeElement === p.button("Stop"), "Play is off while it plays: the focus goes to Stop");
  // a control in another cue keeps its focus while the cue being read moves on
  const later = p.made.node.querySelectorAll("li.chat-listen-cue")[5].querySelector("button");
  later.focus();
  const renders = () => p.asked.filter((item) => item.op === "voicecast.render").map((item) => item.args);
  assert.deepEqual(renders().map((args) => [args.start, args.count, args.fingerprint, args.audio]), [[0, WINDOW, "sha256:one", true]]);
  p.player.sounds[0].end();
  await until(() => p.player.sounds.length === 2, 2000, "the second sound");  // the text cue between is passed
  assert.equal(p.current(), "2");
  assert.ok(document.activeElement === later);
  p.player.sounds[1].end();
  await until(() => p.player.sounds.length === 3, 2000, "the third sound");
  assert.equal(p.current(), "3");
  assert.deepEqual(renders().map((args) => args.start), [0, WINDOW], "asked while the last cue of the window plays");
  p.player.sounds[2].end();
  await until(() => p.player.sounds.length === 4, 2000);
  p.player.sounds[3].end();
  await until(() => p.player.sounds.length === 5, 2000);
  p.player.sounds[4].end();
  await until(() => p.status() === "Played to the end.", 2000, "the end");
  assert.equal(p.current(), null);
  assert.equal(renders().length, 2);
});

test("a window refused `changed` is planned again, and play goes on from the cue that was next", async () => {
  const first = planOf([cue(0, "Mara"), cue(1, "Tamsin"), cue(2, "Mara"), cue(3, "Tamsin"), cue(4, "Mara")], "sha256:old");
  const second = planOf([cue(0, "Mara"), cue(1, "Tamsin"), cue(2, "Mara"), cue(3, "Tamsin"), cue(4, "Mara")], "sha256:new");
  const p = panel({ plans: [first, second], refuse: (args) => (args.fingerprint === "sha256:old" && args.start > 0 ? refusal("changed") : null) });
  p.made.update(scene());
  p.button("Plan the scene").click();
  await until(() => p.made.node.querySelectorAll("li.chat-listen-cue").length === 5, 2000);
  p.button("Play").click();
  for (let at = 0; at < WINDOW; at += 1) {
    await until(() => p.player.sounds.length === at + 1, 2000);
    p.player.sounds[at].end();
  }
  await until(() => /planned again/.test(p.status()), 2000, "the re-plan");
  assert.equal(p.asked.filter((item) => item.op === "voicecast.plan").length, 2);
  const again = p.button("Play from Mara");
  assert.ok(again && !again.disabled);
  again.click();
  await until(() => p.player.sounds.length === WINDOW + 1, 2000);
  assert.equal(p.current(), String(WINDOW));
  const last = p.asked.filter((item) => item.op === "voicecast.render").at(-1).args;
  assert.deepEqual([last.start, last.fingerprint], [WINDOW, "sha256:new"]);
});

test("a forked chat is said plainly and offers no Play", async () => {
  const p = panel({ plans: [refusal("turn_fork")] });
  p.made.update(scene());
  p.button("Plan the scene").click();
  await until(() => /forked/.test(p.status()), 2000);
  assert.equal(p.button("Play").hidden, true);
});

test("Stop and leaving the page stop the sound, and nothing goes on after", async () => {
  const p = panel({ plans: [planOf([cue(0, "Mara"), cue(1, "Tamsin")])] });
  p.made.update(scene());
  p.button("Play").click();  // planned first, then played
  await until(() => p.player.sounds.length === 1, 2000);
  p.button("Stop").click();
  assert.equal(p.player.sounds[0].stopped, true);
  assert.equal(p.status(), "Stopped.");
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(p.player.sounds.length, 1, "nothing plays after Stop");
  p.button("Play").click();
  await until(() => p.player.sounds.length === 2, 2000);
  p.controller.abort();
  assert.equal(p.player.sounds[1].stopped, true, "leaving the chat stops the voice");
});

test("a sound the browser did not play stays text, with that reason", async () => {
  const p = panel({ plans: [planOf([cue(0, "Mara")])] });
  p.made.update(scene());
  p.button("Play").click();
  await until(() => p.player.sounds.length === 1, 2000);
  p.player.sounds[0].end("failed");
  await until(() => p.status() === "Played to the end.", 2000);
  assert.match(p.made.node.textContent, /Text only: this browser did not play it\./);
});

test("with no audio element the scene reads as text, and a changed scene is planned again before playing", async () => {
  const p = panel({ plans: [planOf([cue(0, "Mara"), cue(1, "Anya", "text_only", "action")]), planOf([cue(0, "Mara")], "sha256:two")], player: null });
  p.made.update(scene());
  assert.equal(p.made.node.querySelector("input").disabled, true);
  assert.match(p.made.node.textContent, /cannot play the voices here/);
  p.button("Play").click();
  await until(() => /Read to the end as text/.test(p.status()), 2000);
  assert.equal(p.asked.find((item) => item.op === "voicecast.render").args.audio, false);
  assert.match(p.made.node.textContent, /Text only: audio is off: read as text\./);
  assert.match(p.made.node.textContent, /Text only: an action: shown, not spoken\./);
  p.made.update(scene([{ number: 1, kept: { lines: [1] } }, { number: 2, kept: { lines: [1] } }]));
  assert.match(p.made.node.textContent, /changed since it was planned/);
  p.button("Play").click();
  await until(() => p.asked.filter((item) => item.op === "voicecast.plan").length === 2, 2000, "a new plan");
  await until(() => /Read to the end as text/.test(p.status()) && p.made.node.querySelectorAll("li.chat-listen-cue").length === 1, 2000);
});

test("a plan is stale when a line's text or an outcome changes, not only the count of lines", () => {
  const turn = (text, outcome = null) => ({ turn: "t-1", number: 1, posts: [{ actor: "a", text: "Hello?", private: false }],
    kept: { take: "k-1", lines: [{ line: "l-1", text }], outcome } });
  assert.equal(sceneKey({ turns: [turn("Twelve coins short.")] }), sceneKey({ turns: [turn("Twelve coins short.")] }));
  assert.notEqual(sceneKey({ turns: [turn("Twelve coins short.")] }), sceneKey({ turns: [turn("Eleven coins short.")] }), "a corrected line");
  assert.notEqual(sceneKey({ turns: [turn("Twelve coins short.")] }), sceneKey({ turns: [turn("Twelve coins short.", { decided: "yes" })] }), "an outcome decided");
  const p = panel({ plans: [planOf([cue(0, "Mara")])] });
  p.made.update({ turns: [turn("Twelve coins short.")] });
  p.button("Plan the scene").click();
  return until(() => p.made.node.querySelectorAll("li.chat-listen-cue").length === 1, 2000).then(() => {
    p.made.update({ turns: [turn("Eleven coins short.")] });
    assert.match(p.made.node.textContent, /changed since it was planned/);
  });
});

test("a fork found while playing leaves no plan to play from", async () => {
  const p = panel({ plans: [planOf([cue(0, "Mara"), cue(1, "Tamsin"), cue(2, "Mara"), cue(3, "Tamsin"), cue(4, "Mara")])],
    refuse: (args) => (args.start > 0 ? refusal("turn_fork") : null) });
  p.made.update(scene());
  p.button("Play").click();
  for (let index = 0; index < WINDOW; index += 1) {  // the first window plays; the next is refused
    await until(() => p.player.sounds.length === index + 1, 2000, "sound " + index);
    p.player.sounds[index].end();
  }
  await until(() => /forked/.test(p.status()), 2000, "the fork");
  assert.equal(p.button("Play").hidden, true, "no Play is offered for a forked chat");
  assert.equal(p.made.node.querySelectorAll("li.chat-listen-cue").length, 0);
  assert.equal(p.button("Stop").hidden, true);
});

test("the browser's player keeps one audio element, started from the Play press", async () => {
  install("http://localhost");
  const elements = [], played = [];
  globalThis.Audio = class {
    constructor() { this.listeners = {}; this.muted = false; this.src = ""; elements.push(this); }
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    removeEventListener(type, listener) { this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== listener); }
    play() { played.push({ element: this, src: this.src, muted: this.muted }); return Promise.resolve(); }
    pause() {}
    fire(type) { for (const listener of [...(this.listeners[type] || [])]) listener({ type }); }
  };
  try {
    const play = browserPlayer();
    play.unlock();
    assert.equal(played.length, 1);
    assert.equal(played[0].muted, true, "the press starts the element on a muted silent sample");
    const first = play("UklGRg==");
    assert.equal(played.at(-1).muted, false);
    elements[0].fire("ended");
    assert.equal(await first.done, "ended");
    const second = play("UklGRg==");
    second.stop();
    assert.equal(await second.done, "stopped");
    assert.equal(elements.length, 1, "one element for every cue");
    assert.equal(new Set(played.map((item) => item.src)).size, 3, "a new source each time");
  } finally {
    delete globalThis.Audio;
  }
});

test("text cues are given time to be read with audio off, and Stop is never locked by another action", async () => {
  install("http://localhost");
  const scripted = app({ plans: [planOf([cue(0, "Mara"), cue(1, "Tamsin"), cue(2, "Narrator")])] });
  const ctx = { ui, signal: new AbortController().signal, styles() {}, has: scripted.has, registered: () => false, api: scripted.api };
  const player = fakePlayer();
  const made = makeListen(ctx, CHAT, { player: player.play, pause: 60 });
  document.body.append(made.node);
  made.node.open = true;
  made.update(scene());
  const button = (name) => byText(made.node, "button", name).find((node) => node.textContent.startsWith(name));
  button("Plan the scene").click();
  await until(() => made.node.querySelectorAll("li.chat-listen-cue").length === 3, 2000, "the plan");
  const box = made.node.querySelector('input[type="checkbox"]');
  box.checked = false;
  box.dispatchEvent({ type: "change" });
  const started = Date.now();
  button("Play").click();
  await until(() => made.node.querySelector('li[aria-current="true"]'), 2000, "the first cue read");
  // another chat action disables the page's buttons while it runs (shell ctx.act): Stop must stay usable
  for (const node of made.node.querySelectorAll("button")) node.disabled = true;
  assert.equal(button("Stop").disabled, false, "Stop ignores the page-wide lock");
  await until(() => /Read to the end as text/.test(made.node.querySelector("p.chat-listen-status").textContent), 3000, "read to the end");
  assert.ok(Date.now() - started >= 150, "each text cue is paused on, so it can be followed");
  assert.equal(player.sounds.length, 0, "nothing was played with audio off");
});
