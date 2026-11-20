// Preparing a campaign (Sprint 115): its chats' catalog bindings (056), the ruleset action contracts (062) and one
// prepared scene (064), as the table or the director sees them (docs/PREP.md). Addresses:
//   #/prep/<campaign id>                               the campaign's prep, for the chat played last
//   #/prep/<campaign id>?chat=<ChatRef>                one chat of it
//   ...&audience=director                              the director's view: catalogs and scene preparation too
// Everything is read from, and sent through, the prep.* ops. A ruleset check is a proposal only: nothing is rolled
// or kept. Preparing a scene is one metered model call, estimated first (prep.prepare_plan sends nothing) and sent
// only once the director approves that estimate (prep.prepare, with the review token it answered): anything that
// changed in between refuses it. A prepared scene lives in the app's memory and is never saved. Changing which
// records a chat binds is done on each record's own Library page (its reviewed Attach), never here.

import { campaignName, count } from "../campaign/logic.js";
import { KIND_NAMES, byKind, estimateLines, fieldText, formValues, latest, newRequestId, usageLine } from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];
const FAMILIES = [{ value: "d20", label: "D20" }, { value: "wod", label: "WoD" }, { value: "pbta", label: "PbtA" }, { value: "ars", label: "Ars Magica" }];
// a secret's standing in the director's truth (one ruled unknown or refuted is never offered as a secret)
const STANDINGS = { established: "ruled true", reported: "reported, not ruled on", unasserted: "not ruled on" };
const DRAFT_FIELDS = ["questions", "clues", "tensions", "stakes", "directions"];
const DRAFT_TITLES = { questions: "Questions", clues: "Clues", tensions: "Tensions", stakes: "Stakes", directions: "Directions" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const campaign = ctx.path[0] || null;
  if (!campaign) {
    root.append(ui.notice("Open a campaign's prep from its hub."), ui.actions(ui.buttonLink("All campaigns", "campaign")));
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
  ctx.setTitle("Prep");
  ctx.setCrumb(ui.link("Campaigns", "campaign"), " / ", ui.link(name, "campaign", [campaign], chat ? { chat } : undefined), " / Prep");
  const address = (changes) => {
    const query = { chat, audience, ...changes };
    return ctx.href("prep", [campaign], { chat: query.chat || undefined, audience: query.audience === "director" ? "director" : undefined });
  };
  const views = ui.segments("Whose view", AUDIENCES, { value: audience, onchange: (value) => ctx.navigate(address({ audience: value })) });
  const top = el("div", { class: "g-row prep-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Catalog bindings, ruleset contracts and scene preparation." : "Ruleset contracts for your characters. Catalogs and scene preparation are the director's."),
  ctx.registered("campaign") ? ui.buttonLink("Campaign hub", "campaign", [campaign], { chat: chat || undefined, audience: audience === "director" ? "director" : undefined }) : null);
  root.append(top);
  if (chats.length > 1 || (chat && !chats.some((item) => item.chat === chat))) {
    const options = (chats.some((item) => item.chat === chat) ? chats : [{ chat, label: null }, ...chats])
      .map((item) => ({ value: item.chat, label: item.label || (item.launch && item.launch.name) || item.name || item.chat }));
    const picker = ui.choice("Chat", options, { value: chat, onchange: (value) => ctx.navigate(address({ chat: value })) });
    picker.node.classList.add("prep-chat");
    root.append(picker.node);
  }

  const cards = el("div", { class: "prep-cards" });
  root.append(cards);
  const rulesCard = el("div", {}), catalogCard = el("div", {}), prepCard = el("div", {});
  cards.append(rulesCard, catalogCard, prepCard);
  await Promise.all([
    chat ? showRulesets(rulesCard, ctx, chat, audience) : noChat(rulesCard, ctx, "Ruleset contracts", listing.complete),
    audience === "director" ? showCatalogs(catalogCard, ctx, campaign) : directorsOnly(catalogCard, ctx, "Catalogs",
      "Which catalog records the campaign's chats bind, and what each costs in an NPC's context, are the director's: switch to the director view."),
    audience !== "director" ? directorsOnly(prepCard, ctx, "Scene preparation",
      "Preparing a scene reads the campaign's secrets: it is the director's. Switch to the director view.")
      : chat ? showPreparation(prepCard, ctx, chat) : noChat(prepCard, ctx, "Scene preparation", listing.complete),
  ]);
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("prep-card");
  return node;
}

function group(ui, title, ...children) {
  return ui.el("div", { class: "g-stack prep-group", role: "group", "aria-label": title }, ui.el("h3", { class: "prep-sub" }, title), ...children);
}

function directorsOnly(slot, ctx, title, text) {
  const { ui } = ctx;
  slot.replaceWith(card(ui, title, ui.el("p", { class: "g-muted" }, text)));
}

function noChat(slot, ctx, title, complete) {
  const { ui } = ctx;
  slot.replaceWith(card(ui, title, ui.empty(complete ? "This campaign has no chat in this library."
    : "None of this campaign's chats is among the most recent ones listed.")));
}

function ref(ui, text) { return ui.el("code", { class: "prep-ref" }, text); }

// -- ruleset contracts (062) -----------------------------------------------------------------------------------

async function showRulesets(slot, ctx, chat, audience) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Ruleset contracts");
  slot.replaceWith(node);
  const rules = await ctx.api("prep.rulesets", { chat, audience });
  node.append(ui.unavailable("A ruleset pinned for this campaign", rules.not_pinned),
    ui.notice(rules.noncanonical));
  const familyDraft = ctx.draft("family:" + chat);
  const actorDraft = ctx.draft("actor:" + chat + ":" + audience);
  let family = FAMILIES.some((item) => item.value === familyDraft.get()) ? familyDraft.get() : "d20";
  let actor = rules.actors.some((item) => item.actor === actorDraft.get()) ? actorDraft.get() : (rules.actors[0] || {}).actor || null;
  // a check's answer is drawn only for the choice still on screen, with the values it was sent with
  const gate = latest();
  const body = el("div", { class: "g-stack" });
  const families = ui.segments("Ruleset family", FAMILIES, { value: family, onchange: (value) => { family = value; familyDraft.set(value); gate.bump(); draw(); } });
  node.append(families.node, body);

  function draw() {
    const profile = rules.profiles.find((item) => item.family === family);
    const parts = [el("p", {}, el("strong", {}, profile.title), el("span", { class: "g-muted" }, " · " + profile.edition + " · "), ref(ui, profile.module))];
    parts.push(el("ul", { class: "prep-lines" }, profile.notes.map((text) => el("li", { class: "g-muted" }, text))));
    if (!rules.actors.length) {
      parts.push(ui.empty(audience === "director" ? "No character in this chat's cast has a controller to propose for."
        : "You play no character in this chat, so there is no one to propose an action for. The shapes are below."));
    }
    const who = rules.actors.length ? ui.choice("For", rules.actors.map((item) => ({ value: item.actor,
      label: (item.name || item.actor) + (item.role === "npc" ? " (NPC)" : item.yours ? " (yours)" : "") })),
    { value: actor, onchange: (value) => { actor = value; actorDraft.set(value); gate.bump(); draw(); } }) : null;
    // an unsent action is its proposer's: kept per view, character and family
    const key = (part, field) => "check:" + chat + ":" + audience + ":" + actor + ":" + family + ":" + part + "." + field.name;
    const inputs = {};
    const build = (part, fields) => fields.map((field) => {
      const draft = ctx.draft(key(part, field));
      let made;
      if (field.type === "choice" || field.type === "yesno") {
        const options = field.type === "yesno" ? [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }]
          : field.options.map((option) => ({ value: option, label: option }));
        made = ui.choice(field.label, [{ value: "", label: "Choose…" }, ...options], { value: draft.get(), onchange: (value) => draft.set(value) });
        made.set = (text) => { made.select.value = text; draft.set(text); };
        made.read = () => made.select.value;
      } else {
        const hint = field.type === "dice" ? "Each die as rolled, like 3, 4" + (field.exactly ? " (exactly " + field.exactly + ")" : "")
          : field.type === "whole" ? "A whole number" + (field.note ? ", " + field.note : "") : field.note || (field.optional ? "Optional" : undefined);
        made = ui.field(field.label, { draft, hint });
        made.set = (text) => { made.value = text; };
        made.read = () => made.value;
      }
      inputs[part + "." + field.name] = made;
      return made.node;
    });
    const outcome = el("div", { class: "g-stack" });
    const choose = el("div", { class: "g-field-error", role: "alert", hidden: true });
    const read = () => {
      const action = formValues(profile.action, (name) => inputs["action." + name].read());
      const result = formValues(profile.result, (name) => inputs["result." + name].read());
      const unchosen = [];
      for (const [part, answer] of [["action", action], ["result", result]]) {
        for (const field of profile[part]) {
          const made = inputs[part + "." + field.name];
          if (made.setError) made.setError(answer.errors[field.name] || "");
          else if (answer.errors[field.name]) unchosen.push(field.label);
        }
      }
      choose.textContent = unchosen.length ? "Choose: " + unchosen.join(", ") + "." : "";
      choose.hidden = !unchosen.length;
      const ok = !Object.keys(action.errors).length && !Object.keys(result.errors).length;
      return ok ? { action: action.values, result: result.values } : null;
    };
    const example = () => {
      for (const part of ["action", "result"]) {
        for (const field of profile[part]) inputs[part + "." + field.name].set(fieldText(field, profile.example[part][field.name]));
      }
      gate.bump();
      outcome.replaceChildren();
    };
    const check = () => {
      const values = read();
      if (!values || !actor) return;
      const turn = gate.next();
      const sent = JSON.stringify([family, actor, values]);
      ctx.act(async () => {
        let answer = null, refusal = null;
        try { answer = await ctx.api("prep.ruleset_check", { chat, audience, family, actor, ...values }); }
        catch (error) { if (error && error.reason === "left") return false; refusal = error; }
        // a late answer for an older choice, or for values typed over since, is never shown as this one's
        if (!gate.current(turn) || JSON.stringify([family, actor, read()]) !== sent) {
          outcome.replaceChildren(el("p", { class: "g-muted" }, "The form changed while that was being checked: check it again."));
          return true;
        }
        outcome.replaceChildren(checked(ctx, answer, refusal));
        return true;
      });
    };
    const fields = [
      el("fieldset", { class: "prep-fields" }, el("legend", {}, "The action proposed"), ...build("action", profile.action)),
      el("fieldset", { class: "prep-fields" }, el("legend", {}, "The result as recorded at the table"), ...build("result", profile.result)),
    ];
    parts.push(group(ui, "Check an action", who ? who.node : null, ...fields, choose,
      ui.actions(ui.button("Fill in the example", example), rules.actors.length ? ui.button("Check this action", check, { kind: "primary" }) : null), outcome));
    body.replaceChildren(...parts);
  }
  draw();
}

function checked(ctx, answer, refusal) {
  const { ui } = ctx;
  const { el } = ui;
  if (refusal) {
    return el("div", { class: "g-stack", role: "group", "aria-label": "Check result" },
      ui.notice("The contract refuses it: " + ui.errorText(refusal), { kind: "bad" }));
  }
  const line = (values) => Object.entries(values).filter(([, value]) => value !== null)
    .map(([key, value]) => key + " " + (Array.isArray(value) ? value.join(", ") : String(value))).join(" · ");
  return el("div", { class: "g-stack", role: "group", "aria-label": "Check result" },
    el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, "It fits the " + answer.edition + " contract."), ui.pill("Proposal only")),
    el("p", {}, el("span", { class: "g-muted" }, "Action: "), line(answer.action)),
    el("p", {}, el("span", { class: "g-muted" }, "Result: "), line(answer.result)),
    el("p", { class: "g-muted" }, answer.noncanonical));
}

// -- catalogs (056) ------------------------------------------------------------------------------------------

async function showCatalogs(slot, ctx, campaign) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Catalogs");
  slot.replaceWith(node);
  const answer = await ctx.api("prep.catalogs", { campaign, audience: "director" });
  node.append(el("p", { class: "g-muted" }, "A catalog lists exact records a world offers. A chat uses one only through a binding, "
    + "pinned to one revision; a catalog that excludes a record hides it from discovery, never from a chat that binds it."));
  const catalogs = answer.catalogs.length ? el("ul", { class: "prep-lines" }, answer.catalogs.map((item) => el("li", {},
    el("article", { class: "g-stack prep-catalog", "aria-label": "Catalog " + item.catalog },
      el("div", { class: "g-row" }, el("span", { class: "g-grow" }, ui.sourceLink(item.catalog, item.name)),
        item.problem ? ui.pill(item.problem, { kind: "bad" }) : ui.pill(count(item.available, "record") + " available")),
      item.problem ? el("p", { class: "g-muted" }, "This catalog does not compose, so nothing in it is offered.") : null,
      Object.keys(item.kinds).length ? el("p", { class: "g-muted" }, Object.entries(item.kinds).map(([kind, n]) => count(n, kind)).join(" · ")) : null,
      item.depends_on.length ? el("p", {}, el("span", { class: "g-muted" }, "Builds on: "), ...item.depends_on.map((text) => ref(ui, text))) : null,
      item.excludes.length ? el("p", {}, el("span", { class: "g-muted" }, "Excludes: "), ...item.excludes.map((text) => ref(ui, text))) : null,
      item.records.length ? el("details", { class: "prep-more" }, el("summary", {}, "Its records"),
        el("ul", { class: "prep-lines" }, item.records.map((record) => el("li", { class: "g-row" },
          el("span", { class: "g-grow" }, ui.sourceLink(record.ref, record.name)), el("span", { class: "g-muted" }, record.kind + " · " + record.revision)))),
        item.more_records ? el("p", { class: "g-muted" }, "Only the first " + item.records.length + " are listed.") : null) : null))))
    : ui.empty("This library has no catalog.");
  node.append(el("h3", { class: "prep-sub" }, "In the library"), catalogs);
  if (answer.more_catalogs) node.append(el("p", { class: "g-muted" }, "Only the first " + answer.catalogs.length + " catalogs are shown."));

  node.append(el("h3", { class: "prep-sub" }, "What the chats bind"));
  if (!answer.chats.length) node.append(ui.empty("This campaign has no chat in this library."));
  for (const item of answer.chats) {
    const title = item.name || item.label || item.chat;
    const parts = [el("strong", {}, title)];
    if (item.problem) parts.push(ui.notice("This chat cannot be read: " + item.problem, { kind: "bad" }));
    for (const binding of item.bindings) {
      const offered = binding.available_in.length ? "Available in " + binding.available_in.map((entry) => entry.catalog).join(", ")
        : "In no catalog: bound directly";
      const cost = binding.cost.length ? binding.cost.map((entry) => (entry.name || entry.actor) + "'s context takes about " + count(entry.tokens, "token") + " from it").join("; ") : null;
      parts.push(el("div", { class: "g-stack prep-binding", role: "group", "aria-label": "Binding " + binding.binding },
        el("div", { class: "g-row" }, el("span", { class: "g-grow" }, el("span", { class: "g-muted" }, binding.role + ": "), ui.sourceLink(binding.binding)),
          binding.broken ? ui.pill(binding.broken, { kind: "bad" }) : ui.pill(binding.pinned ? "Pinned at " + binding.revision : "Follows its head")),
        el("p", { class: "g-muted" }, offered + "."),
        binding.excluded_by.length ? el("p", { class: "g-muted" }, "Excluded from discovery by " + binding.excluded_by.join(", ") + "; this chat's binding still holds.") : null,
        cost ? el("p", { class: "g-muted" }, cost + ".") : null));
    }
    if (!item.problem && !item.bindings.length) parts.push(ui.empty("This chat binds no record."));
    for (const npc of item.costs) {
      parts.push(el("p", { class: "g-muted" }, (npc.name || npc.actor) + ": " + (npc.problem ? "its context cannot be read (" + npc.problem + ")"
        : "about " + count(npc.tokens, "token") + " of " + npc.max_tokens + " in its context now")));
    }
    node.append(el("div", { class: "g-stack prep-chat-bindings", "aria-label": "Bindings of " + title, role: "group" }, ...parts));
  }
  if (answer.more_chats) node.append(el("p", { class: "g-muted" }, "Only the most recent chats are shown."));
  node.append(el("p", { class: "g-muted" }, answer.estimate),
    el("h3", { class: "prep-sub" }, "Changing a binding"),
    el("p", {}, "Open a record above and use its Attach to a chat: you review it, and it is refused if the chat changed since."),
    ui.unavailable("Detaching a binding here", "056 adds no write of its own; detaching a working binding has no screen yet."),
    ui.unavailable("Package distribution", "Installing a catalog from a package is Sprint 103's."),
    ui.unavailable("Following a catalog's head", "A chat keeps the exact revision it bound until a reviewed rebase (079)."));
}

// -- scene preparation (064) ---------------------------------------------------------------------------------

async function showPreparation(slot, ctx, chat) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Scene preparation");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(body);
  let state = await ctx.api("prep.preparation", { chat, audience: "director" });
  // the estimate under review, if any: { request, brief, plan }. It is sent exactly as estimated, with its own
  // request id, so a request retried after a lost answer finds the scene it already prepared and sends nothing.
  let review = null;
  let attempt = null;  // { brief, request }: an estimate of the same brief again keeps the same request id
  let stale = "";
  const gate = latest();
  const brief = ui.field("What the scene should be about", { draft: ctx.draft("brief:" + chat), multiline: true,
    hint: "In your own words, at most 2000 characters. The model sees this and the evidence below, nothing else." });

  const reload = async () => { state = await ctx.api("prep.preparation", { chat, audience: "director" }); };
  const estimate = () => {
    const text = brief.value;
    brief.setError(text.trim() ? "" : "Say what the scene should be about.");
    if (!text.trim()) return;
    if (!attempt || attempt.brief !== text) attempt = { brief: text, request: newRequestId() };
    const request = attempt.request;
    const turn = gate.next();
    ctx.act(async () => {
      const answer = await ctx.api("prep.prepare_plan", { chat, audience: "director", request_id: request, brief: text });
      // an estimate for a brief typed over since is never offered for approval as this one's
      if (!gate.current(turn) || brief.value !== text) {
        stale = "The brief changed while the call was being estimated: estimate it again.";
        draw();
        return true;
      }
      stale = "";
      review = { request, brief: text, plan: answer.plan };
      state = { ...state, evidence: answer.evidence };
      draw();
      return true;
    });
  };
  const approve = () => ctx.act(async () => {
    const sent = review;
    try {
      const answer = await ctx.api("prep.prepare", { chat, audience: "director", request_id: sent.request, brief: sent.brief, expected: sent.plan.expected });
      // what was typed while the call was out is a new brief, kept; only the brief that was sent is cleared
      if (brief.value === sent.brief) brief.clear();
      review = null;
      attempt = null;
      state = answer.preparation;
      ctx.notify(answer.done.replay ? "That scene was already prepared; nothing was sent again." : "The scene is prepared. Nothing is saved.");
      draw();
      const shown = node.querySelector(".prep-proposal");
      if (shown) shown.focus();
    } catch (error) {
      if (error && error.reason === "left") throw error;
      review = null;
      await reload();
      draw();
      throw error;
    }
    return true;
  });
  const decide = (request, decision, marks) => ctx.act(async () => {
    const answer = await ctx.api("prep.prepare_review", { chat, audience: "director", request_id: request, decision, ...marks });
    // a decided request is done: the same brief estimated again is a new request (a discarded id is refused)
    if (attempt && attempt.request === request) attempt = null;
    state = answer.preparation;
    ctx.notify(decision === "accept" ? "Accepted for this session. Nothing is saved." : "Discarded.");
    draw();
    return true;
  });

  function draw() {
    const parts = [ui.notice(state.note)];
    parts.push(evidence(ctx, state.evidence));
    const evidenceNone = !state.evidence.evidence.length, placeNone = !state.evidence.locations.length;
    if (state.model) parts.push(ui.unavailable("Preparing a scene", state.model.message));
    else if (evidenceNone) parts.push(ui.unavailable("Preparing a scene", "The library holds no secret, goal, calendar or choice for this chat: nothing could ground a scene."));
    else if (placeNone) parts.push(ui.unavailable("Preparing a scene", "This campaign has no location or place to set a scene in."));
    // the brief stays on screen (and can be written on) while an estimate is reviewed or its call is out: what
    // is approved and sent is the brief the estimate was for, shown in the approval
    else parts.push(group(ui, "Prepare a scene", brief.node, stale ? el("p", { class: "g-muted", role: "status" }, stale) : null,
      review ? approval() : ui.actions(ui.button("Estimate the call", estimate, { kind: "primary" }))));
    parts.push(...proposals());
    if (state.spent.length) {
      parts.push(el("p", { class: "g-muted" }, "This session sent " + count(state.spent.reduce((n, item) => n + item.sent, 0), "call")
        + (state.spent.some((item) => !item.kept) ? ", some of whose replies were refused and kept nothing" : "") + "."));
    }
    body.replaceChildren(...parts);
  }

  function approval() {
    const plan = review.plan;
    if (plan.replay) {
      return group(ui, "Approve this call", el("p", {}, "This request was already prepared: showing it again sends nothing."),
        ui.actions(ui.button("Cancel", () => { review = null; draw(); }), ui.button("Show it again", approve, { kind: "primary" })));
    }
    if (plan.blocked) {
      return group(ui, "Approve this call", ui.unavailable("Preparing this scene", plan.blocked.message),
        ui.actions(ui.button("Back", () => { review = null; draw(); })));
    }
    return group(ui, "Approve this call", el("p", {}, el("span", { class: "g-muted" }, "Brief: "), review.brief),
      ...estimateLines(plan.call).map((text) => el("p", {}, text)),
      el("p", { class: "g-muted" }, "It sends the brief and the " + count(state.evidence.evidence.length, "item") + " of evidence above, and nothing is saved."),
      ui.actions(ui.button("Cancel", () => { review = null; draw(); }), ui.button("Approve and prepare", approve, { kind: "primary" })));
  }

  function proposals() {
    if (!state.proposals.length) return [];
    return [el("h3", { class: "prep-sub" }, "Prepared this session"), ...state.proposals.map((item) => proposal(item))];
  }

  function proposal(item) {
    const draft = item.draft;
    const note = (entry) => el("li", { class: "g-stack prep-note" }, el("span", { class: "prep-text" }, entry.text),
      el("span", { class: "prep-grounds" }, el("span", { class: "g-muted" }, "Grounded in "), ...entry.evidence.map((ground) =>
        el("span", { class: "prep-id", title: ground.source }, ground.evidence + " · " + ground.kind))));
    const usage = usageLine(item.usage);
    const parts = [
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, item.brief),
        item.outcome ? ui.pill(item.outcome === "accepted" ? "Accepted" : "Discarded", { kind: item.outcome === "accepted" ? "good" : "" })
          : item.current ? ui.pill("Proposal") : ui.pill("Evidence changed since", { kind: "bad" })),
      el("p", {}, el("span", { class: "g-muted" }, "Premise: ")), el("ul", { class: "prep-lines" }, note(draft.premise)),
      el("p", {}, el("span", { class: "g-muted" }, "Where: "), draft.location.name || draft.location.actor,
        el("span", { class: "g-muted" }, " · Who: "), draft.cast.map((who) => who.name || who.actor).join(", ")),
    ];
    for (const field of DRAFT_FIELDS) {
      if (draft[field].length) parts.push(el("p", { class: "prep-label" }, DRAFT_TITLES[field]), el("ul", { class: "prep-lines" }, draft[field].map(note)));
    }
    if (usage) parts.push(el("p", { class: "g-muted" }, usage));
    if (!item.outcome && item.current) parts.push(reviewForm(item));
    else if (!item.outcome) parts.push(el("p", { class: "g-muted" }, "The evidence changed since this was prepared: prepare again from what the library holds now, or discard it."),
      ui.actions(ui.button("Discard", () => decide(item.request, "reject", {}))));
    else if (item.outcome === "accepted") parts.push(el("p", { class: "g-muted" }, "Accepted for this session only: it is not canon, an event or a player's choice, and it is gone when the app stops."));
    return el("article", { class: "g-stack prep-proposal", tabindex: "-1", "aria-label": "Prepared scene " + item.request }, ...parts);
  }

  function reviewForm(item) {
    const marks = [["credible_stakes", "Its stakes are credible"], ["open_agency", "It leaves the players' choices open"], ["grounded", "Every item rests on the evidence it names"]];
    const boxes = marks.map(([name, label]) => {
      const box = el("input", { type: "checkbox", name, "data-focus-key": "prep:" + item.request + ":" + name });
      return { name, box, node: el("label", { class: "g-row prep-mark" }, box, el("span", {}, label)) };
    });
    const accept = () => decide(item.request, "accept", Object.fromEntries(boxes.map((entry) => [entry.name, Boolean(entry.box.checked)])));
    return el("fieldset", { class: "prep-fields" }, el("legend", {}, "Your review"), ...boxes.map((entry) => entry.node),
      ui.actions(ui.button("Discard", () => decide(item.request, "reject", {})), ui.button("Accept", accept, { kind: "primary" })));
  }

  draw();
}

function evidence(ctx, shown) {
  const { ui } = ctx;
  const { el } = ui;
  const parts = [];
  for (const [kind, items] of byKind(shown.evidence)) {
    parts.push(el("p", { class: "prep-label" }, KIND_NAMES[kind]), el("ul", { class: "prep-lines" }, items.map((item) => el("li", { class: "g-stack prep-note" },
      el("span", {}, el("span", { class: "prep-id" }, item.evidence), " ", el("span", { class: "prep-text" }, item.text)),
      el("span", { class: "g-muted prep-source" }, (item.name ? item.name + " · " : "") + item.source
        + (STANDINGS[item.standing] ? " · " + STANDINGS[item.standing] : ""))))));
  }
  if (!parts.length) parts.push(ui.empty("The library holds nothing for this chat that a scene could rest on."));
  if (shown.missing.length) parts.push(el("p", { class: "g-muted" }, "Nothing to go on for: " + shown.missing.map((kind) => KIND_NAMES[kind].toLowerCase()).join(", ") + "."));
  const omitted = Object.entries(shown.omitted || {});
  if (omitted.length) parts.push(el("p", { class: "g-muted" }, "Not sent: " + omitted.map(([kind, n]) => n + " more " + KIND_NAMES[kind].toLowerCase()).join(", ") + "."));
  parts.push(el("p", {}, el("span", { class: "g-muted" }, "Cast: "), shown.cast.map((who) => who.name || who.actor).join(", ") || "nobody"),
    el("p", {}, el("span", { class: "g-muted" }, "Places: "), shown.locations.map((where) => where.name || where.actor).join(", ") || "none"));
  return group(ui, "The evidence", el("p", { class: "g-muted" }, "Assembled now from what the library holds. Every item of a prepared scene names the item it rests on."), ...parts);
}
