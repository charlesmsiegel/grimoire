// Opening a chat at one turn (pages/chat/find.js): the `?turn=` of an address, and marking that turn's rows.
import assert from "node:assert/strict";
import { test } from "node:test";

import { install } from "./fakedom.mjs";
import { showTurn, wantedTurn } from "../../src/grimoire/app/static/pages/chat/find.js";

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
