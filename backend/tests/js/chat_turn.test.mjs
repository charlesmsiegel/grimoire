// The chat page's pure logic (pages/chat/turn.js): which takes can be stepped through and which is on show,
// when a reply is being written or did not arrive, what the composer allows and says, and the take's keys.
// Run by test_app_shell_js.py with Node's own test runner. Every name here is made up.
import assert from "node:assert/strict";
import { test } from "node:test";

import { composerState, isCorrectable, isStopped, openTurn, replyState, shownTake, stepTake, takeKey, takeLabel }
  from "../../src/grimoire/app/static/pages/chat/turn.js";

const line = (id, text) => ({ line: id, speaker: "Mara", actor: "campaign:c#character/mara", text });
const take = (number, lines, more = {}) => ({ take: number, state: "proposed", origin: "cast", lines, calls: [], ...more });

function scene({ offered = null, waiting = [], seated = true, persona = { actor: "campaign:c#character/anya", name: "Anya" } } = {}) {
  const turns = [{ turn: "t1", post: null, kept: take(1, [line("a", "Hello.")]), offered: [], open: false, passed: false }];
  if (offered) turns.push({ turn: "t2", post: { actor: persona && persona.actor, speaker: "Anya", text: "Well?" }, kept: null, offered, open: true, passed: false });
  return { persona, turns, state: { waiting, seated, open_turn: offered ? "t2" : null, keepable: [], unscreened: [] } };
}

test("the open turn is the one the scene names, or none", () => {
  assert.equal(openTurn(scene()), null);
  assert.equal(openTurn(scene({ offered: [] })).turn, "t2");
});

test("only takes with a line to read are stepped through, oldest first", () => {
  const offered = [take(1, []), take(2, [line("b", "One.")]), take(3, [line("c", "Two.")]), take(4, [], { state: "running" })];
  const reply = replyState(scene({ offered }));
  assert.deepEqual(reply.takes.map((item) => item.take), [2, 3]);
  assert.equal(reply.writing, true, "a take still running is a reply being written");
  assert.equal(reply.failed, null);
  assert.equal(reply.unanswered, false);
});

test("the newest take is on show until another is chosen, and a chosen take that is gone is not kept", () => {
  const takes = [take(2, [line("b", "One.")]), take(3, [line("c", "Two.")]), take(5, [line("d", "Three.")])];
  assert.equal(shownTake(takes, null).take, 5);
  assert.equal(shownTake(takes, 3).take, 3);
  assert.equal(shownTake(takes, 4).take, 5, "take 4 was withdrawn: the newest shows");
  assert.equal(shownTake([], 3), null);
  assert.equal(takeLabel(takes, takes[1]), "Take 2 of 3");
  assert.equal(takeLabel(takes, { ...takes[2], origin: "director" }).startsWith("Take"), true);
});

test("stepping goes one take along and round at the ends", () => {
  const takes = [take(2, [line("b", "One.")]), take(3, [line("c", "Two.")]), take(5, [line("d", "Three.")])];
  assert.equal(stepTake(takes, null, -1), 3);
  assert.equal(stepTake(takes, 3, -1), 2);
  assert.equal(stepTake(takes, 2, -1), 5);
  assert.equal(stepTake(takes, 5, 1), 2);
  assert.equal(stepTake([], null, 1), null);
  assert.equal(stepTake([takes[0]], 2, 1), 2, "one take steps to itself");
});

test("a reply that did not arrive says why: the ask failed, it was cut off, or every call ended without a line", () => {
  const refused = take(2, [], { calls: [{ actor: "mara", status: "refused", reason: "policy" }] });
  assert.deepEqual(replyState(scene({ offered: [refused] })).failed, { kind: "calls", calls: refused.calls });
  assert.deepEqual(replyState(scene({ offered: [] }), { failure: { text: "network: the app did not answer" } }).failed,
    { kind: "error", text: "network: the app did not answer" });
  assert.deepEqual(replyState(scene({ offered: [take(1, [], { state: "running" })], waiting: ["interrupted"] })).failed, { kind: "interrupted" });
  assert.equal(replyState(scene({ offered: [take(1, [], { state: "running" })], waiting: ["interrupted"] })).writing, false,
    "a run that was cut off is not being written");
  // an older take with lines is still offered beside the newest one that failed
  const both = replyState(scene({ offered: [take(1, [line("b", "One.")]), refused] }));
  assert.equal(both.takes.length, 1);
  assert.equal(both.failed.kind, "calls");
  // while this page waits for an answer nothing has failed yet
  assert.equal(replyState(scene({ offered: [refused] }), { writing: true, failure: { text: "x" } }).failed, null);
  // an ask that failed with no turn open is still said
  assert.equal(replyState(scene(), { failure: { text: "x" } }).failed.kind, "error");
});

test("a turn open with nothing to keep and nothing failed is unanswered", () => {
  assert.equal(replyState(scene({ offered: [] })).unanswered, true);
  assert.equal(replyState(scene({ offered: [take(1, [])] })).unanswered, true, "a take whose lines were all skipped");
  assert.equal(replyState(scene({ offered: [take(1, [line("b", "One.")])] })).unanswered, false);
  assert.equal(replyState(scene()).unanswered, false, "no turn is open");
});

test("the composer posts only with a character, a seat, play not held up, no reply being written and the turn closed", () => {
  const state = (made, options) => composerState(made, replyState(made, options));
  const idle = state(scene());
  assert.deepEqual(idle, { canPost: true, canAsk: true, placeholder: "As Anya: say or do something" });
  const pending = state(scene({ offered: [take(1, [line("b", "One.")])] }));
  assert.equal(pending.canPost, false);
  assert.equal(pending.canAsk, true, "another take may be asked for");
  assert.equal(pending.placeholder, "Keep a take or pass the turn before you post again");
  assert.equal(state(scene({ offered: [] })).placeholder, "Pass the turn or ask for a reply before you post again");
  const noModel = composerState(scene({ offered: [] }), replyState(scene({ offered: [] })), { asks: false });
  assert.deepEqual(noModel, { canPost: false, canAsk: false, placeholder: "Pass the turn before you post again" });
  assert.equal(composerState(scene(), replyState(scene()), { asks: false }).canPost, true, "with no model a post still goes in");
  const paused = state(scene({ waiting: ["paused"] }));
  assert.deepEqual(paused, { canPost: false, canAsk: false, placeholder: "Play is paused. Resume to post" });
  const writing = state(scene(), { writing: true });
  assert.deepEqual(writing, { canPost: false, canAsk: false, placeholder: "The table is answering…" });
  assert.equal(state(scene({ seated: false })).placeholder, "Take your seat before you post");
  assert.equal(state(scene({ waiting: ["forked"] })).canPost, false);
  const nobody = state(scene({ persona: null }));
  assert.equal(nobody.canPost, false);
  assert.equal(nobody.canAsk, true, "a chat with no character to play can still be asked to go on");
  assert.equal(state(scene({ persona: { actor: "a", name: null } })).placeholder, "Say or do something");
});

test("a pause holds play but not corrections; a fork holds both", () => {
  assert.equal(isStopped(scene()), false);
  assert.equal(isStopped(scene({ waiting: ["paused"] })), true);
  assert.equal(isStopped(scene({ seated: false })), true);
  assert.equal(isStopped(scene({ waiting: ["interrupted"] })), false);
  assert.equal(isCorrectable(scene({ waiting: ["paused"] })), true);
  assert.equal(isCorrectable(scene({ waiting: ["paused", "forked"] })), false);
  assert.equal(isCorrectable(scene({ seated: false })), false);
});

test("the take's keys: arrows step, Enter keeps, R tries again, P passes, and a chord is nobody's", () => {
  assert.equal(takeKey({ key: "ArrowLeft" }), "prev");
  assert.equal(takeKey({ key: "ArrowRight" }), "next");
  assert.equal(takeKey({ key: "Enter" }), "keep");
  assert.equal(takeKey({ key: "r" }), "again");
  assert.equal(takeKey({ key: "R", shiftKey: true }), "again");
  assert.equal(takeKey({ key: "p" }), "pass");
  for (const key of ["c", "k", "/", "?", "Escape", " "]) assert.equal(takeKey({ key }), null, key + " is left alone");
  assert.equal(takeKey({ key: "k", ctrlKey: true }), null);
  assert.equal(takeKey({ key: "r", ctrlKey: true }), null, "Ctrl+R is the browser's");
  assert.equal(takeKey({ key: "p", metaKey: true }), null);
  assert.equal(takeKey({ key: "Enter", isComposing: true }), null);
});
