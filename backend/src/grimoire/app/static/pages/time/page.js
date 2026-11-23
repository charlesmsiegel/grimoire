// A campaign's time and commitments (Sprint 116): its long activities (074 downtime), the factions' offscreen plans
// (076) and its quests and promises (077), the director's, on the campaign clock that the Chronicle page keeps
// (docs/TIME.md). Addresses:
//   #/time/<campaign id>?chat=<ChatRef>&audience=director   the director's view, for one chat's cast
//   #/time/<campaign id>                                     the table's view: it says only that these are the director's
// Everything is read from, and written through, the time.* ops. Each change is reviewed first (time.<domain>_plan
// writes nothing) and committed as reviewed (time.<domain>_commit, with the same operation id and the etag the review
// was made against): anything that changed in between refuses it, and the page reads everything again. The three
// domains share one record, so after any change the whole page is read again. Nothing here estimates a time: a
// span is the exact number of days and hours given. Where every Wave 9 slice is on screen is listed at the end.

import { campaignName, chatName, count } from "../campaign/logic.js";
import {
  activityStatus, at, commitmentLines, coverageRows, deadlineState, downtimeLines, names, newOperationId, offscreenLines,
  outcomeText, parseTime, raceText, recordId, recordable, spanMinutes, spanText, statusText, who,
} from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
const KIND_LABELS = { promise: "Promise", mission: "Mission", objective: "Objective" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const campaign = ctx.path[0] || null;
  if (!campaign) {
    root.append(ui.notice("Open a campaign's time and commitments from its hub."), ui.actions(ui.buttonLink("All campaigns", "campaign")));
    return;
  }
  const audience = ctx.query.get("audience") === "director" ? "director" : "table";
  const asked = ctx.query.get("chat");
  if (asked && !asked.startsWith("campaign:" + campaign + "#")) {
    root.append(ui.notice("This address names a chat that is not in the campaign it names."),
      ui.actions(ui.buttonLink("All campaigns", "campaign")));
    return;
  }
  const listing = await ctx.api("campaign.chats", { campaign });
  const found = listing.campaigns.find((item) => item.campaign === campaign) || null;
  const chats = found ? found.chats : [];
  const chat = asked || (chats.length ? chats[0].chat : null);
  const name = found ? campaignName(found) : campaign;
  ctx.setTitle("Time and commitments");
  ctx.setCrumb(ui.link("Campaigns", "campaign"), " / ", ui.link(name, "campaign", [campaign], chat ? { chat } : undefined), " / Time");
  const address = (changes) => {
    const query = { chat, audience, ...changes };
    return ctx.href("time", [campaign], { chat: query.chat || undefined, audience: query.audience === "director" ? "director" : undefined });
  };
  const views = ui.segments("Whose view", AUDIENCES, { value: audience, onchange: (value) => ctx.navigate(address({ audience: value })) });
  const shown = chats.some((item) => item.chat === chat) || !chat ? chats : [{ chat, label: null, name: null }, ...chats];
  const picker = audience === "director" && shown.length > 1
    ? ui.choice("Chat", shown.map((item) => ({ value: item.chat, label: chatName(item) })), { value: chat, onchange: (value) => ctx.navigate(address({ chat: value })) })
    : null;
  root.append(el("div", { class: "g-row time-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Long activities, offscreen plans and commitments on the campaign clock."
      : "What the director keeps of time and commitments."),
  ctx.registered("campaign") ? ui.buttonLink("Campaign hub", "campaign", [campaign], { chat: chat || undefined, audience: audience === "director" ? "director" : undefined }) : null),
  picker ? el("div", { class: "time-chat" }, picker.node) : null);

  const cards = el("div", { class: "time-cards" });
  root.append(cards);
  const coverage = el("div", {});
  if (audience !== "director") {
    cards.append(card(ui, "Time and commitments", el("p", { class: "g-muted" },
      "Long activities, the factions' offscreen plans, and quests and promises are the director's: switch to the director view to see or change them.")), coverage);
    await showCoverage(coverage, ctx, { campaign, chat, audience });
    return;
  }
  const board = el("div", { class: "time-board" });
  cards.append(board, coverage);
  await Promise.all([showBoard(board, ctx, { campaign, chat }), showCoverage(coverage, ctx, { campaign, chat, audience })]);
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("time-card");
  return node;
}

function form(ui, title, fields, submit) {
  return ui.el("div", { class: "g-stack time-form", role: "group", "aria-label": title },
    ui.el("h3", { class: "time-sub" }, title), ...fields, ui.actions(submit));
}

function lines(ui, list) {
  return ui.el("ul", { class: "time-lines" }, list.map((item) => ui.el("li", {}, item)));
}

// Days and hours of campaign time, as one labelled group.
function span(ui, title, { days = "", hours = "" } = {}) {
  const d = ui.field("Days", { value: days }), h = ui.field("Hours", { value: hours });
  return { d, h, node: ui.el("div", { class: "g-row time-span", role: "group", "aria-label": title }, d.node, h.node),
    get minutes() { return spanMinutes(d.value, h.value); }, setError(message) { h.setError(message); } };
}

// A set of checkboxes, one per option ({value, label, detail}), as a labelled fieldset.
function checks(ui, legend, options, { name } = {}) {
  const picked = new Set();
  const status = ui.el("div", { class: "g-field-error", role: "alert", hidden: true });
  const items = options.map((option) => {
    const box = ui.el("input", { type: "checkbox", value: option.value, "data-focus-key": "time:" + name + ":" + option.value });
    box.addEventListener("change", () => { if (box.checked) picked.add(option.value); else picked.delete(option.value); });
    return ui.el("li", {}, ui.el("label", { class: "g-row time-check" }, box, ui.el("span", { class: "g-grow" }, option.label),
      option.detail ? ui.el("span", { class: "g-muted" }, option.detail) : null));
  });
  const node = ui.el("fieldset", { class: "time-checks" }, ui.el("legend", {}, legend), ui.el("ul", { class: "time-lines" }, items), status);
  return { node, get values() { return [...picked].sort(); },
    setError(message) { status.textContent = message || ""; status.hidden = !message; } };
}

// -- the director's board: one read of the clock and all three domains ----------------------------------------

async function showBoard(slot, ctx, { campaign, chat }) {
  const { ui } = ctx;
  const { el } = ui;
  const parts = { clock: el("div", {}), downtime: el("div", {}), offscreen: el("div", {}), commitments: el("div", {}) };
  slot.append(parts.clock, parts.downtime, parts.offscreen, parts.commitments);
  let overview = null;
  // The change under review, if any: { domain, args, plan, context }. It is committed exactly as reviewed, with its
  // own operation id and the etag it was reviewed against, so a commit retried after a lost answer finds the
  // operation it already made, and one made after anything else changed is refused.
  let review = null;
  // Only the newest read is drawn: an answer to an older one (overtaken by a change or another read) is dropped.
  let reading = 0;
  const base = { campaign, audience: "director", ...(chat ? { chat } : {}) };
  const DRAFT = "time:" + campaign + ":";
  const SECTION = { downtime: "downtime", offscreen: "offscreen", commitment: "commitments" };

  // What the person typed, ticked, chose or opened, kept across every redraw (a review, a Cancel, a re-read after a
  // stale review) by where the control is (the labels of what holds it) and its own label. What a change was made
  // from is forgotten once it is made, so its form starts afresh.
  const kept = new Map();
  function keyOf(node) {
    const path = [];
    for (let at = node.parentNode; at && at !== slot; at = at.parentNode) {
      const label = at.getAttribute ? at.getAttribute("aria-label") : null;
      if (label) path.unshift(label);
    }
    let own = null;
    if (node.tagName === "DETAILS") {
      const summary = node.querySelector("summary");
      own = "details:" + (summary ? summary.textContent : "");
    } else if (node.getAttribute("type") === "checkbox") {
      let set = node.parentNode;
      while (set && set.tagName !== "FIELDSET") set = set.parentNode;
      const legend = set ? set.querySelector("legend") : null;
      own = "check:" + (legend ? legend.textContent : "") + ":" + node.getAttribute("value");
    } else if (node.getAttribute("id")) {
      const label = slot.querySelector('label[for="' + node.getAttribute("id") + '"]');
      own = label ? "field:" + label.textContent : null;
    }
    return own === null ? null : path.join(" / ") + " / " + own;
  }
  const controls = () => [...slot.querySelectorAll("input,select,textarea,details")];
  function keep() {
    for (const node of controls()) {
      const key = keyOf(node);
      if (key === null) continue;
      kept.set(key, node.tagName === "DETAILS" ? node.open : node.getAttribute("type") === "checkbox" ? node.checked : node.value);
    }
  }
  function restore() {
    for (const node of controls()) {
      const key = keyOf(node);
      if (key === null || !kept.has(key)) continue;
      const value = kept.get(key);
      if (node.tagName === "DETAILS") { node.open = value; continue; }
      if (node.getAttribute("type") === "checkbox") {
        if (node.checked === value) continue;
        node.checked = value;
        node.dispatchEvent({ type: "change" });
      } else if (node.value !== value) {
        node.value = value;
        node.dispatchEvent({ type: node.tagName === "SELECT" ? "change" : "input" });
      }
    }
  }
  function forget(form) {
    for (const key of [...kept.keys()]) if (key.split(" / ").includes(form)) kept.delete(key);
  }
  // Back to the form a review was made from: its first field takes the focus.
  function backTo(form) {
    // a NodeList has no array methods: spread it first
    const found = form && [...slot.querySelectorAll("div")].find((node) => node.getAttribute("aria-label") === form);
    const first = found && [...found.querySelectorAll("input,select,textarea")].find((node) => node.getAttribute("type") !== "hidden");
    if (first) first.focus();
  }

  const load = async () => {
    const turn = ++reading;
    const answer = await ctx.api("time.overview", base);
    if (turn !== reading) return;
    overview = answer;
    draw();
  };
  const plan = (domain, args, context = {}) => ctx.act(async () => {
    // reviewed against the etag of the domain's own section, as it was read
    const full = { ...base, operation_id: newOperationId(), expected: overview[SECTION[domain]].etag, ...args };
    let answer;
    try {
      answer = await ctx.api("time." + domain + "_plan", full);
    } catch (error) {
      if (error && error.reason === "changed") await load();  // read before reviewing again
      throw error;
    }
    review = { domain, args: full, plan: answer.plan, context };
    draw();
    const box = slot.querySelector(".time-review");
    if (box) box.focus();
    return true;
  });
  const commit = (extra = {}) => ctx.act(async () => {
    const reviewed = review;
    // a decision is made only under the controls it was reviewed under
    const controlled = reviewed.plan.reviewed_controls ? { reviewed_controls: reviewed.plan.reviewed_controls } : {};
    try {
      const answer = await ctx.api("time." + reviewed.domain + "_commit", { ...reviewed.args, ...controlled, ...extra });
      if (reviewed.context.clear) reviewed.context.clear();
      if (reviewed.context.form) forget(reviewed.context.form);
      review = null;
      ctx.notify(answer.done.already_applied ? "That change was already made; nothing was changed again." : "Changed.");
      await load();
      const now = slot.querySelector(".time-now");
      if (now) now.focus();
    } catch (error) {
      if (error && error.reason === "changed") {  // something changed after the review: read it all and review again
        review = null;
        await load();
        backTo(reviewed.context.form);
      }
      throw error;
    }
    return true;
  });
  const cancel = () => {
    const form = review.context.form;
    review = null;
    draw();
    backTo(form);
  };

  function reviewBox(domain) {
    if (!review || review.domain !== domain) return null;
    const { args, plan: reviewed, context } = review;
    const said = domain === "downtime" ? downtimeLines(args, reviewed, context)
      : domain === "offscreen" ? offscreenLines(args, reviewed) : commitmentLines(args, reviewed, context);
    const keys = reviewed.keys && reviewed.keys.length && !reviewed.already_applied ? reviewed.keys : [];
    const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "time-sub" }, "Review this change"),
      ...said.map((line) => el("p", {}, line)),
      keys.length ? el("p", {}, args.operation === "void"
        ? "Your own character backed what this withdraws: making it gives your key for " + names(keys) + "."
        : "This is your own character's decision: making it gives your key for " + names(keys) + ", resting on what you said in the turn it cites.") : null,
      keys.length && reviewed.backing && reviewed.backing.length ? el("div", { class: "g-stack" },
        el("p", {}, "These words of yours become the recorded backing of this decision:"),
        ...reviewed.backing.map((item) => el("figure", { class: "time-backing" },
          el("blockquote", { class: "time-text" }, item.text),
          el("figcaption", { class: "g-muted" }, who(item) + (item.turn_number ? ", turn " + item.turn_number : ""))))) : null,
      el("p", { class: "g-muted" }, reviewed.files.length ? "It writes " + count(reviewed.files.length, "file") + ": " + reviewed.files.join(", ")
        : "It writes nothing."),
      ui.actions(ui.button("Cancel", cancel),
        ui.button(keys.length ? "Make this change with my key" : "Make this change", () => commit(keys.length ? { endorse: true } : {}), { kind: "primary" })));
    box.classList.add("time-review");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Review this change");
    box.setAttribute("tabindex", "-1");
    return box;
  }

  function draw() {
    keep();
    parts.clock = swap(parts.clock, clockCard());
    parts.downtime = swap(parts.downtime, downtimeCard());
    parts.offscreen = swap(parts.offscreen, offscreenCard());
    parts.commitments = swap(parts.commitments, commitmentsCard());
    restore();
  }
  function swap(old, next) { old.replaceWith(next); return next; }

  const refused = (section) => section && section.error ? ui.errorBox(section.error) : null;

  // -- the campaign clock (055, kept on the Chronicle page) -----------------------------------------------------
  function clockCard() {
    const clock = overview.clock;
    const chronicle = ctx.registered("chronicle")
      ? ui.buttonLink("Open the Chronicle", "chronicle", [campaign], { chat: chat || undefined, audience: "director" }, { size: "small" }) : null;
    if (!clock.started) {
      return card(ui, "Campaign clock", el("p", { class: "time-now", tabindex: "-1" }, "This campaign's clock is not started yet."),
        el("p", { class: "g-muted" }, "Long activities and offscreen plans run on campaign time, and nothing here estimates it: start the clock on the Chronicle page first."),
        chronicle ? ui.actions(chronicle) : ui.unavailable("Starting the clock", "the Chronicle page is not in this app"));
    }
    return card(ui, "Campaign clock",
      el("p", { class: "time-now", tabindex: "-1" }, el("span", { class: "g-muted" }, "Now: "), el("strong", {}, at(clock.now))),
      el("h3", { class: "time-sub" }, "Deadlines"),
      clock.deadlines.length ? lines(ui, clock.deadlines.map((item) => el("div", { class: "g-row" },
        el("span", { class: "g-grow" }, el("strong", {}, item.label), el("span", { class: "g-muted" }, " · " + item.kind + " · due " + at(item.due))),
        item.flagged_by ? ui.pill("Flagged", { kind: "accent" }) : ui.pill("Pending")))) : ui.empty("No deadline is scheduled."),
      clock.deadlines.some((item) => item.flagged_by)
        ? el("p", { class: "g-muted" }, "A flag says only that the time came. Nothing happens by itself: decide what follows in play.") : null,
      chronicle ? ui.actions(chronicle) : null);
  }

  // -- 074 downtime ---------------------------------------------------------------------------------------------
  function downtimeCard() {
    const section = overview.downtime;
    const node = card(ui, "Downtime", el("p", { class: "g-muted" }, "Long activities a character pursues over campaign time: each span is the exact time you give, and the clock moves with it."));
    if (section.error) { node.append(refused(section)); return node; }
    node.append(section.activities.length ? lines(ui, section.activities.map((item) => activity(item))) : ui.empty("No activity is begun yet."));
    const box = reviewBox("downtime");
    if (box) { node.append(box); return node; }
    node.append(beginForm(section));
    // only an activity this page may run for is offered to pursue or abandon
    const active = section.activities.filter((item) => item.status === "active" && item.whose && item.whose.available);
    // time is spent only on the campaign clock: with none started, nothing is pursued, and nothing is estimated
    if (!section.started) node.append(ui.unavailable("Pursuing an activity", "the campaign clock is not started: start it on the Chronicle page"));
    else if (active.length) node.append(pursueForm(active));
    if (active.length) node.append(abandonForm(active));
    return node;
  }

  function activity(item) {
    const done = item.required ? Math.min(100, Math.round((item.progress / item.required) * 100)) : 0;
    return el("article", { class: "g-stack time-item", "aria-label": "Activity: " + item.intent },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, item.intent),
        ui.pill(activityStatus(item.status), { kind: item.status === "complete" ? "good" : item.status === "abandoned" ? "bad" : "" })),
      el("div", { class: "g-muted" }, who(item.actor) + " · " + item.kind + " · " + spanText(item.progress) + " of " + spanText(item.required)
        + (item.status === "active" ? " · " + spanText(item.remaining) + " to go" : "")),
      el("progress", { max: "100", value: String(done), "aria-label": "Progress of " + item.intent }),
      item.outcome ? el("p", { class: "time-text" }, (item.status === "abandoned" ? "Abandoned: " : "Outcome: ") + item.outcome) : null,
      item.settled_at ? el("div", { class: "g-muted" }, "Settled at " + at(item.settled_at)) : null,
      item.status === "active" && item.whose && !item.whose.available ? ui.unavailable("Pursuing or abandoning it here", item.whose.detail) : null,
      item.status === "active" && !item.whose ? el("p", { class: "g-muted" }, "Choose a chat to pursue or abandon it: whose it is is read from its records.") : null);
  }

  function beginForm(section) {
    const everyone = overview.cast || [];
    if (!everyone.length) return ui.empty(chat ? "This chat has no cast to begin an activity for." : "Choose a chat: an activity is for one of its cast.");
    // a character another person plays is theirs: what it spends its time on is their choice
    const cast = everyone.filter((item) => item.downtime.available);
    const theirs = everyone.filter((item) => !item.downtime.available)
      .map((item) => ui.unavailable("An activity for " + who(item), item.downtime.detail));
    if (!cast.length) return el("div", { class: "g-stack time-form" }, el("h3", { class: "time-sub" }, "Begin an activity"), ...theirs);
    const actor = ui.choice("For", cast.map((item) => ({ value: item.actor, label: who(item) + (item.role === "npc" ? " (NPC)" : "") })), { value: cast[0].actor });
    const kind = ui.choice("Kind", section.kinds.map((value) => ({ value, label: value })), { value: section.kinds.includes("study") ? "study" : section.kinds[0] });
    const intent = ui.field("What they set out to do", { draft: ctx.draft(DRAFT + "intent") });
    const required = span(ui, "Effort it takes");
    const go = () => {
      const minutes = required.minutes;
      required.setError(minutes === null ? "Give whole days and hours that add up to more than nothing." : "");
      intent.setError(intent.value.trim() ? "" : "Say what they set out to do.");
      if (minutes === null || !intent.value.trim()) return;
      plan("downtime", { operation: "begin", activity: recordId(intent.value, "activity"), actor: actor.value, kind: kind.value,
        intent: intent.value.trim(), required: minutes }, { actor: cast.find((item) => item.actor === actor.value), clear: () => intent.clear(),
        form: "Begin an activity" });
    };
    return form(ui, "Begin an activity", [actor.node, kind.node, intent.node, el("p", { class: "time-label" }, "Effort it takes"), required.node, ...theirs],
      ui.button("Review", go, { kind: "primary" }));
  }

  function pursueForm(active) {
    const which = ui.choice("Activity", active.map((item) => ({ value: item.activity, label: item.intent + " · " + who(item.actor) })), { value: active[0].activity });
    const elapsed = span(ui, "Time that passes");
    const effort = span(ui, "Effort credited, if less");
    const note = ui.field("Note", { hint: "Optional: what came of it, if it completes" });
    const go = () => {
      const minutes = elapsed.minutes;
      elapsed.setError(minutes === null ? "Give the exact days and hours that pass." : "");
      const lessBlank = !effort.d.value.trim() && !effort.h.value.trim();
      const less = lessBlank ? null : effort.minutes;
      effort.setError(!lessBlank && (less === null || (minutes !== null && less > minutes)) ? "Give at most the time that passes, or leave it blank." : "");
      if (minutes === null || (!lessBlank && (less === null || less > minutes))) return;
      const item = active.find((entry) => entry.activity === which.value);
      plan("downtime", { operation: "pursue", activity: which.value, minutes, ...(less === null ? {} : { effort: less }), note: note.value.trim() },
        { activity: item, form: "Pursue an activity" });
    };
    return form(ui, "Pursue an activity", [which.node, el("p", { class: "time-label" }, "Time that passes"), elapsed.node,
      el("p", { class: "time-label" }, "Effort credited, if less than the time that passes"), effort.node, note.node], ui.button("Review", go, { kind: "primary" }));
  }

  function abandonForm(active) {
    const which = ui.choice("Activity", active.map((item) => ({ value: item.activity, label: item.intent + " · " + who(item.actor) })), { value: active[0].activity });
    const reason = ui.field("Why it is abandoned");
    const go = () => {
      reason.setError(reason.value.trim() ? "" : "Say why.");
      if (!reason.value.trim()) return;
      plan("downtime", { operation: "abandon", activity: which.value, note: reason.value.trim() },
        { activity: active.find((item) => item.activity === which.value), form: "Abandon an activity" });
    };
    return form(ui, "Abandon an activity", [which.node, reason.node], ui.button("Review", go));
  }

  // -- 076 offscreen plans --------------------------------------------------------------------------------------
  function offscreenCard() {
    const section = overview.offscreen;
    const node = card(ui, "Offscreen plans", el("p", { class: "g-muted" },
      "What factions do while the story is elsewhere. A development is the director's alone: it delivers no knowledge, event or rumor, and no character learns of it."));
    if (section.error) { node.append(refused(section)); return node; }
    node.append(section.plans.length ? lines(ui, section.plans.map((item) => planItem(item))) : ui.empty("No plan is adopted yet."));
    const box = reviewBox("offscreen");
    node.append(el("h3", { class: "time-sub" }, "Calendar advances not yet applied"),
      section.pending.length ? lines(ui, section.pending.map((item, n) => pendingItem(item, section, box || n > 0))) : ui.empty("Every advance of the clock is applied to the plans."),
      section.pending.length > 1 ? el("p", { class: "g-muted" }, "Advances are applied in the order the clock made them: each later one waits for the one before it.") : null);
    if (section.skips.length) {
      node.append(el("details", { class: "time-history" }, el("summary", {}, count(section.skips.length, "applied advance")),
        lines(ui, section.skips.map((item) => el("div", {}, at(item.before) + " to " + at(item.after) + ": "
          + (item.developments.length ? item.developments.map((step) => step.label || step.step).join(", ") : "no development"))))));
    }
    if (box) { node.append(box); return node; }
    node.append(adoptForm(section));
    return node;
  }

  function planItem(item) {
    return el("article", { class: "g-stack time-item", "aria-label": "Plan: " + item.goal },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, who(item.group) + ": " + item.goal),
        ui.pill(item.complete ? "Complete" : item.completed + " of " + item.steps.length + " steps", { kind: item.complete ? "good" : "" })),
      el("div", { class: "g-muted" }, "Decided: " + item.choice + " · priority " + item.priority + " · started " + at(item.started)),
      lines(ui, item.steps.map((step) => el("div", { class: "g-row" }, el("span", { class: "g-grow" }, step.label,
        el("span", { class: "g-muted" }, " · due " + at(step.due))), step.flagged ? ui.pill("Developed", { kind: "accent" }) : ui.pill("Pending")))),
      item.deadline ? el("p", { class: item.deadline.race === "missed" ? "time-warn" : "g-muted" }, raceText(item.deadline)) : null);
  }

  function pendingItem(item, section, reviewing) {
    const budget = ui.field("Budget", { value: String(Math.min(8, section.max_budget)), hint: "The most developments to apply, up to " + section.max_budget });
    const go = () => {
      const value = /^\d{1,4}$/.test(budget.value.trim()) ? Number(budget.value.trim()) : 0;
      budget.setError(value >= 1 && value <= section.max_budget ? "" : "Give a whole number from 1 to " + section.max_budget + ".");
      if (value >= 1 && value <= section.max_budget) plan("offscreen", { operation: "skip", advance: item.advance, budget: value }, { form: "Apply the advance to " + at(item.after) });
    };
    const head = el("div", {}, el("strong", {}, at(item.before) + " to " + at(item.after)),
      item.flags.length ? el("span", { class: "g-muted" }, " · flags " + item.flags.map((flag) => flag.label || flag.deadline).join(", ")) : null);
    if (reviewing) return head;
    return el("div", { class: "g-stack", role: "group", "aria-label": "Apply the advance to " + at(item.after) }, head, budget.node,
      ui.actions(ui.button("Review", go, { kind: "primary" })));
  }

  function adoptForm(section) {
    const adoption = section.adoption;
    if (!adoption.available) {
      return el("div", { class: "g-stack time-form" }, el("h3", { class: "time-sub" }, "Adopt a plan"),
        ui.unavailable("Adopting a plan here", adoption.detail),
        (adoption.problems || []).length ? el("p", { class: "g-muted" }, count(adoption.problems.length, "record") + " could not be read.") : null);
    }
    const choices = adoption.groups.flatMap((group) => group.members.filter((member) => member.may_decide)
      .map((member) => ({ value: group.actor + "|" + member.actor, label: who(group) + ", as " + who(member) + " decides" })));
    const leader = ui.choice("Group and leader", choices, { value: choices[0].value });
    const goal = ui.field("Goal", { draft: ctx.draft(DRAFT + "goal") });
    const decision = ui.field("What the leader decides", { draft: ctx.draft(DRAFT + "choice") });
    const policy = ui.field("How the group decides", { value: "The leader decides." });
    const steps = [1, 2, 3].map((n) => ({ label: ui.field("Step " + n), length: span(ui, "Step " + n + " takes") }));
    const priority = ui.field("Priority", { value: "50", hint: "From " + section.priority[0] + " to " + section.priority[1] + "; a higher one develops first" });
    const deadlines = overview.clock.deadlines.filter((item) => !item.flagged_by);
    const race = ui.choice("Races the deadline", [{ value: "", label: "None" }, ...deadlines.map((item) => ({ value: item.deadline, label: item.label + " (due " + at(item.due) + ")" }))], { value: "" });
    const go = () => {
      goal.setError(goal.value.trim() ? "" : "Name the goal.");
      decision.setError(decision.value.trim() ? "" : "Say what the leader decides.");
      const laid = [];
      let bad = false;
      for (const step of steps) {
        const minutes = step.length.minutes;
        const blank = !step.label.value.trim() && !step.length.d.value.trim() && !step.length.h.value.trim();
        if (blank) { step.length.setError(""); continue; }
        const ok = step.label.value.trim() && minutes !== null;
        step.length.setError(ok ? "" : "Name the step and give the exact days and hours it takes.");
        if (!ok) bad = true; else laid.push({ label: step.label.value.trim(), minutes });
      }
      if (!laid.length && !bad) steps[0].length.setError("Lay out at least one step.");
      const level = /^\d{1,3}$/.test(priority.value.trim()) ? Number(priority.value.trim()) : -1;
      priority.setError(level >= section.priority[0] && level <= section.priority[1] ? "" : "Give a whole number from " + section.priority[0] + " to " + section.priority[1] + ".");
      if (!goal.value.trim() || !decision.value.trim() || bad || !laid.length || priority.value.trim() === "" || level < section.priority[0] || level > section.priority[1]) return;
      const [group, leaderRef] = leader.value.split("|");
      plan("offscreen", { operation: "adopt", group, leader: leaderRef, goals: [goal.value.trim()], policy: policy.value.trim() || "The leader decides.",
        choice: decision.value.trim(), plan: recordId(goal.value, "plan"), goal: goal.value.trim(), steps: laid, priority: level,
        ...(race.value ? { deadline: race.value } : {}) }, { clear: () => { goal.clear(); decision.clear(); }, form: "Adopt a plan" });
    };
    return form(ui, "Adopt a plan", [leader.node, goal.node, decision.node, policy.node,
      ...steps.flatMap((step, n) => [step.label.node, el("p", { class: "time-label" }, "Step " + (n + 1) + " takes"), step.length.node]),
      priority.node, race.node, el("p", { class: "g-muted" }, "The leader's decision is made here, over the campaign's own records: the group's members in this chat's cast, and the leader one of its NPCs.")],
    ui.button("Review", go, { kind: "primary" }));
  }

  // -- 077 commitments ------------------------------------------------------------------------------------------
  function commitmentsCard() {
    const section = overview.commitments;
    const node = card(ui, "Commitments", el("p", { class: "g-muted" },
      "Quests and promises: who owes what to whom, by when, each step citing the objective events it rests on."));
    if (section.error) { node.append(refused(section)); return node; }
    if (section.problems.length) node.append(ui.notice(count(section.problems.length, "record") + " could not be read and is not shown.", { kind: "soft" }));
    const box = reviewBox("commitment");
    node.append(section.commitments.length ? lines(ui, section.commitments.map((item) => commitmentItem(item, section, Boolean(box))))
      : ui.empty("No commitment is offered yet."));
    if (box) { node.append(box); return node; }
    node.append(offerForm(section));
    return node;
  }

  function stepLine(step) {
    const said = outcomeText(step.outcome);
    return el("div", { class: "g-stack time-step" },
      el("div", { class: "g-row" }, el("span", { class: "g-grow" }, statusText(step.status)
        + (step.decided_by && step.decided_by.length ? ", as " + names(step.decided_by) + " decided" : "")
        + (step.clock_at ? " · " + at(step.clock_at) : "")), said ? ui.pill(step.outcome === "voided" ? "Voided" : "Skipped", { kind: "bad" }) : null),
      el("div", { class: "g-muted time-basis" }, "Cites: " + step.cited.map((item) => item.summary || item.event).join(" / ")),
      step.note ? el("div", { class: "time-text" }, step.note) : null,
      said ? el("div", { class: "g-muted" }, said) : null);
  }

  function commitmentItem(item, section, reviewing) {
    const deadline = deadlineState(item);
    const tone = { overdue: "bad", late: "bad", missing: "bad", passed: "", open: "", none: "" }[deadline.state];
    const node = el("article", { class: "g-stack time-item", "aria-label": (KIND_LABELS[item.kind] || item.kind) + ": " + item.summary },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow time-text" }, item.summary),
        ui.pill(statusText(item.status), { kind: item.status === "fulfilled" ? "good" : item.status === "refused" || item.status === "lapsed" ? "bad" : "" }),
        item.overdue ? ui.pill("Overdue", { kind: "bad" }) : null, item.late ? ui.pill("Late", { kind: "bad" }) : null),
      el("div", { class: "g-muted" }, (KIND_LABELS[item.kind] || item.kind) + " · owed by " + names(item.owed_by) + " to " + names(item.owed_to)),
      el("p", { class: tone === "bad" ? "time-warn" : "g-muted" }, deadline.text),
      item.certain ? null : el("p", { class: "time-warn" }, "Its history is uncertain: a step it rests on cites fiction that was undone."),
      el("details", { class: "time-history" }, el("summary", {}, count(item.steps.length + 1, "recorded step")),
        lines(ui, [stepLine(item.offer), ...item.steps.map(stepLine)])));
    if (reviewing) return node;
    const next = recordable(item, section.outcomes);
    if (next.length) node.append(recordForm(item, section, next));
    else if (item.next.length) node.append(ui.unavailable("Recording " + item.next.map(statusText).join(" or ").toLowerCase() + " here", item.deciders.detail));
    if (!item.deciders.available && next.length && item.next.some((status) => !next.includes(status))) {
      node.append(el("p", { class: "g-muted" }, "A decision is not offered here: " + item.deciders.detail));
    }
    const voidable = item.voidable.filter((entry) => entry.backers.every((backer) => backer.player === section.owner));
    if (voidable.length) node.append(voidForm(item, voidable));
    return node;
  }

  function cites(section, name) {
    const options = section.citable.map((item) => ({ value: item.event, label: item.summary,
      detail: item.turn_number ? "turn " + item.turn_number : "#" + item.at }));
    const picker = checks(ui, "Citing", options, { name });
    if (section.citable_total > section.citable.length) picker.node.append(el("p", { class: "g-muted" }, "Only the " + section.citable.length + " newest events are offered."));
    return picker;
  }

  function recordForm(item, section, next) {
    const status = ui.choice("What happened", next.map((value) => ({ value, label: statusText(value) })), { value: next[0] });
    const picked = cites(section, "record-" + item.commitment);
    const note = ui.field("Note", { hint: "Optional" });
    const go = () => {
      const values = picked.values;
      picked.setError(values.length >= 1 && values.length <= section.max_cites ? "" : "Cite from 1 to " + section.max_cites + " events.");
      if (values.length < 1 || values.length > section.max_cites) return;
      plan("commitment", { operation: "record", commitment: item.commitment, status: status.value, cites: values, note: note.value.trim() },
        { commitment: item, form: "Record a step of " + item.summary });
    };
    const keys = item.deciders.available && item.deciders.keys.length
      ? el("p", { class: "g-muted" }, "A decision for " + names(item.deciders.keys) + " is yours: cite the event of the turn where you said it.") : null;
    return el("details", { class: "time-history" }, el("summary", {}, "Record what happened next"),
      form(ui, "Record a step of " + item.summary, [status.node, keys, picked.node, note.node].filter(Boolean), ui.button("Review", go, { kind: "primary" })));
  }

  function voidForm(item, voidable) {
    const label = (entry) => entry.operation === item.offer.operation ? "The offer itself"
      : statusText((item.steps.find((step) => step.operation === entry.operation) || {}).status || entry.operation);
    const target = ui.choice("Which record", voidable.map((entry) => ({ value: entry.operation, label: label(entry) })), { value: voidable[voidable.length - 1].operation });
    const reason = ui.field("Why it was recorded in error");
    const go = () => {
      reason.setError(reason.value.trim() ? "" : "Say why.");
      if (reason.value.trim()) plan("commitment", { operation: "void", target: target.value, reason: reason.value.trim() }, { commitment: item, form: "Void a record of " + item.summary });
    };
    return el("details", { class: "time-history" }, el("summary", {}, "Void a record made in error"),
      form(ui, "Void a record of " + item.summary, [target.node, reason.node], ui.button("Review", go, { kind: "danger" })));
  }

  function offerForm(section) {
    if (!section.parties.length) return ui.empty("This campaign has no actors to owe anything.");
    if (!section.citable.length) {
      return el("div", { class: "g-stack time-form" }, el("h3", { class: "time-sub" }, "Offer a commitment"),
        ui.unavailable("Offering a commitment", "it must cite an objective event of the campaign, and none is recorded yet"));
    }
    const kind = ui.choice("Kind", section.kinds.map((value) => ({ value, label: KIND_LABELS[value] || value })), { value: section.kinds[0] });
    const summary = ui.field("What is owed", { draft: ctx.draft(DRAFT + "summary") });
    const parties = section.parties.map((item) => ({ value: item.actor, label: who(item), detail: item.kind }));
    const owedBy = checks(ui, "Owed by", parties, { name: "owed-by" });
    const owedTo = checks(ui, "Owed to", parties, { name: "owed-to" });
    const picked = cites(section, "offer");
    const deadlines = overview.clock.deadlines.filter((item) => !item.flagged_by);
    const due = ui.choice("Deadline", [{ value: "", label: "None" }, ...deadlines.map((item) => ({ value: "link:" + item.deadline, label: item.label + " (due " + at(item.due) + ")" })),
      ...(overview.clock.started ? [{ value: "new", label: "A new deadline" }] : [])], { value: "" });
    const label = ui.field("New deadline: what is due");
    const date = ui.field("New deadline: date", { hint: "After now, in the calendar's own notation" });
    const time = ui.field("New deadline: time", { value: "00:00", hint: "24-hour, like 09:30" });
    const fresh = el("div", { class: "g-stack", role: "group", "aria-label": "A new deadline", hidden: true }, label.node, date.node, time.node);
    due.select.addEventListener("change", () => { fresh.hidden = due.value !== "new"; });
    const go = () => {
      summary.setError(summary.value.trim() ? "" : "Say what is owed.");
      owedBy.setError(owedBy.values.length ? "" : "Pick who owes it.");
      owedTo.setError(owedTo.values.length ? "" : "Pick to whom it is owed.");
      const values = picked.values;
      picked.setError(values.length >= 1 && values.length <= section.max_cites ? "" : "Cite from 1 to " + section.max_cites + " events.");
      let deadline;
      let bad = false;
      if (due.value === "new") {
        const minute = parseTime(time.value);
        label.setError(label.value.trim() ? "" : "Say what is due.");
        date.setError(date.value.trim() ? "" : "Give the date it is due.");
        time.setError(minute === null ? "Give a time like 09:30." : "");
        bad = !label.value.trim() || !date.value.trim() || minute === null;
        if (!bad) deadline = { new: { deadline: recordId(label.value, "due"), label: label.value.trim(), date: date.value.trim(), minute } };
      } else if (due.value) {
        deadline = { link: due.value.slice(5) };
      }
      if (!summary.value.trim() || !owedBy.values.length || !owedTo.values.length || values.length < 1 || values.length > section.max_cites || bad) return;
      plan("commitment", { operation: "offer", commitment: recordId(summary.value, "commitment"), kind: kind.value, summary: summary.value.trim(),
        owed_by: owedBy.values, owed_to: owedTo.values, cites: values, ...(deadline ? { deadline } : {}) },
      { clear: () => { summary.clear(); label.clear(); date.clear(); }, form: "Offer a commitment" });
    };
    return form(ui, "Offer a commitment", [kind.node, summary.node, owedBy.node, owedTo.node, picked.node, due.node, fresh],
      ui.button("Review", go, { kind: "primary" }));
  }

  await load();
}

// -- where every Wave 9 slice is on screen --------------------------------------------------------------------

async function showCoverage(slot, ctx, { campaign, chat, audience }) {
  const { ui } = ctx;
  const { el } = ui;
  const answer = await ctx.api("time.coverage", {});
  const { here, elsewhere, waiting } = coverageRows(answer.features, ctx.registered);
  const linked = (item) => {
    if (item.chat) {
      return chat ? ui.link(item.title, item.route, [chat], audience === "director" ? { audience: "director" } : undefined)
        : el("span", {}, item.title + " (open a chat to follow its threads)");
    }
    if (item.campaign) return ui.link(item.title, item.route, [campaign], { chat: chat || undefined, audience: audience === "director" ? "director" : undefined });
    return ui.link(item.title, item.route);
  };
  const node = card(ui, "Wave 9 features",
    el("div", { class: "g-stack" }, el("h3", { class: "time-sub" }, "On this page"),
      lines(ui, here.map((item) => el("span", {}, item.title, el("span", { class: "g-muted" }, audience === "director" ? "" : " · in the director's view"))))),
    elsewhere.length ? el("div", { class: "g-stack" }, el("h3", { class: "time-sub" }, "On other screens"),
      lines(ui, elsewhere.map((item) => el("span", {}, linked(item), el("span", { class: "g-muted" }, " · " + item.where))))) : null,
    el("div", { class: "g-stack" }, el("h3", { class: "time-sub" }, "Not on a screen yet"),
      lines(ui, waiting.map((item) => ui.unavailable(item.title, item.reason)))));
  slot.replaceWith(node);
}
