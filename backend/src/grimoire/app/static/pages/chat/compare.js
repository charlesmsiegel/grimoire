// Compare takes side by side (pages/chat): the proposed reply's other face. Up to three takes stand as
// columns; each line is a card to choose ("Keep this line" / "Keeping"), one a speaker; the footer keeps the
// chosen lines as the turn's one reply (play.keep_lines), in the order it names first; each column keeps its own
// take whole (the core keep). Leaving compare mode goes back to one take at a time with nothing kept.
//
// What is chosen is this page's only: nothing is written until a Keep. The rules (one line a speaker, the
// order) are turn.js's, proved in chat_turn.test.mjs; the app holds the same ones and has the last word.

import { canCompare, compareTakes, keepLabel, orderText, pickLine, pickOrder, speakerKey } from "./turn.js";

// `run(work)` runs one action and `act(op, args, options)` one op that answers the scene, as the page does;
// `redraw()` draws the chat again; `again()` asks for another take; `generates()` is whether one can be asked
// for; `passButton(reply, options)` is the page's Pass; `rows` holds the transcript; `setKeys(keys)` names the
// buttons the take's keys press.
export function makeCompare(ctx, { run, act, redraw, again, generates, passButton, rows, setKeys }) {
  const { ui } = ctx;
  const { el } = ui;
  const served = ctx.has("play.keep_lines");
  const state = { turn: null, on: false, picks: [] };
  if (served) ctx.styles("compare.css");

  // Compare mode belongs to one open turn with at least two takes: anything else is one take at a time.
  function showing(reply) {
    if (!served || !reply.turn || !canCompare(reply.takes)) { state.on = false; state.picks = []; return false; }
    if (state.turn !== reply.turn.turn) { state.turn = reply.turn.turn; state.on = false; state.picks = []; }
    return state.on;
  }

  // "Compare side by side" on the proposed reply, or "One at a time" in compare mode; null with nothing to
  // compare. Only what the page shows changes, so it is pressed while play is held up too.
  function toggle(reply) {
    const on = showing(reply);
    if (!served || !reply.turn || !canCompare(reply.takes)) return null;
    return el("button", { type: "button", class: "g-button g-small chat-compare-toggle", "aria-pressed": String(on), "data-local": "",
      "data-focus-key": "chat-compare", title: on ? "Back to one take at a time. Nothing is kept." : "Set the takes side by side and keep lines from each",
      onclick: () => { state.on = !on; state.picks = []; redraw(); refocus("button.chat-compare-toggle"); } },
    on ? "One at a time" : "Compare side by side");
  }

  // Drawing again replaces the card: focus goes back to the control that was pressed.
  function refocus(selector, line) {
    const found = Array.from(rows.querySelectorAll(selector)).find((node) => !line || node.getAttribute("data-line") === line);
    if (found && !found.disabled) found.focus();
  }

  function lineCard(takes, line) {
    const chosen = state.picks.includes(line.line);
    return el("button", { type: "button", class: "chat-pick" + (chosen ? " chat-picked" : ""), "aria-pressed": String(chosen),
      "data-line": line.line, "data-local": "",
      onclick: () => { state.picks = pickLine(takes, state.picks, line.line); redraw(); refocus("button.chat-pick", line.line); } },
    el("span", { class: "chat-pick-head" }, el("span", { class: "chat-pick-speaker" }, line.speaker || "Narrator"),
      el("span", { class: "chat-pick-mark" }, chosen ? "Keeping" : "Keep this line")),
    el("span", { class: "chat-pick-text g-prose" }, line.text));
  }

  function card(scene, reply) {
    const turn = reply.turn.turn;
    const takes = compareTakes(reply.takes);
    const ordered = pickOrder(takes, state.picks);
    state.picks = ordered.map((item) => item.line.line);  // a line no longer on offer is no longer chosen
    const keys = {};
    const unscreened = (take) => scene.state.unscreened.includes(take.take);
    const head = el("div", { class: "chat-take-head" },
      el("div", {}, ui.eyebrow("Proposed reply", { accent: true }), el("div", { class: "chat-take-sub" }, "Not part of the story until you keep it")));
    keys.compare = toggle(reply);
    head.append(el("div", { class: "chat-stepper" }, keys.compare));

    const columns = el("div", { class: "chat-compare", role: "group", "aria-label": "Takes side by side" });
    for (const take of takes) {
      const name = "Take " + (reply.takes.indexOf(take) + 1) + (take.origin === "director" ? " · director" : "");
      const whole = scene.state.keepable.includes(take.take) ? el("button", { type: "button", class: "g-button g-linklike g-small chat-compare-all",
        "aria-label": "Keep all of " + name.toLowerCase(),
        onclick: () => run(() => act("keep", { turn, take: take.take, acknowledge_unscreened: unscreened(take) }, { bottom: true })) },
      unscreened(take) ? "Keep all of it (uses lore no screen covered)" : "Keep all of it") : null;
      columns.append(el("section", { class: "chat-compare-col", "aria-label": name },
        el("div", { class: "chat-compare-head" }, el("span", { class: "chat-compare-name" }, name), whole),
        ...take.lines.map((line) => lineCard(takes, line))));
    }
    const hidden = reply.takes.length - takes.length;
    const speakers = new Set(takes.flatMap((take) => take.lines).map(speakerKey));
    const notes = [
      orderText(ordered) || null,
      // why two lines of one speaker cannot both be chosen
      takes.some((take) => take.lines.length) && speakers.size ? "One line for each speaker: choosing another take's line for the same speaker replaces it." : null,
      hidden > 0 ? "The newest " + takes.length + " of " + reply.takes.length + " takes are side by side. Go back to one at a time for the others." : null,
    ].filter(Boolean);

    const footer = el("div", { class: "chat-take-foot" });
    const lore = ordered.some((item) => unscreened(item.take));
    keys.keep = ui.button(keepLabel(ordered.length) + (lore ? " (uses lore no screen covered)" : ""), () => run(async () => {
      try {
        await act("play.keep_lines", { turn, lines: ordered.map((item) => item.line.line), acknowledge_unscreened: lore }, { bottom: true });
      } catch (error) {
        // refused (a line was withdrawn or the turn changed meanwhile, say): nothing was kept. The chat is read
        // again, so what is on screen is what is there, and the page says why.
        if (error && error.reason !== "left") { try { await act("open", {}); } catch (again) { if (again && again.reason === "left") throw again; } }
        throw error;
      }
      state.on = false;
      state.picks = [];
    }), { kind: "primary", disabled: !ordered.length });
    keys.keep.classList.add("chat-compare-keep");
    footer.append(keys.keep);
    if (generates()) { keys.again = ui.button("Try again", again); footer.append(keys.again); }
    keys.pass = passButton(reply, { quiet: true });
    footer.append(keys.pass, el("span", { class: "chat-keys" }, "Keys: "
      + ["Enter keeps the selected lines", keys.again && "R tries again", "C goes back to one at a time", "P passes"].filter(Boolean).join(" · ")));
    setKeys(keys);
    return el("article", { class: "chat-take chat-comparing", "aria-label": "Proposed reply" }, head, columns,
      el("div", { class: "chat-compare-notes", "aria-live": "polite" }, ...notes.map((text) => el("p", { class: "chat-compare-note" }, text))), footer);
  }

  return { served, showing, toggle, card };
}
