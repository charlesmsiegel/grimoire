// A campaign's story continuity (Sprint 115): the scenes two devices changed apart offline (049) and the review of
// a fork or retcon (054), as the director sees them (docs/FORKS.md). Addresses:
//   #/forks/<campaign id>                         the table's view: this is the director's, and nothing is offered
//   #/forks/<campaign id>?audience=director       the director's view
//   ...&chat=<ChatRef>                            the chat the campaign hub and the chronicle were opened for
// Everything is read from, and written through, the forks.* ops. Both versions of a divergent scene stay kept
// until the person resolves it: this page resolves nothing itself, says why keeping one side is refused where it
// is, and links to Devices and sync and to Needs you, which keep one side where that is allowed. A preview
// writes no story change. The one write, recording what rests on what, is reviewed first (forks.declare_plan) and made as
// reviewed (forks.declare_commit, with the same operation id and the snapshot revision as reviewed).

import { campaignName } from "../campaign/logic.js";
import {
  actionText, certaintyText, clip, count, declaration, declarationText, declaredText, kindText, latest,
  newOperationId, reasonText, recordText, syncText, takingText,
} from "./logic.js";

const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  const campaign = ctx.path[0] || null;
  if (!campaign) {
    root.append(ui.notice("Open a campaign's story from its hub."), ui.actions(ui.buttonLink("All campaigns", "campaign")));
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
  const chatNames = new Map(chats.map((item) => [item.chat, item.label || item.title || null]));
  ctx.setTitle("Forks and retcons");
  ctx.setCrumb(ui.link("Campaigns", "campaign"), " / ", ui.link(name, "campaign", [campaign], chat ? { chat } : undefined), " / Forks and retcons");
  const query = (view) => ({ chat: chat || undefined, audience: view === "director" ? "director" : undefined });
  const views = ui.segments("Whose view", AUDIENCES, { value: audience, onchange: (value) => ctx.navigate(ctx.href("forks", [campaign], query(value))) });
  root.append(el("div", { class: "g-row forks-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Scenes your devices changed apart, and what a retcon would touch." : "A story's continuity is the director's."),
  ctx.registered("campaign") ? ui.buttonLink("Campaign hub", "campaign", [campaign], query(audience)) : null,
  ctx.registered("chronicle") ? ui.buttonLink("Chronicle", "chronicle", [campaign], query(audience)) : null));

  if (audience !== "director") {
    root.append(card(ui, "Forks and retcons", el("p", { class: "g-muted" },
      "Scenes two devices changed apart offline, and what a retcon or fork would touch, are the director's: they show the exact "
      + "versions every device kept. Switch to the director view to review them.")));
    return;
  }
  const cards = el("div", { class: "forks-cards" });
  root.append(cards);
  const divergentSlot = el("div", {}), retconSlot = el("div", {});
  cards.append(divergentSlot, retconSlot);
  const retcon = await showRetcon(retconSlot, ctx, campaign, chatNames);
  await showDivergent(divergentSlot, ctx, campaign, chatNames, retcon);
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("forks-card");
  return node;
}

function group(ui, label, ...children) {
  return ui.el("div", { class: "g-stack forks-group", role: "group", "aria-label": label }, ...children);
}


// Where a retained fork stands on this device (forks.divergent's `state`), as a pill and a sentence.
const FORK_STATES = {
  reconciled: ["Reconciled here", "This device holds the restored scenes and the accepted effects ordinary sync brought across, each once."],
  played_on: ["Reconciled, played on since", "What ordinary sync owed this device is no longer as it was before the fork, and play went on here since: nothing of the fork is waiting. Sync applies each accepted effect once."],
  waiting: ["Waiting on sync", "Ordinary sync still has to bring the accepted effects across; it applies each once."],
  elsewhere: ["Made on other devices", ""],
};
const forkState = (fork) => (FORK_STATES[fork.state] ? fork.state : fork.reconciled_here ? "reconciled" : "waiting");

function chatText(chatNames, chat) {
  if (!chat) return "";
  return chatNames.get(chat) || chat.split("#chat/").pop();
}

// -- divergent offline scenes (049) ---------------------------------------------------------------------------

async function showDivergent(slot, ctx, campaign, chatNames, retcon) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Divergent offline scenes");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(el("p", { class: "g-muted" }, "When this device and another one each changed a scene while apart, sync keeps both "
    + "versions and holds the scene, with the accepted events and holdings that rest on it, until you resolve it. Nothing here "
    + "picks a version for you."), body);
  const reading = latest();
  const load = async () => {
    const turn = reading.next();
    body.replaceChildren(el("p", { class: "g-muted" }, "Reading…"));
    try {
      const answer = await ctx.api("forks.divergent", { campaign, audience: "director" });
      if (reading.current(turn)) draw(answer);
    } catch (error) {
      if (error && error.reason === "left") return;
      if (reading.current(turn)) body.replaceChildren(ui.errorBox(error));
    }
  };

  function draw(answer) {
    const parts = [];
    if (answer.peers.length) parts.push(el("ul", { class: "forks-lines" }, answer.peers.map((peer) => el("li", {}, syncText(peer)))));
    else parts.push(el("p", { class: "g-muted" }, "No sync with another device since the app started: what earlier syncs kept is listed below."));
    if (answer.unknown) parts.push(ui.notice("Some of this campaign's sync records cannot be read here, so any of its files may be in conflict.", { kind: "bad" }));
    if (answer.ancestry.length) {  // 054 cannot establish these scenes' ancestry: no restoration can be shown safe
      parts.push(el("div", { class: "g-stack", role: "group", "aria-label": "Uncertain ancestry" },
        el("p", {}, el("strong", {}, "Uncertain ancestry: "), "the campaign's records cannot establish which scenes these rest on, "
          + "so neither version of a scene can be shown to fit what follows from it."),
        el("ul", { class: "forks-lines" }, answer.ancestry.map((problem) => el("li", { class: "forks-said" }, problem)))));
    }
    if (!answer.scenes.length) parts.push(ui.empty("No scene of this campaign is kept in two versions on this device."));
    for (const scene of answer.scenes) parts.push(divergentScene(scene));
    if (answer.held.length) {
      parts.push(el("div", { class: "g-stack" }, el("h3", { class: "forks-sub" }, "Held with them"),
        el("p", { class: "g-muted" }, "The last sync did not bring these across either, so no accepted effect is applied twice or lost: "
          + answer.held.map((item) => kindText(item.kind).toLowerCase() + " " + item.path.split("/").pop().replace(/\.json$/, "")).join(", ") + ".")));
    }
    if (answer.fork_choices.length) {
      parts.push(el("p", {}, count(answer.fork_choices.length, "chat") + " of this campaign wait on a choice of which branch goes on. ",
        ctx.registered("inbox") ? ui.link("Choose in Needs you", "inbox") : "Needs you is not in this app yet."));
    }
    parts.push(retained(answer.retained, answer.device));
    for (const problem of answer.problems) parts.push(ui.notice(problem.path + ": " + problem.code, { kind: "bad" }));
    parts.push(el("div", { class: "g-stack" }, el("h3", { class: "forks-sub" }, "Not offered here"),
      el("ul", { class: "forks-lines" }, answer.unavailable.map((item) => el("li", {}, ui.unavailable(item.what, item.why))))));
    parts.push(ui.actions(
      ctx.registered("sync") ? ui.buttonLink("Devices and sync", "sync") : null,
      ui.button("Read again", () => ctx.act(async () => { await load(); return true; }))));
    body.replaceChildren(...parts);
  }

  // one kept version; `where` says whose it is, for a deletion's words
  function side(title, candidate, where) {
    const said = candidate.deleted ? "Deleted on " + where + "." : candidate.set_aside ? "Set aside by a fork choice: heard by no one."
      : candidate.unreadable ? "This version cannot be read here." : candidate.said || "(no words)";
    return el("div", { class: "g-stack forks-side" }, el("div", { class: "g-eyebrow" }, title),
      el("p", { class: "forks-said" }, said));
  }

  function divergentScene(scene) {
    const title = scene.kind === "scene" ? "Scene " + scene.number + (scene.chat ? " · " + chatText(chatNames, scene.chat) : "")
      : kindText(scene.kind) + " " + scene.path.split("/").pop();
    const deleters = (scene.deleted_by || []).join(", ") || "another device";
    const sides = [side("This device", scene.mine, "this device"),
      ...scene.theirs.map((version) => side(version.peers.join(", ") || "Another device", version, version.peers.join(", ") || "another device"))];
    if (scene.their_deletion) sides.push(side((scene.deleted_by || []).join(", ") || "Another device", { deleted: true }, deleters));
    // uncertain: the campaign cannot establish this scene's ancestry, or a version taken here cannot be reviewed
    const uncertain = Boolean(scene.uncertain) || scene.taking_theirs.some((review) => !review || review.uncertain)
      || Boolean(scene.their_deletion && (!scene.taking_deletion || scene.taking_deletion.uncertain));
    const why = scene.theirs.map((version, index) => el("li", {}, takingText(version.peers.join(", ") || "the other device", scene.taking_theirs[index], { uncertain })));
    if (scene.their_deletion) why.push(el("li", {}, takingText(deleters, scene.taking_deletion, { uncertain, deletion: true })));
    // what rests on it is recorded exactly, and a version taken would leave that recorded premise missing
    const reviews = [...scene.taking_theirs, ...(scene.their_deletion ? [scene.taking_deletion] : [])];
    const breaks = Boolean(scene.breaks_recorded) || reviews.some((review) => review && review.breaks_recorded);
    const article = el("article", { class: "g-stack forks-scene", "aria-label": "Divergent: " + title },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, title), ui.pill("Both kept", { kind: "accent" }),
        scene.detected.length ? ui.pill("Held for review", { kind: "bad" }) : null),
      el("div", { class: "forks-sides" }, sides),
      why.length ? el("ul", { class: "forks-lines" }, why) : null,
      uncertain ? el("p", {}, el("strong", {}, "Refused: "), "its ancestry is uncertain, so neither version can be shown to fit what rests on it.") : null,
      breaks ? el("p", {}, el("strong", {}, "Refused: "), "it would leave a recorded premise missing. What rests on this scene is on record, "
        + "and taking that version would leave it without what it rests on.") : null,
      scene.continues ? el("p", {}, el("strong", {}, "Refused: "), "a divergent continuation. An earlier scene of this chat differs too, and "
        + "this one may rest on it, so neither device's earlier scene can be restored over it.") : null,
      scene.keep_one === "refused"
        ? el("p", {}, el("strong", {}, "Keeping one side: refused. "), "049's semantic review holds this file (semantic_review): the ordinary "
          + "resolver would take one version over what rests on the other.")
        : el("p", {}, "Keeping one side is allowed for this file. ", ctx.registered("inbox") ? ui.link("Keep one in Needs you", "inbox") : null),
      scene.kind === "scene" && retcon.has(scene.path)
        ? ui.actions(ui.button("Preview a retcon of this scene", () => retcon.select(scene.path), { size: "small" })) : null);
    return article;
  }

  function retained(forks, device) {
    const head = el("h3", { class: "forks-sub" }, "Retained forks");
    if (!forks.length) return el("div", { class: "g-stack" }, head, ui.empty("No scene of this campaign was restored as a retained fork."));
    return el("div", { class: "g-stack" }, head, el("p", { class: "g-muted" }, "A retained fork keeps every device's exact original "
      + "of a scene it restored. The originals stay history: none of them becomes the story or anyone's memory."),
    ...forks.map((fork) => el("article", { class: "g-stack forks-scene", "aria-label": "Retained fork: " + fork.operation },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, "Restored from " + fork.source_device),
        ui.pill(FORK_STATES[forkState(fork)][0], { kind: fork.reconciled_here ? "good" : "" })),
      el("p", { class: "g-muted" }, forkState(fork) === "elsewhere"
        ? "This fork was made between " + (fork.devices || []).join(" and ") + "; this device keeps its record."
        : FORK_STATES[forkState(fork)][1]),
      ...fork.scenes.map((scene) => el("div", { class: "g-stack" },
        el("div", {}, el("strong", {}, "Scene " + (scene.number || "") + (scene.chat ? " · " + chatText(chatNames, scene.chat) : "")),
          el("span", { class: "g-muted" }, " · " + (scene.here === "restored" ? "restored here" : scene.here === "original" ? "still this device's original here" : "changed since"))),
        el("div", { class: "forks-sides" }, scene.variants.map((variant) => el("div", { class: "g-stack forks-side" },
          el("div", { class: "g-eyebrow" }, (variant.device === device ? "This device" : variant.device) + (variant.selected ? " · restored" : " · original kept")),
          el("p", { class: "forks-said" }, variant.said || "(no words)")))))))));
  }

  await load();
}

// -- reviewing a retcon or fork (054) -------------------------------------------------------------------------

async function showRetcon(slot, ctx, campaign, chatNames) {
  const { ui } = ctx;
  const { el } = ui;
  const node = card(ui, "Review a retcon or fork");
  slot.replaceWith(node);
  const body = el("div", { class: "g-stack" });
  node.append(el("p", { class: "g-muted" }, "Pick what you would change and how, and see everything the campaign's own records say "
    + "rests on it. A preview writes no story change, and no model is called."), body);
  let scenes = null;
  const picked = new Set();
  let action = "edit";
  let result = null;  // the preview drawn now: { request, answer }
  let review = null;  // the declaration under review: { args, plan }
  const previewing = latest();
  const reading = latest();
  const ALTERNATIVE = "forks:" + campaign + ":alternative";
  const alternative = ui.field("Your alternative, in your own words (optional)", { draft: ctx.draft(ALTERNATIVE), multiline: true,
    hint: "A rehearsal: it is not saved and becomes nothing in the forks." });
  let source = "", dependent = "", reason = "dialogue";

  const load = async () => {
    const turn = reading.next();
    const answer = await ctx.api("forks.scenes", { campaign, audience: "director" });
    if (!reading.current(turn)) return;
    // records read anew at another revision: a report or a declaration reviewed against the old ones is no longer
    // of what is shown, so neither stays drawn as current
    if (scenes && answer.etag !== scenes.etag) { previewing.bump(); result = null; review = null; }
    scenes = answer;
    for (const path of [...picked]) if (!scenes.records.some((item) => item.path === path)) picked.delete(path);
    draw();
  };
  const resultSlot = el("div", { class: "g-stack" }), declareSlot = el("div", { class: "g-stack" });
  // a choice changed: any answer still on its way is for what was chosen before, and is dropped (the form itself
  // is not drawn again, so the control the person is in keeps focus)
  const changed = () => { previewing.bump(); result = null; drawResult(); };

  const preview = () => ctx.act(async () => {
    const roots = scenes.records.filter((item) => picked.has(item.path)).map((item) => ({ path: item.path, revision: item.revision }));
    if (!roots.length) { ctx.notify("Pick at least one record to change.", { bad: true }); return false; }
    const request = { campaign, audience: "director", action, roots };
    if (alternative.value.trim()) request.alternative = alternative.value;
    const turn = previewing.next();
    try {
      const answer = await ctx.api("forks.preview", request);
      if (!previewing.current(turn)) return true;
      result = { request, answer };
      drawResult();
    } catch (error) {
      if (error && error.reason === "changed" && previewing.current(turn)) await load();
      throw error;
    }
    return true;
  });

  const plan = (args) => ctx.act(async () => {
    const full = { campaign, audience: "director", operation_id: newOperationId(), expected: scenes.etag, ...args };
    let answer;
    try {
      answer = await ctx.api("forks.declare_plan", full);
    } catch (error) {  // the records moved on since they were shown: read them again, so the next review is of now
      if (error && (error.reason === "changed" || error.reason === "missing_record")) await load();
      throw error;
    }
    review = { args: full, plan: answer.plan };
    scenes = answer.scenes;
    drawDeclare();
    return true;
  });
  const commit = () => ctx.act(async () => {
    try {
      const answer = await ctx.api("forks.declare_commit", review.args);
      review = null;
      scenes = answer.scenes;
      result = null;
      ctx.notify(answer.done.already_applied ? "That was already recorded; nothing was changed again." : "Recorded.");
      draw();
    } catch (error) {
      if (error && error.reason === "changed") {  // the campaign moved on after the review: review it again
        review = null;
        await load();
      }
      throw error;
    }
    return true;
  });

  function draw() {
    if (!scenes) return;
    const parts = [];
    if (scenes.problems.length) {
      parts.push(ui.notice("Some records cannot be read, so no report is complete: " + scenes.problems.join("; "), { kind: "bad" }));
    }
    if (!scenes.records.length) {
      parts.push(ui.empty("This campaign has no scene or accepted record yet."));
      body.replaceChildren(...parts);
      return;
    }
    parts.push(pickForm(), resultSlot, declareSlot, notOffered());
    body.replaceChildren(...parts);
    drawResult();
    drawDeclare();
  }

  function drawResult() { resultSlot.replaceChildren(...(result ? [resultBox()] : [])); }
  function drawDeclare() { declareSlot.replaceChildren(review ? reviewBox() : declareForm()); }

  function pickForm() {
    const order = ["scene", "accepted_event", "relationship", "holdings", "summary", "evidence"];
    const kinds = [...new Set(scenes.records.map((item) => item.kind))].sort((a, b) => rank(a) - rank(b));
    function rank(kind) { const at = order.indexOf(kind); return at < 0 ? order.length : at; }
    const lists = kinds.map((kind) => el("fieldset", { class: "forks-records" }, el("legend", {}, kindText(kind)),
      el("ul", { class: "forks-lines" }, scenes.records.filter((item) => item.kind === kind).map((item) => {
        const box = el("input", { type: "checkbox", value: item.path, "data-focus-key": "forks:root:" + item.path });
        box.checked = picked.has(item.path);
        box.addEventListener("change", () => { if (box.checked) picked.add(item.path); else picked.delete(item.path); changed(); });
        return el("li", {}, el("label", { class: "g-row forks-pick" }, box,
          el("span", { class: "g-grow forks-said" }, recordText(item)),
          item.chat && item.kind === "scene" ? el("span", { class: "g-muted" }, chatText(chatNames, item.chat)) : null));
      }))));
    const how = ui.choice("How", scenes.actions.map((value) => ({ value, label: actionText(value) })),
      { value: action, onchange: (value) => { action = value; changed(); } });
    return group(ui, "What would change", el("h3", { class: "forks-sub" }, "What would change"), ...lists, how.node,
      alternative.node, ui.actions(ui.button("Preview", preview, { kind: "primary" })));
  }

  function resultBox() {
    const { answer } = result;
    const impacts = answer.impacts.map((item) => el("li", { class: "g-stack forks-impact" },
      el("div", { class: "g-row" }, el("span", { class: "g-grow forks-said" }, recordText(item.target)),
        ui.pill(certaintyText(item.certainty), { kind: item.certainty === "known" ? "bad" : "" })),
      el("div", { class: "g-muted" }, "Because " + reasonText(item.reason) + (item.chain.length > 2 ? " (by way of " + count(item.chain.length - 2, "record") + ")" : "") + ".")));
    const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "forks-sub" }, "What " + actionText(answer.action).toLowerCase() + " would touch"),
      answer.complete ? null : ui.notice("This report is incomplete, so do not rely on it: " + answer.problems.join("; "), { kind: "bad" }),
      impacts.length ? el("ul", { class: "forks-lines" }, impacts) : ui.empty("Nothing the campaign records rests on it."),
      answer.rehearsal ? el("p", { class: "g-muted" }, "A rehearsal: your alternative is " + answer.rehearsal.status + ", not saved, and no model was called.") : null,
      answer.corrections ? el("details", { class: "forks-details" }, el("summary", {}, "What a memory correction would cite (053): " + count(answer.corrections.length, "reference")),
        el("ul", { class: "forks-lines" }, answer.corrections.map((item) => el("li", {},
          recordText(scenes.records.find((record) => record.path === item.dependent)) + " ← " + recordText(scenes.records.find((record) => record.path === item.source))
          + " · " + certaintyText(item.certainty))))) : el("p", { class: "g-muted" }, answer.corrections_refused || ""),
      el("p", { class: "g-muted" }, "No story change was made: the scene, its accepted effects and what follows stay as they are."));
    box.setAttribute("role", "region");
    box.setAttribute("aria-label", "What it would touch");
    return box;
  }

  function declareForm() {
    const options = [{ value: "", label: "Pick a record" }, ...scenes.records.map((item) => ({ value: item.path, label: clip(recordText(item), 70) }))];
    const from = ui.choice("This record", options, { value: source, onchange: (value) => { source = value; } });
    const to = ui.choice("rests on it (the record that depends)", options, { value: dependent, onchange: (value) => { dependent = value; } });
    const why = ui.choice("Because of", scenes.declarable.map((value) => ({ value, label: declaredText(value) })), { value: reason, onchange: (value) => { reason = value; } });
    const status = el("div", { class: "g-field-error", role: "alert", hidden: true });
    const go = () => {
      const request = declaration(scenes.records, source, dependent, reason);
      status.hidden = Boolean(request);
      status.textContent = request ? "" : "Pick two different records.";
      if (request) plan(request);
    };
    return group(ui, "Record what rests on what", el("h3", { class: "forks-sub" }, "Record what rests on what"),
      el("p", { class: "g-muted" }, "The campaign records which scene an accepted event rests on, but not which later line of dialogue "
        + "uses it. Say so here, and a retcon of the first will flag the second as known."),
      from.node, to.node, why.node, status, ui.actions(ui.button("Review", go)));
  }

  function reviewBox() {
    const plan = review.plan;
    const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "forks-sub" }, "Review this change"),
      el("p", {}, declarationText(plan)),
      plan.reaches.length ? el("p", {}, "A retcon of the first would then flag: " + plan.reaches.map((item) => recordText(item.target) + " (" + certaintyText(item.certainty).toLowerCase() + ")").join("; ") + ".") : null,
      el("p", { class: "g-muted" }, "It writes " + count(plan.files.length, "file") + ": " + plan.files.join(", ") + ". Once written it is never rewritten."),
      ui.actions(ui.button("Cancel", () => { review = null; drawDeclare(); }), ui.button("Make this change", commit, { kind: "primary" })));
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Review this change");
    return box;
  }

  function notOffered() {
    return el("div", { class: "g-stack" }, el("h3", { class: "forks-sub" }, "Not offered here"),
      el("ul", { class: "forks-lines" }, scenes.unavailable.slice(2).map((item) => el("li", {}, ui.unavailable(item.what, item.why)))));
  }

  try {
    await load();
  } catch (error) {  // the review cannot be read now: say why, and the divergent scenes are still shown
    if (error && error.reason === "left") throw error;
    body.replaceChildren(ui.errorBox(error), ui.actions(ui.button("Read again", () => ctx.act(async () => { await load(); return true; }))));
  }
  return {
    has: (path) => Boolean(scenes && scenes.records.some((item) => item.path === path)),
    select(path) {
      picked.clear();
      picked.add(path);
      action = "edit";
      previewing.bump();
      result = null;
      draw();  // the boxes and the choice show what is now picked
      preview();
      const region = node.querySelector('[aria-label="What would change"]');
      if (region && region.scrollIntoView) region.scrollIntoView();
    },
  };
}
