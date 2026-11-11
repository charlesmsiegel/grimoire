// Campaigns (Sprint 113): every campaign with a chat in this library, and one campaign's hub as the table or
// the director sees it (docs/CAMPAIGN_HUB.md). Addresses:
//   #/campaign                                  every campaign: scenes in progress, and the table of them all
//   #/campaign/<campaign id>                    that campaign's hub, for the chat played last
//   #/campaign/<campaign id>?chat=<ChatRef>     the hub for one chat (&audience=director for the director's view)
// Everything here is read from, and written through, the campaign.* ops: the page keeps no state of its own
// beyond what the person is typing, and after every write it shows the hub the write returned. The list reads
// each campaign's hub as the table sees it, never as the director does. The hub itself is hub.js; what is
// sorted, filtered and shown to which view is logic.js.

import { showHub } from "./hub.js";
import { CHECKED, DEFAULT_SORT, ariaSort, arrow, chatName, columns, count, filters, inProgress, narrowed, nextSort, situation,
  sorted, summarize, turnText, when } from "./logic.js";

export async function mount(root, ctx) {
  const { ui } = ctx;
  ctx.styles("page.css");
  ctx.layout("page");
  const campaign = ctx.path[0] || null;
  const chat = ctx.query.get("chat");
  if (!campaign) return showCampaigns(root, ctx);
  const audience = ctx.query.get("audience") === "director" ? "director" : "table";
  if (chat && !chat.startsWith("campaign:" + campaign + "#")) {
    root.append(ui.notice("This address names a chat that is not in the campaign it names."),
      ui.actions(ui.buttonLink("All campaigns", "campaign")));
    return;
  }
  return showHub(root, ctx, campaign, chat, audience);
}

// -- the list ---------------------------------------------------------------------------------------------

async function showCampaigns(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.setTitle("Campaigns");
  ctx.setCrumb("Table");
  const answer = await ctx.api("campaign.chats", {});
  const rows = answer.campaigns.map(summarize);
  ctx.setCount("campaign", rows.length ? { value: rows.length, label: count(rows.length, "campaign") } : null);
  if (!rows.length) {
    root.append(ui.empty("No campaign has a chat in this library yet."));
    if (!answer.complete) root.append(ui.notice("Only the campaigns of the most recent chats are listed."));
    return;
  }

  const state = { search: "", filter: "all", sort: DEFAULT_SORT, situations: new Map(), checked: false };
  const search = ui.search("Search campaigns and worlds", { oninput: (text) => { state.search = text; draw(); } });
  search.setAttribute("data-focus-key", "campaign:search");
  const segments = el("div", {});
  const scenes = el("div", {});
  const table = el("div", { class: "g-stack" });
  root.append(el("div", { class: "g-stack g-loose" }, el("div", { class: "g-row" }, search, segments), scenes, table));
  if (!answer.complete) root.append(ui.notice("Only the campaigns of the most recent chats are listed."));

  function draw() {
    // every campaign listed was checked: only then do "Needs you" and "Paused" count them all
    const complete = state.checked && answer.complete && rows.length <= CHECKED;
    const offered = filters(rows, state.situations, complete);
    if (!offered.some((item) => item.value === state.filter)) state.filter = "all";
    segments.replaceChildren(...(offered.length > 1 ? [ui.segments("Which campaigns", offered, { value: state.filter,
      onchange: (value) => { state.filter = value; draw(); } }).node] : []));
    const shown = sorted(narrowed(rows, state.situations, state), state.situations, state.sort);
    scenes.replaceChildren(...sceneCards(ctx, inProgress(shown, state.situations)));
    scenes.hidden = !scenes.childNodes.length;
    const cols = columns(state.checked);
    const sortBy = (column) => {
      state.sort = nextSort(state.sort, column);
      draw();  // the header is drawn again: keep the keyboard on the column just sorted
      const again = table.querySelector('button[data-focus-key="campaign:sort:' + column.id + '"]');
      if (again) again.focus();
    };
    table.replaceChildren(...[
      ui.eyebrow(shown.length + " of " + count(rows.length, "campaign")),
      shown.length ? campaignTable(ctx, shown, cols, state, sortBy) : el("p", { class: "g-muted" }, "No campaign matches."),
      state.checked && rows.length > CHECKED ? el("p", { class: "g-muted" },
        "Only the " + CHECKED + " campaigns played last are checked for a scene in progress.") : null].filter(Boolean));
  }

  draw();
  // Ask the hub of each campaign's newest chat what is open there, as the table sees it, three at a time, and
  // draw again once they have all answered. A hub that cannot be read (a chat with a problem) says nothing
  // about its campaign: the row stays, without a scene. The page is shown meanwhile: mount does not wait.
  const queue = sorted(rows, state.situations, DEFAULT_SORT).filter((row) => row.newest).slice(0, CHECKED);
  let left = false;
  const worker = async () => {
    for (let row = queue.shift(); row && !left; row = queue.shift()) {
      try {
        state.situations.set(row.campaign, situation(await ctx.api("campaign.hub", { chat: row.newest.chat })));
      } catch (error) {
        if (error && error.reason === "left") left = true;
      }
    }
  };
  Promise.all([worker(), worker(), worker()]).then(() => {
    if (left || (ctx.signal && ctx.signal.aborted)) return;
    state.checked = true;
    draw();
  });
}

function sceneCards(ctx, open) {
  const { ui } = ctx;
  const { el } = ui;
  if (!open.length) return [];
  const cards = open.map(({ row, at }) => {
    const play = ctx.registered("chat") ? ui.buttonLink("Play", "chat", [row.newest.chat], undefined, { kind: "primary", size: "small" }) : null;
    const card = ui.card({ tag: "article", label: row.name }, el("div", { class: "campaign-scene" },
      el("div", { class: "g-row campaign-scene-head" }, ui.avatars(row.cast, { size: 36 }),
        el("div", { class: "g-grow" }, el("div", { class: "g-muted g-clip" }, row.name),
          el("div", { class: "campaign-scene-title" }, chatName(row.newest)))),
      at.line ? el("p", { class: "campaign-scene-line" }, at.line) : at.actor ? el("p", { class: "g-muted" }, at.actor + " is acting.") : null,
      el("p", { class: "g-muted" }, at.waiting),
      el("div", { class: "g-row g-between campaign-scene-foot" },
        el("span", { class: "campaign-turn" + (at.who === "you" ? " campaign-mine" : "") }, turnText(at)),
        el("span", { class: "g-row" }, ui.buttonLink("Hub", "campaign", [row.campaign], undefined, { size: "small" }), play))));
    card.classList.add("campaign-scene-card");
    return card;
  });
  return [el("section", { class: "g-stack", "aria-label": "Scenes in progress" },
    el("div", { class: "g-row" }, ui.eyebrow("Scenes in progress", { accent: true }),
      el("span", { class: "g-muted" }, count(open.length, "campaign") + (open.length === 1 ? " has" : " have") + " a scene in progress")),
    el("div", { class: "campaign-scenes" }, cards))];
}

function campaignTable(ctx, shown, cols, state, sortBy) {
  const { ui } = ctx;
  const { el } = ui;
  const head = cols.map((column) => el("th", { scope: "col", "aria-sort": column.first ? ariaSort(state.sort, column) : undefined },
    column.first ? el("button", { type: "button", class: "g-sort", "data-focus-key": "campaign:sort:" + column.id, onclick: () => sortBy(column) },
      column.label, el("span", { class: "campaign-arrow", "aria-hidden": "true" }, arrow(state.sort, column))) : column.label));
  const cells = {
    name: (row, at) => el("td", {}, el("div", { class: "g-row campaign-nameline" },
      el("a", { class: "g-title campaign-rowlink", href: ctx.href("campaign", [row.campaign]) }, row.name),
      at && at.paused ? ui.pill("Paused") : null, row.problems ? ui.pill(count(row.problems, "problem"), { kind: "bad" }) : null),
    el("div", { class: "g-muted campaign-small" }, row.world || "No world")),
    last: (row) => el("td", {}, el("div", {}, when(row.last)),
      row.newest ? el("div", { class: "g-muted campaign-small g-clip" }, chatName(row.newest)) : null),
    waiting: (row, at) => el("td", { class: at && at.who === "you" ? "campaign-mine" : "g-muted" }, at ? at.waiting || "Nothing" : ""),
    chats: (row) => el("td", {}, String(row.chats)),
    cast: (row) => el("td", {}, ui.avatars(row.cast, { size: 22 })),
  };
  const body = shown.map((row) => el("tr", { class: "campaign-tr" }, cols.map((column) => cells[column.id](row, state.situations.get(row.campaign)))));
  return el("div", { class: "g-card g-flush campaign-tablewrap" },
    el("table", { class: "g-table campaign-table" }, el("thead", {}, el("tr", {}, head)), el("tbody", {}, body)));
}
