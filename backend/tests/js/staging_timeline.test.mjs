// 072: the VN stage's authored cue timeline, multi-character blocking and transitions, and the slice's
// proving scenario (two outfits and a portrait-only NPC, with focus and historical visibility; the
// transcript is never touched).
import { test } from "node:test";
import assert from "node:assert/strict";
import { install, byText } from "./fakedom.mjs";
import * as ui from "../../src/grimoire/app/static/shell/ui.js";
import { cueStore, timeline } from "../../src/grimoire/app/static/pages/chat/cues.js";
import { makeStaging } from "../../src/grimoire/app/static/pages/chat/staging.js";

const memory = (initial = {}) => {
  const data = { ...initial };
  return {
    data,
    get length() { return Object.keys(data).length; },
    key: (i) => Object.keys(data)[i] ?? null,
    getItem: (k) => data[k] ?? null,
    setItem: (k, v) => { data[k] = v; },
    removeItem: (k) => { delete data[k]; },
  };
};
const K = (chat, turn) => "grimoire.vn.cue:" + chat + "\u001f" + turn;
const turnsIn = (store, chat) => Object.keys(store.data).filter((k) => k.startsWith("grimoire.vn.cue:" + chat + "\u001f")).map((k) => k.split("\u001f")[1]).sort();
const line = (actor, speaker, text) => ({ actor, speaker, text });
const kept = (turn, number, lines, present, post = null) => ({ turn, number, post, kept: { lines, present } });
const who = (...pairs) => pairs.map(([actor, name]) => ({ actor, name }));

test("blocking carries forward, unblocked speakers take free places, and absences end blocking", () => {
  const turns = [
    kept("t1", 1, [line("s", "Seraphine", "Hello."), line("m", "Mara", "Evening.")], who(["s", "Seraphine"], ["m", "Mara"], ["w", "Winifred"])),
    kept("t2", 2, [line("s", "Seraphine", "Again.")], who(["s", "Seraphine"], ["m", "Mara"], ["w", "Winifred"])),
    kept("t3", 3, [line("s", "Seraphine", "Alone now.")], who(["s", "Seraphine"])),
  ];
  const auto = timeline(turns, {});
  // two speakers never share a place while one is free
  assert.deepEqual(auto[0].actors.map((a) => [a.actor, a.position]), [["s", "center"], ["m", "left"]]);
  assert.deepEqual(auto[1].actors.map((a) => [a.actor, a.position]), [["s", "center"], ["m", "left"]], "Mara stays though silent");
  assert.deepEqual(auto[2].actors.map((a) => a.actor), ["s"], "Mara was not present at turn 3");
  assert.ok(auto[2].directions.includes("Mara is no longer on stage."));
  // a cue at t1 moves Seraphine; t2 inherits it; a cue at t2 moves her again
  const cues = {
    t1: { actors: { s: { position: "right", art: "" } }, focus: "auto", background: "inherit", transition: "none", note: "" },
    t2: { actors: { s: { position: "left", art: "" }, m: { position: "right", art: "" } }, focus: "m", background: "inherit", transition: "move", note: "" },
  };
  const authored = timeline(turns, cues);
  assert.equal(authored[0].actors.find((a) => a.actor === "s").position, "right");
  const second = authored[1];
  assert.deepEqual(second.actors.map((a) => [a.actor, a.from, a.position]), [["s", "right", "left"], ["m", "center", "right"]]);
  assert.deepEqual(second.focus, { actor: "m", source: "authored" });
  assert.equal(second.transition, "move");
  assert.ok(second.directions.includes("Seraphine moves from stage right to stage left."));
  assert.equal(authored[2].actors[0].position, "left", "blocking outlives the cue's own turn");
  assert.equal(authored[2].transition, "none", "a transition belongs to its own turn");
});

test("only historically present actors are staged; authored references to others are named, not staged", () => {
  const turns = [
    kept("t1", 1, [line("s", "Seraphine", "Who is there?")], who(["s", "Seraphine"])),
    kept("t2", 2, [line("w", "Winifred", "Only me.")], who(["s", "Seraphine"], ["w", "Winifred"])),
  ];
  const cues = {
    t1: { actors: { w: { position: "left", art: "" } }, focus: "w", background: "inherit", transition: "none", note: "" },
  };
  const [first, second] = timeline(turns, cues);
  assert.deepEqual(first.actors.map((a) => a.actor), ["s"], "Winifred had not arrived at turn 1");
  assert.ok(first.unavailable.includes("Winifred was not present at this turn"), "named by the label she spoke under");
  assert.ok(first.unavailable.some((u) => u.startsWith("focus on")));
  assert.equal(first.focus.actor, null);
  assert.deepEqual(second.actors.map((a) => a.actor).sort(), ["s", "w"]);
  assert.ok(second.directions.includes("Winifred enters stage left."));
  // a payload with no `present` (an older build) offers only the speakers
  const [legacy] = timeline([{ turn: "x", kept: { lines: [line("a", "A", "hi")] } }], {});
  assert.deepEqual(legacy.actors.map((a) => a.actor), ["a"]);
});

test("on a 041 stage an NPC the table did not hear keeps their blocking, unseen, and no departure is claimed", () => {
  const staged = (turn, number, lines, present) => ({ turn, number, post: null, kept: { lines, present, staged: true } });
  const turns = [
    staged("t1", 1, [line("p", "Anya", "Hello?"), line("m", "Mara", "Here.")], who(["p", "Anya"], ["m", "Mara"])),
    staged("t2", 2, [line("p", "Anya", "Mara?")], who(["p", "Anya"])),
    staged("t3", 3, [line("m", "Mara", "Still here.")], who(["p", "Anya"], ["m", "Mara"])),
  ];
  const cues = { t1: { actors: { m: { position: "right", art: "expr.court.x" } }, focus: "auto", background: "inherit", transition: "none", note: "" } };
  const [a, b, c] = timeline(turns, cues);
  assert.deepEqual(a.actors.map((x) => [x.actor, x.position]), [["p", "center"], ["m", "right"]]);
  assert.deepEqual(b.actors.map((x) => x.actor), ["p"], "unheard: not drawn");
  assert.ok(!b.directions.some((d) => d.includes("no longer")), "nothing says she left");
  assert.deepEqual(c.actors.find((x) => x.actor === "m"), { actor: "m", name: "Mara", speaking: true, position: "right", art: "expr.court.x", from: null, entered: true },
    "heard again, she is back at her authored place in her authored outfit");
  // an unstaged take whose cast no longer holds her does end her blocking
  const [, gone] = timeline([turns[0], { ...turns[1], kept: { ...turns[1].kept, staged: false } }], cues);
  assert.ok(gone.directions.includes("Mara is no longer on stage."));
});

test("off stage carries forward until a cue brings the actor back", () => {
  const turns = [1, 2, 3].map((n) => kept("t" + n, n, [line("m", "Mara", "Line " + n)], who(["m", "Mara"])));
  const off = { actors: { m: { position: "off", art: "" } }, focus: "auto", background: "inherit", transition: "none", note: "" };
  const back = { actors: { m: { position: "left", art: "" } }, focus: "auto", background: "inherit", transition: "none", note: "" };
  const [a, b, c] = timeline(turns, { t1: off, t3: back });
  assert.deepEqual([a.actors, b.actors].map((x) => x.length), [0, 0]);
  assert.ok(b.directions.includes("Mara speaks from off stage."));
  assert.deepEqual(c.actors.map((x) => [x.position, x.entered]), [["left", true]]);
});

test("off stage, focus defaults, background inheritance and outfit changes are written as directions", () => {
  const turns = [
    kept("t1", 1, [line("s", "Seraphine", "Listen."), line("m", "Mara", "I am.")], who(["s", "Seraphine"], ["m", "Mara"])),
    kept("t2", 2, [line("m", "Mara", "From the hall.")], who(["s", "Seraphine"], ["m", "Mara"])),
    kept("t3", 3, [line("s", "Seraphine", "Changed.")], who(["s", "Seraphine"], ["m", "Mara"])),
  ];
  const cues = {
    t1: { actors: { s: { position: "left", art: "expr.travel.neutral" } }, focus: "none", background: "media:hall", transition: "fade", note: "Dusk" },
    t2: { actors: { m: { position: "off", art: "" } }, focus: "auto", background: "inherit", transition: "none", note: "" },
    t3: { actors: { s: { position: "", art: "expr.court.happy" } }, focus: "auto", background: "", transition: "none", note: "" },
  };
  const [a, b, c] = timeline(turns, cues);
  assert.deepEqual(a.focus, { actor: null, source: "none" });
  assert.equal(a.background, "media:hall");
  assert.equal(b.background, "media:hall", "an unchanged background carries");
  assert.deepEqual(b.actors.map((x) => x.actor), ["s"]);
  assert.ok(b.directions.includes("Mara steps off stage."));
  assert.ok(b.directions.includes("Mara speaks from off stage."));
  assert.equal(b.focus.actor, null, "automatic focus needs a staged speaker");
  assert.equal(c.background, "");
  assert.ok(c.directions.includes("The background clears."));
  assert.equal(c.actors[0].art, "expr.court.happy");
  assert.equal(c.actors[0].position, "left", "an art-only change keeps the place");
  assert.ok(c.directions.includes("Seraphine appears in the court outfit."));
  assert.deepEqual(c.focus, { actor: "s", source: "automatic" });
});

test("two tabs saving different turns, even interleaved, both keep their cues; unreadable cues are kept", () => {
  const shared = memory();
  const cue = (note) => ({ actors: {}, focus: "auto", background: "inherit", transition: "none", note });
  // Both tabs read the store before either writes: each save writes only its own turn.
  const tabA = cueStore("c", shared, null), tabB = cueStore("c", shared, null);
  assert.equal(tabA.put("t1", cue("from A")), true);
  assert.equal(tabB.put("t2", cue("from B")), true);
  assert.deepEqual(turnsIn(shared, "c"), ["t1", "t2"]);
  assert.equal(tabA.remove("t2"), true);
  tabB.reload();
  assert.deepEqual(Object.keys(tabB.cues), ["t1"]);
  // a newer build's cue: kept and reported; saving over it is refused; Reset local cues removes it
  shared.data[K("c", "t9")] = JSON.stringify({ version: 3, cue: { anything: true } });
  const old = cueStore("c", shared, null);
  assert.match(old.warning, /newer version/);
  assert.equal(old.put("t9", cue("x")), false);
  assert.equal(JSON.parse(shared.data[K("c", "t9")]).version, 3);
  assert.equal(old.put("t3", cue("other turns still save")), true);
  assert.equal(old.reset(), true);
  assert.deepEqual(turnsIn(shared, "c"), []);
  shared.data[K("c", "t1")] = "null";
  assert.match(cueStore("c", shared, null).warning, /malformed/i);
});

test("a change kept only in memory while storage is full survives reloads and the next successful save", () => {
  const shared = memory();
  let full = true;
  const store = cueStore("c", { get length() { return shared.length; }, key: shared.key, getItem: shared.getItem, removeItem: shared.removeItem,
    setItem: (k, v) => { if (full) throw Error("quota"); shared.setItem(k, v); } }, null);
  const cue = (note) => ({ actors: {}, focus: "auto", background: "inherit", transition: "none", note });
  assert.equal(store.put("t1", cue("first")), "unsaved");
  assert.match(store.warning, /remain in memory/);
  shared.data[K("c", "t5")] = JSON.stringify({ version: 2, cue: cue("another tab") });
  store.reload(); // another tab's change arrives: the pending cue and its warning stay
  assert.deepEqual(Object.keys(store.cues).sort(), ["t1", "t5"]);
  assert.match(store.warning, /remain in memory/);
  full = false;
  assert.equal(store.put("t2", cue("second")), true);
  assert.deepEqual(turnsIn(shared, "c"), ["t1", "t2", "t5"]);
  assert.equal(store.warning, "");
  assert.equal(store.put("t1", { ...cue("x"), transition: "spin" }), false, "an invalid cue is refused outright");
});

test("an art-only change keeps an off-stage actor off; unheard actors hold no place for new speakers", () => {
  const turns = [1, 2].map((n) => kept("t" + n, n, [line("m", "Mara", "Line " + n)], who(["m", "Mara"])));
  const cue = (actors) => ({ actors, focus: "auto", background: "inherit", transition: "none", note: "" });
  const [, artOnly] = timeline(turns, { t1: cue({ m: { position: "off", art: "" } }), t2: cue({ m: { position: "", art: "expr.court.x" } }) });
  assert.deepEqual(artOnly.actors, []);
  assert.deepEqual(artOnly.carried, { m: { position: "off", art: "" } });
  const staged = (turn, number, lines, present) => ({ turn, number, post: null, kept: { lines, present, staged: true } });
  const [, second] = timeline([
    staged("t1", 1, [line("p", "P", "1"), line("a", "A", "2"), line("b", "B", "3")], who(["p", "P"], ["a", "A"], ["b", "B"])),
    staged("t2", 2, [line("p", "P", "4"), line("d", "D", "5")], who(["p", "P"], ["d", "D"])),
  ], {});
  assert.deepEqual(second.actors.map((x) => [x.actor, x.position]), [["p", "center"], ["d", "left"]]);
});

test("#694's per-tab cues move once into the device-local store, keeping what they meant", () => {
  const session = memory({
    "grimoire.vn.v1:c": JSON.stringify({ version: 1, cues: { t1: { actors: { a: { position: "left", art: "" } }, focus: "", background: "", transition: "fade", note: "n" } } }),
  });
  const local = memory();
  const store = cueStore("c", local, session);
  assert.equal(store.warning, "");
  assert.equal(store.cues.t1.focus, "none", "an empty v1 focus meant no focus");
  assert.equal(store.cues.t1.background, "", "an empty v1 background meant none, not inherit");
  assert.equal(JSON.parse(local.data[K("c", "t1")]).version, 2);
  const migrated = store.cues.t1;
  // carried over once: after Reset local cues they do not come back
  assert.equal(store.reset(), true);
  assert.deepEqual(Object.keys(cueStore("c", local, session).cues), []);
  // a migrated cue still stages its own turn only: the next turn is back to what came before
  const turns = [1, 2].map((n) => kept("t" + n, n, [line("a", "A", "Line " + n)], who(["a", "A"])));
  const [first, second] = timeline(turns, { t1: { ...migrated, background: "media:hall" } });
  assert.equal(first.actors[0].position, "left");
  assert.equal(first.background, "media:hall");
  assert.equal(second.actors[0].position, "center", "the v1 cue's blocking did not carry");
  assert.equal(second.background, "");
});

// The proving scenario. Seraphine wears two outfits across the scene; Winifred is a portrait-only NPC who
// was present (silent) at the first turn and gone by the third; Mara's private aside stays private; a
// hidden sprite is never requested; switching to the transcript changes no canonical text, and the stage
// never calls the library.
test("proving scenario: two outfits and a portrait-only NPC with focus and historical visibility; transcript untouched", async () => {
  install();
  globalThis.localStorage = memory();
  const requested = [];
  const transcript = ui.el("section", {}, ui.el("p", {}, "Seraphine: The gate is shut."), ui.el("p", {}, "Winifred: I have the key."));
  const before = transcript.textContent;
  const ctx = {
    ui,
    styles() {},
    has() { return false; },
    signal: new AbortController().signal,
    picture(image) { requested.push(image); return Promise.resolve("/authorized/" + image); },
    call() { throw Error("the VN stage must not call the library"); },
  };
  const stage = makeStaging(ctx, "proof-chat", { log: transcript });
  document.body.append(transcript, stage.node);
  // Winifred has left the cast since, so the scene has no name for her now: she is labelled by her own line.
  const present = who(["char:seraphine", "Seraphine"], ["char:mara", "Mara"], ["npc:winifred", null]);
  const scene = {
    turns: [
      kept("t1", 1, [line("char:seraphine", "Seraphine", "The gate is shut.")], present,
        { actor: "char:mara", speaker: "Mara", text: "(I can pick it.)", private: true, to: [] }),
      kept("t2", 2, [line("npc:winifred", "Winifred", "I have the key.")], present),
      kept("t3", 3, [line("char:seraphine", "Seraphine", "She is gone.")], who(["char:seraphine", "Seraphine"], ["char:mara", "Mara"])),
    ],
    cast: [
      { actor: "char:seraphine", art: { slots: [
        { slot: "expr.travel.neutral", state: "shown", image: "img:travel" },
        { slot: "expr.court.neutral", state: "shown", image: "img:court" },
        { slot: "expr.secret.neutral", state: "hidden", image: "img:hidden" },
      ] } },
      { actor: "char:mara", art: { slots: [] } },
      // Winifred is no longer in the cast now; her art is whatever the cast still shows (none)
    ],
  };
  const frozen = JSON.stringify(scene);
  stage.update(scene);
  const mode = stage.node.querySelector('[name="stage-mode"]');
  mode.value = "vn";
  mode.dispatchEvent({ type: "change" });
  const click = (t) => byText(stage.node, "button", t)[0].click();
  const pick = (name, value) => {
    const field = stage.node.querySelector(`[name="${name}"]`);
    field.value = value;
    field.dispatchEvent({ type: "change" });
  };
  const figures = () => stage.node.querySelectorAll("figure").map((f) => [f.getAttribute("data-actor"), f.className, f.textContent]);
  // Author turn 1: Seraphine in travel clothes on the left, silent Winifred on the right in focus, Mara stays off.
  pick("Kept turn", "t1");
  click("Edit cue");
  pick("Position char:seraphine", "left");
  pick("Art char:seraphine", "expr.travel.neutral");
  pick("Position npc:winifred", "right");
  pick("Focus actor", "npc:winifred");
  stage.node.querySelector('[name="Cue note"]').value = "Night at the gate";
  click("Save cue");
  let staged = figures();
  const at = (id) => staged.find(([a]) => a === id) || [id, "", ""];
  assert.deepEqual(staged.map(([a]) => a).sort(), ["char:mara", "char:seraphine", "npc:winifred"]);
  assert.match(at("char:seraphine")[1], /vn-left/);
  assert.match(at("char:seraphine")[2], /travel outfit/);
  assert.match(at("char:mara")[1], /vn-center/, "Mara, who posted, takes the free place");
  assert.match(at("char:mara")[2], /M · Initial fallback/);
  assert.match(at("npc:winifred")[1], /vn-right vn-focus/);
  assert.match(at("npc:winifred")[2], /Winifred · npc:winifred · focus \(authored\) · silent/);
  assert.match(at("npc:winifred")[2], /W · Initial fallback/, "the portrait-only NPC with no art now falls back to an initial");
  assert.match(stage.node.textContent, /Private\./, "Mara's aside is still marked private");
  // Turn 2: Seraphine changes into court clothes, the move slides her, focus follows Winifred automatically.
  click("Next turn");
  click("Edit cue");
  pick("Art char:seraphine", "expr.court.neutral");
  pick("Position char:seraphine", "center");
  pick("Transition", "move");
  click("Save cue");
  click("Previous turn");
  click("Next turn"); // arriving plays the cue's transition
  staged = figures();
  assert.match(at("char:seraphine")[1], /vn-center vn-move vn-shift-m1/, "slides in from the left column");
  assert.match(at("char:seraphine")[2], /court outfit/);
  assert.match(at("npc:winifred")[2], /focus \(automatic\)/);
  assert.match(stage.node.textContent, /Seraphine appears in the court outfit\./);
  stage.update(JSON.parse(frozen)); // a redraw of the same turn replays nothing
  staged = figures();
  assert.doesNotMatch(at("char:seraphine")[1], /vn-move/);
  const live = stage.node.querySelector(".vn-announce");
  assert.match(live.textContent, /^Turn 2\. .*Seraphine appears in the court outfit\./, "arriving is announced once");
  live.textContent = "";
  stage.update(JSON.parse(frozen));
  assert.equal(live.textContent, "", "a redraw announces nothing");
  // Turn 3: Winifred had left; the carried blocking does not keep her on stage.
  const next = byText(stage.node, "button", "Next turn")[0];
  next.focus();
  next.click();
  assert.equal(document.activeElement.getAttribute("name"), "Kept turn", "the last turn keeps focus in the turn controls");
  staged = figures();
  assert.deepEqual(staged.map(([a]) => a).sort(), ["char:mara", "char:seraphine"]);
  assert.match(stage.node.textContent, /Winifred is no longer on stage\./);
  assert.match(at("char:seraphine")[2], /court outfit/, "the outfit carries forward");
  // The timeline lists both cues and jumps back to the first.
  assert.equal(byText(stage.node, "button", "Go to turn").length, 2);
  click("Go to turn 1");
  assert.equal(stage.node.querySelector('[name="Kept turn"]').value, "t1");
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(requested.includes("img:travel") && requested.includes("img:court"));
  assert.ok(!requested.includes("img:hidden"), "a hidden sprite is never requested");
  // Back to the transcript: not one canonical character changed, and the scene payload is as it came.
  mode.value = "transcript";
  mode.dispatchEvent({ type: "change" });
  assert.equal(transcript.hidden, false);
  assert.equal(transcript.textContent, before);
  assert.equal(JSON.stringify(scene), frozen);
  assert.deepEqual(turnsIn(globalThis.localStorage, "proof-chat"), ["t1", "t2"]);
  const saved = JSON.parse(globalThis.localStorage.data[K("proof-chat", "t1")]).cue;
  assert.deepEqual(Object.keys(saved.actors).sort(), ["char:seraphine", "npc:winifred"], "untouched actors are not written");
  delete globalThis.localStorage;
});
