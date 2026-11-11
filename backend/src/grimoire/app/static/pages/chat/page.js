// One chat (Sprint 111): #/chat/<ChatRef>. The page owns the whole pane: the story in one column (what holds
// play up, the scene bar, the transcript, the composer) and a side panel (Cast, Details) beside it, or over it
// on a narrow window. Every action is one op that answers the scene, which is drawn again; the page keeps no
// record of its own except the composer's unsent text, a draft that survives leaving the chat and is cleared
// only once posted. Everything shown from the library (card text, model output) is set as text, never as markup.

import { makeTray } from "./tray.js";
import { makeBackdrop } from "./backdrop.js";
import { makeAttach } from "./attach.js";
import { showTurn, wantedTurn } from "./find.js";
import { composerState, isCorrectable, isStopped, replyState, shownTake, stepTake, takeKey, takeLabel } from "./turn.js";

const MODE_LABELS = { auto: "Auto", speech: "Speech", action: "Action" };
const CALL_NOTES = { refused: "the model refused", rejected: "its line was held back", failed: "the call failed",
  incomplete: "the reply was cut off", cancelled: "cancelled", planned: "not run yet", not_issued: "not asked" };
const LAUNCH_KINDS = { character: "character", scenario: "scenario", bundle: "character with a lorebook" };
const BOUND_ROLES = { character: "Character", persona: "You play", lorebook: "Lorebook", scenario: "Scenario" };
const MAX_LIST = 1000;  // the most chats one list request returns
// Where the transcript was scrolled to in each chat, for coming back to it (the page scrolls its own log).
const scrolls = new Map();

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const chat = ctx.path[0];
  if (!chat) { root.append(ui.notice("No chat is named in this address."), ui.actions(ui.link("All chats", "chats"))); return undefined; }
  ctx.styles("page.css");
  ctx.layout("full");
  ctx.setCrumb(ui.link("Chats", "chats"));
  root.classList.add("chat-page");
  // ctx.act turns the page's buttons back on when its work ends, so the chat is drawn again after every action,
  // or Post would come back on where it must stay off. While one runs, whatever is drawn is drawn off too.
  const run = (work) => {
    if (page.busy || ctx.signal.aborted) return Promise.resolve(undefined);
    page.busy = true;
    page.failure = null;
    return ctx.act(work).finally(() => { page.busy = false; if (page.scene && !ctx.signal.aborted) render(page.scene); });
  };
  const page = { busy: false, scene: null, mode: "auto", modes: null, pickable: null, repairs: null, entry: undefined,
    tab: "cast", panelOpen: false, adding: false, writing: false, failure: null, chosen: null, redirecting: null, striking: null,
    keys: null, stopped: false,
    // a start that stopped before it finished, as Start Chat was told (?unfinished=), until continue answers
    unfinished: ctx.query.get("unfinished") };
  // Answer only where the app serves generate and a model is set up: no button pretends to work.
  const generates = () => ctx.has("generate") && Boolean(ctx.library && ctx.library.generation);
  // Posting with a typed mode is the play adapter's; the core post is 044's, with no mode.
  const posting = ctx.has("play.post") ? "play.post" : "post";
  const tint = ui.hue(chat);

  // -- the frame: built once, so the composer and the panel's tabs keep focus while the rest is drawn again ----
  const bars = el("div", { class: "chat-bars" });
  const sceneBar = el("div", { class: "chat-scene " + tint });
  const rows = el("div", { class: "chat-rows" });
  const log = el("section", { class: "chat-log g-tint " + tint, "aria-label": "Transcript", tabindex: "0" }, rows);
  const composer = ui.field("Your turn", { draft: ctx.draft("composer:" + chat), multiline: true });
  const playing = composer.node.querySelector("label");
  composer.input.setAttribute("maxlength", "20000");
  composer.input.title = "Ctrl+Enter posts";
  const fit = () => composer.input.setAttribute("rows", composer.input.value ? "2" : "1");
  composer.input.addEventListener("input", fit);
  composer.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); if (!send.disabled) run(post); }
  });
  fit();
  const modes = el("div", { class: "chat-modes" });
  const ask = ui.button("Ask for a reply", () => run(() => answer()));
  const send = ui.button("Post", () => run(post), { kind: "primary" });
  const noModel = el("div", { class: "chat-slot" });
  const tray = el("div", { class: "chat-slot" });
  const foot = el("div", { class: "chat-foot" }, tray, noModel,
    el("div", { class: "chat-composer" }, composer.node,
      el("div", { class: "chat-composer-foot" }, modes, el("div", { class: "chat-send" }, ask, send))));
  // the chat's backdrop and the pictures on its posts (./backdrop.js, ./attach.js): beside the story, never in it
  const redrawPictures = () => { if (page.scene && !ctx.signal.aborted) render(page.scene); };
  const backdrop = makeBackdrop(ctx, chat, { run, redraw: redrawPictures });
  const attach = makeAttach(ctx, chat, { field: composer.node, run, redraw: redrawPictures });
  const panelBody = el("div", { class: "chat-panel-body" });
  // The panel's tabs. Each has something to show for every chat; a tab that might have nothing is left out here.
  const PANEL_TABS = [
    { value: "cast", label: "Cast", draw: castTab },
    { value: "details", label: "Details", draw: detailsTab },
  ];
  const panelName = "Cast and " + PANEL_TABS[PANEL_TABS.length - 1].label.toLowerCase();
  const tabs = ui.segments("Side panel", PANEL_TABS, { value: page.tab, onchange: (value) => { page.tab = value; drawPanel(); } });
  tabs.node.classList.add("chat-tabs");
  const panel = el("aside", { class: "chat-panel", "aria-label": panelName },
    el("button", { type: "button", class: "g-button g-linklike chat-panel-close", onclick: () => setPanel(false) }, "Close panel"),
    tabs.node, panelBody);
  const panelToggle = el("button", { type: "button", class: "chat-scene-button chat-panel-toggle", "aria-expanded": "false",
    onclick: () => setPanel(!page.panelOpen) }, panelName);
  root.append(el("div", { class: "chat-frame" }, el("div", { class: "chat-column" }, bars, sceneBar, el("div", { class: "chat-stage" }, log), foot), panel));

  function setPanel(open) {
    page.panelOpen = open;
    panel.classList.toggle("chat-open", open);
    panelToggle.setAttribute("aria-expanded", String(open));
  }
  function showTab(name) { page.tab = name; tabs.set(name); setPanel(true); drawPanel(); }

  // Focus mode: the shell hides the rail and the header; the page hides its scene bar and side panel too.
  const onFocus = (on) => root.classList.toggle("chat-focused", Boolean(on));
  const focus = ui.button("Focus", () => ctx.focusMode(true, onFocus), { title: "Show only the story. Esc leaves." });
  ctx.setActions(focus);

  // The keys of the take on show (see turn.js), and Esc to close the panel where it lies over the story. The
  // shell's own keys (Ctrl+K, /, ?, Esc) are left alone: nothing here stops them.
  document.addEventListener("keydown", (event) => {
    if (ctx.signal.aborted || event.defaultPrevented || document.body.querySelector("dialog.g-dialog")) return;
    if (event.key === "Escape") { if (page.panelOpen) setPanel(false); return; }
    const at = document.activeElement;
    const tag = at && at.tagName ? at.tagName : "";
    if (!page.keys || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (at && at.isContentEditable)) return;
    const key = takeKey(event);
    // Enter on a button or a link is that control's own
    if (!key || (key === "keep" && (tag === "BUTTON" || tag === "A" || tag === "SUMMARY"))) return;
    const target = page.keys[key];
    if (!target || target.disabled) return;
    event.preventDefault();
    target.click();
  }, { signal: ctx.signal });

  async function call(op, args) { return ctx.api(op, { chat, ...args }); }
  async function act(op, args, options) { render(await call(op, args), options); }
  async function open() { render(await call("open", {})); }
  // 052's continue finishes a start that stopped and seats this device; its answer says whether it finished
  async function finish() { const scene = await call("continue", {}); page.unfinished = scene.unfinished || null; render(scene); }

  // Ask for a reply: with `args`, exactly those speakers (or the director alone); with none, whoever answers.
  // A scene with no NPC who can speak is answered by the director: generate is refused nothing_to_generate
  // before anything is written, so the director reply is asked only then. An ask that fails is not a notice
  // above the page: the transcript says the reply did not arrive, with what to do next.
  async function answer(args) {
    page.writing = true;
    render(page.scene, { bottom: true });
    try {
      let scene;
      try { scene = await call("generate", args || {}); }
      catch (error) {
        if (args || error.reason !== "nothing_to_generate") throw error;
        scene = await call("generate", { director: true, speakers: [] });
      }
      page.writing = false;
      page.chosen = null;  // the new take is the one on show
      render(scene, { bottom: true });
    } catch (error) {
      page.writing = false;
      if (error.reason === "left") throw error;
      page.failure = { text: ui.errorText(error) };
      let scene = page.scene;
      try { scene = await call("open", {}); } catch (again) { if (again.reason === "left") throw again; }
      render(scene, { bottom: true });
    }
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
    fit();
    render(scene, { bottom: true });
    await attach.posted(scene, before);
    // a typed table request ("Grimoire, pause") writes no new turn: nobody is asked to answer it
    const last = scene.turns[scene.turns.length - 1];
    const posted = scene.turns.length > before && last && last.post && last.open;
    if (generates() && posted && !scene.state.waiting.length) await answer();
  }

  function render(scene, { bottom = false } = {}) {
    page.scene = scene;
    if (scene.turns.some((turn) => turn.post)) page.unfinished = null;  // a stale address: play has begun
    // while play is held up (no seat, a pause, a fork, a conflict, unreadable turns) nothing new is taken
    page.stopped = isStopped(scene);
    if (!scene.problems.some((problem) => problem.code === "broken_binding")) page.repairs = null;
    const reply = replyState(scene, { writing: page.writing, failure: page.failure });
    drawHeader(scene);
    bars.replaceChildren(...holds(scene));
    drawSceneBar(scene);
    rows.replaceChildren(...transcript(scene, reply));
    drawComposer(scene, reply);
    drawPanel();
    if (bottom) log.scrollTop = log.scrollHeight;
  }

  // What may be pressed now. While an action runs, nothing; while play is held up, in the transcript only what
  // changes nothing by itself (stepping through takes, opening a field) and, where corrections are still taken,
  // the corrections.
  function settle() {
    if (page.busy) {
      for (const region of [bars, rows, panelBody]) for (const node of region.querySelectorAll("button")) node.disabled = true;
      send.disabled = true;
      ask.disabled = true;
      return;
    }
    if (!page.stopped || !page.scene) return;
    const correctable = isCorrectable(page.scene);
    for (const node of rows.querySelectorAll("button")) {
      if (!node.hasAttribute("data-local") && !(correctable && node.hasAttribute("data-correction"))) node.disabled = true;
    }
    for (const node of panelBody.querySelectorAll("button")) if (node.hasAttribute("data-play")) node.disabled = true;
  }

  // a skip, a redirect or a strike, which 035 takes while play is paused (a keep or a pass it does not)
  function correction(node) { node.setAttribute("data-correction", ""); return node; }
  // a control that only changes what this page shows
  function local(node) { node.setAttribute("data-local", ""); return node; }
  // a small action on a line
  function tool(label, onclick, { name, danger = false } = {}) {
    return el("button", { type: "button", class: "chat-tool" + (danger ? " chat-danger" : ""), onclick, "aria-label": name }, label);
  }

  // -- the header ---------------------------------------------------------------------------------------------
  function drawHeader(scene) {
    const players = scene.cast.filter((member) => !member.persona).map((member) => member.name).join(", ");
    ctx.setTitle(scene.label || (scene.launch && scene.launch.kind === "premise" ? "Premise chat" : players)
      || (scene.launch && scene.launch.ref) || "Chat");
    const where = scene.scope && (scene.scope.title || scene.scope.world);
    ctx.setCrumb(ui.link("Chats", "chats"), where ? " / " + where : "");
  }

  // -- pictures -----------------------------------------------------------------------------------------------
  function pictureOf(member) {
    const slot = member && member.art.slots.find((item) => item.slot === "avatar");
    return slot && slot.state === "shown" ? ctx.picture(slot.image) : undefined;
  }
  // The face beside a line: the speaker's picture when the cast has one, else their initial on their own colour.
  function face(scene, actor, name, size = 40) {
    const member = actor ? scene.cast.find((item) => item.actor === actor) : null;
    return ui.avatar(name, { size, key: actor || name, picture: pictureOf(member) });
  }

  // -- what holds play up ---------------------------------------------------------------------------------------
  function hold(text, { bad = false } = {}, ...actions) {
    return el("div", { class: "chat-hold" + (bad ? " chat-bad" : ""), role: bad ? "alert" : "status" }, el("span", {}, text), ...actions);
  }
  function holdButton(label, onclick) {
    const node = ui.button(label, onclick, { size: "small" });
    node.classList.add("chat-outline");
    return node;
  }

  function holds(scene) {
    const out = [];
    const waiting = scene.state.waiting;
    if (waiting.includes("paused")) {
      out.push(hold("Play is paused. Corrections still work.", {}, holdButton("Resume", () => run(() => act("resume", {})))));
    }
    if (page.unfinished && ctx.has("continue")) {
      out.push(hold("Starting stopped before it finished (" + page.unfinished + ").", {}, holdButton("Finish starting", () => run(finish))));
    }
    if (!scene.state.seated && ctx.has("continue")) {
      out.push(hold("This device has not taken its seat at this chat yet.", {}, holdButton("Take your seat", () => run(finish))));
    }
    if (scene.fork) {
      const choices = scene.fork.branches.filter((item) => !item.set_aside).map((branch) =>
        holdButton("Keep the branch from " + (branch.device || "this device") + " (" + branch.turns.length + " turns)",
          () => run(() => act("choose", { turn: branch.head }))));
      out.push(hold("This chat forked: two devices played on. Choose the branch that goes on.", {}, ...choices));
    }
    for (const conflict of scene.state.conflicts) {
      // which device a version came from is not in the scene, so keeping one is offered only with one paired device
      const peers = (ctx.library && ctx.library.peers) || [];
      const peer = peers.length === 1 ? peers[0].peer : null;
      const keeps = peer && ctx.has("resolve") ? ["mine", "theirs"].map((keep) =>
        holdButton(keep === "mine" ? "Keep this device's" : "Keep " + peer + "'s", () => run(async () => {
          const report = await ctx.api("resolve", { peer, path: conflict.path, mine: conflict.mine, theirs: conflict.theirs, keep });
          ctx.notify("Resolve: " + report.status + (report.reason ? " (" + report.reason + ")" : ""), { bad: report.status !== "resolved" });
          await open();
        }))) : [];
      out.push(hold("In conflict after a sync: " + conflict.path + (keeps.length ? "" : ". Resolve it with the other device reachable."), {}, ...keeps));
    }
    if (waiting.includes("turns_unreadable")) {
      out.push(hold("Some turns of this chat cannot be read; nothing is written until they can.", { bad: true }));
    }
    if (scene.problems.some((problem) => problem.code === "broken_binding")) {
      // the mending itself is in the panel's Details
      out.push(hold("A source this chat binds is gone. Its turns stay.", {},
        ctx.has("play.repairs") ? holdButton("See what is missing", () => { showTab("details"); if (!page.repairs) run(findRepairs); }) : null));
    }
    return out;
  }

  async function findRepairs() { page.repairs = (await call("play.repairs", {})).repairs; }

  // -- the scene bar ----------------------------------------------------------------------------------------------
  // Who is in the scene, on a band of the chat's own colour. The scene has no place name and does not say who
  // is elsewhere, so the band shows neither.
  function drawSceneBar(scene) {
    const chips = el("div", { class: "chat-chips", role: "list", "aria-label": "In this scene" });
    for (const member of scene.cast) {
      const short = member.name.trim().split(/\s+/)[0] || member.name;
      chips.append(el("span", { class: "chat-chip", role: "listitem", title: member.name }, face(scene, member.actor, member.name, 22),
        el("span", {}, member.persona ? short + " · you" : short)));
    }
    sceneBar.replaceChildren(chips, panelToggle);
    backdrop.draw(sceneBar);
  }

  // -- the transcript ---------------------------------------------------------------------------------------------
  const speakerOf = (line) => line.speaker || "Narrator";
  function divider(text) { return el("div", { class: "chat-divider g-eyebrow" }, el("span", {}, text)); }
  function note(text) { return el("div", { class: "chat-note" }, text); }

  // One thing said: a face, who said it, the words, and the small actions on it.
  function said(scene, { actor, speaker, text, kind = "chat-line", you = false, to = "", aside = "", tools = null }) {
    return el("div", { class: "chat-row " + kind },
      face(scene, actor, speaker),
      el("div", { class: "chat-said" },
        el("div", { class: "chat-head" }, el("span", { class: "chat-speaker" }, speaker + (you ? " · you" : "") + to), tools),
        el("p", { class: "chat-text g-prose" }, text),
        aside ? el("div", { class: "g-muted" }, aside) : null));
  }

  function transcript(scene, reply) {
    const out = [];
    page.keys = null;
    if (scene.history) {
      out.push(divider("Imported history"));
      for (const item of scene.history) out.push(said(scene, { actor: null, speaker: speakerOf(item), text: item.text }));
      out.push(divider("Played here"));
    }
    for (const turn of scene.turns) {
      const first = out.length;
      if (turn.post) {
        const to = turn.post.to && turn.post.to.length ? " to " + turn.post.to.join(", ") : "";
        const mine = scene.persona && turn.post.actor === scene.persona.actor;
        out.push(said(scene, { actor: turn.post.actor, speaker: turn.post.speaker, text: turn.post.text, kind: "chat-post", you: mine, to,
          aside: turn.post.withheld ? "Only you know this." : turn.post.private ? "Private." : "" }));
        attach.under(turn, out[out.length - 1]);  // the picture attached to this post, if any, under its text
      }
      if (turn.kept) {
        for (const item of turn.kept.lines) {
          out.push(said(scene, { actor: item.actor, speaker: speakerOf(item), text: item.text,
            tools: ctx.has("play.strike") ? strikeTools(item) : null }));
        }
      }
      if (turn.passed && !turn.kept) out.push(note("The turn was passed."));
      for (const row of out.slice(first)) row.setAttribute("data-turn", String(turn.number));  // find.js opens a chat at a turn
    }
    if (scene.set_aside && scene.set_aside.length) {
      out.push(note(scene.set_aside.length + " turn(s) set aside by a fork choice; nobody heard them."));
    }
    if (!scene.turns.length && !scene.history) out.push(note("Nothing has been said yet. You speak first."));
    if (reply.failed) out.push(failedCard(scene, reply));
    if (reply.unanswered) out.push(unanswered(reply));
    if (reply.writing) out.push(el("div", { class: "chat-writing", role: "status" }, "The table is answering…"));
    else if (reply.takes.length) out.push(takeCard(scene, reply));
    return out;
  }

  function strikeTools(item) {
    if (page.striking === item.line) {
      return el("span", { class: "chat-tools" },
        correction(tool("Strike it", () => run(async () => { page.striking = null; await act("play.strike", { line: item.line }); }), { danger: true })),
        correction(tool("Cancel", () => { page.striking = null; render(page.scene); })));
    }
    return el("span", { class: "chat-tools" }, correction(tool("Strike", () => { page.striking = item.line; render(page.scene); },
      { name: "Strike this line: nobody hears it from now on" })));
  }

  function passButton(reply, { quiet = false } = {}) {
    const turn = reply.turn.turn;  // Pass names the turn on screen, so a late tap never closes a newer one
    const node = ui.button("Pass the turn", () => run(() => act("pass", { turn }, { bottom: true })), { size: quiet ? "" : "small" });
    if (quiet) node.classList.add("chat-pass");
    return node;
  }

  function noteOf(scene, callNote) {
    const member = scene.cast.find((item) => item.actor === callNote.actor || item.actor.endsWith("/" + callNote.actor));
    return (member ? member.name : callNote.actor) + ": " + (CALL_NOTES[callNote.status] || callNote.status) + (callNote.reason ? " (" + callNote.reason + ")" : "");
  }

  // The reply did not arrive: what happened, and the ways on.
  function failedCard(scene, reply) {
    const failed = reply.failed;
    const what = failed.kind === "error" ? [failed.text, "Nothing was added to the story."]
      : failed.kind === "interrupted" ? ["A reply was cut off before it finished. Ask again to replace it, or pass the turn."]
        : failed.calls.map((item) => noteOf(scene, item));
    const again = generates() ? ui.button("Try again", () => run(() => answer()), { kind: "primary", size: "small" }) : null;
    return el("div", { class: "chat-failed", role: "alert" },
      el("span", { class: "chat-failed-title" }, "The reply didn’t arrive"),
      ...what.map((text) => el("span", { class: "chat-failed-text" }, text)),
      el("div", { class: "chat-failed-actions" }, again, reply.turn ? passButton(reply) : null,
        ctx.registered("providers") ? ui.buttonLink("Check the model", "providers", [], undefined, { size: "small" }) : null));
  }

  // A turn open with nothing on offer (a post nobody answered, or a take whose lines were all skipped).
  function unanswered(reply) {
    return el("div", { class: "chat-note chat-unanswered" }, el("span", {}, "Nobody has answered this turn yet."), passButton(reply));
  }

  // The proposed reply: one take on show, the others a step away, and what to do with it.
  function takeCard(scene, reply) {
    const turn = reply.turn;
    const chosen = page.chosen && page.chosen.turn === turn.turn ? page.chosen.take : null;
    const take = shownTake(reply.takes, chosen);
    const keys = {};
    const step = (by) => () => { page.chosen = { turn: turn.turn, take: stepTake(reply.takes, take.take, by) }; render(page.scene); };
    const head = el("div", { class: "chat-take-head" },
      el("div", {}, ui.eyebrow("Proposed reply", { accent: true }), el("div", { class: "chat-take-sub" }, "Not part of the story until you keep it")));
    if (reply.takes.length > 1) {
      keys.prev = local(el("button", { type: "button", class: "chat-step", "aria-label": "Previous take", onclick: step(-1) }, "‹"));
      keys.next = local(el("button", { type: "button", class: "chat-step", "aria-label": "Next take", onclick: step(1) }, "›"));
      head.append(el("div", { class: "chat-stepper" }, keys.prev, el("span", { "aria-live": "polite" }, takeLabel(reply.takes, take)), keys.next));
    } else if (take.origin === "director") {
      head.append(el("div", { class: "chat-stepper" }, "Director"));
    }
    const withdraws = take.state === "proposed" && ctx.has("play.withdraw");
    const lines = take.lines.map((item) => item.line);
    if (page.redirecting && (page.redirecting.turn !== turn.turn || page.redirecting.take !== take.take
      || !page.redirecting.lines.every((line) => lines.includes(line)))) page.redirecting = null;
    const redirect = (which) => () => {
      const same = page.redirecting && page.redirecting.lines.length === which.length && which.every((line) => page.redirecting.lines.includes(line));
      page.redirecting = same ? null : { turn: turn.turn, take: take.take, lines: which };
      render(page.scene);
    };
    const body = el("div", { class: "chat-take-body" });
    for (const item of take.lines) {
      const tools = withdraws ? el("span", { class: "chat-tools" },
        correction(tool("Skip", () => run(() => act("play.withdraw", { kind: "skip", lines: [item.line] })), { name: "Skip this line: nobody hears it" })),
        lines.length > 1 ? correction(tool("Redirect", redirect([item.line]), { name: "Redirect this line" })) : null) : null;
      body.append(said(scene, { actor: item.actor, speaker: speakerOf(item), text: item.text, tools }));
    }
    for (const callNote of take.calls) body.append(el("div", { class: "g-muted" }, noteOf(scene, callNote)));
    const card = el("article", { class: "chat-take", "aria-label": "Proposed reply" }, head, body);
    if (page.redirecting && withdraws) card.append(redirectField(lines.length > 1 && page.redirecting.lines.length === 1));
    const footer = el("div", { class: "chat-take-foot" });
    if (scene.state.keepable.includes(take.take)) {
      const unscreened = scene.state.unscreened.includes(take.take);
      keys.keep = ui.button(unscreened ? "Keep this take (uses lore no screen covered)" : "Keep this take", () => run(() =>
        act("keep", { turn: turn.turn, take: take.take, acknowledge_unscreened: unscreened }, { bottom: true })), { kind: "primary" });
      footer.append(keys.keep);
    }
    if (generates()) { keys.again = ui.button("Try again", () => run(() => answer())); footer.append(keys.again); }
    if (withdraws) {
      const all = correction(ui.button("Redirect…", redirect(lines)));
      all.setAttribute("aria-expanded", String(Boolean(page.redirecting && page.redirecting.lines.length === lines.length)));
      footer.append(all);
    }
    keys.pass = passButton(reply, { quiet: true });
    footer.append(keys.pass, el("span", { class: "chat-keys" }, "Keys: "
      + [keys.prev && "← → step through takes", keys.keep && "Enter keeps", keys.again && "R tries again", "P passes"].filter(Boolean).join(" · ")));
    card.append(footer);
    page.keys = keys;
    return card;
  }

  // How the lines should change. The redirect withdraws them with the direction; a new reply is then asked for
  // where one can be (not while play is paused, and not with no model).
  function redirectField(oneLine) {
    const direction = el("input", { type: "text", class: "g-input", maxlength: "2000", "aria-label": oneLine ? "How should this line change" : "How should it change",
      placeholder: "How should it change? e.g. kinder, shorter" });
    const go = correction(ui.button("Redirect", () => run(async () => {
      const text = direction.value.trim();
      const lines = page.redirecting.lines;
      page.redirecting = null;
      await act("play.withdraw", { kind: "redirect", lines, ...(text ? { direction: text } : {}) });
      if (generates() && !page.stopped) await answer();
    })));
    go.classList.add("chat-outline");
    direction.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); go.click(); } });
    setTimeout(() => { if (!ctx.signal.aborted && !direction.disabled) direction.focus(); });
    return el("div", { class: "chat-redirect" }, direction, go);
  }

  // -- above the composer ---------------------------------------------------------------------------------------
  // What waits on the person for this chat (the inbox's open items for it): tray.js reads and draws it.
  const waits = makeTray(ctx, chat, { run, reopen: open, redraw: () => { if (page.scene && !page.busy) render(page.scene); } });
  function waitingTray(scene) { return waits.view(scene); }

  function noModelNotice() {
    if (ctx.library && ctx.library.generation) return null;
    return el("div", { class: "chat-nomodel", role: "status" },
      el("span", { class: "chat-nomodel-text" }, "No model is set up, so nobody will answer. You can still post."),
      ctx.registered("providers") ? ui.buttonLink("Set one up", "providers", [], undefined, { kind: "danger", size: "small" }) : null);
  }

  function drawComposer(scene, reply) {
    const state = composerState(scene, reply, { asks: generates() });
    const waits = waitingTray(scene);
    tray.replaceChildren(...(waits ? [waits] : []));
    const notice = noModelNotice();
    noModel.replaceChildren(...(notice ? [notice] : []));
    playing.replaceChildren(...(scene.persona
      ? [face(scene, scene.persona.actor, scene.persona.name || "?", 22), "Playing " + (scene.persona.name || "your character")]
      : ["Your turn"]));
    composer.input.placeholder = state.placeholder;
    send.disabled = !state.canPost;
    ask.hidden = !generates();
    ask.disabled = generates() && !state.canAsk;  // hidden with no model, and then not off either
  }

  function drawModes() {
    const offered = posting === "play.post" ? page.modes || ["auto"] : ["auto"];
    if (!offered.includes(page.mode)) page.mode = "auto";
    if (offered.length < 2) { modes.replaceChildren(); return; }
    const control = ui.segments("How to read it", offered.map((value) => ({ value, label: MODE_LABELS[value] || value })),
      { value: page.mode, onchange: (value) => { page.mode = value; } });
    modes.replaceChildren(control.node);
  }

  // -- the side panel ---------------------------------------------------------------------------------------------
  function drawPanel() {
    if (!page.scene) return;
    const tab = PANEL_TABS.find((item) => item.value === page.tab) || PANEL_TABS[0];
    panelBody.replaceChildren(...tab.draw(page.scene).filter(Boolean));
    settle();
  }

  // an action that plays (asks someone to speak): off while play is held up
  function plays(node) { node.setAttribute("data-play", ""); return node; }
  function smallAsk(label, args, name) {
    const node = plays(ui.button(label, () => run(() => answer(args)), { size: "small" }));
    node.classList.add("chat-ask");
    if (name) node.setAttribute("aria-label", name);
    return node;
  }

  function castTab(scene) {
    const cast = el("ul", { class: "chat-cast", "aria-label": "Cast" });
    for (const member of scene.cast) {
      const portrait = ui.portrait(member.name, { key: member.actor, picture: pictureOf(member) });
      portrait.classList.add("chat-portrait");
      const state = member.persona ? "You" : member.role === "npc" ? "NPC" : member.role === "unseated" ? "Not seated" : "Player";
      cast.append(el("li", { class: "chat-member" }, portrait,
        el("div", { class: "chat-member-text" }, el("span", { class: "chat-name" }, member.name), el("span", { class: "chat-state" }, state),
          el("div", { class: "chat-member-actions" },
            member.role === "npc" && generates() ? smallAsk("Ask to speak", { speakers: [member.actor] }, "Ask " + member.name + " to speak") : null,
            ctx.has("add_art") ? pictureButton(member) : null))));
    }
    const out = [cast];
    if (generates()) {
      // the director alone: with no speakers named, 035 would ask every NPC on stage too
      out.push(el("div", { class: "chat-member chat-director" },
        el("div", { class: "chat-member-text" }, el("span", { class: "chat-name" }, "The director"),
          el("span", { class: "chat-state" }, "Narrates the scene when asked"),
          el("div", { class: "chat-member-actions" }, smallAsk("Ask the director", { director: true, speakers: [] })))));
    }
    if (ctx.has("play.add_card") && ctx.has("play.sources")) out.push(addCard(scene));
    return out;
  }

  // A picture for one of the cast: their avatar, replaced if they have one.
  function pictureButton(member) {
    const slot = member.art.slots.find((item) => item.slot === "avatar");
    const file = el("input", { type: "file", accept: "image/*", hidden: true, "aria-label": "Picture for " + member.name });
    file.addEventListener("change", () => run(async () => {
      const chosen = file.files[0]; file.value = "";
      if (!chosen) return;
      const data = new Uint8Array(await chosen.arrayBuffer());
      let binary = "";
      for (let i = 0; i < data.length; i += 0x8000) binary += String.fromCharCode.apply(null, data.subarray(i, i + 0x8000));
      const args = { actor: member.actor, slot: "avatar", media: btoa(binary) };
      if (slot && slot.etag) args.expected = slot.etag;
      await act("add_art", args);
    }));
    const label = slot && slot.state === "shown" ? "Change picture" : "Add picture";
    return el("span", {}, tool(label, () => file.click(), { name: label + " for " + member.name }), file);
  }

  // The picker stays open across the chat being drawn again (page.adding); the cards are read once.
  function addCard(scene) {
    const toggle = ui.button("Add a character", () => {
      page.adding = !page.adding;
      if (page.adding && !page.pickable) run(async () => { page.pickable = await ctx.api("play.sources", {}); });  // drawn filled when it ends
      else drawPanel();
    }, { kind: "dashed", size: "small" });
    toggle.setAttribute("aria-expanded", String(page.adding));
    const box = el("div", { class: "chat-add" }, toggle);
    if (!page.adding || !page.pickable) return box;
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
    box.append(pick.node, el("p", { class: "g-muted" }, "A character, a lorebook or a scenario from your library. A newcomer hears only what is said from now on."),
      ui.button("Add", () => run(async () => {
        if (!pick.value) return;
        const space = pick.value.indexOf(" ");
        const scene = await call("play.add_card", { role: pick.value.slice(0, space), ref: pick.value.slice(space + 1) });
        page.adding = false;
        page.unfinished = scene.unfinished || null;  // the newcomer bound but not seated: continue seats it
        render(scene);
        loadEntry();
      }), { size: "small" }));
    return box;
  }

  function detailsTab(scene) {
    const facts = el("dl", { class: "chat-facts" });
    const fact = (name, ...value) => facts.append(el("dt", {}, name), el("dd", {}, ...value));
    const scope = scene.scope;
    if (scope && scope.title) fact("Campaign", scope.title);
    fact("World", scope && scope.world ? el("span", { class: "g-mono" }, scope.world) : "None");
    const launch = scene.launch;
    if (launch) fact("Started from", ...startedFrom(launch));
    fact("Turns", scene.turns.filter((turn) => turn.kept).length + " kept");
    const paused = scene.state.waiting.includes("paused");
    const actions = el("div", { class: "chat-detail-actions" },
      ui.button(paused ? "Resume" : "Pause", () => run(() => act(paused ? "resume" : "pause", {})), { size: "small" }),
      ctx.has("sync") ? ui.button("Sync", () => run(sync), { size: "small" }) : null,
      scope && scope.title && ctx.registered("campaign") ? ui.buttonLink("Campaign hub", "campaign", [scope.campaign], undefined, { size: "small" }) : null);
    return [el("section", { class: "chat-section" }, facts, actions), boundSection(), mending(scene), notesSection(scene)];
  }

  // The name of a bound source, from the chat's entry in the chat list (the scene itself carries refs only).
  function boundName(ref) {
    const bare = String(ref).split("@")[0];
    const found = page.entry && page.entry.sources.find((item) => item.ref === bare);
    return found ? found : null;
  }

  function startedFrom(launch) {
    if (launch.kind === "premise") return ["A premise", launch.premise ? el("div", { class: "chat-premise" }, launch.premise) : null];
    const kind = LAUNCH_KINDS[launch.kind] || launch.kind;
    const found = launch.ref ? boundName(launch.ref) : null;
    const name = (page.entry && page.entry.launch && page.entry.launch.name) || (found && found.name);
    if (!name) return [launch.ref ? el("span", { class: "g-mono" }, launch.ref) : "A " + kind, launch.ref ? " (" + kind + ")" : ""];
    return [found && ctx.registered("source") ? ui.sourceLink(found.ref, name) : name, " (" + kind + ")"];
  }

  // What this chat plays from: every source it binds now, as the chat list names them.
  function boundSection() {
    if (!page.entry || !page.entry.sources.length) return null;
    const linked = ctx.registered("source");
    return el("section", { class: "chat-section" }, ui.eyebrow("What this chat plays from"),
      ...page.entry.sources.map((item) => el(linked ? "a" : "div", { class: "chat-bound", href: linked ? ctx.href("source", [item.ref]) : undefined, title: item.ref },
        el("span", { class: "chat-bound-name" + (item.name ? "" : " g-ref") }, item.name || item.ref),
        el("span", { class: "chat-bound-role" }, BOUND_ROLES[item.role] || item.role))));
  }

  // A source this chat binds is gone: what is missing, and detaching it where that mends the chat.
  function mending(scene) {
    if (!scene.problems.some((problem) => problem.code === "broken_binding")) return null;
    const section = el("section", { class: "chat-section" }, ui.eyebrow("Missing sources"),
      el("p", { class: "g-muted chat-plain" }, "A source this chat binds is gone. Its turns stay."));
    // what play.repairs found is kept on the page, so the redraw after the request draws it again
    if (page.repairs) {
      for (const repair of page.repairs) {
        section.append(el("div", { class: "chat-repair" },
          el("div", {}, el("span", {}, repair.field + ": "), el("span", { class: "g-ref" }, repair.ref), el("span", { class: "g-muted" }, " (" + repair.reason + ")")),
          repair.choices.includes("detach") && ctx.has("play.detach")
            ? ui.button("Detach", () => run(async () => {
              const sure = await ui.confirm("Detach " + repair.ref + " from this chat? It plays on without it, and this cannot be undone here.",
                { confirmLabel: "Detach", danger: true });
              if (!sure) return;
              const rest = page.repairs.filter((item) => item.field !== repair.field || item.ref !== repair.ref);
              await act("play.detach", { field: repair.field, ref: repair.ref });
              // the redraw when this ends shows what is still missing; drawing a chat with none cleared it
              if (page.repairs) page.repairs = rest;
              loadEntry();
            }), { size: "small" })
            : el("span", { class: "g-muted" }, "Import the card again to bring it back.")));
      }
    } else if (ctx.has("play.repairs")) {
      section.append(ui.button("See what is missing", () => run(findRepairs), { size: "small" }));
    }
    return section;
  }

  function notesSection(scene) {
    const other = scene.problems.filter((problem) => problem.code !== "broken_binding");
    if (!other.length) return null;
    return el("details", { class: "chat-section chat-notes" }, el("summary", {}, other.length + " note" + (other.length === 1 ? "" : "s") + " about this chat"),
      el("ul", {}, other.map((problem) => el("li", {}, problem.code + (problem.detail ? ": " + problem.detail : "")))));
  }

  async function sync() {
    const peers = (ctx.library && ctx.library.peers) || [];
    if (!peers.length) { ctx.notify("No paired device is configured for this app.", { bad: true }); return; }
    for (const peer of peers) {
      const report = await ctx.api("sync", { peer: peer.peer });
      ctx.notify(peer.peer + ": " + report.status + (report.reason ? " (" + report.reason + ")" : "")
        + (report.conflicts.length ? ", " + report.conflicts.length + " in conflict" : ""), { bad: report.status !== "synced" });
    }
    await ctx.refreshLibrary();
    await open();
  }

  // This chat as the chat list has it: the names of what it was started from and plays from. Read beside the
  // scene, never waited on; without it Details shows refs where it would show names.
  function loadEntry() {
    if (!ctx.has("chats")) return;
    ctx.api("chats", { filter: "recent", limit: MAX_LIST }).then((answer) => {
      if (ctx.signal.aborted) return;
      page.entry = answer.chats.find((item) => item.chat === chat) || null;
      drawPanel();
    }, () => { /* Details goes without the names */ });
  }

  const leave = () => { scrolls.set(chat, log.scrollTop); };
  if (ctx.has("play.sources")) {
    // the cards the picker offers and the modes the app's play.post takes, read once; a failure leaves the
    // composer on Auto and the picker to read them when it is opened
    try { page.pickable = await ctx.api("play.sources", {}); page.modes = page.pickable.post_modes; }
    catch (error) { if (error.reason === "left") throw error; }
  }
  drawModes();
  render(await call("open", {}), { bottom: true });
  if (scrolls.has(chat)) log.scrollTop = scrolls.get(chat);  // back where the person left it
  const wanted = wantedTurn(ctx.query);  // ?turn=<number>: a line the palette's search found
  if (wanted !== null) showTurn(rows, wanted);
  loadEntry();
  return leave;
}
