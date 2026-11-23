// 116 (sprint 085's draft preview): Preview context, beside the composer. Before posting, the person picks an NPC on
// stage and asks what that NPC's context would likely hold if the composer's unsent draft were posted now:
// providers.preview_context reads the draft as post would (trimmed, in the composer's mode), packs the NPC's context
// twice (without and with it) and answers the lore the draft triggers (exact), the memories it likely recalls (an
// estimate) and any recall the budget would shed (a warning). Nothing is posted, kept, written or sent to a model,
// and the composer is never changed: its text and mode are read when Preview is pressed, and only then. An answer
// that comes back for an earlier draft, mode or NPC is dropped.
//
// An NPC's context (what it remembers, the lore it is given) is the director's. The op acts as the library owner,
// as the providers page does; on this screen, which the table may be looking at, the preview opens only in the
// director's view, chosen here each time the chat is opened (never remembered, never opened by a link). The full
// context and the NPC's tactics are on the Models and costs page. Everything shown from the library is set as text.

const WHY = { lane_cap: "its lane is full", over_budget: "the context budget is full" };

// Who can be previewed: the chat's NPCs on stage, in cast order (the op refuses anyone else `not_npc`).
export function npcsOf(scene) {
  return scene.cast.filter((member) => member.role === "npc").map((member) => ({ actor: member.actor, name: member.name }));
}

// The preview's recalls, sorted for the panel: what the budget sheds (warnings), triggered lore, recalled memory.
export function sortRecalls(result) {
  const recalls = result.recalls || [];
  return {
    shed: recalls.filter((item) => !item.included),
    lore: recalls.filter((item) => item.included && item.lane === "lore" && item.why === "triggered"),
    memory: recalls.filter((item) => item.included && item.lane === "memory"),
    other: recalls.filter((item) => item.included && !(item.lane === "lore" && item.why === "triggered") && item.lane !== "memory"),
  };
}

export function recallWhy(item) {
  const why = item.why === "triggered" ? "your draft triggers this lore (exact)"
    : "your draft shares " + (item.words || []).join(", ") + " (an estimate)";
  return why + (item.included ? "" : "; left out: " + (WHY[item.reason] || item.reason));
}

// Whether an answer still belongs to what is on screen: the same request, NPC, draft and mode as when it was asked.
// The draft is compared as post sends it (trimmed): spaces typed at either end change nothing post would read.
export function current(asked, now) {
  return asked.serial === now.serial && asked.actor === now.actor && asked.draft === now.draft
    && (asked.mode || "auto") === (now.mode || "auto");
}

// makePreview(ctx, chat, { composer, mode }) -> { node, update(scene) }, or { node: null } where the app does not
// serve providers.preview_context. `composer` is the chat page's composer field: read, never written. `mode()` is
// the composer's mode as Post sends it (auto where the composer has no modes), as for makeImagine.
export function makePreview(ctx, chat, { composer, mode = () => "auto" }) {
  if (!ctx.has("providers.preview_context")) return { node: null, update() {} };
  ctx.styles("preview.css");
  const { ui } = ctx;
  const { el } = ui;
  const alive = () => !(ctx.signal && ctx.signal.aborted);
  // Focus moves only from inside this panel (or from nowhere): a person typing in the composer keeps their place.
  const ours = () => { const at = document.activeElement; return !at || at === document.body || node.contains(at); };
  let scene = null, shown = "", serial = 0, director = false, actor = "", asking = false;

  const who = el("div", { class: "chat-preview-who" });
  const status = el("p", { class: "chat-preview-status", role: "status", "aria-live": "polite", tabindex: "-1" });
  const result = el("div", { class: "chat-preview-result" });
  const go = ui.button("Preview context", () => preview(), { kind: "primary" });
  const views = ui.segments("Whose view", [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }],
    { value: "table", onchange: (value) => { director = value === "director"; drop(); draw(); } });
  const tableNote = el("p", { class: "g-muted chat-preview-note" },
    "What an NPC would have in mind (its memories and the lore it is given) is the director's. Switch to the director view to preview it.");
  const directorBox = el("div", { class: "chat-preview-director", role: "group", "aria-label": "Preview an NPC's context" },
    el("p", { class: "g-muted chat-preview-note" },
      "Reads your unsent draft as if it were posted now, in the composer's mode. It is not posted, kept or sent to a model, and your draft stays as it is."),
    who, el("div", { class: "g-actions" }, go,
      ctx.registered("providers") ? ui.buttonLink("Full context", "providers", [], { chat }, { size: "small" }) : null),
    status, result);
  const node = el("details", { class: "chat-preview" }, el("summary", {}, "Preview context"),
    el("div", { class: "chat-preview-body" }, views.node, tableNote, directorBox));

  // What is on screen now, as post would read it.
  const now = () => ({ serial, actor, draft: composer.value.trim(), mode: mode() });
  // What was shown or asked belongs to an NPC, a view or a draft that is gone: an answer on its way is dropped.
  function drop() { serial += 1; asking = false; result.replaceChildren(); status.textContent = ""; }

  async function preview() {
    if (asking || !director) return;
    const draft = composer.value.trim();  // read, never written: the composer keeps its text whatever happens here
    if (!draft) { status.textContent = "Write a draft in the composer first: the preview reads it."; return; }
    if (!actor) { status.textContent = "Choose an NPC."; return; }
    const asked = { serial: ++serial, actor, draft, mode: mode() };
    asking = true;
    status.textContent = "Building the preview…";
    result.replaceChildren();
    draw();
    try {
      const answer = await ctx.api("providers.preview_context", { chat, actor: asked.actor, draft, mode: asked.mode });
      if (!alive()) return;
      if (!current(asked, now())) {
        if (asked.serial === serial) status.textContent = "The draft, its mode or the NPC changed while the preview was built, so it was dropped. Preview again.";
        return;
      }
      drawResult(answer);
      status.textContent = "An estimate for " + nameOf(asked.actor) + ". Nothing was posted, saved or sent to a model.";
      if (ours()) status.focus();
    } catch (error) {
      if (!alive() || error.reason === "left" || asked.serial !== serial) return;
      status.textContent = "The draft could not be previewed: " + ui.errorText(error);
      if (ours()) status.focus();
    } finally {
      if (asked.serial === serial) asking = false;
      if (alive()) draw();
    }
  }

  function nameOf(ref) {
    const found = scene ? npcsOf(scene).find((item) => item.actor === ref) : null;
    return found ? found.name : "this NPC";
  }

  function line(item, extra) {
    return el("li", {}, el("div", { class: "chat-preview-line" }, item.line),
      el("small", { class: "g-muted" }, [item.lane + " · " + item.tokens + " tokens", extra].filter(Boolean).join(" · ")));
  }

  function drawResult(answer) {
    const parts = [el("p", { class: "g-muted" },
      "Estimate: built as if this draft were posted now. The turn's own context is rebuilt when it is answered, so anything that changes first changes it. Token counts are the pack's estimate, not a provider's.")];
    if (!answer.draft.heard) {
      parts.push(el("p", {}, nameOf(answer.actor) + " would not hear this draft, so its context stays as saved."));
    } else {
      const sorted = sortRecalls(answer);
      if (answer.shed_recalls) {
        parts.push(el("div", { class: "g-notice g-bad", role: "alert" },
          "Your draft points at " + (answer.shed_recalls === 1 ? "something" : answer.shed_recalls + " things") + " "
          + nameOf(answer.actor) + " will not get: left out by the budget."),
        el("ul", { class: "chat-preview-list", "aria-label": "Left out by the budget" }, sorted.shed.map((item) => line(item, recallWhy(item)))));
      }
      parts.push(el("h3", { class: "chat-preview-heading" }, "Lore the draft triggers"),
        sorted.lore.length ? el("ul", { class: "chat-preview-list", "aria-label": "Lore the draft triggers" }, sorted.lore.map((item) => line(item, recallWhy(item))))
          : el("p", { class: "g-muted" }, "None: no lore entry is triggered by this draft that was not already."));
      parts.push(el("h3", { class: "chat-preview-heading" }, "Memories it likely recalls"),
        sorted.memory.length ? el("ul", { class: "chat-preview-list", "aria-label": "Memories it likely recalls" }, sorted.memory.map((item) => line(item, recallWhy(item))))
          : el("p", { class: "g-muted" }, "None of its memories shares the draft's words."));
      if (sorted.other.length) parts.push(el("ul", { class: "chat-preview-list" }, sorted.other.map((item) => line(item, recallWhy(item)))));
      if (answer.more_recalls) parts.push(el("p", { class: "g-muted" }, "and " + answer.more_recalls + " more."));
      parts.push(el("p", { class: "g-muted" }, answer.tokens.with_draft + " context tokens with the draft, " + answer.tokens.now + " now."));
    }
    result.replaceChildren(...parts);
  }

  function draw() {
    tableNote.hidden = director;
    directorBox.hidden = !director;
    go.disabled = asking;
    if (!scene) return;
    const people = npcsOf(scene);
    if (!people.some((person) => person.actor === actor)) {  // the chosen NPC left the scene: what was shown was theirs
      if (actor) drop();
      actor = people.length ? people[0].actor : "";
      go.disabled = asking;
    }
    const key = JSON.stringify([people, actor]);
    if (key !== shown) {  // drawn again only when the cast changes, so a choice being made keeps its focus
      shown = key;
      if (!people.length) {
        who.replaceChildren(el("p", { class: "g-muted" }, "No NPC is in this scene to preview."));
      } else {
        const pick = ui.choice("Whose context", people.map((person) => ({ value: person.actor, label: person.name })),
          { value: actor, onchange: (value) => { actor = value; shown = JSON.stringify([people, value]); drop(); go.disabled = false; } });
        who.replaceChildren(pick.node);
      }
    }
    go.hidden = !people.length;  // hidden, not off: no button sits disabled at rest
  }

  draw();
  return { node, update(next) { scene = next; draw(); } };
}
