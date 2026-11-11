// What a labelling and an effect look like in full (113), wherever they are read before a key is given: the hub's
// own cards, and the inbox (pages/inbox) where the table confirms a labelling and endorses a change. Nothing here
// decides anything, and it shows only what `campaign.hub` answered for the view that asked.
//
// Its classes are the campaign page's own (page.css, all "campaign-"), which it links itself, so a page outside
// this folder needs nothing else.

import { leaning } from "./logic.js";

const STYLES = "/static/pages/campaign/page.css";

export function styles() {
  if (!document.head.querySelector('link[data-page-styles="' + CSS.escape(STYLES) + '"]')) {
    const link = document.createElement("link");
    link.setAttribute("rel", "stylesheet");
    link.setAttribute("href", STYLES);
    link.setAttribute("data-page-styles", STYLES);
    document.head.append(link);
  }
}

// A labelling's or a frame's factors (each with how it bears) and its stakes, each said in full. With `pick`,
// each has a tick: the factors a decision cites and the stakes it brings.
export function weighed(ui, factors, stakes, pick) {
  const { el } = ui;
  const ticked = { cites: [], consequences: [] };
  const row = (into, item, detail, tone) => {
    const text = el("span", { class: "g-grow" }, item.text);
    const end = detail ? el("span", { class: "campaign-bearing" + (tone ? " campaign-" + tone : "") }, detail) : null;
    if (!pick) return el("div", { class: "campaign-weigh-row" }, text, end);
    const input = el("input", { type: "checkbox", value: item.id });
    into.push(input);
    return el("label", { class: "campaign-weigh-row" }, input, text, end);
  };
  const column = (title, rows) => (rows.length ? el("div", { class: "campaign-weigh-col", role: "group", "aria-label": title },
    el("div", { class: "campaign-weigh-title" }, title), rows) : null);
  const columns = [column("What bears on it", factors.map((factor) => row(ticked.cites, factor, factor.bearing, leaning(factor.bearing)))),
    column("At stake", stakes.map((stake) => row(ticked.consequences, stake, null, "")))].filter(Boolean);
  const value = (inputs) => inputs.filter((input) => input.checked).map((input) => input.getAttribute("value"));
  return { node: columns.length ? el("div", { class: "campaign-weigh" }, columns) : null, picks: pick && columns.length > 0,
    cites: () => value(ticked.cites), consequences: () => value(ticked.consequences) };
}

// One holdings line in full, as the key on it covers it: who gives, who gets, how many, and who acts. Every
// field the line carries is said, so two different changes never read the same beside "Endorse as shown".
export function holdingLine(item, nameOf) {
  const counted = (item.amount ?? "?") + " " + item.item;
  const said = item.kind === "add" ? "add " + counted + " to " + nameOf(item.holder)
    : item.kind === "consume" ? "consume " + counted + " from " + nameOf(item.holder)
    : item.kind === "move" ? "move " + counted + " from " + nameOf(item.holder) + " to " + nameOf(item.target)
    : item.kind === "adjust" ? "adjust " + nameOf(item.holder) + "'s " + item.item + " from " + item.seen + " to " + item.to
    : item.kind + " " + counted + " (" + nameOf(item.holder) + ")";
  return said + (item.agent ? " (by " + nameOf(item.agent) + ")" : "");
}

// An effect in full, under its summary: its operations with who acts, every holdings line, the holdings note,
// what was waived, and each key on it. `player` is the principal whose key reads "Your key".
export function effectDetails(ui, effect, nameOf, player) {
  const { el } = ui;
  const ops = effect.operations.map((op) => op.kind + " " + op.predicate + ": " + nameOf(op.subject) + " → " + nameOf(op.object)
    + (op.agent ? " (by " + nameOf(op.agent) + ")" : ""));
  const held = effect.holdings ? effect.holdings.lines.map((item) => holdingLine(item, nameOf)) : [];
  const keys = effect.endorsers.map((key) => el("span", { class: "campaign-key" }, (key.principal === player ? "Your key" : key.principal) + ": ",
    el("strong", { class: key.given ? "campaign-given" : "" }, key.given ? "given" : "not given")));
  if (!keys.length) keys.push(el("span", { class: "campaign-key" }, "No player's key is needed"));
  keys.push(el("span", { class: "campaign-key" }, "Director: ", el("strong", { class: effect.approved_by ? "campaign-given" : "" }, effect.approved_by ? "approved" : "not yet")));
  return [
    ops.length || held.length ? el("ul", { class: "campaign-ops" }, [...ops, ...held].map((text) => el("li", {}, text))) : null,
    effect.holdings && effect.holdings.note ? el("p", { class: "g-muted" }, "Holdings note: " + effect.holdings.note) : null,
    effect.waivers.length ? el("p", { class: "g-muted" }, "Waived: " + effect.waivers.map((item) => item.change + " (" + item.reason + ")").join("; ")) : null,
    el("div", { class: "g-row campaign-keys" }, keys)];
}

// Who an actor ref is, by the cast's names: the name the hub gave, else the ref's own, else the ref.
export function namer(names) {
  return (ref) => (ref ? names[ref.ref] || ref.name || ref.ref : "nobody");
}
