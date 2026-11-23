// The Rules page's mechanics module authoring (Sprint 116 over 086): open a module by its id or start a draft from
// a template, edit its JSON text, check it (every problem by its path), save it and publish it, each write reviewed
// first; read a published revision and try it: fill a sheet and grade a check from dice the person enters. A filled
// sheet and a graded check are never saved (086 keeps neither), and the page says so beside them.

import { count } from "../campaign/logic.js";
import { latest } from "../prep/logic.js";
import { fieldHint, moduleIdError, parseFaces, problemGroups, recentIds, sheetValues, shortRevision } from "./logic.js";

const RECENT = "rules:recent";

export async function mountAuthoring(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const moduleId = ctx.query.get("module") || null;
  const revision = moduleId ? ctx.query.get("revision") || null : null;
  if (moduleId && moduleIdError(moduleId)) {
    root.append(ui.notice("This address names a module id 086 would not take: " + moduleId), ui.actions(ui.buttonLink("Mechanics modules", "rules")));
    return;
  }
  ctx.setCrumb(...(moduleId ? [ui.link("Rules", "rules"), " / ",
    revision ? ui.link(moduleId, "rules", [], { module: moduleId }) : moduleId, revision ? " / revision " + shortRevision(revision) : ""] : ["Rules"]));
  const info = await ctx.api("rules.authoring", {});
  root.append(el("p", { class: "g-muted rules-intro" }, "Mechanics modules: sheet types and named checks, written as JSON you own. "
    + "A draft keeps whatever you save, even unfinished; only a draft with no problem publishes, as a revision that is never rewritten. "
    + "A campaign's recorded rolls are on its own Rules page, linked from its hub."));
  const cards = el("div", { class: "rules-cards" });
  root.append(cards);
  if (moduleId) {
    const recent = ctx.draft(RECENT);
    recent.set(JSON.stringify(recentIds(recent.get(), moduleId)));
  }
  if (!moduleId) {
    cards.append(openCard(ctx, info), startCard(ctx, info, ""), notHereCard(ctx, info));
    return;
  }
  if (revision) {
    const slot = el("div", {}), trySlot = el("div", {});
    cards.append(slot, trySlot);
    await showRevision(slot, trySlot, ctx, info, moduleId, revision);
    return;
  }
  const draftSlot = el("div", {}), revisionsSlot = el("div", {});
  cards.append(draftSlot, revisionsSlot);
  // with no draft of this id yet, it can be started here
  if (!await showDraft(draftSlot, revisionsSlot, ctx, info, moduleId)) cards.append(startCard(ctx, info, moduleId));
}

export function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("rules-card");
  return node;
}

export function group(ui, title, ...children) {
  return ui.el("div", { class: "g-stack rules-group", role: "group", "aria-label": title }, ui.el("h3", { class: "rules-sub", tabindex: "-1" }, title), ...children);
}

// Focus the heading of what was just drawn in `node`, so a keyboard or screen reader user is taken to the answer.
export function focusIn(node) {
  const heading = node && (node.querySelector(".rules-sub") || node.querySelector(".rules-answer"));
  if (heading) heading.focus();
}

function code(ui, text) { return ui.el("code", { class: "rules-ref" }, text); }

// -- opening and starting a module ----------------------------------------------------------------------------

function openCard(ctx, info) {
  const { ui } = ctx;
  const { el } = ui;
  const id = ui.field("Module id", { draft: ctx.draft("rules:open"), hint: "Lower case letters, digits and _, like duel_rules" });
  const go = () => {
    const value = id.value.trim();
    const error = moduleIdError(value);
    id.setError(error);
    if (!error) ctx.navigate(ctx.href("rules", [], { module: value }));
  };
  id.input.addEventListener("keydown", (event) => { if (event.key === "Enter") go(); });
  const recent = recentIds(ctx.draft(RECENT).get());
  const listed = (info.unavailable || []).find((item) => item.what.startsWith("A list of"));
  return card(ui, "Open a module",
    listed ? ui.unavailable(listed.what, listed.reason) : null,
    group(ui, "Open by id", id.node, ui.actions(ui.button("Open", go, { kind: "primary" }))),
    recent.length ? el("div", { class: "g-stack" }, el("h3", { class: "rules-sub" }, "Opened in this tab"),
      el("ul", { class: "rules-lines" }, recent.map((item) => el("li", {}, ui.link(item, "rules", [], { module: item }))))) : null);
}

function startCard(ctx, info, given) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Start a draft");
  const body = el("div", { class: "g-stack" });
  node.append(el("p", { class: "g-muted" }, "Start from one of V0.2.1's reference modules, or from nothing. The draft is yours to change; nothing is published until you publish it."), body);
  const id = ui.field("New module id", { draft: ctx.draft("rules:start:id"), value: given, hint: "Lower case letters, digits and _" });
  if (given && !id.value) id.value = given;
  const options = info.templates.map((item) => ({ value: item.template, label: item.name + (item.template === "blank" ? "" : " (" + item.family + ")") }));
  const from = ui.choice("Start from", options, { value: "d20_basic" });
  let review = null;
  const draw = () => {
    if (review) {
      const { plan, check, name } = review;
      const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "rules-sub" }, "Review this draft"),
        el("p", {}, "Start the draft " + plan.module + " from " + name + "."),
        el("p", { class: "g-muted" }, "It writes 1 file: ", code(ui, plan.file), " (" + count(plan.bytes, "byte") + "). "
          + (check.problems.length ? "As written it has " + count(check.problems.length, "problem") + "." : "As written it has no problem.")),
        ui.actions(ui.button("Cancel", () => { review = null; draw(); }), ui.button("Start this draft", start, { kind: "primary" })));
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", "Review this draft");
      body.replaceChildren(box);
      return;
    }
    body.replaceChildren(group(ui, "A new draft", id.node, from.node, ui.actions(ui.button("Review", plan, { kind: "primary" }))));
  };
  const plan = () => {
    const value = id.value.trim();
    const error = moduleIdError(value);
    id.setError(error);
    if (error) return;
    const chosen = from.value;
    ctx.act(async () => {
      const made = await ctx.api("rules.template", { module: value, template: chosen });
      const answer = await ctx.api("rules.save_plan", { module: value, text: made.text, expected: null });
      review = { args: { module: value, text: made.text, expected: null }, plan: answer.plan, check: answer.check,
        name: (info.templates.find((item) => item.template === chosen) || {}).name || chosen };
      draw();
      return true;
    });
  };
  const start = () => ctx.act(async () => {
    try {
      await ctx.api("rules.save", review.args);
    } catch (error) {
      if (error && error.reason === "draft_exists") {
        const existing = review.args.module;
        review = null;
        draw();
        body.append(ui.notice("There is already a draft of " + existing + "."), ui.actions(ui.buttonLink("Open " + existing, "rules", [], { module: existing })));
        return true;
      }
      throw error;
    }
    const made = review.args.module;
    ctx.draft("rules:start:id").clear();
    ctx.notify("The draft " + made + " is started.");
    ctx.navigate(ctx.href("rules", [], { module: made }));
    return true;
  });
  draw();
  return node;
}

function notHereCard(ctx, info) {
  const { ui } = ctx;
  return card(ui, "Not on this screen", ...(info.unavailable || []).filter((item) => !item.what.startsWith("A list of"))
    .map((item) => ui.unavailable(item.what, item.reason)));
}

// -- one draft ------------------------------------------------------------------------------------------------

function problemsBox(ctx, check, said) {
  const { ui } = ctx;
  const { el } = ui;
  if (!check.problems.length) {
    return group(ui, "Problems", el("p", {}, said + ": no problem. ",
      check.revision ? el("span", { class: "g-muted" }, "It would publish as ", code(ui, check.revision),
        check.published ? ", which is already published." : ".") : null));
  }
  const parts = [el("p", {}, said + ": " + count(check.problems.length, "problem") + ". Each names the part at fault.")];
  for (const [head, items] of problemGroups(check.problems)) {
    parts.push(el("h4", { class: "rules-subsub" }, head), el("ul", { class: "rules-lines" }, items.map((item) => el("li", { class: "g-stack rules-problem" },
      el("div", { class: "g-row" }, code(ui, item.path), item.hint ? ui.pill("Did you mean " + item.hint + "?", { kind: "accent" }) : null),
      el("span", {}, item.message)))));
  }
  return group(ui, "Problems", ...parts);
}

async function showDraft(slot, revisionsSlot, ctx, info, moduleId) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Draft " + moduleId);
  slot.replaceWith(node);
  const revisions = card(ui, "Published revisions");
  revisionsSlot.replaceWith(revisions);
  let answer = await ctx.api("rules.module", { module: moduleId });
  const drawRevisions = () => {
    revisions.replaceChildren(el("h2", { class: "g-heading" }, "Published revisions"),
      answer.revisions.length ? el("ul", { class: "rules-lines" }, answer.revisions.map((item) => el("li", { class: "g-stack" },
        ui.link("Revision " + shortRevision(item.revision), "rules", [], { module: moduleId, revision: item.revision }),
        el("span", { class: "g-muted rules-path" }, item.file)))) : ui.empty("Nothing of " + moduleId + " is published yet."),
      el("p", { class: "g-muted" }, "Listed by digest: a revision is its content, not a version number, and is never rewritten."));
  };
  drawRevisions();
  if (!answer.draft) {
    node.append(ui.empty("There is no draft of " + moduleId + "."));
    return false;
  }
  if (answer.draft.text === null) {
    node.append(ui.notice("This draft is not UTF-8 text, so it cannot be edited here. Its " + count(answer.draft.bytes, "byte") + " are kept as they are.", { kind: "bad" }));
    return true;
  }
  const key = "rules:text:" + moduleId;
  const unsent = ctx.draft(key);
  // the etag of the saved draft that the unsaved text in this tab was begun from: a save is reviewed against it, so a
  // draft saved elsewhere meanwhile is never overwritten unseen
  const base = ctx.draft("rules:base:" + moduleId);
  const editor = ui.field("Module JSON", { draft: unsent, value: answer.draft.text, multiline: true,
    hint: "The whole module as JSON. Saving keeps any text, even unfinished JSON; checking writes nothing." });
  // the draft store keeps no empty text, so a cleared editor is remembered by a mark of its own: coming back shows
  // the empty unsaved edit, not the saved text
  const cleared = ctx.draft("rules:cleared:" + moduleId);
  if (cleared.get() && !unsent.get()) editor.input.value = "";
  editor.input.classList.add("rules-editor");
  editor.input.setAttribute("spellcheck", "false");
  const status = el("p", { class: "rules-status", tabindex: "-1" });
  const mine = el("div", {});
  const problems = el("div", {});
  const reviewSlot = el("div", {});
  const checks = latest();
  let review = null;
  const saved = () => answer.draft.text;
  const track = () => {  // the unsaved text is based on the draft as it was when editing began
    if (editor.value === "" && saved() !== "") cleared.set("1"); else cleared.clear();
    if (editor.value === saved()) base.clear();
    else if (!base.get()) base.set(answer.draft.etag);
  };
  const based = () => (editor.value === saved() ? answer.draft.etag : base.get() || answer.draft.etag);
  const stale = () => editor.value !== saved() && based() !== answer.draft.etag;
  const discard = () => { editor.value = saved(); unsent.clear(); base.clear(); cleared.clear(); checks.bump(); problems.replaceChildren(); review = null; drawReview(); drawStatus(); };
  const drawStatus = () => {
    status.replaceChildren(el("span", { class: "g-muted" }, "Saved draft: "), count(answer.draft.bytes, "byte"),
      el("span", { class: "g-muted" }, " · " + answer.file));
    const differs = editor.value !== saved();
    mine.replaceChildren(...(!differs ? [] : stale() ? [
      ui.notice("The saved draft changed since you began editing this text (it was saved from somewhere else). Your text is kept, "
        + "but it does not save over the newer draft until you choose.", { kind: "bad" }),
      ui.actions(ui.button("Keep my text over it", () => { base.set(answer.draft.etag); review = null; drawReview(); drawStatus(); }, { size: "small" }),
        ui.button("Discard my changes", discard, { size: "small" }))]
      : [ui.notice("The text below has changes that are not saved."), ui.actions(ui.button("Discard my changes", discard, { size: "small" }))]));
  };
  track();
  editor.input.addEventListener("input", () => { if (review) { review = null; drawReview(); } track(); drawStatus(); });

  const check = () => {
    const turn = checks.next();
    const sent = editor.value;
    ctx.act(async () => {
      const found = await ctx.api("rules.check", { module: moduleId, text: sent });
      if (!checks.current(turn) || editor.value !== sent) {
        problems.replaceChildren(el("p", { class: "g-muted" }, "The text changed while it was checked: check it again."));
        return true;
      }
      problems.replaceChildren(problemsBox(ctx, found, "The text as typed"));
      focusIn(problems);
      return true;
    });
  };
  const reread = async () => {
    const before = editor.value;
    const unchanged = before === saved();
    answer = await ctx.api("rules.module", { module: moduleId });
    // with no edits of its own, the editor shows the draft as it is now: an unchanged editor is never a stale copy
    // that a later save could put back over the newer draft unseen. Text typed while the draft was read again is
    // an edit, kept with the base it was begun from.
    if (unchanged && editor.value === before && answer.draft && typeof answer.draft.text === "string") {
      editor.value = answer.draft.text; unsent.clear(); base.clear(); cleared.clear();
      problems.replaceChildren(el("p", { class: "g-muted" }, "The draft was saved from somewhere else; it is shown as it is now."));
    }
    drawRevisions();
    drawStatus();
  };
  const planSave = () => ctx.act(async () => {
    const args = { module: moduleId, text: editor.value, expected: based() };
    let found;
    try {
      found = await ctx.api("rules.save_plan", args);
    } catch (error) {
      if (error && error.reason === "changed") await reread();  // saved elsewhere since editing began: say so
      throw error;
    }
    if (editor.value !== args.text) {  // typed on while it was reviewed: that review is not of the text shown
      problems.replaceChildren(el("p", { class: "g-muted" }, "The text changed while the save was reviewed: review it again."));
      return true;
    }
    review = { kind: "save", args, plan: found.plan, check: found.check };
    drawReview();
    return true;
  });
  const save = () => ctx.act(async () => {
    const args = review.args;
    try {
      answer = await ctx.api("rules.save", args);
    } catch (error) {
      if (error && error.reason === "changed") {  // saved elsewhere since it was read: read it again, keep the text
        review = null;
        drawReview();
        await reread();
      }
      throw error;
    }
    review = null;
    // only what was saved is cleared from this tab; text typed meanwhile stays an unsaved change, now based on it
    if (editor.value === args.text) { unsent.clear(); base.clear(); cleared.clear(); } else base.set(answer.draft.etag);
    ctx.notify(answer.done.already ? "That text was already saved; nothing was written again." : "The draft is saved.");
    drawReview();
    drawRevisions();
    drawStatus();
    status.focus();
    return true;
  });
  const planPublish = () => {
    if (editor.value !== saved()) {
      reviewSlot.replaceChildren(ui.notice("Save the draft first: only the saved draft publishes."));
      return;
    }
    ctx.act(async () => {
      let found;
      try {
        found = await ctx.api("rules.publish_plan", { module: moduleId, expected: answer.draft.etag });
      } catch (error) {
        if (error && error.reason === "invalid_module") {  // show every problem of the saved draft
          const draft = await ctx.api("rules.check", { module: moduleId });
          problems.replaceChildren(problemsBox(ctx, draft, "The saved draft"));
          reviewSlot.replaceChildren(ui.notice("A draft with any problem does not publish. Fix the problems below, save, and publish again.", { kind: "bad" }));
          focusIn(problems);
          return true;
        }
        if (error && error.reason === "changed") await reread();
        throw error;
      }
      review = { kind: "publish", args: { module: moduleId, expected: found.plan.etag, revision: found.plan.revision }, plan: found.plan };
      drawReview();
      return true;
    });
  };
  const publish = () => ctx.act(async () => {
    let done;
    try {
      done = await ctx.api("rules.publish", review.args);
    } catch (error) {
      if (error && error.reason === "changed") {
        review = null;
        drawReview();
        await reread();
      }
      throw error;
    }
    answer = done;
    review = null;
    ctx.notify(done.done.already ? "That revision was already published; nothing was written again." : "Published as revision " + shortRevision(done.done.revision) + ".");
    drawReview();
    drawRevisions();
    drawStatus();
    status.focus();
    return true;
  });

  function drawReview() {
    if (!review) { reviewSlot.replaceChildren(); return; }
    let box;
    if (review.kind === "save") {
      const { plan, check: checked } = review;
      box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "rules-sub", tabindex: "-1" }, "Review this save"),
        el("p", {}, plan.unchanged ? "This text is already the saved draft: saving writes nothing."
          : "Save " + count(plan.bytes, "byte") + ", replacing the saved draft of " + count(plan.before, "byte") + "."),
        el("p", { class: "g-muted" }, "It writes 1 file: ", code(ui, plan.file), ". "
          + (checked.problems.length ? "As saved it has " + count(checked.problems.length, "problem") + ": it can be saved, not published." : "As saved it has no problem.")),
        ui.actions(ui.button("Cancel", () => { review = null; drawReview(); }), ui.button("Save the draft", save, { kind: "primary" })));
      box.setAttribute("aria-label", "Review this save");
    } else {
      const { plan } = review;
      box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "rules-sub", tabindex: "-1" }, "Review this publication"),
        el("p", {}, "Publish " + plan.name + " " + plan.version + " (" + plan.family + ") as revision ", code(ui, plan.revision), "."),
        el("p", { class: "g-muted" }, plan.already ? "This revision is already published: publishing again writes nothing."
          : "It writes 1 file, never rewritten: ", plan.already ? null : code(ui, plan.file)),
        el("p", { class: "g-muted" }, "A sheet filled or a check graded under an earlier revision keeps that revision."),
        ui.actions(ui.button("Cancel", () => { review = null; drawReview(); }), ui.button("Publish", publish, { kind: "primary" })));
      box.setAttribute("aria-label", "Review this publication");
    }
    box.setAttribute("role", "group");
    reviewSlot.replaceChildren(box);
    focusIn(box);
  }

  node.append(status, mine, editor.node,
    ui.actions(ui.button("Check", check), ui.button("Save", planSave, { kind: "primary" }), ui.button("Publish", planPublish)),
    reviewSlot, problems);
  drawStatus();
  return true;
}

// -- one published revision, and trying it -------------------------------------------------------------------

async function showRevision(slot, trySlot, ctx, info, moduleId, revision) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Revision " + shortRevision(revision));
  slot.replaceWith(node);
  const answer = await ctx.api("rules.revision", { module: moduleId, revision });
  const view = answer.view;
  node.append(el("p", {}, el("strong", {}, view.name + " " + view.version), el("span", { class: "g-muted" }, " · " + view.family + " family · read-only")),
    el("p", { class: "g-muted" }, "Revision ", code(ui, view.revision), " · ", code(ui, answer.file)),
    ui.actions(ui.buttonLink("Edit the draft", "rules", [], { module: moduleId }, { size: "small" })));
  for (const sheet of view.sheets) {
    node.append(el("article", { class: "g-stack rules-part", "aria-label": "Sheet type " + sheet.label },
      el("strong", {}, sheet.label + " sheet"),
      el("ul", { class: "rules-lines" }, sheet.fields.map((field) => el("li", {}, field.label, el("span", { class: "g-muted" }, " · " + field.type + " · " + fieldHint(field))))),
      sheet.derived.length ? el("p", { class: "g-muted" }, "Derived: " + sheet.derived.map((item) => item.key + " = " + item.formula).join("; ")) : null));
  }
  for (const item of view.checks) {
    node.append(el("article", { class: "g-stack rules-part", "aria-label": "Check " + item.label },
      el("strong", {}, item.label + " check"),
      el("p", { class: "g-muted" }, "On a " + item.sheet + " sheet · dice: " + item.dice.count + " × d" + item.dice.sides
        + (item.dice.target ? ", each at or above " + item.dice.target + " a success" : "")
        + (item.total ? " · total: " + item.total : "") + (item.difficulty !== null ? " · difficulty " + item.difficulty : "")),
      el("p", { class: "g-muted" }, "Outcomes, the first that holds: " + item.outcomes.map((outcome) => outcome.label + " when " + outcome.when).join("; "))));
  }
  node.append(el("details", { class: "rules-more" }, el("summary", {}, "The published file"), el("pre", { class: "rules-file" }, answer.text)));
  const tryCard = card(ui, "Try it");
  trySlot.replaceWith(tryCard);
  showTry(tryCard, ctx, info, moduleId, view);
}

function showTry(node, ctx, info, moduleId, view) {
  const { ui } = ctx;
  const { el } = ui;
  node.append(ui.notice(info.not_saved),
    ...(info.unavailable || []).filter((item) => !item.what.startsWith("A list of")).map((item) => ui.unavailable(item.what, item.reason)));
  if (!view.sheets.length) {
    node.append(ui.empty("This revision has no sheet type to fill."));
    return;
  }
  const body = el("div", { class: "g-stack" });
  let sheetType = view.sheets[0].id;
  const picker = ui.choice("Sheet type", view.sheets.map((sheet) => ({ value: sheet.id, label: sheet.label })),
    { value: sheetType, onchange: (value) => { sheetType = value; gate.bump(); draw(); } });
  node.append(picker.node, body);
  const gate = latest();

  function draw() {
    const sheet = view.sheets.find((item) => item.id === sheetType);
    const inputs = {};
    const fields = sheet.fields.map((field) => {
      const made = ui.field(field.label, { draft: ctx.draft("rules:sheet:" + moduleId + ":" + sheet.id + ":" + field.key),
        multiline: field.type === "list", hint: fieldHint(field) });
      inputs[field.key] = made;
      return made.node;
    });
    const filled = el("div", {});
    const graded = el("div", {});
    const read = () => {
      const found = sheetValues(sheet.fields, (key) => inputs[key].value);
      for (const field of sheet.fields) inputs[field.key].setError(found.errors[field.key] || "");
      return Object.keys(found.errors).length ? null : found.values;
    };
    const fill = () => {
      const values = read();
      if (!values) return;
      const turn = gate.next();
      ctx.act(async () => {
        const answer = await ctx.api("rules.sheet", { module: moduleId, revision: view.revision, sheet_type: sheet.id, values });
        if (!gate.current(turn)) return true;
        filled.replaceChildren(sheetBox(ctx, sheet, answer));
        focusIn(filled);
        return true;
      });
    };
    const checks = view.checks.filter((item) => item.sheet === sheet.id);
    const parts = [group(ui, "Fill " + (/^[aeiou]/i.test(sheet.label) ? "an " : "a ") + sheet.label + " sheet", el("fieldset", { class: "rules-fields" }, el("legend", {}, "Its values"), ...fields),
      ui.actions(ui.button("Fill the sheet", fill, { kind: "primary" })), filled)];
    if (checks.length) {
      let checkId = checks[0].id;
      const which = ui.choice("Check", checks.map((item) => ({ value: item.id, label: item.label })), { value: checkId, onchange: (value) => { checkId = value; hints(); } });
      const modifier = ui.field("Modifier", { value: "0", hint: "A whole number the roller adds" });
      const difficulty = ui.field("Difficulty", { hint: "Empty takes the check's own" });
      const dice = ui.field("Dice as rolled", { hint: "Each face as rolled" });
      const hints = () => {
        const chosen = checks.find((item) => item.id === checkId);
        difficulty.node.querySelector(".g-muted").textContent = chosen.difficulty === null ? "This check has no difficulty of its own: give one if its formulas use it"
          : "Empty takes the check's own, " + chosen.difficulty;
        dice.node.querySelector(".g-muted").textContent = "How many: " + chosen.dice.count + "; each face 1 to " + chosen.dice.sides + ", like 4 or 3, 5, 6";
      };
      const grade = () => {
        const values = read();
        const faces = parseFaces(dice.value);
        const mod = /^\s*-?\d{1,7}\s*$/.test(modifier.value) ? Number(modifier.value) : null;
        const dc = difficulty.value.trim() === "" ? null : /^\s*-?\d{1,7}\s*$/.test(difficulty.value) ? Number(difficulty.value) : undefined;
        dice.setError(faces ? "" : "Give the dice as rolled, like 4 or 3, 5, 6.");
        modifier.setError(mod === null ? "Give a whole number." : "");
        difficulty.setError(dc === undefined ? "Give a whole number, or leave it empty." : "");
        if (!values || !faces || mod === null || dc === undefined) return;
        const turn = gate.next();
        const chosen = checkId;
        ctx.act(async () => {
          let answer = null, refusal = null;
          try {
            answer = await ctx.api("rules.grade", { module: moduleId, revision: view.revision, sheet_type: sheet.id, values,
              check: chosen, dice: faces, modifier: mod, difficulty: dc });
          } catch (error) {
            if (error && error.reason === "left") return false;
            refusal = error;
          }
          if (!gate.current(turn)) return true;
          graded.replaceChildren(gradedBox(ctx, view, checks.find((item) => item.id === chosen), answer, refusal));
          focusIn(graded);
          return true;
        });
      };
      parts.push(group(ui, "Grade a check", el("p", { class: "g-muted" }, "Enter the dice as they were rolled: 086 rolls nothing. The check is graded under this revision and confirmed under it again."),
        which.node, modifier.node, difficulty.node, dice.node, ui.actions(ui.button("Grade", grade, { kind: "primary" })), graded));
      body.replaceChildren(...parts);
      hints();
      return;
    }
    parts.push(ui.empty("No check of this revision uses " + (/^[aeiou]/i.test(sheet.label) ? "an " : "a ") + sheet.label + " sheet."));
    body.replaceChildren(...parts);
  }
  draw();
}

function sheetBox(ctx, sheet, answer) {
  const { ui } = ctx;
  const { el } = ui;
  if (!answer.valid) {
    return el("div", { class: "g-stack rules-answer", role: "group", "aria-label": "The sheet", tabindex: "-1" },
      el("p", {}, "These values do not fit a " + sheet.label + " sheet: " + count(answer.problems.length, "problem") + "."),
      el("ul", { class: "rules-lines" }, answer.problems.map((item) => el("li", {}, code(ui, item.path), " " + item.message))));
  }
  const label = (key) => (sheet.fields.find((field) => field.key === key) || {}).label || key;
  return el("div", { class: "g-stack rules-answer", role: "group", "aria-label": "The sheet", tabindex: "-1" },
    el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, "The sheet fits."), ui.pill("Not saved")),
    el("p", {}, el("span", { class: "g-muted" }, "Values: "), Object.entries(answer.values).map(([key, value]) => label(key) + " " + (Array.isArray(value) ? value.join(", ") || "none" : value)).join(" · ")),
    Object.keys(answer.derived).length ? el("p", {}, el("span", { class: "g-muted" }, "Derived: "), Object.entries(answer.derived).map(([key, value]) => key + " " + value).join(" · ")) : null,
    el("p", { class: "g-muted" }, answer.not_saved));
}

function gradedBox(ctx, view, check, answer, refusal) {
  const { ui } = ctx;
  const { el } = ui;
  if (refusal) {
    return el("div", { class: "g-stack rules-answer", role: "group", "aria-label": "The graded check", tabindex: "-1" }, ui.notice("Not graded: " + ui.errorText(refusal), { kind: "bad" }));
  }
  const record = answer.record;
  return el("div", { class: "g-stack rules-answer", role: "group", "aria-label": "The graded check", tabindex: "-1" },
    el("div", { class: "g-row" }, el("strong", { class: "g-grow rules-outcome" }, check.label + ": " + record.outcome), ui.pill("Not saved")),
    el("p", {}, "Dice " + record.dice.join(", ") + (record.total !== null ? " · total " + record.total : "") + (record.successes !== null ? " · " + count(record.successes, "success") : "")
      + (record.difficulty !== null ? " · difficulty " + record.difficulty : "") + " · modifier " + record.modifier),
    el("p", { class: "g-muted" }, "Confirmed under revision ", code(ui, answer.revision), ": the same dice give the same outcome."),
    el("p", { class: "g-muted" }, answer.not_saved));
}
