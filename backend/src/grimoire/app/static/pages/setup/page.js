// Library setup (108): the first-run workflow, in four steps. Before a library is open, step 1 makes one, or
// opens one, at a place the person types; the app refuses any place inside, around or beside a V1 library before
// anything is written. Once one is open, step 1 says which library it is and how to open it next time, and the
// other steps lead to a first chat with the library's own operations: add a first card (the import's reviewed
// write), name who you play (a character of your own), and begin. A step whose operations this app does not
// serve is shown as a link to the page that does it. Every value shown is only ever set as text.

import { kindOf, shortlist, sourceLabel, unpinned } from "../start/page.js";

const STEPS = [["folder", "Library folder"], ["card", "First card"], ["persona", "Who you play"], ["begin", "Begin"]];

const NEXT = [
  ["start", "Start a chat", "From a character card, a scenario or a premise."],
  ["import", "Import", "A character card, a lorebook or a V1 library, previewed before anything is written."],
  ["providers", "Set up a model", "A hosted or local model endpoint, and what each call may cost."],
  ["chats", "Your chats", "Chats this library already has, synced from another device or made here."],
];

// What each place the app can find says, and the one action it allows.
const PLACES = {
  new: ["A new library will be made in this folder.", "create"],
  empty: ["This folder is empty: a new library can be made in it.", "create"],
  library: ["A V2 library is already here: it can be opened.", "open"],
  invalid: ["This folder has a library marker this app cannot open (another version, or damaged), so nothing is made "
    + "or opened here. Pick another folder.", null],
  occupied: ["This folder already holds other things, so no library is made here. Pick a new or empty folder.", null],
};

// Where a character made here is kept: a source of the person's own, made the first time it is needed.
const MINE = { source: "local:mine", title: "My characters" };
const MAX_FILE = 34 * 1024 * 1024;  // the app takes a request of 48 MiB, base64 included
const ACTIONS = { create: "New file", replace: "Replaces a file", unchanged: "Unchanged", write: "Written" };

async function base64Of(file) {
  if (file.size > MAX_FILE) {
    throw Object.assign(new Error(file.name + " is larger than this app takes in one request"), { reason: "too_large" });
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("reading");
  const status = await ctx.api("setup.status", {});
  const asked = ctx.query.get("step");
  const step = status.open && STEPS.some(([name]) => name === asked) ? asked : "folder";
  // Library setup is where an open library says which it is; the steps to a first chat read as the welcome they are.
  if (status.open && step === "folder") { ctx.setCrumb("System"); ctx.setTitle("Library setup"); }
  else { ctx.setCrumb("First run"); ctx.setTitle("Welcome to Grimoire"); }

  const body = el("div", { class: "setup-body" });
  root.append(el("div", { class: "setup-page" }, stepper(ctx, status, step), body));
  const view = { ctx, status, body, go: (name) => ctx.navigate(ctx.href("setup", [], { step: name })) };
  if (step === "folder") { if (status.open) opened(view); else folder(view); }
  else if (step === "card") await firstCard(view);
  else if (step === "persona") await whoYouPlay(view);
  else await begin(view);
}

// The four steps. Before a library is open only the first can be worked on; after, each is a link.
function stepper(ctx, status, step) {
  const { el } = ctx.ui;
  return el("ol", { class: "setup-steps", "aria-label": "Setup steps" }, STEPS.map(([name, label], index) => {
    const inner = [el("span", { class: "setup-num" }, String(index + 1)), el("span", { class: "setup-step-label" }, label)];
    const current = name === step;
    const node = status.open
      ? el("a", { class: "setup-step", href: ctx.href("setup", [], name === "folder" ? undefined : { step: name }),
        "aria-current": current ? "step" : null, "data-focus-key": "setup:step:" + name }, inner)
      : el("span", { class: "setup-step", "aria-current": current ? "step" : null,
        title: current ? null : "Waits on a library" }, inner);
    return el("li", {}, node);
  }));
}

function intro(ctx, title, text) {
  const { el } = ctx.ui;
  return el("div", { class: "setup-intro" }, el("h2", { class: "g-hero-title" }, title), text ? el("p", { class: "g-muted" }, text) : null);
}

// A shell card with one of this page's own classes.
function box(ui, name, options, ...children) {
  const node = ui.card(options, ...children);
  node.classList.add(name);
  return node;
}

// A step this app cannot do here: said plainly, with the page that does it.
function elsewhere(view, what, route, label) {
  const { ctx } = view;
  const { ui } = ctx;
  return ui.card({}, ui.el("p", { class: "setup-plain" }, what),
    ctx.registered(route) ? ui.actions(ui.buttonLink(label, route, [], undefined, { kind: "primary" })) : null);
}

// -- step 1, before a library is open: check a folder, then make or open the library there ---------------------
function folder(view) {
  const { ctx, status, body } = view;
  const { ui } = ctx;
  const { el } = ui;
  const path = ui.field("Folder", { value: status.suggested || "", draft: ctx.draft("path"),
    hint: "The folder's full path. A new folder is made in a folder that exists already." });
  path.input.classList.add("setup-path");
  const device = ui.field("This device's name", { draft: ctx.draft("device"), placeholder: "desktop",
    hint: "Such as desktop or phone: how your other devices know this one when they sync. A library that already "
      + "names this device keeps its name, so leave this empty to open one." });
  const found = el("div", { class: "setup-found", "aria-live": "polite" });
  const wait = () => el("span", { class: "g-muted setup-small" }, "Check the folder first; nothing is written until you say so.");
  const action = el("div", { class: "setup-action" }, wait());
  const model = status.generation
    ? ui.notice("A model is set up for this app.")
    : ui.notice("No model is set up: chats work, but nothing answers in them until one is. Start the app with "
      + "--model FILE to set one up" + (ctx.registered("providers") ? ", or set up a provider once the library is open." : "."));

  let checked = null;  // { path, state } the server last answered for exactly what is in the box
  const forget = () => { checked = null; found.replaceChildren(); action.replaceChildren(wait()); path.setError(""); };
  path.input.addEventListener("input", forget);

  // The box may change while a check is out: its answer then belongs to a folder no longer typed, and is dropped.
  const stale = (typed) => path.value.trim() !== typed;

  async function check() {
    const typed = path.value.trim();
    if (!typed) { path.setError("Type the folder's full path."); return; }
    let answer;
    try { answer = await ctx.api("setup.check", { path: typed }); }
    catch (error) {
      if (error.reason === "left") throw error;
      if (stale(typed)) return;
      forget(); path.setError(ui.errorText(error)); return;
    }
    if (stale(typed)) return;
    checked = { typed, ...answer };
    path.setError("");
    const [says, does] = PLACES[checked.state] || ["This place cannot hold a library.", null];
    const notice = ui.notice("", { kind: does ? "soft" : "bad" });
    notice.classList.add("setup-check");
    notice.replaceChildren(el("span", {}, checked.path + ": " + says));
    if (does) notice.prepend(el("span", { class: "g-dot g-good" }));
    found.replaceChildren(notice);
    const buttons = [];
    if (does === "create" && ctx.has("setup.create")) {
      buttons.push(ui.button("Make the library here", () => ctx.act(make), { kind: "primary", size: "large" }));
    }
    if (does === "open" && ctx.has("setup.open")) {
      buttons.push(ui.button("Open this library", () => ctx.act(openIt), { kind: "primary", size: "large" }));
    }
    action.replaceChildren(...(buttons.length ? buttons : [el("span", { class: "g-muted setup-small" }, "Nothing is written here.")]));
  }

  // An action acts on the folder checked, and only while that is still what the box says.
  function still() {
    if (checked && !stale(checked.typed)) return true;
    forget();
    path.setError("Check this folder again: it changed.");
    return false;
  }

  async function make() {
    if (!still()) return;
    if (!device.value.trim()) { device.setError("Name this device first."); device.input.focus(); return; }
    device.setError("");
    await done(await ctx.api("setup.create", { path: checked.path, device: device.value.trim() }), "card");
  }

  async function openIt() {
    if (!still()) return;
    const name = device.value.trim();
    await done(await ctx.api("setup.open", name ? { path: checked.path, device: name } : { path: checked.path }));
  }

  // A new library goes on to its first card; one that was opened says which library it is.
  async function done(answer, next) {
    ctx.draft("path").clear();
    ctx.draft("device").clear();
    ctx.notify("Opened " + answer.library + ".");
    await ctx.restart(ctx.href("setup", [], next ? { step: next } : undefined));
  }

  const checkButton = ui.button("Check", () => ctx.act(check), { title: "Check this folder" });
  checkButton.setAttribute("data-focus-key", "setup:check");
  path.input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); ctx.act(check); } });
  const row = el("div", { class: "setup-folder-row" });
  path.input.replaceWith(row);
  row.append(path.input, checkButton);

  body.append(intro(ctx, "Where should your library live?", "Everything you write and play is kept as files in one folder on "
    + "this device. A V1 library is never opened or changed here: a place inside, around or beside one is refused before "
    + "anything is made."),
  box(ui, "setup-card", { tag: "section", label: "Library folder" }, path.node, found, device.node, action), model);
}

// A path as one word a POSIX shell reads back unchanged: quoted only when it needs to be.
function shellWord(path) {
  return /^[\w@%+=:,./-]+$/.test(path) ? path : "'" + path.replaceAll("'", "'\\''") + "'";
}

// -- step 1, once a library is open: which one, and how to open it next time -------------------------------------
function opened(view) {
  const { ctx, status, body } = view;
  const { ui } = ctx;
  const { el } = ui;
  body.append(intro(ctx, "Your library is open"),
    box(ui, "setup-card", { tag: "section" }, el("h2", { class: "g-heading" }, "This library"),
      el("dl", { class: "setup-facts" },
        el("dt", {}, "Folder"), el("dd", { class: "g-mono" }, status.library || "(given when the app started)"),
        el("dt", {}, "Device"), el("dd", {}, status.device || "not named (sync needs a name: run identify)"),
        el("dt", {}, "Owner"), el("dd", {}, status.owner),
        el("dt", {}, "Model"), el("dd", {}, status.generation ? "set up" : "none: start the app with --model FILE")),
      status.library
        ? el("p", { class: "setup-plain" }, "To open this library next time, start the app with: ",
          el("code", { class: "g-mono" }, "python -m grimoire.app serve " + shellWord(status.library)))
        : null,
      el("p", { class: "g-muted setup-plain" }, "To use another library, stop the app and start it on that folder."),
      ui.actions(ui.buttonLink("Next: a first card", "setup", [], { step: "card" }, { kind: "primary" }))));
  const rows = NEXT.map(([route, title, detail]) => ctx.registered(route)
    ? el("a", { href: ctx.href(route) }, ui.rowText(title, detail))
    : el("div", { class: "g-unavailable" }, ui.rowText(title + " (not in this app yet)", detail)));
  body.append(ui.section("Or go straight to", ui.list(rows)));
}

// What the next steps carry from one to the next: kept per tab, like any draft, until the first chat begins.
function picks(ctx) {
  return { card: ctx.draft("first:card"), premise: ctx.draft("first:premise"), persona: ctx.draft("first:persona") };
}

// The library's cards, and what was picked so far if it is still there.
async function cast(ctx) {
  const sources = await ctx.api("play.sources", {});
  const kept = picks(ctx);
  const cards = [...sources.characters, ...sources.scenarios];
  const find = (items, ref) => items.find((item) => item.ref === ref) || null;
  const premise = kept.card.get() === "premise" && (kept.premise.get() || "").trim() ? kept.premise.get() : null;
  return { sources, cards, premise, card: premise ? null : find(cards, kept.card.get()), persona: find(sources.characters, kept.persona.get()) };
}

function tile(ui, item, onclick) {
  const { el } = ui;
  return el("button", { type: "button", class: "g-select setup-pick", title: item.ref, "data-ref": item.ref, "aria-pressed": "false", onclick },
    ui.portrait(item.name, { key: item.ref }),
    el("span", { class: "setup-pick-text" }, el("span", { class: "setup-pick-name" }, item.name),
      el("span", { class: "setup-pick-source" }, kindOf(item.ref) === "scenario" ? "Scenario" : sourceLabel(item.ref))));
}

// -- step 2: a first card ------------------------------------------------------------------------------------
async function firstCard(view) {
  const { ctx, body, go } = view;
  const { ui } = ctx;
  const { el } = ui;
  if (!ctx.has("play.sources")) {
    body.append(intro(ctx, "Who do you want to meet first?"),
      elsewhere(view, "This app adds a first card on its Import page.", "import", "Open Import"));
    return;
  }
  const kept = picks(ctx);
  const found = await cast(ctx);
  const choose = (ref) => { kept.card.set(ref); go("persona"); };
  const imports = ctx.has("imports.card_preview") && ctx.has("imports.proposal_apply");
  body.append(intro(ctx, "Who do you want to meet first?", imports
    ? "Add a character card: a Character Card V3 JSON file. Every file it would write is shown before anything is written."
    : "Pick a card this library already has, or start from a premise."));

  if (found.cards.length) {
    const { shown } = shortlist(found.cards, "", found.card && found.card.ref);
    const tiles = el("div", { class: "setup-picks", role: "group", "aria-label": "Cards in this library" },
      shown.map((item) => {
        const node = tile(ui, item, () => choose(item.ref));
        node.setAttribute("aria-pressed", String(Boolean(found.card && found.card.ref === item.ref)));
        return node;
      }));
    body.append(el("section", { class: "g-stack" }, el("h3", { class: "g-heading" }, "Already in this library"), tiles,
      found.cards.length > shown.length
        ? el("p", { class: "g-muted setup-plain" }, "Showing " + shown.length + " of " + found.cards.length + ". ",
          ctx.registered("start") ? ui.link("Search them all in New chat", "start") : null)
        : null));
  }

  const zone = el("div", { class: "setup-zone", "aria-live": "polite" });
  if (imports) { body.append(zone); dropZone(); }
  else if (ctx.registered("import")) body.append(elsewhere(view, "This app adds a card on its Import page.", "import", "Open Import"));

  function dropZone() {
    const input = el("input", { type: "file", accept: ".json,application/json", hidden: true, "aria-label": "Card file" });
    input.addEventListener("change", () => { const file = input.files[0]; if (file) ctx.act(() => preview(file)); });
    const drop = box(ui, "setup-drop", { kind: "dashed" }, el("span", { class: "setup-drop-title" }, "Drop a card here"),
      ui.actions(ui.button("Choose a file", () => input.click())), input,
      ctx.registered("import")
        ? el("p", { class: "g-muted setup-small" }, "A portrait, a separate lorebook or a scenario card: ", ui.link("use Import", "import"), ".")
        : null);
    drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("setup-over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("setup-over"));
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      drop.classList.remove("setup-over");
      const file = event.dataTransfer && event.dataTransfer.files[0];
      if (file) ctx.act(() => preview(file));
    });
    zone.replaceChildren(drop);
  }

  // The import's own preview: nothing is written until the exact bytes shown are approved by their digest.
  async function preview(file) {
    const proposal = await ctx.api("imports.card_preview", { card: await base64Of(file), origin: file.name });
    const report = proposal.report || {};
    const count = proposal.writes + (proposal.writes === 1 ? " file" : " files");
    const losses = Array.isArray(report.losses) ? report.losses : (report.losses && report.losses.rows) || [];
    const facts = [["Becomes", report.kind], ["Kept in", report.source], ["Status", report.status]].filter(([, value]) => value);
    const write = ui.button(proposal.writes ? "Add this card: write " + count : "Use this card", () => ctx.act(approve),
      { kind: "primary", size: "large" });
    zone.replaceChildren(box(ui, "setup-card", { tag: "section", label: "Review before anything is written" },
      el("h3", { class: "g-heading" }, file.name),
      el("dl", { class: "setup-facts" }, facts.flatMap(([key, value]) => [el("dt", {}, key), el("dd", {}, String(value))])),
      el("details", { class: "setup-files" }, el("summary", {}, proposal.writes ? count + " would be written" : "Nothing to write: the library already holds exactly this"),
        proposal.files.map((item) => el("details", { class: "setup-file" },
          el("summary", {}, el("span", { class: "g-mono" }, item.path), " ", ui.pill(ACTIONS[item.action] || String(item.action)), " ",
            el("span", { class: "g-muted" }, item.size + " bytes")),
          el("p", { class: "g-muted g-mono setup-hash" }, "sha256 " + item.sha256),
          item.diff !== null && item.diff !== undefined ? el("pre", { class: "setup-pre" }, item.diff)
            : item.text !== null && item.text !== undefined ? el("pre", { class: "setup-pre" }, item.text)
              : el("p", { class: "g-muted" }, "Shown by its size and hash only.")))),
      losses.length ? el("details", { class: "setup-files" }, el("summary", {}, "Not carried over (" + losses.length + ")"),
        el("ul", {}, losses.map((row) => el("li", {}, typeof row === "object" ? JSON.stringify(row) : String(row))))) : null,
      el("p", { class: "g-muted g-mono setup-hash" }, "Digest " + proposal.digest),
      ui.actions(write, ui.button("Reject", () => ctx.act(reject)))));

    async function approve() {
      let done;
      try {
        done = await ctx.api("imports.proposal_apply", { proposal: proposal.proposal, digest: proposal.digest });
      } catch (error) {
        // a stale or conflicting approval ended this proposal before anything was written: preview again
        if (["stale", "stale_approval", "conflict"].includes(error.reason)) {
          dropZone();
          ctx.notify(ui.errorText(error) + " Nothing was written; choose the file again.", { bad: true });
          return;
        }
        // the server no longer holds this proposal: an approval whose answer was lost may have written it already
        if (error.reason === "no_proposal") {
          ctx.notify("This proposal is no longer held here, so it may already have been written (or the app restarted). "
            + "Check the library before you add it again.", { bad: true });
          go("card");
          return;
        }
        throw error;
      }
      ctx.notify(proposal.writes ? "Written." : "Nothing needed writing.");
      const main = unpinned(done.result && done.result.main);
      const now = await ctx.api("play.sources", {});
      if ([...now.characters, ...now.scenarios].some((item) => item.ref === main)) choose(main);
      else go("card");  // a lorebook, or a card the library cannot play: pick who to meet from what is here
    }

    async function reject() {
      if (ctx.has("imports.proposal_reject")) await ctx.api("imports.proposal_reject", { proposal: proposal.proposal });
      dropZone();
      ctx.notify("Rejected: nothing was written.");
    }
  }

  // No card: a one-line premise is enough to play.
  const premise = ui.field("Premise", { draft: kept.premise, multiline: true, hint: "What the chat is about. {{user}} is you." });
  premise.input.setAttribute("maxlength", "4000");
  const form = el("div", { class: "g-stack", hidden: !found.premise }, premise.node, ui.actions(ui.button("Continue with this premise", () => {
    if (!premise.value.trim()) { premise.setError("Write a premise first."); premise.input.focus(); return; }
    choose("premise");
  }, { kind: "primary" })));
  const toggle = ui.button("No card yet: start from a one-line premise instead", () => {
    form.hidden = !form.hidden;
    toggle.setAttribute("aria-expanded", String(!form.hidden));
    if (!form.hidden) premise.input.focus();
  }, { kind: "linklike" });
  toggle.classList.add("setup-premise-toggle");
  toggle.setAttribute("aria-expanded", String(!form.hidden));
  body.append(toggle, form);
}

// -- step 3: who you play ------------------------------------------------------------------------------------
async function whoYouPlay(view) {
  const { ctx, body, go } = view;
  const { ui } = ctx;
  const { el } = ui;
  const title = "And who are you in this story?";
  if (!ctx.has("play.sources")) {
    body.append(intro(ctx, title), elsewhere(view, "This app makes the character you play in Characters and lore.", "sources", "Open Characters and lore"));
    return;
  }
  const kept = picks(ctx);
  const found = await cast(ctx);
  const choose = (ref) => { kept.persona.set(ref); go("begin"); };
  const creates = ["sources.catalog", "sources.create_source", "sources.create"].every((op) => ctx.has(op));
  body.append(intro(ctx, title, creates ? "A name is enough. You can flesh them out later, or use any card as yourself."
    : "Use any card as yourself."));

  if (creates) {
    const name = ui.field("Your character's name", { draft: ctx.draft("first:name") });
    name.input.classList.add("setup-name");
    name.input.setAttribute("maxlength", "200");
    const line = ui.field("In a line", { draft: ctx.draft("first:line"), multiline: true, hint: "Optional." });
    line.input.classList.add("setup-line");
    const make = ui.button("Continue", () => ctx.act(async () => {
      if (!name.value.trim()) { name.setError("Name who you play first."); name.input.focus(); return; }
      name.setError("");
      // the character is kept in a source of the person's own, made the first time
      const catalog = await ctx.api("sources.catalog", { source: MINE.source, limit: 1 });
      const mine = catalog.sources.find((source) => source.source === MINE.source);
      const expected = mine ? mine.etag : (await ctx.api("sources.create_source", MINE)).manifest_etag;
      const fields = { name: name.value.trim() };
      if (line.value.trim()) fields.description = line.value.trim();
      const answer = await ctx.api("sources.create", { source: MINE.source, kind: "character", fields, expected });
      name.clear(); line.clear();
      choose(unpinned(answer.ref));
    }), { kind: "primary", size: "large" });
    body.append(box(ui, "setup-card", { tag: "section", label: "A new character" }, name.node, line.node,
      el("span", { class: "g-muted setup-small" }, "Kept in this library under “" + MINE.title + "”."), make));
  } else if (ctx.registered("sources")) {
    body.append(elsewhere(view, "This app makes a new character in Characters and lore.", "sources", "Open Characters and lore"));
  }

  const others = found.sources.characters.filter((item) => !found.card || item.ref !== found.card.ref);
  if (others.length) {
    const { shown } = shortlist(others, "", found.persona && found.persona.ref);
    body.append(el("section", { class: "g-stack" }, el("h3", { class: "g-heading" }, "Or play a card you already have"),
      el("div", { class: "g-chips", role: "group", "aria-label": "You play" }, shown.map((item) =>
        ui.selectable([ui.avatar(item.name, { size: 22, key: item.ref }), el("span", {}, item.name)], {
          chip: true, selected: Boolean(found.persona && found.persona.ref === item.ref), title: item.ref, onclick: () => choose(item.ref) }))),
      others.length > shown.length
        ? el("p", { class: "g-muted setup-plain" }, "Showing " + shown.length + " of " + others.length + ". ",
          ctx.registered("start") ? ui.link("Search them all in New chat", "start") : null)
        : null));
  }
}

// -- step 4: begin -------------------------------------------------------------------------------------------
async function begin(view) {
  const { ctx, body } = view;
  const { ui } = ctx;
  const { el } = ui;
  if (!["play.sources", "play.options", "play.start"].every((op) => ctx.has(op))) {
    body.append(intro(ctx, "Begin"), elsewhere(view, "This app starts a chat on its New chat page.", "start", "Open New chat"));
    return;
  }
  const kept = picks(ctx);
  const found = await cast(ctx);
  if (!found.persona || !(found.card || found.premise)) {
    body.append(intro(ctx, "Begin", "Two things are needed first."), ui.card({},
      el("p", { class: "setup-plain" }, found.card || found.premise ? "Choose who you play." : "Choose a first card, or write a premise."),
      ui.actions(found.card || found.premise ? ui.buttonLink("Who you play", "setup", [], { step: "persona" }, { kind: "primary" })
        : ui.buttonLink("First card", "setup", [], { step: "card" }, { kind: "primary" }))));
    return;
  }
  const you = found.persona;
  const chosen = found.premise ? { kind: "premise", premise: found.premise }
    : { kind: kindOf(found.card.ref) === "scenario" ? "scenario" : "character", ref: found.card.ref };
  const faces = el("div", { class: "setup-pair" }, found.card ? ui.portrait(found.card.name, { key: found.card.ref }) : null,
    ui.portrait(you.name, { key: you.ref }));
  body.append(el("div", { class: "setup-meet" }, faces,
    el("h2", { class: "g-hero-title setup-meet-title" }, found.premise ? you.name + " in a new premise" : you.name + " meets " + found.card.name)));

  const opens = el("div", { class: "g-stack setup-openings", "aria-live": "polite" }, ui.empty("Loading…"));
  body.append(opens);
  let opening = "";
  let ready = false;
  const go = ui.button("Begin", () => ctx.act(async () => {
    if (!ready) return;
    const args = { ...chosen, persona: you.ref };
    if (opening) args.opening = opening;
    const scene = await ctx.api("play.start", args);
    for (const draft of Object.values(kept)) draft.clear();
    // a start that stopped before it finished is finished from the chat, which is told so
    ctx.navigate(ctx.href("chat", [scene.chat], scene.unfinished ? { unfinished: scene.unfinished } : undefined));
  }), { kind: "primary", size: "large" });
  go.classList.add("setup-go");
  go.setAttribute("data-focus-key", "setup:begin");
  body.append(go);
  if (!(ctx.library && ctx.library.generation)) {
    body.append(el("div", { class: "setup-model", role: "note" }, el("span", { class: "g-dot" }),
      el("div", {}, el("div", { class: "setup-model-title" }, "No model is set up yet"),
        el("div", { class: "g-muted" }, "You can post, but nobody answers until one is. ",
          ctx.registered("providers") ? ui.link("Set one up now", "providers") : "Start the app with --model FILE to set one up."))));
  }

  let options;
  try { options = await ctx.api("play.options", chosen); }
  catch (error) {
    if (error.reason === "left") throw error;
    opens.replaceChildren(ui.errorBox(error));
    return;
  }
  ready = true;
  if (!options.openings.length) {
    opens.replaceChildren(found.premise ? el("blockquote", { class: "g-quote" }, found.premise) : null,
      el("p", { class: "g-muted setup-plain" }, found.premise ? "The premise opens the chat." : "Nothing to open with: you speak first."));
    return;
  }
  opening = options.default_opening || "";
  const cards = options.openings.map((item) => {
    const node = ui.selectable([el("span", { class: "setup-opening-text" }, item.text), item.group_only ? ui.pill("group greeting") : null], {
      selected: item.id === opening,
      onclick: () => { opening = item.id; for (const [id, other] of cards) other.setAttribute("aria-pressed", String(id === opening)); },
    });
    node.classList.add("setup-opening");
    return [item.id, node];
  });
  opens.replaceChildren(el("span", { class: "setup-label" }, "How does it open?"), ...cards.map(([, node]) => node));
}
