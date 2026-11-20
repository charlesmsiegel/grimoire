// Opening a chat at one turn (pages/chat/find.js): the `?turn=` of an address, and marking that turn's rows.
import assert from "node:assert/strict";
import { test } from "node:test";

import { install } from "./fakedom.mjs";
import { showTurn, trackTurn, wantedTurn } from "../../src/grimoire/app/static/pages/chat/find.js";

install("http://127.0.0.1:1");

test("only a whole positive number is a turn to open at", () => {
  const wanted = (text) => wantedTurn(new URLSearchParams(text));
  assert.equal(wanted("turn=7"), 7);
  assert.equal(wanted("unfinished=x&turn=120"), 120);
  for (const text of ["", "turn=", "turn=0", "turn=-3", "turn=2.5", "turn=07", "turn=abc", "turn=1e3", "turn=1234567890"]) assert.equal(wanted(text), null, text);
  assert.equal(wantedTurn(null), null);
});

test("the turn's rows are scrolled to and marked for a moment; a turn that is not shown changes nothing", () => {
  const rows = document.createElement("div");
  const row = (turn) => { const node = document.createElement("div"); if (turn) node.setAttribute("data-turn-number", String(turn)); rows.append(node); return node; };
  const [history, post, line, other] = [row(null), row(2), row(2), row(12)];
  let scrolled = null;
  post.scrollIntoView = (how) => { scrolled = how; };
  let later = null;
  assert.equal(showTurn(rows, 2, { timer: (work) => { later = work; } }), true);
  assert.deepEqual(scrolled, { block: "center" });
  assert.deepEqual([history, post, line, other].map((node) => node.classList.contains("chat-found")), [false, true, true, false]);
  later();
  assert.ok(!post.classList.contains("chat-found") && !line.classList.contains("chat-found"), "the mark goes again");
  assert.equal(showTurn(rows, 1, { timer: () => { throw new Error("nothing to unmark"); } }), false);
  assert.ok(!other.classList.contains("chat-found"), "turn 1 is not turn 12");
});


test("late redraws inherit one highlight lifetime without moving scroll or restarting its timer", () => {
  const rows = document.createElement("div");
  let scrolled = 0, timers = 0, expire;
  const row = (n) => { const node = document.createElement("div"); node.setAttribute("data-turn-number", String(n)); node.scrollIntoView = () => { scrolled++; }; return node; };
  const tracking = trackTurn(2, { timer: (work, ms) => { timers++; expire = work; assert.equal(ms, 4000); return 1; } });
  rows.append(row(2));
  assert.equal(tracking.refresh(rows), false, "initial render waits for scroll restoration");
  assert.equal(tracking.start(rows), true);
  rows.replaceChildren(row(2), row(2), row(3));
  assert.equal(tracking.refresh(rows), true);
  assert.equal(rows.querySelectorAll("div.chat-found").length, 2);
  assert.equal(scrolled, 1);
  assert.equal(timers, 1);
  expire();
  assert.equal(rows.querySelectorAll("div.chat-found").length, 0);
  rows.replaceChildren(row(2));
  assert.equal(tracking.refresh(rows), false);
  assert.equal(tracking.start(rows), false, "expiry cannot be restarted");
  assert.equal(timers, 1);
});

test("leaving cancels and clears the live mark and cannot be undone by late redraw or start", () => {
  const rows = document.createElement("div");
  const row = document.createElement("div"); row.setAttribute("data-turn-number", "2"); rows.append(row);
  let cancelled = 0;
  const tracking = trackTurn(2, { timer: () => 17, cancel: (id) => { assert.equal(id, 17); cancelled++; } });
  tracking.start(rows);
  tracking.dispose(); tracking.dispose();
  assert.equal(cancelled, 1);
  assert.equal(rows.querySelectorAll("div.chat-found").length, 0);
  assert.equal(tracking.refresh(rows), false);
  assert.equal(tracking.start(rows), false);
  const aborted = trackTurn(2, { timer: () => { throw new Error("no timer after abort"); } });
  aborted.dispose();
  assert.equal(aborted.start(rows), false);
  for (const number of [null, 9]) {
    const missing = trackTurn(number, { timer: () => { throw new Error("missing turn has no timer"); } });
    assert.equal(missing.start(rows), false);
    assert.equal(missing.refresh(rows), false);
  }
});
