// Chats (044's list, ported into the 108 shell; Sprint 111 owns it from here): recent chats, or the chats of
// one character or scenario. The filter lives in the address (#/chats?filter=character&source=<SourceRef>), so
// a deep link and Back both land on the same list.

const PAGE = 50, MAX_LIST = 1000;  // chats shown at first, and the most one list request returns
const VIEWS = [["recent", "Recent"], ["character", "By character"], ["scenario", "By scenario"]];

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const view = VIEWS.some(([name]) => name === ctx.query.get("filter")) ? ctx.query.get("filter") : "recent";
  const source = view === "recent" ? null : ctx.query.get("source");
  const asked = Number.parseInt(ctx.query.get("limit") || "", 10);
  const limit = Number.isInteger(asked) && asked >= 1 ? Math.min(asked, MAX_LIST) : PAGE;
  const address = (changes) => {
    const query = { filter: view, source, limit: limit === PAGE ? undefined : limit, ...changes };
    if (query.filter === "recent") { delete query.filter; delete query.source; }
    return ctx.href("chats", [], query);
  };

  ctx.setTitle("Chats");
  ctx.setActions(syncButton(ctx));
  const tabs = el("div", { class: "g-actions", role: "group", "aria-label": "Which chats" });
  for (const [name, label] of VIEWS) {
    const link = el("a", { class: "g-button", href: address({ filter: name, source: undefined, limit: undefined }) }, label);
    if (name === view) link.setAttribute("aria-current", "page");
    tabs.append(link);
  }
  root.append(tabs);

  const recent = await ctx.api("chats", { filter: "recent", limit: view === "recent" ? limit : PAGE });
  let chats = recent.chats;
  if (view !== "recent") {
    const options = [{ value: "", label: "Choose a " + view }];
    for (const item of recent.sources.filter((entry) => entry.role === view)) options.push({ value: item.ref, label: item.name || item.ref });
    const picker = ui.choice(view === "character" ? "Character" : "Scenario", options, {
      value: source || "", onchange: (value) => ctx.navigate(address({ source: value || undefined, limit: undefined })),
    });
    root.append(picker.node);
    chats = source ? (await ctx.api("chats", { filter: view, source, limit })).chats : [];
  }
  const rows = chats.map((chat) => {
    const where = chat.scope && chat.scope.world ? chat.scope.title || chat.scope.world : "No world";
    return el("a", { href: ctx.href("chat", [chat.chat]) },
      ui.rowText(chat.label || (chat.launch && chat.launch.name) || chat.chat, where + (chat.problem ? " · " + chat.problem : "")));
  });
  root.append(ui.list(rows, { empty: view === "recent" ? "No chats yet. Sync with your other device." : "No chats here." }));
  // a full page may have more behind it: ask for a longer list, up to the API's largest
  if (chats.length >= limit && limit < MAX_LIST) {
    root.append(el("a", { class: "g-button", href: address({ limit: Math.min(limit * 4, MAX_LIST) }) }, "Show more"));
  }
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
