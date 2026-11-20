// Story threads (081): find a thread of the story (a promise, a rumour, a secret) and follow it to how it ended.
// Address: #/story/<ChatRef>?audience=director&q=<words>&actor=<ref>&thread=<proposition id>
// The chat names the campaign, its cast and whose characters are the table's. Everything is read from the
// story.* ops (docs/STORY.md): the page keeps nothing, and every choice is in its address. Each entry is graded
// (canon, a belief, only proposed, revoked) so an interpretation is never read as truth, and leads back to the
// turn it was recorded at. What is said in words is logic.js.

import { AUDIENCES, NOTES, address, contradictionText, count, entryLine, grade, problemText, problemsOf, readAddress, sceneText,
  scenesWith, sourceOf, standingKind, standingText, summaryLine } from "./logic.js";

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("wide");
  ctx.setTitle("Story threads");
  const state = readAddress(ctx.path, ctx.query);
  const go = (change) => {
    const target = address(state, change);
    ctx.navigate(ctx.href("story", target.segments, target.query));
  };
  if (!state.chat) {
    root.append(ui.empty("Open the story threads from a campaign's hub, for one of its chats."),
      ctx.registered("campaign") ? ui.actions(ui.buttonLink("All campaigns", "campaign")) : null);
    return;
  }
  const campaign = state.chat.startsWith("campaign:") ? state.chat.slice(9).split("#")[0] : null;
  if (campaign && ctx.registered("campaign")) {
    ctx.setCrumb(ui.link("Campaign", "campaign", [campaign], { chat: state.chat,
      audience: state.audience === "director" ? "director" : undefined }));
  }

  const args = { chat: state.chat, audience: state.audience };
  if (state.actor) args.actor = state.actor;
  const listing = await ctx.api("story.threads", { ...args, query: state.q || undefined });
  let opened = null;
  let missing = false;
  if (state.thread) {
    try {
      opened = await ctx.api("story.thread", { ...args, proposition: state.thread });
    } catch (error) {
      if (error.reason !== "no_thread") throw error;
      missing = true;
    }
  }

  // -- what to look for ----------------------------------------------------------------------------------
  const views = ui.segments("Whose view", AUDIENCES, { value: state.audience, onchange: (value) => go({ audience: value, thread: null }) });
  const search = ui.search("Search the story", { value: state.q || "", placeholder: "Words of a promise, or of how it ended" });
  search.setAttribute("data-focus-key", "story:search");
  search.setAttribute("maxlength", "200");
  const find = () => go({ q: search.value.trim() || null, thread: null });
  search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); find(); } });
  const who = ui.choice("Character", [{ value: "", label: "Everyone" },
    ...listing.cast.map((member) => ({ value: member.actor, label: member.name || member.actor }))],
  { value: state.actor || "", onchange: (value) => go({ actor: value || null }) });
  root.append(el("div", { class: "g-stack story-top" },
    el("div", { class: "g-row story-views" }, views.node, el("span", { class: "g-grow g-muted" }, NOTES[state.audience])),
    el("div", { class: "g-row story-find" }, search, ui.button("Find", find, { kind: "primary", size: "small" }), who.node,
      state.q || state.actor ? ui.button("Clear", () => go({ q: null, actor: null, thread: null }), { kind: "linklike", size: "small" }) : null)));
  for (const problem of problemsOf(listing, opened)) root.append(ui.notice(problemText(problem), { kind: "soft" }));

  // -- the threads that match ------------------------------------------------------------------------------
  const rows = listing.threads.map((item) => {
    const target = address(state, { thread: item.proposition });
    const row = el("a", { class: "story-row", href: ctx.href("story", target.segments, target.query) },
      ...ui.rowText(item.text, summaryLine(item)));
    if (item.proposition === state.thread) row.setAttribute("aria-current", "true");
    return row;
  });
  const said = state.q || state.actor ? "No thread of the story matches." : "Nothing has been recorded in this campaign's story yet.";
  const list = ui.section("Threads",
    ui.eyebrow(listing.total > listing.threads.length ? listing.threads.length + " of " + count(listing.total, "thread")
      : count(listing.total, "thread")),
    ui.list(rows, { empty: said }));
  list.setAttribute("aria-label", "Threads");
  const detail = el("div", { class: "g-stack story-detail" });
  root.append(el("div", { class: "story-columns" }, list, detail));

  if (missing) detail.append(ui.notice("That thread is not in this view of the story.", { kind: "soft" }));
  if (opened) showThread(detail, ctx, state, opened.thread);
  else if (!missing) detail.append(ui.empty("Choose a thread to follow it through the story."));
}

function showThread(root, ctx, state, thread) {
  const { ui } = ctx;
  const { el } = ui;
  const entries = thread.entries;
  const head = ui.section(thread.text);
  head.setAttribute("aria-label", "Thread");
  root.append(head);

  // how it ended: canon's standing and what decided it, or that this is the director's
  const outcome = thread.outcome;
  const ended = ui.section("How it ended");
  ended.setAttribute("aria-label", "How it ended");
  if (!outcome) {
    ended.append(el("p", { class: "g-muted" }, "Canon's ruling is the director's. This is what your characters know of it."));
  } else {
    const decided = outcome.ruling ? entries.find((entry) => entry.record === "ruling:" + outcome.ruling) : null;
    const where = decided ? sourceOf(decided) : null;
    ended.append(el("div", { class: "g-row" }, ui.pill(standingText(outcome.standing), { kind: standingKind(outcome.standing) }),
      where ? (where.link ? ui.link(where.text, where.link.route, where.link.segments, where.link.query) : el("span", { class: "g-muted" }, where.text)) : null));
    const grounds = outcome.grounds.map((ground) => ground.entry
      ? el("li", {}, el("span", {}, ground.entry.text), outcome.revoked_grounds.includes(ground.subject.split(":").slice(1).join(":"))
        ? ui.pill("Revoked", { kind: "bad" }) : null)
      : el("li", { class: "g-muted" }, ground.subject));
    if (grounds.length) ended.append(el("p", { class: "g-muted" }, "It rests on:"), el("ul", { class: "story-grounds" }, grounds));
  }
  root.append(ended);

  if (thread.contradictions.length) {
    const against = ui.section("Contradictions",
      el("ul", { class: "story-contradictions" }, thread.contradictions.map((item) => el("li", {}, contradictionText(item, entries)))));
    against.setAttribute("aria-label", "Contradictions");
    root.append(against);
  }

  const scenes = scenesWith(thread.scenes, state.actor);
  const named = state.actor ? (thread.actors.find((actor) => actor.ref === state.actor) || {}).name : null;
  const places = ui.section(state.actor ? "Scenes with " + (named || "this character") : "Scenes",
    ui.list(scenes.map((scene) => el("span", {}, ...ui.rowText(sceneText(scene, entries, state.chat),
      scene.actors.map((actor) => actor.name || actor.ref).join(", ")))), { empty: "This character is in no scene of this thread." }));
  places.setAttribute("aria-label", "Scenes");
  root.append(places);

  const items = entries.map((entry) => {
    const [label, kind] = grade(entry);
    const source = sourceOf(entry);
    return el("li", { class: "story-entry story-" + entry.grade, "data-record": entry.record },
      el("div", { class: "g-row story-entry-head" }, ui.pill(label, { kind }), el("span", { class: "story-line" }, entryLine(entry)),
        source.link ? ui.link(source.text, source.link.route, source.link.segments, source.link.query) : el("span", { class: "g-muted" }, source.text)),
      ["event", "stated", "observation"].includes(entry.kind) ? el("p", { class: "story-text" }, entry.text) : null);
  });
  const through = ui.section("Through the story", el("ol", { class: "story-entries" }, items));
  through.setAttribute("aria-label", "Through the story");
  root.append(through);
}
