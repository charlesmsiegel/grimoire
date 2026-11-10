// Library setup (108): the first-run workflow. Before a library is open it makes one, or opens one, at a place
// the person types; the app refuses any place inside, around or beside a V1 library before anything is written.
// Once one is open it says which, whether a model is set up, and where to go next: Start Chat or Import, each
// shown as not available until its sprint has added it.

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

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.setTitle("Library setup");
  const status = await ctx.api("setup.status", {});
  if (status.open) { opened(root, ctx, status); return; }

  root.append(el("p", {}, "Choose where this device keeps its library. A V1 library is never opened or written "
    + "here: a place inside, around or beside one is refused before anything is made."));
  const path = ui.field("Folder", { value: status.suggested || "", draft: ctx.draft("path"),
    hint: "The folder's full path. A new folder is made in a folder that exists already." });
  const device = ui.field("This device's name", { draft: ctx.draft("device"),
    hint: "Such as desktop or phone: how your other devices know this one when they sync. A library that already "
      + "names this device keeps its name, so leave this empty to open one." });
  const found = el("div", { "aria-live": "polite" });
  const model = status.generation
    ? ui.notice("A model is set up for this app.")
    : ui.notice("No model is set up: chats work, but nothing answers in them until one is. Start the app with "
      + "--model FILE to set one up" + (ctx.registered("providers") ? ", or set up a provider once the library is open." : "."));

  let checked = null;  // { path, state } the server last answered for exactly what is in the box
  const forget = () => { checked = null; found.replaceChildren(); path.setError(""); };
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
      checked = null; found.replaceChildren(); path.setError(ui.errorText(error)); return;
    }
    if (stale(typed)) return;
    checked = { typed, ...answer };
    path.setError("");
    const [says, action] = PLACES[checked.state] || ["This place cannot hold a library.", null];
    const buttons = [];
    if (action === "create" && ctx.has("setup.create")) buttons.push(ui.button("Make the library here", () => ctx.act(make), { kind: "primary" }));
    if (action === "open" && ctx.has("setup.open")) buttons.push(ui.button("Open this library", () => ctx.act(openIt), { kind: "primary" }));
    found.replaceChildren(ui.notice(checked.path + ": " + says, { kind: action ? "" : "bad" }), ui.actions(...buttons));
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
    if (!device.value.trim()) { device.setError("Name this device first."); return; }
    device.setError("");
    await done(await ctx.api("setup.create", { path: checked.path, device: device.value.trim() }));
  }

  async function openIt() {
    if (!still()) return;
    const name = device.value.trim();
    await done(await ctx.api("setup.open", name ? { path: checked.path, device: name } : { path: checked.path }));
  }

  async function done(answer) {
    ctx.draft("path").clear();
    ctx.draft("device").clear();
    ctx.notify("Opened " + answer.library + ".");
    await ctx.restart(ctx.href("setup"));
  }

  root.append(ui.section("Where", path.node, device.node, ui.actions(ui.button("Check this folder", () => ctx.act(check))), found),
    ui.section("Model", model));
}

// A path as one word a POSIX shell reads back unchanged: quoted only when it needs to be.
function shellWord(path) {
  return /^[\w@%+=:,./-]+$/.test(path) ? path : "'" + path.replaceAll("'", "'\\''") + "'";
}

function opened(root, ctx, status) {
  const { ui } = ctx;
  const { el } = ui;
  root.append(ui.section("This library",
    el("dl", {},
      el("dt", {}, "Folder"), el("dd", {}, status.library || "(given when the app started)"),
      el("dt", {}, "Device"), el("dd", {}, status.device || "not named (sync needs a name: run identify)"),
      el("dt", {}, "Owner"), el("dd", {}, status.owner),
      el("dt", {}, "Model"), el("dd", {}, status.generation ? "set up" : "none: start the app with --model FILE")),
    status.library
      ? el("p", {}, "To open this library next time, start the app with: ",
        el("code", {}, "python -m grimoire.app serve " + shellWord(status.library)))
      : null,
    el("p", { class: "g-muted" }, "To use another library, stop the app and start it on that folder.")));
  const rows = NEXT.map(([route, title, detail]) => ctx.registered(route)
    ? el("a", { href: ctx.href(route) }, ui.rowText(title, detail))
    : el("div", { class: "g-unavailable" }, ui.rowText(title + " (not in this app yet)", detail)));
  root.append(ui.section("Next", ui.list(rows)));
}
