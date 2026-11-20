// 073: What might they say? A player tries a line on one of the cast without saying it: the app asks the model how
// that character might answer (`play.imagine`), and shows the reply here, marked as hypothetical, to copy or to
// discard. Nothing is posted, kept or remembered by anyone in the story: the server writes nothing for it, the
// composer's draft is never touched (the line is typed here, or copied in from the draft with Use my draft), and
// the transcript is drawn only from the scene. The replies are this tab's memory of the chat, like 070's
// reference panel: they survive leaving the chat and coming back, and are gone with the tab or on Discard.
// Everything shown from the model is set as text.

// The replies kept for each chat while this tab is open (each chat's own: they never show in another chat).
const kept = new Map();
// the most chats whose replies this tab keeps, and the most replies one chat keeps: the oldest go first
export const MAX_CHATS = 10;
export const MAX_REPLIES = 5;
export const MAX_LINE = 20000;  // a post's limit: the line is read as the next post would be

// Who can be asked: the chat's NPCs, in cast order. The persona and the other players speak for themselves.
export function speakersOf(scene) {
  return scene.cast.filter((member) => member.role === "npc").map((member) => ({ actor: member.actor, name: member.name }));
}

// Why no one can be asked now, in the words the panel shows, or null. The server refuses each of these too; the
// panel says so first, so nothing is asked that cannot be answered.
export function blocked(scene) {
  if (!scene.state.seated) return "Take your seat first.";
  if (scene.state.waiting.length) return "Play is held up; nothing can be imagined until it goes on.";
  if (scene.state.open_turn) return "Keep a take or pass the turn first: a line is imagined as your next post.";
  if (!speakersOf(scene).length) return "No one here can answer.";
  return null;
}

// What a reply that did not come says: the first call's own reason, as 035 names it on a take.
export function missed(answer) {
  const call = answer.calls[0];
  if (!call) return "No reply came.";
  return "No reply came (" + (call.reason || call.status) + ").";
}

// What one reply's call used, as the answer reports it: no take or ledger keeps it, so the card says it.
export function usageText(usage) {
  if (!usage || !usage.attempts) return "";
  // one call is planned; a retry is another attempt of it, never another call
  const calls = usage.attempts === 1 ? "1 model call" : "1 model call (" + usage.attempts + " attempts)";
  if (usage.input_tokens == null || usage.output_tokens == null) return calls + ", tokens not reported";
  return calls + ", " + usage.input_tokens + " tokens in and " + usage.output_tokens + " out";
}

// makeImagine(ctx, chat, { composer, generates, mode }) -> { node, update(scene) }, or { node: null } where the app
// does not serve play.imagine. `generates()` says whether a model is set up (the chat page's own test); `mode()` is
// the composer's mode, so the line is read as Post would read it (auto where the composer has no modes).
export function makeImagine(ctx, chat, { composer, generates, mode = () => "auto" }) {
  if (!ctx.has("play.imagine")) return { node: null, update() {} };
  ctx.styles("imagine.css");
  const { ui } = ctx;
  const { el } = ui;
  const state = kept.get(chat) || { line: "", speaker: "", replies: [] };
  let scene = null, asking = false, serial = 0, shown = "";
  let held = "";  // the reason a held click put in the status line: cleared with the hold
  const alive = () => !(ctx.signal && ctx.signal.aborted);
  const remember = () => {
    if (kept.has(chat)) kept.delete(chat);  // the newest chat goes last, so the oldest is the one let go
    kept.set(chat, state);
    while (kept.size > MAX_CHATS) kept.delete(kept.keys().next().value);
  };

  const who = el("div", { class: "chat-imagine-who" });
  const line = ui.field("If you said or did", { value: state.line, multiline: true, name: "imagine-line",
    hint: "Try a line on someone without saying it. Nothing here is posted, and no one in the story hears it." });
  line.input.setAttribute("maxlength", String(MAX_LINE));
  line.input.setAttribute("rows", "2");
  line.input.addEventListener("input", () => { state.line = line.input.value; remember(); });
  const status = el("p", { class: "chat-imagine-status", role: "status", "aria-live": "polite" });
  const whyId = ui.id("imagine-why");
  const why = el("p", { id: whyId, class: "g-muted chat-imagine-why", hidden: true });  // why nothing can be asked now, while that holds
  const results = el("ul", { class: "chat-imagine-results", "aria-label": "Hypothetical replies" });
  const fromDraft = ui.button("Use my draft", () => {
    line.value = composer.value;
    state.line = line.input.value;
    remember();
    status.textContent = composer.value.trim() ? "Your draft is copied here; the draft itself stays as it is." : "Your draft is empty.";
  }, { size: "small" });
  // Held back, the buttons stay pressable and say why (aria-disabled, the reason beside them): play's own controls
  // are the only ones turned off while a turn waits. Only an ask on its way turns them off.
  const go = ui.button("Imagine a reply", () => ask(), { kind: "primary", size: "small" });
  const hold = (button, reason) => {
    if (reason) button.setAttribute("aria-disabled", "true"); else button.removeAttribute("aria-disabled");
    if (reason) button.setAttribute("aria-describedby", whyId); else button.removeAttribute("aria-describedby");
    return button;
  };
  const discardAll = ui.button("Discard all", () => { state.replies = []; remember(); status.textContent = "Discarded."; draw(); },
    { size: "small" });
  const node = el("details", { class: "chat-imagine" }, el("summary", {}, "What might they say?"),
    who, line.node, el("div", { class: "g-actions chat-imagine-actions" }, fromDraft, go), why, status, results,
    el("div", { class: "g-actions" }, discardAll));

  // Why nothing can be asked now, or null.
  function stopped() {
    if (!scene) return "Reading the scene…";
    return !generates() ? "No model is set up, so no one can be asked." : blocked(scene);
  }

  function chosen() {
    const select = who.querySelector("select");
    return select ? select.value : "";
  }

  // Ask the model; the answer is kept here, newest first. A reply for a chat this page has left is dropped.
  async function ask(again) {
    const text = again ? again.asked : line.value;  // the words as typed, read again as they were the first time
    const speaker = again ? again.speaker : chosen();
    const how = again ? again.mode : mode();
    if (asking) return;
    if (stopped()) { status.textContent = held = stopped(); return; }
    if (!text.trim()) { status.textContent = "Type what you would say or do."; return; }
    if (!speaker) { status.textContent = "Choose who answers."; return; }
    asking = true;
    const mine = ++serial;
    status.textContent = "Imagining a reply…";
    draw();
    try {
      const answer = await ctx.api("play.imagine", { chat, speaker, text, mode: how });
      if (mine !== serial) return;
      if (!alive()) return;  // the page was left: the reply is dropped, never kept for when it comes back
      state.replies.unshift({ asked: text, mode: how, line: answer.line.text, speaker, usage: usageText(answer.usage), name: answer.speaker.name, kind: answer.line.kind,
        replies: answer.replies.map((reply) => ({ speaker: reply.speaker, text: reply.text })),
        missed: answer.replies.length ? null : missed(answer) });
      state.replies = state.replies.slice(0, MAX_REPLIES);
      remember();
      status.textContent = answer.replies.length ? "A possible reply from " + answer.speaker.name + ". It is not in the story."
        : missed(answer);
    } catch (error) {
      if (!alive() || (error && error.reason === "left")) return;
      if (mine === serial) status.textContent = "Nothing was imagined: " + ui.errorText(error);
    } finally {
      if (mine === serial) asking = false;
      if (alive()) draw();
    }
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = "Copied. The scene is unchanged.";
    } catch {
      status.textContent = "Copying is not available here: select the reply to copy it.";
    }
  }

  function card(item, index) {
    const words = item.replies.map((reply) => reply.text).join("\n");
    const said = item.kind === "action" ? "If you did: " : "If you said: ";
    return el("li", { class: "g-card chat-imagine-card" },
      el("div", { class: "g-eyebrow" }, "Hypothetical · not in the story"),
      el("p", { class: "chat-imagine-line" }, said + item.line),
      item.missed ? el("p", { class: "g-muted" }, item.missed)
        : el("div", {}, el("p", { class: "chat-imagine-name" }, item.name + " might say:"),
          ...item.replies.map((reply) => el("blockquote", { class: "chat-imagine-reply" }, reply.text))),
      item.usage ? el("p", { class: "g-muted chat-imagine-usage" }, item.usage) : null,
      el("div", { class: "g-actions" },
        item.missed ? null : ui.button("Copy reply", () => copy(words), { size: "small" }),
        hold(ui.button("Ask again", () => ask(item), { size: "small", disabled: asking }), stopped()),
        ui.button("Discard", () => { state.replies.splice(index, 1); remember(); status.textContent = "Discarded."; draw(); },
          { size: "small" })));
  }

  function draw() {
    if (!scene) return;
    const people = speakersOf(scene);
    if (!people.some((person) => person.actor === state.speaker)) state.speaker = people.length ? people[0].actor : "";
    const key = JSON.stringify([people, state.speaker]);
    if (key !== shown) {  // drawn again only when the cast changes, so a choice being made keeps its focus
      shown = key;
      const pick = ui.choice("Who answers", people.map((person) => ({ value: person.actor, label: person.name })),
        { value: state.speaker, onchange: (value) => { state.speaker = value; shown = JSON.stringify([people, value]); remember(); } });
      who.replaceChildren(pick.node);
    }
    const reason = stopped();
    go.disabled = asking;
    hold(go, reason);
    why.textContent = reason || "";
    why.hidden = !reason;
    if (!reason && held && status.textContent === held) status.textContent = "";
    if (!reason) held = "";
    results.replaceChildren(...state.replies.map(card));
    discardAll.hidden = state.replies.length < 2;
  }

  return {
    node,
    update(next) {
      scene = next;
      draw();
    },
  };
}
