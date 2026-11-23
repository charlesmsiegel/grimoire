// The Voices page's logic (pages/voices/logic.js, 116 over 083): a chat's speakers and what each can be cast as
// (from the rules the plan looked at), the arguments of a review and of the write, what a review says, and whether
// a casting already holds what was reviewed (a write retried after it was made). Every name is made up.
import assert from "node:assert/strict";
import { test } from "node:test";

import { campaignOf, castArgs, formProblem, holdsReviewed, reviewText, speakersOf, targetArgs, targetKey, targetOf, voiceOptions }
  from "../../src/grimoire/app/static/pages/voices/logic.js";
import { install, until } from "./fakedom.mjs";
import * as ui from "../../src/grimoire/app/static/shell/ui.js";
import { mount } from "../../src/grimoire/app/static/pages/voices/page.js";

const CHAT = "campaign:harbor#chat/c-1";
const MARA = "campaign:harbor/character/mara-1", DEF = "card:mara/character/mara";
const plan = { cues: [
  { speaker: "Mara Vell", actor: MARA, status: "voiced", reason: null, voice: { engine: "tone", voice: "kal" },
    considered: [{ rule: "actor", names: MARA }, { rule: "revision", names: DEF + "@r1" }, { rule: "character_any_revision", names: DEF }] },
  { speaker: "Anya Rook", actor: "campaign:harbor/character/anya-1", status: "text_only", reason: "not_cast", voice: null,
    considered: [{ rule: "actor", names: "campaign:harbor/character/anya-1" }] },
  { speaker: "Mara Vell", actor: MARA, status: "voiced", reason: null, voice: { engine: "tone", voice: "kal" },
    considered: [{ rule: "actor", names: MARA }, { rule: "revision", names: DEF + "@r2" }, { rule: "character_any_revision", names: DEF },
      { rule: "voice_profile", names: DEF + "@r1" }] },
  { speaker: "Narrator", actor: null, status: "voiced", reason: null, voice: { engine: "tone", voice: "slt" },
    considered: [{ rule: "narrator", names: "the narrator of harbor" }, { rule: "narrator_every_campaign", names: "the narrator of every campaign" }] },
] };

test("a chat's speakers, in the order they first speak, each with every target once", () => {
  assert.equal(campaignOf(CHAT), "harbor");
  assert.equal(campaignOf("not a chat"), null);
  const speakers = speakersOf(plan, CHAT);
  assert.deepEqual(speakers.map((item) => [item.name, item.cues, item.voices, item.reasons]), [
    ["Mara Vell", 2, ["tone/kal"], []], ["Anya Rook", 1, [], ["not_cast"]], ["Narrator", 1, ["tone/slt"], []]]);
  assert.deepEqual(speakers[0].targets.map((item) => item.label), ["Mara Vell in this campaign only", "Mara Vell at revision r1 only",
    "Mara Vell at any revision", "Mara Vell at revision r2 only"]);  // r1 again as the voice profile: not twice
  assert.deepEqual(speakers[2].targets.map((item) => [item.target, item.campaign]), [["narrator", "harbor"], ["narrator", null]]);
  assert.equal(targetOf("voice_profile", DEF + "@r3", "Mara Vell", "harbor").label, "The voice profile " + DEF + "@r3 (that revision only)");
  assert.equal(targetOf("narrator", "the narrator of x", "Narrator", null), null, "no campaign, no per-campaign narrator");
  assert.equal(targetOf("something_new", "x", "Mara Vell", "harbor"), null);
});

test("the arguments of a review and of the write made as reviewed", () => {
  const [actor, , any] = speakersOf(plan, CHAT)[0].targets;
  assert.deepEqual(castArgs(any, { voice: "tone/kal", rights: "engine", note: " " }),
    { target: "character", ref: DEF, engine: "tone", voice: "kal", rights: "engine" });
  assert.deepEqual(castArgs(actor, { voice: "piper/jo", rights: "consented", note: " Jo agreed for this table " }),
    { target: "actor", ref: MARA, engine: "piper", voice: "jo", rights: "consented", note: "Jo agreed for this table" });
  const narrator = speakersOf(plan, CHAT)[2].targets[1];
  assert.deepEqual(castArgs(narrator, { voice: "tone/slt" }), { target: "narrator", campaign: null, engine: "tone", voice: "slt", rights: "engine" });
  assert.deepEqual(targetArgs(narrator), { target: "narrator", campaign: null });
  assert.deepEqual(targetArgs(any), { target: "character", ref: DEF });
  assert.notEqual(targetKey(narrator), targetKey(speakersOf(plan, CHAT)[2].targets[0]));
  assert.deepEqual(voiceOptions([{ engine: "tone", voices: ["kal", "slt"] }, { engine: "flite", voices: [] }]).map((item) => item.value), ["tone/kal", "tone/slt"]);
  assert.equal(formProblem({ target: "x", voice: "tone/kal", rights: "engine", note: "" }), null);
  assert.match(formProblem({ target: "x", voice: "tone/kal", rights: "licensed", note: "  " }), /needs a note/);
  assert.match(formProblem({ target: "x", voice: "", rights: "engine", note: "" }), /Choose a voice/);
});

test("what a review says, and whether the casting already holds it", () => {
  const after = { engine: "tone", voice: "kal", key: "tone/kal", rights: "engine", note: null };
  const review = { target: "character", ref: DEF, campaign: null, describes: DEF + " at any revision", change: "new", before: null, after };
  assert.equal(reviewText(review), "Cast tone/kal for " + DEF + " at any revision.");
  assert.equal(reviewText({ ...review, change: "replace", before: { key: "tone/slt" } }), "Cast tone/kal for " + DEF + " at any revision, replacing tone/slt.");
  assert.equal(reviewText({ ...review, change: "remove", before: { key: "tone/slt" }, after: null }),
    "Remove tone/slt from " + DEF + " at any revision. It then stays text unless another rule voices it.");
  assert.match(reviewText({ ...review, change: "same" }), /nothing would change/);
  assert.match(reviewText({ ...review, after: { ...after, rights: "licensed", note: "Licence 7" } }), /\(licensed: Licence 7\)/);
  const held = { casting: [{ target: "character", ref: DEF, campaign: null, engine: "tone", voice: "kal", rights: "engine", note: null }] };
  assert.equal(holdsReviewed(held, review), true);
  assert.equal(holdsReviewed(held, { ...review, after: { ...after, voice: "awb" } }), false);
  assert.equal(holdsReviewed({ casting: [] }, { ...review, change: "remove" }), true);
  assert.equal(holdsReviewed(held, { ...review, change: "remove" }), false);
});

// The page itself, against a stand-in API: a review numbered against the form, the focus kept on a choice, and a
// write whose answer was lost read back before anything is said about it.
const ENTRY = (args) => ({ target: args.target, ref: args.ref || null, campaign: args.campaign || null, engine: args.engine,
  voice: args.voice, rights: args.rights, note: args.note || null, describes: "Mara Vell in this campaign only", available: true });
const REVIEWED = (args, change = "new") => ({ kind: args.kind, target: args.target, ref: args.ref || null, campaign: args.campaign || null,
  describes: "Mara Vell in this campaign only", before: change === "same" ? ENTRY(args) : null,
  after: { engine: args.engine, voice: args.voice, key: args.engine + "/" + args.voice, rights: args.rights, note: args.note || null },
  change, available: true, etag: null, writes: change === "same" ? [] : ["config/voices/casting.json"], removes_file: false,
  entries_after: 1, written: false });

async function page({ review = (args) => Promise.resolve(REVIEWED(args)), cast } = {}) {
  install("http://localhost");
  const controller = new AbortController();
  const asked = [];
  const held = { casting: [] };
  const api = (op, args) => {
    asked.push({ op, args });
    if (op === "voicecast.read") return Promise.resolve({ casting: held.casting, etag: null, problem: null, engines: [{ engine: "tone", available: true, voices: ["kal", "slt"] }] });
    if (op === "voicecast.plan") return Promise.resolve({ ...plan, chat: CHAT });
    if (op === "voices.review") return review(args);
    if (op === "voicecast.cast") return cast(args, held);
    return Promise.reject(Object.assign(new Error("not here"), { reason: "unknown_op" }));
  };
  const root = ui.el("main");
  document.body.append(root);
  const ctx = { ui, api, signal: controller.signal, query: new URLSearchParams("chat=" + encodeURIComponent(CHAT)),
    setTitle() {}, layout() {}, styles() {}, setCrumb() {}, navigate() {}, href: () => "#", registered: () => false, has: () => true };
  await mount(root, ctx);
  const group = (label) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === label);
  const button = (within, name) => within.querySelectorAll("button").find((node) => node.textContent === name);
  const form = () => group("Cast a voice for Mara Vell");
  const fieldOf = (label) => {
    const tag = form().querySelectorAll("label").find((node) => node.textContent === label);
    return form().querySelectorAll("select,input").find((node) => node.id === tag.getAttribute("for"));
  };
  const choose = (label, value) => { const select = fieldOf(label); select.focus(); select.value = value; select.dispatchEvent({ type: "change" }); };
  const live = () => root.querySelector("p.voices-live").textContent;
  button(group("Mara Vell"), "Cast a voice…").click();
  return { root, asked, held, group, button, form, fieldOf, choose, live };
}

test("a changed choice keeps the focus on that choice", async () => {
  const p = await page();
  assert.ok(document.activeElement === p.fieldOf("Cast for"), "the form opens on its first choice");
  p.choose("Voice", "tone/slt");
  assert.ok(document.activeElement === p.fieldOf("Voice"), "the focus stays on the select though the row is drawn again");
  assert.equal(p.fieldOf("Voice").value, "tone/slt");
  p.choose("Rights", "licensed");
  assert.ok(document.activeElement === p.fieldOf("Rights"));
  assert.ok(p.fieldOf("Rights note"), "a licensed voice asks for its note");
});

test("a review's late answer for an earlier choice is dropped, and a new note drops a review shown", async () => {
  let release;
  const p = await page({ review: (args) => new Promise((resolve) => { release = () => resolve(REVIEWED(args)); }) });
  p.button(p.form(), "Review").click();
  p.choose("Voice", "tone/slt");  // chosen again while the review was on its way
  release();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(p.group("Review this change"), undefined, "the answer for tone/kal is not shown for tone/slt");
  p.choose("Rights", "consented");
  const note = p.fieldOf("Rights note");
  note.value = "Jo agreed for this table";
  note.dispatchEvent({ type: "input" });
  p.button(p.form(), "Review").click();
  release();
  await until(() => p.group("Review this change"), 2000, "the review");
  assert.deepEqual(p.asked.filter((item) => item.op === "voices.review").at(-1).args.note, "Jo agreed for this table");
  p.fieldOf("Rights note").focus();
  p.fieldOf("Rights note").value = "Jo agreed for this table only";
  p.fieldOf("Rights note").dispatchEvent({ type: "input" });
  assert.equal(p.group("Review this change"), undefined, "a review of the old note is not left to be made");
  assert.ok(document.activeElement === p.fieldOf("Rights note"), "typing goes on in the note");
});

test("a review that would change nothing says so and puts the focus on Cancel", async () => {
  const p = await page({ review: (args) => Promise.resolve(REVIEWED(args, "same")) });
  p.button(p.form(), "Review").click();
  await until(() => p.group("Review this change"), 2000, "the review");
  assert.match(p.live(), /nothing would change/);
  assert.ok(document.activeElement === p.button(p.group("Review this change"), "Cancel"));
});

test("a write whose answer was lost is read back: made, or not made", async () => {
  const lost = () => Object.assign(new Error("the app did not answer"), { reason: "network" });
  const made = await page({ cast: (args, held) => { held.casting = [ENTRY(args)]; return Promise.reject(lost()); } });
  made.button(made.form(), "Review").click();
  await until(() => made.group("Review this change"), 2000, "the review");
  made.button(made.group("Review this change"), "Make this change").click();
  await until(() => /answer was lost/.test(made.live()), 2000, "the read back");
  assert.match(made.live(), /but the change was made: the casting holds it/);
  assert.doesNotMatch(made.live(), /Nothing was written/);
  const not = await page({ cast: () => Promise.reject(Object.assign(new Error("the app sent no answer"), { reason: "bad_response" })) });
  not.button(not.form(), "Review").click();
  await until(() => not.group("Review this change"), 2000, "the review");
  not.button(not.group("Review this change"), "Make this change").click();
  await until(() => /answer was lost/.test(not.live()), 2000, "the read back");
  assert.match(not.live(), /does not hold this change/);
  const refused = await page({ cast: () => Promise.reject(Object.assign(new Error("no such voice"), { reason: "invalid_voice" })) });
  refused.button(refused.form(), "Review").click();
  await until(() => refused.group("Review this change"), 2000, "the review");
  refused.button(refused.group("Review this change"), "Make this change").click();
  await until(() => /Nothing was written/.test(refused.live()), 2000, "a refusal");
});
