// The campaign hub (Sprint 113): one chat's campaign situation as the table or the director sees it. The view
// shows what `campaign.hub` answered for its audience and nothing more; logic.js says which cards a view gets.
// An actor's beliefs are always shown as that actor's, never as what is true. Actions the app does not offer
// are named with what they wait on, never shown as buttons.

import { campaignName, chatName, count, hubSections, isDirector, knowledgeTabs, leaning, pendingEffects } from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
const NOTES = { table: "What the table can see. World truth is the director's.",
  director: "World truth, every effect and every actor's powers and holdings." };
const KINDS = ["begin", "end"];         // 021 operation kinds
const LINE_KINDS = ["add", "consume"];  // the holdings lines this page writes (move and adjust are not here yet)
const TOP = new Set(["action", "recover"]), GRID = new Set(["cast", "holdings", "changes", "obstacles", "truth", "goals", "knowledge"]);

export async function showHub(root, ctx, campaign, asked, audience) {
  const { ui } = ctx;
  const { el } = ui;
  const listing = await ctx.api("campaign.chats", { campaign });
  const found = listing.campaigns.find((item) => item.campaign === campaign) || null;
  const chats = found ? found.chats : [];
  const chat = asked || (chats.length ? chats[0].chat : null);  // with no chat named: the one played last
  const name = found ? campaignName(found) : campaign;
  ctx.setTitle(name);
  ctx.setCrumb(ui.link("Campaigns", "campaign"), " / " + name);
  const address = (changes) => {
    const query = { chat, audience, ...changes };
    return ctx.href("campaign", [campaign], { chat: query.chat || undefined, audience: query.audience === "director" ? "director" : undefined });
  };

  const meta = found ? (found.world || "No world") + " · " + count(chats.length, "chat") : campaign;
  const cover = ui.cover({ key: campaign }, el("div", {}, el("div", { class: "campaign-cover-title" }, name),
    el("div", { class: "campaign-cover-meta" }, meta)));
  const shown = chats.some((item) => item.chat === chat) || !chat ? chats : [{ chat, label: null, name: null, problem: null }, ...chats];
  const chips = shown.map((item) => {
    const chip = el("a", { class: "g-select g-chip campaign-chip", href: address({ chat: item.chat }), title: item.chat },
      chatName(item), item.problem ? ui.pill(item.problem, { kind: "bad" }) : null);
    if (item.chat === chat) chip.setAttribute("aria-current", "page");
    return chip;
  });
  const nav = el("div", { class: "g-row" }, ui.buttonLink("All campaigns", "campaign", [], undefined, { size: "small" }),
    chats.length ? el("span", { class: "g-muted" }, count(chats.length, "chat") + " in this campaign") : null,
    el("nav", { class: "g-chips", "aria-label": "This campaign's chats" }, chips));
  root.append(el("div", { class: "g-stack campaign-top" }, cover, nav));
  if (!listing.complete) root.append(ui.notice("Only the most recent chats are listed."));
  if (!chat) {
    root.append(ui.empty(listing.complete ? "This campaign has no chats in this library."
      : "None of this campaign's chats is among the most recent ones listed."));
    return;
  }

  const views = ui.segments("Whose view", AUDIENCES, { value: audience, onchange: (value) => ctx.navigate(address({ audience: value })) });
  const view = el("div", { class: "g-stack campaign-hub" });
  root.append(el("div", { class: "g-row campaign-views" }, views.node, el("span", { class: "g-grow g-muted" }, NOTES[audience]),
    ctx.registered("chat") ? ui.buttonLink("Open the chat", "chat", [chat]) : null), view);

  const call = (op, args) => ctx.api("campaign." + op, { chat, audience, ...args });
  // Every write answers the hub as it now stands: show that, never the one from before the write.
  // `said` is a notice, or a function of what the write did; `succeeded` runs once it is done, before the hub is
  // drawn again (so a field it clears is drawn empty).
  const write = (op, args, said, succeeded) => ctx.act(async () => {
    const answer = await call(op, args);
    if (succeeded) succeeded(answer.done);
    const notice = typeof said === "function" ? said(answer.done) : said;
    if (notice) ctx.notify(notice);
    if (answer.hub) render(answer.hub);
    else {
      ctx.notify("Done, but the campaign could not be read again: " + problemsText(answer.problems), { bad: true });
      render(await call("hub", {}));
    }
    return true;  // ctx.act answers undefined for a refusal: what was typed is kept for another try
  });
  // The turns an effect can be proposed for: the chat's kept turns, and the open action's (from the core `open`
  // op, which the hub does not repeat: the transcript is the chat page's). Asked only for the director's view.
  let turns = [];
  const numbers = new Map();
  const open = async () => {
    const [hub, scene] = await Promise.all([call("hub", {}), audience === "director" && ctx.has("open") ? ctx.api("open", { chat }) : null]);
    if (scene) {
      for (const turn of scene.turns) numbers.set(turn.turn, turn.number);
      turns = scene.turns.filter((turn) => turn.kept).map((turn) => ({ turn: turn.turn,
        label: "Turn " + turn.number + (turn.post ? ": " + clip(turn.post.text) : "") }));
    }
    render(hub);
  };

  // The proposal being built is kept across re-renders (another write, a refusal) while what it was built from
  // (the cast, the turns it may name, whether proposing is available) is unchanged; a proposal made clears it.
  let built = null;
  const proposal = (hub) => {
    const can = hub.actions.find((item) => item.op === "propose_effect");
    const key = JSON.stringify([hub.cast.map((member) => [member.actor, member.role]), turns, hub.open_action && hub.open_action.outcome
      ? hub.open_action.turn : null, can && can.available]);
    if (!built || built.key !== key) built = { key, node: proposeCard(ctx, hub, chat, turns, write, () => { built = null; }) };
    return built.node;
  };

  // Whose knowledge is showing: kept across re-renders, and read again each time the hub is drawn.
  const knowing = { actor: "", asked: 0 };

  function render(hub) {
    const director = isDirector(hub, audience);
    const names = new Map(hub.cast.map((member) => [member.actor, member.name]));
    const nameOf = (ref) => (ref ? names.get(ref.ref) || ref.name || ref.ref : "nobody");
    for (const turn of hub.turns) numbers.set(turn.turn, turn.number);
    const at = { ctx, hub, nameOf, write, director, call, knowing, chat, numberOf: (turn) => numbers.get(turn) };
    const make = { action: actionCard, recover: recoverCard, cast: castCard, holdings: holdingsCard, changes: changesCard,
      obstacles: obstaclesCard, truth: truthCard, goals: goalsCard, knowledge: knowledgeCard, propose: () => proposal(hub),
      turns: turnsCard, sources: sourcesCard, unavailable: unavailableCard };
    const top = [], grid = [], rest = [];
    for (const id of hubSections(hub, audience)) {
      const node = make[id](at);
      if (node) (TOP.has(id) ? top : GRID.has(id) ? grid : rest).push(node);
    }
    const notices = [];
    if (hub.paused) notices.push(ui.notice("Play is paused in this chat.", { kind: "bad" }));
    for (const problem of hub.problems) notices.push(ui.notice(problem.code + (problem.detail ? ": " + problem.detail : "")));
    view.replaceChildren(...notices, ...top, el("div", { class: "campaign-cards" }, grid), ...rest);
  }

  await open();
}

// A card of the hub: a section named by its title, so a screen reader lists the hub's parts.
function card(ui, title, { kind = "", heading = true, wide = false } = {}, ...children) {
  const own = kind === "private" || kind === "alert";  // this page's own kinds: the director's alone, and a problem
  const node = ui.card({ tag: "section", kind: own ? "" : kind, label: title },
    heading ? ui.el("h2", { class: "g-heading" }, title) : null, ...children);
  node.classList.add("campaign-card");
  if (own) node.classList.add("campaign-" + kind);
  if (wide) node.classList.add("campaign-wide");
  return node;
}

function sub(ui, text) { return ui.el("h3", { class: "campaign-sub" }, text); }

// Lines under one another, each divided from the next.
function lines(ui, items, { empty } = {}) {
  if (!items.length) return empty ? ui.el("p", { class: "g-muted" }, empty) : null;
  return ui.el("ul", { class: "campaign-lines" }, items.map((item) => ui.el("li", {}, item)));
}

function line(ui, title, detail) {
  return [ui.el("span", {}, title), detail ? ui.el("span", { class: "g-muted campaign-small" }, " · " + detail) : null];
}

// -- the open action --------------------------------------------------------------------------------------

function actionCard({ ctx, hub, nameOf, write, director, numberOf }) {
  const { ui } = ctx;
  const { el } = ui;
  const action = hub.open_action;
  const can = Object.fromEntries(hub.actions.map((item) => [item.op, item]));
  if (!action) return card(ui, "Open action", {}, ui.empty("No action is waiting to be adjudicated."));
  const { label, framed, outcome } = action;
  const number = numberOf(action.turn);
  const parts = [el("div", { class: "g-row" }, ui.eyebrow("Open action · turn " + (number || action.turn), { accent: true }),
    ui.pill(label ? "Labelled: " + label.label + " (" + label.state + ")" : "Not labelled yet"))];
  parts.push(el("h2", { class: "campaign-action-title" }, framed ? framed.intent : nameOf(action.actor) + " is acting"));
  const said = [framed ? nameOf(action.actor) + " is acting" : null, label && label.reason ? label.reason : null,
    label && label.against ? "Against " + obstacleName(hub, label.against.obstacle) + (label.against.approach ? " by " + label.against.approach : "")
      + (label.against.novel ? " (a new approach)" : "") : null,
    framed ? "Framed as " + framed.classification + " · allows " + framed.allowed.join(", ") : null].filter(Boolean);
  if (said.length) parts.push(el("p", { class: "g-muted" }, said.join(" · ")));

  // What deciding rests on is ticked where it is read: the frame's factors and stakes, once the action is framed.
  const results = !framed || outcome ? [] : director ? framed.allowed : framed.allowed.filter((result) => result !== "success");
  const deciding = Boolean(framed && !outcome && can.decide && can.decide.available && results.length);
  const read = framed || label;  // what confirming frames, read before it is locked in
  const weigh = read ? weighed(ui, read.factors, read.stakes, deciding) : null;
  if (weigh && weigh.node) parts.push(weigh.node);

  if (outcome) {
    parts.push(el("div", { class: "campaign-band" }, "Outcome: " + outcome.result + " (" + outcome.basis + ")"
      + (outcome.decided_by ? ", decided by " + outcome.decided_by : "") + (outcome.consented_by ? ", consented by " + outcome.consented_by : "")));
    return card(ui, "Open action", { kind: "accent", heading: false }, ...parts);
  }
  if (can.label && can.label.available) {
    const choices = [{ value: "", label: "No obstacle" }, ...hub.obstacles.filter((item) => item.status !== "overcome")
      .map((item) => ({ value: item.obstacle, label: item.goal }))];
    const against = ui.choice("Against", choices);
    parts.push(el("div", { class: "campaign-row" }, against.node, ui.button(label ? "Label again" : "Label this action", () =>
      write("label", against.value ? { obstacle: against.value } : {}))));
  } else if (can.label) parts.push(ui.unavailable("Labelling this action", can.label.reason));
  if (label && !framed) {
    if (director) parts.push(el("div", { class: "campaign-band" }, "Waiting on the table to confirm how this was read."));
    else {
      parts.push(available(ctx, can.confirm, "Confirming the labelling", () => el("div", { class: "g-row campaign-band" },
        el("span", { class: "g-grow" }, "Waiting on you to confirm how this was read."),
        ui.button("Confirm this labelling", () => write("confirm", {}, "Labelling confirmed."), { kind: "primary" }))));
    }
  }
  if (framed) {
    if (!director) parts.push(el("div", { class: "campaign-band" }, "Labelling confirmed. Waiting on the director's decision."));
    parts.push(decideForm(ctx, framed, can.decide, write, director, results, weigh));
  }
  return card(ui, "Open action", { kind: "accent", heading: false }, ...parts);
}

function obstacleName(hub, id) {
  const found = hub.obstacles.find((item) => item.obstacle === id);
  return found ? found.goal : id;
}

// A labelling's or a frame's factors (each with how it bears) and its stakes, each said in full. With `pick`,
// each has a tick: the factors a decision cites and the stakes it brings.
function weighed(ui, factors, stakes, pick) {
  const { el } = ui;
  const ticked = { cites: [], consequences: [] };
  const row = (into, item, detail, tone) => {
    const text = el("span", { class: "g-grow" }, item.text);
    const end = detail ? el("span", { class: "campaign-bearing" + (tone ? " campaign-" + tone : "") }, detail) : null;
    if (!pick) return el("div", { class: "campaign-weigh-row" }, text, end);
    const input = el("input", { type: "checkbox", value: item.id });
    into.push(input);
    return el("label", { class: "campaign-weigh-row" }, input, text, end);
  };
  const column = (title, rows) => (rows.length ? el("div", { class: "campaign-weigh-col", role: "group", "aria-label": title },
    el("div", { class: "campaign-weigh-title" }, title), rows) : null);
  const columns = [column("What bears on it", factors.map((factor) => row(ticked.cites, factor, factor.bearing, leaning(factor.bearing)))),
    column("At stake", stakes.map((stake) => row(ticked.consequences, stake, null, "")))].filter(Boolean);
  const value = (inputs) => inputs.filter((input) => input.checked).map((input) => input.getAttribute("value"));
  return { node: columns.length ? el("div", { class: "campaign-weigh" }, columns) : null, picks: pick && columns.length > 0,
    cites: () => value(ticked.cites), consequences: () => value(ticked.consequences) };
}

// The director adjudicates; the acting player may instead consent to a setback, naming the frame they were shown.
function decideForm(ctx, framed, can, write, director, results, weigh) {
  const { ui } = ctx;
  const { el } = ui;
  if (!can || !can.available) return ui.unavailable(director ? "Deciding this action" : "Consenting to a setback", can && can.reason);
  if (!results.length) return ui.empty("Only the director decides this action.");
  const result = ui.choice(director ? "Outcome" : "Consent to", results.map((value) => ({ value, label: value })));
  result.select.setAttribute("data-focus-key", "campaign:result");
  const go = ui.button(director ? "Decide" : "Consent", async () => {
    const args = { result: result.value, cites: weigh.cites(), consequences: weigh.consequences() };
    if (director) return write("decide", { ...args, basis: "adjudicated" }, "Decided: " + result.value + ".");
    const yes = await ui.confirm("Consent to " + result.value + " for this action, as framed now? This decides it.", { confirmLabel: "Consent" });
    if (yes) return write("decide", { ...args, basis: "consent", fingerprint: framed.fingerprint }, "Consented: " + result.value + ".");
  }, { kind: "primary" });
  return el("div", { class: "campaign-row" }, result.node, go,
    weigh.picks ? el("span", { class: "g-muted" }, "Tick what the decision rests on above, and the stakes it brings.") : null);
}

// -- cast, holdings, obstacles ----------------------------------------------------------------------------

function castCard({ ctx, hub, nameOf }) {
  const { ui } = ctx;
  const { el } = ui;
  const rows = hub.cast.map((member) => {
    const role = member.role === "player" ? (member.controller === hub.player ? "your character" : "another player's character")
      : member.role === "npc" ? "the director's" : "no one seated";
    const where = member.absent ? "absent (" + member.absent.via + ")" : member.on_stage ? "on stage" : "off stage";
    return el("div", { class: "g-row campaign-member" }, ui.avatar(member.name || member.actor, { key: member.actor }),
      el("span", { class: "g-grow" }, el("span", { class: "campaign-name" }, member.name || member.actor),
        el("span", { class: "g-muted campaign-role" }, role + (hub.persona === member.actor ? " · persona" : ""))),
      ui.pill(where));
  });
  const party = hub.party.memberships.map((item) => line(ui, nameOf(item.member), item.status + " in " + nameOf(item.party) + " since " + item.since));
  const parts = [rows.length ? rows : ui.empty("No one is in this chat's cast.")];
  if (party.length) parts.push(sub(ui, "Party"), lines(ui, party));
  const stage = hub.stage;  // named under "Not available yet" when the chat has none
  if (stage) {
    const label = new Map(stage.areas.map((area) => [area.id, area.label]));
    const placed = stage.placements.map((item) => line(ui, nameOf(item.who), "in " + (label.get(item.area) || item.area)
      + (item.holder ? ", held by " + nameOf(item.holder) : "") + " (" + item.basis + ")"));
    const ways = stage.ways.map((way) => line(ui, way.sides.map((side) => label.get(side) || side).join(" to "), way.state));
    parts.push(sub(ui, "Stage"), lines(ui, placed, { empty: "No one is placed." }), lines(ui, ways));
  }
  return card(ui, "Cast and stage", {}, ...parts);
}

function holdingsCard({ ctx, hub, nameOf, director }) {
  const { ui } = ctx;
  const { el } = ui;
  const actors = [...new Set([...Object.keys(hub.holdings), ...Object.keys(hub.items), ...Object.keys(hub.abilities)])];
  const blocks = actors.map((actor) => {
    const counts = Object.entries((hub.holdings[actor] || { counts: {} }).counts).map(([item, n]) => item + " × " + n);
    const items = hub.items[actor];
    const held = items ? items.held.map((item) => item.name || item.item) : [];
    const equipped = items ? Object.entries(items.equipped).map(([slot, found]) => slot + ": " + found.map((item) => item.name || item.item).join(", ")) : [];
    const powers = (hub.abilities[actor] || []).map((ability) => el("div", {}, el("strong", {}, ability.name || ability.ability),
      el("span", { class: "g-muted campaign-small" }, " · " + abilityText(ability))));
    return el("div", { class: "campaign-actor" }, el("div", { class: "campaign-name" }, nameOf({ ref: actor })),
      el("dl", { class: "campaign-facts" },
        el("dt", {}, "Holdings"), el("dd", {}, counts.length ? counts.join(", ") : "none"),
        el("dt", {}, "Items"), el("dd", {}, (held.length ? held.join(", ") : "none") + (equipped.length ? " · equipped " + equipped.join("; ") : "")),
        el("dt", {}, "Powers"), el("dd", {}, powers.length ? powers : "none")));
  });
  return card(ui, "Holdings and powers", {}, el("p", { class: "g-muted" }, director ? "Every actor's." : "Your characters'."),
    blocks.length ? blocks : ui.empty("No character to show."));
}

function abilityText(ability) {
  const effects = ability.effects.map((effect) => effect.text + " (" + [effect.tags.join("/"), effect.range, effect.certainty]
    .filter(Boolean).join(", ") + ")");
  const listed = (label, values) => values.length ? label + " " + values.map((value) => value.text).join("; ") : null;
  return [ability.description || null, effects.length ? "permits " + effects.join("; ") : null, listed("limits", ability.limits),
    listed("costs", ability.costs), listed("needs", ability.prerequisites)].filter(Boolean).join(" · ") || "no effects listed";
}

function obstaclesCard({ ctx, hub }) {
  const { ui } = ctx;
  const { el } = ui;
  const blocks = hub.obstacles.map((obstacle) => el("div", { class: "campaign-obstacle" },
    el("div", { class: "g-row g-between" }, el("strong", { class: "g-grow" }, obstacle.goal), ui.pill(obstacle.status)),
    obstacle.problem ? ui.notice(obstacle.problem) : null,
    lines(ui, obstacle.approaches.map((approach) => line(ui, approach.text,
      [approach.risks.length ? "risks " + approach.risks.join(", ") : null,
        approach.costs.length ? "costs " + approach.costs.map((cost) => cost.text).join("; ") : null].filter(Boolean).join(" · ")))),
    obstacle.facts.length ? lines(ui, obstacle.facts.map((fact) => line(ui, fact.text, fact.kind))) : null,
    obstacle.attempts.length ? lines(ui, obstacle.attempts.map((attempt) => line(ui, "Attempt by " + attempt.approach,
      attempt.status + (attempt.result ? ": " + attempt.result : "")))) : null,
    obstacle.due.length ? el("p", { class: "g-muted" }, "Due from its attempts: " + obstacle.due.map((item) => item.text).join("; ")) : null));
  return card(ui, "Obstacles", {}, blocks);
}

function sourcesCard({ ctx, hub, nameOf, chat }) {
  const { ui } = ctx;
  const { el } = ui;
  const rows = hub.sources.map((source) => {
    const state = source.broken ? "broken: " + source.broken
      : source.behind ? "pinned behind its source (now " + (source.head || "unknown") + ")"
      : source.pinned ? "pinned at " + source.resolved : "follows its source (" + (source.resolved || "unresolved") + ")";
    const notices = source.notices.length ? " · " + source.notices.map((item) => item.code).join(", ") : "";
    const row = el("div", {}, el("div", {}, (source.actor ? nameOf(source.actor) + ": " : "") + source.role),
      el("div", { class: "g-muted campaign-small campaign-break" }, source.binding + " · " + state + (source.problem ? " · " + source.problem : "") + notices));
    if (source.handoff) {
      // 042's rebase review is the import and review screen's (110), on its source-update tab for this chat:
      // link to it only where that page is in the app. The hub rebases nothing.
      row.append(ctx.registered("import") ? ui.link("Review the update", "import", [], { tab: "rebase", chat })
        : ui.unavailable("Reviewing this rebase", "the review screen is Sprint 110's"));
    }
    return row;
  });
  return card(ui, "Sources", { wide: true }, lines(ui, rows, { empty: "No source is bound to this chat." }));
}

// -- effects ----------------------------------------------------------------------------------------------

// The effects still waiting on a key, each shown in full beside the key it waits on.
function changesCard(at) {
  const { ui } = at.ctx;
  const can = Object.fromEntries(at.hub.actions.map((item) => [item.op, item]));
  const blocks = pendingEffects(at.hub).map(({ turn, effect }) => effectBlock(at, turn, effect, can, true));
  return card(ui, "Changes waiting on approval", {}, blocks.length ? blocks : ui.empty("No change is waiting on a key."));
}

// Every turn that was labelled or given an effect: its labellings, the effects no longer waiting, and (for the
// director) what each NPC pursued.
function turnsCard(at) {
  const { ctx, hub, nameOf } = at;
  const { ui } = ctx;
  const { el } = ui;
  const blocks = hub.turns.map((turn) => {
    const parts = [el("h3", { class: "campaign-sub" }, "Turn " + turn.number + (turn.open ? " (open)" : turn.kept ? " (kept)" : ""))];
    if (turn.post) parts.push(el("p", { class: "g-muted" }, nameOf(turn.post.actor) + " " + turn.post.kind + (turn.post.withheld ? " (withheld)" : "")));
    for (const label of turn.labels) parts.push(el("p", { class: "g-muted" }, "Labelled " + label.label + " (" + label.state + ")"));
    for (const effect of turn.effects) {
      const waits = effect.state === "proposed" || effect.state === "approved";
      parts.push(waits ? el("p", { class: "g-muted" }, "Effect " + effect.number + " (" + effect.state + ") is waiting on approval, above.")
        : effectBlock(at, turn, effect, {}, false));
    }
    for (const intention of turn.intentions || []) {
      parts.push(el("div", { class: "campaign-intention" }, el("strong", {}, nameOf(intention.actor) + " (take " + intention.take + "): "),
        intention.goal ? "pursued " + intention.goal + (intention.approach ? " by " + intention.approach : "") : "pursued nothing",
        intention.refusals.length ? "; refused " + intention.refusals.join(", ") : "",
        ...intention.explanation.map((text) => el("div", { class: "g-muted" }, text))));
    }
    return el("div", { class: "campaign-turn-block" }, ...parts);
  });
  return card(ui, "Turns and effects", { wide: true }, blocks.length ? blocks : ui.empty("No turn has been labelled or given an effect yet."));
}

// One holdings line in full, as the key on it covers it: who gives, who gets, how many, and who acts. Every
// field the line carries is said, so two different changes never read the same beside "Endorse as shown".
function holdingLine(item, nameOf) {
  const counted = (item.amount ?? "?") + " " + item.item;
  const said = item.kind === "add" ? "add " + counted + " to " + nameOf(item.holder)
    : item.kind === "consume" ? "consume " + counted + " from " + nameOf(item.holder)
    : item.kind === "move" ? "move " + counted + " from " + nameOf(item.holder) + " to " + nameOf(item.target)
    : item.kind === "adjust" ? "adjust " + nameOf(item.holder) + "'s " + item.item + " from " + item.seen + " to " + item.to
    : item.kind + " " + counted + " (" + nameOf(item.holder) + ")";
  return said + (item.agent ? " (by " + nameOf(item.agent) + ")" : "");
}

function effectBlock({ ctx, hub, nameOf, write, director }, turn, effect, can, waiting) {
  const { ui } = ctx;
  const { el } = ui;
  const ops = effect.operations.map((op) => op.kind + " " + op.predicate + ": " + nameOf(op.subject) + " → " + nameOf(op.object)
    + (op.agent ? " (by " + nameOf(op.agent) + ")" : ""));
  const held = effect.holdings ? effect.holdings.lines.map((item) => holdingLine(item, nameOf)) : [];
  const keys = effect.endorsers.map((key) => el("span", { class: "campaign-key" }, (key.principal === hub.player ? "Your key" : key.principal) + ": ",
    el("strong", { class: key.given ? "campaign-given" : "" }, key.given ? "given" : "not given")));
  if (!keys.length) keys.push(el("span", { class: "campaign-key" }, "No player's key is needed"));
  keys.push(el("span", { class: "campaign-key" }, "Director: ", el("strong", { class: effect.approved_by ? "campaign-given" : "" }, effect.approved_by ? "approved" : "not yet")));
  const box = el("div", { class: "campaign-effect" },
    waiting ? el("div", { class: "g-muted campaign-small" }, "Turn " + turn.number + " · effect " + effect.number + " · " + effect.state) : null,
    waiting ? el("p", { class: "campaign-summary" }, effect.summary)
      : el("p", {}, el("strong", {}, "Effect " + effect.number + " (" + effect.state + "): "), effect.summary),
    ops.length || held.length ? el("ul", { class: "campaign-ops" }, [...ops, ...held].map((text) => el("li", {}, text))) : null,
    effect.holdings && effect.holdings.note ? el("p", { class: "g-muted" }, "Holdings note: " + effect.holdings.note) : null,
    effect.waivers.length ? el("p", { class: "g-muted" }, "Waived: " + effect.waivers.map((item) => item.change + " (" + item.reason + ")").join("; ")) : null,
    el("div", { class: "g-row campaign-keys" }, keys));
  if (!waiting) return box;
  const mine = effect.endorsers.some((key) => key.principal === hub.player && !key.given);
  const args = { turn: turn.turn, number: effect.number };
  // Two keys, two calls: the player endorses exactly what is shown here; the director approves. One person may
  // hold both, but this page never gives one key for the other.
  if (!director && mine) {
    put(box, available(ctx, can.endorse_effect, "Endorsing this effect", () => ui.actions(ui.button("Endorse as shown", async () => {
      if (await ui.confirm("Give your key to effect " + effect.number + ", exactly as shown: " + effect.summary, { confirmLabel: "Endorse" })) {
        write("endorse_effect", { ...args, fingerprint: effect.fingerprint }, "Your key is given.");
      }
    }, { kind: "primary" }))));
  }
  if (director && !effect.approved_by) {
    const last = effect.endorsers.every((key) => key.given);
    put(box, available(ctx, can.approve_effect, "Approving this effect", () => ui.actions(ui.button(last ? "Approve and accept" : "Approve", async () => {
      const message = last ? "Approve effect " + effect.number + "? Every player's key is given, so this accepts it as world truth. It cannot be undone here."
        : "Approve effect " + effect.number + "? It is accepted once every player's key is given.";
      if (await ui.confirm(message, { confirmLabel: "Approve", danger: last })) write("approve_effect", args, last ? "Accepted." : "Approved.");
    }, { kind: "primary" }))));
  }
  return box;
}

// The director proposes a turn's effect whole, in structured form: what an obstacle attempt made due comes
// first, by itself; operations and holdings lines are added here. Nothing is proposed from narration.
function proposeCard(ctx, hub, chat, kept, write, made) {
  const { ui } = ctx;
  const { el } = ui;
  const title = "Propose an effect";
  const can = hub.actions.find((item) => item.op === "propose_effect");
  if (!can || !can.available) return card(ui, title, { wide: true }, ui.unavailable("Proposing an effect", can && can.reason));
  // An effect is proposed for a kept turn or a decided action (035): the open turn is offered only once its
  // action is decided, and the newest kept turn is the default.
  const options = kept.map((item) => ({ value: item.turn, label: item.label }));
  const action = hub.open_action;
  if (action && action.outcome && !options.some((item) => item.value === action.turn)) {
    options.push({ value: action.turn, label: "The open action's turn (decided)" });
  }
  if (!options.length) return card(ui, title, { wide: true }, ui.empty("No turn is kept yet: an effect is proposed for a kept turn or a decided action."));
  const turnChoice = ui.choice("For turn", options, { value: (kept.length ? kept[kept.length - 1].turn : options[0].value) });
  const summary = ui.field("What happens", { draft: ctx.draft("summary:" + chat), multiline: true });
  const cast = hub.cast.map((member) => ({ value: member.actor, label: member.name }));
  const operations = [];
  const opsBox = el("div", {});
  const addOp = ui.button("Add an operation", () => {
    const kind = ui.choice("Kind", KINDS.map((value) => ({ value, label: value })));
    const predicate = ui.field("Relation", { hint: "A 017 relation, such as knows" });
    const subject = ui.choice("Subject", cast);
    const object = ui.choice("Object", cast);
    const agent = ui.choice("Done by", [{ value: "", label: "No one (the world)" }, ...cast]);
    const row = el("div", { class: "campaign-row" }, kind.node, predicate.node, subject.node, object.node, agent.node);
    const entry = { kind, predicate, subject, object, agent, row };
    row.append(ui.button("Remove", () => { operations.splice(operations.indexOf(entry), 1); row.remove(); }));
    operations.push(entry);
    opsBox.append(row);
  }, { kind: "dashed" });
  const held = [];
  const linesBox = el("div", {});
  const addLine = ui.button("Add a holdings line", () => {
    const kind = ui.choice("Kind", LINE_KINDS.map((value) => ({ value, label: value })));
    const holder = ui.choice("Holder", cast);
    const item = ui.field("Item");
    const amount = ui.field("Amount", { type: "number", value: "1" });
    const agent = ui.choice("Done by", [{ value: "", label: "No one (the world)" }, ...cast]);
    const row = el("div", { class: "campaign-row" }, kind.node, holder.node, item.node, amount.node, agent.node);
    const entry = { kind, holder, item, amount, agent, row };
    row.append(ui.button("Remove", () => { held.splice(held.indexOf(entry), 1); row.remove(); }));
    held.push(entry);
    linesBox.append(row);
  }, { kind: "dashed" });
  const players = new Set(hub.cast.filter((member) => member.role === "player").map((member) => member.actor));
  // A player character's act is backed by that turn's own post (021: only the player authors it).
  const acted = (entry, turn, item) => {
    if (!entry.agent.value) return item;
    return { ...item, agent: entry.agent.value, ...(players.has(entry.agent.value) ? { backed_by: turn } : {}) };
  };
  const go = ui.button("Propose", () => {
    const turn = turnChoice.value;
    if (!summary.value.trim()) { summary.setError("Say what happens."); return; }
    summary.setError("");
    for (const entry of held) {
      const amount = Number(entry.amount.value);
      if (!Number.isInteger(amount) || amount < 1) { entry.amount.setError("A whole number, 1 or more."); return; }
      entry.amount.setError("");
    }
    const args = {
      turn, summary: summary.value.trim(),
      operations: operations.map((entry) => acted(entry, turn, { kind: entry.kind.value, predicate: entry.predicate.value.trim(),
        subject: entry.subject.value, object: entry.object.value })),
    };
    if (held.length) {
      args.holdings = { lines: held.map((entry) => acted(entry, turn, { kind: entry.kind.value, holder: entry.holder.value,
        item: entry.item.value.trim(), amount: Number(entry.amount.value) })) };
    }
    return write("propose_effect", args, "Effect proposed.", () => { summary.clear(); made(); });
  }, { kind: "primary" });
  return card(ui, title, { wide: true }, turnChoice.node, summary.node, sub(ui, "Operations"), opsBox, ui.actions(addOp),
    sub(ui, "Holdings"), linesBox, ui.actions(addLine),
    el("p", { class: "g-muted" }, "Moving or adjusting holdings and waiving what an obstacle made due are not on this page yet."),
    ui.actions(go));
}

// What 035 reports for this chat's cut-short acceptances: unsettled ones this library settles, untagged ones only
// on the owner's word that this library accepted them, and ones another device accepted, which that device settles.
function recoverCard({ ctx, hub, write, director }) {
  const { ui } = ctx;
  const { el } = ui;
  const title = "Acceptance cut short";
  const codes = new Set(hub.problems.map((problem) => problem.code));
  const here = codes.has("effect_unsettled"), untagged = codes.has("effect_untagged"), elsewhere = codes.has("effect_elsewhere");
  if (!here && !untagged && !elsewhere) return null;
  const parts = [el("p", {}, "An effect's acceptance was cut short.")];
  if (elsewhere) parts.push(el("p", {}, "One was accepted on another device: settle it there."));
  if (!director) {
    if (here || untagged) parts.push(el("p", { class: "g-muted" }, "The director settles it, in the director's view."));
    return card(ui, title, { kind: "alert" }, ...parts);
  }
  if (!here && !untagged) return card(ui, title, { kind: "alert" }, ...parts);
  const can = hub.actions.find((item) => item.op === "recover_effects");
  const tick = (text) => {
    const id = ui.id("campaign-tick");
    const input = el("input", { type: "checkbox", id });
    return { input, node: el("div", { class: "campaign-tick" }, input, el("label", { for: id }, text)) };
  };
  const key = tick("I am the player who endorsed it, and I give my own key for this settlement");
  const own = untagged ? tick("No device is named for one of them: it was accepted in this library") : null;
  const said = (abort) => (done) => done.settled.length
    ? (abort ? "Undone: " : "Settled: ") + count(done.settled.length, "effect") + "."
    : "Nothing was settled.";
  const settle = (abort) => write("recover_effects", { abort, player_key: Boolean(key.input.checked),
    own: Boolean(own && own.input.checked) }, said(abort));
  parts.push(available(ctx, can, "Settling it", () => el("div", {}, key.node, own ? own.node : null, ui.actions(
    ui.button("Settle by what landed", () => settle(false), { kind: "primary" }),
    ui.button("Undo it", async () => {
      if (await ui.confirm("Undo the acceptance that was cut short? What landed is taken back.", { confirmLabel: "Undo", danger: true })) settle(true);
    }, { kind: "danger" })))));
  return card(ui, title, { kind: "alert" }, ...parts);
}

// -- the director's alone ---------------------------------------------------------------------------------

function truthCard({ ctx, hub, nameOf }) {
  const { ui } = ctx;
  const { el } = ui;
  const truth = hub.truth;
  if (!truth) return null;
  const events = truth.events.map((event) => [el("span", { class: "campaign-summary" }, event.summary),
    el("span", { class: "g-muted campaign-small" }, " · at " + event.moment.sequence + " · " + event.event)]);
  const relations = truth.relations.map((item) => line(ui, nameOf(item.subject) + " " + item.predicate + " " + nameOf(item.object),
    "since " + item.since + (item.ended ? ", until " + item.ended : "")));
  const claims = truth.claims.map((claim) => el("div", { class: "campaign-claim" + (claim.contested ? " campaign-contested" : "") },
    el("div", {}, el("strong", {}, claim.standing + ": "), claim.text, claim.contested ? " (contested)" : ""),
    claim.held.length ? lines(ui, claim.held.map((held) => el("span", {}, nameOf(held.holder) + " " + held.stance + " it (their belief)"))) : null));
  return card(ui, "World truth", { kind: "private" },
    sub(ui, "Accepted events"), lines(ui, events, { empty: "Nothing is accepted yet." }),
    truth.revoked.length ? el("p", { class: "g-muted" }, truth.revoked.length + " revoked") : null,
    sub(ui, "Relations"), lines(ui, relations, { empty: "No relation holds." }),
    sub(ui, "Canon and what the cast believes"),
    truth.claims_complete ? null : ui.notice("Some of canon's records cannot be read: a lost ruling would show here as merely reported.", { kind: "bad" }),
    claims.length ? claims : ui.empty("Canon has no propositions yet."));
}

function goalsCard({ ctx, hub, nameOf }) {
  const { ui } = ctx;
  const { el } = ui;
  if (!hub.goals) return null;
  const blocks = hub.goals.map((agenda) => el("div", { class: "campaign-agenda" },
    el("strong", {}, nameOf(agenda.actor) + (agenda.usable ? "" : " (not in play: " + agenda.problem + ")")),
    lines(ui, agenda.goals.map((goal) => line(ui, goal.aim + " (priority " + goal.priority + ")",
      goal.approaches.map((approach) => approach.action + (approach.needs.length ? " if " + approach.needs.join(" and ") : "")).join("; ")))),
    agenda.limits.length ? lines(ui, agenda.limits.map((limit) => line(ui, "Never: " + limit.rule,
      limit.when.length ? "when " + limit.when.join(" and ") : ""))) : null));
  return card(ui, "NPC goals", { kind: "private" }, blocks.length ? blocks : ui.empty("No NPC agenda is configured for this app."));
}

// -- knowledge --------------------------------------------------------------------------------------------

// One perspective at a time: the table's, or one actor's the view may read (logic.js). It is read when the
// hub is drawn and when another is chosen; a perspective the hub refuses is said, with its reason.
function knowledgeCard({ ctx, hub, call, knowing, director }) {
  const { ui } = ctx;
  const { el } = ui;
  const tabs = knowledgeTabs(hub, director ? "director" : "table");
  if (!tabs.some((tab) => tab.value === knowing.actor)) knowing.actor = "";
  const body = el("div", { class: "g-stack campaign-known", "aria-live": "polite" });
  const show = async (actor) => {
    const token = knowing.asked += 1;
    try {
      const answer = await call("knowledge", actor ? { actor } : {});
      if (token !== knowing.asked) return;  // another was chosen meanwhile
      const parts = (answer.actors || []).map((known) => knownBlock(ctx, known));
      const seen = answer.table_events || [];
      if (seen.length) parts.push(el("div", {}, sub(ui, "What the table saw"), lines(ui, seen.map((item) => el("span", {}, item.summary)))));
      body.replaceChildren(...(parts.length ? parts : [ui.empty("Nothing is known here yet.")]));
    } catch (error) {
      if (token !== knowing.asked || (error && error.reason === "left")) return;
      body.replaceChildren(el("p", { class: "g-muted" }, "This perspective cannot be read: " + ui.errorText(error)));
    }
  };
  const pick = ui.segments("Whose knowledge", tabs, { value: knowing.actor, onchange: (value) => { knowing.actor = value; show(value); } });
  show(knowing.actor);
  return card(ui, "Knowledge", {}, el("p", { class: "g-muted" }, "What someone observed and believes. None of it is world truth."),
    pick.node, body);
}

function knownBlock(ctx, known) {
  const { ui } = ctx;
  const { el } = ui;
  const who = known.actor.name || known.actor.ref;
  const beliefs = known.beliefs.map((item) => el("span", {}, who + " " + item.stance + ": " + item.text));
  const observed = known.observations.map((item) => el("span", {}, item.type + " (" + item.channel + "): " + item.content));
  const sent = known.sent.map((item) => el("span", {}, "told " + (item.recipient ? item.recipient.name || item.recipient.ref : "someone") + ": " + item.text));
  const inherited = (known.inherited || []).map((item) => el("span", {}, item.stance + ": " + item.text));
  return el("div", { class: "campaign-perspective" }, el("h3", { class: "campaign-name" }, who + "'s perspective"),
    sub(ui, "Believes"), lines(ui, beliefs, { empty: "No beliefs." }),
    sub(ui, "Observed"), lines(ui, observed, { empty: "Nothing observed." }),
    sent.length ? sub(ui, "Told others") : null, sent.length ? lines(ui, sent) : null,
    inherited.length ? sub(ui, "Written by the director while an NPC") : null, inherited.length ? lines(ui, inherited) : null);
}

function unavailableCard({ ctx, hub }) {
  const { ui } = ctx;
  const { el } = ui;
  return card(ui, "Not available yet", { kind: "dashed", wide: true }, lines(ui, hub.unavailable.map((item) => el("div", { role: "note" },
    el("strong", {}, feature(item.feature) + " is not available yet."), el("div", { class: "g-muted" }, item.reason + " (" + item.blocker + ")")))));
}

// -- small helpers ----------------------------------------------------------------------------------------

function put(parent, node) { if (node) parent.append(node); }

function available(ctx, can, what, make) {
  if (!can) return null;
  return can.available ? make() : ctx.ui.unavailable(what, can.reason);
}

function feature(name) {
  const text = name.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function clip(text) { return text.length > 60 ? text.slice(0, 59) + "…" : text; }

function problemsText(problems) {
  return (problems || []).map((problem) => problem.code + (problem.detail ? " (" + problem.detail + ")" : "")).join("; ") || "unknown";
}
