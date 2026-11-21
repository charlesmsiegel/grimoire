// Several NPCs answering at once (051, Sprint 115): #/ensemble/<ChatRef>?audience=director. The director authors
// one bid for each NPC of the chat on stage (an answer, an interjection, a side remark, a meaningful silence or a
// nonverbal beat, with who it is to, who would hear it, why, and what it rests on) and previews the floor 051 gives
// them. The preview is an unaccepted proposal: nothing is written, nothing is said in the chat and no model is
// asked (ensemble.preview). Running an exchange and accepting one into the story have no app path yet: both are
// named as not available, never offered. The plan is the director's; the table's view says so and shows nothing.
// The bids being written are a draft of this tab (ctx.draft), kept across leaving the page.

import { GROUNDINGS, KINDS, decisionText, defaultGrounding, heardText, readState, requestOf, viewText } from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const chat = ctx.path[0];
  if (!chat) { root.append(ui.notice("Open this from a chat's Details."), ui.actions(ui.buttonLink("All chats", "chats"))); return; }
  ctx.setTitle("Several NPCs answering");
  ctx.setCrumb(ui.link("Chats", "chats"), " / ", ctx.registered("chat") ? ui.link("This chat", "chat", [chat]) : "This chat");
  const audience = ctx.query.get("audience") === "director" ? "director" : "table";
  const views = ui.segments("Whose view", AUDIENCES, { value: audience,
    onchange: (value) => ctx.navigate(ctx.href("ensemble", [chat], { audience: value === "director" ? "director" : undefined })) });
  root.append(el("div", { class: "g-row ensemble-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Plan how several NPCs answer one moment, and see it before anything is said." : "How NPCs coordinate is the director's.")));
  if (audience !== "director") {
    root.append(card(ui, "The exchange", el("p", { class: "g-muted" },
      "Who would answer, who would interject and why are the director's plan. Switch to the director view to plan an exchange.")));
    return;
  }

  const exchange = await ctx.api("ensemble.exchange", { chat, audience });
  const present = exchange.present;
  const npcs = present.filter((item) => item.role === "npc");
  const draft = ctx.draft("ensemble:" + chat);
  const state = readState(draft.get(), present);
  const result = el("div", {});
  let asked = 0;  // only the preview asked last is drawn
  // a change to the plan takes the preview of the old one away, and an answer still on its way is never drawn
  const save = () => { draft.set(JSON.stringify(state)); asked += 1; result.replaceChildren(); };

  root.append(card(ui, "Who is here", el("ul", { class: "ensemble-lines" }, present.map((item) => el("li", {},
    el("strong", {}, item.name || item.actor), el("span", { class: "g-muted" }, " · " + (item.role === "npc" ? "NPC, the director's" : item.role === "player" ? "played by a person: never voiced" : "nobody plays them"))))),
  el("p", { class: "g-muted" }, "The chat's cast on stage, as control is recorded. Only an NPC may bid.")));
  if (!npcs.length) {
    root.append(card(ui, "The exchange", ui.empty("No NPC is on stage in this chat, so nobody would answer.")));
    return;
  }

  // ticks over everyone here: `get()` is the list ticked now, `set(list)` keeps a new one (in cast order)
  const checks = (label, get, set, key) => {
    const fieldset = el("fieldset", { class: "ensemble-checks" }, el("legend", {}, label));
    for (const item of present) {
      const box = el("input", { type: "checkbox", value: item.actor, "data-focus-key": "ensemble:" + key + ":" + item.actor });
      box.checked = get().includes(item.actor);
      box.addEventListener("change", () => {
        const now = get();
        set(present.map((one) => one.actor).filter((actor) => (actor === item.actor ? box.checked : now.includes(actor))));
      });
      fieldset.append(el("label", { class: "g-row ensemble-check" }, box, el("span", {}, item.name || item.actor)));
    }
    return fieldset;
  };
  const people = present.map((item) => ({ value: item.actor, label: item.name || item.actor }));

  // -- the moment ----------------------------------------------------------------------------------------------
  const initiator = ui.choice("Who spoke", people, { value: state.initiator, onchange: (value) => { state.initiator = value; save(); } });
  const floor = ui.choice("Who holds the floor", [{ value: "", label: "Nobody yet" }, ...people], { value: state.floor,
    onchange: (value) => { state.floor = value; save(); } });
  const speaking = el("input", { type: "checkbox", "data-focus-key": "ensemble:speaking" });
  speaking.checked = state.player_speaking;
  speaking.addEventListener("change", () => { state.player_speaking = speaking.checked; save(); });
  const moment = el("div", { class: "g-stack ensemble-form", role: "group", "aria-label": "The moment" },
    el("h3", { class: "ensemble-sub" }, "The moment"), initiator.node,
    checks("Asked of", () => state.addressees, (value) => { state.addressees = value; save(); }, "addressees"), floor.node,
    el("label", { class: "g-row ensemble-check" }, speaking, el("span", {}, "The player is still speaking: every NPC waits")));

  // -- one bid per NPC -----------------------------------------------------------------------------------------
  const bidForm = (member) => {
    const bid = state.bids[member.actor] || (state.bids[member.actor] = { kind: "", addressees: [], audience: [], reason: "", grounding: "", heard: "", beat: "" });
    const name = member.name || member.actor;
    const details = el("div", { class: "g-stack" });
    const text = (label, key, hint, multiline) => {
      const node = ui.field(label, { value: bid[key], multiline, hint });
      node.input.setAttribute("data-focus-key", "ensemble:" + member.actor + ":" + key);
      node.input.addEventListener("input", () => { bid[key] = node.input.value; save(); });
      return node;
    };
    const drawDetails = () => {
      if (!bid.kind) { details.replaceChildren(); return; }
      if (!bid.grounding) bid.grounding = defaultGrounding(bid.kind);
      const grounding = ui.choice("What " + name + "'s bid rests on", GROUNDINGS, { value: bid.grounding,
        onchange: (value) => { bid.grounding = value; save(); drawDetails(); } });
      details.replaceChildren(
        checks("To", () => bid.addressees, (value) => { bid.addressees = value; save(); }, member.actor + ":to"),
        checks("Who would hear it (none ticked: everyone here)", () => bid.audience, (value) => { bid.audience = value; save(); }, member.actor + ":audience"),
        text("Why (private to the director and " + name + ")", "reason", "At most 1000 characters.", true).node,
        grounding.node,
        bid.grounding === "heard" ? text("Which line " + name + " heard", "heard", "Its number among the lines " + name + " heard, from 1.").node : null,
        bid.kind === "nonverbal" ? text("The beat", "beat", "What " + name + " does, in a few words.").node : null);
    };
    const kind = ui.choice(name + "'s bid", KINDS, { value: bid.kind, onchange: (value) => { bid.kind = value; bid.grounding = ""; save(); drawDetails(); } });
    drawDetails();
    return el("div", { class: "g-stack ensemble-bid", role: "group", "aria-label": name + "'s bid" }, kind.node, details);
  };

  const preview = () => ctx.act(async () => {
    const built = requestOf(state, present, chat);
    if (built.error) { status.textContent = built.error; status.hidden = false; return; }
    status.hidden = true;
    const turn = ++asked;
    const answer = await ctx.api("ensemble.preview", built.request);
    if (turn !== asked || ctx.signal.aborted) return;
    draw(answer);
  });
  const status = el("div", { class: "g-field-error", role: "alert", hidden: true });
  root.append(card(ui, "Plan the exchange", moment,
    el("div", { class: "g-stack ensemble-form" }, el("h3", { class: "ensemble-sub" }, "Each NPC's bid"), ...npcs.map(bidForm)),
    status, ui.actions(ui.button("Preview the exchange", preview, { kind: "primary" }))), result);

  function draw(answer) {
    const line = (item) => el("li", { class: "g-stack ensemble-item" }, el("strong", {}, decisionText(item)),
      el("span", { class: "g-muted" }, heardText(item)),
      item.reason ? el("span", { class: "ensemble-why" }, "Why, privately: " + item.reason) : null);
    const node = card(ui, "Proposed exchange",
      el("p", {}, ui.pill("Proposal", { kind: "accent" }), " Not accepted: nothing is said in the chat and nothing is written."),
      answer.held.length ? el("p", {}, "The player is still speaking, so every NPC waits: no NPC interrupts them.") : null,
      answer.order.length ? el("ol", { class: "ensemble-lines" }, answer.order.map(line)) : (answer.held.length ? null : ui.empty("Nobody would speak.")),
      answer.quiet.length ? el("div", { class: "g-stack" }, el("h3", { class: "ensemble-sub" }, "Quiet"),
        el("ul", { class: "ensemble-lines" }, answer.quiet.map(line))) : null,
      answer.held.length ? el("div", { class: "g-stack" }, el("h3", { class: "ensemble-sub" }, "Waiting"),
        el("ul", { class: "ensemble-lines" }, answer.held.map(line))) : null,
      el("p", { class: "g-muted" }, "Run, it would ask the model " + answer.calls + " time" + (answer.calls === 1 ? "" : "s") + ": once for each NPC who speaks, each in its own voice."),
      el("div", { class: "g-stack" }, el("h3", { class: "ensemble-sub" }, "What each character would be aware of"),
        el("ul", { class: "ensemble-lines" }, answer.views.map((view) => el("li", {}, viewText(view))))),
      ui.unavailable("Running this exchange", answer.not_available.run),
      ui.unavailable("Accepting it into the story", answer.not_available.accept));
    result.replaceChildren(node);
  }
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("ensemble-card");
  return node;
}
