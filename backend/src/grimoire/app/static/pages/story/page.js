// A campaign's story continuity (Sprint 115): the scenes two devices changed apart offline (049) and the review of
// a fork or retcon (054), as the director sees them (docs/STORY.md). Addresses:
//   #/story/<campaign id>                         the table's view: this is the director's, and nothing is offered
//   #/story/<campaign id>?audience=director       the director's view
//   ...&chat=<ChatRef>                            the chat the campaign hub and the chronicle were opened for
// Everything is read from, and written through, the story.* ops. Both versions of a divergent scene stay kept
// until the person resolves it: this page resolves nothing itself, says why keeping one side is refused where it
// is, and links to Devices and sync and to Needs you, which keep one side where that is allowed. A preview
// writes nothing. The one write, recording what rests on what, is reviewed first (story.declare_plan) and made as
// reviewed (story.declare_commit, with the same operation id and the snapshot revision as reviewed).

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
  ctx.setTitle("Story");
  ctx.setCrumb(ui.link("Campaigns", "campaign"), " / ", ui.link(name, "campaign", [campaign], chat ? { chat } : undefined), " / Story");
  const query = (view) => ({ chat: chat || undefined, audience: view === "director" ? "director" : undefined });
  const views = ui.segments("Whose view", AUDIENCES, { value: audience, onchange: (value) => ctx.navigate(ctx.href("story", [campaign], query(value))) });
  root.append(el("div", { class: "g-row story-views" }, views.node, el("span", { class: "g-grow g-muted" },
    audience === "director" ? "Scenes your devices changed apart, and what a retcon would touch." : "A story's continuity is the director's."),
  ctx.registered("campaign") ? ui.buttonLink("Campaign hub", "campaign", [campaign], query(audience)) : null,
  ctx.registered("chronicle") ? ui.buttonLink("Chronicle", "chronicle", [campaign], query(audience)) : null));

  if (audience !== "director") {
    root.append(card(ui, "Story continuity", el("p", { class: "g-muted" },
      "Scenes two devices changed apart offline, and what a retcon or fork would touch, are the director's: they show the exact "
      + "versions every device kept. Switch to the director view to review them.")));
    return;
  }
  const cards = el("div", { class: "story-cards" });
  root.append(cards);
  const divergentSlot = el("div", {}), retconSlot = el("div", {});
  cards.append(divergentSlot, retconSlot);
  const retcon = await showRetcon(retconSlot, ctx, campaign, chatNames);
  await showDivergent(divergentSlot, ctx, campaign, chatNames, retcon);
}

function card(ui, title, ...children) {
  const node = ui.card({ tag: "section", label: title }, ui.el("h2", { class: "g-heading" }, title), ...children);
  node.classList.add("story-card");
  return node;
}

function group(ui, label, ...children) {
  return ui.el("div", { class: "g-stack story-group", role: "group", "aria-label": label }, ...children);
}

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
      const answer = await ctx.api("story.divergent", { campaign, audience: "director" });
      if (reading.current(turn)) draw(answer);
    } catch (error) {
      if (error && error.reason === "left") return;
      if (reading.current(turn)) body.replaceChildren(ui.errorBox(error));
    }
  };

  function draw(answer) {
    const parts = [];
    if (answer.peers.length) parts.push(el("ul", { class: "story-lines" }, answer.peers.map((peer) => el("li", {}, syncText(peer)))));
    else parts.push(el("p", { class: "g-muted" }, "No sync with another device since the app started: what earlier syncs kept is listed below."));
    if (answer.unknown) parts.push(ui.notice("Some of this campaign's sync records cannot be read here, so any of its files may be in conflict.", { kind: "bad" }));
    if (!answer.scenes.length) parts.push(ui.empty("No scene of this campaign is kept in two versions on this device."));
    for (const scene of answer.scenes) parts.push(divergentScene(scene));
    if (answer.held.length) {
      parts.push(el("div", { class: "g-stack" }, el("h3", { class: "story-sub" }, "Held with them"),
        el("p", { class: "g-muted" }, "The last sync did not bring these across either, so no accepted effect is applied twice or lost: "
          + answer.held.map((item) => kindText(item.kind).toLowerCase() + " " + item.path.split("/").pop().replace(/\.json$/, "")).join(", ") + ".")));
    }
    if (answer.fork_choices.length) {
      parts.push(el("p", {}, count(answer.fork_choices.length, "chat") + " of this campaign wait on a choice of which branch goes on. ",
        ctx.registered("inbox") ? ui.link("Choose in Needs you", "inbox") : "Needs you is not in this app yet."));
    }
    parts.push(retained(answer.retained, answer.device));
    for (const problem of answer.problems) parts.push(ui.notice(problem.path + ": " + problem.code, { kind: "bad" }));
    parts.push(el("div", { class: "g-stack" }, el("h3", { class: "story-sub" }, "Not offered here"),
      el("ul", { class: "story-lines" }, answer.unavailable.map((item) => el("li", {}, ui.unavailable(item.what, item.why))))));
    parts.push(ui.actions(
      ctx.registered("sync") ? ui.buttonLink("Devices and sync", "sync") : null,
      ui.button("Read again", () => ctx.act(async () => { await load(); return true; }))));
    body.replaceChildren(...parts);
  }

  function side(title, candidate) {
    const said = candidate.deleted ? "Deleted on this device." : candidate.set_aside ? "Set aside by a fork choice: heard by no one."
      : candidate.unreadable ? "This version cannot be read here." : candidate.said || "(no words)";
    return el("div", { class: "g-stack story-side" }, el("div", { class: "g-eyebrow" }, title),
      el("p", { class: "story-said" }, said));
  }

  function divergentScene(scene) {
    const title = scene.kind === "scene" ? "Scene " + scene.number + (scene.chat ? " · " + chatText(chatNames, scene.chat) : "")
      : kindText(scene.kind) + " " + scene.path.split("/").pop();
    const sides = [side("This device", scene.mine), ...scene.theirs.map((version) => side(version.peers.join(", ") || "Another device", version))];
    if (scene.their_deletion) sides.push(side(scene.peers.join(", ") || "Another device", { deleted: true }));
    const why = scene.theirs.map((version, index) => el("li", {}, takingText(version.peers.join(", ") || "the other device", scene.taking_theirs[index])));
    const uncertain = scene.taking_theirs.some((review) => review && review.uncertain);
    const article = el("article", { class: "g-stack story-scene", "aria-label": "Divergent: " + title },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, title), ui.pill("Both kept", { kind: "accent" }),
        scene.detected.length ? ui.pill("Held for review", { kind: "bad" }) : null),
      el("div", { class: "story-sides" }, sides),
      why.length ? el("ul", { class: "story-lines" }, why) : null,
      uncertain ? el("p", {}, el("strong", {}, "Refused: "), "its ancestry is uncertain, so neither version can be shown to fit what rests on it.") : null,
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
    const head = el("h3", { class: "story-sub" }, "Retained forks");
    if (!forks.length) return el("div", { class: "g-stack" }, head, ui.empty("No scene of this campaign was restored as a retained fork."));
    return el("div", { class: "g-stack" }, head, el("p", { class: "g-muted" }, "A retained fork keeps every device's exact original "
      + "of a scene it restored. The originals stay history: none of them becomes the story or anyone's memory."),
    ...forks.map((fork) => el("article", { class: "g-stack story-scene", "aria-label": "Retained fork: " + fork.operation },
      el("div", { class: "g-row" }, el("strong", { class: "g-grow" }, "Restored from " + fork.source_device),
        ui.pill(fork.reconciled_here ? "Reconciled here" : "Waiting on sync", { kind: fork.reconciled_here ? "good" : "" })),
      el("p", { class: "g-muted" }, fork.reconciled_here
        ? "This device holds the restored scenes and the accepted effects ordinary sync brought across, each once."
        : "Ordinary sync still has to bring the accepted effects across; it applies each once."),
      ...fork.scenes.map((scene) => el("div", { class: "g-stack" },
        el("div", {}, el("strong", {}, "Scene " + (scene.number || "") + (scene.chat ? " · " + chatText(chatNames, scene.chat) : "")),
          el("span", { class: "g-muted" }, " · " + (scene.here === "restored" ? "restored here" : scene.here === "original" ? "still this device's original here" : "changed since"))),
        el("div", { class: "story-sides" }, scene.variants.map((variant) => el("div", { class: "g-stack story-side" },
          el("div", { class: "g-eyebrow" }, (variant.device === device ? "This device" : variant.device) + (variant.selected ? " · restored" : " · original kept")),
          el("p", { class: "story-said" }, variant.said || "(no words)")))))))));
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
    + "rests on it. A preview writes nothing, and no model is called."), body);
  let scenes = null;
  const picked = new Set();
  let action = "edit";
  let result = null;  // the preview drawn now: { request, answer }
  let review = null;  // the declaration under review: { args, plan }
  const previewing = latest();
  const reading = latest();
  const ALTERNATIVE = "story:" + campaign + ":alternative";
  const alternative = ui.field("Your alternative, in your own words (optional)", { draft: ctx.draft(ALTERNATIVE), multiline: true,
    hint: "A rehearsal: it is not saved and becomes nothing in the story." });
  let source = "", dependent = "", reason = "dialogue";

  const load = async () => {
    const turn = reading.next();
    const answer = await ctx.api("story.scenes", { campaign, audience: "director" });
    if (!reading.current(turn)) return;
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
      const answer = await ctx.api("story.preview", request);
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
    const answer = await ctx.api("story.declare_plan", full);
    review = { args: full, plan: answer.plan };
    scenes = answer.scenes;
    drawDeclare();
    return true;
  });
  const commit = () => ctx.act(async () => {
    try {
      const answer = await ctx.api("story.declare_commit", review.args);
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
    const lists = kinds.map((kind) => el("fieldset", { class: "story-records" }, el("legend", {}, kindText(kind)),
      el("ul", { class: "story-lines" }, scenes.records.filter((item) => item.kind === kind).map((item) => {
        const box = el("input", { type: "checkbox", value: item.path, "data-focus-key": "story:root:" + item.path });
        box.checked = picked.has(item.path);
        box.addEventListener("change", () => { if (box.checked) picked.add(item.path); else picked.delete(item.path); changed(); });
        return el("li", {}, el("label", { class: "g-row story-pick" }, box,
          el("span", { class: "g-grow story-said" }, recordText(item)),
          item.chat && item.kind === "scene" ? el("span", { class: "g-muted" }, chatText(chatNames, item.chat)) : null));
      }))));
    const how = ui.choice("How", scenes.actions.map((value) => ({ value, label: actionText(value) })),
      { value: action, onchange: (value) => { action = value; changed(); } });
    return group(ui, "What would change", el("h3", { class: "story-sub" }, "What would change"), ...lists, how.node,
      alternative.node, ui.actions(ui.button("Preview", preview, { kind: "primary" })));
  }

  function resultBox() {
    const { answer } = result;
    const impacts = answer.impacts.map((item) => el("li", { class: "g-stack story-impact" },
      el("div", { class: "g-row" }, el("span", { class: "g-grow story-said" }, recordText(item.target)),
        ui.pill(certaintyText(item.certainty), { kind: item.certainty === "known" ? "bad" : "" })),
      el("div", { class: "g-muted" }, "Because " + reasonText(item.reason) + (item.chain.length > 2 ? " (by way of " + count(item.chain.length - 2, "record") + ")" : "") + ".")));
    const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "story-sub" }, "What " + actionText(answer.action).toLowerCase() + " would touch"),
      answer.complete ? null : ui.notice("This report is incomplete, so do not rely on it: " + answer.problems.join("; "), { kind: "bad" }),
      impacts.length ? el("ul", { class: "story-lines" }, impacts) : ui.empty("Nothing the campaign records rests on it."),
      answer.rehearsal ? el("p", { class: "g-muted" }, "A rehearsal: your alternative is " + answer.rehearsal.status + ", not saved, and no model was called.") : null,
      answer.corrections ? el("details", { class: "story-details" }, el("summary", {}, "What a memory correction would cite (053): " + count(answer.corrections.length, "reference")),
        el("ul", { class: "story-lines" }, answer.corrections.map((item) => el("li", {},
          recordText(scenes.records.find((record) => record.path === item.dependent)) + " ← " + recordText(scenes.records.find((record) => record.path === item.source))
          + " · " + certaintyText(item.certainty))))) : el("p", { class: "g-muted" }, answer.corrections_refused || ""),
      el("p", { class: "g-muted" }, "Nothing was changed: the scene, its accepted effects and what follows stay as they are."));
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
    return group(ui, "Record what rests on what", el("h3", { class: "story-sub" }, "Record what rests on what"),
      el("p", { class: "g-muted" }, "The campaign records which scene an accepted event rests on, but not which later line of dialogue "
        + "uses it. Say so here, and a retcon of the first will flag the second as known."),
      from.node, to.node, why.node, status, ui.actions(ui.button("Review", go)));
  }

  function reviewBox() {
    const plan = review.plan;
    const box = ui.card({ kind: "accent", tag: "div" }, el("h3", { class: "story-sub" }, "Review this change"),
      el("p", {}, declarationText(plan)),
      plan.reaches.length ? el("p", {}, "A retcon of the first would then flag: " + plan.reaches.map((item) => recordText(item.target) + " (" + certaintyText(item.certainty).toLowerCase() + ")").join("; ") + ".") : null,
      el("p", { class: "g-muted" }, "It writes " + count(plan.files.length, "file") + ": " + plan.files.join(", ") + ". Once written it is never rewritten."),
      ui.actions(ui.button("Cancel", () => { review = null; drawDeclare(); }), ui.button("Make this change", commit, { kind: "primary" })));
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Review this change");
    return box;
  }

  function notOffered() {
    return el("div", { class: "g-stack" }, el("h3", { class: "story-sub" }, "Not offered here"),
      el("ul", { class: "story-lines" }, scenes.unavailable.slice(2).map((item) => el("li", {}, ui.unavailable(item.what, item.why)))));
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
