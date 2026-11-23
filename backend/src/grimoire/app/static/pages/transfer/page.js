// Copy this chat into another campaign (084, Sprint 116): #/transfer/<ChatRef>?audience=director. The director picks
// a target campaign, scans the copy (084's scan: the cast with each actor's reuse candidates, and namesakes shown but
// never offered; the sources; the history and what stands in its way), decides every actor (reuse a candidate, new
// or leave: nothing is matched by name), chooses the sources to carry and a label, and plans it: transfer.plan
// writes nothing and shows what would be copied, what is omitted and why, what stays in the origin and the files it
// would write, with the new chat's label and sources. "Copy as reviewed" commits that very plan (transfer.commit,
// with its operation id and review); a change in between (`changed`), or any other refusal, drops the review and the
// page scans again and says why; a call that got no answer keeps it (a retry is safe). The origin is never written.
// The table's view says whose this is and shows nothing. Transferring state or assets, a chronology mapping and
// missing art are named as not built, never offered. The decisions, sources and label being made are a draft of
// this tab (ctx.draft).

import { decisionOptions, leftText, newOperationId, noteText, omittedText, readDecisions, reasonText, requestOf, roleText, whereText } from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
// The shell's own reasons for a call that got no answer from the app (see shell/api.js).
const TRANSPORT = new Set(["network", "left", "bad_response"]);
const NOT_BUILT = { state: "Copying state and attached assets", chronology: "Mapping the copy onto the target's chronology", art: "Presenting missing art" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const chat = ctx.path[0];
  if (!chat) { root.append(ui.notice("Open this from a chat's Details."), ui.actions(ui.buttonLink("All chats", "chats"))); return; }
  ctx.setTitle("Copy into another campaign");
  ctx.setCrumb(ui.link("Chats", "chats"), " / ", ctx.registered("chat") ? ui.link("This chat", "chat", [chat]) : "This chat");
  const audience = ctx.query.get("audience") === "director" ? "director" : "table";
  const views = ui.segments("Whose view", AUDIENCES, { value: audience,
    onchange: (value) => ctx.navigate(ctx.href("transfer", [chat], { audience: value === "director" ? "director" : undefined })) });
  root.append(el("div", { class: "g-row transfer-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Copy this chat's history into a new chat in another campaign. This chat is never changed." : "Copying a chat is the director's.")));
  if (audience !== "director") {
    root.append(card(ui, "Copy into another campaign", el("p", { class: "g-muted" },
      "A copy shows every character of both campaigns and what the table never heard, so it is the director's. Switch to the director view to copy this chat.")));
    return;
  }

  const listing = await ctx.api("transfer.targets", { chat, audience });
  const draft = ctx.draft("transfer:" + chat);
  let saved = {};
  try { saved = JSON.parse(draft.get() || "{}") || {}; } catch { saved = {}; }
  const state = { target: listing.targets.some((item) => item.campaign === saved.target) ? saved.target : "",
    decisions: {}, sources: null, label: typeof saved.label === "string" ? saved.label : "" };
  if (state.target && saved.decisions && typeof saved.decisions === "object") state.decisions = saved.decisions;
  if (state.target && Array.isArray(saved.sources)) state.sources = new Set(saved.sources.filter((ref) => typeof ref === "string"));
  let scan = null, review = null, asked = 0;
  const keep = () => draft.set(JSON.stringify({ target: state.target, label: state.label, decisions: state.decisions,
    sources: state.sources ? [...state.sources] : null }));

  const scanSlot = el("div", {}), reviewSlot = el("div", {}), doneSlot = el("div", {});
  const status = el("div", { class: "g-field-error", role: "alert", tabindex: "-1", hidden: true });
  const say = (text) => { status.textContent = text || ""; status.hidden = !text; };

  // -- the target -------------------------------------------------------------------------------------------------
  const options = [{ value: "", label: "Choose a campaign…" }, ...listing.targets.map((item) => ({ value: item.campaign,
    label: (item.title || item.campaign) + " · " + item.chats + " chat" + (item.chats === 1 ? "" : "s") + (item.world ? " · " + item.world : "") }))];
  const target = ui.choice("Copy into", options, { value: state.target, onchange: (value) => {
    state.target = value; state.decisions = {}; state.sources = null; keep(); loadScan(); } });
  root.append(card(ui, "Copy into",
    listing.targets.length ? target.node : ui.empty("This library has no other campaign with a chat to copy into."),
    el("p", { class: "g-muted" }, "The copy is a new chat of that campaign. This chat's own campaign is not offered: within one campaign, fork the chat.")),
  scanSlot, reviewSlot, doneSlot,
  card(ui, "Not built yet", ...Object.entries(NOT_BUILT).map(([key, title]) => ui.unavailable(title, listing.not_available[key]))));

  // A change to any decision takes the old review away: it was of other decisions.
  const changed = () => { keep(); asked += 1; review = null; reviewSlot.replaceChildren(); say(""); };

  async function loadScan() {
    const turn = ++asked;
    review = null;
    scan = null;
    reviewSlot.replaceChildren();
    doneSlot.replaceChildren();
    say("");
    if (!state.target) { scanSlot.replaceChildren(); return; }
    scanSlot.replaceChildren(card(ui, "What would be copied", el("p", { class: "g-muted" }, "Reading both campaigns…")));
    let answer;
    try {
      answer = await ctx.api("transfer.scan", { chat, target: state.target, audience });
    } catch (error) {
      if (turn !== asked || ctx.signal.aborted) return;
      scanSlot.replaceChildren(card(ui, "What would be copied", el("p", { role: "alert", tabindex: "-1" }, "This copy is refused: " + reasonText(error)),
        el("p", { class: "g-muted" }, ui.errorText(error))));
      return;
    }
    if (turn !== asked || ctx.signal.aborted) return;
    scan = answer;
    state.decisions = readDecisions(JSON.stringify(state.decisions), scan.cast);
    const bound = new Set(scan.sources.map((item) => item.ref));
    state.sources = state.sources ? new Set([...state.sources].filter((ref) => bound.has(ref)))
      : new Set(scan.sources.filter((item) => !item.broken).map((item) => item.ref));
    keep();
    drawScan();
  }

  function drawScan() {
    const decide = (member) => {
      const pick = ui.choice(member.name + " (" + roleText(member.role) + ")", decisionOptions(member), {
        value: state.decisions[member.actor] || "", onchange: (value) => { state.decisions[member.actor] = value; changed(); } });
      pick.select.setAttribute("data-focus-key", "transfer:" + member.actor);
      return el("div", { class: "g-stack transfer-actor", role: "group", "aria-label": member.name },
        pick.node,
        member.active ? null : el("p", { class: "g-muted" }, "No longer in the chat's cast: still decided, as every actor of its roster is."),
        member.problem ? el("p", { class: "g-muted" }, "This chat cannot show it (" + member.problem + "): only leaving it out works.") : null,
        member.candidates.length ? null : el("p", { class: "g-muted" }, "The target has no instance of this character to reuse."),
        member.namesakes.length ? el("p", { class: "transfer-namesake" },
          "Shares the name, another individual (never offered): ",
          member.namesakes.map((item) => (item.name || item.actor)).join(", ") + ".") : null);
    };
    const sources = el("fieldset", { class: "transfer-checks" }, el("legend", {}, "Sources to carry"),
      scan.sources.length ? null : el("p", { class: "g-muted" }, "This chat binds no scenario or lorebook."));
    for (const item of scan.sources) {
      const box = el("input", { type: "checkbox", value: item.ref, "data-focus-key": "transfer:source:" + item.ref });
      box.checked = state.sources.has(item.ref);
      box.addEventListener("change", () => { if (box.checked) state.sources.add(item.ref); else state.sources.delete(item.ref); changed(); });
      sources.append(el("label", { class: "g-row transfer-check" }, box, el("span", {}, (item.name || item.ref) + " (" + item.role + ")"
        + (item.broken ? " · broken: " + item.broken : ""))));
    }
    const label = ui.field("Label of the new chat (optional)", { value: state.label, hint: "At most 200 characters." });
    label.input.addEventListener("input", () => { state.label = label.input.value; changed(); });
    const history = scan.history;
    const blocked = scan.history_problems.length > 0;
    scanSlot.replaceChildren(card(ui, "What would be copied",
      el("p", {}, "This chat holds " + history.turns + " played turn" + (history.turns === 1 ? "" : "s") + " and "
        + history.page_posts + " imported post" + (history.page_posts === 1 ? "" : "s") + " on " + history.pages + " page" + (history.pages === 1 ? "" : "s") + "."),
      blocked ? el("div", { class: "g-stack", role: "alert" }, el("strong", {}, "The history cannot all be read now, so it cannot be copied:"),
        el("ul", { class: "transfer-lines" }, scan.history_problems.map((item) => el("li", {}, item.kind + ": " + item.detail)))) : null,
      scan.notes.length ? el("ul", { class: "transfer-lines" }, scan.notes.map((note) => el("li", {}, noteText(note)))) : null,
      el("div", { class: "g-stack transfer-form", role: "group", "aria-label": "Every actor" },
        el("h3", { class: "transfer-sub" }, "Every actor"),
        el("p", { class: "g-muted" }, "Decide each one. Only the target's instances of the same character may be reused: a name is never a match."),
        ...scan.cast.map(decide)),
      el("div", { class: "g-stack transfer-form", role: "group", "aria-label": "Sources and label" }, sources, label.node),
      status,
      blocked ? ui.actions(ui.button("Scan again", () => ctx.act(loadScan)))
        : ui.actions(ui.button("Plan the copy", plan, { kind: "primary" }))));
  }

  const plan = () => ctx.act(async () => {
    const built = requestOf({ chat, target: state.target, decisions: state.decisions, cast: scan.cast,
      sources: scan.sources.filter((item) => state.sources.has(item.ref)).map((item) => item.ref), label: state.label,
      operationId: newOperationId() });
    if (built.error) { say(built.error); return; }
    say("");
    const turn = ++asked;
    let answer;
    try {
      answer = await ctx.api("transfer.plan", built.request);
    } catch (error) {
      if (turn === asked) say("This copy is refused: " + reasonText(error));
      return;
    }
    if (turn !== asked || ctx.signal.aborted) return;
    review = { args: built.request, plan: answer.plan };
    drawReview();
    const heading = reviewSlot.querySelector(".transfer-focus");
    if (heading) heading.focus();
  });

  function drawReview() {
    const shown = review.plan;
    const mapping = el("ul", { class: "transfer-lines" }, shown.mapping.map((item) => el("li", {},
      el("strong", {}, item.name || item.actor), " · ",
      item.decision === "leave" ? "left out: its posts keep the name, with no actor"
        : item.decision === "new" ? "created new in the target as " + (item.target_name || item.target)
          : "reused as " + (item.target_name || item.target) + ", already in the target")));
    const posts = el("ol", { class: "transfer-lines transfer-posts" }, shown.posts.map((post) => el("li", {},
      el("strong", {}, (post.speaker || post.role) + ": "), el("span", { class: "transfer-said" }, post.text || ""),
      post.from ? el("span", { class: "g-muted" }, " · from " + whereText(post.from)) : null)));
    reviewSlot.replaceChildren(card(ui, "Review the copy",
      el("p", { class: "transfer-focus", tabindex: "-1" }, ui.pill("Not copied yet", { kind: "accent" }),
        " Nothing is written until you copy it as reviewed. This chat is never changed."),
      el("h3", { class: "transfer-sub" }, "Who"), mapping,
      el("h3", { class: "transfer-sub" }, "The new chat"),
      el("ul", { class: "transfer-lines" },
        el("li", {}, shown.label ? "Labelled “" + shown.label + "”" : "No label"),
        ...shown.sources.carried.map((item) => el("li", {}, "Carries " + (item.name || item.ref) + " (" + item.role + ")")),
        ...shown.sources.left_out.map((item) => el("li", {}, "Leaves out " + (item.name || item.ref) + " (" + item.role + ")")),
        shown.sources.carried.length || shown.sources.left_out.length ? null : el("li", {}, "No scenario or lorebook")),
      el("h3", { class: "transfer-sub" }, "Copied: " + shown.posts_total + " post" + (shown.posts_total === 1 ? "" : "s") + " on " + shown.pages + " page" + (shown.pages === 1 ? "" : "s")),
      shown.posts.length ? posts : ui.empty("No post would be copied."),
      shown.posts_total > shown.posts.length ? el("p", { class: "g-muted" }, "And " + (shown.posts_total - shown.posts.length) + " more.") : null,
      el("h3", { class: "transfer-sub" }, "Not copied"),
      shown.omitted.length ? el("ul", { class: "transfer-lines" }, shown.omitted.map((item) => el("li", {}, omittedText(item),
        item.text ? el("span", { class: "g-muted transfer-said" }, " · “" + item.text + "”") : null))) : ui.empty("Everything the table saw would be copied."),
      el("h3", { class: "transfer-sub" }, "Stays in this campaign"),
      el("p", { class: "g-muted" }, "A copy is text, never state: what was accepted here is not accepted there."),
      shown.left.length ? el("ul", { class: "transfer-lines" }, shown.left.map((item) => el("li", {}, leftText(item)))) : ui.empty("No accepted effect or ruling."),
      shown.notes.length ? el("div", { class: "g-stack" }, el("h3", { class: "transfer-sub" }, "To know"),
        el("ul", { class: "transfer-lines" }, shown.notes.map((note) => el("li", {}, noteText(note))))) : null,
      el("details", { class: "transfer-files" }, el("summary", {}, "The " + shown.files.length + " file" + (shown.files.length === 1 ? "" : "s") + " it would write"),
        el("ul", { class: "transfer-lines" }, shown.files.map((file) => el("li", { class: "g-mono" }, file)))),
      el("p", { class: "g-muted" }, "No model is asked: copying is text only."),
      ui.actions(ui.button("Copy as reviewed", commit, { kind: "primary" }),
        ui.button("Change the decisions", () => { changed(); const first = scanSlot.querySelector("select"); if (first) first.focus(); }))));
  }

  const commit = () => ctx.act(async () => {
    const reviewed = review;
    let answer;
    try {
      answer = await ctx.api("transfer.commit", { ...reviewed.args, review: reviewed.plan.review });
    } catch (error) {
      // No answer came (the app is unreachable, or the page was left): the review stands, and copying it again is
      // safe, since a retry finds a copy already made. Any refusal drops the review: scan again, and say why.
      if (!error || TRANSPORT.has(error.reason) || ctx.signal.aborted) throw error;
      const why = error.reason === "changed" ? "Something changed since the review, so nothing was copied. Review the copy again."
        : "Nothing was copied: " + reasonText(error) + ". Review the copy again.";
      ctx.notify(why, { bad: true });
      await loadScan();
      if (ctx.signal.aborted) return;
      say(why);
      // focus moves to why: the scan's own message, or its refusal when the scan itself was refused
      const told = scan && scanSlot.contains(status) ? status : scanSlot.querySelector("p[role]");
      if (told) told.focus();
      return;
    }
    if (ctx.signal.aborted) return;
    draft.clear();
    review = null;
    reviewSlot.replaceChildren();
    const done = answer.done;
    ctx.notify(done.already_applied ? "That copy was already made; nothing was copied again." : "Copied into a new chat.");
    const open = ctx.registered("chat") ? ui.buttonLink("Open the copy", "chat", [done.chat], undefined, { kind: "primary" }) : null;
    doneSlot.replaceChildren(card(ui, "Copied",
      el("p", { class: "transfer-focus", tabindex: "-1" }, done.already_applied ? "This copy was already made." : "The copy is a new chat of the other campaign."),
      el("p", { class: "g-muted" }, "This chat is unchanged."),
      open ? ui.actions(open) : el("p", { class: "g-mono" }, done.chat)));
    const focus = open ? doneSlot.querySelector("a") : doneSlot.querySelector(".transfer-focus");
    if (focus) focus.focus();
  });

  if (state.target) await loadScan();
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("transfer-card");
  return node;
}
