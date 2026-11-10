// Campaigns (Sprint 113): a campaign's chats, and one chat's campaign situation as the table or the director
// sees it (docs/CAMPAIGN_HUB.md). Addresses:
//   #/campaign                                  every campaign with chats in this library
//   #/campaign/<campaign id>                    that campaign's chats
//   #/campaign/<campaign id>?chat=<ChatRef>     the hub for one chat (&audience=director for the director's view)
// Everything here is read from, and written through, the campaign.* ops: the page keeps no state of its own
// beyond what the person is typing, and after every write it shows the hub the write returned. An actor's
// beliefs are always shown as that actor's, never as what is true. Actions the app does not offer are named
// with what they wait on, never shown as buttons.

const AUDIENCES = [["table", "Table"], ["director", "Director"]];
const KINDS = ["begin", "end"];         // 021 operation kinds
const LINE_KINDS = ["add", "consume"];  // the holdings lines this page writes (move and adjust are not here yet)

export async function mount(root, ctx) {
  const { ui } = ctx;
  ctx.styles("page.css");
  const campaign = ctx.path[0] || null;
  const chat = ctx.query.get("chat");
  if (!chat) return campaign ? showCampaign(root, ctx, campaign) : showCampaigns(root, ctx);
  const audience = ctx.query.get("audience") === "director" ? "director" : "table";
  if (!campaign || !chat.startsWith("campaign:" + campaign + "#")) {
    root.append(ui.notice("This address names a chat that is not in the campaign it names."),
      ui.actions(ui.link("All campaigns", "campaign")));
    return;
  }
  return showHub(root, ctx, campaign, chat, audience);
}

// -- lists ------------------------------------------------------------------------------------------------

async function showCampaigns(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.setTitle("Campaigns");
  const answer = await ctx.api("campaign.chats", {});
  const rows = answer.campaigns.map((item) => el("a", { href: ctx.href("campaign", [item.campaign]) },
    ui.rowText(item.campaign, plural(item.chats.length, "chat"))));
  root.append(ui.list(rows, { empty: "No campaign has a chat in this library yet." }));
  if (!answer.complete) root.append(ui.notice("Only the campaigns of the most recent chats are listed."));
}

async function showCampaign(root, ctx, campaign) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.setTitle(campaign);
  root.append(ui.actions(ui.link("All campaigns", "campaign")));
  const answer = await ctx.api("campaign.chats", { campaign });
  const found = answer.campaigns.find((item) => item.campaign === campaign);
  const rows = (found ? found.chats : []).map((item) => el("a", { href: ctx.href("campaign", [campaign], { chat: item.chat }) },
    ui.rowText(item.label || item.name || item.chat, item.problem ? item.chat + " · " + item.problem : item.chat)));
  root.append(ui.list(rows, { empty: "This campaign has no chats in this library." }));
  if (!answer.complete) root.append(ui.notice("Only the most recent chats are listed."));
}

// -- the hub ----------------------------------------------------------------------------------------------

async function showHub(root, ctx, campaign, chat, audience) {
  const { ui } = ctx;
  const { el } = ui;
  const director = audience === "director";
  ctx.setTitle(director ? "Campaign: director" : "Campaign");
  const tabs = el("div", { class: "g-actions", role: "group", "aria-label": "Whose view" });
  for (const [name, label] of AUDIENCES) {
    const link = el("a", { class: "g-button", href: ctx.href("campaign", [campaign], { chat, audience: name === "table" ? undefined : name }) }, label);
    if (name === audience) link.setAttribute("aria-current", "page");
    tabs.append(link);
  }
  const back = [ui.link("This campaign's chats", "campaign", [campaign])];
  if (ctx.registered("chat")) back.push(ui.link("Open the chat", "chat", [chat]));
  const view = el("div", { class: "campaign-hub" });
  const knowledge = el("div", { class: "campaign-knowledge" });
  root.append(ui.actions(...back), tabs, view, knowledge);

  const call = (op, args) => ctx.api("campaign." + op, { chat, audience, ...args });
  // Every write answers the hub as it now stands: show that, never the one from before the write.
  const write = (op, args, said) => ctx.act(async () => {
    const answer = await call(op, args);
    if (said) ctx.notify(said);
    if (answer.hub) render(answer.hub);
    else {
      ctx.notify("Done, but the campaign could not be read again: " + problemsText(answer.problems), { bad: true });
      render(await call("hub", {}));
    }
    return true;  // ctx.act answers undefined for a refusal: what was typed is kept for another try
  });
  // The turns an effect can be proposed for: the chat's kept turns, and the open action's (from the core `open`
  // op, which the hub does not repeat: the transcript is the chat page's).
  let turns = [];
  const open = async () => {
    const [hub, scene] = await Promise.all([call("hub", {}), director && ctx.has("open") ? ctx.api("open", { chat }) : null]);
    if (scene) turns = scene.turns.filter((turn) => turn.kept).map((turn) => ({ turn: turn.turn,
      label: "Turn " + turn.number + (turn.post ? ": " + clip(turn.post.text) : "") }));
    render(hub);
  };

  function render(hub) {
    const names = new Map(hub.cast.map((member) => [member.actor, member.name]));
    const nameOf = (ref) => (ref ? names.get(ref.ref) || ref.name || ref.ref : "nobody");
    const parts = [];
    parts.push(ui.notice(director
      ? "Director's view: world truth, every effect and every actor's powers and holdings."
      : "The table's view: what the table can see. World truth is the director's."));
    if (hub.paused) parts.push(ui.notice("Play is paused in this chat.", { kind: "bad" }));
    for (const problem of hub.problems) parts.push(ui.notice(problem.code + (problem.detail ? ": " + problem.detail : "")));
    parts.push(castSection(ctx, hub, nameOf));
    parts.push(stageSection(ctx, hub, nameOf));
    parts.push(charactersSection(ctx, hub, nameOf));
    parts.push(sourcesSection(ctx, hub, nameOf));
    parts.push(obstaclesSection(ctx, hub, nameOf));
    parts.push(actionSection(ctx, hub, nameOf, write, director));
    parts.push(turnsSection(ctx, hub, nameOf, write, director));
    if (director) parts.push(proposeSection(ctx, hub, chat, turns, write));
    parts.push(recoverSection(ctx, hub, write));
    if (director) parts.push(truthSection(ctx, hub, nameOf), goalsSection(ctx, hub, nameOf));
    parts.push(knowledgeSection(ctx, hub, call, knowledge, director));
    parts.push(unavailableSection(ctx, hub));
    view.replaceChildren(...parts.filter(Boolean));
  }

  await open();
}

function castSection(ctx, hub, nameOf) {
  const { ui } = ctx;
  const rows = hub.cast.map((member) => {
    const role = member.role === "player" ? (member.controller === hub.player ? "your character" : "another player's character")
      : member.role === "npc" ? "the director's" : "no one seated";
    const where = member.absent ? "absent (" + member.absent.via + ")" : member.on_stage ? "on stage" : "off stage";
    return ui.rowText(member.name, role + " · " + where + (hub.persona === member.actor ? " · persona" : ""));
  });
  const party = hub.party.memberships.map((item) =>
    ui.rowText(nameOf(item.member), item.status + " in " + nameOf(item.party) + " since " + item.since));
  return ui.section("Cast", ui.list(rows, { empty: "No one is in this chat's cast." }),
    party.length ? ctx.ui.el("h3", {}, "Party") : null, party.length ? ui.list(party) : null);
}

function stageSection(ctx, hub, nameOf) {
  const { ui } = ctx;
  const stage = hub.stage;
  if (!stage) return null;  // named under "Not available yet" when the chat has none
  const label = new Map(stage.areas.map((area) => [area.id, area.label]));
  const rows = stage.placements.map((item) => ui.rowText(nameOf(item.who), "in " + (label.get(item.area) || item.area)
    + (item.holder ? ", held by " + nameOf(item.holder) : "") + " (" + item.basis + ")"));
  const ways = stage.ways.map((way) => ui.rowText(way.sides.map((side) => label.get(side) || side).join(" to "), way.state));
  return ui.section("Stage", ui.list(rows, { empty: "No one is placed." }), ways.length ? ui.list(ways) : null);
}

function charactersSection(ctx, hub, nameOf) {
  const { ui } = ctx;
  const { el } = ui;
  const actors = [...new Set([...Object.keys(hub.holdings), ...Object.keys(hub.items), ...Object.keys(hub.abilities)])];
  const blocks = actors.map((actor) => {
    const name = nameOf({ ref: actor });
    const counts = Object.entries((hub.holdings[actor] || { counts: {} }).counts).map(([item, n]) => item + " × " + n);
    const items = hub.items[actor];
    const held = items ? items.held.map((item) => item.name || item.item) : [];
    const equipped = items ? Object.entries(items.equipped).map(([slot, found]) => slot + ": " + found.map((item) => item.name || item.item).join(", ")) : [];
    const powers = (hub.abilities[actor] || []).map((ability) => ui.rowText(ability.name || ability.ability, abilityText(ability)));
    return el("div", { class: "campaign-actor" }, el("h3", {}, name),
      el("p", {}, "Holdings: " + (counts.length ? counts.join(", ") : "none")),
      el("p", {}, "Items: " + (held.length ? held.join(", ") : "none") + (equipped.length ? " · equipped " + equipped.join("; ") : "")),
      powers.length ? ui.list(powers) : el("p", { class: "g-muted" }, "No powers."));
  });
  return ui.section(hub.audience === "director" ? "Characters" : "Your characters",
    blocks.length ? blocks : ui.empty("No character to show."));
}

function abilityText(ability) {
  const effects = ability.effects.map((effect) => effect.text + " (" + [effect.tags.join("/"), effect.range, effect.certainty]
    .filter(Boolean).join(", ") + ")");
  const listed = (label, values) => values.length ? label + " " + values.map((value) => value.text).join("; ") : null;
  return [ability.description || null, effects.length ? "permits " + effects.join("; ") : null, listed("limits", ability.limits),
    listed("costs", ability.costs), listed("needs", ability.prerequisites)].filter(Boolean).join(" · ") || "no effects listed";
}

function sourcesSection(ctx, hub, nameOf) {
  const { ui } = ctx;
  const { el } = ui;
  const rows = hub.sources.map((source) => {
    const state = source.broken ? "broken: " + source.broken
      : source.behind ? "pinned behind its source (now " + (source.head || "unknown") + ")"
      : source.pinned ? "pinned at " + source.resolved : "follows its source (" + (source.resolved || "unresolved") + ")";
    const notices = source.notices.length ? " · " + source.notices.map((item) => item.code).join(", ") : "";
    const row = el("div", {}, ...ui.rowText((source.actor ? nameOf(source.actor) + ": " : "") + source.role,
      source.binding + " · " + state + (source.problem ? " · " + source.problem : "") + notices));
    if (source.handoff) {
      // 042's rebase review is the review screen's (110): link to it only where that page is in the app.
      row.append(ctx.registered("import") ? ui.link("Review the rebase", "import")
        : ui.unavailable("Reviewing this rebase", "the review screen is Sprint 110's"));
    }
    return row;
  });
  return ui.section("Sources", ui.list(rows, { empty: "No source is bound to this chat." }));
}

function obstaclesSection(ctx, hub, nameOf) {
  const { ui } = ctx;
  const { el } = ui;
  if (!hub.obstacles.length) return null;
  const blocks = hub.obstacles.map((obstacle) => el("div", { class: "campaign-obstacle" },
    el("h3", {}, obstacle.goal + " (" + obstacle.status + ")"),
    obstacle.problem ? ui.notice(obstacle.problem) : null,
    ui.list(obstacle.approaches.map((approach) => ui.rowText(approach.text,
      [approach.risks.length ? "risks " + approach.risks.join(", ") : null,
        approach.costs.length ? "costs " + approach.costs.map((cost) => cost.text).join("; ") : null].filter(Boolean).join(" · ")))),
    obstacle.facts.length ? ui.list(obstacle.facts.map((fact) => ui.rowText(fact.text, fact.kind))) : null,
    obstacle.attempts.length ? ui.list(obstacle.attempts.map((attempt) => ui.rowText("Attempt by " + attempt.approach,
      attempt.status + (attempt.result ? ": " + attempt.result : "")))) : null,
    obstacle.due.length ? el("p", {}, "Due from its attempts: " + obstacle.due.map((item) => item.text).join("; ")) : null));
  return ui.section("Obstacles", blocks);
}

function actionSection(ctx, hub, nameOf, write, director) {
  const { ui } = ctx;
  const { el } = ui;
  const action = hub.open_action;
  const can = Object.fromEntries(hub.actions.map((item) => [item.op, item]));
  if (!action) return ui.section("Open action", ui.empty("No action is waiting to be adjudicated."));
  const parts = [el("p", {}, nameOf(action.actor) + " is acting (turn " + action.turn + ").")];
  if (action.label) {
    const label = action.label;
    parts.push(el("p", {}, "Labelled " + label.label + " (" + label.state + ")" + (label.reason ? ": " + label.reason : "")
      + (label.against ? ", against " + label.against.obstacle + " by " + label.against.approach + (label.against.novel ? " (a new approach)" : "") : "")));
  }
  const framed = action.framed;
  if (framed) {
    parts.push(el("p", {}, "Framed: " + framed.intent + " · " + framed.classification + " · allows " + framed.allowed.join(", ")),
      framed.factors.length ? ui.list(framed.factors.map((factor) => ui.rowText(factor.text, factor.bearing))) : null);
  }
  const outcome = action.outcome;
  if (outcome) {
    parts.push(el("p", {}, "Outcome: " + outcome.result + " (" + outcome.basis + ")"
      + (outcome.decided_by ? ", decided by " + outcome.decided_by : "") + (outcome.consented_by ? ", consented by " + outcome.consented_by : "")));
  } else {
    if (can.label && can.label.available) {
      const choices = [{ value: "", label: "No obstacle" }, ...hub.obstacles.filter((item) => item.status !== "overcome")
        .map((item) => ({ value: item.obstacle, label: item.goal }))];
      const against = ui.choice("Against", choices);
      parts.push(against.node, ui.actions(ui.button(action.label ? "Label again" : "Label this action", () =>
        write("label", against.value ? { obstacle: against.value } : {}))));
    } else if (can.label) parts.push(ui.unavailable("Labelling this action", can.label.reason));
    if (action.label && !framed && !director) {
      parts.push(available(ctx, can.confirm, "Confirming the labelling", () => ui.button("Confirm this labelling", () =>
        write("confirm", {}, "Labelling confirmed."), { kind: "primary" })));
    }
    if (framed) parts.push(decideForm(ctx, framed, can.decide, write, director));
  }
  return ui.section("Open action", ...parts);
}

// The director adjudicates; the acting player may instead consent to a setback, naming the frame they were shown.
function decideForm(ctx, framed, can, write, director) {
  const { ui } = ctx;
  const { el } = ui;
  if (!can || !can.available) return ui.unavailable(director ? "Deciding this action" : "Consenting to a setback", can && can.reason);
  const results = director ? framed.allowed : framed.allowed.filter((result) => result !== "success");
  if (!results.length) return ui.empty("Only the director decides this action.");
  const result = ui.choice("Result", results.map((value) => ({ value, label: value })));
  const cites = ticks(ctx, "Because of", framed.factors.map((factor) => [factor.id, factor.text]));
  const consequences = ticks(ctx, "With these stakes", framed.stakes.map((stake) => [stake.id, stake.text]));
  const go = ui.button(director ? "Decide" : "Consent", async () => {
    const args = { result: result.value, cites: cites.value(), consequences: consequences.value() };
    if (director) return write("decide", { ...args, basis: "adjudicated" }, "Decided: " + result.value + ".");
    const yes = await ui.confirm("Consent to " + result.value + " for this action, as framed now? This decides it.", { confirmLabel: "Consent" });
    if (yes) return write("decide", { ...args, basis: "consent", fingerprint: framed.fingerprint }, "Consented: " + result.value + ".");
  }, { kind: "primary" });
  return el("div", { class: "campaign-form" }, result.node, cites.node, consequences.node, ui.actions(go));
}

function turnsSection(ctx, hub, nameOf, write, director) {
  const { ui } = ctx;
  const { el } = ui;
  const can = Object.fromEntries(hub.actions.map((item) => [item.op, item]));
  const blocks = hub.turns.map((turn) => {
    const parts = [el("h3", {}, "Turn " + turn.number + (turn.open ? " (open)" : turn.kept ? " (kept)" : ""))];
    if (turn.post) parts.push(el("p", { class: "g-muted" }, nameOf(turn.post.actor) + " " + turn.post.kind + (turn.post.withheld ? " (withheld)" : "")));
    for (const label of turn.labels) parts.push(el("p", { class: "g-muted" }, "Labelled " + label.label + " (" + label.state + ")"));
    for (const effect of turn.effects) parts.push(effectBlock(ctx, turn, effect, hub, nameOf, write, can, director));
    for (const intention of turn.intentions || []) {
      parts.push(el("div", { class: "campaign-intention" }, el("strong", {}, nameOf(intention.actor) + " (take " + intention.take + "): "),
        intention.goal ? "pursued " + intention.goal + (intention.approach ? " by " + intention.approach : "") : "pursued nothing",
        intention.refusals.length ? "; refused " + intention.refusals.join(", ") : "",
        ...intention.explanation.map((line) => el("div", { class: "g-muted" }, line))));
    }
    return el("div", { class: "campaign-turn" }, ...parts);
  });
  return ui.section("Turns and effects", blocks.length ? blocks : ui.empty("No turn has been labelled or given an effect yet."));
}

function effectBlock(ctx, turn, effect, hub, nameOf, write, can, director) {
  const { ui } = ctx;
  const { el } = ui;
  const ops = effect.operations.map((op) => op.kind + " " + op.predicate + ": " + nameOf(op.subject) + " → " + nameOf(op.object)
    + (op.agent ? " (by " + nameOf(op.agent) + ")" : ""));
  const lines = effect.holdings ? effect.holdings.lines.map((line) => line.kind + " " + (line.amount ?? "") + " " + line.item + " for " + nameOf(line.holder)) : [];
  const keys = effect.endorsers.map((key) => (key.principal === hub.player ? "your key" : key.principal) + (key.given ? " given" : " not given"));
  const box = el("div", { class: "campaign-effect" },
    el("p", {}, el("strong", {}, "Effect " + effect.number + " (" + effect.state + "): "), effect.summary),
    ops.length || lines.length ? ui.list([...ops, ...lines].map((text) => el("span", {}, text))) : null,
    effect.waivers.length ? el("p", { class: "g-muted" }, "Waived: " + effect.waivers.map((item) => item.change + " (" + item.reason + ")").join("; ")) : null,
    el("p", { class: "g-muted" }, [keys.length ? "Players: " + keys.join(", ") : "No player's key is needed",
      "director: " + (effect.approved_by ? "approved" : "not yet")].join(" · ")));
  if (effect.state !== "proposed" && effect.state !== "approved") return box;
  const mine = effect.endorsers.some((key) => key.principal === hub.player && !key.given);
  const args = { turn: turn.turn, number: effect.number };
  // Two keys, two calls: the player endorses exactly what is shown here; the director approves. One person may
  // hold both, but this page never gives one key for the other.
  if (!director && mine) {
    put(box, available(ctx, can.endorse_effect, "Endorsing this effect", () => ui.button("Endorse as shown", async () => {
      if (await ui.confirm("Give your key to effect " + effect.number + ", exactly as shown: " + effect.summary, { confirmLabel: "Endorse" })) {
        write("endorse_effect", { ...args, fingerprint: effect.fingerprint }, "Your key is given.");
      }
    }, { kind: "primary" })));
  }
  if (director && !effect.approved_by) {
    const last = effect.endorsers.every((key) => key.given);
    put(box, available(ctx, can.approve_effect, "Approving this effect", () => ui.button(last ? "Approve and accept" : "Approve", async () => {
      const message = last ? "Approve effect " + effect.number + "? Every player's key is given, so this accepts it as world truth. It cannot be undone here."
        : "Approve effect " + effect.number + "? It is accepted once every player's key is given.";
      if (await ui.confirm(message, { confirmLabel: "Approve", danger: last })) write("approve_effect", args, last ? "Accepted." : "Approved.");
    }, { kind: "primary" })));
  }
  return box;
}

// The director proposes a turn's effect whole, in structured form: what an obstacle attempt made due comes
// first, by itself; operations and holdings lines are added here. Nothing is proposed from narration.
function proposeSection(ctx, hub, chat, kept, write) {
  const { ui } = ctx;
  const { el } = ui;
  const can = hub.actions.find((item) => item.op === "propose_effect");
  if (!can || !can.available) return ui.section("Propose an effect", ui.unavailable("Proposing an effect", can && can.reason));
  const options = kept.map((item) => ({ value: item.turn, label: item.label }));
  if (hub.open_action && !options.some((item) => item.value === hub.open_action.turn)) {
    options.push({ value: hub.open_action.turn, label: "The open action's turn" });
  }
  if (!options.length) return ui.section("Propose an effect", ui.empty("No turn is kept yet: an effect is proposed for a kept turn or a decided action."));
  const turnChoice = ui.choice("For turn", options, { value: options[options.length - 1].value });
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
  });
  const lines = [];
  const linesBox = el("div", {});
  const addLine = ui.button("Add a holdings line", () => {
    const kind = ui.choice("Kind", LINE_KINDS.map((value) => ({ value, label: value })));
    const holder = ui.choice("Holder", cast);
    const item = ui.field("Item");
    const amount = ui.field("Amount", { type: "number", value: "1" });
    const agent = ui.choice("Done by", [{ value: "", label: "No one (the world)" }, ...cast]);
    const row = el("div", { class: "campaign-row" }, kind.node, holder.node, item.node, amount.node, agent.node);
    const entry = { kind, holder, item, amount, agent, row };
    row.append(ui.button("Remove", () => { lines.splice(lines.indexOf(entry), 1); row.remove(); }));
    lines.push(entry);
    linesBox.append(row);
  });
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
    for (const entry of lines) {
      const amount = Number(entry.amount.value);
      if (!Number.isInteger(amount) || amount < 1) { entry.amount.setError("A whole number, 1 or more."); return; }
      entry.amount.setError("");
    }
    const args = {
      turn, summary: summary.value.trim(),
      operations: operations.map((entry) => acted(entry, turn, { kind: entry.kind.value, predicate: entry.predicate.value.trim(),
        subject: entry.subject.value, object: entry.object.value })),
    };
    if (lines.length) {
      args.holdings = { lines: lines.map((entry) => acted(entry, turn, { kind: entry.kind.value, holder: entry.holder.value,
        item: entry.item.value.trim(), amount: Number(entry.amount.value) })) };
    }
    return write("propose_effect", args, "Effect proposed.").then((done) => { if (done) summary.clear(); });
  }, { kind: "primary" });
  return ui.section("Propose an effect", turnChoice.node, summary.node, el("h3", {}, "Operations"), opsBox, ui.actions(addOp),
    el("h3", {}, "Holdings"), linesBox, ui.actions(addLine),
    el("p", { class: "g-muted" }, "Moving or adjusting holdings and waiving what an obstacle made due are not on this page yet."),
    ui.actions(go));
}

function recoverSection(ctx, hub, write) {
  const { ui } = ctx;
  const { el } = ui;
  const cut = hub.turns.some((turn) => turn.effects.some((effect) => effect.state === "accepting"))
    || hub.problems.some((problem) => problem.code.startsWith("effect"));
  if (!cut) return null;
  const can = hub.actions.find((item) => item.op === "recover_effects");
  const keyId = ui.id("campaign-key");
  const key = el("input", { type: "checkbox", id: keyId });
  const keyRow = el("div", { class: "g-field" }, key, el("label", { for: keyId },
    " I am the player who endorsed it, and I give my own key for this settlement"));
  const settle = (abort) => write("recover_effects", { abort, player_key: Boolean(key.checked) }, abort ? "Acceptance undone." : "Acceptance settled.");
  return ui.section("Acceptance cut short",
    ui.notice("An effect's acceptance was cut short. Settle it by what landed, or undo it."),
    available(ctx, can, "Settling it", () => el("div", {}, keyRow, ui.actions(
      ui.button("Settle", () => settle(false), { kind: "primary" }),
      ui.button("Undo it", async () => {
        if (await ui.confirm("Undo the acceptance that was cut short? What landed is taken back.", { confirmLabel: "Undo", danger: true })) settle(true);
      }, { kind: "danger" })))));
}

function truthSection(ctx, hub, nameOf) {
  const { ui } = ctx;
  const { el } = ui;
  const truth = hub.truth;
  const events = truth.events.map((event) => ui.rowText(event.summary, "at " + event.moment.sequence + " · " + event.event));
  const relations = truth.relations.map((item) => ui.rowText(nameOf(item.subject) + " " + item.predicate + " " + nameOf(item.object),
    "since " + item.since + (item.ended ? ", until " + item.ended : "")));
  const claims = truth.claims.map((claim) => el("div", { class: "campaign-claim" + (claim.contested ? " campaign-contested" : "") },
    el("div", {}, el("strong", {}, claim.standing + ": "), claim.text, claim.contested ? " (contested)" : ""),
    claim.held.length ? ui.list(claim.held.map((held) => el("span", {}, nameOf(held.holder) + " " + held.stance + " it (their belief)"))) : null));
  return ui.section("World truth",
    el("h3", {}, "Accepted events"), ui.list(events, { empty: "Nothing is accepted yet." }),
    truth.revoked.length ? el("p", { class: "g-muted" }, truth.revoked.length + " revoked") : null,
    el("h3", {}, "Relations"), ui.list(relations, { empty: "No relation holds." }),
    el("h3", {}, "Canon and what the cast believes"),
    truth.claims_complete ? null : ui.notice("Some of canon's records cannot be read: a lost ruling would show here as merely reported.", { kind: "bad" }),
    claims.length ? claims : ui.empty("Canon has no propositions yet."));
}

function goalsSection(ctx, hub, nameOf) {
  const { ui } = ctx;
  const { el } = ui;
  const blocks = hub.goals.map((agenda) => el("div", { class: "campaign-agenda" },
    el("h3", {}, nameOf(agenda.actor) + (agenda.usable ? "" : " (not in play: " + agenda.problem + ")")),
    ui.list(agenda.goals.map((goal) => ui.rowText(goal.aim + " (priority " + goal.priority + ")",
      goal.approaches.map((approach) => approach.action + (approach.needs.length ? " if " + approach.needs.join(" and ") : "")).join("; ")))),
    agenda.limits.length ? ui.list(agenda.limits.map((limit) => ui.rowText("Never: " + limit.rule,
      limit.when.length ? "when " + limit.when.join(" and ") : ""))) : null));
  return ui.section("NPC goals", blocks.length ? blocks : ui.empty("No NPC agenda is configured for this app."));
}

function knowledgeSection(ctx, hub, call, target, director) {
  const { ui } = ctx;
  const { el } = ui;
  const show = async (actor) => {
    const answer = await call("knowledge", actor ? { actor } : {});
    const parts = answer.actors.map((known) => knownBlock(ctx, known));
    if (answer.table_events.length) parts.push(ui.section("What the table saw", ui.list(answer.table_events.map((item) => el("span", {}, item.summary)))));
    target.replaceChildren(...parts);
  };
  const buttons = [ui.button("What the table knows", () => ctx.act(() => show(null)))];
  for (const member of hub.cast) {
    if (director || (member.role === "player" && member.controller === hub.player)) {
      buttons.push(ui.button("What " + member.name + " knows", () => ctx.act(() => show(member.actor))));
    }
  }
  return ui.section("Knowledge", el("p", { class: "g-muted" }, "An actor's perspective: what they observed and believe. None of it is world truth."),
    ui.actions(...buttons));
}

function knownBlock(ctx, known) {
  const { ui } = ctx;
  const { el } = ui;
  const who = known.actor.name || known.actor.ref;
  const beliefs = known.beliefs.map((item) => el("span", {}, who + " " + item.stance + ": " + item.text));
  const observed = known.observations.map((item) => el("span", {}, item.type + " (" + item.channel + "): " + item.content));
  const sent = known.sent.map((item) => el("span", {}, "told " + (item.recipient ? item.recipient.name || item.recipient.ref : "someone") + ": " + item.text));
  const inherited = (known.inherited || []).map((item) => el("span", {}, item.stance + ": " + item.text));
  return ui.section(who + "'s perspective",
    el("h3", {}, "Believes"), ui.list(beliefs, { empty: "No beliefs." }),
    el("h3", {}, "Observed"), ui.list(observed, { empty: "Nothing observed." }),
    sent.length ? el("h3", {}, "Told others") : null, sent.length ? ui.list(sent) : null,
    inherited.length ? el("h3", {}, "Written by the director while an NPC") : null, inherited.length ? ui.list(inherited) : null);
}

function unavailableSection(ctx, hub) {
  const { ui } = ctx;
  return ui.section("Not available yet", ...hub.unavailable.map((item) => ui.unavailable(feature(item.feature), item.reason + " (" + item.blocker + ")")));
}

// -- small helpers ----------------------------------------------------------------------------------------

function put(parent, node) { if (node) parent.append(node); }

function available(ctx, can, what, make) {
  if (!can) return null;
  return can.available ? make() : ctx.ui.unavailable(what, can.reason);
}

function ticks(ctx, label, options) {
  const { el } = ctx.ui;
  if (!options.length) return { node: null, value: () => [] };
  const boxes = options.map(([id, text]) => {
    const inputId = ctx.ui.id("campaign-tick");
    const input = el("input", { type: "checkbox", id: inputId, value: id });
    return { id, input, node: el("div", {}, input, el("label", { for: inputId }, " " + text)) };
  });
  return { node: el("fieldset", { class: "campaign-ticks" }, el("legend", {}, label), ...boxes.map((box) => box.node)),
    value: () => boxes.filter((box) => box.input.checked).map((box) => box.id) };
}

function feature(name) {
  const text = name.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function clip(text) { return text.length > 60 ? text.slice(0, 59) + "…" : text; }

function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

function problemsText(problems) {
  return (problems || []).map((problem) => problem.code + (problem.detail ? " (" + problem.detail + ")" : "")).join("; ") || "unknown";
}
