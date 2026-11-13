import { test } from "node:test";
import assert from "node:assert/strict";
import { install, byText } from "./fakedom.mjs";
import * as ui from "../../src/grimoire/app/static/shell/ui.js";
import {
  eligibleActors,
  actorArt,
  cueStore,
  makeStaging,
} from "../../src/grimoire/app/static/pages/chat/staging.js";
const turn = {
  turn: "t1",
  number: 1,
  post: {
    actor: "p",
    speaker: "Past player",
    text: "Secret",
    private: true,
    to: ["a"],
  },
  kept: {
    lines: [
      { actor: "a", speaker: "Historical A", text: "Exact words" },
      { actor: "gone", speaker: "Departed", text: "Farewell" },
    ],
  },
};
test("eligibility uses kept attribution and post only, preserving historical labels", () => {
  assert.deepEqual(
    eligibleActors(turn).map((x) => [x.actor, x.name]),
    [
      ["p", "Past player"],
      ["a", "Historical A"],
      ["gone", "Departed"],
    ],
  );
  assert.deepEqual(eligibleActors({ ...turn, kept: null }), []);
});
test("resolved shown outfits and portrait fallback exclude unauthorized assets", () => {
  const member = {
    art: {
      slots: [
        { slot: "expr.red.neutral", state: "shown", image: "media:red" },
        { slot: "expr.blue.neutral", state: "shown", image: "media:blue" },
        { slot: "expr.hidden.neutral", state: "hidden", image: "media:secret" },
      ],
    },
  };
  assert.equal(actorArt(member).length, 2);
  assert.equal(
    actorArt({
      art: {
        slots: [{ slot: "portrait", state: "shown", image: "media:face" }],
      },
    })[0].slot,
    "portrait",
  );
  assert.deepEqual(actorArt(null), []);
});
test("storage errors and malformed metadata are observable; bounds enforced", () => {
  let raw = "{";
  const storage = {
    getItem: () => raw,
    setItem: (k, v) => {
      raw = v;
    },
  };
  const s = cueStore("c", storage);
  assert.match(s.warning, /malformed/i);
  assert.equal(s.save({}), true);
  assert.equal(s.warning, "");
  assert.equal(s.save({ t: { note: "x".repeat(1001) } }), false);
  assert.equal(
    s.save(Object.fromEntries(Array.from({ length: 101 }, (_, i) => [i, {}]))),
    false,
  );
  const fail = cueStore("c", {
    getItem() {
      throw Error();
    },
    setItem() {
      throw Error();
    },
  });
  assert.match(fail.warning, /not saved/i);
  assert.equal(
    fail.save({
      t: {
        note: "ok",
        actors: {},
        focus: "",
        background: "",
        transition: "none",
      },
    }),
    false,
  );
  assert.equal(fail.cues.t.note, "ok");
});
test("mode switching leaves transcript nodes intact, preserves privacy and stale turns", () => {
  install();
  const log = ui.el("section", {}, "Transcript unchanged");
  const ctx = {
    ui,
    styles() {},
    has() {
      return false;
    },
    signal: new AbortController().signal,
  };
  const stage = makeStaging(ctx, "exact-chat", { log });
  document.body.append(log, stage.node);
  stage.update({ turns: [turn], cast: [{ actor: "silent", name: "Silent" }] });
  const mode = stage.node.querySelector('[name="stage-mode"]');
  mode.value = "vn";
  mode.dispatchEvent({ type: "change" });
  assert.match(stage.node.textContent, /Historical A/);
  assert.match(stage.node.textContent, /Private/);
  assert.match(stage.node.textContent, /to a/);
  assert.equal(log.textContent, "Transcript unchanged");
  assert.equal(log.hidden, true);
  stage.update({ turns: [], cast: [] });
  assert.match(stage.node.textContent, /unavailable/);
  mode.value = "transcript";
  mode.dispatchEvent({ type: "change" });
  assert.equal(log.hidden, false);
});

test("cue edit cancel save reset and stale assets remain explicit", async () => {
  install();
  const log = ui.el("section", {}, "kept");
  const requests = [];
  const stage = makeStaging(
    {
      ui,
      styles() {},
      has() {
        return false;
      },
      picture(image) {
        requests.push(image);
        return Promise.resolve("/authorized/" + image);
      },
      signal: new AbortController().signal,
    },
    "fixture-cues",
    { log },
  );
  document.body.append(stage.node);
  const scene = {
    turns: [turn],
    cast: [
      {
        actor: "a",
        name: "Renamed",
        art: {
          slots: [
            { slot: "expr.red.neutral", state: "shown", image: "red" },
            { slot: "expr.blue.neutral", state: "shown", image: "blue" },
          ],
        },
      },
      {
        actor: "p",
        art: {
          slots: [{ slot: "portrait", state: "shown", image: "portrait" }],
        },
      },
      {
        actor: "silent",
        art: { slots: [{ slot: "portrait", state: "shown", image: "secret" }] },
      },
    ],
  };
  stage.update(scene);
  const mode = stage.node.querySelector('[name="stage-mode"]');
  mode.value = "vn";
  mode.dispatchEvent({ type: "change" });
  const click = (t) => byText(stage.node, "button", t)[0].click();
  click("Edit cue");
  stage.node.querySelector('[name="Cue note"]').value = "discard";
  click("Cancel cue");
  assert.doesNotMatch(stage.node.textContent, /discard/);
  click("Edit cue");
  const art = stage.node.querySelector('[name="Art a"]');
  art.value = "expr.blue.neutral";
  art.dispatchEvent({ type: "change" });
  stage.node.querySelector('[name="Cue note"]').value = "authored";
  click("Save cue");
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(requests.includes("blue"));
  assert.ok(requests.includes("portrait"));
  assert.ok(!requests.includes("secret"));
  assert.match(stage.node.textContent, /Historical A/);
  assert.doesNotMatch(stage.node.textContent, /Renamed/);
  stage.update({ ...scene, cast: [] });
  assert.match(stage.node.textContent, /Art unavailable/);
  click("Reset cue");
  assert.doesNotMatch(stage.node.textContent, /authored/);
});

test("two historically heard outfit NPCs and a portrait-only NPC resolve authorized assets", async () => {
  install();
  const requested = [];
  const stage = makeStaging(
    {
      ui,
      styles() {},
      has() {
        return false;
      },
      signal: new AbortController().signal,
      picture(image) {
        requested.push(image);
        return Promise.resolve("/authorized/" + image);
      },
    },
    "three-npcs",
    { log: ui.el("section", {}, "canonical") },
  );
  document.body.append(stage.node);
  const npcs = [
    { actor: "npc:a", speaker: "Historical A", text: "A" },
    { actor: "npc:b", speaker: "Historical B", text: "B" },
    { actor: "npc:c", speaker: "Historical C", text: "C" },
  ];
  stage.update({
    turns: [{ turn: "kept-three", kept: { lines: npcs } }],
    cast: npcs.map((a, i) => ({
      actor: a.actor,
      name: "Current " + i,
      art: {
        slots: [
          {
            slot: i === 2 ? "portrait" : "expr.outfit" + i + ".neutral",
            state: "shown",
            image: "authorized-" + i,
          },
        ],
      },
    })),
  });
  const mode = stage.node.querySelector('[name="stage-mode"]');
  mode.value = "vn";
  mode.dispatchEvent({ type: "change" });
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(requested.sort(), [
    "authorized-0",
    "authorized-1",
    "authorized-2",
  ]);
  for (const npc of npcs)
    assert.ok(stage.node.textContent.includes(npc.speaker));
});
