// Captured companions (050, Sprint 115): #/companions/<campaign>?chat=<ChatRef>&audience=director. The director
// sees who trains whom (each companion by its own instance, its species, in the active party or the reserve), and
// records a capture: reviewed first (companions.capture_plan writes nothing), then made exactly as reviewed, once
// (companions.capture_commit, with the same attempt id at the same campaign sequence), so one ball is spent once
// however often the commit is retried. A character a person plays takes that person's own key, given knowingly
// here. Nothing else follows from a capture, and the page says so. The table's view shows nothing: companions and
// captures are the director's. The capture being written is a draft of this tab (ctx.draft).

import { OUTCOMES, PLACEMENTS, companionText, newAttemptId, planText, requestOf } from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
const NOTHING_ELSE = "A capture makes the creature the trainer's companion and nothing more: no NPC speaks for it, "
  + "nobody's control of any character changes, and no character learns of it.";

const playerName = (principal) => String(principal || "").replace(/^human:/, "");

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const campaign = ctx.path[0];
  const chat = ctx.query.get("chat");
  ctx.setTitle("Companions");
  if (campaign) {
    ctx.setCrumb(ui.link("Campaigns", "campaign"), " / ",
      ui.link("This campaign", "campaign", [campaign], chat ? { chat } : undefined));
  }
  const audience = ctx.query.get("audience") === "director" ? "director" : "table";
  const views = ui.segments("Whose view", AUDIENCES, { value: audience,
    onchange: (value) => ctx.navigate(ctx.href("companions", [campaign || ""], { chat: chat || undefined,
      audience: value === "director" ? "director" : undefined })) });
  root.append(el("div", { class: "g-row companions-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Who travels with whom, and the captures that made them companions." : "Companions are the director's.")));
  if (audience !== "director") {
    root.append(card(ui, "Companions", el("p", { class: "g-muted" },
      "Who has captured whom, and recording a capture, are the director's: switch to the director view.")));
    return;
  }
  if (!campaign || !chat || !chat.startsWith("campaign:" + campaign + "#")) {
    root.append(card(ui, "Companions", ui.empty("Open this from one of the campaign's chats: a capture happens in a chat's turn.")),
      ui.actions(ui.buttonLink("All campaigns", "campaign")));
    return;
  }

  const draft = ctx.draft("companions:" + chat);
  const form = readForm(draft.get());
  const save = () => { draft.set(JSON.stringify(form)); };
  let roster = await ctx.api("companions.roster", { chat, audience });
  // The capture under review: { args, plan }. It is committed exactly as reviewed, with its own attempt id and the
  // sequence it was reviewed at, so a commit retried after a lost answer finds the capture it already made.
  let review = null;
  let asked = 0;  // only the review asked last is drawn
  const rosterNode = el("div", {}), formNode = el("div", {});
  root.append(rosterNode, formNode);

  const reload = async () => { roster = await ctx.api("companions.roster", { chat, audience }); draw(); };

  const plan = () => ctx.act(async () => {
    const built = requestOf(form, { chat });
    if (built.error) { status.textContent = built.error; status.hidden = false; return; }
    status.hidden = true;
    const turn = ++asked;
    const args = { ...built.request, attempt_id: newAttemptId() };
    const answer = await ctx.api("companions.capture_plan", args);
    if (turn !== asked || ctx.signal.aborted) return;  // the form changed while it was reviewed: not that capture
    review = { args, plan: answer.plan, keys: new Set() };
    draw();
    const box = formNode.querySelector(".companions-review");
    if (box) box.focus();
  });
  const commit = () => ctx.act(async () => {
    const reviewed = review;
    const args = { ...reviewed.args, sequence: reviewed.plan.sequence, expected: reviewed.plan.etag, keys: [...reviewed.keys] };
    try {
      const answer = await ctx.api("companions.capture_commit", args);
      if (ctx.signal.aborted) return;
      review = null;
      roster = answer.roster;
      draft.clear();
      Object.assign(form, readForm(null));
      ctx.notify(answer.done.already_applied ? "That capture was already made; nothing was spent or written again."
        : answer.done.writes ? "The capture is made." : "Nothing was written.");
      draw();
    } catch (error) {
      if (error && error.reason === "changed") {  // the party or the campaign moved on: review it again
        review = null;
        await reload();
      }
      throw error;
    }
  });
  const status = el("div", { class: "g-field-error", role: "alert", hidden: true });

  // Draw, keeping the person's place: the control they were in is rebuilt, so focus goes to the control with
  // the same data-focus-key (every control of the form has one).
  function draw() {
    const active = document.activeElement;
    const focusKey = active && root.contains(active) && active.getAttribute ? active.getAttribute("data-focus-key") : null;
    rosterNode.replaceChildren(rosterCard());
    formNode.replaceChildren(review ? reviewCard() : captureCard());
    if (!focusKey || root.contains(document.activeElement)) return;
    const next = [...root.querySelectorAll("[data-focus-key]")].find((item) => item.getAttribute("data-focus-key") === focusKey);
    if (next && !next.disabled) next.focus({ preventScroll: true });
  }
  const keyed = (control, key) => { control.setAttribute("data-focus-key", "companions:" + key); return control; };

  function nameOf(actor) {
    const all = [...roster.people, ...roster.creatures, ...roster.trainers.flatMap((item) => item.companions)];
    const found = all.find((item) => item.actor === actor);
    return found ? found.name || found.actor : actor;
  }

  function rosterCard() {
    const parts = [];
    if (roster.problems.length) {
      parts.push(ui.notice("Some of the campaign could not be read: " + roster.problems.map((item) => item.code).join(", ") + ".", { kind: "bad" }));
    }
    if (roster.trainers.length) {
      parts.push(el("ul", { class: "companions-lines" }, roster.trainers.map((trainer) => el("li", { class: "g-stack companions-trainer" },
        el("strong", {}, (trainer.name || trainer.actor) + "'s companions"),
        el("ul", { class: "companions-lines" }, trainer.companions.map((item) => el("li", { class: "g-row" },
          el("span", { class: "g-grow" }, companionText(item)),
          ui.pill(item.placement === "active" ? "Active" : item.placement === "reserve" ? "Reserve" : "Neither", { kind: item.placement === "active" ? "accent" : "" }))))))));
    } else {
      parts.push(ui.empty("Nobody has captured a companion in this campaign yet."));
    }
    if (roster.creatures.length) {
      parts.push(el("h3", { class: "companions-sub" }, "Creatures nobody has captured"),
        el("ul", { class: "companions-lines" }, roster.creatures.map((item) => el("li", {},
          (item.name || item.actor) + (item.species ? " · " + item.species : "")))));
    }
    parts.push(el("p", { class: "g-muted" }, "Each companion is its own individual: a second creature of the same species is another companion, never a copy."),
      el("p", {}, NOTHING_ELSE));
    return card(ui, "Companions", ...parts);
  }

  function captureCard() {
    const changed = () => { save(); asked += 1; status.hidden = true; };
    const people = roster.people.map((item) => ({ value: item.actor, label: item.name || item.actor }));
    const creatures = roster.creatures.map((item) => ({ value: item.actor, label: (item.name || item.actor) + (item.species ? " (" + item.species + ")" : "") }));
    if (!people.length || !creatures.length) {
      return card(ui, "Record a capture", ui.empty(!creatures.length ? "No creature is left to capture in this campaign." : "Nobody in this campaign could be a trainer."));
    }
    const turns = roster.turns.filter((item) => !item.accepted);
    if (!turns.length) {
      return card(ui, "Record a capture", ui.empty(roster.turns.length
        ? "Every turn of this chat already holds an accepted change: play on, then record the capture at its turn."
        : "This chat has no turn yet: a capture is recorded at the turn it happened in."));
    }
    if (!people.some((item) => item.value === form.trainer)) form.trainer = "";
    if (!creatures.some((item) => item.value === form.creature)) form.creature = "";
    if (!turns.some((item) => item.turn === form.turn)) form.turn = turns[turns.length - 1].turn;
    // the devices offered are the trainer's: a new trainer redraws that one choice in place, never the whole
    // form, so the person stays in the Trainer box they just changed
    const deviceSlot = el("div", {});
    const drawDevice = () => {
      const devices = Object.entries(roster.holdings[form.trainer] || {}).filter(([, count]) => count > 0);
      if (form.device && !devices.some(([item]) => item === form.device)) form.device = "";
      const device = ui.choice("Device used", [{ value: "", label: "None" }, ...devices.map(([item, count]) => ({ value: item, label: item + " (" + count + " held)" }))],
        { value: form.device, onchange: (value) => { form.device = value; changed(); } });
      keyed(device.select, "device");
      deviceSlot.replaceChildren(device.node);
    };
    drawDevice();

    const trainer = ui.choice("Trainer", [{ value: "", label: "Choose…" }, ...people], { value: form.trainer, onchange: (value) => { form.trainer = value; changed(); drawDevice(); } });
    const creature = ui.choice("Creature", [{ value: "", label: "Choose…" }, ...creatures], { value: form.creature, onchange: (value) => { form.creature = value; changed(); } });
    const outcome = ui.choice("What happened", OUTCOMES, { value: form.outcome, onchange: (value) => { form.outcome = value; changed(); } });
    const capacity = ui.field("How many the trainer's active party holds", { value: form.capacity, type: "text", hint: "A whole number from 0 to " + roster.max_capacity + ": the director's call." });
    capacity.input.setAttribute("inputmode", "numeric");
    capacity.input.setAttribute("data-focus-key", "companions:capacity");
    capacity.input.addEventListener("input", () => { form.capacity = capacity.input.value; changed(); });
    const placement = ui.choice("Where it goes", PLACEMENTS, { value: form.placement, onchange: (value) => { form.placement = value; changed(); } });
    const turn = ui.choice("The turn it happened in", turns.map((item) => ({ value: item.turn,
      label: "Turn " + item.number + (item.open ? " (open)" : "") + (item.posts.length ? ": " + item.posts.map((post) => post.name || post.actor).join(", ") : "") })),
    { value: form.turn, onchange: (value) => { form.turn = value; changed(); } });
    keyed(trainer.select, "trainer");
    keyed(creature.select, "creature");
    keyed(outcome.select, "outcome");
    keyed(placement.select, "placement");
    keyed(turn.select, "turn");
    return card(ui, "Record a capture",
      el("p", { class: "g-muted" }, "Reviewed first: nothing is written until you make the change. A character a person plays takes that person's own key, from a post they made in the turn."),
      el("div", { class: "g-stack", role: "group", "aria-label": "The capture" }, trainer.node, creature.node, outcome.node, deviceSlot, capacity.node, placement.node, turn.node),
      status, ui.actions(keyed(ui.button("Review the capture", plan, { kind: "primary" }), "review")));
  }

  function reviewCard() {
    const reviewed = review;
    const parts = [el("p", {}, planText(reviewed.plan))];
    if (reviewed.plan.writes && !reviewed.plan.already_applied) {
      parts.push(el("p", { class: "g-muted" }, "It writes " + reviewed.plan.files.length + " file" + (reviewed.plan.files.length === 1 ? "" : "s") + ": " + reviewed.plan.files.join(", ")));
    }
    if (reviewed.plan.keys.length && !reviewed.plan.already_applied) {
      const fieldset = el("fieldset", { class: "companions-keys" }, el("legend", {}, "Each player's own key"));
      for (const item of reviewed.plan.keys) {
        const box = el("input", { type: "checkbox", value: item.player, "data-focus-key": "companions:key:" + item.player });
        box.checked = reviewed.keys.has(item.player);
        box.addEventListener("change", () => { if (box.checked) reviewed.keys.add(item.player); else reviewed.keys.delete(item.player); });
        fieldset.append(el("label", { class: "g-row companions-check" }, box,
          el("span", {}, playerName(item.player) + " agrees, for " + (item.name || nameOf(item.actor)) + ", whom they play")));
      }
      parts.push(fieldset, el("p", { class: "g-muted" }, "Hand the keyboard to each player to tick their own box: a capture never speaks for a player's character without them."));
    }
    parts.push(el("p", {}, NOTHING_ELSE),
      ui.actions(keyed(ui.button("Cancel", () => { review = null; asked += 1; draw(); }), "cancel"),
        reviewed.plan.writes || reviewed.plan.already_applied ? keyed(ui.button("Make this change", commit, { kind: "primary" }), "make") : null));
    const box = ui.card({ kind: "accent", tag: "section", label: "Review this capture" }, el("h2", { class: "g-heading" }, "Review this capture"), ...parts);
    box.classList.add("companions-card", "companions-review");
    box.setAttribute("tabindex", "-1");
    return box;
  }

  draw();
}

// The saved form, read back (anything unreadable starts blank).
function readForm(text) {
  let saved = null;
  try { saved = JSON.parse(text); } catch (error) { saved = null; }
  const value = (key, fallback) => (saved && typeof saved[key] === "string" ? saved[key] : fallback);
  return { trainer: value("trainer", ""), creature: value("creature", ""), outcome: value("outcome", "success"),
    device: value("device", ""), capacity: value("capacity", ""), placement: value("placement", "auto"), turn: value("turn", "") };
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("companions-card");
  return node;
}
