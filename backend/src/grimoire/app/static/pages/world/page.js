// A campaign's world (Sprint 115), the director's alone: its places and routes (059), its rumors (063), a
// faction's decision (057) and what an NPC thinks others think (060) (docs/WORLD.md). Addresses:
//   #/world/<campaign id>?chat=<ChatRef>&audience=director   the director's World page, for one chat's cast
//   #/world/<campaign id>?chat=<ChatRef>                      the table's view: it says only that this is the director's
// Everything is read from, and written through, the world.* ops, and the table's view calls none of them. The two
// changes it makes (a road blocked or opened in this campaign, a rumor's recipient's belief admitted) are reviewed
// first (a plan writes nothing) and made as reviewed, with the same operation id and the revision shown: anything
// changed in between refuses it, and the section is read again. A faction's decision and an NPC's theory of mind are
// previews of the domain's answer for what the director lays out, and nothing of them is saved. What the domain does
// not do is named, never offered. Each section reads on its own: one that is refused shows why, with "Read again",
// and leaves the others working. What the director lays out (typed, picked or ticked) is kept as a draft.

import { campaignName, count } from "../campaign/logic.js";
import {
  heldAt, latest, replacedBy, replacementProblem, newOperationId, nextHypothesisId, parseConditions, parseConfidence, pathPlanText, pathText, percent,
  placeLabels, readHypotheses, readList, routeText, rumorLine, rumorStatus, whose,
} from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
const STANCES = [{ value: "believed", label: "Believes it" }, { value: "suspected", label: "Suspects it" }, { value: "rejected", label: "Rejects it" }];
const D = { audience: "director" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const campaign = ctx.path[0] || null;
  if (!campaign) {
    root.append(ui.notice("Open a campaign's world from its hub."), ui.actions(ui.buttonLink("All campaigns", "campaign")));
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
  ctx.setTitle("World");
  ctx.setCrumb(ui.link("Campaigns", "campaign"), " / ", ui.link(name, "campaign", [campaign], chat ? { chat } : undefined), " / World");
  const address = (wanted) => ctx.href("world", [campaign], { chat: chat || undefined, audience: wanted === "director" ? "director" : undefined });
  const views = ui.segments("Whose view", AUDIENCES, { value: audience, onchange: (value) => ctx.navigate(address(value)) });
  root.append(el("div", { class: "g-row world-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "The campaign's places, rumors, factions and minds. None of it is shown to the table." : "This page is the director's."),
  ctx.registered("campaign") ? ui.buttonLink("Campaign hub", "campaign", [campaign], { chat: chat || undefined, audience: audience === "director" ? "director" : undefined }) : null));

  if (audience !== "director") {
    root.append(card(ui, "World", el("p", {}, "The World page is the director's. Its places, rumors, faction decisions and what NPCs think are not shown at the table:"
      + " a character learns of them only through what it perceives in play.")));
    return;
  }
  const cards = el("div", { class: "world-cards" });
  root.append(cards);
  const placesCard = el("div", {}), rumorsCard = el("div", {}), factionCard = el("div", {}), mindsCard = el("div", {});
  cards.append(placesCard, rumorsCard, factionCard, mindsCard);
  // the chat's cast, read once for both sections that lay it out (and again after a refusal)
  let castAsk = null;
  const castOf = () => castAsk || (castAsk = ctx.api("world.cast", { chat, ...D }).then((answer) => answer.cast, (error) => { castAsk = null; throw error; }));
  await Promise.all([
    showPlaces(placesCard, ctx, campaign),
    showRumors(rumorsCard, ctx, campaign, chat),
    chat ? showFaction(factionCard, ctx, chat, castOf) : noChat(factionCard, ctx, "Faction decision", listing.complete),
    chat ? showMinds(mindsCard, ctx, chat, castOf) : noChat(mindsCard, ctx, "What an NPC thinks", listing.complete),
  ]);
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("world-card");
  return node;
}

function noChat(slot, ctx, title, complete) {
  const { ui } = ctx;
  slot.replaceWith(card(ui, title, ui.empty(complete ? "This campaign has no chat in this library, so no cast to lay out."
    : "None of this campaign's chats is among the most recent ones listed.")));
}

// Run one section's read into `body`. A refusal is shown there with its reason and a "Read again" button: it
// never blanks the page or another section. A page left meanwhile shows nothing.
async function sectionRead(ctx, body, read) {
  try {
    await read();
  } catch (error) {
    if (error && error.reason === "left") return;
    body.replaceChildren(ctx.ui.errorBox(error), ctx.ui.actions(ctx.ui.button("Read again", () => ctx.act(() => sectionRead(ctx, body, read)))));
  }
}

function reviewBox(ui, text, files, onCancel, onMake) {
  const { el } = ui;
  const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "world-sub" }, "Review this change"), el("p", {}, text),
    files.length ? el("p", { class: "g-muted world-files" }, "It writes " + count(files.length, "file") + ": " + files.join(", "))
      : el("p", { class: "g-muted" }, "It writes nothing."),
    ui.actions(ui.button("Cancel", onCancel), ui.button("Make this change", onMake, { kind: "primary" })));
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "Review this change");
  return box;
}

function group(ui, title, ...children) {
  return ui.el("div", { class: "g-stack world-form", role: "group", "aria-label": title }, ui.el("h3", { class: "world-sub" }, title), ...children);
}

function checkbox(ui, label, { checked = false, value, onchange } = {}) {
  const box = ui.el("input", { type: "checkbox", value });
  box.checked = checked;
  box.addEventListener("change", () => onchange(box.checked));
  return ui.el("label", { class: "g-row world-check" }, box, ui.el("span", { class: "g-grow" }, label));
}

// A choice whose pick is kept in the draft `slot`: what was picked last, while it is still offered, else `fallback`.
function keptChoice(ui, label, options, slot, fallback) {
  const kept = slot.get();
  const value = options.some((option) => option.value === kept) ? kept : fallback;
  return ui.choice(label, options, { value, onchange: (picked) => slot.set(picked) });
}

// A set of ids kept in the draft `slot` (as JSON), for ticked boxes.
function keptSet(slot) {
  const ids = new Set(readList(slot.get()));
  return {
    has: (id) => ids.has(id),
    set(id, on) { if (on) ids.add(id); else ids.delete(id); slot.set(ids.size ? JSON.stringify([...ids].sort()) : ""); },
    values: () => [...ids].sort(),
    clear() { ids.clear(); slot.clear(); },
  };
}

// -- places and routes (059) ----------------------------------------------------------------------------------

async function showPlaces(slot, ctx, campaign) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Places and routes");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(body, ui.unavailable("Weighted routes, hazards and travel time", "059 finds the route with the fewest roads; it has no distances, dangers or journeys."),
    ui.unavailable("A map of the places", "059's places have no positions to draw."));
  let topologies = [];  // the library's topologies, listed apart from any one's view
  let current = null;   // the topology chosen
  let shown = null;     // its view in this campaign, or null when it could not be read (viewError)
  let shownAt = 0;      // the read that drew `shown`: while a newer read is out, what is on screen is not acted on
  let viewError = null;
  let review = null;    // { args, plan }: committed exactly as reviewed, with its own operation id
  let found = null, routed = null;
  const reading = latest(), finding = latest(), routing = latest();
  const DRAFT = "places:" + campaign + ":";
  const chosen = ctx.draft(DRAFT + "topology"), fromSlot = ctx.draft(DRAFT + "from"), toSlot = ctx.draft(DRAFT + "to");
  const find = ui.field("Place name", { draft: ctx.draft(DRAFT + "find"), hint: "Every place this name or an alias matches is listed; none is picked for you" });
  const conditions = ui.field("Conditions held", { draft: ctx.draft(DRAFT + "conditions"), hint: "Words separated by commas, like ticket, night" });

  // The view replaced: a search or a route answered for another revision no longer stands for this one.
  const replaced = (topology) => {
    if (!shown || shown.topology !== topology.topology || shown.etag !== topology.etag) { found = null; routed = null; }
    shown = topology;
  };
  // Read one topology's view. Only the newest read is drawn, and a pending review goes only once it lands.
  const view = async (ref) => {
    const turn = reading.start();
    let answer = null, failure = null;
    if (ref) {
      try { answer = await ctx.api("world.places", { campaign, ...D, topology: ref }); }
      catch (error) { if (error && error.reason === "left") return; failure = error; }
    }
    if (!reading.current(turn)) return;
    shownAt = turn;
    current = ref;
    review = null;
    viewError = failure;
    if (answer && answer.topology) replaced(answer.topology); else { shown = null; found = null; routed = null; }
    draw();
  };
  const readAll = async () => {
    topologies = (await ctx.api("world.topologies", { campaign, ...D })).topologies;
    const kept = chosen.get();
    await view(topologies.some((item) => item.topology === kept) ? kept : topologies.length ? topologies[0].topology : null);
  };
  // A review or a change answers for the read it started from: once a newer read has run (another topology chosen,
  // even the same one chosen again), that read is what is shown, and the answer is not drawn over it.
  const plan = (path, blocked) => ctx.act(async () => {
    if (!reading.current(shownAt)) return true;  // another topology is being read: these roads are about to go
    const since = shownAt;
    const args = { campaign, ...D, topology: shown.topology, path: path.path, blocked, operation_id: newOperationId(), expected: shown.etag };
    try {
      const answer = await ctx.api("world.path_plan", args);
      if (!reading.current(since)) return true;  // read again meanwhile: this review is of what was shown before
      review = { args, plan: answer.plan };
      replaced(answer.topology);
      draw();
    } catch (error) {
      // review it again, from now, on the topology chosen last (unless a newer read already shows it)
      if (error && error.reason === "changed" && reading.current(since)) { review = null; await view(chosen.get() || args.topology); }
      throw error;
    }
    return true;
  });
  const commit = () => ctx.act(async () => {
    const reviewed = review;
    if (!reviewed || !reading.current(shownAt)) return true;  // a newer read is out: it drops this review when it lands
    const since = shownAt;
    try {
      const answer = await ctx.api("world.path_commit", reviewed.args);
      ctx.notify(answer.done.already_applied ? "That change was already made; nothing was changed again." : "The road is changed in this campaign.");
      if (review === reviewed) review = null;
      if (reading.current(since)) { replaced(answer.topology); draw(); }
      // read again meanwhile, perhaps before the change landed: read the topology chosen last once more
      else if (chosen.get() || current) await view(chosen.get() || current);
    } catch (error) {
      if (error && error.reason === "changed") { review = null; await view(chosen.get() || reviewed.args.topology); }
      throw error;
    }
    return true;
  });
  const search = () => ctx.act(async () => {
    find.setError(find.value.trim() ? "" : "Type a name to look for.");
    if (!find.value.trim()) return false;
    const turn = finding.start();
    const asked = shown;
    const answer = await ctx.api("world.find_place", { campaign, ...D, topology: asked.topology, name: find.value.trim() });
    if (finding.current(turn) && shown === asked) { found = answer; draw(); }
    return true;
  });
  const go = (from, to) => ctx.act(async () => {
    const turn = routing.start();
    const asked = shown;
    const ends = { from, to };
    const answer = await ctx.api("world.route", { campaign, ...D, topology: asked.topology, ...ends, conditions: parseConditions(conditions.value) });
    if (routing.current(turn) && shown === asked) { routed = { ...ends, answer }; draw(); }
    return true;
  });

  function draw() {
    const parts = [];
    if (!topologies.length) {
      body.replaceChildren(ui.empty("This library has no topology: its places and roads come from an imported or written source."));
      return;
    }
    if (topologies.length > 1) {
      parts.push(ui.choice("Topology", topologies.map((item) => ({ value: item.topology, label: item.name || item.topology })),
        { value: current, onchange: (value) => { chosen.set(value); view(value); } }).node);
    } else {
      parts.push(el("p", { class: "g-muted" }, "Topology: " + ((shown && shown.name) || topologies[0].name || topologies[0].topology)));
    }
    if (!shown) {
      parts.push(ui.errorBox(viewError || { message: "This topology could not be read." }), ui.actions(ui.button("Read again", () => ctx.act(() => view(current)))));
      body.replaceChildren(...parts);
      return;
    }
    const labels = placeLabels(shown.places);
    parts.push(el("h3", { class: "world-sub" }, "Places"), el("ul", { class: "world-lines" }, shown.places.map((place) => el("li", { class: "g-row" },
      el("span", { class: "g-grow" }, el("strong", {}, labels.get(place.place)),
        place.aliases.length ? el("span", { class: "g-muted" }, " · also " + place.aliases.join(", ")) : null,
        place.parent ? el("span", { class: "g-muted" }, " · in " + (labels.get(place.parent) || place.parent)) : null),
      place.missing ? ui.pill("Missing", { kind: "bad" }) : null))));
    if (shown.missing.length) parts.push(el("p", { class: "g-muted" }, count(shown.missing.length, "place") + " has no definition in this library: a route to or from it is refused."));
    parts.push(el("h3", { class: "world-sub" }, "Roads"), el("ul", { class: "world-lines" }, shown.paths.map((path) => el("li", { class: "g-row world-road" },
      el("span", { class: "g-grow" }, el("strong", {}, path.path), " · " + pathText(path, labels)),
      path.blocked ? ui.pill("Blocked", { kind: "bad" }) : null,
      path.changed ? ui.pill("Changed in this campaign", { kind: "accent" }) : null,
      review ? null : el("button", { type: "button", class: "g-button g-small", "aria-label": (path.blocked ? "Open " : "Block ") + path.path,
        onclick: () => plan(path, !path.blocked) }, path.blocked ? "Open" : "Block")))));
    if (review) {
      parts.push(reviewBox(ui, pathPlanText(review.plan, labels), review.plan.files, () => { review = null; draw(); }, commit));
    }
    parts.push(group(ui, "Find a place", find.node, ui.actions(ui.button("Find", search)),
      found ? (found.matches.length ? el("ul", { class: "world-lines" }, found.matches.map((item) => el("li", {},
        el("strong", {}, item.name || item.place), el("span", { class: "g-muted world-ref" }, " · " + item.place),
        item.missing ? ui.pill("Missing", { kind: "bad" }) : null)))
        : ui.empty("No place is called \"" + found.name + "\".")) : null));
    parts.push(routeForm(labels));
    body.replaceChildren(...parts);
  }

  function routeForm(labels) {
    const options = shown.places.map((place) => ({ value: place.place, label: labels.get(place.place) }));
    // the two ends stay as picked (kept as drafts) across every redraw
    const from = keptChoice(ui, "From", options, fromSlot, options[0] && options[0].value);
    const to = keptChoice(ui, "To", options, toSlot, options[1] ? options[1].value : options[0] && options[0].value);
    let result = null;
    if (routed) {
      const answer = routed.answer;
      const held = answer.conditions.length ? " with " + answer.conditions.join(", ") : " with no condition held";
      const ends = " from " + (labels.get(routed.from) || routed.from) + " to " + (labels.get(routed.to) || routed.to);
      result = answer.found
        ? el("div", { class: "g-stack world-route", role: "status" }, el("p", {}, "Route" + held + ": " + answer.route.places.map((place) => labels.get(place.place) || place.name || place.place).join(", then ") + "."),
          el("p", { class: "g-muted" }, "By " + answer.route.paths.join(", ") + " (" + count(answer.route.paths.length, "road") + ")."))
        : el("p", { class: "world-route", role: "status" }, "No route" + ends + held + ": every way is blocked or needs a condition not held.");
    }
    return group(ui, "Find a route", el("div", { class: "g-row world-pair" }, from.node, to.node), conditions.node,
      ui.actions(ui.button("Find a route", () => go(from.value, to.value))), result);
  }

  await sectionRead(ctx, body, readAll);
}

// -- rumors (063) ---------------------------------------------------------------------------------------------

async function showRumors(slot, ctx, campaign, chat) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Rumors");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(el("p", { class: "g-muted" }, "Who told whom, faithfully or not, and when it arrives. A recipient believes a rumor only once you admit its belief."),
    body, ui.unavailable("Sending or delivering a rumor from this page", "063's rumor queue sends and delivers on its own application key and store grant, which the app does not hold yet."),
    ui.unavailable("Spreading a rumor through a faction or public media", "063 carries a rumor from one character to one other."));
  let shown = null;
  let review = null;
  // the stance picked for each rumor, kept as a draft across a review and across leaving the page
  const stanceOf = (rumor) => ctx.draft("rumors:director:" + campaign + ":" + rumor + ":stance");
  const base = { campaign, chat: chat || undefined, ...D };
  const load = async () => { shown = await ctx.api("world.rumors", base); review = null; draw(); };
  const plan = (rumor) => ctx.act(async () => {
    const args = { ...base, rumor: rumor.rumor, stance: stanceOf(rumor.rumor).get() || "believed", operation_id: newOperationId(), expected: shown.etag };
    try {
      const answer = await ctx.api("world.belief_plan", args);
      review = { args, plan: answer.plan, rumor };
      shown = answer.rumors;
      draw();
    } catch (error) {
      if (error && error.reason === "changed") await load();  // review it again, from now
      throw error;
    }
    return true;
  });
  const commit = () => ctx.act(async () => {
    const reviewed = review;
    if (!reviewed) return true;
    try {
      const answer = await ctx.api("world.belief_commit", reviewed.args);
      if (review === reviewed) review = null;
      shown = answer.rumors;
      ctx.notify(answer.done.already_applied ? "That belief was already admitted; nothing was changed again." : "The belief is admitted.");
      draw();
    } catch (error) {
      if (error && error.reason === "changed") await load();
      throw error;
    }
    return true;
  });

  function draw() {
    const parts = [el("p", {}, el("span", { class: "g-muted" }, "Now: "),
      shown.now ? el("strong", {}, shown.now.date + " " + shown.now.time) : el("span", {}, "this campaign has no clock, so arrivals are fixed days"))];
    if (!shown.rumors.length) parts.push(ui.empty("No rumor has been sent in this campaign."));
    for (const rumor of shown.rumors) parts.push(rumorItem(rumor));
    if (review) {
      const who = review.rumor.recipient.name || review.rumor.recipient.actor;
      const stance = STANCES.find((item) => item.value === review.args.stance).label.toLowerCase();
      parts.push(reviewBox(ui, review.plan.already_applied ? "This belief was already admitted: admitting it again changes nothing."
        : "Admit that " + who + " " + stance + ": \"" + review.plan.text + "\". It is " + who + "'s belief alone, resting on the rumor; nothing becomes true by it.",
      review.plan.files, () => { review = null; draw(); }, commit));
    }
    body.replaceChildren(...parts);
  }

  function rumorItem(rumor) {
    const who = (person) => person ? person.name || person.actor : "someone";
    let belief;
    if (rumor.belief) {
      belief = el("p", {}, who(rumor.recipient) + "'s belief was admitted: " + rumor.belief.stance + ".");
    } else if (rumor.can_admit && !review) {
      const stance = ui.choice("What " + who(rumor.recipient) + " makes of it", STANCES, { value: stanceOf(rumor.rumor).get() || "believed",
        onchange: (value) => stanceOf(rumor.rumor).set(value) });
      belief = el("div", { class: "g-row world-admit" }, stance.node, ui.button("Review", () => plan(rumor)));
    } else if (rumor.can_admit) {
      belief = el("p", { class: "g-muted" }, "Not admitted yet.");
    } else if (rumor.status !== "delivered") {
      belief = el("p", { class: "g-muted" }, "Its recipient's belief can be admitted once it arrives.");
    } else {
      belief = el("p", { class: "g-muted" }, rumor.recipient.role === "player" ? "A player's character's belief is the player's own: this page does not admit it."
        : "Its recipient has no single controller, so its belief is not admitted here.");
    }
    return el("article", { class: "g-stack world-rumor", "aria-label": "Rumor: " + rumor.rumor },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, who(rumor.sender) + " told " + who(rumor.recipient)),
        rumor.faithful ? ui.pill("Faithful", { kind: "good" }) : ui.pill("Distorted: " + rumor.distortion, { kind: "bad" }),
        ui.pill(rumorStatus(rumor.status), { kind: rumor.status === "delivered" ? "accent" : "" })),
      el("p", { class: "world-text" }, "\"" + rumor.text + "\""),
      el("p", { class: "g-muted" }, "From: " + (rumor.source.content || rumor.source.evidence)
        + (rumor.attributed_to && rumor.attributed_to.actor !== rumor.sender.actor ? " · attributed to " + who(rumor.attributed_to) : "")),
      el("p", { class: "g-muted" }, rumorLine(rumor) + ". " + routeText(rumor.route) + ". Confidence " + rumor.confidence + "."),
      belief);
  }

  await sectionRead(ctx, body, load);
}

// -- a faction's decision (057) -------------------------------------------------------------------------------

async function showFaction(slot, ctx, chat, castOf) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Faction decision");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(el("p", { class: "g-muted" }, "Lay out a group from this chat's cast and its leader's choice, and see what 057 decides: the dissent it keeps, and who is told."),
    body, ui.unavailable("Accepting a dissenting member's help as an event", "057 keeps a member's preference as an intention; its help becomes canon only as a separately accepted event, which this page does not make."),
    ui.unavailable("A faction deciding by itself", "057 decides only on its leader's explicit choice: there are no autonomous politics."));
  await sectionRead(ctx, body, async () => layOut(ctx, body, chat, await castOf()));
}

function layOut(ctx, body, chat, cast) {
  const { ui } = ctx;
  const { el } = ui;
  const npcs = cast.filter((member) => member.role === "npc" && member.agent);
  if (!npcs.length) {
    body.replaceChildren(ui.empty("No NPC in this chat's cast can lead a group: only a director-controlled character decides."));
    return;
  }
  const KEY = "faction:director:" + chat + ":";
  const groupName = ui.field("Group", { draft: ctx.draft(KEY + "group"), hint: "Its name, like House Vell" });
  const goals = ui.field("Goals", { draft: ctx.draft(KEY + "goals"), multiline: true, hint: "One a line, at most 16" });
  const policy = ui.field("How it decides", { draft: ctx.draft(KEY + "policy"), hint: "Like: the head of the house decides" });
  const proposal = ui.field("What is proposed", { draft: ctx.draft(KEY + "proposal") });
  const leader = keptChoice(ui, "Leader", npcs.map((member) => ({ value: member.actor, label: member.name || member.actor })), ctx.draft(KEY + "leader"), npcs[0].actor);
  const choice = ui.field("The leader's choice", { draft: ctx.draft(KEY + "choice") });
  // every NPC is a member unless unticked, and no one is told unless ticked: both kept as drafts
  const out = keptSet(ctx.draft(KEY + "not-members"));
  const told = keptSet(ctx.draft(KEY + "told"));
  const prefers = new Map(npcs.map((member) => [member.actor, ui.field((member.name || member.actor) + " prefers", {
    draft: ctx.draft(KEY + "prefers:" + member.actor), hint: "Leave empty for no preference" })]));
  const members = el("fieldset", { class: "world-set" }, el("legend", {}, "Members and what each prefers"),
    npcs.map((member) => el("div", { class: "g-stack world-member" },
      checkbox(ui, (member.name || member.actor) + " is a member", { checked: !out.has(member.actor), value: member.actor,
        onchange: (on) => out.set(member.actor, !on) }),
      prefers.get(member.actor).node)));
  const audience = el("fieldset", { class: "world-set" }, el("legend", {}, "Who is told"),
    cast.map((member) => checkbox(ui, member.name || member.actor, { checked: told.has(member.actor), value: member.actor,
      onchange: (on) => told.set(member.actor, on) })));
  const result = el("div", { class: "g-stack", "aria-live": "polite" });
  const asking = latest();
  const go = () => {
    const lines = goals.value.split("\n").map((line) => line.trim()).filter(Boolean);
    const missing = [[groupName, "Name the group."], [policy, "Say how it decides."], [proposal, "Say what is proposed."], [choice, "Say what the leader chooses."]];
    let ok = true;
    for (const [field, message] of missing) { field.setError(field.value.trim() ? "" : message); ok = ok && Boolean(field.value.trim()); }
    goals.setError(lines.length ? "" : "Give at least one goal.");
    if (!ok || !lines.length) return;
    const laidOut = npcs.filter((member) => !out.has(member.actor) || member.actor === leader.value).map((member) => ({
      actor: member.actor, choice: prefers.get(member.actor).value.trim() || null }));
    const audienceIds = told.values().filter((actor) => cast.some((member) => member.actor === actor));
    ctx.act(async () => {
      const turn = asking.start();
      const answer = await ctx.api("world.faction_preview", { chat, ...D, group: groupName.value.trim(), leader: leader.value, goals: lines,
        policy: policy.value.trim(), proposal: proposal.value.trim(), choice: choice.value.trim(), members: laidOut, told: audienceIds });
      if (asking.current(turn)) result.replaceChildren(...decision(ui, answer));
      return true;
    });
  };
  body.replaceChildren(group(ui, "Lay out the decision", groupName.node, goals.node, policy.node, proposal.node, leader.node, choice.node, members, audience,
    ui.actions(ui.button("Show the decision", go, { kind: "primary" }))), result);
}

function decision(ui, answer) {
  const { el } = ui;
  const names = (people) => people.map((item) => item.name || item.actor).join(", ");
  return [
    ui.notice("Nothing is saved: this is 057's answer for what you laid out. Nothing carries the choice out or tells anyone of it.", { kind: "soft" }),
    el("p", { role: "status" }, el("strong", {}, answer.group + " decides: " + answer.decision.choice + "."),
      " By " + (answer.decision.leader.name || answer.decision.leader.actor) + ", on \"" + answer.proposal + "\"."),
    answer.dissent.length
      ? el("div", { class: "g-stack" }, el("h3", { class: "world-sub" }, "Dissent kept"), el("ul", { class: "world-lines" },
        answer.dissent.map((item) => el("li", {}, (item.name || item.actor) + " preferred \"" + item.choice + "\": an intention, not an act."))))
      : el("p", {}, "No member dissents."),
    answer.agreeing.length ? el("p", { class: "g-muted" }, "Agreeing: " + names(answer.agreeing) + ".") : null,
    el("p", { class: "g-muted" }, answer.told.length ? "Told, once it is announced in play: " + names(answer.told) + "." : "No one is told."),
  ];
}

// -- what an NPC thinks others think (060) --------------------------------------------------------------------

async function showMinds(slot, ctx, chat, castOf) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "What an NPC thinks");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(el("p", { class: "g-muted" }, "Write what an NPC thinks another character thinks, each resting on evidence it holds, and see the tactic it proposes."
    + " The director's truth and the NPC's own beliefs are never changed by it."),
  body, ui.unavailable("Inferring a hypothesis from play", "060 admits only what the director writes; it infers nothing."),
  ui.unavailable("Keeping these hypotheses", "060 holds them in memory for one answer: nothing is saved, and they are not used in play."));
  await sectionRead(ctx, body, async () => minds(ctx, body, chat, await castOf()));
}

async function minds(ctx, body, chat, cast) {
  const { ui } = ctx;
  const { el } = ui;
  const npcs = cast.filter((member) => member.role === "npc" && member.agent);
  if (!npcs.length) {
    body.replaceChildren(ui.empty("No NPC in this chat's cast: only a director-controlled character holds a theory of mind here."));
    return;
  }
  let owner = npcs[0].actor;
  const chips = el("nav", { class: "g-chips", "aria-label": "Whose mind" });
  const pane = el("div", { class: "g-stack" });
  body.replaceChildren(chips, pane);
  // Only the NPC selected now is drawn: an answer (read or preview) for one selected before it is dropped.
  const reading = latest(), previewing = latest();
  const drawChips = () => chips.replaceChildren(...npcs.map((member) => {
    const chip = el("button", { type: "button", class: "g-select g-chip world-chip", "aria-pressed": member.actor === owner ? "true" : "false" },
      member.name || member.actor);
    chip.addEventListener("click", () => { owner = member.actor; drawChips(); open(); });
    return chip;
  }));
  const open = async () => {
    const turn = reading.start();
    pane.replaceChildren(el("p", { class: "g-muted" }, "Reading…"));
    try {
      const mind = await ctx.api("world.mind", { chat, ...D, owner });
      if (reading.current(turn)) drawMind(mind, turn);
    } catch (error) {
      if (error && error.reason === "left") return;
      if (reading.current(turn)) pane.replaceChildren(ui.errorBox(error), ui.actions(ui.button("Read again", () => open())));
    }
  };

  function drawMind(mind, turn) {
    const who = mind.owner.name || mind.owner.actor;
    const KEY = "mind:director:" + chat + ":" + mind.owner.actor + ":";
    const listDraft = ctx.draft(KEY + "hypotheses");
    let hypotheses = readHypotheses(listDraft.get());
    const others = cast.filter((member) => member.actor !== mind.owner.actor);
    const nameOf = (actor) => (cast.find((member) => member.actor === actor) || {}).name || actor;
    const result = el("div", { class: "g-stack", "aria-live": "polite" });
    const listed = el("div", { class: "g-stack" });
    const save = () => listDraft.set(hypotheses.length ? JSON.stringify(hypotheses) : "");

    const people = others.map((member) => ({ value: member.actor, label: member.name || member.actor }));
    const about = keptChoice(ui, "Whose belief", people, ctx.draft(KEY + "about"), people[0] ? people[0].value : "");
    const alsoAbout = keptChoice(ui, "As they think this character believes it (optional)", [{ value: "", label: "No one else" }, ...people], ctx.draft(KEY + "about-also"), "");
    const claim = ui.field("What " + who + " thinks they think", { draft: ctx.draft(KEY + "claim"), hint: "In your own words, like: Anya knows what the letter says" });
    const confidence = ui.field("How sure", { draft: ctx.draft(KEY + "confidence"), hint: "From 0 to 1, or a percentage" });
    const held = new Set(mind.evidence.map((item) => item.evidence));
    const picked = keptSet(ctx.draft(KEY + "evidence"));
    const evidence = el("fieldset", { class: "world-set" }, el("legend", {}, "It rests on what " + who + " holds"),
      mind.evidence.length ? mind.evidence.map((item) => checkbox(ui, item.content + " (" + item.kind + ", " + item.channel + ")", { value: item.evidence,
        checked: picked.has(item.evidence), onchange: (on) => picked.set(item.evidence, on) }))
        : ui.empty(who + " holds no evidence yet: a hypothesis must rest on something it perceived or was told."));
    const supersedes = el("div", {});
    const formError = el("div", { class: "g-field-error", role: "alert", hidden: true });
    const tacticFor = el("div", {});
    const minimum = ui.field("Credit it from", { draft: ctx.draft(KEY + "minimum"), value: "0.5", hint: "The confidence the tactic needs, from 0 to 1" });
    const credited = ui.field("Tactic if credited", { draft: ctx.draft(KEY + "credited"), hint: "Like: bluff that the letter was read aloud" });
    const fallback = ui.field("Tactic otherwise", { draft: ctx.draft(KEY + "fallback"), hint: "Like: ask an open question" });
    let replaces = null, tacticChoice = null;
    const truthSlot = el("div", {});
    let truthShown = mind.truth;
    truthSlot.replaceChildren(truthBlock(ui, truthShown, who));

    const drawList = () => {
      listed.replaceChildren(hypotheses.length ? el("ol", { class: "world-lines" }, hypotheses.map((item) => el("li", { class: "g-row" },
        el("span", { class: "g-grow" }, el("strong", {}, item.id + ": "), whose(item.about.map(nameOf)) + ", \"" + item.claim + "\", "
          + percent(item.confidence) + (item.supersedes ? ", replacing " + item.supersedes : "")),
        replacedBy(hypotheses, item.id).length
          ? el("span", { class: "g-muted" }, "Replaced by " + replacedBy(hypotheses, item.id).join(", ") + ": remove that first")
          : ui.button("Remove", () => { hypotheses = hypotheses.filter((other) => other.id !== item.id); save(); drawList(); }, { size: "small" }))))
        : ui.empty("No hypothesis is written yet."));
      const ids = hypotheses.map((item) => ({ value: item.id, label: item.id + ": " + item.claim.slice(0, 50) + " (" + whose(item.about.map(nameOf)) + ")" }));
      // only a hypothesis nothing replaces yet can be replaced: 060 keeps one chain, each link replaced once
      const open = ids.filter((item) => !replacedBy(hypotheses, item.value).length);
      // both picks are kept as this NPC's draft, while what was picked is still offered
      replaces = keptChoice(ui, "It replaces", [{ value: "", label: "Nothing: a new hypothesis" }, ...open], ctx.draft(KEY + "replaces"), "");
      supersedes.replaceChildren(replaces.node);
      // a tactic rests only on a hypothesis still current: 060's choose_tactic refuses a replaced one
      tacticChoice = open.length ? keptChoice(ui, "Tactic rests on", open, ctx.draft(KEY + "tactic"), open[open.length - 1].value) : null;
      tacticFor.replaceChildren(tacticChoice ? tacticChoice.node : ui.empty("Write a hypothesis to choose a tactic from it."));
    };
    const add = () => {
      const sure = parseConfidence(confidence.value);
      const cited = picked.values().filter((id) => held.has(id));
      const longest = mind.max_text || 400;
      const tooLong = claim.value.trim().length > longest;
      claim.setError(!claim.value.trim() ? "Write what " + who + " thinks."
        : tooLong ? "At most " + longest + " characters: this has " + claim.value.trim().length + "." : "");
      confidence.setError(sure === null ? "Give a number from 0 to 1, or a percentage." : "");
      // 060 lets a hypothesis cite at most MAX_EVIDENCE: more is refused here, before the draft keeps it
      const most = mind.max_evidence || 8;
      const citeProblem = !cited.length ? "Pick the evidence it rests on."
        : cited.length > most ? "A hypothesis rests on at most " + most + " pieces of evidence: untick " + (cited.length - most) + "." : "";
      formError.hidden = !citeProblem;
      formError.textContent = citeProblem;
      if (!claim.value.trim() || tooLong || sure === null || citeProblem || !about.value) return;
      const whom = [about.value, ...(alsoAbout.value && alsoAbout.value !== about.value ? [alsoAbout.value] : [])];
      const problem = replacementProblem(hypotheses, replaces.value, whom);
      if (problem) { formError.hidden = false; formError.textContent = problem; return; }
      hypotheses = [...hypotheses, { id: nextHypothesisId(hypotheses), about: whom, claim: claim.value.trim(), confidence: sure,
        evidence: cited, at: heldAt(cited, mind.evidence, hypotheses.find((item) => item.id === replaces.value)),
        supersedes: replaces.value || null }];
      save();
      claim.clear();
      ctx.draft(KEY + "replaces").clear();
      confidence.clear();
      drawList();
    };
    const preview = () => {
      const least = parseConfidence(minimum.value);
      minimum.setError(least === null ? "Give a number from 0 to 1." : "");
      const longest = mind.max_text || 400;
      const over = (box) => { const long = box.value.trim().length > longest;
        box.setError(long ? "At most " + longest + " characters: this has " + box.value.trim().length + "." : ""); return long; };
      const overlong = [over(credited), over(fallback)].some(Boolean);
      if (!hypotheses.length || least === null || overlong) return;
      const tactic = tacticChoice && !replacedBy(hypotheses, tacticChoice.value).length && credited.value.trim() && fallback.value.trim()
        ? { hypothesis: tacticChoice.value, minimum: least, credited: credited.value.trim(), fallback: fallback.value.trim() } : undefined;
      ctx.act(async () => {
        const asked = previewing.start();
        const answer = await ctx.api("world.mind_preview", { chat, ...D, owner: mind.owner.actor, hypotheses, tactic });
        // dropped when another NPC was chosen since, or a newer preview was asked
        if (!reading.current(turn) || !previewing.current(asked)) return true;
        // the truth as the preview read it, after it: drawn in place of the one read with this NPC
        const moved = JSON.stringify(answer.truth) !== JSON.stringify(truthShown);
        truthShown = answer.truth;
        truthSlot.replaceChildren(truthBlock(ui, truthShown, who));
        result.replaceChildren(...previewed(ui, answer, who, nameOf, moved));
        return true;
      });
    };
    drawList();
    pane.replaceChildren(
      el("h3", { class: "world-sub" }, who + "'s evidence"),
      mind.evidence.length ? el("ul", { class: "world-lines" }, mind.evidence.map((item) => el("li", {}, item.content,
        el("span", { class: "g-muted" }, " · " + item.kind + ", " + item.channel)))) : ui.empty(who + " holds no evidence yet."),
      truthSlot,
      el("h3", { class: "world-sub" }, "Hypotheses"), listed,
      group(ui, "Write a hypothesis", about.node, alsoAbout.node, claim.node, confidence.node, evidence, formError, supersedes,
        ui.actions(ui.button("Add this hypothesis", add))),
      group(ui, "Choose a tactic", tacticFor, minimum.node, credited.node, fallback.node,
        ui.actions(ui.button("Show what " + who + " would do", preview, { kind: "primary" }))),
      result);
  }

  drawChips();
  await open();
}

function truthBlock(ui, truth, who) {
  const { el } = ui;
  const own = truth.beliefs.map((entry) => el("li", {}, el("strong", {}, entry.name || entry.actor), ": ",
    entry.beliefs.length ? entry.beliefs.map((item) => item.stance + " \"" + (item.text || item.proposition) + "\"").join("; ") : "no belief admitted"));
  return el("details", { class: "world-truth" }, el("summary", {}, "The director's truth, and each character's own beliefs"),
    truth.rulings.length ? el("ul", { class: "world-lines" }, truth.rulings.map((item) => el("li", {}, "\"" + (item.text || item.proposition) + "\" is " + item.standing + ".",
      (item.earlier || []).length ? el("span", { class: "g-muted" }, " Ruled earlier: " + item.earlier.map((before) => before.standing).join(", then ") + ".") : null)))
      : ui.empty("No ruling is made."),
    el("ul", { class: "world-lines" }, own),
    el("p", { class: "g-muted" }, "A hypothesis is " + who + "'s guess about a mind: it changes none of this."));
}

function previewed(ui, answer, who, nameOf, moved) {
  const { el } = ui;
  const parts = [ui.notice("Nothing is saved: this is 060's answer for the hypotheses written here.", { kind: "soft" })];
  if (moved) parts.push(el("p", { class: "g-muted" }, "The director's truth or a character's beliefs changed since " + who + " was read: they are shown above as they are now."));
  parts.push(el("h3", { class: "world-sub" }, "What " + who + " holds now"), answer.current.length
    ? el("ul", { class: "world-lines" }, answer.current.map((item) => el("li", {}, item.id + ": " + whose(item.about.map((person) => person.name || nameOf(person.actor)))
      + ", \"" + item.claim + "\", " + percent(item.confidence))))
    : ui.empty("No hypothesis stands."));
  if (answer.tactic) {
    parts.push(el("p", { role: "status" }, el("strong", {}, "Proposed tactic: " + answer.tactic.tactic),
      answer.tactic.credited ? " (" + answer.tactic.hypothesis + " is credited at " + percent(answer.tactic.confidence) + ")"
        : " (" + answer.tactic.hypothesis + " is below " + percent(answer.tactic.minimum) + ", at " + percent(answer.tactic.confidence) + ")"));
  } else {
    parts.push(el("p", { class: "g-muted" }, "Give both tactics to see which one is proposed."));
  }
  parts.push(el("p", { class: answer.unchanged ? "g-muted" : "g-field-error" }, answer.unchanged
    ? "The director's truth and every character's own beliefs are unchanged." : "The library changed while this was worked out: read it again."));
  return parts;
}
