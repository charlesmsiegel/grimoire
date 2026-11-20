// Current actor context, the director's device-local tactics (112) and a draft's likely context (085).
// Nothing here generates or stores a prompt snapshot. All authored content is text, never HTML.
const WHY = { lane_cap: "its lane is full", over_budget: "the context budget is full" };

export function contextPanel(ctx, onSaved) {
  const { ui } = ctx, { el } = ui;
  let chat = null, actor = null, serial = 0;
  const body = el("div", { class: "g-stack" });
  const previewBody = el("div", { class: "g-stack" });
  const draft = ui.field("Draft to preview", { multiline: true,
    hint: "Read as if your character posted it now. It is not posted, kept or sent to a model." });
  // A group, not a form: nothing here is saved, so the page's only forms stay the ones that save settings.
  const preview = el("div", { class: "g-stack", role: "group", hidden: true, "aria-label": "Draft preview" },
    el("h3", {}, "Preview a draft"), draft.node,
    el("div", { class: "g-row" }, ui.button("Preview with draft", () => runPreview())), previewBody);
  const picker = el("select", { class: "g-input", "aria-label": "NPC context",
    onchange: () => { actor = picker.value || null; refresh(); } });
  const node = el("section", { class: "g-card providers-card g-stack", "aria-label": "Current context" },
    el("h2", { class: "g-heading" }, "Current context"),
    el("p", { class: "g-muted" }, "Rebuilt for one NPC from the saved chat now. This is not a historical prompt. No model call or library write is made."),
    el("div", { class: "g-row" }, picker, ui.button("Refresh context", () => refresh(), { size: "small" })), body, preview);

  function clear(message) {
    body.replaceChildren(el("p", { class: "g-muted", role: "status" }, message));
    previewBody.replaceChildren();
    if (!actor) preview.hidden = true;
  }
  function itemLine(item, extra) {
    return el("li", {}, el("div", {}, item.line),
      el("small", { class: "g-muted" }, [item.lane + " · " + item.tokens + " tokens", extra].filter(Boolean).join(" · ")));
  }
  function recall(item) {
    const why = item.why === "triggered" ? "your draft triggers this lore" : "your draft shares " + item.words.join(", ");
    const where = item.included ? "in context" : "left out: " + (WHY[item.reason] || item.reason);
    return itemLine(item, why + " · " + where);
  }
  function drawPreview(result) {
    const view = result.context, changes = result.changes;
    const shed = result.recalls.filter(item => !item.included);
    const parts = [
      el("p", { role: "status" }, "Estimate: built as if this draft were posted now. The turn's own prompt is rebuilt when it is generated, so anything that changes first changes it. Nothing was posted, saved or sent."),
    ];
    if (!result.draft.heard) {
      parts.push(el("p", {}, "This NPC would not hear this draft, so its context stays as saved."));
    } else {
      parts.push(el("p", {}, `${result.tokens.with_draft} / ${view.budget.max_tokens} context tokens with the draft, ${result.tokens.now} now (${view.budget.measurement}).`));
      if (shed.length) parts.push(el("p", { role: "alert" }, `Your draft points at ${shed.length === 1 ? "something" : shed.length + " things"} this NPC will not get: ${shed.length === 1 ? "it is" : "they are"} left out by the budget.`));
      parts.push(el("h4", {}, "Likely recalled"),
        result.recalls.length ? el("ul", {}, result.recalls.map(recall)) : el("p", {}, "Nothing in this NPC's memory or lore matches the draft."));
      if (result.more_recalls) parts.push(el("p", { class: "g-muted" }, `and ${result.more_recalls} more.`));
      parts.push(el("details", {}, el("summary", {}, "Changes from the current context"),
        changes.added.length || changes.removed.length
          ? el("ul", {}, ...changes.added.map(item => itemLine(item, "added")),
            ...changes.removed.map(item => itemLine(item, "left out" + (item.reason ? ": " + (WHY[item.reason] || item.reason) : ""))))
          : el("p", {}, "The draft changes nothing packed.")));
    }
    parts.push(el("details", {}, el("summary", {}, result.draft.heard ? "Estimated brief with the draft" : "Current generation brief"),
      el("pre", { class: "providers-context-text" }, view.system),
      el("pre", { class: "providers-context-text" }, view.heard)));
    previewBody.replaceChildren(...parts);
  }
  async function runPreview() {
    const asked = ++serial, selectedChat = chat, selected = actor, text = draft.value;
    draft.setError(null);
    if (!text.trim()) { draft.setError("Write a draft to preview."); return; }
    previewBody.replaceChildren(el("p", { class: "g-muted", role: "status" }, "Building the preview…"));
    try {
      const result = await ctx.api("providers.preview_context", { chat: selectedChat, actor: selected, draft: text });
      if (asked !== serial) return;
      drawPreview(result);
    } catch (error) {
      if (asked !== serial || error.reason === "left") return;
      previewBody.replaceChildren(el("p", { role: "alert" }, "The draft could not be previewed: " + error.message));
    }
  }
  function provenance(value) {
    return Object.entries(value || {}).map(([key, item]) => key + ": " + (Array.isArray(item) ? item.join(", ") : item)).join(" · ");
  }
  function draw(result) {
    const view = result.context;
    preview.hidden = !view;
    if (!view) { clear("Choose an NPC to inspect its current context."); return; }
    const budget = view.budget;
    const items = (view.items || []).map(item => el("li", {},
      el("div", {}, item.line), el("small", { class: "g-muted" }, `${item.lane} · ${item.tokens} tokens · ${provenance(item.provenance)}`)));
    const omitted = (view.omitted || []).map(item => el("li", {},
      `${item.lane}: ${item.reason} · ${item.tokens} tokens · ${provenance(item.provenance)}`));
    body.replaceChildren(
      el("p", {}, result.routes.length ? "Current model routes: " + result.routes.join(", ") : "No model configured; inspection still works."),
      el("p", {}, `${budget.tokens} / ${budget.max_tokens} context tokens (${budget.measurement}); provider usage is counted separately.`),
      el("details", { open: true }, el("summary", {}, "Current generation brief"),
        el("pre", { class: "providers-context-text" }, view.system),
        el("pre", { class: "providers-context-text" }, view.heard)),
      el("details", {}, el("summary", {}, "Selected context and provenance"), el("ul", {}, items)),
      el("details", {}, el("summary", {}, "Budget omissions"), omitted.length ? el("ul", {}, omitted) : el("p", {}, "No authorized context omitted by this budget.")),
      tactics(result));
  }
  function tactics(result) {
    const agenda = result.agenda;
    if (!agenda || !agenda.editable) return el("p", { class: "g-muted" }, agenda ? agenda.reason : "No tactics available.");
    const goal = ui.field("Goal", { value: agenda.aim, required: true });
    const actions = ui.field("Candidate actions", { value: agenda.actions.join("\n"), multiline: true, required: true,
      hint: "One action per line, at most eight. The first permitted action is the deterministic default." });
    const message = el("p", { role: "status" });
    const selected = actor, selectedChat = chat, version = serial;
    async function save(remove) {
      const request = { chat: selectedChat, actor: selected, expected: result.etag, remove };
      if (!remove) { request.aim = goal.value.trim(); request.actions = actions.value.split("\n").map(s => s.trim()).filter(Boolean); }
      try {
        const saved = await ctx.api("providers.save_agenda", request);
        if (version !== serial) return;
        onSaved(saved);
        await refresh();
      } catch (error) {
        if (version !== serial || error.reason === "left") return;
        message.textContent = error.message;
      }
    }
    return el("form", { class: "g-stack", onsubmit: event => { event.preventDefault(); save(false); } },
      el("h3", {}, "NPC tactics"),
      el("p", { class: "g-muted" }, "Director-authored settings for this NPC on this device. Optional decision calls may choose among permitted actions; narration rerolls keep the recorded choice. Changing or removing tactics for a pending decision can require a new turn."),
      goal.node, actions.node,
      result.writable ? null : el("p", {}, "Save model settings to enable tactics editing."),
      el("div", { class: "g-row" },
        el("button", { type: "submit", class: "g-button", disabled: !result.writable }, "Save tactics"),
        agenda.actions.length ? ui.button("Remove tactics", () => save(true), { disabled: !result.writable }) : null), message);
  }
  async function refresh() {
    const asked = ++serial, selectedChat = chat, selected = actor;
    clear(chat ? "Reading current context…" : "Choose a chat above to inspect its NPC context.");
    if (!chat) { picker.replaceChildren(el("option", { value: "" }, "Choose an NPC")); return; }
    try {
      const result = await ctx.api("providers.context", { chat: selectedChat, ...(selected ? { actor: selected } : {}) });
      if (asked !== serial) return;
      picker.replaceChildren(el("option", { value: "" }, "Choose an NPC"),
        ...result.actors.map(entry => el("option", { value: entry.actor, selected: entry.actor === selected }, entry.name)));
      draw(result);
    } catch (error) {
      if (asked !== serial || error.reason === "left") return;
      clear("Current context could not be read: " + error.message);
    }
  }
  return { node, refresh, changeChat(value) { chat = value; actor = null; picker.replaceChildren(el("option", { value: "" }, "Choose an NPC")); refresh(); } };
}
