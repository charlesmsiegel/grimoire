// A source's authoring tools (Sprint 116), for the library's owner (docs/AUTHORING_PAGE.md). Address:
//   #/authoring/<source id>
// Three parts, each over merged domain behaviour through the authoring.* ops:
//   World suggestions (087): a proposal of places and groups, built here by hand or suggested by one model call that
//     is estimated first and sent only once approved; reviewed for namesakes and link problems; a decision drafted
//     into the exact change set (every file byte for byte, and its preconditions); that change set written on the
//     press of Approve and write, or the proposal discarded. A proposal lives in the app's memory only.
//   Consistency audit (082): a bounded, read-only check of the world's records with every finding's citations, and a
//     check for stale evidence. Nothing is written.
//   Source upgrades (079): what removing the source would leave missing, its optional dependencies, and pinning one
//     at its current version, reviewed first.
// What the domains do not build is named, never offered as a control that fails.

import { estimateLines, latest, usageLine } from "../prep/logic.js";
import {
  BUDGET_NAMES, DEPENDENCY_WORDS, ENTITY_WORDS, KIND_NAMES, REFERENCE_WORDS, RELATION_NAMES, ROLE_NAMES, STATUS_WORDS,
  acceptable, addCandidate, addLink, budgetValues, builderBody, citationLine, constraintsOf, endpointOptions,
  newRequestId, plural, removalLine, removeCandidate, removeLink, restoreBuilder, shortVersion,
} from "./logic.js";

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  ctx.setTitle("Authoring");
  const source = ctx.path[0] || null;
  if (!source) {
    root.append(ui.notice("Open a source's authoring tools from one of its records in the Library."), ui.actions(ui.buttonLink("Library", "sources")));
    return;
  }
  let info;
  try {
    info = await ctx.api("authoring.source", { source });
  } catch (error) {
    if (!error || !["missing_source", "excluded_source", "invalid_field"].includes(error.reason)) throw error;
    root.append(ui.errorBox(error), ui.actions(ui.buttonLink("Library", "sources")));
    return;
  }
  const title = info.title || source;
  ctx.setCrumb(ui.link("Library", "sources", [], { source }), " / ", title, " / Authoring");
  root.append(el("p", { class: "g-muted authoring-lead" }, el("strong", {}, title), " · ", el("code", { class: "authoring-ref" }, source), " · ",
    "Tools for the library's owner. Nothing here is written until you approve the exact change it shows."));
  const cards = el("div", { class: "authoring-cards" });
  root.append(cards);
  // what the cards share: the source as last read, and word that a card wrote to it (so another reads it again)
  const page = { ctx, source, info, reload: async () => { page.info = await ctx.api("authoring.source", { source }); return page.info; },
    watchers: [], wrote: async () => { for (const watch of page.watchers) await watch(); } };
  const suggestions = el("div", {}), audit = el("div", {}), upgrades = el("div", {});
  cards.append(suggestions, audit, upgrades);
  await Promise.all([
    info.world ? showSuggestions(suggestions, page) : notAWorld(suggestions, ctx, "World suggestions", "087 proposes places and groups for a world source only: a card's or a bestiary's records are carried by every world that includes it."),
    info.world ? showAudit(audit, page) : notAWorld(audit, ctx, "Consistency audit", "082 audits a world source's records only."),
    showUpgrades(upgrades, page),
  ]);
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("authoring-card");
  return node;
}

function group(ui, title, ...children) {
  return ui.el("div", { class: "g-stack authoring-group", role: "group", "aria-label": title, tabindex: "-1" },
    ui.el("h3", { class: "authoring-sub" }, title), ...children);
}

function notAWorld(slot, ctx, title, why) {
  slot.replaceWith(card(ctx.ui, title, ctx.ui.unavailable(title + " for this source", why)));
}

function missingList(ui, info, slice) {
  return info.unavailable.filter((item) => item.slice === slice).map((item) => ui.unavailable(item.feature, item.reason));
}

function bytes(ui, text, label) {
  return ui.el("pre", { class: "authoring-bytes", tabindex: "0", "aria-label": label }, text);
}

function focusOn(node) {
  if (node && node.focus) node.focus();
}

// A value inside a CSS attribute selector ([data-proposal="..."]): ids are the server's, but never trusted as CSS.
function cssText(text) {
  if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(String(text));
  return String(text).replace(/["\\]/g, "\\$&");
}

function labelled(root, title) {
  return root.querySelector('[aria-label="' + cssText(title) + '"]');
}

// Show what an answer already settled: a failure to draw it is logged, never reported as the answer failing (a
// paid call that was made must never read as one that was not).
function shown(work) {
  try { work(); } catch (error) { console.error("grimoire: the authoring page could not show an answer", error); }
}

// -- world suggestions (087) -----------------------------------------------------------------------------------

async function showSuggestions(slot, page) {
  const { ctx, source } = page;
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "World suggestions");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(el("p", { class: "g-muted" }, page.info.note), body);
  let proposals = page.info.proposals.slice();
  // what the server holds now, merged in by id: a proposal made while an answer was lost shows up
  const merge = (held) => { for (const view of held.slice().reverse()) put(view); };
  // what the person ticked on each proposal, before it is drafted: { accept: Set, distinct: Set }
  const choices = new Map();
  const chosen = (id) => { if (!choices.has(id)) choices.set(id, { accept: new Set(), distinct: new Set() }); return choices.get(id); };
  const notes = new Map();  // a proposal's latest word from the page, shown on it ("reviewed again because...")
  const builderDraft = ctx.draft("build:" + source);
  let builder = restoreBuilder(builderDraft.get());
  const keep = (next) => { builder = next; builderDraft.set(JSON.stringify(builder)); };
  const brief = ui.field("What to suggest", { draft: ctx.draft("brief:" + source), multiline: true,
    hint: "In your own words, at most 4000 characters. The model sees this, your constraints and the names of this world's places and groups." });
  const constraints = ui.field("Constraints, one to a line (optional)", { draft: ctx.draft("constraints:" + source), multiline: true });
  let review = null;  // { request, ask, plan }: the estimate under review
  let attempt = null;  // { key, request }: estimating the same ask again keeps its request id
  let stale = "";
  const gate = latest();

  const put = (view) => {
    const at = proposals.findIndex((item) => item.proposal === view.proposal);
    if (at >= 0) proposals[at] = view; else proposals.unshift(view);
  };
  const article = (id) => node.querySelector('article[data-proposal="' + cssText(id) + '"]');
  const reviewAgain = async (id, why) => {
    const answer = await ctx.api("authoring.review", { source, proposal: id });
    put(answer.proposal);
    notes.set(id, why);
    shown(() => { draw(); focusOn(article(id)); });
  };

  const build = () => {
    const made = builderBody(builder);
    const errors = node.querySelector(".authoring-build-errors");
    if (made.errors) {
      errors.textContent = made.errors.join(" ");
      errors.hidden = false;
      return;
    }
    errors.hidden = true;
    ctx.act(async () => {
      const answer = await ctx.api("authoring.build", { source, ...made.body });
      keep(restoreBuilder(""));
      put(answer.proposal);
      notes.set(answer.proposal.proposal, "Reviewed against the world as it is now. Nothing is stored yet.");
      ctx.notify("Proposal reviewed. Nothing is stored yet.");
      shown(() => { draw(); focusOn(article(answer.proposal.proposal)); });
      return true;
    });
  };

  const estimate = () => {
    const text = brief.value;
    brief.setError(text.trim() ? "" : "Say what to suggest.");
    if (!text.trim()) return;
    const ask = { brief: text, constraints: constraintsOf(constraints.value) };
    const key = JSON.stringify(ask);
    if (!attempt || attempt.key !== key) attempt = { key, request: newRequestId() };
    const request = attempt.request;
    const turn = gate.next();
    ctx.act(async () => {
      let answer;
      try {
        answer = await ctx.api("authoring.suggest_plan", { source, request_id: request, ...ask });
      } catch (error) {
        if (error && ["request_conflict", "forgotten"].includes(error.reason) && attempt && attempt.request === request) attempt = null;
        throw error;
      }
      if (!gate.current(turn) || JSON.stringify({ brief: brief.value, constraints: constraintsOf(constraints.value) }) !== key) {
        stale = "What you asked changed while the call was being estimated: estimate it again.";
        draw();
        return true;
      }
      stale = "";
      review = { request, ask, plan: answer.plan };
      shown(() => { draw(); focusOn(labelled(node, "Approve this call")); });
      return true;
    });
  };

  const approve = () => ctx.act(async () => {
    const sent = review;
    let answer;
    try {
      answer = await ctx.api("authoring.suggest", { source, request_id: sent.request, ...sent.ask, expected: sent.plan.expected });
    } catch (error) {
      if (error && error.reason === "left") throw error;
      // the estimate is spent (sent, or refused because something moved): estimate again for a new approval. The
      // answer may have been lost after the call was made: what the app holds now is shown, the suggestion with it.
      review = null;
      stale = "";
      try {
        page.info = await page.reload();
        merge(page.info.proposals);
        const made = page.info.proposals.find((item) => item.kind === "model" && item.request.request === sent.request);
        if (made) {
          attempt = null;
          notes.set(made.proposal, "The answer to your approval was lost, but the suggestion was made: here it is. Nothing more was sent.");
        }
      } catch (reload) {
        console.error("grimoire: the authoring page could not read the source again", reload);
      }
      shown(draw);
      throw error;
    }
    // the call was made (or replayed): from here on nothing reports it as failed
    if (brief.value === sent.ask.brief) brief.clear();
    if (JSON.stringify(constraintsOf(constraints.value)) === JSON.stringify(sent.ask.constraints)) constraints.clear();
    review = null;
    attempt = null;
    put(answer.proposal);
    const problem = answer.done.review_problem;
    notes.set(answer.proposal.proposal, answer.done.replay ? "That request was already suggested: nothing was sent again."
      : problem ? "Suggested by the model and kept, but it could not be reviewed (" + problem.replace(/_/g, " ") + "): review it again before you decide."
        : "Suggested by the model and reviewed. Nothing is stored yet.");
    shown(() => {
      ctx.notify(answer.done.replay ? "Already suggested; nothing was sent again." : "The suggestion is here. Nothing is stored yet.");
      draw();
      focusOn(article(answer.proposal.proposal));
    });
    return true;
  });

  const draft = (item) => ctx.act(async () => {
    const marks = chosen(item.proposal);
    const accept = item.candidates.filter((candidate) => marks.accept.has(candidate.key)).map((candidate) => candidate.key);
    const distinct = item.candidates.filter((candidate) => marks.accept.has(candidate.key) && marks.distinct.has(candidate.key)).map((candidate) => candidate.key);
    const args = { source, proposal: item.proposal, accept, distinct };
    if (marks.map) args.topology = marks.map;
    try {
      const answer = await ctx.api("authoring.draft", args);
      put(answer.proposal);
      notes.set(item.proposal, "");
      shown(() => { draw(); focusOn(labelled(node, "The change set of " + item.proposal)); });
    } catch (error) {
      if (error && error.reason === "stale_review") {
        await reviewAgain(item.proposal, "The world changed since this proposal was reviewed, so it was reviewed again: check it and draft again.");
        return true;
      }
      if (error && error.reason === "ambiguous_topology") {
        // the world has several maps and the links must go on one: the person names it, then drafts again
        page.info = await page.reload();
        notes.set(item.proposal, "This world has more than one map: choose the map its links go on, then draft again.");
        shown(() => { draw(); focusOn(article(item.proposal) && article(item.proposal).querySelector('[data-focus-key="authoring:map:' + cssText(item.proposal) + '"]')); });
        return true;
      }
      throw error;
    }
    return true;
  });

  const write = (item) => ctx.act(async () => {
    try {
      const answer = await ctx.api("authoring.apply", { source, proposal: item.proposal, draft: item.draft.digest });
      put(answer.proposal);
      choices.delete(item.proposal);
      notes.set(item.proposal, "");
      ctx.notify(answer.done.already_applied ? "That change set was already written; nothing was written again." : "Written: " + plural(answer.proposal.applied.records.length, "record") + ".");
      try {
        page.info = await page.reload();
        await page.wrote();
      } catch (reload) {
        console.error("grimoire: the authoring page could not read the source again", reload);
      }
      shown(() => { draw(); focusOn(labelled(node, "Written from " + item.proposal)); });
    } catch (error) {
      if (error && ["conflict", "changed"].includes(error.reason)) {
        await reviewAgain(item.proposal, "Nothing was written: the world changed since the draft, so the proposal was reviewed again. Check it and draft again.");
        return true;
      }
      throw error;
    }
    return true;
  });

  const discard = (item) => ctx.act(async () => {
    const answer = await ctx.api("authoring.reject", { source, proposal: item.proposal });
    put(answer.proposal);
    choices.delete(item.proposal);
    ctx.notify("Discarded. Nothing of it was written.");
    shown(() => { draw(); focusOn(article(item.proposal)); });
    return true;
  });

  function draw(focusKey) {
    const active = typeof document !== "undefined" ? document.activeElement : null;
    const kept = focusKey || (active && active.getAttribute && body.contains(active) ? active.getAttribute("data-focus-key") : null);
    const parts = [builderGroup(), suggestGroup()];
    if (proposals.length) parts.push(el("h3", { class: "authoring-sub" }, "Proposals this session"), ...proposals.map(proposalView));
    else parts.push(ui.empty("No proposal yet. Build one by hand or ask the model for one."));
    parts.push(...missingList(ui, page.info, "087"));
    body.replaceChildren(...parts);
    if (kept) focusOn(body.querySelector('[data-focus-key="' + cssText(kept) + '"]'));
  }

  // The hand builder: rows of places and groups, and links between them or to places already in the world.
  function builderGroup() {
    const records = page.info.records;
    const keyed = (control, key) => { control.setAttribute("data-focus-key", "authoring:" + key); return control; };
    const rows = builder.candidates.map((item, at) => {
      const set = (field) => (value) => { keep({ ...builder, candidates: builder.candidates.map((other) => other.key === item.key ? { ...other, [field]: value } : other) }); };
      const name = ui.field("Name", { value: item.name });
      keyed(name.input, "name:" + item.key);
      name.input.addEventListener("input", () => set("name")(name.value));
      const description = ui.field("Description (optional)", { value: item.description, multiline: true });
      keyed(description.input, "description:" + item.key);
      description.input.addEventListener("input", () => set("description")(description.value));
      const kind = ui.choice("Kind", Object.entries(KIND_NAMES).map(([value, label]) => ({ value, label })), { value: item.kind, onchange: (value) => { set("kind")(value); draw(); } });
      keyed(kind.select, "kind:" + item.key);
      const role = ui.choice("Role", Object.entries(ROLE_NAMES).map(([value, label]) => ({ value, label })), { value: item.role, onchange: (value) => { set("role")(value); } });
      keyed(role.select, "role:" + item.key);
      // after a Remove the focus goes to the next row's name, else the one before, else to adding another
      const remove = () => {
        const next = builder.candidates[at + 1] || builder.candidates[at - 1];
        keep(removeCandidate(builder, item.key));
        draw(next ? "authoring:name:" + next.key : "authoring:add-candidate");
      };
      return el("fieldset", { class: "authoring-fields authoring-row" }, el("legend", {}, "Candidate " + item.key),
        name.node, kind.node, role.node, description.node,
        ui.actions(keyed(ui.button("Remove " + item.key, remove), "remove:" + item.key)));
    });
    const links = builder.links.map((link, at) => {
      const set = (field) => (value) => { keep({ ...builder, links: builder.links.map((other) => other.id === link.id ? { ...other, [field]: value } : other) }); };
      const relation = ui.choice("Relation", Object.entries(RELATION_NAMES).map(([value, label]) => ({ value, label })), { value: link.relation, onchange: (value) => set("relation")(value) });
      keyed(relation.select, "relation:" + link.id);
      const from = ui.choice("From", [{ value: "", label: "Choose…" }, ...builder.candidates.map((item) => ({ value: item.key, label: (item.name.trim() || item.key) + " (proposed)" }))],
        { value: link.subject, onchange: (value) => { set("subject")(value); draw(); } });
      keyed(from.select, "from:" + link.id);
      const to = ui.choice("To", [{ value: "", label: "Choose…" }, ...endpointOptions(builder, records, link.subject)], { value: link.object, onchange: (value) => set("object")(value) });
      keyed(to.select, "to:" + link.id);
      const remove = () => {
        const next = builder.links[at + 1] || builder.links[at - 1];
        keep(removeLink(builder, link.id));
        draw(next ? "authoring:relation:" + next.id : "authoring:add-link");
      };
      return el("fieldset", { class: "authoring-fields authoring-row" }, el("legend", {}, "Link " + link.id), relation.node, from.node, to.node,
        ui.actions(keyed(ui.button("Remove " + link.id, remove), "remove:" + link.id)));
    });
    const rationale = ui.field("Why (optional)", { value: builder.rationale, multiline: true });
    keyed(rationale.input, "rationale");
    rationale.input.addEventListener("input", () => keep({ ...builder, rationale: rationale.value }));
    const errors = el("div", { class: "g-field-error authoring-build-errors", role: "alert", hidden: true });
    const full = builder.candidates.length >= page.info.limits.candidates;
    const addCandidateButton = () => {
      keep(addCandidate(builder));
      draw("authoring:name:" + builder.candidates[builder.candidates.length - 1].key);
    };
    const addLinkButton = () => {
      keep(addLink(builder));
      draw("authoring:relation:" + builder.links[builder.links.length - 1].id);
    };
    return group(ui, "Build a proposal by hand",
      el("p", { class: "g-muted" }, "Places and groups for this world, joined by roads and by one place lying within another. A link to a place already in the world names its exact revision."),
      ...rows, ...links,
      builder.candidates.length ? rationale.node : null, errors,
      ui.actions(
        full ? null : keyed(ui.button("Add a place or group", addCandidateButton), "add-candidate"),
        builder.candidates.length ? keyed(ui.button("Add a link", addLinkButton), "add-link") : null,
        builder.candidates.length ? keyed(ui.button("Review this proposal", build, { kind: "primary" }), "build") : null));
  }

  function suggestGroup() {
    const blocked = page.info.suggest.blocked;
    if (blocked) {
      return group(ui, "Ask the model", ui.unavailable("A suggestion from the model", blocked.message),
        blocked.reason === "not_enabled" && ctx.registered("providers") ? ui.actions(ui.buttonLink("Models and costs", "providers")) : null);
    }
    return group(ui, "Ask the model", brief.node, constraints.node,
      stale ? el("p", { class: "g-muted", role: "status" }, stale) : null,
      review ? approval() : ui.actions(ui.button("Estimate the call", estimate, { kind: "primary" })));
  }

  function approval() {
    const plan = review.plan;
    const back = ui.button("Cancel", () => { review = null; draw(); });
    if (plan.replay) {
      return group(ui, "Approve this call", el("p", {}, "This request was already suggested: showing it again sends nothing."),
        ui.actions(back, ui.button("Show it again", approve, { kind: "primary" })));
    }
    if (plan.blocked) return group(ui, "Approve this call", ui.unavailable("This suggestion", plan.blocked.message), ui.actions(back));
    return group(ui, "Approve this call", el("p", {}, el("span", { class: "g-muted" }, "Asking: "), review.ask.brief),
      review.ask.constraints.length ? el("ul", { class: "authoring-lines" }, review.ask.constraints.map((line) => el("li", {}, line))) : null,
      ...estimateLines(plan.call).map((text) => el("p", {}, text)),
      el("p", { class: "g-muted" }, "It sends what you asked and the names of " + plural(plan.context.records, "record") + " in this world"
        + (plan.context.truncated ? " (not all of them: the world has more)" : "") + ". Nothing is stored until you accept it."),
      ui.actions(back, ui.button("Approve and suggest", approve, { kind: "primary" })));
  }

  function nameOf(item, key) {
    const found = item.candidates.find((candidate) => candidate.key === key);
    return found ? found.name : key;
  }

  function endpoint(item, object) {
    if (object.candidate) return el("span", {}, nameOf(item, object.candidate));
    if (object.record) return ui.sourceLink(object.record, object.name);
    return el("code", { class: "authoring-ref" }, object.unknown);
  }

  function proposalView(item) {
    const open = item.state === "open";
    const marks = chosen(item.proposal);
    const note = notes.get(item.proposal);
    const usage = usageLine(item.usage);
    const parts = [
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, item.kind === "model" ? item.request.brief : "Built by hand"),
        ui.pill(item.kind === "model" ? "Suggested by the model" : "Built by you"),
        item.state === "applied" ? ui.pill("Written", { kind: "good" }) : item.state === "rejected" ? ui.pill("Discarded") : ui.pill("Not stored")),
      note ? el("p", { class: "g-muted", role: "status" }, note) : null,
      open && !item.reviewed ? el("p", {}, ui.pill("Not reviewed yet", { kind: "bad" }), " Review it again before you decide: until then it cannot be drafted.") : null,
      usage ? el("p", { class: "g-muted" }, usage) : null,
      item.rationale ? el("p", {}, el("span", { class: "g-muted" }, "Why: "), item.rationale) : null,
    ];
    const rows = item.candidates.map((candidate) => {
      const line = [el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, candidate.name),
        el("span", { class: "g-muted" }, (KIND_NAMES[candidate.kind] || candidate.kind) + " · " + (ROLE_NAMES[candidate.role] || candidate.role)))];
      if (candidate.description) line.push(el("p", { class: "authoring-quote" }, candidate.description));
      for (const duplicate of candidate.duplicates) {
        line.push(el("p", {}, ui.pill("Namesake", { kind: "bad" }), " ", duplicate.existing
          ? el("span", {}, "The world already has ", ui.sourceLink(duplicate.existing, duplicate.existing_name), ".")
          : el("span", {}, "Same name as " + nameOf(item, duplicate.other) + " in this proposal.")));
      }
      if (!acceptable(candidate)) line.push(el("p", { class: "g-muted" }, "A loose idea: it is never stored."));
      else if (open && !item.draft) {
        const box = el("input", { type: "checkbox", "data-focus-key": "authoring:accept:" + item.proposal + ":" + candidate.key });
        box.checked = marks.accept.has(candidate.key);
        box.addEventListener("change", () => { if (box.checked) marks.accept.add(candidate.key); else marks.accept.delete(candidate.key); });
        line.push(el("label", { class: "g-row authoring-mark" }, box, el("span", {}, "Accept " + candidate.name)));
        if (candidate.duplicates.length) {
          const other = el("input", { type: "checkbox", "data-focus-key": "authoring:distinct:" + item.proposal + ":" + candidate.key });
          other.checked = marks.distinct.has(candidate.key);
          other.addEventListener("change", () => { if (other.checked) marks.distinct.add(candidate.key); else marks.distinct.delete(candidate.key); });
          line.push(el("label", { class: "g-row authoring-mark" }, other, el("span", {}, "It is a different " + (KIND_NAMES[candidate.kind] || "record").toLowerCase() + ": keep both")));
        }
      }
      return el("li", {}, ...line);
    });
    parts.push(el("fieldset", { class: "authoring-fields" }, el("legend", {}, "Candidates"), el("ul", { class: "authoring-lines" }, rows)));
    if (item.links.length) {
      parts.push(el("p", { class: "g-muted" }, "Links"), el("ul", { class: "authoring-lines" }, item.links.map((link) => el("li", {},
        el("div", { class: "g-row" }, el("span", { class: "g-grow" }, nameOf(item, link.subject), " · " + (RELATION_NAMES[link.relation] || link.relation).toLowerCase() + " · ", endpoint(item, link.object)),
          link.problem ? ui.pill(link.problem, { kind: "bad" }) : null)))));
    }
    if (item.draft && open) parts.push(changeSet(item));
    if (item.applied) parts.push(written(item));
    if (item.state === "rejected") parts.push(el("p", { class: "g-muted" }, "Discarded: nothing of it was written."));
    const maps = page.info.maps || [];
    if (open && !item.draft && item.reviewed && item.links.length && maps.length > 1) {
      const pick = ui.choice("Map its links go on", [{ value: "", label: "Choose a map…" }, ...maps.map((entry) => ({ value: entry.topology, label: entry.name || entry.topology }))],
        { value: marks.map || "", onchange: (value) => { marks.map = value || null; } });
      pick.select.setAttribute("data-focus-key", "authoring:map:" + item.proposal);
      parts.push(pick.node);
    }
    if (open && !item.draft) {
      const again = ui.button("Review again", () => ctx.act(async () => { await reviewAgain(item.proposal, "Reviewed again against the world as it is now."); return true; }));
      parts.push(item.reviewed
        ? ui.actions(again, ui.button("Discard", () => discard(item)), ui.button("Draft the change", () => draft(item), { kind: "primary" }))
        : ui.actions(ui.button("Discard", () => discard(item)), again));
    }
    return el("article", { class: "g-stack authoring-proposal", tabindex: "-1", "data-proposal": item.proposal, "aria-label": "Proposal " + item.proposal }, ...parts);
  }

  function changeSet(item) {
    const draft = item.draft;
    const files = el("ul", { class: "authoring-lines" }, draft.files.map((file) => el("li", {},
      el("details", { class: "authoring-more" }, el("summary", {}, (file.change === "manifest" ? "Updates the manifest" : file.change === "create" ? "Creates" : file.change === "revise" ? "Revises" : "Writes") + " " + file.path),
        el("p", { class: "g-muted" }, file.summary || (file.size + " bytes")),
        file.text !== null ? bytes(ui, file.text, "The bytes of " + file.path) : el("p", { class: "g-muted" }, "Not text: " + file.size + " bytes, sha256 " + file.sha256)))));
    const parts = [
      el("p", {}, "It writes exactly " + plural(draft.files.length, "file") + ": " + plural(Object.keys(draft.records).length, "new record")
        + (draft.topology ? ", one new revision of the world's map" : "") + " and the manifest."),
      files,
    ];
    if (draft.preconditions.length) {
      parts.push(el("p", { class: "g-muted" }, "Only while these are unchanged:"), el("ul", { class: "authoring-lines" }, draft.preconditions.map((item) =>
        el("li", {}, el("span", {}, ui.sourceLink(item.ref, page.info.records.find((record) => record.ref === item.ref)?.name), " at " + item.revision)))));
    }
    if (draft.dropped.length) parts.push(el("p", { class: "g-muted" }, "Links not written: " + draft.dropped.map((entry) => entry.link + " (" + entry.reason.replace(/_/g, " ") + ")").join(", ") + "."));
    if (draft.discarded.length) parts.push(el("p", { class: "g-muted" }, "Not stored: " + draft.discarded.map((key) => nameOf(item, key)).join(", ") + "."));
    if (!draft.applicable) {
      parts.push(ui.notice("This change set cannot be written as it stands: " + [...draft.blocked, ...draft.conflicts.map((entry) => entry.target + " " + entry.reason)].join("; "), { kind: "bad" }),
        ui.actions(ui.button("Review again", () => ctx.act(async () => { await reviewAgain(item.proposal, "Reviewed again."); return true; }))));
    } else {
      parts.push(ui.actions(ui.button("Change the decision", () => ctx.act(async () => { await reviewAgain(item.proposal, "Reviewed again: choose what to accept."); return true; })),
        ui.button("Approve and write", () => write(item), { kind: "primary" })));
    }
    return group(ui, "The change set of " + item.proposal, ...parts);
  }

  function written(item) {
    const applied = item.applied;
    return group(ui, "Written from " + item.proposal,
      el("ul", { class: "authoring-lines" }, applied.records.map((record) => el("li", {}, el("span", {}, ui.sourceLink(record.ref, record.name),
        el("span", { class: "g-muted" }, " · " + (KIND_NAMES[record.kind] || record.kind)))))),
      applied.topology ? el("p", { class: "g-muted" }, "The world's map is now ", el("code", { class: "authoring-ref" }, applied.topology), ", with "
        + plural(applied.links.length, "link") + " from this proposal.") : null,
      el("p", { class: "g-muted" }, "Everything else in the proposal was not stored."));
  }

  draw();
}

// -- consistency audit (082) ----------------------------------------------------------------------------------

async function showAudit(slot, page) {
  const { ctx, source } = page;
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Consistency audit");
  slot.replaceWith(node);
  const bounds = page.info.limits.budget;
  const fields = Object.fromEntries(Object.entries(bounds).map(([name, bound]) => [name, ui.field(BUDGET_NAMES[name] || name, {
    draft: ctx.draft("budget:" + name), hint: "From " + bound.low + " to " + bound.high + "; " + bound.default + " if left empty." })]));
  const shown = el("div", { class: "g-stack" });
  let report = null, checked = null;
  const gate = latest();
  node.append(el("p", { class: "g-muted" }, "A deterministic, read-only check of this world's records for dangling references, possible duplicates and missing links. Every finding cites the exact record, revision and field it rests on."),
    el("fieldset", { class: "authoring-fields" }, el("legend", {}, "Budget"), ...Object.values(fields).map((field) => field.node)),
    ui.actions(ui.button("Run the audit", run, { kind: "primary" })), shown, ...missingList(ui, page.info, "082"));

  function run() {
    const answer = budgetValues(bounds, (name) => fields[name].value);
    for (const [name, field] of Object.entries(fields)) field.setError(answer.errors ? answer.errors[name] || "" : "");
    if (answer.errors) return;
    const turn = gate.next();
    ctx.act(async () => {
      const done = await ctx.api("authoring.audit", { source, ...answer.values });
      if (!gate.current(turn)) return true;
      report = done.audit;
      checked = null;
      draw();
      focusOn(shown.querySelector('[aria-label="Audit report"]'));
      return true;
    });
  }

  const check = () => {
    const turn = gate.next();
    ctx.act(async () => {
      const done = await ctx.api("authoring.audit_review", { source, audit: report.audit });
      if (!gate.current(turn)) return true;
      checked = done.review;
      draw();
      focusOn(shown.querySelector('[aria-label="Audit report"]'));
      return true;
    });
  };

  function citation(item) {
    const record = page.info.records.find((entry) => entry.ref === item.ref);
    return el("li", {}, el("span", {}, ui.sourceLink(item.ref, item.name || (record && record.name))),
      el("span", { class: "g-muted" }, item.kind + " · " + citationLine(item)));
  }

  function draw() {
    if (!report) { shown.replaceChildren(); return; }
    const stale = new Set(checked ? checked.stale : []);
    const changes = new Map(checked ? checked.findings.map((item) => [item.finding, item.changes]) : []);
    const parts = [
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, STATUS_WORDS[report.status] || report.status), ui.pill("Nothing written")),
      report.reason ? el("p", { class: "g-muted" }, "Stopped at: " + report.reason.replace(/_/g, " ") + ".") : null,
      el("p", { class: "g-muted" }, report.said),
      el("p", {}, plural(report.findings.length, "finding") + " · " + plural(report.read.length, "record") + " read · " + plural(report.unread.length, "record") + " not read · " + plural(report.steps, "step")),
    ];
    if (checked) parts.push(ui.notice(stale.size ? plural(stale.size, "finding") + " rest on evidence that changed since." : "Every finding's evidence is as it was.", { kind: stale.size ? "bad" : "" }));
    parts.push(report.findings.length ? el("ul", { class: "authoring-lines" }, report.findings.map((item) => el("li", {},
      el("article", { class: "g-stack", "aria-label": "Finding " + item.finding },
        el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, item.summary), ui.pill(item.category.replace(/_/g, " ")),
          stale.has(item.finding) ? ui.pill("Evidence changed", { kind: "bad" }) : checked ? ui.pill("Current", { kind: "good" }) : null),
        el("ul", { class: "authoring-lines" }, item.citations.map(citation)),
        (changes.get(item.finding) || []).length ? el("p", { class: "g-muted" }, "Changed: " + changes.get(item.finding).map((change) => change.entity_id + " " + change.field + " (" + change.reason.replace(/_/g, " ") + (change.revision ? ", now " + change.revision : "") + ")").join("; ") + ".") : null))))
      : ui.empty("No finding: nothing the audit checks is wrong in the records it read."));
    if (report.rejected.length) {
      parts.push(el("details", { class: "authoring-more" }, el("summary", {}, plural(report.rejected.length, "finding") + " the audit rejected"),
        el("ul", { class: "authoring-lines" }, report.rejected.map((item) => el("li", { class: "g-muted" }, (item.summary || item.category || "Step " + item.step) + ": " + item.reason.replace(/_/g, " "))))));
    }
    parts.push(el("details", { class: "authoring-more" }, el("summary", {}, "Records read (" + report.read.length + ")"),
      el("ul", { class: "authoring-lines" }, report.read.map((item) => el("li", {}, el("span", {}, ui.sourceLink(item.ref, item.name), el("span", { class: "g-muted" }, " · " + item.kind)))))));
    if (report.unread.length) {
      parts.push(el("details", { class: "authoring-more" }, el("summary", {}, "Records not read (" + report.unread.length + ")"),
        el("ul", { class: "authoring-lines" }, report.unread.map((item) => el("li", {}, el("code", { class: "authoring-ref" }, item.ref))))));
    }
    parts.push(ui.actions(ui.button("Check for stale evidence", check)));
    shown.replaceChildren(group(ui, "Audit report", ...parts));
  }
}

// -- source upgrades (079) ------------------------------------------------------------------------------------

async function showUpgrades(slot, page) {
  const { ctx, source } = page;
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Source upgrades");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(body);
  let state = await ctx.api("authoring.upgrades", { source });
  let plan = null;  // { plan } of the pin under review
  let pick = "";
  const gate = latest();

  const review = () => {
    if (!pick) return;
    const turn = gate.next();
    ctx.act(async () => {
      let answer;
      try {
        answer = await ctx.api("authoring.pin_plan", { source, dependency: pick, expected: state.dependencies.etag });
      } catch (error) {
        // the manifest moved since it was read: read it again, so the next review is against what is there now
        if (error && error.reason === "changed") { state = await ctx.api("authoring.upgrades", { source }); draw(); }
        throw error;
      }
      if (!gate.current(turn)) return true;
      plan = answer.plan;
      draw();
      focusOn(body.querySelector('[aria-label="Review the pin"]'));
      return true;
    });
  };

  const pinIt = () => ctx.act(async () => {
    const sent = plan;
    try {
      const answer = await ctx.api("authoring.pin", { source, dependency: sent.dependency, version: sent.version, expected: sent.expected });
      state = { ...state, dependencies: answer.dependencies };
      plan = null;
      pick = "";
      ctx.notify(answer.done.already_applied ? "Already pinned; nothing was written again." : "Pinned " + sent.dependency + " at its current version.");
    } catch (error) {
      if (error && error.reason === "left") throw error;
      plan = null;
      state = await ctx.api("authoring.upgrades", { source });
      draw();
      throw error;
    }
    draw();
    focusOn(body.querySelector('[aria-label="Optional dependencies"]'));
    return true;
  });

  function removal() {
    const shown = state.removal;
    const parts = [el("p", {}, removalLine(shown)),
      shown.complete ? null : ui.notice("This preview is incomplete: some files could not be read, so a reference may be missing from it.", { kind: "bad" })];
    if (shown.files.length) {
      parts.push(el("ul", { class: "authoring-lines" }, shown.files.map((file) => el("li", {},
        el("article", { class: "g-stack", "aria-label": "References in " + file.path },
          el("div", { class: "g-row" }, el("code", { class: "authoring-ref g-grow" }, file.path), ui.pill(file.record)),
          el("ul", { class: "authoring-lines" }, file.references.map((reference) => el("li", {},
            el("div", { class: "g-row" }, el("code", { class: "authoring-ref g-grow" }, reference.text),
              ui.pill(REFERENCE_WORDS[reference.status] || reference.status, { kind: reference.attention ? "bad" : "" })),
            reference.newer_head ? el("p", { class: "g-muted" }, "Its head would be " + reference.newer_head + "; the pin stays.") : null))))))));
      if (shown.more_files) parts.push(el("p", { class: "g-muted" }, plural(shown.more_files, "more file") + " not listed."));
    }
    if (shown.entities.length) {
      parts.push(el("details", { class: "authoring-more" }, el("summary", {}, "This source's records (" + shown.entities.length + ")"),
        el("ul", { class: "authoring-lines" }, shown.entities.map((item) => el("li", {}, el("div", { class: "g-row" },
          el("span", { class: "g-grow" }, item.name || el("code", { class: "authoring-ref" }, item.ref)), ui.pill(ENTITY_WORDS[item.status] || item.status)))))));
    }
    if (shown.problems.length) {
      parts.push(el("p", { class: "g-muted" }, "Could not be read: " + shown.problems.map((problem) => problem.path + " (" + problem.code + ")").join(", ") + "."));
    }
    parts.push(ui.unavailable("Removing or disabling this source", state.apply.reason));
    return group(ui, "If this source were removed", ...parts);
  }

  function dependencies() {
    const shown = state.dependencies;
    const parts = [el("p", { class: "g-muted" }, "Sources this one optionally builds on, each pinned at a version or following its head. Pinning rewrites no reference anywhere.")];
    if (shown.problem) parts.push(ui.notice("The declarations cannot be read: " + shown.problem.message, { kind: "bad" }));
    parts.push(shown.declared.length ? el("ul", { class: "authoring-lines" }, shown.declared.map((item) => el("li", {},
      el("div", { class: "g-row" }, el("code", { class: "authoring-ref g-grow" }, item.source), ui.pill(DEPENDENCY_WORDS[item.status] || item.status, { kind: item.status === "pinned" ? "good" : item.status === "following" ? "" : "bad" })),
      el("p", { class: "g-muted" }, "Declared " + shortVersion(item.version) + " · now " + shortVersion(item.current)))))
      : ui.empty("This source declares no dependency."));
    if (shown.loaded && !shown.problem) {
      const options = [...shown.declared.filter((item) => item.status === "drifted" || item.status === "following").map((item) => item.source), ...shown.offered];
      if (options.length) {
        const picker = ui.choice("Pin a dependency at its current version", [{ value: "", label: "Choose a source…" }, ...options.map((value) => ({ value, label: value }))],
          { value: pick, onchange: (value) => { pick = value; plan = null; gate.bump(); draw(); } });
        parts.push(picker.node, plan ? pinReview() : ui.actions(ui.button("Review the pin", review, { kind: "primary" })));
      }
    }
    return group(ui, "Optional dependencies", ...parts);
  }

  function pinReview() {
    const file = plan.files[0];
    return group(ui, "Review the pin",
      el("p", {}, "Declares ", el("code", { class: "authoring-ref" }, plan.dependency), " at " + shortVersion(plan.version) + (plan.declared ? ", replacing its declaration." : ".")),
      el("p", { class: "g-muted" }, "It writes one file, refused if the manifest changed since you read it:"),
      file ? el("details", { class: "authoring-more" }, el("summary", {}, "Replaces " + file.path), file.diff ? bytes(ui, file.diff, "The change to " + file.path) : null, bytes(ui, file.text || "", "The bytes of " + file.path)) : null,
      ui.actions(ui.button("Cancel", () => { plan = null; draw(); }), ui.button("Pin it", pinIt, { kind: "primary" })));
  }

  // a proposal written from this page moved the manifest: what was read here is read again, and a pin under review
  // (against the old manifest) is set aside
  page.watchers.push(async () => {
    state = await ctx.api("authoring.upgrades", { source });
    plan = null;
    gate.bump();
    draw();
  });

  function draw() {
    body.replaceChildren(removal(), dependencies(),
      group(ui, "Updating this source", ui.unavailable("Previewing an update", state.update.reason)));
  }
  draw();
}
