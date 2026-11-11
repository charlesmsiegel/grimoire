// Start a chat (Sprint 111): pick a character, a character with a lorebook, a scenario or a premise, who you
// play, and an opening, then start a new world-less chat, or continue one already started from the same pick.
// #/start?source=<SourceRef> starts from that character or scenario. The persona and an unsent premise are drafts.
// A card played before sets up the persona (and, with a lorebook, the lorebook) of its newest chat again, and says
// so, unless the person has chosen a persona of their own: a persona draft, or a chip clicked in this visit, wins.
// Every name, opening and premise comes from the library and is only ever set as text.

const KINDS = [
  ["character", "A character", "Talk to one card"],
  ["bundle", "Character + lorebook", "A card with lore bound in"],
  ["scenario", "A scenario", "A set scene with openings"],
  ["premise", "A premise", "Just an idea, no card"],
];
const SHOWN = 12;  // how many cards one list shows before the search narrows it

// The definition kind a SourceRef names: "<source>/<kind>/<entity>[@revision]".
export function kindOf(ref) {
  const parts = unpinned(ref).split("/");
  return parts.length >= 3 ? parts[parts.length - 2] : null;
}

export function unpinned(ref) {
  return String(ref || "").replace(/@[^/]*$/, "");
}

// Where a card comes from, in a word: the source its ref names.
export function sourceLabel(ref) {
  const source = String(ref || "").split("/")[0];
  return source.split(":")[0] === "card" ? "Imported card" : source;
}

// How recently each source was used, from the chats played last: "<ref>" for a card in a chat and "persona <ref>"
// for who was played, each mapped to its place (0 is the newest). Null when the app cannot say.
export async function recentUse(ctx) {
  if (!ctx.has("chats")) return null;
  const rank = new Map();
  try {
    const { chats } = await ctx.api("chats", { filter: "recent", limit: 200 });
    for (const chat of chats) {
      for (const source of chat.sources || []) {
        const key = (source.role === "persona" ? "persona " : "") + unpinned(source.ref);
        if (!rank.has(key)) rank.set(key, rank.size);
      }
    }
  } catch (error) {
    if (error.reason === "left") throw error;
    return null;  // the list still works, in name order
  }
  return rank;
}

// `items` with the ones used most recently first; the rest keep their order (by name).
export function byRecency(items, rank, prefix = "") {
  if (!rank) return items;
  const place = (item) => (rank.has(prefix + item.ref) ? rank.get(prefix + item.ref) : Infinity);
  return items.map((item, index) => [item, index]).sort((a, b) => place(a[0]) - place(b[0]) || a[1] - b[1]).map(([item]) => item);
}

// What a search shows of `items`: the first few that match, and always the one that is chosen.
export function shortlist(items, query, chosen) {
  const needle = query.trim().toLowerCase();
  const matches = needle
    ? items.filter((item) => item.name.toLowerCase().includes(needle) || item.ref.toLowerCase().includes(needle)) : items;
  const shown = matches.slice(0, SHOWN);
  const picked = chosen && !shown.some((item) => item.ref === chosen) ? items.find((item) => item.ref === chosen) : null;
  if (picked) shown.unshift(picked);
  return { matches, shown };
}

// What a card's history sets up again, from the `last` of play.options: the persona when the library can still
// offer it, and the first remembered lorebook that can be chosen and is not the card's own (which is the default).
export function remembered(last, personas, lorebooks, own) {
  const named = (items, ref) => items.some((item) => item.ref === ref);
  if (!last || !last.persona || !named(personas, last.persona.ref)) return { persona: "", lorebook: "" };
  const lorebook = (last.lorebooks || []).find((item) => named(lorebooks, item.ref));
  return { persona: last.persona.ref, lorebook: lorebook && lorebook.ref !== unpinned(own) ? lorebook.ref : "" };
}

function chatTitle(chat) {
  return chat.label || (chat.launch && chat.launch.name)
    || (chat.launch && chat.launch.kind === "premise" ? "Premise chat" : chat.chat);
}

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.setTitle("Start a chat");
  ctx.setCrumb("Play");
  ctx.layout("page");
  ctx.styles("page.css");
  const [sources, rank] = await Promise.all([ctx.api("play.sources", {}), recentUse(ctx)]);
  const characters = byRecency(sources.characters, rank);
  const scenarios = byRecency(sources.scenarios, rank);
  const personas = byRecency(sources.characters, rank, "persona ");
  const linked = ctx.query.get("source");
  const linkedKind = kindOf(linked);
  const personaDraft = ctx.draft("persona");
  const named = (items, ref) => items.some((item) => item.ref === ref);
  const form = {
    kind: linkedKind === "scenario" ? "scenario" : "character",
    ref: linkedKind === "scenario" || linkedKind === "character" ? linked : "",
    lorebook: "", opening: "",
    persona: named(personas, personaDraft.get()) ? personaDraft.get() : "",
  };
  const queries = { pick: "", persona: "" };
  // The person's own choices win over a card's history: a persona draft (chosen earlier in this tab and not yet
  // started with) or a chip clicked now, and a lorebook picked now. `memo` is the history on show, if any.
  const own = { persona: Boolean(form.persona), lorebook: false };
  let memo = null;
  let lorebookChoice = null;
  let asked = 0;  // the newest options request: an older answer that arrives late is dropped
  let shown = null;  // the selection whose options are on screen: Start sends only what was seen

  // A linked source the library cannot bind now is still named by its exact ref, never swapped for another.
  const pickFrom = (kind) => {
    const items = kind === "scenario" ? scenarios : characters;
    return form.ref && !named(items, form.ref) ? [{ ref: form.ref, name: form.ref }, ...items] : items;
  };
  const nameOf = (items, ref) => { const found = items.find((item) => item.ref === ref); return found ? found.name : ref; };
  const press = (box, attribute, value) => {
    for (const node of box.querySelectorAll("button")) {
      if (node.hasAttribute(attribute)) node.setAttribute("aria-pressed", String(node.getAttribute(attribute) === value));
    }
  };

  // -- 1 · Start from ----------------------------------------------------------------------------------------
  const kinds = el("div", { class: "start-kinds", role: "group", "aria-label": "Start from" });
  for (const [value, label, detail] of KINDS) {
    const node = ui.selectable([el("span", { class: "start-kind-label" }, label), el("span", { class: "start-kind-detail" }, detail)], {
      selected: value === form.kind,
      onclick: () => {
        if (form.kind === value) return;
        form.kind = value; form.ref = ""; form.lorebook = ""; form.opening = ""; queries.pick = "";
        own.lorebook = false;
        press(kinds, "data-kind", value);
        drawPick();
      },
    });
    node.classList.add("start-kind");
    node.setAttribute("data-kind", value);
    node.setAttribute("data-focus-key", "start:kind:" + value);
    kinds.append(node);
  }

  // -- 2 · Choose --------------------------------------------------------------------------------------------
  const premise = ui.field("Premise", { draft: ctx.draft("premise"), multiline: true,
    hint: "What the chat is about. {{user}} is you.", placeholder: "What the chat is about. Write yourself in as the player." });
  premise.input.setAttribute("maxlength", "4000");
  premise.input.setAttribute("rows", "5");
  premise.input.classList.add("start-premise");
  premise.input.addEventListener("change", () => refresh());
  const pickTitle = el("h2", { class: "g-heading" });
  const pickBody = el("div", { class: "start-step" });

  function drawPick() {
    const scenario = form.kind === "scenario";
    pickTitle.textContent = form.kind === "premise" ? "2 · Write the premise" : scenario ? "2 · Choose a scenario" : "2 · Choose a character";
    if (form.kind === "premise") { pickBody.replaceChildren(premise.node); refresh(); return; }
    const what = scenario ? "scenarios" : "characters";
    const count = (scenario ? scenarios : characters).length;
    const more = el("div", { class: "g-muted start-more", "aria-live": "polite" });
    const tiles = el("div", { class: "start-picks", role: "group", "aria-label": scenario ? "Scenario" : "Character" });
    const fill = () => {
      const { matches, shown: some } = shortlist(pickFrom(form.kind), queries.pick, form.ref);
      more.textContent = matches.length > SHOWN ? "Showing " + SHOWN + " of " + matches.length + ". Keep typing to narrow."
        : matches.length || !count ? "" : "Nothing matches.";
      tiles.replaceChildren(...some.map((item) => {
        const node = el("button", { type: "button", class: "g-select start-pick", "aria-pressed": String(item.ref === form.ref),
          "data-ref": item.ref, "data-focus-key": "start:pick:" + item.ref, title: item.ref,
          onclick: () => { form.ref = item.ref; form.opening = ""; press(tiles, "data-ref", item.ref); refresh(); } },
        ui.portrait(item.name, { key: item.ref }),
        el("span", { class: "start-pick-text" }, el("span", { class: "start-pick-name" }, item.name),
          el("span", { class: "start-pick-source" }, sourceLabel(item.ref))));
        return node;
      }));
    };
    const search = ui.search("Search " + what, { value: queries.pick, oninput: (text) => { queries.pick = text; fill(); },
      placeholder: "Search " + count + " " + what + (rank ? " · most recent shown first" : "") });
    search.classList.add("start-search");
    search.setAttribute("data-focus-key", "start:search");
    const parts = [search, more, tiles];
    if (!count) {
      parts.push(ui.empty("This library has no " + what + " yet."), ui.actions(
        ctx.registered("import") ? ui.buttonLink("Import a card", "import") : null,
        ctx.registered("sources") ? ui.buttonLink("Characters and lore", "sources") : null));
    }
    lorebookChoice = null;
    if (form.kind === "bundle") {
      lorebookChoice = ui.choice("Lorebook", [{ value: "", label: "The card's own lorebook" },
        ...sources.lorebooks.map((item) => ({ value: item.ref, label: item.name }))], {
        value: form.lorebook, onchange: (value) => { form.lorebook = value; own.lorebook = true; refresh(); },
      });
      parts.push(lorebookChoice.node);
    }
    pickBody.replaceChildren(...parts);
    fill();
    refresh();
  }

  // -- 3 · You play ------------------------------------------------------------------------------------------
  const chips = el("div", { class: "g-chips", role: "group", "aria-label": "You play" });
  const personaMore = el("div", { class: "g-muted start-more", "aria-live": "polite" });
  function drawPersonas() {
    const { matches, shown: some } = shortlist(personas, queries.persona, form.persona);
    personaMore.textContent = matches.length > SHOWN ? "Showing " + SHOWN + " of " + matches.length + ". Keep typing to narrow."
      : matches.length || !personas.length ? "" : "Nothing matches.";
    chips.replaceChildren(...some.map((item) => {
      const node = ui.selectable([ui.avatar(item.name, { size: 22, key: item.ref }), el("span", {}, item.name)], {
        chip: true, selected: item.ref === form.persona, title: item.ref,
        onclick: () => {
          form.persona = item.ref; personaDraft.set(item.ref); own.persona = true; memo = null;
          press(chips, "data-ref", item.ref); summarize();
        },
      });
      node.setAttribute("data-ref", item.ref);
      node.setAttribute("data-focus-key", "start:persona:" + item.ref);
      return node;
    }));
  }
  const personaParts = [];
  if (personas.length > SHOWN) {
    const search = ui.search("Search who you play", { oninput: (text) => { queries.persona = text; drawPersonas(); },
      placeholder: "Search " + personas.length + " characters" + (rank ? " · most recent shown first" : "") });
    search.classList.add("start-search");
    search.setAttribute("data-focus-key", "start:persona-search");
    personaParts.push(search, personaMore);
  }
  personaParts.push(chips);
  if (!sources.characters.length) personaParts.push(ui.notice("There is no character to play yet: import or create one first."));

  // -- the summary -------------------------------------------------------------------------------------------
  const title = el("div", { class: "start-summary-title" });
  const detail = el("div", { class: "g-stack start-detail", "aria-live": "polite" });
  const memoNote = el("div", { class: "start-memo", "aria-live": "polite", hidden: true });
  const related = el("div", { class: "start-related", hidden: true });
  const label = ui.field("Name", { placeholder: "Optional: how the chat is listed" });
  label.input.setAttribute("maxlength", "200");
  label.node.classList.add("start-name");

  function summarize() {
    const who = form.persona ? nameOf(personas, form.persona) : "You";
    if (form.kind === "premise") title.textContent = who + " in a new premise";
    else if (!form.ref) title.textContent = "Choose what to start from";
    else title.textContent = who + (form.persona ? " meets " : " meet ") + nameOf(pickFrom(form.kind), form.ref);
    memoNote.hidden = !memo;
    memoNote.textContent = memo ? "Last time with " + memo.card + " you played " + memo.persona + ", so that is set up again." : "";
  }

  // Sets up what `options.last` remembers (or, with none, takes back what an earlier pick's history set up),
  // leaving alone whatever the person chose themselves. True when the lorebook changed, so the pick did too.
  function recall(options) {
    const found = remembered(options && form.kind !== "premise" ? options.last : null, personas, sources.lorebooks,
      options ? options.lorebook : null);
    if (!own.persona) {
      if (form.persona !== found.persona) { form.persona = found.persona; drawPersonas(); }
      memo = found.persona ? { card: nameOf(pickFrom(form.kind), form.ref), persona: nameOf(personas, found.persona) } : null;
    }
    const lorebook = form.kind === "bundle" ? found.lorebook : "";
    if (own.lorebook || form.lorebook === lorebook) return false;
    form.lorebook = lorebook;
    if (lorebookChoice) lorebookChoice.select.value = lorebook;
    return form.kind === "bundle";
  }

  function selection() {
    if (form.kind === "premise") return premise.value.trim() ? { kind: "premise", premise: premise.value } : null;
    if (!form.ref) return null;
    const chosen = { kind: form.kind, ref: form.ref };
    if (form.kind === "bundle" && form.lorebook) chosen.lorebook = form.lorebook;
    return chosen;
  }

  async function refresh() {
    memo = null;  // the memo is about the pick on show: it comes back with that pick's own answer
    summarize();
    const chosen = selection();
    const mine = ++asked;
    shown = null;
    related.hidden = true;
    related.replaceChildren();
    if (!chosen) {
      recall(null);
      summarize();
      detail.replaceChildren(ui.empty(form.kind === "premise" ? "Write a premise to start from." : "Pick a card to see how it opens."));
      return;
    }
    // the last pick's openings and chats go at once: an opening id or a chat to continue must be this pick's
    detail.replaceChildren(ui.empty("Loading…"));
    let options;
    try { options = await ctx.api("play.options", chosen); }
    catch (error) {
      if (error.reason === "left" || mine !== asked) return;
      recall(null);
      summarize();
      detail.replaceChildren(ui.errorBox(error));
      return;
    }
    if (mine !== asked) return;
    if (recall(options)) { refresh(); return; }  // the remembered lorebook is part of the pick: show that pick
    summarize();
    const parts = [];
    if (form.kind === "bundle") {
      parts.push(el("p", { class: "g-muted" }, options.lorebook || form.lorebook
        ? "The lorebook is bound with the card." : "This card has no lorebook of its own: choose one, or start from the character alone."));
    }
    if (options.openings.length) {
      if (!options.openings.some((opening) => opening.id === form.opening)) form.opening = options.default_opening || "";
      const group = el("div", { class: "g-stack start-openings", role: "group", "aria-label": "Opening" },
        el("div", { class: "start-label" }, "Opening"));
      for (const opening of options.openings) {
        const node = ui.selectable([el("span", { class: "start-opening-text" }, opening.text),
          opening.group_only ? ui.pill("group greeting") : null], {
          selected: opening.id === form.opening,
          onclick: () => { form.opening = opening.id; press(group, "data-opening", opening.id); },
        });
        node.classList.add("start-opening");
        node.setAttribute("data-opening", opening.id);
        group.append(node);
      }
      parts.push(group);
    } else {
      form.opening = "";
      parts.push(el("p", { class: "g-muted" }, form.kind === "premise" ? "The premise opens the chat." : "Nothing to open with: you speak first."));
    }
    detail.replaceChildren(...parts);
    if (options.related.length) {
      related.replaceChildren(el("div", { class: "g-muted" }, "Or continue one already started from this pick"),
        ...options.related.map((chat) => el("a", { class: "start-related-row", href: ctx.href("chat", [chat.chat]) },
          el("span", { class: "start-related-title" }, chatTitle(chat)),
          chat.problem ? el("span", { class: "g-muted" }, chat.problem) : null)));
      related.hidden = false;
    }
    shown = JSON.stringify(chosen);
  }

  const start = ui.button("Start chat", () => ctx.act(async () => {
    if (!form.persona) {
      ctx.notify("Choose who you play first.", { bad: true });
      const first = chips.querySelector("button");
      if (first) first.focus();
      return;
    }
    const chosen = selection();
    if (!chosen) { ctx.notify(form.kind === "premise" ? "Write a premise first." : "Choose what to start from first.", { bad: true }); return; }
    if (shown !== JSON.stringify(chosen)) { await refresh(); return; }  // show this pick's openings before starting
    const args = { ...chosen, persona: form.persona };
    if (form.opening) args.opening = form.opening;
    if (label.value.trim()) args.label = label.value.trim();
    const scene = await ctx.api("play.start", args);
    // the persona was sent, so it is no longer an unsent choice: next time the card's own history decides
    personaDraft.clear();
    // only a premise that was sent is done; text typed into it while the start ran stays a draft
    if (chosen.kind === "premise" && premise.value === chosen.premise) premise.clear();
    // 052 wrote the chat but stopped before it finished: the chat page says so and offers Finish starting, as
    // opening the chat does not say it
    ctx.navigate(ctx.href("chat", [scene.chat], scene.unfinished ? { unfinished: scene.unfinished } : undefined));
  }), { kind: "primary", size: "large" });
  start.classList.add("start-go");
  start.setAttribute("data-focus-key", "start:go");

  const step = (heading, ...children) => el("section", { class: "start-step" }, heading, ...children);
  root.append(el("div", { class: "start-layout" },
    el("div", { class: "start-steps" },
      step(el("h2", { class: "g-heading" }, "1 · Start from"), kinds),
      step(pickTitle, pickBody),
      step(el("h2", { class: "g-heading" }, "3 · You play"), ...personaParts)),
    el("aside", { class: "g-card start-summary", "aria-label": "New chat" },
      el("div", { class: "start-summary-head" }, ui.eyebrow("New chat"), title, memoNote,
        el("div", { class: "g-muted start-small" }, "No world needed. Everything is bound at the revision it has now.")),
      detail, label.node, start, related)));
  drawPersonas();
  drawPick();
}
