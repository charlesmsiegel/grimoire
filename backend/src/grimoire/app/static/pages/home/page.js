// Home (108): where the app opens. It says which library is open and what this app can do with it now; a
// screen another sprint has not added yet is shown as not available, never as a link that fails.

const STARTS = [
  ["start", "Start a chat", "From a character card, a scenario or a premise, no world needed."],
  ["chats", "Continue a chat", "Recent chats, or the chats of one character or scenario."],
  ["import", "Import", "A character card, a scenario, a lorebook or a V1 library, previewed before anything is written."],
  ["sources", "Your library", "Characters, personas, scenarios, lore and worlds."],
  ["providers", "Set up a model", "A hosted or local model endpoint, and what each call may cost."],
];

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const library = ctx.library || {};
  ctx.setTitle("Home");
  const facts = el("p", { class: "g-muted" },
    "Device " + (library.device || "not named") + " · " + (library.generation ? "a model is set up" : "no model is set up"));
  const rows = STARTS.map(([route, title, detail]) => ctx.registered(route)
    ? el("a", { href: ctx.href(route) }, ui.rowText(title, detail))
    : el("div", { class: "g-unavailable" }, ui.rowText(title + " (not in this app yet)", detail)));
  root.append(facts, ui.section("What next", ui.list(rows)));
}
