// Sprint 116 group D1 end to end, in fakedom against a real app server: Mara Vell is cast through the Voices page's
// review; the chat's scene is planned with each voice and why (the uncast persona stays text), played a window at a
// time (the next asked for while the last cue of this one plays), refused `changed` when the casting moves on
// mid-play and planned again, and read as text with audio off. Then a draft naming a lore keyword is previewed for
// Mara: the lore and a recalled memory appear, the composer still holds the draft and nothing was posted. Run by
// test_app_listen.py, which serves the card chat (narrator, Mara, Tamsin, the persona) with the narrator and Tamsin
// already cast to a tone engine, and passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT, GRIMOIRE_TAMSIN and
// GRIMOIRE_MARA. The browser's <audio> is a stand-in whose playing the test ends: the sequence is what is checked.
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
const base = env.GRIMOIRE_APP, CHAT = env.GRIMOIRE_CHAT, TAMSIN = env.GRIMOIRE_TAMSIN, MARA = env.GRIMOIRE_MARA;
const DRAFT = '"Mara, I kept my word about the silver compass. Is the lighthouse still dark?"';
const LIGHTHOUSE = "The lighthouse has been dark for a year.";
const PROMISE = "Anya promised to bring back the silver compass before the spring tide.";

async function call(op, args) {
  const response = await fetch(base + "/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": env.GRIMOIRE_TOKEN } });
  const answer = await response.json();
  assert.ok(response.ok, op + ": " + JSON.stringify(answer));
  return answer;
}
function type(input, text) { input.value = text; input.dispatchEvent({ type: "input" }); }
function field(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}
function choose(select, value) { select.value = value; select.dispatchEvent({ type: "change" }); }
const group = (root, label) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === label);
const section = (root, label) => root.querySelector('section[aria-label="' + label + '"]') || undefined;
const button = (root, label) => byText(root, "button", label).find((node) => node.textContent === label);

// The browser's audio element, as a stand-in: each one plays until the test ends it.
const sounds = [];
// An <audio> element: each sound is one unmuted play of a source (the Play press's own muted start is not one);
// finishing a sound ends it only while its source is still the element's.
const elements = [];
class FakeAudio {
  constructor() { this.listeners = {}; this.src = ""; this.muted = false; elements.push(this); }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  removeEventListener(type, listener) { this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== listener); }
  play() {
    if (!this.muted) {
      const src = this.src;
      sounds.push({ src, finish: () => { if (this.src === src) for (const listener of [...(this.listeners.ended || [])]) listener({ type: "ended" }); } });
    }
    return Promise.resolve();
  }
  pause() {}
}

test("cast through review, listen in windows, re-plan on a casting change, read as text, preview a draft", { skip: !base }, async () => {
  const window = install(base);
  globalThis.Audio = FakeAudio;
  const renders = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (url, options) => {
    if (String(url).endsWith("/api/voicecast.render")) renders.push(JSON.parse(options.body));
    return realFetch(url, options);
  };
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // -- Voices: Mara Vell is cast through a review, and a consented voice needs its note ----------------------------
  const nav = body.querySelectorAll("a").find((node) => node.getAttribute("href") === "#/voices");
  assert.ok(nav, "Settings lists the Voices page");
  window.location.hash = "#/voices?chat=" + encodeURIComponent(CHAT);
  const speakers = await until(() => section(main, "Speakers in this chat"), 15000, "the Voices page");
  const mara = await until(() => group(speakers, "Mara Vell"), 15000, "Mara Vell among the speakers");
  assert.match(mara.textContent, /Text only \(not cast\)/);
  assert.match(group(speakers, "Tamsin Hale").textContent, /Speaks with tone\/rms/);
  assert.match(group(speakers, "Narrator").textContent, /Speaks with tone\/slt/);
  button(mara, "Cast a voice…").click();
  const form = await until(() => group(main, "Cast a voice for Mara Vell"), 15000, "the cast form");
  const target = field(form, "Cast for");
  const labels = target.querySelectorAll("option").map((node) => node.textContent);
  assert.deepEqual(labels, ["Mara Vell in this campaign only", "Mara Vell at revision r1 only", "Mara Vell at any revision"]);
  choose(target, target.querySelectorAll("option")[2].getAttribute("value"));
  choose(field(group(main, "Cast a voice for Mara Vell"), "Voice"), "tone/kal");
  choose(field(group(main, "Cast a voice for Mara Vell"), "Rights"), "consented");
  button(group(main, "Cast a voice for Mara Vell"), "Review").click();
  await until(() => group(main, "Mara Vell").textContent.includes("needs a note"), 15000, "the note asked for");
  assert.equal(group(main, "Review this change"), undefined, "nothing is reviewed without the note");
  choose(field(group(main, "Cast a voice for Mara Vell"), "Rights"), "engine");
  button(group(main, "Cast a voice for Mara Vell"), "Review").click();
  const review = await until(() => group(main, "Review this change"), 15000, "the review");
  assert.match(review.textContent, /Cast tone\/kal for card:mara-vell\/character\/mara-vell at any revision\./);
  assert.match(review.textContent, /Writes config\/voices\/casting\.json on this device\. No turn, source or chat changes\./);
  assert.equal((await call("voicecast.read", {})).casting.length, 2, "the review wrote nothing");
  assert.equal(document.activeElement, button(review, "Make this change"), "focus moves to the review");
  button(review, "Make this change").click();
  await until(() => main.querySelector("p.voices-live").textContent.startsWith("Cast: tone/kal"), 15000, "the change made");
  assert.equal(document.activeElement, main.querySelector("p.voices-live"), "focus moves to what happened");
  assert.match(group(main, "Mara Vell").textContent, /Speaks with tone\/kal/);
  assert.ok(section(main, "Not available yet").textContent.includes("Pronunciation, prosody and expression is not available yet."));

  // -- Listen: the plan, each voice and why -------------------------------------------------------------------------
  window.location.hash = "#/chat/" + encodeURIComponent(CHAT);
  const listen = await until(() => main.querySelector("details.chat-listen"), 15000, "Listen to the scene beside the composer");
  listen.open = true;
  assert.ok(main.querySelector("section.chat-voice") === null || !main.querySelector("section.chat-voice").textContent.includes("Listen to the scene"),
    "067's Read aloud is a separate control");
  button(listen, "Plan the scene").click();
  const cues = await until(() => listen.querySelectorAll("li.chat-listen-cue").length && listen.querySelectorAll("li.chat-listen-cue"), 15000, "the plan");
  const row = (at) => listen.querySelectorAll("li.chat-listen-cue")[at];
  const speaker = (node) => node.querySelector("span.chat-listen-speaker").textContent;
  const pill = (node) => node.querySelector("span.g-pill").textContent;
  assert.deepEqual(cues.map((node) => speaker(node) + ": " + pill(node)), [
    "Mara Vell: tone/kal", "Anya Rook: Text only", "Mara Vell: tone/kal", "Anya Rook: Text only", "Tamsin Hale: tone/rms",
    "Anya Rook: Text only", "Narrator: tone/slt", "Anya Rook: Text only", "Mara Vell: tone/kal"]);
  assert.match(row(1).textContent, /Text only: nobody cast a voice for this speaker\./);
  assert.match(row(3).textContent, /Text only: an action: shown, not spoken\./);
  assert.match(row(4).textContent, /Customs saw nothing come ashore\./, "the accepted text is shown");
  button(row(4), "Why this voice").click();
  assert.match(row(4).textContent, /Tamsin Hale speaks with tone\/rms, cast for the campaign's own choice/);
  assert.match(row(4).textContent, /this campaign's own choice for the actor \(campaign:[^)]+\): tone\/rms/);
  assert.equal(listen.querySelectorAll("div.g-notice").length, 0, "no two speakers share a voice");

  // -- Play: windows in order, the next asked for while the last cue of this one plays -------------------------
  const current = () => listen.querySelector('li[aria-current="true"]');
  const playing = async (count, who) => {
    await until(() => sounds.length === count, 15000, "sound " + count);
    assert.equal(speaker(current()), who, "the cue being read is marked");
    assert.equal(listen.querySelector("div.chat-listen-now").textContent.includes(who), true);
  };
  button(listen, "Play").click();
  await playing(1, "Mara Vell");
  assert.deepEqual(renders.map((item) => [item.start, item.count, item.audio]), [[0, 4, true]]);
  sounds[0].finish();
  await playing(2, "Mara Vell");  // Anya's post, between, stays text and is shown with its reason
  sounds[1].finish();
  await playing(3, "Tamsin Hale");
  assert.deepEqual(renders.map((item) => item.start), [0, 4], "the second window was asked for while the first played");
  assert.ok(renders.every((item) => item.fingerprint && item.fingerprint.startsWith("sha256:")), "every window names the plan");
  sounds[2].finish();
  await playing(4, "Narrator");
  // another screen recasts Tamsin while the narrator is being read: the next window is refused and planned again
  const read = await call("voicecast.read", {});
  await call("voicecast.cast", { target: "actor", ref: TAMSIN, engine: "tone", voice: "awb", expected: read.etag });
  sounds[3].finish();
  const status = listen.querySelector("p.chat-listen-status");
  await until(() => status.textContent.includes("changed while it played, so it was planned again"), 15000, "the re-plan");
  assert.deepEqual(renders.map((item) => item.start), [0, 4, 8]);
  assert.equal(pill(row(4)), "tone/awb", "the new plan names the new voice");
  assert.equal(current(), null, "nothing is marked as reading once stopped");
  const again = button(listen, "Play from Mara Vell");
  assert.ok(again, "play goes on from the cue that was next");
  again.click();
  await playing(5, "Mara Vell");
  sounds[4].finish();
  await until(() => status.textContent === "Played to the end.", 15000, "the end");
  assert.deepEqual(renders.map((item) => item.start), [0, 4, 8, 8]);

  // -- audio off: the scene reads as text, nothing is synthesized ------------------------------------------------
  const audio = field(listen, "Play the voices (off: read as text)");
  audio.checked = false;
  audio.dispatchEvent({ type: "change" });
  button(listen, "Play").click();
  await until(() => status.textContent === "Read to the end as text: nothing was synthesized.", 15000, "read as text");
  assert.equal(sounds.length, 5, "no sound with audio off");
  assert.equal(elements.length, 1, "one audio element, started from the Play press, plays every cue");
  assert.ok(renders.slice(4).length && renders.slice(4).every((item) => item.audio === false));
  assert.match(row(0).textContent, /Text only: audio is off: read as text\./);

  // -- the draft's context, previewed for Mara: never posted, the composer untouched -----------------------------
  const composer = main.querySelector("div.chat-composer").querySelector("textarea");
  type(composer, DRAFT);
  const before = await call("open", { chat: CHAT });
  const preview = main.querySelector("details.chat-preview");
  preview.open = true;
  assert.ok(preview.textContent.includes("is the director's. Switch to the director view"), "the table view shows no NPC's context");
  assert.equal(group(preview, "Preview an NPC's context").hidden, true);
  byText(preview, "button", "Director view").find((node) => node.classList.contains("g-segment")).click();
  const box = group(preview, "Preview an NPC's context");
  assert.equal(box.hidden, false);
  assert.equal(field(box, "Whose context").value, MARA);
  assert.equal(box.querySelectorAll("a").find((node) => node.textContent === "Full context").getAttribute("href"),
    "#/providers?chat=" + encodeURIComponent(CHAT));
  button(box, "Preview context").click();
  await until(() => box.querySelector("p.chat-preview-status").textContent.startsWith("An estimate for Mara Vell"), 30000, "the preview");
  const lore = box.querySelector('ul[aria-label="Lore the draft triggers"]');
  const memory = box.querySelector('ul[aria-label="Memories it likely recalls"]');
  assert.ok(lore && lore.textContent.includes(LIGHTHOUSE) && lore.textContent.includes("(exact)"), "the lore the draft triggers");
  assert.ok(memory && memory.textContent.includes(PROMISE) && memory.textContent.includes("(an estimate)"), "the memory it recalls");
  assert.match(box.textContent, /Nothing was posted, saved or sent to a model\./);
  assert.equal(composer.value, DRAFT, "the composer still holds the draft");
  const after = await call("open", { chat: CHAT });
  assert.equal(JSON.stringify(after.turns), JSON.stringify(before.turns), "nothing was posted");
});
