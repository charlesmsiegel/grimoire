// The Rules page's recorded rolls for one campaign (Sprint 116 over 075): the rolls this view is shown, and rolling
// a check for a character of the chosen chat. A roll is reviewed first (rules.roll_plan draws nothing and names the
// attempt it records under), then rolled once and recorded before it is shown (rules.roll); asking again with the
// same request reads the same roll back and draws no die. The table rolls only for its own characters, backed by
// what the player typed; an NPC's roll is the director's.

import { campaignName } from "../campaign/logic.js";
import { fieldText, formValues, latest } from "../prep/logic.js";
import { card, group } from "./authoring.js";
import { actionText, diceText, newRequestId, planText, resultText } from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
const FAMILIES = [{ value: "d20", label: "D20" }, { value: "wod", label: "WoD" }, { value: "pbta", label: "PbtA" }];

export async function mountRolls(root, ctx, campaign) {
  const { ui } = ctx;
  const { el } = ui;
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
  ctx.setCrumb(ui.link("Campaigns", "campaign"), " / ", ui.link(name, "campaign", [campaign], chat ? { chat } : undefined), " / Rules");
  const address = (changes) => {
    const query = { chat, audience, ...changes };
    return ctx.href("rules", [campaign], { chat: query.chat || undefined, audience: query.audience === "director" ? "director" : undefined });
  };
  const views = ui.segments("Whose view", AUDIENCES, { value: audience, onchange: (value) => ctx.navigate(address({ audience: value })) });
  root.append(el("div", { class: "g-row rules-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Every recorded roll of this campaign, and rolls for the NPCs." : "Rolls your own characters' actions back. NPC rolls are the director's."),
  ctx.registered("campaign") ? ui.buttonLink("Campaign hub", "campaign", [campaign], { chat: chat || undefined, audience: audience === "director" ? "director" : undefined }) : null,
  ui.buttonLink("Mechanics modules", "rules")));
  if (chats.length > 1 || (chat && !chats.some((item) => item.chat === chat))) {
    const options = (chats.some((item) => item.chat === chat) ? chats : [{ chat, label: null }, ...chats])
      .map((item) => ({ value: item.chat, label: item.label || (item.launch && item.launch.name) || item.name || item.chat }));
    const picker = ui.choice("Chat", options, { value: chat, onchange: (value) => ctx.navigate(address({ chat: value })) });
    root.append(picker.node);
  }
  const cards = el("div", { class: "rules-cards" });
  root.append(cards);
  const listCard = card(ui, "Recorded rolls");
  const rollCard = card(ui, "Roll a check");
  cards.append(rollCard, listCard);
  let state = await ctx.api("rules.rolls", { campaign, chat: chat || undefined, audience });
  const drawList = () => showList(listCard, ctx, state, audience);
  const reread = async () => {
    state = await ctx.api("rules.rolls", { campaign, chat: chat || undefined, audience });
    drawList();
  };
  drawList();
  showRoller(rollCard, ctx, { campaign, chat, audience, complete: listing.complete }, state, reread);
}

function rollItem(ctx, roll) {
  const { ui } = ctx;
  const { el } = ui;
  const who = roll.name || roll.actor;
  return el("article", { class: "g-stack rules-roll", "aria-label": "Roll " + roll.attempt },
    el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, who + " · " + actionText(roll.family, roll.action)),
      ui.pill(roll.outcome, { kind: roll.outcome === "success" ? "good" : roll.outcome === "failure" ? "bad" : "accent" })),
    el("p", {}, diceText(roll.dice) + ": " + resultText(roll)),
    el("p", { class: "g-muted" }, "Attempt ", el("code", { class: "rules-ref" }, roll.attempt), " · 003 resolution: " + roll.resolution.result
      + " · " + roll.family + " " + roll.edition),
    roll.backing ? el("p", { class: "g-muted rules-backing" }, (roll.whose === "you" ? "Backed by your action" : "Backed by its player's action")
      + ", turn " + roll.backing.turn + ": “" + roll.backing.text + "”") : el("p", { class: "g-muted" }, "The director's roll: no player's action backs it."));
}

function showList(node, ctx, state, audience) {
  const { ui } = ctx;
  const { el } = ui;
  const rolls = [...state.rolls].reverse();  // newest first
  node.replaceChildren(el("h2", { class: "g-heading" }, "Recorded rolls"),
    el("p", { class: "g-muted" }, audience === "director" ? "Every roll on this campaign's record, newest first. A roll's faces are kept, never its result: the result is worked out from them again each time."
      : "The rolls your own actions back, newest first. The director's rolls are the director's."),
    rolls.length ? el("ul", { class: "rules-lines" }, rolls.map((roll) => el("li", {}, rollItem(ctx, roll))))
      : ui.empty(audience === "director" ? "Nothing is rolled in this campaign yet." : "None of your actions has been rolled yet."));
}

function showRoller(node, ctx, where, state, reread) {
  const { ui } = ctx;
  const { el } = ui;
  const { chat, audience, campaign } = where;
  node.append(ui.unavailable("A ruleset pinned for this campaign", state.module_note));
  if (!chat) {
    node.append(ui.empty(where.complete ? "This campaign has no chat in this library, so no one to roll for." : "None of this campaign's chats is among the most recent ones listed."));
    return;
  }
  const actors = state.actors || [];
  const body = el("div", { class: "g-stack" });
  const familyDraft = ctx.draft("rules:family:" + chat);
  const actorDraft = ctx.draft("rules:actor:" + chat + ":" + audience);
  let family = FAMILIES.some((item) => item.value === familyDraft.get()) ? familyDraft.get() : "d20";
  let actor = actors.some((item) => item.actor === actorDraft.get()) ? actorDraft.get() : (actors[0] || {}).actor || null;
  // the review on screen, kept until the roll is made or the person starts another; its request id is reused when
  // the person asks again, so asking again reads the same roll
  let review = null, rolled = null;
  let attempt = null;
  const gate = latest();
  const families = ui.segments("Ruleset family", FAMILIES, { value: family, onchange: (value) => { family = value; familyDraft.set(value); reset(); } });
  node.append(families.node, el("p", { class: "g-muted" }, state.no_roll), body,
    ...state.unavailable.map((item) => ui.unavailable(item.what, item.reason)));
  const reset = () => { gate.bump(); review = null; rolled = null; draw(); };

  function draw() {
    const profile = state.profiles.find((item) => item.family === family);
    const parts = [el("p", {}, el("strong", {}, profile.title), el("span", { class: "g-muted" }, " · " + profile.edition + " · "),
      el("code", { class: "rules-ref" }, profile.module))];
    if (!actors.length) {
      parts.push(ui.empty(audience === "director" ? "No one in this chat's cast is yours or an NPC to roll for." : "You play no character in this chat, so there is no one for you to roll for."));
      body.replaceChildren(...parts);
      return;
    }
    if (audience !== "director") parts.push(el("p", { class: "g-muted" }, "Only your own characters are listed: NPC rolls are the director's."));
    const who = ui.choice("Roll for", actors.map((item) => ({ value: item.actor,
      label: (item.name || item.actor) + (item.role === "npc" ? " (NPC)" : " (yours)") })),
    { value: actor, onchange: (value) => { actor = value; actorDraft.set(value); reset(); } });
    const member = actors.find((item) => item.actor === actor);
    const key = (name) => "rules:roll:" + chat + ":" + audience + ":" + actor + ":" + family + ":" + name;
    const inputs = {};
    const fields = profile.action.map((field) => {
      const draft = ctx.draft(key(field.name));
      let made;
      if (field.type === "choice") {
        made = ui.choice(field.label, [{ value: "", label: "Choose…" }, ...field.options.map((option) => ({ value: option, label: option }))],
          { value: draft.get() || "", onchange: (value) => { draft.set(value); } });
        made.read = () => made.select.value;
        made.set = (text) => { made.select.value = text; draft.set(text); };
      } else {
        made = ui.field(field.label, { draft, hint: field.type === "whole" ? "A whole number" + (field.note ? ", " + field.note : "") : field.note || undefined });
        made.read = () => made.value;
        made.set = (text) => { made.value = text; };
      }
      inputs[field.name] = made;
      return made.node;
    });
    // what the player's character does, in their own words: the action contribution the roll is backed by
    const words = member.role === "player" ? ui.field("What your character does", { draft: ctx.draft(key("text")), multiline: true,
      hint: "In your own words: the roll is recorded as backed by this action" }) : null;
    const choose = el("div", { class: "g-field-error", role: "alert", hidden: true });
    const read = () => {
      const action = formValues(profile.action, (name) => inputs[name].read());
      const unchosen = [];
      for (const field of profile.action) {
        const made = inputs[field.name];
        if (made.setError) made.setError(action.errors[field.name] || "");
        else if (action.errors[field.name]) unchosen.push(field.label);
      }
      choose.textContent = unchosen.length ? "Choose: " + unchosen.join(", ") + "." : "";
      choose.hidden = !unchosen.length;
      if (words) words.setError(words.value.trim() ? "" : "Say what your character does.");
      if (Object.keys(action.errors).length || (words && !words.value.trim())) return null;
      return { action: action.values, text: words ? words.value : undefined };
    };
    const example = () => {
      for (const field of profile.action) inputs[field.name].set(fieldText(field, profile.example[field.name]));
      reset();
    };
    const plan = () => {
      const values = read();
      if (!values) return;
      // the same request asked for again keeps its id (so a lost answer is never a second roll); a changed one is new
      const sent = JSON.stringify([actor, family, values]);
      if (!attempt || attempt.key !== sent) attempt = { key: sent, id: newRequestId() };
      const turn = gate.next();
      const args = { campaign, chat, audience, actor, family, request_id: attempt.id, action: values.action, text: values.text };
      ctx.act(async () => {
        const answer = await ctx.api("rules.roll_plan", args);
        if (!gate.current(turn)) return true;
        review = { args: { ...args, turn: answer.plan.turn || undefined, review: answer.plan.review }, plan: answer.plan };
        rolled = null;
        draw();
        const box = node.querySelector(".rules-review-title");
        if (box) box.focus();
        return true;
      });
    };
    parts.push(group(ui, "The check", who.node, el("fieldset", { class: "rules-fields" }, el("legend", {}, "The action, as you set it"), ...fields), words ? words.node : null, choose,
      ui.actions(ui.button("Fill in the example", example), ui.button("Review", plan, { kind: "primary" }))));
    if (review) parts.push(reviewBox());
    if (rolled) parts.push(rolledBox());
    body.replaceChildren(...parts);
  }

  // `args` is exactly what was reviewed: the first roll, and every time the person asks again for the same request
  function roll(args) {
    const turn = gate.next();
    ctx.act(async () => {
      let answer;
      try {
        answer = await ctx.api("rules.roll", args);
      } catch (error) {
        if (error && (error.reason === "changed" || error.reason === "already_rolled")) { review = null; rolled = null; draw(); }
        throw error;
      }
      // the roll is recorded: it is always shown, even when the person chose another character or family meanwhile
      const moved = !gate.current(turn);
      review = null;
      rolled = { ...answer, args };
      attempt = null;  // the next review is a new request; asking again reads this one back through "Ask again"
      draw();
      await reread();
      if (moved) ctx.notify("Recorded for " + (answer.roll.name || answer.roll.actor) + ": " + resultText(answer.roll) + ".");
      const heading = node.querySelector(".rules-rolled-title");
      if (heading) heading.focus();
      return true;
    });
  }

  function reviewBox() {
    const { plan } = review;
    const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "rules-sub rules-review-title", tabindex: "-1" }, "Review this roll"),
      el("p", {}, planText(plan)),
      el("p", {}, actionText(plan.family, plan.action), el("span", { class: "g-muted" }, " · " + plan.family + " " + plan.edition)),
      plan.backing ? el("p", { class: "rules-backing" }, "Backed by your action, given in turn " + plan.backing.turn + ": “" + plan.backing.text + "”")
        : el("p", { class: "g-muted" }, "Nothing backs it: it is the director's roll, so it can never bind narration about a player's attempt."),
      ...earlierLines(plan),
      plan.already ? el("p", {}, "This exact request is already rolled (" + resultText(plan.already) + "). Rolling reads it back: no die is drawn.")
        : el("p", { class: "g-muted" }, "Rolling draws the dice once and records them before anyone sees them: it appends to " + plan.files.join(", ")
          + ". The same request asked again reads the same roll back; another request under this attempt is refused."),
      ui.actions(ui.button("Cancel", () => { review = null; draw(); }), ui.button(plan.already ? "Read it back" : "Roll", () => roll(review.args), { kind: "primary" })));
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Review this roll");
    return box;
  }

  // 075 does not limit how many attempts an actor makes in a turn, so the review shows the ones already made
  function earlierLines(plan) {
    const earlier = plan.earlier || [];
    if (!earlier.length) return [];
    const who = plan.name || plan.actor;
    return [el("p", {}, who + " already rolled in this turn: " + earlier.map((item) => actionText(item.family, item.action) + ": "
      + resultText(item)).join("; ") + ". Rolling now is another attempt, recorded beside "
      + (earlier.length === 1 ? "that one" : "those") + "; nothing limits attempts in a turn yet.")];
  }

  function rolledBox() {
    const { roll: made, done } = rolled;
    const box = ui.card({ tag: "div" }, el("h3", { class: "rules-sub rules-rolled-title", tabindex: "-1" }, done.drawn ? "Rolled" : "Read back from the record"),
      el("p", { class: "rules-outcome" }, (made.name || made.actor) + " · " + diceText(made.dice) + ": " + resultText(made)),
      el("p", { class: "g-muted" }, done.drawn ? "Drawn once and recorded under attempt " + made.attempt + "."
        : "No die was drawn: this request was already rolled under attempt " + made.attempt + ", and this is that roll."),
      el("p", { class: "g-muted" }, "003 resolution: " + made.resolution.result + " for attempt " + made.resolution.attempt + ". No effect is applied, and no narration is checked against it yet."),
      ui.actions(ui.button("Ask again", () => roll(rolled.args)), ui.button("New roll", () => { attempt = null; reset(); })));
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "The roll");
    return box;
  }

  draw();
}
