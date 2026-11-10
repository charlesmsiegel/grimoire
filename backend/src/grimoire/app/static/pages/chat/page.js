// One chat (Sprint 111): #/chat/<ChatRef>. The cast, the transcript with each take's lines, and the composer.
// Every action is one op that answers the scene, which is drawn again; the page keeps no record of its own
// except the composer's unsent text, a draft that survives leaving the chat and is cleared only once posted.
// Everything shown from the library (card text, model output) is set as text, never as markup.

const MODE_LABELS = { auto: "Read it for me", speech: "Speech", action: "Action" };
// What stops play until someone acts: no new post, answer or pass is taken meanwhile.
const STOPS = ["paused", "forked", "conflicted", "turns_unreadable"];
const CALL_NOTES = { refused: "the model refused", rejected: "its line was held back", failed: "the call failed",
  incomplete: "the reply was cut off", cancelled: "cancelled", planned: "not run yet", not_issued: "not asked" };

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const chat = ctx.path[0];
  if (!chat) { root.append(ui.notice("No chat is named in this address."), ui.actions(ui.link("All chats", "chats"))); return; }
  ctx.styles("page.css");
  // ctx.act turns the page's buttons back on when its work ends, so the composer's own state (no seat, a stop)
  // is drawn again after every action, or Post would come back on where it must stay off.
  // While one runs, whatever render draws is drawn off too (ctx.act turns off only the buttons it found), and the
  // whole chat is drawn again when it ends.
  const run = (work) => {
    if (page.busy || ctx.signal.aborted) return Promise.resolve(undefined);
    page.busy = true;
    return ctx.act(work).finally(() => { page.busy = false; if (page.scene && !ctx.signal.aborted) render(page.scene); });
  };
  const page = { busy: false, scene: null, mode: "auto", redirecting: null, striking: null, pickable: null, modes: null };
  const view = el("div", {});
  const composer = ui.field("Your turn", { draft: ctx.draft("composer:" + chat), multiline: true });
  composer.input.setAttribute("maxlength", "20000");
  composer.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); if (!send.disabled) run(post); }
  });
  const mode = el("select", { class: "g-input chat-mode", "aria-label": "How to read it",
    onchange: () => { page.mode = mode.value; } });
  const send = ui.button("Post", () => run(post), { kind: "primary" });
  const hints = el("div", { class: "g-muted chat-hints" });
  const bar = el("div", { class: "chat-bar" }, el("div", { class: "chat-bar-row" }, composer.node, mode, send), hints);
  root.append(el("div", { class: "g-actions" }, ui.link("All chats", "chats")), view, bar);
  if (ctx.has("sync")) ctx.setActions(syncButton(ctx, run, () => open()));
  // Answer only where the app serves generate and a model is set up: no button pretends to work.
  const generates = () => ctx.has("generate") && Boolean(ctx.library && ctx.library.generation);
  // Posting with a typed mode is the play adapter's; the core post is 044's, with no mode.
  const posting = ctx.has("play.post") ? "play.post" : "post";

  async function call(op, args) { return ctx.api(op, { chat, ...args }); }
  async function act(op, args) { render(await call(op, args)); }
  async function open() { render(await call("open", {})); }

  // A scene with no NPC who can speak is answered by the director: generate is refused nothing_to_generate
  // before anything is written, so the director reply is asked only then.
  async function answer() {
    try { return render(await call("generate", {})); }
    catch (error) { if (error.reason !== "nothing_to_generate") throw error; }
    render(await call("generate", { director: true }));
  }

  async function post() {
    const typed = composer.value;
    const text = typed.trim();
    if (!text || !page.scene) return;
    const before = page.scene.turns.length;
    const args = { text };
    if (posting === "play.post") args.mode = page.mode;
    const scene = await call(posting, args);
    // saved as a turn: the draft is done, even if the answer below fails. Whatever was typed after Post was
    // pressed is kept as the next draft.
    if (composer.value === typed) composer.clear();
    else composer.value = composer.value.startsWith(typed) ? composer.value.slice(typed.length).trimStart() : composer.value;
    render(scene, { bottom: true });
    // a typed table request ("Grimoire, skip") writes no new turn: nobody is asked to answer it
    const last = scene.turns[scene.turns.length - 1];
    const posted = scene.turns.length > before && last && last.post && last.open;
    if (generates() && posted && !scene.state.waiting.length) { await answer(); scrollDown(); }
  }

  function scrollDown() { window.scrollTo(0, document.body.scrollHeight); }

  function render(scene, { bottom = false } = {}) {
    page.scene = scene;
    // while play is held up (no seat, a pause, a fork, a conflict, unreadable turns) nothing new is taken
    page.stopped = !scene.state.seated || scene.state.waiting.some((item) => STOPS.includes(item));
    const players = scene.cast.filter((member) => !member.persona).map((member) => member.name).join(", ");
    ctx.setTitle(scene.label || (scene.launch && scene.launch.kind === "premise" ? "Premise chat" : players)
      || (scene.launch && scene.launch.ref) || "Chat");
    view.replaceChildren(castStrip(scene), ...notices(scene), transcript(scene), controls(scene));
    drawComposer(scene);
    if (page.busy) for (const node of view.querySelectorAll("button")) node.disabled = true;
    else if (page.stopped) for (const log of view.querySelectorAll("section.chat-log")) for (const node of log.querySelectorAll("button")) node.disabled = true;
    if (bottom) scrollDown();
  }

  // -- the cast -------------------------------------------------------------------------------------------
  function castStrip(scene) {
    const strip = el("ul", { class: "chat-cast", "aria-label": "Cast" });
    for (const member of scene.cast) {
      const avatar = member.art.slots.find((slot) => slot.slot === "avatar");
      const face = el("div", { class: "chat-face", "aria-hidden": "true" },
        avatar && avatar.state === "unavailable" ? "?" : member.name.slice(0, 1));
      if (avatar && avatar.state === "shown") {
        ctx.picture(avatar.image).then((url) => { if (url) face.replaceWith(el("img", { class: "chat-face", src: url, alt: "" })); },
          () => { /* the face stays a letter */ });
      }
      const role = member.persona ? "you" : member.role === "npc" ? "NPC" : member.role === "unseated" ? "not seated" : "player";
      strip.append(el("li", { class: "chat-member" }, face, el("div", {}, member.name), el("div", { class: "g-muted" }, role)));
    }
    const tools = el("div", { class: "g-actions" });
    if (ctx.has("play.add_card") && ctx.has("play.sources")) tools.append(addCard(scene));
    if (ctx.has("add_art") && scene.cast.length) tools.append(addPicture(scene));
    return el("section", { "aria-label": "Who is here" }, strip, tools);
  }

  // The picker stays open across the chat being drawn again (page.adding); the cards are read once, when first opened.
  function addCard(scene) {
    const box = el("details", { class: "chat-add", open: page.adding }, el("summary", {}, "Add a character or card"));
    if (page.adding && page.pickable) fillPicker(box, scene);
    box.addEventListener("toggle", () => {
      page.adding = box.open;
      if (!box.open || box.querySelector("select")) return;
      if (page.pickable) fillPicker(box, scene);
      else run(async () => { page.pickable = await ctx.api("play.sources", {}); });  // drawn filled when it ends
    });
    return box;
  }

  function fillPicker(box, scene) {
    const here = new Set(scene.cast.map((member) => member.name));
    const options = [{ value: "", label: "Choose" }];
    for (const [role, items] of [["character", page.pickable.characters], ["lorebook", page.pickable.lorebooks], ["scenario", page.pickable.scenarios]]) {
      for (const item of items) {
        options.push({ value: role + " " + item.ref,
          label: (role === "character" ? "" : role[0].toUpperCase() + role.slice(1) + ": ") + item.name
            + (role === "character" && here.has(item.name) ? " (a character of that name is here)" : "") });
      }
    }
    const pick = ui.choice("Card to add", options);
    box.append(pick.node, el("p", { class: "g-muted" }, "A newcomer hears only what is said from now on."),
      ui.button("Add", () => run(async () => {
        if (!pick.value) return;
        const space = pick.value.indexOf(" ");
        const scene = await call("play.add_card", { role: pick.value.slice(0, space), ref: pick.value.slice(space + 1) });
        page.adding = false;
        render(scene);
      })));
  }

  function addPicture(scene) {
    const who = ui.choice("Picture for", scene.cast.map((member) => ({ value: member.actor, label: member.name })));
    const file = el("input", { type: "file", accept: "image/*", hidden: true });
    file.addEventListener("change", () => run(async () => {
      const chosen = file.files[0]; file.value = "";
      if (!chosen) return;
      const data = new Uint8Array(await chosen.arrayBuffer());
      let binary = "";
      for (let i = 0; i < data.length; i += 0x8000) binary += String.fromCharCode.apply(null, data.subarray(i, i + 0x8000));
      const member = scene.cast.find((item) => item.actor === who.value);
      const slot = member && member.art.slots.find((item) => item.slot === "avatar");
      const args = { actor: who.value, slot: "avatar", media: btoa(binary) };
      if (slot && slot.etag) args.expected = slot.etag;
      await act("add_art", args);
    }));
    return el("span", { class: "chat-picture" }, who.node, ui.button("Add picture", () => file.click()), file);
  }

  // -- what holds play up ------------------------------------------------------------------------------------
  function notices(scene) {
    const out = [];
    const waiting = scene.state.waiting;
    const box = (text, ...actions) => { const node = ui.notice(text); if (actions.length) node.append(ui.actions(...actions)); return node; };
    if (scene.unfinished && ctx.has("continue")) {
      out.push(box("Starting stopped before it finished (" + scene.unfinished + ").",
        ui.button("Finish starting", () => run(() => act("continue", {})), { kind: "primary" })));
    }
    if (!scene.state.seated && ctx.has("continue")) {
      out.push(box("This device has not taken its seat at this chat yet.",
        ui.button("Take your seat", () => run(() => act("continue", {})), { kind: "primary" })));
    }
    if (waiting.includes("paused")) out.push(box("Play is paused.", ui.button("Resume", () => run(() => act("resume", {})))));
    if (waiting.includes("interrupted")) {
      out.push(box("A reply was cut off before it finished. Ask again to replace it, or pass the turn."));
    }
    if (scene.fork) {
      const choices = scene.fork.branches.filter((item) => !item.set_aside).map((branch) =>
        ui.button("Keep the branch from " + (branch.device || "this device") + " (" + branch.turns.length + " turns)",
          () => run(() => act("choose", { turn: branch.head }))));
      out.push(box("This chat forked: two devices played on. Choose the branch that goes on.", ...choices));
    }
    for (const conflict of scene.state.conflicts) {
      // which device a version came from is not in the scene, so keeping one is offered only with one paired device
      const peers = (ctx.library && ctx.library.peers) || [];
      const peer = peers.length === 1 ? peers[0].peer : null;
      const keeps = peer && ctx.has("resolve") ? ["mine", "theirs"].map((keep) =>
        ui.button(keep === "mine" ? "Keep this device's" : "Keep " + peer + "'s", () => run(async () => {
          const report = await ctx.api("resolve", { peer, path: conflict.path, mine: conflict.mine, theirs: conflict.theirs, keep });
          ctx.notify("Resolve: " + report.status + (report.reason ? " (" + report.reason + ")" : ""), { bad: report.status !== "resolved" });
          await open();
        }))) : [];
      out.push(box("In conflict after a sync: " + conflict.path + (keeps.length ? "" : ". Resolve it with the other device reachable."), ...keeps));
    }
    if (waiting.includes("turns_unreadable")) out.push(ui.notice("Some turns of this chat cannot be read; nothing is written until they can.", { kind: "bad" }));
    const broken = scene.problems.filter((problem) => problem.code === "broken_binding");
    const other = scene.problems.filter((problem) => problem.code !== "broken_binding");
    if (broken.length) {
      const holder = box("A source this chat binds is gone. Its turns stay.");
      if (ctx.has("play.repairs")) {
        holder.append(ui.actions(ui.button("See what is missing", () => run(async () => {
          const found = await call("play.repairs", {});
          holder.replaceChildren("A source this chat binds is gone. Its turns stay.", ui.list(found.repairs.map((repair) =>
            el("div", { class: "chat-repair" }, el("span", {}, repair.field + ": "), el("span", { class: "g-ref" }, repair.ref),
              el("span", { class: "g-muted" }, " (" + repair.reason + ") "),
              repair.choices.includes("detach") && ctx.has("play.detach")
                ? ui.button("Detach", () => run(async () => {
                  const sure = await ui.confirm("Detach " + repair.ref + " from this chat? It plays on without it, and this cannot be undone here.",
                    { confirmLabel: "Detach", danger: true });
                  if (sure) await act("play.detach", { field: repair.field, ref: repair.ref });
                }))
                : el("span", { class: "g-muted" }, "Import the card again to bring it back.")))));
        }))));
      }
      out.push(holder);
    }
    if (other.length) {
      out.push(el("details", { class: "g-notice" }, el("summary", {}, other.length + " note" + (other.length === 1 ? "" : "s") + " about this chat"),
        el("ul", {}, other.map((problem) => el("li", {}, problem.code + (problem.detail ? ": " + problem.detail : ""))))));
    }
    return out;
  }

  // -- the transcript ------------------------------------------------------------------------------------------
  const speakerOf = (line) => line.speaker || "Narrator";
  function lineView(line, tools) {
    return el("div", { class: "chat-line" }, el("span", { class: "chat-speaker" }, speakerOf(line) + ": "), el("span", {}, line.text), tools || null);
  }

  function transcript(scene) {
    const log = el("section", { class: "chat-log", "aria-label": "Transcript" });
    if (scene.history) {
      log.append(el("h2", { class: "g-muted" }, "Imported history"));
      for (const item of scene.history) log.append(lineView(item));
      log.append(el("hr", {}));
    }
    for (const turn of scene.turns) {
      if (turn.post) {
        const to = turn.post.to && turn.post.to.length ? " to " + turn.post.to.join(", ") : "";
        log.append(el("div", { class: "chat-post" }, el("span", { class: "chat-speaker" }, turn.post.speaker + to + ": "), turn.post.text,
          turn.post.withheld ? el("span", { class: "g-muted" }, " (only you know this)") : turn.post.private ? el("span", { class: "g-muted" }, " (private)") : null));
      }
      if (turn.kept) for (const item of turn.kept.lines) log.append(lineView(item, ctx.has("play.strike") ? strikeTools(item) : null));
      if (turn.passed && !turn.kept) log.append(el("div", { class: "g-muted" }, "Passed."));
      for (const take of turn.offered) log.append(takeView(scene, turn, take));
    }
    if (scene.set_aside && scene.set_aside.length) {
      log.append(el("p", { class: "g-muted" }, scene.set_aside.length + " turn(s) set aside by a fork choice; nobody heard them."));
    }
    if (!scene.turns.length && !scene.history) log.append(ui.empty("Nothing has been said yet. You speak first."));
    return log;
  }

  function strikeTools(item) {
    if (page.striking === item.line) {
      return el("span", { class: "chat-tools" },
        ui.button("Strike it", () => run(async () => { page.striking = null; await act("play.strike", { line: item.line }); }), { kind: "danger" }),
        ui.button("Cancel", () => { page.striking = null; render(page.scene); }));
    }
    const strike = ui.button("Strike", () => { page.striking = item.line; render(page.scene); });
    strike.setAttribute("aria-label", "Strike this line: nobody hears it from now on");
    return el("span", { class: "chat-tools" }, strike);
  }

  function takeView(scene, turn, take) {
    const box = el("article", { class: "chat-take", "aria-label": "Take " + take.take });
    const unscreened = scene.state.unscreened.includes(take.take);
    box.append(el("div", { class: "g-muted" }, "Take " + take.take + (take.state === "running" ? " (still being written)" : "")
      + (take.origin === "director" ? " · director" : "")));
    const asks = take.state === "proposed" && ctx.has("play.withdraw");
    for (const item of take.lines) box.append(lineView(item, asks ? lineRequests(item) : null));
    for (const callNote of take.calls) box.append(el("div", { class: "g-muted" }, noteOf(scene, callNote)));
    if (!take.lines.length && take.state !== "running") box.append(el("div", { class: "g-muted" }, "No line to keep."));
    if (scene.state.keepable.includes(take.take)) {
      box.append(ui.actions(ui.button(unscreened ? "Keep (uses lore no screen covered)" : "Keep", () => run(() =>
        act("keep", { turn: turn.turn, take: take.take, acknowledge_unscreened: unscreened })), { kind: "primary" })));
    }
    return box;
  }

  function noteOf(scene, note) {
    const member = scene.cast.find((item) => item.actor === note.actor || item.actor.endsWith("/" + note.actor));
    return (member ? member.name : note.actor) + ": " + (CALL_NOTES[note.status] || note.status) + (note.reason ? " (" + note.reason + ")" : "");
  }

  function lineRequests(item) {
    if (page.redirecting === item.line) {
      const direction = el("input", { type: "text", class: "g-input", maxlength: "2000", "aria-label": "How should it change",
        placeholder: "How should it change?" });
      setTimeout(() => direction.focus());
      return el("span", { class: "chat-tools" }, direction,
        ui.button("Redirect", () => run(async () => {
          const text = direction.value.trim();
          page.redirecting = null;
          await act("play.withdraw", { kind: "redirect", lines: [item.line], ...(text ? { direction: text } : {}) });
        })),
        ui.button("Cancel", () => { page.redirecting = null; render(page.scene); }));
    }
    return el("span", { class: "chat-tools" },
      ui.button("Skip", () => run(() => act("play.withdraw", { kind: "skip", lines: [item.line] }))),
      ui.button("Redirect", () => { page.redirecting = item.line; render(page.scene); }));
  }

  // -- play ----------------------------------------------------------------------------------------------------
  function controls(scene) {
    const bar = el("div", { class: "g-actions", role: "toolbar", "aria-label": "Play" });
    const stopped = !scene.state.seated || scene.state.waiting.some((item) => STOPS.includes(item));
    const ask = (label, args) => { const node = ui.button(label, () => run(() => act("generate", args))); node.disabled = stopped; return node; };
    if (generates()) {
      const npcs = scene.cast.filter((member) => member.role === "npc");
      const first = ui.button(npcs.length ? "Answer" : "Ask the director", () => run(answer));
      first.disabled = stopped;
      bar.append(first);
      if (npcs.length > 1) for (const npc of npcs) bar.append(ask("Ask " + npc.name, { speakers: [npc.actor] }));
      if (npcs.length) bar.append(ask("Ask the director", { director: true }));
    } else if (ctx.has("generate")) {
      bar.append(el("span", { class: "g-muted" }, "No model is set up, so nobody can answer yet. Posts, keeps and passes still work."));
    }
    const openTurn = scene.state.open_turn;  // Pass names the turn on screen, so a late tap never closes a newer one
    if (openTurn) {
      const pass = ui.button("Pass", () => run(() => act("pass", { turn: openTurn })));
      pass.disabled = page.stopped;
      bar.append(pass);
    }
    if (!scene.state.waiting.includes("paused")) bar.append(ui.button("Pause", () => run(() => act("pause", {}))));
    return bar;
  }

  function drawComposer(scene) {
    const modes = posting === "play.post" ? page.modes || ["auto"] : ["auto"];
    mode.replaceChildren(...modes.map((value) => el("option", { value }, MODE_LABELS[value] || value)));
    mode.value = modes.includes(page.mode) ? page.mode : "auto";
    mode.hidden = modes.length < 2;
    composer.input.placeholder = scene.persona ? "As " + scene.persona.name + ": say or do something" : "Say or do something";
    const seated = scene.state.seated && !scene.state.waiting.some((item) => STOPS.includes(item));
    send.disabled = !seated || !scene.persona;
    // 035 takes a new post only once the last turn is closed: keep a take, or pass. A typed table request
    // ("Grimoire, skip") is still read while a turn is open, so the composer stays usable for it.
    const lines = [];
    if (!scene.persona) lines.push("This chat has no character for you to play, so it takes no posts.");
    else if (scene.state.open_turn !== null) lines.push("Keep a take or pass this turn before you post again.");
    lines.push("Ctrl+Enter posts. Type “Grimoire, skip” or “redirect: kinder” to ask for the lines on show.");
    hints.replaceChildren(...lines.map((text) => el("div", {}, text)));
  }

  if (posting === "play.post") {
    // the modes the app's play.post takes, read once; a failure leaves the composer on "Read it for me"
    try { page.modes = (await ctx.api("play.sources", {})).post_modes; } catch (error) { if (error.reason === "left") throw error; }
  }
  render(await call("open", {}), { bottom: true });
}

function syncButton(ctx, run, after) {
  return ctx.ui.button("Sync", () => run(async () => {
    const peers = (ctx.library && ctx.library.peers) || [];
    if (!peers.length) { ctx.notify("No paired device is configured for this app.", { bad: true }); return; }
    for (const peer of peers) {
      const report = await ctx.api("sync", { peer: peer.peer });
      ctx.notify(peer.peer + ": " + report.status + (report.reason ? " (" + report.reason + ")" : "")
        + (report.conflicts.length ? ", " + report.conflicts.length + " in conflict" : ""), { bad: report.status !== "synced" });
    }
    await ctx.refreshLibrary();
    await after();
  }));
}
