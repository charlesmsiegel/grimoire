// What the source page does besides editing (109): a portrayal's override, clear and reset of an inherited field,
// deriving a portrayal from the exact revision shown, and attaching that exact revision to a chat. Each writes
// through its own op against what the page read, and a refusal "changed" reads again instead of guessing.

import { save } from "./editors.js";

// The tools under one field of a derived portrayal's sheet: Override (description and sections), Clear and Reset.
export function portrayalTools(ctx, read, item) {
  const { ui } = ctx;
  const { el } = ui;
  const [kind, name] = item.field.startsWith("section:") ? ["section", item.field.slice(8)]
    : item.field.startsWith("facet:") ? ["facet", item.field.slice(6)] : [item.field, undefined];
  if (!["section", "facet", "description"].includes(kind)) return null;
  const inherited = item.state === "inherited" || item.state === "pinned";
  const own = item.state === "overridden" || item.state === "restated";
  const what = (kind === "description" ? "the description" : kind + " " + name) + " of " + (read.name || read.entity);
  const run = (edit, extra = {}, draft = { clear() {} }) => ctx.act(async () => {
    const args = { edit, ...extra };
    if (name !== undefined) args.name = name;
    await save(ctx, read, "sources.portrayal", args, draft);  // the draft is cleared only once it is saved
  });
  const tools = [];
  if (inherited && kind !== "facet") {
    const draft = ctx.draft("source:" + read.entity + ":override:" + item.field);
    const field = ui.field("Override " + (kind === "description" ? "the description" : "section " + name),
      { multiline: true, draft, value: typeof item.value === "string" ? item.value : "" });
    tools.push(el("details", { class: "source-override" }, el("summary", {}, "Override"), field.node,
      ui.actions(ui.button("Save the override", () => {
        if (!field.value.trim()) { field.setError("An override needs text. To stop showing it, clear it instead."); return undefined; }
        field.setError("");
        return run("override_" + kind, { text: field.value }, draft);
      }, { kind: "primary", size: "small" }))));
  }
  if (inherited) {
    tools.push(ui.button("Clear", async () => {
      const yes = await ui.confirm("Clear " + what + "? The new version stops showing it; the base keeps it, and Reset brings it back.",
        { confirmLabel: "Clear it", danger: true });
      if (yes) await run("clear_" + kind);
    }, { size: "small" }));
  }
  if (own || item.state === "cleared") {
    tools.push(ui.button("Reset", async () => {
      const yes = await ui.confirm("Reset " + what + " to inherit from the base again? "
        + (item.state === "cleared" ? "The clear is lifted." : "The override is dropped from the new version; " + read.revision + " keeps it."),
      { confirmLabel: "Reset it" });
      if (yes) await run("reset_" + kind);
    }, { size: "small" }));
  }
  return tools.length ? el("div", { class: "source-tools" }, ...tools) : null;
}

// "Derive a portrayal": a name, the source it goes in, and the derive itself, from exactly the revision shown.
export function deriveForm(ctx, read, pinned) {
  const { ui } = ctx;
  const { el } = ui;
  if (read.family !== "entity") return null;
  if (read.actions.derive !== null) return el("p", { class: "g-muted" }, read.actions.derive);
  if (!ctx.has("sources.derive") || !ctx.has("sources.catalog")) return null;
  const holder = el("div", { class: "source-derive g-stack" });
  const name = ui.field("Name (optional)", { draft: ctx.draft("source:" + read.entity + ":derive:name") });
  const open = ui.button("Derive a portrayal", () => ctx.act(async () => {
    const catalog = await ctx.api("sources.catalog", { limit: 1 });
    const targets = catalog.sources.filter((source) => source.namespace !== "card" && source.namespace !== "import");
    const about = el("p", { class: "g-muted" }, "A portrayal inherits every field of exactly " + pinned
      + " and overrides or clears only what you change. A newer version of the base reaches it only through a reviewed rebase.");
    if (!targets.length) { holder.replaceChildren(about, ui.notice("Make a source in the library first, such as a world.")); return; }
    const picked = ui.choice("Source", targets.map((source) => ({ value: source.source, label: (source.title || source.source) + " (" + source.source + ")" })));
    const go = ui.button("Derive from " + read.revision, () => ctx.act(async () => {
      const target = targets.find((source) => source.source === picked.value);
      const args = { base: pinned, source: target.source, expected: target.etag };
      if (name.value.trim()) args.name = name.value.trim();
      try {
        const answer = await ctx.api("sources.derive", args);
        name.clear();
        ctx.navigate(ctx.href("source", [answer.ref]));
      } catch (error) {
        if (error.reason === "changed") { ctx.notify("That source changed since it was listed; choose it again.", { bad: true }); holder.replaceChildren(); return; }
        throw error;
      }
    }), { kind: "primary" });
    holder.replaceChildren(about, name.node, picked.node, ui.actions(go, ui.button("Cancel", () => holder.replaceChildren())));
  }), { kind: "dashed", size: "small" });
  open.setAttribute("data-focus-key", "source-derive");
  return el("div", { class: "g-stack" }, ui.actions(open), holder);
}

const CHAT_PAGE = 50;

// "Attach to a chat": the panel the header's button opens. It lists the chats, newest activity first, with a search
// over every chat's label and ref and "Show more chats" while more match, so an older chat is as reachable as a
// recent one; the attach names exactly the revision shown.
export function attachPanel(ctx, read, pinned) {
  const { ui } = ctx;
  const { el } = ui;
  const holder = el("div", {});
  const box = ui.card({ tag: "section", label: "Attach to a chat" }, el("h2", { class: "g-heading" }, "Attach to a chat"),
    el("p", { class: "g-muted" }, "Attaches exactly " + pinned + ". The chat keeps that version until a reviewed rebase."), holder);
  box.classList.add("source-attach");
  box.hidden = true;
  let loaded = false;
  return {
    node: box,
    toggle() {
      box.hidden = !box.hidden;
      if (!box.hidden && !loaded) { loaded = true; ctx.act(() => listChats(ctx, read, pinned, holder)); }
      return !box.hidden;
    },
  };
}

async function listChats(ctx, read, pinned, holder, keep, query = "", limit = CHAT_PAGE) {
  const { ui } = ctx;
  const { el } = ui;
  const answer = await ctx.api("sources.chats", { limit, query });
  const search = ui.field("Find a chat by name or ref", { value: query });
  const find = el("form", { class: "source-chat-search", role: "search" }, search.node,
    el("button", { type: "submit", class: "g-button" }, "Find"));
  find.addEventListener("submit", (event) => {
    event.preventDefault();
    ctx.act(() => listChats(ctx, read, pinned, holder, keep, search.value.trim()));
  });
  if (!answer.chats.length) {
    holder.replaceChildren(find, ui.empty(query ? "No chat matches " + JSON.stringify(query) + "." : "No chat to attach it to yet."));
    return;
  }
  const kept = answer.chats.some((chat) => chat.chat === keep) ? keep : answer.chats[0].chat;
  const picked = ui.choice("Chat", answer.chats.map((chat) => ({ value: chat.chat, label: (chat.label || chat.chat) + " · " + chat.last_activity })),
    { value: kept });
  const more = answer.truncated && limit < 1000
    ? ui.button("Show more chats", () => ctx.act(() => listChats(ctx, read, pinned, holder, picked.value, query, Math.min(limit + CHAT_PAGE, 1000))))
    : null;
  const go = ui.button("Attach " + read.revision, () => ctx.act(async () => {
    const chat = answer.chats.find((item) => item.chat === picked.value);
    const joins = read.kind === "character" ? " It joins as a new actor that has witnessed nothing so far." : "";
    const yes = await ui.confirm("Attach " + (read.name || read.entity) + " at exactly " + pinned + " to " + (chat.label || chat.chat) + "?" + joins,
      { confirmLabel: "Attach " + read.revision });
    if (!yes) return;
    let done;
    try {
      done = await ctx.api("sources.attach", { ref: pinned, chat: chat.chat, expected: chat.etag });
    } catch (error) {
      if (error.reason === "changed") {
        await listChats(ctx, read, pinned, holder, chat.chat, query, limit);
        holder.prepend(ui.notice("That chat changed since it was listed; it is read again. Attach again to use it as it is now.", { kind: "bad" }));
        return;
      }
      throw error;
    }
    const where = ctx.registered("chat") ? el("a", { class: "g-link", href: ctx.href("chat", [done.chat]) }, "Open the chat") : null;
    holder.replaceChildren(ui.notice("Attached " + done.ref + " to " + (chat.label || chat.chat) + " as its " + done.role + "."), where || "");
    ctx.notify("Attached " + done.ref + ".");
  }), { kind: "primary" });
  holder.replaceChildren(find, picked.node, ui.actions(go, more),
    answer.truncated && !more ? el("p", { class: "g-muted" }, "More chats match; search to narrow them.") : "");
}
