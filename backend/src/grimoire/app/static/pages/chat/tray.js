// The waiting tray: one slim row above the composer when the inbox has something open for this chat (the feed
// asked for this chat alone, pages/inbox/feed.js). It shows the first item (its kind and title), its quick
// actions, and "+N more waiting" linking to the inbox. The tray keeps no list and decides nothing of its own: a
// quick action is the item's own op with its own arguments, made as the inbox makes it, and an item that must be
// read in full first (a change's operations, a labelling's factors) links to the inbox instead.
//
// A fork, a conflict on a turn and a reply that did not arrive are left out: the chat already says each of
// those, with its ways on, in its own bars and transcript.

import { actionButton, itemHref, loadFeed, perform, served } from "../inbox/feed.js";

const OWN = new Set(["fork", "conflict", "reply"]);

// `run(work)` is the chat page's way to run one action, `reopen()` reads the chat again after one, and
// `redraw()` draws the chat again when the tray's items change. `view(scene)` is the row to show, or null.
export function makeTray(ctx, chat, { run, reopen, redraw }) {
  if (!served(ctx)) return { view: () => null };
  ctx.styles("tray.css");
  const { ui } = ctx;
  const { el } = ui;
  let items = [];
  let read = null;  // what the chat looked like when the feed was last asked: asked again when that changes

  const load = async () => {
    let found = [];
    try { found = (await loadFeed(ctx, { chat })).items.filter((item) => !OWN.has(item.kind)); }
    catch (error) { if (error && error.reason === "left") return; }  // a tray that cannot be read shows nothing
    const changed = JSON.stringify(found.map((item) => item.id)) !== JSON.stringify(items.map((item) => item.id));
    items = found;
    if (changed) redraw();
  };

  const decide = (item, action) => run(async () => {
    const outcome = await perform(ctx, item, action);
    if (!outcome) return;
    if (outcome.decided) items = items.filter((other) => other.id !== item.id);
    else ctx.notify(outcome.text, { bad: true });
    read = null;
    await reopen();
  });

  function row() {
    const [item] = items;
    const quick = item.actions.filter((action) => action.quick);
    const ways = quick.length ? quick.map((action, index) => actionButton(ctx, action, index, () => decide(item, action), { size: "small" }))
      : [el("a", { class: "g-button g-primary g-small", href: itemHref(ctx, item.id, item.where && item.where.chat) }, "Decide")];
    return el("div", { class: "chat-tray", role: "group", "aria-label": "Waiting on you" },
      el("span", { class: "g-pill chat-tray-kind" }, item.label),
      el("span", { class: "chat-tray-title", title: item.title }, item.title), ways,
      items.length > 1 ? el("a", { class: "chat-tray-more", href: itemHref(ctx) }, "+" + (items.length - 1) + " more waiting") : null);
  }

  return {
    view(scene) {
      const now = scene.turns.length + ":" + (scene.state.open_turn || "") + ":" + scene.state.waiting.join(",");
      if (now !== read) { read = now; load(); }
      return items.length ? row() : null;
    },
  };
}
