// The shared turn (058, Sprint 115): two or more people at this one screen each draft their own character's part
// of one turn, see whose part is whose, and post the turn once, as reviewed. Shown above the composer only when
// the chat has more than one character a person plays, or this device was started for more than one player.
//
// - The person at the keyboard is picked first. Only their own characters' unsent parts are shown, each labelled
//   with its character and its player; each is a draft of this tab (ctx.draft), keyed by chat, character and
//   player, so leaving the chat keeps it and a character handed to someone else never shows the last player's words.
// - "Hold for the turn" gives the part to 058's drafts (the core `draft` op, with `by`): the app holds them in
//   memory until the turn is posted, and the panel says so. The parts held are listed in posting order, each with
//   its character and who wrote it; another player's words are not shown to whoever is at the keyboard.
// - "Review the turn" reads table.review; "Post this turn" posts exactly the parts reviewed (table.submit): a part
//   changed since refuses `changed`, and a retry after a lost answer posts nothing twice.
// - The owner hands a character to a player at this device, or back to the director, with the core `seat` op,
//   after saying exactly what it changes.
// A combined turn takes no label, framing or effect: nothing here offers one.

import { draftsKey, modeLabel, partKey, partsOf, playerLabel, postBlocker, reviewLines, reviewed, seatOptions, sharedTable } from "./table-logic.js";

// who was at the keyboard in each chat, for coming back to it
const keyboards = new Map();
const MODES = ["auto", "speech", "action"];

// `run(work)` is the chat page's way to run one action; `posted(scene)` draws the chat after a turn was posted
// (and asks for the answer); `redraw(scene)` draws it after anything else changed.
export function makeTable(ctx, chat, { run, posted, redraw }) {
  const served = ["table.review", "table.submit", "draft", "drop_draft"].every((op) => ctx.has(op));
  if (!served) return { node: null, update() {}, load: async () => {} };
  ctx.styles("table.css");
  const { ui } = ctx;
  const { el } = ui;
  let scene = null;
  let table = null;  // the last table.review answer: who is at this device, the drafts and their revision
  let review = null;  // the review on show: table.review's answer, posted only while its drafts are the scene's
  let asked = 0;  // generation of table.review requests: an answer to an older one is never drawn
  let player = keyboards.get(chat) || null;
  const editors = new Map();

  const summary = el("summary", { class: "chat-table-summary" });
  const note = el("p", { class: "g-muted chat-table-note" },
    "Each person drafts their own character's part, then the turn is posted once and the table answers once. "
    + "Parts held for the turn are kept by this app in memory until it is posted: restarting the app loses them. "
    + "What you type and do not hold is kept in this browser tab.");
  const keyboard = el("div", { class: "chat-table-keyboard" });
  const mine = el("div", { class: "g-stack chat-table-mine", role: "group", "aria-label": "Your parts" });
  const order = el("div", { class: "chat-table-order" });
  const reviewSlot = el("div", {});
  const seating = el("div", {});
  const node = el("details", { class: "chat-table", "aria-label": "Shared turn" }, summary,
    el("div", { class: "g-stack chat-table-body" }, note, keyboard, mine, order, reviewSlot, seating));
  node.hidden = true;

  async function load() {
    const turn = ++asked;
    try {
      const answer = await ctx.api("table.review", { chat });
      if (turn !== asked || ctx.signal.aborted) return null;
      table = answer;
      if (scene) draw();
      return answer;
    } catch (error) {
      if (error && error.reason === "left") return null;
      throw error;
    }
  }

  function players() { return table ? table.players : []; }
  function owner() { return table ? table.owner : (ctx.library && ctx.library.owner); }

  function update(next) {
    scene = next;
    // a review whose drafts are no longer the ones held is stale: it is never posted
    if (review && draftsKey(review.drafts) !== draftsKey(scene.drafts)) review = null;
    draw();
  }

  function draw() {
    if (!scene || !table) { node.hidden = true; return; }
    node.hidden = !sharedTable(scene, players());
    if (node.hidden) return;
    if (scene.drafts.length) node.open = true;
    summary.replaceChildren("Shared turn", scene.drafts.length ? " · " + scene.drafts.length + " part" + (scene.drafts.length === 1 ? "" : "s") + " held" : "");
    const seated = players().filter((who) => who === owner() || partsOf(scene, who).length);
    if (!player || !seated.includes(player)) player = seated[0] || owner();
    keyboards.set(chat, player);
    const choose = ui.segments("Who is at the keyboard", seated.map((who) => ({ value: who, label: who === owner() ? "You" : playerLabel(who, owner()) })),
      { value: player, onchange: (value) => { player = value; keyboards.set(chat, value); draw(); } });
    keyboard.replaceChildren(el("span", { class: "chat-table-label" }, "At the keyboard"), choose.node);
    drawMine();
    drawOrder();
    drawReview();
    drawSeating();
  }

  // -- the parts of the player at the keyboard ------------------------------------------------------------------
  function editor(member) {
    const key = partKey(chat, member.actor, player);
    if (editors.has(key)) return editors.get(key);
    const who = player;
    const field = ui.field(member.name + "'s part, by " + playerLabel(who, owner()), { draft: ctx.draft(key), multiline: true,
      hint: "Speech in quotes, *actions in asterisks*." });
    field.input.setAttribute("maxlength", String(table.limits.chars));
    const modeDraft = ctx.draft(key + ":mode");
    const mode = ui.choice("How to read " + member.name + "'s part", MODES.map((value) => ({ value, label: modeLabel(value) })),
      { value: modeDraft.get() || "auto", onchange: (value) => modeDraft.set(value === "auto" ? "" : value) });
    const hold = ui.button("Hold for the turn", () => run(async () => {
      const sent = field.value;
      if (!sent.trim()) { field.setError("Write the part first."); return; }
      field.setError("");
      const answer = await ctx.api("draft", { chat, actor: member.actor, by: who, text: sent, mode: mode.value });
      // held: the unsent words are done with, unless more was typed while the request was on its way
      if (field.value === sent) field.clear();
      redraw(answer);
    }), { kind: "primary", size: "small" });
    const edit = ui.button("Edit the held part", () => {
      const held = scene.drafts.find((item) => item.actor === member.actor && item.by === who);
      if (held && !field.value.trim()) { field.value = held.text; field.input.focus(); }
    }, { size: "small" });
    const drop = ui.button("Take it out of the turn", () => run(async () => {
      redraw(await ctx.api("drop_draft", { chat, actor: member.actor, by: who }));
    }), { size: "small" });
    const status = el("span", { class: "g-muted chat-table-status" });
    const box = el("div", { class: "g-stack chat-table-part", role: "group", "aria-label": member.name + "'s part" },
      field.node, el("div", { class: "g-row chat-table-actions" }, mode.node, hold, edit, drop, status));
    const made = { key, box, field, edit, drop, status, actor: member.actor, by: who };
    editors.set(key, made);
    return made;
  }

  function drawMine() {
    const parts = partsOf(scene, player);
    if (!parts.length) {
      mine.replaceChildren(ui.empty(playerLabel(player, owner()) === "you" ? "You play no character in this chat." : playerLabel(player, owner()) + " plays no character in this chat yet."));
      return;
    }
    const shown = parts.map(editor);
    for (const item of shown) {
      const held = scene.drafts.find((draft) => draft.actor === item.actor && draft.by === item.by);
      item.edit.hidden = !held;
      item.drop.hidden = !held;
      item.status.textContent = held ? "Held for the turn. Holding again replaces it, in its place." : "Not held yet.";
    }
    mine.replaceChildren(...shown.map((item) => item.box));
  }

  // -- the parts held, in posting order -------------------------------------------------------------------------
  function drawOrder() {
    const drafts = scene.drafts;
    if (!drafts.length) { order.replaceChildren(el("p", { class: "g-muted" }, "No part is held for this turn yet.")); return; }
    const rows = drafts.map((item, index) => {
      const own = item.by === player;
      const move = (at) => () => run(async () => redraw(await ctx.api("draft", { chat, actor: item.actor, by: item.by, at })));
      const up = own && index > 0 ? ui.button("Move up", move(index - 1), { size: "small" }) : null;
      const down = own && index < drafts.length - 1 ? ui.button("Move down", move(index + 1), { size: "small" }) : null;
      if (up) up.setAttribute("aria-label", "Move " + item.name + "'s part up");
      if (down) down.setAttribute("aria-label", "Move " + item.name + "'s part down");
      const member = scene.cast.find((cast) => cast.actor === item.actor);
      const theirs = member && member.role === "player" && member.controller === item.by;
      return el("li", { class: "chat-table-held" },
        el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, (index + 1) + ". " + item.name),
          el("span", { class: "g-muted" }, "by " + playerLabel(item.by, owner()) + " · " + modeLabel(item.mode)),
          theirs ? null : ui.pill("No longer theirs", { kind: "bad" })),
        own ? el("p", { class: "chat-table-text" }, item.text)
          : el("p", { class: "g-muted" }, "Held by " + playerLabel(item.by, owner()) + ". Their words show only while they are at the keyboard."),
        up || down ? el("div", { class: "g-row chat-table-actions" }, up, down) : null);
    });
    const blocked = postBlocker(scene);
    order.replaceChildren(el("h3", { class: "chat-table-heading" }, "This turn, in order"), el("ol", { class: "chat-table-list" }, rows),
      review ? null : blocked ? el("p", { class: "g-muted" }, blocked)
        : ui.actions(ui.button("Review the turn", () => run(startReview), { kind: "primary" })));
  }

  async function startReview() {
    const answer = await load();
    if (!answer) return;
    if (draftsKey(answer.drafts) !== draftsKey(scene.drafts)) {  // held parts moved on while it was read: read again
      const again = await ctx.api("open", { chat });
      redraw(again);
      return;
    }
    if (!answer.drafts.length) { ctx.notify("No part is held for this turn.", { bad: true }); return; }
    review = answer;
    draw();
  }

  function drawReview() {
    if (!review) { reviewSlot.replaceChildren(); return; }
    const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "chat-table-heading" }, "Review this turn"),
      el("p", {}, "Post " + review.drafts.length + " part" + (review.drafts.length === 1 ? "" : "s") + " as one turn, in this order:"),
      el("ul", { class: "chat-table-list" }, reviewLines(review).map((line) => el("li", {}, line))),
      el("p", { class: "g-muted" }, "The table then answers once. A turn of several characters takes no label, framing or effect."),
      ui.actions(ui.button("Cancel", () => { review = null; draw(); }), ui.button("Post this turn", () => run(commit), { kind: "primary" })));
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Review this turn");
    reviewSlot.replaceChildren(box);
  }

  async function commit() {
    const shown = review;
    try {
      const answer = await ctx.api("table.submit", { chat, expected: shown.etag, reviewed: reviewed(shown), head: shown.head });
      review = null;
      table = answer.review;
      if (answer.done.already_applied) ctx.notify("That turn was already posted; nothing was posted again.");
      await posted(answer.scene);
    } catch (error) {
      if (error && error.reason === "changed") {  // a part changed after the review: read the chat again
        review = null;
        try { redraw(await ctx.api("open", { chat })); } catch (again) { if (again && again.reason === "left") throw again; }
      }
      throw error;
    }
  }

  // -- who plays whom -------------------------------------------------------------------------------------------
  function drawSeating() {
    if (!ctx.has("seat")) { seating.replaceChildren(); return; }
    const others = scene.cast.filter((member) => !member.persona);
    if (!others.length) { seating.replaceChildren(); return; }
    const options = seatOptions(players(), owner());
    const rows = others.map((member) => {
      const now = member.role === "player" ? member.controller : member.role === "npc" ? "director" : "";
      const pick = ui.choice("Who plays " + member.name, options, { value: now });
      const hand = ui.button("Hand over", () => run(async () => {
        const to = pick.value;
        if (!to || to === now) return;
        const label = to === "director" ? "the director, as an NPC" : to === owner() ? "you" : playerLabel(to, owner());
        const sure = await ui.confirm("Hand " + member.name + " to " + label + "? From then on only " + label
          + " posts for " + member.name + ". The change is recorded in the library's control history.", { confirmLabel: "Hand over" });
        if (!sure) return;
        redraw(await ctx.api("seat", { chat, actor: member.actor, player: to }));
      }), { size: "small" });
      return el("li", { class: "g-row chat-table-seat" }, pick.node, hand);
    });
    seating.replaceChildren(el("details", { class: "chat-table-seating" }, el("summary", {}, "Who plays whom"),
      el("p", { class: "g-muted" }, "The library owner hands a character to someone at this device, or back to the director. A character whose part is still in an open turn keeps its player until that turn is kept or passed."),
      el("ul", { class: "chat-table-list" }, rows)));
  }

  return { node, update, load };
}
