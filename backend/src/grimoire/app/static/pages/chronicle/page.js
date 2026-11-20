// A campaign's chronicle (Sprint 115): its clock (055) and its characters' sourced memory (053), as the table or
// the director sees them (docs/CHRONICLE.md). Addresses:
//   #/chronicle/<campaign id>                               the campaign's chronicle, for the chat played last
//   #/chronicle/<campaign id>?chat=<ChatRef>&actor=<ref>    one chat's cast, one character's memory open
//   ...&audience=director                                   the director's view: the clock and the NPCs' memory
// Everything is read from, and written through, the chronicle.* ops. A change to the clock is reviewed first
// (chronicle.clock_plan writes nothing) and committed as reviewed (chronicle.clock_commit, with the same operation
// id and the clock's revision as reviewed): anything that changed in between refuses it. An account of a memory
// is the editor's own words citing the evidence they pick; nothing writes it for them. What Wave 8 delivered
// elsewhere is linked; what has no screen yet is named, with what it waits on.

import { campaignName, count } from "../campaign/logic.js";
import { advanceMinutes, newOperationId, parseTime, planText, statusText, topicId } from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
const KINDS = [{ value: "deadline", label: "Deadline" }, { value: "reminder", label: "Reminder" }, { value: "trigger", label: "Trigger" }];
const CALENDARS = [{ value: "gregorian", label: "Gregorian (2024-02-20)" }, { value: "hebrew", label: "Hebrew (5786-Kislev-25)" }];

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const campaign = ctx.path[0] || null;
  if (!campaign) {
    root.append(ui.notice("Open a campaign's chronicle from its hub."), ui.actions(ui.buttonLink("All campaigns", "campaign")));
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
  ctx.setTitle("Chronicle");
  ctx.setCrumb(ui.link("Campaigns", "campaign"), " / ", ui.link(name, "campaign", [campaign], chat ? { chat } : undefined), " / Chronicle");
  const address = (changes) => {
    const query = { chat, audience, actor: ctx.query.get("actor") || undefined, ...changes };
    return ctx.href("chronicle", [campaign], { chat: query.chat || undefined, actor: query.actor || undefined,
      audience: query.audience === "director" ? "director" : undefined });
  };
  const views = ui.segments("Whose view", AUDIENCES, { value: audience, onchange: (value) => ctx.navigate(address({ audience: value })) });
  root.append(el("div", { class: "g-row chronicle-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "The campaign clock, and what every character remembers." : "What your own characters remember. The clock is the director's."),
  ctx.registered("campaign") ? ui.buttonLink("Campaign hub", "campaign", [campaign], { chat: chat || undefined, audience: audience === "director" ? "director" : undefined }) : null));

  const cards = el("div", { class: "chronicle-cards" });
  root.append(cards);
  const clockCard = el("div", {}), memoryCard = el("div", {}), elsewhereCard = el("div", {});
  cards.append(clockCard, memoryCard, elsewhereCard);
  await Promise.all([
    audience === "director" ? showClock(clockCard, ctx, campaign) : directorsOnly(clockCard, ctx),
    chat ? showMemory(memoryCard, ctx, chat, audience, address) : noChat(memoryCard, ctx, listing.complete),
    showElsewhere(elsewhereCard, ctx),
  ]);
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("chronicle-card");
  return node;
}

function directorsOnly(slot, ctx) {
  const { ui } = ctx;
  slot.replaceWith(card(ui, "Campaign clock", ui.el("p", { class: "g-muted" },
    "The campaign's date and its deadlines are the director's: switch to the director view to see or change them.")));
}

function noChat(slot, ctx, complete) {
  const { ui } = ctx;
  slot.replaceWith(card(ui, "Memory", ui.empty(complete ? "This campaign has no chat in this library, so no cast to remember."
    : "None of this campaign's chats is among the most recent ones listed.")));
}

// -- the campaign clock (055) ---------------------------------------------------------------------------------

async function showClock(slot, ctx, campaign) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Campaign clock");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(body);
  // The plan under review, if any: { args, plan }. It is committed exactly as reviewed, with its own operation id,
  // so a commit retried after a lost answer finds the operation it already made.
  let review = null;
  let clock = null;
  // the deadline being written is kept (across a review, a cancel or a stale commit) until it is scheduled
  const DEADLINE = "deadline:" + campaign + ":";

  const load = async () => { clock = await ctx.api("chronicle.clock", { campaign, audience: "director" }); draw(); };
  const plan = (args) => ctx.act(async () => {
    const full = { campaign, audience: "director", operation_id: newOperationId(), expected: clock.etag, ...args };
    const answer = await ctx.api("chronicle.clock_plan", full);
    review = { args: full, plan: answer.plan };
    clock = answer.clock;
    draw();
    return true;
  });
  const commit = () => ctx.act(async () => {
    try {
      const answer = await ctx.api("chronicle.clock_commit", review.args);
      if (review.args.operation === "schedule") for (const part of ["label", "date", "time"]) ctx.draft(DEADLINE + part).clear();
      review = null;
      clock = answer.clock;
      ctx.notify(answer.done.already_applied ? "That change was already made; nothing was changed again." : "The clock is changed.");
      draw();
      const now = node.querySelector(".chronicle-now");
      if (now) now.focus();
    } catch (error) {
      if (error && error.reason === "changed") {  // the clock changed after the review: review it again
        review = null;
        await load();
      }
      throw error;
    }
    return true;
  });

  function draw() {
    const parts = [];
    if (!clock.started) {
      parts.push(el("p", { class: "g-muted" }, "This campaign has no clock yet. Start it on the date the story is at; nothing older is redated."));
    } else {
      parts.push(el("p", { class: "chronicle-now", tabindex: "-1" }, el("span", { class: "g-muted" }, "Now: "),
        el("strong", {}, clock.now.date + " " + clock.now.time),
        el("span", { class: "g-muted" }, " · " + (clock.calendar.provider === "hebrew" ? "Hebrew" : clock.calendar.provider === "gregorian" ? "Gregorian" : "custom") + " calendar")));
      parts.push(el("h3", { class: "chronicle-sub" }, "Deadlines"),
        clock.deadlines.length ? el("ul", { class: "chronicle-lines" }, clock.deadlines.map((item) => el("li", { class: "g-row" },
          el("span", { class: "g-grow" }, el("strong", {}, item.label), el("span", { class: "g-muted" }, " · " + item.kind + " · due " + item.due.date + " " + item.due.time)),
          item.flagged_by ? ui.pill("Flagged", { kind: "accent" }) : ui.pill("Pending")))) : ui.empty("No deadline is scheduled."));
      if (clock.deadlines.some((item) => item.flagged_by)) {
        parts.push(el("p", { class: "g-muted" }, "A flag says only that the time came. Nothing happens by itself: decide what follows in play."));
      }
    }
    if (review) parts.push(reviewBox());
    else parts.push(...(clock.started ? [advanceForm(), scheduleForm()] : [startForm()]));
    body.replaceChildren(...parts);
  }

  function reviewBox() {
    const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "chronicle-sub" }, "Review this change"),
      el("p", {}, planText(review.args, review.plan)),
      review.plan.flags.length ? el("p", {}, "It flags: " + review.plan.flags.map((flag) => flag.label || flag.deadline).join(", ") + ".") : null,
      el("p", { class: "g-muted" }, "It writes " + count(review.plan.files.length, "file") + ": " + review.plan.files.join(", ")),
      ui.actions(ui.button("Cancel", () => { review = null; draw(); }), ui.button("Make this change", commit, { kind: "primary" })));
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Review this change");
    return box;
  }

  function startForm() {
    const calendar = ui.choice("Calendar", CALENDARS, { value: "gregorian" });
    const date = ui.field("Date", { hint: "In the calendar's own notation" });
    const time = ui.field("Time", { value: "00:00", hint: "24-hour, like 09:30" });
    const go = () => {
      const minute = parseTime(time.value);
      time.setError(minute === null ? "Give a time like 09:30." : "");
      date.setError(date.value.trim() ? "" : "Give the date the story is at.");
      if (minute === null || !date.value.trim()) return;
      plan({ operation: "initialize", provider: calendar.value, date: date.value.trim(), minute });
    };
    return form(ui, "Start the clock", [calendar.node, date.node, time.node], ui.button("Review", go, { kind: "primary" }));
  }

  function advanceForm() {
    const days = ui.field("Days", { value: "0" }), hours = ui.field("Hours", { value: "0" }), minutes = ui.field("Minutes", { value: "0" });
    const go = () => {
      const total = advanceMinutes(days.value, hours.value, minutes.value);
      minutes.setError(total === null ? "Give whole numbers that add up to more than nothing." : "");
      if (total !== null) plan({ operation: "advance", minutes: total });
    };
    return form(ui, "Advance time", [el("div", { class: "g-row chronicle-duration" }, days.node, hours.node, minutes.node)],
      ui.button("Review", go, { kind: "primary" }));
  }

  function scheduleForm() {
    const label = ui.field("What is due", { draft: ctx.draft(DEADLINE + "label") });
    const kind = ui.choice("Kind", KINDS, { value: "deadline" });
    const date = ui.field("Due date", { draft: ctx.draft(DEADLINE + "date"), hint: "After now, in the calendar's own notation" });
    const time = ui.field("Due time", { draft: ctx.draft(DEADLINE + "time"), value: "00:00", hint: "24-hour, like 09:30" });
    const go = () => {
      const minute = parseTime(time.value);
      time.setError(minute === null ? "Give a time like 09:30." : "");
      label.setError(label.value.trim() ? "" : "Say what is due.");
      date.setError(date.value.trim() ? "" : "Give the date it is due.");
      if (minute === null || !label.value.trim() || !date.value.trim()) return;
      plan({ operation: "schedule", deadline: (topicId(label.value).slice(0, 60) || "due") + "-" + newOperationId().slice(3, 9), label: label.value.trim(),
        kind: kind.value, date: date.value.trim(), minute });
    };
    return form(ui, "Schedule a deadline", [label.node, kind.node, date.node, time.node], ui.button("Review", go));
  }

  await load();
}

function form(ui, title, fields, submit) {
  const node = ui.el("div", { class: "g-stack chronicle-form", role: "group", "aria-label": title },
    ui.el("h3", { class: "chronicle-sub" }, title), ...fields, ui.actions(submit));
  return node;
}

// -- memory (053) ---------------------------------------------------------------------------------------------

async function showMemory(slot, ctx, chat, audience, address) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Memory");
  slot.replaceWith(node);
  const hub = await ctx.api("campaign.hub", { chat, audience });
  // whose memory may be read here: the table's own characters; the director's view, the NPCs' too
  const cast = hub.cast.filter((member) => (member.role === "player" && member.controller === hub.player)
    || (audience === "director" && member.role === "npc"));
  const intro = el("p", { class: "g-muted" }, "What each character remembers, in accounts its controller wrote, each citing the evidence it rests on. "
    + (audience === "director" ? "An NPC's accounts are what play gives it to remember." : "NPCs' memory is the director's."));
  node.append(intro);
  if (!cast.length) {
    node.append(ui.empty(audience === "director" ? "No character in this chat's cast has a controller." : "You play no character in this chat."));
    return;
  }
  const wanted = ctx.query.get("actor");
  let actor = cast.some((member) => member.actor === wanted) ? wanted : cast[0].actor;
  const chips = el("nav", { class: "g-chips", "aria-label": "Whose memory" });
  const pane = el("div", { class: "g-stack" });
  node.append(chips, pane);
  const drawChips = () => chips.replaceChildren(...cast.map((member) => {
    const chip = el("a", { class: "g-select g-chip chronicle-chip", href: address({ actor: member.actor }) },
      member.name || member.actor, el("span", { class: "g-muted" }, member.role === "npc" ? " · NPC" : " · yours"));
    chip.addEventListener("click", (event) => { event.preventDefault(); actor = member.actor; drawChips(); open(); });
    if (member.actor === actor) chip.setAttribute("aria-current", "true");
    return chip;
  }));
  // Only the character selected now is drawn: an answer for one selected before it (read or saved) is dropped.
  let reading = 0;
  const open = async () => {
    const turn = ++reading;
    pane.replaceChildren(el("p", { class: "g-muted" }, "Reading…"));
    try {
      const memory = await ctx.api("chronicle.memory", { chat, actor, audience });
      if (turn === reading) draw(memory);
    } catch (error) {
      if (error && error.reason === "left") return;
      if (turn === reading) pane.replaceChildren(ui.errorBox(error));
    }
  };
  function draw(memory) {
    pane.replaceChildren(...dossier(ctx, memory), recordForm(ctx, memory, (answer) => { if (actor === memory.actor) draw(answer.memory); }, { chat, audience }));
  }
  drawChips();
  await open();
}

function dossier(ctx, memory) {
  const { ui } = ctx;
  const { el } = ui;
  const who = memory.name || memory.actor;
  const parts = [el("h3", { class: "chronicle-sub" }, who + (memory.whose === "npc" ? ", as the director keeps it" : ", as you keep it"))];
  if (!memory.topics.length) {
    parts.push(ui.empty("Nothing is remembered yet: no account has been written."));
    return parts;
  }
  for (const topic of memory.topics) {
    const standing = topic.current.map((item) => account(ctx, item));
    const history = topic.history.map((item) => account(ctx, item));
    const block = el("article", { class: "g-stack chronicle-topic", "aria-label": "Remembered: " + topic.topic },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, topic.topic),
        topic.current.length > 1 ? ui.pill("Conflicting accounts", { kind: "bad" }) : null),
      standing.length ? el("ul", { class: "chronicle-lines" }, standing.map((item) => el("li", {}, item))) : ui.empty("No account of this stands now."),
      history.length ? el("details", { class: "chronicle-history" }, el("summary", {}, count(history.length, "earlier account")),
        el("ul", { class: "chronicle-lines" }, history.map((item) => el("li", {}, item)))) : null);
    parts.push(block);
  }
  if (memory.omitted) parts.push(el("p", { class: "g-muted" }, count(memory.omitted, "older account") + " not shown."));
  if (memory.corrected) parts.push(el("p", { class: "g-muted" }, count(memory.corrected, "item") + " of evidence corrected; accounts citing it are never current again."));
  return parts;
}

function account(ctx, item) {
  const { ui } = ctx;
  const { el } = ui;
  return el("div", { class: "g-stack chronicle-account" },
    el("div", { class: "g-row" }, el("span", { class: "g-grow chronicle-text" }, item.text),
      ui.pill(statusText(item.status), { kind: item.status === "current" ? "good" : item.status === "contested" ? "bad" : "" })),
    el("div", { class: "g-muted chronicle-basis" }, "Rests on: " + item.evidence.map((known) => known.content).join(" / ")
      + (item.unavailable.length ? " · " + count(item.unavailable.length, "item") + " no longer held" : "")));
}

function recordForm(ctx, memory, done, { chat, audience }) {
  const { ui } = ctx;
  const { el } = ui;
  // an unsent account is the present editor's: a character handed to another controller never shows it
  const key = "account:" + memory.editor + ":" + memory.actor;
  const topic = ui.field("What it is about", { draft: ctx.draft(key + ":topic"), hint: "A short name for the detail, like the visitor's identity" });
  const text = ui.field("The account", { draft: ctx.draft(key + ":text"), multiline: true, hint: "In your own words, at most " + memory.max_text + " characters" });
  const picked = new Set();
  const evidence = memory.citable.map((item) => {
    const box = el("input", { type: "checkbox", value: item.evidence, "data-focus-key": "chronicle:cite:" + item.evidence });
    box.addEventListener("change", () => { if (box.checked) picked.add(item.evidence); else picked.delete(item.evidence); });
    return el("li", {}, el("label", { class: "g-row chronicle-cite" }, box, el("span", { class: "g-grow" }, item.content),
      el("span", { class: "g-muted" }, item.type + " · " + item.channel)));
  });
  const replaces = [{ value: "", label: "Nothing: a new account" }, ...memory.topics.flatMap((entry) => entry.current
    .map((item) => ({ value: item.account, label: entry.topic + ": " + clip(item.text) })))];
  const supersedes = ui.choice("It replaces", replaces, { value: "" });
  const status = el("div", { class: "g-field-error", role: "alert", hidden: true });
  let attempt = null;
  const save = () => {
    const name = topicId(topic.value);
    topic.setError(name ? "" : "Name what it is about.");
    text.setError(text.value.trim() ? "" : "Write the account.");
    status.hidden = picked.size > 0;
    status.textContent = picked.size ? "" : "Pick the evidence it rests on.";
    if (!name || !text.value.trim() || !picked.size) return;
    const replaced = memory.topics.flatMap((entry) => entry.current).find((item) => item.account === supersedes.value);
    if (replaced && replaced.topic !== name) {
      topic.setError("An account replaces one of the same detail: " + replaced.topic + ".");
      return;
    }
    // the same account sent again (after an answer that never came) keeps its operation id, so it is written once
    const request = { topic: name, text: text.value, evidence: [...picked].sort(), supersedes: supersedes.value || undefined };
    const key = JSON.stringify(request);
    if (!attempt || attempt.key !== key) attempt = { key, id: newOperationId() };
    ctx.act(async () => {
      const answer = await ctx.api("chronicle.remember", { chat, audience, actor: memory.actor, operation_id: attempt.id, ...request });
      attempt = null;
      topic.clear();
      text.clear();
      ctx.notify("Remembered.");
      done(answer);
      return true;
    });
  };
  if (!memory.citable.length) {
    return el("div", { class: "g-stack chronicle-form" }, el("h3", { class: "chronicle-sub" }, "Write an account"),
      ui.empty("This character holds no evidence to cite yet: an account must rest on something it saw or was told."));
  }
  return el("div", { class: "g-stack chronicle-form", role: "group", "aria-label": "Write an account" },
    el("h3", { class: "chronicle-sub" }, "Write an account"), topic.node, text.node,
    el("fieldset", { class: "chronicle-evidence" }, el("legend", {}, "It rests on"),
      el("ul", { class: "chronicle-lines" }, evidence),
      memory.citable_total > memory.citable.length ? el("p", { class: "g-muted" }, "Only the " + memory.citable.length + " newest items are offered.") : null),
    status, supersedes.node, ui.actions(ui.button("Remember this", save, { kind: "primary" })));
}

function clip(text) { return text.length > 60 ? text.slice(0, 59) + "…" : text; }

// -- the rest of Wave 8 ---------------------------------------------------------------------------------------

async function showElsewhere(slot, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const answer = await ctx.api("chronicle.coverage", {});
  const elsewhere = answer.features.filter((item) => item.status === "screen" && ctx.registered(item.route));
  const waiting = answer.features.filter((item) => item.status === "unavailable"
    || (item.status === "screen" && !ctx.registered(item.route)));
  const node = card(ui, "Campaign features",
    elsewhere.length ? el("div", { class: "g-stack" }, el("h3", { class: "chronicle-sub" }, "On other screens"),
      el("ul", { class: "chronicle-lines" }, elsewhere.map((item) => el("li", {},
        ui.link(item.title, item.route), el("span", { class: "g-muted" }, " · " + item.where))))) : null,
    el("div", { class: "g-stack" }, el("h3", { class: "chronicle-sub" }, "Not on a screen yet"),
      el("ul", { class: "chronicle-lines" }, waiting.map((item) => el("li", {}, ui.unavailable(item.title, item.reason))))));
  slot.replaceWith(node);
}
