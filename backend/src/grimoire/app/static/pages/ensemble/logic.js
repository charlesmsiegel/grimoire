// What the ensemble page (051, Sprint 115) works out without the app: the authored form as a preview request, and
// the words a proposed exchange is said in. Pure, so it runs under `node --test`.

export const KINDS = [
  { value: "", label: "No bid: not part of this exchange" },
  { value: "answer", label: "Answers" },
  { value: "interject", label: "Interjects first" },
  { value: "aside", label: "Adds a side remark" },
  { value: "silence", label: "Stays silent, meaningfully" },
  { value: "nonverbal", label: "A nonverbal beat" },
];
export const GROUNDINGS = [
  { value: "addressed", label: "It was asked: an addressee of the question" },
  { value: "heard", label: "It heard a line" },
  { value: "intention", label: "Its own intention" },
  { value: "listening", label: "Listening (silence or a beat only)" },
];
const VERBS = { answer: "answers", interject: "interjects", aside: "adds a side remark", silence: "stays silent",
  nonverbal: "makes a nonverbal beat" };

// The grounding a kind of bid starts with, before the director picks one.
export function defaultGrounding(kind) {
  return kind === "silence" || kind === "nonverbal" ? "listening" : kind === "answer" ? "addressed" : "heard";
}

export function blankState(present) {
  const player = present.find((item) => item.role === "player");
  return { initiator: player ? player.actor : (present[0] ? present[0].actor : ""), addressees: [], floor: "",
    player_speaking: false, bids: {} };
}

// The saved form, read back: anything that no longer fits the cast here is dropped, never sent.
export function readState(text, present) {
  const here = new Set(present.map((item) => item.actor));
  // Only an NPC bids: a character handed to a player since the bid was saved loses it (051 refuses `player_speaker`).
  const npcs = new Set(present.filter((item) => item.role === "npc").map((item) => item.actor));
  let state;
  try { state = JSON.parse(text); } catch (error) { state = null; }
  if (!state || typeof state !== "object") return blankState(present);
  const keep = (list) => (Array.isArray(list) ? list.filter((actor) => here.has(actor)) : []);
  const bids = {};
  for (const [actor, bid] of Object.entries(state.bids || {})) {
    if (!npcs.has(actor) || !bid || typeof bid !== "object") continue;
    bids[actor] = { kind: String(bid.kind || ""), addressees: keep(bid.addressees), audience: keep(bid.audience),
      reason: String(bid.reason || ""), grounding: String(bid.grounding || ""), heard: String(bid.heard || ""), beat: String(bid.beat || "") };
  }
  return { initiator: here.has(state.initiator) ? state.initiator : blankState(present).initiator,
    addressees: keep(state.addressees), floor: here.has(state.floor) ? state.floor : "",
    player_speaking: state.player_speaking === true, bids };
}

// The preview request the form names, or { error } naming the first thing to fix.
export function requestOf(state, present, chat) {
  const everyone = present.map((item) => item.actor);
  const bids = [];
  for (const member of present) {
    if (member.role !== "npc") continue;
    const bid = state.bids[member.actor];
    if (!bid || !bid.kind) continue;
    const name = member.name || member.actor;
    if (!bid.reason.trim()) return { error: name + "'s bid needs its reason." };
    const grounding = bid.grounding || defaultGrounding(bid.kind);
    const audience = bid.audience.length ? bid.audience : everyone;
    const item = { actor: member.actor, kind: bid.kind, addressees: bid.addressees,
      audience: [...new Set([member.actor, ...bid.addressees, ...audience])], reason: bid.reason.trim(), grounding };
    if (grounding === "heard") {
      const line = Number(bid.heard);
      if (!/^\d{1,4}$/.test(bid.heard.trim()) || line < 1) return { error: name + "'s bid says which line it heard: a number from 1." };
      item.heard_index = line - 1;
    }
    if (bid.kind === "nonverbal") {
      if (!bid.beat.trim()) return { error: name + "'s nonverbal bid needs its beat." };
      item.nonverbal = bid.beat.trim();
    }
    bids.push(item);
  }
  if (!state.initiator) return { error: "Say who spoke first." };
  return { request: { chat, audience: "director", initiator: state.initiator, addressees: state.addressees,
    floor: state.floor || null, player_speaking: state.player_speaking, bids } };
}

const names = (list) => list.map((item) => item.name || item.actor).join(", ");

// One proposed contribution as a sentence: "1. Bellamy interjects, to Oda."
export function decisionText(item) {
  const to = item.addressees && item.addressees.length ? ", to " + names(item.addressees) : "";
  const beat = item.kind === "nonverbal" && item.beat ? ": " + item.beat : "";
  return (item.position ? item.position + ". " : "") + (item.name || item.actor) + " " + (VERBS[item.kind] || item.kind) + to + beat + ".";
}

// Who would hear it: the bid's audience, everyone else not.
export function heardText(item) {
  return "Heard by " + names(item.audience) + ".";
}

// What one character would be aware of in the exchange (051's own projection): only bids it may see.
export function viewText(view) {
  if (!view.sees.length) return (view.name || view.actor) + " is aware of nothing in this exchange.";
  return (view.name || view.actor) + " is aware of: " + view.sees.map((item) => (item.name || item.actor) + " "
    + (VERBS[item.kind] || item.kind) + (item.reason ? " (its own reason: " + item.reason + ")" : "")).join("; ") + ".";
}
