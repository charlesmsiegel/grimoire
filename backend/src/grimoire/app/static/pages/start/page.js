// Start Chat (Sprint 111): pick a character, a character with a lorebook, a scenario or a premise, who you play,
// and an opening, then start a new world-less chat, or continue one already started from the same pick.
// #/start?source=<SourceRef> starts from that character or scenario. The persona and an unsent premise are drafts.

import { chatRow } from "../chats/page.js";

const KINDS = [["character", "A character"], ["bundle", "A character with a lorebook"], ["scenario", "A scenario"],
  ["premise", "A premise"]];

// The definition kind a SourceRef names: "<source>/<kind>/<entity>[@revision]".
function kindOf(ref) {
  const parts = String(ref || "").replace(/@[^/]*$/, "").split("/");
  return parts.length >= 3 ? parts[parts.length - 2] : null;
}

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.setTitle("Start Chat");
  ctx.styles("page.css");
  const sources = await ctx.api("play.sources", {});
  const linked = ctx.query.get("source");
  const linkedKind = kindOf(linked);
  const personaDraft = ctx.draft("persona");
  const form = {
    kind: linkedKind === "scenario" ? "scenario" : "character",
    ref: linkedKind === "scenario" || linkedKind === "character" ? linked : "",
    lorebook: "", opening: "",
  };
  const detail = el("div", { class: "start-detail", "aria-live": "polite" });
  const pickers = el("div", {});
  let asked = 0;  // the newest options request: an older answer that arrives late is dropped
  let shown = null;  // the selection whose options are on screen: Start sends only what was seen

  const named = (items, ref) => items.some((item) => item.ref === ref);
  const pickFrom = (kind) => (kind === "scenario" ? sources.scenarios : sources.characters);

  const kind = ui.choice("Start from", KINDS.map(([value, label]) => ({ value, label })), {
    value: form.kind, onchange: (value) => { form.kind = value; form.ref = ""; form.lorebook = ""; form.opening = ""; draw(); },
  });
  const personaOptions = [{ value: "", label: "Choose who you play" },
    ...sources.characters.map((item) => ({ value: item.ref, label: item.name }))];
  const persona = ui.choice("You play", personaOptions, {
    value: named(sources.characters, personaDraft.get()) ? personaDraft.get() : "",
    onchange: (value) => personaDraft.set(value),
  });
  const premise = ui.field("Premise", { draft: ctx.draft("premise"), multiline: true,
    hint: "What the chat is about. {{user}} is you." });
  premise.input.setAttribute("maxlength", "4000");
  premise.input.addEventListener("change", () => refresh());
  const label = ui.field("Name", { hint: "Optional: how the chat is listed." });
  label.input.setAttribute("maxlength", "200");

  function draw() {
    pickers.replaceChildren();
    if (form.kind === "premise") {
      pickers.append(premise.node);
    } else {
      const items = pickFrom(form.kind);
      const options = [{ value: "", label: form.kind === "scenario" ? "Choose a scenario" : "Choose a character" },
        ...items.map((item) => ({ value: item.ref, label: item.name }))];
      // a linked source the library cannot bind now is still named by its exact ref, never swapped for another
      if (form.ref && !named(items, form.ref)) options.push({ value: form.ref, label: form.ref });
      pickers.append(ui.choice(form.kind === "scenario" ? "Scenario" : "Character", options, {
        value: form.ref, onchange: (value) => { form.ref = value; form.opening = ""; refresh(); },
      }).node);
      if (form.kind === "bundle") {
        pickers.append(ui.choice("Lorebook", [{ value: "", label: "The card's own lorebook" },
          ...sources.lorebooks.map((item) => ({ value: item.ref, label: item.name }))], {
          value: form.lorebook, onchange: (value) => { form.lorebook = value; refresh(); },
        }).node);
      }
    }
    refresh();
  }

  function selection() {
    if (form.kind === "premise") return premise.value.trim() ? { kind: "premise", premise: premise.value } : null;
    if (!form.ref) return null;
    const chosen = { kind: form.kind, ref: form.ref };
    if (form.kind === "bundle" && form.lorebook) chosen.lorebook = form.lorebook;
    return chosen;
  }

  async function refresh() {
    const chosen = selection();
    const mine = ++asked;
    shown = null;
    if (!chosen) {
      detail.replaceChildren(ui.empty(form.kind === "premise" ? "Write a premise to start from." : "Choose what to start from."));
      return;
    }
    // the last pick's openings and chats go at once: an opening id or a Continue row must be this pick's
    detail.replaceChildren(ui.empty("Loading…"));
    let options;
    try { options = await ctx.api("play.options", chosen); }
    catch (error) {
      if (error.reason === "left" || mine !== asked) return;
      detail.replaceChildren(ui.errorBox(error));
      return;
    }
    if (mine !== asked) return;
    const parts = [];
    if (form.kind === "bundle") {
      parts.push(el("p", { class: "g-muted" }, options.lorebook || form.lorebook
        ? "The lorebook is bound with the card." : "This card has no lorebook of its own: choose one, or start from the character alone."));
    }
    if (options.openings.length) {
      const group = el("fieldset", { class: "start-openings" }, el("legend", {}, "Opening"));
      if (!options.openings.some((opening) => opening.id === form.opening)) form.opening = options.default_opening || "";
      for (const opening of options.openings) {
        const input = el("input", { type: "radio", name: "start-opening", value: opening.id,
          onchange: () => { form.opening = opening.id; } });
        input.checked = opening.id === form.opening;
        group.append(el("label", { class: "start-opening" }, input, el("span", {}, opening.text),
          opening.group_only ? el("span", { class: "g-muted" }, " (group greeting)") : null));
      }
      parts.push(group);
    } else {
      form.opening = "";
      parts.push(el("p", { class: "g-muted" }, form.kind === "premise" ? "The premise opens the chat." : "Nothing to open with: you speak first."));
    }
    if (options.related.length) {
      parts.push(ui.section("Continue", el("p", { class: "g-muted" }, "Chats already started from this. Continuing never makes a copy."),
        ui.list(options.related.map((chat) => chatRow(ctx, chat)))));
    }
    detail.replaceChildren(...parts);
    shown = JSON.stringify(chosen);
  }

  const start = ui.button("Start Chat", () => ctx.act(async () => {
    if (!persona.value) { ctx.notify("Choose who you play first.", { bad: true }); persona.select.focus(); return; }
    const chosen = selection();
    if (!chosen) { ctx.notify(form.kind === "premise" ? "Write a premise first." : "Choose what to start from first.", { bad: true }); return; }
    if (shown !== JSON.stringify(chosen)) { await refresh(); return; }  // show this pick's openings before starting
    const args = { ...chosen, persona: persona.value };
    if (form.opening) args.opening = form.opening;
    if (label.value.trim()) args.label = label.value.trim();
    const scene = await ctx.api("play.start", args);
    // only a premise that was sent is done; text typed into it while the start ran stays a draft
    if (chosen.kind === "premise" && premise.value === chosen.premise) premise.clear();
    // 052 wrote the chat but stopped before it finished; the chat says how (take your seat) once it opens
    if (scene.unfinished) ctx.notify("The chat was started, but starting stopped before it finished (" + scene.unfinished + ").", { bad: true });
    ctx.navigate(ctx.href("chat", [scene.chat]));
  }), { kind: "primary" });

  root.append(el("p", { class: "g-muted" }, "No world is needed. Every source is bound at the revision it has now."),
    kind.node, pickers, persona.node);
  if (!sources.characters.length) root.append(ui.notice("There is no character to play yet: import or create one first."));
  root.append(label.node, detail, ui.actions(start));
  draw();
}
