// Sprint 111: Chats, Start Chat and scene play, as self-contained pages over the text API.
//
// Every value shown comes from the library (card text, model output) and is only ever set as text, never
// as markup. Every action is one text API op; the page keeps no record of its own except the composer's
// unsent draft, which stays in this tab's sessionStorage until it is posted.

const DRAFT_PREFIX = "grimoire.draft.";
const PAGE_SIZE = 50;
const MAX_LIST = 1000;

// -- small DOM helpers --------------------------------------------------------------------------------------
export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "onclick") node.addEventListener("click", value);
    else if (key === "oninput") node.addEventListener("input", value);
    else if (key === "onchange") node.addEventListener("change", value);
    else if (key === "onkeydown") node.addEventListener("keydown", value);
    else if (key === "class") node.className = value;
    else if (key === "value") node.value = value;
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

// replaceChildren with the empty slots (null, false) left out: the DOM would write them as text
function fill(root, ...children) { root.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false)); }

function speakerOf(line) { return line.speaker || "Narrator"; }

function readDraft(key) { try { return sessionStorage.getItem(DRAFT_PREFIX + key) || ""; } catch (e) { return ""; } }
function writeDraft(key, text) {
  try { if (text) sessionStorage.setItem(DRAFT_PREFIX + key, text); else sessionStorage.removeItem(DRAFT_PREFIX + key); }
  catch (e) { /* a draft is a convenience: private browsing may refuse it */ }
}

// The shell gives each page `ctx`: `api(op, args)` (rejects with an Error carrying `.reason`),
// `image(ref)` (a blob URL or null), `go(route, params)`, `title(text)` and `notice(text, bad)`.
// `busy(work)` runs one action at a time and reports its refusal.
function runner(ctx) {
  let running = false;
  return async function busy(work) {
    if (running) return;
    running = true;
    const buttons = [...document.querySelectorAll("main button, main select, main textarea, main input")];
    for (const control of buttons) control.setAttribute("aria-disabled", "true");
    try { await work(); }
    catch (error) { ctx.notice((error.reason ? error.reason + ": " : "") + error.message, true); }
    finally {
      running = false;
      for (const control of buttons) control.removeAttribute("aria-disabled");
    }
  };
}

// -- Chats: Recent, by character, by scenario ---------------------------------------------------------------
export async function chatsPage(root, ctx, params) {
  const busy = runner(ctx);
  const view = ["recent", "all", "character", "scenario"].includes(params.view) ? params.view : "recent";
  // All is every chat, newest activity first, as far as one list request goes
  const limit = view === "all" ? MAX_LIST : Math.min(Math.max(Number(params.limit) || PAGE_SIZE, 1), MAX_LIST);
  ctx.title("Chats");
  const listed = view === "recent" || view === "all";
  const recent = await ctx.api("chats", { filter: "recent", limit: listed ? limit : PAGE_SIZE });
  let chats = recent.chats;
  const tabs = el("div", { class: "tabs", role: "tablist", "aria-label": "Which chats" });
  for (const [key, label] of [["recent", "Recent"], ["all", "All"], ["character", "By character"], ["scenario", "By scenario"]]) {
    tabs.append(el("button", { role: "tab", "aria-selected": String(view === key), onclick: () => ctx.go("chats", { view: key }) }, label));
  }
  const body = [el("div", { class: "row" }, el("button", { class: "primary", onclick: () => ctx.go("start", {}) }, "Start Chat")), tabs];
  if (!listed) {
    const picker = el("select", { "aria-label": "Choose a " + view, onchange: () => ctx.go("chats", { view, source: picker.value || undefined }) },
      el("option", { value: "" }, "Choose a " + view));
    for (const source of recent.sources.filter((item) => item.role === view)) {
      picker.append(el("option", { value: source.ref, selected: source.ref === params.source }, source.name || source.ref));
    }
    body.push(picker);
    chats = params.source ? (await ctx.api("chats", { filter: view, source: params.source, limit })).chats : [];
  }
  if (!chats.length) body.push(el("p", { class: "muted" }, listed ? "No chats yet. Start one, or sync with your other device." : params.source ? "No chats here." : ""));
  const list = el("ul", { class: "list" });
  for (const chat of chats) list.append(el("li", {}, chatButton(ctx, chat)));
  body.push(list);
  if (chats.length >= limit && limit < MAX_LIST) {
    body.push(el("button", { onclick: () => busy(() => ctx.go("chats", { ...params, limit: Math.min(limit * 4, MAX_LIST) })) }, "Show more"));
  }
  fill(root, body);
}

function chatButton(ctx, chat) {
  const where = chat.scope && chat.scope.world ? (chat.scope.title || chat.scope.world) : "No world";
  const names = (chat.sources || []).filter((s) => s.role !== "persona").map((s) => s.name || s.ref).join(", ");
  return el("button", { class: "item", onclick: () => ctx.go("chat", { chat: chat.chat }) },
    el("div", {}, chat.label || (chat.launch && chat.launch.name) || (chat.launch && chat.launch.kind === "premise" ? "Premise chat" : chat.chat)),
    el("div", { class: "muted" }, [where, names, chat.problem].filter(Boolean).join(" · ")));
}

// -- Start Chat -----------------------------------------------------------------------------------------------
const KINDS = [["character", "A character"], ["bundle", "A character with a lorebook"], ["scenario", "A scenario"], ["premise", "A premise"]];

export async function startPage(root, ctx, params) {
  const busy = runner(ctx);
  ctx.title("Start Chat");
  const sources = await ctx.api("start_sources", {});
  const form = { kind: KINDS.some(([k]) => k === params.kind) ? params.kind : "character", ref: params.ref || "",
                 lorebook: "", persona: readDraft("start.persona"), premise: readDraft("start.premise"), opening: "", label: "" };
  const detail = el("div", {});

  function pickFrom(kind) { return kind === "scenario" ? sources.scenarios : sources.characters; }

  async function refresh() {
    detail.replaceChildren();
    const selection = { kind: form.kind };
    if (form.kind === "premise") {
      if (!form.premise.trim()) { detail.append(el("p", { class: "muted" }, "Write a premise to start from.")); return; }
      selection.premise = form.premise;
    } else {
      if (!form.ref) { detail.append(el("p", { class: "muted" }, "Choose what to start from.")); return; }
      selection.ref = form.ref;
      if (form.kind === "bundle" && form.lorebook) selection.lorebook = form.lorebook;
    }
    let options;
    try { options = await ctx.api("start_options", selection); }
    catch (error) { detail.append(el("p", { class: "bad", role: "alert" }, (error.reason || "refused") + ": " + error.message)); return; }
    if (form.kind === "bundle") {
      detail.append(el("p", { class: "muted" }, options.lorebook ? "Its own lorebook is bound unless you choose another." : "This card has no lorebook of its own: choose one."));
    }
    if (options.openings.length) {
      const group = el("fieldset", {}, el("legend", {}, "Opening"));
      const chosen = form.opening || options.default_opening;
      for (const opening of options.openings) {
        const input = el("input", { type: "radio", name: "opening", value: opening.id, checked: opening.id === chosen,
                                    onchange: () => { form.opening = opening.id; } });
        group.append(el("label", { class: "choice" }, input, el("span", {}, opening.text),
                        opening.group_only ? el("span", { class: "muted" }, " (group greeting)") : null));
      }
      detail.append(group);
    } else {
      detail.append(el("p", { class: "muted" }, form.kind === "premise" ? "The premise opens the chat." : "Nothing to open with: you speak first."));
    }
    if (options.related.length) {
      detail.append(el("h2", {}, "Continue"), el("p", { class: "muted" }, "Chats already started from this. Continuing never makes a copy."));
      const list = el("ul", { class: "list" });
      for (const chat of options.related) list.append(el("li", {}, chatButton(ctx, chat)));
      detail.append(list);
    }
  }

  const kind = el("select", { id: "start-kind", onchange: () => { form.kind = kind.value; form.ref = ""; form.opening = ""; draw(); } });
  for (const [value, label] of KINDS) kind.append(el("option", { value, selected: value === form.kind }, label));

  const persona = el("select", { id: "start-persona", onchange: () => { form.persona = persona.value; writeDraft("start.persona", persona.value); } },
    el("option", { value: "" }, "Choose who you play"));
  for (const item of sources.characters) persona.append(el("option", { value: item.ref, selected: item.ref === form.persona }, item.name));

  const label = el("input", { id: "start-label", type: "text", maxlength: "200", placeholder: "Optional", oninput: () => { form.label = label.value; } });
  const pickers = el("div", {});

  function draw() {
    pickers.replaceChildren();
    if (form.kind === "premise") {
      const text = el("textarea", { id: "start-premise", maxlength: "4000", rows: "4", oninput: () => { form.premise = text.value; writeDraft("start.premise", text.value); } });
      text.value = form.premise;
      text.addEventListener("change", () => busy(refresh));
      pickers.append(el("label", { for: "start-premise" }, "Premise"), text);
    } else {
      const pick = el("select", { id: "start-ref", onchange: () => { form.ref = pick.value; form.opening = ""; busy(refresh); } },
        el("option", { value: "" }, form.kind === "scenario" ? "Choose a scenario" : "Choose a character"));
      for (const item of pickFrom(form.kind)) pick.append(el("option", { value: item.ref, selected: item.ref === form.ref }, item.name));
      pickers.append(el("label", { for: "start-ref" }, form.kind === "scenario" ? "Scenario" : "Character"), pick);
      if (form.kind === "bundle") {
        const lore = el("select", { id: "start-lorebook", onchange: () => { form.lorebook = lore.value; busy(refresh); } },
          el("option", { value: "" }, "The card's own lorebook"));
        for (const item of sources.lorebooks) lore.append(el("option", { value: item.ref, selected: item.ref === form.lorebook }, item.name));
        pickers.append(el("label", { for: "start-lorebook" }, "Lorebook"), lore);
      }
    }
    busy(refresh);
  }

  const start = el("button", { class: "primary", onclick: () => busy(async () => {
    if (!form.persona) { ctx.notice("Choose who you play first.", true); persona.focus(); return; }
    const args = { kind: form.kind, persona: form.persona };
    if (form.kind === "premise") args.premise = form.premise; else args.ref = form.ref;
    if (form.kind === "bundle" && form.lorebook) args.lorebook = form.lorebook;
    if (form.opening) args.opening = form.opening;
    if (form.label.trim()) args.label = form.label.trim();
    const scene = await ctx.api("start", args);
    writeDraft("start.premise", "");
    ctx.go("chat", { chat: scene.chat });
  }) }, "Start Chat");

  fill(root,
    el("p", { class: "muted" }, "No world is needed. Every source is bound at the revision it has now."),
    el("label", { for: "start-kind" }, "Start from"), kind, pickers,
    el("label", { for: "start-persona" }, "You play"), persona,
    sources.characters.length ? null : el("p", { class: "muted" }, "There is no character to play yet: import or create one first."),
    el("label", { for: "start-label" }, "Name"), label,
    detail, el("div", { class: "row" }, start));
  draw();
}

// -- one chat ---------------------------------------------------------------------------------------------------
export async function chatPage(root, ctx, params) {
  const busy = runner(ctx);
  const chat = params.chat;
  if (!chat) { ctx.go("chats", {}); return; }
  const library = await ctx.api("library", {});
  let pickable = null;  // start_sources, read once the first time a card is added
  const page = { scene: null, mode: "auto", redirecting: null, striking: null };

  async function act(op, args) { render(await ctx.api(op, { chat, ...args })); }
  async function answer(args) {
    // with no NPC who can speak, a director reply is asked only then (generate refuses nothing_to_generate first)
    try { return await act("generate", args || {}); }
    catch (error) { if (error.reason !== "nothing_to_generate" || (args && args.speakers)) throw error; }
    return act("generate", { director: true });
  }

  function render(scene) {
    page.scene = scene;
    ctx.title(scene.label || (scene.launch && scene.launch.kind === "premise" ? "Premise chat" : castNames(scene)) || "Chat");
    const out = [];
    out.push(castStrip(scene));
    out.push(...statusNotices(scene));
    out.push(transcript(scene));
    out.push(controls(scene));
    out.push(composer(scene));
    fill(root, out);
    const log = root.querySelector(".transcript");
    if (log) log.scrollTop = log.scrollHeight;
  }

  function castNames(scene) { return scene.cast.filter((m) => !m.persona).map((m) => m.name).join(", "); }

  function castStrip(scene) {
    const strip = el("ul", { class: "cast", "aria-label": "Cast" });
    for (const member of scene.cast) {
      const avatar = member.art.slots.find((slot) => slot.slot === "avatar");
      const face = el("div", { class: "face", "aria-hidden": "true" }, avatar && avatar.state === "unavailable" ? "?" : member.name.slice(0, 1));
      if (avatar && avatar.state === "shown") ctx.image(avatar.image).then((url) => { if (url) face.replaceWith(el("img", { class: "face", src: url, alt: "" })); });
      const role = member.persona ? "you" : member.role === "npc" ? "NPC" : member.role === "unseated" ? "not seated" : "player";
      strip.append(el("li", { class: "member" }, face, el("div", {}, member.name), el("div", { class: "muted" }, role)));
    }
    return el("section", { "aria-label": "Who is here" }, strip, addCard(scene));
  }

  function addCard(scene) {
    const box = el("details", {}, el("summary", {}, "Add a character or card"));
    box.addEventListener("toggle", () => busy(async () => {
      if (!box.open || box.dataset.ready) return;
      pickable = pickable || await ctx.api("start_sources", {});
      const bound = new Set(scene.cast.map((m) => m.name));
      const pick = el("select", { "aria-label": "Card to add" }, el("option", { value: "" }, "Choose"));
      for (const [role, items, title] of [["character", pickable.characters, "Characters"], ["lorebook", pickable.lorebooks, "Lorebooks"], ["scenario", pickable.scenarios, "Scenarios"]]) {
        const group = el("optgroup", { label: title });
        for (const item of items) group.append(el("option", { value: role + " " + item.ref }, item.name + (role === "character" && bound.has(item.name) ? " (already here)" : "")));
        pick.append(group);
      }
      box.append(pick, el("p", { class: "muted" }, "A newcomer hears only what is said from now on."),
        el("button", { onclick: () => busy(async () => {
          if (!pick.value) return;
          const [role, ref] = pick.value.split(" ");
          await act("add_card", { role, ref });
        }) }, "Add"));
      box.dataset.ready = "1";
    }));
    return box;
  }

  function statusNotices(scene) {
    const out = [];
    const waiting = scene.state.waiting;
    if (scene.unfinished) out.push(el("div", { class: "notice", role: "status" }, "Starting stopped (" + scene.unfinished + "). ",
      el("button", { onclick: () => busy(() => act("continue", {})) }, "Finish starting")));
    if (!scene.state.seated) out.push(el("div", { class: "notice" }, "This device has not taken its seat at this chat yet. ",
      el("button", { class: "primary", onclick: () => busy(() => act("continue", {})) }, "Take your seat")));
    if (waiting.includes("paused")) out.push(el("div", { class: "notice", role: "status" }, "Play is paused. ",
      el("button", { onclick: () => busy(() => act("resume", {})) }, "Resume")));
    if (waiting.includes("interrupted")) out.push(el("div", { class: "notice", role: "status" },
      "A reply was cut off before it finished. Ask again to replace it, or pass the turn."));
    if (scene.fork) {
      const box = el("div", { class: "notice", role: "status" }, "This chat forked: two devices played on. Choose the branch that goes on.");
      for (const branch of scene.fork.branches.filter((item) => !item.set_aside)) {
        box.append(el("button", { onclick: () => busy(() => act("choose", { turn: branch.head })) },
          "Keep the branch from " + (branch.device || "this device") + " (" + branch.turns.length + " turns)"));
      }
      out.push(box);
    }
    if (waiting.includes("conflicted") || scene.state.conflicts.length) out.push(el("div", { class: "notice", role: "status" },
      "Part of this chat is in conflict after a sync. Resolve it in Sync, with the other device reachable."));
    if (waiting.includes("turns_unreadable")) out.push(el("div", { class: "notice bad", role: "alert" }, "Some turns of this chat cannot be read; nothing is written until they can."));
    const broken = scene.problems.filter((p) => p.code === "broken_binding");
    const other = scene.problems.filter((p) => p.code !== "broken_binding");
    if (broken.length) {
      const box = el("div", { class: "notice" }, "A source this chat binds is gone. ");
      box.append(el("button", { onclick: () => busy(async () => {
        const found = await ctx.api("repairs", { chat });
        const list = el("ul", { class: "list" });
        for (const repair of found.repairs) {
          list.append(el("li", {}, repair.field + ": " + repair.ref + " (" + repair.reason + ")",
            repair.choices.includes("detach")
              ? el("button", { onclick: () => busy(() => act("detach", { field: repair.field, ref: repair.ref })) }, "Detach")
              : el("span", { class: "muted" }, " Re-import the card to bring it back.")));
        }
        fill(box, "A source this chat binds is gone. Its turns stay.", list);
      }) }, "See what is missing"));
      out.push(box);
    }
    if (other.length) out.push(el("details", { class: "notice" }, el("summary", {}, other.length + " note" + (other.length === 1 ? "" : "s") + " about this chat"),
      el("ul", {}, other.map((p) => el("li", {}, p.code + (p.detail ? ": " + p.detail : ""))))));
    return out;
  }

  function lineView(line, actions) {
    return el("div", { class: "line" }, el("span", { class: "speaker" }, speakerOf(line) + ": "), el("span", {}, line.text), actions || null);
  }

  function transcript(scene) {
    const log = el("section", { class: "transcript", "aria-label": "Transcript", "aria-live": "polite" });
    if (scene.history) {
      log.append(el("h2", { class: "muted" }, "Imported history"));
      for (const line of scene.history) log.append(lineView(line));
      log.append(el("hr", {}));
    }
    for (const turn of scene.turns) {
      if (turn.post) {
        const private_ = turn.post.private || turn.post.withheld;
        log.append(el("div", { class: "post" }, el("span", { class: "speaker" }, turn.post.speaker + (turn.post.to.length ? " to " + turn.post.to.join(", ") : "") + ": "),
          turn.post.text, private_ ? el("span", { class: "muted" }, turn.post.withheld ? " (only you know this)" : " (private)") : null));
      }
      if (turn.kept) for (const line of turn.kept.lines) log.append(lineView(line, keptActions(turn, line)));
      if (turn.passed && !turn.kept) log.append(el("div", { class: "muted" }, "Passed."));
      for (const take of turn.offered) log.append(takeView(scene, turn, take));
    }
    if (scene.set_aside.length) log.append(el("p", { class: "muted" }, scene.set_aside.length + " turn(s) set aside by a fork choice; nobody heard them."));
    if (!scene.turns.length && !scene.history) log.append(el("p", { class: "muted" }, "Nothing has been said yet. You speak first."));
    return log;
  }

  function keptActions(turn, line) {
    if (page.striking === line.line) {
      return el("span", { class: "inline" }, el("button", { class: "danger", onclick: () => busy(async () => { page.striking = null; await act("strike", { line: line.line }); }) }, "Strike it"),
        el("button", { onclick: () => { page.striking = null; render(page.scene); } }, "Cancel"));
    }
    return el("button", { class: "small", "aria-label": "Strike this line", onclick: () => { page.striking = line.line; render(page.scene); } }, "Strike");
  }

  function takeView(scene, turn, take) {
    const box = el("article", { class: "take", "aria-label": "Take " + take.take });
    const unscreened = scene.state.unscreened.includes(take.take);
    box.append(el("div", { class: "muted" }, "Take " + take.take + (take.state === "running" ? " (still being written)" : "") + (take.origin === "director" ? " · director" : "")));
    for (const line of take.lines) box.append(lineView(line, take.state === "proposed" ? lineRequests(line) : null));
    for (const call of take.calls) box.append(el("div", { class: "muted" }, callNote(scene, call)));
    if (!take.lines.length && take.state !== "running") box.append(el("div", { class: "muted" }, "No line to keep."));
    if (scene.state.keepable.includes(take.take)) {
      box.append(el("button", { class: "primary", onclick: () => busy(() => act("keep", { turn: turn.turn, take: take.take, acknowledge_unscreened: unscreened })) },
        unscreened ? "Keep (uses lore no screen covered)" : "Keep"));
    }
    return box;
  }

  function callNote(scene, call) {
    const who = (scene.cast.find((m) => m.actor.endsWith("/" + call.actor) || m.actor === call.actor) || {}).name || call.actor;
    const what = { refused: "the model refused", rejected: "its line was held back", failed: "the call failed", incomplete: "the reply was cut off",
                   cancelled: "cancelled", planned: "not run yet", not_issued: "not asked" }[call.status] || call.status;
    return who + ": " + what + (call.reason ? " (" + call.reason + ")" : "");
  }

  function lineRequests(line) {
    if (page.redirecting === line.line) {
      const direction = el("input", { type: "text", maxlength: "2000", "aria-label": "How should it change", placeholder: "How should it change?" });
      setTimeout(() => direction.focus());
      return el("span", { class: "inline" }, direction,
        el("button", { onclick: () => busy(async () => { const text = direction.value.trim(); page.redirecting = null; await act("withdraw", { kind: "redirect", lines: [line.line], ...(text ? { direction: text } : {}) }); }) }, "Redirect"),
        el("button", { onclick: () => { page.redirecting = null; render(page.scene); } }, "Cancel"));
    }
    return el("span", { class: "inline" },
      el("button", { class: "small", onclick: () => busy(() => act("withdraw", { kind: "skip", lines: [line.line] })) }, "Skip"),
      el("button", { class: "small", onclick: () => { page.redirecting = line.line; render(page.scene); } }, "Redirect"));
  }

  function controls(scene) {
    const bar = el("div", { class: "actions", role: "toolbar", "aria-label": "Play" });
    const blocked = !scene.state.seated || scene.state.waiting.some((w) => ["paused", "forked", "conflicted", "turns_unreadable"].includes(w));
    if (library.generation) {
      const npcs = scene.cast.filter((m) => m.role === "npc");
      bar.append(el("button", { disabled: blocked, onclick: () => busy(() => answer()) }, npcs.length ? "Answer" : "Ask the director"));
      if (npcs.length > 1) for (const npc of npcs) bar.append(el("button", { disabled: blocked, onclick: () => busy(() => act("generate", { speakers: [npc.actor] })) }, "Ask " + npc.name));
      if (npcs.length) bar.append(el("button", { disabled: blocked, onclick: () => busy(() => act("generate", { director: true })) }, "Ask the director"));
    } else {
      bar.append(el("span", { class: "muted" }, "No model is configured, so nobody can answer yet. Posts, keeps and passes still work."));
    }
    const open = scene.state.open_turn;
    if (open) bar.append(el("button", { disabled: scene.state.waiting.includes("paused"), onclick: () => busy(() => act("pass", { turn: open })) }, "Pass"));
    if (!scene.state.waiting.includes("paused") && scene.turns.length) bar.append(el("button", { onclick: () => busy(() => act("pause", {})) }, "Pause"));
    return bar;
  }

  function composer(scene) {
    const text = el("textarea", { id: "composer", rows: "3", maxlength: "20000", "aria-label": "Say or do something",
                                   placeholder: scene.persona ? "As " + scene.persona.name + ": say or do something" : "Say or do something" });
    text.value = readDraft(chat);
    text.addEventListener("input", () => writeDraft(chat, text.value));
    const mode = el("select", { "aria-label": "How to read it", onchange: () => { page.mode = mode.value; } });
    const labels = { auto: "Read it for me", speech: "Speech", action: "Action" };
    for (const value of library.post_modes || ["auto"]) mode.append(el("option", { value, selected: value === page.mode }, labels[value] || value));
    async function send() {
      const said = text.value.trim();
      if (!said) return;
      const before = page.scene.turns.length;
      const scene = await ctx.api("post", { chat, text: said, mode: page.mode });
      writeDraft(chat, "");  // saved as a turn: the draft is done, even if the answer below fails
      render(scene);
      // a typed table request ("Grimoire, skip") writes no new turn: nobody is asked to answer it
      const last = scene.turns[scene.turns.length - 1];
      const posted = scene.turns.length > before && last && last.post && last.open;
      if (library.generation && posted && !scene.state.waiting.length) await answer();
    }
    text.addEventListener("keydown", (event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); busy(send); } });
    const seated = scene.state.seated && !scene.state.waiting.some((w) => ["paused", "forked", "conflicted"].includes(w));
    // 035 takes a new post only once the last turn is closed: keep a take, or pass. A typed table request
    // ("Grimoire, skip") is still read while a turn is open, so the composer stays usable for it.
    const open = scene.state.open_turn !== null;
    return el("form", { class: "composer", onsubmit: (event) => { event.preventDefault(); } },
      text, el("div", { class: "row" }, mode, el("button", { type: "button", class: "primary", disabled: !seated || !scene.persona, onclick: () => busy(send) }, "Post")),
      scene.persona ? null : el("p", { class: "muted" }, "This chat has no character for you to play, so it takes no posts."),
      open && scene.persona ? el("p", { class: "muted" }, "Keep a take or pass this turn before you post again.") : null,
      el("p", { class: "muted" }, "Ctrl+Enter posts. Type “Grimoire, skip” or “redirect: kinder” to ask for the lines on show."));
  }

  render(await ctx.api("open", { chat }));
}

export const pages = { chats: chatsPage, start: startPage, chat: chatPage };
