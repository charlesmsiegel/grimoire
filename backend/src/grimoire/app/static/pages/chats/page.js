// Chats (Sprint 111): recent chats, every chat, or the chats of one character or scenario, and the way to Start
// Chat. The view lives in the address (#/chats?filter=all, #/chats?filter=character&source=<SourceRef>), so a
// deep link and Back both land on the same list. Every value shown comes from the library and is set as text.

const PAGE = 50, MAX_LIST = 1000;  // chats shown at first, and the most one list request returns
const VIEWS = [["recent", "Recent"], ["all", "All"], ["character", "By character"], ["scenario", "By scenario"]];

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const view = VIEWS.some(([name]) => name === ctx.query.get("filter")) ? ctx.query.get("filter") : "recent";
  const listed = view === "recent" || view === "all";
  const source = listed ? null : ctx.query.get("source");
  const asked = Number.parseInt(ctx.query.get("limit") || "", 10);
  // All is every chat, newest activity first, as far as one list request goes
  const limit = view === "all" ? MAX_LIST : Number.isInteger(asked) && asked >= 1 ? Math.min(asked, MAX_LIST) : PAGE;
  const address = (changes) => {
    const query = { filter: view, source, limit: limit === PAGE || view === "all" ? undefined : limit, ...changes };
    if (query.filter === "recent") delete query.filter;
    if (query.filter === "recent" || query.filter === "all" || query.filter === undefined) delete query.source;
    return ctx.href("chats", [], query);
  };

  ctx.setTitle("Chats");
  const actions = [];
  if (ctx.registered("start") && ctx.has("play.start")) {
    actions.push(el("a", { class: "g-button g-primary", href: ctx.href("start") }, "Start Chat"));
  }
  if (ctx.has("sync")) actions.push(syncButton(ctx));
  ctx.setActions(...actions);
  const tabs = el("div", { class: "g-actions", role: "group", "aria-label": "Which chats" });
  for (const [name, label] of VIEWS) {
    const link = el("a", { class: "g-button", href: address({ filter: name, source: undefined, limit: undefined }) }, label);
    if (name === view) link.setAttribute("aria-current", "page");
    tabs.append(link);
  }
  root.append(tabs);

  const recent = await ctx.api("chats", { filter: "recent", limit: listed ? limit : PAGE });
  let chats = recent.chats;
  if (!listed) {
    const options = [{ value: "", label: "Choose a " + view }];
    for (const item of recent.sources.filter((entry) => entry.role === view)) options.push({ value: item.ref, label: item.name || item.ref });
    // A linked source the list does not name (not bound by any chat, or not in this library) is still the one
    // shown: the picker says its exact ref rather than silently showing another.
    if (source && !options.some((option) => option.value === source)) options.push({ value: source, label: source });
    const picker = ui.choice(view === "character" ? "Character" : "Scenario", options, {
      value: source || "", onchange: (value) => ctx.navigate(address({ source: value || undefined, limit: undefined })),
    });
    root.append(picker.node);
    chats = source ? (await ctx.api("chats", { filter: view, source, limit })).chats : [];
  }
  const rows = chats.map((chat) => chatRow(ctx, chat));
  const none = listed ? "No chats yet. Start one, or sync with your other device."
    : source ? "No chats here." : "Choose a " + view + " to see its chats.";
  root.append(ui.list(rows, { empty: none }));
  // a full page may have more behind it: ask for a longer list, up to the API's largest
  if (chats.length >= limit && limit < MAX_LIST) {
    root.append(el("a", { class: "g-button", href: address({ limit: Math.min(limit * 4, MAX_LIST) }) }, "Show more"));
  }
}

// One chat as a row: its label (or what it was started from), where it is, who is in it and any problem.
export function chatRow(ctx, chat) {
  const { el, rowText } = ctx.ui;
  const where = chat.scope && chat.scope.world ? chat.scope.title || chat.scope.world : "No world";
  const names = (chat.sources || []).filter((item) => item.role !== "persona").map((item) => item.name || item.ref).join(", ");
  const title = chat.label || (chat.launch && chat.launch.name)
    || (chat.launch && chat.launch.kind === "premise" ? "Premise chat" : chat.chat);
  return el("a", { href: ctx.href("chat", [chat.chat]) }, rowText(title, [where, names, chat.problem].filter(Boolean).join(" · ")));
}

function syncButton(ctx) {
  return ctx.ui.button("Sync", () => ctx.act(async () => {
    const peers = (ctx.library && ctx.library.peers) || [];
    if (!peers.length) { ctx.notify("No paired device is configured for this app.", { bad: true }); return; }
    for (const peer of peers) {
      const report = await ctx.api("sync", { peer: peer.peer });
      ctx.notify(peer.peer + ": " + report.status + (report.reason ? " (" + report.reason + ")" : "")
        + (report.conflicts.length ? ", " + report.conflicts.length + " in conflict" : ""), { bad: report.status !== "synced" });
    }
    await ctx.refreshLibrary();
    ctx.navigate(window.location.hash);
  }));
}
